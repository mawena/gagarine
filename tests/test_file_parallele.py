"""Tests de la file à plusieurs travaux (conception 1.3.0, section 1) : règle de tour, réserve mémoire, cœurs récents,
priorités, famine, anciens outils, motifs, `file priorite`. Le poste est simulé."""
import json
import unittest

from gagarine import cli
from gagarine import file as fa
from gagarine.reglages import Reglages
from tests.test_file import BancFile


class BancParallele(BancFile):
    def setUp(self):
        super().setUp()
        self.base = int(self.poste.t * 1000)
        self.poste.t += 100      # les tickets posés par les tests sont plus anciens que celui du travail qui se lance
        self.poste.ram = 16.0
        self.poste.nb_coeurs = 8

    def gagarine(self, rang, pid, nom, etat='en attente', ram=1, coeurs=1, priorite='normal', age=0, debut=None,
                 unite=None, **champs):
        """Ticket d'un travail de cette version ; `rang` ordonne les tickets (plus petit = plus ancien)."""
        champs.update(source='gagarine', priorite=priorite, etat=etat, ram=ram, coeurs=coeurs,
                      depuis=fa.iso_ms(self.poste.t - age), unite=unite, nom_travail=nom)
        if debut is not None:
            champs['debut'] = fa.iso_ms(self.poste.t - debut)
        nom_ticket = f'{self.base + rang}-{pid}.json'
        self.ticket(nom_ticket, pid, **champs)
        return nom_ticket

    def evaluer(self, paralleles=2):
        return fa.evaluer(self.tickets, self.poste, paralleles)

    def motif(self, ticket, paralleles=2):
        return self.evaluer(paralleles)['motifs'][ticket]

    def tour_libre(self, ticket, paralleles=2):
        return self.motif(ticket, paralleles) == (None, None)


class TestLimiteDeTravaux(BancParallele):
    def test_un_en_cours_sur_deux(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        b = self.gagarine(2, 20, 'b')
        self.assertTrue(self.tour_libre(b))

    def test_deux_en_cours_sur_deux(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.gagarine(2, 20, 'b', etat='en cours')
        c = self.gagarine(3, 30, 'c')
        self.assertEqual(self.motif(c), ('cours', '2 travaux en cours'))

    def test_un_seul_en_cours_au_singulier(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        c = self.gagarine(3, 30, 'c')
        self.assertEqual(self.motif(c, paralleles=1), ('cours', '1 travail en cours'))

    def test_trois_paralleles(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.gagarine(2, 20, 'b', etat='en cours')
        c = self.gagarine(3, 30, 'c')
        self.assertTrue(self.tour_libre(c, paralleles=3))

    def test_compte_les_en_cours(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.gagarine(2, 20, 'b')
        self.assertEqual(self.evaluer()['cours'], 1)


class TestReserveMemoire(BancParallele):
    def test_reserve_de_ce_que_le_travail_en_cours_peut_encore_prendre(self):
        """Un travail annoncé à 4 Go qui en a pris 1 peut encore en prendre 3 : réserve 1 + 3 = 4 Go."""
        self.gagarine(1, 10, 'a', etat='en cours', ram=4, unite='u-a.scope')
        self.poste.memoires['u-a.scope'] = 1.0
        self.poste.ram = 9.0
        ok = self.gagarine(2, 20, 'ok', ram=5)
        self.assertTrue(self.tour_libre(ok))
        self.poste.ram = 8.9
        self.assertEqual(self.motif(ok), ('ram', 'mémoire : 4,9 Go libres, il en faut 5'))

    def test_travail_qui_a_pris_tout_son_du_ne_reserve_rien(self):
        self.gagarine(1, 10, 'a', etat='en cours', ram=4, unite='u-a.scope')
        self.poste.memoires['u-a.scope'] = 6.0   # plus que l'annonce : rien à réserver
        self.poste.ram = 5.0
        self.assertTrue(self.tour_libre(self.gagarine(2, 20, 'b', ram=4)))   # 5 - 1 de marge

    def test_sans_cgroup_toute_l_annonce_est_reservee(self):
        self.gagarine(1, 10, 'a', etat='en cours', ram=4, unite=None)
        self.poste.ram = 8.0
        b = self.gagarine(2, 20, 'b', ram=4)
        self.assertEqual(self.motif(b)[0], 'ram')   # 8 - (1 + 4) = 3
        self.poste.ram = 9.0
        self.assertTrue(self.tour_libre(b))         # 9 - 5 = 4

    def test_lecture_du_cgroup_en_erreur_compte_comme_inconnue(self):
        self.gagarine(1, 10, 'a', etat='en cours', ram=4, unite='u-a.scope')

        def casse(chemin):
            raise OSError('illisible')
        self.poste.memoire_cgroup = casse
        self.poste.memoires['u-a.scope'] = 0
        self.poste.ram = 8.0
        self.assertEqual(self.motif(self.gagarine(2, 20, 'b', ram=4))[0], 'ram')

    def test_ancien_outil_en_cours_reserve_deux_go(self):
        self.ticket(f'{self.base}-10.json', 10, nom_travail='node')   # le plus ancien : il a la main
        self.poste.ram = 5.0
        self.assertTrue(self.tour_libre(self.gagarine(2, 20, 'b', ram=2)))        # 5 - (1 + 2) = 2
        self.poste.ram = 4.9
        self.assertEqual(self.motif(self.gagarine(3, 30, 'c', ram=2))[0], 'ram')

    def test_marge_d_un_go_seulement_avec_un_travail_en_cours(self):
        """Seul, un travail peut prendre toute la mémoire disponible (comme avant) ; à côté d'un autre, 1 Go de marge."""
        self.poste.ram = 4.0
        a = self.gagarine(1, 10, 'a', ram=4)
        self.assertTrue(self.tour_libre(a))
        self.gagarine(0, 5, 'en cours', etat='en cours', ram=1, unite='u.scope')
        self.poste.memoires['u.scope'] = 1.0    # il a pris tout son dû : réserve = marge seule
        self.assertEqual(self.motif(a)[0], 'ram')   # 4 - 1 = 3 < 4
        self.poste.ram = 5.0
        self.assertTrue(self.tour_libre(a))


class TestCoeurs(BancParallele):
    def test_charge(self):
        self.poste.charge_1min = 6.5
        a = self.gagarine(1, 10, 'a', coeurs=2)
        self.assertEqual(self.motif(a), ('cpu', 'processeurs occupés (charge 6.5 pour 8 cœurs), il lui en faut 2 libres'))

    def test_cœurs_d_un_travail_lance_depuis_moins_de_60_s(self):
        self.gagarine(1, 10, 'a', etat='en cours', coeurs=3, debut=30)
        b = self.gagarine(2, 20, 'b', coeurs=6)
        self.assertEqual(self.motif(b)[0], 'cpu')      # 8 - 0 - 3 = 5 < 6
        c = self.gagarine(3, 30, 'c', coeurs=5)
        self.assertTrue(self.tour_libre(c))     # 5 libres : il passe devant b, qui ne tient pas

    def test_cœurs_d_un_travail_plus_ancien_que_60_s_deja_dans_la_charge(self):
        self.gagarine(1, 10, 'a', etat='en cours', coeurs=3, debut=61)
        self.assertTrue(self.tour_libre(self.gagarine(2, 20, 'b', coeurs=8)))

    def test_cœurs_recents_comptent_a_la_limite(self):
        self.gagarine(1, 10, 'a', etat='en cours', coeurs=3, debut=59)
        b = self.gagarine(2, 20, 'b', coeurs=6)
        self.assertEqual(self.motif(b)[0], 'cpu')
        self.poste.t += 2    # il a maintenant 61 s
        self.assertTrue(self.tour_libre(b))


class TestOrdre(BancParallele):
    def test_urgent_passe_devant_normal_plus_ancien(self):
        a = self.gagarine(1, 10, 'a')
        b = self.gagarine(2, 20, 'b', priorite='urgent')
        self.assertTrue(self.tour_libre(b))
        self.assertEqual(self.motif(a), ('rang:' + b, 'derrière « b » (urgent)'))

    def test_fond_derriere_normal_plus_recent(self):
        a = self.gagarine(1, 10, 'a', priorite='fond')
        b = self.gagarine(2, 20, 'b')
        self.assertTrue(self.tour_libre(b))
        self.assertEqual(self.motif(a)[1], 'derrière « b » (normal)')

    def test_meme_priorite_par_anciennete(self):
        a = self.gagarine(1, 10, 'a')
        b = self.gagarine(2, 20, 'b')
        self.assertTrue(self.tour_libre(a))
        self.assertEqual(self.motif(b)[1], 'derrière « a » (normal)')

    def test_passage_devant_si_le_plus_haut_ne_tient_pas(self):
        a = self.gagarine(1, 10, 'gros', ram=40)
        b = self.gagarine(2, 20, 'petit', ram=1)
        self.assertEqual(self.motif(a)[0], 'ram')
        self.assertTrue(self.tour_libre(b))

    def test_pas_de_passage_devant_un_travail_qui_tient(self):
        self.gagarine(1, 10, 'a')
        b = self.gagarine(2, 20, 'b')
        self.assertEqual(self.motif(b)[0], 'rang:' + f'{self.base + 1}-10.json')

    def test_famine_apres_dix_minutes(self):
        a = self.gagarine(1, 10, 'gros', ram=40, age=601)
        b = self.gagarine(2, 20, 'petit', ram=1)
        self.assertEqual(self.motif(b), ('rang:' + a, 'derrière « gros » (normal)'))

    def test_pas_de_famine_avant_dix_minutes(self):
        self.gagarine(1, 10, 'gros', ram=40, age=599)
        self.assertTrue(self.tour_libre(self.gagarine(2, 20, 'petit', ram=1)))

    def test_famine_aussi_pour_un_travail_de_priorite_plus_haute(self):
        a = self.gagarine(1, 10, 'gros', ram=40, age=700, priorite='urgent')
        b = self.gagarine(2, 20, 'petit', priorite='fond')
        self.assertEqual(self.motif(b)[0], 'rang:' + a)

    def test_la_famine_ne_retient_pas_un_travail_plus_prioritaire(self):
        """Un travail de fond qui attend depuis 10 min ne fait pas passer devant un urgent (priorité = rang)."""
        self.gagarine(1, 10, 'fond', ram=1, age=900, priorite='fond')
        u = self.gagarine(2, 20, 'urgent', priorite='urgent')
        self.assertTrue(self.tour_libre(u))

    def test_priorite_inconnue_ou_absente_vaut_normal(self):
        a = self.gagarine(1, 10, 'a', priorite='bizarre')
        b = self.gagarine(2, 20, 'b', priorite='fond')
        self.assertTrue(self.tour_libre(a))
        self.assertEqual(self.motif(b)[0], 'rang:' + a)

    def test_un_travail_en_cours_n_est_pas_un_rang(self):
        self.gagarine(1, 10, 'a', etat='en cours', priorite='fond')
        self.assertTrue(self.tour_libre(self.gagarine(2, 20, 'b', priorite='urgent')))


class TestAnciensOutils(BancParallele):
    def test_ticket_node_en_attente_n_est_jamais_double(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        node = f'{self.base + 2}-20.json'
        self.ticket(node, 20, nom_travail='node')                        # derrière a : il attend
        b = self.gagarine(3, 30, 'b', priorite='urgent', ram=1)
        self.assertEqual(self.motif(b), ('ancien', 'derrière « node » (ancien outil)'))

    def test_plus_ancien_que_le_ticket_node_ne_le_double_pas_mais_il_passe_avant(self):
        """Un ticket plus ancien que celui de l'ancien outil n'est pas retenu par lui."""
        a = self.gagarine(1, 10, 'a')
        self.ticket(f'{self.base + 2}-20.json', 20, nom_travail='node')
        self.assertTrue(self.tour_libre(a))

    def test_ticket_node_en_cours_compte_comme_travail_en_cours(self):
        self.ticket(f'{self.base}-10.json', 10, nom_travail='node')   # le plus ancien : il a la main
        self.gagarine(2, 20, 'a', etat='en cours')
        self.assertEqual(self.evaluer()['cours'], 2)
        c = self.gagarine(3, 30, 'c')
        self.assertEqual(self.motif(c), ('cours', '2 travaux en cours'))

    def test_ticket_node_en_cours_laisse_la_place_a_un_autre_avec_deux_paralleles(self):
        self.ticket(f'{self.base}-10.json', 10, nom_travail='node')
        self.assertTrue(self.tour_libre(self.gagarine(2, 20, 'a')))

    def test_ticket_node_en_cours_bloque_avec_un_seul_travail(self):
        self.ticket(f'{self.base}-10.json', 10, nom_travail='node')
        self.assertEqual(self.motif(self.gagarine(2, 20, 'a'), paralleles=1), ('cours', '1 travail en cours'))

    def test_ticket_node_ne_se_double_pas_a_cause_d_un_urgent_en_famine(self):
        """Pas d'interblocage : un urgent coincé derrière un ancien outil ne retient pas un travail plus ancien que lui."""
        a = self.gagarine(1, 10, 'a')
        self.ticket(f'{self.base + 2}-20.json', 20, nom_travail='node')
        self.gagarine(3, 30, 'u', priorite='urgent', age=2000)
        self.assertTrue(self.tour_libre(a))

    def test_ticket_gagarine_1_2_en_attente_est_strict(self):
        """Un ticket de Gagarine sans priorité (1.2) attend comme un ancien outil : on ne le double pas."""
        self.gagarine(1, 10, 'a', etat='en cours')
        ancien = f'{self.base + 2}-20.json'
        self.ticket(ancien, 20, nom_travail='v12', source='gagarine', etat='en attente', ram=1, coeurs=1)
        self.assertEqual(self.motif(self.gagarine(3, 30, 'b'))[0], 'ancien')

    def test_ticket_illisible_compte_comme_ancien_outil(self):
        (self.tickets).mkdir(parents=True, exist_ok=True)
        (self.tickets / f'{self.base}-10.json').write_text('{')
        self.poste.vivants[10] = 'x'
        self.assertEqual(self.evaluer()['infos'][0]['statut'], 'cours')

    def test_etat_texte_et_json_du_ticket_node_en_attente(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.ticket(f'{self.base + 2}-20.json', 20, nom_travail='node')
        e = fa.donnees_etat_file(self.env, self.poste, Reglages(file_paralleles=2))
        self.assertEqual(e['travaux'][1]['motif'], '1 travail(aux) avant lui, dont « a »')
        self.assertEqual((e['travaux'][1]['priorite'], e['travaux'][1]['besoin_go']), (None, None))


class TestParalleleUn(BancParallele):
    """Avec file_paralleles = 1, la règle redevient exactement celle d'avant (un seul travail, par ancienneté)."""

    def raison(self, ticket, ram=1, coeurs=1):
        return fa.raison_d_attendre(ticket, 'essai', ram, coeurs, None, self.tickets, self.poste, 1, 'urgent')

    def test_la_priorite_est_ignoree(self):
        self.gagarine(1, 10, 'a', priorite='fond')
        self.assertEqual(self.raison(f'{self.base + 2}-500.json'), ('avant', '1 travail(aux) avant lui, dont « a »'))

    def test_pas_de_reserve_ni_de_marge(self):
        self.poste.ram = 1.0
        self.assertEqual(self.raison('5-500.json', ram=1), (None, None))

    def test_meme_texte_de_ram(self):
        self.poste.ram = 0.54
        self.assertEqual(self.raison('5-500.json', ram=4), ('ram', 'mémoire disponible 0.5 Go, il lui en faut 4'))

    def test_travail_en_cours_plus_ancien_retient(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.assertEqual(self.raison(f'{self.base + 2}-500.json')[0], 'avant')

    def test_etat_motifs_comme_avant(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.gagarine(2, 20, 'b')
        e = fa.donnees_etat_file(self.env, self.poste, Reglages(file_paralleles=1))
        self.assertEqual([t['motif'] for t in e['travaux']], [None, '1 travail(aux) avant lui, dont « a »'])
        self.assertEqual((e['paralleles'], e['en_cours']), (1, 1))

    def test_lancement_complet_un_a_la_fois(self):
        self.gagarine(1, 10, 'a', etat='en cours')

        def fin():
            del self.poste.vivants[10]
        self.poste.etapes = [fin]
        self.assertEqual(self.lancer_file(paralleles=1), 0)
        self.assertEqual(self.poste.pauses, 1)
        self.assertIn('1 travail(aux) avant lui', self.sortie)


class TestLancement(BancParallele):
    def test_part_a_cote_d_un_travail_en_cours(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.assertEqual(self.lancer_file(paralleles=2), 0)
        self.assertEqual(self.poste.pauses, 0)
        self.assertEqual(self.sortie, '')

    def test_attend_puis_part_avec_messages(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.gagarine(2, 20, 'b', etat='en cours')

        def fin():
            del self.poste.vivants[20]
        self.poste.etapes = [lambda: None, fin]
        self.assertEqual(self.lancer_file(paralleles=2), 0)
        lignes = self.sortie.splitlines()
        self.assertEqual(lignes[0], "  en file d'attente (essai) : 2 travaux en cours")
        self.assertTrue(lignes[1].startswith("  file d'attente (essai) : son tour est venu"))
        self.assertEqual(len(lignes), 2)

    def test_message_de_rang(self):
        self.gagarine(1, 10, 'a', priorite='urgent', age=5)

        def fin():
            del self.poste.vivants[10]
        self.poste.etapes = [fin]
        self.lancer_file(paralleles=2)
        self.assertEqual(self.sortie.splitlines()[0], "  en file d'attente (essai) : derrière « a » (urgent)")

    def test_ticket_porte_priorite_et_debut(self):
        vu = {}
        lancer = self.poste.lancer

        def espion(argv, env, pendant=None):
            vu.update(json.loads((self.tickets / env['FILE_ATTENTE_TICKET']).read_text()))
            return lancer(argv, env)
        self.poste.lancer = espion
        self.lancer_file(priorite='fond', paralleles=2)
        self.assertEqual((vu['priorite'], vu['etat']), ('fond', 'en cours'))
        self.assertEqual(vu['debut'], fa.iso_ms(self.poste.t))

    def test_priorite_changee_pendant_l_attente_est_gardee(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.gagarine(2, 20, 'b', etat='en cours')
        vu = {}

        def change():
            ticket = [n for n in self.noms_tickets() if n.endswith('-500.json')][0]
            t = json.loads((self.tickets / ticket).read_text())
            t['priorite'] = 'urgent'
            (self.tickets / ticket).write_text(json.dumps(t))
            del self.poste.vivants[20]
        self.poste.etapes = [change]
        lancer = self.poste.lancer

        def espion(argv, env, pendant=None):
            vu.update(json.loads((self.tickets / env['FILE_ATTENTE_TICKET']).read_text()))
            return lancer(argv, env)
        self.poste.lancer = espion
        self.lancer_file(paralleles=2)
        self.assertEqual(vu['priorite'], 'urgent')

    def test_travail_herite_inchange(self):
        self.gagarine(1, 10, 'a', etat='en cours')
        self.assertEqual(self.lancer_file(env={**self.env, 'FILE_ATTENTE_TICKET': f'{self.base + 1}-10.json'},
                                          paralleles=2), 0)
        self.assertEqual(self.poste.pauses, 0)

    def test_deux_travaux_qui_partent_ensemble_ne_se_doublent_pas(self):
        """Deux tickets en attente qui tiennent chacun : seul le premier du rang part."""
        a = self.gagarine(1, 10, 'a')
        b = self.gagarine(2, 20, 'b')
        motifs = self.evaluer()['motifs']
        self.assertEqual(motifs[a], (None, None))
        self.assertNotEqual(motifs[b], (None, None))


class TestEtatParallele(BancParallele):
    def etat(self, *args, paralleles='2'):
        env = {**self.env, 'GAGARINE_FILE_PARALLELES': paralleles}
        return self.lancer_main(['file', *args], cli.Contexte(None, root=False, env=env, poste=self.poste))

    def test_json(self):
        self.gagarine(1, 10, 'a', etat='en cours', ram=4)
        self.gagarine(2, 20, 'b', ram=2, priorite='urgent')
        self.gagarine(3, 30, 'c', ram=40, priorite='fond')
        e = json.loads(self.etat('--etat', '--json')[1])
        self.assertEqual((e['paralleles'], e['en_cours']), (2, 1))
        self.assertEqual([(t['priorite'], t['besoin_go']) for t in e['travaux']], [('normal', 4), ('urgent', 2), ('fond', 40)])
        self.assertIsNone(e['travaux'][0]['motif'])
        self.assertIsNone(e['travaux'][1]['motif'])
        self.assertEqual(e['travaux'][2]['motif'], 'derrière « b » (urgent)')

    def test_json_motif_de_memoire(self):
        self.poste.ram = 3.0
        self.gagarine(1, 10, 'c', ram=4)
        t = json.loads(self.etat('--etat', '--json')[1])['travaux'][0]
        self.assertEqual(t['motif'], 'mémoire : 3 Go libres, il en faut 4')

    def test_lecture_seule(self):
        self.gagarine(1, 10, 'a')
        self.gagarine(2, 20, 'b', vivant=False)
        self.etat('--etat', '--json')
        self.assertEqual(len(self.noms_tickets()), 2)

    def test_texte(self):
        self.gagarine(1, 10, 'rendu', etat='en cours')
        self.gagarine(2, 20, 'tests', priorite='urgent', ram=40)
        lignes = self.etat('--etat')[1].splitlines()
        self.assertEqual(lignes[1], 'Travaux en cours : 1 sur 2 possibles')
        self.assertTrue(lignes[2].startswith('en cours   : rendu (processus 10, depuis '))
        self.assertTrue(lignes[3].startswith('en attente : tests (processus 20, depuis '))
        self.assertIn('priorité urgent', lignes[3])
        self.assertTrue(lignes[3].endswith(' — mémoire : 14 Go libres, il en faut 40'))

    def test_texte_un_seul_garde_les_anciens_libelles(self):
        self.gagarine(1, 10, 'rendu')
        self.gagarine(2, 20, 'tests')
        lignes = self.etat('--etat', paralleles='1')[1].splitlines()
        self.assertNotIn('Travaux en cours', lignes[1])
        self.assertTrue(lignes[1].startswith('en tête    : rendu'))
        self.assertTrue(lignes[2].startswith('en attente : tests'))

    def test_aide(self):
        for mot in ('--priorite', 'file priorite', 'file_paralleles', 'tendance'):
            self.assertIn(mot, cli.AIDE + cli.USAGE_FILE)


class TestPriorite(BancParallele):
    def prio(self, *args, root=False):
        return self.lancer_main(['file', 'priorite', *args],
                                cli.Contexte(None, root=root, env=self.env, poste=self.poste))

    def test_par_pid(self):
        a = self.gagarine(1, 10, 'a')
        code, sortie, _ = self.prio('10', 'urgent')
        self.assertEqual(code, 0)
        self.assertIn('priorité normal → urgent', sortie)
        self.assertEqual(json.loads((self.tickets / a).read_text())['priorite'], 'urgent')

    def test_par_ticket_avec_ou_sans_extension(self):
        a = self.gagarine(1, 10, 'a')
        self.assertEqual(self.prio(a[:-5], 'fond')[0], 0)
        self.assertEqual(self.prio(a, 'urgent')[0], 0)
        self.assertEqual(json.loads((self.tickets / a).read_text())['priorite'], 'urgent')

    def test_niveau_inconnu(self):
        self.gagarine(1, 10, 'a')
        code, _, erreur = self.prio('10', 'vite')
        self.assertEqual(code, 2)
        self.assertIn('urgent, normal, fond', erreur)

    def test_travail_en_cours_refuse(self):
        a = self.gagarine(1, 10, 'a', etat='en cours')
        code, _, erreur = self.prio('10', 'urgent')
        self.assertEqual(code, 1)
        self.assertIn('tourne déjà', erreur)
        self.assertEqual(json.loads((self.tickets / a).read_text())['priorite'], 'normal')

    def test_ticket_d_un_ancien_outil_refuse(self):
        self.ticket('1-10.json', 10, nom_travail='node')
        code, _, erreur = self.prio('10', 'urgent')
        self.assertEqual(code, 1)
        self.assertIn('ancien outil', erreur)

    def test_inconnu(self):
        self.assertEqual(self.prio('999', 'urgent')[0], 1)

    def test_travail_d_un_autre_refuse_sauf_root(self):
        a = self.gagarine(1, 10, 'a')
        self.poste.proprietaires[a] = 2000
        code, _, erreur = self.prio('10', 'urgent')
        self.assertEqual(code, 1)
        self.assertIn('autre utilisateur', erreur)
        self.assertEqual(self.prio('10', 'urgent', root=True)[0], 0)

    def test_usage(self):
        code, _, erreur = self.prio('10')
        self.assertEqual(code, 2)
        self.assertIn('file priorite', erreur)

    def test_option_priorite_de_la_commande(self):
        ctx = cli.Contexte(None, root=False, env=self.env, poste=self.poste)
        vu = {}
        lancer = self.poste.lancer

        def espion(argv, env, pendant=None):
            vu.update(json.loads((self.tickets / env['FILE_ATTENTE_TICKET']).read_text()))
            return lancer(argv, env)
        self.poste.lancer = espion
        self.assertEqual(self.lancer_main(['file', 'essai', '1', '1', '--priorite', 'urgent', '--', 'true'], ctx)[0], 0)
        self.assertEqual(vu['priorite'], 'urgent')

    def test_option_priorite_par_defaut_normal(self):
        ctx = cli.Contexte(None, root=False, env=self.env, poste=self.poste)
        vu = {}
        lancer = self.poste.lancer

        def espion(argv, env, pendant=None):
            vu.update(json.loads((self.tickets / env['FILE_ATTENTE_TICKET']).read_text()))
            return lancer(argv, env)
        self.poste.lancer = espion
        self.lancer_main(['file', 'essai', '1', '1', '--', 'true'], ctx)
        self.assertEqual(vu['priorite'], 'normal')

    def test_option_priorite_invalide(self):
        ctx = cli.Contexte(None, root=False, env=self.env, poste=self.poste)
        for args in (['--priorite', 'vite'], ['--priorite']):
            code, _, erreur = self.lancer_main(['file', 'essai', '1', '1', *args, '--', 'true'], ctx)
            self.assertEqual(code, 2)
            self.assertIn('--priorite demande un niveau', erreur)
        self.assertEqual(self.poste.lances, [])


if __name__ == '__main__':
    unittest.main()
