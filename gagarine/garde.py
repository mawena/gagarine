"""Le garde : décisions prises sur des mesures (sans rien lire lui-même) et boucle de surveillance du service.

Quand la RAM atteint le seuil (ou, au-delà de `vigilance`, quand un programme la porterait au seuil dans moins de
`anticipation` secondes, ou que le noyau peine déjà : `pression`) : figer le programme qui grossit le plus vite (SIGSTOP), le brider, le ranger dans le swap,
puis l'arrêter (SIGKILL) si la RAM ne redescend pas. Le démon relit ses réglages sur SIGHUP sans perdre son état.
"""
import ctypes
import json
import os
import re
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path

from gagarine import ntfy
from gagarine.historique import Enregistreur
from gagarine.journal import Journal
from gagarine.reglages import charger_reglages, dossier_etat
from gagarine.systeme import CROISSANCE_MIN, ENCORE_EN_RAM, FENETRES, Mo, Systeme, construire_proteges, go
from gagarine.version import VERSION


def lancer_fil(f, *args):
    threading.Thread(target=f, args=args, daemon=True).start()


class Garde:
    def __init__(self, r, systeme, journal, fichier_figes, lancer=lancer_fil, charger_reglages=None, alerter=ntfy.envoyer_en_fond):
        self.r = r
        self.s = systeme
        self.journal = journal
        self.fichier_figes = Path(fichier_figes)
        self.lancer = lancer
        self.charger_reglages = charger_reglages   # () -> (Reglages, avertissements), pour SIGHUP
        self.a_recharger = False
        self.alerter = alerter   # (cible, titre, texte, priorite, etiquettes) : alerte ntfy, jamais bloquante
        self.enregistreur = Enregistreur(journal.dossier, systeme.heure)
        self.proteges = construire_proteges(r.proteges)
        self.filtre = re.compile(r.filtre) if r.filtre else None
        self.historique = {}   # pid -> deque[(instant, rss + swap)], un point par seconde
        self.figes = self.charger_figes()
        self.repos_jusqua = 0.0
        self.vidage = set()    # programmes figés que le noyau est en train de ranger dans le swap

    # --- programmes figés, conservés d'un redémarrage de Gagarine à l'autre ---
    def charger_figes(self):
        try:
            figes = {int(k): v for k, v in json.loads(self.fichier_figes.read_text()).items()}
        except (OSError, ValueError, AttributeError):
            return {}
        return {p: f for p, f in figes.items() if self.s.demarrage(p) == f['debut']}

    def sauver_figes(self):
        try:
            self.fichier_figes.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            tmp = self.fichier_figes.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.figes, ensure_ascii=False, indent=1))
            os.chmod(tmp, 0o644)
            tmp.replace(self.fichier_figes)
        except OSError:
            pass

    def evenement(self, action, pid, nom, pct=None, **champs):
        f = self.figes.get(pid, {})
        champs.setdefault('commande', f.get('commande') or self.s.commande(pid) or None)
        champs.setdefault('utilisateur', f.get('utilisateur'))
        self.journal.ecrire(action, pid=pid, nom=nom, ram=None if pct is None else round(pct, 1), **champs)

    # --- choix du coupable ---
    def croissance(self, pid, fenetre, maintenant):
        h = self.historique.get(pid)
        if not h:
            return 0
        anciens = [m for t, m in h if maintenant - t <= fenetre]
        return h[-1][1] - anciens[0] if anciens else 0

    def est_protege(self, nom):
        return bool(self.proteges.match(nom))

    def coupable(self, procs, maintenant):
        """Le programme qui grossit le plus vite (10 s, puis 60 s), à défaut le plus gros."""
        candidats = [(pid, i) for pid, i in procs.items()
                     if pid not in self.figes and i.etat not in 'TtZX' and not self.est_protege(i.nom)
                     and (not self.filtre or self.filtre.search(self.s.commande(pid)))]
        if not candidats:
            return None, 0
        for fenetre in FENETRES:
            pid, c = max(((p, self.croissance(p, fenetre, maintenant)) for p, _ in candidats), key=lambda x: x[1])
            if c >= CROISSANCE_MIN:
                return pid, c
        return max(candidats, key=lambda x: x[1].rss)[0], 0

    def anticipe(self, procs, pct, maintenant):
        """(pid, croissance) du programme qui, à son rythme des 10 dernières secondes, porterait la RAM au seuil en moins de
        `anticipation` secondes ; (None, 0) sinon. Il faut au moins 10 s de mesures : une croissance plus brève ne suffit pas."""
        r = self.r
        total = getattr(self.s, 'memoire_totale', lambda: 0)()
        if r.anticipation <= 0 or not total or pct >= r.seuil:
            return None, 0
        manque = (r.seuil - pct) / 100 * total   # octets à ajouter pour atteindre le seuil
        meilleur, vitesse_max, gain_max = None, 0, 0
        for pid, i in procs.items():
            if (pid in self.figes or i.etat in 'TtZX' or self.est_protege(i.nom)
                    or (self.filtre and not self.filtre.search(self.s.commande(pid)))):
                continue
            h = self.historique.get(pid)
            anciens = [(t, m) for t, m in h if maintenant - t <= FENETRES[0]] if h else []
            if not anciens or maintenant - anciens[0][0] < FENETRES[0] - 1:
                continue
            gain = h[-1][1] - anciens[0][1]
            vitesse = gain / max(h[-1][0] - anciens[0][0], 1e-9)
            if gain >= CROISSANCE_MIN and vitesse > vitesse_max:
                meilleur, vitesse_max, gain_max = pid, vitesse, gain
        if meilleur is not None and manque / vitesse_max < r.anticipation:
            return meilleur, gain_max
        return None, 0

    def en_cours_de_pause(self):
        """Un programme vient d'être figé et le noyau le range encore : on ne fige pas un deuxième."""
        r = self.r
        return any(self.s.heure() - f['depuis'] < (r.vidage_max if p in self.vidage else r.grace)
                   for p, f in self.figes.items())

    # --- actions ---
    def alerte(self, titre, texte, priorite='haute', etiquettes=()):
        if self.r.ntfy:
            try:
                self.alerter(self.r.ntfy, titre, texte, priorite, etiquettes)
            except Exception:   # une alerte perdue ne doit rien casser
                pass

    def figer(self, pid, info, croissance, pct, declencheur='seuil'):
        debut = self.s.demarrage(pid)
        try:
            self.s.tuer(pid, signal.SIGSTOP)
        except OSError:
            return
        cmd = self.s.commande(pid)
        user = self.s.utilisateur(info.uid)
        mem = info.rss + info.swap
        self.figes[pid] = {'debut': debut, 'nom': info.nom, 'commande': cmd, 'utilisateur': user,
                           'depuis': self.s.heure(), 'memoire': mem}
        self.sauver_figes()
        vitesse = f', +{croissance / Mo:.0f} Mo en peu de temps' if croissance else ''
        self.evenement('fige', pid, info.nom, pct, memoire=mem, declencheur=declencheur)
        self.s.notifier(f'RAM à {pct:.0f} % : « {info.nom} » figé',
                        f'{info.nom} (processus {pid}) occupait {go(mem)}{vitesse}. Il est mis en pause et rangé dans le '
                        f'swap ; il sera arrêté si la RAM ne redescend pas. Repris, il ne pourra plus grossir.', pid, debut)
        raisons = {'anticipation': 'il aurait rempli la RAM en quelques secondes', 'pression': 'le noyau manquait de mémoire'}
        self.alerte(f'RAM à {pct:.0f} % : « {info.nom} » figé',
                    f'{info.nom} ({go(mem)}) est en pause{vitesse} ; {raisons.get(declencheur, "la RAM avait atteint le seuil")}.',
                    etiquettes=('warning',))
        self.vidage.add(pid)
        self.lancer(self.isoler, pid, debut, info.rss, info.nom)

    def isoler(self, pid, debut, rss, nom='?'):
        """Bride le programme figé puis le range dans le swap (dans un fil à part : le noyau peut y mettre du temps)."""
        try:
            origine = self.s.cgroup(pid)
            erreur = self.s.brider(pid)
            if erreur:
                self.evenement('bridage-impossible', pid, nom, erreur=erreur)
            if self.s.demarrage(pid) != debut:
                return
            self.s.ranger(pid, rss, origine)
        finally:
            self.vidage.discard(pid)

    def arreter(self, pid, nom, raison, pct, declencheur=None):
        try:
            self.s.tuer(pid, signal.SIGKILL)
        except OSError:
            pass
        f = self.figes.get(pid, {})
        self.evenement('arrete', pid, nom, pct, raison=raison, memoire=f.get('memoire'), declencheur=declencheur)
        self.figes.pop(pid, None)
        self.sauver_figes()
        self.s.notifier(f'« {nom} » arrêté', f'{nom} (processus {pid}) a été arrêté : {raison}. Le poste reste utilisable.')
        self.alerte(f'« {nom} » arrêté', f'{nom} a été arrêté : {raison}.', etiquettes=('skull',))
        self.repos_jusqua = self.s.mono() + 1.5   # laisser le noyau rendre la mémoire avant de réévaluer

    # --- un tour de surveillance ---
    def tour(self):
        r = self.r
        maintenant = self.s.mono()
        pct = self.s.ram()
        procs = self.s.processus()
        for pid, p in procs.items():
            h = self.historique.setdefault(pid, deque())
            if not h or maintenant - h[-1][0] >= 1:
                h.append((maintenant, p.rss + p.swap))
                while maintenant - h[0][0] > FENETRES[-1]:
                    h.popleft()
        for pid in [p for p in self.historique if p not in procs]:
            del self.historique[pid]

        changement = False
        for pid, f in list(self.figes.items()):   # programmes repris ou terminés depuis
            if pid not in procs or self.s.demarrage(pid) != f['debut']:
                del self.figes[pid]
                changement = True
            elif procs[pid].etat in 'RS' and self.s.heure() - f['depuis'] > 2:   # « D » : la pause n'a pas encore pris
                self.evenement('repris', pid, f['nom'], pct, commande=f.get('commande'), utilisateur=f.get('utilisateur'))
                del self.figes[pid]
                changement = True
        if changement:
            self.sauver_figes()

        for pid, f in list(self.figes.items()):
            if self.s.heure() - f['depuis'] > r.delai_fige:
                duree = f'{r.delai_fige / 60:.0f} min' if r.delai_fige >= 60 else f'{r.delai_fige:.0f} s'
                self.arreter(pid, f['nom'], f'figé depuis {duree} sans décision', pct)

        self.noter_historique(pct, procs)
        if maintenant < self.repos_jusqua:
            return pct
        en_ram = sorted(((procs[p].rss, p) for p in self.figes if p in procs and procs[p].rss > ENCORE_EN_RAM),
                        reverse=True)
        if pct >= r.urgence:
            pid = en_ram[0][1] if en_ram else self.coupable(procs, maintenant)[0]
            if pid:
                self.arreter(pid, procs[pid].nom, f'la RAM a atteint {pct:.0f} %', pct, 'urgence')
        elif pct >= r.seuil:
            if self.en_cours_de_pause():
                return pct   # un programme vient d'être figé : on laisse le temps au swap de rendre la RAM
            if en_ram:
                pid = en_ram[0][1]
                self.arreter(pid, procs[pid].nom,
                             f'la RAM est restée au-dessus de {r.seuil:.0f} % malgré la pause', pct)
            else:
                pid, c = self.coupable(procs, maintenant)
                if pid:
                    self.figer(pid, procs[pid], c, pct)
        elif pct >= r.vigilance and not self.en_cours_de_pause() and not en_ram:
            pression = getattr(self.s, 'pression', lambda: 0.0)() if r.pression > 0 else 0.0
            pid, c, declencheur = None, 0, None
            if r.pression > 0 and pression >= r.pression:
                pid, c = self.coupable(procs, maintenant)
                declencheur = 'pression'
            if pid is None:
                pid, c = self.anticipe(procs, pct, maintenant)
                declencheur = 'anticipation'
            if pid:
                self.figer(pid, procs[pid], c, pct, declencheur)
        return pct

    def noter_historique(self, pct, procs):
        """Une mesure par minute dans l'historique (jamais d'erreur ni d'attente pour la boucle)."""
        mesure = getattr(self.s, 'mesure', None)
        if mesure is None or self.s.heure() < self.enregistreur.prochaine:
            return
        try:
            pression = getattr(self.s, 'pression', lambda: 0.0)()
            self.enregistreur.noter(pct, mesure(), pression, [(i.nom, i.rss + i.swap) for i in procs.values()])
        except Exception:
            pass

    # --- rechargement des réglages (SIGHUP) : l'état (programmes figés, historique) reste ---
    def appliquer_reglages(self, r):
        self.r = r
        self.s.r = r
        self.proteges = construire_proteges(r.proteges)
        self.filtre = re.compile(r.filtre) if r.filtre else None

    def demander_rechargement(self, *_):
        """Appelé par le gestionnaire de SIGHUP : ne fait que lever un drapeau, la boucle recharge au tour suivant."""
        self.a_recharger = True

    def recharger(self):
        self.a_recharger = False
        if self.charger_reglages is None:
            return
        try:
            r, avertissements = self.charger_reglages()
        except Exception as e:   # un fichier invalide ne doit pas arrêter le garde : on garde les réglages en vigueur
            print(f'avertissement : réglages non rechargés ({e!r})', file=sys.stderr, flush=True)
            return
        for a in avertissements:
            print(f'avertissement : {a}', file=sys.stderr, flush=True)
        self.appliquer_reglages(r)
        self.journal.ecrire('rechargement', pid=os.getpid(), nom='gagarine', seuil=r.seuil, urgence=r.urgence)

    def boucle(self, dormir=time.sleep):
        while True:
            if self.a_recharger:
                self.recharger()
            try:
                pct = self.tour()
            except Exception as e:   # Gagarine ne doit jamais s'arrêter sur une erreur
                print(f'erreur : {e!r}', file=sys.stderr, flush=True)
                pct = 100
            dormir(0.25 if pct >= self.r.vigilance else 1.0)


def verrouiller_en_ram():
    """Garde Gagarine en RAM : il doit répondre même quand le poste échange avec le swap."""
    try:
        ctypes.CDLL('libc.so.6', use_errno=True).mlockall(1 | 2 | 4)   # MCL_CURRENT | MCL_FUTURE | MCL_ONFAULT
    except OSError:
        pass


def surveiller(arg_config=None):
    r, avertissements = charger_reglages(arg_config)
    for a in avertissements:
        print(f'avertissement : {a}', file=sys.stderr, flush=True)
    dossier = dossier_etat()
    systeme = Systeme(r)
    garde = Garde(r, systeme, Journal(dossier), dossier / 'figes.json',
                  charger_reglages=lambda: charger_reglages(arg_config))
    signal.signal(signal.SIGHUP, garde.demander_rechargement)   # systemctl reload gagarine
    verrouiller_en_ram()
    threading.stack_size(256 * 1024)
    garde.journal.ecrire('demarrage', pid=os.getpid(), nom='gagarine', seuil=r.seuil, urgence=r.urgence,
                         mode='root' if systeme.root else 'utilisateur', figes=list(garde.figes),
                         version=VERSION)
    garde.boucle()
