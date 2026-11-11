"""Historique de 7 jours : une mesure par minute écrite par le service, et la commande `gagarine historique`.

Un fichier `historique-AAAA-MM-JJ.jsonl` (date UTC, comme celles du journal) par jour dans le dossier d'état, 8 jours
gardés. L'écriture ne doit jamais ralentir la boucle du garde ni la faire tomber : toute erreur est ignorée.
"""
import calendar
import json
import os
import re
import sys
import time
from pathlib import Path

from gagarine.journal import fichiers_journaux, horodatage, ligne_lisible, lire_evenements
from gagarine.reglages import dossier_etat_lecture

JOURS_GARDES = 8
PAS_S = 60              # une mesure par minute
PAS_LONG_S = 600        # au-delà de 24 h : points moyennés par 10 min
SEUIL_LONG_S = 24 * 3600
DEPUIS_DEFAUT = '24h'
UNITES = {'min': 60, 'h': 3600, 'j': 86400}
MOTIF_FICHIER = re.compile(r'^historique-(\d{4})-(\d\d)-(\d\d)\.jsonl$')

AIDE_COMMANDES = ('  historique [--depuis 5min|1h|24h|7j] [--json]   mesures des derniers jours : RAM, incidents, plus gros '
                  'programmes, processeur, travaux\n')


def date_iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(t))


def nom_fichier(t):
    return 'historique-' + time.strftime('%Y-%m-%d', time.gmtime(t)) + '.jsonl'


class Enregistreur:
    """Écrit une mesure par minute ; supprime les fichiers de plus de 8 jours quand un nouveau jour commence."""

    def __init__(self, dossier, heure=time.time):
        self.dossier = Path(dossier)
        self.heure = heure
        self.prochaine = 0.0
        self.dernier_fichier = None

    def noter(self, ram, mesure, pression, procs, force=False, cpu=None, charge=None, temperature=None, actifs=None):
        """Écrit la mesure si une minute s'est écoulée. procs : [(nom, octets)] déjà triés ou non (3 plus gros gardés).
        Processeur (1.4.0, facultatif) : cpu (% moyen de la minute), charge (1 min), temperature (°C), actifs [[nom, cœurs]].
        Rend True si une ligne a été écrite. Ne lève jamais."""
        try:
            t = self.heure()
            if not force and t < self.prochaine:
                return False
            self.prochaine = t + PAS_S
            gros = sorted(procs, key=lambda x: -x[1])[:3]
            ligne = {'date': date_iso(t), 'ram': round(ram, 1), 'disponible_mo': mesure.get('disponible_mo'),
                     'swap_mo': mesure.get('swap_mo'), 'zram_mo': mesure.get('zram_mo'), 'pression': round(pression, 2),
                     'gros': [[n, int(o // 2 ** 20)] for n, o in gros],
                     'cpu': cpu, 'charge': charge, 'temperature': temperature, 'actifs': actifs}
            fichier = self.dossier / nom_fichier(t)
            self.dossier.mkdir(parents=True, exist_ok=True, mode=0o755)
            fd = os.open(fichier, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.fchmod(fd, 0o644)
                os.write(fd, (json.dumps(ligne, ensure_ascii=False) + '\n').encode())
            finally:
                os.close(fd)
            if fichier != self.dernier_fichier:
                self.dernier_fichier = fichier
                self.nettoyer(t)
            return True
        except Exception:   # l'historique est secondaire : jamais d'erreur vers la boucle
            return False

    def nettoyer(self, t):
        limite = nom_fichier(t - (JOURS_GARDES - 1) * 86400)
        for f in self.dossier.glob('historique-*.jsonl'):
            if MOTIF_FICHIER.match(f.name) and f.name < limite:
                try:
                    f.unlink()
                except OSError:
                    pass


def lire_mesures(dossier, depuis_s, maintenant):
    """Mesures (dicts) des fichiers du dossier dont la date est dans les depuis_s dernières secondes, triées."""
    debut = maintenant - depuis_s
    mesures = []
    for f in sorted(Path(dossier).glob('historique-*.jsonl')):
        m = MOTIF_FICHIER.match(f.name)
        if not m:
            continue
        try:   # un fichier est couvert par sa journée UTC : l'ignorer s'il finit avant le début de la période
            if calendar.timegm((int(m[1]), int(m[2]), int(m[3]), 0, 0, 0)) + 86400 < debut:
                continue
            lignes = f.read_text(encoding='utf-8', errors='replace').splitlines()
        except (OSError, ValueError, OverflowError):
            continue
        for ligne in lignes:
            try:
                e = json.loads(ligne)
            except ValueError:
                continue
            t = horodatage(e.get('date')) if isinstance(e, dict) else None
            if t is not None and debut <= t <= maintenant + 60:
                mesures.append((t, e))
    mesures.sort(key=lambda x: x[0])
    return mesures


def _moyenne(valeurs):
    v = [x for x in valeurs if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return sum(v) / len(v) if v else 0


def _nombres(valeurs):
    return [x for x in valeurs if isinstance(x, (int, float)) and not isinstance(x, bool)]


def _moyenne_ou_none(valeurs, decimales):
    v = _nombres(valeurs)
    return round(sum(v) / len(v), decimales) if v else None


def _maximum_ou_none(valeurs):
    v = _nombres(valeurs)
    return max(v) if v else None


def points(mesures, pas_long):
    """Points du contrat : un par mesure, ou moyennés par 10 min (maximum de `ram` gardé) si pas_long. Processeur (1.4.0) :
    `cpu`, `cpu_max`, `charge`, `temperature` ; les anciennes lignes, sans ces champs, donnent null et sont ignorées des
    moyennes. Pas long : cpu et charge moyennes, cpu_max et temperature les plus hautes."""
    def point(t, e):
        return {'date': date_iso(t), 'ram': e.get('ram'), 'swap_mo': e.get('swap_mo'), 'zram_mo': e.get('zram_mo'),
                'pression': e.get('pression'), 'cpu': e.get('cpu'), 'cpu_max': e.get('cpu'), 'charge': e.get('charge'),
                'temperature': e.get('temperature')}
    if not pas_long:
        return [point(t, e) for t, e in mesures]
    groupes = {}
    for t, e in mesures:
        groupes.setdefault(int(t // PAS_LONG_S), []).append((t, e))
    res = []
    for k in sorted(groupes):
        g = [e for _, e in groupes[k]]
        res.append({'date': date_iso(k * PAS_LONG_S),
                    'ram': round(max(_moyenne([e.get('ram')]) for e in g), 1),
                    'swap_mo': round(_moyenne([e.get('swap_mo') for e in g])),
                    'zram_mo': round(_moyenne([e.get('zram_mo') for e in g])),
                    'pression': round(_moyenne([e.get('pression') for e in g]), 2),
                    'cpu': _moyenne_ou_none([e.get('cpu') for e in g], 1),
                    'cpu_max': _maximum_ou_none([e.get('cpu') for e in g]),
                    'charge': _moyenne_ou_none([e.get('charge') for e in g], 2),
                    'temperature': _maximum_ou_none([e.get('temperature') for e in g])})
    return res


def actifs(mesures):
    """[{nom, pic_coeurs, minutes, cpu_min}] : plus haut nombre de cœurs vu de chaque programme, nombre de minutes où il était
    dans les 3 plus actifs, et somme des cœurs sur ces minutes (« minutes de processeur »). Trié par cpu_min décroissant."""
    vus = {}
    for _, e in mesures:
        liste = e.get('actifs')
        for item in liste if isinstance(liste, list) else []:
            try:
                nom, coeurs = item[0], float(item[1])
            except (TypeError, IndexError, ValueError):
                continue
            v = vus.setdefault(nom, {'nom': nom, 'pic_coeurs': 0.0, 'minutes': 0, 'cpu_min': 0.0})
            v['pic_coeurs'] = max(v['pic_coeurs'], coeurs)
            v['minutes'] += 1
            v['cpu_min'] += coeurs
    for v in vus.values():
        v['pic_coeurs'], v['cpu_min'] = round(v['pic_coeurs'], 2), round(v['cpu_min'], 2)
    return sorted(vus.values(), key=lambda v: (-v['cpu_min'], v['nom']))[:10]


def gros(mesures):
    """[{nom, pic_mo, minutes}] : plus haute valeur vue de chaque programme et nombre de mesures où il était dans les 3."""
    vus = {}
    for _, e in mesures:
        for item in e.get('gros') or []:
            try:
                nom, mo = item[0], item[1]
            except (TypeError, IndexError):
                continue
            v = vus.setdefault(nom, {'nom': nom, 'pic_mo': 0, 'minutes': 0})
            v['pic_mo'] = max(v['pic_mo'], mo)
            v['minutes'] += 1
    return sorted(vus.values(), key=lambda v: (-v['pic_mo'], v['nom']))[:10]


def evenements_periode(evenements, debut):
    incidents, travaux = [], []
    for e in evenements:
        t = horodatage(e.get('date'))
        if t is None or t < debut:
            continue
        if e.get('action') in ('fige', 'arrete', 'limite'):
            incidents.append(e)
        elif e.get('action') == 'travail-fin':
            travaux.append({'nom': e.get('nom'), 'date': e.get('date'), 'duree': e.get('duree'), 'code': e.get('code'),
                            'pic_go': e.get('pic_go')})
    return incidents, travaux


def donnees_historique(dossier, depuis_s, maintenant, evenements):
    mesures = lire_mesures(dossier, depuis_s, maintenant)
    long = depuis_s > SEUIL_LONG_S
    incidents, travaux = evenements_periode(evenements, maintenant - depuis_s)
    return {'depuis_s': depuis_s, 'pas_s': PAS_LONG_S if long else PAS_S, 'points': points(mesures, long),
            'incidents': incidents, 'gros': gros(mesures), 'actifs': actifs(mesures), 'travaux': travaux}


def analyser_depuis(texte):
    """Secondes d'une durée « 5min », « 1h », « 24h », « 7j » ; None si elle est invalide."""
    m = re.fullmatch(r'(\d+)\s*(min|h|j)', texte.strip())
    if not m or int(m[1]) == 0:
        return None
    return int(m[1]) * UNITES[m[2]]


def duree_lisible(s):
    s = round(s)
    if s >= 3600:
        return f'{s // 3600} h {s % 3600 // 60:02d} min'
    if s >= 60:
        return f'{s // 60} min {s % 60:02d} s'
    return f'{s} s'


def _ligne_processeur(pts):
    """« Processeur : 18 % en moyenne, au plus 96 % (14:32) ; 72 °C au plus » ; None sans mesure du processeur."""
    avec = [p for p in pts if isinstance(p.get('cpu'), (int, float))]
    temps = _nombres([p.get('temperature') for p in pts])
    if not avec:
        return None
    moyenne = sum(p['cpu'] for p in avec) / len(avec)
    pic = max(avec, key=lambda p: p.get('cpu_max') if isinstance(p.get('cpu_max'), (int, float)) else p['cpu'])
    haut = pic['cpu_max'] if isinstance(pic.get('cpu_max'), (int, float)) else pic['cpu']
    t = horodatage(pic.get('date'))
    heure = f' ({time.strftime("%H:%M", time.localtime(t))})' if t is not None else ''
    texte = f'Processeur : {moyenne:.0f} % en moyenne, au plus {haut:.0f} %{heure}'
    if temps:
        texte += f' ; {max(temps):.0f} °C au plus'
    return texte


def texte_historique(d, ligne_evenement):
    """Résumé lisible de donnees_historique."""
    pts = d['points']
    sortie = [f'Historique sur {duree_lisible(d["depuis_s"])}']
    if not pts:
        sortie.append('Aucune mesure sur cette période.')
    else:
        rams = [p['ram'] for p in pts if p.get('ram') is not None]
        sortie.append(f'{len(pts)} mesures (une toutes les {duree_lisible(d["pas_s"])}).')
        if rams:
            sortie.append(f'RAM : moyenne {sum(rams) / len(rams):.0f} %, maximale {max(rams):.0f} %')
        swaps = [p['swap_mo'] for p in pts if p.get('swap_mo') is not None]
        if swaps:
            sortie.append(f'Swap utilisé au plus haut : {max(swaps)} Mo')
        press = [p['pression'] for p in pts if p.get('pression') is not None]
        if press:
            sortie.append(f'Pression mémoire (full avg10) au plus haut : {max(press):.1f} %')
        ligne_cpu = _ligne_processeur(pts)
        if ligne_cpu:
            sortie.append(ligne_cpu)
    if d.get('actifs'):
        sortie.append('Les plus actifs : ' + ', '.join(f'{a["nom"]} ({a["cpu_min"]:.0f} min de processeur)'
                                                       for a in d['actifs'][:5]))
    sortie.append(f'Incidents : {len(d["incidents"])}' if d['incidents'] else 'Incidents : aucun')
    for e in d['incidents'][-10:]:
        sortie.append('  ' + ligne_evenement(e))
    if d['gros']:
        sortie.append('Plus gros programmes :')
        for g in d['gros'][:5]:
            sortie.append(f'  {g["nom"]} : {g["pic_mo"]} Mo au plus haut, parmi les 3 plus gros pendant {g["minutes"]} min')
    if d['travaux']:
        sortie.append(f'Travaux de la file : {len(d["travaux"])}')
        for w in d['travaux'][-10:]:
            pic = f', pic {w["pic_go"]} Go' if w.get('pic_go') is not None else ''
            duree = duree_lisible(w['duree']) if isinstance(w.get('duree'), (int, float)) else '?'
            sortie.append(f'  {w["nom"]} : {duree}, code {w.get("code")}{pic}')
    else:
        sortie.append('Travaux de la file : aucun')
    return '\n'.join(sortie)


def commande_historique(args, ctx, maintenant=None):
    depuis, json_ = DEPUIS_DEFAUT, False
    args = list(args)
    while args:
        a = args.pop(0)
        if a == '--json':
            json_ = True
        elif a == '--depuis' and args:
            depuis = args.pop(0)
        elif a.startswith('--depuis='):
            depuis = a.split('=', 1)[1]
        else:
            print('usage : gagarine historique [--depuis 5min|1h|24h|7j] [--json]', file=sys.stderr)
            return 2
    depuis_s = analyser_depuis(depuis)
    if depuis_s is None:
        print(f'durée invalide : {depuis!r} (exemples : 5min, 1h, 24h, 7j)', file=sys.stderr)
        return 2
    dossier = dossier_etat_lecture(ctx.env)
    maintenant = time.time() if maintenant is None else maintenant
    evenements = lire_evenements(fichiers_journaux(ctx.env), n=100000)
    d = donnees_historique(dossier, depuis_s, maintenant, evenements)
    if json_:
        print(json.dumps(d, ensure_ascii=False))
    elif not d['points'] and not any(Path(dossier).glob('historique-*.jsonl')):
        print(f'Pas encore d\'historique dans {dossier} : le service en écrit une mesure par minute (version 1.2.0 '
              f'ou plus, service en marche).')
    else:
        print(texte_historique(d, ligne_lisible))
    return 0


COMMANDES = {'historique': commande_historique}
