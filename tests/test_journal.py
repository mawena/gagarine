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


class TestLigneLisible(unittest.TestCase):
    """Texte de « gagarine journal » : chaque type d'événement avec ses propres unités."""

    def ligne(self, **e):
        return jn.ligne_lisible({'date': '2026-11-10T15:13:18+0000', **e})

    def test_fige_garde_le_pourcentage_de_ram(self):
        l = self.ligne(action='fige', source='gagarine', nom='python3', pid=42, ram=96.2)
        self.assertIn('python3 42 (RAM 96.2 %)', l)

    def test_travail_debut_montre_la_ram_annoncee_en_go(self):
        l = self.ligne(action='travail-debut', source='file', nom='rendu', pid=7, ram=0.5, coeurs=1, limite=2.5)
        self.assertIn('rendu 7 (0.5 Go annoncés, 1 cœur, limite 2.5 Go)', l)
        self.assertNotIn('%', l)

    def test_travail_fin_montre_le_code_et_la_duree(self):
        l = self.ligne(action='travail-fin', source='file', nom='rendu', pid=7, code=0, duree=40.1)
        self.assertIn('rendu 7 (code 0, 40 s)', l)

    def test_travail_fin_montre_les_coeurs_en_moyenne(self):
        l = self.ligne(action='travail-fin', source='file', nom='rendu', pid=7, code=0, duree=40.1, pic_go=2.5,
                       cpu_s=216.2, coeurs_moyens=5.39)
        self.assertIn('rendu 7 (code 0, 40 s, pic 2.5 Go, 5,4 cœurs en moyenne)', l)

    def test_coeurs_moyens_pluriel_et_entiers(self):
        for valeur, texte in ((0.8, '0,8 cœur'), (1.0, '1 cœur'), (1.4, '1,4 cœur'), (2.0, '2 cœurs'), (13.04, '13 cœurs')):
            self.assertIn(f'{texte} en moyenne', self.ligne(action='travail-fin', nom='x', coeurs_moyens=valeur))

    def test_travail_fin_sans_coeurs_ou_invalides(self):
        for e in ({}, {'coeurs_moyens': None}, {'coeurs_moyens': 'beaucoup'}, {'coeurs_moyens': True}):
            self.assertNotIn('en moyenne', self.ligne(action='travail-fin', nom='x', code=0, **e))

    def test_travail_annule_dit_s_il_attendait(self):
        self.assertIn('(annulé en attente)', self.ligne(action='travail-annule', source='file', nom='a', pid=1,
                                                        signal=15, en_cours=False))
        self.assertIn('(arrêté en cours)', self.ligne(action='travail-annule', source='file', nom='a', pid=1,
                                                      signal=15, en_cours=True))

    def test_limite_montre_l_annonce_et_la_limite(self):
        l = self.ligne(action='limite', source='file', nom='tests', declare=4, limite=8)
        self.assertIn('tests (annoncé 4 Go, limite 8 Go dépassée)', l)
