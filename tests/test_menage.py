"""Tests du ménage des inactifs : processus, horloge, API et commandes simulés."""
import io
import json
import signal
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from gagarine import menage as me
from gagarine.systeme import Proc

Mo = me.Mo
UID = 1000
LIGNE_GRADLE = ('/usr/lib/jvm/java-17/bin/java -Xmx512m -cp /home/u/.gradle/wrapper/dists/gradle-8.5/lib/'
                'gradle-launcher-8.5.jar org.gradle.launcher.daemon.bootstrap.GradleDaemon 8.5')
LIGNE_KOTLIN = 'java -Xmx1g -cp kotlin-compiler.jar org.jetbrains.kotlin.daemon.KotlinCompileDaemon --daemon-runFilesPath x'


class FauxPoste:
    """Poste simulé : processus, temps processeur, horloge, signaux, API Ollama et commandes lms."""

    def __init__(self):
        self.t = 10000.0
        self.pct = 50.0
        self.procs = {}      # pid -> [Proc, ligne, cpu]
        self.signaux = []
        self.commandes = []
        self.requetes = []
        self.ollama = None   # None : absent ; sinon {"models": [...]}
        self.lms_ps = '[]'
        self.lms_code = 0
        self.ignore_sigterm = set()
        self.api_ok = True

    def ajouter(self, pid, nom, ligne, mo, cpu=0.0, uid=UID):
        self.procs[pid] = [Proc(nom, 'S', mo * Mo, 0, uid), ligne, cpu]

    def maintenant(self):
        return self.t

    def dormir(self, s):
        self.t += s

    def ram(self):
        return self.pct

    def processus(self):
        return {pid: p[0] for pid, p in self.procs.items()}

    def ligne_commande(self, pid):
        return self.procs[pid][1] if pid in self.procs else ''

    def cpu(self, pid):
        return self.procs[pid][2] if pid in self.procs else None

    def demarrage(self, pid):
        return f'd{pid}' if pid in self.procs else None

    def vivant(self, pid):
        return pid in self.procs

    def tuer(self, pid, sig):
        if pid not in self.procs:
            raise ProcessLookupError
        self.signaux.append((pid, sig))
        if sig == signal.SIGKILL or pid not in self.ignore_sigterm:
            del self.procs[pid]

    def chemin_lms(self):
        return '/fake/lms'

    def lancer(self, commande, delai=15):
        self.commandes.append(commande)
        if commande[1:2] == ['ps']:
            return self.lms_code, self.lms_ps
        return self.lms_code, ''

    def api(self, methode, chemin, corps=None, delai=1.5):
        self.requetes.append((methode, chemin, corps))
        if self.ollama is None:
            return None
        return self.ollama if methode == 'GET' else {}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dossier = Path(self._tmp.name)
        self.poste = FauxPoste()
        self.env = {'GAGARINE_ETAT': str(self.dossier), 'GAGARINE_CONFIG': str(self.dossier / 'absent.conf')}

    def ctx(self, **reglages):
        env = dict(self.env)
        env.update({f'GAGARINE_{k.upper()}': str(v) for k, v in reglages.items()})
        return SimpleNamespace(env=env, config=None)

    def lancer(self, args, **reglages):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            code = me.commande_menage(args, self.ctx(**reglages), self.poste)
        return code, sortie.getvalue()

    def json(self, **reglages):
        code, sortie = self.lancer(['--json'], **reglages)
        self.assertEqual(code, 0)
        return json.loads(sortie)

    def evenements(self):
        f = self.dossier / 'evenements.jsonl'
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def gradle(self, pid=100, mo=500, cpu=1.0):
        self.poste.ajouter(pid, 'java', LIGNE_GRADLE, mo, cpu)

    def passages(self, n, pas=120, **reglages):
        """n passages « auto » espacés de `pas` secondes ; retourne le code du dernier."""
        for _ in range(n):
            code, _ = self.lancer(['auto'], **reglages)
            self.poste.t += pas
        return code


class TestReconnaissance(Base):
    def test_gradle_et_kotlin(self):
        self.gradle(100)
        self.poste.ajouter(101, 'java', LIGNE_KOTLIN, 400)
        c = {x['id']: x for x in self.json()['candidats']}
        self.assertEqual((c['100']['type'], c['100']['pid'], c['100']['memoire_mo'], c['100']['action']),
                         ('gradle', 100, 500, 'arreter'))
        self.assertEqual(c['101']['type'], 'kotlin')

    def test_faux_positifs_ecartes(self):
        self.poste.ajouter(1, 'java', 'java -jar /srv/app/production.jar --port 8080', 2000)
        self.poste.ajouter(2, 'node', 'node /srv/app/server.js', 900)
        self.poste.ajouter(3, 'node', 'node /app/node_modules/.bin/next start', 900)
        self.poste.ajouter(4, 'node', 'node /app/node_modules/vite/bin/vite.js build', 900)
        self.poste.ajouter(5, 'node', 'node /app/node_modules/vite/bin/vite.js preview', 900)
        self.poste.ajouter(6, 'python3', 'python3 GradleDaemon.py', 900)
        self.poste.ajouter(7, 'node', 'node /app/vite-plugin-x.js', 900)
        self.poste.ajouter(8, 'chrome', '/opt/google/chrome/chrome', 900)
        self.assertEqual(self.json()['candidats'], [])

    def test_serveurs_de_dev(self):
        lignes = {10: 'node /app/node_modules/vite/bin/vite.js', 11: 'node /app/node_modules/.bin/next dev -p 3001',
                  12: 'node /app/node_modules/.bin/webpack serve --mode development',
                  13: 'node /app/node_modules/@angular/cli/bin/ng serve', 14: 'bun /app/node_modules/.bin/astro dev'}
        for pid, ligne in lignes.items():
            self.poste.ajouter(pid, ligne.split()[0], ligne, 400)
        c = self.json()['candidats']
        self.assertEqual(sorted(x['pid'] for x in c), [10, 11, 12, 13, 14])
        self.assertTrue(all(x['type'] == 'serveur-dev' and x['auto'] is False for x in c))

    def test_seuil_de_300_mo(self):
        self.gradle(100, mo=299)
        self.gradle(101, mo=300)
        self.assertEqual([c['pid'] for c in self.json()['candidats']], [101])

    def test_seulement_les_processus_de_l_utilisateur(self):
        # Le poste réel ne fournit que ceux de l'utilisateur ; ici on vérifie que Poste.processus filtre par uid.
        appels = []
        vrai = me.lire_processus
        me.lire_processus = lambda uid=None, moi=None: appels.append(uid) or {}
        try:
            me.Poste().processus()
        finally:
            me.lire_processus = vrai
        self.assertEqual(appels, [me.os.getuid()])


class TestInactivite(Base):
    def test_premier_passage_puis_inactivite_qui_grandit(self):
        self.gradle()
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'], 0)
        self.poste.t += 600
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'], 600)

    def test_temps_processeur_qui_bouge_vaut_activite(self):
        self.gradle()
        self.json()
        self.poste.t += 600
        self.json()
        self.poste.t += 60
        self.poste.procs[100][2] += 30   # 30 s de processeur : un build
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'], 0)
        self.poste.t += 60
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'], 60)

    def test_tolerance_du_temps_processeur(self):
        self.gradle()
        self.json()
        self.poste.t += 120
        self.poste.procs[100][2] += 0.1   # bruit du ramasse-miettes
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'], 120)

    def test_pid_repris_par_un_autre_programme(self):
        self.gradle()
        self.json()
        self.poste.t += 600
        del self.poste.procs[100]
        self.gradle()
        self.poste.demarrage = lambda pid: 'autre'
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'], 0)

    def test_suivi_oublie_les_disparus(self):
        self.gradle()
        self.json()
        del self.poste.procs[100]
        self.json()
        self.assertEqual(json.loads((self.dossier / 'menage.json').read_text())['suivi'], {})

    def test_menage_json_corrompu_ignore(self):
        (self.dossier / 'menage.json').write_text('{pas du json')
        self.gradle()
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'], 0)
        (self.dossier / 'menage.json').write_text('{"suivi": {"100": {"cpu": "x"}, "101": 3}}')
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'], 0)
        self.assertEqual(json.loads((self.dossier / 'menage.json').read_text())['version'], 1)

    def test_processus_disparu_pendant_la_lecture(self):
        self.gradle()
        self.poste.cpu = lambda pid: None
        self.assertEqual(len(self.json()['candidats']), 1)


class TestJson(Base):
    def test_contrat_exact(self):
        self.gradle(100, mo=512)
        d = self.json(menage_delai=45)
        self.assertEqual(set(d), {'auto', 'delai_min', 'candidats'})
        self.assertIs(d['auto'], True)
        self.assertEqual(d['delai_min'], 45)
        self.assertEqual(list(d['candidats'][0]),
                         ['id', 'type', 'nom', 'pid', 'memoire_mo', 'inactif_depuis_s', 'action', 'auto'])
        self.assertEqual(d['candidats'][0]['id'], '100')
        self.assertIs(d['candidats'][0]['auto'], True)

    def test_auto_non_dans_le_json(self):
        self.assertIs(self.json(menage_auto='non')['auto'], False)

    def test_vide(self):
        self.assertEqual(self.json(), {'auto': True, 'delai_min': 30, 'candidats': []})

    def test_sortie_texte(self):
        self.gradle()
        code, sortie = self.lancer([])
        self.assertEqual(code, 0)
        self.assertIn('Gradle (démon)', sortie)

    def test_usage(self):
        self.assertEqual(self.lancer(['bizarre'])[0], 2)
        self.assertEqual(self.lancer(['arreter'])[0], 2)


class TestAuto(Base):
    def test_delai_normal_de_30_min(self):
        self.gradle()
        self.passages(15)   # 15 passages, dernier à 28 min
        self.assertEqual(self.poste.signaux, [])
        self.passages(2)    # passages à 30 et 32 min
        self.assertEqual(self.poste.signaux, [(100, signal.SIGTERM)])

    def test_delai_court_au_dela_de_vigilance(self):
        self.gradle()
        self.poste.pct = 90
        self.passages(2)    # passages à 0 et 2 min
        self.assertEqual(self.poste.signaux, [])
        self.passages(2)    # 4 et 6 min
        self.assertEqual(self.poste.signaux, [(100, signal.SIGTERM)])

    def test_delai_court_ne_rallonge_jamais_le_delai(self):
        self.gradle()
        self.poste.pct = 90
        self.passages(4, menage_delai=2)
        self.assertEqual(len(self.poste.signaux), 1)

    def test_programme_actif_jamais_arrete(self):
        self.gradle()
        for _ in range(30):
            self.poste.procs[100][2] += 20
            self.lancer(['auto'])
            self.poste.t += 120
        self.assertEqual(self.poste.signaux, [])

    def test_serveur_de_dev_jamais_automatique(self):
        self.poste.ajouter(20, 'node', 'node /app/node_modules/vite/bin/vite.js', 600)
        self.passages(40)
        self.poste.pct = 99
        self.passages(5)
        self.assertEqual(self.poste.signaux, [])
        self.assertEqual(self.json()['candidats'][0]['inactif_depuis_s'] > 3600, True)

    def test_menage_auto_non_ne_fait_rien(self):
        self.gradle()
        self.poste.ollama = {'models': [{'name': 'llama3', 'size': 4000 * Mo, 'expires_at': 'x'}]}
        self.passages(40, menage_auto='non')
        self.assertEqual((self.poste.signaux, self.poste.commandes), ([], []))
        self.assertNotIn(('POST', '/api/generate', {'model': 'llama3', 'keep_alive': 0}), self.poste.requetes)
        self.assertEqual(self.evenements(), [])
        # le suivi continue : l'inactivité est connue dès qu'on réactive le ménage
        self.assertGreater(self.json(menage_auto='non')['candidats'][0]['inactif_depuis_s'], 3600)

    def test_evenement_menage_au_journal(self):
        self.gradle(100, mo=512)
        self.passages(17)
        ev = self.evenements()
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]['action'], ev[0]['type'], ev[0]['nom'], ev[0]['pid'], ev[0]['memoire_mo'], ev[0]['auto']),
                         ('menage', 'gradle', 'Gradle (démon)', 100, 512, True))

    def test_ollama_decharge_par_l_api(self):
        self.poste.ollama = {'models': [{'name': 'llama3:8b', 'size': 5000 * Mo, 'expires_at': '2026-11-10T10:00:00Z'}]}
        self.passages(17)
        self.assertIn(('POST', '/api/generate', {'model': 'llama3:8b', 'keep_alive': 0}), self.poste.requetes)
        ev = self.evenements()
        self.assertEqual((ev[0]['type'], ev[0]['memoire_mo'], ev[0]['auto']), ('ollama', 5000, True))
        self.assertNotIn('pid', ev[0])   # le journal n'écrit pas les champs vides

    def test_ollama_expires_at_qui_change_vaut_activite(self):
        for i in range(40):
            self.poste.ollama = {'models': [{'name': 'm', 'size': 5000 * Mo, 'expires_at': f'e{i}'}]}
            self.lancer(['auto'])
            self.poste.t += 120
        self.assertNotIn('POST', [r[0] for r in self.poste.requetes])

    def test_lmstudio_decharge_si_il_tourne(self):
        self.poste.ajouter(30, 'lm-studio', '/opt/LM-Studio/lm-studio --no-sandbox', 150)
        self.poste.lms_ps = json.dumps([{'identifier': 'qwen-7b', 'sizeBytes': 4500 * Mo}])
        self.passages(17)
        self.assertIn(['/fake/lms', 'unload', '--all'], self.poste.commandes)
        self.assertEqual(self.evenements()[0]['type'], 'lmstudio')


class TestApi(Base):
    def test_lms_jamais_lance_si_lm_studio_ne_tourne_pas(self):
        self.poste.lms_ps = json.dumps([{'identifier': 'x', 'sizeBytes': 4000 * Mo}])
        self.passages(40)
        self.json()
        self.lancer(['arreter', 'lmstudio'])
        self.assertEqual(self.poste.commandes, [])

    def test_lms_non_lance_pour_un_programme_qui_s_appelle_lms(self):
        self.poste.ajouter(31, 'lms', '/home/u/.lmstudio/bin/lms log stream', 50)
        self.json()
        self.assertEqual(self.poste.commandes, [])

    def test_lmstudio_sans_modele_ou_trop_petit(self):
        self.poste.ajouter(30, 'lm-studio', '/opt/LM-Studio/lm-studio', 150)
        self.assertEqual(self.json()['candidats'], [])
        self.poste.lms_ps = json.dumps([{'identifier': 'minuscule', 'sizeBytes': 100 * Mo}])
        self.assertEqual(self.json()['candidats'], [])
        self.poste.lms_ps = 'pas du json'
        self.assertEqual(self.json()['candidats'], [])

    def test_lmstudio_candidat(self):
        self.poste.ajouter(30, 'lm-studio', '/opt/LM-Studio/lm-studio', 150)
        self.poste.lms_ps = json.dumps([{'identifier': 'qwen', 'sizeBytes': 4096 * Mo},
                                        {'identifier': 'gemma', 'sizeBytes': 2048 * Mo}])
        c = self.json()['candidats'][0]
        self.assertEqual((c['id'], c['type'], c['pid'], c['memoire_mo'], c['action']),
                         ('lmstudio', 'lmstudio', None, 6144, 'decharger'))
        self.assertIn('qwen', c['nom'])

    def test_ollama_absent_sans_erreur(self):
        self.poste.ollama = None
        self.gradle()
        code, _ = self.lancer(['--json'])
        self.assertEqual(code, 0)
        self.assertEqual(self.passages(2), 0)
        self.assertEqual(self.json()['candidats'][0]['type'], 'gradle')

    def test_ollama_modeles_candidats(self):
        self.poste.ollama = {'models': [{'name': 'llama3', 'size': 5000 * Mo, 'expires_at': 'x'},
                                        {'name': 'petit', 'size': 100 * Mo, 'expires_at': 'x'}, 'bizarre']}
        c = self.json()['candidats']
        self.assertEqual([(x['id'], x['type'], x['action'], x['pid']) for x in c],
                         [('ollama:llama3', 'ollama', 'decharger', None)])

    def test_reponse_ollama_inattendue(self):
        self.poste.ollama = {'models': None}
        self.assertEqual(self.json()['candidats'], [])
        self.poste.ollama = {'autre': 1}
        self.assertEqual(self.json()['candidats'], [])


class TestArreter(Base):
    def test_sigterm_suffit(self):
        self.gradle()
        code, sortie = self.lancer(['arreter', '100'])
        self.assertEqual(code, 0)
        self.assertEqual(self.poste.signaux, [(100, signal.SIGTERM)])
        self.assertIn('arrêté', sortie)
        ev = self.evenements()
        self.assertEqual((ev[0]['action'], ev[0]['auto']), ('menage', False))

    def test_sigkill_apres_10_s(self):
        self.gradle()
        self.poste.ignore_sigterm.add(100)
        debut = self.poste.t
        code, _ = self.lancer(['arreter', '100'])
        self.assertEqual(code, 0)
        self.assertEqual(self.poste.signaux, [(100, signal.SIGTERM), (100, signal.SIGKILL)])
        self.assertGreaterEqual(self.poste.t - debut, 10)

    def test_id_inconnu_ou_pas_candidat_jamais_touche(self):
        self.gradle(100)
        self.poste.ajouter(200, 'chrome', '/opt/google/chrome/chrome', 900)
        code, sortie = self.lancer(['arreter', '200', '999', 'ollama:x'])
        self.assertEqual(code, 1)
        self.assertEqual(self.poste.signaux, [])
        self.assertIn('pas un candidat', sortie)

    def test_plusieurs_ids(self):
        self.gradle(100)
        self.gradle(101)
        self.assertEqual(self.lancer(['arreter', '100', '101'])[0], 0)
        self.assertEqual(len(self.poste.signaux), 2)

    def test_arreter_un_serveur_de_dev_a_la_main(self):
        self.poste.ajouter(20, 'node', 'node /app/node_modules/vite/bin/vite.js', 600)
        self.assertEqual(self.lancer(['arreter', '20'])[0], 0)
        self.assertEqual(self.poste.signaux, [(20, signal.SIGTERM)])

    def test_decharger_ollama_a_la_main(self):
        self.poste.ollama = {'models': [{'name': 'llama3', 'size': 5000 * Mo, 'expires_at': 'x'}]}
        self.assertEqual(self.lancer(['arreter', 'ollama:llama3'])[0], 0)
        self.assertIn(('POST', '/api/generate', {'model': 'llama3', 'keep_alive': 0}), self.poste.requetes)

    def test_decharger_ollama_en_erreur(self):
        self.poste.ollama = {'models': [{'name': 'llama3', 'size': 5000 * Mo, 'expires_at': 'x'}]}
        vrai = self.poste.api
        self.poste.api = lambda m, c, corps=None, delai=1.5: vrai(m, c, corps, delai) if m == 'GET' else None
        self.assertEqual(self.lancer(['arreter', 'ollama:llama3'])[0], 1)
        self.assertEqual(self.evenements(), [])

    def test_disparu_avant_le_signal(self):
        self.gradle()
        vrai = self.poste.tuer

        def tuer(pid, sig):
            del self.poste.procs[pid]
            raise ProcessLookupError
        self.poste.tuer = tuer
        self.assertEqual(self.lancer(['arreter', '100'])[0], 0)


class TestUnites(unittest.TestCase):
    def test_duree(self):
        self.assertEqual((me.duree(30), me.duree(600), me.duree(7200)), ('30 s', '10 min', '2,0 h'))

    def test_extensions_du_cli(self):
        from gagarine import cli
        trouvees = {m.__name__: c for m, c, _ in cli.charger_extensions(('menage',))}
        self.assertIn('menage', trouvees['gagarine.menage'])
        self.assertIn('menage', cli.aide())


if __name__ == '__main__':
    unittest.main()
