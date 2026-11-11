"""Nouveau design (1.5.0) des pages Journal et Réglages : fonctions pures, puis, sous Xvfb seulement, la frise du journal
groupée par jour et les deux colonnes des réglages.

Les tests GTK n'ouvrent une fenêtre que si GAGARINE_TESTS_GTK=1 (comme tests/test_interface_gtk.py) :

    GAGARINE_TESTS_GTK=1 dbus-run-session -- xvfb-run -a -s "-screen 0 1600x1000x24" env GDK_BACKEND=x11 \
        python3 -m unittest tests.test_interface_design_journal
"""
import os
import unittest
from datetime import date, datetime

try:
    from gagarine.interface import journal as J
    JOURNAL = True
except (ImportError, ValueError):
    JOURNAL = False

try:
    from tests.test_interface_gtk import FAUX, GTK, attendre
    if GTK:
        from gi.repository import Adw, Gio, Graphene, Gtk
except (ImportError, ValueError):
    GTK = False


def iso(d):
    """Date ISO 8601 avec le fuseau local, comme l'écrit le cœur."""
    return d.astimezone().isoformat()


@unittest.skipUnless(JOURNAL, 'PyGObject ou GTK 4 absents')
class TestJoursDuJournal(unittest.TestCase):
    AUJOURDHUI = date(2026, 11, 11)      # un mercredi

    def test_aujourdhui_et_hier(self):
        self.assertEqual(J.libelle_jour(date(2026, 11, 11), self.AUJOURDHUI), 'Aujourd’hui')
        self.assertEqual(J.libelle_jour(date(2026, 11, 10), self.AUJOURDHUI), 'Hier')

    def test_jour_de_la_semaine_en_francais(self):
        self.assertEqual(J.libelle_jour(date(2026, 11, 9), self.AUJOURDHUI), 'Lundi 9 novembre')
        self.assertEqual(J.libelle_jour(date(2026, 11, 1), self.AUJOURDHUI), 'Dimanche 1er novembre')
        self.assertEqual(J.libelle_jour(date(2026, 8, 15), self.AUJOURDHUI), 'Samedi 15 août')

    def test_autre_annee_et_date_inconnue(self):
        self.assertEqual(J.libelle_jour(date(2025, 12, 30), self.AUJOURDHUI), 'Mardi 30 décembre 2025')
        self.assertEqual(J.libelle_jour(None, self.AUJOURDHUI), 'Date inconnue')

    def test_date_future_ecrite_en_toutes_lettres(self):
        """Une horloge décalée ne doit pas écrire « Hier » pour demain."""
        self.assertEqual(J.libelle_jour(date(2026, 11, 12), self.AUJOURDHUI), 'Jeudi 12 novembre')

    def test_grouper_par_jour(self):
        maintenant = datetime(2026, 11, 11, 15, 0).timestamp()
        evs = [
            {'date': iso(datetime(2026, 11, 11, 14, 0)), 'action': 'fige'},
            {'date': iso(datetime(2026, 11, 11, 9, 0)), 'action': 'repris'},
            {'date': 'n’importe quoi', 'action': 'menage'},
            {'date': iso(datetime(2026, 11, 10, 23, 30)), 'action': 'demarrage'},
            {'date': iso(datetime(2026, 11, 9, 10, 0)), 'action': 'fuite'},
        ]
        groupes = J.grouper_par_jour(evs, maintenant)
        self.assertEqual([(g[1], [e['action'] for e in g[2]]) for g in groupes], [
            ('Aujourd’hui', ['fige', 'repris']), ('Hier', ['demarrage']), ('Lundi 9 novembre', ['fuite']),
            ('Date inconnue', ['menage'])])
        self.assertEqual(groupes[0][0], date(2026, 11, 11))
        self.assertIsNone(groupes[-1][0])
        self.assertEqual(J.grouper_par_jour([], maintenant), [])

    def test_le_jour_suit_l_heure_locale(self):
        """23 h 30 locale reste « hier » même si, en temps universel, c'est déjà le lendemain (ou l'inverse)."""
        ev = {'date': iso(datetime(2026, 11, 10, 23, 30))}
        self.assertEqual(J.jour_evenement(ev), date(2026, 11, 10))
        self.assertEqual(J.heure_evenement(ev), '23:30:00')

    def test_heure_illisible(self):
        self.assertEqual(J.heure_evenement({'date': None}), '—')
        self.assertEqual(J.heure_evenement({'date': 'hier soir'}), '—')
        self.assertIsNone(J.jour_evenement({}))

    def test_cles_distinctes_meme_pour_deux_evenements_identiques(self):
        ev = {'date': '2026-11-11T10:00:00+01:00', 'action': 'menage', 'nom': 'gradle'}
        cles = J.cles_evenements([ev, dict(ev), dict(ev, nom='kotlin')])
        self.assertEqual(len(set(cles)), 3)
        self.assertEqual(cles, J.cles_evenements([ev, dict(ev), dict(ev, nom='kotlin')]))      # stables

    def test_resume(self):
        self.assertEqual(J.resume_journal(29, 29), '29 événements, du plus récent au plus ancien.')
        self.assertEqual(J.resume_journal(1, 29), '1 événement sur 29, du plus récent au plus ancien.')


@unittest.skipUnless(JOURNAL, 'PyGObject ou GTK 4 absents')
class TestTeintes(unittest.TestCase):
    def test_teinte_par_type(self):
        attendu = {'fige': 'alerte', 'arrete': 'alerte', 'limite': 'alerte', 'fuite': 'vigilance',
                   'fuite-travail': 'vigilance', 'bridage-impossible': 'vigilance', 'repris': 'calme',
                   'travail-debut': 'travail', 'travail-fin': 'travail', 'travail-annule': 'travail',
                   'menage': 'neutre', 'demarrage': 'neutre', 'rechargement': 'neutre'}
        for action, teinte in attendu.items():
            self.assertEqual(J.teinte_evenement({'action': action}), teinte, action)

    def test_action_inconnue_neutre(self):
        self.assertEqual(J.teinte_evenement({'action': 'nouvelle-action-du-coeur'}), 'neutre')
        self.assertEqual(J.teinte_evenement({}), 'neutre')

    def test_chaque_action_connue_a_une_teinte(self):
        from gagarine.interface import donnees
        for action in donnees._ACTIONS:
            self.assertIn(J.teinte_evenement({'action': action}), J.TOUTES_TEINTES, action)
            self.assertIn(action, J.TEINTES, f'{action} : teinte à choisir')

    def test_chaque_teinte_a_son_style(self):
        """Une teinte sans règle CSS donnerait une pastille grise sans qu'on le voie."""
        chemin = os.path.join(os.path.dirname(J.__file__), 'styles', 'journal.css')
        with open(chemin, encoding='utf-8') as f:
            css = f.read()
        for teinte in J.TOUTES_TEINTES:
            if teinte != 'neutre':
                self.assertIn(f'.pastille-evenement.{teinte}', css)


@unittest.skipUnless(GTK, 'GTK 4, libadwaita ou affichage (Xvfb) absents, ou GAGARINE_TESTS_GTK non défini')
class TestDesignJournalReglages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id='io.github.mawena.GagarineTestDesignC',
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    def ouvrir(self, mode='normal', taille=None, page=None):
        os.environ['GAGARINE_COMMANDE'] = FAUX
        os.environ['FAUX_MODE'] = mode
        from gagarine.interface.fenetre import Fenetre
        f = Fenetre(self.app)
        self.addCleanup(lambda: (f.source.arreter(), f.destroy()))
        if taille:
            f.set_default_size(*taille)
        f.present()
        if page:
            f.aller_a(page)
        return f

    @staticmethod
    def position(widget, repere):
        ok, p = widget.compute_point(repere, Graphene.Point().init(0, 0))
        return (round(p.x), round(p.y)) if ok else None

    # --- journal ---
    def test_journal_groupe_par_jour(self):
        f = self.ouvrir(page='journal')
        page = f.pages['journal']
        self.assertTrue(attendre(lambda: f.source.journal and page.lignes_affichees()))
        evs = f.source.journal
        lignes = page.lignes_affichees()
        self.assertEqual(len(lignes), len(evs))            # la liste ne contient que les événements
        groupes = J.grouper_par_jour(evs)
        entetes = [l for l in lignes if l.entete.get_visible()]
        self.assertEqual([l.jour.get_label() for l in entetes], [g[1] for g in groupes])
        self.assertEqual([l.nombre.get_label() for l in entetes], [f'{len(g[2])} événement' + ('s' if len(g[2]) > 1 else '')
                                                                   for g in groupes])
        self.assertIn('premier-de-la-liste', lignes[0].get_css_classes())
        self.assertEqual(sum('premier-du-jour' in l.get_css_classes() for l in lignes), len(groupes))
        self.assertEqual(sum('dernier-du-jour' in l.get_css_classes() for l in lignes), len(groupes))
        self.assertIn('dernier-du-jour', lignes[-1].get_css_classes())
        for ligne, ev in zip(lignes, evs):                 # même ordre : du plus récent au plus ancien
            self.assertRegex(ligne.heure.get_label(), r'^\d\d:\d\d:\d\d$')
            self.assertIn(J.teinte_evenement(ev), ligne.pastille.get_css_classes())
        self.assertIn('29 événements', page.resume.get_label())

    def test_recherche_filtre_et_garde_les_lignes(self):
        f = self.ouvrir(page='journal')
        page = f.pages['journal']
        self.assertTrue(attendre(lambda: f.source.journal and page.lignes_affichees()))
        total = len(page.lignes_affichees())
        page.recherche.set_text('fuite')
        page.maj(force=True)
        trouvees = page.lignes_affichees()
        self.assertEqual(len(trouvees), 4)
        self.assertTrue(all('fuite' in (l.texte_titre + l.texte_detail).lower() for l in trouvees))
        self.assertIn('4 événements sur', page.resume.get_label())
        self.assertTrue(any(l.entete.get_visible() for l in trouvees[:1]))      # la première porte l'en-tête de son jour
        page.recherche.set_text('')
        page.maj(force=True)
        self.assertEqual(len(page.lignes_affichees()), total)
        self.assertTrue(all(any(l is m for m in page.lignes_affichees()) for l in trouvees))   # mêmes lignes : pas de clignotement
        page.recherche.set_text('aucun-evenement-ne-dit-ça')
        page.maj(force=True)
        self.assertEqual(page.lignes_affichees(), [])
        self.assertFalse(page.liste.get_visible())
        self.assertTrue(page.vide.get_visible())
        self.assertEqual(page.vide.get_title(), 'Aucun résultat')

    def test_filtre_par_type(self):
        f = self.ouvrir(page='journal')
        page = f.pages['journal']
        self.assertTrue(attendre(lambda: f.source.journal and page.lignes_affichees()))
        from gagarine.interface import donnees
        page.filtre.set_selected([c for c, _ in donnees.CATEGORIES].index('file'))
        lignes = page.lignes_affichees()
        self.assertTrue(lignes)
        self.assertTrue(all(any(t in l.pastille.get_css_classes() for t in ('travail', 'alerte', 'vigilance'))
                            for l in lignes))

    def test_detail_coupe_sur_une_ligne_avec_infobulle(self):
        f = self.ouvrir(taille=(520, 760), page='journal')
        page = f.pages['journal']
        self.assertTrue(attendre(lambda: f.source.journal and page.lignes_affichees()
                                 and page.lignes_affichees()[0].get_width() > 0))
        lignes = page.lignes_affichees()
        fige = next(l for l, ev in zip(lignes, f.source.journal) if ev.get('action') == 'fige')
        self.assertTrue(attendre(lambda: fige.est_coupe()))
        self.assertTrue(fige.detail.get_single_line_mode())
        self.assertIn(fige.texte_detail, fige.texte_infobulle())
        self.assertIn('python3 -m pytest tests/lourds', fige.texte_infobulle())       # la fin du détail, coupée à l'écran
        repris = next(l for l, ev in zip(lignes, f.source.journal) if ev.get('action') == 'repris' and ev.get('pid') == 99)
        self.assertFalse(repris.est_coupe())

    # --- réglages ---
    def test_reglages_deux_colonnes_en_large_empilees_en_etroit(self):
        from gagarine.interface.reglages import GROUPES_DROITE, GROUPES_GAUCHE
        f = self.ouvrir(taille=(1560, 900), page='reglages')
        page = f.pages['reglages']
        self.assertTrue(attendre(lambda: page.courants and page.get_width() > 1100))
        for cle in GROUPES_GAUCHE:
            self.assertIs(page.groupes[cle].get_parent(), page.colonne_gauche, cle)
        for cle in GROUPES_DROITE:
            self.assertIs(page.groupes[cle].get_parent(), page.colonne_droite, cle)
        self.assertTrue(attendre(lambda: page.rangee.get_orientation() == Gtk.Orientation.HORIZONTAL))
        self.assertTrue(attendre(lambda: (self.position(page.colonne_droite, page.colonne_gauche) or (0, 1))[0] > 400))
        x, y = self.position(page.colonne_droite, page.colonne_gauche)
        self.assertEqual(y, 0)                                  # côte à côte, alignées en haut
        x0, _ = self.position(page.colonne_gauche, page)
        self.assertLess(x0, 40)                                 # alignées à gauche, pas centrées
        f.set_default_size(760, 820)
        self.assertTrue(attendre(lambda: page.rangee.get_orientation() == Gtk.Orientation.VERTICAL))
        self.assertTrue(attendre(lambda: (self.position(page.colonne_droite, page.colonne_gauche) or (1, 0))[1] > 0))
        x, y = self.position(page.colonne_droite, page.colonne_gauche)
        self.assertEqual(x, 0)                                  # empilées

    def test_barre_d_enregistrement(self):
        f = self.ouvrir(page='reglages')
        page = f.pages['reglages']
        self.assertTrue(attendre(lambda: page.courants))
        self.assertFalse(page.vue.get_reveal_bottom_bars())     # rien de modifié : pas de barre
        page.champs['seuil'].set_value(page.champs['seuil'].get_value() - 1)
        self.assertTrue(page.vue.get_reveal_bottom_bars())
        self.assertTrue(page.bouton_enregistrer.get_sensitive())
        self.assertIn('mot de passe', page.message_reglages.get_label())
        page.champs['vigilance'].set_value(99)                   # vigilance au-dessus du seuil : erreur
        self.assertFalse(page.bouton_enregistrer.get_sensitive())
        self.assertIn('error', page.message_reglages.get_css_classes())
        page.recharger(force=True)                               # « Annuler les modifications »
        self.assertFalse(page.vue.get_reveal_bottom_bars())
        self.assertNotIn('error', page.message_reglages.get_css_classes())

    def test_service_actif_montre_les_bonnes_commandes(self):
        f = self.ouvrir(page='reglages')
        page = f.pages['reglages']
        self.assertTrue(attendre(lambda: f.source.etat and page.etat_service.get_label()))
        self.assertEqual(page.etat_service.get_label(), 'Actif')
        b = page.boutons_service
        self.assertEqual((b['demarrer'].get_visible(), b['redemarrer'].get_visible(), b['arreter'].get_visible()),
                         (False, True, True))
        self.assertNotIn('alerte', page.voyant_service.get_css_classes())
        self.assertIn('flat', b['arreter'].get_css_classes())

    # --- cœurs plus anciens ---
    def test_coeur_1_3_et_1_2(self):
        from gagarine.interface import donnees
        for mode in ('v13', 'v12'):
            f = self.ouvrir(mode, page='journal')
            journal, reglages = f.pages['journal'], f.pages['reglages']
            self.assertTrue(attendre(lambda: f.source.journal and reglages.courants and journal.lignes_affichees()), mode)
            self.assertFalse(f.banniere.get_revealed(), mode)
            self.assertEqual(len(journal.lignes_affichees()), len(f.source.journal), mode)
            journal.filtre.set_selected([c for c, _ in donnees.CATEGORIES].index('menage'))
            self.assertEqual(len(journal.lignes_affichees()), 2, mode)
            self.assertFalse(reglages.bouton_enregistrer.get_sensitive(), mode)      # pas de modification fantôme
            self.assertFalse(reglages.vue.get_reveal_bottom_bars(), mode)
            self.assertIn(reglages.champs['file_paralleles'].get_value(), (2, 3), mode)


if __name__ == '__main__':
    unittest.main()
