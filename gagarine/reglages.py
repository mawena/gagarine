"""Réglages de Gagarine : lecture, validation et écriture sur place de gagarine.conf."""
import os
import re
import tempfile
from configparser import ConfigParser, Error as ErreurConfig
from dataclasses import dataclass, fields
from pathlib import Path

CONFIG_SYSTEME = '/etc/gagarine.conf'
ETAT_SYSTEME = '/var/lib/gagarine'
# Clés qu'on peut écrire avec « gagarine reglages --ecrire » (« filtre » ne sert qu'aux essais)
CLES_ECRITURE = ('seuil', 'urgence', 'vigilance', 'grace', 'vidage_max', 'delai_fige', 'reste_si_repris',
                 'notifications', 'proteges',
                 # 1.2.0
                 'anticipation', 'pression', 'notifications_fin', 'notification_fin_min', 'ntfy', 'file_ajuster',
                 'menage_auto', 'menage_delai', 'claude_bloquer')
# Réglages numériques pour lesquels 0 veut dire « désactivé »
ZERO_PERMIS = ('anticipation', 'pression', 'notification_fin_min')
# Réglages exprimés en pourcentage (100 au plus)
POURCENTAGES = ('seuil', 'urgence', 'vigilance', 'pression')
# ntfy : un sujet (servi par ntfy.sh) ou l'adresse complète d'un sujet sur un autre serveur
MOTIF_NTFY = re.compile(r'[A-Za-z0-9_-]{1,64}|https?://[^\s/]+(/[^\s/]+)*/[A-Za-z0-9_-]{1,64}')


@dataclass
class Reglages:
    seuil: float = 95            # % de RAM à partir duquel on fige un programme
    urgence: float = 98          # % de RAM à partir duquel on arrête sans attendre
    vigilance: float = 85        # % à partir duquel on mesure quatre fois par seconde
    grace: float = 10            # secondes laissées après la pause avant d'arrêter si la RAM ne baisse pas
    vidage_max: float = 60       # secondes au plus tant que le rangement dans le swap n'est pas fini
    delai_fige: float = 600      # secondes figé sans décision avant l'arrêt
    reste_si_repris: float = 256  # Mo qu'un programme repris peut encore demander
    notifications: bool = True
    proteges: str = ''           # expression régulière de noms à protéger en plus de la liste intégrée
    filtre: str = ''             # essais : ne viser que les programmes dont la ligne de commande correspond
    # 1.2.0
    anticipation: float = 15     # s : figer plus tôt un programme qui remplirait la RAM jusqu'au seuil dans ce délai (0 : non)
    pression: float = 20         # % de « full avg10 » de /proc/pressure/memory qui fait agir au-delà de vigilance (0 : non)
    notifications_fin: bool = True       # notification quand un travail de la file se termine
    notification_fin_min: float = 30     # s : pas de notification de fin pour un travail plus court (0 : toujours)
    ntfy: str = ''               # sujet ntfy (ou adresse complète) pour recevoir aussi les alertes sur le téléphone
    file_ajuster: bool = True    # la file corrige d'elle-même une RAM annoncée bien plus basse que celle déjà mesurée
    menage_auto: bool = True     # arrêter tout seul les démons et modèles inactifs depuis menage_delai
    menage_delai: float = 30     # min d'inactivité avant le ménage automatique
    claude_bloquer: bool = True  # le hook des sessions Claude refuse les travaux lourds lancés hors de la file


def booleen(texte):
    t = str(texte).strip().lower()
    if t in ('oui', 'o', 'yes', 'y', 'true', '1', 'on', 'vrai'):
        return True
    if t in ('non', 'n', 'no', 'false', '0', 'off', 'faux'):
        return False
    raise ValueError(f'« {texte} » n\'est ni oui ni non')


def chemin_config(arg=None, env=None):
    """--config, puis GAGARINE_CONFIG, puis /etc/gagarine.conf."""
    env = os.environ if env is None else env
    return arg or env.get('GAGARINE_CONFIG') or CONFIG_SYSTEME


def convertir(nom, texte):
    """Valeur typée d'un réglage, d'après le texte. Lève ValueError (ou re.error) si elle est invalide : mêmes règles
    pour la lecture du fichier et pour l'écriture."""
    champ = {c.name: c for c in fields(Reglages)}[nom]
    texte = str(texte).strip()
    if champ.type is bool:
        return booleen(texte)
    if champ.type is float:
        v = float(texte)
        minimum_ok = v >= 0 if nom in ZERO_PERMIS else v > 0
        if not (minimum_ok and v <= (100 if nom in POURCENTAGES else 10 ** 9)):
            raise ValueError('hors limites')
        return v
    if nom in ('proteges', 'filtre') and texte:
        re.compile(texte)
    if nom == 'ntfy' and texte and not MOTIF_NTFY.fullmatch(texte):
        raise ValueError('un sujet ntfy (lettres, chiffres, - et _) ou une adresse http(s)://serveur/sujet')
    return texte


def charger_reglages(arg=None, env=None):
    """Retourne (Reglages, avertissements). Priorité : variables GAGARINE_* > fichier > valeurs par défaut.
    Une valeur invalide est ignorée (avec un avertissement) : le service ne doit pas tomber sur une faute de frappe."""
    env = os.environ if env is None else env
    r = Reglages()
    avertissements = []
    chemin = chemin_config(arg, env)
    brut = {}
    cp = ConfigParser(interpolation=None)
    try:
        with open(chemin, encoding='utf-8') as f:
            cp.read_file(f)
        if cp.has_section('gagarine'):
            brut = dict(cp['gagarine'])
    except FileNotFoundError:
        if arg or env.get('GAGARINE_CONFIG'):
            avertissements.append(f'fichier de réglages absent : {chemin} (valeurs par défaut)')
    except (OSError, ErreurConfig, UnicodeDecodeError) as e:
        avertissements.append(f'fichier de réglages illisible : {chemin} ({e})')
    for nom, valeur in brut.items():
        if nom not in {c.name for c in fields(r)}:
            avertissements.append(f'clé inconnue ignorée : {nom}')
    for nom, valeur in ((n, env[f'GAGARINE_{n.upper()}']) for n in (c.name for c in fields(r))
                        if f'GAGARINE_{n.upper()}' in env):
        brut[nom] = valeur   # les variables passent devant le fichier
    for c in fields(r):
        if c.name not in brut:
            continue
        valeur = brut[c.name].strip()
        try:
            v = convertir(c.name, valeur)
        except (ValueError, re.error) as e:
            avertissements.append(f'réglage {c.name} invalide ({valeur!r} : {e}), valeur par défaut gardée')
            continue
        setattr(r, c.name, v)
    return r, avertissements


def dossier_etat(env=None, euid=None, home=None):
    """GAGARINE_ETAT, sinon /var/lib/gagarine en root, sinon ~/.local/state/gagarine."""
    env = os.environ if env is None else env
    euid = os.geteuid() if euid is None else euid
    if env.get('GAGARINE_ETAT'):
        return Path(env['GAGARINE_ETAT'])
    if euid == 0:
        return Path(ETAT_SYSTEME)
    return Path(home or Path.home()) / '.local/state/gagarine'


def dossier_etat_lecture(env=None, euid=None, home=None, lisible=os.access):
    """Pour `etat` et les commandes de reprise : le dossier root s'il est lisible, sinon celui de l'utilisateur."""
    env = os.environ if env is None else env
    if env.get('GAGARINE_ETAT'):
        return Path(env['GAGARINE_ETAT'])
    racine = Path(ETAT_SYSTEME)
    if lisible(racine / 'evenements.jsonl', os.R_OK) or lisible(racine / 'figes.json', os.R_OK):
        return racine
    return Path(home or Path.home()) / '.local/state/gagarine'


def reglages_en_dict(r):
    """Les réglages tels que les montrent `etat --json` et `reglages --json` (sans « filtre », réservé aux essais)."""
    return {c: getattr(r, c) for c in CLES_ECRITURE}


class ErreurReglages(ValueError):
    """Une ou plusieurs clés ou valeurs refusées (message : un défaut par ligne)."""


def valider_ecriture(valeurs):
    """{clé: texte} → {clé: texte à écrire}. Refuse l'ensemble (ErreurReglages) si une clé est inconnue ou une valeur
    invalide ; les booléens sont écrits « oui » ou « non »."""
    erreurs = []
    propres = {}
    for cle, valeur in valeurs.items():
        if cle not in CLES_ECRITURE:
            erreurs.append(f'clé inconnue : {cle} (clés : {", ".join(CLES_ECRITURE)})')
            continue
        texte = str(valeur).strip()
        if '\n' in texte or '\r' in texte:
            erreurs.append(f'réglage {cle} : la valeur tient sur une seule ligne')
            continue
        try:
            v = convertir(cle, texte)
        except (ValueError, re.error) as e:
            erreurs.append(f'réglage {cle} invalide ({texte!r} : {e})')
            continue
        propres[cle] = ('oui' if v else 'non') if isinstance(v, bool) else texte
    if erreurs:
        raise ErreurReglages('\n'.join(erreurs))
    return propres


def texte_modifie(texte, valeurs):
    """Texte du fichier avec les valeurs mises à jour sur place : la ligne « cle = … » de la section [gagarine] est
    remplacée (commentaires, ordre et autres sections intacts), sinon la clé est ajoutée à la fin de la section."""
    lignes = texte.splitlines()
    debut = fin = None
    for i, l in enumerate(lignes):
        m = re.match(r'\s*\[([^\]]*)\]', l)
        if m:
            if debut is not None and fin is None:
                fin = i
            if m.group(1).strip() == 'gagarine' and debut is None:
                debut = i
    if debut is None:
        if lignes and lignes[-1].strip():
            lignes.append('')
        lignes.append('[gagarine]')
        debut, fin = len(lignes) - 1, len(lignes)
    elif fin is None:
        fin = len(lignes)
    for cle, valeur in valeurs.items():
        motif = re.compile(r'(\s*' + re.escape(cle) + r'\s*[=:]\s*)', re.I)
        trouve = None
        for i in range(debut + 1, fin):
            m = motif.match(lignes[i])
            if m and not lignes[i].lstrip().startswith(('#', ';')):
                if trouve is None:
                    trouve = i
                    lignes[i] = m.group(1) + valeur
                else:
                    lignes[i] = None   # doublon : ConfigParser le refuserait
        if trouve is None:
            fin_utile = fin
            while fin_utile > debut + 1 and not lignes[fin_utile - 1].strip():
                fin_utile -= 1
            lignes.insert(fin_utile, f'{cle}={valeur}')
            fin += 1
        else:
            nombre = sum(1 for i in range(debut + 1, fin) if lignes[i] is None)
            lignes = [l for l in lignes if l is not None]
            fin -= nombre
    return '\n'.join(lignes) + '\n'


def ecrire_reglages(chemin, valeurs):
    """Écrit les réglages dans le fichier (valeurs déjà validées par valider_ecriture) : modification sur place, écriture
    atomique (fichier temporaire du même dossier puis renommage), droits du fichier conservés. Lève OSError."""
    chemin = Path(chemin)
    try:
        texte = chemin.read_text(encoding='utf-8')
        mode = chemin.stat().st_mode & 0o777
    except FileNotFoundError:
        texte, mode = '', 0o644
    nouveau = texte_modifie(texte, valeurs)
    cp = ConfigParser(interpolation=None)
    try:
        cp.read_string(nouveau)
    except ErreurConfig as e:   # ne jamais écrire un fichier que le service ne saurait pas relire
        raise OSError(f'le fichier modifié ne serait pas lisible : {e}') from e
    fd, tmp = tempfile.mkstemp(dir=chemin.parent, prefix='.gagarine.conf.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(nouveau)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, chemin)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
