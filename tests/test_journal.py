"""Tests du journal des événements."""
import json
import tempfile
import unittest
from pathlib import Path

from gagarine import journal as jn


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name) / 'etat'

    def test_format_de_l_evenement(self):
        j = jn.Journal(self.d, heure=lambda: 0, sortie=False)
        j.ecrire('fige', pid=5, nom='x', utilisateur='u', commande='c', memoire=123, ram=95.5, raison=None)
        ligne = (self.d / 'evenements.jsonl').read_text()
        e = json.loads(ligne)
        self.assertEqual(e, {'date': '1970-01-01T00:00:00+0000', 'source': 'gagarine', 'action': 'fige', 'pid': 5,
                             'nom': 'x', 'utilisateur': 'u', 'commande': 'c', 'memoire': 123, 'ram': 95.5})
        self.assertTrue(ligne.endswith('\n') and ligne.count('\n') == 1)
        self.assertEqual((self.d / 'evenements.jsonl').stat().st_mode & 0o777, 0o644)

    def test_accents_conserves(self):
        j = jn.Journal(self.d, sortie=False)
        j.ecrire('arrete', raison='figé depuis 10 min')
        self.assertIn('figé', (self.d / 'evenements.jsonl').read_text())

    def test_rotation_au_dela_de_la_taille_max(self):
        j = jn.Journal(self.d, sortie=False, taille_max=300)
        for i in range(20):
            j.ecrire('fige', pid=i, nom='x' * 20)
        self.assertTrue((self.d / 'evenements.jsonl.1').exists())
        courant = (self.d / 'evenements.jsonl').read_text().splitlines()
        self.assertTrue(0 < len(courant) < 20)
        self.assertEqual(json.loads(courant[-1])['pid'], 19)   # le dernier événement est dans le fichier courant
        self.assertTrue((self.d / 'evenements.jsonl.1').read_text().strip())

    def test_taille_par_defaut_512_ko(self):
        self.assertEqual(jn.TAILLE_JOURNAL, 512 * 1024)

    def test_impression_sur_la_sortie(self):
        import io
        sortie = io.StringIO()
        jn.Journal(self.d, sortie=sortie).ecrire('demarrage')
        self.assertEqual(json.loads(sortie.getvalue())['action'], 'demarrage')
