"""Commande essai : auto-test réel, avec un moniteur à part qui ne peut viser que le programme d'essai."""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gagarine.systeme import ram_utilisee
from gagarine.version import VERSION

# Le moniteur d'essai est un autre Gagarine (mode « surveiller »), lancé par le paquet lui-même : il fonctionne
# donc aussi bien depuis le dépôt que depuis /usr/share/gagarine.
RACINE_PAQUET = str(Path(__file__).resolve().parent.parent)
LANCEUR_MONITEUR = ('import sys; sys.path.insert(0, sys.argv[1]); from gagarine.cli import main; '
                    'sys.exit(main(["surveiller"]))')


PROGRAMME_ESSAI = ('import time\nb = []\nfor _ in range(40):\n    b.append(bytearray(b"\\xa5") * (50 * 2 ** 20))\n'
                   '    time.sleep(0.1)\ntime.sleep(120)\n')


def reglages_essai(base):
    return {'GAGARINE_SEUIL': f'{base + 5:.1f}', 'GAGARINE_URGENCE': f'{base + 14:.1f}', 'GAGARINE_GRACE': '3',
            'GAGARINE_VIDAGE_MAX': '10', 'GAGARINE_DELAI_FIGE': '20', 'GAGARINE_NOTIFICATIONS': 'non'}


def verdict_essai(echantillons, evenements, code_sortie):
    """Verdict de l'essai d'après les mesures : [(libellé, ok, détail)].
    echantillons : [(instant, état, rss_Mo, swap_Mo)] ; evenements : actions notées par le moniteur."""
    pic = max((e[2] for e in echantillons), default=0)
    fige = any(e[1] == 'T' for e in echantillons) or 'fige' in evenements
    apres = [e for e in echantillons if e[1] == 'T']
    mini = min((e[2] for e in apres), default=pic)
    swap_max = max((e[3] for e in echantillons), default=0)
    range_ok = fige and pic > 0 and mini <= 0.8 * pic
    arrete = 'arrete' in evenements and code_sortie == -signal.SIGKILL
    return [('figé', fige, 'le programme est passé en pause' if fige else 'le programme n\'a jamais été figé'),
            ('rangé dans le swap', range_ok, f'RSS {pic} Mo au plus haut, {mini} Mo une fois figé, swap {swap_max} Mo'),
            ('arrêté', arrete, 'arrêt par Gagarine (SIGKILL)' if arrete else
             f'pas d\'arrêt constaté (code de sortie {code_sortie})')]


def essai():
    root = os.geteuid() == 0
    base = ram_utilisee()
    if base > 80:
        sys.exit(f'RAM déjà utilisée à {base:.0f} % : essai impossible sans risque, réessayez quand le poste est plus libre')
    marque = f'gagarine-programme-essai-{os.getpid()}'
    tmp = Path(tempfile.mkdtemp(prefix='gagarine-essai-'))
    env = {k: v for k, v in os.environ.items() if not k.startswith('GAGARINE_')}
    env.update(reglages_essai(base), GAGARINE_ETAT=str(tmp), GAGARINE_FILTRE=re.escape(marque),
               GAGARINE_CONFIG=os.devnull, PYTHONUNBUFFERED='1')
    print(f'Essai de Gagarine {VERSION} ({"service système, root" if root else "sans privilèges"})')
    print(f'RAM de départ {base:.1f} % ; seuil d\'essai {base + 5:.1f} %, urgence {base + 14:.1f} % ; seul le programme '
          f'd\'essai (« {marque} ») peut être visé', flush=True)
    log = open(tmp / 'moniteur.log', 'w')
    moniteur = subprocess.Popen([sys.executable, '-I', '-c', LANCEUR_MONITEUR, RACINE_PAQUET], env=env, stdout=log,
                                stderr=subprocess.STDOUT)
    alloc = None
    echantillons = []
    try:
        for _ in range(50):   # attendre l'événement « demarrage » du moniteur
            if (tmp / 'evenements.jsonl').exists() or moniteur.poll() is not None:
                break
            time.sleep(0.1)
        if moniteur.poll() is not None:
            sys.exit(f'le moniteur d\'essai s\'est arrêté : {(tmp / "moniteur.log").read_text()[-500:]}')
        alloc = subprocess.Popen([sys.executable, '-I', '-c', PROGRAMME_ESSAI, marque])
        debut = time.time()
        while time.time() - debut < 40 and alloc.poll() is None:
            try:
                st = Path(f'/proc/{alloc.pid}/status').read_text()
                etat_p = re.search(r'State:\s+(\S)', st).group(1)
                rss = int(re.search(r'VmRSS:\s+(\d+)', st).group(1)) // 1024
                swap = int(re.search(r'VmSwap:\s+(\d+)', st).group(1)) // 1024
                cg = Path(f'/proc/{alloc.pid}/cgroup').read_text().strip().rsplit('/', 1)[-1]
            except (OSError, AttributeError):
                break
            echantillons.append((time.time() - debut, etat_p, rss, swap))
            print(f'{time.time() - debut:5.1f} s  RAM {ram_utilisee():5.1f} %  essai : état {etat_p}  RSS {rss:5d} Mo  '
                  f'swap {swap:5d} Mo  {cg}', flush=True)
            time.sleep(1)
        try:
            alloc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        code = alloc.poll()
    finally:
        if alloc is not None and alloc.poll() is None:
            alloc.kill()
            alloc.wait()
        moniteur.terminate()
        moniteur.wait()
        log.close()
    evenements = []
    try:
        for l in (tmp / 'evenements.jsonl').read_text().splitlines():
            e = json.loads(l)
            if e.get('action') != 'demarrage' and e.get('pid') == alloc.pid:
                evenements.append(e['action'])
            elif e.get('action') != 'demarrage':
                print(f'ATTENTION : événement sur un autre processus : {l}')
    except (OSError, ValueError):
        pass
    print('--- journal du moniteur d\'essai ---')
    print((tmp / 'moniteur.log').read_text()[-3000:].rstrip())
    print('--- verdict ---')
    resultats = verdict_essai(echantillons, evenements, code)
    for libelle, ok, detail in resultats:
        print(f'{"✓" if ok else "✗"} {libelle} : {detail}')
    tout = all(ok for _, ok, _ in resultats)
    print('Essai réussi : Gagarine fonctionne.' if tout else 'Essai échoué : voir ci-dessus.')
    subprocess.run(['rm', '-rf', str(tmp)])
    return 0 if tout else 1
