"""Tests de l'affichage lisible du journal : nouveaux champs de la 1.2.0."""
import unittest

from gagarine import journal as jn


def e(action, **champs):
    return {'date': '2026-11-10T12:00:00+0000', 'action': action, **champs}


class TestDetailLisible(unittest.TestCase):
    def test_declencheur_du_fige(self):
        self.assertEqual(jn.detail_lisible(e('fige', ram=91.2, declencheur='anticipation')), 'RAM 91.2 %, anticipation')
        self.assertEqual(jn.detail_lisible(e('fige', ram=96, declencheur='seuil')), 'RAM 96 %, seuil')
        self.assertEqual(jn.detail_lisible(e('fige', ram=96)), 'RAM 96 %')   # ancien événement

    def test_pic_de_travail_fin(self):
        self.assertEqual(jn.detail_lisible(e('travail-fin', code=0, duree=61.4, pic_go=6.3)), 'code 0, 61 s, pic 6.3 Go')
        self.assertEqual(jn.detail_lisible(e('travail-fin', code=0, duree=3)), 'code 0, 3 s')

    def test_pic_de_limite(self):
        self.assertEqual(jn.detail_lisible(e('limite', declare=1, limite=2, pic_go=2.1)),
                         'annoncé 1 Go, limite 2 Go dépassée, pic 2.1 Go')
        self.assertEqual(jn.detail_lisible(e('limite', declare=1, limite=2)), 'annoncé 1 Go, limite 2 Go dépassée')

    def test_annonce_corrigee(self):
        self.assertEqual(jn.detail_lisible(e('travail-debut', ram=8, coeurs=2, annonce_initiale=1, ajuste=True)),
                         'annonce corrigée 1 → 8 Go, 2 cœurs')
        self.assertEqual(jn.detail_lisible(e('travail-debut', ram=8, coeurs=1, limite=16)), '8 Go annoncés, 1 cœur, limite 16 Go')

    def test_menage(self):
        self.assertEqual(jn.detail_lisible(e('menage', type='gradle', nom='GradleDaemon', memoire_mo=1234, auto=True)),
                         'gradle, 1234 Mo, automatique')
        self.assertEqual(jn.detail_lisible(e('menage', type='ollama', nom='llama', memoire_mo=500, auto=False)),
                         'ollama, 500 Mo')

    def test_ligne_complete(self):
        ligne = jn.ligne_lisible(e('menage', type='gradle', nom='GradleDaemon', pid=42, memoire_mo=700, auto=True))
        self.assertIn('menage', ligne)
        self.assertIn('GradleDaemon 42', ligne)
        self.assertTrue(ligne.endswith('(gradle, 700 Mo, automatique)'))


if __name__ == '__main__':
    unittest.main()
