"""Simulations et banc d'essai communs aux tests : système simulé, garde à tester."""
import json
import signal
import tempfile
import unittest
from pathlib import Path

from gagarine import journal as jn
from gagarine import reglages as rg
from gagarine import systeme as sy
from gagarine.garde import Garde

Mo = sy.Mo


class Faux:
    """Système simulé : RAM, processus, horloge et actions enregistrées."""

    def __init__(self, r=None):
        self.r = r
        self.pct = 50.0
        self.procs = {}
        self.t = 1000.0
        self.signaux = []
        self.notifs = []
        self.rangements = []
        self.bridages = []
        self.erreur_bridage = None
        self.debuts = {}
        self.commandes = {}

    def ram(self):
        return self.pct

    def processus(self):
        return dict(self.procs)

    def demarrage(self, pid):
        return self.debuts.get(pid, f'd{pid}') if pid in self.procs else None

    def commande(self, pid):
        return self.commandes.get(pid, f'/usr/bin/prog{pid}')

    def cgroup(self, pid):
        return Path('/sys/fs/cgroup/test')

    def utilisateur(self, uid):
        return f'u{uid}'

    def heure(self):
        return self.t

    def mono(self):
        return self.t

    def tuer(self, pid, sig):
        self.signaux.append((pid, sig))
        if sig == signal.SIGSTOP and pid in self.procs:
            self.procs[pid] = self.procs[pid]._replace(etat='T')
        if sig == signal.SIGKILL:
            self.procs.pop(pid, None)
        if sig == signal.SIGCONT and pid in self.procs:
            self.procs[pid] = self.procs[pid]._replace(etat='S')

    def brider(self, pid):
        self.bridages.append(pid)
        return self.erreur_bridage

    def ranger(self, pid, rss, origine):
        self.rangements.append((pid, rss))

    def notifier(self, titre, texte, pid=None, debut=None):
        self.notifs.append((titre, pid))

    def ajouter(self, pid, nom, rss_mo, etat='S', swap_mo=0, uid=1000):
        self.procs[pid] = sy.Proc(nom, etat, rss_mo * Mo, swap_mo * Mo, uid)

    def avancer(self, secondes=1.0):
        self.t += secondes


class Banc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dossier = Path(self.tmp.name)
        self.paquets = []

    def garde(self, **reglages):
        self.r = rg.Reglages(**reglages)
        self.s = Faux(self.r)
        self.j = jn.Journal(self.dossier, heure=lambda: self.s.t, sortie=False)
        self.g = Garde(self.r, self.s, self.j, self.dossier / 'figes.json', lancer=lambda f, *a: self.paquets.append((f, a)))
        return self.g

    def evenements(self):
        try:
            return [json.loads(l) for l in (self.dossier / 'evenements.jsonl').read_text().splitlines()]
        except OSError:
            return []

    def actions(self):
        return [e['action'] for e in self.evenements()]

    def tours(self, n, pas=1.0, pct=None):
        for _ in range(n):
            if pct is not None:
                self.s.pct = pct
            self.g.tour()
            self.s.avancer(pas)
