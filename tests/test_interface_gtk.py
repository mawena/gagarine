"""Tests de l'interface avec GTK : exécuteur asynchrone (sans affichage) et fenêtre (sous Xvfb seulement).

La fenêtre ne s'ouvre que si GAGARINE_TESTS_GTK=1, pour ne jamais ouvrir de fenêtre sur l'écran réel :

    GAGARINE_TESTS_GTK=1 xvfb-run -a -s "-screen 0 1280x860x24" env GDK_BACKEND=x11 python3 -m unittest discover -s tests -t .
"""
import os
import sys
import time
import unittest
from pathlib import Path

FAUX = str(Path(__file__).resolve().parent / 'donnees_interface' / 'faux-gagarine')

try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import GLib
    from gagarine.interface.source import Executeur
    GI = True
except (ImportError, ValueError):
    GI = False

try:
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gdk, Gtk
    Gtk.init_check() if os.environ.get('GAGARINE_TESTS_GTK') == '1' else None
    GTK = os.environ.get('GAGARINE_TESTS_GTK') == '1' and Gdk.Display.get_default() is not None
except (NameError, ImportError, ValueError):
    GTK = False


def attendre(condition, delai=10.0):
    """Fait tourner la boucle principale jusqu'à ce que condition() soit vraie ; rend sa valeur."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        while contexte.pending():
            contexte.iteration(False)
        if condition():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(GI, 'PyGObject absent')
class TestExecuteur(unittest.TestCase):
    def lancer(self, argv, **kw):
        resultats = []
        executeur = kw.pop('executeur', None) or Executeur()
        executeur.lancer(argv, resultats.append, **kw)
        self.assertTrue(attendre(lambda: resultats), 'la commande ne rend pas la main')
        return resultats[0]

    def test_sortie_et_code(self):
        r = self.lancer([sys.executable, '-c', 'print("bonjour"); import sys; sys.exit(3)'])
        self.assertEqual((r.code, r.sortie.strip()), (3, 'bonjour'))
        self.assertFalse(r.ok)

    def test_faux_gagarine(self):
        r = self.lancer([FAUX, 'etat', '--json'])
        self.assertTrue(r.ok)
        self.assertIn('"ram"', r.sortie)

    def test_commande_absente(self):
        r = self.lancer(['/commande/qui/n/existe/pas'])
        self.assertTrue(r.absente)
        self.assertIn('introuvable', r.message)

    def test_delai_maximal(self):
        debut = time.monotonic()
        r = self.lancer(['sleep', '30'], delai=1)
        self.assertTrue(r.delai)
        self.assertLess(time.monotonic() - debut, 6)
        self.assertIn('trop de temps', r.message)

    def test_jamais_deux_appels_identiques(self):
        e = Executeur()
        resultats = []
        self.assertTrue(e.lancer(['sleep', '0.3'], resultats.append, cle='x'))
        self.assertFalse(e.lancer(['sleep', '0.3'], resultats.append, cle='x'))
        self.assertTrue(attendre(lambda: resultats))
        self.assertEqual(len(resultats), 1)
        self.assertTrue(e.lancer(['true'], resultats.append, cle='x'))   # libre de nouveau
        self.assertTrue(attendre(lambda: len(resultats) == 2))

    def test_la_boucle_reste_libre(self):
        """Pendant qu'une commande lente tourne, la boucle principale continue de répondre."""
        ticks = []
        GLib.timeout_add(50, lambda: ticks.append(1) or True)
        e = Executeur()
        resultats = []
        e.lancer(['sleep', '1'], resultats.append)
        self.assertTrue(attendre(lambda: resultats))
        self.assertGreater(len(ticks), 8)


@unittest.skipUnless(GTK, 'GTK 4, libadwaita ou affichage (Xvfb) absents, ou GAGARINE_TESTS_GTK non défini')
class TestFenetre(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id='io.github.mawena.GagarineTest')
        cls.app.register(None)

    def ouvrir(self, mode='normal'):
        os.environ['GAGARINE_COMMANDE'] = FAUX
        os.environ['FAUX_MODE'] = mode
        from gagarine.interface.fenetre import Fenetre
        app = self.app
        fenetre = Fenetre(app)
        self.addCleanup(lambda: (fenetre.source.arreter(), fenetre.destroy()))
        fenetre.present()
        return fenetre

    def test_donnees_chargees(self):
        f = self.ouvrir()
        self.assertTrue(attendre(lambda: f.source.etat and f.source.file and f.source.journal))
        self.assertFalse(f.banniere.get_revealed())
        self.assertEqual(len(f.pages['programmes'].lignes_figes), 1)
        self.assertEqual(len(f.pages['file'].lignes), 2)
        self.assertEqual(len(f.pages['apercu'].lignes_swap), 2)
        self.assertIsNotNone(f.source.ram_pourcent)

    def test_etat_vide(self):
        f = self.ouvrir('vide')
        self.assertTrue(attendre(lambda: f.source.etat and f.source.file))
        self.assertEqual(f.pages['programmes'].lignes_figes, {})
        self.assertTrue(f.pages['programmes'].vide_figes.get_visible())
        self.assertTrue(f.pages['file'].vide.get_visible())

    def test_sortie_invalide_affiche_une_erreur(self):
        f = self.ouvrir('erreur')
        self.assertTrue(attendre(lambda: f.banniere.get_revealed()))
        self.assertIn('pas lisible', f.banniere.get_title())

    def test_commande_absente(self):
        os.environ['GAGARINE_COMMANDE'] = '/non/existant/gagarine'
        from gagarine.interface.fenetre import Fenetre
        f = Fenetre(self.app)
        self.addCleanup(lambda: (f.source.arreter(), f.destroy()))
        self.assertTrue(attendre(lambda: f.banniere.get_revealed()))
        self.assertIn('introuvable', f.banniere.get_title())

    def test_filtre_du_journal(self):
        f = self.ouvrir()
        page = f.pages['journal']
        self.assertTrue(attendre(lambda: f.source.journal))

        def lignes():
            n, c = 0, page.liste.get_first_child()
            while c:
                n, c = n + 1, c.get_next_sibling()
            return n
        total = lignes()
        self.assertGreaterEqual(total, 20)
        page.recherche.set_text('chrome')
        page.maj(force=True)
        self.assertTrue(0 < lignes() < total)


if __name__ == '__main__':
    unittest.main()
