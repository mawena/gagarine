"""Tests de la détection des fuites de mémoire (garde, fuites.json, etat --json, journal) sur des mesures simulées."""
import json
import unittest
from unittest import mock

from gagarine import cli, journal as jn, reglages as rg
from tests.commun import Banc, Mo

TOTAL = 16384   # Mo de RAM simulée


class BancFuite(Banc):
    def garde(self, **reglages):
        g = super().garde(**reglages)
        self.s.memoire_totale = lambda: TOTAL * Mo
        self.alertes = []
        g.alerter = lambda *a: self.alertes.append(a)
        return g

    def croitre(self, secondes, mo_min=200, depart=600, pid=20, nom='glouton', f=None, pct=50.0, etat='S'):
        """`pid` grossit de mo_min Mo/min pendant `secondes` secondes (f(i) : mémoire en Mo à la seconde i, sinon linéaire)."""
        for i in range(secondes):
            self.s.ajouter(pid, nom, f(i) if f else depart + mo_min * i / 60, etat=etat)
            self.s.pct = pct
            self.g.tour()
            self.s.avancer()

    def fuites(self):
        return [e for e in self.evenements() if e['action'] == 'fuite']

    def fichier(self):
        return json.loads((self.dossier / 'fuites.json').read_text())


class TestDetection(BancFuite):
    def test_fuite_reperee(self):
        self.garde()
        self.croitre(310)
        f = self.fuites()
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]['pid'], f[0]['nom']), (20, 'glouton'))
        self.assertAlmostEqual(f[0]['pente_mo_min'], 200, delta=3)
        self.assertEqual(f[0]['utilisateur'], 'u1000')
        self.assertEqual(f[0]['commande'], '/usr/bin/prog20')
        self.assertGreater(f[0]['memoire'], 1000 * Mo)
        self.assertEqual(list(self.g.fuites), [20])

    def test_aucune_action_sur_le_programme(self):
        self.garde()
        self.croitre(310)
        self.assertEqual(self.s.signaux, [])
        self.assertEqual(self.g.figes, {})

    def test_prevision(self):
        self.garde()   # RAM à 50 % : il manque (seuil − 50) % de 16384 Mo, à 200 Mo/min
        self.croitre(310, pct=50.0)
        manque = (self.r.seuil - 50) / 100 * TOTAL
        self.assertAlmostEqual(self.fuites()[0]['prevision_s'], manque / 200 * 60, delta=60)

    def test_prevision_cas_limites(self):
        self.garde(seuil=90)
        self.assertEqual(self.g.prevision(Mo, 95, TOTAL * Mo), 0)              # déjà au seuil
        self.assertIsNone(self.g.prevision(0.001 * Mo, 50, TOTAL * Mo))        # au-delà de 24 h
        self.assertIsNone(self.g.prevision(Mo, 50, 0))                         # RAM totale inconnue

    def test_pas_de_fuite_si_pente_faible(self):
        self.garde()
        self.croitre(400, mo_min=50)
        self.assertEqual(self.fuites(), [])
        self.assertEqual(self.g.fuites, {})

    def test_pas_de_fuite_si_croissance_irreguliere(self):
        self.garde()
        # des paliers : pente moyenne élevée, mais mémoire montée sur très peu d'intervalles
        self.croitre(400, f=lambda i: 600 + (i // 100) * 400)
        self.assertEqual(self.fuites(), [])

    def test_pas_de_fuite_si_programme_protege(self):
        self.garde()
        self.croitre(400, nom='gnome-shell')
        self.assertEqual(self.fuites(), [])

    def test_pas_de_fuite_sous_500_mo(self):
        self.garde()
        self.croitre(400, f=lambda i: 10 + 400 * min(i, 300) / 300)   # 80 Mo/min... puis plat, jamais 500 Mo
        self.assertEqual(self.fuites(), [])
        self.croitre(400, f=lambda i: 10 + 480 * i / 400, pid=21)     # 72 Mo/min sous le seuil et sous 500 Mo
        self.assertEqual(self.fuites(), [])

    def test_pas_de_fuite_en_moins_de_5_min(self):
        self.garde()
        self.croitre(280, mo_min=300)
        self.assertEqual(self.fuites(), [])
        self.croitre(40, mo_min=300, depart=600 + 280 * 5)
        self.assertEqual(len(self.fuites()), 1)   # la durée est atteinte : la fuite apparaît

    def test_programme_fige_ignore(self):
        self.garde()
        self.g.figes[20] = {'debut': 'd20', 'nom': 'glouton', 'commande': 'x', 'utilisateur': 'u', 'depuis': 0,
                            'memoire': 1}
        self.croitre(310, etat='T')
        self.assertEqual(self.fuites(), [])

    def test_filtre_respecte(self):
        self.garde(filtre='cible')
        self.s.commandes[20] = 'python3 autre'
        self.croitre(310)
        self.assertEqual(self.fuites(), [])

    def test_fuite_seuil_zero_desactive(self):
        self.garde(fuite_seuil=0)
        self.croitre(400)
        self.assertEqual(self.fuites(), [])
        self.assertEqual(self.g.mesures_fuite, {})

    def test_la_fuite_s_arrete_quand_le_programme_se_stabilise(self):
        self.garde()
        self.croitre(310)
        self.assertEqual(list(self.g.fuites), [20])
        self.croitre(400, mo_min=0, depart=1500)
        self.assertEqual(self.g.fuites, {})
        self.assertEqual(self.fichier(), [])

    def test_la_fuite_disparait_avec_le_programme(self):
        self.garde()
        self.croitre(310)
        del self.s.procs[20]
        self.g.tour()
        self.assertEqual(self.g.mesures_fuite, {})
        self.s.avancer(11)
        self.g.tour()
        self.assertEqual(self.g.fuites, {})


class TestNotification(BancFuite):
    def notifs_fuite(self):
        return [n for n in self.s.notifs if 'uite' in n[0]]

    def test_une_notification_sans_boutons_et_ntfy(self):
        self.garde(ntfy='sujet')
        self.croitre(310)
        n = self.notifs_fuite()
        self.assertEqual(len(n), 1)
        self.assertIn('glouton', n[0][0])
        self.assertIsNone(n[0][1])   # pas de pid : pas de boutons
        self.assertEqual(len(self.alertes), 1)
        self.assertEqual(self.alertes[0][0], 'sujet')
        self.assertIn('grossit de', self.alertes[0][2])

    def test_une_seule_fois_par_30_min(self):
        self.garde()
        self.croitre(310)
        self.croitre(1400, depart=1500, mo_min=200)   # jusqu'à ~28 min après le premier signalement
        self.assertEqual(len(self.notifs_fuite()), 1)
        self.assertEqual(len(self.fuites()), 1)
        self.croitre(500, depart=1500 + 200 * 24, mo_min=200)   # plus de 30 min : de nouveau signalée
        self.assertEqual(len(self.notifs_fuite()), 2)
        self.assertEqual(len(self.fuites()), 2)

    def test_sans_notification_si_fuite_notifier_non(self):
        self.garde(fuite_notifier=False, ntfy='sujet')
        self.croitre(310)
        self.assertEqual(self.notifs_fuite(), [])
        self.assertEqual(self.alertes, [])
        self.assertEqual(len(self.fuites()), 1)   # l'événement reste écrit

    def test_sighup_les_reglages_suivent(self):
        self.garde()
        self.croitre(100)
        self.g.appliquer_reglages(rg.Reglages(fuite_seuil=0))
        self.croitre(400, depart=800)
        self.assertEqual(self.fuites(), [])
        self.assertEqual(self.g.mesures_fuite, {})
        self.g.appliquer_reglages(rg.Reglages(fuite_seuil=100))
        self.croitre(310, depart=2000)
        self.assertEqual(len(self.fuites()), 1)

    def test_sighup_efface_les_fuites_en_cours(self):
        self.garde()
        self.croitre(310)
        self.assertEqual(len(self.fichier()), 1)
        self.g.appliquer_reglages(rg.Reglages(fuite_seuil=0))
        self.assertEqual(self.fichier(), [])


class TestFichierEtJson(BancFuite):
    def test_fuites_json(self):
        self.garde()
        self.croitre(310)
        f = self.fichier()
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]['pid'], f[0]['nom'], f[0]['debut']), (20, 'glouton', 'd20'))
        self.assertAlmostEqual(f[0]['pente_mo_min'], 200, delta=3)
        self.assertIn('depuis', f[0])
        self.assertEqual((self.dossier / 'fuites.json').stat().st_mode & 0o777, 0o644)
        self.assertFalse((self.dossier / 'fuites.tmp').exists())

    def donnees(self, fuites=None):
        return cli.donnees_etat(rg.Reglages(), '/x', self.dossier, lancer=lambda c: 'active',
                                meminfo='MemTotal: 1000 kB\nMemAvailable: 500 kB\n',
                                swaps='Filename Type Size Used Priority\n', procs={}, figes={}, travaux=0,
                                fuites=fuites)

    def test_etat_json(self):
        self.garde()
        self.croitre(310)
        with mock.patch.object(cli, 'demarrage', lambda p: 'd%d' % p):
            d = self.donnees()
        self.assertEqual(len(d['fuites']), 1)
        f = d['fuites'][0]
        self.assertEqual((f['pid'], f['nom']), (20, 'glouton'))
        for cle in ('pente_mo_min', 'depuis', 'memoire_mo', 'prevision_s'):
            self.assertIn(cle, f)
        json.dumps(d)

    def test_etat_json_sans_fichier_ou_programme_disparu(self):
        self.assertEqual(self.donnees()['fuites'], [])
        self.garde()
        self.croitre(310)
        with mock.patch.object(cli, 'demarrage', lambda p: None):
            self.assertEqual(self.donnees()['fuites'], [])

    def test_fichier_ancien_vide_au_demarrage(self):
        self.dossier.joinpath('fuites.json').write_text('[{"pid": 1, "debut": "x"}]')
        self.garde()
        self.assertEqual(self.fichier(), [])


class TestJournalLisible(unittest.TestCase):
    def ligne(self, **e):
        return jn.ligne_lisible({'date': '2026-11-10T10:00:00+0000', 'source': 'gagarine', **e})

    def test_fuite(self):
        l = self.ligne(action='fuite', pid=7, nom='python3', pente_mo_min=210, prevision_s=720)
        self.assertIn('python3', l)
        self.assertIn('(210 Mo/min, RAM pleine dans ~12 min)', l)

    def test_fuite_unites_de_prevision(self):
        self.assertIn('~45 s', self.ligne(action='fuite', pente_mo_min=1, prevision_s=45))
        self.assertIn('~3 h', self.ligne(action='fuite', pente_mo_min=1, prevision_s=3 * 3600))

    def test_fuite_sans_prevision(self):
        l = self.ligne(action='fuite', pid=7, nom='python3', pente_mo_min=210)
        self.assertTrue(l.endswith('(210 Mo/min)'))

    def test_fuite_travail(self):
        l = self.ligne(action='fuite-travail', nom='tests voix', tendance='hausse', pics_go=[2.1, 3.4, 5.0])
        self.assertIn('tests voix', l)
        self.assertIn('pic en hausse à chaque lancement (2,1 → 3,4 → 5,0 Go)', l)
        l = self.ligne(action='fuite-travail', nom='tests voix', tendance='limite', pics_go=[4.0, 4.1])
        self.assertIn('arrêté par sa limite', l)

    def test_fuite_travail_sans_pics(self):
        l = self.ligne(action='fuite-travail', nom='x', tendance='hausse')
        self.assertTrue(l.endswith('(pic en hausse à chaque lancement)') or l.endswith('pic en hausse à chaque lancement'))


if __name__ == '__main__':
    unittest.main()
