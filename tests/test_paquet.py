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

    def test_fichiers_de_la_120_installes(self):
        """Hook Claude, ménage (unités utilisateur), dépôt APT : installés au bon endroit, et rien d'autre de data/apt."""
        installes = {os.path.normpath(s): d for s, d, *_ in lignes('gagarine.install')}
        attendus = {
            'data/claude/ressources-poste.sh': 'usr/share/gagarine/claude',
            'data/systemd-user/gagarine-menage.service': 'usr/lib/systemd/user',
            'data/systemd-user/gagarine-menage.timer': 'usr/lib/systemd/user',
            'data/apt/gagarine.sources': 'etc/apt/sources.list.d',
            'data/apt/gagarine.gpg': 'usr/share/keyrings',
        }
        for source, destination in attendus.items():
            self.assertEqual(installes.get(source), destination, source)
        self.assertNotIn('data/apt/LISEZMOI', installes)

    def test_depot_apt_coherent(self):
        """La source APT désigne la clé que le paquet installe."""
        with open(os.path.join(RACINE, 'data/apt/gagarine.sources'), encoding='utf-8') as f:
            texte = f.read()
        self.assertIn('Signed-By: /usr/share/keyrings/gagarine.gpg', texte)
        self.assertIn('https://mawena.github.io/gagarine/apt', texte)

    def test_minuteur_du_menage_active_pour_tous(self):
        """Le minuteur a une section [Install] (dh_installsystemduser l'active pour tous les utilisateurs)."""
        with open(os.path.join(RACINE, 'data/systemd-user/gagarine-menage.timer'), encoding='utf-8') as f:
            self.assertIn('WantedBy=timers.target', f.read())
        with open(os.path.join(RACINE, 'data/systemd-user/gagarine-menage.service'), encoding='utf-8') as f:
            self.assertIn('ExecStart=/usr/bin/gagarine menage auto', f.read())
        with open(os.path.join(RACINE, 'debian/rules'), encoding='utf-8') as f:
            self.assertNotIn('no-enable', f.read())

    def test_manuel_documente_les_commandes_de_la_120(self):
        with open(os.path.join(RACINE, 'man/gagarine.8'), encoding='utf-8') as f:
            texte = f.read()
        for mot in ('gagarine file appris', 'gagarine historique', 'gagarine menage', 'gagarine applications',
                    'gagarine hook\\-claude', 'gagarine ntfy essai',
                    'anticipation', 'pression', 'notifications_fin', 'notification_fin_min', 'file_ajuster',
                    'menage_auto', 'menage_delai', 'claude_bloquer', 'travaux.json', 'gagarine.sources'):
            self.assertTrue(mot in texte, f'{mot} absent de man/gagarine.8')

    def test_manuel_documente_la_130(self):
        with open(os.path.join(RACINE, 'man/gagarine.8'), encoding='utf-8') as f:
            texte = f.read()
        for mot in ('gagarine file priorite', 'gagarine ligne\\-etat', '\\-\\-priorite', 'paralleles', 'en_cours', 'motif',
                    'besoin_go', 'tendance', 'fuites', 'prevision_s', 'fuite\\-travail', 'fuite_seuil',
                    'fuite_notifier', 'file_paralleles', 'fuites.json', 'FUITES DE MÉMOIRE'):
            self.assertIn(mot, texte, f'{mot} absent de man/gagarine.8')

    def test_ligne_d_etat_installee_et_sans_purge_a_prevoir(self):
        """gagarine/*.py (donc ligne.py) est installé ; le paquet ne nettoie pas le dossier d'état à la purge
        (StateDirectory de systemd), donc rien à ajouter pour fuites.json."""
        sources = [s for s, *_ in lignes('gagarine.install')]
        self.assertIn('gagarine/*.py', sources)
        self.assertTrue(os.path.exists(os.path.join(RACINE, 'gagarine', 'ligne.py')))
        for nom in ('postrm', 'gagarine.postrm', 'prerm', 'gagarine.prerm'):
            self.assertFalse(os.path.exists(os.path.join(RACINE, 'debian', nom)),
                             f'debian/{nom} : si le paquet nettoie /var/lib/gagarine, il doit aussi retirer fuites.json')

    def test_description_a_jour(self):
        with open(os.path.join(RACINE, 'debian/control'), encoding='utf-8') as f:
            texte = f.read()
        for mot in ('hook-claude', 'historique', 'menage', 'APT', 'ligne-etat', 'leak', 'processeur', 'processor'):
            self.assertTrue(mot in texte, f'{mot} absent de la description')

    def test_dossier_execution_du_service(self):
        """1.4.0 : le service publie processeur.json dans /run/gagarine, lisible par tous (ligne d'état, interface)."""
        with open(os.path.join(RACINE, 'systemd/gagarine.service'), encoding='utf-8') as f:
            unite = f.read().splitlines()
        self.assertIn('RuntimeDirectory=gagarine', unite)
        self.assertIn('RuntimeDirectoryMode=0755', unite)
        self.assertTrue(os.path.exists(os.path.join(RACINE, 'gagarine', 'processeur.py')))

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
