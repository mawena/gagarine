"""Tests de la préparation de la 1.2.0 : nouveaux réglages, alertes ntfy, commandes apportées par des extensions."""
import io
import json
import types
import unittest
from contextlib import redirect_stdout

from gagarine import cli
from gagarine import ntfy
from gagarine import reglages as rg


class TestReglages120(unittest.TestCase):
    def test_valeurs_par_defaut(self):
        r = rg.Reglages()
        self.assertEqual((r.anticipation, r.pression, r.notification_fin_min, r.menage_delai), (15, 20, 30, 30))
        self.assertEqual((r.notifications_fin, r.file_ajuster, r.menage_auto, r.claude_bloquer), (True,) * 4)
        self.assertEqual(r.ntfy, '')

    def test_zero_desactive_seulement_ou_permis(self):
        self.assertEqual(rg.valider_ecriture({'anticipation': '0', 'pression': '0', 'notification_fin_min': '0'}),
                         {'anticipation': '0', 'pression': '0', 'notification_fin_min': '0'})
        for cle in ('menage_delai', 'seuil', 'grace'):
            with self.assertRaises(rg.ErreurReglages):
                rg.valider_ecriture({cle: '0'})

    def test_pression_est_un_pourcentage(self):
        with self.assertRaises(rg.ErreurReglages):
            rg.valider_ecriture({'pression': '101'})

    def test_ntfy(self):
        for bon in ('', 'gagarine-k7f3q9', 'https://ntfy.exemple.fr/gagarine', 'http://10.0.0.2:8080/a_b'):
            self.assertEqual(rg.convertir('ntfy', bon), bon)
        for mauvais in ('a b', 'ftp://x/y', 'https://serveur/', 'sujet/avec/barres', 'x' * 65):
            with self.assertRaises(ValueError, msg=mauvais):
                rg.convertir('ntfy', mauvais)

    def test_nouvelles_cles_dans_le_json_des_reglages(self):
        d = rg.reglages_en_dict(rg.Reglages())
        for cle in ('anticipation', 'pression', 'notifications_fin', 'notification_fin_min', 'ntfy', 'file_ajuster',
                    'menage_auto', 'menage_delai', 'claude_bloquer'):
            self.assertIn(cle, d)

    def test_fichier_par_defaut_sans_avertissement(self):
        r, av = rg.charger_reglages('etc/gagarine.conf', {})
        self.assertEqual(av, [])
        self.assertEqual(r.anticipation, 15)


class TestNtfy(unittest.TestCase):
    def test_destination(self):
        self.assertIsNone(ntfy.destination(''))
        self.assertEqual(ntfy.destination('gagarine-x'), ('https://ntfy.sh', 'gagarine-x'))
        self.assertEqual(ntfy.destination('https://n.exemple.fr/a/b/sujet'), ('https://n.exemple.fr/a/b', 'sujet'))

    def test_message_json_en_utf8(self):
        adresse, corps = ntfy.message('gagarine-x', 'Programme figé', '« python3 » : 8,9 Go', 'haute', ('warning',))
        self.assertEqual(adresse, 'https://ntfy.sh/')
        self.assertEqual(json.loads(corps.decode('utf-8')),
                         {'topic': 'gagarine-x', 'title': 'Programme figé', 'message': '« python3 » : 8,9 Go',
                          'priority': 4, 'tags': ['warning']})

    def test_envoyer_ne_leve_jamais(self):
        def panne(*a, **k):
            raise OSError('réseau coupé')
        self.assertFalse(ntfy.envoyer('gagarine-x', 't', 'm', ouvrir=panne))
        self.assertFalse(ntfy.envoyer('', 't', 'm', ouvrir=panne))

    def test_envoyer_poste_la_requete(self):
        vu = []

        class Reponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def ouvrir(requete, timeout):
            vu.append((requete.full_url, requete.get_method(), timeout))
            return Reponse()
        self.assertTrue(ntfy.envoyer('gagarine-x', 't', 'm', ouvrir=ouvrir, delai=3))
        self.assertEqual(vu, [('https://ntfy.sh/', 'POST', 3)])

    def test_en_fond_rien_sans_reglage(self):
        self.assertIsNone(ntfy.envoyer_en_fond('', 't', 'm'))


class TestExtensions(unittest.TestCase):
    def importer(self, modules):
        def importer(nom):
            if nom in modules:
                return modules[nom]
            e = ModuleNotFoundError(f'No module named {nom!r}')
            e.name = nom
            raise e
        return importer

    def test_module_absent_ignore_module_present_charge(self):
        m = types.SimpleNamespace(COMMANDES={'menage': lambda a, c: 7}, AIDE_COMMANDES='  menage [--json]   ménage')
        ext = cli.charger_extensions(('claude', 'menage'), self.importer({'gagarine.menage': m}))
        self.assertEqual([c for _, c, _ in ext], [m.COMMANDES])
        self.assertIn('  menage [--json]   ménage\n  version ', cli.aide(ext))

    def test_dependance_manquante_d_une_extension_n_est_pas_masquee(self):
        def importer(nom):
            e = ModuleNotFoundError("No module named 'gi'")
            e.name = 'gi'
            raise e
        with self.assertRaises(ModuleNotFoundError):
            cli.charger_extensions(('menage',), importer)

    def test_main_trouve_une_commande_d_extension(self):
        m = types.SimpleNamespace(COMMANDES={'bidule': lambda a, c: 42}, AIDE_COMMANDES='')
        ancien = cli.charger_extensions
        cli.charger_extensions = lambda *a, **k: [(m, m.COMMANDES, '')]
        try:
            self.assertEqual(cli.main(['bidule'], ctx=object()), 42)
        finally:
            cli.charger_extensions = ancien

    def test_aide_sans_extension_inchangee(self):
        self.assertEqual(cli.aide([]), cli.AIDE)
        with redirect_stdout(io.StringIO()) as s:
            self.assertEqual(cli.main(['-h']), 0)
        self.assertIn('gagarine', s.getvalue())


if __name__ == '__main__':
    unittest.main()
