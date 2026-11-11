"""Jauge de la RAM, courbe RAM et processeur, et barres des cœurs, dessinées avec Cairo."""
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


# ligne du processeur sur la courbe : violet, distinct des trois couleurs de niveau de la RAM
COULEUR_CPU = (0.57, 0.34, 0.76)


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
    """Courbe de la RAM utilisée et, en seconde ligne, du processeur, avec les repères de réglage : les 5 dernières minutes
    (lues en direct dans /proc/meminfo et /proc/stat) ou les dernières 24 h et 7 jours (historique du service), incidents
    marqués."""

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.periode = '5min'
        self.set_content_height(204)
        self.set_hexpand(True)
        self.set_draw_func(self._dessiner)
        self._etiquette()

    def _etiquette(self):
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f'Courbe de la mémoire vive utilisée et du processeur, {donnees.libelle_periode(self.periode)}'])

    def choisir_periode(self, periode):
        self.periode = periode
        self._etiquette()
        self.queue_draw()

    @staticmethod
    def _trait(cr, points, couleur, epaisseur):
        cr.set_line_width(epaisseur)
        cr.set_line_join(1)
        cr.move_to(*points[0])
        for x, y in points[1:]:
            cr.line_to(x, y)
        cr.set_source_rgb(*couleur)
        cr.stroke()

    def _dessiner(self, _zone, cr, largeur, hauteur):
        fg = self.get_color()
        cr.select_font_face('sans-serif')
        gauche, droite, haut, bas = 8, 46, 26, 22
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
        graduations = donnees.graduations_axe(self.periode)
        for i, (f, texte) in enumerate(graduations):
            if self.periode == '7j' and i % 2 and f < 1:
                continue    # un jour sur deux : l'axe reste lisible en fenêtre étroite
            ancre = 'gauche' if f == 0 else 'droite' if f == 1 else 'centre'
            _texte(cr, texte, gauche + w * f, hauteur - 6, 10, fg, 0.6, ancre)
            if 0 < f < 1:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.07)
                cr.move_to(gauche + w * f, haut)
                cr.line_to(gauche + w * f, haut + h)
                cr.stroke()
        # la courbe
        if self.periode == '5min':
            maintenant = time.monotonic()
            points = [(gauche + w * (1 - (maintenant - t) / DUREE_COURBE), y_de(p)) for t, p in self.source.historique
                      if maintenant - t <= DUREE_COURBE]
            incidents = []
            points_cpu = [(gauche + w * (1 - (maintenant - t) / DUREE_COURBE), y_de(p))
                          for t, p in self.source.cpu_historique if maintenant - t <= DUREE_COURBE]
        else:
            hist = self.source.historiques.get(self.periode)
            duree = donnees.duree_periode(self.periode)
            maintenant = time.time()
            points, incidents, points_cpu = [], [], []
            if hist:
                for pt in hist['points']:
                    f = donnees.fraction_temps(pt['t'], maintenant, duree)
                    if f is not None:
                        points.append((gauche + w * max(0.0, min(1.0, f)), y_de(pt['ram'])))
                        if pt.get('cpu') is not None:
                            points_cpu.append((gauche + w * max(0.0, min(1.0, f)), y_de(pt['cpu'])))
                for ev in hist['incidents']:
                    f = donnees.fraction_temps(donnees.en_timestamp(ev.get('date')) or 0, maintenant, duree)
                    if f is not None:
                        incidents.append((gauche + w * max(0.0, min(1.0, f)), ev))
            if len(points) < 2:
                _texte(cr, 'Pas encore assez de mesures pour cette période', gauche + w / 2, haut + h / 2, 12, fg, 0.6, 'centre')
        epaisseur = 2 if self.periode == '5min' else 1.5
        cr.save()
        cr.rectangle(gauche, haut, w, h + 1)
        cr.clip()
        if len(points) >= 2:
            r, g, b = couleur_niveau(self.source.niveau())
            cr.move_to(points[0][0], haut + h)
            for x, y in points:
                cr.line_to(x, y)
            cr.line_to(points[-1][0], haut + h)
            cr.close_path()
            cr.set_source_rgba(r, g, b, 0.18)
            cr.fill()
            self._trait(cr, points, (r, g, b), epaisseur)
        if len(points_cpu) >= 2:       # processeur : un simple trait par-dessus, sans remplissage
            self._trait(cr, points_cpu, COULEUR_CPU, epaisseur)
        cr.restore()
        # légende « RAM — Processeur »
        x = gauche
        for libelle, couleur in (('RAM', couleur_niveau(self.source.niveau())), ('Processeur', COULEUR_CPU)):
            cr.set_source_rgb(*couleur)
            cr.set_line_width(3)
            cr.move_to(x, 8)
            cr.line_to(x + 16, 8)
            cr.stroke()
            _texte(cr, libelle, x + 21, 12, 11, fg, 0.8)
            x += 21 + cr.text_extents(libelle).x_advance + 16
        # incidents : trait fin et point en haut de la courbe
        r, g, b = COULEURS['alerte']
        for x, ev in incidents:
            cr.set_source_rgba(r, g, b, 0.55)
            cr.set_line_width(1)
            cr.move_to(x, haut)
            cr.line_to(x, haut + h)
            cr.stroke()
            cr.set_source_rgb(r, g, b)
            cr.arc(x, haut - 3, 3.5, 0, 2 * math.pi)
            cr.fill()


class Coeurs(Gtk.DrawingArea):
    """Une barre verticale par cœur logique, les P puis les E, avec une étiquette P/E sous chaque groupe si le processeur
    est hybride. La couleur suit la charge du cœur comme la jauge suit celle de la RAM."""

    def __init__(self):
        super().__init__()
        self.pourcents = []
        self.types = []
        self.set_content_height(84)
        self.set_hexpand(True)
        self.set_draw_func(self._dessiner)
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Charge de chaque cœur du processeur'])

    def maj(self, pourcents, types=None):
        self.pourcents, self.types = list(pourcents or []), list(types or [])
        self.set_visible(bool(self.pourcents))
        self.queue_draw()

    def _dessiner(self, _zone, cr, largeur, hauteur):
        n = len(self.pourcents)
        if not n:
            return
        fg = self.get_color()
        cr.select_font_face('sans-serif')
        hybride = len(self.types) == n
        bas = 16 if hybride else 2       # place des étiquettes P/E
        h = hauteur - bas
        ordre = donnees.ordre_coeurs(self.types if hybride else None, n)
        ecart = 3 if n > 16 else 5
        groupe = 10 if hybride else 0    # espace entre le dernier P et le premier E
        largeur_barre = max(4.0, min(34.0, (largeur - (n - 1) * ecart - groupe) / n))
        x = 0.0
        debut_groupe, precedent = 0.0, None
        for position, i in enumerate(ordre):
            type_ = self.types[i] if hybride else None
            if precedent is not None and type_ != precedent:
                self._etiquette(cr, precedent, debut_groupe, x - ecart, hauteur, fg)
                x += groupe
                debut_groupe = x
            precedent = type_
            pct = max(0.0, min(100.0, self.pourcents[i]))
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
            _rect_arrondi(cr, x, 0, largeur_barre, h, 3)
            cr.fill()
            plein = h * pct / 100
            if plein > 0.5:
                r, g, b = couleur_niveau(donnees.niveau_cpu(pct))
                cr.save()
                _rect_arrondi(cr, x, 0, largeur_barre, h, 3)
                cr.clip()
                cr.set_source_rgb(r, g, b)
                cr.rectangle(x, h - max(plein, 3), largeur_barre, max(plein, 3))
                cr.fill()
                cr.restore()
            x += largeur_barre + ecart
        if hybride:
            self._etiquette(cr, precedent, debut_groupe, x - ecart, hauteur, fg)

    @staticmethod
    def _etiquette(cr, type_, x1, x2, hauteur, fg):
        texte = 'P : performants' if type_ == 'P' else 'E : économes'
        _texte(cr, texte, (x1 + x2) / 2, hauteur - 3, 10, fg, 0.65, 'centre')
