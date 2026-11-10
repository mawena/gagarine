"""Page « Réglages » : seuils, délais, service, essai et indicateur de la barre du haut."""
import os
import subprocess

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from . import donnees
from .outils import bouton, confirmer, echappe, etiquette


class PageReglages(Adw.PreferencesPage):
    def __init__(self, fenetre):
        super().__init__(title='Réglages', name='reglages', icon_name='preferences-system-symbolic')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.courants = {}
        self.champs = {}
        self.en_cours_chargement = False
        self.essai = None

        # --- seuils et délais ---
        g_seuils = Adw.PreferencesGroup(title='Seuils', description='Pourcentage de RAM utilisée à partir duquel Gagarine agit.')
        g_delais = Adw.PreferencesGroup(title='Délais et bridage')
        for cle, titre, aide, unite, mini, maxi in donnees.REGLAGES_NOMBRES:
            ligne = Adw.SpinRow.new_with_range(mini, maxi, 1)
            ligne.set_title(f'{titre} ({unite})')
            ligne.set_subtitle(aide)
            ligne.connect('notify::value', self._modifie)
            self.champs[cle] = ligne
            (g_seuils if unite == '%' else g_delais).add(ligne)
        self.add(g_seuils)
        self.add(g_delais)

        g_autres = Adw.PreferencesGroup(title='Notifications et protection')
        self.notifications = Adw.SwitchRow(title='Notifications', subtitle='Boutons « Arrêter » et « Reprendre » dans le bureau')
        self.notifications.connect('notify::active', self._modifie)
        self.proteges = Adw.EntryRow(title='Programmes protégés en plus (expression régulière)')
        self.proteges.connect('changed', self._modifie)
        g_autres.add(self.notifications)
        g_autres.add(self.proteges)
        self.add(g_autres)

        g_enreg = Adw.PreferencesGroup()
        self.message_reglages = Gtk.Label(wrap=True, xalign=0, label='', margin_top=6)
        self.message_reglages.add_css_class('dim-label')
        self.bouton_enregistrer = Gtk.Button(label='Enregistrer', halign=Gtk.Align.END, sensitive=False)
        self.bouton_enregistrer.add_css_class('suggested-action')
        self.bouton_enregistrer.add_css_class('pill')
        self.bouton_enregistrer.connect('clicked', lambda _b: self.enregistrer())
        self.bouton_annuler = Gtk.Button(label='Annuler les modifications', halign=Gtk.Align.END, sensitive=False)
        self.bouton_annuler.add_css_class('pill')
        self.bouton_annuler.connect('clicked', lambda _b: self.recharger(force=True))
        boutons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        boutons.append(self.bouton_annuler)
        boutons.append(self.bouton_enregistrer)
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        boite.append(boutons)
        boite.append(self.message_reglages)
        g_enreg.add(boite)
        self.add(g_enreg)

        # --- service ---
        g_service = Adw.PreferencesGroup(title='Service', description='Le service Gagarine tourne en arrière-plan, avec les droits '
                                         'd’administrateur. Chaque action demande votre mot de passe.')
        self.ligne_service = Adw.ActionRow(title='Service Gagarine', subtitle='État inconnu')
        self.etat_service = etiquette('', ['heading'])
        self.ligne_service.add_suffix(self.etat_service)
        g_service.add(self.ligne_service)
        ligne_boutons = Adw.ActionRow(title='Actions')
        self.boutons_service = {
            'demarrer': bouton('Démarrer', lambda: self.service('demarrer', 'Service démarré')),
            'arreter': bouton('Arrêter', self.demander_arret_service, ['destructive-action']),
            'redemarrer': bouton('Redémarrer', lambda: self.service('redemarrer', 'Service redémarré')),
        }
        for b in self.boutons_service.values():
            ligne_boutons.add_suffix(b)
        g_service.add(ligne_boutons)
        self.add(g_service)

        # --- essai ---
        g_essai = Adw.PreferencesGroup(
            title='Essai', description='Un programme d’essai grossit : Gagarine doit le figer, le ranger dans le swap puis '
                                       'l’arrêter. Sans risque pour vos autres programmes ; refusé au-delà de 80 % de RAM.')
        ligne_essai = Adw.ActionRow(title='Vérifier que la protection fonctionne',
                                    subtitle='En administrateur, l’essai teste aussi le rangement dans le swap')
        self.lancer_essai = bouton('Lancer l’essai', lambda: self.lancer_essai_cmd(False))
        self.lancer_essai_admin = bouton('En administrateur', lambda: self.lancer_essai_cmd(True))
        ligne_essai.add_suffix(self.lancer_essai)
        ligne_essai.add_suffix(self.lancer_essai_admin)
        g_essai.add(ligne_essai)
        self.verdict = Adw.ActionRow(title='')
        self.icone_verdict = Gtk.Image()
        self.verdict.add_prefix(self.icone_verdict)
        liste_verdict = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        liste_verdict.add_css_class('boxed-list')
        liste_verdict.append(self.verdict)
        self.liste_verdict = liste_verdict
        liste_verdict.set_visible(False)
        self.sortie_essai = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, top_margin=8, bottom_margin=8,
                                         left_margin=10, right_margin=10, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.defile_essai = Gtk.ScrolledWindow(child=self.sortie_essai, min_content_height=130, visible=False)
        self.defile_essai.add_css_class('card')
        zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12)
        zone.append(liste_verdict)
        zone.append(self.defile_essai)
        g_essai.add(zone)
        self.add(g_essai)

        # --- indicateur ---
        g_ind = Adw.PreferencesGroup(title='Barre du haut')
        self.ligne_indicateur = Adw.SwitchRow(title='Afficher dans la barre du haut',
                                              subtitle='Montre la RAM en permanence et donne accès aux actions urgentes')
        self.chemin_autostart = donnees.fichier_autostart_indicateur()
        self.commande_indicateur = trouver_indicateur()
        self.ligne_indicateur.set_active(donnees.indicateur_affiche(self.chemin_autostart))
        if not self.commande_indicateur:
            self.ligne_indicateur.set_sensitive(False)
            self.ligne_indicateur.set_subtitle('L’indicateur n’est pas installé (paquet gagarine, programme gagarine-indicateur)')
        self.ligne_indicateur.connect('notify::active', self._indicateur_change)
        g_ind.add(self.ligne_indicateur)
        self.add(g_ind)

        self.source.connect('etat', lambda *_: self.recharger())
        self.recharger()

    # --- réglages ---
    def valeurs(self):
        v = {cle: int(ch.get_value()) for cle, ch in self.champs.items()}
        v['notifications'] = self.notifications.get_active()
        v['proteges'] = self.proteges.get_text().strip()
        return v

    def recharger(self, force=False):
        etat = self.source.etat
        if not etat or not etat['reglages']:
            return
        if not force and self.modifie():
            self.maj_service()
            return
        self.courants = dict(etat['reglages'])
        self.en_cours_chargement = True
        for cle, ch in self.champs.items():
            if self.courants.get(cle) is not None:
                ch.set_value(self.courants[cle])
        self.notifications.set_active(bool(self.courants.get('notifications', True)))
        self.proteges.set_text(str(self.courants.get('proteges') or ''))
        self.en_cours_chargement = False
        self._modifie()
        self.maj_service()

    def modifie(self):
        return donnees.arguments_reglages([], self.courants, self.valeurs()) is not None if self.courants else False

    def _modifie(self, *_):
        if self.en_cours_chargement:
            return
        sale = self.modifie()
        erreur = donnees.verifier_reglages(self.valeurs()) if sale else None
        self.bouton_enregistrer.set_sensitive(sale and erreur is None)
        self.bouton_annuler.set_sensitive(sale)
        self.message_reglages.set_label(erreur or ('L’enregistrement demande votre mot de passe d’administrateur.' if sale else ''))
        for cle in ('vigilance', 'seuil', 'urgence'):
            (self.champs[cle].add_css_class if erreur else self.champs[cle].remove_css_class)('error')

    def enregistrer(self):
        args = donnees.arguments_reglages(donnees.commande_admin(), self.courants, self.valeurs())
        if not args:
            return
        self.bouton_enregistrer.set_sensitive(False)
        valeurs = self.valeurs()

        def fini(res):
            if res.ok:
                self.courants.update(valeurs)
                self._modifie()
                self.fenetre.toast('Réglages enregistrés')
                self.source.rafraichir()
            else:
                self.fenetre.toast(res.message, longue=True)
                self._modifie()
        self.fenetre.source.executeur.lancer(args, fini, delai=300, cle='reglages-ecrire')

    # --- service ---
    def maj_service(self):
        etat = self.source.etat
        if not etat:
            return
        actif = self.source.service_actif()
        nom = etat['service']['etat']
        self.etat_service.set_label('Actif' if actif else ('Arrêté' if nom in ('inactive', 'failed') else nom.capitalize()))
        for c in ('success', 'error'):
            self.etat_service.remove_css_class(c)
        self.etat_service.add_css_class('success' if actif else 'error')
        self.ligne_service.set_subtitle(f'Fichier de réglages : {etat["fichier_reglages"]}')
        self.boutons_service['demarrer'].set_sensitive(not actif)
        self.boutons_service['arreter'].set_sensitive(actif)
        self.boutons_service['redemarrer'].set_sensitive(actif)

    def service(self, action, message):
        self.fenetre.executer(donnees.commande_admin() + ['service', action], message)

    def demander_arret_service(self):
        confirmer(self.fenetre, 'Arrêter le service Gagarine ?',
                  'La RAM ne sera plus protégée tant que le service sera arrêté : un programme qui grossit pourrait figer le poste.',
                  'Arrêter le service', lambda: self.service('arreter', 'Service arrêté'))

    # --- essai ---
    def lancer_essai_cmd(self, admin):
        if self.essai:
            self.essai.force_exit()
            return
        base = donnees.commande_admin() if admin else donnees.commande_gagarine()
        argv = base + ['essai']
        try:
            lanceur = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
            lanceur.setenv('PYTHONUNBUFFERED', '1', True)
            self.essai = lanceur.spawnv(argv)
        except GLib.Error:
            self._verdict(False, f'La commande « {argv[0]} » est introuvable.')
            return
        self.sortie_texte = ''
        self.sortie_essai.get_buffer().set_text('')
        self.defile_essai.set_visible(True)
        self.liste_verdict.set_visible(False)
        for b in (self.lancer_essai, self.lancer_essai_admin):
            b.set_sensitive(False)
        self.lancer_essai.set_label('Arrêter')
        self.lancer_essai.set_sensitive(not admin)   # pkexec : on n'interrompt pas un processus d'administrateur
        self.flux = Gio.DataInputStream.new(self.essai.get_stdout_pipe())
        self.delai_essai = GLib.timeout_add_seconds(300, self._delai_essai)
        self.flux.read_line_async(GLib.PRIORITY_DEFAULT, None, self._ligne_essai, self.essai)
        self.essai.wait_async(None, self._fin_essai)

    def _delai_essai(self):
        if self.essai:
            self.essai.force_exit()
        self.delai_essai = 0
        return GLib.SOURCE_REMOVE

    def _ligne_essai(self, flux, res, processus):
        try:
            ligne, _ = flux.read_line_finish_utf8(res)
        except GLib.Error:
            return
        if ligne is None:
            return
        self.sortie_texte += ligne + '\n'
        tampon = self.sortie_essai.get_buffer()
        tampon.insert(tampon.get_end_iter(), ligne + '\n')
        GLib.idle_add(lambda: self.defile_essai.get_vadjustment().set_value(self.defile_essai.get_vadjustment().get_upper()))
        flux.read_line_async(GLib.PRIORITY_DEFAULT, None, self._ligne_essai, processus)

    def _fin_essai(self, processus, res):
        try:
            processus.wait_finish(res)
        except GLib.Error:
            pass
        if getattr(self, 'delai_essai', 0):
            GLib.source_remove(self.delai_essai)
            self.delai_essai = 0
        code = processus.get_exit_status() if processus.get_if_exited() else 128 + processus.get_term_sig()
        self.essai = None
        self.lancer_essai.set_label('Lancer l’essai')
        for b in (self.lancer_essai, self.lancer_essai_admin):
            b.set_sensitive(True)
        if code in (126, 127) and not self.sortie_texte.strip().startswith('Essai'):
            self._verdict(False, 'L’autorisation d’administrateur a été refusée ou annulée.')
        elif code >= 128:
            self._verdict(False, 'L’essai a été interrompu.')
        else:
            self._verdict(*donnees.verdict_essai(self.sortie_texte, code))

    def _verdict(self, reussi, texte):
        self.verdict.set_title(echappe(texte))
        self.icone_verdict.set_from_icon_name('object-select-symbolic' if reussi else 'dialog-warning-symbolic')
        for c in ('success', 'error'):
            self.icone_verdict.remove_css_class(c)
        self.icone_verdict.add_css_class('success' if reussi else 'error')
        self.liste_verdict.set_visible(True)

    # --- indicateur ---
    def _indicateur_change(self, *_):
        affiche = self.ligne_indicateur.get_active()
        if affiche == donnees.indicateur_affiche(self.chemin_autostart) and not getattr(self, '_forcer', False):
            return
        # le fichier d'autostart est écrit par l'indicateur lui-même (--afficher / --masquer) : une seule écriture
        try:
            subprocess.run(self.commande_indicateur + ['--afficher' if affiche else '--masquer'], check=True, timeout=10,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            self.fenetre.toast(f'Réglage impossible : {getattr(e, "strerror", None) or e}', longue=True)
            return
        try:
            if affiche:
                subprocess.Popen(self.commande_indicateur, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            else:   # arrête seulement l'indicateur de l'utilisateur (« python3 …/gagarine-indicateur » en entier)
                subprocess.run(['pkill', '-u', str(os.getuid()), '-f', r'^/usr/bin/python3 \S*gagarine-indicateur$'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        self.fenetre.toast('Indicateur affiché dans la barre du haut' if affiche else 'Indicateur masqué')


def trouver_indicateur():
    """Commande de l'indicateur (variable GAGARINE_INDICATEUR, sinon `gagarine-indicateur` du PATH ou de bin/), ou None."""
    env = os.environ.get('GAGARINE_INDICATEUR', '').strip()
    if env:
        return env.split()
    chemin = GLib.find_program_in_path('gagarine-indicateur')
    if not chemin:
        voisin = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                              'bin', 'gagarine-indicateur')
        chemin = voisin if os.access(voisin, os.X_OK) else None
    return [chemin] if chemin else None
