"""`gagarine ntfy essai CIBLE` : message d'essai, sans aucun envoi réel (faux `ouvrir`)."""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from gagarine import ntfy
from gagarine.cli import main


class Reponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def appeler(args, ouvrir):
    sortie, erreur = io.StringIO(), io.StringIO()
    with redirect_stdout(sortie), redirect_stderr(erreur):
        code = ntfy.commande_essai(args, None, ouvrir=ouvrir)
    return code, sortie.getvalue(), erreur.getvalue()


class Essai(unittest.TestCase):
    def test_accepte_code_0(self):
        vues = []

        def ouvrir(requete, timeout=None):
            vues.append(requete)
            return Reponse(200)
        code, sortie, _ = appeler(['mon-sujet'], ouvrir)
        self.assertEqual(code, 0)
        self.assertIn('mon-sujet', sortie)
        self.assertEqual(vues[0].full_url, 'https://ntfy.sh/')
        corps = json.loads(vues[0].data)
        self.assertEqual(corps['topic'], 'mon-sujet')
        self.assertIn('essai', corps['title'].lower())

    def test_serveur_a_part(self):
        vues = []
        code, _, _ = appeler(['https://ntfy.exemple.fr/alertes'], lambda r, timeout=None: vues.append(r) or Reponse())
        self.assertEqual(code, 0)
        self.assertEqual(vues[0].full_url, 'https://ntfy.exemple.fr/')
        self.assertEqual(json.loads(vues[0].data)['topic'], 'alertes')

    def test_refuse_code_1(self):
        self.assertEqual(appeler(['sujet'], lambda r, timeout=None: Reponse(403))[0], 1)

    def test_injoignable_code_1(self):
        def ouvrir(r, timeout=None):
            raise OSError('pas de réseau')
        code, _, erreur = appeler(['sujet'], ouvrir)
        self.assertEqual(code, 1)
        self.assertIn('pas accepté', erreur)

    def test_cible_invalide_code_2_sans_envoi(self):
        ouvrir = mock.Mock()
        for cible in ('deux mots', 'a/b c', '', 'ftp://x/y', 'é'):
            with self.subTest(cible=cible):
                self.assertEqual(appeler([cible], ouvrir)[0], 2)
        ouvrir.assert_not_called()

    def test_arguments(self):
        ouvrir = mock.Mock()
        self.assertEqual(appeler([], ouvrir)[0], 2)
        self.assertEqual(appeler(['a', 'b'], ouvrir)[0], 2)
        ouvrir.assert_not_called()

    def test_extension_branchee_et_aide(self):
        from gagarine import cli
        self.assertIn('ntfy', cli.EXTENSIONS)
        self.assertIn('ntfy essai CIBLE', cli.aide())
        erreur = io.StringIO()
        with redirect_stderr(erreur):
            self.assertEqual(main(['ntfy']), 2)
        self.assertIn('ntfy essai', erreur.getvalue())


if __name__ == '__main__':
    unittest.main()
