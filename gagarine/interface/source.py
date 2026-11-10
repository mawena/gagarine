"""Sources de données de l'interface : sous-processus asynchrones, lecture de /proc/meminfo, signaux vers les pages."""
import shutil
import time
from collections import deque

import gi

gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib, GObject

from . import donnees

DUREE_COURBE = 300          # secondes d'historique de la courbe (5 minutes)
DELAI_COMMANDE = 8          # secondes au plus pour une commande de lecture
PERIODE_COMMANDES = 2500    # ms entre deux lectures de l'état et de la file
PERIODE_JOURNAL = 5000      # ms entre deux lectures du journal
PERIODE_OPTIONS = 10000     # ms entre deux lectures du ménage et des applications
PERIODE_HISTORIQUE = 30000  # ms entre deux lectures de l'historique (une mesure par minute)
NOMBRE_EVENEMENTS = 200


class Resultat:
    """Fin d'une commande : code (None si non lancée ou trop longue), sortie, erreur et message d'erreur éventuel."""

    def __init__(self, argv, code=None, sortie='', erreur='', delai=False, absente=False):
        self.argv, self.code, self.sortie, self.erreur = argv, code, sortie or '', erreur or ''
        self.delai, self.absente = delai, absente
        self.message = donnees.message_erreur_commande(argv, code, self.erreur, delai, absente)
        if self.message is None and code is None:
            self.message = f'« {argv[0]} » n’a pas pu être lancée.'

    @property
    def ok(self):
        return self.message is None


class Executeur:
    """Lance des commandes sans jamais bloquer la boucle principale, avec un délai maximal."""

    def __init__(self):
        self.en_cours = {}

    def occupe(self, cle):
        return cle in self.en_cours

    def lancer(self, argv, rappel, delai=DELAI_COMMANDE, cle=None, repertoire=None):
        """Lance argv ; rappel(Resultat) est appelé dans la boucle principale. Un appel de même clé déjà en cours est
        ignoré (retourne False)."""
        if cle is not None and cle in self.en_cours:
            return False
        try:
            lanceur = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE |
                                                 Gio.SubprocessFlags.STDIN_INHERIT)
            if repertoire:
                lanceur.set_cwd(repertoire)
            lanceur.setenv('LC_ALL', 'C.UTF-8', False)
            processus = lanceur.spawnv(argv)
        except GLib.Error as e:
            absente = shutil.which(argv[0]) is None
            GLib.idle_add(rappel, Resultat(argv, absente=absente, erreur=e.message))
            return True
        annulation = Gio.Cancellable()
        etat = {'delai': False}
        if cle is not None:
            self.en_cours[cle] = processus

        def trop_long():
            etat['delai'] = True
            annulation.cancel()
            processus.force_exit()
            return GLib.SOURCE_REMOVE

        minuterie = GLib.timeout_add_seconds(delai, trop_long)

        def fini(proc, res):
            if not etat['delai']:
                GLib.source_remove(minuterie)
            self.en_cours.pop(cle, None)
            try:
                _, sortie, erreur = proc.communicate_utf8_finish(res)
                code = proc.get_exit_status() if proc.get_if_exited() else 128 + proc.get_term_sig()
                resultat = Resultat(argv, code, sortie, erreur)
            except GLib.Error:
                resultat = Resultat(argv, delai=etat['delai'], erreur='' if etat['delai'] else 'échec de lecture')
            rappel(resultat)

        processus.communicate_utf8_async(None, annulation, fini)
        return True


class Source(GObject.Object):
    """Dernières données connues (RAM, état, file, journal, erreurs), mises à jour en continu, et signaux de changement.

    Signaux : « ram » (chaque seconde), « etat », « file », « journal », « erreurs », et pour les commandes de la 1.2.0
    « menage », « applications », « appris », « historique ». Ces dernières sont facultatives : si le `gagarine` installé
    ne les connaît pas, la section reste masquée (`disponible(cle)` faux) et aucune erreur n'est montrée dans le bandeau.
    """

    __gsignals__ = {n: (GObject.SignalFlags.RUN_FIRST, None, ())
                    for n in ('ram', 'etat', 'file', 'journal', 'erreurs', 'menage', 'applications', 'appris', 'historique')}

    def __init__(self, executeur=None):
        super().__init__()
        self.executeur = executeur or Executeur()
        self.base = donnees.commande_gagarine()
        self.ram_pourcent = None
        self.ram_total_mo = None
        self.ram_dispo_mo = None
        self.historique = deque()   # (instant monotone, pourcent)
        self.etat = None
        self.file = None
        self.journal = None
        self.erreurs = {}
        self.menage = None
        self.applications = None
        self.appris = None
        self.historiques = {}       # période ('24h', '7j') -> historique normalisé
        self.periodes_suivies = set()
        self.options = {}           # clé -> (message d'erreur, commande inconnue ?) ; absente si tout va bien
        self._minuteries = []

    # --- cycle de vie ---
    def demarrer(self):
        self.lire_ram()
        self.rafraichir()
        self.rafraichir_journal()
        self.rafraichir_options()
        self._minuteries = [GLib.timeout_add(1000, self._tic_ram),
                            GLib.timeout_add(PERIODE_COMMANDES, self._tic_commandes),
                            GLib.timeout_add(PERIODE_JOURNAL, self._tic_journal),
                            GLib.timeout_add(PERIODE_OPTIONS, self._tic_options),
                            GLib.timeout_add(PERIODE_HISTORIQUE, self._tic_historique)]

    def arreter(self):
        for m in self._minuteries:
            GLib.source_remove(m)
        self._minuteries = []

    def _tic_ram(self):
        self.lire_ram()
        return GLib.SOURCE_CONTINUE

    def _tic_commandes(self):
        self.rafraichir()
        return GLib.SOURCE_CONTINUE

    def _tic_journal(self):
        self.rafraichir_journal()
        return GLib.SOURCE_CONTINUE

    def _tic_options(self):
        self.rafraichir_options()
        return GLib.SOURCE_CONTINUE

    def _tic_historique(self):
        for periode in sorted(self.periodes_suivies):
            self.rafraichir_historique(periode)
        return GLib.SOURCE_CONTINUE

    # --- RAM, lue dans le processus ---
    def lire_ram(self):
        try:
            with open('/proc/meminfo', encoding='utf-8') as f:
                total, dispo, pct = donnees.lire_meminfo(f.read())
        except (OSError, donnees.ErreurDonnees):
            return
        self.ram_total_mo, self.ram_dispo_mo, self.ram_pourcent = total, dispo, pct
        maintenant = time.monotonic()
        self.historique.append((maintenant, pct))
        while self.historique and maintenant - self.historique[0][0] > DUREE_COURBE + 5:
            self.historique.popleft()
        self.emit('ram')

    # --- commandes JSON ---
    def rafraichir(self):
        self._lire('etat', self.base + ['etat', '--json'], donnees.lire_etat)
        self._lire('file', self.base + ['file', '--etat', '--json'], donnees.lire_file)

    def rafraichir_journal(self):
        self._lire('journal', self.base + ['journal', '--json', '-n', str(NOMBRE_EVENEMENTS)], donnees.lire_journal)

    def rafraichir_options(self):
        """Ménage, applications du bureau et mémoire apprise (commandes de la 1.2.0, facultatives)."""
        self._lire_option('menage', self.base + ['menage', '--json'], donnees.lire_menage)
        self._lire_option('applications', self.base + ['applications', '--json'], donnees.lire_applications)
        self._lire_option('appris', self.base + ['file', 'appris', '--json'], donnees.lire_appris)

    def suivre_historique(self, periode):
        """Demande l'historique d'une période ('24h', '7j') et le garde à jour toutes les 30 s."""
        self.periodes_suivies.add(periode)
        self.rafraichir_historique(periode)

    def rafraichir_historique(self, periode):
        self._lire_option('historique-' + periode, self.base + ['historique', '--json', '--depuis', periode],
                          donnees.lire_historique, 'historique', lambda v: self.historiques.__setitem__(periode, v))

    def valeur(self, cle):
        """Dernières données d'une commande facultative, ou None."""
        if cle.startswith('historique-'):
            return self.historiques.get(cle[len('historique-'):])
        return getattr(self, cle, None)

    def etat_option(self, cle):
        """(« ok », None) si les données sont là ; (« absente », message) si la commande est inconnue du `gagarine` installé
        (section à masquer) ; (« erreur », message) pour une vraie panne ; (« attente », None) avant la première réponse."""
        if self.valeur(cle) is not None:
            return 'ok', None
        if cle not in self.options:
            return 'attente', None
        message, inconnue = self.options[cle]
        return ('absente' if inconnue else 'erreur'), message

    def _lire_option(self, cle, argv, lecteur, signal=None, stocker=None):
        def fini(res):
            if res.ok:
                try:
                    (stocker or (lambda v: setattr(self, cle, v)))(lecteur(res.sortie))
                    self.options.pop(cle, None)
                except donnees.ErreurDonnees as e:
                    self.options[cle] = (str(e), False)
            else:
                # code 2 = commande inconnue (cœur plus ancien) ; commande absente : la section reste masquée
                self.options[cle] = (res.message, res.code == 2 or res.absente)
            self.emit(signal or cle)
        self.executeur.lancer(argv, fini, cle=cle)

    def _lire(self, cle, argv, lecteur):
        def fini(res):
            if res.ok:
                try:
                    setattr(self, cle, lecteur(res.sortie))
                    self.erreurs.pop(cle, None)
                except donnees.ErreurDonnees as e:
                    self.erreurs[cle] = str(e)
            else:
                self.erreurs[cle] = res.message
            self.emit(cle)
            self.emit('erreurs')
        self.executeur.lancer(argv, fini, cle=cle)

    # --- aides pour les pages ---
    def reglages(self):
        return (self.etat or {}).get('reglages') or {}

    def niveau(self):
        """Niveau de la RAM seule (couleur de la jauge et de la courbe)."""
        return donnees.niveau_ram(self.ram_pourcent, self.reglages())

    def nombre_figes(self):
        return len((self.etat or {}).get('figes', []))

    def service_actif(self):
        return ((self.etat or {}).get('service') or {}).get('etat') == 'active'
