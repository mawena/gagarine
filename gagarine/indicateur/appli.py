"""Indicateur de la barre du haut : GTK 3 + AyatanaAppIndicator3, dans son propre processus.

Il ne parle au cœur de Gagarine qu'à travers la ligne de commande (`gagarine etat --json`, `gagarine file --etat --json`,
`gagarine menage --json`, `gagarine menage arreter ID`, `gagarine reprendre|arreter PID`). La RAM est lue directement dans /proc/meminfo. Il ne bloque et ne plante jamais :
quoi qu'il arrive à la commande, l'étiquette montre la RAM.
"""
import fcntl
import getpass
import os
import signal
import subprocess
import sys

from gagarine.indicateur import logique

INTERVALLE_S = 2
DELAI_MAX_S = 6  # une commande plus lente est arrêtée
AIDE = """Usage : gagarine-indicateur [--afficher | --masquer | --aide]

  (sans option)  lance l'indicateur dans la barre du haut (une seule instance)
  --masquer      ne plus le lancer à l'ouverture de session (écrit la surcharge d'autostart, Hidden=true)
  --afficher     le lancer de nouveau à l'ouverture de session (enlève la surcharge)
"""


def dossier_racine():
    """Racine du dépôt quand on tourne depuis les sources, None une fois installé."""
    ici = os.path.dirname(os.path.realpath(sys.argv[0] or __file__))
    return None if ici == '/usr/bin' else os.path.dirname(ici)


def prendre_verrou():
    """Verrou exclusif dans $XDG_RUNTIME_DIR. Rend le fichier ouvert (à garder), ou None si une instance tourne déjà."""
    dossier = os.environ.get('XDG_RUNTIME_DIR') or '/tmp'
    f = open(os.path.join(dossier, 'gagarine-indicateur.%d.lock' % os.getuid()), 'a')
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def importer_gtk():
    """Importe GTK 3 et AppIndicator ; en cas d'absence, dit quel paquet installer et rend None."""
    try:
        import gi
    except ImportError:
        print("gagarine-indicateur : PyGObject manque. Installer le paquet : python3-gi", file=sys.stderr)
        return None
    manquants = []
    modules = {}
    for nom, version, paquet in (('Gtk', '3.0', 'gir1.2-gtk-3.0'),
                                 ('AyatanaAppIndicator3', '0.1', 'gir1.2-ayatanaappindicator3-0.1')):
        try:
            gi.require_version(nom, version)
        except ValueError:
            manquants.append(paquet)
    if not manquants:
        try:
            from gi.repository import AyatanaAppIndicator3, Gio, GLib, Gtk
            modules = {'Gtk': Gtk, 'Gio': Gio, 'GLib': GLib, 'Indicateur': AyatanaAppIndicator3}
        except (ImportError, ValueError):
            manquants = ['gir1.2-gtk-3.0', 'gir1.2-ayatanaappindicator3-0.1']
    if manquants:
        print("gagarine-indicateur : il manque des bibliothèques. Installer les paquets : " + ' '.join(manquants),
              file=sys.stderr)
        return None
    return modules


class Indicateur:
    def __init__(self, m):
        self.Gtk, self.Gio, self.GLib, self.AI = m['Gtk'], m['Gio'], m['GLib'], m['Indicateur']
        self.utilisateur = getpass.getuser()
        self.etat = None
        self.file = None
        self.menage = None
        self.pourcent = None
        self.en_cours = False
        self.menu_actuel = None
        self.icone_actuelle = None
        self.indic = self.AI.Indicator.new('gagarine-indicateur', logique.nom_icone(logique.NORMAL),
                                           self.AI.IndicatorCategory.SYSTEM_SERVICES)
        racine = dossier_racine()
        if racine and os.path.isdir(os.path.join(racine, 'data', 'icons')):
            self.indic.set_icon_theme_path(os.path.join(racine, 'data', 'icons'))
        self.indic.set_title('Gagarine')
        self.indic.set_status(self.AI.IndicatorStatus.ACTIVE)
        self.rafraichir_affichage()
        self.tic()
        self.GLib.timeout_add_seconds(INTERVALLE_S, self.tic)

    # --- Mise à jour -----------------------------------------------------------------------------------------------
    def tic(self):
        try:
            with open('/proc/meminfo') as f:
                self.pourcent = logique.ram_depuis_meminfo(f.read())
        except OSError:
            self.pourcent = None
        self.rafraichir_affichage()
        if not self.en_cours:  # jamais deux appels en même temps
            self.en_cours = True
            self.lancer(['etat', '--json'], self.apres_etat)
        return True

    def lancer(self, arguments, suite):
        """Lance `gagarine <arguments>` sans bloquer ; suite(dictionnaire ou None) est appelée à la fin."""
        Gio = self.Gio
        try:
            proc = Gio.Subprocess.new(logique.commande_gagarine() + arguments,
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except self.GLib.Error:
            suite(None)
            return
        fini = {'oui': False}

        def delai():
            if not fini['oui']:
                proc.force_exit()
            return False

        def lu(p, resultat):
            fini['oui'] = True
            try:
                _, sortie, _ = p.communicate_utf8_finish(resultat)
                donnees = logique.lire_json(sortie) if p.get_successful() else None
            except self.GLib.Error:
                donnees = None
            suite(donnees)

        self.GLib.timeout_add_seconds(DELAI_MAX_S, delai)
        proc.communicate_utf8_async(None, None, lu)

    def apres_etat(self, donnees):
        self.etat = donnees
        self.lancer(['file', '--etat', '--json'], self.apres_file)

    def apres_file(self, donnees):
        self.file = donnees
        self.lancer(['menage', '--json'], self.apres_menage)

    def apres_menage(self, donnees):
        self.menage = donnees   # None si la commande est absente (cœur plus ancien) : l'entrée disparaît
        self.en_cours = False
        self.rafraichir_affichage()

    # --- Affichage -------------------------------------------------------------------------------------------------
    def rafraichir_affichage(self):
        try:
            niv = logique.niveau(self.pourcent, self.etat)
            texte = logique.etiquette(self.pourcent)
            if niv != self.icone_actuelle:
                self.icone_actuelle = niv
                self.indic.set_icon_full(logique.nom_icone(niv), logique.tooltip(niv, len(logique.figes(self.etat))))
            self.indic.set_label(texte, '100 %')
            contenu = logique.contenu_menu(self.pourcent, self.etat, self.file, self.utilisateur, self.menage)
            if contenu != self.menu_actuel:
                self.menu_actuel = contenu
                self.indic.set_menu(self.construire_menu(contenu))
        except Exception as e:  # l'indicateur ne doit jamais planter
            print('gagarine-indicateur : erreur d\'affichage : %s' % e, file=sys.stderr)

    def construire_menu(self, contenu):
        Gtk = self.Gtk
        menu = Gtk.Menu()
        for element in contenu:
            if element[0] == 'sep':
                ligne = Gtk.SeparatorMenuItem()
            elif element[0] == 'info':
                ligne = Gtk.MenuItem(label=element[1])
                ligne.set_sensitive(False)
            elif element[0] == 'fige':
                _, pid, titre, direct = element
                ligne = Gtk.MenuItem(label=titre)
                sous = Gtk.Menu()
                for action, libelle in (('reprendre', 'Reprendre'), ('arreter', 'Arrêter')):
                    sous_ligne = Gtk.MenuItem(label=libelle)
                    sous_ligne.connect('activate', self.agir, action, pid, titre, direct)
                    sous.append(sous_ligne)
                ligne.set_submenu(sous)
            elif element[0] == 'menage':
                _, titre, candidats = element
                ligne = Gtk.MenuItem(label=titre)
                sous = Gtk.Menu()
                for identifiant, libelle, action in candidats:
                    sous_ligne = Gtk.MenuItem(label=libelle)
                    sous_ligne.connect('activate', self.nettoyer, identifiant, libelle, action)
                    sous.append(sous_ligne)
                ligne.set_submenu(sous)
            else:
                _, identifiant, libelle = element
                ligne = Gtk.MenuItem(label=libelle)
                ligne.connect('activate', {'ouvrir': self.ouvrir, 'masquer': self.masquer}[identifiant])
            menu.append(ligne)
        menu.show_all()
        return menu

    # --- Actions ---------------------------------------------------------------------------------------------------
    def agir(self, _item, action, pid, titre, direct):
        if action == 'arreter' and not self.confirmer(titre):
            return
        commande = logique.commande_action(action, pid, direct, logique.commande_gagarine())
        try:
            self.Gio.Subprocess.new(commande, self.Gio.SubprocessFlags.STDOUT_SILENCE |
                                    self.Gio.SubprocessFlags.STDERR_SILENCE)
        except self.GLib.Error as e:
            print('gagarine-indicateur : %s : %s' % (' '.join(commande), e), file=sys.stderr)

    def nettoyer(self, _item, identifiant, libelle, action):
        decharger = action == 'decharger'
        if not self.confirmer_menage(libelle, decharger):
            return
        try:
            commande = logique.commande_menage(identifiant, logique.commande_gagarine())
            self.Gio.Subprocess.new(commande, self.Gio.SubprocessFlags.STDOUT_SILENCE |
                                    self.Gio.SubprocessFlags.STDERR_SILENCE)
        except (ValueError, self.GLib.Error) as e:
            print('gagarine-indicateur : ménage : %s' % e, file=sys.stderr)

    def confirmer_menage(self, libelle, decharger):
        Gtk = self.Gtk
        verbe = 'Décharger' if decharger else 'Arrêter'
        boite = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION,
                                  text='%s ce programme inactif ?' % verbe,
                                  secondary_text=('%s. Il se rechargera au prochain usage.' % libelle) if decharger else
                                  ('%s. S\'il a quelque chose de non enregistré, cela sera perdu.' % libelle))
        boite.add_button('Annuler', Gtk.ResponseType.CANCEL)
        boite.add_button(verbe, Gtk.ResponseType.OK)
        boite.set_default_response(Gtk.ResponseType.CANCEL)
        reponse = boite.run()
        boite.destroy()
        return reponse == Gtk.ResponseType.OK

    def confirmer(self, titre):
        Gtk = self.Gtk
        boite = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION, text='Arrêter ce programme ?',
                                  secondary_text='%s sera arrêté définitivement (SIGKILL) : son travail non enregistré '
                                                 'sera perdu.' % titre)
        boite.add_button('Annuler', Gtk.ResponseType.CANCEL)
        boite.add_button('Arrêter', Gtk.ResponseType.OK)
        boite.set_default_response(Gtk.ResponseType.CANCEL)
        reponse = boite.run()
        boite.destroy()
        return reponse == Gtk.ResponseType.OK

    def ouvrir(self, _item=None):
        racine = dossier_racine()
        candidats = [os.path.join(racine, 'bin', 'gagarine-interface')] if racine else []
        lanceur = next((c for c in candidats if os.path.exists(c)), 'gagarine-interface')
        try:
            subprocess.Popen([lanceur], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            print('gagarine-indicateur : impossible de lancer gagarine-interface : %s' % e, file=sys.stderr)

    def masquer(self, _item=None):
        try:
            logique.masquer()
        except OSError as e:
            print('gagarine-indicateur : impossible d\'écrire la surcharge d\'autostart : %s' % e, file=sys.stderr)
        self.Gtk.main_quit()


def main(argv):
    if argv in (['--afficher'], ['--masquer']):
        try:
            (logique.afficher if argv[0] == '--afficher' else logique.masquer)()
        except OSError as e:
            print('gagarine-indicateur : %s' % e, file=sys.stderr)
            return 1
        print('Indicateur %s à l\'ouverture de session.' % ('affiché' if argv[0] == '--afficher' else 'masqué'))
        return 0
    if argv:
        print(AIDE, end='', file=sys.stdout if argv in (['-h'], ['--aide'], ['--help']) else sys.stderr)
        return 0 if argv in (['-h'], ['--aide'], ['--help']) else 2
    verrou = prendre_verrou()
    if verrou is None:
        print('gagarine-indicateur : déjà lancé.', file=sys.stderr)
        return 0
    modules = importer_gtk()
    if modules is None:
        return 1
    indicateur = Indicateur(modules)  # noqa: F841 (garde la référence)
    for sig in (signal.SIGINT, signal.SIGTERM):
        modules['GLib'].unix_signal_add(modules['GLib'].PRIORITY_DEFAULT, sig, modules['Gtk'].main_quit)
    modules['Gtk'].main()
    return 0
