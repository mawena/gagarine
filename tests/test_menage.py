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
APPLI = '/opt/LM-Studio/lm-studio'
CRASHPAD = '/opt/LM-Studio/chrome_crashpad_handler'
NODE_INTERNE = '/home/u/.lmstudio/.internal/utils/node'
LMS = '/home/u/.lmstudio/bin/lms'
PORT = 41343


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
        self.exes = {}           # pid -> exécutable (/proc/<pid>/exe) ; par défaut /usr/bin/<nom>
        self.environ = {}        # pid -> {variable: valeur}
        self.parents = {}        # pid -> pid du parent
        self.ports = set()       # ports locaux qui acceptent une connexion
        self.connexions = []
        self.envs = []           # variables passées à chaque commande lancée
        self.au_lancement = None  # fonction(commande, env) : ce que la commande fait au poste (démarrer LM Studio…)
        self.lms_dossier = None

    def ajouter(self, pid, nom, ligne, mo, cpu=0.0, uid=UID, exe=None, parent=None, environ=None):
        self.procs[pid] = [Proc(nom, 'S', mo * Mo, 0, uid), ligne, cpu]
        self.exes[pid] = exe if exe is not None else f'/usr/bin/{nom}'
        if parent is not None:
            self.parents[pid] = parent
        if environ is not None:
            self.environ[pid] = environ

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
        if sig in (signal.SIGSTOP, signal.SIGCONT):
            return
        if sig == signal.SIGKILL or pid not in self.ignore_sigterm:
            del self.procs[pid]

    def exe(self, pid):
        return self.exes.get(pid, '') if pid in self.procs else ''

    def arguments(self, pid):
        return self.procs[pid][1].split() if pid in self.procs else []

    def parent(self, pid):
        return self.parents.get(pid) if pid in self.procs else None

    def variable(self, pid, nom):
        return self.environ.get(pid, {}).get(nom) if pid in self.procs else None

    def dossier_lms(self):
        return self.lms_dossier

    def port_ouvert(self, hote, port, delai=0.5):
        self.connexions.append((hote, port))
        return port in self.ports

    def chemin_lms(self):
        return '/fake/lms'

    def lancer(self, commande, delai=15, env=None):
        self.commandes.append(commande)
        self.envs.append(env)
        if self.au_lancement is not None:
            self.au_lancement(commande, env)
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
        self.poste.lms_dossier = self.dossier / 'lms'
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

    def lmstudio(self, pid=30, args='', exe=APPLI, cpu=0.0, serveur=True, port=PORT):
        """Le vrai LM Studio : son processus principal, et, si `serveur`, le verrou, http-server.json et le port prêts."""
        self.poste.ajouter(pid, exe.rsplit('/', 1)[-1][:15], f'{exe} {args}'.strip(), 150, cpu, exe=exe)
        if serveur:
            self.serveur_lms(pid, port=port)

    def serveur_lms(self, pid, verrou=None, port=PORT, ouvert=True):
        interne = self.poste.lms_dossier / '.internal'
        interne.mkdir(parents=True, exist_ok=True)
        (interne / 'llmster-pid.lock').write_text(str(pid if verrou is None else verrou))
        (interne / 'http-server.json').write_text(json.dumps({'host': '127.0.0.1', 'pid': pid, 'port': port}, indent=2))
        if ouvert:
            self.poste.ports.add(port)

    def nouveau_poste(self, sous_dossier='lms'):
        """Un poste neuf pour un sous-test, sans le cache de `lms ps` du précédent."""
        self.poste = FauxPoste()
        self.poste.lms_dossier = self.dossier / sous_dossier
        (self.dossier / 'lmstudio.json').unlink(missing_ok=True)

    def modele(self, nom='qwen-7b', mo=4500):
        self.poste.lms_ps = json.dumps([{'identifier': nom, 'sizeBytes': mo * Mo}])

    def appels_lms_ps(self):
        return [c for c in self.poste.commandes if c == ['/fake/lms', 'ps', '--json']]

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
        self.lmstudio(30, '--no-sandbox')
        self.modele('qwen-7b', 4500)
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
        self.poste.ajouter(31, 'lms', LMS + ' log stream', 50, exe=LMS)
        self.json()
        self.assertEqual(self.poste.commandes, [])

    def test_lmstudio_sans_modele_ou_trop_petit(self):
        self.lmstudio(30)
        self.assertEqual(self.json()['candidats'], [])
        self.poste.t += me.CACHE_LMS + 1   # la liste des modèles est gardée CACHE_LMS secondes
        self.poste.lms_ps = json.dumps([{'identifier': 'minuscule', 'sizeBytes': 100 * Mo}])
        self.assertEqual(self.json()['candidats'], [])
        self.poste.t += me.CACHE_LMS + 1
        self.poste.lms_ps = 'pas du json'
        self.assertEqual(self.json()['candidats'], [])
        self.assertEqual(len(self.appels_lms_ps()), 3)

    def test_lmstudio_candidat(self):
        self.lmstudio(30)
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


class TestDetectionLmStudio(Base):
    """LM Studio ne « tourne » que si son vrai programme tourne (1.5.1) : `lms` démarre LM Studio s'il ne tourne pas."""

    FAUX_POSITIFS = {
        # 1. une commande qui cite LM Studio : commande Bash d'une session Claude, tâche CCS, journal suivi, éditeur
        'bash': ('bash', '/usr/bin/bash -c sleep 30 # LM Studio lm-studio llmster /home/u/.lmstudio/x', '/usr/bin/bash'),
        'claude -p': ('claude', 'claude -p --verbose -- relance LM Studio puis /home/u/.lmstudio/bin/lms ps',
                      '/home/u/.local/share/claude/versions/2.1.322/claude'),
        'tail': ('tail', 'tail -f /home/u/.lmstudio/server-logs/2026-11/2026-11-13.1.log', '/usr/bin/tail'),
        'éditeur': ('gnome-text-edit', 'gnome-text-editor /home/u/.lmstudio/settings.json', '/usr/bin/gnome-text-editor'),
        # 2. les restes de LM Studio pendant sa fermeture
        'crashpad': ('chrome_crashpad', CRASHPAD + ' --monitor-self-annotation=ptype=crashpad-handler --no-rate-limit '
                     '--database=/home/u/.config/LM Studio/Crashpad --url=https://x', CRASHPAD),
        'node interne': ('node', NODE_INTERNE + ' -e function connectPort(port) {}', NODE_INTERNE),
        'fils zygote': ('lm-studio', APPLI + ' --type=zygote --no-zygote-sandbox', APPLI),
        'fils utilitaire': ('lm-studio', '/proc/self/exe --type=utility --utility-sub-type=network.mojom.NetworkService',
                            APPLI),
        'fils rendu': ('lm-studio', '/proc/self/exe --type=renderer --crashpad-handler-pid=42', APPLI),
        # 3. lms lui-même
        'lms': ('lms', LMS + ' log stream', LMS),
        # un programme qui porte le nom sans être LM Studio
        'nom trompeur': ('lm-studio', 'lm-studio --serve', '/home/u/bin/faux/python3'),
    }

    def test_aucun_faux_positif_ne_compte(self):
        for cas, (nom, ligne, exe) in self.FAUX_POSITIFS.items():
            with self.subTest(cas):
                self.nouveau_poste()
                self.poste.ajouter(40, nom, ligne, 400, exe=exe)
                # même avec un verrou, un http-server.json et un port qui le désignent
                self.serveur_lms(40)
                self.modele()
                self.assertEqual(me.instances_lmstudio(self.poste, self.poste.processus()), {})
                self.assertEqual(self.json()['candidats'], [])
                self.passages(3)
                self.lancer(['arreter', 'lmstudio'])
                self.assertEqual(self.poste.commandes, [])

    def test_tous_les_faux_positifs_ensemble(self):
        for i, (nom, ligne, exe) in enumerate(self.FAUX_POSITIFS.values()):
            self.poste.ajouter(40 + i, nom, ligne, 400, exe=exe)
        self.modele()
        self.assertEqual(self.json()['candidats'], [])
        self.assertEqual(self.poste.commandes, [])

    def test_le_vrai_processus_principal_compte(self):
        self.lmstudio(30)
        self.poste.ajouter(31, 'lm-studio', APPLI + ' --type=zygote', 100, exe=APPLI)
        self.poste.ajouter(32, 'chrome_crashpad', CRASHPAD + ' --database=/home/u/.config/LM Studio/Crashpad', 5,
                           exe=CRASHPAD)
        self.modele('qwen-7b', 4500)
        self.assertEqual(list(me.instances_lmstudio(self.poste, self.poste.processus())), [30])
        c = self.json()['candidats']
        self.assertEqual([(x['id'], x['memoire_mo']) for x in c], [('lmstudio', 4500)])
        self.assertEqual(self.poste.commandes, [['/fake/lms', 'ps', '--json']])

    def test_service_et_demon_sans_interface(self):
        for exe, args in ((APPLI, '--run-as-service'), ('/home/u/.lmstudio/llmster/0.0.3/llmster', ''),
                          (APPLI + ' (deleted)', '')):
            with self.subTest(exe):
                self.poste.procs.clear()
                self.lmstudio(30, args, exe=exe)
                self.assertEqual(list(me.instances_lmstudio(self.poste, self.poste.processus())), [30])

    def test_lms_n_est_appele_que_si_le_serveur_est_pret(self):
        """Verrou périmé, PID repris par un autre programme, http-server.json d'une autre instance, port fermé, fichiers
        absents ou abîmés : LM Studio tourne peut-être, mais `lms` pourrait en démarrer un autre ; on ne l'appelle pas."""
        interne = lambda: self.poste.lms_dossier / '.internal'
        cas = {
            'fichiers absents': lambda: None,
            'verrou périmé (PID mort)': lambda: self.serveur_lms(30, verrou=2517596),
            'verrou repris par un autre programme': lambda: (self.poste.ajouter(77, 'bash', 'bash', 5),
                                                             self.serveur_lms(30, verrou=77)),
            'http-server.json d\'avant': lambda: (self.serveur_lms(30),
                                                  (interne() / 'http-server.json').write_text(
                                                      json.dumps({'host': '127.0.0.1', 'pid': 99, 'port': PORT}))),
            'port fermé': lambda: self.serveur_lms(30, ouvert=False),
            'verrou abîmé': lambda: (self.serveur_lms(30), (interne() / 'llmster-pid.lock').write_text('x')),
            'http-server.json abîmé': lambda: (self.serveur_lms(30),
                                               (interne() / 'http-server.json').write_text('{')),
            'hôte distant': lambda: (self.serveur_lms(30), (interne() / 'http-server.json').write_text(
                json.dumps({'host': '192.168.1.5', 'pid': 30, 'port': PORT}))),
            'hôte qui n\'est pas du texte': lambda: (self.serveur_lms(30), (interne() / 'http-server.json').write_text(
                json.dumps({'host': ['127.0.0.1'], 'pid': 30, 'port': PORT}))),
        }
        for nom, preparer in cas.items():
            with self.subTest(nom):
                self.nouveau_poste(nom.replace(' ', '-'))
                self.lmstudio(30, serveur=False)
                preparer()
                self.modele()
                self.assertEqual(self.json()['candidats'], [])
                self.lancer(['arreter', 'lmstudio'])
                self.assertEqual(self.poste.commandes, [])
                self.assertNotIn(('192.168.1.5', PORT), self.poste.connexions)

    def test_serveur_sur_toutes_les_interfaces(self):
        self.lmstudio(30)
        (self.poste.lms_dossier / '.internal' / 'http-server.json').write_text(
            json.dumps({'host': '0.0.0.0', 'pid': 30, 'port': PORT}))
        self.modele()
        self.assertEqual(len(self.json()['candidats']), 1)
        self.assertEqual(self.poste.connexions, [('127.0.0.1', PORT)])

    def test_lms_recoit_la_marque_de_gagarine(self):
        self.lmstudio(30)
        self.modele()
        self.json()
        marque = self.poste.envs[0][me.MARQUE_LMS]
        self.assertTrue(marque.startswith(f'{me.os.getpid()}-'))


class TestCacheLms(Base):
    def test_lms_ps_n_est_pas_appele_a_chaque_passage(self):
        self.lmstudio(30)
        self.modele('qwen', 4096)
        debut = self.poste.t
        while self.poste.t < debut + me.CACHE_LMS - 1:   # l'indicateur : toutes les 2 s
            self.assertEqual(self.json()['candidats'][0]['memoire_mo'], 4096)
            self.poste.t += 2
        self.assertEqual(len(self.appels_lms_ps()), 1)
        self.poste.t = debut + me.CACHE_LMS + 1
        self.json()
        self.assertEqual(len(self.appels_lms_ps()), 2)

    def test_le_cache_suit_l_instance(self):
        self.lmstudio(30)
        self.modele()
        self.json()
        # LM Studio redémarre (autre PID) : on redemande tout de suite
        del self.poste.procs[30]
        self.lmstudio(31)
        self.json()
        self.assertEqual(len(self.appels_lms_ps()), 2)
        # LM Studio arrêté : plus de candidat, cache ou pas, et pas d'appel
        del self.poste.procs[31]
        self.assertEqual(self.json()['candidats'], [])
        self.assertEqual(len(self.appels_lms_ps()), 2)

    def test_un_echec_est_garde_aussi(self):
        self.lmstudio(30)
        self.poste.lms_code = 1
        for _ in range(10):
            self.json()
            self.poste.t += 2
        self.assertEqual(len(self.appels_lms_ps()), 1)

    def test_cache_abime_ignore(self):
        self.lmstudio(30)
        self.modele()
        abimes = ('{"pid": 30, "modeles": "pas une liste"',
                  json.dumps({'pid': [30], 'demarrage': 'd30', 'quand': self.poste.t, 'modeles': []}),
                  json.dumps({'pid': 30, 'demarrage': 'd30', 'quand': self.poste.t, 'modeles': [['x']]}),
                  json.dumps({'pid': 30, 'demarrage': 'd30', 'quand': 'hier', 'modeles': []}))
        for i, texte in enumerate(abimes, 1):
            with self.subTest(texte):
                (self.dossier / 'lmstudio.json').write_text(texte)
                self.assertEqual(len(self.json()['candidats']), 1)
                self.assertEqual(len(self.appels_lms_ps()), i)

    def test_decharger_vide_le_cache(self):
        self.lmstudio(30)
        self.modele()
        self.json()
        self.assertEqual(self.lancer(['arreter', 'lmstudio'])[0], 0)
        self.assertIn(['/fake/lms', 'unload', '--all'], self.poste.commandes)
        self.poste.lms_ps = '[]'
        self.assertEqual(self.json()['candidats'], [])
        self.assertEqual(len(self.appels_lms_ps()), 2)

    def test_decharger_reverifie_strictement(self):
        """Le cache dit qu'un modèle est chargé, mais LM Studio est en train de fermer (port fermé) : pas de `lms`."""
        self.lmstudio(30)
        self.modele()
        self.json()
        self.poste.ports.clear()
        code, sortie = self.lancer(['arreter', 'lmstudio'])
        self.assertEqual(code, 1)
        self.assertNotIn(['/fake/lms', 'unload', '--all'], self.poste.commandes)


class TestFiletLms(Base):
    """Si un appel de Gagarine à `lms` démarre LM Studio malgré tout, Gagarine l'arrête aussitôt et l'écrit au journal."""

    def demarrer_pendant_l_appel(self, marque=True, fermer_avant=True, args='--run-as-service'):
        """Pendant `lms ps`, LM Studio (pid 30) se ferme et `lms` en démarre un autre (50 et sa famille)."""
        def au_lancement(commande, env):
            if commande[1:2] != ['ps'] or 50 in self.poste.procs:
                return
            if fermer_avant:
                del self.poste.procs[30]
            m = {me.MARQUE_LMS: env[me.MARQUE_LMS]} if marque and env else {}
            self.poste.ajouter(50, 'lm-studio', f'{APPLI} {args}'.strip(), 150, exe=APPLI, parent=5613, environ=m)
            self.poste.ajouter(51, 'lm-studio', APPLI + ' --type=zygote', 100, exe=APPLI, parent=50, environ=m)
            self.poste.ajouter(52, 'chrome_crashpad', CRASHPAD + ' --database=/home/u/.config/LM Studio/Crashpad', 5,
                               exe=CRASHPAD, parent=5613, environ=m)
            self.poste.ajouter(53, 'node', NODE_INTERNE + ' -e x', 80, exe=NODE_INTERNE, parent=50)
        self.poste.au_lancement = au_lancement

    def test_instance_marquee_arretee_et_journal(self):
        self.poste.ajouter(5613, 'systemd', '/usr/lib/systemd/systemd --user', 10)
        self.lmstudio(30)
        self.modele()
        self.demarrer_pendant_l_appel()
        self.json()
        self.assertFalse({50, 51, 52, 53} & set(self.poste.procs))
        self.assertIn(5613, self.poste.procs)
        # figée d'abord (plus de nouveaux fils), puis SIGKILL : LM Studio ignore SIGTERM
        self.assertEqual([s for p, s in self.poste.signaux if p == 50], [signal.SIGSTOP, signal.SIGKILL])
        ev = [e for e in self.evenements() if e['action'] == 'lmstudio-arrete']
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]['pid'], ev[0]['nom'], ev[0]['commande']), (50, 'LM Studio', 'lms ps --json'))
        self.assertIn('raison', ev[0])

    def test_sans_marque_mais_aucune_instance_avant(self):
        """La variable n'est pas arrivée jusqu'à LM Studio : on reconnaît quand même l'instance démarrée par l'appel."""
        self.lmstudio(30)
        self.modele()
        self.demarrer_pendant_l_appel(marque=False)
        self.json()
        self.assertFalse({50, 51, 52, 53} & set(self.poste.procs))
        self.assertEqual(len([e for e in self.evenements() if e['action'] == 'lmstudio-arrete']), 1)

    def test_instance_de_l_utilisateur_jamais_touchee(self):
        for nom, options in (('ouverte par l\'utilisateur', {'marque': False, 'args': ''}),
                             ('LM Studio tournait encore', {'marque': False, 'fermer_avant': False})):
            with self.subTest(nom):
                self.nouveau_poste()
                self.lmstudio(30)
                self.modele()
                self.demarrer_pendant_l_appel(**options)
                self.json()
                self.assertEqual(self.poste.signaux, [])
                self.assertIn(50, self.poste.procs)

    def test_arret_immediat(self):
        """Pas d'attente de SIGTERM (LM Studio l'ignore) : l'indicateur arrête `menage --json` au bout de 6 s."""
        self.lmstudio(30)
        self.modele()
        self.demarrer_pendant_l_appel()
        self.poste.ignore_sigterm.update({50, 51})
        debut = self.poste.t
        self.json()
        self.assertFalse({50, 51, 52, 53} & set(self.poste.procs))
        self.assertNotIn(signal.SIGTERM, [s for _, s in self.poste.signaux])
        self.assertLessEqual(self.poste.t - debut, me.ATTENTE_FILET)
        self.assertLess(me.DELAI_LMS_PS + me.ATTENTE_FILET + 2 * me.DELAI_PORT, 6)

    def test_instance_disparue_ne_designe_pas_tous_les_programmes(self):
        """L'instance fautive a disparu (exécutable illisible) : son dossier n'est pas « / », et un programme né pendant
        l'appel (un terminal ouvert par l'utilisateur) n'est jamais touché."""
        self.poste.ajouter(70, 'gnome-terminal', '/usr/bin/gnome-terminal', 80, exe='/usr/bin/gnome-terminal')
        self.poste.ajouter(71, 'node', '/home/u/projet/node_modules/.bin/vite', 400, exe='/usr/bin/node')
        self.assertEqual(me.dossiers_lmstudio(self.poste, [99]), {str(self.poste.lms_dossier) + '/'})
        me.arreter_demarrage(self.poste, [99], None, set(), self.poste.processus(), None, 'lms ps --json')
        self.assertEqual([p for p, _ in self.poste.signaux if p != 99], [])
        self.assertEqual({70, 71} & set(self.poste.procs), {70, 71})

    def test_instance_ouverte_par_l_utilisateur_au_meme_moment(self):
        """Pendant l'appel, `lms` démarre une instance marquée ET l'utilisateur ouvre la sienne : seule la marquée part."""
        self.lmstudio(30)
        self.modele()
        self.demarrer_pendant_l_appel()
        suite = self.poste.au_lancement

        def au_lancement(commande, env):
            suite(commande, env)
            self.poste.ajouter(80, 'lm-studio', APPLI, 150, exe=APPLI, parent=2000)
            self.poste.ajouter(81, 'lm-studio', APPLI + ' --type=zygote', 100, exe=APPLI, parent=80)
            self.poste.ajouter(82, 'lm-studio', APPLI + ' --type=renderer', 100, exe=APPLI, parent=81)
            self.poste.ajouter(83, 'node', NODE_INTERNE + ' -e y', 80, exe=NODE_INTERNE, parent=80)
        self.poste.au_lancement = au_lancement
        self.json()
        self.assertFalse({50, 51, 52, 53} & set(self.poste.procs))
        self.assertEqual({80, 81, 82, 83} & set(self.poste.procs), {80, 81, 82, 83})
        self.assertFalse([p for p, _ in self.poste.signaux if p >= 80])

    def test_filet_aussi_pour_decharger(self):
        self.lmstudio(30)
        self.modele()
        self.json()

        def au_lancement(commande, env):
            if commande[1:2] == ['unload']:
                del self.poste.procs[30]
                self.poste.ajouter(60, 'lm-studio', APPLI + ' --run-as-service', 150, exe=APPLI,
                                   environ={me.MARQUE_LMS: env[me.MARQUE_LMS]})
        self.poste.au_lancement = au_lancement
        self.lancer(['arreter', 'lmstudio'])
        self.assertNotIn(60, self.poste.procs)
        ev = [e for e in self.evenements() if e['action'] == 'lmstudio-arrete']
        self.assertEqual(ev[0]['commande'], 'lms unload --all')

    def test_appel_interrompu_rattrape_au_passage_suivant(self):
        """`menage --json` arrêté par l'indicateur (6 s) avant d'avoir fini : l'instance marquée reste. Le passage suivant
        l'arrête si l'appel est fini et si elle est récente ; sinon il n'y touche pas."""
        jeton = lambda appelant, age: f'{appelant}-{int(self.poste.t - age)}'
        cas = (('appel fini, récente', jeton(999999, 10), True),
               ('appel fini, ancienne', jeton(999999, me.AGE_MARQUE + 5), False),
               ('appel en cours', jeton(4242, 10), False),
               ('marque abîmée', 'n-importe-quoi', False))
        for nom, marque, arretee in cas:
            with self.subTest(nom):
                self.nouveau_poste()
                self.poste.ajouter(4242, 'gagarine', '/usr/bin/python3 /usr/bin/gagarine menage --json', 30)
                self.poste.ajouter(50, 'lm-studio', APPLI + ' --run-as-service', 150, exe=APPLI,
                                   environ={me.MARQUE_LMS: marque})
                self.poste.ajouter(51, 'lm-studio', APPLI + ' --type=zygote', 100, exe=APPLI, parent=50,
                                   environ={me.MARQUE_LMS: marque})
                self.json()
                self.assertEqual(50 not in self.poste.procs, arretee)
                self.assertEqual(51 not in self.poste.procs, arretee)
                self.assertEqual(self.poste.commandes, [])


class TestPosteReel(unittest.TestCase):
    """Les lectures de /proc du vrai poste, sur des processus sans rapport avec LM Studio."""

    def test_exe_arguments_parent_variable(self):
        import os
        import subprocess
        import sys
        poste = me.Poste()
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', 'argument-témoin'],
                             env=dict(os.environ, GAGARINE_TEMOIN='42'))
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.assertEqual(poste.exe(p.pid), os.path.realpath(sys.executable))
        self.assertEqual(poste.arguments(p.pid)[-1], 'argument-témoin')
        self.assertEqual(poste.parent(p.pid), os.getpid())
        self.assertEqual(poste.variable(p.pid, 'GAGARINE_TEMOIN'), '42')
        self.assertIsNone(poste.variable(p.pid, 'GAGARINE_ABSENTE'))
        self.assertEqual((poste.exe(999999999), poste.arguments(999999999), poste.parent(999999999),
                          poste.variable(999999999, 'PATH')), ('', [], None, None))

    def test_port_ouvert(self):
        import socket
        poste = me.Poste()
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        port = s.getsockname()[1]
        self.assertTrue(poste.port_ouvert('127.0.0.1', port))
        s.close()
        self.assertFalse(poste.port_ouvert('127.0.0.1', port))

    def test_lancer_passe_les_variables(self):
        import sys
        code, sortie = me.Poste().lancer([sys.executable, '-c', 'import os; print(os.environ["GAGARINE_X"])'],
                                         env={'GAGARINE_X': 'oui'})
        self.assertEqual((code, sortie.strip()), (0, 'oui'))


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
