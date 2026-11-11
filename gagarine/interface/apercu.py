"""Page « Aperçu » (1.5.0, docs/conception-1.5.0.md) : phrase d'état et voyant, instruments RAM et processeur côte à côte,
courbe pleine largeur, puis trois colonnes : swap, plus gros (ou incidents), plus actifs."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import donnees
from .graphiques import Coeurs, Courbe, Jauge, couleur_hex
from .outils import ListeEncadree, PageLarge, Section, Voyant, changer_classe, echappe, etiquette, synchroniser

BASELINE = getattr(Gtk.Align, 'BASELINE_FILL', Gtk.Align.BASELINE)
TITRES_COURBE = {'5min': 'Les cinq dernières minutes', '24h': 'Les dernières 24 heures', '7j': 'Les 7 derniers jours'}
NIVEAUX_RAM = {'normal': ('Calme', ''), 'vigilance': ('Vigilance', 'vigilance'), 'alerte': ('Seuil atteint', 'alerte')}
NIVEAUX_CPU = {'success': '', 'warning': 'vigilance', 'error': 'alerte'}


def lecture(valeur, unite):
    """Balisage d'une lecture d'instrument : grand nombre, unité plus petite et atténuée sur la même ligne de base."""
    return f'{echappe(valeur)}<span size="40%" weight="normal" fgalpha="62%">{echappe(unite)}</span>'


def insecables(morceaux, chaud=None):
    """Balisage de morceaux séparés par « · » : une ligne ne se coupe qu'entre deux morceaux, jamais au milieu d'une valeur
    (« 97 °C », « 7,5 Go libres »). morceaux : [texte] ou [(texte, chaud)] ; chaud : couleur des morceaux chauds."""
    sortie = []
    for m in morceaux:
        texte, est_chaud = m if isinstance(m, tuple) else (m, False)
        style = f' foreground="{chaud}" weight="bold"' if est_chaud and chaud else ''
        sortie.append(f'<span allow_breaks="false"{style}>{echappe(texte)}</span>')
    return ' · '.join(sortie)


def ligne_info(titre, sous_titre='', icone=None, valeur=None):
    """Adw.ActionRow d'information sur une ligne : titre et sous-titre coupés avec « … », texte entier en infobulle."""
    r = Adw.ActionRow(title=echappe(titre), subtitle=echappe(sous_titre), title_lines=1, subtitle_lines=1,
                      selectable=False, activatable=False)
    r.set_tooltip_text(f'{titre}\n{sous_titre}' if sous_titre else titre)
    if icone:
        r.add_prefix(Gtk.Image.new_from_icon_name(icone))
    r.valeur = etiquette(valeur or '', ['numeric'])
    r.valeur.set_visible(bool(valeur))
    r.add_suffix(r.valeur)
    return r


class PageApercu(PageLarge):
    def __init__(self, fenetre):
        super().__init__('Aperçu', 'apercu', 'computer-symbolic')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.lignes_swap, self.lignes_gros_direct, self.lignes_actifs_direct = {}, {}, {}
        self.lignes_incidents, self.lignes_gros, self.lignes_actifs = [], [], []
        self.periode = '5min'

        self._phrase_etat()
        self._instruments()
        self._courbe()
        self._colonnes_du_bas()

        self.source.connect('ram', lambda *_: self.maj_ram())
        self.source.connect('etat', lambda *_: self.maj_etat())
        self.source.connect('historique', lambda *_: self.maj_historique())
        self.maj_ram()
        self.maj_etat()
        self.maj_cpu()
        self.maj_historique()

    # --- construction ---
    def _phrase_etat(self):
        """En tête : voyant, phrase d'état et détail ; bouton vers les Programmes si un programme est figé."""
        self.tete = Gtk.Box(spacing=16)
        self.tete.add_css_class('tete-etat')
        self.voyant = Voyant()
        self.tete.append(self.voyant)
        corps = Gtk.Box(spacing=20, hexpand=True)
        textes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        self.titre_etat = Gtk.Label(xalign=0, wrap=True, max_width_chars=72, accessible_role=Gtk.AccessibleRole.HEADING)
        self.titre_etat.add_css_class('titre-etat')
        self.detail_etat = Gtk.Label(xalign=0, wrap=True, max_width_chars=90)
        self.detail_etat.add_css_class('detail-etat')
        textes.append(self.titre_etat)
        textes.append(self.detail_etat)
        corps.append(textes)
        self.bouton_programmes = Gtk.Button(label='Voir les programmes', valign=Gtk.Align.CENTER, visible=False)
        self.bouton_programmes.add_css_class('suggested-action')
        self.bouton_programmes.connect('clicked', lambda _b: self.fenetre.aller_a('programmes'))
        corps.append(self.bouton_programmes)
        self.tete.append(corps)
        self.niveau_etat = None
        # page moyenne ou étroite : le bouton passe sous la phrase, qui garde toute la largeur
        for point in (self.point_moyen, self.point_etroit):
            point.add_setter(corps, 'orientation', Gtk.Orientation.VERTICAL)
            point.add_setter(corps, 'spacing', 12)
            point.add_setter(self.bouton_programmes, 'halign', Gtk.Align.START)
        self.add(self.tete)

    def _instrument(self, titre):
        """Carte d'instrument : titre et pastille de niveau, lecture et détail, place pour le dessin et sa légende."""
        carte = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=True)
        for c in ('card', 'panneau', 'instrument'):
            carte.add_css_class(c)
        tete = Gtk.Box(spacing=12)
        nom = Gtk.Label(label=titre, xalign=0, hexpand=True, accessible_role=Gtk.AccessibleRole.HEADING)
        nom.add_css_class('titre-section')
        statut = Gtk.Label(label='—', valign=Gtk.Align.CENTER)
        statut.add_css_class('pastille-niveau')
        tete.append(nom)
        tete.append(statut)
        rangee = Gtk.Box(spacing=14)
        valeur = Gtk.Label(label=lecture('—', ' %'), use_markup=True, xalign=0, valign=BASELINE)
        valeur.add_css_class('lecture')
        detail = Gtk.Label(xalign=0, hexpand=True, wrap=True, valign=BASELINE, use_markup=True)
        detail.add_css_class('lecture-unite')
        rangee.append(valeur)
        rangee.append(detail)
        carte.append(tete)
        carte.append(rangee)
        return carte, statut, valeur, detail

    def _instruments(self):
        gauche, droite = self.colonnes(2)
        for colonne in (gauche, droite):
            colonne.set_valign(Gtk.Align.FILL)       # cartes de même hauteur côte à côte
        gauche.get_parent().set_vexpand(False)       # mais la rangée ne s'étire pas dans une grande fenêtre

        carte, self.statut, self.pourcent, self.detail = self._instrument('Mémoire vive')
        self.jauge = Jauge()
        carte.append(self.jauge)
        self.legende = Gtk.Label(xalign=0, wrap=True)
        self.legende.add_css_class('legende-instrument')
        carte.append(self.legende)
        self.carte_ram = carte
        gauche.append(carte)

        # processeur (1.4.0) : mesure locale chaque seconde, détails de `etat --json` quand le cœur les donne
        carte, self.cpu_statut, self.cpu_pourcent, self.cpu_detail = self._instrument('Processeur')
        self.coeurs = Coeurs()
        carte.append(self.coeurs)
        self.cpu_legende = Gtk.Label(xalign=0, wrap=True, use_markup=True)
        self.cpu_legende.add_css_class('legende-instrument')
        carte.append(self.cpu_legende)
        self.groupe_cpu = carte
        droite.append(carte)

    def _courbe(self):
        selecteur = Gtk.Box()
        selecteur.add_css_class('linked')
        self.boutons_periode = {}
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
        self.groupe_courbe = Section(TITRES_COURBE[self.periode], suffixe=selecteur)
        carte = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        carte.add_css_class('card')
        carte.add_css_class('carte-courbe')
        self.courbe = Courbe(self.source)
        carte.append(self.courbe)
        self.groupe_courbe.ajouter(carte)
        self.resume = Gtk.Label(xalign=0, wrap=True, max_width_chars=90, visible=False)
        self.resume.add_css_class('legende-instrument')
        self.groupe_courbe.ajouter(self.resume)
        self.point_etroit.add_setter(self.courbe, 'content-height', 200)
        self.add(self.groupe_courbe)

    def _section(self, colonne, titre, description=None, visible=True):
        section = Section(titre, description)
        section.liste = section.ajouter(ListeEncadree())
        section.set_visible(visible)
        colonne.append(section)
        return section

    def _colonnes_du_bas(self):
        swap, gros, actifs = self.colonnes(3)
        self.groupe_swap = self._section(swap, 'Swap', 'Espace de débordement quand la RAM est pleine.')
        # 5 minutes : qui charge le poste en ce moment ; 24 h et 7 jours : ce que l'historique a retenu
        self.groupe_gros_direct = self._section(gros, 'Les plus gros en ce moment', 'Mémoire vive et swap de chaque programme.',
                                                False)
        self.groupe_incidents = self._section(gros, 'Incidents de la période', None, False)
        self.groupe_gros = self._section(gros, 'Les plus gros de la période',
                                         'Le pic de mémoire de chaque programme, mesuré une fois par minute.', False)
        self.groupe_actifs_direct = self._section(actifs, 'Les plus actifs en ce moment',
                                                  'Part du processeur sur les dernières secondes.', False)
        self.groupe_actifs = self._section(actifs, 'Les plus actifs de la période',
                                           'En minutes de processeur : un cœur plein pendant une minute.', False)

    # --- période de la courbe ---
    def _periode_choisie(self, bouton, cle):
        if not bouton.get_active():
            return
        self.periode = cle
        self.courbe.choisir_periode(cle)
        self.groupe_courbe.set_title(TITRES_COURBE[cle])
        if cle != '5min':
            self.source.suivre_historique(cle)
        self.maj_historique()

    @staticmethod
    def _remplir(section, lignes, nouvelles):
        for ligne in lignes:
            section.liste.remove(ligne)
        lignes[:] = nouvelles
        for ligne in nouvelles:
            section.liste.add(ligne)
        section.set_visible(bool(nouvelles))

    def maj_historique(self):
        long = self.periode != '5min'
        for g in (self.groupe_incidents, self.groupe_gros, self.groupe_actifs):
            g.set_visible(False)
        self.resume.set_visible(long)
        self.maj_direct()
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
        incidents = []
        for ev in list(reversed(hist['incidents']))[:6]:
            icone, titre, detail = donnees.decrire_evenement(ev)
            r = ligne_info(titre, detail, icone, donnees.heure_locale(ev.get('date')))
            r.valeur.add_css_class('dim-label')
            incidents.append(r)
        self._remplir(self.groupe_incidents, self.lignes_incidents, incidents)
        n = len(hist['incidents'])
        self.groupe_incidents.set_description(
            '' if not n else f'Marqués par un point rouge sur la courbe{f" ({min(n, 6)} derniers sur {n})" if n > 6 else ""}.')
        gros = []
        for g in sorted(hist['gros'], key=lambda g: g.get('pic_mo') or 0, reverse=True)[:5]:
            titre, detail = donnees.titre_gros(g)
            gros.append(ligne_info(titre, detail, 'application-x-executable-symbolic'))
        self._remplir(self.groupe_gros, self.lignes_gros, gros)
        actifs = []
        for a in hist['actifs'][:5]:       # vide avec un cœur plus ancien que la 1.4.0 : la section reste masquée
            titre, detail = donnees.titre_actif_periode(a)
            actifs.append(ligne_info(titre, detail, 'system-run-symbolic'))
        self._remplir(self.groupe_actifs, self.lignes_actifs, actifs)

    # --- en ce moment : plus gros et plus actifs (5 minutes) ---
    def maj_direct(self):
        direct = self.periode == '5min'
        etat = self.source.etat or {}
        gros = {g.get('pid'): g for g in (etat.get('plus_gros') or [])[:5]} if direct else {}
        synchroniser(self.groupe_gros_direct.liste, self.lignes_gros_direct, list(gros), self._creer_direct,
                     lambda pid, ligne: self._maj_gros_direct(ligne, gros[pid]))
        self.groupe_gros_direct.set_visible(bool(gros))
        proc = self.source.processeur() or {}
        actifs = {a['pid']: a for a in (proc.get('plus_actifs') or [])[:5]} if direct else {}
        synchroniser(self.groupe_actifs_direct.liste, self.lignes_actifs_direct, list(actifs), self._creer_direct,
                     lambda pid, ligne: self._maj_actif_direct(ligne, actifs[pid]))
        self.groupe_actifs_direct.set_visible(bool(actifs))

    @staticmethod
    def _creer_direct(_cle):
        r = ligne_info('', '', None, '')
        r.valeur.set_visible(True)
        return r

    @staticmethod
    def _maj_info(ligne, titre, sous_titre, valeur):
        ligne.set_title(echappe(titre))
        ligne.set_subtitle(echappe(sous_titre))
        ligne.set_tooltip_text(f'{titre}\n{sous_titre}')
        ligne.valeur.set_label(valeur)

    def _maj_gros_direct(self, ligne, g):
        rss, swap = g.get('rss_mo') or 0, g.get('swap_mo') or 0
        morceaux = [f'PID {g.get("pid")}']
        if swap:
            morceaux.append(f'{donnees.formater_taille_mo(swap)} dans le swap')
        if g.get('protege'):
            morceaux.append('protégé')
        self._maj_info(ligne, g.get('nom') or 'programme', ' · '.join(morceaux), donnees.formater_taille_mo(rss + swap))

    def _maj_actif_direct(self, ligne, a):
        titre, sous, valeur = donnees.titre_actif(a)
        self._maj_info(ligne, titre, sous, valeur)

    # --- phrase d'état ---
    def maj_phrase(self):
        s = self.source
        proc = s.processeur()
        cpu = s.cpu_moyen() if s.cpu_pourcent is not None else (proc or {}).get('pourcent')
        niveau, titre, detail = donnees.phrase_etat(s.etat, s.ram_pourcent, s.ram_dispo_mo, cpu)
        self.voyant.set_niveau(niveau)
        self.titre_etat.set_label(titre)
        self.detail_etat.set_label(detail)
        self.detail_etat.set_visible(bool(detail))
        self.bouton_programmes.set_visible(bool(s.nombre_figes()) and s.service_actif())
        self.niveau_etat = niveau

    # --- instruments ---
    def maj_ram(self):
        s = self.source
        self.maj_phrase()
        if s.ram_pourcent is None:
            return
        niveau = s.niveau()
        self.pourcent.set_label(lecture(donnees.formater_pourcent(s.ram_pourcent)[:-2], ' %'))
        utilise = s.ram_total_mo - s.ram_dispo_mo
        self.detail.set_label(insecables([
            f'{donnees.formater_taille_mo(utilise)} sur {donnees.formater_taille_mo(s.ram_total_mo)}',
            f'{donnees.formater_taille_mo(s.ram_dispo_mo)} libres']))
        r = s.reglages()
        self.jauge.maj(s.ram_pourcent, r, niveau)
        self.legende.set_label(f'Vigilance à {r.get("vigilance", 85):g} %, intervention à {r.get("seuil", 95):g} %, '
                               f'urgence à {r.get("urgence", 98):g} %.')
        texte, classe = NIVEAUX_RAM[niveau]
        self.statut.set_label(texte)
        changer_classe(self.statut, classe)
        self.maj_cpu()
        if self.periode == '5min':
            self.courbe.queue_draw()

    def maj_cpu(self):
        """Instrument « Processeur » : mesure locale (% et cœurs, chaque seconde) et détails du service (modèle, types,
        température, fréquence, pression) quand `etat --json` les donne. Sans mesure du tout, la carte est masquée."""
        s = self.source
        proc = s.processeur()
        pct = s.cpu_pourcent if s.cpu_pourcent is not None else (proc or {}).get('pourcent')
        coeurs = s.cpu_coeurs or (proc or {}).get('par_coeur') or []
        self.groupe_cpu.set_visible(pct is not None)
        if pct is None:
            return
        self.cpu_pourcent.set_label(lecture(donnees.formater_pourcent(pct)[:-2], ' %'))
        detail = donnees.detail_processeur(proc, len(coeurs) or None)
        self.cpu_detail.set_label(insecables(detail.split(' · ')) if detail else '')
        types = (proc or {}).get('types') or []
        self.coeurs.maj(coeurs, types if len(types) == len(coeurs) else [], (proc or {}).get('coeurs_performants'))
        texte, classe = donnees.pastille_cpu(s.cpu_moyen() if s.cpu_pourcent is not None else pct)
        self.cpu_statut.set_label(texte)
        changer_classe(self.cpu_statut, NIVEAUX_CPU.get(classe, ''))
        morceaux = donnees.legende_processeur(proc, len(coeurs) or None)
        self.cpu_legende.set_markup(insecables(morceaux, couleur_hex(self, 'gagarine_vigilance')))
        self.cpu_legende.set_visible(bool(morceaux))
        if self.periode == '5min':
            self.courbe.queue_draw()

    def maj_etat(self):
        self.maj_phrase()
        self.maj_direct()
        self.maj_cpu()           # modèle, types des cœurs, température : donnés par `etat --json`
        etat = self.source.etat
        if not etat:
            return
        swap = etat['swap']
        synchroniser(self.groupe_swap.liste, self.lignes_swap, [s.get('nom') for s in swap], self._creer_swap,
                     lambda cle, ligne: self._maj_swap(ligne, next(s for s in swap if s.get('nom') == cle)))
        self.groupe_swap.liste.set_visible(bool(swap))
        self.groupe_swap.set_description('Aucun espace de swap : si la RAM se remplit, rien ne la décharge.' if not swap
                                         else 'Espace de débordement quand la RAM est pleine.')

    def _creer_swap(self, _cle):
        r = Adw.ActionRow(title_lines=1, subtitle_lines=1, selectable=False)
        r.add_prefix(Gtk.Image.new_from_icon_name('drive-harddisk-symbolic'))
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        r.valeur = etiquette('', ['numeric', 'caption'])
        r.barre = Gtk.LevelBar(min_value=0, max_value=1, width_request=96)
        boite.append(r.valeur)
        boite.append(r.barre)
        r.add_suffix(boite)
        return r

    def _maj_swap(self, ligne, s):
        taille, utilise = s.get('taille_mo') or 0, s.get('utilise_mo') or 0
        ligne.set_title(donnees.nom_swap(s))
        ligne.set_subtitle(echappe(f'{s.get("nom", "")} · priorité {s.get("priorite", "—")}'))
        ligne.valeur.set_label(f'{donnees.formater_taille_mo(utilise)} sur {donnees.formater_taille_mo(taille)}')
        ligne.barre.set_value(utilise / taille if taille else 0)
