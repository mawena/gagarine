"""Tests de la tendance des travaux d'un lancement à l'autre (conception 1.3.0, section 2) : hausse, limite, stable,
message et événement `fuite-travail` au lancement, `tendance` dans `file appris`."""
import json
import unittest

from gagarine import cli
from gagarine import file as fa
from tests.test_file_appris import BancAppris


class TestTendance(unittest.TestCase):
    def test_hausse_sur_trois_pics(self):
        self.assertEqual(fa.tendance([2.1, 3.4, 5.0]), 'hausse')

    def test_hausse_sur_la_fin_seulement(self):
        self.assertEqual(fa.tendance([9.0, 2.0, 2.5, 3.0]), 'hausse')

    def test_hausse_trop_faible(self):
        """Il faut que le dernier vaille au moins 1,3 fois le plus ancien des trois."""
        self.assertEqual(fa.tendance([2.0, 2.2, 2.5]), 'stable')
        self.assertEqual(fa.tendance([2.0, 2.2, 2.6]), 'hausse')

    def test_deux_pics_ne_suffisent_pas(self):
        self.assertEqual(fa.tendance([1.0, 5.0]), 'stable')

    def test_un_pic_qui_ne_monte_pas_casse_la_serie(self):
        self.assertEqual(fa.tendance([2.0, 4.0, 4.0, 6.0]), 'stable')
        self.assertEqual(fa.tendance([2.0, 4.0, 3.0, 6.0]), 'stable')

    def test_limite_aux_deux_derniers(self):
        self.assertEqual(fa.tendance([3, 3], [False, True, True]), 'limite')
        self.assertEqual(fa.tendance([3, 3], [True, False, True]), 'stable')
        self.assertEqual(fa.tendance([3], [True]), 'stable')

    def test_hausse_passe_avant_limite(self):
        self.assertEqual(fa.tendance([1.0, 2.0, 3.0], [False, True, True]), 'hausse')

    def test_vide(self):
        self.assertEqual(fa.tendance([]), 'stable')


class TestTendanceFichier(BancAppris):
    def test_noter_pic_ecrit_la_tendance(self):
        for pic in (2.1, 3.4, 5.0):
            fa.noter_pic(self.env, 'tests voix', pic, 1, self.poste.t)
        t = self.travaux()['tests voix']
        self.assertEqual((t['tendance'], t['pics_go'], t['limites']), ('hausse', [2.1, 3.4, 5.0], [False] * 3))

    def test_noter_pic_note_les_arrets_par_la_limite(self):
        fa.noter_pic(self.env, 'x', 3, 1, self.poste.t, True)
        fa.noter_pic(self.env, 'x', 3, 1, self.poste.t, True)
        t = self.travaux()['x']
        self.assertEqual((t['tendance'], t['limites']), ('limite', [True, True]))

    def test_ancien_fichier_sans_limites(self):
        self.savoir('x', [3.0, 3.0])        # écrit comme avant la 1.3.0 : ni « limites » ni « tendance »
        fa.noter_pic(self.env, 'x', 3, 1, self.poste.t, True)
        t = self.travaux()['x']
        self.assertEqual(t['limites'], [False, False, True])
        self.assertEqual(t['tendance'], 'stable')

    def test_la_limite_depassee_est_notee_par_le_lancement(self):
        self.poste.resultat = 'oom-kill'
        self.poste.pics = [0.5]
        self.lancer_avec(ram=1)
        self.assertEqual(self.travaux()['tests']['limites'], [True])


class TestMessageAuLancement(BancAppris):
    def fuite(self):
        return [e for e in self.journal() if e['action'] == 'fuite-travail']

    def test_hausse(self):
        self.savoir('tests voix', [2.1, 3.4, 5.0], annonce=8)
        self.lancer_avec(nom='tests voix', ram=8)
        self.assertIn('  file d\'attente (tests voix) : pic en hausse à chaque lancement (2,1 → 3,4 → 5,0 Go) : fuite probable',
                      self.sortie)
        (e,) = self.fuite()
        self.assertEqual((e['nom'], e['tendance'], e['pics_go'], e['source']), ('tests voix', 'hausse', [2.1, 3.4, 5.0], 'file'))

    def test_limite(self):
        self.savoir('x', [3.0, 3.0])
        t = json.loads((self.dossier_etat / 'travaux.json').read_text())
        t['x']['limites'] = [True, True]
        (self.dossier_etat / 'travaux.json').write_text(json.dumps(t))
        self.lancer_avec(nom='x', ram=1)
        self.assertIn('arrêté par sa limite mémoire aux deux derniers lancements : fuite probable', self.sortie)
        self.assertEqual(self.fuite()[0]['tendance'], 'limite')

    def test_stable_sans_message_ni_evenement(self):
        self.savoir('x', [2.0, 2.0, 2.0])
        self.lancer_avec(nom='x', ram=2)
        self.assertEqual(self.sortie, '')
        self.assertEqual(self.fuite(), [])

    def test_premier_lancement_sans_message(self):
        self.lancer_avec(nom='neuf')
        self.assertEqual(self.sortie, '')
        self.assertEqual(self.fuite(), [])

    def test_travail_herite_sans_message(self):
        self.savoir('x', [2.0, 3.4, 5.0], annonce=8)
        self.ticket('1-10.json', 10)
        self.lancer_avec(nom='x', ram=8, env={**self.env, 'FILE_ATTENTE_TICKET': '1-10.json'})
        self.assertEqual(self.fuite(), [])

    def test_evenement_apres_le_debut_du_travail(self):
        self.savoir('x', [2.0, 3.4, 5.0], annonce=8)
        self.lancer_avec(nom='x', ram=8)
        actions = [e['action'] for e in self.journal()]
        self.assertLess(actions.index('travail-debut'), actions.index('fuite-travail'))


class TestAppris(BancAppris):
    def appris(self, *args):
        return self.lancer_main(['file', 'appris', *args], cli.Contexte(None, root=False, env=self.env, poste=self.poste))

    def test_json_tendance(self):
        self.savoir('monte', [2.1, 3.4, 5.0], derniere='2026-11-10T10:00:00+0000')
        self.savoir('calme', [2.0, 2.1, 2.0], derniere='2026-11-09T10:00:00+0000')
        t = {x['nom']: x['tendance'] for x in json.loads(self.appris('--json')[1])['travaux']}
        self.assertEqual(t, {'monte': 'hausse', 'calme': 'stable'})

    def test_texte_fuite_probable(self):
        self.savoir('monte', [2.1, 3.4, 5.0])
        self.assertIn('fuite probable', self.appris()[1])

    def test_texte_stable_inchange(self):
        self.savoir('calme', [2.0])
        self.assertNotIn('fuite', self.appris()[1])


if __name__ == '__main__':
    unittest.main()
