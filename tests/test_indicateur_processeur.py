"""Tests de la partie « processeur » (1.4.0) de l'indicateur, sans GTK : /proc/stat, moyenne glissante, étiquette, menu."""
import os
import subprocess
import unittest

from gagarine.indicateur import logique as L

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAUX = os.path.join(RACINE, 'tests', 'donnees_indicateur', 'faux-gagarine')
PCT = ' %'     # espace insécable fine avant le signe
DEG = ' °C'
PROC = {'pourcent': 23.4, 'charge': [2.41, 1.98, 1.5], 'temperature': 72.0,
        'plus_actifs': [{'pid': 9, 'nom': 'chrome', 'coeurs': 1.4}, {'pid': 4242, 'nom': 'ffmpeg', 'coeurs': 6.21}]}
ETAT = {'ram': {'total_mo': 14600, 'disponible_mo': 3620}, 'swap': [], 'figes': [], 'processeur': PROC}


class LireStat(unittest.TestCase):
    def test_releve(self):
        # 10100 = user+nice+system+idle+iowait+irq+softirq+steal ; occupé = 10100 - 8000 - 500
        self.assertEqual(L.lire_cpu_stat('cpu  1000 100 400 8000 500 20 30 50 7 3\ncpu0 1 1 1 1 1 1 1 1\n'), (1600, 10100))
        self.assertEqual(L.lire_cpu_stat('cpu  10 0 5 80 5\n'), (15, 100))      # anciens noyaux

    def test_illisible(self):
        for texte in (None, '', 'rien', 'cpu0 1 2 3 4\n', 'cpu  a b c\n'):
            self.assertIsNone(L.lire_cpu_stat(texte), texte)

    def test_moyenne_glissante(self):
        # la moyenne couvre le plus ancien et le plus récent relevé, pas seulement la dernière seconde
        self.assertEqual(L.cpu_glissant([(0, 0), (100, 100), (150, 200), (300, 300)]), 100.0)
        self.assertAlmostEqual(L.cpu_glissant([(0, 100), (50, 200), (100, 300)]), 50.0)
        self.assertAlmostEqual(L.cpu_glissant([(0, 0), (0, 100), (0, 200), (60, 300)]), 20.0)
        self.assertIsNone(L.cpu_glissant([(5, 100)]))        # un seul relevé
        self.assertIsNone(L.cpu_glissant([]))
        self.assertIsNone(L.cpu_glissant([(5, 100), (5, 100)]))      # aucun tick écoulé
        self.assertEqual(L.cpu_glissant([(0, 0), (500, 100)]), 100.0)    # borné


class Textes(unittest.TestCase):
    def test_etiquette_de_la_barre(self):
        self.assertEqual(L.etiquette_barre(77.3, 23.4), f'RAM 77{PCT} · CPU 23{PCT}')
        self.assertEqual(L.etiquette_barre(77.3, None), f'RAM 77{PCT}')          # CPU pas encore mesuré
        self.assertEqual(L.etiquette_barre(None, 5), f'RAM -- % · CPU 5{PCT}')
        self.assertGreaterEqual(len(L.ETIQUETTE_LARGEUR), len(L.etiquette_barre(100, 100)))

    def test_coeurs(self):
        self.assertEqual(L.coeurs(6.21), '6,2 cœurs')
        self.assertEqual(L.coeurs(0.5), '0,5 cœur')

    def test_ligne_processeur(self):
        self.assertEqual(L.ligne_processeur(23.4, ETAT), f'Processeur : 23{PCT} · charge 2,4 · 72{DEG}')
        self.assertEqual(L.ligne_processeur(23.4, None), f'Processeur : 23{PCT}')           # cœur 1.3 : mesure locale seule
        self.assertEqual(L.ligne_processeur(None, ETAT), f'Processeur : 23{PCT} · charge 2,4 · 72{DEG}')   # secours : service
        self.assertIsNone(L.ligne_processeur(None, {}))
        self.assertEqual(L.ligne_processeur(5, {'processeur': {'charge': 'x', 'temperature': None}}), f'Processeur : 5{PCT}')

    def test_plus_actif(self):
        self.assertEqual(L.ligne_plus_actif(ETAT), 'Plus actif : ffmpeg (6,2 cœurs)')
        bas = {'processeur': {'plus_actifs': [{'pid': 1, 'nom': 'a', 'coeurs': 0.49}]}}
        self.assertIsNone(L.ligne_plus_actif(bas))                         # au moins 0,5 cœur
        limite = {'processeur': {'plus_actifs': [{'pid': 1, 'nom': 'a', 'coeurs': 0.5}]}}
        self.assertEqual(L.ligne_plus_actif(limite), 'Plus actif : a (0,5 cœur)')
        for etat in (None, {}, {'processeur': 'x'}, {'processeur': {'plus_actifs': 'x'}}, {'processeur': {'plus_actifs': [{}]}}):
            self.assertIsNone(L.ligne_plus_actif(etat))

    def test_infobulle(self):
        self.assertEqual(L.tooltip(L.NORMAL, 0), 'Gagarine : RAM normale')
        self.assertEqual(L.tooltip(L.NORMAL, 0, 23.4), f'Gagarine : RAM normale · processeur 23{PCT}')
        self.assertEqual(L.tooltip(L.ALERTE, 2, 90), f'Gagarine : 2 programmes figés · processeur 90{PCT}')


class Menu(unittest.TestCase):
    def test_lignes_apres_la_ram_et_le_swap(self):
        menu = L.contenu_menu(75.2, ETAT, None, cpu=23.4)
        infos = [e[1] for e in menu if e[0] == 'info']
        self.assertTrue(infos[0].startswith('RAM :'))
        self.assertEqual(infos[1], 'Swap : aucun')
        self.assertEqual(infos[2], f'Processeur : 23{PCT} · charge 2,4 · 72{DEG}')
        self.assertEqual(infos[3], 'Plus actif : ffmpeg (6,2 cœurs)')
        self.assertEqual(menu.index(('info', infos[3])) + 1, menu.index(('sep',)))

    def test_sans_processeur_le_menu_ne_change_pas(self):
        ancien = {k: v for k, v in ETAT.items() if k != 'processeur'}
        sans = L.contenu_menu(75.2, ancien, None)
        self.assertFalse(any(e[0] == 'info' and e[1].startswith(('Processeur', 'Plus actif')) for e in sans))
        self.assertEqual(sans, L.contenu_menu(75.2, ancien, None, cpu=None))
        # avec la seule mesure locale : la ligne Processeur, pas de « Plus actif »
        local = [e[1] for e in L.contenu_menu(75.2, ancien, None, cpu=10) if e[0] == 'info']
        self.assertIn(f'Processeur : 10{PCT}', local)
        self.assertFalse(any(t.startswith('Plus actif') for t in local))


class Faux(unittest.TestCase):
    def sortie(self, **env):
        return subprocess.run([FAUX, 'etat', '--json'], env=dict(os.environ, **env), capture_output=True, text=True).stdout

    def test_contrat_1_4_0(self):
        etat = L.lire_json(self.sortie(FAUX_GAGARINE_CPU='oui'))
        self.assertEqual(L.ligne_plus_actif(etat), 'Plus actif : ffmpeg (6,2 cœurs)')
        self.assertEqual(L.ligne_processeur(None, etat), f'Processeur : 23{PCT} · charge 2,4 · 72{DEG}')

    def test_cœur_1_3_sans_processeur(self):
        etat = L.lire_json(self.sortie())
        self.assertEqual(L.processeur(etat), {})
        self.assertIsNone(L.ligne_processeur(None, etat))


if __name__ == '__main__':
    unittest.main()
