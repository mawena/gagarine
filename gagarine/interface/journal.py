"""Page « Journal » : événements fusionnés, filtre par type et recherche."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import donnees
from .outils import echappe


class PageJournal(Adw.PreferencesPage):
    def __init__(self, fenetre):
        super().__init__(title='Journal', name='journal', icon_name='document-open-recent-symbolic')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.signature = None

        groupe = Adw.PreferencesGroup(title='Événements', description='Ce que Gagarine a fait récemment, du plus récent au plus ancien.')
        outils = Gtk.Box(spacing=6)
        self.recherche = Gtk.SearchEntry(placeholder_text='Rechercher dans le journal', hexpand=True)
        self.recherche.connect('search-changed', lambda _e: self.maj(force=True))
        self.filtre = Gtk.DropDown.new_from_strings([nom for _, nom in donnees.CATEGORIES])
        self.filtre.set_tooltip_text('Type d’événement')
        self.filtre.connect('notify::selected', lambda *_: self.maj(force=True))
        outils.append(self.recherche)
        outils.append(self.filtre)
        groupe.set_header_suffix(None)
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        boite.append(outils)
        self.liste = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.liste.add_css_class('boxed-list')
        self.vide = Adw.StatusPage(icon_name='document-open-recent-symbolic', title='Aucun événement',
                                   description='Les actions de Gagarine apparaîtront ici.')
        self.vide.add_css_class('compact')
        self.liste.set_placeholder(self.vide)
        boite.append(self.liste)
        groupe.add(boite)
        self.add(groupe)
        self.groupe = groupe
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
        signature = (len(evenements), evenements[0].get('date') if evenements else None, categorie, texte)
        if signature == self.signature and not force:
            return
        self.signature = signature
        visibles = donnees.filtrer_evenements(evenements, categorie, texte)
        if evenements and not visibles:
            self.vide.set_title('Aucun résultat')
            self.vide.set_description('Aucun événement ne correspond à votre recherche.')
            self.vide.set_icon_name('system-search-symbolic')
        else:
            self.vide.set_title('Aucun événement')
            self.vide.set_description('Les actions de Gagarine apparaîtront ici.')
            self.vide.set_icon_name('document-open-recent-symbolic')
        while (ligne := self.liste.get_first_child()) is not None:
            self.liste.remove(ligne)
        for ev in visibles:
            self.liste.append(self._ligne(ev))
        self.groupe.set_description(
            f'{donnees.pluriel(len(visibles), "événement")} affiché{"s" if len(visibles) > 1 else ""}'
            + (f' sur {len(evenements)}' if len(visibles) != len(evenements) else '') + ', du plus récent au plus ancien.')

    @staticmethod
    def _ligne(ev):
        icone, titre, detail = donnees.decrire_evenement(ev)
        r = Adw.ActionRow(title=echappe(titre), subtitle=echappe(detail), subtitle_lines=2, selectable=False)
        r.add_prefix(Gtk.Image.new_from_icon_name(icone))
        heure = Gtk.Label(label=donnees.heure_locale(ev.get('date')), valign=Gtk.Align.CENTER)
        heure.add_css_class('dim-label')
        heure.add_css_class('numeric')
        r.add_suffix(heure)
        return r
