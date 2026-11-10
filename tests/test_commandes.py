"""Tests des commandes de la 1.1.0 : etat --json, journal, reglages, service, rechargement sur SIGHUP, notifications."""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from gagarine import cli
from gagarine import journal as jn
from gagarine import reglages as rg
from gagarine import systeme as sy
from tests.commun import Banc, Mo

CHEMIN = Path(__file__).resolve().parent.parent / 'bin' / 'gagarine'

MEMINFO = 'MemTotal:       16000000 kB\nMemFree: 1 kB\nMemAvailable:    4000000 kB\n'
SWAPS = ('Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n'
         '/dev/zram0                              partition\t8388608\t\t1048576\t\t100\n'
         '/dev/nvme0n1p7                          partition\t10485756\t6081380\t\t-2\n')
CONF = ('# Réglages de Gagarine\n\n[gagarine]\n\n# Le seuil.\n#seuil=95\n\nurgence = 97\n# Les notifications.\n'
        'notifications=oui\n\n[autre]\nseuil=1\n')


class Banc2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def ecrire(self, nom, texte):
        p = self.d / nom
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texte)
        return p

    def lancer_main(self, args, ctx):
        sortie, erreur = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(erreur):
            code = cli.main(args, ctx)
        return code, sortie.getvalue(), erreur.getvalue()


class FauxSystemctl:
    """Enregistre les commandes ; `actif` est la réponse de « systemctl is-active »."""

    def __init__(self, actif='active', code=0):
        self.appels = []
        self.actif = actif
        self.code = code

    def __call__(self, cmd):
        self.appels.append(cmd)
        if cmd[1:2] == ['is-active']:
            return 0, self.actif + '\n', ''
        return self.code, '', 'échec simulé' if self.code else ''


class TestEtatJson(Banc2):
    def donnees(self, **kw):
        procs = {10: sy.Proc('petit', 'S', 10 * Mo, 0, 1000), 11: sy.Proc('gros', 'S', 900 * Mo, 100 * Mo, 1000),
                 12: sy.Proc('gnome-shell', 'S', 500 * Mo, 5 * Mo, 1000)}
        procs.update({100 + i: sy.Proc(f'p{i}', 'S', (20 + i) * Mo, 0, 1000) for i in range(15)})
        sortie = {('systemctl', 'is-active', 'gagarine'): 'active\n', ('systemctl', 'is-enabled', 'gagarine'): 'enabled\n'}
        args = dict(r=rg.Reglages(), fichier_reglages='/etc/gagarine.conf', dossier=self.d,
                    lancer=lambda c: sortie.get(tuple(c), ''), meminfo=MEMINFO, swaps=SWAPS, procs=procs,
                    figes={4242: {'debut': 'x', 'nom': 'python3', 'depuis': 1762785000.5, 'memoire': 9100 * Mo,
                                  'commande': 'python3 -m pytest', 'utilisateur': 'moi'}},
                    bride=lambda p: True, maintenant=lambda: 1762785000)
        args.update(kw)
        return cli.donnees_etat(**args)

    def test_contrat(self):
        e = self.donnees()
        self.assertEqual(e['version'], '1.0.0')
        self.assertRegex(e['date'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$')
        self.assertEqual(e['ram'], {'pourcent': 75.0, 'total_mo': 15625, 'disponible_mo': 3906})
        self.assertEqual(e['swap'][0], {'nom': '/dev/zram0', 'type': 'partition', 'taille_mo': 8192, 'utilise_mo': 1024,
                                        'priorite': 100, 'zram': True})
        self.assertFalse(e['swap'][1]['zram'])
        self.assertEqual(e['swap'][1]['priorite'], -2)
        self.assertEqual(set(e['reglages']), {'seuil', 'urgence', 'vigilance', 'grace', 'vidage_max', 'delai_fige',
                                              'reste_si_repris', 'notifications', 'proteges'})
        self.assertEqual(e['fichier_reglages'], '/etc/gagarine.conf')
        self.assertEqual(e['service'], {'etat': 'active', 'actif_au_demarrage': True})
        self.assertEqual(e['dossier_etat'], str(self.d))
        self.assertEqual(e['figes'], [{'pid': 4242, 'nom': 'python3', 'depuis': 1762785000.5, 'memoire_mo': 9100,
                                       'commande': 'python3 -m pytest', 'utilisateur': 'moi', 'bride': True}])
        self.assertEqual(e['file'], {'travaux': 0})

    def test_plus_gros_dix_par_ram_et_swap(self):
        gros = self.donnees()['plus_gros']
        self.assertEqual(len(gros), 10)
        self.assertEqual([g['nom'] for g in gros[:3]], ['gros', 'gnome-shell', 'p14'])
        self.assertEqual(set(gros[0]), {'pid', 'nom', 'rss_mo', 'swap_mo', 'utilisateur', 'protege'})
        self.assertEqual((gros[0]['rss_mo'], gros[0]['swap_mo'], gros[0]['protege']), (900, 100, False))
        self.assertTrue(gros[1]['protege'])
        tailles = [g['rss_mo'] + g['swap_mo'] for g in gros]
        self.assertEqual(tailles, sorted(tailles, reverse=True))

    def test_service_inactif(self):
        e = self.donnees(lancer=lambda c: 'inactive\n' if c[1] == 'is-active' else 'disabled\n')
        self.assertEqual(e['service'], {'etat': 'inactive', 'actif_au_demarrage': False})

    def test_systemctl_absent(self):
        self.assertEqual(self.donnees(lancer=lambda c: '')['service'], {'etat': 'unknown', 'actif_au_demarrage': False})

    def test_commande_json_valide_sur_une_seule_ligne(self):
        conf = self.ecrire('a.conf', '[gagarine]\nseuil=90\n')
        ctx = cli.Contexte(str(conf), root=False, env={'GAGARINE_ETAT': str(self.d)}, lancer=lambda c: 'active\n')
        code, sortie, _ = self.lancer_main(['etat', '--json'], ctx)
        self.assertEqual(code, 0)
        self.assertEqual(sortie.count('\n'), 1)
        e = json.loads(sortie)
        self.assertEqual(e['reglages']['seuil'], 90)
        self.assertEqual(e['fichier_reglages'], str(conf))
        self.assertEqual(e['dossier_etat'], str(self.d))
        self.assertLessEqual(len(e['plus_gros']), 10)

    def test_etat_texte_inchange(self):
        ctx = cli.Contexte(None, root=False, env={'GAGARINE_ETAT': str(self.d), 'GAGARINE_CONFIG': os.devnull},
                           lancer=lambda c: 'active\n')
        code, sortie, _ = self.lancer_main(['etat'], ctx)
        self.assertEqual(code, 0)
        self.assertIn('RAM utilisée :', sortie)
        self.assertIn('Service gagarine : active', sortie)
        self.assertIn('Aucun programme figé.', sortie)

    def test_swap_ignore_les_lignes_invalides(self):
        self.assertEqual(sy.lire_swaps('Filename Type Size Used Priority\nn’importe quoi\n'), [])


class TestJournalCommande(Banc2):
    def ev(self, date, action, **champs):
        return json.dumps({'date': date, 'source': 'gagarine', 'action': action, **champs}, ensure_ascii=False)

    def env(self):
        etat = self.d / 'etat'
        self.ecrire('etat/evenements.jsonl', '\n'.join([
            self.ev('2026-11-10T10:00:03+0000', 'fige', pid=1, nom='a'),
            'ceci n\'est pas du JSON',
            self.ev('2026-11-10T10:00:01+0000', 'demarrage', pid=9, nom='gagarine'),
            '[1, 2]',
            json.dumps({'action': 'sans-date'}),
            self.ev('illisible', 'date-illisible'),
            '']))
        self.ecrire('.local/state/file-attente/evenements.jsonl', '\n'.join([
            json.dumps({'date': '2026-11-10T10:00:02.123Z', 'source': 'file', 'action': 'travail-debut', 'nom': 'x'}),
            json.dumps({'date': '2026-11-10T10:00:04.000Z', 'source': 'file', 'action': 'travail-fin', 'nom': 'x'}),
            '']))
        self.ecrire('.local/state/gagarine/evenements.jsonl', self.ev('2026-11-10T09:00:00+0000', 'arrete', pid=3) + '\n')
        return {'GAGARINE_ETAT': str(etat), 'HOME': str(self.d)}

    def test_fusion_tri_et_lignes_invalides_ignorees(self):
        env = self.env()
        evs = jn.lire_evenements(jn.fichiers_journaux(env, self.d), 50)
        self.assertEqual([e['action'] for e in evs], ['demarrage', 'travail-debut', 'fige', 'travail-fin'])

    def test_les_trois_sources_sans_gagarine_etat(self):
        f = jn.fichiers_journaux({}, '/home/u')
        self.assertEqual([str(x) for x in f], ['/var/lib/gagarine/evenements.jsonl',
                                               '/home/u/.local/state/gagarine/evenements.jsonl',
                                               '/home/u/.local/state/file-attente/evenements.jsonl'])

    def test_n_derniers(self):
        env = self.env()
        evs = jn.lire_evenements(jn.fichiers_journaux(env, self.d), 2)
        self.assertEqual([e['action'] for e in evs], ['fige', 'travail-fin'])
        self.assertEqual(jn.lire_evenements(jn.fichiers_journaux(env, self.d), 0), [])

    def test_fichier_illisible_ou_absent_ignore(self):
        evs = jn.lire_evenements([self.d / 'nexiste-pas', self.d], 5)   # absent, et un dossier
        self.assertEqual(evs, [])

    def test_commande_json(self):
        env = self.env()
        code, sortie, _ = self.lancer_main(['journal', '-n', '3', '--json'], cli.Contexte(env=env))
        self.assertEqual(code, 0)
        obj = json.loads(sortie)
        self.assertEqual(list(obj), ['evenements'])
        self.assertEqual([e['action'] for e in obj['evenements']], ['travail-debut', 'fige', 'travail-fin'])
        self.assertEqual(obj['evenements'][1], {'date': '2026-11-10T10:00:03+0000', 'source': 'gagarine',
                                                'action': 'fige', 'pid': 1, 'nom': 'a'})

    def test_commande_texte_et_defaut_50(self):
        env = self.env()
        code, sortie, _ = self.lancer_main(['journal'], cli.Contexte(env=env))
        self.assertEqual(code, 0)
        lignes = sortie.splitlines()
        self.assertEqual(len(lignes), 4)
        self.assertIn('fige', lignes[2])
        self.assertIn('a 1', lignes[2])

    def test_aucun_evenement(self):
        env = {'GAGARINE_ETAT': str(self.d / 'vide'), 'HOME': str(self.d)}
        code, sortie, _ = self.lancer_main(['journal'], cli.Contexte(env=env))
        self.assertEqual((code, sortie), (0, 'Aucun événement.\n'))
        code, sortie, _ = self.lancer_main(['journal', '--json'], cli.Contexte(env=env))
        self.assertEqual(json.loads(sortie), {'evenements': []})

    def test_options_invalides(self):
        for args in (['journal', '-n', 'beaucoup'], ['journal', '-n'], ['journal', '--bizarre']):
            self.assertEqual(self.lancer_main(args, cli.Contexte(env={}))[0], 2)

    def test_horodatages(self):
        self.assertEqual(jn.horodatage('1970-01-01T00:00:00+0000'), 0)
        self.assertEqual(jn.horodatage('1970-01-01T00:00:01Z'), 1)
        self.assertEqual(jn.horodatage('1970-01-01T01:00:00+01:00'), 0)
        self.assertEqual(jn.horodatage(5), 5.0)
        for mauvais in (None, 'hier', True, ['x']):
            self.assertIsNone(jn.horodatage(mauvais))


class TestReglagesCommande(Banc2):
    def ctx(self, root=True, actif='active', code=0, fichier=None):
        self.systemctl = FauxSystemctl(actif, code)
        return cli.Contexte(str(fichier) if fichier else None, root=root, env={}, executer=self.systemctl)

    def test_json(self):
        f = self.ecrire('a.conf', CONF)
        code, sortie, _ = self.lancer_main(['reglages', '--json'], self.ctx(False, fichier=f))
        obj = json.loads(sortie)
        self.assertEqual(obj['fichier'], str(f))
        self.assertEqual(obj['reglages']['urgence'], 97)
        self.assertEqual(obj['reglages']['seuil'], 95)
        self.assertIs(obj['reglages']['notifications'], True)
        self.assertEqual(set(obj['reglages']), set(rg.CLES_ECRITURE))

    def test_texte(self):
        f = self.ecrire('a.conf', CONF)
        code, sortie, _ = self.lancer_main(['reglages'], self.ctx(False, fichier=f))
        self.assertEqual(code, 0)
        self.assertIn('urgence', sortie)
        self.assertIn(str(f), sortie)

    def test_ecrire_garde_commentaires_et_ordre(self):
        f = self.ecrire('a.conf', CONF)
        code, sortie, erreur = self.lancer_main(['reglages', '--ecrire', 'urgence=99', 'notifications=non', 'grace=5'],
                                                self.ctx(fichier=f))
        self.assertEqual((code, erreur), (0, ''))
        attendu = CONF.replace('urgence = 97', 'urgence = 99').replace('notifications=oui', 'notifications=non')
        attendu = attendu.replace('notifications=non\n', 'notifications=non\ngrace=5\n', 1)
        self.assertEqual(f.read_text(), attendu)
        r, av = rg.charger_reglages(str(f), {})
        self.assertEqual((r.urgence, r.notifications, r.grace, av), (99, False, 5, []))
        self.assertEqual(sorted(p.name for p in self.d.iterdir()), ['a.conf'])   # aucun fichier temporaire

    def test_ajout_a_la_fin_de_la_section_et_pas_de_l_autre(self):
        f = self.ecrire('a.conf', CONF)
        self.lancer_main(['reglages', '--ecrire', 'seuil=90'], self.ctx(fichier=f))
        lignes = f.read_text().splitlines()
        self.assertEqual(lignes[lignes.index('notifications=oui') + 1:lignes.index('notifications=oui') + 2], ['seuil=90'])
        self.assertIn('#seuil=95', lignes)          # le commentaire de la valeur par défaut est gardé
        self.assertEqual(lignes[-2:], ['[autre]', 'seuil=1'])   # l'autre section ne bouge pas

    def test_ajout_sans_section_et_fichier_absent(self):
        f = self.d / 'neuf.conf'
        self.assertEqual(self.lancer_main(['reglages', '--ecrire', 'seuil=90', 'proteges=a|b'], self.ctx(fichier=f))[0], 0)
        self.assertEqual(f.read_text(), '[gagarine]\nseuil=90\nproteges=a|b\n')
        g = self.ecrire('b.conf', '# rien\n')
        self.lancer_main(['reglages', '--ecrire', 'seuil=90'], self.ctx(fichier=g))
        self.assertEqual(g.read_text(), '# rien\n\n[gagarine]\nseuil=90\n')

    def test_droits_du_fichier_conserves(self):
        f = self.ecrire('a.conf', CONF)
        os.chmod(f, 0o640)
        self.lancer_main(['reglages', '--ecrire', 'seuil=90'], self.ctx(fichier=f))
        self.assertEqual(f.stat().st_mode & 0o777, 0o640)

    def test_booleens_ecrits_oui_non(self):
        f = self.ecrire('a.conf', CONF)
        self.lancer_main(['reglages', '--ecrire', 'notifications=false'], self.ctx(fichier=f))
        self.assertIn('\nnotifications=non\n', f.read_text())

    def test_cle_inconnue_refus_en_bloc(self):
        f = self.ecrire('a.conf', CONF)
        code, _, erreur = self.lancer_main(['reglages', '--ecrire', 'seuil=90', 'couleur=bleu'], self.ctx(fichier=f))
        self.assertEqual(code, 2)
        self.assertIn('couleur', erreur)
        self.assertEqual(f.read_text(), CONF)
        self.assertEqual(self.systemctl.appels, [])

    def test_valeur_invalide_refus_en_bloc(self):
        f = self.ecrire('a.conf', CONF)
        for mauvais in ('seuil=beaucoup', 'seuil=0', 'urgence=101', 'grace=-1', 'notifications=peut-etre', 'proteges=(',
                        'filtre=x', 'seuil=nan'):
            code, _, erreur = self.lancer_main(['reglages', '--ecrire', 'grace=5', mauvais], self.ctx(fichier=f))
            self.assertEqual(code, 2, mauvais)
            self.assertTrue(erreur, mauvais)
            self.assertEqual(f.read_text(), CONF, mauvais)

    def test_refus_sans_root(self):
        f = self.ecrire('a.conf', CONF)
        code, _, erreur = self.lancer_main(['reglages', '--ecrire', 'seuil=90'], self.ctx(root=False, fichier=f))
        self.assertEqual(code, 1)
        self.assertIn('administrateur', erreur)
        self.assertEqual(f.read_text(), CONF)
        self.assertEqual(self.systemctl.appels, [])

    def test_ecrire_sans_argument(self):
        self.assertEqual(self.lancer_main(['reglages', '--ecrire'], self.ctx(fichier=self.d / 'x'))[0], 2)
        self.assertEqual(self.lancer_main(['reglages', '--ecrire', 'seuil'], self.ctx(fichier=self.d / 'x'))[0], 2)

    def test_rechargement_du_service_s_il_tourne(self):
        f = self.ecrire('a.conf', CONF)
        code, sortie, _ = self.lancer_main(['reglages', '--ecrire', 'seuil=90'], self.ctx(fichier=f))
        self.assertEqual(code, 0)
        self.assertIn(['systemctl', 'reload', 'gagarine'], self.systemctl.appels)
        self.assertIn('rechargé', sortie)

    def test_pas_de_rechargement_si_le_service_ne_tourne_pas(self):
        f = self.ecrire('a.conf', CONF)
        code, sortie, _ = self.lancer_main(['reglages', '--ecrire', 'seuil=90'], self.ctx(actif='inactive', fichier=f))
        self.assertEqual(code, 0)
        self.assertNotIn(['systemctl', 'reload', 'gagarine'], self.systemctl.appels)
        self.assertIn('seuil=90', f.read_text())

    def test_rechargement_en_echec_signale(self):
        f = self.ecrire('a.conf', CONF)
        ctx = self.ctx(fichier=f)
        ctx.executer = lambda c: (0, 'active\n', '') if c[1] == 'is-active' else (1, '', 'refusé')
        code, _, erreur = self.lancer_main(['reglages', '--ecrire', 'seuil=90'], ctx)
        self.assertEqual(code, 1)
        self.assertIn('rechargement', erreur)
        self.assertIn('seuil=90', f.read_text())   # le fichier, lui, est bien écrit

    def test_doublons_fusionnes(self):
        self.assertEqual(rg.texte_modifie('[gagarine]\nseuil=1\nSeuil = 2\n', {'seuil': '9'}), '[gagarine]\nseuil=9\n')


class TestServiceCommande(Banc2):
    def test_chaque_action(self):
        attendu = {'demarrer': ['start'], 'arreter': ['stop'], 'redemarrer': ['restart'],
                   'activer': ['enable', '--now'], 'desactiver': ['disable', '--now']}
        for action, verbe in attendu.items():
            sc = FauxSystemctl()
            code, sortie, _ = self.lancer_main(['service', action], cli.Contexte(root=True, env={}, executer=sc))
            self.assertEqual(code, 0, action)
            self.assertEqual(sc.appels, [['systemctl', *verbe, 'gagarine']])
            self.assertIn('Service gagarine', sortie)

    def test_refus_sans_root(self):
        sc = FauxSystemctl()
        code, _, erreur = self.lancer_main(['service', 'demarrer'], cli.Contexte(root=False, env={}, executer=sc))
        self.assertEqual(code, 1)
        self.assertIn('administrateur', erreur)
        self.assertEqual(sc.appels, [])

    def test_echec_de_systemctl(self):
        code, _, erreur = self.lancer_main(['service', 'stop'.replace('stop', 'arreter')],
                                           cli.Contexte(root=True, env={}, executer=FauxSystemctl(code=1)))
        self.assertEqual(code, 1)
        self.assertIn('échec simulé', erreur)

    def test_action_inconnue(self):
        for args in (['service'], ['service', 'detruire'], ['service', 'demarrer', 'x']):
            self.assertEqual(self.lancer_main(args, cli.Contexte(root=True, env={}, executer=FauxSystemctl()))[0], 2)


class TestRechargementSighup(Banc):
    def test_reglages_relus_sans_perdre_l_etat(self):
        g = self.garde(seuil=95)
        self.s.ajouter(10, 'gros', 3000)
        self.tours(2, pct=96)
        self.assertIn(10, g.figes)
        self.assertTrue(g.historique)
        historique = {p: list(h) for p, h in g.historique.items()}
        nouveaux = rg.Reglages(seuil=80, notifications=False, proteges='gros')
        g.charger_reglages = lambda: (nouveaux, ['un avertissement'])
        g.demander_rechargement()
        self.assertTrue(g.a_recharger)
        pauses = []

        def dormir(t):
            pauses.append(t)
            raise StopIteration

        with self.assertRaises(StopIteration):
            g.boucle(dormir=dormir)
        self.assertFalse(g.a_recharger)
        self.assertEqual((g.r.seuil, g.s.r.seuil, g.r.notifications), (80, 80, False))
        self.assertTrue(g.est_protege('gros'))
        self.assertIn(10, g.figes)                         # programmes figés gardés
        for p, h in historique.items():                     # historique gardé (un point de plus au plus)
            self.assertEqual(list(g.historique[p])[:len(h)], h)
        self.assertIn('rechargement', self.actions())
        self.assertEqual(self.evenements()[-1]['seuil'], 80)

    def test_fichier_invalide_garde_les_reglages_en_vigueur(self):
        g = self.garde(seuil=95)

        def casse():
            raise OSError('illisible')
        g.charger_reglages = casse
        g.recharger()
        self.assertEqual(g.r.seuil, 95)
        self.assertNotIn('rechargement', self.actions())

    def test_sans_chargeur_rien_ne_change(self):
        g = self.garde(seuil=95)
        g.demander_rechargement()
        g.recharger()
        self.assertEqual(g.r.seuil, 95)

    def test_le_gestionnaire_de_signal_leve_le_drapeau(self):
        g = self.garde()
        ancien = signal.signal(signal.SIGHUP, g.demander_rechargement)
        try:
            os.kill(os.getpid(), signal.SIGHUP)
            for _ in range(50):
                if g.a_recharger:
                    break
                time.sleep(0.01)
        finally:
            signal.signal(signal.SIGHUP, ancien)
        self.assertTrue(g.a_recharger)

    def test_demon_reel_recharge_sur_sighup(self):
        conf = self.dossier / 'a.conf'
        conf.write_text('[gagarine]\nseuil=99.5\nurgence=99.9\nnotifications=non\n')
        env = dict(os.environ, GAGARINE_ETAT=str(self.dossier / 'etat'), GAGARINE_CONFIG=str(conf),
                   GAGARINE_FILTRE='aucun-programme-ne-porte-ce-nom-xyz')
        env = {k: v for k, v in env.items() if k not in ('GAGARINE_SEUIL', 'GAGARINE_URGENCE')}
        p = subprocess.Popen([str(CHEMIN), 'surveiller'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            journal = self.dossier / 'etat' / 'evenements.jsonl'
            for _ in range(100):
                if journal.exists():
                    break
                time.sleep(0.1)
            self.assertTrue(journal.exists(), 'le démon n\'a pas démarré')
            conf.write_text('[gagarine]\nseuil=99.4\nurgence=99.8\nnotifications=non\n')
            p.send_signal(signal.SIGHUP)
            vu = None
            for _ in range(100):
                evs = [json.loads(l) for l in journal.read_text().splitlines()]
                vu = next((e for e in evs if e['action'] == 'rechargement'), None)
                if vu:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(vu, 'pas de rechargement noté')
            self.assertEqual((vu['seuil'], vu['urgence']), (99.4, 99.8))
            self.assertIsNone(p.poll(), 'le démon doit survivre au SIGHUP')
        finally:
            p.terminate()
            p.wait(timeout=10)
            p.stderr.close()


class TestNotificationsEtFichiers(unittest.TestCase):
    def test_identite_de_l_application(self):
        for cmd in (sy.commandes_notification('T', 'X', True, False, [])[0],
                    sy.commandes_notification('T', 'X', False, True, [(1000, 'moi')])[0]):
            self.assertIn('--app-name=Gagarine', cmd)
            self.assertEqual(cmd[cmd.index('-i') + 1], 'io.github.mawena.Gagarine')
            self.assertEqual(cmd[cmd.index('-h') + 1], 'string:desktop-entry:io.github.mawena.Gagarine')
            self.assertEqual(cmd[-2:], ['T', 'X'])

    def test_boutons_gardes(self):
        cmd = sy.commandes_notification('T', 'X', True, False, [])[0]
        self.assertIn('arreter=Arrêter', cmd)

    def test_unite_systemd_recharge_sur_sighup(self):
        unite = (Path(__file__).resolve().parent.parent / 'systemd' / 'gagarine.service').read_text()
        self.assertIn('ExecReload=/bin/kill -HUP $MAINPID', unite)

    def test_lanceur_depuis_un_autre_dossier(self):
        r = subprocess.run([str(CHEMIN), 'version'], capture_output=True, text=True, cwd='/')
        self.assertEqual(r.stdout, 'gagarine 1.0.0\n')

    def test_aide_des_nouvelles_commandes(self):
        r = subprocess.run([str(CHEMIN), '-h'], capture_output=True, text=True)
        for mot in ('journal', 'reglages', '--ecrire', 'service', '--json'):
            self.assertIn(mot, r.stdout)

    def test_json_via_le_lanceur(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GAGARINE_ETAT=d, GAGARINE_CONFIG=os.devnull)
            for args in (['etat', '--json'], ['reglages', '--json'], ['journal', '--json']):
                r = subprocess.run([sys.executable, str(CHEMIN), *args], capture_output=True, text=True, env=env)
                self.assertEqual(r.returncode, 0, args)
                json.loads(r.stdout)
            r = subprocess.run([str(CHEMIN), 'reglages', '--ecrire', 'seuil=94'], capture_output=True, text=True, env=env)
            if os.geteuid() != 0:
                self.assertEqual(r.returncode, 1)


if __name__ == '__main__':
    unittest.main()
