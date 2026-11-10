"""Application GTK : instance unique, page de départ, boîte « À propos »."""
import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import donnees
from .fenetre import PAGES, Fenetre

VERSION = '1.1.0'
DEPOT = 'https://github.com/mawena/gagarine'


def dossier_icones_depot():
    """data/icons du dépôt quand on tourne depuis les sources (là, hicolor n'a pas encore nos icônes), sinon None."""
    racine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dossier = os.path.join(racine, 'data', 'icons')
    if os.path.exists(os.path.join(racine, 'bin', 'gagarine-interface')) and os.path.isdir(dossier):
        return dossier
    return None


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=donnees.APPLICATION_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name('Gagarine')
        GLib.set_prgname(donnees.APPLICATION_ID)
        self.add_main_option('page', 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             'Page à ouvrir : ' + ', '.join(PAGES), 'NOM')
        self.fenetre = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        dossier = dossier_icones_depot()
        if dossier and Gdk.Display.get_default():
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(dossier)
        Gtk.Window.set_default_icon_name(donnees.APPLICATION_ID)
        a = Gio.SimpleAction.new('a-propos', None)
        a.connect('activate', lambda *_: self.a_propos())
        self.add_action(a)
        q = Gio.SimpleAction.new('quitter', None)
        q.connect('activate', lambda *_: self.quit())
        self.add_action(q)
        self.set_accels_for_action('app.quitter', ['<Control>q'])

    def do_activate(self):
        if self.fenetre is None:
            self.fenetre = Fenetre(self)
        self.fenetre.present()
        self.fenetre.set_focus(None)

    def a_propos(self):
        d = Adw.AboutDialog(application_name='Gagarine', application_icon=donnees.APPLICATION_ID, version=VERSION,
                            developer_name='mawena', developers=['mawena'], copyright='© 2026 mawena',
                            license_type=Gtk.License.MIT_X11, website=DEPOT, issue_url=DEPOT + '/issues',
                            comments='Garde-RAM : empêche la mémoire de se remplir au point de figer le poste.')
        d.present(self.fenetre)


AIDE = """Usage : gagarine-interface [--page PAGE | --help]

Interface graphique de Gagarine (GTK 4 + libadwaita).

  --page PAGE    ouvre la page demandée : """ + ', '.join(PAGES) + """
  -h, --help, --aide   affiche cette aide

Variables : GAGARINE_COMMANDE remplace la commande « gagarine » utilisée (développement, tests).
"""


def principal(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if any(a in ('-h', '--help', '--aide') for a in argv[1:]):
        print(AIDE, end='')   # sans affichage ni GTK : fonctionne aussi dans un terminal sans écran
        return 0
    page = None
    for i, a in enumerate(argv):
        if a.startswith('--page='):
            page = a.split('=', 1)[1]
        elif a == '--page' and i + 1 < len(argv):
            page = argv[i + 1]
    Gtk.init_check()
    if Gdk.Display.get_default() is None:
        sys.exit('Gagarine : aucun affichage disponible (écran graphique introuvable).')
    app = Application()
    if page in PAGES:
        app.connect_after('activate', lambda a: a.fenetre and a.fenetre.aller_a(page))
    return app.run(argv)
