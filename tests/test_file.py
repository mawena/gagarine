"""Tests de la file d'attente des travaux lourds (`gagarine file`) : tickets, tour, limite, codes de sortie, signaux,
annulation, JSON de --etat et journal. Le poste est simulé : rien n'est lancé pour de vrai."""
import contextlib
import io
import json
import signal
import unittest
from pathlib import Path

from gagarine import cli
from gagarine import file as fa
from tests.test_commandes import Banc2


class FauxPoste:
    """Horloge, /proc, systemctl et lancement simulés. `etapes` : fonctions appelées à chaque pause (dormir)."""

    def __init__(self, pid=500):
        self.t = 1762785000.0
        self.ram = 8.0
        self.charge_1min = 0.0
        self.nb_coeurs = 4
        self.pid_courant = pid
        self.uid_courant = 1000
        self.vivants = {pid: f'd{pid}'}
        self.proprietaires = {}
        self.systemd = True
        self.etapes = []
        self.pauses = 0
        self.lances = []
        self.code = 0
        self.resultat = 'success'
        self.leve = None          # ErreurSignal levée pendant l'exécution
        self.commandes = []
        self.tues = []
        self.arretes = []
        self.armes = 0

    def maintenant(self):
        return self.t

    def mono(self):
        return self.t

    def dormir(self, s):
        self.t += s
        self.pauses += 1
        if self.etapes:
            self.etapes.pop(0)()

    def ram_disponible_go(self):
        return self.ram

    def charge(self):
        return self.charge_1min

    def coeurs(self):
        return self.nb_coeurs

    def demarrage(self, pid):
        return self.vivants.get(pid)

    def pid(self):
        return self.pid_courant

    def uid(self):
        return self.uid_courant

    def proprietaire(self, chemin):
        return self.proprietaires.get(Path(chemin).name, self.uid_courant)

    def tuer(self, pid, sig):
        self.tues.append((pid, sig))
        if pid not in self.vivants:
            raise ProcessLookupError(3, 'Aucun processus de ce type')
        if getattr(self, 'meurt_sur_tuer', True):
            del self.vivants[pid]

    def systemd_utilisateur(self):
        return self.systemd

    def executer(self, cmd):
        self.commandes.append(cmd)
        if cmd[:3] == ['systemctl', '--user', 'show'] and '--value' in cmd:
            return 0, self.resultat + '\n'
        return 0, ''

    def lancer(self, argv, env):
        self.lances.append((argv, env))
        if self.leve:
            raise self.leve
        return self.code

    def arreter(self, unite):
        self.arretes.append(unite)

    def armer_signaux(self):
        self.armes += 1

    def desarmer_signaux(self):
        pass


class BancFile(Banc2):
    def setUp(self):
        super().setUp()
        self.tickets = self.d / 'tickets'
        self.dossier_etat = self.d / 'etat'
        self.env = {'FILE_ATTENTE_DOSSIER': str(self.tickets), 'GAGARINE_ETAT': str(self.dossier_etat), 'HOME': str(self.d)}
        self.poste = FauxPoste()

    def ticket(self, nom, pid, demarrage='x', vivant=True, **champs):
        """Pose un ticket de l'ancien outil (champs de base seulement, sauf ajouts)."""
        self.tickets.mkdir(parents=True, exist_ok=True)
        t = {'pid': pid, 'demarrage': demarrage, 'nom': champs.pop('nom_travail', f'travail {pid}'),
             'depuis': '2026-11-10T14:30:00.000Z', **champs}
        (self.tickets / nom).write_text(json.dumps(t))
        if vivant:
            self.poste.vivants[pid] = demarrage

    def lancer_file(self, commande=('echo', 'ok'), nom='essai', ram=1, coeurs=1, apres=None, env=None):
        sortie, erreur = io.StringIO(), io.StringIO()
        code = fa.lancer_travail(nom, ram, coeurs, apres, list(commande), dict(env or self.env), self.poste, sortie, erreur)
        self.sortie, self.erreur = sortie.getvalue(), erreur.getvalue()
        return code

    def journal(self):
        try:
            return [json.loads(l) for l in (self.dossier_etat / 'evenements.jsonl').read_text().splitlines()]
        except OSError:
            return []

    def noms_tickets(self):
        return sorted(p.name for p in self.tickets.glob('*.json')) if self.tickets.exists() else []


class TestTickets(BancFile):
    def test_ordre_alphabetique(self):
        self.ticket('1762785000002-20.json', 20)
        self.ticket('1762785000001-10.json', 10)
        self.ticket('1762785000003-30.json', 30)
        self.assertEqual(fa.tickets(self.tickets, self.poste),
                         ['1762785000001-10.json', '1762785000002-20.json', '1762785000003-30.json'])

    def test_ticket_mort_supprime(self):
        self.ticket('1-10.json', 10, vivant=False)
        self.ticket('2-20.json', 20)
        self.assertEqual(fa.tickets(self.tickets, self.poste), ['2-20.json'])
        self.assertEqual(self.noms_tickets(), ['2-20.json'])

    def test_pid_repris_par_un_autre_programme_est_mort(self):
        self.ticket('1-10.json', 10, demarrage='ancien')
        self.poste.vivants[10] = 'nouveau'
        self.assertEqual(fa.tickets(self.tickets, self.poste), [])

    def test_ticket_illisible_compte_comme_vivant(self):
        self.tickets.mkdir()
        (self.tickets / '1-10.json').write_text('{"pid": 1')
        self.assertEqual(fa.tickets(self.tickets, self.poste), ['1-10.json'])
        self.assertEqual(self.noms_tickets(), ['1-10.json'])

    def test_lecture_seule_ne_supprime_rien(self):
        self.ticket('1-10.json', 10, vivant=False)
        self.assertEqual(fa.tickets(self.tickets, self.poste, nettoyer=False), [])
        self.assertEqual(self.noms_tickets(), ['1-10.json'])
        self.assertEqual(fa.compter_travaux(self.env, self.poste), 0)

    def test_dossier_absent(self):
        self.assertEqual(fa.compter_travaux(self.env, self.poste), 0)
        self.assertFalse(self.tickets.exists())

    def test_fichiers_autres_que_json_ignores(self):
        self.tickets.mkdir()
        (self.tickets / 'notes.txt').write_text('x')
        self.assertEqual(fa.tickets(self.tickets, self.poste), [])

    def test_dossier_par_defaut_et_variable(self):
        self.assertEqual(fa.dossier_tickets({}), Path('/tmp/file-attente-travaux-lourds'))
        self.assertEqual(fa.dossier_tickets({'FILE_ATTENTE_DOSSIER': '/x'}), Path('/x'))

    def test_date_iso_comme_node(self):
        self.assertEqual(fa.iso_ms(1762785000.123), '2025-11-10T14:30:00.123Z')


class TestTour(BancFile):
    def raison(self, ticket='5-500.json', ram=1, coeurs=1, apres=None):
        return fa.raison_d_attendre(ticket, 'essai', ram, coeurs, apres, self.tickets, self.poste)

    def test_tour_libre(self):
        self.assertEqual(self.raison(), (None, None))

    def test_travail_plus_ancien(self):
        self.ticket('1-10.json', 10, nom_travail='rendu')
        cle, texte = self.raison()
        self.assertEqual((cle, texte), ('avant', '1 travail(aux) avant lui, dont « rendu »'))

    def test_travail_plus_recent_ne_retient_pas(self):
        self.ticket('9-10.json', 10)
        self.assertEqual(self.raison(), (None, None))

    def test_ram(self):
        self.poste.ram = 0.54
        self.assertEqual(self.raison(ram=4), ('ram', 'mémoire disponible 0.5 Go, il lui en faut 4'))

    def test_coeurs(self):
        self.poste.charge_1min = 3.5
        self.assertEqual(self.raison(coeurs=2),
                         ('cpu', 'processeurs occupés (charge 3.5 pour 4 cœurs), il lui en faut 2 libres'))

    def test_apres(self):
        self.poste.vivants[77] = 'd77'
        self.assertEqual(self.raison(apres=77), ('apres', 'attend la fin du travail 77, lancé hors de la file'))
        del self.poste.vivants[77]
        self.assertEqual(self.raison(apres=77), (None, None))

    def test_priorite_apres_puis_avant_puis_ram_puis_cpu(self):
        self.poste.vivants[77] = 'd77'
        self.ticket('1-10.json', 10)
        self.poste.ram = 0
        self.poste.charge_1min = 4
        self.assertEqual(self.raison(apres=77, ram=1)[0], 'apres')
        self.assertEqual(self.raison(ram=1)[0], 'avant')
        del self.poste.vivants[10]
        self.assertEqual(self.raison(ram=1)[0], 'ram')
        self.poste.ram = 5
        self.assertEqual(self.raison(ram=1)[0], 'cpu')

    def test_attente_puis_tour_avec_messages(self):
        """Il attend la fin du travail plus ancien, puis la RAM : un message à chaque changement de raison."""
        self.ticket('1-10.json', 10, nom_travail='rendu')
        self.poste.ram = 0.5

        def fin_du_travail():
            del self.poste.vivants[10]

        def ram_liberee():
            self.poste.ram = 6

        self.poste.etapes = [lambda: None, fin_du_travail, lambda: None, ram_liberee]
        code = self.lancer_file(ram=2)
        self.assertEqual(code, 0)
        lignes = self.sortie.splitlines()
        self.assertEqual(lignes[0], "  en file d'attente (essai) : 1 travail(aux) avant lui, dont « rendu »")
        self.assertEqual(lignes[1], "  en file d'attente (essai) : mémoire disponible 0.5 Go, il lui en faut 2")
        self.assertEqual(lignes[2], "  file d'attente (essai) : son tour est venu (6.0 Go libres, charge 0.0)")
        self.assertEqual(len(lignes), 3)
        self.assertEqual(self.poste.pauses, 4)

    def test_pas_de_message_si_tour_immediat(self):
        self.assertEqual(self.lancer_file(), 0)
        self.assertEqual(self.sortie, '')

    def test_pause_de_cinq_secondes(self):
        self.assertEqual(fa.PAUSE, 5.0)


class TestHeritage(BancFile):
    def test_ticket_du_parent_existant_passe_direct(self):
        self.ticket('1-10.json', 10)
        env = {**self.env, 'FILE_ATTENTE_TICKET': '1-10.json'}
        self.poste.ram = 0     # n'attend pas, malgré la RAM et le travail devant
        code = self.lancer_file(env=env, ram=4)
        self.assertEqual(code, 0)
        argv, envfils = self.poste.lances[0]
        self.assertEqual(argv, ['echo', 'ok'])                       # sans scope : reste dans la limite du parent
        self.assertEqual(envfils['FILE_ATTENTE_TICKET'], '1-10.json')
        self.assertNotIn('FILE_ATTENTE_LIMITE_GO', envfils)
        self.assertEqual(self.noms_tickets(), ['1-10.json'])         # le ticket du parent n'est pas touché
        self.assertEqual(self.journal(), [])

    def test_ticket_du_parent_absent_ne_protege_pas(self):
        env = {**self.env, 'FILE_ATTENTE_TICKET': '1-10.json'}
        self.lancer_file(env=env)
        self.assertTrue(self.poste.lances[0][0][0] == 'systemd-run')


class TestLimite(BancFile):
    def test_limite_go(self):
        self.assertEqual(fa.limite_go(0.1), 2.1)
        self.assertEqual(fa.limite_go(0.2), 2.2)
        self.assertEqual(fa.limite_go(1), 3)
        self.assertEqual(fa.limite_go(2), 4)
        self.assertEqual(fa.limite_go(4), 8)
        self.assertEqual(fa.limite_go(10), 20)

    def test_limite_en_mo(self):
        self.assertEqual(fa.limite_mo(1), 3072)
        self.assertEqual(fa.limite_mo(0.2), 2253)
        self.assertEqual(fa.limite_mo(4), 8192)

    def test_argv_systemd_run(self):
        self.lancer_file(commande=('ffmpeg', '-i', 'a b.mp4'), ram=4)
        argv, env = self.poste.lances[0]
        nom = self.journal()[0]['ticket'][:-5]
        self.assertEqual(argv, ['systemd-run', '--user', '--scope', '--quiet', f'--unit=gagarine-travail-{nom}',
                                '-p', 'MemoryMax=8192M', '-p', 'MemorySwapMax=512M', '--', 'ffmpeg', '-i', 'a b.mp4'])
        self.assertEqual(env['FILE_ATTENTE_LIMITE_GO'], '8')
        self.assertEqual(env['FILE_ATTENTE_TICKET'], self.journal()[0]['ticket'])

    def test_nom_du_ticket_a_la_milliseconde(self):
        self.lancer_file()
        self.assertEqual(self.journal()[0]['ticket'], '1762785000000-500.json')

    def test_sans_systemd_lancement_direct(self):
        self.poste.systemd = False
        self.assertEqual(self.lancer_file(), 0)
        self.assertEqual(self.poste.lances[0][0], ['echo', 'ok'])
        self.assertIn('systemd utilisateur injoignable, travail lancé sans limite mémoire', self.sortie)
        self.assertNotIn('FILE_ATTENTE_LIMITE_GO', self.poste.lances[0][1])

    def test_ticket_porte_l_unite_pendant_le_travail(self):
        vu = {}
        lancer = self.poste.lancer

        def espion(argv, env):
            vu.update(json.loads((self.tickets / env['FILE_ATTENTE_TICKET']).read_text()))
            return lancer(argv, env)
        self.poste.lancer = espion
        self.lancer_file(ram=2)
        self.assertEqual(vu['etat'], 'en cours')
        self.assertEqual(vu['unite'], 'gagarine-travail-1762785000000-500.scope')
        self.assertEqual((vu['ram'], vu['coeurs'], vu['source']), (2, 1, 'gagarine'))
        self.assertEqual(vu['demarrage'], 'd500')
        self.assertEqual(vu['depuis'], '2025-11-10T14:30:00.000Z')


class TestCodes(BancFile):
    def test_code_normal_rendu(self):
        self.poste.code = 3
        self.assertEqual(self.lancer_file(), 3)
        self.assertEqual([e['action'] for e in self.journal()], ['travail-debut', 'travail-fin'])
        fin = self.journal()[1]
        self.assertEqual((fin['code'], fin['nom'], fin['source']), (3, 'essai', 'file'))
        self.assertIn('duree', fin)

    def test_ticket_retire_a_la_fin(self):
        self.lancer_file()
        self.assertEqual(self.noms_tickets(), [])

    def test_oom_kill_rend_137_et_note_la_limite(self):
        self.poste.resultat = 'oom-kill'
        self.poste.code = 137
        self.assertEqual(self.lancer_file(commande=('python3', '-c', 'x'), ram=0.2), 137)
        self.assertIn('ARRÊTÉ par sa limite mémoire : il a dépassé 2.2 Go alors qu\'il en annonçait 0.2', self.erreur)
        limite = [e for e in self.journal() if e['action'] == 'limite']
        self.assertEqual(len(limite), 1)
        self.assertEqual((limite[0]['declare'], limite[0]['limite'], limite[0]['commande'], limite[0]['source']),
                         (0.2, 2.2, 'python3 -c x', 'file'))
        self.assertIn(['systemctl', '--user', 'reset-failed', 'gagarine-travail-1762785000000-500.scope'],
                      self.poste.commandes)

    def test_oom_kill_avec_un_code_quelconque_donne_137(self):
        self.poste.resultat = 'oom-kill'
        self.poste.code = 0
        self.assertEqual(self.lancer_file(), 137)

    def test_signal_de_la_commande(self):
        self.poste.code = 128 + signal.SIGKILL      # Poste.lancer rend 128 + n pour un programme tué par n
        self.assertEqual(self.lancer_file(), 137)
        self.assertNotIn('limite', [e['action'] for e in self.journal()])

    def test_lancer_reel_signal_et_introuvable(self):
        import sys
        p = fa.Poste()
        self.assertEqual(p.lancer([sys.executable, '-c', 'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'], {}), 143)
        self.assertEqual(p.lancer([sys.executable, '-c', 'raise SystemExit(7)'], {}), 7)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(p.lancer(['/inexistant/prog'], {}), 127)

    def test_sortie_reprise_de_gagarine_sortie(self):
        env = {**self.env, 'GAGARINE_SORTIE': '/tmp/x.log'}
        vu = {}
        lancer = self.poste.lancer

        def espion(argv, e):
            vu.update(json.loads((self.tickets / e['FILE_ATTENTE_TICKET']).read_text()))
            return lancer(argv, e)
        self.poste.lancer = espion
        self.lancer_file(env=env)
        self.assertEqual(vu['sortie'], '/tmp/x.log')
        self.assertEqual(self.journal()[0]['sortie'], '/tmp/x.log')

    def test_sortie_absente(self):
        self.lancer_file()
        self.assertNotIn('sortie', self.journal()[0])


class TestSignaux(BancFile):
    def test_signal_pendant_l_attente(self):
        self.ticket('1-10.json', 10)

        def sigterm():
            raise fa.ErreurSignal(signal.SIGTERM)
        self.poste.etapes = [sigterm]
        self.assertEqual(self.lancer_file(), 143)
        self.assertEqual(self.noms_tickets(), ['1-10.json'])     # le sien est retiré, pas celui des autres
        self.assertEqual(self.poste.lances, [])
        self.assertEqual(self.poste.arretes, [])
        e = self.journal()
        self.assertEqual([x['action'] for x in e], ['travail-annule'])
        self.assertEqual((e[0]['source'], e[0]['en_cours'], e[0]['signal']), ('file', False, 15))

    def test_sigint_pendant_l_attente(self):
        self.ticket('1-10.json', 10)
        self.poste.etapes = [lambda: (_ for _ in ()).throw(fa.ErreurSignal(signal.SIGINT))]
        self.assertEqual(self.lancer_file(), 130)

    def test_signal_pendant_l_execution_arrete_le_scope(self):
        self.poste.leve = fa.ErreurSignal(signal.SIGTERM)
        self.assertEqual(self.lancer_file(), 143)
        self.assertEqual(self.poste.arretes, ['gagarine-travail-1762785000000-500'])
        self.assertEqual(self.noms_tickets(), [])
        self.assertEqual([e['action'] for e in self.journal()], ['travail-debut', 'travail-annule'])
        self.assertTrue(self.journal()[1]['en_cours'])

    def test_signaux_armes(self):
        self.lancer_file()
        self.assertEqual(self.poste.armes, 1)


class TestEtat(BancFile):
    def etat(self, *args):
        return self.lancer_main(['file', *args], cli.Contexte(None, root=False, env=self.env, poste=self.poste))

    def test_json_ticket_de_gagarine(self):
        self.ticket('1762785000000-1234.json', 1234, nom_travail='rendu vidéo', etat='en cours', ram=4, coeurs=4,
                    commande='ffmpeg …', unite='gagarine-travail-1762785000000-1234.scope', sortie='/x.log',
                    source='gagarine')
        self.poste.ram, self.poste.charge_1min = 3.64, 2.14
        code, sortie, _ = self.etat('--etat', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(sortie.count('\n'), 1)
        e = json.loads(sortie)
        self.assertEqual(set(e), {'ram_disponible_go', 'charge', 'coeurs', 'dossier', 'travaux'})
        self.assertEqual((e['ram_disponible_go'], e['charge'], e['coeurs'], e['dossier']), (3.6, 2.1, 4, str(self.tickets)))
        self.assertEqual(e['travaux'], [{
            'ticket': '1762785000000-1234.json', 'position': 1, 'nom': 'rendu vidéo', 'pid': 1234,
            'depuis': '2026-11-10T14:30:00.000Z', 'etat': 'en cours', 'ram': 4, 'coeurs': 4, 'commande': 'ffmpeg …',
            'unite': 'gagarine-travail-1762785000000-1234.scope', 'sortie': '/x.log', 'source': 'gagarine'}])

    def test_json_ticket_de_l_outil_node(self):
        """Un ticket écrit par l'outil Node : quatre champs, les autres à null, source « autre »."""
        self.ticket('1762785000000-1234.json', 1234, nom_travail='node')
        e = json.loads(self.etat('--etat', '--json')[1])
        self.assertEqual(e['travaux'], [{
            'ticket': '1762785000000-1234.json', 'position': 1, 'nom': 'node', 'pid': 1234,
            'depuis': '2026-11-10T14:30:00.000Z', 'etat': None, 'ram': None, 'coeurs': None, 'commande': None,
            'unite': None, 'sortie': None, 'source': 'autre'}])

    def test_json_positions_et_ticket_mort_absent(self):
        self.ticket('1-10.json', 10)
        self.ticket('2-20.json', 20, vivant=False)
        self.ticket('3-30.json', 30)
        e = json.loads(self.etat('--etat', '--json')[1])
        self.assertEqual([(t['ticket'], t['position']) for t in e['travaux']], [('1-10.json', 1), ('3-30.json', 2)])
        self.assertEqual(self.noms_tickets(), ['1-10.json', '2-20.json', '3-30.json'])   # lecture seule

    def test_json_file_vide(self):
        self.assertEqual(json.loads(self.etat('etat', '--json')[1])['travaux'], [])

    def test_texte(self):
        self.ticket('1-10.json', 10, nom_travail='rendu')
        self.ticket('2-20.json', 20, nom_travail='tests')
        code, sortie, _ = self.etat('--etat')
        lignes = sortie.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lignes[0], 'Mémoire disponible : 8.0 Go ; charge 0.0 pour 4 cœurs')
        self.assertTrue(lignes[1].startswith('en tête    : rendu (processus 10, depuis '))
        self.assertTrue(lignes[2].startswith('en attente : tests (processus 20, depuis '))

    def test_texte_vide_et_alias(self):
        for args in (('--etat',), ('etat',)):
            self.assertIn('Aucun travail en file.', self.etat(*args)[1])

    def test_etat_general_montre_les_travaux(self):
        self.ticket('1-10.json', 10)
        self.ticket('2-20.json', 20)
        ctx = cli.Contexte(None, root=False, env=self.env, lancer=lambda c: 'active\n', poste=self.poste)
        e = json.loads(self.lancer_main(['etat', '--json'], ctx)[1])
        self.assertEqual(e['file'], {'travaux': 2})
        self.assertIn('File d\'attente : 2 travail(aux) en file', self.lancer_main(['etat'], ctx)[1])

    def test_usage(self):
        for args in (('--bidon',), ('annuler',), ('essai', '1', '1')):
            code, _, erreur = self.etat(*args)
            self.assertEqual(code, 2)
            self.assertIn('usage : gagarine file', erreur)


class TestAnnuler(BancFile):
    def annuler(self, cible, root=False):
        ctx = cli.Contexte(None, root=root, env=self.env, poste=self.poste)
        return self.lancer_main(['file', 'annuler', cible], ctx)

    def test_par_pid(self):
        self.ticket('1-10.json', 10, nom_travail='rendu')
        code, sortie, _ = self.annuler('10')
        self.assertEqual(code, 0)
        self.assertEqual(self.poste.tues, [(10, signal.SIGTERM)])
        self.assertIn('annulé', sortie)

    def test_par_ticket_avec_ou_sans_extension(self):
        self.ticket('1-10.json', 10)
        self.assertEqual(self.annuler('1-10')[0], 0)
        self.ticket('2-20.json', 20)
        self.assertEqual(self.annuler('2-20.json')[0], 0)

    def test_ticket_d_un_ancien_outil_retire_apres_sa_mort(self):
        self.ticket('1-10.json', 10)
        self.annuler('10')
        self.assertEqual(self.noms_tickets(), [])

    def test_travail_qui_ne_meurt_pas_garde_son_ticket(self):
        self.ticket('1-10.json', 10)
        self.poste.meurt_sur_tuer = False
        self.annuler('10')
        self.assertEqual(self.noms_tickets(), ['1-10.json'])

    def test_inconnu(self):
        code, _, erreur = self.annuler('99')
        self.assertEqual(code, 1)
        self.assertIn('aucun travail', erreur)
        self.assertEqual(self.poste.tues, [])

    def test_travail_mort_est_inconnu(self):
        self.ticket('1-10.json', 10, vivant=False)
        self.assertEqual(self.annuler('10')[0], 1)

    def test_travail_d_un_autre_refuse(self):
        self.ticket('1-10.json', 10)
        self.poste.proprietaires['1-10.json'] = 1001
        code, _, erreur = self.annuler('10')
        self.assertEqual(code, 1)
        self.assertIn('autre utilisateur', erreur)
        self.assertEqual(self.poste.tues, [])

    def test_root_annule_tout(self):
        self.ticket('1-10.json', 10)
        self.poste.proprietaires['1-10.json'] = 1001
        self.assertEqual(self.annuler('10', root=True)[0], 0)
        self.assertEqual(self.poste.tues, [(10, signal.SIGTERM)])

    def test_pas_n_importe_quel_processus(self):
        """Un PID qui n'est pas dans la file n'est jamais touché, même s'il existe."""
        self.poste.vivants[4242] = 'd'
        self.assertEqual(self.annuler('4242')[0], 1)
        self.assertEqual(self.poste.tues, [])

    def test_soi_meme_refuse(self):
        self.ticket('1-500.json', 500, demarrage='d500')
        self.assertEqual(self.annuler('500')[0], 1)
        self.assertEqual(self.poste.tues, [])


class TestCommande(BancFile):
    def file(self, *args):
        ctx = cli.Contexte(None, root=False, env=self.env, poste=self.poste)
        return self.lancer_main(['file', *args], ctx)

    def test_lance_la_commande_et_rend_son_code(self):
        self.poste.code = 3
        code, _, _ = self.file('essai', '0.1', '1', '--', 'sh', '-c', 'echo ok; exit 3')
        self.assertEqual(code, 3)
        argv = self.poste.lances[0][0]
        self.assertIn('MemoryMax=2150M', argv)       # 2,1 Go : max(0,2 ; 2,1)
        self.assertEqual(argv[-4:], ['--', 'sh', '-c', 'echo ok; exit 3'])

    def test_apres(self):
        self.poste.vivants[77] = 'd77'
        self.poste.etapes = [lambda: self.poste.vivants.pop(77)]
        self.file('essai', '1', '1', '--apres', '77', '--', 'true')
        self.assertEqual(self.poste.pauses, 1)

    def test_virgule_decimale(self):
        self.file('essai', '0,5', '1', '--', 'true')
        self.assertEqual(self.journal()[0]['ram'], 0.5)

    def test_arguments_invalides(self):
        for args in (('essai', 'beaucoup', '1', '--', 'true'), ('essai', '1', '--', 'true'),
                     ('essai', '1', '1', '--'), ('essai', '1', '1', '--apres', 'x', '--', 'true'),
                     ('essai', '-1', '1', '--', 'true')):
            code, _, erreur = self.file(*args)
            self.assertEqual(code, 2, args)
            self.assertTrue(erreur)
        self.assertEqual(self.poste.lances, [])

    def test_aide_mentionne_la_file(self):
        self.assertIn('file "NOM" GO COEURS', cli.AIDE)
        self.assertIn('file annuler', cli.AIDE)


if __name__ == '__main__':
    unittest.main()
