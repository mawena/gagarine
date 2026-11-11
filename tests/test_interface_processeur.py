"""Tests de la partie « processeur » (1.4.0) de la logique de l'interface, sans GTK : /proc/stat, moyennes, contrat de
`processeur`, mise en forme, historique, file d'attente."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from gagarine.interface import donnees as d

RACINE = Path(__file__).resolve().parent.parent
FAUX = RACINE / 'tests' / 'donnees_interface' / 'faux-gagarine'

STAT = """cpu  1000 100 400 8000 500 20 30 50 7 3
cpu0 500 50 200 4000 250 10 15 25 0 0
cpu1 500 50 200 4000 250 10 15 25 7 3
intr 123456 0 0
ctxt 99
"""


def faux(*args, mode='normal', **env):
    e = dict(os.environ, FAUX_MODE=mode, **env)
    return subprocess.run([sys.executable, str(FAUX), *args], capture_output=True, text=True, env=e, timeout=20).stdout


class ProcStat(unittest.TestCase):
    def test_releve(self):
        r = d.lire_proc_stat(STAT)
        # total = user+nice+system+idle+iowait+irq+softirq+steal (guest et guest_nice exclus) = 10100 ; occupé = total-idle-iowait
        self.assertEqual(r['total'], (10100 - 8000 - 500, 10100))
        self.assertEqual(len(r['coeurs']), 2)
        self.assertEqual(r['coeurs'][0], (5050 - 4000 - 250, 5050))

    def test_anciens_noyaux_sans_steal(self):
        r = d.lire_proc_stat('cpu  10 0 5 80 5\ncpu0 10 0 5 80 5\n')
        self.assertEqual(r['total'], (15, 100))

    def test_illisible(self):
        for texte in ('', 'rien du tout', 'intr 1 2 3\n'):
            with self.assertRaises(d.ErreurDonnees):
                d.lire_proc_stat(texte)

    def test_pourcents(self):
        self.assertAlmostEqual(d.pourcent_entre((100, 1000), (400, 1600)), 50.0)
        self.assertIsNone(d.pourcent_entre((100, 1000), (100, 1000)))      # aucun tick écoulé
        self.assertIsNone(d.pourcent_entre((100, 1000), (50, 900)))        # compteur remonté
        self.assertIsNone(d.pourcent_entre(None, (1, 1)))
        self.assertEqual(d.pourcent_entre((0, 10), (50, 20)), 100.0)       # borné à 100
        avant = {'total': (0, 0), 'coeurs': [(0, 0), (0, 0)]}
        apres = {'total': (3, 8), 'coeurs': [(2, 4), (1, 4)]}
        self.assertEqual(d.pourcents_coeurs(avant, apres), [50.0, 25.0])
        self.assertEqual(d.pourcents_coeurs(avant, {'total': (1, 1), 'coeurs': [(1, 1)]}), [])   # nombre de cœurs changé
        self.assertEqual(d.pourcents_coeurs(None, apres), [])

    def test_niveaux_et_pastille(self):
        self.assertEqual([d.niveau_cpu(x) for x in (None, 0, 49.9, 50, 89.9, 90, 100)],
                         ['normal', 'normal', 'normal', 'vigilance', 'vigilance', 'alerte', 'alerte'])
        self.assertEqual(d.pastille_cpu(12), ('Calme', 'success'))
        self.assertEqual(d.pastille_cpu(65), ('Occupé', 'warning'))
        self.assertEqual(d.pastille_cpu(95), ('Saturé', 'error'))
        self.assertEqual(d.pastille_cpu(None), ('—', None))

    def test_moyenne_sur_10_secondes(self):
        mesures = [(0, 100.0), (88, 20.0), (92, 40.0), (100, 60.0)]
        self.assertAlmostEqual(d.moyenne_recente(mesures, 100), 50.0)        # t=0 et t=88 ont plus de 10 s
        self.assertAlmostEqual(d.moyenne_recente(mesures, 100, 3), 60.0)
        self.assertIsNone(d.moyenne_recente([], 100))
        self.assertIsNone(d.moyenne_recente([(0, 5.0)], 100))


class Contrat(unittest.TestCase):
    def objet(self):
        return json.loads(faux('etat'))['processeur']

    def test_objet_complet(self):
        p = d.lire_processeur(self.objet())
        self.assertEqual((p['nb_coeurs'], len(p['par_coeur']), len(p['types'])), (14, 14, 14))
        self.assertEqual(p['modele'], 'Intel(R) Core(TM) Ultra 7 155U')
        self.assertEqual((p['temperature'], p['temperature_critique'], p['chaud']), (72.0, 110.0, False))
        self.assertEqual(p['plus_actifs'][0]['nom'], 'ffmpeg')
        self.assertEqual(p['plus_actifs'][0]['coeurs'], 6.21)
        self.assertTrue(p['plus_actifs'][4]['noyau'])

    def test_champs_illisibles_valent_none(self):
        p = d.lire_processeur({'pourcent': 12.0, 'modele': None, 'temperature': None, 'frequence_mhz': None, 'types': None,
                               'par_coeur': [1.0, 2.0], 'charge': None,
                               'plus_actifs': [{'pid': 'x'}, {'pid': 5, 'nom': 'a', 'coeurs': 1.0}]})
        self.assertEqual((p['pourcent'], p['modele'], p['temperature'], p['frequence_mhz'], p['types'], p['charge']),
                         (12.0, None, None, None, [], []))
        self.assertEqual([a['pid'] for a in p['plus_actifs']], [5])
        self.assertEqual(p['nb_coeurs'], 2)

    def test_absent_ou_invalide(self):
        for v in (None, 'x', 3, [], True):
            self.assertIsNone(d.lire_processeur(v))

    def test_types_incoherents_ignores(self):
        p = d.lire_processeur({'par_coeur': [1, 2, 3], 'types': ['P', 'E']})
        self.assertEqual(p['types'], [])

    def test_lire_etat(self):
        self.assertEqual(d.lire_etat(faux('etat'))['processeur']['pourcent'], d.lire_processeur(self.objet())['pourcent'])
        for mode in ('v13', 'v12'):      # cœur plus ancien : pas de `processeur`, pas d'erreur
            self.assertIsNone(d.lire_etat(faux('etat', mode=mode))['processeur'], mode)


class MiseEnForme(unittest.TestCase):
    def test_coeurs(self):
        self.assertEqual(d.formater_coeurs(6.21), '6,2 cœurs')
        self.assertEqual(d.formater_coeurs(1.4), '1,4 cœur')
        self.assertEqual(d.formater_coeurs(0.8), '0,8 cœur')
        self.assertEqual(d.formater_coeurs(2), '2,0 cœurs')
        self.assertEqual(d.formater_coeurs(None), '—')

    def test_frequence_et_temperature(self):
        self.assertEqual(d.formater_ghz(1850), '1,9 GHz')
        self.assertEqual(d.formater_ghz(800), '0,8 GHz')
        self.assertEqual(d.formater_ghz(None), '—')
        self.assertEqual(d.formater_temperature(72.4), '72 °C')
        self.assertEqual(d.formater_temperature(None), '—')

    def test_detail(self):
        p = d.lire_processeur(json.loads(faux('etat'))['processeur'])
        self.assertEqual(d.detail_processeur(p), 'charge 2,4 · 14 cœurs · 1,9 GHz')
        self.assertEqual(d.detail_processeur(None, 8), '8 cœurs')          # mesure locale seule (cœur 1.3)
        self.assertEqual(d.detail_processeur(None), '')

    def test_repartition(self):
        self.assertEqual(d.repartition_coeurs(['P'] * 4 + ['E'] * 10, 14), '4 cœurs performants (P) et 10 économes (E)')
        self.assertEqual(d.repartition_coeurs(['P', 'E'], 2), '1 cœur performant (P) et 1 économe (E)')
        self.assertEqual(d.repartition_coeurs([], 8), '8 cœurs')
        self.assertEqual(d.repartition_coeurs([], None), '')

    def test_legende_et_chaud(self):
        p = d.lire_processeur(json.loads(faux('etat'))['processeur'])
        textes = [t for t, _ in d.legende_processeur(p)]
        self.assertEqual(textes, ['Intel(R) Core(TM) Ultra 7 155U', '4 cœurs performants (P) et 10 économes (E)', '72 °C',
                                  '1,9 GHz sur 4,8 GHz', 'pression 0,0 %'])
        self.assertFalse(any(chaud for _, chaud in d.legende_processeur(p)))
        chaud = d.lire_processeur(json.loads(faux('etat', FAUX_CHAUD='1'))['processeur'])
        self.assertTrue(d.est_chaud(chaud))
        self.assertIn(('97 °C (chaud)', True), d.legende_processeur(chaud))
        self.assertEqual(d.legende_processeur(None), [])
        self.assertEqual(d.legende_processeur({'modele': None, 'types': [], 'temperature': None}), [])   # tout illisible

    def test_ordre_p_puis_e(self):
        self.assertEqual(d.ordre_coeurs(['E', 'P', 'E', 'P'], 4), [1, 3, 0, 2])
        self.assertEqual(d.ordre_coeurs(None, 3), [0, 1, 2])
        self.assertEqual(d.ordre_coeurs(['P'], 3), [0, 1, 2])      # types incohérents : ordre du système

    def test_programme_actif(self):
        self.assertEqual(d.titre_actif({'pid': 4242, 'nom': 'ffmpeg', 'coeurs': 6.21, 'pourcent': 44.4,
                                        'utilisateur': 'moi', 'noyau': False}),
                         ('ffmpeg', 'PID 4242 · moi', '6,2 cœurs · 44 %'))
        self.assertEqual(d.titre_actif({'pid': 105, 'nom': 'kswapd0', 'coeurs': 0.31, 'pourcent': None, 'utilisateur': 'root',
                                        'noyau': True}), ('kswapd0', 'PID 105 · root · noyau', '0,3 cœur'))


class Historique(unittest.TestCase):
    def test_points_et_actifs(self):
        h = d.lire_historique(faux('historique', '--json', '--depuis', '24h'))
        p = h['points'][500]
        self.assertIsNotNone(p['cpu'])
        self.assertIsNotNone(p['temperature'])
        self.assertEqual(h['actifs'][0]['nom'], 'ffmpeg')
        self.assertEqual([a['cpu_min'] for a in h['actifs']], sorted((a['cpu_min'] for a in h['actifs']), reverse=True))
        self.assertIn('processeur au plus', d.resume_historique(h))
        self.assertEqual(d.titre_actif_periode(h['actifs'][0]),
                         ('ffmpeg', 'pic 12,8 cœurs · 1260 min de processeur · parmi les plus actifs pendant 3 h 34 min'))

    def test_anciennes_lignes_sans_processeur(self):
        h = d.lire_historique(json.dumps({'points': [{'date': '2026-11-11T10:00:00+0000', 'ram': 50.0},
                                                      {'date': '2026-11-11T10:01:00+0000', 'ram': 60.0, 'cpu': None}]}))
        self.assertEqual([p['cpu'] for p in h['points']], [None, None])
        self.assertEqual(h['actifs'], [])
        self.assertNotIn('processeur', d.resume_historique(h))
        h = d.lire_historique(faux('historique', '--json', '--depuis', '24h', mode='v13'))
        self.assertTrue(all(p['cpu'] is None for p in h['points']))
        self.assertEqual(h['actifs'], [])

    def test_actifs_invalides_ignores(self):
        self.assertEqual(d.lire_actifs([{'nom': 'a'}, 'x', {'nom': 'b', 'cpu_min': 3}, {'nom': 'c', 'cpu_min': 9, 'pic_coeurs': 2}]),
                         [{'nom': 'c', 'pic_coeurs': 2.0, 'minutes': None, 'cpu_min': 9.0},
                          {'nom': 'b', 'pic_coeurs': None, 'minutes': None, 'cpu_min': 3.0}])
        self.assertEqual(d.lire_actifs(None), [])


class File(unittest.TestCase):
    def test_travail_en_cours(self):
        t = {'etat': 'en cours', 'ram': 4, 'coeurs': 4, 'coeurs_utilises': 6.2, 'depuis': None}
        self.assertIn('processeur : 6,2 cœurs', d.detail_travail_file(t))
        self.assertNotIn('processeur', d.detail_travail_file(dict(t, coeurs_utilises=None)))
        self.assertNotIn('processeur', d.detail_travail_file({'etat': 'en cours', 'ram': 4}))    # cœur 1.3
        self.assertNotIn('processeur', d.detail_travail_file(dict(t, etat='en attente')))

    def test_travail_passe(self):
        _, detail, ok = d.detail_travail_passe({'nom': 'rendu', 'code': 0, 'duree': 754.0, 'coeurs_moyens': 5.4})
        self.assertIn('5,4 cœurs en moyenne', detail)
        self.assertTrue(ok)
        self.assertNotIn('moyenne', d.detail_travail_passe({'nom': 'rendu', 'code': 0, 'coeurs_moyens': None})[1])
        self.assertNotIn('moyenne', d.detail_travail_passe({'nom': 'rendu', 'code': 0})[1])

    def test_faux(self):
        f = d.lire_file(faux('file', '--etat'))
        self.assertEqual(f['travaux'][0]['coeurs_utilises'], 3.9)
        self.assertNotIn('coeurs_utilises', d.lire_file(faux('file', '--etat', mode='v13'))['travaux'][0])


if __name__ == '__main__':
    unittest.main()
