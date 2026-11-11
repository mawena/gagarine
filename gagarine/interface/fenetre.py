"""Fenêtre principale (1.5.0) : barre latérale (pages, compteurs vivants, état du service), contenu avec sa barre d'en-tête,
menu, bandeau d'erreur et notifications internes."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk, Pango

from . import donnees
from .apercu import PageApercu
from .file_attente import PageFile
from .journal import PageJournal
from .outils import Voyant, changer_classe
from .programmes import PageProgrammes
from .reglages import PageReglages
from .source import Source

PAGES = ['apercu', 'programmes', 'file', 'journal', 'reglages']
ETROITE = 'max-width: 640sp'     # en dessous, la barre latérale se replie : le contenu d'abord, la liste derrière un retour


class Fenetre(Adw.ApplicationWindow):
    def __init__(self, application, source=None):
        super().__init__(application=application, title='Gagarine', default_width=1280, default_height=820)
        self.set_size_request(360, 400)
        self.source = source or Source()
        self.executeur = self.source.executeur

        # pages (Adw.PreferencesPage ou outils.PageLarge : les deux ont un nom, un titre et une icône)
        self.pile = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        self.pages = {}
        for classe in (PageApercu, PageProgrammes, PageFile, PageJournal, PageReglages):
            page = classe(self)
            self.pages[page.get_name()] = page
            self.pile.add_named(page, page.get_name())

        # contenu : barre d'en-tête (titre de la page, menu), bandeau d'erreur, page
        self.banniere = Adw.Banner(revealed=False)
        self.banniere.connect('button-clicked', lambda _b: self._action_banniere())
        self.action_banniere = None
        self.entete = Adw.HeaderBar()
        self.entete.pack_end(self._menu())
        vue = Adw.ToolbarView()
        vue.add_top_bar(self.entete)
        vue.add_top_bar(self.banniere)
        vue.set_content(self.pile)
        self.page_contenu = Adw.NavigationPage(title='Aperçu', tag='contenu', child=vue)

        # barre latérale : liste des pages et état du service
        self.page_barre = Adw.NavigationPage(title='Gagarine', tag='pages', child=self._barre_laterale())
        self.split = Adw.NavigationSplitView(sidebar=self.page_barre, content=self.page_contenu, show_content=True,
                                             min_sidebar_width=248, max_sidebar_width=280, sidebar_width_fraction=0.2)
        self.split.connect('notify::collapsed', lambda *_: self.maj_banniere())

        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)

        point = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(ETROITE))
        point.add_setter(self.split, 'collapsed', True)
        self.add_breakpoint(point)

        self._raccourcis()
        self.aller_a('apercu')
        for signal in ('erreurs', 'etat'):
            self.source.connect(signal, lambda *_: self.maj_banniere())
        for signal in ('ram', 'etat', 'file'):
            self.source.connect(signal, lambda *_: self.maj_compteurs())
        self.source.connect('etat', lambda *_: self.maj_service())
        self.source.connect('erreurs', lambda *_: self.maj_service())
        self.maj_service()
        self.source.demarrer()
        self.connect('close-request', self._fermeture)

    # --- barre latérale ---
    def _barre_laterale(self):
        self.liste = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.liste.add_css_class('navigation-sidebar')
        self.liste.update_property([Gtk.AccessibleProperty.LABEL], ['Pages'])
        self.lignes_nav, self.compteurs = {}, {}
        for nom in PAGES:
            page = self.pages[nom]
            ligne = Gtk.ListBoxRow()
            ligne.nom = nom
            boite = Gtk.Box(spacing=12)
            boite.append(Gtk.Image.new_from_icon_name(page.get_icon_name()))
            titre = Gtk.Label(label=page.get_title(), xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            boite.append(titre)
            compteur = Gtk.Label(visible=False, valign=Gtk.Align.CENTER)
            compteur.add_css_class('compteur')
            boite.append(compteur)
            ligne.set_child(boite)
            self.liste.append(ligne)
            self.lignes_nav[nom], self.compteurs[nom] = ligne, compteur
        self.liste.connect('row-selected', self._ligne_choisie)
        self.liste.connect('row-activated', lambda *_: self.split.set_show_content(True))
        defilement = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.liste)

        # bas de la barre : voyant et état du service, bouton « Démarrer » s'il est arrêté
        bas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        bas.add_css_class('barre-service')
        ligne = Gtk.Box(spacing=12)
        self.voyant_service = Voyant('inconnu')
        self.voyant_service.set_valign(Gtk.Align.CENTER)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        self.titre_service = Gtk.Label(xalign=0, wrap=True)
        self.titre_service.add_css_class('heading')
        self.detail_service = Gtk.Label(xalign=0, wrap=True)
        self.detail_service.add_css_class('caption')
        self.detail_service.add_css_class('dim-label')
        textes.append(self.titre_service)
        textes.append(self.detail_service)
        ligne.append(self.voyant_service)
        ligne.append(textes)
        self.bouton_demarrer = Gtk.Button(label='Démarrer', visible=False, halign=Gtk.Align.START)
        self.bouton_demarrer.set_tooltip_text('Démarrer le service Gagarine (mot de passe administrateur)')
        self.bouton_demarrer.add_css_class('suggested-action')
        self.bouton_demarrer.connect('clicked', lambda _b: self.demarrer_service())
        bas.append(ligne)
        bas.append(self.bouton_demarrer)

        vue = Adw.ToolbarView(content=defilement)
        vue.add_top_bar(Adw.HeaderBar())
        vue.add_bottom_bar(bas)
        return vue

    def _ligne_choisie(self, _liste, ligne):
        if ligne is not None:
            self._montrer(ligne.nom)

    def _montrer(self, nom):
        if self.pile.get_visible_child_name() != nom:
            self.pile.set_visible_child_name(nom)
        self.page_contenu.set_title(self.pages[nom].get_title())

    def maj_compteurs(self):
        s = self.source
        for nom, (texte, classe) in donnees.compteurs_navigation(s.ram_pourcent, s.etat, s.file).items():
            c = self.compteurs[nom]
            c.set_visible(bool(texte))
            if texte:
                c.set_label(texte)
                changer_classe(c, classe)

    def maj_service(self):
        classe, titre, detail, demarrer = donnees.etat_service(self.source.etat)
        self.voyant_service.set_niveau(classe or 'calme')
        self.titre_service.set_label(titre)
        self.detail_service.set_label(detail)
        self.detail_service.set_visible(bool(detail))
        self.bouton_demarrer.set_visible(demarrer)

    def demarrer_service(self):
        self.executer(donnees.commande_admin() + ['service', 'demarrer'], 'Service démarré')

    # --- navigation ---
    def aller_a(self, nom):
        """Montre la page `nom` ; en fenêtre étroite, le contenu passe devant la liste des pages."""
        if nom not in self.pages:
            return
        ligne = self.lignes_nav[nom]
        if self.liste.get_selected_row() is not ligne:
            self.liste.select_row(ligne)
        self._montrer(nom)
        self.split.set_show_content(True)

    def _raccourcis(self):
        def action(nom, rappel, accel):
            a = Gio.SimpleAction.new(nom, None)
            a.connect('activate', lambda *_: rappel())
            self.add_action(a)
            self.get_application().set_accels_for_action(f'win.{nom}', accel)
        for i, nom in enumerate(PAGES, 1):
            action(f'page-{nom}', lambda n=nom: self.aller_a(n), [f'<Control>{i}'])
        action('nouveau-travail', lambda: (self.aller_a('file'), self.pages['file'].nouveau_travail()), ['<Control>n'])
        action('rechercher', lambda: (self.aller_a('journal'), self.pages['journal'].focaliser_recherche()), ['<Control>f'])
        action('raccourcis', self.montrer_raccourcis, ['<Control>question'])

    def _menu(self):
        menu = Gio.Menu()
        menu.append('Nouveau travail…', 'win.nouveau-travail')
        section = Gio.Menu()
        section.append('Raccourcis clavier', 'win.raccourcis')
        section.append('À propos de Gagarine', 'app.a-propos')
        menu.append_section(None, section)
        bouton = Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, primary=True, tooltip_text='Menu principal')
        bouton.update_property([Gtk.AccessibleProperty.LABEL], ['Menu principal'])
        return bouton

    def montrer_raccourcis(self):
        xml = '''<interface><object class="GtkShortcutsWindow" id="w"><property name="modal">1</property>
<child><object class="GtkShortcutsSection"><property name="section-name">principal</property>
<property name="max-height">12</property>
<child><object class="GtkShortcutsGroup"><property name="title">Pages</property>%s</object></child>
<child><object class="GtkShortcutsGroup"><property name="title">Général</property>%s</object></child>
</object></child></object></interface>'''

        def raccourci(accel, titre):
            return (f'<child><object class="GtkShortcutsShortcut"><property name="accelerator">{accel}</property>'
                    f'<property name="title">{titre}</property></object></child>')
        pages = ''.join(raccourci(f'&lt;Control&gt;{i}', t) for i, t in enumerate(
            ['Aperçu', 'Programmes', 'File d’attente', 'Journal', 'Réglages'], 1))
        general = (raccourci('&lt;Control&gt;n', 'Nouveau travail') + raccourci('&lt;Control&gt;f', 'Rechercher dans le journal') +
                   raccourci('&lt;Control&gt;question', 'Raccourcis clavier') + raccourci('&lt;Control&gt;q', 'Quitter'))
        fenetre = Gtk.Builder.new_from_string(xml % (pages, general), -1).get_object('w')
        fenetre.set_transient_for(self)
        fenetre.present()

    # --- bandeau d'erreur ---
    def maj_banniere(self):
        """Erreurs de lecture ; et, en fenêtre étroite seulement (la barre latérale est alors cachée), le service arrêté."""
        erreurs = self.source.erreurs
        self.action_banniere = None
        if erreurs:
            self.banniere.set_title(next(iter(erreurs.values())))
            self.banniere.set_button_label(None)
            self.banniere.set_revealed(True)
        elif self.source.etat and not self.source.service_actif() and self.split.get_collapsed():
            self.banniere.set_title('Le service Gagarine est arrêté : la RAM n’est pas protégée.')
            self.banniere.set_button_label('Démarrer')
            self.action_banniere = self.demarrer_service
            self.banniere.set_revealed(True)
        else:
            self.banniere.set_revealed(False)

    def _action_banniere(self):
        if self.action_banniere:
            self.action_banniere()

    # --- actions ---
    def toast(self, texte, longue=False):
        t = Adw.Toast(title=texte, timeout=6 if longue else 3)
        self.toasts.add_toast(t)

    def executer(self, argv, message_ok, delai=None):
        """Lance une action en arrière-plan ; une notification dit le résultat, puis les données sont relues."""
        if delai is None:
            delai = 300 if argv[0] == 'pkexec' else 15   # pkexec attend le mot de passe

        def fini(res):
            if res.ok:
                self.toast(message_ok)
            else:
                self.toast(res.message, longue=True)
            self.source.rafraichir()
            self.source.rafraichir_journal()
            self.source.rafraichir_options()
        self.executeur.lancer(argv, fini, delai=delai)

    def _fermeture(self, *_):
        self.source.arreter()
        return False
