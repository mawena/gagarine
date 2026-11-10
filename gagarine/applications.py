"""Plafond de mémoire des applications du bureau : `gagarine applications`.

GNOME lance chaque application dans un scope systemd de l'utilisateur (`app-gnome-<id>-<pid>.scope`, ou
`app-<lanceur>-<id>-<pid>.scope`, ou `snap.<snap>.<app>-<uuid>.scope`). Un dossier de préfixe
`~/.config/systemd/user/<préfixe>-.scope.d/` s'applique à tous les scopes de ce nom, déjà ouverts ou à venir (vérifié sur
systemd 255) : on y met `MemoryHigh`, qui freine l'application et la range dans le swap au-delà du plafond, sans l'arrêter.
"""
import json
import os
import re
import sys
from pathlib import Path

FICHIER_PLAFOND = '50-gagarine.conf'
SCOPE = re.compile(r'^(?P<prefixe>(?:app-.+|snap\..+))-(?P<suffixe>[^-]+)\.scope$')
DOSSIERS_BUREAU = ('~/.local/share/applications', '/usr/share/applications', '/usr/local/share/applications',
                   '/var/lib/snapd/desktop/applications', '/var/lib/flatpak/exports/share/applications',
                   '~/.local/share/flatpak/exports/share/applications')
PERMIS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:_.')
GO = 1024 ** 3


# --- Noms d'unités systemd ---------------------------------------------------------------------------------------------
def echapper(texte):
    """Échappement des noms d'unités de systemd (comme systemd-escape) : « - » devient \\x2d, « / » devient « - »."""
    res = []
    for i, c in enumerate(texte):
        if c == '/':
            res.append('-')
        elif c in PERMIS and not (c == '.' and i == 0):
            res.append(c)
        else:
            res.append(''.join(f'\\x{b:02x}' for b in c.encode()))
    return ''.join(res)


def deséchapper(texte):
    octets = bytearray()
    i = 0
    while i < len(texte):
        m = re.match(r'\\x([0-9a-fA-F]{2})', texte[i:])
        if m:
            octets.append(int(m.group(1), 16))
            i += 4
        else:
            octets += texte[i].encode()
            i += 1
    return octets.decode('utf-8', 'replace')


def prefixe_du_scope(nom):
    """Préfixe (avec son tiret final) d'un scope d'application, ou None si ce n'en est pas un."""
    m = SCOPE.match(nom)
    return m.group('prefixe') + '-' if m else None


def id_du_prefixe(prefixe):
    """Identifiant montré à l'utilisateur : le préfixe sans tiret final, déséchappé, sans « app-gnome- » (les applications
    lancées par GNOME sont les plus courantes)."""
    brut = deséchapper(prefixe[:-1])
    return brut[len('app-gnome-'):] if brut.startswith('app-gnome-') else brut


def prefixe_de_l_id(identifiant):
    """Préfixe d'un identifiant donné à la main : « app-… » et « snap.… » tels quels, le reste est une application GNOME."""
    if identifiant.startswith('snap.'):
        return echapper(identifiant) + '-'
    if identifiant.startswith('app-'):
        return 'app-' + echapper(identifiant[4:]) + '-'
    return 'app-gnome-' + echapper(identifiant) + '-'


def dossier_plafond(prefixe, env):
    return dossier_unites(env) / f'{prefixe}.scope.d'


def dossier_unites(env):
    base = env.get('XDG_CONFIG_HOME') or str(Path(env.get('HOME') or Path.home()) / '.config')
    return Path(base) / 'systemd/user'


# --- Nom lisible -------------------------------------------------------------------------------------------------------
def candidats_desktop(identifiant):
    """Noms de fichiers .desktop possibles pour un identifiant."""
    brut = identifiant[4:] if identifiant.startswith('app-') else identifiant
    if brut.startswith('snap.'):
        morceaux = brut.split('.')
        if len(morceaux) >= 3:
            return [f'{morceaux[1]}_{morceaux[2]}.desktop']
    return [f'{brut}.desktop']


def nom_lisible(identifiant, env, dossiers=None):
    """Champ Name= du fichier .desktop de l'application, ou l'identifiant s'il n'est pas trouvé."""
    if dossiers is None:
        extras = [d + '/applications' for d in (env.get('XDG_DATA_DIRS') or '').split(':') if d]
        dossiers = [Path(d).expanduser() if not d.startswith('~') else Path(env.get('HOME') or Path.home()) / d[2:]
                    for d in DOSSIERS_BUREAU + tuple(extras)]
    for nom in candidats_desktop(identifiant):
        for d in dossiers:
            try:
                texte = (Path(d) / nom).read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            dans = False
            for ligne in texte.splitlines():
                if ligne.startswith('['):
                    dans = ligne.strip() == '[Desktop Entry]'
                elif dans and ligne.startswith('Name='):
                    return ligne[5:].strip() or identifiant
    return identifiant


# --- État --------------------------------------------------------------------------------------------------------------
def lire_systemctl(ctx, *args):
    code, sortie, _ = ctx.executer(['systemctl', '--user', *args])
    return sortie if code == 0 else ''


def instances_en_cours(ctx):
    """[{'scope', 'memoire', 'plafond'}] : les scopes d'application ouverts (mémoire et plafond en octets, None si inconnus)."""
    sortie = lire_systemctl(ctx, 'show', 'app-*.scope', 'snap.*.scope', '-p', 'Id', '-p', 'MemoryCurrent',
                            '-p', 'MemoryHigh', '--no-pager')
    res = []
    for bloc in re.split(r'\n\s*\n', sortie):
        valeurs = dict(l.split('=', 1) for l in bloc.splitlines() if '=' in l)
        scope = valeurs.get('Id')
        if not scope or prefixe_du_scope(scope) is None:
            continue
        res.append({'scope': scope, 'memoire': octets(valeurs.get('MemoryCurrent')),
                    'plafond': octets(valeurs.get('MemoryHigh'))})
    return res


def octets(texte):
    """Nombre d'octets d'une propriété systemd ; None si absent, « infinity », « [not set] » ou 2^64 - 1."""
    try:
        v = int(texte)
    except (TypeError, ValueError):
        return None
    return None if v >= 2 ** 63 else v


def plafond_du_dossier(prefixe, env):
    """Plafond (octets) écrit par Gagarine pour ce préfixe, ou None."""
    try:
        texte = (dossier_plafond(prefixe, env) / FICHIER_PLAFOND).read_text()
    except OSError:
        return None
    m = re.search(r'^MemoryHigh=(\S+)$', texte, re.M)
    if not m:
        return None
    valeur = m.group(1)
    m = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([KMGT]?)', valeur)
    if not m:
        return None
    return int(float(m.group(1)) * {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': GO, 'T': 1024 ** 4}[m.group(2)])


def prefixes_plafonnes(env):
    """Préfixes qui ont un plafond de Gagarine, lancés ou non."""
    try:
        noms = sorted(os.listdir(dossier_unites(env)))
    except OSError:
        return []
    return [n[:-len('.scope.d')] for n in noms
            if n.endswith('-.scope.d') and (dossier_unites(env) / n / FICHIER_PLAFOND).is_file()]


def donnees_applications(ctx, dossiers_bureau=None):
    """{'applications': [{'id', 'nom', 'prefixe', 'instances', 'memoire_mo', 'plafond_go'}]}, les plus gourmandes d'abord."""
    apps = {}
    for inst in instances_en_cours(ctx):
        p = prefixe_du_scope(inst['scope'])
        a = apps.setdefault(p, {'instances': 0, 'octets': 0, 'plafond': None})
        a['instances'] += 1
        a['octets'] += inst['memoire'] or 0
        if inst['plafond'] is not None:
            a['plafond'] = max(a['plafond'] or 0, inst['plafond'])
    for p in prefixes_plafonnes(ctx.env):
        apps.setdefault(p, {'instances': 0, 'octets': 0, 'plafond': None})
        apps[p]['plafond'] = plafond_du_dossier(p, ctx.env) or apps[p]['plafond']
    entrees = []
    for p, a in apps.items():
        identifiant = id_du_prefixe(p)
        entrees.append({'id': identifiant, 'nom': nom_lisible(identifiant, ctx.env, dossiers_bureau), 'prefixe': p,
                        'instances': a['instances'], 'memoire_mo': round(a['octets'] / 1048576),
                        'plafond_go': round(a['plafond'] / GO, 2) if a['plafond'] else None})
    res = regrouper(entrees)
    res.sort(key=lambda a: (-a['memoire_mo'], a['id']))
    return {'applications': res}


def cle_de_regroupement(e):
    """Même application : même nom lisible ; sans fichier .desktop, même identifiant au préfixe « app- » près."""
    return (e['nom'] if e['nom'] != e['id'] else e['id'].removeprefix('app-')).casefold()


def regrouper(entrees):
    """Sous GNOME, une application peut tourner dans deux familles de scopes : celle du lanceur du bureau
    (`app-gnome-<id>-…`) et la sienne (`app-<id>-…`) ; Chrome et VS Code le font. On les réunit en une seule
    application (mémoire et instances additionnées) ; son plafond vaut pour toutes ses familles (`prefixes`, `ids`)."""
    groupes = {}
    for e in entrees:
        groupes.setdefault(cle_de_regroupement(e), []).append(e)
    res = []
    for membres in groupes.values():
        # identifiant principal : celui du lanceur du bureau (sans « app- » ni « snap. »), sinon le plus gourmand
        membres.sort(key=lambda e: (e['id'].startswith(('app-', 'snap.')), -e['memoire_mo'], e['id']))
        principal = membres[0]
        nom = next((e['nom'] for e in membres if e['nom'] != e['id']), principal['nom'])
        plafonds = [e['plafond_go'] for e in membres if e['plafond_go']]
        res.append({'id': principal['id'], 'nom': nom, 'prefixe': principal['prefixe'],
                    'instances': sum(e['instances'] for e in membres),
                    'memoire_mo': sum(e['memoire_mo'] for e in membres),
                    'plafond_go': max(plafonds) if plafonds else None,
                    'ids': [e['id'] for e in membres], 'prefixes': [e['prefixe'] for e in membres]})
    return res


def afficher(ctx, en_json):
    d = donnees_applications(ctx)
    if en_json:
        print(json.dumps(d, ensure_ascii=False))
        return 0
    if not d['applications']:
        print('Aucune application du bureau en cours ni plafonnée.')
        return 0
    print(f'{"Application":<34} {"Instances":>9} {"Mémoire":>10} {"Plafond":>9}  Identifiant')
    for a in d['applications']:
        plafond = f'{go_lisible(a["plafond_go"])} Go' if a['plafond_go'] else 'aucun'
        print(f'{a["nom"][:34]:<34} {a["instances"]:>9} {a["memoire_mo"]:>7} Mo {plafond:>9}  {a["id"]}')
    return 0


# --- Plafond -----------------------------------------------------------------------------------------------------------
def lire_go(texte):
    """Nombre de Go (virgule ou point) strictement positif ; ValueError sinon."""
    v = float(texte.replace(',', '.').lower().removesuffix('g').removesuffix('o').strip())
    if not 0 < v <= 10 ** 4 or v != v:
        raise ValueError('hors limites')
    return v


def go_texte(v):
    """Pour systemd (point décimal) : 1.5"""
    return f'{v:.2f}'.rstrip('0').rstrip('.')


def go_lisible(v):
    """Pour les messages, à la française : 1,5"""
    return go_texte(v).replace('.', ',')


def poser_plafond(identifiant, valeur, ctx):
    """Écrit (ou retire) le dossier de préfixe puis l'applique aux instances ouvertes. Code de sortie."""
    if not identifiant or '/' in identifiant or identifiant in ('.', '..') or '\n' in identifiant:
        print(f'identifiant d\'application invalide : « {identifiant} »', file=sys.stderr)
        return 2
    aucun = valeur.lower() in ('aucun', 'none', 'non', '0')
    if not aucun:
        try:
            go = lire_go(valeur)
        except ValueError:
            print(f'plafond invalide : « {valeur} » (un nombre de Go, ou « aucun »)', file=sys.stderr)
            return 2
    # Toutes les familles de scopes de l'application (voir regrouper) ; à défaut, celle que donne l'identifiant
    groupe = next((a for a in donnees_applications(ctx)['applications'] if identifiant in a['ids']), None)
    if groupe:
        prefixes, nom = groupe['prefixes'], groupe['id']
    else:
        prefixes, nom = [prefixe_de_l_id(identifiant)], identifiant
    propriete = 'MemoryHigh=infinity' if aucun else f'MemoryHigh={go_texte(go)}G'
    for prefixe in prefixes:
        dossier = dossier_plafond(prefixe, ctx.env)
        fichier = dossier / FICHIER_PLAFOND
        if aucun:
            try:
                fichier.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                print(f'impossible de retirer {fichier} : {e}', file=sys.stderr)
                return 1
            try:
                dossier.rmdir()   # seulement s'il est vide : le reste n'est pas à nous
            except OSError:
                pass
        else:
            try:
                dossier.mkdir(parents=True, exist_ok=True)
                fichier.write_text(f'# Écrit par « gagarine applications plafond » ; se retire par « gagarine applications '
                                   f'plafond {nom} aucun »\n[Scope]\n{propriete}\n')
            except OSError as e:
                print(f'impossible d\'écrire {fichier} : {e}', file=sys.stderr)
                return 1
    code, _, erreur = ctx.executer(['systemctl', '--user', 'daemon-reload'])
    if code != 0:
        print(f'daemon-reload a échoué : {erreur.strip()}', file=sys.stderr)
        return 1
    ouverts = [i['scope'] for i in instances_en_cours(ctx) if prefixe_du_scope(i['scope']) in prefixes]
    echecs = 0
    for scope in ouverts:
        code, _, erreur = ctx.executer(['systemctl', '--user', 'set-property', '--runtime', scope, propriete])
        if code != 0:
            echecs += 1
            print(f'{scope} : {erreur.strip()}', file=sys.stderr)
    quand = (f'appliqué à {len(ouverts)} instance{"s" if len(ouverts) > 1 else ""} ouverte{"s" if len(ouverts) > 1 else ""}'
             if ouverts else 'applicable au prochain lancement (application non ouverte)')
    if aucun:
        print(f'Plafond de « {nom} » retiré ; {quand}.')
    else:
        print(f'Plafond de « {nom} » : {go_lisible(go)} Go ({quand}). Au-delà, l\'application est freinée et rangée dans '
              f'le swap, jamais arrêtée.')
    return 1 if echecs else 0


def commande_applications(args, ctx):
    en_json = '--json' in args
    reste = [a for a in args if a != '--json']
    if not reste:
        return afficher(ctx, en_json)
    if reste[0] == 'plafond' and len(reste) == 3:
        return poser_plafond(reste[1], reste[2], ctx)
    print('usage : gagarine applications [--json] | gagarine applications plafond ID GO|aucun', file=sys.stderr)
    return 2


COMMANDES = {'applications': commande_applications}

AIDE_COMMANDES = """\
  applications [--json]          applications du bureau (Chrome, VS Code…) : mémoire, plafond
  applications plafond ID GO|aucun
                                 plafonne la mémoire d'une application (MemoryHigh : freinée et rangée dans le swap
                                 au-delà de GO, jamais arrêtée) ; « aucun » retire le plafond
"""
