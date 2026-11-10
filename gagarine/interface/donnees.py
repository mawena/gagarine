"""Logique de l'interface sans GTK : lecture des JSON, mise en forme des tailles, des durées et des événements."""
import json
import os
import re
import shlex
import time
from datetime import datetime, timezone

APPLICATION_ID = 'io.github.mawena.Gagarine'


class ErreurDonnees(Exception):
    """Sortie de commande inutilisable : le message est montré tel quel à l'utilisateur."""


# --- commande gagarine -------------------------------------------------------------------------------------------------

def commande_gagarine(environ=None):
    """Liste d'arguments de la commande `gagarine` (variable GAGARINE_COMMANDE, sinon « gagarine »)."""
    env = os.environ if environ is None else environ
    texte = (env.get('GAGARINE_COMMANDE') or '').strip()
    return shlex.split(texte) if texte else ['gagarine']


# --- mise en forme -----------------------------------------------------------------------------------------------------

def _decimal(x, chiffres=1):
    return f'{x:.{chiffres}f}'.replace('.', ',')


def formater_taille_mo(mo):
    """3620 -> « 3,5 Go » ; 900 -> « 900 Mo » ; None -> « — »."""
    if mo is None:
        return '—'
    mo = float(mo)
    if mo >= 1024 * 1024:
        return f'{_decimal(mo / 1024 / 1024)} To'
    if mo >= 1024:
        return f'{_decimal(mo / 1024)} Go'
    return f'{round(mo)} Mo'


def formater_go(go):
    """4 -> « 4 Go » ; 3.6 -> « 3,6 Go »."""
    if go is None:
        return '—'
    go = float(go)
    return f'{int(go)} Go' if go == int(go) else f'{_decimal(go)} Go'


def formater_pourcent(p):
    return '—' if p is None else f'{_decimal(float(p))} %'


def formater_duree(secondes):
    """Durée lisible : « 45 s », « 2 min 5 s », « 3 h 12 min », « 2 j 4 h »."""
    if secondes is None:
        return '—'
    s = max(0, int(secondes))
    if s < 60:
        return f'{s} s'
    if s < 3600:
        m, r = divmod(s, 60)
        return f'{m} min {r} s' if r else f'{m} min'
    if s < 86400:
        h, r = divmod(s // 60, 60)
        return f'{h} h {r:02d} min' if r else f'{h} h'
    j, r = divmod(s // 3600, 24)
    return f'{j} j {r} h' if r else f'{j} j'


def depuis(horodatage, maintenant=None):
    """« depuis 2 min 5 s » à partir d'un horodatage (nombre ou ISO 8601)."""
    t = en_timestamp(horodatage)
    if t is None:
        return '—'
    maintenant = time.time() if maintenant is None else maintenant
    return 'à l’instant' if maintenant - t < 5 else f'depuis {formater_duree(maintenant - t)}'


def en_timestamp(valeur):
    """Nombre de secondes depuis 1970 à partir d'un nombre ou d'une date ISO 8601 ; None si illisible."""
    if valeur is None or valeur == '':
        return None
    if isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
        return float(valeur)
    texte = str(valeur).strip()
    if texte.endswith('Z'):
        texte = texte[:-1] + '+00:00'
    texte = re.sub(r'([+-]\d\d)(\d\d)$', r'\1:\2', texte)   # +0000 -> +00:00 (Python 3.9 et avant)
    try:
        d = datetime.fromisoformat(texte)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d.timestamp()


def heure_locale(valeur, maintenant=None):
    """« 14:30:05 » si c'est aujourd'hui, sinon « 09/11 14:30 »."""
    t = en_timestamp(valeur)
    if t is None:
        return '—'
    d = datetime.fromtimestamp(t)
    ref = datetime.fromtimestamp(time.time() if maintenant is None else maintenant)
    return d.strftime('%H:%M:%S') if d.date() == ref.date() else d.strftime('%d/%m %H:%M')


def tronquer(texte, longueur=90):
    texte = ' '.join(str(texte or '').split())
    return texte if len(texte) <= longueur else texte[:longueur - 1] + '…'


def pluriel(n, singulier, plur=None):
    return f'{n} {singulier if n < 2 else (plur or singulier + "s")}'


# --- RAM ---------------------------------------------------------------------------------------------------------------

def lire_meminfo(texte):
    """(total_mo, disponible_mo, pourcent_utilise) depuis le contenu de /proc/meminfo."""
    valeurs = {}
    for ligne in texte.splitlines():
        m = re.match(r'(\w+):\s+(\d+)', ligne)
        if m:
            valeurs[m.group(1)] = int(m.group(2))
    total, dispo = valeurs.get('MemTotal'), valeurs.get('MemAvailable')
    if not total or dispo is None:
        raise ErreurDonnees('/proc/meminfo illisible')
    return total / 1024, dispo / 1024, 100.0 * (1 - dispo / total)


def niveau_ram(pourcent, reglages, figes=0):
    """« normal », « vigilance » ou « alerte » (seuil atteint ou programme figé)."""
    if pourcent is None:
        return 'normal'
    if figes or pourcent >= reglages.get('seuil', 95):
        return 'alerte'
    if pourcent >= reglages.get('vigilance', 85):
        return 'vigilance'
    return 'normal'


# --- JSON des commandes ------------------------------------------------------------------------------------------------

def lire_json(texte, nom='la commande'):
    """Objet JSON d'une sortie de commande, ou ErreurDonnees avec un message clair."""
    if not (texte or '').strip():
        raise ErreurDonnees(f'{nom} n’a rien renvoyé')
    try:
        donnees = json.loads(texte)
    except ValueError:
        raise ErreurDonnees(f'La réponse de {nom} n’est pas lisible') from None
    if not isinstance(donnees, dict):
        raise ErreurDonnees(f'La réponse de {nom} n’a pas le format attendu')
    return donnees


def _liste(d, cle):
    v = d.get(cle)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def lire_etat(texte):
    """Normalise `gagarine etat --json` : toutes les clés existent, avec des valeurs par défaut sûres."""
    d = lire_json(texte, 'gagarine etat')
    ram = d.get('ram') if isinstance(d.get('ram'), dict) else {}
    service = d.get('service') if isinstance(d.get('service'), dict) else {}
    reglages = d.get('reglages') if isinstance(d.get('reglages'), dict) else {}
    return {
        'version': d.get('version') or '',
        'date': d.get('date'),
        'ram': {'pourcent': ram.get('pourcent'), 'total_mo': ram.get('total_mo'),
                'disponible_mo': ram.get('disponible_mo')},
        'swap': _liste(d, 'swap'),
        'reglages': reglages,
        'fichier_reglages': d.get('fichier_reglages') or '/etc/gagarine.conf',
        'service': {'etat': service.get('etat') or 'inconnu', 'actif_au_demarrage': service.get('actif_au_demarrage')},
        'dossier_etat': d.get('dossier_etat'),
        'figes': _liste(d, 'figes'),
        'plus_gros': _liste(d, 'plus_gros'),
        'file': (d.get('file') or {}).get('travaux', 0) if isinstance(d.get('file'), dict) else 0,
    }


def lire_file(texte):
    """Normalise `gagarine file --etat --json`."""
    d = lire_json(texte, 'gagarine file')
    return {'ram_disponible_go': d.get('ram_disponible_go'), 'charge': d.get('charge'), 'coeurs': d.get('coeurs'),
            'dossier': d.get('dossier'), 'travaux': _liste(d, 'travaux')}


def lire_journal(texte):
    """Liste d'événements (les plus récents en premier) depuis `gagarine journal --json`."""
    evenements = _liste(lire_json(texte, 'gagarine journal'), 'evenements')
    evenements.sort(key=lambda e: en_timestamp(e.get('date')) or 0, reverse=True)
    return evenements


def lire_reglages(texte):
    d = lire_json(texte, 'gagarine reglages')
    return {'fichier': d.get('fichier'), 'reglages': d.get('reglages') if isinstance(d.get('reglages'), dict) else {}}


def lire_historique(texte, maintenant=None):
    """Normalise `gagarine historique --json` : points (t, ram), incidents, plus gros et travaux, tous triés par date."""
    d = lire_json(texte, 'gagarine historique')
    points = []
    for p in _liste(d, 'points'):
        t, ram = en_timestamp(p.get('date')), p.get('ram')
        if t is not None and isinstance(ram, (int, float)) and not isinstance(ram, bool):
            points.append({'t': t, 'ram': float(ram), 'pression': p.get('pression'), 'swap_mo': p.get('swap_mo')})
    points.sort(key=lambda p: p['t'])
    incidents = sorted(_liste(d, 'incidents'), key=lambda e: en_timestamp(e.get('date')) or 0)
    travaux = sorted(_liste(d, 'travaux'), key=lambda t: en_timestamp(t.get('date')) or 0, reverse=True)
    depuis_s = d.get('depuis_s')
    return {'depuis_s': depuis_s if isinstance(depuis_s, (int, float)) and depuis_s > 0 else None,
            'pas_s': d.get('pas_s'), 'points': points, 'incidents': incidents, 'gros': _liste(d, 'gros'),
            'travaux': travaux}


def lire_menage(texte):
    """Normalise `gagarine menage --json` : état du ménage automatique et candidats (les plus gros d'abord)."""
    d = lire_json(texte, 'gagarine menage')
    candidats = [c for c in _liste(d, 'candidats') if c.get('id') is not None]
    candidats.sort(key=lambda c: c.get('memoire_mo') or 0, reverse=True)
    return {'auto': bool(d.get('auto')), 'delai_min': d.get('delai_min'), 'candidats': candidats}


def lire_applications(texte):
    """Normalise `gagarine applications --json` : applications en cours ou plafonnées, les plus gourmandes d'abord."""
    d = lire_json(texte, 'gagarine applications')
    applis = [a for a in _liste(d, 'applications') if a.get('id')]
    applis.sort(key=lambda a: (a.get('memoire_mo') or 0, a.get('plafond_go') or 0), reverse=True)
    return {'applications': applis}


def lire_appris(texte):
    """Normalise `gagarine file appris --json` : mémoire apprise par travail, du plus récent au plus ancien."""
    d = lire_json(texte, 'gagarine file appris')
    travaux = _liste(d, 'travaux')
    travaux.sort(key=lambda t: en_timestamp(t.get('derniere')) or 0, reverse=True)
    return {'travaux': travaux}


def swap_resume(swap):
    """(utilisé_mo, taille_mo) cumulés sur tous les espaces de swap."""
    return (sum(s.get('utilise_mo') or 0 for s in swap), sum(s.get('taille_mo') or 0 for s in swap))


def nom_swap(s):
    """« zram (compressé en RAM) » ou « Disque » + nom du périphérique."""
    if s.get('zram'):
        return 'Swap compressé en RAM (zram)'
    return 'Swap sur disque' if s.get('type') != 'file' else 'Fichier de swap'


# --- événements du journal ---------------------------------------------------------------------------------------------

# action -> (icône symbolique, catégorie de filtre, verbe)
_ACTIONS = {
    'fige': ('media-playback-pause-symbolic', 'protection', 'figé'),
    'repris': ('media-playback-start-symbolic', 'protection', 'repris'),
    'arrete': ('process-stop-symbolic', 'protection', 'arrêté'),
    'bridage-impossible': ('dialog-warning-symbolic', 'protection', 'bridage impossible'),
    'limite': ('dialog-warning-symbolic', 'file', 'limite de mémoire dépassée'),
    'travail-debut': ('system-run-symbolic', 'file', 'travail lancé'),
    'travail-fin': ('object-select-symbolic', 'file', 'travail terminé'),
    'travail-annule': ('edit-delete-symbolic', 'file', 'travail annulé'),
    'demarrage': ('emblem-system-symbolic', 'service', 'service démarré'),
    'rechargement': ('view-refresh-symbolic', 'service', 'réglages rechargés'),
    'menage': ('user-trash-symbolic', 'menage', 'ménage'),
}

CATEGORIES = [('tous', 'Tous les événements'), ('protection', 'Protection de la RAM'), ('file', 'File d’attente'),
              ('menage', 'Ménage'), ('service', 'Service')]

DECLENCHEURS = {'seuil': 'seuil atteint', 'anticipation': 'croissance trop rapide', 'pression': 'pression du noyau',
                'urgence': 'urgence'}
TYPES_MENAGE = {'gradle': 'Démon Gradle', 'kotlin': 'Démon de compilation Kotlin', 'lmstudio': 'Modèles de LM Studio',
                'ollama': 'Modèle Ollama', 'serveur-dev': 'Serveur de développement'}


def categorie_evenement(ev):
    return _ACTIONS.get(ev.get('action'), (None, 'service', None))[1]


def _memoire_evenement(octets):
    """Le cœur écrit la mémoire d'un événement en octets (RAM + swap) ; mise en forme lisible."""
    return formater_taille_mo(float(octets) / (1024 * 1024))


def decrire_evenement(ev):
    """(icône, titre, détail) en français pour un événement du journal."""
    action = ev.get('action') or 'inconnu'
    icone, _, verbe = _ACTIONS.get(action, ('dialog-information-symbolic', 'service', action))
    nom = ev.get('nom') or 'programme'
    pid = ev.get('pid')
    morceaux = []
    if action == 'fige':
        titre = f'« {nom} » figé'
        if ev.get('declencheur'):
            morceaux.append('déclencheur : ' + DECLENCHEURS.get(ev['declencheur'], str(ev['declencheur'])))
        if ev.get('memoire') is not None:
            morceaux.append(f'{_memoire_evenement(ev["memoire"])} de mémoire')
    elif action == 'repris':
        titre = f'« {nom} » repris'
    elif action == 'arrete':
        titre = f'« {nom} » arrêté'
        if ev.get('raison'):
            morceaux.append(str(ev['raison']))
        if ev.get('memoire') is not None:
            morceaux.append(f'{_memoire_evenement(ev["memoire"])} libérés')
    elif action == 'bridage-impossible':
        titre = f'« {nom} » : bridage impossible'
        if ev.get('erreur'):
            morceaux.append(str(ev['erreur']))
    elif action == 'limite':
        # le cœur écrit `declare` (RAM annoncée) et `limite` en Go, sous forme de nombres
        titre = f'« {nom} » arrêté : limite de mémoire dépassée'
        if ev.get('declare') is not None:
            morceaux.append(f'annoncé : {formater_go(ev["declare"])}')
        if ev.get('limite') is not None:
            morceaux.append(f'limite : {formater_go(ev["limite"])}')
        if ev.get('pic_go') is not None:
            morceaux.append(f'pic de mémoire : {formater_go(ev["pic_go"])}')
    elif action == 'travail-debut':
        titre = f'Travail « {nom} » lancé'
        if ev.get('ajuste'):
            initiale, courante = ev.get('annonce_initiale'), next((ev[c] for c in ('declare', 'ram') if ev.get(c) is not None), None)
            morceaux.append('annonce corrigée' + (f' : {formater_go(initiale)} → {formater_go(courante)}'
                                                  if initiale is not None and courante is not None else
                                                  f' (annoncé au départ : {formater_go(initiale)})' if initiale is not None else ''))
    elif action == 'travail-fin':
        code = ev.get('code')
        titre = f'Travail « {nom} » terminé' + ('' if code in (0, None) else f' (code {code})')
        if ev.get('duree') is not None:
            morceaux.append(f'durée : {formater_duree(ev["duree"])}')
        if ev.get('pic_go') is not None:
            morceaux.append(f'pic de mémoire : {formater_go(ev["pic_go"])}')
    elif action == 'travail-annule':
        titre = f'Travail « {nom} » annulé'
    elif action == 'menage':
        decharge = ev.get('type') in ('lmstudio', 'ollama')
        titre = f'« {nom} » {"déchargé" if decharge else "arrêté"} par le ménage'
        morceaux.append(TYPES_MENAGE.get(ev.get('type'), str(ev['type'])) if ev.get('type') else 'inactif')
        morceaux.append('ménage automatique' if ev.get('auto') else 'à votre demande')
        if ev.get('memoire_mo') is not None:
            morceaux.append(f'{formater_taille_mo(ev["memoire_mo"])} libérés')
    elif action == 'rechargement':
        titre = 'Réglages rechargés'
        if ev.get('seuil') is not None:
            morceaux.append(f'seuil {ev["seuil"]} %')
        if ev.get('urgence') is not None:
            morceaux.append(f'urgence {ev["urgence"]} %')
        if ev.get('anticipation') is not None:
            morceaux.append('anticipation ' + (f'{ev["anticipation"]} s' if ev['anticipation'] else 'désactivée'))
        if ev.get('pression') is not None:
            morceaux.append('pression ' + (f'{ev["pression"]} %' if ev['pression'] else 'désactivée'))
    elif action == 'demarrage':
        titre = 'Service Gagarine démarré'
        if ev.get('mode'):
            morceaux.append('mode administrateur' if ev['mode'] == 'root' else 'mode sans privilèges')
        if ev.get('version'):
            morceaux.append(f'version {ev["version"]}')
    else:
        titre = f'{verbe} : {nom}'
    if pid is not None and action not in ('demarrage', 'rechargement'):
        morceaux.append(f'processus {pid}')
    if ev.get('ram') is not None and action in ('fige', 'arrete', 'repris'):
        morceaux.append(f'RAM à {formater_pourcent(ev["ram"])}')
    if ev.get('declencheur') and action == 'arrete':
        morceaux.append('déclencheur : ' + DECLENCHEURS.get(ev['declencheur'], str(ev['declencheur'])))
    if ev.get('commande') and action in ('fige', 'arrete', 'repris', 'travail-debut', 'limite'):
        morceaux.append(tronquer(ev['commande'], 70))
    return icone, titre, ' · '.join(morceaux)


def filtrer_evenements(evenements, categorie='tous', recherche=''):
    """Filtre par catégorie et par texte (insensible à la casse, sur le titre, le détail et les champs)."""
    mots = recherche.lower().split()
    resultat = []
    for ev in evenements:
        if categorie != 'tous' and categorie_evenement(ev) != categorie:
            continue
        if mots:
            _, titre, detail = decrire_evenement(ev)
            botte = ' '.join([titre, detail, str(ev.get('commande') or ''), str(ev.get('utilisateur') or '')]).lower()
            if not all(m in botte for m in mots):
                continue
        resultat.append(ev)
    return resultat


# --- nouveau travail ---------------------------------------------------------------------------------------------------

def nom_fichier_sur(nom):
    """Nom utilisable dans un nom de fichier : lettres, chiffres, tirets."""
    s = re.sub(r'[^\w-]+', '-', nom.strip(), flags=re.UNICODE).strip('-_')
    return s[:40] or 'travail'


def fichier_sortie(dossier, nom, maintenant=None):
    """`<dossier>/<horodatage>-<nom>.log`."""
    d = datetime.fromtimestamp(time.time() if maintenant is None else maintenant)
    return os.path.join(dossier, f'{d:%Y%m%d-%H%M%S}-{nom_fichier_sur(nom)}.log')


def dossier_sorties(environ=None):
    env = os.environ if environ is None else environ
    base = env.get('XDG_STATE_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'state')
    return os.path.join(base, 'gagarine', 'travaux')


def arguments_travail(nom, ram_go, coeurs, commande, base=None):
    """Arguments de `gagarine file` pour un travail de l'interface : ... file NOM RAM CŒURS -- sh -c COMMANDE."""
    base = base or commande_gagarine()
    ram = f'{float(ram_go):g}'
    return base + ['file', nom, ram, str(int(coeurs)), '--', 'sh', '-c', commande]


def valider_travail(nom, ram_go, coeurs, commande):
    """Message d'erreur en français, ou None si le formulaire est valide."""
    if not nom.strip():
        return 'Donnez un nom au travail.'
    if not commande.strip():
        return 'Indiquez la commande à lancer.'
    if not 0 < ram_go <= 512:
        return 'La RAM annoncée doit être comprise entre 0 et 512 Go.'
    if coeurs < 0 or coeurs > 256:
        return 'Le nombre de cœurs n’est pas valable.'
    return None


# --- réglages ----------------------------------------------------------------------------------------------------------

# clé : (libellé, aide, unité, minimum, maximum)
REGLAGES_NOMBRES = [
    ('vigilance', 'Vigilance', 'Au-delà, la RAM est mesurée quatre fois par seconde', '%', 1, 99),
    ('seuil', 'Seuil d’intervention', 'Gagarine fige le programme qui grossit le plus vite', '%', 1, 100),
    ('urgence', 'Seuil d’urgence', 'Au-delà, le programme est arrêté sans attendre', '%', 1, 100),
    ('grace', 'Délai de grâce', 'Secondes laissées après la pause avant d’arrêter si la RAM reste haute', 's', 0, 3600),
    ('vidage_max', 'Rangement dans le swap', 'Secondes au plus pour ranger le programme figé dans le swap', 's', 0, 3600),
    ('delai_fige', 'Délai sans décision', 'Secondes de pause après lesquelles un programme figé est arrêté', 's', 1, 86400),
    ('reste_si_repris', 'Bridage après reprise', 'Mémoire qu’un programme repris peut encore demander', 'Mo', 0, 1048576),
]


# Nouveaux réglages de la 1.2.0 : (clé, libellé, aide, unité, minimum, maximum) pour les nombres.
REGLAGES_ANTICIPATION = [
    ('anticipation', 'Anticipation', 'Fige un programme qui remplirait la RAM jusqu’au seuil dans ce délai, à son rythme '
                                     'des 10 dernières secondes. 0 pour désactiver.', 's', 0, 600),
    ('pression', 'Pression mémoire du noyau', 'Agit quand le noyau peine déjà à trouver de la mémoire (pression « full » '
                                               'moyenne sur 10 s). 0 pour désactiver.', '%', 0, 100),
]
REGLAGES_NOTIFICATIONS = [
    ('notification_fin_min', 'Durée minimale d’un travail notifié', 'Les travaux plus courts finissent sans notification.',
     's', 0, 86400),
]
REGLAGES_MENAGE = [
    ('menage_delai', 'Inactif depuis', 'Délai sans activité avant le ménage automatique (5 min seulement si la RAM '
                                       'dépasse la vigilance).', 'min', 1, 1440),
]
# Interrupteurs : clé -> (titre, aide)
REGLAGES_BOOLEENS = {
    'notifications_fin': ('Notifier la fin des travaux', 'Notification du bureau quand un travail de la file se termine, '
                                                        'échoue ou est arrêté.'),
    'file_ajuster': ('Corriger les annonces trop basses', 'Gagarine retient la mémoire réellement prise par chaque '
                                                         'travail et relève l’annonce suivante si elle était trop basse.'),
    'menage_auto': ('Ménage automatique', 'Arrête ou décharge seul les démons Gradle, Kotlin et les modèles de '
                                          'LM Studio ou d’Ollama restés inactifs. Les serveurs de développement ne sont '
                                          'jamais arrêtés seuls.'),
    'claude_bloquer': ('Bloquer les travaux lourds des sessions Claude', 'Un travail lourd lancé hors de la file par une '
                                                                        'session Claude est refusé, avec la commande corrigée.'),
}
DEFAUTS_120 = {'anticipation': 15, 'pression': 20, 'notifications_fin': True, 'notification_fin_min': 30, 'ntfy': '',
               'file_ajuster': True, 'menage_auto': True, 'menage_delai': 30, 'claude_bloquer': True}


def reglages_complets(courants):
    """Réglages lus, complétés par les valeurs par défaut des clés que le service installé ne connaît pas encore."""
    complet = dict(courants)
    for cle, defaut in DEFAUTS_120.items():
        if complet.get(cle) is None:
            complet[cle] = defaut
    return complet


def commande_essai_ntfy(base, cible):
    """`gagarine ntfy essai CIBLE` : envoie un message d'essai (sans privilèges)."""
    return list(base) + ['ntfy', 'essai', cible.strip()]


def arguments_reglages(base, courants, nouveaux):
    """Arguments `reglages --ecrire CLE=VALEUR…` pour les seules clés modifiées ; None s'il n'y a rien à écrire."""
    def texte(v):
        return ('oui' if v else 'non') if isinstance(v, bool) else str(v)
    changes = [f'{k}={texte(v)}' for k, v in nouveaux.items() if texte(courants.get(k, '')) != texte(v)]
    return base + ['reglages', '--ecrire'] + changes if changes else None


def verifier_reglages(valeurs):
    """Message d'erreur si les seuils sont incohérents (vigilance < seuil <= urgence), sinon None."""
    v, s, u = valeurs.get('vigilance'), valeurs.get('seuil'), valeurs.get('urgence')
    if any(c in str(valeurs.get('ntfy') or '') for c in ' \t\n'):
        return 'Le sujet ou l’adresse ntfy ne contient ni espace ni retour à la ligne.'
    if None in (v, s, u):
        return None
    if not v < s:
        return 'La vigilance doit être inférieure au seuil d’intervention.'
    if not s <= u:
        return 'Le seuil d’intervention ne peut pas dépasser le seuil d’urgence.'
    return None


# --- indicateur --------------------------------------------------------------------------------------------------------

def fichier_autostart_indicateur(environ=None):
    env = os.environ if environ is None else environ
    base = env.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'autostart', f'{APPLICATION_ID}.Indicateur.desktop')


def indicateur_affiche(chemin):
    """True sauf si le fichier d'autostart de l'utilisateur contient `Hidden=true`."""
    try:
        with open(chemin, encoding='utf-8') as f:
            return not re.search(r'^Hidden\s*=\s*true\s*$', f.read(), re.M | re.I)
    except OSError:
        return True


def contenu_autostart_indicateur(affiche):
    """Contenu du fichier d'autostart utilisateur ; `Hidden=true` pour masquer l'indicateur."""
    lignes = ['[Desktop Entry]', 'Type=Application', 'Name=Indicateur Gagarine',
              'Comment=Affiche l’état de la RAM dans la barre du haut', 'Exec=gagarine-indicateur',
              'Icon=io.github.mawena.Gagarine', 'NoDisplay=true']
    if not affiche:
        lignes.append('Hidden=true')
    return '\n'.join(lignes) + '\n'


# --- essai -------------------------------------------------------------------------------------------------------------

def verdict_essai(sortie, code):
    """(réussi, texte) d'après la sortie de `gagarine essai` et son code de sortie."""
    if code == 0:
        return True, 'L’essai a réussi : la protection fonctionne.'
    derniere = next((l.strip() for l in reversed(sortie.splitlines()) if l.strip()), '')
    if 'impossible sans risque' in sortie:
        return False, 'L’essai est refusé : la RAM est déjà trop utilisée. Réessayez quand le poste est plus libre.'
    return False, 'L’essai a échoué' + (f' : {derniere}' if derniere else '.')


# --- actions d'administrateur ------------------------------------------------------------------------------------------

def commande_admin(environ=None):
    """Préfixe des actions d'administrateur : `pkexec /usr/bin/gagarine`.

    Si GAGARINE_COMMANDE est définie (développement, tests, captures), la commande est lancée telle quelle, sans pkexec :
    aucune fenêtre de mot de passe ne s'ouvre jamais pendant les essais.
    """
    env = os.environ if environ is None else environ
    if (env.get('GAGARINE_COMMANDE') or '').strip():
        return commande_gagarine(env)
    return ['pkexec', '/usr/bin/gagarine']


def message_erreur_commande(argv, code, erreur, delai=False, absente=False):
    """Message en français pour une commande qui échoue, ou None si tout va bien."""
    nom = ' '.join(argv[:2]) if argv and argv[0] != 'pkexec' else 'gagarine'
    if absente:
        return f'La commande « {argv[0]} » est introuvable. Installez le paquet gagarine.'
    if delai:
        return f'« {nom} » met trop de temps à répondre.'
    if code in (126, 127) and argv and argv[0] == 'pkexec':
        return 'L’autorisation d’administrateur a été refusée ou annulée.'
    if code:
        ligne = next((l.strip() for l in (erreur or '').splitlines() if l.strip()), '')
        return ligne or f'« {nom} » a échoué (code {code}).'
    return None


# --- historique : périodes de la courbe ----------------------------------------------------------------------------------

# (clé de `historique --depuis`, libellé, durée en secondes)
PERIODES = [('5min', '5 min', 300), ('24h', '24 h', 86400), ('7j', '7 jours', 7 * 86400)]


def duree_periode(cle):
    return next((s for c, _, s in PERIODES if c == cle), 300)


def libelle_periode(cle):
    return next((n for c, n, _ in PERIODES if c == cle), cle)


def graduations_axe(cle):
    """Repères de l'axe du temps : liste de (fraction de la largeur de 0 = début à 1 = maintenant, étiquette)."""
    if cle == '5min':
        return [(0.0, '5 min'), (0.6, '2 min'), (1.0, 'maintenant')]
    if cle == '24h':
        return [(0.0, '-24 h'), (0.25, '-18 h'), (0.5, '-12 h'), (0.75, '-6 h'), (1.0, 'maintenant')]
    return [(i / 7, f'-{7 - i} j') for i in range(7)] + [(1.0, 'maintenant')]


def fraction_temps(t, maintenant, duree):
    """Position d'un instant sur la courbe : 0 = il y a `duree` secondes, 1 = maintenant ; None hors de la période."""
    f = 1 - (maintenant - t) / duree
    return f if -0.001 <= f <= 1.001 else None


def resume_historique(hist, maintenant=None):
    """Phrase de résumé d'une période : pic de RAM et incidents. « Pas encore de mesures » si rien."""
    if not hist or not hist['points']:
        return 'Pas encore de mesures pour cette période.'
    pic = max(hist['points'], key=lambda p: p['ram'])
    texte = f'Pic à {formater_pourcent(pic["ram"])}, {heure_locale(pic["t"], maintenant)}'
    n = len(hist['incidents'])
    return texte + (' · aucun incident' if not n else f' · {pluriel(n, "incident")}')


def titre_gros(g):
    """« chrome » : pic de 2,8 Go, présent 340 min -> (titre, détail)."""
    minutes = g.get('minutes')
    detail = f'pic de {formater_taille_mo(g.get("pic_mo"))}'
    if minutes:
        detail += f' · parmi les plus gros pendant {formater_duree(minutes * 60)}'
    return g.get('nom') or 'programme', detail


def detail_travail_passe(t, maintenant=None):
    """(titre, détail, réussi) d'un travail de `historique --json`."""
    code = t.get('code')
    reussi = code in (0, None)
    titre = t.get('nom') or 'Travail sans nom'
    morceaux = [heure_locale(t.get('date'), maintenant)]
    if t.get('duree') is not None:
        morceaux.append(f'durée {formater_duree(t["duree"])}')
    if t.get('pic_go') is not None:
        morceaux.append(f'pic {formater_go(round(float(t["pic_go"]), 1))}')
    morceaux.append('terminé' if reussi else f'échec (code {code})')
    return titre, ' · '.join(morceaux), reussi


def detail_appris(t):
    """(titre, détail, trop_bas) d'un travail de `file appris --json`."""
    pic, annonce = t.get('pic_go'), t.get('annonce_go')
    trop_bas = pic is not None and annonce is not None and float(pic) > float(annonce) * 1.1
    morceaux = []
    if pic is not None:
        morceaux.append(f'pic habituel {formater_go(round(float(pic), 1))}')
    if annonce is not None:
        morceaux.append(f'annoncé {formater_go(annonce)}')
    if t.get('executions') is not None:
        morceaux.append(pluriel(t['executions'], 'exécution'))
    if t.get('derniere'):
        morceaux.append('dernière : ' + heure_locale(t['derniere']))
    return t.get('nom') or 'Travail sans nom', ' · '.join(morceaux), trop_bas


# --- ménage -----------------------------------------------------------------------------------------------------------

def libelle_type_menage(type_):
    return TYPES_MENAGE.get(type_, str(type_ or 'Programme inactif'))


def action_menage(c):
    """« decharger » pour un modèle (LM Studio, Ollama) ou si le cœur le dit, sinon « arreter »."""
    action = str(c.get('action') or '')
    if 'decharg' in action.lower() or (not action and c.get('type') in ('lmstudio', 'ollama')):
        return 'decharger'
    return 'arreter'


def libelle_action_menage(c):
    return 'Décharger' if action_menage(c) == 'decharger' else 'Arrêter'


def detail_menage(c):
    morceaux = [libelle_type_menage(c.get('type'))]
    if c.get('pid') is not None:
        morceaux.append(f'PID {c["pid"]}')
    morceaux.append(formater_taille_mo(c.get('memoire_mo')))
    if c.get('inactif_depuis_s') is not None:
        morceaux.append('inactif depuis ' + formater_duree(c['inactif_depuis_s']))
    morceaux.append('arrêt automatique possible' if c.get('auto') else 'proposé seulement, jamais automatique')
    return ' · '.join(morceaux)


def etat_menage_auto(m):
    """Phrase sur le ménage automatique d'après `menage --json`."""
    if not m.get('auto'):
        return 'Le ménage automatique est désactivé (Réglages).'
    delai = m.get('delai_min')
    return 'Le ménage automatique est activé' + (f' : arrêt après {formater_duree(delai * 60)} d’inactivité.' if delai else '.')


def total_menage(m):
    """(nombre de candidats, mémoire totale en Mo)."""
    c = (m or {}).get('candidats') or []
    return len(c), sum(x.get('memoire_mo') or 0 for x in c)


def arguments_menage(base, c):
    return list(base) + ['menage', 'arreter', str(c['id'])]


# --- applications du bureau ------------------------------------------------------------------------------------------

def detail_application(a):
    morceaux = []
    n = a.get('instances') or 0
    morceaux.append(pluriel(n, 'instance') + ' ouverte' + ('s' if n > 1 else '') if n else 'fermée')
    if n:
        morceaux.append(formater_taille_mo(a.get('memoire_mo')))
    return ' · '.join(morceaux)


def libelle_plafond(a):
    return 'aucun plafond' if a.get('plafond_go') in (None, 0) else f'plafond {formater_go(a["plafond_go"])}'


def arguments_plafond(base, appli_id, go):
    """`gagarine applications plafond ID GO|aucun` ; go None ou 0 retire le plafond."""
    valeur = 'aucun' if not go else f'{float(go):g}'
    return list(base) + ['applications', 'plafond', str(appli_id), valeur]
