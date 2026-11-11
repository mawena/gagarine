"""Page « Réglages » (1.5.0) : seuils, délais, file, service, essai et indicateur, en deux colonnes sur grand écran.

Colonne de gauche, la garde : seuils, agir avant le seuil, délais et bridage, programmes protégés, notifications.
Colonne de droite : file d'attente (et sessions Claude), fuites, ménage, service, essai, barre du haut. Les colonnes
s'empilent sous 1000 sp de page (outils.PageLarge). Une barre fixe en bas de la page apparaît dès qu'un réglage est
modifié : « Annuler les modifications » et « Enregistrer » (avec le mot de passe d'administrateur, par pkexec).
"""
import os
import subprocess

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from . import donnees
from .outils import PageLarge, bouton, confirmer, echappe, etiquette

# Répartition des groupes : (clé, colonne). Les hauteurs des deux colonnes restent proches.
GROUPES_GAUCHE = ('seuils', 'anticipation', 'delais', 'proteges', 'notifications')
GROUPES_DROITE = ('file', 'fuites', 'menage', 'service', 'essai', 'indicateur')


class PageReglages(PageLarge):
    def __init__(self, fenetre):
        super().__init__('Réglages', 'reglages', 'preferences-system-symbolic')
        self.add_css_class('page-reglages')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.courants = {}
        self.champs = {}
        self.interrupteurs = {}
        self.groupes = {}
        self.en_cours_chargement = False
        self.essai = None

        gauche, droite = self.colonnes(2)
        self.colonne_gauche, self.colonne_droite = gauche, droite
        self.rangee = gauche.get_parent()

        def groupe(cle, colonne, titre, description=None):
            g = Adw.PreferencesGroup(title=titre, description=description)
            colonne.append(g)
            self.groupes[cle] = g
            return g

        # --- colonne de gauche : la garde ---
        g_seuils = groupe('seuils', gauche, 'Seuils', 'Pourcentage de RAM utilisée à partir duquel Gagarine agit.')
        g_tot = groupe('anticipation', gauche, 'Agir avant le seuil',
                       'Gagarine peut intervenir avant que la RAM n’atteigne le seuil.')
        g_delais = groupe('delais', gauche, 'Délais et bridage',
                          'Le temps laissé à un programme figé, et ce qu’un programme repris peut encore demander.')
        for cle, titre, aide, unite, mini, maxi in donnees.REGLAGES_NOMBRES:
            ligne = Adw.SpinRow.new_with_range(mini, maxi, 1)
            ligne.set_title(f'{titre} ({unite})')
            ligne.set_subtitle(aide)
            ligne.connect('notify::value', self._modifie)
            self.champs[cle] = ligne
            (g_seuils if unite == '%' else g_delais).add(ligne)
        for cle, titre, aide, unite, mini, maxi in donnees.REGLAGES_ANTICIPATION:
            g_tot.add(self._ligne_nombre(cle, titre, aide, unite, mini, maxi))

        g_proteges = groupe('proteges', gauche, 'Programmes protégés',
                            'Gagarine ne fige ni n’arrête jamais ces programmes, en plus de sa liste intégrée '
                            '(systemd, gnome-shell, sshd, dbus…).')
        self.proteges = Adw.EntryRow(title='Noms de programmes, en expression régulière (ex. postgres|mysqld)')
        self.proteges.connect('changed', self._modifie)
        g_proteges.add(self.proteges)

        g_notif = groupe('notifications', gauche, 'Notifications',
                         'Sur le bureau, et sur le téléphone avec ntfy.')
        self.notifications = Adw.SwitchRow(title='Boutons dans les notifications',
                                           subtitle='Boutons « Arrêter » et « Reprendre » dans le bureau')
        self.notifications.connect('notify::active', self._modifie)
        g_notif.add(self.notifications)
        g_notif.add(self._ligne_interrupteur('notifications_fin'))
        for cle, titre, aide, unite, mini, maxi in donnees.REGLAGES_NOTIFICATIONS:
            g_notif.add(self._ligne_nombre(cle, titre, aide, unite, mini, maxi))
        self.ntfy = Adw.EntryRow(title='ntfy : sujet ou adresse (notifications sur le téléphone)')
        self.ntfy.connect('changed', self._modifie)
        self.essai_ntfy = bouton('Envoyer un essai', self.envoyer_essai_ntfy, ['flat'],
                                 aide='Envoie un message d’essai à ce sujet ntfy')
        self.essai_ntfy.set_sensitive(False)
        self.ntfy.add_suffix(self.essai_ntfy)
        g_notif.add(self.ntfy)

        # --- colonne de droite : file, fuites, ménage, puis ce qui agit tout de suite ---
        g_file = groupe('file', droite, 'File d’attente',
                        'Les travaux lourds passent par la file : combien à la fois, et ce qui est refusé hors d’elle.')
        for cle, titre, aide, unite, mini, maxi in donnees.REGLAGES_FILE_130:
            g_file.add(self._ligne_nombre(cle, titre, aide, unite, mini, maxi))
        g_file.add(self._ligne_interrupteur('file_ajuster'))
        g_file.add(self._ligne_interrupteur('claude_bloquer'))      # sessions Claude : travaux lourds hors de la file
        g_fuites = groupe('fuites', droite, 'Fuites de mémoire',
                          'Gagarine repère un programme qui grossit régulièrement, bien avant le seuil.')
        for cle, titre, aide, unite, mini, maxi in donnees.REGLAGES_FUITES:
            g_fuites.add(self._ligne_nombre(cle, titre, aide, unite, mini, maxi))
        g_fuites.add(self._ligne_interrupteur('fuite_notifier'))
        g_menage = groupe('menage', droite, 'Ménage',
                          'Démons Gradle et Kotlin, modèles de LM Studio et d’Ollama restés inactifs.')
        g_menage.add(self._ligne_interrupteur('menage_auto'))
        for cle, titre, aide, unite, mini, maxi in donnees.REGLAGES_MENAGE:
            g_menage.add(self._ligne_nombre(cle, titre, aide, unite, mini, maxi))

        self._service(groupe('service', droite, 'Service',
                             'Le service Gagarine tourne en arrière-plan, avec les droits d’administrateur. Chaque '
                             'action demande votre mot de passe.'))
        self._essai(groupe('essai', droite, 'Essai',
                           'Un programme d’essai grossit : Gagarine doit le figer, le ranger dans le swap puis '
                           'l’arrêter. Sans risque pour vos autres programmes ; refusé au-delà de 80 % de RAM.'))
        self._indicateur(groupe('indicateur', droite, 'Barre du haut',
                                'L’indicateur de Gagarine, à côté de l’horloge, s’affiche à l’ouverture de session.'))

        # --- barre d'enregistrement, fixe en bas de la page, visible seulement quand un réglage est modifié ---
        self._barre_enregistrement()

        self.source.connect('etat', lambda *_: self.recharger())
        self.recharger()

    # --- construction ---
    def _barre_enregistrement(self):
        self.titre_modifs = Gtk.Label(label='Modifications non enregistrées', xalign=0, wrap=True)
        self.titre_modifs.add_css_class('heading')
        self.message_reglages = Gtk.Label(wrap=True, xalign=0, label='')
        self.message_reglages.add_css_class('message-reglages')
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        textes.append(self.titre_modifs)
        textes.append(self.message_reglages)
        self.bouton_annuler = Gtk.Button(label='Annuler les modifications', valign=Gtk.Align.CENTER, sensitive=False)
        self.bouton_annuler.connect('clicked', lambda _b: self.recharger(force=True))
        self.bouton_enregistrer = Gtk.Button(label='Enregistrer', valign=Gtk.Align.CENTER, sensitive=False)
        self.bouton_enregistrer.add_css_class('suggested-action')
        self.bouton_enregistrer.connect('clicked', lambda _b: self.enregistrer())
        boutons = Gtk.Box(spacing=10, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        boutons.append(self.bouton_annuler)
        boutons.append(self.bouton_enregistrer)
        self.barre = Gtk.Box(spacing=16, margin_start=28, margin_end=28, margin_top=10, margin_bottom=10)
        self.barre.add_css_class('barre-enregistrement')
        self.barre.append(textes)
        self.barre.append(boutons)
        # fenêtre étroite : le message au-dessus, les boutons dessous
        for prop, valeur in (('margin-start', 14), ('margin-end', 14), ('orientation', Gtk.Orientation.VERTICAL),
                             ('spacing', 8)):
            self.point_etroit.add_setter(self.barre, prop, valeur)
        self.point_etroit.add_setter(self.bouton_annuler, 'label', 'Annuler')

        # la page de PageLarge passe dans une vue à barre fixe en bas
        self.set_child(None)
        self.vue = Adw.ToolbarView(content=self.defilement, bottom_bar_style=Adw.ToolbarStyle.RAISED,
                                   reveal_bottom_bars=False)
        self.vue.add_bottom_bar(self.barre)
        self.set_child(self.vue)

    def _service(self, g_service):
        self.ligne_service = Adw.ActionRow(title='Service Gagarine', subtitle='État inconnu')
        self.voyant_service = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, margin_start=4, margin_end=4,
                                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.voyant_service.add_css_class('voyant')
        self.voyant_service.add_css_class('eteint')
        self.ligne_service.add_prefix(self.voyant_service)
        self.etat_service = etiquette('', ['heading'])
        self.ligne_service.add_suffix(self.etat_service)
        g_service.add(self.ligne_service)
        self.ligne_commandes = Adw.ActionRow(title='Commandes')
        self.boutons_service = {
            'demarrer': bouton('Démarrer', lambda: self.service('demarrer', 'Service démarré'), ['suggested-action']),
            'redemarrer': bouton('Redémarrer', lambda: self.service('redemarrer', 'Service redémarré')),
            'arreter': bouton('Arrêter', self.demander_arret_service, ['flat', 'destructive-action', 'texte-destructif']),
        }
        for b in self.boutons_service.values():
            self.ligne_commandes.add_suffix(b)
        g_service.add(self.ligne_commandes)

    def _essai(self, g_essai):
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
        self.sortie_essai = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, top_margin=8,
                                         bottom_margin=8, left_margin=10, right_margin=10,
                                         wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.sortie_essai.update_property([Gtk.AccessibleProperty.LABEL], ['Sortie de l’essai'])
        self.sortie_essai.add_css_class('sortie-essai')
        self.defile_essai = Gtk.ScrolledWindow(child=self.sortie_essai, min_content_height=130, visible=False,
                                               overflow=Gtk.Overflow.HIDDEN)
        self.defile_essai.add_css_class('card')
        zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12)
        zone.append(liste_verdict)
        zone.append(self.defile_essai)
        g_essai.add(zone)

    def _indicateur(self, g_ind):
        self.ligne_indicateur = Adw.SwitchRow(title='Afficher dans la barre du haut',
                                              subtitle='Montre la RAM en permanence et donne accès aux actions urgentes')
        self.chemin_autostart = donnees.fichier_autostart_indicateur()
        self.commande_indicateur = trouver_indicateur()
        self.ligne_indicateur.set_active(donnees.indicateur_affiche(self.chemin_autostart))
        if not self.commande_indicateur:
            self.ligne_indicateur.set_sensitive(False)
            self.ligne_indicateur.set_subtitle('L’indicateur n’est pas installé (paquet gagarine, programme '
                                               'gagarine-indicateur)')
        self.ligne_indicateur.connect('notify::active', self._indicateur_change)
        g_ind.add(self.ligne_indicateur)

    # --- réglages ---
    def _ligne_nombre(self, cle, titre, aide, unite, mini, maxi):
        ligne = Adw.SpinRow.new_with_range(mini, maxi, 1)
        ligne.set_title(f'{titre} ({unite})')
        ligne.set_subtitle(aide)
        ligne.set_subtitle_lines(0)     # en fenêtre étroite, l'aide prend les lignes qu'il lui faut
        ligne.connect('notify::value', self._modifie)
        self.champs[cle] = ligne
        return ligne

    def _ligne_interrupteur(self, cle):
        titre, aide = donnees.REGLAGES_BOOLEENS[cle]
        ligne = Adw.SwitchRow(title=titre, subtitle=aide, subtitle_lines=0)
        ligne.connect('notify::active', self._modifie)
        self.interrupteurs[cle] = ligne
        return ligne

    def valeurs(self):
        v = {cle: int(ch.get_value()) for cle, ch in self.champs.items()}
        v['notifications'] = self.notifications.get_active()
        v['proteges'] = self.proteges.get_text().strip()
        v['ntfy'] = self.ntfy.get_text().strip()
        for cle, ligne in self.interrupteurs.items():
            v[cle] = ligne.get_active()
        return v

    def recharger(self, force=False):
        etat = self.source.etat
        if not etat or not etat['reglages']:
            return
        if not force and self.modifie():
            self.maj_service()
            return
        self.courants = donnees.reglages_complets(etat['reglages'])
        self.en_cours_chargement = True
        for cle, ch in self.champs.items():
            if self.courants.get(cle) is not None:
                ch.set_value(self.courants[cle])
        self.notifications.set_active(bool(self.courants.get('notifications', True)))
        self.proteges.set_text(str(self.courants.get('proteges') or ''))
        self.ntfy.set_text(str(self.courants.get('ntfy') or ''))
        for cle, ligne in self.interrupteurs.items():
            ligne.set_active(bool(self.courants.get(cle)))
        self.en_cours_chargement = False
        self._modifie()
        self.maj_service()

    def modifie(self):
        return donnees.arguments_reglages([], self.courants, self.valeurs()) is not None if self.courants else False

    def _modifie(self, *_):
        if self.en_cours_chargement:
            return
        self.essai_ntfy.set_sensitive(bool(self.ntfy.get_text().strip()))
        sale = self.modifie()
        erreur = donnees.verifier_reglages(self.valeurs()) if sale else None
        self.bouton_enregistrer.set_sensitive(sale and erreur is None)
        self.bouton_annuler.set_sensitive(sale)
        self.message_reglages.set_label(erreur or ('L’enregistrement demande votre mot de passe d’administrateur.'
                                                   if sale else ''))
        (self.message_reglages.add_css_class if erreur else self.message_reglages.remove_css_class)('error')
        self.vue.set_reveal_bottom_bars(sale)
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

    def envoyer_essai_ntfy(self):
        """Message d'essai vers le sujet saisi (même avant l'enregistrement) : `gagarine ntfy essai CIBLE`, sans pkexec."""
        cible = self.ntfy.get_text().strip()
        if not cible:
            return
        if any(c in cible for c in ' \t\n'):
            self.fenetre.toast('Le sujet ntfy ne doit contenir ni espace ni retour à la ligne.', longue=True)
            return

        def fini(res):
            self.essai_ntfy.set_sensitive(bool(self.ntfy.get_text().strip()))
            if res.ok:
                self.fenetre.toast('Message d’essai envoyé : regardez votre téléphone')
            elif res.code == 2:
                self.fenetre.toast('Cette version de Gagarine ne sait pas encore envoyer d’essai ntfy.', longue=True)
            else:
                self.fenetre.toast(res.message, longue=True)
        self.essai_ntfy.set_sensitive(False)
        self.fenetre.executeur.lancer(donnees.commande_essai_ntfy(donnees.commande_gagarine(), cible), fini, delai=20,
                                      cle='ntfy-essai')

    # --- service ---
    def maj_service(self):
        etat = self.source.etat
        if not etat:
            return
        actif = self.source.service_actif()
        nom = etat['service']['etat']
        arrete = nom in ('inactive', 'failed')
        self.etat_service.set_label('Actif' if actif else ('Arrêté' if arrete else nom.capitalize()))
        for c in ('success', 'error'):
            self.etat_service.remove_css_class(c)
        self.etat_service.add_css_class('success' if actif else 'error')
        for c in ('alerte', 'eteint'):
            self.voyant_service.remove_css_class(c)
        if not actif:
            self.voyant_service.add_css_class('alerte' if arrete else 'eteint')
        self.ligne_service.set_subtitle(f'Fichier de réglages : {etat["fichier_reglages"]}')
        # seules les commandes utiles sont montrées : Démarrer quand il est arrêté, sinon Redémarrer et Arrêter
        self.boutons_service['demarrer'].set_visible(not actif)
        self.boutons_service['redemarrer'].set_visible(actif)
        self.boutons_service['arreter'].set_visible(actif)
        for b in self.boutons_service.values():
            b.set_sensitive(True)

    def service(self, action, message):
        self.fenetre.executer(donnees.commande_admin() + ['service', action], message)

    def demander_arret_service(self):
        confirmer(self.fenetre, 'Arrêter le service Gagarine ?',
                  'La RAM ne sera plus protégée tant que le service sera arrêté : un programme qui grossit pourrait '
                  'figer le poste.', 'Arrêter le service', lambda: self.service('arreter', 'Service arrêté'))

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
        GLib.idle_add(lambda: self.defile_essai.get_vadjustment().set_value(
            self.defile_essai.get_vadjustment().get_upper()))
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
