"""Page « Programmes » : programmes figés (Reprendre, Arrêter) et plus gros programmes."""
import getpass

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import donnees
from .outils import bouton, confirmer, echappe, etiquette, ligne_vide, synchroniser


class PageProgrammes(Adw.PreferencesPage):
    def __init__(self, fenetre):
        super().__init__(title='Programmes', name='programmes', icon_name='media-playback-pause-symbolic')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.lignes_figes, self.lignes_gros = {}, {}
        self.figes = []
        self.utilisateur = getpass.getuser()

        self.groupe_figes = Adw.PreferencesGroup(
            title='Programmes figés',
            description='Gagarine fige le programme qui grossit le plus vite quand la RAM approche du seuil. '
                        'Vous décidez ensuite de le reprendre ou de l’arrêter.')
        self.vide_figes = ligne_vide('Aucun programme figé', 'La RAM est sous contrôle : Gagarine n’a rien eu à faire.')
        self.add(self.groupe_figes)
        self.groupe_gros = Adw.PreferencesGroup(
            title='Plus gros programmes', description='Les dix programmes qui prennent le plus de mémoire (RAM et swap).')
        self.vide_gros = ligne_vide('Liste indisponible', 'Elle s’affichera dès que Gagarine répondra.', 'dialog-information-symbolic')
        self.add(self.groupe_gros)
        self.groupe_figes.add(self.vide_figes)
        self.groupe_gros.add(self.vide_gros)
        self.source.connect('etat', lambda *_: self.maj())
        self.source.connect('ram', lambda *_: self.maj_temps())
        self.maj()

    # --- mise à jour ---
    def maj(self):
        etat = self.source.etat
        if not etat:
            return
        self.figes = {f.get('pid'): f for f in etat['figes']}
        self._basculer(self.groupe_figes, self.vide_figes, bool(self.figes))
        synchroniser(self.groupe_figes, self.lignes_figes, list(self.figes), self._creer_fige, self._maj_fige)
        gros = {g.get('pid'): g for g in etat['plus_gros']}
        self._basculer(self.groupe_gros, self.vide_gros, bool(gros))
        synchroniser(self.groupe_gros, self.lignes_gros, list(gros), self._creer_gros,
                     lambda pid, ligne: self._maj_gros(ligne, gros[pid]))

    @staticmethod
    def _basculer(groupe, vide, a_des_lignes):
        """La ligne d'état vide est visible seulement quand la liste est vide."""
        vide.set_visible(not a_des_lignes)

    def maj_temps(self):
        for pid, ligne in self.lignes_figes.items():
            if pid in self.figes:
                self._maj_fige(pid, ligne)

    # --- programmes figés ---
    def _creer_fige(self, pid):
        r = Adw.ActionRow(subtitle_lines=3, selectable=False)
        r.add_prefix(Gtk.Image.new_from_icon_name('media-playback-pause-symbolic'))
        r.reprendre = bouton('Reprendre', lambda: self.reprendre(pid))
        r.arreter = bouton('Arrêter', lambda: self.demander_arret(pid), ['destructive-action'])
        r.add_suffix(r.reprendre)
        r.add_suffix(r.arreter)
        return r

    def _maj_fige(self, pid, ligne):
        f = self.figes[pid]
        ligne.set_title(echappe(f.get('nom') or 'programme'))
        morceaux = [f'PID {pid}', donnees.formater_taille_mo(f.get('memoire_mo')), donnees.depuis(f.get('depuis'))]
        if f.get('utilisateur'):
            morceaux.append(f['utilisateur'])
        sous = ' · '.join(morceaux)
        if f.get('commande'):
            sous += '\n' + echappe(donnees.tronquer(f['commande'], 110))
        ligne.set_subtitle(sous)

    # --- actions ---
    def _base(self, pid):
        """Un programme d'un autre utilisateur passe par pkexec ; le sien ne demande rien."""
        f = self.figes.get(pid, {})
        autre = f.get('utilisateur') not in (None, self.utilisateur)
        return donnees.commande_admin() if autre else donnees.commande_gagarine()

    def reprendre(self, pid):
        nom = self.figes.get(pid, {}).get('nom', 'programme')
        self.fenetre.executer(self._base(pid) + ['reprendre', str(pid)], f'« {nom} » repris')

    def demander_arret(self, pid):
        nom = self.figes.get(pid, {}).get('nom', 'programme')
        confirmer(self.fenetre, f'Arrêter « {nom} » ?',
                  f'Le programme (PID {pid}) sera arrêté définitivement. Ce qu’il n’a pas enregistré sera perdu.',
                  'Arrêter', lambda: self.fenetre.executer(self._base(pid) + ['arreter', str(pid)], f'« {nom} » arrêté'))

    # --- plus gros programmes ---
    def _creer_gros(self, pid):
        r = Adw.ActionRow(selectable=False)
        r.protege = Gtk.Image.new_from_icon_name('changes-prevent-symbolic')
        r.protege.set_tooltip_text('Protégé : Gagarine ne le figera jamais')
        r.add_prefix(Gtk.Image.new_from_icon_name('application-x-executable-symbolic'))
        r.valeur = etiquette('', ['numeric'])
        r.add_suffix(r.valeur)
        r.add_suffix(r.protege)
        return r

    def _maj_gros(self, ligne, g):
        ligne.set_title(echappe(g.get('nom') or 'programme'))
        morceaux = [f'PID {g.get("pid")}']
        if g.get('utilisateur'):
            morceaux.append(g['utilisateur'])
        if g.get('protege'):
            morceaux.append('protégé')
        ligne.set_subtitle(' · '.join(morceaux))
        ligne.protege.set_visible(bool(g.get('protege')))
        swap = g.get('swap_mo') or 0
        ligne.valeur.set_label(donnees.formater_taille_mo((g.get('rss_mo') or 0)) +
                               (f' + {donnees.formater_taille_mo(swap)} de swap' if swap else ''))

