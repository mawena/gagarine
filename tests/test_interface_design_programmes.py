"""Nouveau design (1.5.0) des pages Programmes et File d'attente : fonctions pures, et fenêtre sous Xvfb seulement.

    GAGARINE_TESTS_GTK=1 xvfb-run -a -s "-screen 0 1600x1000x24" env GDK_BACKEND=x11 python3 -m unittest discover -s tests -t .
"""
import os
import time
import unittest
from pathlib import Path

try:
    from gagarine.interface import file_attente as F
    from gagarine.interface import programmes as P
    PAGES = True
except (ImportError, ValueError):
    PAGES = False

try:
    from tests.test_interface_gtk import FAUX, GTK, attendre
except ImportError:
    GTK = False

STYLES = Path(__file__).resolve().parent.parent / 'gagarine' / 'interface' / 'styles'


@unittest.skipUnless(PAGES, 'PyGObject, GTK 4 ou libadwaita absents')
class FonctionsProgrammes(unittest.TestCase):
    def test_nombre(self):
        self.assertEqual(P.nombre_fr(95), '95')
        self.assertEqual(P.nombre_fr(92.5), '92,5')

    def test_phrase_vide_figes(self):
        self.assertEqual(P.phrase_vide_figes({'seuil': 95}),
                         'Aucun programme figé. Gagarine interviendra si la RAM atteint 95 %.')
        self.assertIn('92,5 %', P.phrase_vide_figes({'seuil': 92.5}))
        self.assertEqual(P.phrase_vide_figes({}), 'Aucun programme figé : la RAM est sous contrôle.')
        self.assertEqual(P.phrase_vide_figes(None), 'Aucun programme figé : la RAM est sous contrôle.')

    def test_fuites_connues_et_phrase(self):
        self.assertTrue(P.connait_fuites({'fuite_seuil': 0}))
        self.assertFalse(P.connait_fuites({'seuil': 95}))           # cœur 1.2 : pas de détection des fuites
        self.assertFalse(P.connait_fuites(None))
        self.assertIn('plus de 100 Mo/min depuis 5 minutes', P.phrase_vide_fuites({'fuite_seuil': 100}))
        self.assertEqual(P.phrase_vide_fuites({'fuite_seuil': 0}), 'La détection des fuites est désactivée (Réglages).')
        self.assertEqual(P.phrase_vide_fuites({}), 'Aucune fuite : aucun programme ne grossit sans cesse.')

    def test_detail_fige_sur_une_ligne(self):
        f = {'pid': 4242, 'depuis': 1000, 'utilisateur': 'alice', 'commande': 'python3  -m pytest\n tests/lourds'}
        d = P.detail_fige(f, maintenant=1185)
        self.assertEqual(d, 'Figé depuis 3 min 5 s · PID 4242 · alice · python3 -m pytest tests/lourds')
        self.assertNotIn('\n', d)
        self.assertEqual(P.detail_fige({'pid': 7}), 'PID 7')

    def test_detail_fuite_sans_la_pente(self):
        f = {'pid': 6001, 'nom': 'node', 'pente_mo_min': 210, 'depuis': 1000, 'utilisateur': 'alice'}
        self.assertEqual(P.detail_fuite(f, maintenant=1420), 'PID 6001 · alice · depuis 7 min')
        self.assertEqual(P.detail_fuite({'pid': 7}), 'PID 7')

    def test_detail_programme(self):
        self.assertEqual(P.detail_programme({'pid': 3100, 'utilisateur': 'alice'}), 'PID 3100 · alice')
        self.assertEqual(P.detail_programme({'pid': 880, 'utilisateur': 'root', 'protege': True}), 'PID 880 · root · protégé')
        self.assertEqual(P.detail_programme({'pid': 105, 'noyau': True}), 'PID 105 · noyau')

    def test_valeur_gros(self):
        self.assertEqual(P.valeur_gros({'rss_mo': 2900, 'swap_mo': 420}), ('2,8 Go', '+ 420 Mo de swap'))
        self.assertEqual(P.valeur_gros({'rss_mo': 610, 'swap_mo': 0}), ('610 Mo', ''))
        self.assertEqual(P.valeur_gros({}), ('0 Mo', ''))

    def test_detail_candidat_sans_la_memoire(self):
        c = {'type': 'gradle', 'nom': 'Démon Gradle', 'pid': 5551, 'memoire_mo': 3100, 'inactif_depuis_s': 2640, 'auto': True}
        self.assertEqual(P.detail_candidat(c), 'PID 5551 · inactif depuis 44 min · arrêt automatique possible')
        c = {'type': 'ollama', 'nom': 'llama3.1:8b', 'pid': None, 'inactif_depuis_s': 1260, 'auto': False}
        self.assertEqual(P.detail_candidat(c), 'Modèle Ollama · inactif depuis 21 min · proposé seulement, jamais automatique')
        self.assertNotIn('Go', P.detail_candidat(c))

    def test_detail_appli_et_plafond(self):
        self.assertEqual(P.detail_appli({'instances': 2, 'memoire_mo': 2900}), ('2 instances ouvertes', '2,8 Go'))
        self.assertEqual(P.detail_appli({'instances': 1, 'memoire_mo': 1100}), ('1 instance ouverte', '1,1 Go'))
        self.assertEqual(P.detail_appli({'instances': 0}), ('fermée', ''))
        self.assertEqual(P.libelle_bouton_plafond({'plafond_go': 4}), 'Plafond 4 Go')
        self.assertEqual(P.libelle_bouton_plafond({'plafond_go': None}), 'Aucun plafond')


@unittest.skipUnless(PAGES, 'PyGObject, GTK 4 ou libadwaita absents')
class FonctionsFile(unittest.TestCase):
    EN_COURS = {'etat': 'en cours', 'ram': 4, 'coeurs': 4, 'coeurs_utilises': 3.9, 'depuis': 1000, 'priorite': 'urgent',
                'commande': 'ffmpeg -i montage.mkv  sortie.mp4', 'nom': 'rendu vidéo'}
    ATTENTE = {'etat': 'en attente', 'ram': 6, 'coeurs': 8, 'depuis': 1000, 'motif': 'mémoire : 1,8 Go libres, il en faut 6',
               'commande': 'python3 -m unittest', 'nom': 'tests'}

    def test_duree_courte(self):
        self.assertEqual(F.duree_courte(45), '45 s')
        self.assertEqual(F.duree_courte(754), '12 min')
        self.assertEqual(F.duree_courte(3 * 3600 + 12 * 60 + 5), '3 h 12 min')
        self.assertEqual(F.duree_courte(None), '—')
        self.assertEqual(F.duree_courte(-3), '0 s')

    def test_etat_file_130(self):
        f = {'travaux': [self.EN_COURS, self.ATTENTE, self.ATTENTE], 'en_cours': 1, 'paralleles': 3,
             'ram_disponible_go': 6.8, 'charge': 2.1, 'coeurs': 14}
        self.assertEqual(F.etat_file(f), '1 en cours sur 3 possibles · 2 en attente · 6,8 Go libres · charge 2,1 sur 14 cœurs')
        self.assertIn('0 en cours sur 1 possible ·', F.etat_file({'travaux': [], 'en_cours': 0, 'paralleles': 1,
                                                                  'ram_disponible_go': 4}))

    def test_etat_file_cœur_1_2(self):
        f = {'travaux': [self.EN_COURS, self.ATTENTE], 'en_cours': None, 'paralleles': None, 'charge': 2.1, 'coeurs': 14}
        texte = F.etat_file(f)
        self.assertEqual(texte, '1 en cours · 1 en attente · charge 2,1 sur 14 cœurs')
        self.assertNotIn('en cours sur', texte)

    def test_colonnes_travail(self):
        c = F.colonnes_travail(self.EN_COURS, maintenant=1754)
        self.assertEqual(c, {'ram': ('RAM annoncée', '4 Go'), 'coeurs': ('Cœurs', '4'),
                             'processeur': ('Processeur', '3,9 cœurs'), 'duree': ('Durée', '12 min')})
        c = F.colonnes_travail(self.ATTENTE, maintenant=1095)
        self.assertEqual(c['processeur'], ('Processeur', '—'))         # en attente : pas de processeur
        self.assertEqual(c['duree'], ('Attente', '1 min'))
        ancien = {'etat': 'en attente', 'ram': None, 'coeurs': 2}
        c = F.colonnes_travail(ancien, maintenant=0)
        self.assertEqual((c['ram'][1], c['coeurs'][1], c['duree'][1]), ('—', '2', '—'))
        self.assertEqual(F.colonnes_travail({'etat': 'en attente', 'besoin_go': 1.5, 'coeurs': 0.5}, 0)['ram'][1], '1,5 Go')
        self.assertEqual(F.colonnes_travail({'etat': 'en attente', 'coeurs': 0.5}, 0)['coeurs'][1], '0,5')

    def test_etat_et_motif(self):
        self.assertEqual(F.etat_travail(self.EN_COURS), ('en-cours', 'En cours'))
        self.assertEqual(F.etat_travail(self.ATTENTE), ('en-attente', 'En attente'))
        self.assertEqual(F.motif_travail(self.ATTENTE), 'Attend : mémoire : 1,8 Go libres, il en faut 6')
        self.assertIsNone(F.motif_travail(self.EN_COURS))
        self.assertIsNone(F.motif_travail({'etat': 'en attente'}))

    def test_description_complete(self):
        d = F.description_travail(self.EN_COURS, maintenant=1754)
        self.assertIn('processeur : 3,9 cœurs', d)
        self.assertTrue(d.endswith('\nffmpeg -i montage.mkv sortie.mp4'))
        self.assertIn('Attend : mémoire : 1,8 Go libres, il en faut 6', F.description_travail(self.ATTENTE, 1095))

    def test_ligne_appris(self):
        titre, sous, valeurs, niveau = F.ligne_appris({'nom': 'tests voix', 'executions': 2, 'pic_go': 9.0,
                                                        'annonce_go': 1, 'tendance': 'hausse'})
        self.assertEqual((titre, niveau), ('tests voix', 'vigilance'))
        self.assertTrue(sous.startswith('Pic en hausse : fuite probable · 2 exécutions'))
        self.assertEqual(valeurs, {'pic': '9 Go', 'annonce': '1 Go'})
        self.assertEqual(F.ligne_appris({'nom': 'g', 'tendance': 'limite'})[3], 'alerte')
        self.assertEqual(F.ligne_appris({'nom': 'r', 'pic_go': 3.8, 'annonce_go': 4, 'tendance': 'stable'})[3], None)
        bas = F.ligne_appris({'nom': 'b', 'pic_go': 6.3, 'annonce_go': 4, 'executions': 1})
        self.assertEqual((bas[1], bas[3]), ('Annonce trop basse · 1 exécution', 'vigilance'))

    def test_ligne_passe(self):
        t = {'nom': 'rendu vidéo', 'date': 1000, 'duree': 754.0, 'code': 0, 'pic_go': 3.8, 'coeurs_moyens': 5.4}
        titre, sous, valeurs, reussi = F.ligne_passe(t, maintenant=2000)
        self.assertEqual((titre, reussi), ('rendu vidéo', True))
        self.assertTrue(sous.startswith('Terminé · '))
        self.assertEqual(valeurs, {'duree': '12 min', 'pic': '3,8 Go', 'processeur': '5,4 cœurs'})
        echec = F.ligne_passe({'nom': 'x', 'date': 1000, 'code': 1}, maintenant=2000)
        self.assertTrue(echec[1].startswith('Échec (code 1) · '))
        self.assertEqual(echec[2], {'duree': '—', 'pic': '—', 'processeur': '—'})
        self.assertFalse(echec[3])


@unittest.skipUnless(GTK and PAGES, 'GTK 4, libadwaita ou affichage (Xvfb) absents, ou GAGARINE_TESTS_GTK non défini')
class TestPagesDesign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gi.repository import Adw
        cls.app = Adw.Application(application_id='io.github.mawena.GagarineTestDesignB')
        cls.app.register(None)

    def ouvrir(self, mode='normal', largeur=1580, hauteur=960):
        os.environ['GAGARINE_COMMANDE'] = FAUX
        os.environ['FAUX_MODE'] = mode
        from gagarine.interface.fenetre import Fenetre
        fenetre = Fenetre(self.app)
        fenetre.set_default_size(largeur, hauteur)
        self.addCleanup(lambda: (fenetre.source.arreter(), fenetre.destroy()))
        fenetre.present()
        return fenetre

    @staticmethod
    def boite(widget, page):
        ok, r = widget.compute_bounds(page)
        return (r.get_x(), r.get_y(), r.get_width()) if ok else None

    def sans_debordement(self, page):
        """Les valeurs et les actions de chaque travail restent dans leur ligne (rien ne déborde sur la suivante)."""
        for r in page.lignes.values():
            for w in (r.valeurs, r.actions, r.textes):
                self.assertLessEqual(self.boite(w, r)[1] + w.get_height(), r.get_height() + 1, r.get_title())
                self.assertLessEqual(self.boite(w, r)[0] + w.get_width(), r.get_width() + 1, r.get_title())

    def montrer(self, f, nom, condition):
        f.aller_a(nom)
        page = f.pages[nom]
        self.assertTrue(attendre(lambda: page.get_width() > 0 and condition(page)), f'page {nom} pas prête')
        attendre(lambda: False, 0.4)     # laisse s'appliquer le point de rupture et la mise en page
        return page

    def test_programmes_deux_colonnes_en_large(self):
        f = self.ouvrir(largeur=1580)
        page = self.montrer(f, 'programmes', lambda p: p.lignes_actifs and p.lignes_figes and p.groupe_applis.get_visible())
        self.assertGreater(page.get_width(), 1000)
        figes, fuites, gros = (self.boite(s, page) for s in (page.groupe_figes, page.section_fuites, page.groupe_gros))
        actifs, menage, applis = (self.boite(s, page) for s in (page.groupe_actifs, page.groupe_menage, page.groupe_applis))
        self.assertLess(figes[0] + figes[2], actifs[0])             # à gauche, puis à droite
        self.assertAlmostEqual(figes[1], actifs[1], delta=2)         # sur la même ligne
        self.assertTrue(figes[1] < fuites[1] < gros[1])
        self.assertTrue(actifs[1] < menage[1] < applis[1])
        self.assertEqual({round(b[0]) for b in (figes, fuites, gros)}, {round(figes[0])})

    def test_programmes_empilees_en_etroit(self):
        f = self.ouvrir(largeur=760)
        page = self.montrer(f, 'programmes', lambda p: p.lignes_actifs and p.groupe_applis.get_visible())
        self.assertLess(page.get_width(), 1000)
        figes, gros, actifs = (self.boite(s, page) for s in (page.groupe_figes, page.groupe_gros, page.groupe_actifs))
        self.assertAlmostEqual(figes[0], actifs[0], delta=1)         # une seule colonne
        self.assertGreater(actifs[1], gros[1])                       # la colonne de droite passe dessous

    def test_actions_plates(self):
        f = self.ouvrir()
        prog = self.montrer(f, 'programmes', lambda p: p.lignes_figes and p.lignes_menage and p.lignes_applis)
        fige = prog.lignes_figes[4242]
        self.assertTrue(fige.reprendre.has_css_class('flat'))
        self.assertFalse(fige.reprendre.has_css_class('destructive-action'))
        for b in [fige.arreter] + [r.action for r in prog.lignes_menage.values()]:
            self.assertTrue(all(b.has_css_class(c) for c in ('flat', 'destructive-action', 'texte-destructif')),
                            b.get_label())
        self.assertTrue(all(r.plafond.has_css_class('flat') for r in prog.lignes_applis.values()))
        fichier = self.montrer(f, 'file', lambda p: len(p.lignes) == 5)
        for r in fichier.lignes.values():
            self.assertTrue(all(r.annuler.has_css_class(c) for c in ('flat', 'destructive-action', 'texte-destructif')))
            self.assertTrue(r.sortie.has_css_class('flat') and r.changer.has_css_class('flat'))
            self.assertFalse(r.sortie.has_css_class('destructive-action'))
        self.assertTrue(fichier.bouton_nouveau.has_css_class('suggested-action'))

    def test_textes_sur_une_ligne_avec_infobulle(self):
        f = self.ouvrir()
        prog = self.montrer(f, 'programmes', lambda p: p.lignes_figes)
        fige = prog.lignes_figes[4242]
        self.assertEqual((fige.get_title_lines(), fige.get_subtitle_lines()), (1, 1))
        self.assertEqual(len(P._libelles_du_titre(fige)), 2)             # titre et sous-titre trouvés (infobulle)
        self.assertIn('python3 -m pytest tests/lourds', fige.get_subtitle())
        self.assertEqual(fige.valeur.get_label(), '8,9 Go')

    def test_sections_vides_ont_leur_phrase(self):
        f = self.ouvrir('vide')
        src = f.source
        self.assertTrue(attendre(lambda: src.etat and src.file and src.menage is not None and src.applications is not None
                                 and src.appris is not None and '7j' in src.historiques))
        prog = f.pages['programmes']
        self.assertTrue(prog.vide_figes.get_visible())
        self.assertEqual(prog.vide_figes.get_title(), 'Aucun programme figé. Gagarine interviendra si la RAM atteint 95 %.')
        self.assertTrue(prog.section_fuites.get_visible())              # cœur 1.3 : la section reste, avec sa phrase
        self.assertFalse(prog.groupe_fuites.get_visible())
        self.assertTrue(prog.vide_fuites.get_visible() and prog.liste_vide_fuites.get_visible())
        self.assertIn('Aucune fuite', prog.vide_fuites.get_title())
        self.assertTrue(prog.groupe_actifs.get_visible() and prog.vide_actifs.get_visible())
        self.assertTrue(prog.groupe_menage.get_visible() and prog.vide_menage.get_visible())
        self.assertTrue(prog.groupe_applis.get_visible() and prog.vide_applis.get_visible())
        self.assertIn('Aucune application', prog.vide_applis.get_title())
        fichier = f.pages['file']
        self.assertTrue(fichier.vide.get_visible())
        self.assertIn('0 en cours sur 3 possibles', fichier.groupe.get_description())
        self.assertTrue(attendre(lambda: fichier.groupe_appris.get_visible() and fichier.groupe_passes.get_visible()))
        self.assertEqual([r.get_title() for r in fichier.lignes_appris], [F.VIDE_APPRIS])
        self.assertEqual([r.get_title() for r in fichier.lignes_passes], [F.VIDE_PASSES])

    def test_travaux_en_colonnes_puis_sous_le_nom(self):
        f = self.ouvrir(largeur=1580)
        page = self.montrer(f, 'file', lambda p: len(p.lignes) == 5)
        self.assertGreater(page.get_width(), F.TRAVAUX_SP)
        en_cours = page.lignes['1762785000000-1234.json']
        attente = page.lignes['1762785100000-1301.json']
        self.assertFalse(en_cours.etroite)
        self.assertEqual(en_cours.colonnes['processeur'][2].get_label(), '3,9 cœurs')
        self.assertEqual(attente.colonnes['processeur'][2].get_label(), '—')
        self.assertEqual(attente.colonnes['duree'][1].get_label(), 'Attente')
        self.assertEqual(attente.motif.get_label(), 'Attend : mémoire : 1,8 Go libres, il en faut 6')
        self.assertTrue(en_cours.pastille.has_css_class('en-cours') and attente.pastille.has_css_class('en-attente'))
        # colonnes alignées d'une ligne à l'autre, à droite du nom
        xs = {round(self.boite(r.colonnes['ram'][0], page)[0]) for r in page.lignes.values()}
        self.assertEqual(len(xs), 1)
        titre = self.boite(en_cours.titre, page)
        self.assertAlmostEqual(self.boite(en_cours.colonnes['ram'][0], page)[1], titre[1], delta=24)
        # les valeurs suivent le nom ; la place libre est avant les actions, calées à droite
        textes, ram = self.boite(en_cours.textes, page), self.boite(en_cours.colonnes['ram'][0], page)
        self.assertAlmostEqual(ram[0], textes[0] + textes[2] + 16, delta=2)
        annuler, liste = self.boite(en_cours.annuler, page), self.boite(page.groupe.liste, page)
        self.assertAlmostEqual(annuler[0] + annuler[2], liste[0] + liste[2] - 12, delta=3)

        etroite = self.ouvrir(largeur=900)
        page = self.montrer(etroite, 'file', lambda p: len(p.lignes) == 5)
        ligne = page.lignes['1762785000000-1234.json']
        self.assertTrue(attendre(lambda: ligne.etroite))
        attendre(lambda: False, 0.4)
        titre, ram = self.boite(ligne.titre, page), self.boite(ligne.colonnes['ram'][0], page)
        self.assertGreater(ram[1], titre[1] + 10)                       # les valeurs passent sous le nom
        self.assertAlmostEqual(ram[0], titre[0], delta=2)
        self.sans_debordement(page)
        actions, valeurs = self.boite(ligne.actions, page), self.boite(ligne.valeurs, page)
        if ligne.disposition == 'moyenne':                              # actions à droite des valeurs
            self.assertGreater(actions[0], valeurs[0] + valeurs[2] - 1)
        else:                                                           # actions sur leur propre ligne
            self.assertGreater(actions[1], valeurs[1] + 10)

    def regler_largeur_page(self, f, page, cible, hauteur=960):
        """Redimensionne la fenêtre pour que la page fasse `cible` px de large (la barre latérale éventuelle est déduite)."""
        for _ in range(3):      # la fenêtre a des bords : on corrige d'après la largeur obtenue
            largeur, _h = f.get_default_size()
            f.set_default_size(largeur - (page.get_width() - cible), hauteur)
            if attendre(lambda: abs(page.get_width() - cible) <= 2, 3):
                break
        self.assertLessEqual(abs(page.get_width() - cible), 2, f'largeur de page {page.get_width()}')
        attendre(lambda: False, 0.4)

    def test_point_de_rupture_des_travaux(self):
        """Entre LARGE_SP et TRAVAUX_SP, les colonnes de la page restent côte à côte, mais les travaux passent en moyen."""
        f = self.ouvrir(largeur=1580)
        page = self.montrer(f, 'file', lambda p: len(p.lignes) == 5 and p.lignes_passes)
        self.regler_largeur_page(f, page, (outils_large() + F.TRAVAUX_SP) // 2)
        self.assertEqual(page.get_current_breakpoint(), page.point_travaux)
        self.assertEqual({r.disposition for r in page.lignes.values()}, {'moyenne'})
        self.sans_debordement(page)
        self.assertFalse(any(r.etroite for r in page.lignes_passes if isinstance(r, F.LigneColonnes)))
        appris, passes = self.boite(page.groupe_appris, page), self.boite(page.groupe_passes, page)
        self.assertLess(appris[0] + appris[2], passes[0])               # colonnes encore côte à côte

    def test_travaux_etroits_et_colonnes_empilees(self):
        """Entre ETROITE_SP et TRAVAUX_ETROITS_SP : actions sur leur propre ligne, colonnes de la page empilées."""
        from gagarine.interface.outils import ETROITE_SP
        f = self.ouvrir(largeur=1000)
        page = self.montrer(f, 'file', lambda p: len(p.lignes) == 5 and p.lignes_passes)
        self.regler_largeur_page(f, page, (ETROITE_SP + F.TRAVAUX_ETROITS_SP) // 2)
        self.assertEqual(page.get_current_breakpoint(), page.point_travaux_etroits)
        self.assertEqual({r.disposition for r in page.lignes.values()}, {'etroite'})
        self.sans_debordement(page)
        self.assertFalse(any(r.etroite for r in page.lignes_passes if isinstance(r, F.LigneColonnes)))
        appris, passes = self.boite(page.groupe_appris, page), self.boite(page.groupe_passes, page)
        self.assertAlmostEqual(appris[0], passes[0], delta=1)            # empilées
        self.assertGreater(passes[1], appris[1])
        self.assertLessEqual(page.contenu.get_width(), page.get_width())   # rien ne déborde

    def test_rien_ne_deborde_a_360(self):
        """Fenêtre la plus étroite (360 px) : les colonnes de valeurs passent à la ligne, rien ne déborde."""
        from gi.repository import Gtk
        f = self.ouvrir(largeur=360, hauteur=760)
        for nom, pret in (('file', lambda p: len(p.lignes) == 5 and p.lignes_passes),
                          ('programmes', lambda p: p.lignes_figes and p.lignes_menage and p.lignes_applis)):
            page = self.montrer(f, nom, pret)
            minimum = page.contenu.measure(Gtk.Orientation.HORIZONTAL, -1)[0]      # marges comprises
            self.assertLessEqual(minimum, page.get_width(), nom)
        self.assertEqual({r.disposition for r in f.pages['file'].lignes.values()}, {'tres-etroite'})
        ligne = f.pages['file'].lignes['1762785000000-1234.json']
        ram, proc, duree = (self.boite(ligne.colonnes[c][0], f.pages['file']) for c in ('ram', 'processeur', 'duree'))
        self.assertGreater(proc[1], ram[1] + 10)                        # deux valeurs par ligne
        self.assertAlmostEqual(proc[0], ram[0], delta=1)
        self.assertAlmostEqual(duree[1], proc[1], delta=1)
        self.sans_debordement(f.pages['file'])
        fige = f.pages['programmes'].lignes_figes[4242]                 # boutons à icône, pour laisser place au nom
        self.assertEqual((fige.reprendre.get_icon_name(), fige.arreter.get_icon_name()),
                         ('media-playback-start-symbolic', 'process-stop-symbolic'))
        self.assertEqual(fige.arreter.get_tooltip_text(), 'Arrêter')
        self.assertEqual(fige.get_subtitle_lines(), 2)

    def test_cœur_1_3_marche_toujours(self):
        f = self.ouvrir('v13')
        src = f.source
        self.assertTrue(attendre(lambda: src.etat and src.file and len(f.pages['file'].lignes) == 5))
        self.assertFalse(f.banniere.get_revealed())
        prog = f.pages['programmes']
        self.assertFalse(prog.groupe_actifs.get_visible())             # 1.4.0 : processeur absent
        self.assertTrue(prog.section_fuites.get_visible())             # 1.3.0 : fuites connues
        self.assertEqual(len(prog.lignes_fuites), 2)
        fichier = f.pages['file']
        ligne = fichier.lignes['1762785000000-1234.json']
        self.assertFalse(ligne.colonnes['processeur'][0].get_visible())
        self.assertNotIn('processeur', ligne.get_subtitle())
        self.assertEqual(ligne.priorite.get_label(), 'Urgent')
        self.assertTrue(attendre(lambda: fichier.lignes_passes))
        self.assertTrue(all(not r.colonnes['processeur'][0].get_visible() for r in fichier.lignes_passes
                            if isinstance(r, F.LigneColonnes)))

    def test_cœur_1_2_masque_les_fuites(self):
        f = self.ouvrir('v12')
        self.assertTrue(attendre(lambda: f.source.etat and f.source.file))
        prog = f.pages['programmes']
        self.assertFalse(prog.section_fuites.get_visible())
        fichier = f.pages['file']
        self.assertTrue(attendre(lambda: len(fichier.lignes) == 5))
        self.assertTrue(all(not r.ligne_priorite.get_visible() for r in fichier.lignes.values()))

    def test_styles_sans_erreur(self):
        from gi.repository import Gtk
        for nom in ('programmes.css', 'file.css'):
            erreurs = []
            fournisseur = Gtk.CssProvider()
            fournisseur.connect('parsing-error', lambda _f, _s, e: erreurs.append(e.message))
            fournisseur.load_from_path(str(STYLES / nom))
            self.assertEqual(erreurs, [], nom)


def outils_large():
    from gagarine.interface.outils import LARGE_SP
    return LARGE_SP


if __name__ == '__main__':
    unittest.main()
