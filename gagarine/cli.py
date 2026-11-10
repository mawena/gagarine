"""Ligne de commande de Gagarine : analyse des commandes, sorties texte et JSON.

Chaque commande est une fonction `commande_xxx(args, ctx)` qui retourne le code de sortie ; la table COMMANDES les
rassemble (ajouter une commande, comme `file`, c'est ajouter une fonction et une ligne dans la table).
"""
import importlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from gagarine.essai import essai
from gagarine import file as fa
from gagarine.garde import surveiller
from gagarine.journal import fichiers_journaux, ligne_lisible, lire_evenements
from gagarine.reglages import (CONFIG_SYSTEME, ETAT_SYSTEME, ErreurReglages, charger_reglages,
                               chemin_config, dossier_etat_lecture, ecrire_reglages, reglages_en_dict,
                               valider_ecriture)
from gagarine.systeme import (Mo, construire_proteges, demarrage, go, lancer_texte, lire_meminfo, lire_processus,
                              lire_swaps, nom_utilisateur, ram_depuis_meminfo, ram_utilisee)
from gagarine.version import VERSION

AIDE = f"""gagarine {VERSION} : garde-RAM, empêche la RAM du poste de se remplir au-delà de 95 %.

Usage : gagarine COMMANDE [options]

Commandes :
  surveiller [--config FICHIER]  surveille la RAM, au premier plan (lancé par le service gagarine.service)
  etat [--json]                  RAM, swap, état du service, programmes figés, plus gros programmes, derniers événements
  reprendre PID                  relance un programme figé (il reste bridé à sa taille)
  arreter PID                    arrête un programme figé
  file "NOM" GO COEURS [--priorite urgent|normal|fond] [--apres PID] -- COMMANDE…
                                 file d'attente des travaux lourds : attend son tour (RAM, cœurs libres, priorité ;
                                 plusieurs travaux à la fois si le réglage file_paralleles le permet), puis lance la
                                 commande dans sa limite mémoire ; rend le code de la commande (137 si limite dépassée) ;
                                 retient le pic mémoire, relève une annonce trop basse, repère les fuites d'un
                                 lancement à l'autre et prévient à la fin du travail
  file --etat [--json]           mémoire disponible, charge, travaux en cours et en file, avec priorité et motif d'attente
                                 (aussi : file etat)
  file priorite PID|TICKET NIVEAU  change la priorité (urgent, normal, fond) d'un travail en attente (le sien ; tous : root)
  file annuler PID|TICKET        annule un travail en attente ou arrête un travail en cours (le sien ; tous : root)
  file appris [--json]           mémoire apprise : pic et tendance (hausse, limite, stable) de chaque travail
  journal [-n N] [--json]        les N derniers événements (50 par défaut), tous journaux fusionnés
  reglages [--json]              affiche les réglages en vigueur
  reglages --ecrire CLE=VALEUR…  modifie /etc/gagarine.conf et recharge le service (administrateur)
  service ACTION                 demarrer, arreter, redemarrer, activer ou desactiver le service (administrateur)
  essai                          auto-test réel, sans risque : un programme d'essai grossit, Gagarine doit le figer,
                                 le ranger dans le swap puis l'arrêter (à lancer avec sudo pour tester le mode root)
  version                        affiche la version
  -h, --help                     affiche cette aide

Réglages : {CONFIG_SYSTEME}, section [gagarine] (voir ce fichier pour le détail des clés) ; --config FICHIER ou la
variable GAGARINE_CONFIG en choisissent un autre. Les variables GAGARINE_SEUIL, GAGARINE_URGENCE, GAGARINE_VIGILANCE,
GAGARINE_GRACE, GAGARINE_VIDAGE_MAX, GAGARINE_DELAI_FIGE, GAGARINE_RESTE_SI_REPRIS, GAGARINE_NOTIFICATIONS,
GAGARINE_PROTEGES et GAGARINE_FILTRE passent devant tout (essais). GAGARINE_ETAT remplace le dossier d'état
({ETAT_SYSTEME} en root, sinon ~/.local/state/gagarine). La file garde ses tickets dans {fa.DOSSIER_DEFAUT}
(variable FILE_ATTENTE_DOSSIER), partagés avec l'ancien outil file-attente.
"""

ACTIONS_SERVICE = {
    'demarrer': (['start'], 'démarré'),
    'arreter': (['stop'], 'arrêté'),
    'redemarrer': (['restart'], 'redémarré'),
    'activer': (['enable', '--now'], 'activé (il démarrera avec le poste) et démarré'),
    'desactiver': (['disable', '--now'], 'désactivé et arrêté'),
}


def executer(cmd):
    """(code, sortie, erreurs) d'une commande ; code 127 si elle est introuvable."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, '', str(e)
    return res.returncode, res.stdout, res.stderr


class Contexte:
    """Ce que les commandes demandent au poste, remplaçable par les tests : privilèges, environnement, exécution de
    commandes (systemctl), lecture de commandes (sortie texte), configuration donnée par --config."""

    def __init__(self, config=None, root=None, env=None, executer=executer, lancer=lancer_texte, poste=None):
        self.config = config
        self.root = (os.geteuid() == 0) if root is None else root
        self.env = os.environ if env is None else env
        self.executer = executer
        self.lancer = lancer
        self.poste = poste or fa.Poste()   # ce que la file demande au poste (horloge, /proc, systemctl, lancement)

    def fichier_reglages(self):
        return chemin_config(self.config, self.env)


# ======================================================================================================================
# etat
# ======================================================================================================================
def etat_service(lancer=lancer_texte, root=None):
    root = (os.geteuid() == 0) if root is None else root
    res = [('gagarine', lancer(['systemctl', 'is-active', 'gagarine']).strip() or '?')]
    if not root:
        res.append(('garde-ram (service utilisateur)', lancer(['systemctl', '--user', 'is-active', 'garde-ram']).strip() or '?'))
    return res


def lire_figes(dossier):
    try:
        figes = json.loads((Path(dossier) / 'figes.json').read_text())
    except (OSError, ValueError):
        return {}
    return {int(p): f for p, f in figes.items() if demarrage(int(p)) == f['debut']}


def lire_fuites(dossier):
    """Fuites repérées en cours par le service (fuites.json) : celles dont le programme tourne encore."""
    try:
        fuites = json.loads((Path(dossier) / 'fuites.json').read_text())
        return [f for f in fuites if demarrage(int(f['pid'])) == f['debut']]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def est_bride(pid):
    """Un programme figé puis bridé vit dans un scope « gagarine-fige-… »."""
    try:
        return 'gagarine-fige-' in Path(f'/proc/{pid}/cgroup').read_text()
    except OSError:
        return False


def lire_texte(chemin):
    with open(chemin) as f:
        return f.read()


def donnees_etat(r, fichier_reglages, dossier, lancer=lancer_texte, root=False, meminfo=None, swaps=None, procs=None,
                 figes=None, bride=est_bride, maintenant=time.time, travaux=0, fuites=None):
    """L'objet de `gagarine etat --json` (contrat : docs/conception-1.1.0.md, section 2). Tout ce qui vient du poste
    peut être donné en argument (tests) ; sinon il est lu."""
    meminfo = lire_texte('/proc/meminfo') if meminfo is None else meminfo
    swaps = lire_texte('/proc/swaps') if swaps is None else swaps
    total, dispo = lire_meminfo(meminfo)
    if procs is None:
        procs = lire_processus(None if root else os.getuid())
    figes = lire_figes(dossier) if figes is None else figes
    fuites = lire_fuites(dossier) if fuites is None else fuites
    proteges = construire_proteges(r.proteges)
    gros = sorted(procs.items(), key=lambda x: x[1].rss + x[1].swap, reverse=True)[:10]
    return {
        'version': VERSION,
        'date': time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(maintenant())),
        'ram': {'pourcent': round(ram_depuis_meminfo(meminfo), 1), 'total_mo': total // Mo, 'disponible_mo': dispo // Mo},
        'swap': lire_swaps(swaps),
        'reglages': reglages_en_dict(r),
        'fichier_reglages': str(fichier_reglages),
        'service': {'etat': lancer(['systemctl', 'is-active', 'gagarine']).strip() or 'unknown',
                    'actif_au_demarrage': lancer(['systemctl', 'is-enabled', 'gagarine']).strip() == 'enabled'},
        'dossier_etat': str(dossier),
        'figes': [{'pid': p, 'nom': f['nom'], 'depuis': f['depuis'], 'memoire_mo': int(f['memoire'] // Mo),
                   'commande': f['commande'], 'utilisateur': f['utilisateur'], 'bride': bride(p)}
                  for p, f in figes.items()],
        'fuites': [{'pid': f['pid'], 'nom': f['nom'], 'pente_mo_min': f['pente_mo_min'], 'depuis': f['depuis'],
                    'memoire_mo': int(f['memoire'] // Mo), 'prevision_s': f.get('prevision_s')} for f in fuites],
        'file': {'travaux': travaux},
        'plus_gros': [{'pid': p, 'nom': i.nom, 'rss_mo': i.rss // Mo, 'swap_mo': i.swap // Mo,
                       'utilisateur': nom_utilisateur(i.uid), 'protege': bool(proteges.match(i.nom))}
                      for p, i in gros],
    }


def commande_etat(args, ctx):
    if args not in ([], ['--json']):
        return usage()
    r, _ = charger_reglages(ctx.config, ctx.env)
    dossier = dossier_etat_lecture(ctx.env)
    if args:
        print(json.dumps(donnees_etat(r, ctx.fichier_reglages(), dossier, ctx.lancer, ctx.root,
                                      travaux=fa.compter_travaux(ctx.env, ctx.poste)), ensure_ascii=False))
        return 0
    print(f'RAM utilisée : {ram_utilisee():.0f} % (seuil {r.seuil:.0f} %, urgence {r.urgence:.0f} %)')
    for nom, actif in etat_service(ctx.lancer, ctx.root):
        print(f'Service {nom} : {actif}')
    print(f'Dossier d\'état : {dossier}')
    figes = lire_figes(dossier)
    for p, f in figes.items():
        print(f'  figé : {f["nom"]} (processus {p}, {go(f["memoire"])}, depuis '
              f'{time.strftime("%H:%M:%S", time.localtime(f["depuis"]))}) — {f["commande"][:120]}')
    if not figes:
        print('Aucun programme figé.')
    for f in lire_fuites(dossier):
        print(f'  fuite : {f["nom"]} (processus {f["pid"]}, +{f["pente_mo_min"]} Mo/min, {go(f["memoire"])}, depuis '
              f'{time.strftime("%H:%M:%S", time.localtime(f["depuis"]))})')
    travaux = fa.compter_travaux(ctx.env, ctx.poste)
    print(f'File d\'attente : {travaux} travail(aux) en file' if travaux else 'File d\'attente : aucun travail en file')
    try:
        lignes = (dossier / 'evenements.jsonl').read_text().splitlines()[-8:]
    except OSError:
        lignes = []
    if lignes:
        print('Derniers événements :')
        for l in lignes:
            try:
                e = json.loads(l)
            except ValueError:
                continue
            if e.get('action') == 'demarrage':
                continue
            print(f'  {e["date"][:19].replace("T", " ")}  {e["action"]:<10} {e.get("nom", "")} {e.get("pid", "")}'
                  f' {e.get("raison", "")}'.rstrip())
    return 0


# ======================================================================================================================
# file
# ======================================================================================================================
USAGE_FILE = ('usage : gagarine file "<nom>" <Go de RAM> <cœurs libres> [--priorite urgent|normal|fond] [--apres PID] '
              '-- <commande> [arguments…]\n'
              '        gagarine file --etat [--json]   |   gagarine file etat\n'
              '        gagarine file priorite <PID|ticket> <urgent|normal|fond>\n'
              '        gagarine file annuler <PID|ticket>\n'
              '        gagarine file appris [--json]')


def commande_file(args, ctx):
    if '--' not in args:
        # Sans commande à lancer : état ou annulation
        if args in (['--etat'], ['etat']):
            return fa.afficher_etat(ctx.env, False, ctx.poste, reglages=charger_reglages(ctx.config, ctx.env)[0])
        if args in (['--etat', '--json'], ['etat', '--json'], ['--json', '--etat'], ['--json', 'etat']):
            return fa.afficher_etat(ctx.env, True, ctx.poste, reglages=charger_reglages(ctx.config, ctx.env)[0])
        if args in (['appris'], ['appris', '--json'], ['--json', 'appris']):
            return fa.afficher_appris(ctx.env, '--json' in args)
        if len(args) == 2 and args[0] == 'annuler':
            return fa.annuler(args[1], ctx.env, ctx.root, ctx.poste)
        if len(args) == 3 and args[0] == 'priorite':
            return fa.changer_priorite(args[1], args[2], ctx.env, ctx.root, ctx.poste)
        print(USAGE_FILE, file=sys.stderr)
        return 2
    tiret = args.index('--')
    avant, commande = args[:tiret], args[tiret + 1:]
    apres = None
    priorite = fa.PRIORITE_DEFAUT
    if '--priorite' in avant:
        i = avant.index('--priorite')
        if i + 1 >= len(avant) or avant[i + 1] not in fa.NIVEAUX:
            print(f'--priorite demande un niveau : {", ".join(fa.NIVEAUX)}', file=sys.stderr)
            return 2
        priorite = avant[i + 1]
        avant = avant[:i] + avant[i + 2:]
    if '--apres' in avant:
        i = avant.index('--apres')
        if i + 1 >= len(avant) or not avant[i + 1].isdigit():
            print('--apres demande le numéro d\'un processus', file=sys.stderr)
            return 2
        apres = int(avant[i + 1])
        avant = avant[:i] + avant[i + 2:]
    if len(avant) != 3 or not commande:
        print(USAGE_FILE, file=sys.stderr)
        return 2
    nom, ram, coeurs = avant[0], fa.nombre(avant[1]), fa.nombre(avant[2])
    if not nom or ram is None or coeurs is None or ram < 0 or coeurs < 0:
        print(USAGE_FILE, file=sys.stderr)
        return 2
    env = dict(ctx.env)
    reglages = charger_reglages(ctx.config, env)[0]
    return fa.lancer_travail(nom, ram or 1, coeurs or 1, apres, commande, env, ctx.poste, reglages=reglages,
                             priorite=priorite)


# ======================================================================================================================
# reprendre, arreter
# ======================================================================================================================
def reprendre_ou_arreter(action, pid):
    """Reprend (SIGCONT) ou arrête (SIGKILL) un programme que Gagarine a figé : jamais un autre."""
    if pid not in lire_figes(dossier_etat_lecture()):
        sys.exit(f'processus {pid} : ne figure pas parmi les programmes figés par Gagarine (voir « gagarine etat »)')
    try:
        os.kill(pid, signal.SIGCONT if action == 'reprendre' else signal.SIGKILL)
    except OSError as e:
        sys.exit(f'processus {pid} : {e.strerror}')
    print(f'processus {pid} {"repris (il reste bridé à sa taille)" if action == "reprendre" else "arrêté"}')


def commande_reprendre_ou_arreter(action):
    def commande(args, ctx):
        if len(args) != 1 or not args[0].isdigit():
            return usage()
        reprendre_ou_arreter(action, int(args[0]))
        return 0
    return commande


# ======================================================================================================================
# journal
# ======================================================================================================================
def commande_journal(args, ctx):
    n, en_json = 50, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--json':
            en_json = True
        elif a == '-n' and i + 1 < len(args):
            i += 1
            if not args[i].isdigit():
                print(f'-n demande un nombre d\'événements, pas « {args[i]} »', file=sys.stderr)
                return 2
            n = int(args[i])
        elif a.startswith('-n') and a[2:].isdigit():
            n = int(a[2:])
        else:
            return usage()
        i += 1
    evenements = lire_evenements(fichiers_journaux(ctx.env), n)
    if en_json:
        print(json.dumps({'evenements': evenements}, ensure_ascii=False))
    elif evenements:
        for e in evenements:
            print(ligne_lisible(e))
    else:
        print('Aucun événement.')
    return 0


# ======================================================================================================================
# reglages
# ======================================================================================================================
def commande_reglages(args, ctx):
    if args and args[0] == '--ecrire':
        return ecrire(args[1:], ctx)
    if args not in ([], ['--json']):
        return usage()
    r, avertissements = charger_reglages(ctx.config, ctx.env)
    fichier = ctx.fichier_reglages()
    if args:
        print(json.dumps({'fichier': str(fichier), 'reglages': reglages_en_dict(r)}, ensure_ascii=False))
        return 0
    print(f'Réglages en vigueur (fichier {fichier}) :')
    for cle, valeur in reglages_en_dict(r).items():
        if isinstance(valeur, bool):
            valeur = 'oui' if valeur else 'non'
        print(f'  {cle:<16} {valeur}')
    for a in avertissements:
        print(f'avertissement : {a}', file=sys.stderr)
    return 0


def ecrire(args, ctx):
    """reglages --ecrire CLE=VALEUR… : tout ou rien, modification sur place, puis rechargement du service."""
    if not args or any('=' not in a for a in args):
        print('usage : gagarine reglages --ecrire CLE=VALEUR [CLE=VALEUR…]', file=sys.stderr)
        return 2
    if not ctx.root:
        print(f'écrire les réglages demande les droits d\'administrateur (sudo gagarine reglages --ecrire …) : '
              f'{ctx.fichier_reglages()} n\'a pas été modifié', file=sys.stderr)
        return 1
    valeurs = {}
    for a in args:
        cle, valeur = a.split('=', 1)
        valeurs[cle.strip()] = valeur
    try:
        propres = valider_ecriture(valeurs)
    except ErreurReglages as e:
        print(f'réglages refusés, rien n\'a été écrit :\n{e}', file=sys.stderr)
        return 2
    fichier = ctx.fichier_reglages()
    try:
        ecrire_reglages(fichier, propres)
    except OSError as e:
        print(f'impossible d\'écrire {fichier} : {e}', file=sys.stderr)
        return 1
    print(f'{fichier} mis à jour : ' + ', '.join(f'{c}={v}' for c, v in propres.items()))
    actif = ctx.executer(['systemctl', 'is-active', 'gagarine'])[1].strip() == 'active'
    if not actif:
        print('Le service gagarine ne tourne pas : les réglages seront lus à son prochain démarrage.')
        return 0
    code, _, erreur = ctx.executer(['systemctl', 'reload', 'gagarine'])
    if code != 0:
        print(f'réglages écrits, mais le rechargement du service a échoué : {erreur.strip() or f"code {code}"}',
              file=sys.stderr)
        return 1
    print('Service gagarine rechargé : les nouveaux réglages sont en vigueur.')
    return 0


# ======================================================================================================================
# service
# ======================================================================================================================
def commande_service(args, ctx):
    if len(args) != 1 or args[0] not in ACTIONS_SERVICE:
        print(f'usage : gagarine service {"|".join(ACTIONS_SERVICE)}', file=sys.stderr)
        return 2
    if not ctx.root:
        print(f'« gagarine service {args[0]} » demande les droits d\'administrateur (sudo) : rien n\'a été fait',
              file=sys.stderr)
        return 1
    verbe, participe = ACTIONS_SERVICE[args[0]]
    code, _, erreur = ctx.executer(['systemctl', *verbe, 'gagarine'])
    if code != 0:
        print(f'impossible : systemctl {" ".join(verbe)} gagarine a échoué : {erreur.strip() or f"code {code}"}',
              file=sys.stderr)
        return 1
    print(f'Service gagarine {participe}.')
    return 0


# ======================================================================================================================
# Ligne de commande
# ======================================================================================================================
# Commandes apportées par des modules à part (1.2.0) : chaque module d'EXTENSIONS définit
#   COMMANDES = {'nom': fonction(args, ctx) -> code de sortie}
#   AIDE_COMMANDES = '  nom ARGUMENTS…                description\n…'   (lignes ajoutées à l'aide)
# Un module absent est ignoré ; un module présent mais cassé fait échouer la commande avec son erreur.
EXTENSIONS = ('claude', 'menage', 'historique', 'applications', 'ntfy', 'ligne')


def charger_extensions(noms=EXTENSIONS, importer=importlib.import_module):
    """[(module, COMMANDES, AIDE_COMMANDES)] des extensions présentes."""
    trouvees = []
    for nom in noms:
        complet = f'gagarine.{nom}'
        try:
            module = importer(complet)
        except ModuleNotFoundError as e:
            if e.name == complet:
                continue
            raise
        trouvees.append((module, dict(getattr(module, 'COMMANDES', {})), getattr(module, 'AIDE_COMMANDES', '')))
    return trouvees


def aide(extensions=None):
    """Le texte d'aide, avec les lignes des extensions présentes avant « version »."""
    extensions = charger_extensions() if extensions is None else extensions
    lignes = ''.join(a if a.endswith('\n') else a + '\n' for _, _, a in extensions if a)
    if not lignes:
        return AIDE
    marque = '  version                        affiche la version\n'
    return AIDE.replace(marque, lignes + marque, 1)


def usage():
    print(aide(), file=sys.stderr)
    return 2


def commande_surveiller(args, ctx):
    if args:
        return usage()
    surveiller(ctx.config)
    return 0


def commande_essai(args, ctx):
    return usage() if args else essai()


def commande_version(args, ctx):
    print(f'gagarine {VERSION}')
    return 0


COMMANDES = {
    'surveiller': commande_surveiller,
    'etat': commande_etat,
    'reprendre': commande_reprendre_ou_arreter('reprendre'),
    'arreter': commande_reprendre_ou_arreter('arreter'),
    'file': commande_file,
    'journal': commande_journal,
    'reglages': commande_reglages,
    'service': commande_service,
    'essai': commande_essai,
    'version': commande_version,
    '--version': commande_version,
    '-V': commande_version,
}


def main(argv, ctx=None):
    config = None
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == '--config':
            if i + 1 >= len(argv):
                sys.exit('--config demande un nom de fichier')
            config = argv[i + 1]
            i += 2
        elif argv[i].startswith('--config='):
            config = argv[i].split('=', 1)[1]
            i += 1
        else:
            args.append(argv[i])
            i += 1
    if not args or args[0] in ('-h', '--help', 'aide'):
        print(aide())
        return 0 if args else 2
    commande = COMMANDES.get(args[0])
    if commande is None:
        for _, commandes, _ in charger_extensions():
            if args[0] in commandes:
                commande = commandes[args[0]]
                break
    if commande is None:
        return usage()
    if ctx is None:
        ctx = Contexte(config)
    elif config is not None:
        ctx.config = config
    return commande(args[1:], ctx)
