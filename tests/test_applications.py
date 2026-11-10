"""Tests de `gagarine applications` (gagarine/applications.py) avec des scopes simulés et un dossier temporaire."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gagarine import applications as ap
from gagarine.cli import main

GO = 1024 ** 3
MO = 1024 ** 2


class FauxSystemd:
    """Remplace `systemctl --user` : des scopes avec mémoire et plafond, et la liste des appels."""

    def __init__(self, env, scopes):
        self.env = env
        self.scopes = dict(scopes)   # nom -> {'memoire': octets, 'plafond': octets ou None}
        self.appels = []
        self.echec = None

    def executer(self, cmd):
        self.appels.append(cmd)
        if self.echec and self.echec in cmd:
            return 1, '', 'Échec simulé'
        verbe = cmd[2]
        if verbe == 'show':
            blocs = []
            for nom, s in self.scopes.items():
                blocs.append(f'MemoryCurrent={s["memoire"] if s["memoire"] is not None else "[not set]"}\n'
                             f'MemoryHigh={s["plafond"] if s["plafond"] else "infinity"}\nId={nom}\n')
            return 0, '\n'.join(blocs), ''
        if verbe == 'set-property':
            scope, propriete = cmd[4], cmd[5]
            v = propriete.split('=', 1)[1]
            self.scopes[scope]['plafond'] = None if v == 'infinity' else int(float(v[:-1]) * GO)
        return 0, '', ''


class Ctx:
    def __init__(self, env, systemd):
        self.env = env
        self.config = None
        self.executer = systemd.executer


SCOPES = {
    'app-gnome-google\\x2dchrome-38855.scope': {'memoire': 4000 * MO, 'plafond': None},
    'app-gnome-google\\x2dchrome-40001.scope': {'memoire': 500 * MO, 'plafond': None},
    'app-gnome-org.gnome.Nautilus-1234.scope': {'memoire': 80 * MO, 'plafond': None},
    'app-com.microsoft.VSCode-40595.scope': {'memoire': 700 * MO, 'plafond': None},
    'snap.firefox.firefox-6d3a1b2c4e5f.scope': {'memoire': 900 * MO, 'plafond': None},
    'app-gnome-vide-5.scope': {'memoire': None, 'plafond': None},
    'vte-spawn-24cc.scope': {'memoire': 10 * MO, 'plafond': None},
    'init.scope': {'memoire': 1 * MO, 'plafond': None},
    'gagarine-travail-1-2.scope': {'memoire': 1 * MO, 'plafond': None},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        bureau = self.home / 'bureau'
        bureau.mkdir()
        (bureau / 'google-chrome.desktop').write_text(
            '[Desktop Action new]\nName=Nouvelle fenêtre\n[Desktop Entry]\nName=Google Chrome\nName[fr]=Chrome FR\n')
        (bureau / 'org.gnome.Nautilus.desktop').write_text('[Desktop Entry]\nName=Fichiers\n')
        (bureau / 'firefox_firefox.desktop').write_text('[Desktop Entry]\nName=Firefox\n')
        self.bureau = [bureau]
        self.env = {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / 'config')}
        self.systemd = FauxSystemd(self.env, SCOPES)
        self.ctx = Ctx(self.env, self.systemd)
        self.unites = self.home / 'config/systemd/user'

    def lancer(self, *args):
        sortie, erreur = io.StringIO(), io.StringIO()
        with redirect_stdout(sortie), redirect_stderr(erreur):
            code = ap.commande_applications(list(args), self.ctx)
        return code, sortie.getvalue(), erreur.getvalue()

    def donnees(self):
        return ap.donnees_applications(self.ctx, self.bureau)['applications']


class TestNoms(unittest.TestCase):
    def test_echappement_systemd(self):
        self.assertEqual(ap.echapper('google-chrome'), 'google\\x2dchrome')
        self.assertEqual(ap.echapper('org.gnome.Nautilus'), 'org.gnome.Nautilus')
        self.assertEqual(ap.echapper('a/b'), 'a-b')
        self.assertEqual(ap.echapper('.caché'), '\\x2ecach\\xc3\\xa9')
        self.assertEqual(ap.deséchapper('google\\x2dchrome'), 'google-chrome')
        self.assertEqual(ap.deséchapper(ap.echapper('é-à b')), 'é-à b')

    def test_prefixes(self):
        self.assertEqual(ap.prefixe_du_scope('app-gnome-google\\x2dchrome-38855.scope'), 'app-gnome-google\\x2dchrome-')
        self.assertEqual(ap.prefixe_du_scope('app-com.microsoft.VSCode-1.scope'), 'app-com.microsoft.VSCode-')
        self.assertEqual(ap.prefixe_du_scope('snap.firefox.firefox-6d3a.scope'), 'snap.firefox.firefox-')
        for faux in ('init.scope', 'vte-spawn-1.scope', 'gagarine-travail-1-2.scope', 'app-x.service', 'app-gnome.scope'):
            self.assertIsNone(ap.prefixe_du_scope(faux), faux)

    def test_identifiants(self):
        self.assertEqual(ap.id_du_prefixe('app-gnome-google\\x2dchrome-'), 'google-chrome')
        self.assertEqual(ap.id_du_prefixe('app-com.microsoft.VSCode-'), 'app-com.microsoft.VSCode')
        self.assertEqual(ap.id_du_prefixe('snap.firefox.firefox-'), 'snap.firefox.firefox')
        self.assertEqual(ap.prefixe_de_l_id('google-chrome'), 'app-gnome-google\\x2dchrome-')
        self.assertEqual(ap.prefixe_de_l_id('app-com.microsoft.VSCode'), 'app-com.microsoft.VSCode-')
        self.assertEqual(ap.prefixe_de_l_id('snap.firefox.firefox'), 'snap.firefox.firefox-')
        for p in ('app-gnome-google\\x2dchrome-', 'app-com.microsoft.VSCode-', 'snap.firefox.firefox-'):
            self.assertEqual(ap.prefixe_de_l_id(ap.id_du_prefixe(p)), p)


class TestListe(Base):
    def test_liste(self):
        apps = {a['id']: a for a in self.donnees()}
        self.assertEqual(set(apps), {'google-chrome', 'org.gnome.Nautilus', 'app-com.microsoft.VSCode',
                                     'snap.firefox.firefox', 'vide'})
        chrome = apps['google-chrome']
        self.assertEqual(chrome['nom'], 'Google Chrome')
        self.assertEqual(chrome['instances'], 2)
        self.assertEqual(chrome['memoire_mo'], 4500)
        self.assertEqual(chrome['prefixe'], 'app-gnome-google\\x2dchrome-')
        self.assertIsNone(chrome['plafond_go'])
        self.assertEqual(apps['snap.firefox.firefox']['nom'], 'Firefox')
        self.assertEqual(apps['org.gnome.Nautilus']['nom'], 'Fichiers')
        self.assertEqual(apps['app-com.microsoft.VSCode']['nom'], 'app-com.microsoft.VSCode')   # pas de .desktop trouvé
        self.assertEqual(apps['vide']['memoire_mo'], 0)

    def test_tri_par_memoire(self):
        self.assertEqual([a['id'] for a in self.donnees()][:2], ['google-chrome', 'snap.firefox.firefox'])

    def test_json(self):
        code, sortie, _ = self.lancer('--json')
        self.assertEqual(code, 0)
        d = json.loads(sortie)
        self.assertEqual(set(d['applications'][0]), {'id', 'nom', 'prefixe', 'instances', 'memoire_mo', 'plafond_go'})

    def test_texte(self):
        code, sortie, _ = self.lancer()
        self.assertEqual(code, 0)
        self.assertIn('Application', sortie.splitlines()[0])
        self.assertIn('4500 Mo', sortie)

    def test_systemctl_absent(self):
        self.systemd.executer = lambda cmd: (127, '', 'absent')
        self.ctx.executer = self.systemd.executer
        code, sortie, _ = self.lancer()
        self.assertEqual((code, sortie), (0, 'Aucune application du bureau en cours ni plafonnée.\n'))

    def test_plafond_existant_de_systemd_montre(self):
        self.systemd.scopes['app-gnome-google\\x2dchrome-38855.scope']['plafond'] = 2 * GO
        self.assertEqual({a['id']: a['plafond_go'] for a in self.donnees()}['google-chrome'], 2.0)


class TestPlafond(Base):
    prefixe = 'app-gnome-google\\x2dchrome-'

    def fichier(self):
        return self.unites / f'{self.prefixe}.scope.d' / '50-gagarine.conf'

    def test_pose_du_plafond(self):
        code, sortie, _ = self.lancer('plafond', 'google-chrome', '3')
        self.assertEqual(code, 0)
        self.assertIn('3 Go', sortie)
        self.assertIn('2 instances', sortie)
        self.assertTrue(self.fichier().read_text().endswith('[Scope]\nMemoryHigh=3G\n'))
        verbes = [c[2] for c in self.systemd.appels]
        self.assertEqual(verbes.count('daemon-reload'), 1)
        regles = [c for c in self.systemd.appels if c[2] == 'set-property']
        self.assertEqual(sorted(c[4] for c in regles),
                         ['app-gnome-google\\x2dchrome-38855.scope', 'app-gnome-google\\x2dchrome-40001.scope'])
        self.assertTrue(all(c[3] == '--runtime' and c[5] == 'MemoryHigh=3G' for c in regles))
        self.assertLess(verbes.index('daemon-reload'), verbes.index('set-property'))
        self.assertEqual({a['id']: a['plafond_go'] for a in self.donnees()}['google-chrome'], 3.0)

    def test_decimales_avec_virgule(self):
        self.assertEqual(self.lancer('plafond', 'google-chrome', '1,5')[0], 0)
        self.assertIn('MemoryHigh=1.5G', self.fichier().read_text())

    def test_retrait_du_plafond(self):
        self.lancer('plafond', 'google-chrome', '3')
        code, sortie, _ = self.lancer('plafond', 'google-chrome', 'aucun')
        self.assertEqual(code, 0)
        self.assertIn('retiré', sortie)
        self.assertFalse(self.fichier().exists())
        self.assertFalse(self.fichier().parent.exists())
        self.assertEqual(self.systemd.scopes['app-gnome-google\\x2dchrome-38855.scope']['plafond'], None)
        self.assertEqual([c[5] for c in self.systemd.appels if c[2] == 'set-property'][-1], 'MemoryHigh=infinity')

    def test_retrait_garde_les_fichiers_des_autres(self):
        self.lancer('plafond', 'google-chrome', '3')
        autre = self.fichier().parent / '10-autre.conf'
        autre.write_text('[Scope]\nCPUWeight=50\n')
        self.lancer('plafond', 'google-chrome', 'aucun')
        self.assertTrue(autre.exists())
        self.assertFalse(self.fichier().exists())

    def test_application_snap_et_non_gnome(self):
        self.lancer('plafond', 'snap.firefox.firefox', '2')
        self.assertTrue((self.unites / 'snap.firefox.firefox-.scope.d/50-gagarine.conf').exists())
        self.lancer('plafond', 'app-com.microsoft.VSCode', '1')
        self.assertTrue((self.unites / 'app-com.microsoft.VSCode-.scope.d/50-gagarine.conf').exists())

    def test_application_pas_ouverte(self):
        code, sortie, _ = self.lancer('plafond', 'gimp', '2')
        self.assertEqual(code, 0)
        self.assertIn('prochain lancement', sortie)
        self.assertTrue((self.unites / 'app-gnome-gimp-.scope.d/50-gagarine.conf').exists())
        self.assertEqual([c for c in self.systemd.appels if c[2] == 'set-property'], [])
        self.assertIn('gimp', {a['id'] for a in self.donnees()})   # plafonnée : listée même fermée

    def test_valeurs_invalides(self):
        for valeur in ('abc', '-1', '0.0', '', 'nan', '1e9999'):
            with self.subTest(valeur=valeur):
                code, _, erreur = self.lancer('plafond', 'google-chrome', valeur)
                self.assertEqual(code, 2)
                self.assertIn('invalide', erreur)
        self.assertFalse(self.unites.exists())

    def test_identifiants_invalides(self):
        for identifiant in ('../x', 'a/b', '..', ''):
            with self.subTest(identifiant=identifiant):
                self.assertEqual(self.lancer('plafond', identifiant, '2')[0], 2)
        self.assertFalse(self.unites.exists())

    def test_echec_daemon_reload(self):
        self.systemd.echec = 'daemon-reload'
        code, _, erreur = self.lancer('plafond', 'google-chrome', '3')
        self.assertEqual(code, 1)
        self.assertIn('daemon-reload', erreur)

    def test_echec_sur_une_instance(self):
        self.systemd.echec = 'app-gnome-google\\x2dchrome-40001.scope'
        code, _, erreur = self.lancer('plafond', 'google-chrome', '3')
        self.assertEqual(code, 1)
        self.assertIn('Échec simulé', erreur)

    def test_dossier_non_inscriptible(self):
        (self.home / 'config').write_text('un fichier, pas un dossier')
        code, _, erreur = self.lancer('plafond', 'google-chrome', '3')
        self.assertEqual(code, 1)
        self.assertIn('impossible d\'écrire', erreur)

    def test_retirer_sans_plafond(self):
        self.assertEqual(self.lancer('plafond', 'google-chrome', 'aucun')[0], 0)


class TestLigneDeCommande(Base):
    def test_usage(self):
        for args in (('plafond',), ('plafond', 'x'), ('bidule',), ('plafond', 'a', 'b', 'c')):
            with self.subTest(args=args):
                self.assertEqual(self.lancer(*args)[0], 2)

    def test_extension_chargee_par_cli(self):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            self.assertEqual(main(['--help']), 0)
        self.assertIn('applications plafond ID GO|aucun', sortie.getvalue())


if __name__ == '__main__':
    unittest.main()
