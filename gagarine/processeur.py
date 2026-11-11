"""Le processeur : utilisation (totale, par cœur, par programme), température, fréquence, charge et pression.

Des fonctions pures lisent des textes (`/proc/stat`, `/proc/PID/stat`, `/proc/cpuinfo`…). Les lectures du système sont
confiées à `Lecteur` (racines `/proc` et `/sys` remplaçables : les tests y mettent de faux dossiers). L'`Echantillonneur`
garde le relevé précédent et produit l'objet `processeur` du contrat (docs/conception-1.4.0.md, sections 1 et 2). Le
service l'écrit toutes les 2 s dans `processeur.json` ; les commandes le relisent s'il est frais, sinon le mesurent.

Ce module n'importe aucun autre module de Gagarine : il reste léger (le service tourne avec 128 Mo).
"""
import json
import os
import re
import sys
import time

PAS_S = 2.0                  # secondes entre deux mesures du service
FRAICHEUR_S = 6.0            # une mesure du service plus vieille n'est pas utilisée
DUREE_DIRECTE_S = 0.5        # durée d'une mesure prise par la commande elle-même
NOM_FICHIER = 'processeur.json'
DOSSIER_EXECUTION = '/run/gagarine'
MAX_ACTIFS = 10
SEUIL_ACTIF = 0.01           # cœurs : en dessous, un programme n'est pas listé
CHAUD_MARGE = 15.0           # chaud = température ≥ critique − 15 °C
CHAUD_DEFAUT = 90.0          # … ou ≥ 90 °C si la critique est inconnue
ACTIFS_MINUTE = 3            # programmes gardés par minute dans l'historique

AIDE_COMMANDES = '  processeur [--json]            utilisation du processeur : cœurs, température, fréquence, plus actifs\n'


# ======================================================================================================================
# Fonctions pures sur des textes
# ======================================================================================================================
def _entiers(champs):
    return [int(c) for c in champs]


def total_et_occupe(champs):
    """(total, occupé) d'une ligne `cpu` de /proc/stat, champs après le nom. guest et guest_nice ne sont pas ajoutés : le
    noyau les compte déjà dans user et nice. occupé = total − idle − iowait."""
    v = _entiers(champs[:8]) + [0] * (8 - len(champs[:8]))
    user, nice, system, idle, iowait, irq, softirq, steal = v
    total = user + nice + system + idle + iowait + irq + softirq + steal
    return total, total - idle - iowait


def analyser_stat(texte):
    """((total, occupé) de l'ensemble, [(total, occupé) de cpu0, cpu1…]) d'après /proc/stat."""
    ensemble, coeurs = None, {}
    for ligne in texte.splitlines():
        champs = ligne.split()
        if not champs or not champs[0].startswith('cpu'):
            continue
        if champs[0] == 'cpu':
            ensemble = total_et_occupe(champs[1:])
        elif champs[0][3:].isdigit():
            coeurs[int(champs[0][3:])] = total_et_occupe(champs[1:])
    if ensemble is None:
        raise ValueError('pas de ligne « cpu » dans /proc/stat')
    return ensemble, [coeurs[k] for k in sorted(coeurs)]


def pourcent(avant, apres):
    """% occupé entre deux relevés (total, occupé) ; 0.0 si le temps n'a pas avancé."""
    dt = apres[0] - avant[0]
    return round(100 * max(0, apres[1] - avant[1]) / dt, 1) if dt > 0 else 0.0


def analyser_stat_processus(texte):
    """{nom, etat, ppid, ticks, debut} d'après /proc/PID/stat. On coupe après la dernière « ) » : le nom peut contenir des
    espaces et des parenthèses. ticks = utime + stime (le noyau y met déjà tous les fils d'exécution) ; pas de cutime,
    qui compterait deux fois le temps des programmes enfants."""
    ouvre, ferme = texte.index('('), texte.rindex(')')
    reste = texte[ferme + 2:].split()
    return {'nom': texte[ouvre + 1:ferme], 'etat': reste[0], 'ppid': int(reste[1]),
            'ticks': int(reste[11]) + int(reste[12]), 'debut': reste[19]}


def analyser_modele(texte):
    """Premier « model name » de /proc/cpuinfo, sinon « Hardware » (ARM), sinon None."""
    for cle in ('model name', 'Hardware'):
        m = re.search(rf'^{cle}\s*:\s*(.+?)\s*$', texte, re.M)
        if m:
            return m[1]
    return None


def analyser_charge(texte):
    """[1 min, 5 min, 15 min] d'après /proc/loadavg."""
    return [float(x) for x in texte.split()[:3]]


def analyser_pression(texte):
    """« some avg10 » (en %) de /proc/pressure/cpu ; 0.0 si la ligne manque."""
    m = re.search(r'^some\s.*?\bavg10=([\d.]+)', texte or '', re.M)
    return float(m[1]) if m else 0.0


def analyser_liste_coeurs(texte):
    """Liste de cœurs « 0-3,8 » → [0, 1, 2, 3, 8] ; None si le texte est vide ou invalide."""
    res = []
    try:
        for morceau in texte.strip().split(','):
            if '-' in morceau:
                a, b = morceau.split('-')
                res.extend(range(int(a), int(b) + 1))
            else:
                res.append(int(morceau))
    except (ValueError, AttributeError):
        return None
    return res or None


def types_des_coeurs(performants, economes, nb):
    """["P", "P", …, "E", …] pour nb cœurs logiques, ou None si le processeur n'est pas hybride (ou si les listes ne
    couvrent pas exactement les cœurs)."""
    p, e = analyser_liste_coeurs(performants or ''), analyser_liste_coeurs(economes or '')
    if not p or not e or set(p) & set(e) or sorted(p + e) != list(range(nb)):
        return None
    return ['P' if k in p else 'E' for k in range(nb)]


def est_chaud(temperature, critique):
    if temperature is None:
        return False
    return temperature >= (critique - CHAUD_MARGE if critique is not None else CHAUD_DEFAUT)


def fr(valeur, decimales=1):
    """Un nombre à la française : virgule décimale (2,4)."""
    return f'{valeur:.{decimales}f}'.replace('.', ',')


def date_locale(t):
    return time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(t))


# ======================================================================================================================
# Lectures du système (minces, remplaçables)
# ======================================================================================================================
class Lecteur:
    """Ce que le module demande à /proc et /sys. Les tests donnent d'autres racines."""

    def __init__(self, proc='/proc', sys='/sys'):
        self.proc = proc
        self.sys = sys

    @staticmethod
    def _lire(chemin):
        try:
            with open(chemin, encoding='utf-8', errors='replace') as f:
                return f.read()
        except OSError:
            return None

    def texte_proc(self, rel):
        return self._lire(os.path.join(self.proc, rel))

    def texte_sys(self, rel):
        return self._lire(os.path.join(self.sys, rel))

    def lister_sys(self, rel):
        try:
            return sorted(os.listdir(os.path.join(self.sys, rel)))
        except OSError:
            return []

    def programmes(self):
        """[(pid, texte de /proc/PID/stat)] ; un processus qui disparaît pendant la lecture est ignoré."""
        res = []
        try:
            noms = os.listdir(self.proc)
        except OSError:
            return res
        for n in noms:
            if n.isdigit():
                t = self._lire(os.path.join(self.proc, n, 'stat'))
                if t:
                    res.append((int(n), t))
        return res

    def uid(self, pid):
        try:
            return os.stat(os.path.join(self.proc, str(pid))).st_uid
        except OSError:
            return None


def _nombre(texte, diviseur=1.0):
    try:
        return int(texte.strip()) / diviseur
    except (ValueError, AttributeError):
        return None


def trouver_capteur(lecteur):
    """(chemin de la température, chemin de la critique ou None) du premier capteur du processeur, dans l'ordre du contrat
    (docs/conception-1.4.0.md, section 1) ; None si aucun."""
    capteurs = []   # (nom du pilote, {libellé: n}, {n: libellé})
    for d in lecteur.lister_sys('class/hwmon'):
        base = f'class/hwmon/{d}'
        nom = (lecteur.texte_sys(f'{base}/name') or '').strip()
        libelles = {}
        for f in lecteur.lister_sys(base):
            m = re.fullmatch(r'temp(\d+)_label', f)
            if m:
                libelles[(lecteur.texte_sys(f'{base}/{f}') or '').strip()] = m[1]
        capteurs.append((nom, base, libelles, lecteur.lister_sys(base)))
    ordre = (('coretemp', ('Package id 0',)), ('k10temp', ('Tctl', 'Tdie')), ('zenpower', ('Tdie',)),
             ('cpu_thermal', (None,)), ('soc_thermal', (None,)))
    for pilote, etiquettes in ordre:
        for etiquette in etiquettes:
            for nom, base, libelles, fichiers in capteurs:
                if nom != pilote:
                    continue
                n = '1' if etiquette is None else libelles.get(etiquette)
                if n and f'temp{n}_input' in fichiers:
                    crit = f'{base}/temp{n}_crit' if f'temp{n}_crit' in fichiers else None
                    return f'{base}/temp{n}_input', crit
    zones = {}
    for z in lecteur.lister_sys('class/thermal'):
        if z.startswith('thermal_zone'):
            zones.setdefault((lecteur.texte_sys(f'class/thermal/{z}/type') or '').strip(), z)
    for type_ in ('x86_pkg_temp', 'TCPU', 'cpu-thermal'):
        z = zones.get(type_)
        if z:
            base = f'class/thermal/{z}'
            crit = None
            for f in lecteur.lister_sys(base):
                m = re.fullmatch(r'trip_point_(\d+)_type', f)
                if m and (lecteur.texte_sys(f'{base}/{f}') or '').strip() == 'critical':
                    crit = f'{base}/trip_point_{m[1]}_temp'
            return f'{base}/temp', crit
    return None


def lire_temperature(lecteur, capteur):
    """(°C à une décimale, critique en °C ou None) du capteur ; (None, None) s'il est illisible."""
    if not capteur:
        return None, None
    t = _nombre(lecteur.texte_sys(capteur[0]), 1000.0)
    if t is None:
        return None, None
    c = _nombre(lecteur.texte_sys(capteur[1]), 1000.0) if capteur[1] else None
    return round(t, 1), (round(c, 1) if c is not None else None)


def lire_frequences(lecteur):
    """(moyenne des fréquences actuelles, maximum) en MHz entiers ; None si le système ne les donne pas."""
    actuelles, maximum = [], []
    for c in lecteur.lister_sys('devices/system/cpu'):
        if re.fullmatch(r'cpu\d+', c):
            f = _nombre(lecteur.texte_sys(f'devices/system/cpu/{c}/cpufreq/scaling_cur_freq'), 1000.0)
            m = _nombre(lecteur.texte_sys(f'devices/system/cpu/{c}/cpufreq/cpuinfo_max_freq'), 1000.0)
            if f is not None:
                actuelles.append(f)
            if m is not None:
                maximum.append(m)
    return (round(sum(actuelles) / len(actuelles)) if actuelles else None, round(max(maximum)) if maximum else None)


def lire_types(lecteur, nb):
    """(types, nombre de cœurs physiques performants) : (None, None) si le processeur n'est pas hybride."""
    perf = lecteur.texte_sys('devices/cpu_core/cpus')
    types = types_des_coeurs(perf, lecteur.texte_sys('devices/cpu_atom/cpus'), nb)
    if types is None:
        return None, None
    ids = {lecteur.texte_sys(f'devices/system/cpu/cpu{k}/topology/core_id') for k, t in enumerate(types) if t == 'P'}
    ids.discard(None)
    return types, (len(ids) or None)


# ======================================================================================================================
# Échantillonneur
# ======================================================================================================================
class Echantillonneur:
    """Garde le relevé précédent (total, par cœur, pid → ticks) et produit l'objet `processeur`.

    Un programme apparu entre deux relevés n'est pas compté avant le suivant (il n'a pas de relevé précédent) : cela évite
    de lire l'heure de démarrage, et un numéro de processus repris par un autre programme n'est jamais confondu avec
    l'ancien (le démarrage est comparé). Le premier relevé n'a rien à comparer : utilisation et programmes valent null."""

    def __init__(self, lecteur=None, hz=None, horloge=time.monotonic, heure=time.time, nom_utilisateur=None):
        self.lecteur = lecteur or Lecteur()
        self.hz = hz or os.sysconf('SC_CLK_TCK')
        self.horloge = horloge
        self.heure = heure
        self.nom_utilisateur = nom_utilisateur or _nom_utilisateur
        self.precedent = None    # (instant, (total, occupé), [par cœur], {pid: (nom, ticks, debut, ppid)})
        self.dernier = None      # dernier objet produit
        self.statique = None     # modèle, types… : ne changent pas pendant que le service tourne
        self.capteur = None
        self.vider_minute()

    # --- cumul sur la minute (historique) ---
    def vider_minute(self):
        self.min_total = [0, 0]
        self.min_noms = {}
        self.min_duree = 0.0

    def prendre_minute(self):
        """Ce que la ligne de la minute garde : cpu (% moyen depuis la dernière prise), charge, température, 3 plus actifs
        (ticks additionnés par nom : les 20 `cc1` d'une compilation forment une ligne). Vide ensuite le cumul."""
        cpu = round(100 * self.min_total[1] / self.min_total[0], 1) if self.min_total[0] > 0 else None
        actifs = []
        if self.min_duree > 0:
            for nom, ticks in sorted(self.min_noms.items(), key=lambda x: -x[1]):
                coeurs = round(ticks / self.hz / self.min_duree, 2)
                if coeurs >= SEUIL_ACTIF and len(actifs) < ACTIFS_MINUTE:
                    actifs.append([nom, coeurs])
        d = self.dernier or {}
        charge = d.get('charge')
        res = {'cpu': cpu, 'charge': charge[0] if charge else None, 'temperature': d.get('temperature'), 'actifs': actifs}
        self.vider_minute()
        return res

    # --- relevé ---
    def _fixes(self, nb):
        if self.statique is None or self.statique.get('nb') != nb:
            texte = self.lecteur.texte_proc('cpuinfo')
            types, perf = lire_types(self.lecteur, nb)
            self.statique = {'nb': nb, 'modele': analyser_modele(texte) if texte else None, 'types': types,
                             'performants': perf, 'frequence_max': lire_frequences(self.lecteur)[1]}
        return self.statique

    def mesurer(self, source='service'):
        """Relève le système et rend l'objet `processeur`. Un champ illisible vaut null ; seule une panne totale de
        /proc/stat et de /proc/PID/stat laisse un objet presque vide."""
        instant, t = self.horloge(), self.heure()
        obj = {'t': round(t, 2), 'date': date_locale(t), 'source': source, 'intervalle_s': None, 'pourcent': None,
               'nb_coeurs': None, 'par_coeur': None, 'types': None, 'coeurs_performants': None, 'modele': None, 'charge': None, 'pression': None,
               'temperature': None, 'temperature_critique': None, 'chaud': False, 'frequence_mhz': None,
               'frequence_max_mhz': None, 'plus_actifs': None}
        ensemble, coeurs = None, []
        try:
            ensemble, coeurs = analyser_stat(self.lecteur.texte_proc('stat') or '')
        except (ValueError, IndexError):
            pass
        programmes = {}
        try:
            for pid, texte in self.lecteur.programmes():
                try:
                    p = analyser_stat_processus(texte)
                except (ValueError, IndexError):
                    continue
                programmes[pid] = (p['nom'], p['ticks'], p['debut'], p['ppid'])
        except Exception:
            programmes = None
        avant = self.precedent
        dt = instant - avant[0] if avant else 0.0
        if dt > 0 and ensemble is not None and avant[1] is not None:
            obj['intervalle_s'] = round(dt, 2)
            obj['pourcent'] = pourcent(avant[1], ensemble)
            if len(avant[2]) == len(coeurs):
                obj['par_coeur'] = [pourcent(a, b) for a, b in zip(avant[2], coeurs)]
            self.min_total[0] += max(0, ensemble[0] - avant[1][0])
            self.min_total[1] += max(0, ensemble[1] - avant[1][1])
            self.min_duree += dt
        nb = len(coeurs) or os.cpu_count() or None
        obj['nb_coeurs'] = nb
        if programmes is not None and avant and dt > 0 and avant[3] is not None:
            obj['plus_actifs'] = self._actifs(programmes, avant[3], dt, nb)
        if ensemble is not None or programmes is not None:
            self.precedent = (instant, ensemble if ensemble is not None else (avant[1] if avant else None),
                              coeurs if ensemble is not None else (avant[2] if avant else []),
                              programmes if programmes is not None else (avant[3] if avant else None))
        for champ, f in (('statique', lambda: self._champs_fixes(obj, nb)), ('charge', lambda: self._charge(obj)),
                         ('pression', lambda: self._pression(obj)), ('temperature', lambda: self._temperature(obj)),
                         ('frequence', lambda: self._frequence(obj))):
            try:
                f()
            except Exception:
                pass
        self.dernier = obj
        return obj

    def _actifs(self, programmes, avant, dt, nb):
        """Les plus actifs depuis le relevé précédent ; ajoute au passage les ticks de la minute, par nom."""
        liste = []
        for pid, (nom, ticks, debut, ppid) in programmes.items():
            ancien = avant.get(pid)
            if ancien is None or ancien[2] != debut:
                continue   # apparu entre deux relevés (ou numéro repris) : compté au prochain
            delta = max(0, ticks - ancien[1])
            if delta:
                self.min_noms[nom] = self.min_noms.get(nom, 0) + delta
                liste.append((delta, pid, nom, ppid))
        res = []
        for delta, pid, nom, ppid in sorted(liste, reverse=True):
            coeurs = delta / self.hz / dt
            if coeurs < SEUIL_ACTIF or len(res) >= MAX_ACTIFS:
                break
            uid = self.lecteur.uid(pid)
            res.append({'pid': pid, 'nom': nom, 'coeurs': round(coeurs, 2),
                        'pourcent': round(coeurs / nb * 100, 1) if nb else None,
                        'utilisateur': self.nom_utilisateur(uid) if uid is not None else None,
                        'noyau': pid == 2 or ppid == 2})
        return res

    def _champs_fixes(self, obj, nb):
        f = self._fixes(nb)
        obj['modele'], obj['types'], obj['frequence_max_mhz'] = f['modele'], f['types'], f['frequence_max']
        obj['coeurs_performants'] = f['performants']

    def _charge(self, obj):
        obj['charge'] = analyser_charge(self.lecteur.texte_proc('loadavg'))

    def _pression(self, obj):
        obj['pression'] = analyser_pression(self.lecteur.texte_proc('pressure/cpu'))

    def _temperature(self, obj):
        if self.capteur is None:
            self.capteur = trouver_capteur(self.lecteur)
        temperature, critique = lire_temperature(self.lecteur, self.capteur)
        if temperature is None:
            self.capteur = None   # capteur disparu : on le cherchera de nouveau
        obj['temperature'], obj['temperature_critique'] = temperature, critique
        obj['chaud'] = est_chaud(temperature, critique)

    def _frequence(self, obj):
        obj['frequence_mhz'] = lire_frequences(self.lecteur)[0]


def _nom_utilisateur(uid):
    import pwd
    try:
        return pwd.getpwuid(uid).pw_name
    except (KeyError, OverflowError):
        return str(uid)


def mesure_directe(lecteur=None, duree=DUREE_DIRECTE_S, dormir=time.sleep, **kw):
    """Deux relevés à `duree` secondes d'écart (0,5 s) ; rend l'objet `processeur` (source « directe »)."""
    e = Echantillonneur(lecteur, **kw)
    e.mesurer()
    dormir(duree)
    return e.mesurer('directe')


# ======================================================================================================================
# processeur.json
# ======================================================================================================================
def dossier_ecriture(env=None, root=None):
    """Où le service écrit : $RUNTIME_DIRECTORY, sinon $GAGARINE_DOSSIER_EXECUTION, sinon /run/gagarine s'il est
    administrateur, sinon nulle part (None)."""
    env = os.environ if env is None else env
    root = (os.geteuid() == 0) if root is None else root
    d = (env.get('RUNTIME_DIRECTORY') or '').split(':')[0] or env.get('GAGARINE_DOSSIER_EXECUTION')
    return d or (DOSSIER_EXECUTION if root else None)


def dossiers_lecture(env=None):
    """Où lire : $GAGARINE_DOSSIER_EXECUTION puis /run/gagarine."""
    env = os.environ if env is None else env
    d = env.get('GAGARINE_DOSSIER_EXECUTION')
    return [d, DOSSIER_EXECUTION] if d else [DOSSIER_EXECUTION]


def ecrire_fichier(dossier, obj):
    """Écrit processeur.json de façon atomique (fichier temporaire puis renommage), en 0644."""
    os.makedirs(dossier, mode=0o755, exist_ok=True)
    final = os.path.join(dossier, NOM_FICHIER)
    tmp = final + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False))
    os.chmod(tmp, 0o644)
    os.replace(tmp, final)


def lire_fichier(env=None, maintenant=time.time):
    """La mesure du service si elle est fraîche (`t` de 6 s au plus), sinon None (absente, illisible ou trop ancienne)."""
    for d in dossiers_lecture(env):
        try:
            with open(os.path.join(d, NOM_FICHIER), encoding='utf-8') as f:
                obj = json.load(f)
            age = maintenant() - obj['t']
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(obj, dict) and -FRAICHEUR_S <= age <= FRAICHEUR_S:
            return obj
    return None


def obtenir(env=None, directe=True, maintenant=time.time, mesurer=None):
    """La mesure du service si elle est fraîche, sinon (si `directe`) une mesure directe sur 0,5 s, sinon None."""
    obj = lire_fichier(env, maintenant)
    if obj is None and directe:
        try:
            obj = (mesurer or mesure_directe)()
        except Exception:
            obj = None
    return obj


# ======================================================================================================================
# Textes (à la française)
# ======================================================================================================================
def _coeurs(v):
    return f'{fr(v, 2 if v < 0.1 else 1)} cœur{"s" if v >= 2 else ""}'


def ghz(mhz):
    return f'{fr(mhz / 1000)} GHz'


def ligne_resume(p):
    """« Processeur : 23 % (charge 2,41 pour 14 cœurs) · 72 °C · 1,9 GHz » ; None si la mesure est vide."""
    if not p or p.get('pourcent') is None:
        return None
    detail = []
    if p.get('charge'):
        detail.append(f'charge {fr(p["charge"][0], 2)}' + (f' pour {p["nb_coeurs"]} cœurs' if p.get('nb_coeurs') else ''))
    texte = f'Processeur : {p["pourcent"]:.0f} %' + (f' ({", ".join(detail)})' if detail else '')
    if p.get('temperature') is not None:
        texte += f' · {p["temperature"]:.0f} °C' + (' (chaud)' if p.get('chaud') else '')
    if p.get('frequence_mhz') is not None:
        texte += f' · {ghz(p["frequence_mhz"])}'
    return texte


def ligne_actifs(p, n=3):
    """« Plus actifs : ffmpeg 6,2 cœurs, firefox 0,8 cœur » ; None s'il n'y en a pas."""
    actifs = (p or {}).get('plus_actifs') or []
    if not actifs:
        return None
    return 'Plus actifs : ' + ', '.join(f'{a["nom"]} {_coeurs(a["coeurs"])}' for a in actifs[:n])


def description_coeurs(p):
    """« 2 cœurs performants (4 fils) et 10 économes », ou « 14 cœurs »."""
    nb, types = p.get('nb_coeurs'), p.get('types')
    if not nb:
        return None
    if not types:
        return f'{nb} cœurs'
    fils, eco = types.count('P'), types.count('E')
    perf = p.get('coeurs_performants')
    if perf and perf != fils:
        return f'{perf} cœurs performants ({fils} fils) et {eco} économes'
    return f'{fils} cœurs performants et {eco} économes'


def barre(pct, largeur=10):
    pleines = max(0, min(largeur, round(pct / 100 * largeur)))
    return '█' * pleines + '░' * (largeur - pleines)


def texte_processeur(p):
    """Le détail de `gagarine processeur`."""
    if not p:
        return 'Mesure du processeur indisponible.'
    sortie = []
    cartouche = ' · '.join(x for x in (p.get('modele'), description_coeurs(p)) if x)
    if cartouche:
        sortie.append(cartouche)
    if p.get('pourcent') is not None:
        sortie.append(f'Utilisation : {p["pourcent"]:.0f} %')
    types = p.get('types') or []
    for k, v in enumerate(p.get('par_coeur') or []):
        sortie.append(f'  cpu{k:<3}{types[k] if k < len(types) else " "}  {barre(v)}  {v:.0f} %')
    if p.get('temperature') is not None:
        crit = f' (critique {p["temperature_critique"]:.0f} °C)' if p.get('temperature_critique') is not None else ''
        sortie.append(f'Température : {p["temperature"]:.0f} °C{crit}' + (' : chaud' if p.get('chaud') else ''))
    if p.get('frequence_mhz') is not None:
        maxi = f' / maximum {ghz(p["frequence_max_mhz"])}' if p.get('frequence_max_mhz') else ''
        sortie.append(f'Fréquence : {ghz(p["frequence_mhz"])}{maxi}')
    if p.get('charge'):
        sortie.append('Charge : ' + ' '.join(fr(c, 2) for c in p['charge']) + ' (1, 5 et 15 min)')
    if p.get('pression') is not None:
        sortie.append(f'Pression : {fr(p["pression"])} % (some avg10)')
    actifs = p.get('plus_actifs') or []
    if actifs:
        sortie.append('Les plus actifs :')
        for a in actifs:
            qui = f'processus {a["pid"]}' + (f', {a["utilisateur"]}' if a.get('utilisateur') else '')
            noyau = ' (noyau)' if a.get('noyau') else ''
            pc = f' · {a["pourcent"]:.0f} %' if a.get('pourcent') is not None else ''
            sortie.append(f'  {a["nom"]}{noyau} ({qui}) : {_coeurs(a["coeurs"])}{pc}')
    elif p.get('plus_actifs') is not None:
        sortie.append('Aucun programme n\'utilise le processeur de façon notable.')
    return '\n'.join(sortie)


def commande_processeur(args, ctx):
    """gagarine processeur [--json] : la mesure du service si elle est fraîche, sinon une mesure directe sur 0,5 s."""
    if args not in ([], ['--json']):
        print('usage : gagarine processeur [--json]', file=sys.stderr)
        return 2
    p = obtenir(ctx.env)
    if args:
        print(json.dumps(p, ensure_ascii=False))
    else:
        print(texte_processeur(p))
    return 0


COMMANDES = {'processeur': commande_processeur}
