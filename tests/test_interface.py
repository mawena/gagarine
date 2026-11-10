"""Tests de la logique de l'interface sans GTK (gagarine/interface/donnees.py)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gagarine.interface import donnees as d

RACINE = Path(__file__).resolve().parent.parent
FAUX = RACINE / 'tests' / 'donnees_interface' / 'faux-gagarine'


def faux(*args, mode='normal'):
    env = dict(os.environ, FAUX_MODE=mode)
    return subprocess.run([sys.executable, str(FAUX), *args], capture_output=True, text=True, env=env, timeout=20).stdout


class Formats(unittest.TestCase):
    def test_tailles(self):
        self.assertEqual(d.formater_taille_mo(900), '900 Mo')
        self.assertEqual(d.formater_taille_mo(3620), '3,5 Go')
        self.assertEqual(d.formater_taille_mo(2 * 1024 * 1024), '2,0 To')
        self.assertEqual(d.formater_taille_mo(None), '—')
        self.assertEqual(d.formater_go(4), '4 Go')
        self.assertEqual(d.formater_go(3.6), '3,6 Go')

    def test_durees(self):
        self.assertEqual(d.formater_duree(45), '45 s')
        self.assertEqual(d.formater_duree(125), '2 min 5 s')
        self.assertEqual(d.formater_duree(120), '2 min')
        self.assertEqual(d.formater_duree(3 * 3600 + 12 * 60), '3 h 12 min')
        self.assertEqual(d.formater_duree(2 * 86400 + 4 * 3600), '2 j 4 h')
        self.assertEqual(d.formater_duree(-5), '0 s')

    def test_depuis_et_dates(self):
        self.assertEqual(d.depuis(1000, maintenant=1185), 'depuis 3 min 5 s')
        self.assertEqual(d.depuis(1000, maintenant=1002), 'à l’instant')
        self.assertEqual(d.en_timestamp('2026-11-10T14:30:00+0000'), d.en_timestamp('2026-11-10T14:30:00.000Z'))
        self.assertIsNone(d.en_timestamp('n’importe quoi'))
        self.assertEqual(d.en_timestamp(12.5), 12.5)

    def test_pourcent_et_pluriel(self):
        self.assertEqual(d.formater_pourcent(75.24), '75,2 %')
        self.assertEqual(d.pluriel(1, 'travail', 'travaux'), '1 travail')
        self.assertEqual(d.pluriel(3, 'travail', 'travaux'), '3 travaux')
        self.assertEqual(d.tronquer('a   b ' + 'x' * 200, 10), 'a b xxxxx…')


class Ram(unittest.TestCase):
    def test_meminfo(self):
        total, dispo, pct = d.lire_meminfo('MemTotal:  16384000 kB\nMemFree: 100 kB\nMemAvailable:  4096000 kB\n')
        self.assertAlmostEqual(total, 16000)
        self.assertAlmostEqual(dispo, 4000)
        self.assertAlmostEqual(pct, 75.0)

    def test_meminfo_illisible(self):
        with self.assertRaises(d.ErreurDonnees):
            d.lire_meminfo('rien')

    def test_niveaux(self):
        r = {'vigilance': 85, 'seuil': 95}
        self.assertEqual(d.niveau_ram(50, r), 'normal')
        self.assertEqual(d.niveau_ram(85, r), 'vigilance')
        self.assertEqual(d.niveau_ram(96, r), 'alerte')
        self.assertEqual(d.niveau_ram(10, r, figes=1), 'alerte')


class LectureJson(unittest.TestCase):
    def test_etat_du_faux(self):
        e = d.lire_etat(faux('etat', '--json'))
        self.assertEqual(e['version'], '1.1.0')
        self.assertEqual(len(e['swap']), 2)
        self.assertTrue(e['swap'][0]['zram'])
        self.assertEqual(e['figes'][0]['pid'], 4242)
        self.assertEqual(len(e['plus_gros']), 10)
        self.assertEqual(e['file'], 2)
        self.assertEqual(e['service']['etat'], 'active')

    def test_etat_minimal_ou_incomplet(self):
        e = d.lire_etat('{}')
        self.assertEqual(e['figes'], [])
        self.assertEqual(e['file'], 0)
        self.assertEqual(e['service']['etat'], 'inconnu')
        self.assertEqual(d.lire_etat('{"file": 3, "figes": "n’importe quoi"}')['figes'], [])

    def test_erreurs(self):
        for mauvais in ('', '   ', 'pas du json', '[1, 2]', faux('etat', mode='erreur')):
            with self.assertRaises(d.ErreurDonnees):
                d.lire_etat(mauvais)

    def test_file_du_faux(self):
        f = d.lire_file(faux('file', '--etat', '--json'))
        self.assertEqual([t['position'] for t in f['travaux']], [1, 2])
        self.assertEqual(f['travaux'][1]['source'], 'autre')
        self.assertEqual(d.lire_file(faux('file', '--etat', '--json', mode='vide'))['travaux'], [])

    def test_journal_du_faux(self):
        ev = d.lire_journal(faux('journal', '--json', '-n', '200'))
        self.assertGreaterEqual(len(ev), 20)
        dates = [d.en_timestamp(e['date']) for e in ev]
        self.assertEqual(dates, sorted(dates, reverse=True))   # plus récent en premier
        self.assertEqual(len(d.lire_journal(faux('journal', '--json', '-n', '5'))), 5)

    def test_reglages(self):
        r = d.lire_reglages(faux('reglages', '--json'))
        self.assertEqual(r['reglages']['seuil'], 95)


class Evenements(unittest.TestCase):
    def setUp(self):
        self.ev = d.lire_journal(faux('journal', '--json', '-n', '200'))

    def test_chaque_action_a_un_libelle(self):
        for e in self.ev:
            icone, titre, detail = d.decrire_evenement(e)
            self.assertTrue(icone.endswith('-symbolic'))
            self.assertTrue(titre)
            self.assertNotIn('None', titre + detail)

    def test_libelles_precis(self):
        _, titre, detail = d.decrire_evenement({'action': 'fige', 'nom': 'node', 'pid': 7, 'memoire': 4200 * 2 ** 20, 'ram': 95.9})
        self.assertEqual(titre, '« node » figé')
        self.assertIn('4,1 Go de mémoire', detail)
        self.assertIn('processus 7', detail)
        _, titre, detail = d.decrire_evenement({'action': 'travail-fin', 'nom': 'x', 'code': 1, 'duree': 125})
        self.assertIn('code 1', titre)
        self.assertIn('2 min 5 s', detail)
        _, titre, detail = d.decrire_evenement({'action': 'limite', 'nom': 'tests', 'declare': 0.1, 'limite': 2.1,
                                                'commande': 'pytest'})
        self.assertEqual(titre, '« tests » arrêté : limite de mémoire dépassée')
        self.assertIn('annoncé : 0,1 Go', detail)
        self.assertIn('limite : 2,1 Go', detail)
        _, titre, detail = d.decrire_evenement({'action': 'rechargement', 'pid': 5, 'nom': 'gagarine', 'seuil': 95,
                                                'urgence': 98})
        self.assertEqual(titre, 'Réglages rechargés')
        self.assertIn('seuil 95 %', detail)
        _, titre, _ = d.decrire_evenement({'action': 'inconnue-demain', 'nom': 'y'})
        self.assertIn('y', titre)   # une action future ne plante pas

    def test_filtre(self):
        self.assertEqual(len(d.filtrer_evenements(self.ev)), len(self.ev))
        prot = d.filtrer_evenements(self.ev, 'protection')
        self.assertTrue(prot and all(e['action'] in ('fige', 'repris', 'arrete', 'bridage-impossible') for e in prot))
        self.assertTrue(all(e['action'].startswith('travail') or e['action'] == 'limite'
                            for e in d.filtrer_evenements(self.ev, 'file')))
        chrome = d.filtrer_evenements(self.ev, 'tous', 'CHROME')
        self.assertTrue(chrome and all('chrome' in json.dumps(e) for e in chrome))
        self.assertEqual(d.filtrer_evenements(self.ev, 'tous', 'introuvable-xyz'), [])
        self.assertTrue(d.filtrer_evenements(self.ev, 'protection', 'node arrêté'))   # plusieurs mots


class Travail(unittest.TestCase):
    def test_arguments(self):
        a = d.arguments_travail('rendu', 4, 2, 'make -j4 && echo ok', base=['gagarine'])
        self.assertEqual(a, ['gagarine', 'file', 'rendu', '4', '2', '--', 'sh', '-c', 'make -j4 && echo ok'])
        self.assertEqual(d.arguments_travail('x', 1.5, 1, 'c', base=['g'])[3], '1.5')

    def test_validation(self):
        self.assertIsNone(d.valider_travail('a', 2, 2, 'ls'))
        self.assertIn('nom', d.valider_travail(' ', 2, 2, 'ls'))
        self.assertIn('commande', d.valider_travail('a', 2, 2, ''))
        self.assertIn('RAM', d.valider_travail('a', 0, 2, 'ls'))
        self.assertIn('cœurs', d.valider_travail('a', 2, -1, 'ls'))

    def test_fichier_sortie(self):
        f = d.fichier_sortie('/x/travaux', 'Rendu vidéo n°1 !', maintenant=1762785000)
        self.assertTrue(f.startswith('/x/travaux/2025') or f.startswith('/x/travaux/2026'))
        self.assertTrue(f.endswith('-Rendu-vidéo-n-1.log'))
        self.assertEqual(d.nom_fichier_sur('///'), 'travail')
        self.assertEqual(d.dossier_sorties({'XDG_STATE_HOME': '/s'}), '/s/gagarine/travaux')


class Commandes(unittest.TestCase):
    def test_commande_gagarine(self):
        self.assertEqual(d.commande_gagarine({}), ['gagarine'])
        self.assertEqual(d.commande_gagarine({'GAGARINE_COMMANDE': '/a b/faux --x'}), ['/a', 'b/faux', '--x'])
        self.assertEqual(d.commande_gagarine({'GAGARINE_COMMANDE': '"/a b/faux"'}), ['/a b/faux'])

    def test_admin_passe_par_pkexec_sauf_en_test(self):
        self.assertEqual(d.commande_admin({}), ['pkexec', '/usr/bin/gagarine'])
        self.assertEqual(d.commande_admin({'GAGARINE_COMMANDE': '/tmp/faux'}), ['/tmp/faux'])

    def test_messages_d_erreur(self):
        self.assertIsNone(d.message_erreur_commande(['gagarine', 'etat'], 0, ''))
        self.assertIn('introuvable', d.message_erreur_commande(['gagarine'], None, '', absente=True))
        self.assertIn('trop de temps', d.message_erreur_commande(['gagarine', 'etat'], None, '', delai=True))
        self.assertIn('refusée', d.message_erreur_commande(['pkexec', '/usr/bin/gagarine'], 126, ''))
        self.assertEqual(d.message_erreur_commande(['gagarine', 'x'], 1, '\nréservé à root\nsuite'), 'réservé à root')
        self.assertIn('code 3', d.message_erreur_commande(['gagarine', 'x'], 3, ''))


class Reglages(unittest.TestCase):
    def test_arguments_seulement_les_changements(self):
        courants = {'seuil': 95, 'notifications': True, 'proteges': ''}
        a = d.arguments_reglages(['pkexec', 'g'], courants, {'seuil': 90, 'notifications': True, 'proteges': ''})
        self.assertEqual(a, ['pkexec', 'g', 'reglages', '--ecrire', 'seuil=90'])
        a = d.arguments_reglages(['g'], courants, {'seuil': 95, 'notifications': False, 'proteges': 'a|b'})
        self.assertEqual(a, ['g', 'reglages', '--ecrire', 'notifications=non', 'proteges=a|b'])
        self.assertIsNone(d.arguments_reglages(['g'], courants, dict(courants)))

    def test_coherence_des_seuils(self):
        self.assertIsNone(d.verifier_reglages({'vigilance': 85, 'seuil': 95, 'urgence': 98}))
        self.assertIsNone(d.verifier_reglages({'vigilance': 85, 'seuil': 95, 'urgence': 95}))
        self.assertIn('vigilance', d.verifier_reglages({'vigilance': 96, 'seuil': 95, 'urgence': 98}))
        self.assertIn('urgence', d.verifier_reglages({'vigilance': 85, 'seuil': 99, 'urgence': 98}))
        self.assertIsNone(d.verifier_reglages({}))


class Indicateur(unittest.TestCase):
    def test_autostart(self):
        with tempfile.TemporaryDirectory() as t:
            chemin = d.fichier_autostart_indicateur({'XDG_CONFIG_HOME': t})
            self.assertTrue(chemin.endswith('autostart/io.github.mawena.Gagarine.Indicateur.desktop'))
            self.assertTrue(d.indicateur_affiche(chemin))   # pas de fichier : affiché
            os.makedirs(os.path.dirname(chemin))
            Path(chemin).write_text(d.contenu_autostart_indicateur(False))
            self.assertFalse(d.indicateur_affiche(chemin))
            Path(chemin).write_text(d.contenu_autostart_indicateur(True))
            self.assertTrue(d.indicateur_affiche(chemin))
        self.assertIn('Hidden=true', d.contenu_autostart_indicateur(False))


class Essai(unittest.TestCase):
    def test_verdicts(self):
        self.assertTrue(d.verdict_essai('tout va bien', 0)[0])
        ok, texte = d.verdict_essai('RAM déjà utilisée à 85 % : essai impossible sans risque', 1)
        self.assertFalse(ok)
        self.assertIn('trop utilisée', texte)
        ok, texte = d.verdict_essai('a\nÉCHEC : pas figé\n', 1)
        self.assertFalse(ok)
        self.assertIn('pas figé', texte)

    def test_swap(self):
        self.assertEqual(d.swap_resume([{'utilise_mo': 1, 'taille_mo': 10}, {'utilise_mo': 2, 'taille_mo': 5}]), (3, 15))
        self.assertIn('zram', d.nom_swap({'zram': True}))


if __name__ == '__main__':
    unittest.main()
