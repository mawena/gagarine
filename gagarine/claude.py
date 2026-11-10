"""Hook PreToolUse des sessions Claude Code : `gagarine hook-claude`.

Il reprend le hook shell du poste (rappel quand le poste est chargé, événements des 30 dernières minutes, service arrêté)
et y ajoute le refus des travaux lourds lancés hors de la file d'attente (réglage `claude_bloquer`).

Règle d'or : ne jamais planter et ne jamais bloquer à tort. Une erreur interne, une commande qu'on ne sait pas analyser,
une entrée qui n'est pas du JSON : sortie vide, code 0.
"""
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from gagarine.journal import fichiers_journaux

# ======================================================================================================================
# Analyse d'une commande Bash
# ======================================================================================================================
AFFECTATION = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\+?=')
MOTS_DE_CONTROLE = frozenset({'if', 'then', 'else', 'elif', 'do', 'while', 'until', '!', '{', '}', 'exec', 'command',
                              'builtin', 'nohup', 'setsid', 'unbuffer'})
SHELLS = frozenset({'bash', 'sh', 'zsh', 'dash', 'ksh'})
SANS_TRAVAIL = frozenset({'--help', '-h', '--version', '-V'})


class Jeton:
    """Un mot (texte sans guillemets) ou un opérateur, avec sa place dans la commande d'origine."""
    __slots__ = ('genre', 'texte', 'debut', 'fin')

    def __init__(self, genre, texte, debut, fin):
        self.genre, self.texte, self.debut, self.fin = genre, texte, debut, fin

    def __repr__(self):
        return f'Jeton({self.genre}, {self.texte!r})'


def fin_substitution(s, i):
    """Position de la parenthèse fermante qui répond à la « ( » juste avant i, en tenant compte des guillemets et des
    parenthèses imbriquées. ValueError si elle manque."""
    prof = 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise ValueError('guillemet simple ouvert')
            i = j + 1
            continue
        if c == '"':
            i += 1
            while i < n and s[i] != '"':
                if s[i] == '\\':
                    i += 1
                elif s[i] == '$' and s[i + 1:i + 2] == '(':
                    i = fin_substitution(s, i + 2)
                i += 1
            if i >= n:
                raise ValueError('guillemet ouvert')
            i += 1
            continue
        if c == '(':
            prof += 1
        elif c == ')':
            prof -= 1
            if prof == 0:
                return i
        i += 1
    raise ValueError('parenthèse ouverte')


def decouper(s):
    """(jetons, substitutions) de la commande s. Les substitutions sont des (début, fin) de contenu de `$( )`, `<( )`,
    `>( )` ou de guillemets inversés, à analyser à part. Lève ValueError si les guillemets ne se ferment pas."""
    jetons = []
    subs = []
    n = len(s)
    i = 0
    mot = None   # [texte, début]

    def clore(fin):
        nonlocal mot
        if mot is not None:
            jetons.append(Jeton('mot', mot[0], mot[1], fin))
            mot = None

    def ajouter(texte, pos):
        nonlocal mot
        if mot is None:
            mot = ['', pos]
        mot[0] += texte

    while i < n:
        c = s[i]
        if c in ' \t':
            clore(i)
            i += 1
        elif c == '\\':
            if s[i + 1:i + 2] == '\n':   # continuation de ligne
                clore(i)
                i += 2
            else:
                ajouter(s[i + 1:i + 2], i)
                i += 2
        elif c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise ValueError('guillemet simple ouvert')
            ajouter(s[i + 1:j], i)
            i = j + 1
        elif c == '"':
            ajouter('', i)
            i += 1
            while i < n and s[i] != '"':
                if s[i] == '\\' and i + 1 < n:
                    if s[i + 1] in '"\\$`':
                        ajouter(s[i + 1], i)
                    else:
                        ajouter(s[i:i + 2], i)
                    i += 2
                elif s[i] == '$' and s[i + 1:i + 2] == '(':
                    j = fin_substitution(s, i + 2)
                    subs.append((i + 2, j))
                    ajouter(s[i:j + 1], i)
                    i = j + 1
                elif s[i] == '`':
                    j = s.find('`', i + 1)
                    if j < 0:
                        raise ValueError('guillemet inversé ouvert')
                    subs.append((i + 1, j))
                    ajouter(s[i:j + 1], i)
                    i = j + 1
                else:
                    ajouter(s[i], i)
                    i += 1
            if i >= n:
                raise ValueError('guillemet ouvert')
            i += 1
        elif c == '$' and s[i + 1:i + 2] == '(':
            j = fin_substitution(s, i + 2)
            subs.append((i + 2, j))
            ajouter(s[i:j + 1], i)
            i = j + 1
        elif c in '<>' and s[i + 1:i + 2] == '(':
            j = fin_substitution(s, i + 2)
            subs.append((i + 2, j))
            clore(i)
            jetons.append(Jeton('mot', s[i:j + 1], i, j + 1))
            i = j + 1
        elif c == '`':
            j = s.find('`', i + 1)
            if j < 0:
                raise ValueError('guillemet inversé ouvert')
            subs.append((i + 1, j))
            ajouter(s[i:j + 1], i)
            i = j + 1
        elif c == '#' and mot is None:   # commentaire jusqu'à la fin de la ligne
            j = s.find('\n', i)
            i = n if j < 0 else j
        elif c in '\n;&|()<>':
            suivant = s[i + 1:i + 2]
            if c in '<>' or (c == '&' and suivant == '>'):   # redirection : « 2>&1 », « &>f », « >>f », « <<<x »
                if mot is not None and mot[0].isdigit():
                    debut = mot[1]   # le numéro de descripteur fait partie de la redirection
                    mot = None
                else:
                    clore(i)
                    debut = i
                j = i + 1
                while j < n and s[j] in '<>&|' and j - i < 3 and not (s[j] == '&' and s[j - 1] == '&'):
                    j += 1
                jetons.append(Jeton('redir', s[debut:j], debut, j))
            else:
                clore(i)
                op = s[i:i + 2] if s[i:i + 2] in ('&&', '||', '|&', ';;') else c
                jetons.append(Jeton('op', op, i, i + len(op)))
                j = i + len(op)
            i = j
        else:
            ajouter(c, i)
            i += 1
    clore(n)
    return jetons, subs


def segments(jetons):
    """Les commandes simples : listes de mots, séparées par les opérateurs ; les redirections et leur cible sont
    retirées. Chaque segment : (mots, début, fin) avec la place dans la commande d'origine."""
    res = []
    courant = []
    ignorer = False
    for j in jetons:
        if j.genre == 'op':
            if courant:
                res.append((courant[0].debut, courant[-1].fin, [m.texte for m in courant]))
            courant = []
            ignorer = False
        elif j.genre == 'redir':
            ignorer = True   # la cible de la redirection n'est pas un mot de la commande
        elif ignorer:
            ignorer = False   # cible de la redirection
        else:
            courant.append(j)
    if courant:
        res.append((courant[0].debut, courant[-1].fin, [m.texte for m in courant]))
    return res


def nom_programme(mot):
    """Nom du programme : sans dossier, ni numéro de version de python (python3.12 → python)."""
    nom = os.path.basename(mot)
    if re.fullmatch(r'python[0-9.]*', nom):
        return 'python'
    return nom


def sauter_options(mots, avec_valeur=(), courtes_collees=()):
    """mots sans les options en tête. `avec_valeur` : options qui prennent le mot suivant ; `courtes_collees` : options
    courtes dont la valeur peut être collée (-n10)."""
    i = 0
    while i < len(mots):
        m = mots[i]
        if m == '--':
            return mots[i + 1:]
        if not m.startswith('-') or m == '-':
            break
        if m in avec_valeur:
            i += 2
        else:
            i += 1
    return mots[i:]


def retirer_prefixes(mots):
    """Les mots de la vraie commande, sans les affectations A=b, ni env, nice, ionice, timeout, time, sudo…"""
    mots = list(mots)
    for _ in range(20):
        if not mots:
            return mots
        premier = mots[0]
        nom = nom_programme(premier)
        if AFFECTATION.match(premier) or nom in MOTS_DE_CONTROLE:
            mots = mots[1:]
        elif nom == 'env':
            mots = sauter_options(mots[1:], ('-u', '-C', '-S', '--unset', '--chdir'))
        elif nom == 'nice':
            mots = sauter_options(mots[1:], ('-n', '--adjustment'))
        elif nom == 'ionice':
            mots = sauter_options(mots[1:], ('-c', '-n', '-p', '-P', '-u', '--class', '--classdata'))
        elif nom == 'timeout':
            reste = sauter_options(mots[1:], ('-s', '-k', '--signal', '--kill-after'))
            mots = reste[1:]   # la durée
        elif nom == 'time':
            mots = sauter_options(mots[1:], ('-f', '-o', '--format', '--output'))
        elif nom in ('sudo', 'doas'):
            mots = sauter_options(mots[1:], ('-u', '-g', '-h', '-p', '-C', '-D', '-R', '-T', '--user', '--group'))
        elif nom == 'stdbuf':
            mots = sauter_options(mots[1:], ('-i', '-o', '-e'))
        elif nom == 'taskset':
            mots = sauter_options(mots[1:], ('-c',))[1:]
        elif nom == 'chrt':
            mots = sauter_options(mots[1:])[1:]
        elif nom == 'xargs':
            mots = sauter_options(mots[1:], ('-n', '-P', '-I', '-L', '-s', '-d', '-E', '-a'))
        elif nom in ('npx', 'pnpx', 'bunx'):
            mots = sauter_options(mots[1:], ('-p', '--package'))
        else:
            return mots
    return mots


# --- Fiches des travaux lourds : (nom proposé, Go de RAM, cœurs) ---------------------------------------------------
def fiche(nom, go, coeurs, raison):
    return {'nom': nom, 'go': go, 'coeurs': coeurs, 'raison': raison}


def sous_commande(mots, avec_valeur=()):
    """Premier mot qui n'est pas une option (cargo +nightly build : « +toolchain » est sauté)."""
    reste = sauter_options(mots, avec_valeur)
    if reste and reste[0].startswith('+'):
        reste = reste[1:]
    return reste[0] if reste else None


def lourd_python(args):
    if 'pytest' in args[:1] or ('-m' in args and args.index('-m') + 1 < len(args)):
        module = args[args.index('-m') + 1] if '-m' in args else 'pytest'
        if module == 'pytest':
            return fiche('tests', 2, 2, 'python -m pytest')
        if module == 'unittest':
            return fiche('tests', 2, 2, 'python -m unittest')
    return None


ALIAS_CARGO = {'b': 'build', 't': 'test', 'c': 'check', 'r': 'run'}


def lourd_cargo(args):
    sc = sous_commande(args, ('-Z', '--color', '--config', '-C', '--manifest-path'))
    sc = ALIAS_CARGO.get(sc, sc)
    if sc in ('build', 'test', 'check', 'clippy', 'run', 'bench'):
        return fiche(f'cargo-{sc}', 4, 4, f'cargo {sc}')
    return None


def lourd_gradle(args):
    if any(a in ('--stop', '--status') for a in args):
        return None
    return fiche('gradle', 4, 4, 'gradle')


def lourd_flutter(args):
    sc = sous_commande(args)
    if sc in ('build', 'test'):
        return fiche(f'flutter-{sc}', 3, 4, f'flutter {sc}')
    return None


CIBLES_MAKE = {'test': ('tests', 2, 2), 'tests': ('tests', 2, 2), 'check': ('tests', 2, 2), 'deb': ('make-deb', 2, 4),
               'build': ('make-build', 2, 4), 'all': ('make-all', 2, 4)}


def lourd_make(args):
    cibles = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--':
            cibles += [x for x in args[i + 1:] if '=' not in x]
            break
        if a in ('-n', '--just-print', '--dry-run', '--recon', '-p', '-q', '--question', '--print-data-base'):
            return None
        if a in ('-C', '-f', '-I', '-o', '-W', '-l', '--directory', '--file', '--makefile', '--include-dir',
                 '--old-file', '--what-if', '--load-average'):
            i += 2
            continue
        if a == '-j' and i + 1 < len(args) and args[i + 1].isdigit():
            i += 2
            continue
        if a.startswith('-') or '=' in a:
            i += 1
            continue
        cibles.append(a)
        i += 1
    if not cibles:
        return fiche('make', 2, 4, 'make sans cible')
    for c in cibles:
        if c in CIBLES_MAKE:
            nom, go, coeurs = CIBLES_MAKE[c]
            return fiche(nom, go, coeurs, f'make {c}')
    return None


def lourd_npm(prog, args):
    val = ('--prefix', '-C', '--cwd', '--workspace', '-w', '--filter', '-F', '--dir')
    reste = sauter_options(args, val)
    if not reste:
        return None
    if reste[0] in ('exec', 'dlx', 'x'):
        return lourd_commande(retirer_prefixes(sauter_options(reste[1:], ('-p', '--package'))))
    script = None
    if reste[0] in ('run', 'run-script', 'run-s', 'rum'):
        script = sous_commande(reste[1:], val)
    elif reste[0] in ('test', 't', 'tst') and prog == 'npm':
        script = 'test'
    elif prog in ('yarn', 'pnpm') and reste[0] in ('build', 'test'):
        script = reste[0]
    if script and (script in ('build', 'test') or script.startswith(('build:', 'test:'))):
        return fiche(f'{script.split(":")[0]}-{prog}', 3, 2, f'{prog} run {script}')
    return None


def lourd_vite(args):
    return fiche('build-vite', 3, 2, 'vite build') if sous_commande(args) == 'build' else None


def lourd_soffice(args):
    if any(a == '--convert-to' or a.startswith('--convert-to=') for a in args):
        return fiche('conversion-documents', 2, 1, 'conversion de documents')
    return None


def lourd_ollama(args):
    return fiche('modele-ollama', 8, 4, 'ollama run') if sous_commande(args) == 'run' else None


def lourd_lms(args):
    return fiche('modele-lmstudio', 8, 4, 'lms load') if sous_commande(args) == 'load' else None


def lourd_docker(args):
    mots = sauter_options(args, ('-H', '--host', '-c', '--context', '--config', '-l', '--log-level'))
    if mots[:1] == ['build'] or mots[:2] == ['buildx', 'build'] or mots[:2] == ['compose', 'build'] \
            or mots[:2] == ['image', 'build']:
        return fiche('docker-build', 3, 4, 'docker build')
    return None


def lourd_go(args):
    sc = sous_commande(args)
    return fiche(f'go-{sc}', 2, 4, f'go {sc}') if sc in ('build', 'test') else None


def lourd_dotnet(args):
    sc = sous_commande(args)
    return fiche(f'dotnet-{sc}', 3, 4, f'dotnet {sc}') if sc in ('build', 'test') else None


FICHES = {
    'pytest': lambda a: fiche('tests', 2, 2, 'pytest'),
    'py.test': lambda a: fiche('tests', 2, 2, 'pytest'),
    'python': lourd_python,
    'cargo': lourd_cargo,
    'gradle': lourd_gradle,
    'gradlew': lourd_gradle,
    'flutter': lourd_flutter,
    'make': lourd_make,
    'gmake': lourd_make,
    'vite': lourd_vite,
    'ffmpeg': lambda a: fiche('ffmpeg', 2, 4, 'ffmpeg'),
    'soffice': lourd_soffice,
    'libreoffice': lourd_soffice,
    'whisper': lambda a: fiche('whisper', 4, 4, 'whisper'),
    'whisper-cli': lambda a: fiche('whisper', 4, 4, 'whisper'),
    'ollama': lourd_ollama,
    'lms': lourd_lms,
    'docker': lourd_docker,
    'go': lourd_go,
    'mvn': lambda a: fiche('maven', 3, 4, 'mvn'),
    'mvnw': lambda a: fiche('maven', 3, 4, 'mvn'),
    'dotnet': lourd_dotnet,
    'xvfb-run': lambda a: fiche('xvfb', 2, 2, 'xvfb-run'),
}


def deja_dans_la_file(mots):
    """gagarine file … -- COMMANDE, file-attente … -- COMMANDE (même sans le « -- » : seule la file les lance)."""
    nom = nom_programme(mots[0])
    if nom == 'file-attente':
        return True
    return nom == 'gagarine' and 'file' in mots[1:3]


def lourd_commande(mots, profondeur=0):
    """La fiche du travail lourd lancé par cette commande simple (après les préfixes), ou None."""
    if not mots:
        return None
    if deja_dans_la_file(mots):
        return None
    if any(m in SANS_TRAVAIL for m in mots[1:]):
        return None
    nom = nom_programme(mots[0])
    if nom in SHELLS and profondeur < 5:
        for i, m in enumerate(mots[1:], 1):
            if m.startswith('-') and not m.startswith('--') and 'c' in m[1:]:
                if i + 1 < len(mots):
                    return analyser_interne(mots[i + 1], profondeur + 1)
                return None
        return None
    if nom in ('npm', 'pnpm', 'yarn', 'bun'):
        return lourd_npm(nom, mots[1:])
    f = FICHES.get(nom)
    if f is None:
        return None
    return f(mots[1:])


def analyser_interne(texte, profondeur):
    """Premier travail lourd d'un texte de commande (argument de bash -c) ; sans positions."""
    for f in trouver(texte, profondeur):
        return f
    return None


def trouver(commande, profondeur=0, decalage=0):
    """Les travaux lourds de la commande : liste de fiches avec 'debut' et 'fin' (place dans la commande d'origine, de
    la commande simple ; pour un shell -c, de la commande simple qui le lance). Lève ValueError si la commande
    est illisible."""
    if profondeur > 5:
        return []
    commande = retirer_heredocs(commande)
    jetons, subs = decouper(commande)
    trouves = []
    for debut, fin in subs:
        for f in trouver(commande[debut:fin], profondeur + 1, decalage + debut):
            trouves.append(f)
    for debut, fin, mots in segments(jetons):
        f = lourd_commande(retirer_prefixes(mots), profondeur)
        if f:
            f = dict(f, debut=decalage + debut, fin=decalage + fin)
            trouves.append(f)
    # les substitutions imbriquées ont déjà leur propre place ; si elles chevauchent un segment trouvé, on garde le plus
    # large (celui qui contient l'autre) pour ne pas envelopper deux fois
    trouves.sort(key=lambda f: (f['debut'], -f['fin']))
    res = []
    for f in trouves:
        if res and f['debut'] < res[-1]['fin']:
            continue
        res.append(f)
    return res


def retirer_heredocs(commande):
    """Retire le corps des documents ici (<<EOF … EOF) : ce n'est pas du code à exécuter par le shell de l'agent."""
    if '<<' not in commande:
        return commande
    lignes = commande.split('\n')
    sortie = []
    fin = None
    for ligne in lignes:
        if fin is not None:
            if ligne.strip() == fin:
                fin = None
            continue
        sortie.append(ligne)
        m = re.search(r'<<-?\s*([\'"]?)([A-Za-z_][A-Za-z0-9_]*)\1', ligne)
        if m and not ligne.rstrip().endswith('<<<'):
            fin = m.group(2)
    return '\n'.join(sortie)


def ram_apprise(nom, appris):
    """La RAM (Go) que la file a apprise pour ce travail : 1,2 × le plus haut des derniers pics, arrondi au 0,5 Go
    supérieur ; None si la file ne le connaît pas."""
    try:
        pic = max(appris[nom]['pics_go'])
        return math.ceil(round(1.2 * pic * 2, 6)) / 2
    except (KeyError, TypeError, ValueError):
        return None


def go_texte(x):
    return str(int(x)) if float(x).is_integer() else str(x)


def commande_corrigee(commande, travaux, appris=None):
    """La commande réécrite : chaque travail lourd passe par `gagarine file "<nom>" <Go> <cœurs> -- …`. Si la file
    connaît déjà le travail (`appris`, la mémoire des travaux), elle propose la RAM apprise à la place de la valeur fixe."""
    res = commande
    for f in sorted(travaux, key=lambda f: -f['debut']):
        segment = res[f['debut']:f['fin']]
        apprise = ram_apprise(f['nom'], appris or {})
        if apprise is not None:
            f = {**f, 'go': go_texte(apprise)}
        if AFFECTATION.match(segment):
            segment = 'env ' + segment   # la file lance un programme : les affectations passent par env
        res = res[:f['debut']] + f'gagarine file "{f["nom"]}" {f["go"]} {f["coeurs"]} -- {segment}' + res[f['fin']:]
    return res


def verdict(commande, appris=None):
    """{'lourd': bool, 'raison', 'travaux', 'corrigee'} ; ne bloque jamais en cas de doute (commande illisible)."""
    try:
        travaux = trouver(commande)
    except (ValueError, IndexError, RecursionError):
        return {'lourd': False, 'raison': 'commande non analysable', 'travaux': [], 'corrigee': None}
    if not travaux:
        return {'lourd': False, 'raison': 'rien de lourd', 'travaux': [], 'corrigee': None}
    return {'lourd': True, 'raison': ' ; '.join(t['raison'] for t in travaux), 'travaux': travaux,
            'corrigee': commande_corrigee(commande, travaux, appris)}


def motif_du_refus(v):
    return (f'Travail lourd lancé hors de la file d\'attente du poste ({v["raison"]}). Deux travaux lourds en même temps '
            f'ont fait redémarrer le PC le 02/11, et un test lancé hors de la file a pris 9 Go le 10/11. Relance la commande '
            f'par la file : {v["corrigee"]}\n'
            f'Remplace le nom, la RAM (Go) et les cœurs par ce qu\'il lui faut vraiment : la file enferme le travail dans '
            f'max(2 × annoncé, annoncé + 2 Go) et l\'arrête s\'il les dépasse ; elle attend son tour si le poste est chargé '
            f'(état : gagarine file --etat). Dis à l\'utilisateur pourquoi le travail attend. Si cette commande n\'est '
            f'pas un travail lourd, signale-le à l\'utilisateur : le blocage se règle par claude_bloquer dans '
            f'/etc/gagarine.conf.')


# ======================================================================================================================
# Rappels sur l'état du poste (même texte que l'ancien hook shell)
# ======================================================================================================================
def lire_ram_disponible(chemin='/proc/meminfo'):
    try:
        for ligne in Path(chemin).read_text().splitlines():
            if ligne.startswith('MemAvailable:'):
                return int(ligne.split()[1]) / 1048576
    except (OSError, ValueError, IndexError):
        pass
    return None


def lire_charge(chemin='/proc/loadavg'):
    try:
        return float(Path(chemin).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def travaux_en_cours(dossier):
    """Noms des travaux de la file dont le processus tourne (tickets *.json avec pid et nom)."""
    noms = []
    for t in sorted(Path(dossier).glob('*.json')):
        try:
            d = json.loads(t.read_text())
            pid = d.get('pid')
            if not pid:
                continue
            os.kill(int(pid), 0)
            noms.append(str(d.get('nom') or '?'))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return noms


def message_charge(ram, charge, coeurs, travaux):
    if ram is None or charge is None:
        return ''
    if not (charge > 0.75 * coeurs or ram < 4 or travaux):
        return ''
    liste = f' ; travaux lourds dans la file : {" ; ".join(travaux)}' if travaux else ''
    return (f'Poste chargé : {ram:.1f} Go de RAM disponibles, charge {charge:.2f} pour {coeurs} cœurs{liste}. '
            'Si cette commande lance un travail lourd (rendu ou montage vidéo, voix de synthèse, conversion de documents, '
            'compilation, longue suite de tests, modèle local…), ne la lance pas en parallèle : passe par la file '
            'd\'attente de Gagarine (gagarine file "<nom>" <Go de RAM> <cœurs libres> -- <commande>, état : gagarine file '
            '--etat ; file-attente reste un raccourci) et dis à l\'utilisateur pourquoi le travail attend. Annonce la RAM '
            'qu\'il lui faut vraiment : la file l\'enferme dans une limite de max(2 × annoncé, annoncé + 2 Go) et l\'arrête '
            's\'il la dépasse. Les commandes légères (lecture, git, recherche…) ne sont pas concernées.')


def fin_de_fichier(chemin, lignes=30):
    """Les dernières lignes d'un fichier, sans le lire en entier."""
    with open(chemin, 'rb') as f:
        f.seek(0, os.SEEK_END)
        taille = f.tell()
        f.seek(max(0, taille - 65536))
        return f.read().decode('utf-8', 'replace').splitlines()[-lignes:]


def nombre_lisible(x):
    return '?' if x is None else str(x)


def phrase_evenement(e):
    action = e.get('action')
    nom = e.get('nom') or '?'
    texte = f'{str(e.get("date", ""))[11:16]} UTC : « {nom} »'
    if action == 'fige':
        texte += f' FIGÉ par Gagarine (RAM à {nombre_lisible(e.get("ram"))} %)'
    elif action == 'arrete':
        texte += f' ARRÊTÉ par Gagarine ({e.get("raison") or "RAM pleine"})'
    else:
        texte += (f' ARRÊTÉ par la file d\'attente (dépassement de sa limite de {nombre_lisible(e.get("limite"))} Go, '
                  f'annoncé {nombre_lisible(e.get("declare"))} Go)')
    if e.get('pid'):
        texte += f', processus {e["pid"]}'
    return texte + ' ; commande : ' + re.sub(r'\s+', ' ', str(e.get('commande') or ''))[:160]


def message_evenements(journaux, maintenant=None):
    """Événements fige/arrete/limite de la dernière demi-heure (au plus 3), ou ''."""
    depuis = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime((maintenant or time.time()) - 1800))
    trouves = []
    for j in journaux:
        try:
            if os.path.getsize(j) == 0:
                continue
            lignes = fin_de_fichier(j)
        except OSError:
            continue
        for ligne in lignes:
            try:
                e = json.loads(ligne)
            except ValueError:
                continue
            if (isinstance(e, dict) and e.get('action') in ('fige', 'arrete', 'limite')
                    and str(e.get('date', ''))[:19] >= depuis):
                trouves.append(e)
    if not trouves:
        return ''
    trouves.sort(key=lambda e: str(e.get('date', '')))
    phrases = ' — '.join(phrase_evenement(e) for e in trouves[-3:])
    return (f'Garde-RAM du poste (Gagarine et file d\'attente), dernière demi-heure : {phrases}. Ces programmes remplissaient '
            'la RAM du poste (elle a figé le PC le 02/11 et le 10/11). Si l\'un d\'eux vient de cette session, ne le '
            'relance pas tel quel : cherche et corrige la cause (fuite de mémoire, données trop grosses), puis relance-le '
            'via gagarine file en annonçant la RAM qu\'il lui faut vraiment ; un programme figé se reprend avec gagarine '
            'reprendre <pid> ou s\'arrête avec gagarine arreter <pid> (état : gagarine etat). Dis-le à l\'utilisateur.')


MESSAGE_SERVICE = ('Le service Gagarine ne tourne pas (systemctl status gagarine) : préviens l\'utilisateur, le poste n\'est '
                   'plus protégé contre une RAM pleine.')


def message_poste(env, executer, maintenant=None):
    """Les rappels à donner à Claude (texte, éventuellement vide)."""
    from gagarine.file import dossier_tickets
    morceaux = []
    m = message_charge(lire_ram_disponible(), lire_charge(), os.cpu_count() or 1, travaux_en_cours(dossier_tickets(env)))
    if m:
        morceaux.append(m)
    m = message_evenements(fichiers_journaux(env), maintenant)
    if m:
        morceaux.append(m)
    if executer(['systemctl', 'is-active', '--quiet', 'gagarine'])[0] != 0:
        morceaux.append(MESSAGE_SERVICE)
    return ' '.join(morceaux)


# ======================================================================================================================
# Commande
# ======================================================================================================================
def sortie_hook(contexte=None, refus=None):
    """Le JSON de réponse d'un hook PreToolUse (None s'il n'y a rien à dire)."""
    if refus:
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                       'permissionDecisionReason': refus}}
    if contexte:
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': contexte}}
    return None


def decider(entree, bloquer, env, executer):
    """La réponse du hook (dict ou None) pour l'entrée JSON de Claude Code."""
    if bloquer and isinstance(entree, dict) and entree.get('tool_name') == 'Bash':
        commande = (entree.get('tool_input') or {}).get('command')
        if isinstance(commande, str) and commande.strip():
            v = verdict(commande)
            if v['lourd']:
                try:   # la file connaît peut-être déjà le travail : on propose la RAM apprise
                    from gagarine.file import lire_travaux
                    v = verdict(commande, lire_travaux(env))
                except Exception:
                    pass
                return sortie_hook(refus=motif_du_refus(v))
    return sortie_hook(contexte=message_poste(env, executer))


def tester(commande, env=None):
    appris = None
    if env is not None:
        try:
            from gagarine.file import lire_travaux
            appris = lire_travaux(env)
        except Exception:
            pass
    v = verdict(commande, appris)
    if v['lourd']:
        print(f'lourd : {v["raison"]}')
        print(f'commande corrigée : {v["corrigee"]}')
    else:
        print(f'libre : {v["raison"]}')
    return 0


def commande_hook_claude(args, ctx):
    if args[:1] == ['--tester']:
        if len(args) != 2:
            print('usage : gagarine hook-claude --tester "<commande>"', file=sys.stderr)
            return 2
        return tester(args[1], ctx.env)
    try:
        from gagarine.reglages import charger_reglages
        try:
            entree = json.loads(sys.stdin.read() or 'null')
        except ValueError:
            entree = None
        bloquer = charger_reglages(ctx.config, ctx.env)[0].claude_bloquer
        reponse = decider(entree, bloquer, ctx.env, ctx.executer)
        if reponse:
            print(json.dumps(reponse, ensure_ascii=False))
    except Exception:   # jamais de blocage ni d'erreur sur un hook : sortie vide
        pass
    return 0


COMMANDES = {'hook-claude': commande_hook_claude}

AIDE_COMMANDES = """\
  hook-claude [--tester "CMD"]   hook PreToolUse des sessions Claude Code (JSON sur l'entrée standard) : rappels sur l'état
                                 du poste et refus des travaux lourds lancés hors de la file (réglage claude_bloquer) ;
                                 --tester donne le verdict sur une commande
"""
