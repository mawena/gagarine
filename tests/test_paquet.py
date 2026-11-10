"""Cohérence du paquet Debian : tout ce que debian/gagarine.install et gagarine.manpages citent existe, et les lanceurs
répondent à l'aide sans affichage."""
import glob
import os
import subprocess
import sys
import unittest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lignes(nom):
    with open(os.path.join(RACINE, 'debian', nom), encoding='utf-8') as f:
        return [l.split() for l in f if l.strip() and not l.startswith('#')]


class Paquet(unittest.TestCase):
    def test_sources_installees_existent(self):
        for source, *_ in lignes('gagarine.install'):
            self.assertTrue(glob.glob(os.path.join(RACINE, source)), f'{source} introuvable')

    def test_chaque_module_est_installe(self):
        sources = {os.path.normpath(os.path.join(RACINE, s)) for s, *_ in lignes('gagarine.install')}
        installes = set()
        for motif in sources:
            installes.update(glob.glob(motif))
        for sous in ('gagarine', 'gagarine/interface', 'gagarine/indicateur'):
            for module in glob.glob(os.path.join(RACINE, sous, '*.py')):
                self.assertIn(module, installes, f'{module} n\'est pas dans debian/gagarine.install')

    def test_pages_de_manuel(self):
        pages = [p for p, *_ in lignes('gagarine.manpages')]
        for attendu in ('man/gagarine.8', 'man/gagarine-interface.1', 'man/gagarine-indicateur.1'):
            self.assertIn(attendu, pages)
            self.assertTrue(os.path.exists(os.path.join(RACINE, attendu)))

    def test_aides_sans_affichage(self):
        env = {k: v for k, v in os.environ.items() if k not in ('DISPLAY', 'WAYLAND_DISPLAY')}
        for lanceur, option in (('gagarine-indicateur', '--aide'), ('gagarine-interface', '--help')):
            r = subprocess.run([sys.executable, os.path.join(RACINE, 'bin', lanceur), option], env=env,
                               capture_output=True, text=True, timeout=30)
            if lanceur == 'gagarine-interface' and 'PyGObject' in r.stderr + r.stdout:
                self.skipTest('PyGObject absent')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Usage : ' + lanceur, r.stdout)


if __name__ == '__main__':
    unittest.main()
