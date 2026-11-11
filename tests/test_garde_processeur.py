"""Le garde publie la mesure du processeur toutes les 2 s sans jamais gêner la protection de la RAM."""
import io
import json
import stat
from contextlib import redirect_stderr

from tests.commun import Banc


class FauxMesureur:
    def __init__(self, panne=None):
        self.appels = 0
        self.panne = panne

    def mesurer(self):
        self.appels += 1
        if self.panne:
            raise self.panne
        return {'t': 1.0, 'pourcent': float(self.appels)}

    def prendre_minute(self):
        return {}


class TestGardeProcesseur(Banc):
    def avec(self, mesureur, ecrire=True):
        self.garde()
        self.m = mesureur
        self.g.mesureur = mesureur
        self.g.dossier_cpu = str(self.dossier / 'run') if ecrire else None
        return self.g

    def fichier(self):
        return self.dossier / 'run' / 'processeur.json'

    def test_cadence_de_2_s(self):
        self.avec(FauxMesureur())
        self.tours(10, pas=1.0, pct=50)   # 10 s de service : mesures à 0, 2, 4, 6 et 8 s
        self.assertEqual(self.m.appels, 5)

    def test_ecrit_processeur_json_en_0644(self):
        self.avec(FauxMesureur())
        self.tours(1, pct=50)
        self.assertEqual(json.loads(self.fichier().read_text())['pourcent'], 1.0)
        self.assertEqual(stat.S_IMODE(self.fichier().stat().st_mode), 0o644)

    def test_sans_dossier_mesure_sans_ecrire(self):
        self.avec(FauxMesureur(), ecrire=False)
        self.tours(1, pct=50)
        self.assertEqual(self.m.appels, 1)
        self.assertFalse(self.fichier().exists())

    def test_sans_mesureur_rien_ne_change(self):
        self.garde()
        self.tours(3, pct=50)
        self.assertFalse(self.fichier().exists())

    def test_erreur_avalee_et_signalee_une_seule_fois(self):
        self.avec(FauxMesureur(RuntimeError('boum')))
        err = io.StringIO()
        with redirect_stderr(err):
            self.tours(10, pct=50)
        self.assertEqual(err.getvalue().count('erreur (processeur)'), 1)
        self.assertGreater(self.m.appels, 1)   # il continue d'essayer

    def test_erreur_d_ecriture_avalee(self):
        self.avec(FauxMesureur())
        self.g.dossier_cpu = '/proc/inexistant/interdit'
        with redirect_stderr(io.StringIO()):
            self.tours(3, pct=50)

    def test_la_ram_reste_protegee_quand_le_processeur_echoue(self):
        self.avec(FauxMesureur(RuntimeError('boum')))
        self.s.ajouter(10, 'gros', 3000)
        with redirect_stderr(io.StringIO()):
            self.tours(3, pct=96)
        self.assertIn('fige', self.actions())

    def test_mesure_aussi_apres_une_sortie_anticipee_du_tour(self):
        self.avec(FauxMesureur())
        self.g.repos_jusqua = self.s.t + 100   # le tour de RAM sort tôt
        self.tours(1, pct=50)
        self.assertEqual(self.m.appels, 1)
