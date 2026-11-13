"""Page « Journal » (1.5.0) : les événements en frise, groupés par jour, sur toute la largeur ; recherche et filtre par type.

Chaque ligne : l'heure dans une colonne fixe aux chiffres tabulaires, une pastille teintée selon le type, le titre, puis le
détail sur une ligne (coupé avec « … », en entier dans l'infobulle). Les jours sont des cartes avec un en-tête
(« Aujourd’hui », « Hier », « Lundi 9 novembre »).

`liste` ne contient que les lignes des événements affichés (ni en-tête, ni page vide) : l'en-tête d'un jour est porté par
la première ligne de ce jour. Les lignes sont gardées d'une mise à jour à l'autre (pas de clignotement).
"""
import time
from datetime import date, datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Pango

from . import donnees
from .outils import PageLarge

JOURS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre',
        'décembre')

# Teinte de la pastille selon l'action (docs/conception-1.5.0.md, « Journal ») : figé ou arrêté en alerte, fuite en
# vigilance, repris en calme, travail de la file en bleu, ménage et service en neutre.
TEINTES = {
    'fige': 'alerte', 'arrete': 'alerte', 'limite': 'alerte',
    'bridage-impossible': 'vigilance', 'fuite': 'vigilance', 'fuite-travail': 'vigilance',
    'repris': 'calme',
    'travail-debut': 'travail', 'travail-fin': 'travail', 'travail-annule': 'travail',
    'menage': 'neutre', 'demarrage': 'neutre', 'rechargement': 'neutre',
    'lmstudio-arrete': 'vigilance',   # 1.5.1 : instance démarrée par erreur par un appel à lms, arrêtée aussitôt
}
TOUTES_TEINTES = ('alerte', 'vigilance', 'calme', 'travail', 'neutre')


# --- fonctions pures ---------------------------------------------------------------------------------------------------

def travail_echoue(ev):
    """Travail de la file terminé avec un code de sortie non nul."""
    return ev.get('action') == 'travail-fin' and ev.get('code') not in (0, None)


def teinte_evenement(ev):
    """« alerte », « vigilance », « calme », « travail » ou « neutre » (action inconnue : neutre).

    Un travail terminé en erreur (code non nul) passe en vigilance : il mérite qu'on le remarque.
    """
    if travail_echoue(ev):
        return 'vigilance'
    return TEINTES.get(ev.get('action'), 'neutre')


def icone_evenement(ev, icone):
    """Icône de la pastille : celle de donnees.decrire_evenement, sauf la coche d'un travail échoué."""
    return 'dialog-warning-symbolic' if travail_echoue(ev) else icone


def _date_locale(t):
    try:
        return datetime.fromtimestamp(t)
    except (OverflowError, OSError, ValueError):
        return None


def jour_evenement(ev):
    """Jour local (datetime.date) de l'événement, ou None si sa date est illisible."""
    t = donnees.en_timestamp(ev.get('date'))
    d = None if t is None else _date_locale(t)
    return d.date() if d else None


def heure_evenement(ev):
    """« 14:30:05 » (heure locale), ou « — » si la date est illisible."""
    t = donnees.en_timestamp(ev.get('date'))
    d = None if t is None else _date_locale(t)
    return d.strftime('%H:%M:%S') if d else '—'


def libelle_jour(jour, aujourd_hui):
    """« Aujourd’hui », « Hier », « Lundi 9 novembre », « Dimanche 1er novembre », « Mardi 30 décembre 2025 »."""
    if jour is None:
        return 'Date inconnue'
    ecart = (aujourd_hui - jour).days
    if ecart == 0:
        return 'Aujourd’hui'
    if ecart == 1:
        return 'Hier'
    texte = f'{JOURS[jour.weekday()]} {"1er" if jour.day == 1 else jour.day} {MOIS[jour.month - 1]}'
    if jour.year != aujourd_hui.year:
        texte += f' {jour.year}'
    return texte[0].upper() + texte[1:]


def grouper_par_jour(evenements, maintenant=None):
    """[(jour, libellé, [événements])] dans l'ordre reçu (du plus récent au plus ancien) ; dates illisibles à la fin."""
    aujourd_hui = date.fromtimestamp(time.time() if maintenant is None else maintenant)
    groupes, index, inconnus = [], {}, []
    for ev in evenements:
        jour = jour_evenement(ev)
        if jour is None:
            inconnus.append(ev)
            continue
        if jour not in index:
            index[jour] = len(groupes)
            groupes.append((jour, libelle_jour(jour, aujourd_hui), []))
        groupes[index[jour]][2].append(ev)
    if inconnus:
        groupes.append((None, libelle_jour(None, aujourd_hui), inconnus))
    return groupes


def cles_evenements(evenements):
    """Une clé stable par événement (date, action, processus, nom), numérotée si deux événements sont identiques."""
    vus, cles = {}, []
    for ev in evenements:
        base = (str(ev.get('date')), str(ev.get('action')), str(ev.get('pid')), str(ev.get('nom')))
        n = vus.get(base, 0)
        vus[base] = n + 1
        cles.append(base + (n,))
    return cles


def resume_journal(affiches, total):
    """« 29 événements, du plus récent au plus ancien. » ; « 4 événements sur 29, … » quand un filtre en cache."""
    return (donnees.pluriel(affiches, 'événement') + (f' sur {total}' if affiches != total else '')
            + ', du plus récent au plus ancien.')


# --- ligne d'événement -------------------------------------------------------------------------------------------------

class LigneEvenement(Gtk.ListBoxRow):
    """Une ligne de la frise ; porte l'en-tête de son jour quand elle est la première de ce jour."""

    def __init__(self):
        super().__init__(activatable=False, selectable=False)
        self.add_css_class('evenement')
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.entete = Gtk.Box(spacing=12, visible=False)
        self.entete.add_css_class('entete-jour')
        self.jour = Gtk.Label(xalign=0, hexpand=True, accessible_role=Gtk.AccessibleRole.HEADING,
                              ellipsize=Pango.EllipsizeMode.END, valign=Gtk.Align.BASELINE_FILL)
        self.jour.add_css_class('titre-section')
        self.nombre = Gtk.Label(xalign=1, valign=Gtk.Align.BASELINE_FILL)
        self.nombre.add_css_class('etiquette')
        self.nombre.add_css_class('numeric')
        self.entete.append(self.jour)
        self.entete.append(self.nombre)

        self.carte = Gtk.Box()          # écarts dans journal.css (plus serrés en fenêtre étroite)
        self.carte.add_css_class('ligne-evenement')
        self.heure = Gtk.Label(width_chars=8, xalign=0, valign=Gtk.Align.CENTER)
        self.heure.add_css_class('heure-evenement')
        self.heure.add_css_class('numeric')
        self.pastille = Gtk.Image(valign=Gtk.Align.CENTER, pixel_size=16,
                                  accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.pastille.add_css_class('pastille-evenement')
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
        self.titre = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.titre.add_css_class('titre-evenement')
        self.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
        self.detail.add_css_class('detail-evenement')
        self.detail.add_css_class('dim-label')
        textes.append(self.titre)
        textes.append(self.detail)
        self.carte.append(self.heure)
        self.carte.append(self.pastille)
        self.carte.append(textes)
        # l'infobulle ne s'ouvre que si le titre ou le détail est coupé
        self.carte.set_has_tooltip(True)
        self.carte.connect('query-tooltip', self._infobulle)

        boite.append(self.entete)
        boite.append(self.carte)
        self.set_child(boite)
        self.teinte = None
        self.texte_titre = self.texte_detail = ''

    def remplir(self, ev, entete=None, premier=False, dernier=False, tout_premier=False):
        """Met la ligne à jour. entete : (libellé du jour, nombre d'événements du jour) pour la première ligne d'un jour."""
        icone, titre, detail = donnees.decrire_evenement(ev)
        heure = heure_evenement(ev)
        self.texte_titre, self.texte_detail = titre, detail
        self.heure.set_label(heure)
        self.pastille.set_from_icon_name(icone_evenement(ev, icone))
        teinte = teinte_evenement(ev)
        if teinte != self.teinte:
            if self.teinte:
                self.pastille.remove_css_class(self.teinte)
            self.pastille.add_css_class(teinte)
            self.teinte = teinte
        self.titre.set_label(titre)
        self.detail.set_label(detail)
        self.detail.set_visible(bool(detail))
        if entete:
            libelle, nombre = entete
            self.jour.set_label(libelle)
            self.nombre.set_label(donnees.pluriel(nombre, 'événement'))
        self.entete.set_visible(bool(entete))
        for classe, actif in (('premier-du-jour', premier), ('dernier-du-jour', dernier),
                              ('premier-de-la-liste', tout_premier)):
            (self.add_css_class if actif else self.remove_css_class)(classe)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                             [f'{heure}, {titre}', detail])

    def est_coupe(self):
        """Vrai si le titre ou le détail ne tient pas sur sa ligne (texte coupé avec « … »)."""
        return any(e.get_visible() and e.get_layout().is_ellipsized() for e in (self.titre, self.detail))

    def texte_infobulle(self):
        return '\n'.join(t for t in (self.texte_titre, self.texte_detail) if t)

    def _infobulle(self, _widget, _x, _y, _clavier, infobulle):
        if not self.est_coupe():
            return False
        infobulle.set_text(self.texte_infobulle())
        return True


# --- page --------------------------------------------------------------------------------------------------------------

class PageJournal(PageLarge):
    def __init__(self, fenetre):
        super().__init__('Journal', 'journal', 'document-open-recent-symbolic')
        self.add_css_class('page-journal')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.signature = None
        self.lignes = {}

        # barre d'outils, fixe en haut de la page : la recherche prend la largeur, puis le filtre par type
        self.barre = Gtk.Box(spacing=8, margin_start=28, margin_end=28, margin_top=14, margin_bottom=10)
        self.barre.add_css_class('barre-journal')
        self.recherche = Gtk.SearchEntry(placeholder_text='Rechercher dans le journal', hexpand=True)
        self.recherche.update_property([Gtk.AccessibleProperty.LABEL], ['Rechercher dans le journal'])
        self.recherche.connect('search-changed', lambda _e: self.maj(force=True))
        self.recherche.connect('stop-search', lambda e: e.set_text(''))
        self.filtre = Gtk.DropDown.new_from_strings([nom for _, nom in donnees.CATEGORIES])
        self.filtre.set_tooltip_text('Type d’événement')
        self.filtre.update_property([Gtk.AccessibleProperty.LABEL], ['Type d’événement'])
        self.filtre.connect('notify::selected', lambda *_: self.maj(force=True))
        self.barre.append(self.recherche)
        self.barre.append(self.filtre)
        for prop in ('margin-start', 'margin-end'):
            self.point_etroit.add_setter(self.barre, prop, 14)
        # fenêtre étroite : lignes plus serrées (une classe sur la liste plutôt qu'un réglage par ligne)
        self.point_etroit.connect('apply', lambda *_: self.liste.add_css_class('etroite'))
        self.point_etroit.connect('unapply', lambda *_: self.liste.remove_css_class('etroite'))

        # la page de PageLarge passe dans une vue à barre fixe (la barre reste visible quand on fait défiler)
        self.set_child(None)
        self.vue = Adw.ToolbarView(content=self.defilement, top_bar_style=Adw.ToolbarStyle.FLAT)
        self.vue.add_top_bar(self.barre)
        self.set_child(self.vue)
        self.contenu.set_margin_top(6)

        corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.resume = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.resume.add_css_class('description-section')
        self.liste = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self.liste.add_css_class('journal')
        self.liste.update_property([Gtk.AccessibleProperty.LABEL], ['Événements du journal'])
        self.vide = Adw.StatusPage(icon_name='document-open-recent-symbolic', title='Aucun événement',
                                   description='Les actions de Gagarine apparaîtront ici.', visible=False, vexpand=True)
        self.vide.add_css_class('compact')
        corps.append(self.resume)
        corps.append(self.liste)
        corps.append(self.vide)
        self.add(corps)

        self.source.connect('journal', lambda *_: self.maj())
        self.maj()

    def focaliser_recherche(self):
        self.recherche.grab_focus()

    def maj(self, force=False):
        evenements = self.source.journal
        if evenements is None:
            return
        categorie = donnees.CATEGORIES[self.filtre.get_selected()][0]
        texte = self.recherche.get_text()
        maintenant = time.time()
        # le jour fait partie de la signature : après minuit, « Aujourd’hui » devient « Hier »
        signature = (len(evenements), evenements[0].get('date') if evenements else None, categorie, texte,
                     date.fromtimestamp(maintenant))
        if signature == self.signature and not force:
            return
        self.signature = signature
        visibles = donnees.filtrer_evenements(evenements, categorie, texte)
        cles = dict(zip(map(id, visibles), cles_evenements(visibles)))
        entrees = []
        for _jour, libelle, evs in grouper_par_jour(visibles, maintenant):
            for i, ev in enumerate(evs):
                entrees.append((cles[id(ev)], ev, (libelle, len(evs)) if i == 0 else None, i == 0, i == len(evs) - 1))
        self._synchroniser(entrees)

        vide = not visibles
        if evenements and vide:
            self.vide.set_title('Aucun résultat')
            self.vide.set_description('Aucun événement ne correspond à votre recherche.')
            self.vide.set_icon_name('system-search-symbolic')
        else:
            self.vide.set_title('Aucun événement')
            self.vide.set_description('Les actions de Gagarine apparaîtront ici.')
            self.vide.set_icon_name('document-open-recent-symbolic')
        self.vide.set_visible(vide)
        self.liste.set_visible(not vide)
        self.resume.set_visible(not vide)
        self.resume.set_label(resume_journal(len(visibles), len(evenements)))

    def _synchroniser(self, entrees):
        """Garde les lignes existantes (clé par événement), insère les nouvelles à leur place, retire les autres."""
        voulues = {cle for cle, *_ in entrees}
        for cle in [c for c in self.lignes if c not in voulues]:
            self.liste.remove(self.lignes.pop(cle))
        for i, (cle, ev, entete, premier, dernier) in enumerate(entrees):
            ligne = self.lignes.get(cle)
            if ligne is None:
                ligne = self.lignes[cle] = LigneEvenement()
                self.liste.insert(ligne, i)
            elif ligne.get_index() != i:
                self.liste.remove(ligne)
                self.liste.insert(ligne, i)
            ligne.remplir(ev, entete, premier, dernier, i == 0)

    def lignes_affichees(self):
        """Les lignes de la liste, dans l'ordre affiché."""
        res, c = [], self.liste.get_first_child()
        while c is not None:
            res.append(c)
            c = c.get_next_sibling()
        return res
