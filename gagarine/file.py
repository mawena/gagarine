"""File d'attente des travaux lourds du poste (`gagarine file`) : un seul travail à la fois, dans l'ordre d'arrivée, et
seulement quand la mémoire et les processeurs suffisent ; chaque travail tourne dans sa propre limite mémoire.

Reprise en Python de l'ancien outil Node `file-attente`, avec lequel (et avec les copies `file-attente.cjs` des projets)
la file reste partagée : même dossier de tickets, même nom de ticket `<ms>-<pid>.json`, mêmes champs de base, même
héritage par FILE_ATTENTE_TICKET. Tout ce qui vient du poste (horloge, /proc, systemctl, lancement) passe par la classe
`Poste`, que les tests remplacent.
"""
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from gagarine.journal import Journal, horodatage
from gagarine.systeme import demarrage as demarrage_systeme

DOSSIER_DEFAUT = '/tmp/file-attente-travaux-lourds'
PAUSE = 5.0
ETAT_ATTENTE = 'en attente'
ETAT_COURS = 'en cours'


class ErreurSignal(Exception):
    """Levée dans le fil principal quand SIGTERM ou SIGINT arrive pendant l'attente ou l'exécution."""

    def __init__(self, numero):
        super().__init__(numero)
        self.numero = numero


def limite_go(ram):
    """Limite mémoire d'un travail, en Go : ce qu'il annonce, avec de la marge."""
    return max(2 * ram, ram + 2)


def limite_mo(ram):
    return round(limite_go(ram) * 1024)


def nombre(texte):
    """Un nombre décimal (virgule acceptée), ou None."""
    try:
        valeur = float(texte.replace(',', '.'))
    except (ValueError, AttributeError):
        return None
    return valeur if valeur == valeur and valeur not in (float('inf'), float('-inf')) else None


def joli(x):
    return int(x) if float(x).is_integer() else x


def dossier_tickets(env):
    return Path(env.get('FILE_ATTENTE_DOSSIER') or DOSSIER_DEFAUT)


def dossier_journal(env):
    """Journal de l'utilisateur ; GAGARINE_ETAT le remplace (essais), comme pour les autres commandes."""
    if env.get('GAGARINE_ETAT'):
        return Path(env['GAGARINE_ETAT'])
    return Path(env.get('HOME') or Path.home()) / '.local/state/gagarine'


# ======================================================================================================================
# Le poste
# ======================================================================================================================
class Poste:
    """Tout ce que la file demande au système. Les tests le remplacent par une simulation."""

    def maintenant(self):
        """Secondes depuis 1970."""
        return time.time()

    def mono(self):
        return time.monotonic()

    def dormir(self, secondes):
        time.sleep(secondes)

    def ram_disponible_go(self):
        """Mémoire vraiment disponible (MemAvailable, cache récupérable compris), en Go."""
        try:
            for ligne in Path('/proc/meminfo').read_text().splitlines():
                if ligne.startswith('MemAvailable:'):
                    return int(ligne.split()[1]) * 1024 / 2 ** 30
        except (OSError, ValueError, IndexError):
            pass
        return 0.0

    def charge(self):
        return os.getloadavg()[0]

    def coeurs(self):
        return os.cpu_count() or 1

    def demarrage(self, pid):
        """Champ 22 de /proc/PID/stat, en chaîne (comme l'outil Node), ou None si le processus n'existe plus."""
        return demarrage_systeme(pid)

    def pid(self):
        return os.getpid()

    def uid(self):
        return os.geteuid()

    def proprietaire(self, chemin):
        try:
            return os.stat(chemin).st_uid
        except OSError:
            return None

    def tuer(self, pid, sig):
        os.kill(pid, sig)

    def systemd_utilisateur(self):
        return self.executer(['systemctl', '--user', 'show-environment'])[0] == 0

    def executer(self, cmd):
        """(code, sortie) d'une commande courte ; 127 si elle est introuvable."""
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return 127, ''
        return res.returncode, res.stdout

    def lancer(self, argv, env):
        """Lance le travail (entrées et sorties héritées) et rend son code ; signal n → 128 + n ; 127 si introuvable.
        Un ErreurSignal levé pendant l'attente laisse le travail tourner : l'appelant l'arrête (arreter)."""
        try:
            self.enfant = subprocess.Popen(argv, env=env)
        except OSError as e:
            print(f'  impossible de lancer « {argv[0]} » : {e.strerror}', file=sys.stderr)
            return 127
        code = self.enfant.wait()
        return 128 - code if code < 0 else code

    def arreter(self, unite):
        """Arrête le travail en cours : son scope s'il en a un, sinon le programme lancé."""
        if unite:
            self.executer(['systemctl', '--user', 'stop', unite + '.scope'])
        enfant = getattr(self, 'enfant', None)
        if enfant is not None and enfant.poll() is None:
            enfant.terminate()
            try:
                enfant.wait(10)
            except subprocess.TimeoutExpired:
                enfant.kill()
                enfant.wait()

    def armer_signaux(self):
        """Fait lever ErreurSignal sur SIGTERM et SIGINT (une seule fois : le nettoyage ne doit pas être interrompu)."""
        self.anciens = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        def gestionnaire(numero, _cadre):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            raise ErreurSignal(numero)
        signal.signal(signal.SIGTERM, gestionnaire)
        signal.signal(signal.SIGINT, gestionnaire)

    def desarmer_signaux(self):
        anciens = getattr(self, 'anciens', None)
        if anciens:
            signal.signal(signal.SIGTERM, anciens[0])
            signal.signal(signal.SIGINT, anciens[1])


# ======================================================================================================================
# Tickets
# ======================================================================================================================
def iso_ms(secondes):
    """Date ISO 8601 à la milliseconde, en UTC, comme `toISOString()` de l'outil Node."""
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(secondes)) + '.%03dZ' % int(secondes * 1000 % 1000)


def lire_ticket(dossier, nom):
    """Le contenu d'un ticket, ou None s'il est illisible (en cours d'écriture) ou n'est pas un objet."""
    try:
        t = json.loads((Path(dossier) / nom).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return t if isinstance(t, dict) else None


def ecrire_ticket(dossier, nom, contenu):
    """Écriture atomique (fichier voisin puis renommage) : l'outil Node ne voit jamais un ticket à moitié écrit."""
    dossier = Path(dossier)
    tmp = dossier / f'.{nom}.{os.getpid()}.tmp'
    tmp.write_text(json.dumps(contenu, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, dossier / nom)


def retirer_ticket(dossier, nom):
    try:
        (Path(dossier) / nom).unlink()
    except OSError:
        pass


def vivant(t, poste):
    """Le travail du ticket existe-t-il encore ? Un ticket lisible sans pid valide n'est à personne : mort."""
    pid = t.get('pid')
    if not isinstance(pid, int) or isinstance(pid, bool):
        return False
    d = poste.demarrage(pid)
    return d is not None and d == t.get('demarrage')


def tickets(dossier, poste, nettoyer=True):
    """Les noms des tickets des travaux vivants, du plus ancien au plus récent ; ceux des travaux arrêtés sont jetés
    (sauf nettoyer=False : lecture seule). Un ticket illisible (en cours d'écriture) compte comme vivant."""
    dossier = Path(dossier)
    if nettoyer:
        dossier.mkdir(parents=True, exist_ok=True)
    try:
        noms = sorted(f for f in os.listdir(dossier) if f.endswith('.json'))
    except OSError:
        return []
    vivants = []
    for nom in noms:
        t = lire_ticket(dossier, nom)
        if t is None or vivant(t, poste):
            vivants.append(nom)
        elif nettoyer:
            retirer_ticket(dossier, nom)
    return vivants


def compter_travaux(env, poste=None):
    """Nombre de travaux en file, sans rien modifier (pour `gagarine etat`)."""
    try:
        return len(tickets(dossier_tickets(env), poste or Poste(), nettoyer=False))
    except Exception:
        return 0


def donnees_travaux(dossier, poste):
    """Les travaux de `--etat --json`. Un ticket d'un ancien outil : champs absents à null, source « autre »."""
    travaux = []
    for nom in tickets(dossier, poste, nettoyer=False):
        t = lire_ticket(dossier, nom)
        if t is None:
            continue
        travaux.append({
            'ticket': nom, 'position': len(travaux) + 1, 'nom': t.get('nom'), 'pid': t.get('pid'),
            'depuis': t.get('depuis'), 'etat': t.get('etat'), 'ram': t.get('ram'), 'coeurs': t.get('coeurs'),
            'commande': t.get('commande'), 'unite': t.get('unite'), 'sortie': t.get('sortie'),
            'source': 'gagarine' if t.get('source') == 'gagarine' else 'autre'})
    return travaux


# ======================================================================================================================
# Attente du tour et exécution
# ======================================================================================================================
def raison_d_attendre(ticket, nom, ram, coeurs, apres, dossier, poste):
    """(clé, texte) de ce qui retient le travail, ou (None, None) si son tour est venu. Mêmes textes que l'outil Node."""
    avant = [f for f in tickets(dossier, poste) if f < ticket]
    ram_libre, libres = poste.ram_disponible_go(), poste.coeurs() - poste.charge()
    if apres and poste.demarrage(apres):
        return 'apres', f'attend la fin du travail {apres}, lancé hors de la file'
    if avant:
        premier = lire_ticket(dossier, avant[0]) or {}
        return 'avant', f'{len(avant)} travail(aux) avant lui, dont « {premier.get("nom") or "?"} »'
    if ram_libre < ram:
        return 'ram', f'mémoire disponible {ram_libre:.1f} Go, il lui en faut {joli(ram)}'
    if libres < coeurs:
        return 'cpu', (f'processeurs occupés (charge {poste.charge():.1f} pour {poste.coeurs()} cœurs), '
                       f'il lui en faut {joli(coeurs)} libres')
    return None, None


def attendre_son_tour(nom, ram, coeurs, apres, commande, env, poste, dossier, sortie):
    """Rend 'herite' si un travail parent a déjà son tour, sinon le nom du ticket obtenu (le ticket est alors posé et
    l'appelant doit le retirer). Peut lever ErreurSignal : le ticket est alors encore à retirer par l'appelant."""
    herite = env.get('FILE_ATTENTE_TICKET')
    if herite and (dossier / herite).exists():
        return 'herite'
    dossier.mkdir(parents=True, exist_ok=True)
    pid = poste.pid()
    maintenant = poste.maintenant()
    ticket = f'{int(maintenant * 1000)}-{pid}.json'
    contenu = {'pid': pid, 'demarrage': poste.demarrage(pid), 'nom': nom, 'depuis': iso_ms(maintenant),
               'ram': joli(ram), 'coeurs': joli(coeurs), 'commande': commande[:300], 'etat': ETAT_ATTENTE,
               'unite': None, 'sortie': env.get('GAGARINE_SORTIE') or None, 'source': 'gagarine'}
    ecrire_ticket(dossier, ticket, contenu)
    dit = ''
    try:
        while True:
            cle, motif = raison_d_attendre(ticket, nom, ram, coeurs, apres, dossier, poste)
            if not cle:
                break
            if cle != dit:
                print(f'  en file d\'attente ({nom}) : {motif}', file=sortie, flush=True)
                dit = cle
            poste.dormir(PAUSE)
    except BaseException:
        retirer_ticket(dossier, ticket)
        raise
    if dit:
        print(f'  file d\'attente ({nom}) : son tour est venu ({poste.ram_disponible_go():.1f} Go libres, '
              f'charge {poste.charge():.1f})', file=sortie, flush=True)
    contenu['etat'] = ETAT_COURS
    ecrire_ticket(dossier, ticket, contenu)
    return ticket


def lancer_travail(nom, ram, coeurs, apres, commande, env, poste=None, sortie=None, erreur=None, journal=None):
    """Attend son tour, lance la commande, rend son code de sortie (128 + n si tuée par le signal n, 137 si la limite
    mémoire l'a arrêtée, 143 ou 130 si la file a reçu SIGTERM ou SIGINT)."""
    poste = poste or Poste()
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    dossier = dossier_tickets(env)
    journal = journal or Journal(dossier_journal(env), heure=poste.maintenant, sortie=False)

    def note(action, **champs):
        journal.ecrire(action, source='file', **champs)
    texte = shlex.join(commande)
    ticket = None
    unite = None
    debut = None
    poste.armer_signaux()
    try:
        ticket = attendre_son_tour(nom, ram, coeurs, apres, texte, env, poste, dossier, sortie)
        herite = ticket == 'herite'
        env = dict(env)
        env['FILE_ATTENTE_TICKET'] = env['FILE_ATTENTE_TICKET'] if herite else ticket
        argv = list(commande)
        limite = limite_go(ram)
        if not herite and poste.systemd_utilisateur():
            unite = f'gagarine-travail-{ticket[:-5]}'
            argv = ['systemd-run', '--user', '--scope', '--quiet', f'--unit={unite}', '-p',
                    f'MemoryMax={limite_mo(ram)}M', '-p', 'MemorySwapMax=512M', '--', *commande]
            env['FILE_ATTENTE_LIMITE_GO'] = str(joli(limite))
            contenu = lire_ticket(dossier, ticket) or {}
            contenu['unite'] = unite + '.scope'
            ecrire_ticket(dossier, ticket, contenu)
        elif not herite:
            print(f'  file d\'attente ({nom}) : systemd utilisateur injoignable, travail lancé sans limite mémoire',
                  file=sortie, flush=True)
        if not herite:
            note('travail-debut', nom=nom, pid=poste.pid(), ticket=ticket, ram=joli(ram), coeurs=joli(coeurs),
                           limite=joli(limite), commande=texte[:300], sortie=env.get('GAGARINE_SORTIE') or None)
        debut = poste.mono()
        code = poste.lancer(argv, env)
        duree = round(poste.mono() - debut, 1)
        if unite:
            resultat = poste.executer(['systemctl', '--user', 'show', f'{unite}.scope', '-p', 'Result', '--value'])[1].strip()
            poste.executer(['systemctl', '--user', 'reset-failed', f'{unite}.scope'])
            if resultat == 'oom-kill':
                print(f'\n  file d\'attente ({nom}) : travail ARRÊTÉ par sa limite mémoire : il a dépassé '
                      f'{joli(limite)} Go alors qu\'il en annonçait {joli(ram)}.'
                      f'\n  Le poste n\'a rien eu. Si ce travail a vraiment besoin de plus, relance-le en annonçant la '
                      f'RAM qu\'il lui faut ;\n  sinon, il a une fuite de mémoire à corriger avant de le relancer.',
                      file=erreur, flush=True)
                note('limite', nom=nom, declare=joli(ram), limite=joli(limite), commande=texte[:300])
                code = 137
        if not herite:
            note('travail-fin', nom=nom, pid=poste.pid(), ticket=ticket, code=code, duree=duree)
        return code
    except ErreurSignal as e:
        if debut is not None:
            poste.arreter(unite)
            if unite:
                poste.executer(['systemctl', '--user', 'reset-failed', f'{unite}.scope'])
        note('travail-annule', nom=nom, pid=poste.pid(), ticket=None if ticket in (None, 'herite') else ticket,
                       signal=e.numero, en_cours=debut is not None)
        print(f'  file d\'attente ({nom}) : travail {"arrêté" if debut is not None else "annulé"} '
              f'(signal {e.numero})', file=erreur, flush=True)
        return 128 + e.numero
    finally:
        if ticket and ticket != 'herite':
            retirer_ticket(dossier, ticket)
        poste.desarmer_signaux()


# ======================================================================================================================
# État et annulation
# ======================================================================================================================
def donnees_etat_file(env, poste=None):
    """L'objet de `gagarine file --etat --json` (contrat : docs/conception-1.1.0.md, section 3)."""
    poste = poste or Poste()
    dossier = dossier_tickets(env)
    return {'ram_disponible_go': round(poste.ram_disponible_go(), 1), 'charge': round(poste.charge(), 1),
            'coeurs': poste.coeurs(), 'dossier': str(dossier), 'travaux': donnees_travaux(dossier, poste)}


def afficher_etat(env, en_json, poste=None, sortie=None):
    poste = poste or Poste()
    sortie = sortie or sys.stdout
    d = donnees_etat_file(env, poste)
    if en_json:
        print(json.dumps(d, ensure_ascii=False), file=sortie)
        return 0
    print(f'Mémoire disponible : {d["ram_disponible_go"]:.1f} Go ; charge {d["charge"]:.1f} pour {d["coeurs"]} cœurs',
          file=sortie)
    if not d['travaux']:
        print('Aucun travail en file.', file=sortie)
    for k, t in enumerate(d['travaux']):
        secondes = horodatage(t['depuis'])
        heure = time.strftime('%H:%M:%S', time.localtime(secondes)) if secondes is not None else '?'
        print(f'{"en attente" if k else "en tête   "} : {t["nom"]} (processus {t["pid"]}, depuis {heure})', file=sortie)
    return 0


def annuler(cible, env, root, poste=None, sortie=None, erreur=None):
    """Annule un travail de la file (PID ou nom de ticket) : SIGTERM à la commande `gagarine file`, qui retire son
    ticket, arrête le scope et note l'annulation. Seulement les travaux de l'utilisateur ; root : tous."""
    poste = poste or Poste()
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    dossier = dossier_tickets(env)
    trouve = None
    for nom in tickets(dossier, poste, nettoyer=False):
        t = lire_ticket(dossier, nom)
        if t is None:
            continue
        if cible in (nom, nom[:-5]) or (cible.isdigit() and str(t.get('pid')) == cible):
            trouve = (nom, t)
            break
    if not trouve:
        print(f'« {cible} » : aucun travail de ce nom ou de ce processus dans la file (voir « gagarine file --etat »)',
              file=erreur)
        return 1
    nom, t = trouve
    if not root and poste.proprietaire(dossier / nom) != poste.uid():
        print(f'« {t.get("nom")} » (processus {t.get("pid")}) appartient à un autre utilisateur : seul lui ou '
              f'l\'administrateur peut l\'annuler', file=erreur)
        return 1
    if t.get('pid') == poste.pid():
        print('un travail ne peut pas s\'annuler lui-même', file=erreur)
        return 1
    try:
        poste.tuer(t['pid'], signal.SIGTERM)
    except OSError as e:
        print(f'processus {t.get("pid")} : {e.strerror}', file=erreur)
        return 1
    # Un travail de Gagarine nettoie lui-même ; le ticket d'un ancien outil, qui ne le fait pas, est retiré ici
    for _ in range(50):
        if not vivant(t, poste):
            break
        poste.dormir(0.1)
    if not vivant(t, poste):
        retirer_ticket(dossier, nom)
    print(f'travail « {t.get("nom")} » (processus {t["pid"]}) annulé', file=sortie)
    return 0
