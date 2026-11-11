"""État du poste dans la ligne d'état de Claude Code : `gagarine ligne-etat [--avec "COMMANDE"] [--sans-couleur]`.

Claude Code envoie son JSON sur l'entrée standard ; la commande répond par une ligne courte
(`RAM 72 % · CPU 23 % · file 3 (2 en cours) · 1 figé · fuite : python3`) où seules les parties non nulles apparaissent.
Avec `--avec`, la ligne d'état existante est lancée d'abord (même entrée, 1 s au plus) et la partie de Gagarine
est ajoutée à la fin de sa dernière ligne. Ce module ne plante jamais : au pire, la RAM seule, code de sortie 0.
"""
import json
import os
import sys
import time
from pathlib import Path

DELAI_AVEC = 1.0          # s laissées à la commande de --avec
FRAICHEUR_CPU = 6.0       # s : la mesure du processeur du service (processeur.json) n'est plus utilisée au-delà
DOSSIER_EXECUTION = '/run/gagarine'
FENETRE_FUITES = 1800     # s : les événements « fuite » plus anciens ne comptent plus
SEPARATEUR = ' │ '
NOMS_MAX = 2              # noms de programmes en fuite montrés avant « +N »
COULEURS = {'normal': '32', 'vigilance': '33', 'seuil': '31', 'file': '36', 'cpu_calme': '32', 'cpu_occupe': '33', 'cpu_sature': '31', 'chaud': '31', 'fige': '33', 'fuite': '31'}


def couleur(texte, code, avec):
    return f'\x1b[{code}m{texte}\x1b[0m' if avec else texte


def lire_ram():
    """Pourcentage de RAM utilisée d'après /proc/meminfo, ou None."""
    try:
        total = dispo = None
        with open('/proc/meminfo') as f:
            for ligne in f:
                if ligne.startswith('MemTotal:'):
                    total = int(ligne.split()[1])
                elif ligne.startswith('MemAvailable:'):
                    dispo = int(ligne.split()[1])
                if total is not None and dispo is not None:
                    break
        return 100 * (1 - dispo / total)
    except (OSError, ValueError, IndexError, TypeError, ZeroDivisionError):
        return None


def lire_processeur(env, maintenant=None):
    """La mesure du processeur du service (processeur.json), si elle est fraîche ; sinon None. Aucune mesure directe."""
    maintenant = time.time() if maintenant is None else maintenant
    dossier = env.get('GAGARINE_DOSSIER_EXECUTION') or DOSSIER_EXECUTION   # comme le service : l'un, sinon l'autre
    d = lire_json(Path(dossier) / 'processeur.json')
    if isinstance(d, dict):
        t, pct = d.get('t'), d.get('pourcent')
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (t, pct)) \
                and -1 <= maintenant - t <= FRAICHEUR_CPU:
            return d
    return None


def partie_cpu(d, avec_couleur=True):
    """`CPU 23 %`, suivi de la température en rouge si le processeur est chaud."""
    pct = d['pourcent']
    niveau = 'cpu_sature' if pct >= 90 else 'cpu_occupe' if pct >= 70 else 'cpu_calme'
    texte = couleur(f'CPU {round(pct)} %', COULEURS[niveau], avec_couleur)
    temperature = d.get('temperature')
    if d.get('chaud') is True and isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
        texte += ' ' + couleur(f'{round(temperature)} °C', COULEURS['chaud'], avec_couleur)
    return texte


def niveau_ram(pct, vigilance, seuil):
    return 'seuil' if pct >= seuil else 'vigilance' if pct >= vigilance else 'normal'


def lire_json(chemin):
    try:
        return json.loads(Path(chemin).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def en_vie(pid):
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and Path(f'/proc/{pid}').exists()


def compter_file(env):
    """(travaux en file, dont en cours). Un ticket d'ancien outil compte comme un travail en cours."""
    from gagarine.file import ETAT_ATTENTE, ETAT_COURS, Poste, dossier_tickets, lire_ticket, tickets
    dossier = dossier_tickets(env)
    total = cours = 0
    for nom in tickets(dossier, Poste(), nettoyer=False):
        total += 1
        t = lire_ticket(dossier, nom) or {}
        etat = t.get('etat')
        if etat == ETAT_COURS or (t.get('source') != 'gagarine' and etat != ETAT_ATTENTE):
            cours += 1
    return total, cours


def compter_figes(dossier):
    """Programmes figés encore en vie d'après figes.json (une entrée dont le processus a disparu ne compte pas)."""
    donnees = lire_json(Path(dossier) / 'figes.json')
    if not isinstance(donnees, dict):
        return 0
    n = 0
    for cle in donnees:
        try:
            if en_vie(int(cle)):
                n += 1
        except (TypeError, ValueError):
            continue
    return n


def _fuites_du_fichier(dossier):
    """[(pid ou None, nom)] de fuites.json ; tolère une liste, un dict {'fuites': …} ou un dict pid -> fuite."""
    donnees = lire_json(Path(dossier) / 'fuites.json')
    if isinstance(donnees, dict) and isinstance(donnees.get('fuites'), (list, dict)):
        donnees = donnees['fuites']
    elements = []
    if isinstance(donnees, list):
        elements = [(None, e) for e in donnees]
    elif isinstance(donnees, dict):
        elements = list(donnees.items())
    trouvees = []
    for cle, e in elements:
        if isinstance(e, str):
            trouvees.append((None, e))
        elif isinstance(e, dict):
            pid = e.get('pid', cle)
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                pid = None
            nom = e.get('nom')
            if nom:
                trouvees.append((pid, str(nom)))
    return trouvees


def _fuites_des_journaux(env, maintenant):
    """[(pid ou None, nom)] des événements « fuite » de la dernière demi-heure."""
    from gagarine.journal import fichiers_journaux, horodatage
    trouvees = []
    for fichier in fichiers_journaux(env):
        try:
            with open(fichier, encoding='utf-8', errors='replace') as f:
                for ligne in f:
                    if '"fuite"' not in ligne:
                        continue
                    try:
                        e = json.loads(ligne)
                    except ValueError:
                        continue
                    if not isinstance(e, dict) or e.get('action') != 'fuite' or not e.get('nom'):
                        continue
                    t = horodatage(e.get('date'))
                    if t is None or maintenant - t > FENETRE_FUITES:
                        continue
                    pid = e.get('pid')
                    trouvees.append((pid if isinstance(pid, int) else None, str(e['nom'])))
        except OSError:
            continue
    return trouvees


def noms_fuites(env, dossier, maintenant=None):
    """Noms des programmes en fuite (sans doublon), du fichier d'état et des journaux. Un programme dont on connaît le
    pid et qui n'existe plus n'est plus une fuite."""
    maintenant = time.time() if maintenant is None else maintenant
    sources = []
    for lecteur in (lambda: _fuites_du_fichier(dossier), lambda: _fuites_des_journaux(env, maintenant)):
        try:
            sources += lecteur()
        except Exception:
            continue
    noms, pids = [], set()
    for pid, nom in sources:
        if pid is not None:
            if pid in pids or not en_vie(pid):
                continue
            pids.add(pid)
        if nom not in noms:
            noms.append(nom)
    return noms


def texte_fuites(noms):
    montres = ', '.join(noms[:NOMS_MAX])
    return f'fuite : {montres}' + (f' +{len(noms) - NOMS_MAX}' if len(noms) > NOMS_MAX else '')


def partie_gagarine(env, avec_couleur=True, config=None):
    """La ligne de Gagarine. Chaque source qui échoue est simplement absente ; sans RAM lisible : « RAM ? »."""
    vigilance, seuil = 85.0, 95.0
    dossier = None
    try:
        from gagarine.reglages import charger_reglages, dossier_etat_lecture
        r, _ = charger_reglages(config, env)
        vigilance, seuil = r.vigilance, r.seuil
        dossier = dossier_etat_lecture(env)
    except Exception:
        pass
    parties = []
    pct = lire_ram()
    if pct is None:
        parties.append('RAM ?')
    else:
        parties.append(couleur(f'RAM {round(pct)} %', COULEURS[niveau_ram(pct, vigilance, seuil)], avec_couleur))
    try:
        mesure = lire_processeur(env)
        if mesure is not None:
            parties.append(partie_cpu(mesure, avec_couleur))
    except Exception:
        pass
    try:
        total, cours = compter_file(env)
        if total:
            parties.append(couleur(f'file {total} ({cours} en cours)', COULEURS['file'], avec_couleur))
    except Exception:
        pass
    if dossier is not None:
        try:
            n = compter_figes(dossier)
            if n:
                parties.append(couleur(f'{n} figé{"s" if n > 1 else ""}', COULEURS['fige'], avec_couleur))
        except Exception:
            pass
        try:
            noms = noms_fuites(env, dossier)
            if noms:
                parties.append(couleur(texte_fuites(noms), COULEURS['fuite'], avec_couleur))
        except Exception:
            pass
    return ' · '.join(parties)


def lancer_avec(commande, entree):
    """Lance la ligne d'état existante avec la même entrée ; retourne le Popen, ou None si elle ne démarre pas."""
    import shlex
    import subprocess   # importés ici : seul --avec s'en sert, et la commande doit démarrer vite
    try:
        return subprocess.Popen(shlex.split(commande), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except (OSError, ValueError):
        return None


def sortie_avec(proc, entree, echeance):
    """Sortie texte de la commande si elle finit avant l'échéance avec le code 0, sinon ''. Passé le délai, elle est tuée."""
    if proc is None:
        return ''
    import signal
    import subprocess
    try:
        sortie, _ = proc.communicate(entree, timeout=max(0.0, echeance - time.monotonic()))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.communicate(timeout=1)
        except Exception:
            pass
        return ''
    except Exception:
        return ''
    return sortie.decode('utf-8', errors='replace') if proc.returncode == 0 else ''


def assembler(sortie_avec_cmd, partie):
    """La partie de Gagarine à la fin de la dernière ligne de la sortie de --avec (ou seule si elle est vide)."""
    lignes = sortie_avec_cmd.rstrip('\n').split('\n') if sortie_avec_cmd.strip() else []
    if not lignes:
        return partie
    lignes[-1] = lignes[-1].rstrip() + SEPARATEUR + partie
    return '\n'.join(lignes)


def lire_entree():
    """Le JSON (en octets, transmis tel quel) lu sur l'entrée standard ; vide si c'est un terminal ou si la lecture échoue."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return b''
        flux = getattr(sys.stdin, 'buffer', None)
        donnees = flux.read() if flux is not None else sys.stdin.read()
        return donnees if isinstance(donnees, bytes) else donnees.encode('utf-8')
    except Exception:
        return b''


def commande_ligne_etat(args, ctx):
    avec = None
    couleurs = True
    i = 0
    while i < len(args):
        if args[i] == '--avec' and i + 1 < len(args):
            avec = args[i + 1]
            i += 2
        elif args[i].startswith('--avec='):
            avec = args[i][len('--avec='):]
            i += 1
        elif args[i] == '--sans-couleur':
            couleurs = False
            i += 1
        else:
            print('Usage :\n' + AIDE_COMMANDES.rstrip('\n'), file=sys.stderr)
            return 2
    env = getattr(ctx, 'env', os.environ)
    if env.get('NO_COLOR'):
        couleurs = False
    sortie = ''
    try:
        entree = lire_entree()
        proc = None
        echeance = time.monotonic() + DELAI_AVEC
        if avec:
            proc = lancer_avec(avec, entree)
        try:
            partie = partie_gagarine(env, couleurs, getattr(ctx, 'config', None))
        except Exception:
            partie = 'RAM ?'
        avec_sortie = sortie_avec(proc, entree, echeance) if avec else ''
        sortie = assembler(avec_sortie, partie)
    except Exception:
        try:
            pct = lire_ram()
            sortie = f'RAM {round(pct)} %' if pct is not None else 'RAM ?'
        except Exception:
            sortie = 'RAM ?'
    try:
        sys.stdout.write(sortie + '\n')
        sys.stdout.flush()
    except Exception:
        pass
    return 0


COMMANDES = {'ligne-etat': commande_ligne_etat}
AIDE_COMMANDES = (
    '  ligne-etat [--avec "CMD"]      une ligne d\'état du poste pour Claude Code (RAM, CPU, file, figés, fuites)\n'
    '                                 [--sans-couleur] ; --avec : ajoute à la fin de la ligne d\'état de CMD\n')
