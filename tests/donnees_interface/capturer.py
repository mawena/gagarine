#!/usr/bin/env python3
"""Captures des pages de l'interface dans docs/captures/, avec le faux gagarine, SOUS Xvfb uniquement :

    dbus-run-session -- xvfb-run -a -s "-screen 0 1280x860x24" env GDK_BACKEND=x11 python3 tests/donnees_interface/capturer.py [DOSSIER]

Refuse de tourner sans Xvfb (jamais d'ouverture de fenêtre sur l'écran réel) et sans session D-Bus privée : l'interface
est une application unique (org.gagarine…), et sur le bus de la session une interface déjà ouverte serait réveillée
au lieu que la capture s'ouvre. `dbus-run-session` donne un bus à part.
"""
import math
import os
import sys
import tempfile
import time

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RACINE)
DOSSIER = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RACINE, 'docs', 'captures')

if os.environ.get('GDK_BACKEND') != 'x11' or os.environ.get('DISPLAY', ':1') == ':1' or not os.environ.get('DISPLAY'):
    sys.exit('À lancer sous Xvfb : xvfb-run -a -s "-screen 0 1280x860x24" env GDK_BACKEND=x11 python3 ' + sys.argv[0])

bus = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
if not bus or bus == f'unix:path=/run/user/{os.getuid()}/bus':
    sys.exit('À lancer avec une session D-Bus privée : dbus-run-session -- xvfb-run … (voir l\'en-tête)')

# environnement isolé : faux gagarine, pas d'indicateur réel, pas de réglages du poste
tmp = tempfile.mkdtemp(prefix='gagarine-captures-')
os.environ['GAGARINE_COMMANDE'] = os.path.join(RACINE, 'tests', 'donnees_interface', 'faux-gagarine')
os.environ['XDG_CONFIG_HOME'] = os.path.join(tmp, 'config')
os.environ['XDG_STATE_HOME'] = os.path.join(tmp, 'state')
os.environ['GAGARINE_INDICATEUR'] = '/bin/true'
os.environ.setdefault('FAUX_RAM', '78.4')
os.environ.setdefault('FAUX_CHAUD', '1')      # température en orange sur la capture du processeur
os.environ.setdefault('GSK_RENDERER', 'cairo')

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk

from gagarine.interface.application import Application


def historique_exemple(source):
    """Remplit la courbe : montée, pic au-dessus du seuil, retombée ; le processeur (1.4.0) monte avec un rendu, puis retombe."""
    maintenant = time.monotonic()
    source.historique.clear()
    source.cpu_historique.clear()
    source.chemin_stat = '/inexistant'       # plus de mesure réelle : la capture montre les données d'essai
    for i in range(0, 296):
        t = 296 - i
        c = 24 + 6 * math.sin(i / 9) + (3 if (i // 13) % 2 else -2)
        if i > 120:        # le rendu commence : le processeur monte et reste occupé
            c = 60 + 14 * min(1.0, (i - 120) / 25) - 0.06 * (i - 120) + 5 * math.sin(i / 3)
        source.cpu_historique.append((maintenant - t, max(2.0, min(c, 99.0))))
    # mesure locale de la seconde : un rendu ffmpeg occupe les 4 cœurs P et une partie des E (cf. faux-gagarine)
    source.cpu_coeurs = [88.0, 91.5, 74.0, 79.5, 34.0, 41.5, 22.0, 63.0, 18.5, 27.0, 55.0, 12.0, 8.5, 46.0]
    source.cpu_pourcent = sum(source.cpu_coeurs) / len(source.cpu_coeurs)
    for i in range(0, 296):
        t = 296 - i
        p = 58 + 8 * (i / 296) + (3 if (i // 17) % 2 else -2)
        if 150 < i < 175:
            p = 70 + (i - 150) * 1.05
        elif 175 <= i < 190:
            p = 96.5 - (i - 175) * 1.9
        source.historique.append((maintenant - t, min(p, 97.5)))


def photographier(fenetre, nom):
    """Enregistre la fenêtre en PNG. Si elle n'a pas (encore) de moteur de rendu, la remontre et réessaie quelques fois."""
    for _ in range(20):
        natif = fenetre.get_native()
        if natif is not None and natif.get_renderer() is not None:
            break
        print('fenêtre sans moteur de rendu : nouvelle tentative', flush=True)
        fenetre.present()
        contexte = GLib.MainContext.default()
        fin = time.monotonic() + 0.5
        while time.monotonic() < fin:
            contexte.iteration(False)
    else:
        print('capture abandonnée :', nom, flush=True)
        return
    w, h = fenetre.get_width(), fenetre.get_height()
    paintable = Gtk.WidgetPaintable.new(fenetre)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    noeud = snap.to_node()
    texture = fenetre.get_native().get_renderer().render_texture(noeud, None)
    chemin = os.path.join(DOSSIER, nom + '.png')
    texture.save_to_png(chemin)
    print('capture', chemin, f'{w}x{h}', flush=True)


def defiler(page, fraction):
    """Fait défiler une page jusqu'à une fraction de sa hauteur (0 = haut, 1 = bas), une fois la mise en page faite."""
    def aller():
        a = _defileur(page).get_vadjustment()
        a.set_value(a.get_lower() + fraction * max(0.0, a.get_upper() - a.get_page_size() - a.get_lower()))
        return False
    GLib.timeout_add(900, aller)


def periode(f, cle):
    f.pages['apercu'].boutons_periode[cle].set_active(True)


def _defileur(w):
    pile = [w]
    while pile:
        n = pile.pop()
        if isinstance(n, Gtk.ScrolledWindow):
            return n
        c = n.get_first_child()
        while c:
            pile.append(c)
            c = c.get_next_sibling()


def main():
    os.makedirs(DOSSIER, exist_ok=True)
    app = Application()
    # (nom du fichier, page, action avant la prise, attente en ms)
    etapes = [
        ('apercu', 'apercu', lambda f: periode(f, '5min'), 1500),
        ('apercu-24h', 'apercu', lambda f: periode(f, '24h'), 2500),
        ('apercu-7j', 'apercu', lambda f: (periode(f, '7j'), defiler(f.pages['apercu'], 0.62)), 2500),
        ('apercu-processeur', 'apercu', lambda f: (periode(f, '5min'), defiler(f.pages['apercu'], 0.38)), 2500),
        ('programmes', 'programmes', None, 1500),
        ('programmes-actifs', 'programmes', lambda f: defiler(f.pages['programmes'], 0.55), 2500),
        ('programmes-menage', 'programmes', lambda f: defiler(f.pages['programmes'], 0.5), 2500),
        ('programmes-gros', 'programmes', lambda f: defiler(f.pages['programmes'], 1), 2500),
        ('programmes-plafond', 'programmes', lambda f: f.pages['programmes'].regler_plafond(next(iter(f.pages['programmes'].applis))), 2500),
        ('file', 'file', None, 1500),
        ('file-historique', 'file', lambda f: defiler(f.pages['file'], 1), 2500),
        ('journal', 'journal', None, 1500), ('reglages', 'reglages', None, 1500),
        ('reglages-notifications', 'reglages', lambda f: defiler(f.pages['reglages'], 0.3), 2500),
        ('reglages-service', 'reglages', lambda f: (defiler(f.pages['reglages'], 1), f.pages['reglages'].lancer_essai_cmd(False)), 5500),
        ('file-priorite', 'file', lambda f: f.pages['file'].changer_priorite('1762785100000-1301.json'), 1500),
        ('journal-fuites', 'journal', lambda f: (f.pages['journal'].recherche.set_text('fuite'), f.pages['journal'].maj(force=True)), 1500),
        ('reglages-fuites', 'reglages', lambda f: defiler(f.pages['reglages'], 0.62), 2500),
        ('nouveau-travail', 'file', lambda f: f.pages['file'].nouveau_travail(), 1500),
        ('sortie-travail', 'file', lambda f: f.pages['file'].voir_sortie(next(iter(f.pages['file'].travaux))), 1500),
        ('etroit', 'apercu', lambda f: (periode(f, '24h'), defiler(f.pages['apercu'], 0), f.set_default_size(420, 720)), 2500),
    ]

    # CAPTURES_DEPUIS=nom : reprend à cette capture (les précédentes sont gardées) ; CAPTURES_SEULEMENT=a,b : seulement celles-là
    depuis, seulement = os.environ.get('CAPTURES_DEPUIS'), os.environ.get('CAPTURES_SEULEMENT')
    if depuis:
        etapes[:] = etapes[[e[0] for e in etapes].index(depuis):]
    if seulement:
        etapes[:] = [e for e in etapes if e[0] in seulement.split(',')]

    def demarre(_app):
        f = app.fenetre
        f.set_default_size(920, 800)
        suite = iter(etapes)

        def pas():
            try:
                nom, page, action, attente = next(suite)
            except StopIteration:
                app.quit()
                return GLib.SOURCE_REMOVE
            print('étape', nom, flush=True)
            if f.get_visible_dialog():
                f.get_visible_dialog().force_close()
            f.aller_a(page)
            historique_exemple(f.source)
            if action:
                action(f)

            def prise():
                historique_exemple(f.source)
                f.pages['apercu'].maj_ram()
                GLib.timeout_add(300, lambda: (photographier(f, nom), GLib.timeout_add(100, pas), False)[2])
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(attente, prise)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(3500, pas)
    app.connect_after('activate', demarre)
    app.run([sys.argv[0]])


main()
