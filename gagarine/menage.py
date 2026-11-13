"""Ménage des programmes gourmands inactifs : démons Gradle et Kotlin, modèles LM Studio et Ollama, serveurs de dev.

`gagarine menage [--json]` liste les candidats, `gagarine menage arreter ID…` les arrête ou les décharge,
`gagarine menage auto` fait le ménage automatique (lancé toutes les 2 min par le minuteur utilisateur).
Seuls les processus de l'utilisateur sont touchés, et seulement ceux qui sont des candidats reconnus.
Tout ce que le module demande au poste passe par `Poste` ; les tests le remplacent par une simulation.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from gagarine.file import dossier_journal
from gagarine.journal import Journal
from gagarine.reglages import charger_reglages
from gagarine.systeme import Mo, lire_processus, ram_utilisee

MEMOIRE_MIN = 300 * Mo       # en dessous, un programme ne vaut pas le ménage
DELAI_COURT = 300            # s d'inactivité suffisant quand la RAM dépasse « vigilance »
ATTENTE_ARRET = 10           # s entre SIGTERM et SIGKILL
TOLERANCE_MIN = 0.5          # s de temps processeur tolérés entre deux passages pour rester « inactif »
TOLERANCE_PART = 0.02        # … ou 2 % du temps écoulé entre les deux passages, si c'est plus
URL_OLLAMA = 'http://127.0.0.1:11434'
DELAI_API = 1.5              # s : Ollama absent ne doit pas ralentir la commande
TYPES = ('gradle', 'kotlin', 'lmstudio', 'ollama', 'serveur-dev')
TYPES_AUTO = ('gradle', 'kotlin', 'lmstudio', 'ollama')   # serveur-dev : proposé, jamais arrêté tout seul
NOMS = {'gradle': 'Gradle (démon)', 'kotlin': 'Kotlin (démon de compilation)'}

# LM Studio. `lms`, appelé quand LM Studio ne tourne pas, le DÉMARRE (`lm-studio --run-as-service`, ~360 Mo) et n'a
# aucune option pour l'en empêcher. LM Studio n'est donc reconnu que par son vrai programme (/proc/<pid>/exe), jamais par
# une ligne de commande, et `lms` n'est appelé que si son serveur interne répond (verrou, http-server.json et port).
PROGRAMMES_LMSTUDIO = ('lm-studio', 'lm studio', 'llmster')   # application, démon sans interface
CACHE_LMS = 60               # s : la liste des modèles (`lms ps`) est gardée, pour la même instance de LM Studio
FICHIER_CACHE_LMS = 'lmstudio.json'
DELAI_LMS_PS = 3             # s : `lms ps` répond en 0,4 s ; l'indicateur arrête `menage --json` au bout de 6 s
DELAI_PORT = 0.5             # s pour se connecter au serveur interne de LM Studio
HOTES_LOCAUX = {'127.0.0.1': '127.0.0.1', 'localhost': 'localhost', '::1': '::1',
                '0.0.0.0': '127.0.0.1', '::': '::1'}   # hôte de http-server.json -> adresse où se connecter
MARQUE_LMS = 'GAGARINE_APPEL_LMS'   # passée à `lms` : un LM Studio qui la porte a été démarré par un appel de Gagarine
AGE_MARQUE = 120             # s : une instance marquée plus récente, d'un appel fini, est arrêtée au passage suivant
ATTENTE_FILET = 1            # s pour constater l'arrêt d'une instance démarrée par erreur (figée, puis SIGKILL)

# Serveurs de développement : programme (nom de fichier sans extension) -> arguments qui en font un serveur de dev.
# None : toujours un serveur de dev ; « interdits » : mots qui indiquent autre chose (build, preview).
SERVEURS_DEV = {
    'vite': {'interdits': ('build', 'preview', 'optimize')},
    'next': {'requis': ('dev',)},
    'nuxt': {'requis': ('dev',)},
    'nuxi': {'requis': ('dev',)},
    'ng': {'requis': ('serve', 'start')},
    'astro': {'requis': ('dev',)},
    'webpack': {'requis': ('serve',)},
    'webpack-dev-server': {},
    'react-scripts': {'requis': ('start',)},
    'storybook': {'requis': ('dev',)},
    'remix': {'requis': ('dev', 'vite:dev')},
}
EXECUTEURS_JS = ('node', 'nodejs', 'bun', 'deno')


# ======================================================================================================================
# Le poste
# ======================================================================================================================
class Poste:
    """Tout ce que le ménage demande au système. Les tests le remplacent par une simulation."""

    def maintenant(self):
        return time.time()

    def dormir(self, secondes):
        time.sleep(secondes)

    def ram(self):
        return ram_utilisee()

    def processus(self):
        """{pid: Proc} des processus de l'utilisateur."""
        return lire_processus(uid=os.getuid())

    def ligne_commande(self, pid):
        try:
            return Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
        except OSError:
            return ''

    def arguments(self, pid):
        """Arguments du processus (argv), [] s'il a disparu."""
        try:
            return [a.decode(errors='replace') for a in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]]
        except OSError:
            return []

    def exe(self, pid):
        """Programme réellement exécuté (/proc/<pid>/exe), '' s'il est illisible. Une ligne de commande peut tout dire ;
        ceci, non."""
        try:
            return os.readlink(f'/proc/{pid}/exe').removesuffix(' (deleted)')
        except OSError:
            return ''

    def parent(self, pid):
        try:
            s = Path(f'/proc/{pid}/stat').read_text()
            return int(s[s.rindex(') ') + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            return None

    def variable(self, pid, nom):
        """Valeur d'une variable d'environnement du processus, None si elle est absente ou illisible."""
        try:
            environ = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        except OSError:
            return None
        prefixe = nom.encode() + b'='
        for v in environ:
            if v.startswith(prefixe):
                return v[len(prefixe):].decode(errors='replace')
        return None

    def dossier_lms(self):
        return Path.home() / '.lmstudio'

    def port_ouvert(self, hote, port, delai=DELAI_PORT):
        try:
            with socket.create_connection((hote, port), timeout=delai):
                return True
        except OSError:
            return False

    def cpu(self, pid):
        """Temps processeur (utilisateur + système) du processus en secondes, None s'il a disparu."""
        try:
            s = Path(f'/proc/{pid}/stat').read_text()
            champs = s[s.rindex(') ') + 2:].split()
            return (int(champs[11]) + int(champs[12])) / os.sysconf('SC_CLK_TCK')
        except (OSError, ValueError, IndexError):
            return None

    def demarrage(self, pid):
        try:
            s = Path(f'/proc/{pid}/stat').read_text()
            return s[s.rindex(') ') + 2:].split()[19]
        except (OSError, ValueError, IndexError):
            return None

    def vivant(self, pid):
        try:
            s = Path(f'/proc/{pid}/stat').read_text()
            return s[s.rindex(') ') + 2:][:1] not in ('Z', 'X')
        except (OSError, ValueError):
            return False

    def tuer(self, pid, sig):
        os.kill(pid, sig)

    def chemin_lms(self):
        return shutil.which('lms') or str(Path.home() / '.lmstudio/bin/lms')

    def lancer(self, commande, delai=15, env=None):
        """(code, sortie standard) ; (None, '') si la commande est absente ou trop lente. env : variables ajoutées."""
        try:
            r = subprocess.run(commande, capture_output=True, text=True, timeout=delai,
                               env=dict(os.environ, **env) if env else None)
            return r.returncode, r.stdout
        except (OSError, subprocess.SubprocessError):
            return None, ''

    def api(self, methode, chemin, corps=None, delai=DELAI_API):
        """Réponse JSON de l'API locale d'Ollama, None si elle est absente ou en erreur."""
        donnees = json.dumps(corps).encode() if corps is not None else None
        requete = urllib.request.Request(URL_OLLAMA + chemin, data=donnees, method=methode,
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(requete, timeout=delai) as r:
                texte = r.read().decode(errors='replace')
            return json.loads(texte) if texte.strip() else {}
        except (OSError, ValueError):
            return None


# ======================================================================================================================
# Reconnaître les candidats
# ======================================================================================================================
def type_processus(nom, ligne):
    """Type de candidat d'un processus (nom du programme, ligne de commande), ou None."""
    if nom == 'java' or nom.startswith('java'):
        if 'KotlinCompileDaemon' in ligne:
            return 'kotlin'
        if 'GradleDaemon' in ligne:
            return 'gradle'
        return None
    if nom in EXECUTEURS_JS and est_serveur_dev(ligne):
        return 'serveur-dev'
    return None


def est_serveur_dev(ligne):
    """Vrai si la ligne de commande d'un node/bun/deno lance un serveur de développement (pas un build, pas la production)."""
    mots = ligne.split()
    for i, mot in enumerate(mots):
        programme = mot.rsplit('/', 1)[-1]
        for ext in ('.js', '.mjs', '.cjs'):
            if programme.endswith(ext):
                programme = programme[:-len(ext)]
        regle = SERVEURS_DEV.get(programme)
        if regle is None:
            continue
        suite = mots[i + 1:]
        if any(m in suite for m in regle.get('interdits', ())):
            continue
        if 'requis' in regle and not any(m in suite for m in regle['requis']):
            continue
        return True
    return False


# ======================================================================================================================
# LM Studio : détection stricte, cache de `lms ps`, filet de sécurité
# ======================================================================================================================
def programme_lmstudio(exe):
    """Vrai si l'exécutable est celui de LM Studio (application) ou de son démon sans interface (llmster)."""
    return exe.removesuffix(' (deleted)').rsplit('/', 1)[-1].lower() in PROGRAMMES_LMSTUDIO


def instances_lmstudio(poste, processus):
    """{pid: démarrage} des instances de LM Studio qui tournent, reconnues par leur vrai programme (/proc/<pid>/exe) :
    le processus principal de l'application (`lm-studio` sans `--type=`) ou le démon `llmster`.
    Ne comptent jamais : les processus fils de l'application (`--type=…`), chrome_crashpad_handler, le `node` de
    ~/.lmstudio/.internal, `lms`, ni un processus dont seule la ligne de commande cite LM Studio."""
    trouves = {}
    for pid in sorted(processus):
        if not programme_lmstudio(poste.exe(pid)):
            continue
        if any(a.startswith('--type=') for a in poste.arguments(pid)[1:]):
            continue
        if poste.vivant(pid):
            trouves[pid] = poste.demarrage(pid)
    return trouves


def dossiers_lmstudio(poste, pids):
    """Dossiers des programmes de LM Studio (celui des instances, et ~/.lmstudio), terminés par « / ». Un exécutable
    illisible (processus disparu) ne donne rien : jamais « / », qui désignerait tous les programmes."""
    dossiers = {str(poste.dossier_lms()).rstrip('/') + '/'}
    for pid in pids:
        exe = poste.exe(pid)
        if '/' in exe.strip('/'):
            dossiers.add(exe.rsplit('/', 1)[0] + '/')
    return {d for d in dossiers if len(d) > 1}


def de_lmstudio(exe, dossiers):
    """Vrai si le programme est installé avec LM Studio (sauf `lms`, qui n'en est que le client)."""
    return bool(exe) and any(exe.startswith(d) for d in dossiers) and not exe.endswith('/lms')


def famille_lmstudio(poste, processus, instances):
    """pid de tout ce qui appartient à LM Studio (fils, crashpad, node interne), pour mesurer son activité."""
    dossiers = dossiers_lmstudio(poste, instances)
    return [pid for pid in sorted(processus) if pid in instances or de_lmstudio(poste.exe(pid), dossiers)]


def lire_json(chemin):
    try:
        return json.loads(Path(chemin).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def serveur_lmstudio(poste, instances):
    """pid de l'instance dont le serveur interne répond, celle à qui `lms` parlera ; None s'il n'y en a pas.
    Il faut que le verrou (~/.lmstudio/.internal/llmster-pid.lock) et http-server.json désignent la même instance vivante
    (un verrou reste après un arrêt brutal, et son PID peut avoir été repris par un autre programme), et que le port de
    http-server.json accepte une connexion. Sinon `lms` démarrerait peut-être LM Studio lui-même."""
    if not instances:
        return None
    interne = Path(poste.dossier_lms()) / '.internal'
    try:
        pid = int((interne / 'llmster-pid.lock').read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None
    serveur = lire_json(interne / 'http-server.json')
    if pid not in instances or not isinstance(serveur, dict) or serveur.get('pid') != pid:
        return None
    port, hote = serveur.get('port'), serveur.get('host') or '127.0.0.1'
    if (not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536
            or not isinstance(hote, str) or hote not in HOTES_LOCAUX):
        return None
    return pid if poste.port_ouvert(HOTES_LOCAUX[hote], port) else None


def lire_cache_lms(fichier, instances, maintenant):
    """(pid, modèles) gardés il y a moins de CACHE_LMS secondes pour une instance qui tourne encore, sinon None."""
    d = lire_json(fichier) if fichier else None
    if not isinstance(d, dict):
        return None
    pid, quand, modeles = d.get('pid'), d.get('quand'), d.get('modeles')
    if (not isinstance(pid, int) or pid not in instances or d.get('demarrage') != instances[pid] or not isinstance(quand, (int, float))
            or not 0 <= maintenant - quand < CACHE_LMS or not isinstance(modeles, list)):
        return None
    try:
        return pid, [(str(nom), int(taille)) for nom, taille in modeles]
    except (TypeError, ValueError):
        return None


def ecrire_cache_lms(fichier, pid, demarrage, maintenant, modeles):
    if not fichier:
        return
    fichier = Path(fichier)
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        tmp = fichier.with_name(f'{fichier.name}.{os.getpid()}.tmp')
        tmp.write_text(json.dumps({'version': 1, 'pid': pid, 'demarrage': demarrage, 'quand': maintenant,
                                   'modeles': modeles}, ensure_ascii=False), encoding='utf-8')
        tmp.replace(fichier)
    except OSError:
        pass


def oublier_cache_lms(fichier):
    if fichier:
        try:
            Path(fichier).unlink(missing_ok=True)
        except OSError:
            pass


def appeler_lms(poste, arguments, journal=None, delai=15):
    """Lance `lms <arguments>`, marqué par MARQUE_LMS. Filet de sécurité : si l'appel a démarré LM Studio, l'instance
    démarrée est arrêtée aussitôt et écrite au journal. (code, sortie) comme Poste.lancer."""
    avant = poste.processus()
    instances_avant = instances_lmstudio(poste, avant)
    jeton = f'{os.getpid()}-{int(poste.maintenant())}'
    code, sortie = poste.lancer([poste.chemin_lms()] + list(arguments), delai=delai, env={MARQUE_LMS: jeton})
    apres = poste.processus()
    nouvelles = {pid: d for pid, d in instances_lmstudio(poste, apres).items()
                 if pid not in instances_avant or instances_avant[pid] != d}
    if nouvelles:
        # « aucune instance avant l'appel » : celles d'avant sont parties pendant l'appel (LM Studio se fermait)
        personne_avant = not any(poste.vivant(pid) and poste.demarrage(pid) == d for pid, d in instances_avant.items())
        fautives = [pid for pid in nouvelles if poste.variable(pid, MARQUE_LMS) == jeton
                    or (personne_avant and '--run-as-service' in poste.arguments(pid))]
        if fautives:
            arreter_demarrage(poste, fautives, jeton, set(avant), apres, journal, ' '.join(['lms'] + list(arguments)))
    return code, sortie


def descendants(poste, processus, racines):
    """pid des descendants (fils, petits-fils…) des processus `racines`."""
    trouves, ajoute = set(), True
    racines = set(racines)
    parents = {pid: poste.parent(pid) for pid in processus}
    while ajoute:
        ajoute = False
        for pid, parent in parents.items():
            if pid not in trouves and pid not in racines and parent in racines | trouves:
                trouves.add(pid)
                ajoute = True
    return trouves


def arreter_demarrage(poste, fautives, jeton, avant, processus, journal, commande):
    """Arrête aussitôt les instances de LM Studio démarrées par un appel de Gagarine à `lms`, avec leur famille : ce qui
    porte la même marque, ce qui en descend, et ce qui est né pendant l'appel parmi les programmes de LM Studio
    (crashpad, node interne). Les instances sont d'abord figées (SIGSTOP) : elles ne lancent plus de fils pendant qu'on
    relève leur famille ; puis tout reçoit SIGKILL (LM Studio ignore SIGTERM, et une instance qui vient de démarrer n'a
    rien à enregistrer)."""
    for pid in fautives:
        try:
            poste.tuer(pid, signal.SIGSTOP)
        except OSError:
            pass
    processus = poste.processus() or processus   # relevé une fois les instances figées
    dossiers = dossiers_lmstudio(poste, fautives)
    # une autre instance (ouverte par l'utilisateur au même moment) et sa famille ne sont jamais touchées
    autres = [pid for pid in instances_lmstudio(poste, processus) if pid not in fautives]
    proteges = set(autres) | descendants(poste, processus, autres)
    victimes = list(fautives)
    for pid in sorted(processus):
        if pid in victimes or pid in proteges:
            continue
        if (jeton is not None and poste.variable(pid, MARQUE_LMS) == jeton) or (
                pid not in avant and de_lmstudio(poste.exe(pid), dossiers)):
            victimes.append(pid)
    victimes += sorted(descendants(poste, processus, victimes) - set(victimes) - proteges)
    for pid in victimes:
        try:
            poste.tuer(pid, signal.SIGKILL)
        except OSError:
            pass
    attendu = 0.0
    while attendu < ATTENTE_FILET and any(poste.vivant(pid) for pid in victimes):
        poste.dormir(0.1)
        attendu += 0.1
    raison = 'démarré par un appel de Gagarine à lms'
    for pid in fautives:
        if journal is not None:
            journal.ecrire('lmstudio-arrete', nom='LM Studio', pid=pid, raison=raison, commande=commande,
                           processus=len(victimes))
        print(f'LM Studio (pid {pid}) {raison} (« {commande} ») : arrêté aussitôt', file=sys.stderr)


def arreter_instances_marquees(poste, instances, processus, journal):
    """Suite du filet : un appel à `lms` interrompu (l'indicateur arrête `menage --json` au bout de 6 s) a pu laisser une
    instance marquée. Elle est arrêtée si l'appel est fini et si elle a moins de AGE_MARQUE secondes. Vrai si arrêtée."""
    arretees = False
    for pid in instances:
        jeton = poste.variable(pid, MARQUE_LMS)
        if not jeton:
            continue
        try:
            appelant, quand = (int(x) for x in jeton.rsplit('-', 1))
        except ValueError:
            continue
        if appelant != os.getpid() and poste.vivant(appelant):
            continue   # l'appel est en cours : son propre filet s'en charge
        if 0 <= poste.maintenant() - quand <= AGE_MARQUE and poste.vivant(pid):
            arreter_demarrage(poste, [pid], jeton, set(processus), processus, journal, 'lms (appel interrompu)')
            arretees = True
    return arretees


def modeles_lmstudio(sortie):
    """[(identifiant, octets)] des modèles chargés, d'après `lms ps --json`."""
    try:
        donnees = json.loads(sortie)
    except ValueError:
        return []
    if isinstance(donnees, dict):
        donnees = donnees.get('models') or donnees.get('data') or []
    modeles = []
    for m in donnees if isinstance(donnees, list) else []:
        if isinstance(m, dict):
            nom = m.get('identifier') or m.get('modelKey') or m.get('path') or 'modèle'
            taille = m.get('sizeBytes') or m.get('size') or 0
            modeles.append((str(nom), int(taille) if isinstance(taille, (int, float)) else 0))
    return modeles


def detecter(poste, cache=None, journal=None):
    """Candidats actuels, sans l'inactivité : [{id, type, nom, pid, memoire_mo, action, cpu, signe, demarrage}].
    cache : fichier du cache de `lms ps` ; journal : pour le filet de sécurité de LM Studio."""
    processus = poste.processus()
    lignes = {}

    def ligne(pid):
        if pid not in lignes:
            lignes[pid] = poste.ligne_commande(pid)
        return lignes[pid]

    trouves = []
    for pid, p in sorted(processus.items()):
        if p.rss < MEMOIRE_MIN:
            continue
        genre = type_processus(p.nom, ligne(pid))
        if genre is None:
            continue
        nom = NOMS.get(genre)
        if nom is None:
            programme = next((m.rsplit('/', 1)[-1] for m in ligne(pid).split()
                              if m.rsplit('/', 1)[-1].split('.')[0] in SERVEURS_DEV), p.nom)
            nom = f"Serveur de dev ({programme.split('.')[0]})"
        trouves.append({'id': str(pid), 'type': genre, 'nom': nom, 'pid': pid, 'memoire_mo': p.rss // Mo,
                        'action': 'arreter', 'cpu': poste.cpu(pid), 'signe': None, 'demarrage': poste.demarrage(pid)})
    trouves += candidats_lmstudio(poste, processus, cache, journal)
    trouves += candidats_ollama(poste, processus)
    return trouves


def somme_cpu(poste, pids):
    temps = [poste.cpu(pid) for pid in pids]
    return sum(t for t in temps if t is not None)


def candidats_lmstudio(poste, processus, cache=None, journal=None):
    """Les modèles chargés dans LM Studio, d'après `lms ps` gardé CACHE_LMS secondes (jamais à chaque passage)."""
    instances = instances_lmstudio(poste, processus)
    if arreter_instances_marquees(poste, instances, processus, journal):
        processus = poste.processus()
        instances = instances_lmstudio(poste, processus)
    if not instances:
        return []   # LM Studio ne tourne pas : on ne lance surtout pas `lms` (il démarrerait un service de ~360 Mo)
    garde = lire_cache_lms(cache, instances, poste.maintenant())
    if garde is not None:
        pid, modeles = garde
    else:
        pid = serveur_lmstudio(poste, instances)
        if pid is None:
            return []   # démarrage, fermeture ou verrou périmé : `lms` pourrait démarrer une autre instance
        code, sortie = appeler_lms(poste, ['ps', '--json'], journal, delai=DELAI_LMS_PS)
        if not poste.vivant(pid) or poste.demarrage(pid) != instances[pid]:
            return []   # LM Studio s'est fermé pendant l'appel
        modeles = modeles_lmstudio(sortie) if code == 0 else []
        ecrire_cache_lms(cache, pid, instances[pid], poste.maintenant(), modeles)
    total = sum(t for _, t in modeles)
    if not modeles or total < MEMOIRE_MIN:
        return []
    return [{'id': 'lmstudio', 'type': 'lmstudio', 'nom': 'LM Studio : ' + ', '.join(n for n, _ in modeles),
             'pid': None, 'memoire_mo': total // Mo, 'action': 'decharger',
             'cpu': somme_cpu(poste, famille_lmstudio(poste, processus, instances)),
             'signe': None, 'demarrage': instances[pid]}]


def candidats_ollama(poste, processus):
    reponse = poste.api('GET', '/api/ps')
    if not isinstance(reponse, dict):
        return []
    pids = [pid for pid, p in processus.items() if p.nom.startswith('ollama')]
    cpu = somme_cpu(poste, pids)
    trouves = []
    for m in reponse.get('models') or []:
        if not isinstance(m, dict):
            continue
        nom = str(m.get('name') or m.get('model') or '')
        taille = m.get('size') if isinstance(m.get('size'), (int, float)) else 0
        if not nom or taille < MEMOIRE_MIN:
            continue
        trouves.append({'id': f'ollama:{nom}', 'type': 'ollama', 'nom': f'Ollama : {nom}', 'pid': None,
                        'memoire_mo': int(taille) // Mo, 'action': 'decharger', 'cpu': cpu,
                        # expires_at est repoussé à chaque utilisation du modèle : un changement vaut activité
                        'signe': str(m.get('expires_at') or ''), 'demarrage': None})
    return trouves


# ======================================================================================================================
# Suivi de l'inactivité
# ======================================================================================================================
def lire_suivi(fichier):
    """{id: {cpu, vu, depuis, signe, demarrage}} ; un fichier absent ou abîmé donne un suivi vide."""
    try:
        donnees = json.loads(Path(fichier).read_text(encoding='utf-8'))
        suivi = donnees['suivi']
        if not isinstance(suivi, dict):
            return {}
        return {str(k): v for k, v in suivi.items() if isinstance(v, dict)
                and all(isinstance(v.get(c), (int, float)) for c in ('cpu', 'vu', 'depuis'))}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def ecrire_suivi(fichier, suivi):
    fichier = Path(fichier)
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        tmp = fichier.with_name(fichier.name + '.tmp')
        tmp.write_text(json.dumps({'version': 1, 'suivi': suivi}, ensure_ascii=False), encoding='utf-8')
        tmp.replace(fichier)
    except OSError:
        pass


def mettre_a_jour_suivi(ancien, candidats, maintenant):
    """Nouveau suivi des candidats actuels (les disparus sont oubliés) et leur inactivité en secondes.
    Actif : le temps processeur a bougé de plus que la tolérance depuis le passage précédent, ou la signature a changé."""
    suivi = {}
    inactivite = {}
    for c in candidats:
        cpu = c['cpu'] if c['cpu'] is not None else 0.0
        avant = ancien.get(c['id'])
        meme = (avant is not None and avant.get('demarrage') == c['demarrage'] and avant.get('signe') == c['signe']
                and cpu >= avant['cpu'])
        depuis = maintenant
        if meme:
            ecoule = max(0.0, maintenant - avant['vu'])
            if cpu - avant['cpu'] <= max(TOLERANCE_MIN, TOLERANCE_PART * ecoule) and avant['depuis'] <= maintenant:
                depuis = avant['depuis']
        suivi[c['id']] = {'cpu': cpu, 'vu': maintenant, 'depuis': depuis, 'signe': c['signe'],
                          'demarrage': c['demarrage']}
        inactivite[c['id']] = int(maintenant - depuis)
    return suivi, inactivite


def lister(poste, fichier_suivi, cache=None, journal=None):
    """Candidats complets (avec inactif_depuis_s et auto) ; le suivi est mis à jour au passage."""
    candidats = detecter(poste, cache, journal)
    suivi, inactivite = mettre_a_jour_suivi(lire_suivi(fichier_suivi), candidats, poste.maintenant())
    ecrire_suivi(fichier_suivi, suivi)
    for c in candidats:
        c['inactif_depuis_s'] = inactivite[c['id']]
        c['auto'] = c['type'] in TYPES_AUTO
    return candidats


def public(c):
    """Le candidat tel qu'il est publié (contrat de `menage --json`)."""
    return {cle: c[cle] for cle in ('id', 'type', 'nom', 'pid', 'memoire_mo', 'inactif_depuis_s', 'action', 'auto')}


# ======================================================================================================================
# Arrêter, décharger
# ======================================================================================================================
def arreter_processus(poste, pid):
    """SIGTERM, puis SIGKILL si le programme tient encore après ATTENTE_ARRET secondes. Vrai s'il est parti."""
    try:
        poste.tuer(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    attendu = 0.0
    while attendu < ATTENTE_ARRET:
        if not poste.vivant(pid):
            return True
        poste.dormir(0.5)
        attendu += 0.5
    if not poste.vivant(pid):
        return True
    try:
        poste.tuer(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    poste.dormir(0.5)
    return not poste.vivant(pid)


def decharger(poste, c, cache=None, journal=None):
    """Décharge les modèles du candidat ; vrai si la commande ou l'appel a réussi."""
    if c['type'] == 'lmstudio':
        # Revérifié strictement juste avant : `lms` ne doit jamais démarrer LM Studio lui-même.
        if serveur_lmstudio(poste, instances_lmstudio(poste, poste.processus())) is None:
            return False
        code, _ = appeler_lms(poste, ['unload', '--all'], journal)
        oublier_cache_lms(cache)
        return code == 0
    if c['type'] == 'ollama':
        modele = c['id'].split(':', 1)[1]
        return poste.api('POST', '/api/generate', {'model': modele, 'keep_alive': 0}, delai=10) is not None
    return False


def appliquer(poste, c, cache=None, journal=None):
    return arreter_processus(poste, c['pid']) if c['action'] == 'arreter' else decharger(poste, c, cache, journal)


def traiter(poste, journal, c, auto, cache=None):
    """Arrête ou décharge un candidat et l'écrit au journal. Vrai si c'est fait."""
    ok = appliquer(poste, c, cache, journal)
    verbe = 'arrêté' if c['action'] == 'arreter' else 'déchargé'
    if ok:
        journal.ecrire('menage', type=c['type'], nom=c['nom'], pid=c['pid'], memoire_mo=c['memoire_mo'], auto=auto)
        print(f"{c['nom']} {verbe} ({c['memoire_mo']} Mo libérés)")
    else:
        print(f"{c['nom']} : impossible " + ("de l'arrêter" if c['action'] == 'arreter' else 'de le décharger'))
    return ok


# ======================================================================================================================
# Commandes
# ======================================================================================================================
def duree(secondes):
    if secondes < 90:
        return f'{secondes} s'
    if secondes < 5400:
        return f'{round(secondes / 60)} min'
    return f'{secondes / 3600:.1f} h'.replace('.', ',')


def contexte_menage(ctx, poste):
    env = ctx.env
    reglages, _ = charger_reglages(getattr(ctx, 'config', None), env)
    dossier = dossier_journal(env)
    return (reglages, dossier / FICHIER_CACHE_LMS, dossier / 'menage.json',
            Journal(dossier, heure=poste.maintenant, sortie=False))


def delai_auto(reglages, poste):
    """Secondes d'inactivité qui suffisent aujourd'hui : menage_delai, ou 5 min si la RAM dépasse vigilance."""
    delai = reglages.menage_delai * 60
    if poste.ram() > reglages.vigilance:
        delai = min(delai, DELAI_COURT)
    return delai


def nombre(x):
    return int(x) if float(x).is_integer() else x


def commande_lister(args, ctx, poste):
    json_demande = args == ['--json']
    if args and not json_demande:
        return usage()
    reglages, cache, suivi, journal = contexte_menage(ctx, poste)
    candidats = lister(poste, suivi, cache, journal)
    if json_demande:
        print(json.dumps({'auto': reglages.menage_auto, 'delai_min': nombre(reglages.menage_delai),
                          'candidats': [public(c) for c in candidats]}, ensure_ascii=False))
        return 0
    etat = 'activé' if reglages.menage_auto else 'désactivé'
    print(f'Ménage automatique : {etat} (arrêt après {nombre(reglages.menage_delai)} min d\'inactivité)')
    if not candidats:
        print('Aucun programme gourmand inactif.')
        return 0
    print(f"{'ID':<28} {'TYPE':<12} {'MÉMOIRE':>9}  {'INACTIF':>8}  AUTO  NOM")
    for c in candidats:
        print(f"{c['id']:<28} {c['type']:<12} {str(c['memoire_mo']) + ' Mo':>9}  {duree(c['inactif_depuis_s']):>8}  "
              f"{'oui' if c['auto'] else 'non':<4}  {c['nom']}")
    return 0


def commande_arreter(args, ctx, poste):
    if not args:
        return usage()
    _, cache, suivi, journal = contexte_menage(ctx, poste)
    par_id = {c['id']: c for c in lister(poste, suivi, cache, journal)}
    code = 0
    for identifiant in args:
        c = par_id.get(identifiant)
        if c is None:
            print(f"« {identifiant} » n'est pas un candidat au ménage (voir gagarine menage)")
            code = 1
        elif not traiter(poste, journal, c, False, cache):
            code = 1
    return code


def commande_auto(args, ctx, poste):
    if args:
        return usage()
    reglages, cache, suivi, journal = contexte_menage(ctx, poste)
    candidats = lister(poste, suivi, cache, journal)   # le suivi est tenu à jour même quand le ménage auto est coupé
    if not reglages.menage_auto:
        return 0
    delai = delai_auto(reglages, poste)
    code = 0
    for c in candidats:
        if c['type'] in TYPES_AUTO and c['inactif_depuis_s'] >= delai:
            if not traiter(poste, journal, c, True, cache):
                code = 1
    return code


def usage():
    print('Usage :\n' + AIDE_COMMANDES.rstrip('\n'), file=sys.stderr)
    return 2


def commande_menage(args, ctx, poste=None):
    poste = poste or Poste()
    if args and args[0] == 'arreter':
        return commande_arreter(args[1:], ctx, poste)
    if args and args[0] == 'auto':
        return commande_auto(args[1:], ctx, poste)
    return commande_lister(args, ctx, poste)


COMMANDES = {'menage': commande_menage}
AIDE_COMMANDES = (
    '  menage [--json]                liste les démons et modèles gourmands inactifs\n'
    '  menage arreter ID…             arrête ou décharge les candidats (ID : voir menage)\n'
    '  menage auto                    ménage automatique (lancé toutes les 2 min par le minuteur)\n')
