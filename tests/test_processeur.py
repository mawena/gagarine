"""Tests du processeur : analyseurs de textes, échantillonneur sur de faux /proc et /sys, processeur.json, textes, commande."""
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gagarine import processeur as pr

HZ = 100


def stat_cpu(nom, user, idle, system=0, iowait=0, guest=0):
    return f'{nom} {user} 0 {system} {idle} {iowait} 0 0 0 {guest} 0'


def stat_processus(pid, nom, utime, stime=0, ppid=1, debut=100):
    champs = ['S', str(ppid)] + ['0'] * 9 + [str(utime), str(stime), '0', '0', '20', '0', '1', '0', str(debut)]
    return f'{pid} ({nom}) ' + ' '.join(champs) + ' 0 0'


class Monde:
    """De faux /proc et /sys dans un dossier temporaire, modifiables entre deux relevés."""

    def __init__(self, testcase, coeurs=2):
        self.tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.tmp.cleanup)
        self.racine = Path(self.tmp.name)
        self.proc, self.sys = self.racine / 'proc', self.racine / 'sys'
        self.proc.mkdir()
        self.sys.mkdir()
        self.coeurs = coeurs
        self.lecteur = pr.Lecteur(str(self.proc), str(self.sys))

    def ecrire(self, chemin, texte):
        f = self.racine / chemin
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(texte)

    def cpu(self, ensemble, *par_coeur):
        self.ecrire('proc/stat', '\n'.join([ensemble, *par_coeur, 'intr 1 2 3', 'btime 1']) + '\n')

    def programme(self, pid, nom, utime, stime=0, ppid=1, debut=100):
        self.ecrire(f'proc/{pid}/stat', stat_processus(pid, nom, utime, stime, ppid, debut))

    def retirer(self, pid):
        for f in (self.proc / str(pid)).iterdir():
            f.unlink()
        (self.proc / str(pid)).rmdir()


class TestAnalyseurs(unittest.TestCase):
    def test_total_et_occupe(self):
        # guest (9e champ) déjà compté dans user : jamais ajouté ; occupé = total − idle − iowait
        self.assertEqual(pr.total_et_occupe('100 10 50 800 40 5 5 10 99 9'.split()), (1020, 180))

    def test_analyser_stat_ensemble_et_coeurs_dans_l_ordre(self):
        texte = '\n'.join([stat_cpu('cpu', 30, 70), stat_cpu('cpu1', 20, 30), stat_cpu('cpu0', 10, 40), 'intr 1']) + '\n'
        ensemble, coeurs = pr.analyser_stat(texte)
        self.assertEqual(ensemble, (100, 30))
        self.assertEqual(coeurs, [(50, 10), (50, 20)])
        with self.assertRaises(ValueError):
            pr.analyser_stat('intr 1\n')

    def test_pourcent(self):
        self.assertEqual(pr.pourcent((100, 10), (200, 60)), 50.0)
        self.assertEqual(pr.pourcent((100, 10), (100, 10)), 0.0)

    def test_stat_processus_nom_avec_espaces_et_parentheses(self):
        p = pr.analyser_stat_processus(stat_processus(7, 'Web Content (x) y', 120, 30, ppid=2, debut=555))
        self.assertEqual((p['nom'], p['ticks'], p['ppid'], p['debut'], p['etat']), ('Web Content (x) y', 150, 2, '555', 'S'))

    def test_modele(self):
        self.assertEqual(pr.analyser_modele('processor : 0\nmodel name\t: Intel Truc 7\nmodel name\t: autre\n'), 'Intel Truc 7')
        self.assertEqual(pr.analyser_modele('Hardware\t: BCM2711\n'), 'BCM2711')
        self.assertIsNone(pr.analyser_modele('processor : 0\n'))

    def test_charge_et_pression(self):
        self.assertEqual(pr.analyser_charge('2.41 1.98 1.50 3/900 1234\n'), [2.41, 1.98, 1.5])
        self.assertEqual(pr.analyser_pression('some avg10=0.72 avg60=0.26 avg300=0.40 total=5\nfull avg10=0.10 avg60=0 avg300=0 total=0'), 0.72)
        self.assertEqual(pr.analyser_pression(None), 0.0)
        self.assertEqual(pr.analyser_pression('rien'), 0.0)

    def test_listes_de_coeurs(self):
        self.assertEqual(pr.analyser_liste_coeurs('0-3,8\n'), [0, 1, 2, 3, 8])
        self.assertEqual(pr.analyser_liste_coeurs('5'), [5])
        self.assertIsNone(pr.analyser_liste_coeurs(''))
        self.assertIsNone(pr.analyser_liste_coeurs('a-b'))
        self.assertIsNone(pr.analyser_liste_coeurs(None))

    def test_types_hybride_ou_non(self):
        self.assertEqual(pr.types_des_coeurs('0-1', '2-3', 4), ['P', 'P', 'E', 'E'])
        self.assertIsNone(pr.types_des_coeurs(None, None, 4))
        self.assertIsNone(pr.types_des_coeurs('0-3', None, 4))
        self.assertIsNone(pr.types_des_coeurs('0-1', '2-5', 4))   # ne couvre pas les cœurs
        self.assertIsNone(pr.types_des_coeurs('0-2', '2-3', 4))   # chevauchement

    def test_chaud(self):
        self.assertTrue(pr.est_chaud(95.0, 110.0))
        self.assertFalse(pr.est_chaud(94.9, 110.0))
        self.assertTrue(pr.est_chaud(90.0, None))
        self.assertFalse(pr.est_chaud(89.9, None))
        self.assertFalse(pr.est_chaud(None, 110.0))

    def test_nombres_a_la_francaise(self):
        self.assertEqual(pr.fr(2.41, 2), '2,41')
        self.assertEqual(pr.ghz(1850), '1,9 GHz')


class TestCapteurs(unittest.TestCase):
    def setUp(self):
        self.m = Monde(self)

    def hwmon(self, n, nom, **fichiers):
        self.m.ecrire(f'sys/class/hwmon/hwmon{n}/name', nom + '\n')
        for f, v in fichiers.items():
            self.m.ecrire(f'sys/class/hwmon/hwmon{n}/{f}', str(v) + '\n')

    def temperature(self):
        capteur = pr.trouver_capteur(self.m.lecteur)
        return pr.lire_temperature(self.m.lecteur, capteur)

    def test_coretemp_package_et_critique(self):
        self.hwmon(0, 'acpitz', temp1_input=40000)
        self.hwmon(1, 'coretemp', temp1_label='Package id 0', temp1_input=72043, temp1_crit=110000,
                   temp2_label='Core 0', temp2_input=60000)
        self.assertEqual(self.temperature(), (72.0, 110.0))

    def test_ordre_k10temp_tctl_avant_tdie(self):
        self.hwmon(0, 'k10temp', temp1_label='Tdie', temp1_input=50000, temp2_label='Tctl', temp2_input=65500)
        self.assertEqual(self.temperature(), (65.5, None))

    def test_coretemp_passe_avant_k10temp(self):
        self.hwmon(0, 'k10temp', temp1_label='Tctl', temp1_input=65000)
        self.hwmon(1, 'coretemp', temp1_label='Package id 0', temp1_input=50000)
        self.assertEqual(self.temperature()[0], 50.0)

    def test_cpu_thermal_temp1(self):
        self.hwmon(0, 'cpu_thermal', temp1_input=47000)
        self.assertEqual(self.temperature()[0], 47.0)

    def test_zone_thermique(self):
        self.m.ecrire('sys/class/thermal/thermal_zone0/type', 'acpitz\n')
        self.m.ecrire('sys/class/thermal/thermal_zone0/temp', '30000\n')
        self.m.ecrire('sys/class/thermal/thermal_zone1/type', 'x86_pkg_temp\n')
        self.m.ecrire('sys/class/thermal/thermal_zone1/temp', '58000\n')
        self.m.ecrire('sys/class/thermal/thermal_zone1/trip_point_0_type', 'critical\n')
        self.m.ecrire('sys/class/thermal/thermal_zone1/trip_point_0_temp', '105000\n')
        self.assertEqual(self.temperature(), (58.0, 105.0))

    def test_aucun_capteur(self):
        self.hwmon(0, 'nvme', temp1_input=40000)
        self.assertIsNone(pr.trouver_capteur(self.m.lecteur))
        self.assertEqual(self.temperature(), (None, None))

    def test_frequences(self):
        for k, (cur, maxi) in enumerate([(1000000, 4800000), (3000000, 4800000)]):
            self.m.ecrire(f'sys/devices/system/cpu/cpu{k}/cpufreq/scaling_cur_freq', f'{cur}\n')
            self.m.ecrire(f'sys/devices/system/cpu/cpu{k}/cpufreq/cpuinfo_max_freq', f'{maxi}\n')
        self.assertEqual(pr.lire_frequences(self.m.lecteur), (2000, 4800))

    def test_pas_de_frequence(self):
        self.assertEqual(pr.lire_frequences(self.m.lecteur), (None, None))

    def test_types_hybrides_et_coeurs_physiques(self):
        self.m.ecrire('sys/devices/cpu_core/cpus', '0-3\n')
        self.m.ecrire('sys/devices/cpu_atom/cpus', '4-5\n')
        for k, cid in enumerate([8, 8, 9, 9, 16, 17]):
            self.m.ecrire(f'sys/devices/system/cpu/cpu{k}/topology/core_id', f'{cid}\n')
        self.assertEqual(pr.lire_types(self.m.lecteur, 6), (['P'] * 4 + ['E'] * 2, 2))
        self.assertEqual(pr.lire_types(pr.Lecteur(str(self.m.proc), str(self.m.racine / 'vide')), 6), (None, None))


class TestEchantillonneur(unittest.TestCase):
    def setUp(self):
        self.m = Monde(self)
        self.horloge = 1000.0
        self.m.cpu(stat_cpu('cpu', 100, 900), stat_cpu('cpu0', 50, 450), stat_cpu('cpu1', 50, 450))
        self.m.ecrire('proc/loadavg', '2.41 1.98 1.50 1/100 5\n')
        self.m.ecrire('proc/cpuinfo', 'model name\t: Faux Processeur\n')
        self.m.ecrire('proc/pressure/cpu', 'some avg10=1.50 avg60=0 avg300=0 total=0\n')
        self.m.programme(10, 'ffmpeg', 0)
        self.m.programme(11, 'discret', 0)
        self.e = pr.Echantillonneur(self.m.lecteur, hz=HZ, horloge=lambda: self.horloge, heure=lambda: 1_800_000_000.0 + self.horloge,
                                    nom_utilisateur=lambda uid: f'u{uid}')

    def avancer(self, secondes, ensemble, c0, c1, **ticks):
        self.horloge += secondes
        self.m.cpu(ensemble, c0, c1)
        for pid, (nom, utime) in ticks.items():
            self.m.programme(int(pid[1:]), nom, utime)

    def test_premier_releve_sans_pourcentage(self):
        o = self.e.mesurer()
        self.assertIsNone(o['pourcent'])
        self.assertIsNone(o['par_coeur'])
        self.assertIsNone(o['plus_actifs'])
        self.assertEqual((o['nb_coeurs'], o['modele'], o['source']), (2, 'Faux Processeur', 'service'))
        self.assertEqual((o['charge'], o['pression']), ([2.41, 1.98, 1.5], 1.5))

    def test_pourcentages_total_coeur_et_programmes(self):
        self.e.mesurer()
        # 2 s : l'ensemble passe de 1000 à 1400 ticks (+300 occupés), cpu0 +250 occupés sur 200 ticks de... (valeurs simples)
        self.avancer(2.0, stat_cpu('cpu', 400, 1000), stat_cpu('cpu0', 350, 450), stat_cpu('cpu1', 50, 550),
                     p10=('ffmpeg', 300), p11=('discret', 1))
        o = self.e.mesurer()
        self.assertEqual(o['pourcent'], 75.0)               # 300 occupés sur 400
        self.assertEqual(o['par_coeur'], [100.0, 0.0])
        self.assertEqual(o['intervalle_s'], 2.0)
        [a] = o['plus_actifs']                              # « discret » (0,005 cœur) est sous le seuil de 0,01
        self.assertEqual((a['pid'], a['nom'], a['coeurs'], a['noyau']), (10, 'ffmpeg', 1.5, False))
        self.assertEqual(a['pourcent'], 75.0)               # 1,5 cœur sur 2
        self.assertTrue(a['utilisateur'].startswith('u'))

    def test_dix_plus_actifs_tries(self):
        self.e.mesurer()
        for pid in range(20, 40):
            self.m.programme(pid, f'p{pid}', 0)
        self.e.mesurer()                                    # les 20 sont « apparus » : relevé de départ
        self.avancer(1.0, stat_cpu('cpu', 200, 900), stat_cpu('cpu0', 100, 450), stat_cpu('cpu1', 100, 450))
        for pid in range(20, 40):
            self.m.programme(pid, f'p{pid}', (pid - 19) * 5)
        o = self.e.mesurer()
        self.assertEqual(len(o['plus_actifs']), 10)
        self.assertEqual(o['plus_actifs'][0]['nom'], 'p39')
        coeurs = [a['coeurs'] for a in o['plus_actifs']]
        self.assertEqual(coeurs, sorted(coeurs, reverse=True))

    def test_programme_apparu_entre_deux_releves_n_est_pas_compte(self):
        self.e.mesurer()
        self.avancer(2.0, stat_cpu('cpu', 200, 1000), stat_cpu('cpu0', 100, 500), stat_cpu('cpu1', 100, 500))
        self.m.programme(50, 'nouveau', 400)
        o = self.e.mesurer()
        self.assertEqual(o['plus_actifs'], [])
        self.avancer(2.0, stat_cpu('cpu', 400, 1100), stat_cpu('cpu0', 200, 550), stat_cpu('cpu1', 200, 550))
        self.m.programme(50, 'nouveau', 600)
        self.assertEqual(self.e.mesurer()['plus_actifs'][0]['coeurs'], 1.0)   # 200 ticks / 100 Hz / 2 s

    def test_numero_repris_par_un_autre_programme(self):
        self.m.programme(60, 'ancien', 5000, debut=10)
        self.e.mesurer()
        self.avancer(1.0, stat_cpu('cpu', 200, 900), stat_cpu('cpu0', 100, 450), stat_cpu('cpu1', 100, 450))
        self.m.programme(60, 'autre', 10, debut=99999)      # même pid, autre démarrage : ni négatif ni énorme
        self.assertNotIn(60, [a['pid'] for a in self.e.mesurer()['plus_actifs']])

    def test_programme_disparu_oublie(self):
        self.e.mesurer()
        self.m.retirer(11)
        self.avancer(1.0, stat_cpu('cpu', 200, 900), stat_cpu('cpu0', 100, 450), stat_cpu('cpu1', 100, 450))
        self.e.mesurer()
        self.assertNotIn(11, self.e.precedent[3])

    def test_noyau(self):
        self.m.programme(2, 'kthreadd', 0, ppid=0)
        self.m.programme(30, 'kswapd0', 0, ppid=2)
        self.e.mesurer()
        self.avancer(1.0, stat_cpu('cpu', 200, 900), stat_cpu('cpu0', 100, 450), stat_cpu('cpu1', 100, 450))
        self.m.programme(30, 'kswapd0', 80, ppid=2)
        self.m.programme(2, 'kthreadd', 50, ppid=0)
        o = self.e.mesurer()
        self.assertEqual({a['nom']: a['noyau'] for a in o['plus_actifs']}, {'kswapd0': True, 'kthreadd': True})

    def test_temperature_frequence_et_chaud(self):
        self.m.ecrire('sys/class/hwmon/hwmon0/name', 'coretemp\n')
        self.m.ecrire('sys/class/hwmon/hwmon0/temp1_label', 'Package id 0\n')
        self.m.ecrire('sys/class/hwmon/hwmon0/temp1_input', '96000\n')
        self.m.ecrire('sys/class/hwmon/hwmon0/temp1_crit', '110000\n')
        self.m.ecrire('sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq', '1850000\n')
        self.m.ecrire('sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq', '4800000\n')
        o = self.e.mesurer()
        self.assertEqual((o['temperature'], o['temperature_critique'], o['chaud']), (96.0, 110.0, True))
        self.assertEqual((o['frequence_mhz'], o['frequence_max_mhz']), (1850, 4800))
        self.assertIsNone(o['types'])

    def test_champ_illisible_vaut_null_sans_faire_echouer_l_objet(self):
        (self.m.proc / 'loadavg').unlink()
        (self.m.proc / 'cpuinfo').unlink()
        o = self.e.mesurer()
        self.assertIsNone(o['charge'])
        self.assertIsNone(o['modele'])
        self.assertIsNone(o['temperature'])
        self.assertFalse(o['chaud'])
        self.assertEqual(o['nb_coeurs'], 2)

    def test_stat_illisible(self):
        (self.m.proc / 'stat').unlink()
        o = self.e.mesurer()
        self.assertIsNone(o['pourcent'])
        self.assertEqual(o['charge'], [2.41, 1.98, 1.5])

    def test_contrat_des_cles(self):
        self.e.mesurer()
        self.avancer(2.0, stat_cpu('cpu', 200, 1000), stat_cpu('cpu0', 100, 500), stat_cpu('cpu1', 100, 500))
        o = self.e.mesurer()
        cles = {'t', 'date', 'source', 'intervalle_s', 'pourcent', 'nb_coeurs', 'par_coeur', 'types', 'modele', 'charge',
                'pression', 'temperature', 'temperature_critique', 'chaud', 'frequence_mhz', 'frequence_max_mhz', 'plus_actifs'}
        self.assertLessEqual(cles, set(o))
        json.dumps(o)

    def test_prise_de_la_minute(self):
        self.e.mesurer()
        self.avancer(30.0, stat_cpu('cpu', 300, 1000), stat_cpu('cpu0', 150, 500), stat_cpu('cpu1', 150, 500),
                     p10=('cc1', 1500), p11=('cc1', 900))
        self.e.mesurer()
        self.m.programme(12, 'cc1', 0)
        self.e.mesurer()                                    # relevé de départ du pid 12, sans durée écoulée utile
        self.avancer(30.0, stat_cpu('cpu', 500, 1100), stat_cpu('cpu0', 250, 550), stat_cpu('cpu1', 250, 550),
                     p10=('cc1', 3000), p11=('cc1', 900))
        self.e.mesurer()
        m = self.e.prendre_minute()
        self.assertEqual(m['cpu'], 66.7)                    # 400 ticks occupés sur 600 écoulés
        self.assertEqual(m['actifs'], [['cc1', 0.5 * 2 * 0 + round((1500 + 900 + 1500) / HZ / 60.0, 2)]])   # additionnés par nom
        self.assertEqual(m['charge'], 2.41)
        self.assertEqual(self.e.prendre_minute()['actifs'], [])  # le cumul est vidé


class TestFichier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def test_ecriture_atomique_en_0644(self):
        pr.ecrire_fichier(self.d / 'sous' / 'run', {'t': 1.0})
        f = self.d / 'sous' / 'run' / 'processeur.json'
        self.assertEqual(json.loads(f.read_text()), {'t': 1.0})
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o644)
        self.assertEqual([p.name for p in f.parent.iterdir()], ['processeur.json'])   # pas de .tmp laissé

    def test_dossier_d_ecriture(self):
        self.assertEqual(pr.dossier_ecriture({'RUNTIME_DIRECTORY': '/run/x:/run/y', 'GAGARINE_DOSSIER_EXECUTION': '/z'}, False), '/run/x')
        self.assertEqual(pr.dossier_ecriture({'GAGARINE_DOSSIER_EXECUTION': '/z'}, False), '/z')
        self.assertEqual(pr.dossier_ecriture({}, True), '/run/gagarine')
        self.assertIsNone(pr.dossier_ecriture({}, False))

    def test_dossiers_de_lecture(self):
        self.assertEqual(pr.dossiers_lecture({'GAGARINE_DOSSIER_EXECUTION': '/z'}), ['/z'])
        self.assertEqual(pr.dossiers_lecture({}), ['/run/gagarine'])

    def test_fraicheur_de_6_s(self):
        env = {'GAGARINE_DOSSIER_EXECUTION': str(self.d)}
        pr.ecrire_fichier(self.d, {'t': 100.0, 'pourcent': 5.0})
        self.assertEqual(pr.lire_fichier(env, lambda: 100.0)['pourcent'], 5.0)
        self.assertIsNotNone(pr.lire_fichier(env, lambda: 106.0))
        self.assertIsNone(pr.lire_fichier(env, lambda: 106.1))
        self.assertIsNone(pr.lire_fichier(env, lambda: 90.0))   # venue du futur : pas fiable

    def test_absent_illisible_ou_sans_t(self):
        env = {'GAGARINE_DOSSIER_EXECUTION': str(self.d)}
        self.assertIsNone(pr.lire_fichier(env, lambda: 1.0))
        (self.d / 'processeur.json').write_text('{pas du json')
        self.assertIsNone(pr.lire_fichier(env, lambda: 1.0))
        (self.d / 'processeur.json').write_text('{"pourcent": 1}')
        self.assertIsNone(pr.lire_fichier(env, lambda: 1.0))
        (self.d / 'processeur.json').write_text('[1]')
        self.assertIsNone(pr.lire_fichier(env, lambda: 1.0))

    def test_obtenir_prefere_le_service_sinon_mesure_directe(self):
        env = {'GAGARINE_DOSSIER_EXECUTION': str(self.d)}
        directe = lambda: {'source': 'directe'}
        self.assertEqual(pr.obtenir(env, True, lambda: 1.0, directe), {'source': 'directe'})
        self.assertIsNone(pr.obtenir(env, False, lambda: 1.0, directe))
        pr.ecrire_fichier(self.d, {'t': 1.0, 'source': 'service'})
        self.assertEqual(pr.obtenir(env, True, lambda: 2.0, directe)['source'], 'service')
        self.assertIsNone(pr.obtenir(env, True, lambda: 50.0, lambda: 1 / 0))   # une mesure qui échoue : null

    def test_mesure_directe_sur_de_faux_dossiers(self):
        m = Monde(self)
        m.cpu(stat_cpu('cpu', 100, 900), stat_cpu('cpu0', 100, 900))
        pauses = []
        def dormir(s):
            pauses.append(s)
            m.cpu(stat_cpu('cpu', 200, 1000), stat_cpu('cpu0', 200, 1000))
        o = pr.mesure_directe(m.lecteur, dormir=dormir, hz=HZ)
        self.assertEqual(pauses, [0.5])
        self.assertEqual((o['source'], o['pourcent']), ('directe', 50.0))


OBJET = {'t': 1.0, 'pourcent': 23.4, 'nb_coeurs': 14, 'par_coeur': [62.0] + [0.0] * 13, 'types': ['P'] * 4 + ['E'] * 10,
         'coeurs_performants': 2, 'modele': 'Intel Faux', 'charge': [2.41, 1.98, 1.5], 'pression': 0.03,
         'temperature': 72.0, 'temperature_critique': 110.0, 'chaud': False, 'frequence_mhz': 1850,
         'frequence_max_mhz': 4800,
         'plus_actifs': [{'pid': 4242, 'nom': 'ffmpeg', 'coeurs': 6.21, 'pourcent': 44.4, 'utilisateur': 'moi', 'noyau': False},
                         {'pid': 9, 'nom': 'firefox', 'coeurs': 0.8, 'pourcent': 5.7, 'utilisateur': 'moi', 'noyau': False},
                         {'pid': 3, 'nom': 'kswapd0', 'coeurs': 0.05, 'pourcent': 0.4, 'utilisateur': 'root', 'noyau': True},
                         {'pid': 5, 'nom': 'gnome-shell', 'coeurs': 0.04, 'pourcent': 0.3, 'utilisateur': 'moi', 'noyau': False}]}


class TestTextes(unittest.TestCase):
    def test_resume(self):
        self.assertEqual(pr.ligne_resume(OBJET), 'Processeur : 23 % (charge 2,41 pour 14 cœurs) · 72 °C · 1,9 GHz')
        self.assertIn('(chaud)', pr.ligne_resume(dict(OBJET, chaud=True)))
        self.assertEqual(pr.ligne_resume(dict(OBJET, temperature=None, frequence_mhz=None, charge=None)), 'Processeur : 23 %')
        self.assertIsNone(pr.ligne_resume(None))
        self.assertIsNone(pr.ligne_resume(dict(OBJET, pourcent=None)))

    def test_plus_actifs(self):
        self.assertEqual(pr.ligne_actifs(OBJET), 'Plus actifs : ffmpeg 6,2 cœurs, firefox 0,8 cœur, kswapd0 0,05 cœur')
        self.assertIsNone(pr.ligne_actifs(dict(OBJET, plus_actifs=[])))

    def test_description_des_coeurs(self):
        self.assertEqual(pr.description_coeurs(OBJET), '2 cœurs performants (4 fils) et 10 économes')
        self.assertEqual(pr.description_coeurs(dict(OBJET, types=None)), '14 cœurs')

    def test_detail(self):
        t = pr.texte_processeur(OBJET)
        self.assertIn('Intel Faux · 2 cœurs performants (4 fils) et 10 économes', t)
        self.assertIn('  cpu0  P  ██████░░░░  62 %', t)
        self.assertIn('  cpu13 E  ░░░░░░░░░░  0 %', t)
        self.assertIn('Température : 72 °C (critique 110 °C)', t)
        self.assertIn('Fréquence : 1,9 GHz / maximum 4,8 GHz', t)
        self.assertIn('Charge : 2,41 1,98 1,50', t)
        self.assertIn('Pression : 0,0 %', t)
        self.assertIn('  ffmpeg (processus 4242, moi) : 6,2 cœurs · 44 %', t)
        self.assertIn('kswapd0 (noyau) (processus 3, root)', t)
        self.assertEqual(pr.texte_processeur(None), 'Mesure du processeur indisponible.')

    def test_detail_chaud(self):
        self.assertIn(': chaud', pr.texte_processeur(dict(OBJET, chaud=True)))


class Ctx:
    def __init__(self, env):
        self.env = env


class TestCommande(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def lancer(self, *args, t=None):
        pr.ecrire_fichier(self.d, dict(OBJET, t=t if t is not None else __import__('time').time(), source='service'))
        sortie, erreur = io.StringIO(), io.StringIO()
        with redirect_stdout(sortie), redirect_stderr(erreur):
            code = pr.commande_processeur(list(args), Ctx({'GAGARINE_DOSSIER_EXECUTION': str(self.d)}))
        return code, sortie.getvalue(), erreur.getvalue()

    def test_json_du_service(self):
        code, sortie, _ = self.lancer('--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sortie)['source'], 'service')
        self.assertEqual(sortie.count('\n'), 1)

    def test_texte(self):
        code, sortie, _ = self.lancer()
        self.assertEqual(code, 0)
        self.assertIn('Utilisation : 23 %', sortie)

    def test_argument_inconnu(self):
        code, _, erreur = self.lancer('--bof')
        self.assertEqual(code, 2)
        self.assertIn('usage', erreur)

    def test_commande_declaree(self):
        self.assertIn('processeur', pr.COMMANDES)
        self.assertIn('processeur', pr.AIDE_COMMANDES)


class TestSansImportDeGagarine(unittest.TestCase):
    def test_aucun_autre_module_de_gagarine(self):
        import subprocess
        import sys
        racine = str(Path(__file__).resolve().parent.parent)
        code = 'import sys; import gagarine.processeur; print([m for m in sys.modules if m.startswith("gagarine.")])'
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=racine)
        self.assertEqual(r.stdout.strip(), "['gagarine.processeur']")


if __name__ == '__main__':
    unittest.main()
