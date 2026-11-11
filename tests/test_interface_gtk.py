"""Tests de l'interface avec GTK : exécuteur asynchrone (sans affichage) et fenêtre (sous Xvfb seulement).

La fenêtre ne s'ouvre que si GAGARINE_TESTS_GTK=1, pour ne jamais ouvrir de fenêtre sur l'écran réel :

    GAGARINE_TESTS_GTK=1 dbus-run-session -- xvfb-run -a -s "-screen 0 1600x1000x24" env GDK_BACKEND=x11 python3 -m unittest discover -s tests -t .
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


try:
    from gagarine.interface.application import STYLES, fichiers_styles
    STYLES_OK = True
except (NameError, ImportError, ValueError):
    STYLES_OK = False


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
        self.assertEqual(len(f.pages['file'].lignes), 5)
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

    def test_sections_de_la_120(self):
        f = self.ouvrir()
        src = f.source
        self.assertTrue(attendre(lambda: src.menage and src.applications and src.appris))
        prog = f.pages['programmes']
        self.assertTrue(prog.groupe_menage.get_visible())
        self.assertEqual(len(prog.lignes_menage), 3)
        self.assertFalse(prog.vide_menage.get_visible())
        self.assertEqual(prog.lignes_menage['5551'].action.get_label(), 'Arrêter')
        self.assertEqual(prog.lignes_menage['ollama:llama3.1:8b'].action.get_label(), 'Décharger')
        self.assertIn('3 programmes inactifs', prog.groupe_menage.get_description())
        self.assertTrue(prog.groupe_applis.get_visible())
        self.assertEqual(len(prog.lignes_applis), 3)
        self.assertEqual(prog.lignes_applis['google-chrome'].plafond.get_label(), 'Plafond 4 Go')
        self.assertTrue(attendre(lambda: '7j' in src.historiques))
        fichier = f.pages['file']
        self.assertTrue(fichier.groupe_appris.get_visible())
        self.assertEqual(len(fichier.lignes_appris), 4)
        self.assertTrue(fichier.groupe_passes.get_visible())
        self.assertEqual(len(fichier.lignes_passes), 4)

    def test_courbe_24h_et_7j(self):
        f = self.ouvrir()
        page = f.pages['apercu']
        self.assertEqual(page.periode, '5min')
        self.assertFalse(page.groupe_incidents.get_visible())
        page.boutons_periode['24h'].set_active(True)
        self.assertEqual(page.courbe.periode, '24h')
        self.assertTrue(attendre(lambda: '24h' in f.source.historiques))
        self.assertTrue(attendre(lambda: page.groupe_incidents.get_visible()))
        self.assertIn('Pic à', page.resume.get_label())
        self.assertEqual(len(page.lignes_incidents), 4)
        self.assertEqual(page.groupe_courbe.get_title(), 'Les dernières 24 heures')
        page.boutons_periode['7j'].set_active(True)
        self.assertTrue(attendre(lambda: '7j' in f.source.historiques))
        page.boutons_periode['5min'].set_active(True)
        self.assertFalse(page.groupe_incidents.get_visible())

    def test_coeur_ancien_masque_les_sections_sans_bandeau(self):
        f = self.ouvrir('ancien')
        self.assertTrue(attendre(lambda: f.source.etat and f.source.options.get('menage')
                                 and f.source.options.get('applications') and f.source.options.get('appris')
                                 and f.source.options.get('historique-7j')))
        self.assertFalse(f.banniere.get_revealed())
        self.assertEqual(f.source.etat_option('menage')[0], 'absente')
        self.assertFalse(f.pages['programmes'].groupe_menage.get_visible())
        self.assertFalse(f.pages['programmes'].groupe_applis.get_visible())
        self.assertFalse(f.pages['file'].groupe_appris.get_visible())
        self.assertFalse(f.pages['file'].groupe_passes.get_visible())
        page = f.pages['apercu']
        page.boutons_periode['24h'].set_active(True)
        self.assertTrue(attendre(lambda: f.source.options.get('historique-24h')))
        self.assertIn('pas disponible', page.resume.get_label())

    def test_menage_vide(self):
        f = self.ouvrir('vide')
        self.assertTrue(attendre(lambda: f.source.menage is not None))
        prog = f.pages['programmes']
        self.assertTrue(prog.groupe_menage.get_visible())
        self.assertTrue(prog.vide_menage.get_visible())

    def test_reglages_de_la_120(self):
        f = self.ouvrir()
        page = f.pages['reglages']
        self.assertTrue(attendre(lambda: page.courants))
        v = page.valeurs()
        self.assertEqual((v['anticipation'], v['pression'], v['notification_fin_min'], v['menage_delai']), (15, 20, 30, 30))
        self.assertEqual(v['ntfy'], 'gagarine-mawena')
        self.assertTrue(all(v[c] for c in ('notifications_fin', 'file_ajuster', 'menage_auto', 'claude_bloquer')))
        self.assertFalse(page.bouton_enregistrer.get_sensitive())      # rien de modifié
        self.assertTrue(page.essai_ntfy.get_sensitive())
        page.champs['anticipation'].set_value(30)
        page.interrupteurs['claude_bloquer'].set_active(False)
        self.assertTrue(page.bouton_enregistrer.get_sensitive())
        self.assertEqual(donnees_args(page), ['reglages', '--ecrire', 'anticipation=30', 'claude_bloquer=non'])
        page.ntfy.set_text('')
        self.assertFalse(page.essai_ntfy.get_sensitive())

    def test_reglages_service_ancien_sans_nouvelles_cles(self):
        f = self.ouvrir()
        page = f.pages['reglages']
        self.assertTrue(attendre(lambda: page.courants))
        f.source.etat['reglages'] = {k: v for k, v in f.source.etat['reglages'].items()
                                     if k not in ('anticipation', 'ntfy', 'menage_auto')}
        page.recharger(force=True)
        self.assertEqual(page.champs['anticipation'].get_value(), 15)      # valeur par défaut
        self.assertFalse(page.bouton_enregistrer.get_sensitive())          # et pas de « modification » fantôme

    def test_essai_ntfy(self):
        f = self.ouvrir()
        page = f.pages['reglages']
        self.assertTrue(attendre(lambda: page.courants))
        page.envoyer_essai_ntfy()
        self.assertFalse(page.essai_ntfy.get_sensitive())      # en cours : pas de second envoi
        self.assertTrue(attendre(lambda: page.essai_ntfy.get_sensitive()))

    def test_journal_nouveaux_evenements(self):
        f = self.ouvrir()
        page = f.pages['journal']
        self.assertTrue(attendre(lambda: f.source.journal))
        from gagarine.interface import donnees
        page.filtre.set_selected([c for c, _ in donnees.CATEGORIES].index('menage'))
        page.maj(force=True)
        n, c = 0, page.liste.get_first_child()
        while c:
            n, c = n + 1, c.get_next_sibling()
        self.assertEqual(n, 2)


    def test_file_130(self):
        f = self.ouvrir()
        page = f.pages['file']
        self.assertTrue(attendre(lambda: f.source.file and f.source.appris and len(page.lignes) == 5))
        self.assertIn('2 en cours sur 3', page.groupe.get_description())
        l = page.lignes
        self.assertEqual(l['1762785000000-1234.json'].priorite.get_label(), 'Urgent')
        self.assertEqual(l['1762785050000-1280.json'].priorite.get_label(), 'En fond')
        self.assertFalse(l['1762785000000-1234.json'].changer.get_visible())          # en cours : pas de changement
        self.assertTrue(l['1762785100000-1301.json'].changer.get_visible())           # en attente
        self.assertIn('Attend : mémoire : 1,8 Go libres, il en faut 6', l['1762785100000-1301.json'].get_subtitle())
        self.assertFalse(l['1762785300000-1400.json'].priorite.get_visible())         # ancien outil : pas de priorité
        self.assertFalse(l['1762785300000-1400.json'].changer.get_visible())
        self.assertEqual(len(page.lignes_appris), 4)

    def test_file_130_avec_un_cœur_1_2(self):
        f = self.ouvrir('v12')
        page = f.pages['file']
        self.assertTrue(attendre(lambda: f.source.file and len(page.lignes) == 5))
        self.assertFalse(f.banniere.get_revealed())
        self.assertNotIn('en cours sur', page.groupe.get_description())
        self.assertTrue(all(not r.priorite.get_visible() and not r.changer.get_visible() for r in page.lignes.values()))

    def test_nouveau_travail_avec_priorite(self):
        f = self.ouvrir()
        from gagarine.interface.file_attente import DialogueNouveauTravail
        d = DialogueNouveauTravail(f)
        self.assertEqual(d.priorite.get_selected(), 1)      # normal par défaut
        self.assertEqual(d.priorite.get_model().get_n_items(), 3)

    def test_fuites_dans_programmes(self):
        f = self.ouvrir()
        page = f.pages['programmes']
        self.assertTrue(attendre(lambda: f.source.etat and len(page.lignes_fuites) == 2))
        self.assertTrue(page.groupe_fuites.get_visible())
        self.assertEqual(page.lignes_fuites[6001].pente.get_label(), '+210 Mo/min')
        self.assertTrue(page.lignes_gros[6001].fuite.get_visible())
        self.assertEqual(page.lignes_gros[6001].fuite.get_label(), 'fuite +210 Mo/min')
        self.assertFalse(page.lignes_gros[3100].fuite.get_visible())      # chrome ne fuit pas

    def test_fuites_masquees_sans_fuite_ou_avec_un_cœur_1_2(self):
        for mode in ('vide', 'v12'):
            f = self.ouvrir(mode)
            page = f.pages['programmes']
            self.assertTrue(attendre(lambda: f.source.etat))
            self.assertFalse(page.groupe_fuites.get_visible(), mode)
            self.assertEqual(page.lignes_fuites, {})
            self.assertFalse(any(r.fuite.get_visible() for r in page.lignes_gros.values()))

    def test_reglages_130(self):
        f = self.ouvrir()
        page = f.pages['reglages']
        self.assertTrue(attendre(lambda: page.courants))
        v = page.valeurs()
        self.assertEqual((v['file_paralleles'], v['fuite_seuil'], v['fuite_notifier']), (3, 100, True))
        page.champs['file_paralleles'].set_value(5)
        page.champs['fuite_seuil'].set_value(0)
        page.interrupteurs['fuite_notifier'].set_active(False)
        self.assertEqual(sorted(donnees_args(page)[2:]), ['file_paralleles=5', 'fuite_notifier=non', 'fuite_seuil=0'])

    def test_reglages_130_service_ancien(self):
        f = self.ouvrir('v12')
        page = f.pages['reglages']
        self.assertTrue(attendre(lambda: page.courants))
        v = page.valeurs()
        self.assertEqual((v['file_paralleles'], v['fuite_seuil'], v['fuite_notifier']), (2, 100, True))   # valeurs par défaut
        self.assertFalse(page.bouton_enregistrer.get_sensitive())

    def test_journal_fuites(self):
        f = self.ouvrir()
        page = f.pages['journal']
        self.assertTrue(attendre(lambda: f.source.journal))
        page.recherche.set_text('fuite')
        page.maj(force=True)
        n, c = 0, page.liste.get_first_child()
        while c:
            n, c = n + 1, c.get_next_sibling()
        self.assertEqual(n, 4)

    def test_processeur_140(self):
        f = self.ouvrir()
        page = f.pages['apercu']
        self.assertTrue(attendre(lambda: f.source.etat and f.source.processeur() and f.source.cpu_pourcent is not None))
        self.assertTrue(page.groupe_cpu.get_visible())
        self.assertIn('%', page.cpu_pourcent.get_label())
        self.assertIn('14 cœurs', page.cpu_detail.get_label() + page.cpu_legende.get_label())      # service : 14 cœurs logiques
        self.assertIn('Ultra 7', page.cpu_legende.get_label())
        self.assertIn('2 cœurs performants (P, 4 fils) et 10 économes (E)', page.cpu_legende.get_label())
        self.assertIn(page.cpu_statut.get_label(), ('Calme', 'Occupé', 'Saturé'))
        self.assertNotIn('foreground=', page.cpu_legende.get_label())      # pas chaud : pas de couleur
        self.assertTrue(page.coeurs.get_visible())
        self.assertEqual(len(page.coeurs.pourcents), len(f.source.cpu_coeurs))
        prog = f.pages['programmes']
        self.assertTrue(prog.groupe_actifs.get_visible())
        self.assertEqual(len(prog.lignes_actifs), 10)
        self.assertEqual(prog.lignes_actifs[4242].get_title(), 'ffmpeg')
        self.assertEqual(prog.lignes_actifs[4242].valeur.get_label(), '6,2 cœurs · 44 %')
        self.assertIn('noyau', prog.lignes_actifs[105].get_subtitle())
        fichier = f.pages['file']
        self.assertTrue(attendre(lambda: len(fichier.lignes) == 5))
        self.assertIn('processeur : 3,9 cœurs', fichier.lignes['1762785000000-1234.json'].get_subtitle())
        self.assertNotIn('processeur', fichier.lignes['1762785100000-1301.json'].get_subtitle())      # en attente
        self.assertTrue(attendre(lambda: '7j' in f.source.historiques))
        self.assertTrue(any('5,4 cœurs en moyenne' in (r.get_subtitle() or '') for r in fichier.lignes_passes))

    def test_courbe_processeur_24h(self):
        f = self.ouvrir()
        page = f.pages['apercu']
        page.boutons_periode['24h'].set_active(True)
        self.assertTrue(attendre(lambda: '24h' in f.source.historiques and page.groupe_actifs.get_visible()))
        self.assertIn('processeur au plus', page.resume.get_label())
        self.assertEqual(len(page.lignes_actifs), 5)
        self.assertIn('min de processeur', page.lignes_actifs[0].get_subtitle())
        page.boutons_periode['5min'].set_active(True)
        self.assertFalse(page.groupe_actifs.get_visible())

    def test_processeur_chaud_en_orange(self):
        os.environ['FAUX_CHAUD'] = '1'
        self.addCleanup(os.environ.pop, 'FAUX_CHAUD', None)
        f = self.ouvrir()
        page = f.pages['apercu']
        self.assertTrue(attendre(lambda: f.source.processeur() is not None))
        from gagarine.interface.graphiques import couleur_hex
        # 1.5.0 : la couleur de vigilance du thème (jeton gagarine_vigilance), plus un orange codé en dur
        self.assertIn(f'foreground="{couleur_hex(page, "gagarine_vigilance")}"', page.cpu_legende.get_label())
        self.assertIn('97 °C (chaud)', page.cpu_legende.get_label())

    def test_cœur_1_3_ne_montre_que_la_mesure_locale(self):
        f = self.ouvrir('v13')
        page = f.pages['apercu']
        self.assertTrue(attendre(lambda: f.source.etat and f.source.cpu_pourcent is not None))
        self.assertFalse(f.banniere.get_revealed())
        self.assertIsNone(f.source.processeur())
        self.assertTrue(page.groupe_cpu.get_visible())
        self.assertIn('cœurs', page.cpu_detail.get_label())            # nombre de cœurs de la mesure locale
        self.assertEqual(page.cpu_legende.get_label(), '')              # ni modèle, ni température
        self.assertEqual(page.coeurs.types, [])                          # barres égales, sans P ni E
        self.assertEqual(page.titre_etat.get_label(), 'python3 est figé : il attend votre décision.')
        self.assertTrue(attendre(lambda: page.groupe_gros_direct.get_visible()))
        self.assertFalse(page.groupe_actifs_direct.get_visible())       # pas de plus actifs sans `processeur`
        self.assertFalse(f.pages['programmes'].groupe_actifs.get_visible())
        fichier = f.pages['file']
        self.assertTrue(attendre(lambda: len(fichier.lignes) == 5))
        self.assertNotIn('processeur', fichier.lignes['1762785000000-1234.json'].get_subtitle())
        page.boutons_periode['24h'].set_active(True)
        self.assertTrue(attendre(lambda: '24h' in f.source.historiques))
        self.assertFalse(page.groupe_actifs.get_visible())
        self.assertNotIn('processeur au plus', page.resume.get_label())


    # --- 1.5.0 : fenêtre, barre latérale, Aperçu ---
    def test_barre_laterale_et_compteurs(self):
        f = self.ouvrir()
        from gagarine.interface.fenetre import PAGES
        self.assertEqual(list(f.lignes_nav), PAGES)
        self.assertEqual(list(f.pages), PAGES)
        c = f.compteurs
        self.assertTrue(attendre(lambda: f.source.etat and f.source.file and c['programmes'].get_visible()
                                 and c['file'].get_visible()))
        self.assertEqual(c['programmes'].get_label(), '1 figé')
        self.assertTrue(c['programmes'].has_css_class('alerte'))
        self.assertEqual(c['file'].get_label(), '2 en cours')
        self.assertRegex(c['apercu'].get_label(), r'^\d+ %$')
        self.assertFalse(c['journal'].get_visible() or c['reglages'].get_visible())
        self.assertEqual(f.titre_service.get_label(), 'Service actif')
        self.assertEqual(f.voyant_service.niveau, '')
        self.assertFalse(f.bouton_demarrer.get_visible())

    def test_service_arrete(self):
        os.environ['FAUX_SERVICE'] = 'inactive'
        self.addCleanup(os.environ.pop, 'FAUX_SERVICE', None)
        f = self.ouvrir()
        self.assertTrue(attendre(lambda: f.source.etat))
        self.assertEqual(f.titre_service.get_label(), 'Service arrêté')
        self.assertEqual(f.voyant_service.niveau, 'alerte')
        self.assertTrue(f.bouton_demarrer.get_visible())
        self.assertFalse(f.banniere.get_revealed())      # fenêtre large : la barre latérale le dit déjà
        page = f.pages['apercu']
        self.assertEqual(page.titre_etat.get_label(), 'Le service est arrêté : la RAM n’est pas protégée.')
        self.assertEqual(page.voyant.niveau, 'alerte')
        self.assertFalse(page.bouton_programmes.get_visible())

    def test_aller_a_et_raccourcis(self):
        f = self.ouvrir()
        self.assertEqual(f.pile.get_visible_child_name(), 'apercu')
        self.assertEqual(f.page_contenu.get_title(), 'Aperçu')
        f.aller_a('journal')
        self.assertEqual(f.pile.get_visible_child_name(), 'journal')
        self.assertEqual(f.page_contenu.get_title(), f.pages['journal'].get_title())
        self.assertIs(f.liste.get_selected_row(), f.lignes_nav['journal'])
        f.activate_action('win.page-file', None)
        self.assertEqual(f.pile.get_visible_child_name(), 'file')
        f.liste.select_row(f.lignes_nav['reglages'])          # clic dans la barre latérale
        self.assertEqual(f.pile.get_visible_child_name(), 'reglages')
        f.aller_a('inconnue')
        self.assertEqual(f.pile.get_visible_child_name(), 'reglages')

    def test_phrase_d_etat_et_bouton_programmes(self):
        f = self.ouvrir()
        page = f.pages['apercu']
        self.assertTrue(attendre(lambda: f.source.etat))
        self.assertEqual(page.titre_etat.get_label(), 'python3 est figé : il attend votre décision.')
        self.assertEqual(page.voyant.niveau, 'alerte')
        self.assertTrue(page.bouton_programmes.get_visible())
        page.bouton_programmes.emit('clicked')
        self.assertEqual(f.pile.get_visible_child_name(), 'programmes')

    def test_phrase_calme(self):
        f = self.ouvrir('vide')
        page, s = f.pages['apercu'], f.source
        self.assertTrue(attendre(lambda: s.etat))
        s.arreter()                                   # plus de mesure réelle : la phrase lit les valeurs posées ici
        s.ram_pourcent, s.ram_dispo_mo, s.cpu_pourcent = 40.0, 8192, None
        page.maj_phrase()
        self.assertEqual(page.voyant.niveau, '')
        self.assertEqual(page.titre_etat.get_label(), 'Le poste respire : 8,0 Go libres, processeur calme.')
        self.assertFalse(page.bouton_programmes.get_visible())

    def test_mode_etroit(self):
        os.environ['GAGARINE_COMMANDE'] = FAUX
        os.environ['FAUX_MODE'] = 'normal'
        os.environ['FAUX_SERVICE'] = 'inactive'
        self.addCleanup(os.environ.pop, 'FAUX_SERVICE', None)
        from gagarine.interface.fenetre import Fenetre
        f = Fenetre(self.app)
        self.addCleanup(lambda: (f.source.arreter(), f.destroy()))
        f.set_default_size(420, 700)
        f.present()
        self.assertTrue(attendre(lambda: f.split.get_collapsed()))
        self.assertTrue(f.split.get_show_content())               # le contenu d'abord
        f.split.set_show_content(False)                           # bouton retour : la liste des pages
        f.aller_a('file')
        self.assertTrue(f.split.get_show_content())
        self.assertEqual(f.pile.get_visible_child_name(), 'file')
        # barre latérale cachée : le bandeau dit que le service est arrêté
        self.assertTrue(attendre(lambda: f.banniere.get_revealed()))
        self.assertIn('arrêté', f.banniere.get_title())

    def test_apercu_pleine_largeur(self):
        f = self.ouvrir()
        from gagarine.interface.outils import PageLarge
        page = f.pages['apercu']
        self.assertIsInstance(page, PageLarge)
        self.assertIsInstance(page.defilement, Gtk.ScrolledWindow)
        self.assertTrue(attendre(lambda: f.source.etat and len(page.lignes_actifs_direct) == 5))
        self.assertTrue(page.groupe_gros_direct.get_visible())
        self.assertEqual(len(page.lignes_gros_direct), 5)
        self.assertEqual(page.lignes_actifs_direct[4242].get_title(), 'ffmpeg')
        self.assertEqual(page.coeurs.coeurs_performants, 2)
        page.boutons_periode['24h'].set_active(True)               # 24 h : ce que l'historique a retenu
        self.assertFalse(page.groupe_gros_direct.get_visible() or page.groupe_actifs_direct.get_visible())
        self.assertTrue(attendre(lambda: page.groupe_gros.get_visible()))

    def test_colonnes_cote_a_cote_ou_empilees(self):
        from gagarine.interface.outils import PageLarge
        for largeur, attendu in ((1390, Gtk.Orientation.HORIZONTAL), (490, Gtk.Orientation.VERTICAL)):
            w = Gtk.Window(default_width=largeur, default_height=500)
            page = PageLarge('Essai', 'essai', 'computer-symbolic')
            colonnes = page.colonnes(2)
            for c in colonnes:
                c.append(Gtk.Label(label='colonne'))
            w.set_child(page)
            w.present()
            self.addCleanup(w.destroy)
            barre = page.fixer_en_haut(Gtk.Label(label='barre fixe'))
            self.assertIsInstance(page.vue, Adw.ToolbarView)
            self.assertIs(page.defilement.get_ancestor(Adw.ToolbarView), page.vue)     # le défilement passe sous la barre
            rangee = colonnes[0].get_parent()
            self.assertTrue(attendre(lambda: page.get_width() > 0 and rangee.get_orientation() == attendu, 5), largeur)
            etroite = attendu == Gtk.Orientation.VERTICAL
            self.assertEqual(page.contenu.has_css_class('etroite'), etroite)     # classe pour les lignes créées ensuite
            self.assertEqual(barre.get_margin_start(), 14 if etroite else 28)

    def test_styles_sans_erreur_et_couleurs_du_theme(self):
        from gagarine.interface.application import charger_styles

        def lire(nom):
            with open(os.path.join(STYLES, nom), encoding='utf-8') as fichier:
                return fichier.read()
        clairs, sombres = fichiers_styles()
        erreurs = []
        for fichiers in (clairs, sombres):
            p = Gtk.CssProvider()
            p.connect('parsing-error', lambda _p, _section, e: erreurs.append(e.message))
            p.load_from_string('\n'.join(lire(n) for n in fichiers))
        self.assertEqual(erreurs, [])
        self.assertTrue(charger_styles())
        f = self.ouvrir()
        palette = f.pages['apercu'].palette
        for nom in palette.sondes:
            self.assertIsNotNone(palette.couleur(nom), nom)
        # les sondes suivent le thème : bleu de la RAM plus clair en sombre (base-sombre.css)
        gestion = Adw.StyleManager.get_default()
        self.addCleanup(gestion.set_color_scheme, Adw.ColorScheme.DEFAULT)
        couleurs = {}
        for schema, sombre in ((Adw.ColorScheme.FORCE_LIGHT, False), (Adw.ColorScheme.FORCE_DARK, True)):
            gestion.set_color_scheme(schema)
            self.assertTrue(attendre(lambda: gestion.get_dark() == sombre, 3))
            couleurs[sombre] = tuple(round(255 * v) for v in palette.couleur('gagarine_ram')[:3])
        self.assertEqual(couleurs, {False: (0x35, 0x84, 0xe4), True: (0x62, 0xa0, 0xea)})


@unittest.skipUnless(STYLES_OK, 'PyGObject, GTK 4 ou libadwaita absents')
class TestFichiersStyles(unittest.TestCase):
    def test_base_d_abord_puis_ordre_alphabetique(self):
        import tempfile
        with tempfile.TemporaryDirectory() as dossier:
            for nom in ('programmes.css', 'base.css', 'apercu-sombre.css', 'apercu.css', 'base-sombre.css', 'lisezmoi.txt',
                        'journal-sombre.css'):
                open(os.path.join(dossier, nom), 'w').close()
            clairs, sombres = fichiers_styles(dossier)
        self.assertEqual(clairs, ['base.css', 'apercu.css', 'programmes.css'])
        self.assertEqual(sombres, ['base-sombre.css', 'apercu-sombre.css'])     # « journal-sombre » sans journal.css : ignoré

    def test_dossier_absent(self):
        self.assertEqual(fichiers_styles('/n/existe/pas'), ([], []))

    def test_styles_du_paquet(self):
        clairs, sombres = fichiers_styles()
        self.assertEqual(clairs[0], 'base.css')
        self.assertIn('apercu.css', clairs)
        self.assertIn('base-sombre.css', sombres)


def donnees_args(page):
    """Arguments de `reglages --ecrire` pour les modifications en cours de la page (sans le préfixe de commande)."""
    from gagarine.interface import donnees
    return donnees.arguments_reglages([], page.courants, page.valeurs())


if __name__ == '__main__':
    unittest.main()
