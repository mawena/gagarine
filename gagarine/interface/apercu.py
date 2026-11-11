"""Page « Vue d'ensemble » : jauge de la RAM, processeur, courbe, swap, service."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import donnees
from .graphiques import Coeurs, Courbe, Jauge
from .outils import echappe, etiquette, page_preferences, synchroniser


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

        # processeur (1.4.0) : mesure locale chaque seconde, détails de `etat --json` quand le cœur les donne
        groupe = Adw.PreferencesGroup(title='Processeur')
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        en_tete = Gtk.Box(spacing=12)
        self.cpu_pourcent = Gtk.Label(label='— %', xalign=0)
        self.cpu_pourcent.add_css_class('title-1')
        self.cpu_pourcent.add_css_class('numeric')
        self.cpu_detail = Gtk.Label(label='', xalign=0, hexpand=True, valign=Gtk.Align.END, wrap=True)
        self.cpu_detail.add_css_class('dim-label')
        self.cpu_statut = Gtk.Label(label='', valign=Gtk.Align.CENTER)
        self.cpu_statut.add_css_class('pill-etat')
        en_tete.append(self.cpu_pourcent)
        en_tete.append(self.cpu_detail)
        en_tete.append(self.cpu_statut)
        self.coeurs = Coeurs()
        boite.append(en_tete)
        boite.append(self.coeurs)
        self.cpu_legende = Gtk.Label(label='', xalign=0, wrap=True, use_markup=True)
        self.cpu_legende.add_css_class('dim-label')
        self.cpu_legende.add_css_class('caption')
        boite.append(self.cpu_legende)
        groupe.add(self._carte(boite))
        self.groupe_cpu = groupe
        self.add(groupe)

        self.groupe_courbe = Adw.PreferencesGroup(title='Les cinq dernières minutes')
        self.periode = '5min'
        self.boutons_periode = {}
        selecteur = Gtk.Box(valign=Gtk.Align.CENTER)
        selecteur.add_css_class('linked')
        premier = None
        for cle, libelle, _ in donnees.PERIODES:
            b = Gtk.ToggleButton(label=libelle, active=cle == self.periode)
            b.set_tooltip_text(f'Afficher la RAM et le processeur sur {libelle}')
            if premier is None:
                premier = b
            else:
                b.set_group(premier)
            b.connect('toggled', self._periode_choisie, cle)
            self.boutons_periode[cle] = b
            selecteur.append(b)
        self.groupe_courbe.set_header_suffix(selecteur)
        self.courbe = Courbe(self.source)
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        boite.append(self.courbe)
        self.resume = Gtk.Label(label='', xalign=0, wrap=True)
        self.resume.add_css_class('dim-label')
        self.resume.add_css_class('caption')
        self.resume.set_visible(False)
        boite.append(self.resume)
        self.groupe_courbe.add(self._carte(boite))
        self.add(self.groupe_courbe)

        # incidents et plus gros programmes de la période (24 h et 7 jours)
        self.groupe_incidents = Adw.PreferencesGroup(title='Incidents de la période', visible=False)
        self.lignes_incidents = []
        self.add(self.groupe_incidents)
        self.groupe_gros = Adw.PreferencesGroup(
            title='Les plus gros de la période', visible=False,
            description='Les programmes qui ont le plus pris de mémoire (pic mesuré une fois par minute).')
        self.lignes_gros = []
        self.add(self.groupe_gros)
        self.groupe_actifs = Adw.PreferencesGroup(
            title='Les plus actifs de la période', visible=False,
            description='Les programmes qui ont le plus occupé le processeur, en minutes de processeur (un cœur plein pendant '
                        'une minute). Les programmes du même nom sont additionnés.')
        self.lignes_actifs = []
        self.add(self.groupe_actifs)

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
        self.source.connect('etat', lambda *_: self.maj_cpu())
        self.source.connect('historique', lambda *_: self.maj_historique())
        self.maj_ram()
        self.maj_etat()
        self.maj_cpu()

    # --- période de la courbe ---
    def _periode_choisie(self, bouton, cle):
        if not bouton.get_active():
            return
        self.periode = cle
        self.courbe.choisir_periode(cle)
        titres = {'5min': 'Les cinq dernières minutes', '24h': 'Les dernières 24 heures', '7j': 'Les 7 derniers jours'}
        self.groupe_courbe.set_title(titres[cle])
        if cle != '5min':
            self.source.suivre_historique(cle)
        self.maj_historique()

    def maj_historique(self):
        long = self.periode != '5min'
        for g in (self.groupe_incidents, self.groupe_gros, self.groupe_actifs):
            g.set_visible(False)
        self.resume.set_visible(long)
        if not long:
            return
        hist = self.source.historiques.get(self.periode)
        if hist is None:
            etat, message = self.source.etat_option('historique-' + self.periode)
            self.resume.set_label('Lecture de l’historique…' if etat == 'attente' else
                                  'L’historique n’est pas disponible avec cette version de Gagarine.' if etat == 'absente'
                                  else f'Historique indisponible : {message}')
            self.courbe.queue_draw()
            return
        self.resume.set_label(donnees.resume_historique(hist))
        self.courbe.queue_draw()
        for ligne in self.lignes_incidents:
            self.groupe_incidents.remove(ligne)
        self.lignes_incidents = []
        for ev in list(reversed(hist['incidents']))[:6]:
            icone, titre, detail = donnees.decrire_evenement(ev)
            r = Adw.ActionRow(title=echappe(titre), subtitle=echappe(detail), subtitle_lines=2, selectable=False)
            r.add_prefix(Gtk.Image.new_from_icon_name(icone))
            r.add_suffix(etiquette(donnees.heure_locale(ev.get('date')), ['dim-label', 'numeric']))
            self.groupe_incidents.add(r)
            self.lignes_incidents.append(r)
        n = len(hist['incidents'])
        self.groupe_incidents.set_description(
            '' if not n else f'Marqués par un point rouge sur la courbe{f" ({min(n, 6)} derniers sur {n})" if n > 6 else ""}.')
        self.groupe_incidents.set_visible(bool(n))
        for ligne in self.lignes_gros:
            self.groupe_gros.remove(ligne)
        self.lignes_gros = []
        for g in sorted(hist['gros'], key=lambda g: g.get('pic_mo') or 0, reverse=True)[:5]:
            titre, detail = donnees.titre_gros(g)
            r = Adw.ActionRow(title=echappe(titre), subtitle=echappe(detail), selectable=False)
            r.add_prefix(Gtk.Image.new_from_icon_name('application-x-executable-symbolic'))
            self.groupe_gros.add(r)
            self.lignes_gros.append(r)
        self.groupe_gros.set_visible(bool(self.lignes_gros))
        for ligne in self.lignes_actifs:
            self.groupe_actifs.remove(ligne)
        self.lignes_actifs = []
        for a in hist['actifs'][:5]:       # vide avec un cœur plus ancien que la 1.4.0 : le groupe reste masqué
            titre, detail = donnees.titre_actif_periode(a)
            r = Adw.ActionRow(title=echappe(titre), subtitle=echappe(detail), selectable=False)
            r.add_prefix(Gtk.Image.new_from_icon_name('system-run-symbolic'))
            self.groupe_actifs.add(r)
            self.lignes_actifs.append(r)
        self.groupe_actifs.set_visible(bool(self.lignes_actifs))

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
        self.maj_cpu()
        if self.periode == '5min':
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

    def maj_cpu(self):
        """Groupe « Processeur » : mesure locale (% et cœurs, chaque seconde) et détails du service (modèle, types,
        température, fréquence, pression) quand `etat --json` les donne. Sans mesure du tout, le groupe est masqué."""
        s = self.source
        proc = s.processeur()
        pct = s.cpu_pourcent if s.cpu_pourcent is not None else (proc or {}).get('pourcent')
        coeurs = s.cpu_coeurs or (proc or {}).get('par_coeur') or []
        self.groupe_cpu.set_visible(pct is not None)
        if pct is None:
            return
        self.cpu_pourcent.set_label(donnees.formater_pourcent(pct))
        self.cpu_detail.set_label(donnees.detail_processeur(proc, len(coeurs) or None))
        types = (proc or {}).get('types') or []
        self.coeurs.maj(coeurs, types if len(types) == len(coeurs) else [])
        texte, classe = donnees.pastille_cpu(s.cpu_moyen() if s.cpu_pourcent is not None else pct)
        for c in ('success', 'warning', 'error'):
            self.cpu_statut.remove_css_class(c)
        if classe:
            self.cpu_statut.add_css_class(classe)
        self.cpu_statut.set_label(texte)
        morceaux = donnees.legende_processeur(proc, len(coeurs) or None)
        self.cpu_legende.set_markup(' · '.join(
            f'<span foreground="#e66100" weight="bold">{echappe(t)}</span>' if chaud else echappe(t) for t, chaud in morceaux))
        self.cpu_legende.set_visible(bool(morceaux))
        if self.periode == '5min':
            self.courbe.queue_draw()

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
