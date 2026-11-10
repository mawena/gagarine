"""Tests de Gagarine : décisions sur des mesures simulées, sans root ni vraie allocation."""
import importlib.machinery
import importlib.util
import json
import os
import signal
import tempfile
import unittest
from pathlib import Path

CHEMIN = Path(__file__).resolve().parent.parent / 'bin' / 'gagarine'
chargeur = importlib.machinery.SourceFileLoader('gagarine', str(CHEMIN))
spec = importlib.util.spec_from_loader('gagarine', chargeur)
g = importlib.util.module_from_spec(spec)
chargeur.exec_module(g)

Mo = g.Mo


class Faux:
    """Système simulé : RAM, processus, horloge et actions enregistrées."""

    def __init__(self, r=None):
        self.r = r
        self.pct = 50.0
        self.procs = {}
        self.t = 1000.0
        self.signaux = []
        self.notifs = []
        self.rangements = []
        self.bridages = []
        self.erreur_bridage = None
        self.debuts = {}
        self.commandes = {}

    def ram(self):
        return self.pct

    def processus(self):
        return dict(self.procs)

    def demarrage(self, pid):
        return self.debuts.get(pid, f'd{pid}') if pid in self.procs else None

    def commande(self, pid):
        return self.commandes.get(pid, f'/usr/bin/prog{pid}')

    def cgroup(self, pid):
        return Path('/sys/fs/cgroup/test')

    def utilisateur(self, uid):
        return f'u{uid}'

    def heure(self):
        return self.t

    def mono(self):
        return self.t

    def tuer(self, pid, sig):
        self.signaux.append((pid, sig))
        if sig == signal.SIGSTOP and pid in self.procs:
            self.procs[pid] = self.procs[pid]._replace(etat='T')
        if sig == signal.SIGKILL:
            self.procs.pop(pid, None)
        if sig == signal.SIGCONT and pid in self.procs:
            self.procs[pid] = self.procs[pid]._replace(etat='S')

    def brider(self, pid):
        self.bridages.append(pid)
        return self.erreur_bridage

    def ranger(self, pid, rss, origine):
        self.rangements.append((pid, rss))

    def notifier(self, titre, texte, pid=None, debut=None):
        self.notifs.append((titre, pid))

    def ajouter(self, pid, nom, rss_mo, etat='S', swap_mo=0, uid=1000):
        self.procs[pid] = g.Proc(nom, etat, rss_mo * Mo, swap_mo * Mo, uid)

    def avancer(self, secondes=1.0):
        self.t += secondes


class Banc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dossier = Path(self.tmp.name)
        self.paquets = []

    def garde(self, **reglages):
        self.r = g.Reglages(**reglages)
        self.s = Faux(self.r)
        self.j = g.Journal(self.dossier, heure=lambda: self.s.t, sortie=False)
        self.g = g.Garde(self.r, self.s, self.j, self.dossier / 'figes.json', lancer=lambda f, *a: self.paquets.append((f, a)))
        return self.g

    def evenements(self):
        try:
            return [json.loads(l) for l in (self.dossier / 'evenements.jsonl').read_text().splitlines()]
        except OSError:
            return []

    def actions(self):
        return [e['action'] for e in self.evenements()]

    def tours(self, n, pas=1.0, pct=None):
        for _ in range(n):
            if pct is not None:
                self.s.pct = pct
            self.g.tour()
            self.s.avancer(pas)


class TestCoupable(Banc):
    def test_le_plus_rapide_sur_10_s(self):
        self.garde()
        self.s.ajouter(10, 'gros', 3000)
        self.s.ajouter(11, 'rapide', 100)
        for i in range(12):
            self.s.ajouter(11, 'rapide', 100 + i * 100)
            self.g.tour()
            self.s.avancer()
        pid, c = self.g.coupable(self.s.procs, self.s.mono())
        self.assertEqual(pid, 11)
        self.assertGreaterEqual(c, 64 * Mo)

    def test_a_defaut_le_plus_gros(self):
        self.garde()
        self.s.ajouter(10, 'gros', 3000)
        self.s.ajouter(11, 'petit', 100)
        self.tours(3)
        self.assertEqual(self.g.coupable(self.s.procs, self.s.mono()), (10, 0))

    def test_fenetre_de_60_s_si_rien_sur_10_s(self):
        self.garde()
        self.s.ajouter(10, 'gros', 3000)
        self.s.ajouter(11, 'lent', 100)
        for i in range(40):   # +10 Mo par seconde : 100 Mo sur 10 s mais seulement 65 Mo sur 6,5 s... on vise la fenêtre longue
            self.s.ajouter(11, 'lent', 100 + i * 3)
            self.g.tour()
            self.s.avancer()
        # 3 Mo/s : 30 Mo sur 10 s (< 64), 117 Mo sur 40 s mais fenêtre limitée à 60 s : retenu à la 2e fenêtre
        self.assertEqual(self.g.coupable(self.s.procs, self.s.mono())[0], 11)

    def test_swap_compte_dans_la_croissance(self):
        self.garde()
        self.s.ajouter(10, 'a', 100)
        self.tours(1)
        self.s.ajouter(10, 'a', 10, swap_mo=300)
        self.tours(1)
        self.assertGreaterEqual(self.g.croissance(10, 10, self.s.mono()), 64 * Mo)

    def test_ignore_proteges_figes_et_arretes(self):
        self.garde()
        self.s.ajouter(10, 'gnome-shell', 5000)
        self.s.ajouter(11, 'figé', 4000, etat='T')
        self.s.ajouter(12, 'zombie', 3000, etat='Z')
        self.s.ajouter(13, 'ok', 100)
        self.tours(2)
        self.assertEqual(self.g.coupable(self.s.procs, self.s.mono())[0], 13)

    def test_aucun_candidat(self):
        self.garde()
        self.s.ajouter(10, 'sshd', 5000)
        self.tours(1)
        self.assertEqual(self.g.coupable(self.s.procs, self.s.mono()), (None, 0))

    def test_filtre_sur_la_ligne_de_commande(self):
        self.garde(filtre='cible')
        self.s.ajouter(10, 'python3', 5000)
        self.s.ajouter(11, 'python3', 100)
        self.s.commandes[11] = 'python3 cible-42'
        self.tours(1)
        self.assertEqual(self.g.coupable(self.s.procs, self.s.mono())[0], 11)


class TestProteges(unittest.TestCase):
    def test_liste_integree(self):
        p = g.construire_proteges()
        for nom in ['systemd', 'systemd-journal', 'systemd-journald', 'systemd-logind', '(sd-pam)', 'dbus-daemon',
                    'dbus-broker', 'NetworkManager', 'wpa_supplicant', 'polkitd', 'gdm-session-wor', 'gdm3', 'sshd',
                    'cron', 'rsyslogd', 'udisksd', 'upowerd', 'accounts-daemon', 'snapd', 'dpkg', 'apt', 'apt-get',
                    'unattended-upgr', 'packagekitd', 'containerd', 'dockerd', 'login', 'agetty', 'runuser',
                    'gnome-shell', 'Xorg', 'pipewire-pulse', 'gnome-terminal-', 'busctl', 'notify-send', 'gagarine']:
            self.assertTrue(p.match(nom), nom)

    def test_programmes_ordinaires_non_proteges(self):
        p = g.construire_proteges()
        for nom in ['python3', 'node', 'firefox', 'java', 'cronos', 'sshd-fake', 'aptitude', 'loginx', 'code', 'php']:
            self.assertFalse(p.match(nom), nom)

    def test_supplement_utilisateur(self):
        p = g.construire_proteges('postgres|mysqld')
        self.assertTrue(p.match('postgres'))
        self.assertTrue(p.match('mysqld'))
        self.assertTrue(p.match('sshd'))
        self.assertFalse(p.match('postgres2'))


class TestLogique(Banc):
    def monter(self, **kw):
        self.garde(**kw)
        self.s.ajouter(10, 'fuite', 500)
        self.s.ajouter(11, 'calme', 300)

    def test_les_proteges_ne_sont_jamais_figes(self):
        self.garde(proteges='mon-service')
        self.s.ajouter(10, 'mon-service', 9000)
        self.s.ajouter(11, 'dockerd', 8000)
        self.s.ajouter(12, 'systemd-resolve', 8000)
        self.tours(30, pct=96)
        self.assertEqual(self.s.signaux, [])

    def test_sous_le_seuil_rien(self):
        self.monter()
        self.tours(5, pct=94.9)
        self.assertEqual(self.s.signaux, [])

    def test_seuil_fige_le_plus_vite(self):
        self.monter()
        for i in range(12):
            self.s.ajouter(10, 'fuite', 500 + i * 200)
            self.tours(1, pct=80)
        self.s.pct = 95.0
        self.g.tour()
        self.assertEqual(self.s.signaux, [(10, signal.SIGSTOP)])
        self.assertEqual(self.actions(), ['fige'])
        e = self.evenements()[0]
        self.assertEqual((e['pid'], e['nom'], e['utilisateur'], e['ram']), (10, 'fuite', 'u1000', 95.0))
        self.assertIn('commande', e)
        self.assertIn(10, self.g.figes)
        self.assertEqual(len(self.paquets), 1)   # le fil de bridage et de rangement est parti

    def test_grace_puis_arret_si_la_ram_ne_baisse_pas(self):
        self.monter(grace=10)
        self.tours(1, pct=96)
        self.paquets.clear()
        self.g.vidage.clear()   # rangement fini
        self.tours(9, pct=96)
        self.assertEqual(len(self.s.signaux), 1)   # toujours en grâce
        self.tours(2, pct=96)
        self.assertIn((10, signal.SIGKILL), self.s.signaux)
        self.assertEqual(self.actions(), ['fige', 'arrete'])
        self.assertIn('au-dessus de 95', self.evenements()[1]['raison'])

    def test_grace_longue_tant_que_le_rangement_n_est_pas_fini(self):
        self.monter(grace=10, vidage_max=60)
        self.tours(1, pct=96)
        self.tours(40, pct=96)   # vidage non fini : on attend jusqu'à vidage_max
        self.assertNotIn((10, signal.SIGKILL), self.s.signaux)
        self.tours(30, pct=96)
        self.assertIn((10, signal.SIGKILL), self.s.signaux)

    def test_pas_d_arret_si_la_ram_redescend(self):
        self.monter()
        self.tours(1, pct=96)
        self.g.vidage.clear()
        self.tours(20, pct=90)
        self.assertEqual(self.actions(), ['fige'])
        self.assertIn(10, self.g.figes)

    def test_figé_peu_en_ram_n_est_pas_arrete_au_seuil(self):
        """Une fois rangé (RSS < 256 Mo), le programme figé n'est plus la cible : on fige un autre programme."""
        self.monter(grace=2)
        self.tours(1, pct=96)
        self.g.vidage.clear()
        self.s.ajouter(10, 'fuite', 100, etat='T', swap_mo=400)
        self.tours(4, pct=96)
        self.assertNotIn((10, signal.SIGKILL), self.s.signaux)
        self.assertIn((11, signal.SIGSTOP), self.s.signaux)

    def test_urgence_arrete_sans_attendre(self):
        self.monter()
        self.tours(1, pct=98.5)
        self.assertEqual(self.s.signaux, [(10, signal.SIGKILL)])
        self.assertEqual(self.actions(), ['arrete'])
        self.assertIn('98', self.evenements()[0]['raison'])

    def test_urgence_vise_d_abord_le_fige_encore_en_ram(self):
        self.monter()
        self.tours(1, pct=96)
        self.g.vidage.clear()
        self.s.ajouter(10, 'fuite', 3000, etat='T')
        self.tours(1, pct=98.5)
        self.assertIn((10, signal.SIGKILL), self.s.signaux)

    def test_delai_fige(self):
        self.monter(delai_fige=20, grace=1000, vidage_max=1000)
        self.tours(1, pct=96)
        self.tours(15, pct=90)
        self.assertEqual(self.actions(), ['fige'])
        self.tours(8, pct=90)
        self.assertEqual(self.actions(), ['fige', 'arrete'])
        self.assertIn('20 s', self.evenements()[1]['raison'])

    def test_reprise_detectee_seulement_en_R_ou_S_apres_2_s(self):
        self.monter()
        self.tours(1, pct=96)
        self.s.procs[10] = self.s.procs[10]._replace(etat='D')
        self.tours(3, pct=90)
        self.assertIn(10, self.g.figes)    # D : la pause n'a pas encore pris
        self.s.procs[10] = self.s.procs[10]._replace(etat='S')
        self.tours(1, pct=90)
        self.assertNotIn(10, self.g.figes)
        self.assertEqual(self.actions(), ['fige', 'repris'])

    def test_reprise_trop_tot_ignoree(self):
        self.monter()
        self.tours(1, pct=96, pas=0.5)
        self.s.procs[10] = self.s.procs[10]._replace(etat='R')
        self.tours(1, pct=90)
        self.assertIn(10, self.g.figes)

    def test_pid_reutilise_n_est_plus_fige(self):
        self.monter()
        self.tours(1, pct=96)
        self.s.debuts[10] = 'autre'
        self.tours(1, pct=90)
        self.assertNotIn(10, self.g.figes)

    def test_repos_apres_un_arret(self):
        self.monter()
        self.tours(1, pct=99)
        self.s.ajouter(12, 'autre', 800)
        self.tours(1, pct=99, pas=0.5)   # moins de 1,5 s après l'arrêt : on laisse le noyau rendre la mémoire
        self.assertEqual(len(self.s.signaux), 1)

    def test_isoler_brider_puis_ranger(self):
        self.monter()
        self.g.isoler(10, 'd10', 500 * Mo, 'fuite')
        self.assertEqual(self.s.bridages, [10])
        self.assertEqual(self.s.rangements, [(10, 500 * Mo)])
        self.assertNotIn(10, self.g.vidage)
        self.assertEqual(self.actions(), [])

    def test_bridage_impossible_on_continue(self):
        self.monter()
        self.s.erreur_bridage = 'Access denied'
        self.g.vidage.add(10)
        self.g.isoler(10, 'd10', 500 * Mo, 'fuite')
        self.assertEqual(self.actions(), ['bridage-impossible'])
        self.assertEqual(self.evenements()[0]['erreur'], 'Access denied')
        self.assertEqual(self.s.rangements, [(10, 500 * Mo)])
        self.assertNotIn(10, self.g.vidage)

    def test_pas_de_rangement_si_le_pid_a_change(self):
        self.monter()
        self.g.isoler(10, 'autre-debut', 500 * Mo, 'fuite')
        self.assertEqual(self.s.rangements, [])

    def test_persistance_des_figes(self):
        self.monter()
        self.tours(1, pct=96)
        data = json.loads((self.dossier / 'figes.json').read_text())
        self.assertEqual(data['10']['nom'], 'fuite')
        self.assertEqual(oct((self.dossier / 'figes.json').stat().st_mode & 0o777), '0o644')
        autre = g.Garde(self.r, self.s, self.j, self.dossier / 'figes.json')
        self.assertIn(10, autre.figes)
        self.s.debuts[10] = 'autre'
        self.assertEqual(g.Garde(self.r, self.s, self.j, self.dossier / 'figes.json').figes, {})

    def test_erreur_de_lecture_ne_tue_pas_la_boucle(self):
        self.monter()
        appels = []

        def tour():
            appels.append(1)
            if len(appels) == 1:
                raise RuntimeError('boum')
            raise KeyboardInterrupt
        self.g.tour = tour
        pauses = []
        with self.assertRaises(KeyboardInterrupt):
            self.g.boucle(dormir=pauses.append)
        self.assertEqual(pauses, [0.25])   # l'erreur compte comme RAM pleine : on mesure vite

    def test_cadence_de_mesure(self):
        self.monter()
        self.s.pct = 80
        n = []
        self.g.tour = lambda: 86 if not n else (_ for _ in ()).throw(KeyboardInterrupt())
        pauses = []
        with self.assertRaises(KeyboardInterrupt):
            def dormir(x):
                pauses.append(x)
                n.append(1)
            self.g.boucle(dormir=dormir)
        self.assertEqual(pauses, [0.25])


class TestReglages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fichier(self, nom, texte):
        p = Path(self.tmp.name) / nom
        p.write_text(texte)
        return str(p)

    def test_valeurs_par_defaut_si_fichier_absent(self):
        r, av = g.charger_reglages(None, {'GAGARINE_CONFIG': '/nexiste/pas'})
        self.assertEqual(r, g.Reglages())
        self.assertEqual((r.seuil, r.urgence, r.vigilance, r.grace, r.vidage_max, r.delai_fige, r.reste_si_repris),
                         (95, 98, 85, 10, 60, 600, 256))
        self.assertTrue(r.notifications)
        self.assertTrue(av)

    def test_lecture_du_fichier(self):
        f = self.fichier('a.conf', '[gagarine]\nseuil=90\nurgence = 97.5\nnotifications=non\nproteges=foo|bar\n'
                                   'reste_si_repris=128\n')
        r, av = g.charger_reglages(f, {})
        self.assertEqual((r.seuil, r.urgence, r.reste_si_repris, r.notifications, r.proteges), (90, 97.5, 128, False, 'foo|bar'))
        self.assertEqual(av, [])

    def test_priorite_arg_puis_variable_config(self):
        a = self.fichier('a.conf', '[gagarine]\nseuil=91\n')
        b = self.fichier('b.conf', '[gagarine]\nseuil=92\n')
        self.assertEqual(g.charger_reglages(a, {'GAGARINE_CONFIG': b})[0].seuil, 91)
        self.assertEqual(g.charger_reglages(None, {'GAGARINE_CONFIG': b})[0].seuil, 92)
        self.assertEqual(g.chemin_config(None, {}), '/etc/gagarine.conf')

    def test_variables_passent_devant_le_fichier(self):
        a = self.fichier('a.conf', '[gagarine]\nseuil=91\ngrace=7\n')
        r, _ = g.charger_reglages(a, {'GAGARINE_SEUIL': '80.5', 'GAGARINE_NOTIFICATIONS': '0'})
        self.assertEqual((r.seuil, r.grace, r.notifications), (80.5, 7, False))

    def test_valeur_invalide_ignoree(self):
        a = self.fichier('a.conf', '[gagarine]\nseuil=beaucoup\nurgence=250\nproteges=(\nnotifications=peut-etre\ninconnue=1\n')
        r, av = g.charger_reglages(a, {})
        self.assertEqual(r, g.Reglages())
        self.assertEqual(len(av), 5)

    def test_fichier_sans_section(self):
        a = self.fichier('a.conf', 'seuil=90\n')
        r, av = g.charger_reglages(a, {})
        self.assertEqual(r.seuil, 95)
        self.assertTrue(av)

    def test_fichier_integre_documente_et_valide(self):
        conf = (Path(__file__).resolve().parent.parent / 'etc' / 'gagarine.conf').read_text()
        for cle in ('seuil', 'urgence', 'vigilance', 'grace', 'vidage_max', 'delai_fige', 'reste_si_repris',
                    'notifications', 'proteges'):
            self.assertIn(f'#{cle}=', conf)
        r, av = g.charger_reglages(str(Path(__file__).resolve().parent.parent / 'etc' / 'gagarine.conf'), {})
        self.assertEqual((r, av), (g.Reglages(), []))

    def test_dossier_etat(self):
        self.assertEqual(g.dossier_etat({'GAGARINE_ETAT': '/x'}, 0), Path('/x'))
        self.assertEqual(g.dossier_etat({}, 0), Path('/var/lib/gagarine'))
        self.assertEqual(g.dossier_etat({}, 1000, '/home/u'), Path('/home/u/.local/state/gagarine'))

    def test_dossier_etat_lecture(self):
        self.assertEqual(g.dossier_etat_lecture({}, 1000, '/home/u', lisible=lambda p, m: True), Path('/var/lib/gagarine'))
        self.assertEqual(g.dossier_etat_lecture({}, 1000, '/home/u', lisible=lambda p, m: False),
                         Path('/home/u/.local/state/gagarine'))
        self.assertEqual(g.dossier_etat_lecture({'GAGARINE_ETAT': '/x'}), Path('/x'))


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name) / 'etat'

    def test_format_de_l_evenement(self):
        j = g.Journal(self.d, heure=lambda: 0, sortie=False)
        j.ecrire('fige', pid=5, nom='x', utilisateur='u', commande='c', memoire=123, ram=95.5, raison=None)
        ligne = (self.d / 'evenements.jsonl').read_text()
        e = json.loads(ligne)
        self.assertEqual(e, {'date': '1970-01-01T00:00:00+0000', 'source': 'gagarine', 'action': 'fige', 'pid': 5,
                             'nom': 'x', 'utilisateur': 'u', 'commande': 'c', 'memoire': 123, 'ram': 95.5})
        self.assertTrue(ligne.endswith('\n') and ligne.count('\n') == 1)
        self.assertEqual((self.d / 'evenements.jsonl').stat().st_mode & 0o777, 0o644)

    def test_accents_conserves(self):
        j = g.Journal(self.d, sortie=False)
        j.ecrire('arrete', raison='figé depuis 10 min')
        self.assertIn('figé', (self.d / 'evenements.jsonl').read_text())

    def test_rotation_au_dela_de_la_taille_max(self):
        j = g.Journal(self.d, sortie=False, taille_max=300)
        for i in range(20):
            j.ecrire('fige', pid=i, nom='x' * 20)
        self.assertTrue((self.d / 'evenements.jsonl.1').exists())
        courant = (self.d / 'evenements.jsonl').read_text().splitlines()
        self.assertTrue(0 < len(courant) < 20)
        self.assertEqual(json.loads(courant[-1])['pid'], 19)   # le dernier événement est dans le fichier courant
        self.assertTrue((self.d / 'evenements.jsonl.1').read_text().strip())

    def test_taille_par_defaut_512_ko(self):
        self.assertEqual(g.TAILLE_JOURNAL, 512 * 1024)

    def test_impression_sur_la_sortie(self):
        import io
        sortie = io.StringIO()
        g.Journal(self.d, sortie=sortie).ecrire('demarrage')
        self.assertEqual(json.loads(sortie.getvalue())['action'], 'demarrage')


class TestMesures(unittest.TestCase):
    def test_ram_depuis_meminfo(self):
        t = 'MemTotal:       14000000 kB\nMemFree: 1 kB\nMemAvailable:    7000000 kB\n'
        self.assertAlmostEqual(g.ram_depuis_meminfo(t), 50.0)

    def test_lire_processus_du_poste(self):
        procs = g.lire_processus(os.getuid())
        self.assertIn(os.getppid() if os.getppid() in procs else os.getpid() + 0, list(procs) + [os.getppid()])
        self.assertNotIn(os.getpid(), procs)
        self.assertNotIn(1, procs)
        self.assertNotIn(2, procs)
        self.assertTrue(all(p.uid == os.getuid() for p in procs.values()))
        mien = g.lire_processus(os.getuid(), moi=-1)
        self.assertIn(os.getpid(), mien)
        self.assertGreater(mien[os.getpid()].rss, 0)

    def test_demarrage_et_commande_de_soi(self):
        self.assertTrue(g.demarrage(os.getpid()))
        self.assertIsNone(g.demarrage(2 ** 22 + 5))
        self.assertTrue(g.commande(os.getpid()))


class TestRangement(unittest.TestCase):
    MAPS = ('55d0-55e0 r--p 00000000 08:01 1 /usr/bin/x\n'
            '55e0-55f0 r-xp 00000000 08:01 1 /usr/bin/x\n'
            '55f0-5600 rw-p 00000000 08:01 1 /usr/bin/x\n'
            '5600-5800 rw-p 00000000 00:00 0 [heap]\n'
            '7f00-7f80 rw-p 00000000 00:00 0 \n'
            '7f80-7fa0 rw-s 00000000 00:01 5 /dev/shm/x\n'
            '7ffc-7fff rw-p 00000000 00:00 0 [stack]\n'
            '7fff-8000 r--p 00000000 00:00 0 [vvar]\n'
            '8000-8001 rw-p 00000000 00:00 0 [vdso]\n'
            'ffffffffff600000-ffffffffff601000 --xp 00000000 00:00 0 [vsyscall]\n')

    def test_zones_retenues(self):
        z = g.zones_a_ranger(self.MAPS)
        self.assertEqual(z, [(0x55f0, 0x5600), (0x5600, 0x5800), (0x7f00, 0x7f80), (0x7ffc, 0x7fff)])

    def test_ranger_precis_paquets_de_1024(self):
        maps = ''.join(f'{0x1000 * i:x}-{0x1000 * i + 0x800:x} rw-p 00000000 00:00 0 \n' for i in range(1, 2501))
        appels = []

        def appel(num, *args):
            appels.append((num, [getattr(a, 'value', a) for a in args]))
            return 100 if num == g.SYS_PROCESS_MADVISE else 77
        fermes = []
        total = g.ranger_precis(42, appel, lambda p: maps, fermes.append)
        self.assertEqual(appels[0], (434, [42, 0]))
        madv = [a for a in appels if a[0] == 440]
        self.assertEqual([a[1][2] for a in madv], [1024, 1024, 452])
        self.assertTrue(all(a[1][0] == 77 and a[1][3] == 21 and a[1][4] == 0 for a in madv))
        self.assertEqual(total, 300)
        self.assertEqual(fermes, [77])

    def test_iovec_construit_depuis_les_zones(self):
        vus = []

        def appel(num, *args):
            if num == g.SYS_PROCESS_MADVISE:
                import ctypes
                tab = ctypes.cast(args[1], ctypes.POINTER(g.Iovec))
                vus.extend((tab[i].base, tab[i].longueur) for i in range(args[2].value))
            return 3
        g.ranger_precis(1, appel, lambda p: self.MAPS, lambda fd: None)
        self.assertEqual(vus, [(0x55f0, 0x10), (0x5600, 0x200), (0x7f00, 0x80), (0x7ffc, 3)])

    def test_refus_des_le_premier_paquet_leve(self):
        def appel(num, *args):
            if num == g.SYS_PROCESS_MADVISE:
                raise PermissionError(1, 'refusé')
            return 5
        fermes = []
        with self.assertRaises(OSError):
            g.ranger_precis(1, appel, lambda p: self.MAPS, fermes.append)
        self.assertEqual(fermes, [5])

    def test_pidfd_open_refuse_leve(self):
        def appel(num, *args):
            raise OSError(38, 'ENOSYS')
        with self.assertRaises(OSError):
            g.ranger_precis(1, appel, lambda p: self.MAPS, lambda fd: None)

    def test_aucune_zone_leve(self):
        with self.assertRaises(OSError):
            g.ranger_precis(1, lambda *a: 1, lambda p: '', lambda fd: None)

    def test_repli_sur_memory_reclaim_sans_privileges(self):
        s = g.Systeme(g.Reglages(), root=False)
        with tempfile.TemporaryDirectory() as d:
            s.ranger(1, 12345, Path(d))   # écrit memory.reclaim dans le dossier d'origine
            self.assertEqual((Path(d) / 'memory.reclaim').read_text(), '12345')

    def test_repli_si_process_madvise_echoue_en_root(self):
        s = g.Systeme(g.Reglages(), root=True)
        vieux = g.ranger_precis
        g.ranger_precis = lambda pid: (_ for _ in ()).throw(OSError(1, 'non'))
        try:
            with tempfile.TemporaryDirectory() as d:
                s.ranger(1, 999, Path(d))
                self.assertEqual((Path(d) / 'memory.reclaim').read_text(), '999')
        finally:
            g.ranger_precis = vieux


class TestBridageEtNotifications(unittest.TestCase):
    CG_USER = '0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-x.scope\n'
    CG_SYS = '0::/system.slice/ssh.service\n'

    def test_root_sous_user_service(self):
        c = g.commande_bridage(42, self.CG_USER, True, 256, 'u.scope', nom_de_uid=lambda u: 'moi')
        self.assertEqual(c[:5], ['busctl', '--machine=moi@.host', '--user', 'call', 'org.freedesktop.systemd1'])
        self.assertIn('StartTransientUnit', c)

    def test_root_autre_processus_gestionnaire_systeme(self):
        c = g.commande_bridage(42, self.CG_SYS, True, 256, 'u.scope')
        self.assertEqual(c[:2], ['busctl', 'call'])

    def test_sans_privileges(self):
        c = g.commande_bridage(42, self.CG_USER, False, 256, 'u.scope')
        self.assertEqual(c[:3], ['busctl', '--user', 'call'])

    def test_parametres_du_scope(self):
        c = g.commande_bridage(4242, self.CG_SYS, True, 128, 'gagarine-fige-4242-1.scope')
        i = c.index('ssa(sv)a(sa(sv))')
        self.assertEqual(c[i + 1:i + 3], ['gagarine-fige-4242-1.scope', 'fail'])
        self.assertEqual(c[c.index('PIDs'):c.index('PIDs') + 4], ['PIDs', 'au', '1', '4242'])
        self.assertEqual(c[c.index('MemoryMax') + 2], str(128 * Mo))
        self.assertEqual(c[c.index('MemorySwapMax') + 1:c.index('MemorySwapMax') + 3], ['t', '0'])

    SORTIE = {
        ('loginctl', 'list-sessions', '--no-legend'): '  2 1000 charles seat0 tty2\n  c1 120 gdm seat0 tty1\n  4 1001 bob - pts/0\n'
                                                      '  5 1000 charles seat0 tty3\n',
        ('loginctl', 'show-session', '2', '-p', 'Type', '-p', 'Active'): 'Type=wayland\nActive=yes\n',
        ('loginctl', 'show-session', '4', '-p', 'Type', '-p', 'Active'): 'Type=tty\nActive=yes\n',
        ('loginctl', 'show-session', '5', '-p', 'Type', '-p', 'Active'): 'Type=x11\nActive=no\n',
    }

    def test_sessions_graphiques_actives(self):
        self.assertEqual(g.sessions_graphiques(lambda c: self.SORTIE.get(tuple(c), '')), [(1000, 'charles')])

    def test_notification_root_par_session(self):
        cmds = g.commandes_notification('T', 'X', True, True, [(1000, 'charles'), (1001, 'bob')])
        self.assertEqual(len(cmds), 2)
        self.assertEqual(cmds[0][:4], ['runuser', '-u', 'charles', '--'])
        self.assertIn('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', cmds[0])
        self.assertIn('arreter=Arrêter', cmds[0])
        self.assertIn('reprendre=Reprendre', cmds[0])
        self.assertEqual(cmds[0][-2:], ['T', 'X'])

    def test_notification_sans_privileges_directe_et_sans_boutons(self):
        cmds = g.commandes_notification('T', 'X', False, False, [])
        self.assertEqual(cmds[0][0], 'notify-send')
        self.assertNotIn('-A', cmds[0])

    def test_reponse_ignoree_si_le_pid_a_change(self):
        s = g.Systeme(g.Reglages(), root=False)
        signaux = []
        s.tuer = lambda pid, sig: signaux.append((pid, sig))
        vieux = g.subprocess.run

        class R:
            stdout = 'arreter\n'
        g.subprocess.run = lambda *a, **k: R()
        try:
            s._attendre_choix(['x'], os.getpid(), 'pas-le-bon-debut')
            self.assertEqual(signaux, [])
            s._attendre_choix(['x'], os.getpid(), g.demarrage(os.getpid()))
            self.assertEqual(signaux, [(os.getpid(), signal.SIGKILL)])
        finally:
            g.subprocess.run = vieux

    def test_etat_service(self):
        appels = []

        def lancer(c):
            appels.append(c)
            return 'active\n'
        self.assertEqual(g.etat_service(lancer, root=True), [('gagarine', 'active')])
        self.assertEqual([c[-1] for c in appels], ['gagarine'])
        self.assertEqual(len(g.etat_service(lancer, root=False)), 2)
        self.assertIn(['systemctl', '--user', 'is-active', 'garde-ram'], appels)


class TestEssaiEtCommandes(unittest.TestCase):
    def test_reglages_d_essai(self):
        e = g.reglages_essai(50.0)
        self.assertEqual((e['GAGARINE_SEUIL'], e['GAGARINE_URGENCE'], e['GAGARINE_GRACE'], e['GAGARINE_DELAI_FIGE']),
                         ('55.0', '64.0', '3', '20'))
        self.assertEqual(e['GAGARINE_NOTIFICATIONS'], 'non')

    def test_verdict_complet(self):
        ech = [(0, 'S', 100, 0), (1, 'S', 600, 0), (2, 'T', 650, 0), (3, 'T', 40, 600)]
        v = g.verdict_essai(ech, ['fige', 'arrete'], -signal.SIGKILL)
        self.assertEqual([ok for _, ok, _ in v], [True, True, True])

    def test_verdict_pas_range(self):
        ech = [(0, 'S', 600, 0), (1, 'T', 650, 0), (2, 'T', 640, 0)]
        v = g.verdict_essai(ech, ['fige', 'arrete'], -signal.SIGKILL)
        self.assertEqual([ok for _, ok, _ in v], [True, False, True])

    def test_verdict_jamais_fige(self):
        v = g.verdict_essai([(0, 'S', 600, 0)], [], 0)
        self.assertEqual([ok for _, ok, _ in v], [False, False, False])

    def test_version_et_aide(self):
        self.assertEqual(g.VERSION, '1.0.0')
        import subprocess
        for a in ('version', '--version'):
            r = subprocess.run([str(CHEMIN), a], capture_output=True, text=True)
            self.assertEqual(r.stdout, 'gagarine 1.0.0\n')
        r = subprocess.run([str(CHEMIN), '--help'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        for mot in ('surveiller', 'etat', 'reprendre', 'arreter', 'essai', 'version'):
            self.assertIn(mot, r.stdout)
        self.assertEqual(subprocess.run([str(CHEMIN), 'nimportequoi'], capture_output=True).returncode, 2)

    def test_reprendre_refuse_un_programme_non_fige(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([str(CHEMIN), 'arreter', str(os.getpid())], capture_output=True, text=True,
                               env=dict(os.environ, GAGARINE_ETAT=d))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('ne figure pas', r.stderr)


if __name__ == '__main__':
    unittest.main()
