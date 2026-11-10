"""Accès au système : /proc, cgroups, appels système, busctl, notifications, swap.

Tout ce que le garde demande au poste passe par ici ; les tests remplacent `Systeme` par une simulation.
"""
import ctypes
import os
import pwd
import re
import signal
import subprocess
import threading
import time
from collections import namedtuple
from pathlib import Path

Mo = 2 ** 20
CROISSANCE_MIN = 64 * Mo   # en dessous, un programme n'est pas considéré comme « en train de grossir »
ENCORE_EN_RAM = 256 * Mo   # un programme figé qui garde plus que ça en RAM n'a pas pu partir dans le swap
FENETRES = (10, 60)        # secondes sur lesquelles on mesure qui grossit
APPLICATION = 'Gagarine'
IDENTIFIANT = 'io.github.mawena.Gagarine'   # icône et fichier .desktop des notifications
MADV_PAGEOUT = 21
SYS_PIDFD_OPEN = 434
SYS_PROCESS_MADVISE = 440
PAQUET_IOVEC = 1024        # limite du noyau (UIO_MAXIOV)

# Programmes jamais figés ni arrêtés. Le noyau tronque les noms à 15 caractères dans /proc/PID/status.
PROTEGES_INTEGRES = (
    r'gnome-shell', r'gnome-session.*', r'gnome-terminal-.*', r'Xorg', r'Xwayland', r'gdm.*', r'systemd.*',
    r'\(sd-pam\)', r'systemd-journal.*', r'dbus.*', r'pipewire.*', r'wireplumber', r'pulseaudio', r'gsd-.*', r'ibus-.*',
    r'at-spi.*', r'xdg-.*', r'gvfs.*', r'mutter.*', r'garde-ram', r'gagarine', r'busctl', r'notify-send', r'polkit.*',
    r'evolution-.*', r'goa-.*', r'tracker-.*', r'NetworkManager', r'wpa_supplicant', r'sshd', r'cron', r'rsyslogd',
    r'udisksd', r'upowerd', r'accounts-daemon', r'snapd', r'dpkg', r'apt', r'apt-get', r'unattended-upgr',
    r'packagekitd', r'containerd', r'dockerd', r'login', r'agetty', r'runuser',
)


Proc = namedtuple('Proc', 'nom etat rss swap uid')


def go(octets):
    return f'{octets / 2 ** 30:.1f} Go'


def ram_depuis_meminfo(texte):
    """Pourcentage de RAM utilisée : 100 − MemAvailable / MemTotal (le cache récupérable compte comme libre)."""
    m = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+)', texte, re.M))
    return 100 * (1 - int(m['MemAvailable']) / int(m['MemTotal']))


def ram_utilisee():
    with open('/proc/meminfo') as f:
        return ram_depuis_meminfo(f.read())


def lire_processus(uid=None, moi=None):
    """{pid: Proc} des processus. uid : ne garder que ceux de cet utilisateur (None = tous, mode root).
    Sont exclus : le PID 1, le programme lui-même et les threads du noyau (PID 2 et enfants, sans mémoire propre)."""
    moi = os.getpid() if moi is None else moi
    res = {}
    for p in os.listdir('/proc'):
        if not p.isdigit() or int(p) in (1, 2, moi):
            continue
        try:
            with open(f'/proc/{p}/status', 'rb') as f:
                txt = f.read()
        except OSError:
            continue
        c = dict(re.findall(rb'^(Name|State|PPid|Uid|VmRSS|VmSwap):[ \t]+([^\n]*)', txt, re.M))
        if b'VmRSS' not in c or c.get(b'PPid') == b'2':   # zombie, thread du noyau
            continue
        puid = int(c[b'Uid'].split()[0])
        if uid is not None and puid != uid:
            continue
        res[int(p)] = Proc(c[b'Name'].decode(errors='replace'), c[b'State'].decode()[:1],
                           int(c[b'VmRSS'].split()[0]) * 1024, int(c.get(b'VmSwap', b'0').split()[0]) * 1024, puid)
    return res


def demarrage(pid):
    """Date de démarrage du processus : distingue un programme d'un autre qui aurait repris son numéro."""
    try:
        s = Path(f'/proc/{pid}/stat').read_text()
        return s[s.rindex(') ') + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def commande(pid):
    try:
        return Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()[:300]
    except OSError:
        return ''


def nom_utilisateur(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OverflowError):
        return str(uid)


def construire_proteges(supplement=''):
    """Expression régulière des noms protégés : liste intégrée + celle de l'utilisateur (réglage « proteges »)."""
    motifs = list(PROTEGES_INTEGRES)
    if supplement:
        motifs.append(supplement)
    return re.compile('^(?:' + '|'.join(f'(?:{m})' for m in motifs) + ')$')


class Iovec(ctypes.Structure):
    _fields_ = [('base', ctypes.c_void_p), ('longueur', ctypes.c_size_t)]


def zones_a_ranger(texte_maps):
    """Zones (début, fin) de /proc/PID/maps qui valent la peine d'être rangées : privées et modifiables (tas, piles,
    mémoire anonyme, données de bibliothèques). Les zones spéciales ([vvar], [vdso], [vsyscall]) sont ignorées."""
    zones = []
    for ligne in texte_maps.splitlines():
        champs = ligne.split(None, 5)
        if len(champs) < 5:
            continue
        adresses, droits = champs[0], champs[1]
        nom = champs[5] if len(champs) > 5 else ''
        if len(droits) < 4 or droits[1] != 'w' or droits[3] != 'p' or nom in ('[vvar]', '[vdso]', '[vsyscall]', '[vvar_vclock]'):
            continue
        debut, fin = (int(x, 16) for x in adresses.split('-'))
        if fin > debut:
            zones.append((debut, fin))
    return zones


def appel_systeme(numero, *args):
    """Appel système brut par ctypes. Lève OSError en cas d'échec."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    r = libc.syscall(ctypes.c_long(numero), *args)
    if r < 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return r


def ranger_precis(pid, appel=appel_systeme, lire_maps=lambda p: Path(f'/proc/{p}/maps').read_text(),
                  fermer=os.close):
    """Range dans le swap les seules pages du processus (process_madvise MADV_PAGEOUT, root). Le processus doit être figé :
    ses zones ne bougent plus. Retourne le nombre d'octets visés ; lève OSError si le noyau refuse dès le début
    (droits, noyau trop ancien) : l'appelant revient alors à memory.reclaim."""
    zones = zones_a_ranger(lire_maps(pid))
    if not zones:
        raise OSError(61, 'aucune zone à ranger')
    pidfd = appel(SYS_PIDFD_OPEN, ctypes.c_int(pid), ctypes.c_uint(0))
    total = 0
    try:
        for i in range(0, len(zones), PAQUET_IOVEC):
            paquet = zones[i:i + PAQUET_IOVEC]
            tableau = (Iovec * len(paquet))(*((d, f - d) for d, f in paquet))
            try:
                total += appel(SYS_PROCESS_MADVISE, ctypes.c_int(pidfd), ctypes.byref(tableau),
                               ctypes.c_size_t(len(paquet)), ctypes.c_int(MADV_PAGEOUT), ctypes.c_uint(0))
            except OSError:
                if i == 0 and total == 0:
                    raise    # refus dès le premier paquet : le mécanisme n'est pas utilisable
                # sinon une zone a disparu en route ; on continue avec les paquets suivants
    finally:
        fermer(pidfd)
    return total


def cgroup_origine(pid):
    try:
        return Path('/sys/fs/cgroup' + Path(f'/proc/{pid}/cgroup').read_text().strip().split('::', 1)[1])
    except (OSError, IndexError):
        return None


def commande_bridage(pid, cgroup, root, reste_mo, unite, nom_de_uid=nom_utilisateur):
    """Commande busctl qui met le PID dans un scope à lui, plafonné à reste_mo, sans swap.
    root + processus sous user@UID.service : le gestionnaire de cet utilisateur ; root + autre : le gestionnaire système ;
    sans privilèges : celui de l'utilisateur."""
    m = re.search(r'/user@(\d+)\.service(?:/|$)', cgroup or '')
    if not root:
        base = ['busctl', '--user']
    elif m:
        base = ['busctl', f'--machine={nom_de_uid(int(m.group(1)))}@.host', '--user']
    else:
        base = ['busctl']
    return base + ['call', 'org.freedesktop.systemd1', '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
                   'StartTransientUnit', 'ssa(sv)a(sa(sv))', unite, 'fail', '4', 'PIDs', 'au', '1', str(pid),
                   'MemoryMax', 't', str(int(reste_mo * Mo)), 'MemorySwapMax', 't', '0',
                   'CollectMode', 's', 'inactive-or-failed', '0']


def sessions_graphiques(lancer):
    """[(uid, utilisateur)] des sessions graphiques actives (x11 ou wayland), d'après loginctl.
    `lancer(commande)` retourne la sortie texte ('' en cas d'échec)."""
    res = []
    for ligne in lancer(['loginctl', 'list-sessions', '--no-legend']).splitlines():
        c = ligne.split()
        if len(c) < 3 or not c[1].isdigit():
            continue
        props = dict(l.split('=', 1) for l in lancer(['loginctl', 'show-session', c[0], '-p', 'Type', '-p', 'Active']
                                                     ).splitlines() if '=' in l)
        if props.get('Type') in ('x11', 'wayland') and props.get('Active') == 'yes' and (int(c[1]), c[2]) not in res:
            res.append((int(c[1]), c[2]))
    return res


def commandes_notification(titre, texte, avec_boutons, root, sessions):
    """Commandes notify-send à lancer : une seule sans privilèges ; en root, une par session graphique, sous l'utilisateur
    de la session, avec l'adresse de son bus."""
    cmd = ['notify-send', f'--app-name={APPLICATION}', '-u', 'critical', '-i', IDENTIFIANT,
           '-h', f'string:desktop-entry:{IDENTIFIANT}']
    if avec_boutons:
        cmd += ['-A', 'arreter=Arrêter', '-A', 'reprendre=Reprendre']
    cmd += [titre, texte]
    if not root:
        return [cmd]
    return [['runuser', '-u', nom, '--', 'env', f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus',
             f'XDG_RUNTIME_DIR=/run/user/{uid}'] + cmd for uid, nom in sessions]


def lancer_texte(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


class Systeme:
    """Tout ce que Garde demande au système. Les tests le remplacent par une simulation."""

    def __init__(self, r, root=None):
        self.r = r
        self.root = (os.geteuid() == 0) if root is None else root
        self.uid = os.getuid()
        self.moi = os.getpid()

    def ram(self):
        return ram_utilisee()

    def processus(self):
        return lire_processus(None if self.root else self.uid, self.moi)

    demarrage = staticmethod(demarrage)
    commande = staticmethod(commande)
    cgroup = staticmethod(cgroup_origine)
    utilisateur = staticmethod(nom_utilisateur)

    def heure(self):
        return time.time()

    def mono(self):
        return time.monotonic()

    def tuer(self, pid, sig):
        os.kill(pid, sig)

    def brider(self, pid):
        """None si le bridage a réussi, sinon le texte de l'erreur."""
        try:
            cg = Path(f'/proc/{pid}/cgroup').read_text()
        except OSError as e:
            return str(e)
        cmd = commande_bridage(pid, cg, self.root, self.r.reste_si_repris, f'gagarine-fige-{pid}-{int(time.time())}.scope')
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            return str(e)[:200]
        return None if res.returncode == 0 else (res.stderr.strip()[:200] or f'code {res.returncode}')

    def ranger(self, pid, rss, origine):
        """Range le programme dans le swap : process_madvise en root, memory.reclaim du groupe d'origine sinon
        (cgroup v2 ne déplace pas la mémoire déjà prise : c'est ce groupe-là que le noyau vide, en commençant par les pages
        qui ne servent plus, celles du programme figé)."""
        if self.root:
            try:
                return ranger_precis(pid)
            except OSError:
                pass
        if origine is None:
            return 0
        try:   # peut échouer en partie si le swap est plein : le programme sera alors arrêté
            (origine / 'memory.reclaim').write_text(str(rss))
        except OSError:
            pass
        return 0

    def notifier(self, titre, texte, pid=None, debut=None):
        """Notification du bureau ; avec un pid, elle propose « Arrêter » et « Reprendre »."""
        if not self.r.notifications:
            return
        sessions = sessions_graphiques(lancer_texte) if self.root else []
        for cmd in commandes_notification(titre, texte, bool(pid), self.root, sessions):
            threading.Thread(target=self._attendre_choix, args=(cmd, pid, debut), daemon=True).start()

    def _attendre_choix(self, cmd, pid, debut):
        try:
            choix = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        except OSError:
            return
        if pid and choix in ('arreter', 'reprendre') and demarrage(pid) == debut:
            try:
                self.tuer(pid, signal.SIGKILL if choix == 'arreter' else signal.SIGCONT)
            except OSError:
                pass



def lire_meminfo(texte):
    """(MemTotal, MemAvailable) en octets d'après le texte de /proc/meminfo."""
    m = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+)', texte, re.M))
    return int(m['MemTotal']) * 1024, int(m['MemAvailable']) * 1024


def lire_swaps(texte):
    """Zones de swap d'après /proc/swaps : [{nom, type, taille_mo, utilise_mo, priorite, zram}]."""
    res = []
    for ligne in texte.splitlines()[1:]:
        c = ligne.split()
        if len(c) < 5 or not (c[2].isdigit() and c[3].isdigit()):
            continue
        try:
            priorite = int(c[4])
        except ValueError:
            continue
        res.append({'nom': c[0], 'type': c[1], 'taille_mo': int(c[2]) // 1024, 'utilise_mo': int(c[3]) // 1024,
                    'priorite': priorite, 'zram': os.path.basename(c[0]).startswith('zram')})
    return res
