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

    Signaux : « ram » (chaque seconde), « etat », « file », « journal », « erreurs ».
    """

    __gsignals__ = {n: (GObject.SignalFlags.RUN_FIRST, None, ()) for n in ('ram', 'etat', 'file', 'journal', 'erreurs')}

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
        self._minuteries = []

    # --- cycle de vie ---
    def demarrer(self):
        self.lire_ram()
        self.rafraichir()
        self.rafraichir_journal()
        self._minuteries = [GLib.timeout_add(1000, self._tic_ram),
                            GLib.timeout_add(PERIODE_COMMANDES, self._tic_commandes),
                            GLib.timeout_add(PERIODE_JOURNAL, self._tic_journal)]

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
