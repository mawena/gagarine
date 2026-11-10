#!/usr/bin/env python3
"""Captures des pages de l'interface dans docs/captures/, avec le faux gagarine, SOUS Xvfb uniquement :

    xvfb-run -a -s "-screen 0 1280x860x24" env GDK_BACKEND=x11 python3 tests/donnees_interface/capturer.py [DOSSIER]

Refuse de tourner sans Xvfb (jamais d'ouverture de fenêtre sur l'écran réel).
"""
import os
import sys
import tempfile
import time

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RACINE)
DOSSIER = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RACINE, 'docs', 'captures')

if os.environ.get('GDK_BACKEND') != 'x11' or os.environ.get('DISPLAY', ':1') == ':1' or not os.environ.get('DISPLAY'):
    sys.exit('À lancer sous Xvfb : xvfb-run -a -s "-screen 0 1280x860x24" env GDK_BACKEND=x11 python3 ' + sys.argv[0])

# environnement isolé : faux gagarine, pas d'indicateur réel, pas de réglages du poste
tmp = tempfile.mkdtemp(prefix='gagarine-captures-')
os.environ['GAGARINE_COMMANDE'] = os.path.join(RACINE, 'tests', 'donnees_interface', 'faux-gagarine')
os.environ['XDG_CONFIG_HOME'] = os.path.join(tmp, 'config')
os.environ['XDG_STATE_HOME'] = os.path.join(tmp, 'state')
os.environ['GAGARINE_INDICATEUR'] = '/bin/true'
os.environ.setdefault('FAUX_RAM', '78.4')
os.environ.setdefault('GSK_RENDERER', 'cairo')

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk

from gagarine.interface.application import Application


def historique_exemple(source):
    """Remplit la courbe : montée, pic au-dessus du seuil, retombée."""
    maintenant = time.monotonic()
    source.historique.clear()
    for i in range(0, 296):
        t = 296 - i
        p = 58 + 8 * (i / 296) + (3 if (i // 17) % 2 else -2)
        if 150 < i < 175:
            p = 70 + (i - 150) * 1.05
        elif 175 <= i < 190:
            p = 96.5 - (i - 175) * 1.9
        source.historique.append((maintenant - t, min(p, 97.5)))


def photographier(fenetre, nom):
    w, h = fenetre.get_width(), fenetre.get_height()
    paintable = Gtk.WidgetPaintable.new(fenetre)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    noeud = snap.to_node()
    texture = fenetre.get_native().get_renderer().render_texture(noeud, None)
    chemin = os.path.join(DOSSIER, nom + '.png')
    texture.save_to_png(chemin)
    print('capture', chemin, f'{w}x{h}', flush=True)


def main():
    os.makedirs(DOSSIER, exist_ok=True)
    app = Application()
    etapes = [('apercu', None), ('programmes', None), ('file', None), ('journal', None), ('reglages', None),
              ('nouveau-travail', 'file'), ('sortie-travail', 'file'), ('etroit', 'apercu')]

    def demarre(_app):
        f = app.fenetre
        f.set_default_size(920, 760)
        suite = iter(etapes)

        def pas():
            try:
                nom, page = next(suite)
            except StopIteration:
                app.quit()
                return GLib.SOURCE_REMOVE
            if f.get_visible_dialog():
                f.get_visible_dialog().force_close()
            f.aller_a(page or nom)
            if nom == 'nouveau-travail':
                f.pages['file'].nouveau_travail()
            elif nom == 'sortie-travail':
                f.pages['file'].voir_sortie(next(iter(f.pages['file'].travaux)))
            elif nom == 'etroit':
                f.set_default_size(410, 720)
            if nom in ('nouveau-travail', 'sortie-travail'):
                pass
            GLib.timeout_add(1500, lambda: (historique_exemple(f.source), f.pages['apercu'].maj_ram(),
                                            GLib.timeout_add(500, lambda: (photographier(f, nom), GLib.timeout_add(100, pas), 0)[2]),
                                            0)[3])
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(3500, pas)
    app.connect_after('activate', demarre)
    app.run([sys.argv[0]])


main()
