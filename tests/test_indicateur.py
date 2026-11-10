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

    def test_ligne_file_avec_travaux_en_cours(self):
        self.assertEqual(L.ligne_file(3, 2), "File d'attente : 3 travaux (2 en cours)")
        self.assertEqual(L.ligne_file(3, 0), "File d'attente : 3 travaux")
        self.assertEqual(L.ligne_file(0, 2), "File d'attente : aucun travail")
        self.assertEqual(L.en_cours_file({'en_cours': 2, 'travaux': []}), 2)
        self.assertEqual(L.en_cours_file({'travaux': [{'etat': 'en cours'}, {'etat': 'en attente'}, {'etat': 'en cours'}]}), 2)
        self.assertIsNone(L.en_cours_file({'travaux': [{'etat': 'en attente'}]}))
        self.assertIsNone(L.en_cours_file(None))
        self.assertIsNone(L.en_cours_file({'en_cours': 'x', 'travaux': 'x'}))

    def test_fuites(self):
        lente, rapide = {'pid': 2, 'nom': 'node', 'pente_mo_min': 120}, {'pid': 1, 'nom': 'python3', 'pente_mo_min': 210}
        self.assertEqual(L.fuites({'fuites': [lente, rapide, {'nom': 'sans pid'}, 'x']}), [rapide, lente])
        self.assertEqual(L.fuites({'fuites': 'x'}), [])
        self.assertEqual(L.fuites(None), [])
        self.assertEqual(L.ligne_fuite([rapide]), 'Fuite : python3 (+210 Mo/min)')
        self.assertEqual(L.ligne_fuite([rapide, lente]), 'Fuite : python3 (+210 Mo/min) et 1 autre')
        self.assertEqual(L.ligne_fuite([rapide, lente, lente]), 'Fuite : python3 (+210 Mo/min) et 2 autres')
        self.assertEqual(L.ligne_fuite([{'pid': 1}]), 'Fuite : ?')
        self.assertIsNone(L.ligne_fuite([]))

    def test_menu_avec_fuite_et_file_parallele(self):
        etat = dict(ETAT, fuites=[{'pid': 9, 'nom': 'python3', 'pente_mo_min': 210}])
        menu = L.contenu_menu(75.2, etat, {'travaux': [{}, {}, {}], 'en_cours': 2})
        self.assertIn(('info', 'Fuite : python3 (+210 Mo/min)'), menu)
        self.assertIn(('info', "File d'attente : 3 travaux (2 en cours)"), menu)
        sans = L.contenu_menu(75.2, ETAT, {'travaux': [{}]})      # cœur 1.2.x : menu inchangé
        self.assertFalse([e for e in sans if e[0] == 'info' and e[1].startswith('Fuite')])
        self.assertIn(('info', "File d'attente : 1 travail"), sans)

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


class TestMenage(unittest.TestCase):
    CANDIDATS = {'auto': True, 'delai_min': 30, 'candidats': [
        {'id': '5551', 'type': 'gradle', 'nom': 'Démon Gradle', 'pid': 5551, 'memoire_mo': 3100, 'inactif_depuis_s': 2640,
         'action': 'arreter', 'auto': True},
        {'id': 'ollama:llama3.1:8b', 'type': 'ollama', 'nom': 'llama3.1:8b', 'pid': None, 'memoire_mo': 5400,
         'inactif_depuis_s': 1260, 'action': 'decharger', 'auto': True}]}

    def test_ligne(self):
        c = L.candidats_menage(self.CANDIDATS)
        self.assertEqual([x['id'] for x in c], ['ollama:llama3.1:8b', '5551'])     # les plus gros d'abord
        self.assertEqual(L.ligne_menage(c), 'Ménage : 2 inactifs (8,3 Go)')
        self.assertEqual(L.ligne_menage(c[:1]), 'Ménage : 1 inactif (5,3 Go)')
        self.assertIsNone(L.ligne_menage([]))

    def test_entree_du_menu(self):
        menu = L.contenu_menu(75.2, ETAT, {'travaux': []}, 'moi', self.CANDIDATS)
        entree = next(e for e in menu if e[0] == 'menage')
        self.assertEqual(entree[1], 'Ménage : 2 inactifs (8,3 Go)')
        self.assertEqual(entree[2][0], ('ollama:llama3.1:8b', 'Décharger llama3.1:8b (5,3 Go, inactif depuis 21 min)', 'decharger'))
        self.assertEqual(entree[2][1], ('5551', 'Arrêter Démon Gradle (3,0 Go, inactif depuis 44 min)', 'arreter'))
        # l'entrée se place avant les actions finales, le menu reste comparable (pas de reconstruction inutile)
        self.assertLess(menu.index(entree), menu.index(('action', 'ouvrir', 'Ouvrir Gagarine')))
        self.assertEqual(menu, L.contenu_menu(75.2, ETAT, {'travaux': []}, 'moi', self.CANDIDATS))

    def test_pas_d_entree_sans_candidat_ni_commande(self):
        for menage in (None, {}, {'candidats': []}, {'candidats': 'x'}, {'candidats': [3, {'nom': 'sans id'}]}):
            menu = L.contenu_menu(75.2, ETAT, None, 'moi', menage)
            self.assertFalse([e for e in menu if e[0] == 'menage'], menage)
        self.assertEqual(L.contenu_menu(75.2, ETAT, None, 'moi'), L.contenu_menu(75.2, ETAT, None, 'moi', None))

    def test_identifiants_dangereux_ignores(self):
        c = L.candidats_menage({'candidats': [{'id': '--tout', 'memoire_mo': 9}, {'id': 'a b'}, {'id': 'ok', 'memoire_mo': 1}]})
        self.assertEqual([x['id'] for x in c], ['ok'])

    def test_commande(self):
        self.assertEqual(L.commande_menage('5551'), ['gagarine', 'menage', 'arreter', '5551'])
        self.assertEqual(L.commande_menage('ollama:x:7b', ['/x/g']), ['/x/g', 'menage', 'arreter', 'ollama:x:7b'])
        for mauvais in ('', '-f', 'a b'):
            with self.assertRaises(ValueError):
                L.commande_menage(mauvais)

    def test_duree(self):
        self.assertEqual([L.duree(x) for x in (5, 125, 3900, 200000, None)], ['5 s', '2 min', '1 h 05', '2 j', '0 s'])

    def test_action(self):
        self.assertEqual(L.action_menage({'action': 'decharger'}), 'decharger')
        self.assertEqual(L.action_menage({'type': 'lmstudio'}), 'decharger')
        self.assertEqual(L.action_menage({'type': 'gradle'}), 'arreter')


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

    def test_fuites_du_faux(self):
        env = dict(os.environ, FAUX_GAGARINE_FUITE='oui')
        etat = L.lire_json(subprocess.run([FAUX, 'etat', '--json'], env=env, capture_output=True, text=True).stdout)
        fichier = L.lire_json(subprocess.run([FAUX, 'file', '--etat', '--json'], env=env, capture_output=True, text=True).stdout)
        self.assertEqual(L.ligne_fuite(L.fuites(etat)), 'Fuite : python3 (+210 Mo/min) et 1 autre')
        self.assertEqual(L.ligne_file(L.travaux_en_file(fichier), L.en_cours_file(fichier)),
                         "File d'attente : 3 travaux (2 en cours)")
        ancien = L.lire_json(self.sortie('normal', 'etat', '--json').stdout)       # sans la variable : cœur 1.2.x
        self.assertEqual(L.fuites(ancien), [])

    def test_menage_du_faux(self):
        m = L.lire_json(self.sortie('normal', 'menage', '--json').stdout)
        self.assertEqual(len(L.candidats_menage(m)), 2)
        self.assertEqual(L.candidats_menage(L.lire_json(
            subprocess.run([FAUX, 'menage', '--json'], env=dict(os.environ, FAUX_GAGARINE_MENAGE='vide'),
                           capture_output=True, text=True).stdout)), [])
        absent = subprocess.run([FAUX, 'menage', '--json'], env=dict(os.environ, FAUX_GAGARINE_MENAGE='absent'),
                                capture_output=True, text=True)
        self.assertEqual(absent.returncode, 2)

    def test_pannes(self):
        self.assertIsNone(L.lire_json(self.sortie('invalide', 'etat', '--json').stdout))
        self.assertNotEqual(self.sortie('panne', 'etat', '--json').returncode, 0)


if __name__ == '__main__':
    unittest.main()
