"""Tests des cœurs utilisés par les travaux de la file (1.4.0) : `coeurs_utilises` de `--etat --json`, relevé précédent
dans $XDG_RUNTIME_DIR, `cpu_s` et `coeurs_moyens` de `travail-fin`. Le poste est simulé."""
import json
import tempfile
import unittest
from pathlib import Path

from gagarine import cli
from gagarine import file as fa
from tests.test_file import BancFile

T0 = 1762785000.0


class BancCpu(BancFile):
    def setUp(self):
        super().setUp()
        self.xdg = self.d / 'xdg'
        self.env['XDG_RUNTIME_DIR'] = str(self.xdg)
        self.releve = self.xdg / 'gagarine' / 'cpu-travaux.json'

    def travail(self, numero, pid, nom, etat='en cours', unite=True, **champs):
        """Un ticket de la file Gagarine ; `unite` : lui donne son scope gagarine-travail-<ticket>."""
        ticket = f'{numero}-{pid}.json'
        if unite:
            champs['unite'] = f'gagarine-travail-{numero}-{pid}.scope'
        self.ticket(ticket, pid, nom_travail=nom, source='gagarine', etat=etat, ram=1, coeurs=1, **champs)
        return ticket

    def etat(self):
        code, sortie, _ = self.lancer_main(['file', '--etat', '--json'],
                                           cli.Contexte(None, root=False, env=self.env, poste=self.poste))
        self.assertEqual(code, 0)
        return json.loads(sortie)

    def texte(self):
        return self.lancer_main(['file', '--etat'], cli.Contexte(None, root=False, env=self.env, poste=self.poste))[1]


class TestCoeursUtilises(BancCpu):
    def test_mesure_de_0_3_s_sans_releve_precedent(self):
        self.travail(1, 10, 'rendu')
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [1_000_000, 1_000_000 + 1_860_000]   # 1,86 s en 0,3 s
        t = self.etat()['travaux'][0]
        self.assertEqual(t['coeurs_utilises'], 6.2)
        self.assertEqual(self.poste.pauses, 1)
        self.assertAlmostEqual(self.poste.t, T0 + 0.3)

    def test_releve_precedent_entre_0_5_et_30_s(self):
        t = self.travail(1, 10, 'rendu')
        self.releve.parent.mkdir(parents=True)
        self.releve.write_text(json.dumps({'t': T0 - 10, 'travaux': {t: 5_000_000}}))
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [5_000_000 + 25_000_000]       # 25 s en 10 s
        self.assertEqual(self.etat()['travaux'][0]['coeurs_utilises'], 2.5)
        self.assertEqual(self.poste.pauses, 0)

    def test_le_releve_est_garde_pour_la_fois_suivante(self):
        t = self.travail(1, 10, 'rendu')
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [0, 300_000, 9_000_000]
        self.etat()
        garde = json.loads(self.releve.read_text())
        self.assertEqual(garde['travaux'], {t: 300_000})
        self.assertAlmostEqual(garde['t'], T0 + 0.3)
        self.poste.t = T0 + 5.3
        self.assertEqual(self.etat()['travaux'][0]['coeurs_utilises'], 1.74)   # 8,7 s de processeur en 5 s
        self.assertEqual(self.poste.pauses, 1)

    def test_releve_trop_recent_ou_trop_vieux_remesure(self):
        t = self.travail(1, 10, 'rendu')
        for age in (0.2, 31):
            self.poste.pauses = 0
            self.releve.parent.mkdir(parents=True, exist_ok=True)
            self.releve.write_text(json.dumps({'t': T0 - age, 'travaux': {t: 0}}))
            self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [0, 600_000]
            self.assertEqual(self.etat()['travaux'][0]['coeurs_utilises'], 2.0, age)
            self.assertEqual(self.poste.pauses, 1, age)

    def test_releve_abime_remesure(self):
        self.travail(1, 10, 'rendu')
        self.releve.parent.mkdir(parents=True)
        self.releve.write_text('pas du json')
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [0, 300_000]
        self.assertEqual(self.etat()['travaux'][0]['coeurs_utilises'], 1.0)

    def test_illisible_donne_null(self):
        self.travail(1, 10, 'rendu')
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = []
        self.poste.memoires['gagarine-travail-1-10.scope'] = 1.0   # le cgroup existe mais cpu.stat est illisible
        t = self.etat()['travaux'][0]
        self.assertIn('coeurs_utilises', t)
        self.assertIsNone(t['coeurs_utilises'])

    def test_sans_cgroup_null_et_sans_pause(self):
        self.travail(1, 10, 'rendu')
        self.assertIsNone(self.etat()['travaux'][0]['coeurs_utilises'])
        self.assertEqual(self.poste.pauses, 0)

    def test_en_attente_et_ancien_outil_null(self):
        self.travail(1, 10, 'a', etat='en attente', unite=False)
        self.ticket('2-20.json', 20)
        for t in self.etat()['travaux']:
            self.assertIsNone(t['coeurs_utilises'])
        self.assertEqual(self.poste.pauses, 0)

    def test_deux_travaux_une_seule_pause(self):
        self.travail(1, 10, 'a')
        self.travail(2, 20, 'b')
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [0, 300_000]
        self.poste.cpu_scope['gagarine-travail-2-20.scope'] = [0, 150_000]
        self.assertEqual([t['coeurs_utilises'] for t in self.etat()['travaux']], [1.0, 0.5])
        self.assertEqual(self.poste.pauses, 1)

    def test_compteur_qui_recule_donne_null(self):
        t = self.travail(1, 10, 'rendu')
        self.releve.parent.mkdir(parents=True)
        self.releve.write_text(json.dumps({'t': T0 - 10, 'travaux': {t: 9_000_000}}))
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [1_000_000]
        self.assertIsNone(self.etat()['travaux'][0]['coeurs_utilises'])

    def test_sans_xdg_runtime_dir_rien_n_est_ecrit(self):
        del self.env['XDG_RUNTIME_DIR']
        self.travail(1, 10, 'rendu')
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [0, 300_000]
        self.assertEqual(self.etat()['travaux'][0]['coeurs_utilises'], 1.0)
        self.assertFalse(self.xdg.exists())

    def test_ne_change_pas_les_regles_de_passage(self):
        self.travail(1, 10, 'a')
        self.travail(2, 20, 'b', etat='en attente', unite=False)
        avant = self.etat()
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [0, 300_000]
        apres = self.etat()
        self.assertEqual([t['motif'] for t in avant['travaux']], [t['motif'] for t in apres['travaux']])

    def test_texte_ajoute_les_coeurs_au_travail_en_cours(self):
        self.travail(1, 10, 'rendu')
        self.travail(2, 20, 'tests', etat='en attente', unite=False)
        self.poste.cpu_scope['gagarine-travail-1-10.scope'] = [0, 1_860_000]
        lignes = self.texte().splitlines()
        self.assertTrue(lignes[1].endswith(', RAM annoncée 1 Go) · 6,2 cœurs'), lignes[1])
        self.assertNotIn('cœur', lignes[2])

    def test_texte_sans_mesure_ne_change_pas(self):
        self.travail(1, 10, 'rendu')
        self.assertNotIn('cœurs', self.texte().splitlines()[1])

    def test_fr_coeurs(self):
        self.assertEqual([fa.fr_coeurs(x) for x in (6.2, 1.4, 0.8, 8, 0.04)],
                         ['6,2 cœurs', '1,4 cœur', '0,8 cœur', '8 cœurs', '0 cœur'])


class TestCpuDuTravail(BancCpu):
    def lancer(self, **kw):
        self.poste.t = T0
        return self.lancer_file(**kw)

    def fin(self):
        return [e for e in self.journal() if e['action'] == 'travail-fin'][0]

    def test_cpu_s_et_coeurs_moyens(self):
        self.poste.pics = [0.2]
        self.poste.cpus = [1_000_000, 20_000_000]     # le dernier relevé tient
        self.poste.lancer = self._lancer_en(10)
        self.lancer()
        fin = self.fin()
        self.assertEqual(fin['cpu_s'], 20.0)
        self.assertEqual(fin['coeurs_moyens'], 2.0)

    def _lancer_en(self, secondes):
        faux = self.poste

        def lancer(argv, env, pendant=None):
            faux.lances.append((argv, env))
            pendant()
            faux.t += secondes
            return 0
        return lancer

    def test_le_plus_haut_releve_est_garde(self):
        self.poste.pics = [0.2]
        self.poste.cpus = [8_000_000, 3_000_000]     # compteur illisible ensuite ou en recul : on garde le plus haut
        self.poste.lancer = self._lancer_en(4)
        self.lancer()
        self.assertEqual(self.fin()['cpu_s'], 8.0)
        self.assertEqual(self.fin()['coeurs_moyens'], 2.0)

    def test_scope_illisible_pas_de_cpu(self):
        self.poste.pics = [0.2]
        self.lancer()
        fin = self.fin()
        self.assertNotIn('cpu_s', fin)
        self.assertNotIn('coeurs_moyens', fin)

    def test_sans_systemd_pas_de_cpu(self):
        self.poste.systemd = False
        self.lancer()
        self.assertNotIn('cpu_s', self.fin())

    def test_duree_nulle_pas_de_moyenne(self):
        self.poste.pics = [0.2]
        self.poste.cpus = [500_000]
        self.lancer()
        self.assertEqual(self.fin()['cpu_s'], 0.5)
        self.assertNotIn('coeurs_moyens', self.fin())

    def test_ne_change_ni_le_code_ni_le_pic(self):
        self.poste.pics = [0.4]
        self.poste.cpus = [1_000_000]
        self.poste.code = 3
        self.assertEqual(self.lancer(), 3)
        self.assertEqual(self.fin()['pic_go'], 0.4)


class TestPosteCpuReel(unittest.TestCase):
    """Le vrai Poste lit un faux cpu.stat dans un dossier temporaire."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cg = Path(self.tmp.name)
        self.poste = fa.Poste()

    def test_usage_usec(self):
        (self.cg / 'cpu.stat').write_text('usage_usec 123456\nuser_usec 100000\nsystem_usec 23456\n')
        self.assertEqual(self.poste.cpu_cgroup(self.cg), 123456)

    def test_illisible(self):
        self.assertIsNone(self.poste.cpu_cgroup(self.cg))
        (self.cg / 'cpu.stat').write_text('user_usec 1\n')
        self.assertIsNone(self.poste.cpu_cgroup(self.cg))
        (self.cg / 'cpu.stat').write_text('usage_usec abc\n')
        self.assertIsNone(self.poste.cpu_cgroup(self.cg))


if __name__ == '__main__':
    unittest.main()
