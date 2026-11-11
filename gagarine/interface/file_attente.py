"""Page « File d'attente » (1.5.0, docs/conception-1.5.0.md) : travaux lourds en cours et en attente sur toute la largeur,
nouveau travail, sortie d'un travail ; dessous, en deux colonnes, la mémoire apprise et les travaux passés.

Chaque travail est une ligne en colonnes : rang, nom et commande, colonnes de valeurs alignées d'une ligne à l'autre
(RAM annoncée, cœurs, processeur utilisé, durée), état et priorité, actions plates. Trois dispositions, selon la largeur
de la page :
- « large » (plus de `TRAVAUX_SP`) : tout sur une ligne ;
- « moyenne » : les valeurs passent sous le nom, les actions à leur droite ;
- « étroite » (moins de `TRAVAUX_ETROITS_SP`) : les actions passent sur leur propre ligne ;
- « très étroite » (moins de `outils.ETROITE_SP`) : les valeurs passent en deux colonnes, pour tenir dans 360 px.
La mémoire apprise et les travaux passés passent leurs valeurs sous le nom seulement sous `outils.ETROITE_SP`.
"""
import os
import subprocess
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk, Pango

from . import donnees
from .outils import ETROITE_SP, LARGE_SP, PageLarge, bouton, confirmer, synchroniser
from .programmes import DESTRUCTIF, SectionListe, infobulle_si_coupe, libelle_accessible, ligne_vide_phrase, nombre_fr

LIGNES_MAX = 12   # travaux affichés dans chaque liste de l'historique
DESCRIPTION_APPRIS = ('Gagarine retient la mémoire que prend chaque travail pour annoncer la bonne quantité la fois '
                      'suivante.')
TAILLE_MAX_SORTIE = 64 * 1024   # on n'affiche que la fin d'un journal volumineux
TRAVAUX_SP = 1120          # en dessous (largeur de la page), les valeurs d'un travail passent sous son nom
TRAVAUX_ETROITS_SP = 720   # en dessous, ses actions passent sur leur propre ligne
DISPOSITIONS = ('large', 'moyenne', 'etroite', 'tres-etroite')

VIDE_FILE = ('Aucun travail dans la file. Lancez un travail lourd avec « Nouveau travail… » ou avec la commande '
             '« gagarine file ».')
VIDE_APPRIS = 'Rien d’appris pour l’instant : après le premier travail de la file, Gagarine retiendra sa mémoire ici.'
VIDE_PASSES = 'Aucun travail terminé ces 7 derniers jours.'

# colonnes de valeurs : (clé, libellé, largeur minimale en caractères)
COLONNES_TRAVAIL = [('ram', 'RAM annoncée', 6), ('coeurs', 'Cœurs', 3), ('processeur', 'Processeur', 9),
                    ('duree', 'Durée', 9)]
COLONNES_APPRIS = [('pic', 'Pic habituel', 6), ('annonce', 'Annoncé', 6)]
COLONNES_PASSE = [('duree', 'Durée', 9), ('pic', 'Pic', 6), ('processeur', 'Processeur', 9)]


# --- fonctions pures (testées sans GTK) --------------------------------------------------------------------------------

def duree_courte(secondes):
    """Durée calme, sans les secondes au-delà d'une minute : « 45 s », « 12 min », « 3 h 12 min », « 2 j 4 h »."""
    if secondes is None:
        return '—'
    s = max(0, int(secondes))
    return donnees.formater_duree(s if s < 60 else s - s % 60)


def etat_file(f):
    """En-tête de la file : « 2 en cours sur 3 possibles · 3 en attente · 6,8 Go libres · charge 2,1 sur 14 cœurs ».

    Un cœur 1.2.x (sans `en_cours` ni `paralleles`) passe les travaux un par un : « 1 en cours · … »."""
    travaux = (f or {}).get('travaux') or []
    attente = sum(1 for t in travaux if t.get('etat') != 'en cours')
    morceaux = []
    if f.get('en_cours') is not None and f.get('paralleles') is not None:
        p = f['paralleles']
        morceaux.append(f'{f["en_cours"]} en cours sur {p} possible' + ('s' if p > 1 else ''))
    else:
        morceaux.append(f'{len(travaux) - attente} en cours')
    if attente:
        morceaux.append(f'{attente} en attente')
    if f.get('ram_disponible_go') is not None:
        morceaux.append(f'{donnees.formater_go(f["ram_disponible_go"])} libres')
    if f.get('charge') is not None:
        charge = str(f['charge']).replace('.', ',')
        morceaux.append(f'charge {charge} sur {f.get("coeurs") or "?"} cœurs')
    return ' · '.join(morceaux)


def colonnes_travail(t, maintenant=None):
    """Valeurs des colonnes d'un travail : {clé: (libellé, valeur)} pour la RAM annoncée, les cœurs, le processeur utilisé
    (travail en cours d'un cœur 1.4.0 seulement, sinon « — ») et la durée (« Attente » pour un travail en attente)."""
    en_cours = t.get('etat') == 'en cours'
    ram = t.get('ram') if t.get('ram') is not None else t.get('besoin_go')
    debut = donnees.en_timestamp(t.get('depuis'))
    maintenant = time.time() if maintenant is None else maintenant
    proc = t.get('coeurs_utilises') if en_cours else None
    return {
        'ram': ('RAM annoncée', donnees.formater_go(ram) if ram is not None else '—'),
        'coeurs': ('Cœurs', nombre_fr(t['coeurs']) if isinstance(t.get('coeurs'), (int, float)) else '—'),
        'processeur': ('Processeur', donnees.formater_coeurs(proc) if proc is not None else '—'),
        'duree': ('Durée' if en_cours else 'Attente', duree_courte(maintenant - debut) if debut is not None else '—'),
    }


def etat_travail(t):
    """(classe, libellé) de la pastille d'état : (« en-cours », « En cours ») ou (« en-attente », « En attente »)."""
    return ('en-cours', 'En cours') if t.get('etat') == 'en cours' else ('en-attente', 'En attente')


def motif_travail(t):
    """Motif d'un travail en attente (`donnees.phrase_motif`) ; None pour un travail en cours ou sans motif."""
    if t.get('etat') == 'en cours' or not t.get('motif'):
        return None
    return donnees.phrase_motif(t['motif'])


def description_travail(t, maintenant=None):
    """Description complète d'un travail (lecteurs d'écran) : état, valeurs, motif, puis la commande."""
    texte = donnees.detail_travail_file(t, maintenant)
    if t.get('commande'):
        texte += '\n' + ' '.join(str(t['commande']).split())
    return texte


def ligne_appris(t):
    """Ligne de la mémoire apprise : (titre, sous-titre, {clé: valeur}, niveau) ; niveau None, « vigilance » (pic en hausse,
    annonce trop basse) ou « alerte » (dépasse sa limite)."""
    titre, _detail, trop_bas = donnees.detail_appris(t)
    tendance, texte_tendance = donnees.tendance_travail(t)
    morceaux = []
    if tendance:
        morceaux.append(texte_tendance[0].upper() + texte_tendance[1:])
    elif trop_bas:
        morceaux.append('Annonce trop basse')
    if t.get('executions') is not None:
        morceaux.append(donnees.pluriel(t['executions'], 'exécution'))
    if t.get('derniere'):
        morceaux.append('dernière : ' + donnees.heure_locale(t['derniere']))
    pic, annonce = t.get('pic_go'), t.get('annonce_go')
    valeurs = {'pic': donnees.formater_go(round(float(pic), 1)) if pic else '—',
               'annonce': donnees.formater_go(annonce) if annonce is not None else '—'}
    niveau = 'alerte' if tendance == 'limite' else 'vigilance' if (tendance or trop_bas) else None
    return titre, ' · '.join(morceaux), valeurs, niveau


def ligne_passe(t, maintenant=None):
    """Ligne d'un travail passé : (titre, sous-titre, {clé: valeur}, réussi) ; sous-titre « Terminé · 14:30:05 »."""
    titre, _detail, reussi = donnees.detail_travail_passe(t, maintenant)
    issue = 'Terminé' if reussi else f'Échec (code {t.get("code")})'
    valeurs = {'duree': duree_courte(t['duree']) if t.get('duree') is not None else '—',
               'pic': donnees.formater_go(round(float(t['pic_go']), 1)) if (t.get('pic_go') or 0) > 0 else '—',
               'processeur': donnees.formater_coeurs(t['coeurs_moyens']) if t.get('coeurs_moyens') is not None else '—'}
    return titre, f'{issue} · {donnees.heure_locale(t.get("date"), maintenant)}', valeurs, reussi


# --- lignes en colonnes ---------------------------------------------------------------------------------------------------

class LigneColonnes(Gtk.ListBoxRow):
    """Ligne de liste en colonnes, dans une `boxed-list` comme un Adw.ActionRow.

    - préfixe (icône ou rang), puis le titre et des lignes de détail (une ligne chacune, coupées avec « … », entières en
      infobulle) ;
    - des colonnes de valeurs (petit libellé `.etiquette` au-dessus de la valeur, chiffres tabulaires), alignées d'une
      ligne à l'autre par les Gtk.SizeGroup de `groupes` (un par clé de colonne, partagé par toute la liste) ; elles sont
      dans une Gtk.Grid, sur une ligne, ou deux par ligne en « tres-etroite » (les colonnes masquées ne laissent pas de
      trou) ;
    - un bloc d'état et un bloc d'actions, facultatifs.
    `disposer(disposition)` : « large » (tout sur une ligne), « moyenne » (état en haut à droite, valeurs sous le titre,
    actions à leur droite), « etroite » (actions sur leur propre ligne) ou « tres-etroite » (valeurs deux par ligne).
    `etroite` est vrai hors de « large ».
    `get_title()` rend le titre, `get_subtitle()` la description complète, lue par les lecteurs d'écran.

    Avec un bloc d'actions et `groupes['textes']`, la disposition « large » groupe le nom et les valeurs à gauche (nom de
    largeur commune, bornée en caractères) et laisse la place libre avant les actions, calées à droite : sur un grand
    écran, les valeurs restent près du nom au lieu d'être rejetées à l'autre bout de la ligne.
    """

    def __init__(self, colonnes, groupes):
        super().__init__(activatable=False, selectable=False)
        self.add_css_class('ligne-colonnes')
        self.grille = Gtk.Grid(column_spacing=16, row_spacing=8, margin_top=10, margin_bottom=10, margin_start=12,
                               margin_end=12)
        self.set_child(self.grille)
        self.textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        self.titre = infobulle_si_coupe(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, width_chars=8,
                                                  max_width_chars=48))
        if 'textes' in groupes:
            groupes['textes'].add_widget(self.textes)
        self.textes.append(self.titre)
        self.valeurs = Gtk.Grid(column_spacing=16, row_spacing=8, valign=Gtk.Align.CENTER)
        self.valeurs.add_css_class('valeurs')
        self.colonnes = {}
        self.ordre = [c[0] for c in colonnes]
        self.par_ligne = len(colonnes)
        for cle, libelle, largeur in colonnes:
            boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            nom = Gtk.Label(label=libelle, xalign=0)
            nom.add_css_class('etiquette')
            valeur = Gtk.Label(xalign=0, width_chars=largeur)
            valeur.add_css_class('numeric')
            valeur.add_css_class('valeur-colonne')
            boite.append(nom)
            boite.append(valeur)
            groupes[cle].add_widget(boite)
            self.colonnes[cle] = (boite, nom, valeur)
            self.valeurs.attach(boite, len(self.colonnes) - 1, 0, 1, 1)
        self.prefixe = self.etat = self.actions = None
        self._description = ''
        self.disposition = None

    def placer(self, prefixe=None, etat=None, actions=None):
        """Met en place les blocs facultatifs ; à appeler une fois, avant `disposer()`."""
        self.prefixe, self.etat, self.actions = prefixe, etat, actions
        for w in (prefixe, self.textes, self.valeurs, etat, actions):
            if w is not None:
                self.grille.attach(w, 0, 0, 1, 1)

    def ajouter_detail(self, classes=()):
        """Ajoute une ligne de détail sous le titre (petite, atténuée, coupée avec infobulle) ; la rend."""
        libelle = infobulle_si_coupe(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, width_chars=8,
                                               max_width_chars=64))
        libelle.add_css_class('subtitle')
        for c in classes:
            libelle.add_css_class(c)
        self.textes.append(libelle)
        return libelle

    @property
    def etroite(self):
        return self.disposition not in (None, 'large')

    def disposer(self, disposition):
        if disposition not in DISPOSITIONS:
            disposition = 'etroite' if disposition is True else 'large'
        if disposition == self.disposition:
            return
        self.disposition = disposition
        places = {'large': [(self.prefixe, 0, 0, 1), (self.textes, 1, 0, 1), (self.valeurs, 2, 0, 1),
                            (self.etat, 3, 0, 1), (self.actions, 4, 0, 1)],
                  'moyenne': [(self.prefixe, 0, 0, 1), (self.textes, 1, 0, 1), (self.etat, 2, 0, 1),
                              (self.valeurs, 1, 1, 1), (self.actions, 2, 1, 1)],
                  'etroite': [(self.prefixe, 0, 0, 1), (self.textes, 1, 0, 1), (self.etat, 2, 0, 1),
                              (self.valeurs, 1, 1, 2), (self.actions, 1, 2, 2)]}[
                      'etroite' if disposition == 'tres-etroite' else disposition]
        gestion = self.grille.get_layout_manager()
        for w, colonne, rang, largeur in places:
            if w is None:
                continue
            enfant = gestion.get_layout_child(w)
            enfant.set_column(colonne)
            enfant.set_row(rang)
            enfant.set_column_span(largeur)
        large = disposition == 'large'
        # en étroit, des espacements plus serrés ; en très étroit, deux valeurs par ligne (fenêtre de 360 px)
        espace = 12 if disposition in ('etroite', 'tres-etroite') else 16
        self.grille.set_column_spacing(espace)
        self.valeurs.set_column_spacing(espace)
        self.par_ligne = 2 if disposition == 'tres-etroite' else len(self.colonnes)
        self._ranger_valeurs()
        # une seule colonne prend la place libre (sinon les valeurs ne seraient plus alignées d'une ligne à l'autre) :
        # les actions en « large » quand il y en a, le nom sinon
        actions_extensibles = large and self.actions is not None
        self.textes.set_hexpand(not actions_extensibles)
        if self.actions is not None:
            self.actions.set_hexpand(actions_extensibles)
        for w in (self.prefixe, self.etat):
            if w is not None:
                w.set_valign(Gtk.Align.CENTER if large else Gtk.Align.START)
        if self.etat is not None:      # sur deux rangs, l'état se cale à droite, au-dessus des actions
            self.etat.set_halign(Gtk.Align.FILL if large else Gtk.Align.END)

    def _ranger_valeurs(self):
        """Place les colonnes visibles dans la grille des valeurs, `par_ligne` par ligne, sans trou."""
        gestion = self.valeurs.get_layout_manager()
        visibles = [cle for cle in self.ordre if self.colonnes[cle][0].get_visible()]
        for i, cle in enumerate(visibles):
            enfant = gestion.get_layout_child(self.colonnes[cle][0])
            enfant.set_column(i % self.par_ligne)
            enfant.set_row(i // self.par_ligne)

    def valeur(self, cle, texte, libelle=None):
        _boite, nom, valeur = self.colonnes[cle]
        valeur.set_label(texte)
        if libelle is not None:
            nom.set_label(libelle)
        return valeur

    def montrer_colonne(self, cle, visible):
        if self.colonnes[cle][0].get_visible() != visible:
            self.colonnes[cle][0].set_visible(visible)
            self._ranger_valeurs()

    def decrire(self, titre, description):
        self.titre.set_label(titre)
        self._description = description
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [titre, description])

    def get_title(self):
        return self.titre.get_label()

    def get_subtitle(self):
        """Description complète de la ligne (compatibilité : la 1.4 lisait le sous-titre d'un Adw.ActionRow)."""
        return self._description


class LigneTravail(LigneColonnes):
    """Travail de la file : rang, nom, commande et motif d'attente, colonnes de valeurs, pastille d'état et priorité,
    actions plates (Voir la sortie, Priorité…, Annuler)."""

    def __init__(self, page, cle, groupes):
        super().__init__(COLONNES_TRAVAIL, groupes)
        self.add_css_class('travail')
        self.position = Gtk.Label(xalign=0.5, width_chars=2, valign=Gtk.Align.CENTER)
        self.position.add_css_class('rang')
        self.position.add_css_class('numeric')
        self.commande = self.ajouter_detail(['commande'])
        self.motif = self.ajouter_detail(['motif'])

        etat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, valign=Gtk.Align.CENTER)
        self.pastille = Gtk.Label(halign=Gtk.Align.START)
        self.pastille.add_css_class('pastille')
        self.ligne_priorite = Gtk.Box(spacing=6)
        nom_priorite = Gtk.Label(label='Priorité', xalign=0)
        nom_priorite.add_css_class('etiquette')
        self.priorite = Gtk.Label(xalign=0)
        self.priorite.add_css_class('priorite')
        self.ligne_priorite.append(nom_priorite)
        self.ligne_priorite.append(self.priorite)
        etat.append(self.pastille)
        etat.append(self.ligne_priorite)
        groupes['etat'].add_widget(etat)

        # largeur commune (SizeGroup), boutons calés à droite ; `hexpand` à faux : seul le nom prend la place libre,
        # sinon la grille partagerait l'espace entre deux colonnes et les valeurs ne seraient plus alignées
        actions = Gtk.Box(valign=Gtk.Align.CENTER, hexpand=False)
        interieur = Gtk.Box(spacing=6, halign=Gtk.Align.END, hexpand=True)
        self.sortie = bouton('Voir la sortie', lambda: page.voir_sortie(cle), ['flat'])
        self.changer = bouton('Priorité…', lambda: page.changer_priorite(cle), ['flat'],
                              aide='Changer la priorité de ce travail en attente')
        self.annuler = bouton('Annuler', lambda: page.demander_annulation(cle), DESTRUCTIF)
        for b in (self.sortie, self.changer, self.annuler):
            interieur.append(b)
        actions.append(interieur)
        groupes['actions'].add_widget(actions)
        self.placer(self.position, etat, actions)

    def maj(self, t, avec_processeur, maintenant=None):
        nom = t.get('nom') or 'Travail sans nom'
        self.decrire(nom, description_travail(t, maintenant))
        self.position.set_label(str(t.get('position') or ''))
        commande = ' '.join(str(t.get('commande') or '').split())
        self.commande.set_label(commande)
        self.commande.set_visible(bool(commande))
        motif = motif_travail(t)
        self.motif.set_label(motif or '')
        self.motif.set_visible(motif is not None)
        for cle, (libelle, valeur) in colonnes_travail(t, maintenant).items():
            self.valeur(cle, valeur, libelle)
        self.montrer_colonne('processeur', avec_processeur)
        classe, libelle = etat_travail(t)
        for c in ('en-cours', 'en-attente'):
            self.pastille.remove_css_class(c)
        self.pastille.add_css_class(classe)
        self.pastille.set_label(libelle)
        # priorité (cœur 1.3.0 seulement) : libellé, et action sur les travaux en attente
        niveau = t.get('priorite')
        texte = donnees.libelle_priorite(niveau)
        self.priorite.set_visible(bool(texte))
        self.ligne_priorite.set_visible(bool(texte))
        if texte:
            self.priorite.set_label(texte)
            for c in ('urgente', 'en-fond'):
                self.priorite.remove_css_class(c)
            if niveau in ('urgent', 'fond'):
                self.priorite.add_css_class('urgente' if niveau == 'urgent' else 'en-fond')
        self.changer.set_visible(bool(texte) and donnees.peut_changer_priorite(t))
        self.sortie.set_visible(bool(t.get('sortie')))
        libelle_accessible(self.sortie, f'Voir la sortie de « {nom} »')
        libelle_accessible(self.changer, f'Changer la priorité de « {nom} »')
        libelle_accessible(self.annuler, f'Annuler « {nom} »')


# --- page -----------------------------------------------------------------------------------------------------------------

def _groupes(colonnes, autres=()):
    return {cle: Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL) for cle in [c[0] for c in colonnes] + list(autres)}


class PageFile(PageLarge):
    def __init__(self, fenetre):
        super().__init__('File d’attente', 'file', 'view-list-symbolic')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.lignes = {}
        self.travaux = {}
        self.avec_processeur = False
        self.groupes_travaux = _groupes(COLONNES_TRAVAIL, ('textes', 'etat', 'actions'))
        self.groupes_appris = _groupes(COLONNES_APPRIS)
        self.groupes_passes = _groupes(COLONNES_PASSE)

        # travaux : l'état de la file à gauche, « Nouveau travail… » à droite
        self.bouton_nouveau = bouton('Nouveau travail…', self.nouveau_travail, ['suggested-action'])
        self.groupe = SectionListe('Travaux lourds', 'Lecture de la file…', self.bouton_nouveau)
        self.vide = ligne_vide_phrase(VIDE_FILE, 'view-list-symbolic')
        self.groupe.liste.add(self.vide)
        self.add(self.groupe)

        # mémoire apprise et travaux passés (1.2.0) : sections masquées si le cœur ne les connaît pas
        gauche, droite = self.colonnes(2)
        self.groupe_appris = SectionListe('Mémoire apprise', DESCRIPTION_APPRIS, visible=False)
        self.lignes_appris = []
        self.groupe_passes = SectionListe('Travaux passés', 'Les travaux de la file terminés ces 7 derniers jours.',
                                          visible=False)
        self.lignes_passes = []
        gauche.append(self.groupe_appris)
        droite.append(self.groupe_passes)

        # points de rupture des travaux. PageLarge n'applique qu'un point à la fois (le dernier ajouté qui correspond) :
        # - `point_travaux` (entre LARGE_SP et TRAVAUX_SP) : les colonnes de la page restent côte à côte, mais une ligne
        #   de travail n'a plus la place ;
        # - `point_travaux_etroits` (entre ETROITE_SP et TRAVAUX_ETROITS_SP) : il reprend l'empilement des colonnes de
        #   `point_moyen`, et les actions d'un travail passent sur leur propre ligne.
        self.point_travaux = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(f'min-width: {LARGE_SP + 1}sp and max-width: {TRAVAUX_SP}sp'))
        self.point_travaux_etroits = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(f'min-width: {ETROITE_SP + 1}sp and max-width: {TRAVAUX_ETROITS_SP}sp'))
        gauche.get_parent().empiler_avec(self.point_travaux_etroits)
        self.add_breakpoint(self.point_travaux)
        self.add_breakpoint(self.point_travaux_etroits)
        self.connect('notify::current-breakpoint', lambda *_: self.disposer())

        self.source.suivre_historique('7j')
        self.source.connect('appris', lambda *_: self.maj_appris())
        self.source.connect('historique', lambda *_: self.maj_passes())
        self.source.connect('file', lambda *_: self.maj())
        self.source.connect('ram', lambda *_: self.maj_temps())
        self.maj()

    # --- disposition ---
    def disposition_travaux(self):
        """« large » sans point de rupture ; « etroite » sous TRAVAUX_ETROITS_SP ; « moyenne » entre les deux."""
        point = self.get_current_breakpoint()
        if point is None:
            return 'large'
        if point == self.point_etroit:
            return 'tres-etroite'
        return 'etroite' if point == self.point_travaux_etroits else 'moyenne'

    def disposition_listes(self):
        """Mémoire apprise et travaux passés : valeurs sous le nom seulement sous ETROITE_SP."""
        return 'etroite' if self.get_current_breakpoint() == self.point_etroit else 'large'

    def disposer(self):
        for ligne in self.lignes.values():
            ligne.disposer(self.disposition_travaux())
        for ligne in self.lignes_appris + self.lignes_passes:
            if isinstance(ligne, LigneColonnes):
                ligne.disposer(self.disposition_listes())

    # --- travaux ---
    def maj(self):
        f = self.source.file
        if not f:
            return
        self.travaux = {t.get('ticket') or str(t.get('pid')): t for t in f['travaux']}
        self.avec_processeur = (self.source.processeur() is not None
                                or any(t.get('coeurs_utilises') is not None for t in f['travaux']))
        self.vide.set_visible(not self.travaux)
        synchroniser(self.groupe.liste, self.lignes, list(self.travaux), self._creer, self._maj_ligne)
        self.groupe.set_description(etat_file(f))

    def maj_temps(self):
        maintenant = time.time()
        for cle, ligne in self.lignes.items():
            if cle in self.travaux:
                ligne.maj(self.travaux[cle], self.avec_processeur, maintenant)

    def _creer(self, cle):
        ligne = LigneTravail(self, cle, self.groupes_travaux)
        ligne.disposer(self.disposition_travaux())
        return ligne

    def _maj_ligne(self, cle, ligne):
        ligne.maj(self.travaux[cle], self.avec_processeur)

    # --- mémoire apprise ---
    def maj_appris(self):
        etat, _ = self.source.etat_option('appris')
        self.groupe_appris.set_visible(etat in ('ok', 'erreur'))
        a = self.source.appris
        if not a:
            return
        self._vider(self.groupe_appris, self.lignes_appris)
        travaux = a['travaux']
        for t in travaux[:LIGNES_MAX]:
            titre, sous, valeurs, niveau = ligne_appris(t)
            r = LigneColonnes(COLONNES_APPRIS, self.groupes_appris)
            icone = Gtk.Image.new_from_icon_name(
                'go-up-symbolic' if t.get('tendance') == 'hausse' else
                'dialog-warning-symbolic' if niveau else 'dialog-information-symbolic')
            icone.add_css_class({'alerte': 'error', 'vigilance': 'warning'}.get(niveau, 'dim-label'))
            r.placer(icone)
            r.ajouter_detail().set_label(sous)
            for cle, valeur in valeurs.items():
                r.valeur(cle, valeur)
            _titre, detail, trop_bas = donnees.detail_appris(t)
            if trop_bas:      # l'annonce est sous le pic habituel : en ambre, avec le mot dans le sous-titre
                r.valeur('annonce', valeurs['annonce']).add_css_class('warning')
            r.decrire(titre, detail + (f'\n{sous}' if niveau else ''))
            r.disposer(self.disposition_listes())
            self._ajouter(self.groupe_appris, self.lignes_appris, r)
        if not travaux:
            self._ajouter(self.groupe_appris, self.lignes_appris, ligne_vide_phrase(VIDE_APPRIS, 'dialog-information-symbolic'))
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
        self._vider(self.groupe_passes, self.lignes_passes)
        travaux = hist['travaux']
        avec_processeur = any(t.get('coeurs_moyens') is not None for t in travaux)
        for t in travaux[:LIGNES_MAX]:
            titre, sous, valeurs, reussi = ligne_passe(t)
            r = LigneColonnes(COLONNES_PASSE, self.groupes_passes)
            icone = Gtk.Image.new_from_icon_name('object-select-symbolic' if reussi else 'dialog-error-symbolic')
            icone.add_css_class('success' if reussi else 'error')
            r.placer(icone)
            r.ajouter_detail().set_label(sous)
            for cle, valeur in valeurs.items():
                r.valeur(cle, valeur)
            r.montrer_colonne('processeur', avec_processeur)
            r.decrire(titre, donnees.detail_travail_passe(t)[1])
            r.disposer(self.disposition_listes())
            self._ajouter(self.groupe_passes, self.lignes_passes, r)
        if not travaux:
            self._ajouter(self.groupe_passes, self.lignes_passes, ligne_vide_phrase(VIDE_PASSES, 'dialog-information-symbolic'))
        self.groupe_passes.set_description(
            'Les travaux de la file terminés ces 7 derniers jours' +
            (f' ({LIGNES_MAX} plus récents sur {len(travaux)}).' if len(travaux) > LIGNES_MAX else '.'))

    @staticmethod
    def _vider(section, lignes):
        for ligne in lignes:
            section.liste.remove(ligne)
        lignes.clear()

    @staticmethod
    def _ajouter(section, lignes, ligne):
        section.liste.add(ligne)
        lignes.append(ligne)

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
