"""Alertes sur le téléphone par ntfy (https://ntfy.sh) : publication JSON, jamais bloquante, jamais d'exception.

Réglage `ntfy` : un sujet (servi par https://ntfy.sh) ou l'adresse complète d'un sujet sur un autre serveur
(https://ntfy.exemple.fr/gagarine). Vide : rien n'est envoyé. Utilisé par le service (programme figé ou arrêté) et par
la file d'attente (fin d'un travail).
"""
import json
import threading
import urllib.request

SERVEUR_DEFAUT = 'https://ntfy.sh'
DELAI = 5            # secondes au plus pour un envoi
PRIORITES = {'basse': 2, 'normale': 3, 'haute': 4, 'urgente': 5}


def destination(cible):
    """(adresse du serveur, sujet) d'après le réglage, ou None si le réglage est vide."""
    cible = (cible or '').strip()
    if not cible:
        return None
    if cible.startswith(('http://', 'https://')):
        serveur, _, sujet = cible.rstrip('/').rpartition('/')
        return serveur, sujet
    return SERVEUR_DEFAUT, cible


def message(cible, titre, texte, priorite='normale', etiquettes=()):
    """(adresse, corps JSON en octets) de la publication, ou None si le réglage est vide."""
    d = destination(cible)
    if d is None:
        return None
    serveur, sujet = d
    corps = {'topic': sujet, 'title': titre, 'message': texte, 'priority': PRIORITES.get(priorite, 3)}
    if etiquettes:
        corps['tags'] = list(etiquettes)
    return serveur + '/', json.dumps(corps, ensure_ascii=False).encode('utf-8')


def envoyer(cible, titre, texte, priorite='normale', etiquettes=(), ouvrir=urllib.request.urlopen, delai=DELAI):
    """Publie l'alerte ; rend True si le serveur l'a acceptée. Ne lève jamais : une alerte perdue ne doit rien casser."""
    m = message(cible, titre, texte, priorite, etiquettes)
    if m is None:
        return False
    adresse, corps = m
    try:
        requete = urllib.request.Request(adresse, data=corps, method='POST',
                                         headers={'Content-Type': 'application/json', 'User-Agent': 'gagarine'})
        with ouvrir(requete, timeout=delai) as reponse:
            return 200 <= getattr(reponse, 'status', 200) < 300
    except Exception:   # réseau, DNS, serveur : on n'insiste pas
        return False


def envoyer_en_fond(cible, titre, texte, priorite='normale', etiquettes=(), **options):
    """Même chose dans un fil à part (le service ne doit jamais attendre le réseau). Rend le fil, ou None."""
    if destination(cible) is None:
        return None
    fil = threading.Thread(target=envoyer, args=(cible, titre, texte, priorite, etiquettes), kwargs=options,
                           daemon=True, name='gagarine-ntfy')
    fil.start()
    return fil
