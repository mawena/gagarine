"""Fenêtre principale : pages, barre d'en-tête, menu, bandeau d'erreur et notifications internes."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from . import donnees
from .apercu import PageApercu
from .file_attente import PageFile
from .journal import PageJournal
from .programmes import PageProgrammes
from .reglages import PageReglages
from .source import Source

PAGES = ['apercu', 'programmes', 'file', 'journal', 'reglages']


class Fenetre(Adw.ApplicationWindow):
    def __init__(self, application, source=None):
        super().__init__(application=application, title='Gagarine', default_width=920, default_height=760)
        self.set_size_request(420, 400)
        self.source = source or Source()
        self.executeur = self.source.executeur

        self.pile = Adw.ViewStack()
        self.pages = {}
        for classe in (PageApercu, PageProgrammes, PageFile, PageJournal, PageReglages):
            page = classe(self)
            self.pages[page.get_name()] = page
            self.pile.add_titled_with_icon(page, page.get_name(), page.get_title(), page.get_icon_name())

        self.banniere = Adw.Banner(revealed=False)
        self.banniere.connect('button-clicked', lambda _b: self._action_banniere())
        self.action_banniere = None

        entete = Adw.HeaderBar()
        self.commutateur = Adw.ViewSwitcher(policy=Adw.ViewSwitcherPolicy.WIDE, stack=self.pile)
        entete.set_title_widget(self.commutateur)
        entete.pack_end(self._menu())
        self.barre_bas = Adw.ViewSwitcherBar(stack=self.pile)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(self.pile)
        vue = Adw.ToolbarView()
        vue.add_top_bar(entete)
        vue.add_top_bar(self.banniere)
        vue.set_content(self.toasts)
        vue.add_bottom_bar(self.barre_bas)
        self.set_content(vue)

        # fenêtre étroite : le sélecteur de pages passe en bas
        condition = Adw.BreakpointCondition.parse('max-width: 780sp')
        point = Adw.Breakpoint.new(condition)
        point.add_setter(self.barre_bas, 'reveal', True)
        point.add_setter(self.commutateur, 'visible', False)
        self.add_breakpoint(point)

        self._raccourcis()
        self.source.connect('erreurs', lambda *_: self.maj_banniere())
        self.source.connect('etat', lambda *_: self.maj_banniere())
        self.source.demarrer()
        self.connect('close-request', self._fermeture)

    # --- navigation ---
    def aller_a(self, nom):
        if nom in self.pages:
            self.pile.set_visible_child_name(nom)

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
        return Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, primary=True, tooltip_text='Menu principal')

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
        erreurs = self.source.erreurs
        self.action_banniere = None
        if erreurs:
            self.banniere.set_title(next(iter(erreurs.values())))
            self.banniere.set_button_label(None)
            self.banniere.set_revealed(True)
        elif self.source.etat and not self.source.service_actif():
            self.banniere.set_title('Le service Gagarine est arrêté : la RAM n’est pas protégée.')
            self.banniere.set_button_label('Démarrer le service')
            self.action_banniere = lambda: self.executer(donnees.commande_admin() + ['service', 'demarrer'], 'Service démarré')
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
        self.executeur.lancer(argv, fini, delai=delai)

    def _fermeture(self, *_):
        self.source.arreter()
        return False
