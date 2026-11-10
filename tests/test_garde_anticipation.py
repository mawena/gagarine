"""Tests de l'anticipation, de la pression et des alertes ntfy du garde, sur des mesures simulées."""
import signal
import unittest

from gagarine import reglages as rg
from tests.commun import Banc, Mo

TOTAL = 16384   # Mo de RAM simulée


class BancAnticipation(Banc):
    def garde(self, pression=0.0, **reglages):
        g = super().garde(**reglages)
        self.pression = pression
        self.s.memoire_totale = lambda: TOTAL * Mo
        self.s.pression = lambda: self.pression
        self.alertes = []
        g.alerter = lambda *a: self.alertes.append(a)
        return g

    def grossir(self, n, pct, vitesse_mo=100, pid=20, nom='glouton', depart=1000):
        """n secondes pendant lesquelles `pid` prend `vitesse_mo` Mo par seconde, la RAM restant à pct %."""
        for i in range(n):
            etat = self.s.procs[pid].etat if pid in self.s.procs else 'S'   # un programme figé reste figé
            self.s.ajouter(pid, nom, depart + i * vitesse_mo, etat=etat)
            self.s.pct = pct
            self.g.tour()
            self.s.avancer()

    def fige(self):
        return [e for e in self.evenements() if e['action'] == 'fige']


class TestAnticipation(BancAnticipation):
    def test_declenchee_sous_le_seuil_avec_declencheur(self):
        self.garde()
        self.s.ajouter(10, 'tranquille', 500)
        self.grossir(14, 90)   # 5 % de 16 Go = 819 Mo à 100 Mo/s : 8 s < 15 s
        f = self.fige()
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]['declencheur'], 'anticipation')
        self.assertEqual(f[0]['pid'], 20)
        self.assertIn((20, signal.SIGSTOP), self.s.signaux)

    def test_pas_declenchee_si_croissance_trop_breve(self):
        self.garde()
        self.grossir(8, 90, vitesse_mo=200)   # moins de 10 s de mesures
        self.assertEqual(self.fige(), [])
        self.assertEqual(self.s.signaux, [])

    def test_pas_declenchee_sous_vigilance(self):
        self.garde()
        self.grossir(20, 80)
        self.assertEqual(self.fige(), [])

    def test_pas_declenchee_si_trop_lente(self):
        self.garde()
        self.grossir(20, 90, vitesse_mo=10)   # 819 Mo à 10 Mo/s : 80 s > 15 s (et 100 Mo < 64 Mo mini ? non : 100 Mo sur 10 s)
        self.assertEqual(self.fige(), [])

    def test_reglage_zero_la_desactive(self):
        self.garde(anticipation=0)
        self.grossir(20, 90)
        self.assertEqual(self.fige(), [])

    def test_programme_protege_jamais_touche(self):
        self.garde()
        self.grossir(20, 90, nom='gnome-shell')
        self.assertEqual(self.fige(), [])
        self.assertEqual(self.s.signaux, [])

    def test_rechargement_les_nouveaux_reglages_suivent(self):
        self.garde(anticipation=0)
        self.grossir(14, 90)
        self.assertEqual(self.fige(), [])
        self.g.appliquer_reglages(rg.Reglages(anticipation=15))
        self.grossir(1, 90, depart=2400)
        self.assertEqual(len(self.fige()), 1)
        self.g.appliquer_reglages(rg.Reglages(anticipation=0))
        self.assertEqual(self.g.r.anticipation, 0)

    def test_au_seuil_le_declencheur_est_seuil(self):
        self.garde()
        self.s.ajouter(10, 'gros', 3000)
        self.tours(1, pct=96)
        self.assertEqual(self.fige()[0]['declencheur'], 'seuil')

    def test_au_seuil_urgence_arrete_avec_declencheur(self):
        self.garde()
        self.s.ajouter(10, 'gros', 3000)
        self.tours(1, pct=99)
        self.assertEqual([e for e in self.evenements() if e['action'] == 'arrete'][0]['declencheur'], 'urgence')

    def test_pas_de_deuxieme_programme_fige_pendant_la_grace(self):
        self.garde()
        self.s.ajouter(21, 'autre', 500)
        self.grossir(14, 90)
        self.assertEqual(len(self.fige()), 1)
        self.s.ajouter(21, 'autre', 5000)
        self.tours(2, pct=90)
        self.assertEqual(len(self.fige()), 1)


class TestPression(BancAnticipation):
    def test_declenchee_au_dela_de_vigilance(self):
        self.garde(pression=25.0, anticipation=0)
        self.s.ajouter(10, 'gros', 3000)
        self.s.ajouter(11, 'petit', 100)
        self.tours(2, pct=90)
        f = self.fige()
        self.assertEqual([(e['pid'], e['declencheur']) for e in f], [(10, 'pression')])

    def test_pression_sous_le_reglage(self):
        self.garde(pression=10.0, anticipation=0)
        self.s.ajouter(10, 'gros', 3000)
        self.tours(3, pct=90)
        self.assertEqual(self.fige(), [])

    def test_pas_declenchee_sous_vigilance(self):
        self.garde(pression=50.0, anticipation=0)
        self.s.ajouter(10, 'gros', 3000)
        self.tours(3, pct=70)
        self.assertEqual(self.fige(), [])

    def test_reglage_zero_la_desactive(self):
        self.garde(pression=50.0, anticipation=0)
        self.g.appliquer_reglages(rg.Reglages(pression=0, anticipation=0))
        self.s.ajouter(10, 'gros', 3000)
        self.tours(3, pct=90)
        self.assertEqual(self.fige(), [])

    def test_programme_protege_jamais_touche(self):
        self.garde(pression=50.0, anticipation=0)
        self.s.ajouter(10, 'gnome-shell', 5000)
        self.tours(3, pct=90)
        self.assertEqual(self.s.signaux, [])

    def test_systeme_sans_pression_ne_casse_rien(self):
        self.garde(anticipation=0)
        del self.s.pression
        self.s.ajouter(10, 'gros', 3000)
        self.tours(3, pct=90)
        self.assertEqual(self.fige(), [])


class TestAlertesNtfy(BancAnticipation):
    def test_alerte_haute_pour_un_programme_fige_et_un_arrete(self):
        self.garde(ntfy='mon-sujet')
        self.s.ajouter(10, 'gros', 3000)
        self.tours(1, pct=96)
        self.assertEqual(len(self.alertes), 1)
        cible, titre, texte, priorite, _ = self.alertes[0]
        self.assertEqual((cible, priorite), ('mon-sujet', 'haute'))
        self.assertIn('gros', titre)
        self.s.avancer(30)
        self.tours(1, pct=96)   # toujours en RAM, grâce dépassée : arrêt
        self.s.procs[10] = self.s.procs[10]._replace(etat='T')
        self.s.pct = 99
        self.g.repos_jusqua = 0
        self.g.tour()
        self.assertEqual(len(self.alertes), 2)
        self.assertIn('arrêté', self.alertes[1][1])

    def test_rien_sans_ntfy(self):
        self.garde()
        self.s.ajouter(10, 'gros', 3000)
        self.tours(1, pct=96)
        self.assertEqual(self.alertes, [])

    def test_alerte_qui_echoue_ne_bloque_pas(self):
        self.garde(ntfy='sujet')
        def casse(*a):
            raise RuntimeError('réseau')
        self.g.alerter = casse
        self.s.ajouter(10, 'gros', 3000)
        self.tours(1, pct=96)
        self.assertEqual(len(self.fige()), 1)

    def test_le_vrai_alerteur_ne_part_pas_sans_ntfy(self):
        self.garde()
        self.g.alerter = __import__('gagarine.ntfy', fromlist=['x']).envoyer_en_fond
        self.s.ajouter(10, 'gros', 3000)
        self.tours(1, pct=96)
        self.assertEqual(len(self.fige()), 1)


if __name__ == '__main__':
    unittest.main()
