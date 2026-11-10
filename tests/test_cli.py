"""Tests de l'essai et des commandes existantes (lanceur, version, aide)."""
import os
import signal
import tempfile
import unittest
from pathlib import Path

from gagarine import essai as es
from gagarine import version as ve

CHEMIN = Path(__file__).resolve().parent.parent / 'bin' / 'gagarine'


class TestEssaiEtCommandes(unittest.TestCase):
    def test_reglages_d_essai(self):
        e = es.reglages_essai(50.0)
        self.assertEqual((e['GAGARINE_SEUIL'], e['GAGARINE_URGENCE'], e['GAGARINE_GRACE'], e['GAGARINE_DELAI_FIGE']),
                         ('55.0', '64.0', '3', '20'))
        self.assertEqual(e['GAGARINE_NOTIFICATIONS'], 'non')

    def test_verdict_complet(self):
        ech = [(0, 'S', 100, 0), (1, 'S', 600, 0), (2, 'T', 650, 0), (3, 'T', 40, 600)]
        v = es.verdict_essai(ech, ['fige', 'arrete'], -signal.SIGKILL)
        self.assertEqual([ok for _, ok, _ in v], [True, True, True])

    def test_verdict_pas_range(self):
        ech = [(0, 'S', 600, 0), (1, 'T', 650, 0), (2, 'T', 640, 0)]
        v = es.verdict_essai(ech, ['fige', 'arrete'], -signal.SIGKILL)
        self.assertEqual([ok for _, ok, _ in v], [True, False, True])

    def test_verdict_jamais_fige(self):
        v = es.verdict_essai([(0, 'S', 600, 0)], [], 0)
        self.assertEqual([ok for _, ok, _ in v], [False, False, False])

    def test_version_et_aide(self):
        self.assertEqual(ve.VERSION, '1.2.0')
        import subprocess
        for a in ('version', '--version'):
            r = subprocess.run([str(CHEMIN), a], capture_output=True, text=True)
            self.assertEqual(r.stdout, 'gagarine 1.2.0\n')
        r = subprocess.run([str(CHEMIN), '--help'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        for mot in ('surveiller', 'etat', 'reprendre', 'arreter', 'essai', 'version'):
            self.assertIn(mot, r.stdout)
        self.assertEqual(subprocess.run([str(CHEMIN), 'nimportequoi'], capture_output=True).returncode, 2)

    def test_reprendre_refuse_un_programme_non_fige(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([str(CHEMIN), 'arreter', str(os.getpid())], capture_output=True, text=True,
                               env=dict(os.environ, GAGARINE_ETAT=d))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('ne figure pas', r.stderr)
