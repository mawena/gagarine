"""Journal des événements : écriture (JSONL, rotation) et lecture fusionnée des journaux du poste."""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from gagarine.reglages import ETAT_SYSTEME

TAILLE_JOURNAL = 512 * 1024


class Journal:
    """Une ligne JSON par événement dans evenements.jsonl (0644), renommé en .1 au-delà de 512 Ko."""

    def __init__(self, dossier, heure=time.time, sortie=None, taille_max=TAILLE_JOURNAL):
        self.dossier = Path(dossier)
        self.fichier = self.dossier / 'evenements.jsonl'
        self.heure = heure
        self.sortie = sortie
        self.taille_max = taille_max
        self.verrou = threading.Lock()

    def ecrire(self, action, **champs):
        ev = {'date': time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(self.heure())), 'source': 'gagarine',
              'action': action, **{k: v for k, v in champs.items() if v is not None}}
        ligne = json.dumps(ev, ensure_ascii=False)
        if self.sortie is not False:
            print(ligne, file=self.sortie or sys.stdout, flush=True)
        with self.verrou:
            try:
                self.dossier.mkdir(parents=True, exist_ok=True, mode=0o755)
                if self.fichier.exists() and self.fichier.stat().st_size > self.taille_max:
                    self.fichier.replace(self.fichier.with_suffix('.jsonl.1'))
                fd = os.open(self.fichier, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    os.fchmod(fd, 0o644)
                    os.write(fd, (ligne + '\n').encode())
                finally:
                    os.close(fd)
            except OSError:
                pass
        return ev


def horodatage(date):
    """Secondes depuis 1970 d'une date de journal (ISO 8601 : « …+0000 », « …Z » ou avec fraction de seconde), ou un
    nombre déjà en secondes. None si la date est absente ou illisible."""
    if isinstance(date, bool):
        return None
    if isinstance(date, (int, float)):
        return float(date)
    if not isinstance(date, str):
        return None
    texte = re.sub(r'Z$', '+00:00', date.strip())
    texte = re.sub(r'([+-]\d\d)(\d\d)$', r'\1:\2', texte)
    try:
        d = datetime.fromisoformat(texte)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()   # sans fuseau : heure locale
    return d.timestamp()


def fichiers_journaux(env=None, home=None):
    """Journaux à fusionner : celui du service (ou GAGARINE_ETAT), celui de l'utilisateur et l'ancien de file-attente."""
    env = os.environ if env is None else env
    home = Path(home or Path.home())
    if env.get('GAGARINE_ETAT'):
        principaux = [Path(env['GAGARINE_ETAT']) / 'evenements.jsonl']
    else:
        principaux = [Path(ETAT_SYSTEME) / 'evenements.jsonl', home / '.local/state/gagarine/evenements.jsonl']
    return principaux + [home / '.local/state/file-attente/evenements.jsonl']


def lire_evenements(fichiers, n=50):
    """Les n derniers événements des fichiers, triés par date. Fichier illisible, ligne invalide ou sans date lisible :
    ignorés. Chaque événement est la ligne telle qu'écrite (dict)."""
    trouves = []
    vus = set()
    for fichier in fichiers:
        try:
            chemin = Path(fichier).resolve()
            if chemin in vus:
                continue
            vus.add(chemin)
            lignes = chemin.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        for ligne in lignes:
            try:
                e = json.loads(ligne)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            t = horodatage(e.get('date'))
            if t is not None:
                trouves.append((t, len(trouves), e))
    trouves.sort(key=lambda x: (x[0], x[1]))
    return [e for _, _, e in (trouves[-n:] if n > 0 else [])]


def ligne_lisible(e):
    """Une ligne de texte pour un événement, à l'heure locale."""
    t = horodatage(e.get('date'))
    heure = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t)) if t is not None else '?'
    morceaux = [heure, f'{e.get("action", "?"):<14}']
    for cle in ('nom', 'pid', 'raison'):
        if e.get(cle) not in (None, ''):
            morceaux.append(str(e[cle]))
    if e.get('ram') is not None:
        morceaux.append(f'(RAM {e["ram"]} %)')
    return ' '.join(morceaux)
