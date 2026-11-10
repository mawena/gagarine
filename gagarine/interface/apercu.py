"""Page « Vue d'ensemble » : jauge, courbe, swap, service."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import donnees
from .graphiques import Courbe, Jauge
from .outils import etiquette, page_preferences, synchroniser


class PageApercu(Adw.PreferencesPage):
    def __init__(self, fenetre):
        super().__init__(title='Aperçu', name='apercu', icon_name='computer-symbolic')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.lignes_swap = {}

        # mémoire vive
        groupe = Adw.PreferencesGroup(title='Mémoire vive')
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        en_tete = Gtk.Box(spacing=12)
        self.pourcent = Gtk.Label(label='— %', xalign=0)
        self.pourcent.add_css_class('title-1')
        self.pourcent.add_css_class('numeric')
        self.detail = Gtk.Label(label='', xalign=0, hexpand=True, valign=Gtk.Align.END, wrap=True)
        self.detail.add_css_class('dim-label')
        self.statut = Gtk.Label(label='', valign=Gtk.Align.CENTER)
        self.statut.add_css_class('pill-etat')
        en_tete.append(self.pourcent)
        en_tete.append(self.detail)
        en_tete.append(self.statut)
        self.jauge = Jauge()
        boite.append(en_tete)
        boite.append(self.jauge)
        self.legende = Gtk.Label(label='', xalign=0, wrap=True)
        self.legende.add_css_class('dim-label')
        self.legende.add_css_class('caption')
        boite.append(self.legende)
        groupe.add(self._carte(boite))
        self.add(groupe)

        groupe = Adw.PreferencesGroup(title='Les cinq dernières minutes')
        self.courbe = Courbe(self.source)
        groupe.add(self._carte(self.courbe))
        self.add(groupe)

        # swap
        self.groupe_swap = Adw.PreferencesGroup(title='Swap', description='Espace de débordement quand la RAM est pleine.')
        self.add(self.groupe_swap)

        # service
        groupe = Adw.PreferencesGroup(title='Gagarine')
        self.ligne_service = Adw.ActionRow(title='Service', subtitle='État inconnu')
        self.ligne_service.add_prefix(Gtk.Image.new_from_icon_name('emblem-system-symbolic'))
        self.etat_service = etiquette('', ['heading'])
        self.ligne_service.add_suffix(self.etat_service)
        groupe.add(self.ligne_service)
        self.ligne_figes = self._ligne_lien('Programmes figés', 'media-playback-pause-symbolic', 'programmes')
        self.ligne_file = self._ligne_lien('Travaux dans la file d’attente', 'view-list-symbolic', 'file')
        groupe.add(self.ligne_figes)
        groupe.add(self.ligne_file)
        self.add(groupe)

        self.source.connect('ram', lambda *_: self.maj_ram())
        self.source.connect('etat', lambda *_: self.maj_etat())
        self.maj_ram()
        self.maj_etat()

    def _carte(self, enfant):
        """Enfant dans une carte arrondie (comme une liste encadrée)."""
        carte = Gtk.Box()
        carte.add_css_class('card')
        enfant.set_margin_top(14)
        enfant.set_margin_bottom(14)
        enfant.set_margin_start(16)
        enfant.set_margin_end(16)
        enfant.set_hexpand(True)
        carte.append(enfant)
        return carte

    def _ligne_lien(self, titre, icone, page):
        r = Adw.ActionRow(title=titre, activatable=True)
        r.add_prefix(Gtk.Image.new_from_icon_name(icone))
        r.valeur = etiquette('', ['numeric'])
        r.add_suffix(r.valeur)
        r.add_suffix(Gtk.Image.new_from_icon_name('go-next-symbolic'))
        r.connect('activated', lambda _r: self.fenetre.aller_a(page))
        return r

    def maj_ram(self):
        s = self.source
        if s.ram_pourcent is None:
            return
        niveau = s.niveau()
        self.pourcent.set_label(donnees.formater_pourcent(s.ram_pourcent))
        utilise = s.ram_total_mo - s.ram_dispo_mo
        self.detail.set_label(f'{donnees.formater_taille_mo(utilise)} utilisés sur {donnees.formater_taille_mo(s.ram_total_mo)}'
                              f' · {donnees.formater_taille_mo(s.ram_dispo_mo)} disponibles')
        self.jauge.maj(s.ram_pourcent, s.reglages(), niveau)
        self.courbe.queue_draw()
        r = s.reglages()
        self.legende.set_label(f'Repères : vigilance {r.get("vigilance", 85)} % · seuil d’intervention {r.get("seuil", 95)} % · '
                               f'urgence {r.get("urgence", 98)} %')
        textes = {'normal': ('Tout va bien', 'success'), 'vigilance': ('Vigilance', 'warning'),
                  'alerte': ('Seuil atteint', 'error')}
        texte, classe = textes[niveau]
        if s.nombre_figes():
            texte, classe = donnees.pluriel(s.nombre_figes(), 'programme figé', 'programmes figés'), 'warning'
        for c in ('success', 'warning', 'error'):
            self.statut.remove_css_class(c)
        self.statut.add_css_class(classe)
        self.statut.set_label(texte)

    def maj_etat(self):
        etat = self.source.etat
        if not etat:
            return
        swap = etat['swap']
        synchroniser(self.groupe_swap, self.lignes_swap, [s.get('nom') for s in swap], self._creer_swap,
                     lambda cle, ligne: self._maj_swap(ligne, next(s for s in swap if s.get('nom') == cle)))
        if not swap:
            self.groupe_swap.set_description('Aucun espace de swap : si la RAM se remplit, rien ne la décharge.')
        else:
            self.groupe_swap.set_description('Espace de débordement quand la RAM est pleine.')
        actif = self.source.service_actif()
        self.etat_service.set_label('Actif' if actif else 'Arrêté')
        for c in ('success', 'error'):
            self.etat_service.remove_css_class(c)
        self.etat_service.add_css_class('success' if actif else 'error')
        demarrage = etat['service']['actif_au_demarrage']
        self.ligne_service.set_subtitle(
            ('Protège la RAM en continu' if actif else 'La RAM n’est pas protégée') +
            ('' if demarrage is None else (' · lancé au démarrage du poste' if demarrage else ' · pas lancé au démarrage')))
        figes, travaux = len(etat['figes']), etat['file']
        self._valeur(self.ligne_figes, 'Aucun' if not figes else donnees.pluriel(figes, 'programme figé', 'programmes figés'))
        self._valeur(self.ligne_file, 'Aucun' if not travaux else donnees.pluriel(travaux, 'travail'))

    @staticmethod
    def _valeur(ligne, texte):
        ligne.valeur.set_label(texte)

    def _creer_swap(self, cle):
        r = Adw.ActionRow()
        r.add_prefix(Gtk.Image.new_from_icon_name('drive-harddisk-symbolic'))
        barre = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER)
        barre.set_size_request(110, -1)
        r.barre = barre
        r.valeur = etiquette('', ['numeric'])
        r.add_suffix(r.valeur)
        r.add_suffix(barre)
        return r

    def _maj_swap(self, ligne, s):
        taille, utilise = s.get('taille_mo') or 0, s.get('utilise_mo') or 0
        ligne.set_title(donnees.nom_swap(s))
        ligne.set_subtitle(f'{s.get("nom", "")} · priorité {s.get("priorite", "—")}')
        ligne.valeur.set_label(f'{donnees.formater_taille_mo(utilise)} / {donnees.formater_taille_mo(taille)}')
        ligne.barre.set_value(utilise / taille if taille else 0)
