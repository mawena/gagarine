"""Jauge de la RAM et courbe des cinq dernières minutes, dessinées avec Cairo."""
import math
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import donnees
from .source import DUREE_COURBE

# couleurs de secours si le thème ne les définit pas (bleu, orange, rouge de GNOME)
COULEURS = {'normal': (0.21, 0.52, 0.89), 'vigilance': (0.90, 0.38, 0.0), 'alerte': (0.88, 0.11, 0.14)}


def couleur_niveau(niveau):
    return COULEURS[niveau]


def _texte(cr, texte, x, y, taille, couleur, alpha=0.75, ancre='gauche'):
    cr.set_font_size(taille)
    etendue = cr.text_extents(texte)
    if ancre == 'droite':
        x -= etendue.x_advance
    elif ancre == 'centre':
        x -= etendue.x_advance / 2
    cr.set_source_rgba(couleur.red, couleur.green, couleur.blue, alpha)
    cr.move_to(x, y)
    cr.show_text(texte)


def _rect_arrondi(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Jauge(Gtk.DrawingArea):
    """Barre de la RAM utilisée avec les repères vigilance, seuil et urgence."""

    def __init__(self):
        super().__init__()
        self.pourcent = 0.0
        self.reglages = {}
        self.niveau = 'normal'
        self.set_content_height(58)
        self.set_hexpand(True)
        self.set_draw_func(self._dessiner)
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Jauge de la mémoire vive utilisée'])

    def maj(self, pourcent, reglages, niveau):
        self.pourcent, self.reglages, self.niveau = pourcent or 0.0, reglages, niveau
        self.queue_draw()

    def _dessiner(self, _zone, cr, largeur, hauteur):
        fg = self.get_color()
        cr.select_font_face('sans-serif')
        haut, bas = 18, 18          # marges pour les étiquettes
        h = hauteur - haut - bas
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
        _rect_arrondi(cr, 0, haut, largeur, h, h / 2)
        cr.fill()
        plein = max(0.0, min(100.0, self.pourcent)) / 100 * largeur
        if plein > 0:
            r, g, b = couleur_niveau(self.niveau)
            cr.save()
            _rect_arrondi(cr, 0, haut, largeur, h, h / 2)
            cr.clip()
            cr.set_source_rgb(r, g, b)
            cr.rectangle(0, haut, max(plein, h), h)
            cr.fill()
            cr.restore()
        # repères : vigilance et urgence au-dessus, seuil en dessous (95 et 98 % sont proches)
        defauts = {'vigilance': 85, 'seuil': 95, 'urgence': 98}
        for cle, cote in (('vigilance', 'haut'), ('seuil', 'bas'), ('urgence', 'haut')):
            valeur = self.reglages.get(cle, defauts[cle])
            x = valeur / 100 * largeur
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.set_line_width(1.5)
            cr.move_to(x, haut - 3)
            cr.line_to(x, haut + h + 3)
            cr.stroke()
            _texte(cr, f'{valeur:g}', x, 11 if cote == 'haut' else hauteur - 4, 11, fg, 0.8, 'centre')


class Courbe(Gtk.DrawingArea):
    """Courbe de la RAM utilisée sur les 5 dernières minutes, avec les repères de réglage."""

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.set_content_height(190)
        self.set_hexpand(True)
        self.set_draw_func(self._dessiner)
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Courbe de la mémoire vive utilisée sur les cinq dernières minutes'])

    def _dessiner(self, _zone, cr, largeur, hauteur):
        fg = self.get_color()
        cr.select_font_face('sans-serif')
        gauche, droite, haut, bas = 8, 46, 8, 22
        w, h = largeur - gauche - droite, hauteur - haut - bas
        reglages = self.source.reglages()

        def y_de(p):
            return haut + h * (1 - max(0.0, min(100.0, p)) / 100)

        # cadre et lignes de fond
        cr.set_line_width(1)
        for p in (0, 25, 50, 75, 100):
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10)
            cr.move_to(gauche, y_de(p) + 0.5)
            cr.line_to(gauche + w, y_de(p) + 0.5)
            cr.stroke()
            if p in (0, 50, 100):
                _texte(cr, f'{p} %', largeur - 4, y_de(p) + (10 if p == 100 else -2), 10, fg, 0.55, 'droite')
        # repères de réglage, en pointillés
        defauts = {'vigilance': 85, 'seuil': 95, 'urgence': 98}
        cr.set_dash([4, 4])
        for cle in ('vigilance', 'seuil', 'urgence'):
            valeur = reglages.get(cle, defauts[cle])
            r, g, b = COULEURS['vigilance'] if cle != 'urgence' else COULEURS['alerte']
            cr.set_source_rgba(r, g, b, 0.85)
            cr.move_to(gauche, y_de(valeur))
            cr.line_to(gauche + w, y_de(valeur))
            cr.stroke()
        cr.set_dash([])
        # axe du temps
        _texte(cr, '5 min', gauche, hauteur - 6, 10, fg, 0.6)
        _texte(cr, '2 min', gauche + w * 0.6, hauteur - 6, 10, fg, 0.6, 'centre')
        _texte(cr, 'maintenant', gauche + w, hauteur - 6, 10, fg, 0.6, 'droite')
        # la courbe
        maintenant = time.monotonic()
        points = [(gauche + w * (1 - (maintenant - t) / DUREE_COURBE), y_de(p)) for t, p in self.source.historique
                  if maintenant - t <= DUREE_COURBE]
        if len(points) >= 2:
            r, g, b = couleur_niveau(self.source.niveau())
            cr.save()
            cr.rectangle(gauche, haut, w, h + 1)
            cr.clip()
            cr.move_to(points[0][0], haut + h)
            for x, y in points:
                cr.line_to(x, y)
            cr.line_to(points[-1][0], haut + h)
            cr.close_path()
            cr.set_source_rgba(r, g, b, 0.18)
            cr.fill()
            cr.set_line_width(2)
            cr.set_line_join(1)
            cr.move_to(*points[0])
            for x, y in points[1:]:
                cr.line_to(x, y)
            cr.set_source_rgb(r, g, b)
            cr.stroke()
            cr.restore()
