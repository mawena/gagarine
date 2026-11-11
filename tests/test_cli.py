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
        self.assertEqual(ve.VERSION, '1.4.0')
        import subprocess
        for a in ('version', '--version'):
            r = subprocess.run([str(CHEMIN), a], capture_output=True, text=True)
            self.assertEqual(r.stdout, 'gagarine 1.4.0\n')
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


class TestDemarrageRapide(unittest.TestCase):
    """`ligne-etat` et `hook-claude` tournent à chaque message de Claude Code : ils ne doivent pas charger le reste."""

    def test_index_des_extensions_d_accord_avec_les_modules(self):
        from gagarine import cli
        for commande, nom in cli.COMMANDES_EXTENSIONS.items():
            [(_, commandes, _)] = cli.charger_extensions((nom,))
            self.assertIn(commande, commandes)
        for _, commandes, _ in cli.charger_extensions():
            for commande in commandes:
                self.assertIn(commande, cli.COMMANDES_EXTENSIONS)

    def test_modules_lourds_non_importes(self):
        import subprocess
        import sys
        code = ('import sys, json; from gagarine.cli import main; main(["ligne-etat"]); '
                'print(json.dumps([m for m in ("gagarine.file", "gagarine.garde", "gagarine.ntfy", "gagarine.essai", '
                '"gagarine.claude", "urllib.request", "subprocess") if m in sys.modules]))')
        racine = str(CHEMIN.parent.parent)
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, input='{}',
                           env=dict(os.environ, PYTHONPATH=racine))
        # file est chargé pour compter les tickets ; ni le garde, ni le réseau, ni l'essai, ni le hook
        import json
        charges = json.loads(r.stdout.strip().splitlines()[-1])
        for m in ('gagarine.garde', 'gagarine.essai', 'gagarine.claude', 'urllib.request'):
            self.assertNotIn(m, charges)

    def test_aide_complete_avec_les_extensions(self):
        from gagarine import cli
        texte = cli.aide()
        for mot in ('ligne-etat', 'hook-claude', 'menage', 'historique', 'applications', 'ntfy', 'file priorite'):
            self.assertIn(mot, texte)
        self.assertEqual(cli.AIDE, cli.texte_aide())
