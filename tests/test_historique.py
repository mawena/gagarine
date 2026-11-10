"""Tests de l'historique : écriture par minute, rotation, agrégation, commande."""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from gagarine import historique as hi
from gagarine.reglages import Reglages
from tests.commun import Banc, Mo

JOUR = 86400
T0 = 1_800_000_000.0   # 2027-01-15 environ ; un instant fixe


def ecrire_mesures(dossier, t, ram, **kw):
    ligne = {'date': hi.date_iso(t), 'ram': ram, 'disponible_mo': 1000, 'swap_mo': kw.get('swap', 0), 'zram_mo': 0,
             'pression': kw.get('pression', 0.0), 'gros': kw.get('gros', [['chrome', 2800]])}
    with open(Path(dossier) / hi.nom_fichier(t), 'a') as f:
        f.write(json.dumps(ligne) + '\n')


class TestEnregistreur(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.t = T0
        self.e = hi.Enregistreur(self.d, lambda: self.t)

    def noter(self, ram=50.0):
        return self.e.noter(ram, {'disponible_mo': 4000, 'swap_mo': 12, 'zram_mo': 34}, 1.234,
                            [('a', 100 * Mo), ('b', 900 * Mo), ('c', 500 * Mo), ('d', 5 * Mo)])

    def test_une_mesure_par_minute(self):
        self.assertTrue(self.noter())
        self.t += 30
        self.assertFalse(self.noter())
        self.t += 30
        self.assertTrue(self.noter())
        lignes = (self.d / hi.nom_fichier(T0)).read_text().splitlines()
        self.assertEqual(len(lignes), 2)

    def test_format_de_la_mesure(self):
        self.noter(91.23)
        m = json.loads((self.d / hi.nom_fichier(T0)).read_text())
        self.assertEqual(set(m), {'date', 'ram', 'disponible_mo', 'swap_mo', 'zram_mo', 'pression', 'gros'})
        self.assertEqual(m['ram'], 91.2)
        self.assertEqual(m['gros'], [['b', 900], ['c', 500], ['a', 100]])
        self.assertEqual((self.d / hi.nom_fichier(T0)).stat().st_mode & 0o777, 0o644)

    def test_rotation_8_jours(self):
        for i in range(12):
            self.t = T0 + i * JOUR
            self.assertTrue(self.noter())
        noms = sorted(f.name for f in self.d.glob('historique-*.jsonl'))
        self.assertEqual(len(noms), 8)
        self.assertEqual(noms[-1], hi.nom_fichier(T0 + 11 * JOUR))
        self.assertEqual(noms[0], hi.nom_fichier(T0 + 4 * JOUR))

    def test_autres_fichiers_intouches(self):
        (self.d / 'evenements.jsonl').write_text('x')
        (self.d / 'historique-2000-01-01.jsonl').write_text('vieux')
        self.noter()
        self.assertTrue((self.d / 'evenements.jsonl').exists())
        self.assertFalse((self.d / 'historique-2000-01-01.jsonl').exists())

    def test_erreur_d_ecriture_ignoree(self):
        fichier = self.d / 'pas-un-dossier'
        fichier.write_text('x')
        e = hi.Enregistreur(fichier / 'sous', lambda: self.t)
        self.assertFalse(e.noter(50, {}, 0, []))

    def test_mesure_illisible_ignoree(self):
        self.assertFalse(self.e.noter(50, None, 0, []))


class TestLecture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def test_periode_courte(self):
        for i in range(10):
            ecrire_mesures(self.d, T0 - i * 60, 40 + i)
        d = hi.donnees_historique(self.d, 300, T0, [])
        self.assertEqual(d['depuis_s'], 300)
        self.assertEqual(d['pas_s'], 60)
        self.assertEqual(len(d['points']), 6)   # T0 et les 5 minutes d'avant
        self.assertEqual(set(d['points'][0]), {'date', 'ram', 'swap_mo', 'zram_mo', 'pression'})

    def test_agregation_10_min_au_dela_de_24_h_maximum_garde(self):
        base = (T0 // 600) * 600 - 3 * JOUR
        for i in range(30):   # 30 minutes : trois seaux de 10 min
            ecrire_mesures(self.d, base + i * 60, 50.0 if i != 4 else 97.0, swap=100 * (i % 2))
        d = hi.donnees_historique(self.d, 7 * JOUR, T0, [])
        self.assertEqual(d['pas_s'], 600)
        self.assertEqual(len(d['points']), 3)
        self.assertEqual(d['points'][0]['ram'], 97.0)   # le pic du premier seau n'est pas lissé
        self.assertEqual(d['points'][1]['ram'], 50.0)
        self.assertEqual(d['points'][0]['swap_mo'], 50)

    def test_24_h_pile_garde_la_minute(self):
        ecrire_mesures(self.d, T0 - 100, 50)
        self.assertEqual(hi.donnees_historique(self.d, JOUR, T0, [])['pas_s'], 60)

    def test_gros_agreges(self):
        ecrire_mesures(self.d, T0 - 120, 50, gros=[['chrome', 2000], ['code', 900]])
        ecrire_mesures(self.d, T0 - 60, 50, gros=[['chrome', 2800]])
        ecrire_mesures(self.d, T0, 50, gros=[['chrome', 1000], ['code', 1000]])
        g = hi.donnees_historique(self.d, 600, T0, [])['gros']
        self.assertEqual(g, [{'nom': 'chrome', 'pic_mo': 2800, 'minutes': 3}, {'nom': 'code', 'pic_mo': 1000, 'minutes': 2}])

    def test_incidents_et_travaux(self):
        ev = [{'date': hi.date_iso(T0 - 100), 'action': 'fige', 'nom': 'x'},
              {'date': hi.date_iso(T0 - 90), 'action': 'repris', 'nom': 'x'},
              {'date': hi.date_iso(T0 - 80), 'action': 'limite', 'nom': 'y'},
              {'date': hi.date_iso(T0 - 50), 'action': 'travail-fin', 'nom': 'tests', 'duree': 61, 'code': 0, 'pic_go': 6.3,
               'pid': 3},
              {'date': hi.date_iso(T0 - 5000), 'action': 'arrete', 'nom': 'vieux'}]
        d = hi.donnees_historique(self.d, 300, T0, ev)
        self.assertEqual([e['action'] for e in d['incidents']], ['fige', 'limite'])
        self.assertEqual(d['travaux'], [{'nom': 'tests', 'date': hi.date_iso(T0 - 50), 'duree': 61, 'code': 0, 'pic_go': 6.3}])

    def test_lignes_invalides_ignorees(self):
        ecrire_mesures(self.d, T0, 50)
        with open(self.d / hi.nom_fichier(T0), 'a') as f:
            f.write('pas du json\n[1]\n{"date": "?"}\n')
        self.assertEqual(len(hi.donnees_historique(self.d, 300, T0, [])['points']), 1)

    def test_analyser_depuis(self):
        self.assertEqual([hi.analyser_depuis(x) for x in ('5min', '1h', '24h', '7j')], [300, 3600, 86400, 604800])
        self.assertIsNone(hi.analyser_depuis('bientôt'))
        self.assertIsNone(hi.analyser_depuis('0h'))


class Ctx:
    def __init__(self, env):
        self.env = env


class TestCommande(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.ctx = Ctx({'GAGARINE_ETAT': str(self.d), 'HOME': str(self.d / 'home')})

    def lancer(self, *args, maintenant=T0):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            code = hi.commande_historique(list(args), self.ctx, maintenant)
        return code, sortie.getvalue()

    def remplir(self):
        for i in range(5):
            ecrire_mesures(self.d, T0 - i * 60, 40 + 10 * i, swap=100, pression=3.5)
        evt = [{'date': hi.date_iso(T0 - 100), 'source': 'gagarine', 'action': 'fige', 'nom': 'glouton', 'pid': 9,
                'ram': 91.2, 'declencheur': 'anticipation'},
               {'date': hi.date_iso(T0 - 50), 'source': 'file', 'action': 'travail-fin', 'nom': 'tests', 'pid': 3,
                'code': 0, 'duree': 75, 'pic_go': 6.3}]
        (self.d / 'evenements.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in evt))

    def test_json_conforme_au_contrat(self):
        self.remplir()
        code, sortie = self.lancer('--depuis', '5min', '--json')
        self.assertEqual(code, 0)
        d = json.loads(sortie)
        self.assertEqual(set(d), {'depuis_s', 'pas_s', 'points', 'incidents', 'gros', 'travaux'})
        self.assertEqual(len(d['points']), 5)
        self.assertEqual(d['incidents'][0]['declencheur'], 'anticipation')
        self.assertEqual(d['gros'], [{'nom': 'chrome', 'pic_mo': 2800, 'minutes': 5}])
        self.assertEqual(d['travaux'][0]['pic_go'], 6.3)

    def test_texte_lisible(self):
        self.remplir()
        code, sortie = self.lancer('--depuis', '5min')
        self.assertEqual(code, 0)
        for morceau in ('moyenne 60 %', 'maximale 80 %', 'Incidents : 1', 'anticipation', 'chrome', 'tests', 'pic 6.3 Go'):
            self.assertIn(morceau, sortie)

    def test_defaut_24_h(self):
        self.remplir()
        d = json.loads(self.lancer('--json')[1])
        self.assertEqual(d['depuis_s'], 86400)

    def test_pas_encore_d_historique(self):
        code, sortie = self.lancer()
        self.assertEqual(code, 0)
        self.assertIn('Pas encore d\'historique', sortie)
        self.assertEqual(json.loads(self.lancer('--json')[1])['points'], [])

    def test_arguments_invalides(self):
        for args in (['--depuis'], ['--depuis', 'x'], ['--bof']):
            err = io.StringIO()
            from contextlib import redirect_stderr
            with redirect_stderr(err):
                self.assertEqual(self.lancer(*args)[0], 2)

    def test_extension_declaree(self):
        self.assertIn('historique', hi.COMMANDES)
        self.assertIn('historique', hi.AIDE_COMMANDES)


class TestGardeHistorique(Banc):
    def garde(self, **kw):
        g = super().garde(**kw)
        self.s.mesure = lambda: {'disponible_mo': 4000, 'swap_mo': 5, 'zram_mo': 6}
        self.s.pression = lambda: 2.0
        return g

    def lignes(self):
        return [json.loads(l) for f in sorted(self.dossier.glob('historique-*.jsonl')) for l in f.read_text().splitlines()]

    def test_une_ligne_par_minute_de_service(self):
        self.garde()
        self.s.ajouter(10, 'gros', 3000)
        self.tours(125, pct=60)
        self.assertEqual(len(self.lignes()), 3)   # à 0, 60 et 120 s
        m = self.lignes()[0]
        self.assertEqual((m['ram'], m['swap_mo'], m['zram_mo'], m['pression']), (60.0, 5, 6, 2.0))
        self.assertEqual(m['gros'], [['gros', 3000]])

    def test_sans_mesure_pas_d_historique(self):
        self.garde()
        del self.s.mesure
        self.tours(70, pct=60)
        self.assertEqual(self.lignes(), [])

    def test_erreur_d_ecriture_ne_fait_pas_tomber_la_boucle(self):
        self.garde()
        self.s.mesure = lambda: 1 / 0
        self.s.ajouter(10, 'gros', 3000)
        self.tours(3, pct=96)
        self.assertIn('fige', self.actions())


if __name__ == '__main__':
    unittest.main()
