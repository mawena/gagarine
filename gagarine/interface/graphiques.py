"""Instruments de l'Aperçu, dessinés avec Cairo : échelle graduée de la RAM, courbe RAM et processeur, cœurs du processeur.

Couleurs : toujours celles du thème (jetons `gagarine_*` de styles/base.css), lues par `couleur()` ; les valeurs de secours
ne servent que si le thème ne les définit pas. Textes : police du système (Pango), chiffres tabulaires.
"""
import math
import time

import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Pango, PangoCairo

from . import donnees
from .source import DUREE_COURBE

# valeurs de secours (palette GNOME) si le thème ne définit pas un jeton
SECOURS = {
    'gagarine_ram': (0.21, 0.52, 0.89), 'gagarine_cpu': (0.57, 0.25, 0.67),
    'gagarine_calme': (0.15, 0.64, 0.41), 'gagarine_vigilance': (0.90, 0.65, 0.04), 'gagarine_alerte': (0.88, 0.11, 0.14),
    'gagarine_calme_fond': (0.15, 0.64, 0.41), 'gagarine_vigilance_fond': (0.90, 0.65, 0.04),
    'gagarine_alerte_fond': (0.88, 0.11, 0.14),
}
DEFAUTS_REGLAGES = {'vigilance': 85, 'seuil': 95, 'urgence': 98}


def couleur(widget, nom):
    """(r, g, b, a) du jeton de couleur `nom` dans le thème courant du widget, ou sa valeur de secours.

    GtkStyleContext.lookup_color est marqué déprécié depuis GTK 4.10 mais reste dans tout GTK 4 ; c'est le seul endroit
    qui l'appelle. Il suit le thème clair ou sombre à chaque dessin."""
    try:
        trouve, c = widget.get_style_context().lookup_color(nom)
    except (AttributeError, TypeError):
        trouve = False
    if trouve:
        return c.red, c.green, c.blue, c.alpha
    return (*SECOURS.get(nom, (0.5, 0.5, 0.5)), 1.0)


def couleur_hex(widget, nom):
    """« #rrggbb » d'un jeton du thème, pour le balisage Pango (`<span foreground=…>`)."""
    r, g, b, _ = couleur(widget, nom)
    return '#{:02x}{:02x}{:02x}'.format(*(round(255 * max(0.0, min(1.0, v))) for v in (r, g, b)))


def niveau_couleur(niveau, fond=False):
    """Jeton de couleur d'un niveau (« normal »/« calme », « vigilance », « alerte ») ; `fond` : variante pour les aplats."""
    nom = {'normal': 'gagarine_calme', 'calme': 'gagarine_calme', 'vigilance': 'gagarine_vigilance',
           'alerte': 'gagarine_alerte'}.get(niveau, 'gagarine_calme')
    return nom + ('_fond' if fond else '')


def _source(cr, c, alpha=1.0):
    cr.set_source_rgba(c[0], c[1], c[2], (c[3] if len(c) > 3 else 1.0) * alpha)


def _fg(widget):
    c = widget.get_color()
    return c.red, c.green, c.blue, c.alpha


def _rect_arrondi(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def mise_en_page(widget, texte, echelle=0.85, gras=False):
    """Pango.Layout du texte dans la police du widget (police du système), à `echelle` de sa taille, chiffres tabulaires."""
    layout = widget.create_pango_layout(texte)
    police = widget.get_pango_context().get_font_description()
    police = police.copy() if police is not None else Pango.FontDescription.from_string('Sans 10')
    taille = police.get_size() or 10 * Pango.SCALE
    if police.get_size_is_absolute():
        police.set_absolute_size(taille * echelle)
    else:
        police.set_size(int(taille * echelle))
    if gras:
        police.set_weight(Pango.Weight.SEMIBOLD)
    layout.set_font_description(police)
    attributs = Pango.AttrList()
    attributs.insert(Pango.attr_font_features_new('tnum'))
    layout.set_attributes(attributs)
    return layout


def _ecrire(cr, layout, x, y, c, alpha=1.0):
    """Écrit le layout avec son coin haut gauche en (x, y)."""
    _source(cr, c, alpha)
    cr.move_to(round(x), round(y))
    PangoCairo.show_layout(cr, layout)


def _taille(layout):
    return layout.get_pixel_size()


class Jauge(Gtk.DrawingArea):
    """Échelle graduée de la RAM : petits traits tous les 5 %, grands traits et nombres tous les 25 %, zone de vigilance
    en ambre léger, seuil et urgence en rouge, remplissage (bleu, puis ambre et rouge selon le niveau) et repère au niveau
    actuel."""

    HAUT_ETIQUETTES = 16    # rangée des repères (85, 95, 98) au-dessus de la bande
    BANDE = 20              # hauteur de la bande
    HAUTEUR = 80            # hauteur du dessin ; s'il reçoit plus (carte voisine plus haute), il se centre

    def __init__(self):
        super().__init__()
        self.pourcent = 0.0
        self.reglages = {}
        self.niveau = 'normal'
        self.set_content_height(self.HAUTEUR)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._dessiner)
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Échelle de la mémoire vive utilisée'])

    def maj(self, pourcent, reglages, niveau):
        self.pourcent, self.reglages, self.niveau = pourcent or 0.0, reglages or {}, niveau
        r = {c: self.reglages.get(c, v) for c, v in DEFAUTS_REGLAGES.items()}
        self.update_property([Gtk.AccessibleProperty.LABEL], [
            f'Mémoire vive utilisée : {donnees.formater_pourcent(self.pourcent)} ; vigilance à {r["vigilance"]:g} %, '
            f'intervention à {r["seuil"]:g} %, urgence à {r["urgence"]:g} %'])
        self.queue_draw()

    def _dessiner(self, _zone, cr, largeur, hauteur):
        fg = _fg(self)
        r = {c: float(self.reglages.get(c, v)) for c, v in DEFAUTS_REGLAGES.items()}
        y0, hb = self.HAUT_ETIQUETTES + 4 + max(0, (hauteur - self.HAUTEUR) // 2), self.BANDE

        def x_de(p):
            return max(0.0, min(100.0, p)) / 100 * largeur

        # bande : fond, zone de vigilance (vigilance → seuil) et zone d'intervention (seuil → 100 %)
        cr.save()
        _rect_arrondi(cr, 0, y0, largeur, hb, 5)
        cr.clip()
        _source(cr, fg, 0.09)
        cr.paint()
        _source(cr, couleur(self, 'gagarine_vigilance_fond'), 0.28)
        cr.rectangle(x_de(r['vigilance']), y0, x_de(r['seuil']) - x_de(r['vigilance']), hb)
        cr.fill()
        _source(cr, couleur(self, 'gagarine_alerte_fond'), 0.30)
        cr.rectangle(x_de(r['seuil']), y0, largeur - x_de(r['seuil']), hb)
        cr.fill()
        # remplissage jusqu'au niveau actuel
        plein = x_de(self.pourcent)
        if plein > 0:
            nom = 'gagarine_ram' if self.niveau == 'normal' else niveau_couleur(self.niveau, fond=True)
            _source(cr, couleur(self, nom))
            cr.rectangle(0, y0, plein, hb)
            cr.fill()
        cr.restore()

        # repères de réglage : traits à travers la bande, nombres au-dessus (écartés s'ils sont trop proches)
        reperes = [('vigilance', 'gagarine_vigilance', 1.5), ('seuil', 'gagarine_alerte', 2.0),
                   ('urgence', 'gagarine_alerte', 2.0)]
        etiquettes = []
        for cle, nom, epaisseur in reperes:
            x = x_de(r[cle])
            _source(cr, couleur(self, nom))
            cr.set_line_width(epaisseur)
            cr.move_to(round(x) + 0.5 if epaisseur < 2 else round(x), y0 - 3)
            cr.line_to(round(x) + 0.5 if epaisseur < 2 else round(x), y0 + hb + 3)
            cr.stroke()
            layout = mise_en_page(self, f'{r[cle]:g}', 0.8, gras=True)
            etiquettes.append((x, layout, nom))
        gauches = donnees.placer_etiquettes([(x, _taille(l)[0]) for x, l, _ in etiquettes], largeur, 5)
        for (x, layout, nom), g in zip(etiquettes, gauches):
            _ecrire(cr, layout, g, y0 - 4 - _taille(layout)[1], couleur(self, nom))

        # repère du niveau actuel : une aiguille qui dépasse de la bande
        if self.pourcent > 0:
            x = min(max(plein, 1.5), largeur - 1.5)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            _source(cr, fg, 0.95)
            cr.set_line_width(3)
            cr.move_to(x, y0 - 5)
            cr.line_to(x, y0 + hb + 5)
            cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_BUTT)

        # graduations : petits traits tous les 5 %, grands tous les 25 % avec leur nombre
        haut_traits = y0 + hb + 4
        for p in range(0, 101, 5):
            x = min(max(x_de(p), 0.5), largeur - 0.5)
            grand = p % 25 == 0
            _source(cr, fg, 0.55 if grand else 0.28)
            cr.set_line_width(1)
            cr.move_to(round(x) + 0.5 if 0 < p < 100 else x, haut_traits)
            cr.line_to(round(x) + 0.5 if 0 < p < 100 else x, haut_traits + (7 if grand else 4))
            cr.stroke()
            if grand:
                layout = mise_en_page(self, f'{p}', 0.8)
                w, _ = _taille(layout)
                gauche = 0 if p == 0 else largeur - w if p == 100 else x - w / 2
                _ecrire(cr, layout, gauche, haut_traits + 9, fg, 0.62)


class Courbe(Gtk.DrawingArea):
    """Courbe de la RAM (aire bleue) et du processeur (trait violet), avec les repères de réglage : les 5 dernières minutes
    (lues en direct dans /proc/meminfo et /proc/stat) ou les dernières 24 h et 7 jours (historique du service), incidents
    marqués en rouge."""

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.periode = '5min'
        self.set_content_height(260)
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

    def _points(self, x_de_f, y_de):
        """(points RAM, points processeur, incidents) de la période, en coordonnées du dessin."""
        if self.periode == '5min':
            maintenant = time.monotonic()

            def serie(mesures):
                return [(x_de_f(1 - (maintenant - t) / DUREE_COURBE), y_de(p)) for t, p in mesures
                        if maintenant - t <= DUREE_COURBE]
            return serie(self.source.historique), serie(self.source.cpu_historique), []
        hist = self.source.historiques.get(self.periode)
        duree = donnees.duree_periode(self.periode)
        maintenant = time.time()
        points, points_cpu, incidents = [], [], []
        if hist:
            for pt in hist['points']:
                f = donnees.fraction_temps(pt['t'], maintenant, duree)
                if f is not None:
                    x = x_de_f(max(0.0, min(1.0, f)))
                    points.append((x, y_de(pt['ram'])))
                    if pt.get('cpu') is not None:
                        points_cpu.append((x, y_de(pt['cpu'])))
            for ev in hist['incidents']:
                f = donnees.fraction_temps(donnees.en_timestamp(ev.get('date')) or 0, maintenant, duree)
                if f is not None:
                    incidents.append((x_de_f(max(0.0, min(1.0, f))), ev))
        return points, points_cpu, incidents

    @staticmethod
    def _chemin(cr, points):
        cr.move_to(*points[0])
        for x, y in points[1:]:
            cr.line_to(x, y)

    def _dessiner(self, _zone, cr, largeur, hauteur):
        fg = _fg(self)
        ram, cpu = couleur(self, 'gagarine_ram'), couleur(self, 'gagarine_cpu')
        gauche, droite, haut, bas = 2, 46, 30, 24
        w, h = max(1, largeur - gauche - droite), max(1, hauteur - haut - bas)
        reglages = self.source.reglages()

        def y_de(p):
            return haut + h * (1 - max(0.0, min(100.0, p)) / 100)

        def x_de_f(f):
            return gauche + w * f

        # lignes de fond et graduations de droite
        cr.set_line_width(1)
        for p in (0, 25, 50, 75, 100):
            _source(cr, fg, 0.14 if p == 0 else 0.07)
            cr.move_to(gauche, round(y_de(p)) + 0.5)
            cr.line_to(gauche + w, round(y_de(p)) + 0.5)
            cr.stroke()
            if p in (0, 50, 100):
                layout = mise_en_page(self, f'{p} %', 0.8)
                tw, th = _taille(layout)
                _ecrire(cr, layout, largeur - tw, y_de(p) - th / 2, fg, 0.55)
        # axe du temps
        for i, (f, texte) in enumerate(donnees.graduations_axe(self.periode)):
            if self.periode == '7j' and i % 2 and f < 1 and largeur < 700:
                continue    # un jour sur deux en fenêtre étroite
            layout = mise_en_page(self, texte, 0.8)
            tw, _ = _taille(layout)
            x = x_de_f(f)
            _ecrire(cr, layout, x if f == 0 else x - tw if f == 1 else x - tw / 2, haut + h + 6, fg, 0.6)
            if 0 < f < 1:
                _source(cr, fg, 0.06)
                cr.move_to(round(x) + 0.5, haut)
                cr.line_to(round(x) + 0.5, haut + h)
                cr.stroke()
        # repères de réglage, en pointillés : vigilance en ambre, seuil et urgence en rouge
        cr.set_dash([5, 4])
        for cle, nom in (('vigilance', 'gagarine_vigilance'), ('seuil', 'gagarine_alerte'), ('urgence', 'gagarine_alerte')):
            _source(cr, couleur(self, nom), 0.75)
            y = round(y_de(reglages.get(cle, DEFAUTS_REGLAGES[cle]))) + 0.5
            cr.move_to(gauche, y)
            cr.line_to(gauche + w, y)
            cr.stroke()
        cr.set_dash([])

        points, points_cpu, incidents = self._points(x_de_f, y_de)
        if self.periode != '5min' and len(points) < 2:
            layout = mise_en_page(self, 'Pas encore assez de mesures pour cette période', 1.0)
            tw, th = _taille(layout)
            _ecrire(cr, layout, gauche + (w - tw) / 2, haut + (h - th) / 2, fg, 0.6)
        epaisseur = 2 if self.periode == '5min' else 1.5
        cr.save()
        cr.rectangle(gauche, haut - 2, w, h + 3)
        cr.clip()
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        if len(points) >= 2:
            # aire de la RAM : dégradé discret vers le bas, puis le trait
            self._chemin(cr, points)
            cr.line_to(points[-1][0], haut + h)
            cr.line_to(points[0][0], haut + h)
            cr.close_path()
            degrade = cairo.LinearGradient(0, haut, 0, haut + h)
            degrade.add_color_stop_rgba(0, ram[0], ram[1], ram[2], 0.30)
            degrade.add_color_stop_rgba(1, ram[0], ram[1], ram[2], 0.04)
            cr.set_source(degrade)
            cr.fill()
            self._chemin(cr, points)
            _source(cr, ram)
            cr.set_line_width(epaisseur)
            cr.stroke()
        if len(points_cpu) >= 2:     # processeur : un trait par-dessus, sans aire
            self._chemin(cr, points_cpu)
            _source(cr, cpu)
            cr.set_line_width(epaisseur * 0.85)
            cr.stroke()
        cr.restore()
        # point de la dernière mesure (5 min) : où en est-on maintenant
        if self.periode == '5min':
            for serie, c in ((points, ram), (points_cpu, cpu)):
                if serie:
                    x, y = serie[-1]
                    _source(cr, c)
                    cr.arc(min(x, gauche + w), y, 3.5, 0, 2 * math.pi)
                    cr.fill()
        # légende
        x = gauche
        for libelle, c in (('RAM', ram), ('Processeur', cpu)):
            _source(cr, c)
            _rect_arrondi(cr, x, 8, 14, 4, 2)
            cr.fill()
            layout = mise_en_page(self, libelle, 0.85)
            tw, th = _taille(layout)
            _ecrire(cr, layout, x + 20, 10 - th / 2, fg, 0.8)
            x += 20 + tw + 18
        # incidents : trait fin et point en haut de la courbe
        alerte = couleur(self, 'gagarine_alerte')
        for x, _ev in incidents:
            _source(cr, alerte, 0.5)
            cr.set_line_width(1)
            cr.move_to(round(x) + 0.5, haut)
            cr.line_to(round(x) + 0.5, haut + h)
            cr.stroke()
            _source(cr, alerte)
            cr.arc(x, haut - 3, 3.5, 0, 2 * math.pi)
            cr.fill()


class Coeurs(Gtk.DrawingArea):
    """Une barre verticale par cœur logique. Processeur hybride : les cœurs performants (P) d'abord, plus larges, leurs
    deux fils groupés ; puis les économes (E). Barres violettes, ambre au-delà de 50 %, rouges à partir de 90 %."""

    HAUT_BARRES = 70

    def __init__(self):
        super().__init__()
        self.pourcents = []
        self.types = []
        self.coeurs_performants = None
        self.set_content_height(self.HAUT_BARRES + 24)
        self.set_hexpand(True)
        self.set_vexpand(True)          # les barres prennent la hauteur libre de la carte
        self.set_draw_func(self._dessiner)
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Charge de chaque cœur du processeur'])

    def maj(self, pourcents, types=None, coeurs_performants=None):
        self.pourcents, self.types = list(pourcents or []), list(types or [])
        self.coeurs_performants = coeurs_performants
        self.set_visible(bool(self.pourcents))
        hybride = len(self.types) == len(self.pourcents) and 'P' in self.types and 'E' in self.types
        self.set_content_height(self.HAUT_BARRES + (24 if hybride else 2))
        if self.pourcents:
            plus = max(self.pourcents)
            self.update_property([Gtk.AccessibleProperty.LABEL], [
                f'Charge de chaque cœur du processeur : {len(self.pourcents)} cœurs, le plus chargé à {round(plus)} %'])
        self.queue_draw()

    def _dessiner(self, _zone, cr, largeur, hauteur):
        n = len(self.pourcents)
        if not n:
            return
        fg = _fg(self)
        plan = donnees.disposition_coeurs(n, largeur, self.types, self.coeurs_performants)
        h = max(self.HAUT_BARRES, hauteur - (24 if plan['groupes'] else 2))
        couleurs = {'normal': couleur(self, 'gagarine_cpu'), 'vigilance': couleur(self, 'gagarine_vigilance_fond'),
                    'alerte': couleur(self, 'gagarine_alerte_fond')}
        for i, x, w in plan['barres']:
            pct = max(0.0, min(100.0, self.pourcents[i]))
            _source(cr, fg, 0.09)
            _rect_arrondi(cr, x, 0, w, h, 3)
            cr.fill()
            plein = h * pct / 100
            if plein > 0.5:
                cr.save()
                _rect_arrondi(cr, x, 0, w, h, 3)
                cr.clip()
                _source(cr, couleurs[donnees.niveau_cpu(pct)])
                cr.rectangle(x, h - max(plein, 2), w, max(plein, 2))
                cr.fill()
                cr.restore()
        # les deux fils d'un même cœur P : une accolade discrète sous la paire
        for x0, x1 in plan['paires']:
            _source(cr, fg, 0.35)
            cr.set_line_width(1.5)
            cr.move_to(x0 + 1, h + 4)
            cr.line_to(x1 - 1, h + 4)
            cr.stroke()
        for type_, x0, _x1 in plan['groupes']:
            layout = mise_en_page(self, 'Performants (P)' if type_ == 'P' else 'Économes (E)', 0.8)
            _ecrire(cr, layout, x0, h + 8, fg, 0.62)
