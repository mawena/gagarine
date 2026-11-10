"""Tests des réglages : lecture, priorités, dossiers d'état."""
import tempfile
import unittest
from pathlib import Path

from gagarine import reglages as rg


class TestReglages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fichier(self, nom, texte):
        p = Path(self.tmp.name) / nom
        p.write_text(texte)
        return str(p)

    def test_valeurs_par_defaut_si_fichier_absent(self):
        r, av = rg.charger_reglages(None, {'GAGARINE_CONFIG': '/nexiste/pas'})
        self.assertEqual(r, rg.Reglages())
        self.assertEqual((r.seuil, r.urgence, r.vigilance, r.grace, r.vidage_max, r.delai_fige, r.reste_si_repris),
                         (95, 98, 85, 10, 60, 600, 256))
        self.assertTrue(r.notifications)
        self.assertTrue(av)

    def test_lecture_du_fichier(self):
        f = self.fichier('a.conf', '[gagarine]\nseuil=90\nurgence = 97.5\nnotifications=non\nproteges=foo|bar\n'
                                   'reste_si_repris=128\n')
        r, av = rg.charger_reglages(f, {})
        self.assertEqual((r.seuil, r.urgence, r.reste_si_repris, r.notifications, r.proteges), (90, 97.5, 128, False, 'foo|bar'))
        self.assertEqual(av, [])

    def test_priorite_arg_puis_variable_config(self):
        a = self.fichier('a.conf', '[gagarine]\nseuil=91\n')
        b = self.fichier('b.conf', '[gagarine]\nseuil=92\n')
        self.assertEqual(rg.charger_reglages(a, {'GAGARINE_CONFIG': b})[0].seuil, 91)
        self.assertEqual(rg.charger_reglages(None, {'GAGARINE_CONFIG': b})[0].seuil, 92)
        self.assertEqual(rg.chemin_config(None, {}), '/etc/gagarine.conf')

    def test_variables_passent_devant_le_fichier(self):
        a = self.fichier('a.conf', '[gagarine]\nseuil=91\ngrace=7\n')
        r, _ = rg.charger_reglages(a, {'GAGARINE_SEUIL': '80.5', 'GAGARINE_NOTIFICATIONS': '0'})
        self.assertEqual((r.seuil, r.grace, r.notifications), (80.5, 7, False))

    def test_valeur_invalide_ignoree(self):
        a = self.fichier('a.conf', '[gagarine]\nseuil=beaucoup\nurgence=250\nproteges=(\nnotifications=peut-etre\ninconnue=1\n')
        r, av = rg.charger_reglages(a, {})
        self.assertEqual(r, rg.Reglages())
        self.assertEqual(len(av), 5)

    def test_fichier_sans_section(self):
        a = self.fichier('a.conf', 'seuil=90\n')
        r, av = rg.charger_reglages(a, {})
        self.assertEqual(r.seuil, 95)
        self.assertTrue(av)

    def test_fichier_integre_documente_et_valide(self):
        conf = (Path(__file__).resolve().parent.parent / 'etc' / 'gagarine.conf').read_text()
        for cle in ('seuil', 'urgence', 'vigilance', 'grace', 'vidage_max', 'delai_fige', 'reste_si_repris',
                    'notifications', 'proteges'):
            self.assertIn(f'#{cle}=', conf)
        r, av = rg.charger_reglages(str(Path(__file__).resolve().parent.parent / 'etc' / 'gagarine.conf'), {})
        self.assertEqual((r, av), (rg.Reglages(), []))

    def test_dossier_etat(self):
        self.assertEqual(rg.dossier_etat({'GAGARINE_ETAT': '/x'}, 0), Path('/x'))
        self.assertEqual(rg.dossier_etat({}, 0), Path('/var/lib/gagarine'))
        self.assertEqual(rg.dossier_etat({}, 1000, '/home/u'), Path('/home/u/.local/state/gagarine'))

    def test_dossier_etat_lecture(self):
        self.assertEqual(rg.dossier_etat_lecture({}, 1000, '/home/u', lisible=lambda p, m: True), Path('/var/lib/gagarine'))
        self.assertEqual(rg.dossier_etat_lecture({}, 1000, '/home/u', lisible=lambda p, m: False),
                         Path('/home/u/.local/state/gagarine'))
        self.assertEqual(rg.dossier_etat_lecture({'GAGARINE_ETAT': '/x'}), Path('/x'))
