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

    def lancer(self, commande, delai=15):
        """(code, sortie standard) ; (None, '') si la commande est absente ou trop lente."""
        try:
            r = subprocess.run(commande, capture_output=True, text=True, timeout=delai)
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


def lms_tourne(processus, lignes):
    """LM Studio (application ou service llmster) tourne déjà : sinon toute commande `lms` en démarrerait un."""
    for pid, p in processus.items():
        if p.nom == 'lms':
            continue
        ligne = lignes(pid).lower()
        if (p.nom.lower() in ('lm-studio', 'lm studio', 'llmster') or '/.lmstudio/' in ligne
                or 'lm-studio' in ligne or 'lm studio' in ligne or 'llmster' in ligne):
            yield pid


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


def detecter(poste):
    """Candidats actuels, sans l'inactivité : [{id, type, nom, pid, memoire_mo, action, cpu, signe, demarrage}]."""
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
    trouves += candidats_lmstudio(poste, processus, ligne)
    trouves += candidats_ollama(poste, processus)
    return trouves


def somme_cpu(poste, pids):
    temps = [poste.cpu(pid) for pid in pids]
    return sum(t for t in temps if t is not None)


def candidats_lmstudio(poste, processus, ligne):
    pids = list(lms_tourne(processus, ligne))
    if not pids:
        return []   # LM Studio ne tourne pas : on ne lance surtout pas `lms` (il démarrerait un service de ~360 Mo)
    code, sortie = poste.lancer([poste.chemin_lms(), 'ps', '--json'])
    if code != 0:
        return []
    modeles = modeles_lmstudio(sortie)
    total = sum(t for _, t in modeles)
    if not modeles or total < MEMOIRE_MIN:
        return []
    return [{'id': 'lmstudio', 'type': 'lmstudio', 'nom': 'LM Studio : ' + ', '.join(n for n, _ in modeles),
             'pid': None, 'memoire_mo': total // Mo, 'action': 'decharger', 'cpu': somme_cpu(poste, pids),
             'signe': None, 'demarrage': None}]


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


def lister(poste, fichier_suivi):
    """Candidats complets (avec inactif_depuis_s et auto) ; le suivi est mis à jour au passage."""
    candidats = detecter(poste)
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


def decharger(poste, c):
    """Décharge les modèles du candidat ; vrai si la commande ou l'appel a réussi."""
    if c['type'] == 'lmstudio':
        # Revérifié juste avant : `lms` ne doit jamais démarrer LM Studio lui-même.
        if not list(lms_tourne(poste.processus(), poste.ligne_commande)):
            return False
        code, _ = poste.lancer([poste.chemin_lms(), 'unload', '--all'])
        return code == 0
    if c['type'] == 'ollama':
        modele = c['id'].split(':', 1)[1]
        return poste.api('POST', '/api/generate', {'model': modele, 'keep_alive': 0}, delai=10) is not None
    return False


def appliquer(poste, c):
    return arreter_processus(poste, c['pid']) if c['action'] == 'arreter' else decharger(poste, c)


def traiter(poste, journal, c, auto):
    """Arrête ou décharge un candidat et l'écrit au journal. Vrai si c'est fait."""
    ok = appliquer(poste, c)
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
    return reglages, dossier, dossier / 'menage.json', Journal(dossier, heure=poste.maintenant, sortie=False)


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
    reglages, _, suivi, _ = contexte_menage(ctx, poste)
    candidats = lister(poste, suivi)
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
    _, _, suivi, journal = contexte_menage(ctx, poste)
    par_id = {c['id']: c for c in lister(poste, suivi)}
    code = 0
    for identifiant in args:
        c = par_id.get(identifiant)
        if c is None:
            print(f"« {identifiant} » n'est pas un candidat au ménage (voir gagarine menage)")
            code = 1
        elif not traiter(poste, journal, c, False):
            code = 1
    return code


def commande_auto(args, ctx, poste):
    if args:
        return usage()
    reglages, _, suivi, journal = contexte_menage(ctx, poste)
    candidats = lister(poste, suivi)   # le suivi est tenu à jour même quand le ménage automatique est coupé
    if not reglages.menage_auto:
        return 0
    delai = delai_auto(reglages, poste)
    code = 0
    for c in candidats:
        if c['type'] in TYPES_AUTO and c['inactif_depuis_s'] >= delai:
            if not traiter(poste, journal, c, True):
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
