"""Page « File d'attente » : travaux lourds en cours et en attente, nouveau travail, sortie d'un travail."""
import os
import subprocess

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk

from . import donnees
from .outils import bouton, confirmer, echappe, ligne_vide, synchroniser

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
        base = 'Les travaux passent un par un, chacun dans une limite de mémoire, pour que le poste reste fluide.'
        self.groupe.set_description(base + ('\nMaintenant : ' + ' · '.join(morceaux) if morceaux else ''))

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
        r.annuler = bouton('Annuler', lambda: self.demander_annulation(cle), ['destructive-action'])
        r.add_suffix(r.sortie)
        r.add_suffix(r.annuler)
        return r

    def _maj_ligne(self, cle, ligne):
        t = self.travaux[cle]
        en_cours = t.get('etat') == 'en cours'
        ligne.set_title(echappe(t.get('nom') or 'Travail sans nom'))
        ligne.position.set_label(str(t.get('position') or ''))
        morceaux = ['En cours' if en_cours else 'En attente']
        if t.get('ram') is not None:
            morceaux.append(f'{donnees.formater_go(t["ram"])} de RAM')
        if t.get('coeurs') is not None:
            morceaux.append(donnees.pluriel(t['coeurs'], 'cœur'))
        morceaux.append(donnees.depuis(t.get('depuis')))
        sous = ' · '.join(morceaux)
        if t.get('commande'):
            sous += '\n' + echappe(donnees.tronquer(t['commande'], 110))
        ligne.set_subtitle(sous)
        ligne.sortie.set_visible(bool(t.get('sortie')))

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
                                                  'de mémoire. Il continue même si vous fermez cette fenêtre.')
        self.nom = Adw.EntryRow(title='Nom')
        self.ram = Adw.SpinRow.new_with_range(0.5, 512, 0.5)
        self.ram.set_title('RAM nécessaire (Go)')
        self.ram.set_subtitle('Annoncez ce qu’il faut vraiment : le travail est arrêté s’il dépasse le double.')
        self.ram.set_value(2)
        self.ram.set_digits(1)
        self.coeurs = Adw.SpinRow.new_with_range(0, 64, 1)
        self.coeurs.set_title('Cœurs nécessaires')
        self.coeurs.set_value(2)
        self.dossier = Adw.EntryRow(title='Dossier de travail')
        self.dossier.set_text(GLib.get_home_dir())
        choisir = bouton('', self.choisir_dossier, ['flat'], 'folder-open-symbolic', 'Choisir un dossier')
        self.dossier.add_suffix(choisir)
        self.commande = Adw.EntryRow(title='Commande')
        self.commande.connect('entry-activated', lambda _r: self.valider())
        for ligne in (self.nom, self.commande, self.ram, self.coeurs, self.dossier):
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
            sortie = lancer_detache(nom, self.ram.get_value(), int(self.coeurs.get_value()), commande, dossier)
        except OSError as e:
            self.erreur.set_label(f'Impossible de lancer le travail : {e.strerror or e}')
            self.erreur.set_visible(True)
            return
        self.fenetre.toast(f'« {nom} » lancé · sortie : {sortie}')
        self.close()
        GLib.timeout_add(1200, lambda: self.fenetre.source.rafraichir() or GLib.SOURCE_REMOVE)


def lancer_detache(nom, ram_go, coeurs, commande, dossier):
    """Lance `gagarine file …` détaché de l'interface (nouvelle session) ; rend le fichier de sortie."""
    dossier_sortie = donnees.dossier_sorties()
    os.makedirs(dossier_sortie, exist_ok=True)
    sortie = donnees.fichier_sortie(dossier_sortie, nom)
    env = dict(os.environ, GAGARINE_SORTIE=sortie)
    with open(sortie, 'ab') as f:
        p = subprocess.Popen(donnees.arguments_travail(nom, ram_go, coeurs, commande), cwd=dossier, env=env,
                             stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, p.pid, lambda *_: None)   # récolte le processus quand il finit
    return sortie
