"""Tests du garde : choix du coupable, protégés et décisions sur des mesures simulées."""
import json
import signal
import unittest

from gagarine import systeme as sy
from gagarine.garde import Garde
from tests.commun import Banc, Mo


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
        p = sy.construire_proteges()
        for nom in ['systemd', 'systemd-journal', 'systemd-journald', 'systemd-logind', '(sd-pam)', 'dbus-daemon',
                    'dbus-broker', 'NetworkManager', 'wpa_supplicant', 'polkitd', 'gdm-session-wor', 'gdm3', 'sshd',
                    'cron', 'rsyslogd', 'udisksd', 'upowerd', 'accounts-daemon', 'snapd', 'dpkg', 'apt', 'apt-get',
                    'unattended-upgr', 'packagekitd', 'containerd', 'dockerd', 'login', 'agetty', 'runuser',
                    'gnome-shell', 'Xorg', 'pipewire-pulse', 'gnome-terminal-', 'busctl', 'notify-send', 'gagarine']:
            self.assertTrue(p.match(nom), nom)

    def test_programmes_ordinaires_non_proteges(self):
        p = sy.construire_proteges()
        for nom in ['python3', 'node', 'firefox', 'java', 'cronos', 'sshd-fake', 'aptitude', 'loginx', 'code', 'php']:
            self.assertFalse(p.match(nom), nom)

    def test_supplement_utilisateur(self):
        p = sy.construire_proteges('postgres|mysqld')
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
        autre = Garde(self.r, self.s, self.j, self.dossier / 'figes.json')
        self.assertIn(10, autre.figes)
        self.s.debuts[10] = 'autre'
        self.assertEqual(Garde(self.r, self.s, self.j, self.dossier / 'figes.json').figes, {})

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
