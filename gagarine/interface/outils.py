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
