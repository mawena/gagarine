"""File d'attente des travaux lourds du poste (`gagarine file`) : `file_paralleles` travaux à la fois au plus (un seul avec
1), par priorité puis par ancienneté, et seulement quand la mémoire et les processeurs suffisent ; chaque travail tourne
dans sa propre limite mémoire.

Reprise en Python de l'ancien outil Node `file-attente`, avec lequel (et avec les copies `file-attente.cjs` des projets)
la file reste partagée : même dossier de tickets, même nom de ticket `<ms>-<pid>.json`, mêmes champs de base, même
héritage par FILE_ATTENTE_TICKET. Tout ce qui vient du poste (horloge, /proc, systemctl, lancement) passe par la classe
`Poste`, que les tests remplacent.
"""
import json
import math
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from gagarine import ntfy
from gagarine.journal import Journal, horodatage
from gagarine.reglages import charger_reglages
from gagarine.systeme import demarrage as demarrage_systeme

DOSSIER_DEFAUT = '/tmp/file-attente-travaux-lourds'
PAUSE = 5.0
ETAT_ATTENTE = 'en attente'
ETAT_COURS = 'en cours'
INTERVALLE_PIC = 0.5         # s entre deux relevés du pic mémoire (la conception dit « toutes les 2 s » au plus)
PREMIER_RELEVE = 0.2         # s : premier relevé, pour ne pas manquer un travail très court
PICS_GARDES = 5
PLAFOND_ANNONCE = 0.6        # part de la RAM totale qu'une annonce corrigée ne dépasse jamais
ICONE = 'io.github.mawena.Gagarine'
NIVEAUX = ('urgent', 'normal', 'fond')   # du plus pressé au moins pressé
PRIORITE_DEFAUT = 'normal'
MARGE_GO = 1.0               # Go laissés libres en plus de ce que les travaux en cours peuvent encore prendre
RESERVE_ANCIEN_GO = 2.0      # Go réservés par travail en cours d'un ancien outil (sa consommation est inconnue)
COEURS_RECENTS_S = 60        # un travail lancé depuis moins de ça n'apparaît pas encore dans la charge
FAMINE_S = 600               # un travail qui attend depuis plus de ça n'est plus doublé


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

    def ram_totale_go(self):
        """RAM totale (MemTotal), en Go ; 0 si illisible."""
        try:
            for ligne in Path('/proc/meminfo').read_text().splitlines():
                if ligne.startswith('MemTotal:'):
                    return int(ligne.split()[1]) * 1024 / 2 ** 30
        except (OSError, ValueError, IndexError):
            pass
        return 0.0

    racine_cgroup = Path('/sys/fs/cgroup')

    def cgroup_scope(self, scope):
        """Dossier cgroup du scope (ControlGroup de systemd), ou None s'il n'existe pas (encore)."""
        code, sortie = self.executer(['systemctl', '--user', 'show', scope, '-p', 'ControlGroup', '--value'])
        chemin = sortie.strip()
        if code != 0 or not chemin.startswith('/'):
            return None
        return self.racine_cgroup / chemin.lstrip('/')

    def memoire_cgroup(self, chemin):
        """Mémoire actuelle du cgroup, RAM + swap, en Go ; None si memory.current est illisible."""
        try:
            octets = int((Path(chemin) / 'memory.current').read_text())
        except (OSError, ValueError):
            return None
        try:
            octets += int((Path(chemin) / 'memory.swap.current').read_text())
        except (OSError, ValueError):
            pass
        return octets / 2 ** 30

    def pic_cgroup(self, chemin):
        """Plus haut atteint par le cgroup, RAM + swap, en Go ; None si memory.peak est illisible."""
        try:
            octets = int((Path(chemin) / 'memory.peak').read_text())
        except (OSError, ValueError):
            return None
        try:
            octets += int((Path(chemin) / 'memory.swap.peak').read_text())
        except (OSError, ValueError):
            pass
        return octets / 2 ** 30

    def notifier(self, titre, texte):
        """Notification de bureau sur la session de l'utilisateur, sans attendre ni échouer."""
        env = dict(os.environ)
        bus = f'/run/user/{os.geteuid()}/bus'
        if not env.get('DBUS_SESSION_BUS_ADDRESS') and os.path.exists(bus):
            env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path={bus}'
        try:
            subprocess.Popen(['notify-send', '--app-name=Gagarine', '-i', ICONE, '-h', f'string:desktop-entry:{ICONE}',
                              titre, texte], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass

    def alerter_ntfy(self, cible, titre, texte, priorite, etiquettes):
        """Alerte ntfy ; attend l'envoi au plus quelques secondes (le fil est un démon : il mourrait avec la file)."""
        fil = ntfy.envoyer_en_fond(cible, titre, texte, priorite, etiquettes)
        if fil is not None:
            fil.join(ntfy.DELAI + 1)

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

    def lancer(self, argv, env, pendant=None):
        """Lance le travail (entrées et sorties héritées) et rend son code ; signal n → 128 + n ; 127 si introuvable.
        `pendant`, s'il est donné, est appelé toutes les INTERVALLE_PIC s tant que le travail tourne.
        Un ErreurSignal levé pendant l'attente laisse le travail tourner : l'appelant l'arrête (arreter)."""
        try:
            self.enfant = subprocess.Popen(argv, env=env)
        except OSError as e:
            print(f'  impossible de lancer « {argv[0]} » : {e.strerror}', file=sys.stderr)
            return 127
        delai = PREMIER_RELEVE
        while True:
            try:
                code = self.enfant.wait(delai if pendant else None)
                break
            except subprocess.TimeoutExpired:
                pendant()
                delai = INTERVALLE_PIC
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


def donnees_travaux(dossier, poste, paralleles=1, evaluation=None):
    """Les travaux de `--etat --json`. Un ticket d'un ancien outil : champs absents à null, source « autre »."""
    ev = evaluation or evaluer(dossier, poste, paralleles, nettoyer=False)
    travaux = []
    for k, info in enumerate(ev['infos']):
        t = lire_ticket(dossier, info['nom'])
        if t is None:
            continue
        gagarine = t.get('source') == 'gagarine'
        travaux.append({
            'ticket': info['nom'], 'position': len(travaux) + 1, 'nom': t.get('nom'), 'pid': t.get('pid'),
            'depuis': t.get('depuis'), 'etat': t.get('etat'), 'ram': t.get('ram'), 'coeurs': t.get('coeurs'),
            'commande': t.get('commande'), 'unite': t.get('unite'), 'sortie': t.get('sortie'),
            'source': 'gagarine' if gagarine else 'autre',
            'priorite': priorite_de(t) if gagarine else None,
            'besoin_go': nombre_ticket(t, 'ram') if gagarine else None,
            'motif': motif_de(info, k, ev, poste, paralleles)})
    return travaux


# ======================================================================================================================
# Mémoire apprise des travaux
# ======================================================================================================================
class Releveur:
    """Relève le pic mémoire (RAM + swap) du scope d'un travail ; ne lève jamais."""

    def __init__(self, poste, unite):
        self.poste, self.scope, self.chemin, self.pic = poste, unite + '.scope', None, None

    def relever(self):
        try:
            if self.chemin is None:
                self.chemin = self.poste.cgroup_scope(self.scope)
            if self.chemin is None:
                return
            v = self.poste.pic_cgroup(self.chemin)
            if v is not None and (self.pic is None or v > self.pic):
                self.pic = v
        except Exception:
            pass


def fichier_travaux(env):
    return dossier_journal(env) / 'travaux.json'


def lire_travaux(env):
    """La mémoire des travaux ({nom: {...}}) ; fichier absent, corrompu ou mal formé : vide (les entrées valides sont
    gardées)."""
    try:
        brut = json.loads(fichier_travaux(env).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(brut, dict):
        return {}
    travaux = {}
    for nom, t in brut.items():
        if not isinstance(t, dict) or not isinstance(t.get('pics_go'), list):
            continue
        pics = [p for p in t['pics_go'] if isinstance(p, (int, float)) and not isinstance(p, bool) and p == p]
        if pics:
            limites = t.get('limites')
            limites = [bool(x) for x in limites][-PICS_GARDES:] if isinstance(limites, list) else []
            travaux[nom] = {**t, 'pics_go': pics[-PICS_GARDES:], 'limites': limites}
    return travaux


def tendance(pics, limites=()):
    """'hausse' si les 3 derniers pics (au moins) montent à chaque fois et que le dernier vaut au moins 1,3 × le plus
    ancien d'entre eux ; 'limite' si les 2 derniers lancements ont été arrêtés par leur limite ; sinon 'stable'."""
    k = 1
    while k < len(pics) and pics[-k - 1] < pics[-k]:
        k += 1
    if k >= 3 and pics[-1] >= 1.3 * pics[-k]:
        return 'hausse'
    limites = list(limites)
    if len(limites) >= 2 and limites[-1] and limites[-2]:
        return 'limite'
    return 'stable'


def noter_pic(env, nom, pic, annonce, instant, limite_depassee=False):
    """Ajoute le pic de cette exécution à la mémoire des travaux (5 derniers gardés) et met à jour la tendance,
    écriture atomique. Ne lève jamais."""
    try:
        travaux = lire_travaux(env)
        ancien = travaux.get(nom, {})
        pics = (ancien.get('pics_go', []) + [round(pic, 2)])[-PICS_GARDES:]
        # Les lancements d'avant la 1.3.0 n'ont pas noté leur arrêt par la limite : on les compte pour « non »
        precedents = ancien.get('limites', [])
        precedents = [False] * (len(ancien.get('pics_go', [])) - len(precedents)) + precedents
        limites = (precedents + [bool(limite_depassee)])[-PICS_GARDES:]
        travaux[nom] = {'executions': int(ancien.get('executions') or 0) + 1, 'pics_go': pics, 'limites': limites,
                        'tendance': tendance(pics, limites),
                        'annonce_go': joli(annonce), 'derniere': time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(instant))}
        chemin = fichier_travaux(env)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        tmp = chemin.with_name(f'.{chemin.name}.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(travaux, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, chemin)
    except Exception:
        pass


def fr(x):
    """Un nombre de Go en français : 6,3 ; 0,5 ; 8."""
    return ('%g' % round(x, 1)).replace('.', ',')


def corriger_annonce(nom, ram, env, reglages, poste, sortie):
    """(annonce à utiliser, corrigée ?). Si le plus haut des derniers pics dépasse l'annonce de plus de 10 %, dit
    combien le travail a pris ; avec `file_ajuster`, l'annonce devient min(1,2 × pic arrondi au 0,5 Go supérieur, 60 %
    de la RAM totale). Ne lève jamais."""
    try:
        t = lire_travaux(env).get(nom)
        if not t:
            return ram, False
        pic = max(t['pics_go'])
        if round(pic, 6) <= round(ram * 1.1, 6):
            return ram, False
        message = f'  file d\'attente ({nom}) : annoncé {fr(ram)} Go, il en a pris {fr(pic)} Go la dernière fois'
        if not reglages.file_ajuster:
            print(message + ' (correction des annonces désactivée : file_ajuster = non)', file=sortie, flush=True)
            return ram, False
        voulu = math.ceil(round(1.2 * pic * 2, 6)) / 2
        plafond = PLAFOND_ANNONCE * poste.ram_totale_go()
        nouveau = min(voulu, plafond) if plafond > 0 else voulu
        if nouveau <= ram:
            print(message, file=sortie, flush=True)
            return ram, False
        suite = f' ; annonce corrigée à {fr(nouveau)} Go'
        if nouveau < voulu:
            suite += (f' (plafond : 60 % de la RAM) : ce travail a sans doute une fuite de mémoire à corriger '
                      f'plutôt qu\'une annonce à gonfler')
        print(message + suite, file=sortie, flush=True)
        return nouveau, True
    except Exception:
        return ram, False


def donnees_appris(env):
    """L'objet de `gagarine file appris --json`, du plus récent au plus ancien."""
    travaux = [{'nom': nom, 'executions': t.get('executions'), 'pic_go': max(t['pics_go']),
                'annonce_go': t.get('annonce_go'), 'derniere': t.get('derniere'),
                'tendance': tendance(t['pics_go'], t['limites'])}
               for nom, t in lire_travaux(env).items()]
    travaux.sort(key=lambda t: horodatage(t['derniere']) or 0, reverse=True)
    return {'travaux': travaux}


def afficher_appris(env, en_json, sortie=None):
    sortie = sortie or sys.stdout
    d = donnees_appris(env)
    if en_json:
        print(json.dumps(d, ensure_ascii=False), file=sortie)
        return 0
    if not d['travaux']:
        print('Aucun travail appris pour l\'instant (la file retient le pic mémoire de chaque travail).', file=sortie)
    for t in d['travaux']:
        annonce = f', annoncé {fr(t["annonce_go"])} Go' if isinstance(t['annonce_go'], (int, float)) else ''
        fuite = {'hausse': ', pic en hausse : fuite probable',
                 'limite': ', arrêté par sa limite : fuite probable'}.get(t['tendance'], '')
        print(f'{t["nom"]} : pic {fr(t["pic_go"])} Go{annonce} ({t["executions"]} exécution(s){fuite})', file=sortie)
    return 0


def duree_texte(secondes):
    minutes = round(secondes / 60)
    if secondes < 60:
        return f'{round(secondes)} s'
    if minutes < 60:
        return f'{minutes} min'
    return f'{minutes // 60} h {minutes % 60:02d} min'


def notifier_fin(nom, code, duree, limite_depassee, reglages, poste):
    """Notification de bureau et ntfy à la fin d'un travail assez long. Ne lève jamais, ne retient pas le travail."""
    try:
        if not reglages.notifications_fin or duree < reglages.notification_fin_min:
            return
        if limite_depassee:
            titre, texte, priorite, etiquettes = 'Travail arrêté', f'« {nom} » arrêté : limite mémoire dépassée', 'haute', ('warning',)
        elif code != 0:
            titre, texte, priorite, etiquettes = 'Travail échoué', f'« {nom} » a échoué (code {code})', 'haute', ('x',)
        else:
            titre, texte, priorite, etiquettes = ('Travail terminé', f'« {nom} » terminé en {duree_texte(duree)}',
                                                  'normale', ('white_check_mark',))
        poste.notifier(titre, texte)
        if reglages.ntfy:
            poste.alerter_ntfy(reglages.ntfy, titre, texte, priorite, etiquettes)
    except Exception:
        pass


# ======================================================================================================================
# Attente du tour et exécution
# ======================================================================================================================
def priorite_de(t):
    """Priorité d'un ticket ; absente ou inconnue (ancien outil, ticket de la 1.2) : normal."""
    p = t.get('priorite')
    return p if p in NIVEAUX else PRIORITE_DEFAUT


def nombre_ticket(t, champ, defaut=1):
    v = t.get(champ)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else defaut


def attente_depuis(info, maintenant):
    """Secondes écoulées depuis l'entrée du travail dans la file."""
    t = info['t']
    depuis = horodatage(t.get('depuis'))
    if depuis is None:
        try:
            depuis = int(info['nom'].split('-')[0]) / 1000
        except ValueError:
            return 0
    return maintenant - depuis


def evaluer(dossier, poste, paralleles, nettoyer=True, moi=None):
    """Le tour de chacun, en une passe : `infos` (tickets vivants du plus ancien au plus récent, avec leur statut :
    « cours », « attente » d'un travail de cette version ou « ancien » pour un ticket d'ancien outil qui attend),
    `motifs` ({ticket: (clé, texte)} de ce qui retient chaque travail en attente, (None, None) si son tour est venu) et
    `cours` (nombre de travaux en cours). `moi` = (ticket, contenu) d'un ticket pas encore écrit (tests)."""
    noms = tickets(dossier, poste, nettoyer=nettoyer)
    if moi and moi[0] not in noms:
        noms = sorted(noms + [moi[0]])
    infos = []
    for k, nom in enumerate(noms):
        t = lire_ticket(dossier, nom)
        if t is None and moi and moi[0] == nom:
            t = moi[1]
        t = t or {}
        moderne = t.get('source') == 'gagarine' and 'priorite' in t
        connu = t.get('source') == 'gagarine' and t.get('etat') == ETAT_COURS
        # Un ticket d'ancien outil a la main quand rien n'est plus ancien que lui ; sinon il attend, et personne ne le double
        statut = 'cours' if connu or (not moderne and k == 0) else ('attente' if moderne else 'ancien')
        infos.append({'nom': nom, 't': t, 'statut': statut, 'connu': connu})
    maintenant = poste.maintenant()
    cours = [i for i in infos if i['statut'] == 'cours']
    reserve, recents = MARGE_GO if cours else 0, 0   # seul, un travail peut prendre toute la mémoire disponible
    for i in cours:
        t = i['t']
        if not i['connu']:
            reserve += RESERVE_ANCIEN_GO
            continue
        utilise = 0
        try:
            chemin = poste.cgroup_scope(t['unite']) if t.get('unite') else None
            utilise = (poste.memoire_cgroup(chemin) if chemin else None) or 0
        except Exception:
            pass
        reserve += max(0, nombre_ticket(t, 'ram') - utilise)
        debut = horodatage(t.get('debut'))
        if debut is not None and 0 <= maintenant - debut < COEURS_RECENTS_S:
            recents += nombre_ticket(t, 'coeurs')
    ram_libre, charge, nb_coeurs = poste.ram_disponible_go(), poste.charge(), poste.coeurs()

    def tient(i):
        """(clé, texte) de ce qui empêche ce travail de démarrer maintenant, ou None."""
        t = i['t']
        if len(cours) >= paralleles:
            return 'cours', f'{len(cours)} {"travail" if len(cours) == 1 else "travaux"} en cours'
        besoin, coeurs = nombre_ticket(t, 'ram'), nombre_ticket(t, 'coeurs')
        if ram_libre - reserve < besoin:
            return 'ram', f'mémoire : {fr(max(0, ram_libre - reserve))} Go libres, il en faut {fr(besoin)}'
        if nb_coeurs - charge - recents < coeurs:
            return 'cpu', (f'processeurs occupés (charge {charge:.1f} pour {nb_coeurs} cœurs), '
                           f'il lui en faut {joli(coeurs)} libres')
        return None

    attente = sorted((i for i in infos if i['statut'] == 'attente'),
                     key=lambda i: (NIVEAUX.index(priorite_de(i['t'])), i['nom']))
    for i in attente:
        i['ancien'] = next((a for a in infos if a['statut'] == 'ancien' and a['nom'] < i['nom']), None)
        i['tient'] = tient(i)
        i['famine'] = attente_depuis(i, maintenant) > FAMINE_S
    motifs = {}
    for k, i in enumerate(attente):
        if i['ancien']:
            motif = ('ancien', f'derrière « {i["ancien"]["t"].get("nom") or "?"} » (ancien outil)')
        else:
            # Un travail plus haut dans le rang passe d'abord, sauf s'il ne tient pas en ce moment, et sauf famine : il
            # est alors le prochain, quoi qu'il arrive (à moins qu'un ancien outil ne le retienne lui-même)
            devant = next((h for h in attente[:k] if not h['ancien'] and (h['famine'] or not h['tient'])), None)
            if devant:
                motif = ('rang:' + devant['nom'],
                         f'derrière « {devant["t"].get("nom") or "?"} » ({priorite_de(devant["t"])})')
            else:
                motif = i['tient'] or (None, None)
        motifs[i['nom']] = motif
    return {'infos': infos, 'motifs': motifs, 'cours': len(cours)}


def motif_de(info, rang, ev, poste, paralleles):
    """Texte court de ce qui retient un travail dans `--etat` ; None s'il tourne (ou si on ne peut pas le dire)."""
    if info['statut'] == 'cours':
        return None
    if paralleles > 1 and info['nom'] in ev['motifs']:
        return ev['motifs'][info['nom']][1]
    avant = ev['infos'][:rang]
    if avant:
        return f'{len(avant)} travail(aux) avant lui, dont « {avant[0]["t"].get("nom") or "?"} »'
    t = info['t']
    if t.get('source') == 'gagarine':
        # Un seul travail à la fois : au premier rang, il n'attend plus que la mémoire et les processeurs
        ram_libre, libres = poste.ram_disponible_go(), poste.coeurs() - poste.charge()
        if ram_libre < nombre_ticket(t, 'ram'):
            return f'mémoire disponible {ram_libre:.1f} Go, il lui en faut {joli(nombre_ticket(t, "ram"))}'
        if libres < nombre_ticket(t, 'coeurs'):
            return (f'processeurs occupés (charge {poste.charge():.1f} pour {poste.coeurs()} cœurs), '
                    f'il lui en faut {joli(nombre_ticket(t, "coeurs"))} libres')
    return None


def raison_d_attendre(ticket, nom, ram, coeurs, apres, dossier, poste, paralleles=1, priorite=PRIORITE_DEFAUT):
    """(clé, texte) de ce qui retient le travail, ou (None, None) si son tour est venu. Avec un seul travail à la fois,
    mêmes textes et même règle que l'outil Node ; avec plusieurs, voir `evaluer`."""
    if paralleles > 1:
        if apres and poste.demarrage(apres):
            return 'apres', f'attend la fin du travail {apres}, lancé hors de la file'
        moi = (ticket, {'source': 'gagarine', 'priorite': priorite, 'nom': nom, 'ram': ram, 'coeurs': coeurs,
                        'etat': ETAT_ATTENTE, 'depuis': iso_ms(poste.maintenant())})
        return evaluer(dossier, poste, paralleles, moi=moi)['motifs'].get(ticket, (None, None))
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


def attendre_son_tour(nom, ram, coeurs, apres, commande, env, poste, dossier, sortie, initiale=None, paralleles=1,
                      priorite=PRIORITE_DEFAUT):
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
               'unite': None, 'sortie': env.get('GAGARINE_SORTIE') or None, 'source': 'gagarine',
               'priorite': priorite}
    if initiale is not None and initiale != ram:
        contenu['annonce_initiale'] = joli(initiale)   # annonce corrigée : ce que le travail avait demandé
    ecrire_ticket(dossier, ticket, contenu)
    dit = ''
    try:
        while True:
            cle, motif = raison_d_attendre(ticket, nom, ram, coeurs, apres, dossier, poste, paralleles, priorite)
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
    contenu = lire_ticket(dossier, ticket) or contenu   # la priorité a pu changer pendant l'attente
    contenu['etat'] = ETAT_COURS
    contenu['debut'] = iso_ms(poste.maintenant())
    ecrire_ticket(dossier, ticket, contenu)
    return ticket


def paralleles_de(reglages):
    """Nombre de travaux à la fois (au moins 1)."""
    try:
        return max(1, int(reglages.file_paralleles))
    except (AttributeError, TypeError, ValueError):
        return 1


def signaler_tendance(nom, env, sortie, note):
    """Au lancement d'un travail dont le pic monte d'un lancement à l'autre, ou que sa limite a arrêté les deux dernières
    fois : le dit et le note (événement `fuite-travail`). Ne lève jamais."""
    try:
        t = lire_travaux(env).get(nom)
        if not t:
            return
        etat = tendance(t['pics_go'], t['limites'])
        if etat == 'stable':
            return
        if etat == 'hausse':
            pics = t['pics_go']
            k = 1
            while k < len(pics) and pics[-k - 1] < pics[-k]:
                k += 1
            suite = ' → '.join(('%.1f' % p).replace('.', ',') for p in pics[-k:])
            print(f'  file d\'attente ({nom}) : pic en hausse à chaque lancement ({suite} Go) : fuite probable',
                  file=sortie, flush=True)
        else:
            print(f'  file d\'attente ({nom}) : arrêté par sa limite mémoire aux deux derniers lancements : '
                  f'fuite probable', file=sortie, flush=True)
        note('fuite-travail', nom=nom, tendance=etat, pics_go=t['pics_go'])
    except Exception:
        pass


def lancer_travail(nom, ram, coeurs, apres, commande, env, poste=None, sortie=None, erreur=None, journal=None,
                   reglages=None, priorite=PRIORITE_DEFAUT):
    """Attend son tour, lance la commande, rend son code de sortie (128 + n si tuée par le signal n, 137 si la limite
    mémoire l'a arrêtée, 143 ou 130 si la file a reçu SIGTERM ou SIGINT)."""
    poste = poste or Poste()
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    dossier = dossier_tickets(env)
    journal = journal or Journal(dossier_journal(env), heure=poste.maintenant, sortie=False)
    if reglages is None:
        try:
            reglages = charger_reglages(env=env)[0]
        except Exception:
            from gagarine.reglages import Reglages
            reglages = Reglages()

    def note(action, **champs):
        journal.ecrire(action, source='file', **champs)
    texte = shlex.join(commande)
    ticket = None
    unite = None
    debut = None
    poste.armer_signaux()
    try:
        initiale, ajuste = ram, False
        heritage = env.get('FILE_ATTENTE_TICKET')
        if not (heritage and (dossier / heritage).exists()):
            ram, ajuste = corriger_annonce(nom, ram, env, reglages, poste, sortie)
        ticket = attendre_son_tour(nom, ram, coeurs, apres, texte, env, poste, dossier, sortie, initiale,
                                   paralleles_de(reglages), priorite)
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
                           limite=joli(limite), commande=texte[:300], sortie=env.get('GAGARINE_SORTIE') or None,
                           annonce_initiale=joli(initiale) if ajuste else None, ajuste=True if ajuste else None)
            signaler_tendance(nom, env, sortie, note)
        debut = poste.mono()
        releveur = Releveur(poste, unite) if unite else None
        code = poste.lancer(argv, env, releveur.relever) if releveur else poste.lancer(argv, env)
        duree = round(poste.mono() - debut, 1)
        if releveur:
            releveur.relever()   # le cgroup a le plus souvent disparu avec le travail : le dernier relevé tient alors
        pic = releveur.pic if releveur else None
        limite_depassee = False
        if unite:
            resultat = poste.executer(['systemctl', '--user', 'show', f'{unite}.scope', '-p', 'Result', '--value'])[1].strip()
            poste.executer(['systemctl', '--user', 'reset-failed', f'{unite}.scope'])
            if resultat == 'oom-kill':
                print(f'\n  file d\'attente ({nom}) : travail ARRÊTÉ par sa limite mémoire : il a dépassé '
                      f'{joli(limite)} Go alors qu\'il en annonçait {joli(ram)}.'
                      f'\n  Le poste n\'a rien eu. Si ce travail a vraiment besoin de plus, relance-le en annonçant la '
                      f'RAM qu\'il lui faut ;\n  sinon, il a une fuite de mémoire à corriger avant de le relancer.',
                      file=erreur, flush=True)
                limite_depassee = True
                pic = max(pic or 0, limite)   # arrêté par sa limite : il a pris au moins celle-ci
                note('limite', nom=nom, declare=joli(ram), limite=joli(limite), commande=texte[:300],
                     pic_go=round(pic, 2))
                code = 137
        if not herite:
            note('travail-fin', nom=nom, pid=poste.pid(), ticket=ticket, code=code, duree=duree,
                 pic_go=round(pic, 2) if pic is not None else None)
            if pic is not None:
                noter_pic(env, nom, pic, initiale, poste.maintenant(), limite_depassee)
            notifier_fin(nom, code, duree, limite_depassee, reglages, poste)
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
def reglages_file(env, reglages=None):
    """Les réglages de la file (ceux du fichier et de l'environnement si on ne les donne pas)."""
    if reglages is not None:
        return reglages
    try:
        return charger_reglages(env=env)[0]
    except Exception:
        from gagarine.reglages import Reglages
        return Reglages()


def donnees_etat_file(env, poste=None, reglages=None):
    """L'objet de `gagarine file --etat --json` (contrats : docs/conception-1.1.0.md section 3, conception-1.3.0.md section 1)."""
    poste = poste or Poste()
    dossier = dossier_tickets(env)
    paralleles = paralleles_de(reglages_file(env, reglages))
    ev = evaluer(dossier, poste, paralleles, nettoyer=False)
    return {'ram_disponible_go': round(poste.ram_disponible_go(), 1), 'charge': round(poste.charge(), 1),
            'coeurs': poste.coeurs(), 'dossier': str(dossier), 'paralleles': paralleles, 'en_cours': ev['cours'],
            'travaux': donnees_travaux(dossier, poste, paralleles, ev)}


def afficher_etat(env, en_json, poste=None, sortie=None, reglages=None):
    poste = poste or Poste()
    sortie = sortie or sys.stdout
    d = donnees_etat_file(env, poste, reglages)
    if en_json:
        print(json.dumps(d, ensure_ascii=False), file=sortie)
        return 0
    print(f'Mémoire disponible : {d["ram_disponible_go"]:.1f} Go ; charge {d["charge"]:.1f} pour {d["coeurs"]} cœurs',
          file=sortie)
    if d['paralleles'] > 1:
        print(f'Travaux en cours : {d["en_cours"]} sur {d["paralleles"]} possibles', file=sortie)
    if not d['travaux']:
        print('Aucun travail en file.', file=sortie)
    for k, t in enumerate(d['travaux']):
        secondes = horodatage(t['depuis'])
        heure = time.strftime('%H:%M:%S', time.localtime(secondes)) if secondes is not None else '?'
        annonce = ''
        if isinstance(t['ram'], (int, float)):
            initiale = (lire_ticket(dossier_tickets(env), t['ticket']) or {}).get('annonce_initiale')
            annonce = f', RAM annoncée {fr(t["ram"])} Go'
            if isinstance(initiale, (int, float)):
                annonce += f' (corrigée : {fr(initiale)} Go demandés)'
        if t['priorite'] not in (None, PRIORITE_DEFAUT):
            annonce += f', priorité {t["priorite"]}'
        if d['paralleles'] > 1:
            etiquette = 'en attente' if t['motif'] else 'en cours  '
        else:
            etiquette = 'en attente' if k else 'en tête   '
        motif = f' — {t["motif"]}' if t['motif'] else ''
        print(f'{etiquette} : {t["nom"]} (processus {t["pid"]}, depuis {heure}{annonce}){motif}', file=sortie)
    return 0


def trouver_travail(cible, dossier, poste):
    """(nom du ticket, contenu) du travail désigné par un PID ou un nom de ticket, ou None."""
    for nom in tickets(dossier, poste, nettoyer=False):
        t = lire_ticket(dossier, nom)
        if t is None:
            continue
        if cible in (nom, nom[:-5]) or (cible.isdigit() and str(t.get('pid')) == cible):
            return nom, t
    return None


def changer_priorite(cible, niveau, env, root, poste=None, sortie=None, erreur=None):
    """Change la priorité d'un travail en attente (le sien ; root : tous). N'interrompt jamais un travail en cours."""
    poste = poste or Poste()
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    if niveau not in NIVEAUX:
        print(f'priorité « {niveau} » inconnue : {", ".join(NIVEAUX)}', file=erreur)
        return 2
    dossier = dossier_tickets(env)
    trouve = trouver_travail(cible, dossier, poste)
    if not trouve:
        print(f'« {cible} » : aucun travail de ce nom ou de ce processus dans la file (voir « gagarine file --etat »)',
              file=erreur)
        return 1
    nom, t = trouve
    if not root and poste.proprietaire(dossier / nom) != poste.uid():
        print(f'« {t.get("nom")} » (processus {t.get("pid")}) appartient à un autre utilisateur : seul lui ou '
              f'l\'administrateur peut changer sa priorité', file=erreur)
        return 1
    if t.get('source') != 'gagarine' or 'priorite' not in t:
        print(f'« {t.get("nom")} » a été lancé par un ancien outil : il n\'a pas de priorité', file=erreur)
        return 1
    if t.get('etat') != ETAT_ATTENTE:
        print(f'« {t.get("nom")} » tourne déjà : sa priorité ne peut plus changer', file=erreur)
        return 1
    ancien = priorite_de(t)
    t['priorite'] = niveau
    proprietaire = poste.proprietaire(dossier / nom)
    ecrire_ticket(dossier, nom, t)
    if proprietaire is not None and proprietaire != poste.uid():
        try:
            os.chown(dossier / nom, proprietaire, -1)   # le ticket reste à son propriétaire
        except OSError:
            pass
    print(f'travail « {t.get("nom")} » (processus {t["pid"]}) : priorité {ancien} → {niveau}', file=sortie)
    return 0


def annuler(cible, env, root, poste=None, sortie=None, erreur=None):
    """Annule un travail de la file (PID ou nom de ticket) : SIGTERM à la commande `gagarine file`, qui retire son
    ticket, arrête le scope et note l'annulation. Seulement les travaux de l'utilisateur ; root : tous."""
    poste = poste or Poste()
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    dossier = dossier_tickets(env)
    trouve = trouver_travail(cible, dossier, poste)
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
