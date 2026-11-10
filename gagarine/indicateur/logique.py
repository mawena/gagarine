"""Logique de l'indicateur, sans GTK : niveaux, libellés, contenu du menu, surcharge d'autostart.

Ce module n'importe que la bibliothèque standard (et pas le cœur de Gagarine) : il se teste sans affichage.
"""
import json
import os
import re
import shlex

IDENTIFIANT_AUTOSTART = 'io.github.mawena.Gagarine.Indicateur.desktop'
VIGILANCE_DEFAUT = 85
SEUIL_DEFAUT = 95

NORMAL, VIGILANCE, ALERTE = 'normal', 'vigilance', 'alerte'
ICONES = {
    NORMAL: 'gagarine-indicateur-normal-symbolic',
    VIGILANCE: 'gagarine-indicateur-vigilance-symbolic',
    ALERTE: 'gagarine-indicateur-alerte-symbolic',
}


# --- Lecture des données -----------------------------------------------------------------------------------------------

def ram_depuis_meminfo(texte):
    """Pourcentage de RAM utilisée : 100 − MemAvailable / MemTotal. None si le texte est inutilisable."""
    m = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+)', texte or '', re.M))
    try:
        return 100 * (1 - int(m['MemAvailable']) / int(m['MemTotal']))
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def lire_json(octets):
    """Objet JSON (dictionnaire) lu dans la sortie d'une commande ; None si vide, invalide ou pas un objet."""
    try:
        if isinstance(octets, (bytes, bytearray)):
            octets = octets.decode('utf-8', 'replace')
        donnees = json.loads(octets)
    except (ValueError, TypeError):
        return None
    return donnees if isinstance(donnees, dict) else None


def commande_gagarine(env=None):
    """Commande `gagarine` sous forme de liste : variable GAGARINE_COMMANDE (peut contenir des arguments), sinon `gagarine`."""
    env = os.environ if env is None else env
    try:
        liste = shlex.split(env.get('GAGARINE_COMMANDE') or '')
    except ValueError:
        liste = []
    return liste or ['gagarine']


def _nombre(valeur, defaut):
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return defaut


def seuils(etat):
    """(vigilance, seuil) tirés des réglages de `etat --json`, avec des valeurs par défaut."""
    reglages = (etat or {}).get('reglages') if isinstance((etat or {}).get('reglages'), dict) else {}
    return (_nombre(reglages.get('vigilance'), VIGILANCE_DEFAUT), _nombre(reglages.get('seuil'), SEUIL_DEFAUT))


def figes(etat):
    """Liste des programmes figés (dictionnaires valides) de `etat --json`."""
    liste = (etat or {}).get('figes')
    if not isinstance(liste, list):
        return []
    return [p for p in liste if isinstance(p, dict) and isinstance(p.get('pid'), int)]


def travaux_en_file(file_etat):
    """Nombre de travaux en file d'après `file --etat --json` (None si inconnu)."""
    travaux = (file_etat or {}).get('travaux')
    return len(travaux) if isinstance(travaux, list) else None


# --- Niveau, icône, libellés -------------------------------------------------------------------------------------------

def niveau(pourcent, etat=None):
    """alerte : RAM ≥ seuil ou programme figé ; vigilance : RAM ≥ vigilance ; sinon normal."""
    vigilance, seuil = seuils(etat)
    if figes(etat) or (pourcent is not None and pourcent >= seuil):
        return ALERTE
    if pourcent is not None and pourcent >= vigilance:
        return VIGILANCE
    return NORMAL


def nom_icone(niv):
    return ICONES.get(niv, ICONES[NORMAL])


def etiquette(pourcent):
    """« 75 % » (espace insécable avant le signe, comme en français) ; « -- % » si la RAM est inconnue."""
    if pourcent is None:
        return '-- %'
    return '%d %%' % round(pourcent)


def taille_mo(mo):
    """« 3,6 Go » ou « 512 Mo »."""
    mo = _nombre(mo, 0)
    if mo >= 1024:
        return ('%.1f Go' % (mo / 1024)).replace('.', ',')
    return '%d Mo' % round(mo)


def ligne_ram(pourcent, etat=None):
    ram = (etat or {}).get('ram') if isinstance((etat or {}).get('ram'), dict) else {}
    texte = 'RAM : %s' % etiquette(pourcent)
    total, dispo = _nombre(ram.get('total_mo'), 0), _nombre(ram.get('disponible_mo'), None)
    if total > 0 and dispo is not None:
        texte += ' (%s utilisés sur %s)' % (taille_mo(max(total - dispo, 0)), taille_mo(total))
    return texte


def ligne_swap(etat):
    """Ligne d'information du swap, ou None si l'état est inconnu."""
    swap = (etat or {}).get('swap')
    if not isinstance(swap, list):
        return None
    total = sum(_nombre(s.get('taille_mo'), 0) for s in swap if isinstance(s, dict))
    if total <= 0:
        return 'Swap : aucun'
    utilise = sum(_nombre(s.get('utilise_mo'), 0) for s in swap if isinstance(s, dict))
    return 'Swap : %s utilisés sur %s' % (taille_mo(utilise), taille_mo(total))


def titre_fige(programme):
    return '%s (PID %d), %s' % (programme.get('nom') or '?', programme['pid'], taille_mo(programme.get('memoire_mo')))


def ligne_file(nombre):
    if nombre is None:
        return 'File d\'attente : inconnue'
    if nombre == 0:
        return 'File d\'attente : aucun travail'
    return 'File d\'attente : %d %s' % (nombre, 'travaux' if nombre > 1 else 'travail')


def tooltip(niv, nb_figes):
    if nb_figes:
        return 'Gagarine : %d programme%s figé%s' % (nb_figes, 's' if nb_figes > 1 else '', 's' if nb_figes > 1 else '')
    return {NORMAL: 'Gagarine : RAM normale', VIGILANCE: 'Gagarine : RAM en vigilance',
            ALERTE: 'Gagarine : RAM au seuil'}[niv]


# --- Contenu du menu ---------------------------------------------------------------------------------------------------
# Le menu est décrit par une liste de tuples comparables : on ne reconstruit les widgets que si cette liste change.
#   ('info', texte)                       ligne grisée
#   ('sep',)                              séparateur
#   ('fige', pid, titre, direct)          sous-menu d'un programme figé (direct : l'utilisateur peut agir sans pkexec)
#   ('action', identifiant, texte)        ligne cliquable

def contenu_menu(pourcent, etat, file_etat, utilisateur=None):
    elements = [('info', ligne_ram(pourcent, etat))]
    swap = ligne_swap(etat)
    if swap:
        elements.append(('info', swap))
    elements.append(('sep',))
    liste = figes(etat)
    if liste:
        elements.append(('info', 'Programmes figés :'))
        for p in liste:
            direct = utilisateur is not None and p.get('utilisateur') == utilisateur
            elements.append(('fige', p['pid'], titre_fige(p), direct))
    else:
        elements.append(('info', 'Aucun programme figé'))
    elements.append(('info', ligne_file(travaux_en_file(file_etat))))
    elements += [('sep',), ('action', 'ouvrir', 'Ouvrir Gagarine'), ('action', 'masquer', 'Masquer l\'indicateur')]
    return elements


def commande_action(action, pid, direct, base=None):
    """Commande pour reprendre ou arrêter un programme : directe s'il est à l'utilisateur, sinon par pkexec."""
    if action not in ('reprendre', 'arreter'):
        raise ValueError('action inconnue : %s' % action)
    if direct:
        return list(base or ['gagarine']) + [action, str(int(pid))]
    return ['pkexec', '/usr/bin/gagarine', action, str(int(pid))]


# --- Surcharge d'autostart ---------------------------------------------------------------------------------------------

def chemin_surcharge(env=None):
    env = os.environ if env is None else env
    config = env.get('XDG_CONFIG_HOME') or os.path.join(env.get('HOME') or os.path.expanduser('~'), '.config')
    return os.path.join(config, 'autostart', IDENTIFIANT_AUTOSTART)


def masque(env=None):
    """Vrai si la surcharge de l'utilisateur contient Hidden=true."""
    try:
        with open(chemin_surcharge(env), encoding='utf-8') as f:
            return any(re.fullmatch(r'Hidden\s*=\s*true', ligne.strip(), re.I) for ligne in f)
    except OSError:
        return False


def masquer(env=None):
    """Écrit la surcharge avec Hidden=true (écriture atomique)."""
    chemin = chemin_surcharge(env)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    temporaire = chemin + '.tmp'
    with open(temporaire, 'w', encoding='utf-8') as f:
        f.write('[Desktop Entry]\nType=Application\nName=Indicateur Gagarine\nExec=gagarine-indicateur\nHidden=true\n')
    os.replace(temporaire, chemin)


def afficher(env=None):
    """Enlève la surcharge (l'autostart du système reprend la main)."""
    try:
        os.remove(chemin_surcharge(env))
    except FileNotFoundError:
        pass
