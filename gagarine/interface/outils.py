"""Petits assistants GTK communs aux pages."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk


def bouton(libelle, rappel, classes=(), icone=None, aide=None):
    """Bouton texte (ou icône) aligné au centre d'une ligne."""
    b = Gtk.Button.new_from_icon_name(icone) if icone else Gtk.Button(label=libelle)
    b.set_valign(Gtk.Align.CENTER)
    for c in classes:
        b.add_css_class(c)
    if aide:
        b.set_tooltip_text(aide)
        b.update_property([Gtk.AccessibleProperty.LABEL], [aide])
    b.connect('clicked', lambda _b: rappel())
    return b


def etiquette(texte='', classes=()):
    e = Gtk.Label(label=texte, xalign=1.0, valign=Gtk.Align.CENTER)
    for c in classes:
        e.add_css_class(c)
    return e


def ligne_vide(titre, sous_titre, icone='object-select-symbolic'):
    """Ligne d'état vide : une icône, un titre et une phrase d'explication."""
    r = Adw.ActionRow(title=titre, subtitle=sous_titre, selectable=False)
    r.add_prefix(Gtk.Image.new_from_icon_name(icone))
    return r


def synchroniser(groupe, lignes, cles, creer, maj):
    """Met à jour les lignes d'un Adw.PreferencesGroup d'après `cles` (liste ordonnée de clés).

    lignes : dict clé -> ligne, modifié sur place. creer(cle) rend une ligne ; maj(cle, ligne) la met à jour.
    Les lignes existantes sont gardées (pas de clignotement) ; l'ordre est refait seulement s'il change.
    """
    for cle in [c for c in lignes if c not in cles]:
        groupe.remove(lignes.pop(cle))
    ordre_change = False
    for cle in cles:
        if cle not in lignes:
            lignes[cle] = creer(cle)
            ordre_change = True
        maj(cle, lignes[cle])
    if ordre_change or getattr(groupe, '_ordre', None) != list(cles):
        for ligne in lignes.values():
            if ligne.get_parent() is not None:
                groupe.remove(ligne)
        for cle in cles:
            groupe.add(lignes[cle])
        groupe._ordre = list(cles)


def confirmer(parent, titre, corps, libelle, rappel, destructif=True):
    """Boîte de confirmation ; rappel() n'est appelé que si l'utilisateur confirme."""
    d = Adw.AlertDialog.new(titre, corps)
    d.add_response('annuler', 'Annuler')
    d.add_response('ok', libelle)
    d.set_response_appearance('ok', Adw.ResponseAppearance.DESTRUCTIVE if destructif
                              else Adw.ResponseAppearance.SUGGESTED)
    d.set_default_response('annuler')
    d.set_close_response('annuler')
    d.connect('response', lambda _d, r: rappel() if r == 'ok' else None)
    d.present(parent)
    return d


def page_preferences(titre, nom, icone):
    p = Adw.PreferencesPage()
    p.set_title(titre)
    p.set_name(nom)
    p.set_icon_name(icone)
    return p


def echappe(texte):
    """Les titres et sous-titres d'Adw.ActionRow lisent le balisage Pango : échapper & < >."""
    return str(texte).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# --- Pages pleine largeur (1.5.0, docs/conception-1.5.0.md) ---

LARGE_SP = 1000     # au-delà, les colonnes d'une page sont côte à côte
ETROITE_SP = 600    # en dessous, marges réduites


class PageLarge(Adw.BreakpointBin):
    """Page pleine largeur, alignée à gauche, qui remplace Adw.PreferencesPage (colonne étroite et centrée).

    - `contenu` : boîte verticale des sections (32 px entre elles), marges de 28 px, 14 px sous ETROITE_SP ;
    - `add(widget)` / `remove(widget)` : comme Adw.PreferencesPage ;
    - `colonnes(n)` : n colonnes côte à côte au-delà de `large` sp de page, empilées en dessous ;
    - `defilement` : la Gtk.ScrolledWindow de la page ;
    - `get_title()`, `get_icon_name()` et le nom (`get_name()`) servent à la fenêtre, comme pour Adw.PreferencesPage.
    Les points de rupture portent sur la largeur de la page, pas de la fenêtre (la barre latérale est déduite).
    """

    def __init__(self, titre, nom, icone, large=LARGE_SP, etroite=ETROITE_SP):
        super().__init__(width_request=360, height_request=300)
        self.set_name(nom)
        self._titre = titre
        self._icone = icone
        self.contenu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=32,
                               margin_top=24, margin_bottom=32, margin_start=28, margin_end=28)
        self.contenu.add_css_class('page-large')
        self.defilement = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.defilement.set_child(self.contenu)
        self.set_child(self.defilement)
        # Une seule règle s'applique à la fois (la dernière qui correspond) : les colonnes s'empilent dans les deux.
        self.point_moyen = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f'max-width: {large}sp'))
        self.point_etroit = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f'max-width: {etroite}sp'))
        for prop, valeur in (('margin-start', 14), ('margin-end', 14), ('margin-top', 14), ('margin-bottom', 20)):
            self.point_etroit.add_setter(self.contenu, prop, valeur)
        self.add_breakpoint(self.point_moyen)
        self.add_breakpoint(self.point_etroit)

    def get_title(self):
        return self._titre

    def get_icon_name(self):
        return self._icone

    def add(self, widget):
        self.contenu.append(widget)

    def remove(self, widget):
        self.contenu.remove(widget)

    def colonnes(self, n=2, espace=28, homogene=True):
        """Ajoute à la page une rangée de n colonnes ; rend la liste des colonnes (boîtes verticales, 32 px entre sections)."""
        rangee = Colonnes(n, espace=espace, homogene=homogene)
        for point in (self.point_moyen, self.point_etroit):
            rangee.empiler_avec(point)
        self.add(rangee)
        return rangee.colonnes


class Colonnes(Gtk.Box):
    """Rangée de colonnes côte à côte ; `empiler_avec(point)` les empile quand ce point de rupture s'applique."""

    def __init__(self, n=2, espace=28, homogene=True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=espace, homogeneous=homogene)
        self.add_css_class('colonnes')
        self.colonnes = []
        for _ in range(n):
            c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=32, hexpand=True, valign=Gtk.Align.START)
            self.colonnes.append(c)
            self.append(c)

    def empiler_avec(self, point):
        point.add_setter(self, 'orientation', Gtk.Orientation.VERTICAL)
        point.add_setter(self, 'homogeneous', False)
        point.add_setter(self, 'spacing', 32)


class Section(Gtk.Box):
    """Section de page : titre et description à gauche, actions à droite (`suffixe`), puis le corps.

    `corps` reçoit le contenu (`ajouter(widget)`) ; `set_titre()`, `set_description()` et `set_suffixe()` changent
    l'en-tête.
    """

    def __init__(self, titre, description=None, suffixe=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class('section')
        self.tete = Gtk.Box(spacing=12)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True, valign=Gtk.Align.END)
        self.titre = Gtk.Label(label=titre, xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.HEADING)
        self.titre.add_css_class('titre-section')
        self.description = Gtk.Label(label=description or '', xalign=0, wrap=True, max_width_chars=90,
                                     visible=bool(description))
        self.description.add_css_class('description-section')
        textes.append(self.titre)
        textes.append(self.description)
        self.tete.append(textes)
        self.suffixe = None
        self.set_suffixe(suffixe)
        self.corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.append(self.tete)
        self.append(self.corps)

    def set_titre(self, titre):
        self.titre.set_label(titre)

    def set_description(self, description):
        self.description.set_label(description or '')
        self.description.set_visible(bool(description))

    def set_suffixe(self, widget):
        """Remplace les actions de droite (None : aucune)."""
        if self.suffixe is not None:
            self.tete.remove(self.suffixe)
        self.suffixe = widget
        if widget is not None:
            widget.set_valign(Gtk.Align.END)
            self.tete.append(widget)

    def ajouter(self, widget):
        self.corps.append(widget)
        return widget
