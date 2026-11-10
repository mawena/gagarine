"""Page « Programmes » : programmes figés (Reprendre, Arrêter), ménage des inactifs, plafond des applications du bureau
et plus gros programmes."""
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

        # ménage : démons et modèles inactifs (commande de la 1.2.0, section masquée si le cœur ne la connaît pas)
        self.groupe_menage = Adw.PreferencesGroup(
            title='Ménage', visible=False,
            description='Démons et modèles restés inactifs qui gardent de la mémoire.')
        self.vide_menage = ligne_vide('Rien à nettoyer', 'Aucun démon ni modèle inactif ne garde de mémoire.')
        self.groupe_menage.add(self.vide_menage)
        self.lignes_menage, self.candidats = {}, {}
        self.add(self.groupe_menage)

        # applications du bureau : plafond de mémoire
        self.groupe_applis = Adw.PreferencesGroup(
            title='Applications du bureau', visible=False,
            description='Un plafond ralentit et range dans le swap une application qui le dépasse, sans jamais la fermer.')
        self.vide_applis = ligne_vide('Aucune application détectée', 'Les applications lancées depuis le bureau '
                                      'apparaîtront ici.', 'dialog-information-symbolic')
        self.groupe_applis.add(self.vide_applis)
        self.lignes_applis, self.applis = {}, {}
        self.add(self.groupe_applis)

        self.groupe_gros = Adw.PreferencesGroup(
            title='Plus gros programmes', description='Les dix programmes qui prennent le plus de mémoire (RAM et swap).')
        self.vide_gros = ligne_vide('Liste indisponible', 'Elle s’affichera dès que Gagarine répondra.', 'dialog-information-symbolic')
        self.add(self.groupe_gros)
        self.groupe_figes.add(self.vide_figes)
        self.groupe_gros.add(self.vide_gros)
        self.source.connect('etat', lambda *_: self.maj())
        self.source.connect('menage', lambda *_: self.maj_menage())
        self.source.connect('applications', lambda *_: self.maj_applications())
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

    # --- ménage ---
    def maj_menage(self):
        etat, message = self.source.etat_option('menage')
        self.groupe_menage.set_visible(etat in ('ok', 'erreur'))
        if etat == 'erreur':
            self.vide_menage.set_title('Ménage indisponible')
            self.vide_menage.set_subtitle(echappe(message))
            self.vide_menage.set_visible(True)
            return
        m = self.source.menage
        if not m:
            return
        self.vide_menage.set_title('Rien à nettoyer')
        self.vide_menage.set_subtitle('Aucun démon ni modèle inactif ne garde de mémoire.')
        self.candidats = {str(c['id']): c for c in m['candidats']}
        self.vide_menage.set_visible(not self.candidats)
        synchroniser(self.groupe_menage, self.lignes_menage, list(self.candidats), self._creer_candidat, self._maj_candidat)
        n, mo = donnees.total_menage(m)
        self.groupe_menage.set_description(
            (f'{donnees.pluriel(n, "programme inactif", "programmes inactifs")}, {donnees.formater_taille_mo(mo)} récupérables. '
             if n else '') + donnees.etat_menage_auto(m))

    def _creer_candidat(self, cle):
        r = Adw.ActionRow(subtitle_lines=3, selectable=False)
        r.add_prefix(Gtk.Image.new_from_icon_name('user-trash-symbolic'))
        r.action = bouton('Arrêter', lambda: self.demander_menage(cle), ['destructive-action'])
        r.add_suffix(r.action)
        return r

    def _maj_candidat(self, cle, ligne):
        c = self.candidats[cle]
        ligne.set_title(echappe(c.get('nom') or 'programme'))
        ligne.set_subtitle(echappe(donnees.detail_menage(c)))
        ligne.action.set_label(donnees.libelle_action_menage(c))

    def demander_menage(self, cle):
        c = self.candidats.get(cle)
        if not c:
            return
        nom = c.get('nom') or 'ce programme'
        decharge = donnees.action_menage(c) == 'decharger'
        corps = (f'« {nom} » sera déchargé : les {donnees.formater_taille_mo(c.get("memoire_mo"))} qu’il garde seront libérés. '
                 'Il se rechargera au prochain usage.' if decharge else
                 f'« {nom} » ({donnees.formater_taille_mo(c.get("memoire_mo"))}) sera arrêté. Il est inactif ; '
                 's’il a quelque chose de non enregistré, cela sera perdu.')
        confirmer(self.fenetre, f'{donnees.libelle_action_menage(c)} « {nom} » ?', corps, donnees.libelle_action_menage(c),
                  lambda: self.fenetre.executer(donnees.arguments_menage(donnees.commande_gagarine(), c),
                                                f'« {nom} » {"déchargé" if decharge else "arrêté"}'))

    # --- applications du bureau ---
    def maj_applications(self):
        etat, message = self.source.etat_option('applications')
        self.groupe_applis.set_visible(etat in ('ok', 'erreur'))
        if etat == 'erreur':
            self.vide_applis.set_title('Applications indisponibles')
            self.vide_applis.set_subtitle(echappe(message))
            self.vide_applis.set_visible(True)
            return
        a = self.source.applications
        if not a:
            return
        self.vide_applis.set_title('Aucune application détectée')
        self.vide_applis.set_subtitle('Les applications lancées depuis le bureau apparaîtront ici.')
        self.applis = {x['id']: x for x in a['applications']}
        self.vide_applis.set_visible(not self.applis)
        synchroniser(self.groupe_applis, self.lignes_applis, list(self.applis), self._creer_appli, self._maj_appli)

    def _creer_appli(self, cle):
        r = Adw.ActionRow(selectable=False)
        r.add_prefix(Gtk.Image.new_from_icon_name('view-app-grid-symbolic'))
        r.plafond = bouton('', lambda: self.regler_plafond(cle), aide='Régler le plafond de mémoire')
        r.add_suffix(r.plafond)
        return r

    def _maj_appli(self, cle, ligne):
        a = self.applis[cle]
        ligne.set_title(echappe(a.get('nom') or cle))
        ligne.set_subtitle(echappe(donnees.detail_application(a)))
        ligne.plafond.set_label(donnees.libelle_plafond(a).capitalize())

    def regler_plafond(self, cle):
        a = self.applis.get(cle)
        if not a:
            return
        nom = a.get('nom') or cle
        courant = a.get('plafond_go') or 0
        d = Adw.AlertDialog.new(f'Plafond de mémoire de « {nom} »',
                                'Au-delà du plafond, l’application est ralentie et rangée dans le swap ; elle n’est jamais '
                                'fermée. Appliqué tout de suite aux fenêtres ouvertes et à chaque lancement.')
        spin = Gtk.SpinButton.new_with_range(0.5, 64, 0.5)
        spin.set_value(courant or max(1.0, round((a.get('memoire_mo') or 2048) / 1024 * 1.5 * 2) / 2))
        spin.set_digits(1)
        spin.set_halign(Gtk.Align.CENTER)
        spin.update_property([Gtk.AccessibleProperty.LABEL], ['Plafond en gigaoctets'])
        ligne = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        ligne.append(spin)
        ligne.append(Gtk.Label(label='Go'))
        d.set_extra_child(ligne)
        d.add_response('annuler', 'Annuler')
        if courant:
            d.add_response('aucun', 'Retirer le plafond')
        d.add_response('ok', 'Appliquer')
        d.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response('ok')
        d.set_close_response('annuler')

        def repondu(_d, reponse):
            if reponse == 'ok':
                go = spin.get_value()
                self._plafond(a, go, f'Plafond de « {nom} » : {donnees.formater_go(go)}')
            elif reponse == 'aucun':
                self._plafond(a, None, f'Plafond de « {nom} » retiré')
        d.connect('response', repondu)
        d.present(self.fenetre)

    def _plafond(self, a, go, message):
        self.fenetre.executer(donnees.arguments_plafond(donnees.commande_gagarine(), a['id'], go), message)

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

