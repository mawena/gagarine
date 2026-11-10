"""Tests de la mémoire apprise de la file (pic mémoire, travaux.json, correction de l'annonce) et de la notification de
fin de travail. Le poste est simulé : ni cgroup réel, ni notify-send, ni réseau."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from gagarine import cli
from gagarine import file as fa
from gagarine import ntfy
from gagarine.reglages import Reglages
from tests.test_file import BancFile


class BancAppris(BancFile):
    def travaux(self):
        return json.loads((self.dossier_etat / 'travaux.json').read_text())

    def savoir(self, nom, pics, executions=None, annonce=1, derniere='2026-11-10T10:00:00+0000'):
        self.dossier_etat.mkdir(parents=True, exist_ok=True)
        try:
            t = json.loads((self.dossier_etat / 'travaux.json').read_text())
        except OSError:
            t = {}
        t[nom] = {'executions': executions or len(pics), 'pics_go': pics, 'annonce_go': annonce, 'derniere': derniere}
        (self.dossier_etat / 'travaux.json').write_text(json.dumps(t))

    def lancer_avec(self, reglages=None, **kw):
        sortie, erreur = __import__('io').StringIO(), __import__('io').StringIO()
        code = fa.lancer_travail(kw.pop('nom', 'tests'), kw.pop('ram', 1), kw.pop('coeurs', 1), None,
                                 ['true'], dict(kw.pop('env', self.env)), self.poste, sortie, erreur,
                                 reglages=reglages or Reglages())
        self.sortie, self.erreur = sortie.getvalue(), erreur.getvalue()
        return code


class TestPic(BancAppris):
    def test_pic_dans_travail_fin(self):
        self.poste.pics = [0.2, 0.31, 0.3]
        self.lancer_avec()
        fin = [e for e in self.journal() if e['action'] == 'travail-fin'][0]
        self.assertEqual(fin['pic_go'], 0.31)

    def test_sans_scope_pas_de_pic(self):
        self.poste.systemd = False
        self.lancer_avec()
        fin = [e for e in self.journal() if e['action'] == 'travail-fin'][0]
        self.assertNotIn('pic_go', fin)
        self.assertFalse((self.dossier_etat / 'travaux.json').exists())

    def test_cgroup_introuvable_pas_de_pic(self):
        self.poste.pics = []
        self.lancer_avec()
        self.assertNotIn('pic_go', [e for e in self.journal() if e['action'] == 'travail-fin'][0])

    def test_oom_compte_au_moins_la_limite(self):
        self.poste.resultat = 'oom-kill'
        self.poste.pics = [0.5]
        self.lancer_avec(ram=1)
        e = {x['action']: x for x in self.journal()}
        self.assertEqual(e['limite']['pic_go'], 3)
        self.assertEqual(e['travail-fin']['pic_go'], 3)
        self.assertEqual(self.travaux()['tests']['pics_go'], [3])

    def test_travail_herite_sans_pic_ni_memoire(self):
        self.ticket('1-10.json', 10)
        self.poste.pics = [1.0]
        self.lancer_avec(env={**self.env, 'FILE_ATTENTE_TICKET': '1-10.json'})
        self.assertEqual(self.journal(), [])
        self.assertFalse((self.dossier_etat / 'travaux.json').exists())


class TestReleveurReel(unittest.TestCase):
    """Le vrai Poste lit un faux cgroup dans un dossier temporaire."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.racine = Path(self.tmp.name)
        self.cg = self.racine / 'user.slice/x.scope'
        self.cg.mkdir(parents=True)
        self.poste = fa.Poste()
        self.poste.racine_cgroup = self.racine
        self.poste.executer = lambda cmd: (0, '/user.slice/x.scope\n')

    def test_ram_et_swap(self):
        (self.cg / 'memory.peak').write_text(str(2 ** 30) + '\n')
        (self.cg / 'memory.swap.peak').write_text(str(2 ** 29) + '\n')
        self.assertEqual(self.poste.pic_cgroup(self.cg), 1.5)

    def test_sans_swap_peak(self):
        (self.cg / 'memory.peak').write_text(str(3 * 2 ** 30))
        self.assertEqual(self.poste.pic_cgroup(self.cg), 3.0)

    def test_sans_memory_peak_ou_illisible(self):
        self.assertIsNone(self.poste.pic_cgroup(self.cg))
        (self.cg / 'memory.peak').write_text('max')
        self.assertIsNone(self.poste.pic_cgroup(self.cg))

    def test_chemin_du_scope(self):
        self.assertEqual(self.poste.cgroup_scope('x.scope'), self.cg)
        self.poste.executer = lambda cmd: (0, '\n')
        self.assertIsNone(self.poste.cgroup_scope('x.scope'))
        self.poste.executer = lambda cmd: (1, '')
        self.assertIsNone(self.poste.cgroup_scope('x.scope'))

    def test_releveur_garde_le_plus_haut_et_supporte_la_disparition(self):
        r = fa.Releveur(self.poste, 'x')
        r.relever()
        self.assertIsNone(r.pic)
        (self.cg / 'memory.peak').write_text(str(2 ** 30))
        r.relever()
        (self.cg / 'memory.peak').write_text(str(2 ** 29))
        r.relever()
        self.assertEqual(r.pic, 1.0)
        (self.cg / 'memory.peak').unlink()
        r.relever()
        self.assertEqual(r.pic, 1.0)

    def test_lancer_appelle_pendant(self):
        appels = []
        code = self.poste.lancer(['sleep', '0.7'], dict(os.environ), lambda: appels.append(1))
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(appels), 1)


class TestTravauxJson(BancAppris):
    def test_creation(self):
        self.poste.pics = [0.4]
        self.lancer_avec()
        t = self.travaux()['tests']
        self.assertEqual((t['executions'], t['pics_go'], t['annonce_go']), (1, [0.4], 1))
        self.assertEqual(t['derniere'], '2025-11-10T14:30:00+0000')

    def test_cinq_derniers_pics(self):
        for k in range(7):
            self.poste.pics = [float(k + 1)]
            self.lancer_avec(reglages=Reglages(file_ajuster=False))
        t = self.travaux()['tests']
        self.assertEqual(t['executions'], 7)
        self.assertEqual(t['pics_go'], [3.0, 4.0, 5.0, 6.0, 7.0])

    def test_ecriture_atomique_sans_reste(self):
        self.poste.pics = [0.4]
        self.lancer_avec()
        self.assertEqual(sorted(p.name for p in self.dossier_etat.iterdir() if p.name.startswith('.')), [])

    def test_fichier_corrompu_ignore_puis_remplace(self):
        self.dossier_etat.mkdir(parents=True)
        (self.dossier_etat / 'travaux.json').write_text('{pas du json')
        self.assertEqual(fa.lire_travaux(self.env), {})
        self.poste.pics = [0.4]
        self.lancer_avec()
        self.assertEqual(self.travaux()['tests']['pics_go'], [0.4])

    def test_entrees_mal_formees_ignorees(self):
        self.dossier_etat.mkdir(parents=True)
        (self.dossier_etat / 'travaux.json').write_text(json.dumps(
            {'a': 3, 'b': {'pics_go': 'x'}, 'c': {'pics_go': [1, 'y', None, 2]}, 'd': []}))
        self.assertEqual(list(fa.lire_travaux(self.env)), ['c'])
        self.assertEqual(fa.lire_travaux(self.env)['c']['pics_go'], [1, 2])
        (self.dossier_etat / 'travaux.json').write_text('[1]')
        self.assertEqual(fa.lire_travaux(self.env), {})

    def test_travail_annule_ne_note_rien(self):
        self.poste.pics = [0.4]
        self.poste.leve = fa.ErreurSignal(15)
        self.lancer_avec()
        self.assertFalse((self.dossier_etat / 'travaux.json').exists())

    def test_dossier_de_l_etat_par_defaut(self):
        self.assertEqual(fa.fichier_travaux({'HOME': '/h'}), Path('/h/.local/state/gagarine/travaux.json'))


class TestCorrection(BancAppris):
    def test_dans_les_10_pour_cent_rien(self):
        self.savoir('tests', [1.1])
        self.lancer_avec(ram=1)
        self.assertEqual(self.sortie, '')
        self.assertEqual(self.journal()[0]['ram'], 1)
        self.assertNotIn('ajuste', self.journal()[0])

    def test_au_dessus_message_et_correction(self):
        self.savoir('tests', [6.1, 6.3, 5.9])
        self.lancer_avec(ram=1)
        self.assertIn('annoncé 1 Go, il en a pris 6,3 Go la dernière fois ; annonce corrigée à 8 Go', self.sortie)
        d = self.journal()[0]
        self.assertEqual((d['ram'], d['annonce_initiale'], d['ajuste'], d['limite']), (8, 1, True, 16))
        self.assertIn('MemoryMax=16384M', self.poste.lances[0][0])

    def test_arrondi_au_demi_go_superieur(self):
        for pic, voulu in ((0.3, 0.5), (0.5, 1), (2.5, 3), (2.6, 3.5), (3.0, 4)):
            with self.subTest(pic=pic):
                self.savoir('tests', [pic])
                self.poste.lances.clear()
                self.lancer_avec(ram=0.1)
                self.assertEqual(self.journal()[-2]['ram'], voulu)

    def test_plafond_60_pour_cent_de_la_ram(self):
        self.poste.total = 10.0
        self.savoir('tests', [9.0])
        self.lancer_avec(ram=1)
        self.assertIn('annonce corrigée à 6 Go', self.sortie)
        self.assertIn('fuite de mémoire', self.sortie)
        self.assertEqual(self.journal()[0]['ram'], 6)

    def test_file_ajuster_non_message_seulement(self):
        self.savoir('tests', [6.3])
        self.lancer_avec(ram=1, reglages=Reglages(file_ajuster=False))
        self.assertIn('annoncé 1 Go, il en a pris 6,3 Go la dernière fois', self.sortie)
        self.assertNotIn('corrigée', self.sortie.replace('correction des annonces', ''))
        d = self.journal()[0]
        self.assertEqual(d['ram'], 1)
        self.assertNotIn('ajuste', d)

    def test_autre_travail_non_touche(self):
        self.savoir('autre', [6.3])
        self.lancer_avec(ram=1)
        self.assertEqual(self.sortie, '')

    def test_annonce_deja_suffisante_apres_plafond(self):
        self.poste.total = 4.0          # plafond 2,4 Go, annonce 3 : rien à corriger
        self.savoir('tests', [9.0])
        self.lancer_avec(ram=3)
        self.assertEqual(self.journal()[0]['ram'], 3)

    def test_ticket_porte_l_annonce_corrigee_et_etat_texte(self):
        self.savoir('tests', [6.3])
        vu = {}
        lancer = self.poste.lancer

        def espion(argv, env, pendant=None):
            vu.update(json.loads((self.tickets / env['FILE_ATTENTE_TICKET']).read_text()))
            vu['texte'] = self.lancer_main(['file', '--etat'], cli.Contexte(None, root=False, env=self.env, poste=self.poste))[1]
            return lancer(argv, env, pendant)
        self.poste.lancer = espion
        self.lancer_avec(ram=1)
        self.assertEqual((vu['ram'], vu['annonce_initiale']), (8, 1))
        self.assertIn('RAM annoncée 8 Go (corrigée : 1 Go demandés)', vu['texte'])

    def test_heritage_sans_correction(self):
        self.savoir('tests', [6.3])
        self.ticket('1-10.json', 10)
        self.lancer_avec(ram=1, env={**self.env, 'FILE_ATTENTE_TICKET': '1-10.json'})
        self.assertEqual(self.sortie, '')

    def test_etat_texte_sans_correction(self):
        self.ticket('1-10.json', 10, nom_travail='rendu', ram=4, coeurs=2)
        self.assertIn('rendu (processus 10, depuis ', self.lancer_main(
            ['file', '--etat'], cli.Contexte(None, root=False, env=self.env, poste=self.poste))[1])
        self.assertIn(', RAM annoncée 4 Go)', self.lancer_main(
            ['file', '--etat'], cli.Contexte(None, root=False, env=self.env, poste=self.poste))[1])


class TestAppris(BancAppris):
    def appris(self, *args):
        return self.lancer_main(['file', 'appris', *args], cli.Contexte(None, root=False, env=self.env, poste=self.poste))

    def test_json_du_plus_recent_au_plus_ancien(self):
        self.savoir('ancien', [2.0], derniere='2026-11-08T10:00:00+0000')
        self.savoir('recent', [6.1, 6.3, 5.9], annonce=1, derniere='2026-11-10T10:00:00+0000')
        code, sortie, _ = self.appris('--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sortie), {'travaux': [
            {'nom': 'recent', 'executions': 3, 'pic_go': 6.3, 'annonce_go': 1, 'derniere': '2026-11-10T10:00:00+0000'},
            {'nom': 'ancien', 'executions': 1, 'pic_go': 2.0, 'annonce_go': 1, 'derniere': '2026-11-08T10:00:00+0000'}]})

    def test_vide(self):
        self.assertEqual(json.loads(self.appris('--json')[1]), {'travaux': []})
        self.assertIn('Aucun travail appris', self.appris()[1])

    def test_texte(self):
        self.savoir('tests voix', [6.3], annonce=1)
        self.assertIn('tests voix : pic 6,3 Go, annoncé 1 Go (1 exécution(s))', self.appris()[1])

    def test_fichier_corrompu(self):
        self.dossier_etat.mkdir(parents=True)
        (self.dossier_etat / 'travaux.json').write_text('???')
        self.assertEqual(json.loads(self.appris('--json')[1]), {'travaux': []})

    def test_aide(self):
        self.assertIn('file appris', cli.AIDE)
        self.assertIn('gagarine file appris', cli.USAGE_FILE)


class TestNotification(BancAppris):
    def fin(self, duree=45, code=0, **kw):
        avant = self.poste.t
        lancer = self.poste.lancer

        def lent(argv, env, pendant=None):
            self.poste.t += duree
            return lancer(argv, env, pendant)
        self.poste.lancer = lent
        self.poste.code = code
        return self.lancer_avec(**kw)

    def test_terminé(self):
        self.fin(duree=12 * 60, nom='rendu vidéo')
        self.assertEqual(self.poste.notifs, [('Travail terminé', '« rendu vidéo » terminé en 12 min')])
        self.assertEqual(self.poste.ntfys, [])

    def test_echec(self):
        self.fin(code=3, nom='tests')
        self.assertEqual(self.poste.notifs, [('Travail échoué', '« tests » a échoué (code 3)')])

    def test_limite_depassee(self):
        self.poste.resultat = 'oom-kill'
        self.fin(nom='tests')
        self.assertEqual(self.poste.notifs, [('Travail arrêté', '« tests » arrêté : limite mémoire dépassée')])

    def test_seuil_de_duree(self):
        self.fin(duree=29)
        self.assertEqual(self.poste.notifs, [])
        self.fin(duree=30)
        self.assertEqual(len(self.poste.notifs), 1)

    def test_seuil_zero_toujours(self):
        self.fin(duree=0, reglages=Reglages(notification_fin_min=0))
        self.assertEqual(len(self.poste.notifs), 1)

    def test_reglage_non(self):
        self.fin(reglages=Reglages(notifications_fin=False, ntfy='sujet'))
        self.assertEqual((self.poste.notifs, self.poste.ntfys), ([], []))

    def test_ntfy_si_regle(self):
        self.fin(duree=90, code=2, reglages=Reglages(ntfy='mon-sujet'), nom='tests')
        self.assertEqual(self.poste.ntfys, [('mon-sujet', 'Travail échoué', '« tests » a échoué (code 2)', 'haute', ('x',))])
        self.poste.ntfys.clear()
        self.fin(duree=90, reglages=Reglages(ntfy='mon-sujet'), nom='tests')
        self.assertEqual(self.poste.ntfys[0][3], 'normale')

    def test_pas_de_notification_pour_un_travail_annule(self):
        self.poste.leve = fa.ErreurSignal(15)
        self.fin(duree=60)
        self.assertEqual(self.poste.notifs, [])

    def test_pas_de_notification_pour_un_travail_herite(self):
        self.ticket('1-10.json', 10)
        self.fin(duree=60, env={**self.env, 'FILE_ATTENTE_TICKET': '1-10.json'})
        self.assertEqual(self.poste.notifs, [])

    def test_jamais_d_exception(self):
        def casse(*a):
            raise RuntimeError('pas de bureau')
        self.poste.notifier = casse
        self.assertEqual(self.fin(duree=60, reglages=Reglages(ntfy='s')), 0)
        self.poste.notifier = lambda *a: None
        self.poste.alerter_ntfy = casse
        self.assertEqual(self.fin(duree=60, reglages=Reglages(ntfy='s')), 0)
        self.assertEqual(self.journal()[-1]['action'], 'travail-fin')

    def test_duree_en_texte(self):
        self.assertEqual([fa.duree_texte(s) for s in (5, 59, 60, 750, 3600, 3900)],
                         ['5 s', '59 s', '1 min', '12 min', '1 h 00 min', '1 h 05 min'])


class TestPosteNotifier(unittest.TestCase):
    def test_commande_notify_send(self):
        appels = []
        import subprocess
        vrai = subprocess.Popen
        subprocess.Popen = lambda cmd, **kw: appels.append(cmd)
        try:
            fa.Poste().notifier('Travail terminé', '« x » terminé en 1 min')
        finally:
            subprocess.Popen = vrai
        self.assertEqual(appels, [['notify-send', '--app-name=Gagarine', '-i', 'io.github.mawena.Gagarine', '-h',
                                   'string:desktop-entry:io.github.mawena.Gagarine', 'Travail terminé',
                                   '« x » terminé en 1 min']])

    def test_notify_send_absent_sans_exception(self):
        import subprocess
        vrai = subprocess.Popen

        def casse(*a, **k):
            raise FileNotFoundError(2, 'absent')
        subprocess.Popen = casse
        try:
            fa.Poste().notifier('a', 'b')
        finally:
            subprocess.Popen = vrai

    def test_ntfy_attend_le_fil_et_utilise_un_faux_ouvrir(self):
        corps = []

        class Reponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def ouvrir(requete, timeout=None):
            corps.append(json.loads(requete.data))
            return Reponse()
        fil = ntfy.envoyer_en_fond('sujet-test', 't', 'm', 'haute', ('x',), ouvrir=ouvrir)
        fil.join(5)
        self.assertEqual(corps[0]['topic'], 'sujet-test')


if __name__ == '__main__':
    unittest.main()
