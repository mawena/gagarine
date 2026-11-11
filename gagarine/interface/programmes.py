"""Page « Programmes » (1.5.0, docs/conception-1.5.0.md) : pleine largeur, deux colonnes au-delà de 1000 sp.

- à gauche : programmes figés (Reprendre, Arrêter), fuites en cours, les plus gros ;
- à droite : les plus actifs (processeur), ménage des inactifs, plafond des applications du bureau.

Une section vide reste visible et dit en une ligne ce qui se passe. Seules les sections qu'un cœur ancien ne connaît pas
restent masquées (fuites avant la 1.3.0, ménage et applications avant la 1.2.0, processeur avant la 1.4.0).

Les aides de liste (`Liste`, `SectionListe`, `ligne_vide_phrase`, `infobulle_si_coupe`, `rangee_programme`) servent
aussi à la page « File d'attente ».
"""
import getpass
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from . import donnees
from .outils import PageLarge, Section, bouton, confirmer, echappe, etiquette, synchroniser

DESCRIPTION_FIGES = ('Gagarine fige le programme qui grossit le plus vite quand la RAM approche du seuil. Vous décidez '
                     'ensuite de le reprendre ou de l’arrêter.')
DESCRIPTION_FUITES = ('Programmes dont la mémoire monte sans cesse. Gagarine prévient mais n’agit pas : il ne fige qu’au '
                      'seuil, comme d’habitude.')
DESCRIPTION_GROS = 'Les dix programmes qui prennent le plus de mémoire (RAM et swap).'
DESCRIPTION_ACTIFS = 'Les dix programmes qui occupent le plus le processeur en ce moment (un cœur plein = 1 cœur).'
DESCRIPTION_MENAGE = 'Démons et modèles restés inactifs qui gardent de la mémoire.'
DESCRIPTION_APPLIS = ('Un plafond ralentit et range dans le swap une application qui le dépasse, sans jamais la fermer.')

VIDE_GROS = 'Liste indisponible : elle s’affichera dès que Gagarine répondra.'
VIDE_ACTIFS = 'Le poste est calme : aucun programme n’utilise le processeur de façon notable.'
VIDE_MENAGE = 'Rien à nettoyer : aucun démon ni modèle inactif ne garde de mémoire.'
VIDE_APPLIS = 'Aucune application détectée : celles que vous lancez depuis le bureau apparaîtront ici.'


# --- fonctions pures (testées sans GTK) --------------------------------------------------------------------------------

def nombre_fr(x):
    """95 -> « 95 » ; 92.5 -> « 92,5 »."""
    x = float(x)
    return str(int(x)) if x == int(x) else f'{x:.1f}'.replace('.', ',')


def _positif(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def phrase_vide_figes(reglages):
    """Phrase de la section « Programmes figés » vide, avec le seuil des réglages s'il est connu."""
    seuil = (reglages or {}).get('seuil')
    if _positif(seuil):
        return f'Aucun programme figé. Gagarine interviendra si la RAM atteint {nombre_fr(seuil)} %.'
    return 'Aucun programme figé : la RAM est sous contrôle.'


def connait_fuites(reglages):
    """Le cœur repère-t-il les fuites (1.3.0) ? Ses réglages ont alors `fuite_seuil`, même à 0."""
    return 'fuite_seuil' in (reglages or {})


def phrase_vide_fuites(reglages):
    """Phrase de la section « Fuites en cours » vide : seuil de détection, ou détection désactivée."""
    seuil = (reglages or {}).get('fuite_seuil')
    if isinstance(seuil, (int, float)) and not isinstance(seuil, bool) and seuil <= 0:
        return 'La détection des fuites est désactivée (Réglages).'
    if _positif(seuil):
        return f'Aucune fuite : aucun programme ne grossit de plus de {nombre_fr(seuil)} Mo/min depuis 5 minutes.'
    return 'Aucune fuite : aucun programme ne grossit sans cesse.'


def _commande(texte):
    return ' '.join(str(texte or '').split())


def detail_fige(f, maintenant=None):
    """Sous-titre d'un programme figé, sur une ligne : « Figé depuis 3 min 5 s · PID 4242 · alice · python3 -m … »."""
    morceaux = []
    quand = donnees.depuis(f.get('depuis'), maintenant)
    if quand != '—':
        morceaux.append('Figé ' + quand)
    morceaux.append(f'PID {f.get("pid")}')
    if f.get('utilisateur'):
        morceaux.append(str(f['utilisateur']))
    if f.get('commande'):
        morceaux.append(_commande(f['commande']))
    return ' · '.join(morceaux)


def detail_programme(p):
    """« PID 3100 · alice » (+ « protégé », « noyau ») : sous-titre d'un des plus gros ou des plus actifs."""
    morceaux = [f'PID {p.get("pid")}']
    if p.get('utilisateur'):
        morceaux.append(str(p['utilisateur']))
    if p.get('protege'):
        morceaux.append('protégé')
    if p.get('noyau'):
        morceaux.append('noyau')
    return ' · '.join(morceaux)


def valeur_gros(g):
    """(valeur, complément) d'un des plus gros : (« 2,8 Go », « + 420 Mo de swap ») ; complément vide sans swap."""
    swap = g.get('swap_mo') or 0
    return (donnees.formater_taille_mo(g.get('rss_mo') or 0),
            f'+ {donnees.formater_taille_mo(swap)} de swap' if swap else '')


def detail_candidat(c):
    """Sous-titre d'un candidat au ménage, sans sa mémoire (elle est à droite) : type, PID, inactivité, arrêt automatique."""
    morceaux = []
    type_ = donnees.libelle_type_menage(c.get('type'))
    if type_ != (c.get('nom') or ''):
        morceaux.append(type_)
    if c.get('pid') is not None:
        morceaux.append(f'PID {c["pid"]}')
    if c.get('inactif_depuis_s') is not None:
        morceaux.append('inactif depuis ' + donnees.formater_duree(c['inactif_depuis_s']))
    morceaux.append('arrêt automatique possible' if c.get('auto') else 'proposé seulement, jamais automatique')
    return ' · '.join(morceaux)


def detail_appli(a):
    """(sous-titre, valeur) d'une application du bureau : (« 2 instances ouvertes », « 2,8 Go ») ; (« fermée », « »)."""
    n = a.get('instances') or 0
    if not n:
        return 'fermée', ''
    return donnees.pluriel(n, 'instance') + (' ouvertes' if n > 1 else ' ouverte'), donnees.formater_taille_mo(a.get('memoire_mo'))


def libelle_bouton_plafond(a):
    """« Plafond 4 Go » ou « Aucun plafond »."""
    texte = donnees.libelle_plafond(a)
    return texte[0].upper() + texte[1:]


# --- aides de liste (aussi pour la File d'attente) -----------------------------------------------------------------------

def _bulle_si_coupe(libelle, _x, _y, _clavier, bulle):
    couche = libelle.get_layout()
    if couche is None or not couche.is_ellipsized():
        return False
    bulle.set_text(libelle.get_text())
    return True


def infobulle_si_coupe(libelle):
    """Affiche le texte entier d'un libellé en infobulle, seulement quand il est coupé avec « … »."""
    libelle.set_has_tooltip(True)
    libelle.connect('query-tooltip', _bulle_si_coupe)
    return libelle


def _libelles_du_titre(rangee):
    """Libellés du titre et du sous-titre d'un Adw.ActionRow (enfants de sa boîte « title »)."""
    trouves, pile = [], [rangee]
    while pile:
        w = pile.pop()
        if isinstance(w, Gtk.Label) and w.get_parent() is not None and w.get_parent().has_css_class('title'):
            trouves.append(w)
        c = w.get_first_child()
        while c is not None:
            pile.append(c)
            c = c.get_next_sibling()
    return trouves


def rangee_programme(icone=None):
    """Adw.ActionRow d'une liste : titre et sous-titre sur une ligne chacun, coupés avec « … », entiers en infobulle."""
    r = Adw.ActionRow(selectable=False, title_lines=1, subtitle_lines=1)
    for libelle in _libelles_du_titre(r):
        infobulle_si_coupe(libelle)
    if icone:
        r.icone = Gtk.Image.new_from_icon_name(icone)
        r.add_prefix(r.icone)
    return r


def ligne_vide_phrase(phrase, icone='object-select-symbolic'):
    """Ligne d'une section vide : une icône et une phrase qui dit ce qui se passe."""
    r = Adw.ActionRow(title=echappe(phrase), selectable=False)
    r.add_css_class('phrase-vide')
    r.add_prefix(Gtk.Image.new_from_icon_name(icone))
    return r


def libelle_accessible(widget, texte):
    widget.update_property([Gtk.AccessibleProperty.LABEL], [texte])


class Liste(Gtk.ListBox):
    """Liste en carte (`boxed-list`), sans sélection ; `add()` pour `outils.synchroniser`."""

    def __init__(self, **proprietes):
        super().__init__(selection_mode=Gtk.SelectionMode.NONE, **proprietes)
        self.add_css_class('boxed-list')

    def add(self, rangee):
        self.append(rangee)


class SectionListe(Section):
    """outils.Section dont le corps est une `Liste` (`liste`) ; `get_title()` et `get_description()` comme un
    Adw.PreferencesGroup."""

    def __init__(self, titre, description=None, suffixe=None, visible=True):
        super().__init__(titre, description, suffixe)
        self.set_visible(visible)
        self.liste = Liste()
        self.ajouter(self.liste)

    def get_title(self):
        return self.titre.get_label()

    def get_description(self):
        return self.description.get_label()


# --- page -----------------------------------------------------------------------------------------------------------------

class PageProgrammes(PageLarge):
    def __init__(self, fenetre):
        super().__init__('Programmes', 'programmes', 'media-playback-pause-symbolic')
        self.fenetre = fenetre
        self.source = fenetre.source
        self.lignes_figes, self.lignes_gros, self.lignes_actifs = {}, {}, {}
        self.figes = {}
        self.utilisateur = getpass.getuser()
        # même largeur pour les boutons d'une section : les valeurs restent alignées à droite
        self.largeur_menage = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self.largeur_applis = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        gauche, droite = self.colonnes(2)

        # programmes figés
        self.groupe_figes = SectionListe('Programmes figés', DESCRIPTION_FIGES)
        self.vide_figes = ligne_vide_phrase(phrase_vide_figes({}))
        self.groupe_figes.liste.add(self.vide_figes)
        gauche.append(self.groupe_figes)

        # fuites en cours (1.3.0) : section masquée si le cœur ne les repère pas ; `groupe_fuites` est la liste des
        # fuites, visible seulement quand il y en a (sinon, la phrase de `vide_fuites`)
        self.section_fuites = Section('Fuites en cours', DESCRIPTION_FUITES)
        self.section_fuites.set_visible(False)
        self.groupe_fuites = Liste(visible=False)
        self.liste_vide_fuites = Liste()
        self.vide_fuites = ligne_vide_phrase(phrase_vide_fuites({}))
        self.liste_vide_fuites.add(self.vide_fuites)
        self.section_fuites.ajouter(self.groupe_fuites)
        self.section_fuites.ajouter(self.liste_vide_fuites)
        self.lignes_fuites, self.fuites = {}, {}
        gauche.append(self.section_fuites)

        # les plus gros
        self.groupe_gros = SectionListe('Les plus gros', DESCRIPTION_GROS)
        self.vide_gros = ligne_vide_phrase(VIDE_GROS, 'dialog-information-symbolic')
        self.groupe_gros.liste.add(self.vide_gros)
        gauche.append(self.groupe_gros)

        # les plus actifs (1.4.0) : masqué avec un cœur qui ne mesure pas le processeur
        self.groupe_actifs = SectionListe('Les plus actifs', DESCRIPTION_ACTIFS, visible=False)
        self.vide_actifs = ligne_vide_phrase(VIDE_ACTIFS, 'dialog-information-symbolic')
        self.groupe_actifs.liste.add(self.vide_actifs)
        self.actifs = {}
        droite.append(self.groupe_actifs)

        # ménage : démons et modèles inactifs (1.2.0, masqué si le cœur ne le connaît pas)
        self.groupe_menage = SectionListe('Ménage', DESCRIPTION_MENAGE, visible=False)
        self.vide_menage = ligne_vide_phrase(VIDE_MENAGE)
        self.groupe_menage.liste.add(self.vide_menage)
        self.lignes_menage, self.candidats = {}, {}
        droite.append(self.groupe_menage)

        # applications du bureau : plafond de mémoire (1.2.0)
        self.groupe_applis = SectionListe('Applications du bureau', DESCRIPTION_APPLIS, visible=False)
        self.vide_applis = ligne_vide_phrase(VIDE_APPLIS, 'dialog-information-symbolic')
        self.groupe_applis.liste.add(self.vide_applis)
        self.lignes_applis, self.applis = {}, {}
        droite.append(self.groupe_applis)

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
        reglages = etat.get('reglages') or {}
        self.fuites = donnees.fuites_par_pid(etat.get('fuites'))
        connues = connait_fuites(reglages) or bool(self.fuites)
        self.section_fuites.set_visible(connues)
        self.groupe_fuites.set_visible(bool(self.fuites))
        self.liste_vide_fuites.set_visible(connues and not self.fuites)
        self.vide_fuites.set_title(echappe(phrase_vide_fuites(reglages)))
        synchroniser(self.groupe_fuites, self.lignes_fuites, list(self.fuites), self._creer_fuite, self._maj_fuite)

        self.figes = {f.get('pid'): f for f in etat['figes']}
        self.vide_figes.set_title(echappe(phrase_vide_figes(reglages)))
        self._basculer(self.groupe_figes, self.vide_figes, bool(self.figes))
        synchroniser(self.groupe_figes.liste, self.lignes_figes, list(self.figes), self._creer_fige, self._maj_fige)

        proc = etat.get('processeur')
        self.groupe_actifs.set_visible(proc is not None)
        self.actifs = {a['pid']: a for a in (proc or {}).get('plus_actifs', [])[:10]}
        self.vide_actifs.set_visible(not self.actifs)
        synchroniser(self.groupe_actifs.liste, self.lignes_actifs, list(self.actifs), self._creer_actif, self._maj_actif)

        gros = {g.get('pid'): g for g in etat['plus_gros']}
        self._basculer(self.groupe_gros, self.vide_gros, bool(gros))
        synchroniser(self.groupe_gros.liste, self.lignes_gros, list(gros), self._creer_gros,
                     lambda pid, ligne: self._maj_gros(ligne, gros[pid]))

    @staticmethod
    def _basculer(groupe, vide, a_des_lignes):
        """La ligne d'état vide est visible seulement quand la liste est vide."""
        vide.set_visible(not a_des_lignes)

    def maj_temps(self):
        maintenant = time.time()
        for pid, ligne in self.lignes_figes.items():
            if pid in self.figes:
                self._maj_fige(pid, ligne, maintenant)

    # --- programmes figés ---
    def _creer_fige(self, pid):
        r = rangee_programme('media-playback-pause-symbolic')
        r.icone.add_css_class('error')
        r.icone.set_tooltip_text('Figé')
        r.valeur = etiquette('', ['numeric'])
        r.valeur.set_tooltip_text('Mémoire du programme quand il a été figé')
        r.reprendre = bouton('Reprendre', lambda: self.reprendre(pid), ['flat'])
        r.arreter = bouton('Arrêter', lambda: self.demander_arret(pid), ['flat', 'destructive-action'])
        r.add_suffix(r.valeur)
        r.add_suffix(r.reprendre)
        r.add_suffix(r.arreter)
        return r

    def _maj_fige(self, pid, ligne, maintenant=None):
        f = self.figes[pid]
        nom = f.get('nom') or 'programme'
        ligne.set_title(echappe(nom))
        ligne.set_subtitle(echappe(detail_fige(f, maintenant)))
        ligne.valeur.set_label(donnees.formater_taille_mo(f.get('memoire_mo')))
        libelle_accessible(ligne.reprendre, f'Reprendre « {nom} »')
        libelle_accessible(ligne.arreter, f'Arrêter « {nom} »')

    # --- fuites ---
    def _creer_fuite(self, pid):
        r = rangee_programme('go-up-symbolic')
        r.icone.add_css_class('warning')
        r.pente = etiquette('', ['warning', 'numeric', 'heading'])
        r.pente.set_tooltip_text('Vitesse à laquelle sa mémoire monte')
        r.add_suffix(r.pente)
        return r

    def _maj_fuite(self, pid, ligne):
        f = self.fuites[pid]
        titre, detail = donnees.detail_fuite(f)
        ligne.set_title(echappe(titre))
        ligne.set_subtitle(echappe(detail))
        ligne.pente.set_label('+' + donnees.formater_pente(f.get('pente_mo_min')) if f.get('pente_mo_min') is not None else '')

    # --- les plus gros ---
    def _creer_gros(self, pid):
        r = rangee_programme('application-x-executable-symbolic')
        r.protege = Gtk.Image.new_from_icon_name('changes-prevent-symbolic')
        r.protege.set_tooltip_text('Protégé : Gagarine ne le figera jamais')
        r.add_prefix(r.protege)
        r.fuite = etiquette('', ['warning', 'caption-heading'])
        r.fuite.set_tooltip_text('La mémoire de ce programme monte régulièrement : fuite probable')
        r.fuite.set_visible(False)
        valeurs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        r.valeur = etiquette('', ['numeric'])
        r.swap = etiquette('', ['numeric', 'caption', 'dim-label'])
        valeurs.append(r.valeur)
        valeurs.append(r.swap)
        r.add_suffix(r.fuite)
        r.add_suffix(valeurs)
        return r

    def _maj_gros(self, ligne, g):
        ligne.set_title(echappe(g.get('nom') or 'programme'))
        ligne.set_subtitle(echappe(detail_programme(g)))
        protege = bool(g.get('protege'))
        ligne.protege.set_visible(protege)       # le cadenas remplace l'icône du programme
        ligne.icone.set_visible(not protege)
        fuite = self.fuites.get(g.get('pid'))
        ligne.fuite.set_visible(fuite is not None)
        if fuite is not None:
            ligne.fuite.set_label(donnees.badge_fuite(fuite))
        valeur, swap = valeur_gros(g)
        ligne.valeur.set_label(valeur)
        ligne.swap.set_label(swap)
        ligne.swap.set_visible(bool(swap))

    # --- les plus actifs (processeur) ---
    def _creer_actif(self, pid):
        r = rangee_programme('system-run-symbolic')
        r.valeur = etiquette('', ['numeric'])
        r.valeur.set_tooltip_text('Processeur utilisé (un cœur plein = 1 cœur) et part de tout le processeur')
        r.add_suffix(r.valeur)
        return r

    def _maj_actif(self, pid, ligne):
        a = self.actifs[pid]
        titre, _sous, valeur = donnees.titre_actif(a)
        ligne.set_title(echappe(titre))
        ligne.set_subtitle(echappe(detail_programme(a)))
        ligne.valeur.set_label(valeur)

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
        self.vide_menage.set_title(echappe(VIDE_MENAGE))
        self.vide_menage.set_subtitle('')
        self.candidats = {str(c['id']): c for c in m['candidats']}
        self.vide_menage.set_visible(not self.candidats)
        synchroniser(self.groupe_menage.liste, self.lignes_menage, list(self.candidats), self._creer_candidat,
                     self._maj_candidat)
        n, mo = donnees.total_menage(m)
        self.groupe_menage.set_description(
            (f'{donnees.pluriel(n, "programme inactif", "programmes inactifs")}, {donnees.formater_taille_mo(mo)} récupérables. '
             if n else '') + donnees.etat_menage_auto(m))

    def _creer_candidat(self, cle):
        r = rangee_programme('user-trash-symbolic')
        r.valeur = etiquette('', ['numeric'])
        r.valeur.set_tooltip_text('Mémoire gardée')
        r.action = bouton('Arrêter', lambda: self.demander_menage(cle), ['flat', 'destructive-action'])
        self.largeur_menage.add_widget(r.action)
        r.add_suffix(r.valeur)
        r.add_suffix(r.action)
        return r

    def _maj_candidat(self, cle, ligne):
        c = self.candidats[cle]
        nom = c.get('nom') or 'programme'
        ligne.set_title(echappe(nom))
        ligne.set_subtitle(echappe(detail_candidat(c)))
        ligne.valeur.set_label(donnees.formater_taille_mo(c.get('memoire_mo')))
        ligne.action.set_label(donnees.libelle_action_menage(c))
        libelle_accessible(ligne.action, f'{donnees.libelle_action_menage(c)} « {nom} »')

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
        self.vide_applis.set_title(echappe(VIDE_APPLIS))
        self.vide_applis.set_subtitle('')
        self.applis = {x['id']: x for x in a['applications']}
        self.vide_applis.set_visible(not self.applis)
        synchroniser(self.groupe_applis.liste, self.lignes_applis, list(self.applis), self._creer_appli, self._maj_appli)

    def _creer_appli(self, cle):
        r = rangee_programme('view-app-grid-symbolic')
        r.valeur = etiquette('', ['numeric'])
        r.valeur.set_tooltip_text('Mémoire de toutes ses fenêtres')
        r.plafond = bouton('', lambda: self.regler_plafond(cle), ['flat'], aide='Régler le plafond de mémoire')
        self.largeur_applis.add_widget(r.plafond)
        r.add_suffix(r.valeur)
        r.add_suffix(r.plafond)
        return r

    def _maj_appli(self, cle, ligne):
        a = self.applis[cle]
        nom = a.get('nom') or cle
        ligne.set_title(echappe(nom))
        sous, valeur = detail_appli(a)
        ligne.set_subtitle(echappe(sous))
        ligne.valeur.set_label(valeur)
        ligne.valeur.set_visible(bool(valeur))
        ligne.plafond.set_label(libelle_bouton_plafond(a))
        libelle_accessible(ligne.plafond, f'Plafond de mémoire de « {nom} » : {donnees.libelle_plafond(a)}')

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

    # --- actions sur les programmes figés ---
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
