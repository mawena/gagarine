"""Tests de la logique de l'indicateur (sans GTK) : niveaux, libellés, menu, surcharge d'autostart."""
import os
import subprocess
import sys
import tempfile
import unittest

from gagarine.indicateur import logique as L

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAUX = os.path.join(RACINE, 'tests', 'donnees_indicateur', 'faux-gagarine')
ETAT = {'ram': {'pourcent': 75.2, 'total_mo': 14600, 'disponible_mo': 3620},
        'swap': [{'taille_mo': 15237, 'utilise_mo': 0}, {'taille_mo': 10239, 'utilise_mo': 6041}],
        'reglages': {'seuil': 95, 'vigilance': 85}, 'figes': []}
FIGE = {'pid': 4242, 'nom': 'python3', 'memoire_mo': 9100, 'utilisateur': 'moi'}


class TestNiveau(unittest.TestCase):
    def test_seuils(self):
        self.assertEqual(L.niveau(75, ETAT), L.NORMAL)
        self.assertEqual(L.niveau(85, ETAT), L.VIGILANCE)
        self.assertEqual(L.niveau(94.9, ETAT), L.VIGILANCE)
        self.assertEqual(L.niveau(95, ETAT), L.ALERTE)

    def test_programme_fige_donne_alerte(self):
        self.assertEqual(L.niveau(10, dict(ETAT, figes=[FIGE])), L.ALERTE)

    def test_reglages_personnalises(self):
        etat = {'reglages': {'seuil': 80, 'vigilance': 60}}
        self.assertEqual(L.niveau(65, etat), L.VIGILANCE)
        self.assertEqual(L.niveau(80, etat), L.ALERTE)

    def test_sans_etat_valeurs_par_defaut(self):
        self.assertEqual(L.niveau(90, None), L.VIGILANCE)
        self.assertEqual(L.niveau(96, {}), L.ALERTE)
        self.assertEqual(L.niveau(None, None), L.NORMAL)

    def test_reglages_invalides(self):
        self.assertEqual(L.niveau(90, {'reglages': {'vigilance': 'x', 'seuil': None}}), L.VIGILANCE)
        self.assertEqual(L.niveau(90, {'reglages': 'n'}), L.VIGILANCE)

    def test_icones(self):
        self.assertEqual(L.nom_icone(L.ALERTE), 'gagarine-indicateur-alerte-symbolic')
        self.assertEqual(L.nom_icone('inconnu'), 'gagarine-indicateur-normal-symbolic')

    def test_fichiers_icones_existent(self):
        for nom in L.ICONES.values():
            self.assertTrue(os.path.exists(os.path.join(RACINE, 'data/icons/hicolor/scalable/status', nom + '.svg')))


class TestLibelles(unittest.TestCase):
    def test_etiquette(self):
        self.assertEqual(L.etiquette(75.2), '75 %')
        self.assertEqual(L.etiquette(None), '-- %')

    def test_taille(self):
        self.assertEqual(L.taille_mo(512), '512 Mo')
        self.assertEqual(L.taille_mo(3686), '3,6 Go')

    def test_ram_depuis_meminfo(self):
        self.assertAlmostEqual(L.ram_depuis_meminfo('MemTotal: 1000 kB\nMemFree: 1 kB\nMemAvailable: 250 kB\n'), 75)
        self.assertIsNone(L.ram_depuis_meminfo('rien'))
        self.assertIsNone(L.ram_depuis_meminfo('MemTotal: 0 kB\nMemAvailable: 0 kB\n'))

    def test_lignes(self):
        self.assertEqual(L.ligne_ram(75.2, ETAT), 'RAM : 75 % (10,7 Go utilisés sur 14,3 Go)')
        self.assertEqual(L.ligne_ram(75.2, None), 'RAM : 75 %')
        self.assertEqual(L.ligne_swap(ETAT), 'Swap : 5,9 Go utilisés sur 24,9 Go')
        self.assertEqual(L.ligne_swap({'swap': []}), 'Swap : aucun')
        self.assertIsNone(L.ligne_swap(None))
        self.assertEqual(L.ligne_file(0), "File d'attente : aucun travail")
        self.assertEqual(L.ligne_file(1), "File d'attente : 1 travail")
        self.assertEqual(L.ligne_file(3), "File d'attente : 3 travaux")
        self.assertEqual(L.ligne_file(None), "File d'attente : inconnue")

    def test_travaux_en_file(self):
        self.assertEqual(L.travaux_en_file({'travaux': [{}, {}]}), 2)
        self.assertIsNone(L.travaux_en_file({'travaux': 'x'}))
        self.assertIsNone(L.travaux_en_file(None))


class TestLecture(unittest.TestCase):
    def test_json(self):
        self.assertEqual(L.lire_json(b'{"a": 1}'), {'a': 1})
        self.assertIsNone(L.lire_json('pas du json'))
        self.assertIsNone(L.lire_json('[1]'))
        self.assertIsNone(L.lire_json(None))
        self.assertIsNone(L.lire_json(b'\xff\xfe'))

    def test_commande(self):
        self.assertEqual(L.commande_gagarine({}), ['gagarine'])
        self.assertEqual(L.commande_gagarine({'GAGARINE_COMMANDE': '/a b/c --x'}), ['/a', 'b/c', '--x'])
        self.assertEqual(L.commande_gagarine({'GAGARINE_COMMANDE': "'oups"}), ['gagarine'])

    def test_figes_ignore_les_entrees_invalides(self):
        self.assertEqual(L.figes({'figes': [FIGE, 'x', {'nom': 'sans pid'}]}), [FIGE])
        self.assertEqual(L.figes({'figes': 'x'}), [])


class TestMenu(unittest.TestCase):
    def test_sans_fige(self):
        menu = L.contenu_menu(75.2, ETAT, {'travaux': []}, 'moi')
        self.assertIn(('info', 'Aucun programme figé'), menu)
        self.assertIn(('info', "File d'attente : aucun travail"), menu)
        self.assertEqual(menu[-2:], [('action', 'ouvrir', 'Ouvrir Gagarine'),
                                     ('action', 'masquer', "Masquer l'indicateur")])

    def test_avec_fige(self):
        menu = L.contenu_menu(96, dict(ETAT, figes=[FIGE, dict(FIGE, pid=7, utilisateur='autre')]), {'travaux': [{}]}, 'moi')
        figes = [e for e in menu if e[0] == 'fige']
        self.assertEqual(figes[0], ('fige', 4242, 'python3 (PID 4242), 8,9 Go', True))
        self.assertFalse(figes[1][3])
        self.assertIn(('info', "File d'attente : 1 travail"), menu)

    def test_menu_degrade_sans_donnees(self):
        menu = L.contenu_menu(None, None, None)
        self.assertEqual(menu[0], ('info', 'RAM : -- %'))
        self.assertIn(('action', 'ouvrir', 'Ouvrir Gagarine'), menu)

    def test_menu_comparable(self):
        self.assertEqual(L.contenu_menu(75.2, ETAT, None, 'moi'), L.contenu_menu(75.2, ETAT, None, 'moi'))
        self.assertNotEqual(L.contenu_menu(76, ETAT, None), L.contenu_menu(75, ETAT, None))

    def test_commande_action(self):
        self.assertEqual(L.commande_action('reprendre', 12, True), ['gagarine', 'reprendre', '12'])
        self.assertEqual(L.commande_action('arreter', 12, True, ['/x/g']), ['/x/g', 'arreter', '12'])
        self.assertEqual(L.commande_action('arreter', 12, False), ['pkexec', '/usr/bin/gagarine', 'arreter', '12'])
        with self.assertRaises(ValueError):
            L.commande_action('detruire', 1, True)


class TestSurcharge(unittest.TestCase):
    def test_masquer_puis_afficher(self):
        with tempfile.TemporaryDirectory() as d:
            env = {'XDG_CONFIG_HOME': d}
            chemin = os.path.join(d, 'autostart', L.IDENTIFIANT_AUTOSTART)
            self.assertEqual(L.chemin_surcharge(env), chemin)
            self.assertFalse(L.masque(env))
            L.masquer(env)
            self.assertTrue(os.path.exists(chemin))
            with open(chemin) as f:
                self.assertIn('Hidden=true', f.read())
            self.assertTrue(L.masque(env))
            L.masquer(env)  # idempotent
            L.afficher(env)
            self.assertFalse(os.path.exists(chemin))
            L.afficher(env)  # rien à enlever : pas d'erreur

    def test_chemin_par_defaut(self):
        self.assertEqual(L.chemin_surcharge({'HOME': '/h'}), '/h/.config/autostart/' + L.IDENTIFIANT_AUTOSTART)

    def test_option_en_ligne_de_commande(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, XDG_CONFIG_HOME=d)
            lanceur = os.path.join(RACINE, 'bin', 'gagarine-indicateur')
            r = subprocess.run([sys.executable, lanceur, '--masquer'], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(L.masque(env))
            subprocess.run([sys.executable, lanceur, '--afficher'], env=env, check=True, capture_output=True)
            self.assertFalse(L.masque(env))


class TestFauxGagarine(unittest.TestCase):
    def sortie(self, mode, *args):
        r = subprocess.run([FAUX, *args], env=dict(os.environ, FAUX_GAGARINE_MODE=mode), capture_output=True, text=True)
        return r

    def test_contrats(self):
        etat = L.lire_json(self.sortie('fige', 'etat', '--json').stdout)
        self.assertEqual(L.niveau(50, etat), L.ALERTE)
        self.assertEqual(L.niveau(90, L.lire_json(self.sortie('normal', 'etat', '--json').stdout)), L.VIGILANCE)
        self.assertEqual(L.travaux_en_file(L.lire_json(self.sortie('normal', 'file', '--etat', '--json').stdout)), 1)

    def test_pannes(self):
        self.assertIsNone(L.lire_json(self.sortie('invalide', 'etat', '--json').stdout))
        self.assertNotEqual(self.sortie('panne', 'etat', '--json').returncode, 0)


if __name__ == '__main__':
    unittest.main()
