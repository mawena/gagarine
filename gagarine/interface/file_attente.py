"""Page « File d'attente » : travaux lourds en cours et en attente, nouveau travail, sortie d'un travail."""
import os
import subprocess

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk

from . import donnees
from .outils import bouton, confirmer, echappe, etiquette, ligne_vide, synchroniser

LIGNES_MAX = 12   # travaux affichés dans chaque liste de l'historique
DESCRIPTION_APPRIS = ('Gagarine retient la mémoire que prend chaque travail pour annoncer la bonne quantité la fois '
                      'suivante.')
TAILLE_MAX_SORTIE = 64 * 1024   # on n'affiche que la fin d'un journal volumineux


class PageFile(Adw.PreferencesPage):
    def __init__(self, fenetre):
        super().__init__(title='File d’attente', name='file', icon_name='view-list-symbolic')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.lignes = {}
        self.travaux = {}

        self.groupe = Adw.PreferencesGroup(
            title='Travaux lourds',
            description='Les travaux passent un par un, chacun dans une limite de mémoire, pour que le poste reste fluide.')
        self.groupe.set_header_suffix(bouton('Nouveau travail…', self.nouveau_travail, ['suggested-action']))
        self.vide = ligne_vide('Aucun travail dans la file', 'Lancez un travail lourd avec « Nouveau travail… » '
                               'ou avec la commande « gagarine file ».', 'view-list-symbolic')
        self.groupe.add(self.vide)
        self.add(self.groupe)

        # mémoire apprise et travaux passés (1.2.0) : sections masquées si le cœur ne les connaît pas
        self.groupe_appris = Adw.PreferencesGroup(
            title='Mémoire apprise', visible=False,
            description='Gagarine retient la mémoire que prend chaque travail pour annoncer la bonne quantité la fois suivante.')
        self.lignes_appris = []
        self.groupe_passes = Adw.PreferencesGroup(
            title='Travaux passés', visible=False, description='Les travaux de la file terminés ces 7 derniers jours.')
        self.lignes_passes = []
        self.add(self.groupe_appris)
        self.add(self.groupe_passes)
        self.source.suivre_historique('7j')
        self.source.connect('appris', lambda *_: self.maj_appris())
        self.source.connect('historique', lambda *_: self.maj_passes())
        self.source.connect('file', lambda *_: self.maj())
        self.source.connect('ram', lambda *_: self.maj_temps())
        self.maj()

    def maj(self):
        f = self.source.file
        if not f:
            return
        self.travaux = {t.get('ticket') or str(t.get('pid')): t for t in f['travaux']}
        self.vide.set_visible(not self.travaux)
        synchroniser(self.groupe, self.lignes, list(self.travaux), self._creer, self._maj_ligne)
        morceaux = []
        if f.get('ram_disponible_go') is not None:
            morceaux.append(f'{donnees.formater_go(f["ram_disponible_go"])} de RAM disponibles')
        if f.get('charge') is not None:
            morceaux.append(f'charge {str(f["charge"]).replace(".", ",")} sur {f.get("coeurs") or "?"} cœurs')
        entete = donnees.entete_file(f)
        if entete:   # cœur 1.3.0 : plusieurs travaux à la fois
            base = (f'{entete}. Plusieurs travaux peuvent tourner ensemble tant que la RAM et les cœurs suffisent, '
                    'chacun dans une limite de mémoire.')
        else:
            base = 'Les travaux passent un par un, chacun dans une limite de mémoire, pour que le poste reste fluide.'
        self.groupe.set_description(base + ('\nMaintenant : ' + ' · '.join(morceaux) if morceaux else ''))

    # --- mémoire apprise ---
    def maj_appris(self):
        etat, _ = self.source.etat_option('appris')
        self.groupe_appris.set_visible(etat in ('ok', 'erreur'))
        a = self.source.appris
        if not a:
            return
        for ligne in self.lignes_appris:
            self.groupe_appris.remove(ligne)
        self.lignes_appris = []
        travaux = a['travaux']
        for t in travaux[:LIGNES_MAX]:
            titre, detail, trop_bas = donnees.detail_appris(t)
            tendance, texte_tendance = donnees.tendance_travail(t)
            r = Adw.ActionRow(title=echappe(titre), subtitle=echappe(detail), subtitle_lines=2, selectable=False)
            if tendance == 'hausse':
                icone = 'go-up-symbolic'
            else:
                icone = 'dialog-warning-symbolic' if trop_bas or tendance else 'dialog-information-symbolic'
            r.add_prefix(Gtk.Image.new_from_icon_name(icone))
            if tendance:
                r.add_suffix(etiquette(texte_tendance, ['error' if tendance == 'limite' else 'warning', 'caption-heading']))
            if trop_bas:
                r.add_suffix(etiquette('annonce trop basse', ['warning', 'caption-heading']))
            self.groupe_appris.add(r)
            self.lignes_appris.append(r)
        if not travaux:
            r = ligne_vide('Rien d’appris pour l’instant', 'Après le premier travail de la file, Gagarine retiendra '
                           'sa mémoire ici.', 'dialog-information-symbolic')
            self.groupe_appris.add(r)
            self.lignes_appris.append(r)
        if len(travaux) > LIGNES_MAX:
            self.groupe_appris.set_description(f'{LIGNES_MAX} travaux les plus récents sur {len(travaux)}. ' + DESCRIPTION_APPRIS)
        else:
            self.groupe_appris.set_description(DESCRIPTION_APPRIS)

    # --- travaux passés ---
    def maj_passes(self):
        etat, _ = self.source.etat_option('historique-7j')
        self.groupe_passes.set_visible(etat in ('ok', 'erreur'))
        hist = self.source.historiques.get('7j')
        if not hist:
            return
        for ligne in self.lignes_passes:
            self.groupe_passes.remove(ligne)
        self.lignes_passes = []
        travaux = hist['travaux']
        for t in travaux[:LIGNES_MAX]:
            titre, detail, reussi = donnees.detail_travail_passe(t)
            r = Adw.ActionRow(title=echappe(titre), subtitle=echappe(detail), subtitle_lines=2, selectable=False)
            icone = Gtk.Image.new_from_icon_name('object-select-symbolic' if reussi else 'dialog-error-symbolic')
            icone.add_css_class('success' if reussi else 'error')
            r.add_prefix(icone)
            self.groupe_passes.add(r)
            self.lignes_passes.append(r)
        if not travaux:
            r = ligne_vide('Aucun travail terminé', 'Les travaux de la file apparaîtront ici une fois terminés.',
                           'dialog-information-symbolic')
            self.groupe_passes.add(r)
            self.lignes_passes.append(r)
        self.groupe_passes.set_description(
            'Les travaux de la file terminés ces 7 derniers jours' +
            (f' ({LIGNES_MAX} plus récents sur {len(travaux)}).' if len(travaux) > LIGNES_MAX else '.'))

    def maj_temps(self):
        for cle, ligne in self.lignes.items():
            if cle in self.travaux:
                self._maj_ligne(cle, ligne)

    def _creer(self, cle):
        r = Adw.ActionRow(subtitle_lines=3, selectable=False)
        r.position = Gtk.Label(valign=Gtk.Align.CENTER)
        r.position.add_css_class('title-3')
        r.position.add_css_class('numeric')
        r.position.set_width_chars(2)
        r.add_prefix(r.position)
        r.sortie = bouton('Voir la sortie', lambda: self.voir_sortie(cle))
        r.priorite = etiquette('', ['caption-heading'])
        r.changer = bouton('Priorité…', lambda: self.changer_priorite(cle), aide='Changer la priorité de ce travail en attente')
        r.add_suffix(r.priorite)
        r.add_suffix(r.changer)
        r.annuler = bouton('Annuler', lambda: self.demander_annulation(cle), ['destructive-action'])
        r.add_suffix(r.sortie)
        r.add_suffix(r.annuler)
        return r

    def _maj_ligne(self, cle, ligne):
        t = self.travaux[cle]
        en_cours = t.get('etat') == 'en cours'
        ligne.set_title(echappe(t.get('nom') or 'Travail sans nom'))
        ligne.position.set_label(str(t.get('position') or ''))
        sous = echappe(donnees.detail_travail_file(t))
        if t.get('commande'):
            sous += '\n' + echappe(donnees.tronquer(t['commande'], 110))
        ligne.set_subtitle(sous)
        ligne.sortie.set_visible(bool(t.get('sortie')))
        # priorité (cœur 1.3.0 seulement) : pastille, et action sur les travaux en attente
        niveau = t.get('priorite')
        libelle = donnees.libelle_priorite(niveau)
        ligne.priorite.set_visible(bool(libelle))
        if libelle:
            ligne.priorite.set_label(libelle)
            for c in ('error', 'dim-label'):
                ligne.priorite.remove_css_class(c)
            ligne.priorite.add_css_class('error' if niveau == 'urgent' else 'dim-label')
        ligne.changer.set_visible(bool(libelle) and donnees.peut_changer_priorite(t))

    # --- annuler ---
    def demander_annulation(self, cle):
        t = self.travaux.get(cle, {})
        nom = t.get('nom') or 'ce travail'
        en_cours = t.get('etat') == 'en cours'
        corps = (f'« {nom} » est en cours : il sera arrêté et ce qu’il a fait jusque-là sera perdu.' if en_cours
                 else f'« {nom} » quittera la file d’attente sans avoir été lancé.')
        cible = t.get('ticket') or str(t.get('pid'))
        confirmer(self.fenetre, f'Annuler « {nom} » ?', corps, 'Annuler le travail',
                  lambda: self.fenetre.executer(donnees.commande_gagarine() + ['file', 'annuler', cible],
                                                f'« {nom} » annulé'))

    # --- priorité ---
    def changer_priorite(self, cle):
        t = self.travaux.get(cle, {})
        nom = t.get('nom') or 'ce travail'
        cible = t.get('ticket') or str(t.get('pid'))
        d = Adw.AlertDialog.new(f'Priorité de « {nom} »',
                                'Un travail plus prioritaire passe devant les autres quand il tient dans la RAM et les cœurs '
                                'disponibles. Un travail déjà en cours n’est jamais interrompu.')
        liste = Gtk.DropDown.new_from_strings([n for _, n in donnees.PRIORITES])
        liste.set_selected(next((i for i, (c, _) in enumerate(donnees.PRIORITES) if c == t.get('priorite')), 1))
        liste.set_halign(Gtk.Align.CENTER)
        liste.update_property([Gtk.AccessibleProperty.LABEL], ['Priorité'])
        d.set_extra_child(liste)
        d.add_response('annuler', 'Annuler')
        d.add_response('ok', 'Changer')
        d.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response('ok')
        d.set_close_response('annuler')

        def repondu(_d, reponse):
            if reponse != 'ok':
                return
            niveau, nom_niveau = donnees.PRIORITES[liste.get_selected()]
            self.fenetre.executer(donnees.arguments_priorite(donnees.commande_gagarine(), cible, niveau),
                                  f'« {nom} » : priorité « {nom_niveau} »')
        d.connect('response', repondu)
        d.present(self.fenetre)

    # --- sortie ---
    def voir_sortie(self, cle):
        t = self.travaux.get(cle, {})
        DialogueSortie(t.get('nom') or 'Travail', t.get('sortie')).present(self.fenetre)

    # --- nouveau travail ---
    def nouveau_travail(self):
        d = DialogueNouveauTravail(self.fenetre)
        d.present(self.fenetre)


class DialogueSortie(Adw.Dialog):
    """Fin du fichier de sortie d'un travail, rafraîchie toutes les 2 s."""

    def __init__(self, nom, chemin):
        super().__init__(title=f'Sortie de « {nom} »', content_width=640, content_height=460)
        self.chemin = chemin
        vue = Adw.ToolbarView()
        vue.add_top_bar(Adw.HeaderBar())
        self.texte = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                  top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        fenetre = Gtk.ScrolledWindow(child=self.texte, vexpand=True)
        self.defile = fenetre
        vue.set_content(fenetre)
        self.set_child(vue)
        self.connect('closed', self._ferme)
        self.minuterie = GLib.timeout_add_seconds(2, self._tic)
        self._lire()

    def _ferme(self, *_):
        if self.minuterie:
            GLib.source_remove(self.minuterie)
            self.minuterie = 0

    def _tic(self):
        self._lire()
        return GLib.SOURCE_CONTINUE

    def _lire(self):
        try:
            taille = os.path.getsize(self.chemin)
            with open(self.chemin, 'rb') as f:
                if taille > TAILLE_MAX_SORTIE:
                    f.seek(taille - TAILLE_MAX_SORTIE)
                contenu = f.read().decode('utf-8', 'replace')
            if taille > TAILLE_MAX_SORTIE:
                contenu = '[…début non affiché]\n' + contenu
        except (OSError, TypeError):
            contenu = 'Le fichier de sortie n’existe pas (ou plus) :\n' + str(self.chemin)
        tampon = self.texte.get_buffer()
        if tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False) != contenu:
            tampon.set_text(contenu)
            GLib.idle_add(lambda: self.defile.get_vadjustment().set_value(self.defile.get_vadjustment().get_upper()))


class DialogueNouveauTravail(Adw.Dialog):
    def __init__(self, fenetre):
        super().__init__(title='Nouveau travail', content_width=520)
        self.fenetre = fenetre
        vue = Adw.ToolbarView()
        entete = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        annuler = Gtk.Button(label='Annuler')
        annuler.connect('clicked', lambda _b: self.close())
        self.lancer = Gtk.Button(label='Lancer')
        self.lancer.add_css_class('suggested-action')
        self.lancer.connect('clicked', lambda _b: self.valider())
        entete.pack_start(annuler)
        entete.pack_end(self.lancer)
        vue.add_top_bar(entete)

        groupe = Adw.PreferencesGroup(description='Le travail attend son tour dans la file, puis se lance dans une limite '
                                                  'de mémoire. Un travail urgent passe devant les autres, un travail en fond '
                                                  'leur laisse la place. Il continue même si vous fermez cette fenêtre.')
        self.nom = Adw.EntryRow(title='Nom')
        self.ram = Adw.SpinRow.new_with_range(0.5, 512, 0.5)
        self.ram.set_title('RAM nécessaire (Go)')
        self.ram.set_subtitle('Annoncez ce qu’il faut vraiment : le travail est arrêté s’il dépasse le double.')
        self.ram.set_value(2)
        self.ram.set_digits(1)
        self.coeurs = Adw.SpinRow.new_with_range(0, 64, 1)
        self.coeurs.set_title('Cœurs nécessaires')
        self.coeurs.set_value(2)
        self.priorite = Adw.ComboRow(title='Priorité')
        self.priorite.set_model(Gtk.StringList.new([n for _, n in donnees.PRIORITES]))
        self.priorite.set_selected(1)
        self.dossier = Adw.EntryRow(title='Dossier de travail')
        self.dossier.set_text(GLib.get_home_dir())
        choisir = bouton('', self.choisir_dossier, ['flat'], 'folder-open-symbolic', 'Choisir un dossier')
        self.dossier.add_suffix(choisir)
        self.commande = Adw.EntryRow(title='Commande')
        self.commande.connect('entry-activated', lambda _r: self.valider())
        for ligne in (self.nom, self.commande, self.ram, self.coeurs, self.priorite, self.dossier):
            groupe.add(ligne)
        self.erreur = Gtk.Label(wrap=True, xalign=0)
        self.erreur.add_css_class('error')
        self.erreur.set_visible(False)
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=24, margin_start=12,
                        margin_end=12)
        boite.append(groupe)
        boite.append(self.erreur)
        vue.set_content(Gtk.ScrolledWindow(child=boite, propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(vue)
        self.set_default_widget(self.lancer)
        self.set_focus(self.nom)

    def choisir_dossier(self):
        d = Gtk.FileDialog(title='Dossier de travail')
        d.select_folder(self.fenetre, None, self._dossier_choisi)

    def _dossier_choisi(self, dialogue, res):
        try:
            dossier = dialogue.select_folder_finish(res)
        except GLib.Error:
            return
        self.dossier.set_text(dossier.get_path())

    def valider(self):
        nom, commande = self.nom.get_text().strip(), self.commande.get_text().strip()
        dossier = self.dossier.get_text().strip() or GLib.get_home_dir()
        message = donnees.valider_travail(nom, self.ram.get_value(), int(self.coeurs.get_value()), commande)
        if message is None and not os.path.isdir(dossier):
            message = 'Ce dossier de travail n’existe pas.'
        if message:
            self.erreur.set_label(message)
            self.erreur.set_visible(True)
            return
        try:
            sortie = lancer_detache(nom, self.ram.get_value(), int(self.coeurs.get_value()), commande, dossier,
                                    donnees.PRIORITES[self.priorite.get_selected()][0])
        except OSError as e:
            self.erreur.set_label(f'Impossible de lancer le travail : {e.strerror or e}')
            self.erreur.set_visible(True)
            return
        self.fenetre.toast(f'« {nom} » lancé · sortie : {sortie}')
        self.close()
        GLib.timeout_add(1200, lambda: self.fenetre.source.rafraichir() or GLib.SOURCE_REMOVE)


def lancer_detache(nom, ram_go, coeurs, commande, dossier, priorite='normal'):
    """Lance `gagarine file …` détaché de l'interface (nouvelle session) ; rend le fichier de sortie."""
    dossier_sortie = donnees.dossier_sorties()
    os.makedirs(dossier_sortie, exist_ok=True)
    sortie = donnees.fichier_sortie(dossier_sortie, nom)
    env = dict(os.environ, GAGARINE_SORTIE=sortie)
    with open(sortie, 'ab') as f:
        p = subprocess.Popen(donnees.arguments_travail(nom, ram_go, coeurs, commande, priorite=priorite), cwd=dossier, env=env,
                             stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, p.pid, lambda *_: None)   # récolte le processus quand il finit
    return sortie
