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
        self.assertEqual(e['version'], '1.3.0')
        self.assertEqual(len(e['swap']), 2)
        self.assertTrue(e['swap'][0]['zram'])
        self.assertEqual(e['figes'][0]['pid'], 4242)
        self.assertEqual(len(e['plus_gros']), 10)
        self.assertEqual(e['file'], 4)
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
        self.assertEqual([t['position'] for t in f['travaux']], [1, 2, 3, 4, 5])
        self.assertEqual(f['travaux'][4]['source'], 'autre')
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
        self.assertTrue(prot and all(e['action'] in ('fige', 'repris', 'arrete', 'bridage-impossible', 'fuite') for e in prot))
        self.assertTrue(all(e['action'].startswith('travail') or e['action'] in ('limite', 'fuite-travail')
                            for e in d.filtrer_evenements(self.ev, 'file')))
        chrome = d.filtrer_evenements(self.ev, 'tous', 'CHROME')
        self.assertTrue(chrome and all('chrome' in json.dumps(e) for e in chrome))
        self.assertEqual(d.filtrer_evenements(self.ev, 'tous', 'introuvable-xyz'), [])
        self.assertTrue(d.filtrer_evenements(self.ev, 'protection', 'node arrêté'))   # plusieurs mots


class Historique120(unittest.TestCase):
    """Contrats de docs/conception-1.2.0.md : historique, ménage, applications, mémoire apprise."""

    def test_historique_24h(self):
        h = d.lire_historique(faux('historique', '--json', '--depuis', '24h'))
        self.assertEqual((h['depuis_s'], h['pas_s']), (86400, 60))
        self.assertEqual(len(h['points']), 1440)
        self.assertEqual([p['t'] for p in h['points']], sorted(p['t'] for p in h['points']))
        self.assertTrue(h['incidents'] and h['gros'] and h['travaux'])
        self.assertEqual(h['travaux'][0]['nom'], 'rendu vidéo')    # le plus récent d'abord

    def test_historique_7j_moyenne_par_10_min(self):
        h = d.lire_historique(faux('historique', '--json', '--depuis', '7j'))
        self.assertEqual(h['pas_s'], 600)
        self.assertEqual(len(h['points']), 1008)

    def test_historique_robuste(self):
        h = d.lire_historique(json.dumps({'points': [{'date': 'n', 'ram': 5}, {'date': '2026-11-10T10:00:00+0000', 'ram': 'x'},
                                                      {'date': '2026-11-10T10:00:00+0000', 'ram': 50}], 'depuis_s': -3}))
        self.assertEqual(len(h['points']), 1)
        self.assertIsNone(h['depuis_s'])
        self.assertEqual((h['incidents'], h['gros'], h['travaux']), ([], [], []))
        with self.assertRaises(d.ErreurDonnees):
            d.lire_historique('pas du json')

    def test_periodes_et_axe(self):
        self.assertEqual([c for c, _, _ in d.PERIODES], ['5min', '24h', '7j'])
        self.assertEqual(d.duree_periode('7j'), 7 * 86400)
        self.assertEqual(d.libelle_periode('24h'), '24 h')
        self.assertEqual(d.graduations_axe('24h')[0], (0.0, '-24 h'))
        self.assertEqual(d.graduations_axe('7j')[-1], (1.0, 'maintenant'))
        self.assertEqual(len(d.graduations_axe('7j')), 8)
        self.assertAlmostEqual(d.fraction_temps(500, 1000, 1000), 0.5)
        self.assertIsNone(d.fraction_temps(-500, 1000, 1000))
        self.assertIsNone(d.fraction_temps(1100, 1000, 1000))

    def test_resume(self):
        h = d.lire_historique(faux('historique', '--json', '--depuis', '24h'))
        texte = d.resume_historique(h)
        self.assertIn('Pic à', texte)
        self.assertIn('4 incidents', texte)
        self.assertIn('aucun incident', d.resume_historique(dict(h, incidents=[])))
        self.assertIn('Pas encore', d.resume_historique(dict(h, points=[])))
        self.assertIn('Pas encore', d.resume_historique(None))

    def test_travaux_passes(self):
        h = d.lire_historique(faux('historique', '--json', '--depuis', '7j'))
        titre, detail, ok = d.detail_travail_passe(h['travaux'][0])
        self.assertEqual(titre, 'rendu vidéo')
        self.assertIn('durée 12 min 34 s', detail)
        self.assertIn('pic 3,8 Go', detail)
        self.assertTrue(ok)
        titre, detail, ok = d.detail_travail_passe({'nom': 'tests', 'code': 3, 'duree': 60})
        self.assertFalse(ok)
        self.assertIn('échec (code 3)', detail)
        self.assertNotIn('None', detail)
        self.assertEqual(d.titre_gros({'nom': 'chrome', 'pic_mo': 4100, 'minutes': 90}),
                         ('chrome', 'pic de 4,0 Go · parmi les plus gros pendant 1 h 30 min'))

    def test_vide(self):
        h = d.lire_historique(faux('historique', '--json', '--depuis', '24h', mode='vide'))
        self.assertEqual(h['travaux'], [])

    def test_memoire_apprise(self):
        a = d.lire_appris(faux('file', 'appris', '--json'))
        self.assertEqual(len(a['travaux']), 4)
        self.assertEqual(a['travaux'][0]['nom'], 'rendu vidéo')    # le plus récent d'abord
        titre, detail, trop_bas = d.detail_appris(next(t for t in a['travaux'] if t['nom'] == 'tests voix'))
        self.assertTrue(trop_bas)
        self.assertIn('pic habituel 9 Go', detail)
        self.assertIn('annoncé 1 Go', detail)
        self.assertIn('2 exécutions', detail)
        self.assertFalse(d.detail_appris({'nom': 'a', 'pic_go': 4, 'annonce_go': 4})[2])
        self.assertFalse(d.detail_appris({'nom': 'a'})[2])   # champs absents : pas d'erreur
        self.assertEqual(d.lire_appris(faux('file', 'appris', '--json', mode='vide'))['travaux'], [])

    def test_menage(self):
        m = d.lire_menage(faux('menage', '--json'))
        self.assertTrue(m['auto'])
        self.assertEqual([c['id'] for c in m['candidats']], ['ollama:llama3.1:8b', '5551', '6001'])   # les plus gros d'abord
        self.assertEqual(d.total_menage(m), (3, 9110))
        self.assertEqual(d.libelle_action_menage(m['candidats'][0]), 'Décharger')
        self.assertEqual(d.libelle_action_menage(m['candidats'][1]), 'Arrêter')
        self.assertEqual(d.libelle_action_menage({'type': 'lmstudio'}), 'Décharger')
        detail = d.detail_menage(m['candidats'][1])
        self.assertIn('Démon Gradle', detail)
        self.assertIn('PID 5551', detail)
        self.assertIn('3,0 Go', detail)
        self.assertIn('inactif depuis 44 min', detail)
        self.assertIn('jamais automatique', d.detail_menage(m['candidats'][2]))
        self.assertIn('30 min', d.etat_menage_auto(m))
        self.assertIn('désactivé', d.etat_menage_auto({'auto': False}))
        self.assertEqual(d.lire_menage(faux('menage', '--json', mode='vide'))['candidats'], [])
        self.assertEqual(d.lire_menage('{"candidats": [{"nom": "sans id"}, 3]}')['candidats'], [])

    def test_menage_commande(self):
        self.assertEqual(d.arguments_menage(['g'], {'id': 'ollama:x:7b'}), ['g', 'menage', 'arreter', 'ollama:x:7b'])
        self.assertEqual(d.arguments_menage(['g'], {'id': 5551}), ['g', 'menage', 'arreter', '5551'])

    def test_applications(self):
        a = d.lire_applications(faux('applications', '--json'))['applications']
        self.assertEqual([x['id'] for x in a], ['google-chrome', 'code', 'org.telegram.desktop'])
        self.assertEqual(d.libelle_plafond(a[0]), 'plafond 4 Go')
        self.assertEqual(d.libelle_plafond(a[1]), 'aucun plafond')
        self.assertEqual(d.detail_application(a[0]), '2 instances ouvertes · 2,8 Go')
        self.assertEqual(d.detail_application(a[1]), '1 instance ouverte · 1,1 Go')
        self.assertEqual(d.detail_application(a[2]), 'fermée')
        self.assertEqual(d.lire_applications(faux('applications', '--json', mode='vide'))['applications'], [])

    def test_plafond_commande(self):
        self.assertEqual(d.arguments_plafond(['g'], 'code', 3.5), ['g', 'applications', 'plafond', 'code', '3.5'])
        self.assertEqual(d.arguments_plafond(['g'], 'code', 4.0), ['g', 'applications', 'plafond', 'code', '4'])
        self.assertEqual(d.arguments_plafond(['g'], 'code', None), ['g', 'applications', 'plafond', 'code', 'aucun'])
        self.assertEqual(d.arguments_plafond(['g'], 'code', 0), ['g', 'applications', 'plafond', 'code', 'aucun'])

    def test_cœur_ancien(self):
        """Avec un cœur 1.1, les commandes de la 1.2.0 échouent (code 2) : l'interface masque les sections."""
        for args in (('menage', '--json'), ('applications', '--json'), ('historique', '--json'), ('file', 'appris', '--json')):
            env = dict(os.environ, FAUX_MODE='ancien')
            r = subprocess.run([sys.executable, str(FAUX), *args], capture_output=True, text=True, env=env, timeout=20)
            self.assertEqual(r.returncode, 2, args)


class Reglages120(unittest.TestCase):
    def test_nouvelles_cles_du_faux(self):
        r = d.lire_reglages(faux('reglages', '--json'))['reglages']
        for cle in d.DEFAUTS_120:
            self.assertIn(cle, r)

    def test_valeurs_par_defaut_pour_un_service_plus_ancien(self):
        complet = d.reglages_complets({'seuil': 95, 'anticipation': 0})
        self.assertEqual(complet['anticipation'], 0)      # 0 est une vraie valeur, pas une absence
        self.assertEqual(complet['pression'], 20)
        self.assertTrue(complet['menage_auto'])
        self.assertEqual(complet['ntfy'], '')
        # un enregistrement sans modification ne réécrit pas les clés que le service ne connaît pas encore
        self.assertIsNone(d.arguments_reglages(['g'], complet, dict(d.DEFAUTS_120, **d.DEFAUTS_130, anticipation=0)))

    def test_enregistrement_des_seules_cles_modifiees(self):
        courants = d.reglages_complets({})
        nouveaux = dict(courants, anticipation=30, notifications_fin=False, ntfy='gagarine-test', menage_delai=45,
                        claude_bloquer=False)
        self.assertEqual(d.arguments_reglages(['pkexec', 'g'], courants, nouveaux),
                         ['pkexec', 'g', 'reglages', '--ecrire', 'anticipation=30', 'notifications_fin=non',
                          'ntfy=gagarine-test', 'menage_delai=45', 'claude_bloquer=non'])

    def test_definitions_completes(self):
        cles = ([c[0] for c in d.REGLAGES_ANTICIPATION + d.REGLAGES_NOTIFICATIONS + d.REGLAGES_MENAGE + d.REGLAGES_FILE_130 +
                 d.REGLAGES_FUITES] + list(d.REGLAGES_BOOLEENS) + ['ntfy'])
        self.assertEqual(sorted(set(cles) - {'notifications'}), sorted({**d.DEFAUTS_120, **d.DEFAUTS_130}))
        for _, _, aide, _, mini, maxi in (d.REGLAGES_ANTICIPATION + d.REGLAGES_NOTIFICATIONS + d.REGLAGES_MENAGE +
                                          d.REGLAGES_FILE_130 + d.REGLAGES_FUITES):
            self.assertTrue(aide.endswith('.'))
            self.assertLess(mini, maxi)

    def test_sujet_ntfy(self):
        self.assertIsNone(d.verifier_reglages({'ntfy': 'gagarine-mawena'}))
        self.assertIsNone(d.verifier_reglages({'ntfy': 'https://ntfy.sh/mon-sujet'}))
        self.assertIn('ntfy', d.verifier_reglages({'ntfy': 'deux mots'}))

    def test_essai_ntfy(self):
        self.assertEqual(d.commande_essai_ntfy(['g'], ' sujet '), ['g', 'ntfy', 'essai', 'sujet'])
        self.assertIn('essai envoyé', faux('ntfy', 'essai', 'sujet'))


class Interface130(unittest.TestCase):
    def test_lecture_file_et_etat(self):
        f = d.lire_file(faux('file', '--etat', '--json'))
        self.assertEqual((f['paralleles'], f['en_cours']), (3, 2))
        self.assertEqual(d.entete_file(f), '2 en cours sur 3')
        ancien = d.lire_file(faux('file', '--etat', '--json', mode='v12'))
        self.assertEqual((ancien['paralleles'], ancien['en_cours']), (None, None))
        self.assertIsNone(d.entete_file(ancien))
        self.assertEqual([x['pid'] for x in d.lire_etat(faux('etat', '--json'))['fuites']], [6001, 7301])
        self.assertEqual(d.lire_etat(faux('etat', '--json', mode='v12'))['fuites'], [])
        self.assertEqual(d.lire_etat('{"fuites": [{"nom": "sans pid"}, 3]}')['fuites'], [])

    def test_detail_travail_et_motif(self):
        t = {'etat': 'en attente', 'ram': 6, 'besoin_go': 6, 'coeurs': 8, 'depuis': None, 'motif': '2 travaux en cours'}
        self.assertEqual(d.detail_travail_file(t), 'En attente · 6 Go de RAM · 8 cœurs · —\nAttend : 2 travaux en cours')
        self.assertNotIn('Attend', d.detail_travail_file(dict(t, etat='en cours')))
        self.assertIn('4 Go de RAM', d.detail_travail_file({'etat': 'en attente', 'besoin_go': 4}))   # besoin sans `ram`
        self.assertNotIn('None', d.detail_travail_file({'etat': 'en attente'}))

    def test_priorites(self):
        self.assertEqual([c for c, _ in d.PRIORITES], ['urgent', 'normal', 'fond'])
        self.assertEqual(d.libelle_priorite('fond'), 'En fond')
        self.assertIsNone(d.libelle_priorite(None))
        self.assertIsNone(d.libelle_priorite('inconnue'))
        self.assertTrue(d.peut_changer_priorite({'etat': 'en attente', 'ticket': 'a.json'}))
        self.assertFalse(d.peut_changer_priorite({'etat': 'en cours', 'ticket': 'a.json'}))
        self.assertEqual(d.arguments_priorite(['g'], 'a.json', 'urgent'), ['g', 'file', 'priorite', 'a.json', 'urgent'])
        with self.assertRaises(ValueError):
            d.arguments_priorite(['g'], 'a.json', '--force')
        self.assertIn('Priorité', faux('file', 'priorite', 'a.json', 'fond'))
        self.assertEqual(subprocess.run([sys.executable, str(FAUX), 'file', 'priorite', 'a', 'fond'], env=dict(os.environ, FAUX_MODE='v12'),
                                        capture_output=True).returncode, 2)

    def test_arguments_travail_avec_priorite(self):
        self.assertEqual(d.arguments_travail('x', 2, 1, 'ls', base=['g'], priorite='urgent'),
                         ['g', 'file', '--priorite', 'urgent', 'x', '2', '1', '--', 'sh', '-c', 'ls'])
        self.assertEqual(d.arguments_travail('x', 2, 1, 'ls', base=['g'], priorite='fond')[2:5], ['--priorite', 'fond', 'x'])
        # normal : rien d'ajouté, pour qu'un cœur 1.2.x ne refuse pas la ligne
        self.assertNotIn('--priorite', d.arguments_travail('x', 2, 1, 'ls', base=['g'], priorite='normal'))

    def test_fuites(self):
        fuites = d.fuites_par_pid(d.lire_etat(faux('etat', '--json'))['fuites'])
        self.assertEqual(d.detail_fuite(fuites[6001], maintenant=d.en_timestamp(fuites[6001]['depuis']) + 420),
                         ('node', 'PID 6001 · +210 Mo/min · depuis 7 min'))
        self.assertEqual(d.badge_fuite(fuites[6001]), 'fuite +210 Mo/min')
        self.assertEqual(d.badge_fuite({'pid': 1}), 'fuite')
        self.assertEqual(d.formater_pente(1800), '1,8 Go/min')
        self.assertEqual(d.fuites_par_pid(None), {})

    def test_tendance(self):
        self.assertEqual(d.tendance_travail({'tendance': 'hausse'}), ('hausse', 'pic en hausse : fuite probable'))
        self.assertEqual(d.tendance_travail({'tendance': 'limite'}), ('limite', 'dépasse sa limite'))
        for t in ({'tendance': 'stable'}, {}, {'tendance': 'inconnue'}):
            self.assertEqual(d.tendance_travail(t)[0], None)
        appris = d.lire_appris(faux('file', 'appris', '--json'))['travaux']
        self.assertEqual({t['nom']: t.get('tendance') for t in appris}['tests voix'], 'hausse')

    def test_evenements_fuite(self):
        icone, titre, detail = d.decrire_evenement({'action': 'fuite', 'nom': 'python3', 'pid': 7, 'pente_mo_min': 210,
                                                    'memoire': 2600 * 2 ** 20, 'prevision_s': 720, 'commande': 'python3 x.py'})
        self.assertEqual(icone, 'go-up-symbolic')
        self.assertIn('python3', titre)
        self.assertIn('+210 Mo/min', detail)
        self.assertIn('RAM pleine dans ~12 min', detail)
        self.assertIn('2,5 Go de mémoire', detail)
        self.assertIn('processus 7', detail)
        _, _, detail = d.decrire_evenement({'action': 'fuite', 'nom': 'x', 'pente_mo_min': 150, 'prevision_s': None})
        self.assertIn('la RAM ne serait pas pleine à ce rythme', detail)
        _, _, detail = d.decrire_evenement({'action': 'fuite', 'nom': 'x', 'pente_mo_min': 150, 'prevision_s': 30})
        self.assertIn('moins d’une minute', detail)
        _, titre, detail = d.decrire_evenement({'action': 'fuite-travail', 'nom': 'tests voix', 'tendance': 'hausse',
                                                'pics_go': [2.1, 3.4, 5.0]})
        self.assertEqual(titre, 'Travail « tests voix » : fuite probable')
        self.assertIn('pic en hausse à chaque lancement', detail)
        self.assertIn('pics : 2,1 → 3,4 → 5 Go', detail)
        _, _, detail = d.decrire_evenement({'action': 'fuite-travail', 'nom': 'x', 'tendance': 'limite'})
        self.assertIn('limite', detail)
        self.assertNotIn('None', detail)
        _, _, detail = d.decrire_evenement({'action': 'fuite-travail', 'nom': 'x'})      # champs absents : rien d'inventé
        self.assertNotIn('None', detail)
        self.assertEqual(d.categorie_evenement({'action': 'fuite'}), 'protection')
        self.assertEqual(d.categorie_evenement({'action': 'fuite-travail'}), 'file')

    def test_journal_du_faux_et_cœur_ancien(self):
        ev = d.lire_journal(faux('journal', '--json', '-n', '200'))
        self.assertEqual(sum(1 for e in ev if e['action'] == 'fuite'), 2)
        self.assertEqual(len(d.filtrer_evenements(ev, 'file', 'fuite probable')), 2)
        self.assertEqual(len(d.filtrer_evenements(ev, 'tous', 'fuite')), 4)
        ancien = d.lire_journal(faux('journal', '--json', '-n', '200', mode='v12'))
        self.assertFalse([e for e in ancien if e['action'].startswith('fuite')])

    def test_reglages_130(self):
        r = d.lire_reglages(faux('reglages', '--json'))['reglages']
        for cle in d.DEFAUTS_130:
            self.assertIn(cle, r)
        self.assertNotIn('file_paralleles', d.lire_reglages(faux('reglages', '--json', mode='v12'))['reglages'])
        complet = d.reglages_complets({'seuil': 95, 'fuite_seuil': 0})
        self.assertEqual((complet['file_paralleles'], complet['fuite_seuil'], complet['fuite_notifier']), (2, 0, True))
        nouveaux = dict(complet, file_paralleles=4, fuite_seuil=250, fuite_notifier=False)
        self.assertEqual(sorted(d.arguments_reglages(['pkexec', 'g'], complet, nouveaux)[4:]),
                         ['file_paralleles=4', 'fuite_notifier=non', 'fuite_seuil=250'])
        self.assertEqual(d.REGLAGES_FILE_130[0][4:], (1, 6))


class EvenementsJournal120(unittest.TestCase):
    def test_menage(self):
        _, titre, detail = d.decrire_evenement({'action': 'menage', 'nom': 'Démon Gradle', 'type': 'gradle', 'pid': 5551,
                                                'memoire_mo': 3100, 'auto': True})
        self.assertEqual(titre, '« Démon Gradle » arrêté par le ménage')
        self.assertIn('ménage automatique', detail)
        self.assertIn('3,0 Go libérés', detail)
        self.assertIn('processus 5551', detail)
        icone, titre, detail = d.decrire_evenement({'action': 'menage', 'nom': 'llama3', 'type': 'ollama', 'memoire_mo': 5400,
                                                    'auto': False})
        self.assertEqual(titre, '« llama3 » déchargé par le ménage')
        self.assertIn('à votre demande', detail)
        self.assertEqual(icone, 'user-trash-symbolic')
        self.assertEqual(d.categorie_evenement({'action': 'menage'}), 'menage')

    def test_lmstudio_arrete_151(self):
        icone, titre, detail = d.decrire_evenement({'action': 'lmstudio-arrete', 'nom': 'LM Studio', 'pid': 50,
                                                    'raison': 'démarré par un appel de Gagarine à lms',
                                                    'commande': 'lms ps --json', 'processus': 8})
        self.assertEqual(titre, 'LM Studio arrêté : démarré par erreur par le ménage')
        self.assertEqual(detail, 'démarré par « lms ps --json » · processus 50')
        self.assertEqual(icone, 'process-stop-symbolic')
        self.assertEqual(d.categorie_evenement({'action': 'lmstudio-arrete'}), 'menage')

    def test_declencheur(self):
        for decl, texte in (('seuil', 'seuil atteint'), ('anticipation', 'croissance trop rapide'),
                            ('pression', 'pression du noyau'), ('urgence', 'urgence'), ('futur', 'futur')):
            _, _, detail = d.decrire_evenement({'action': 'fige', 'nom': 'x', 'declencheur': decl})
            self.assertIn('déclencheur : ' + texte, detail)
        _, _, detail = d.decrire_evenement({'action': 'arrete', 'nom': 'x', 'declencheur': 'urgence'})
        self.assertIn('déclencheur : urgence', detail)
        _, _, detail = d.decrire_evenement({'action': 'fige', 'nom': 'x'})
        self.assertNotIn('déclencheur', detail)     # ancien événement : rien d'inventé

    def test_pic_et_ajustement(self):
        _, _, detail = d.decrire_evenement({'action': 'travail-fin', 'nom': 'x', 'code': 0, 'duree': 60, 'pic_go': 6.31})
        self.assertIn('pic de mémoire : 6,3 Go', detail)
        _, _, detail = d.decrire_evenement({'action': 'limite', 'nom': 'x', 'declare': 1, 'limite': 3, 'pic_go': 3.2})
        self.assertIn('pic de mémoire : 3,2 Go', detail)
        _, _, detail = d.decrire_evenement({'action': 'travail-debut', 'nom': 'x', 'ajuste': True, 'annonce_initiale': 1,
                                            'declare': 11})
        self.assertIn('annonce corrigée : 1 Go → 11 Go', detail)
        _, _, detail = d.decrire_evenement({'action': 'travail-debut', 'nom': 'x', 'ajuste': True, 'annonce_initiale': 1})
        self.assertIn('annoncé au départ : 1 Go', detail)
        _, _, detail = d.decrire_evenement({'action': 'travail-debut', 'nom': 'x', 'ajuste': True})
        self.assertIn('annonce corrigée', detail)
        _, _, detail = d.decrire_evenement({'action': 'travail-debut', 'nom': 'x'})
        self.assertNotIn('annonce', detail)

    def test_rechargement(self):
        _, _, detail = d.decrire_evenement({'action': 'rechargement', 'seuil': 95, 'anticipation': 0, 'pression': 20})
        self.assertIn('anticipation désactivée', detail)
        self.assertIn('pression 20 %', detail)

    def test_filtre_menage_et_journal_du_faux(self):
        ev = d.lire_journal(faux('journal', '--json', '-n', '200'))
        menage = d.filtrer_evenements(ev, 'menage')
        self.assertEqual(len(menage), 2)
        self.assertTrue(all(e['action'] == 'menage' for e in menage))
        for e in ev:
            _, titre, detail = d.decrire_evenement(e)
            self.assertNotIn('None', titre + detail)
        self.assertIn('menage', [c for c, _ in d.CATEGORIES])


class PicNul(unittest.TestCase):
    """Un travail trop court pour qu'un pic ait été relevé (pic_go = 0) ne montre pas « pic 0 Go » (intégration réelle)."""

    def test_pic_nul_masque(self):
        self.assertNotIn('pic', d.decrire_evenement({'action': 'travail-fin', 'nom': 'x', 'code': 0, 'duree': 1, 'pic_go': 0.0})[2])
        self.assertNotIn('pic', d.detail_travail_passe({'nom': 'x', 'date': '2026-11-10T10:00:00+0000', 'code': 0, 'pic_go': 0.0})[1])
        self.assertNotIn('pic', d.detail_appris({'nom': 'x', 'pic_go': 0.0, 'annonce_go': 1})[1])

    def test_pic_reel_montre(self):
        self.assertIn('pic de mémoire : 0,6 Go', d.decrire_evenement({'action': 'travail-fin', 'nom': 'x', 'pic_go': 0.59})[2])


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


class PhraseEtat150(unittest.TestCase):
    """Phrase d'état en tête de l'Aperçu (1.5.0) : la première situation vraie l'emporte."""

    @staticmethod
    def etat(**kw):
        e = {'service': {'etat': 'active', 'actif_au_demarrage': True}, 'figes': [], 'fuites': [],
             'reglages': {'vigilance': 85, 'seuil': 95, 'urgence': 98}}
        e.update(kw)
        return e

    FUITES = [{'pid': 7301, 'nom': 'firefox', 'pente_mo_min': 130}, {'pid': 6001, 'nom': 'node', 'pente_mo_min': 210}]

    def test_service_arrete_l_emporte_sur_tout(self):
        n, titre, detail = d.phrase_etat(self.etat(service={'etat': 'inactive'}, figes=[{'nom': 'python3'}],
                                                   fuites=self.FUITES), 99, 100, 99)
        self.assertEqual((n, titre), ('alerte', 'Le service est arrêté : la RAM n’est pas protégée.'))
        self.assertIn('Démarrez le service', detail)
        self.assertEqual(d.phrase_etat(self.etat(service={'etat': 'failed'}), 40, 8000, 10)[0], 'alerte')

    def test_programme_fige(self):
        n, titre, detail = d.phrase_etat(self.etat(figes=[{'nom': 'python3', 'depuis': 1000}], fuites=self.FUITES),
                                         99, 100, 99, maintenant=1185)
        self.assertEqual((n, titre), ('alerte', 'python3 est figé : il attend votre décision.'))
        self.assertIn('depuis 3 min 5 s', detail)
        n, titre, _ = d.phrase_etat(self.etat(figes=[{'nom': 'a'}, {'nom': 'b'}]), 50, 8000, 10)
        self.assertEqual((n, titre), ('alerte', '2 programmes sont figés : ils attendent votre décision.'))

    def test_ram_au_seuil_puis_vigilance(self):
        n, titre, _ = d.phrase_etat(self.etat(fuites=self.FUITES), 96.0, 600, 99)
        self.assertEqual((n, titre), ('alerte', 'La RAM a atteint le seuil : 96,0 % utilisés.'))
        n, titre, detail = d.phrase_etat(self.etat(fuites=self.FUITES), 88.0, 2048, 99)
        self.assertEqual((n, titre), ('vigilance', 'La RAM approche du seuil : Gagarine mesure quatre fois par seconde.'))
        self.assertEqual(detail, 'Il reste 2,0 Go ; Gagarine interviendra à 95 %.')

    def test_seuils_du_service(self):
        e = self.etat(reglages={'vigilance': 70, 'seuil': 90})
        self.assertEqual(d.phrase_etat(e, 75, 3000, 10)[0], 'vigilance')
        self.assertIn('interviendra à 90 %', d.phrase_etat(e, 75, 3000, 10)[2])
        self.assertEqual(d.phrase_etat(e, 91, 1000, 10)[0], 'alerte')

    def test_fuite(self):
        n, titre, detail = d.phrase_etat(self.etat(fuites=self.FUITES), 60, 6000, 99)
        self.assertEqual((n, titre), ('vigilance', 'node grossit sans cesse (+210 Mo/min).'))
        self.assertEqual(detail, '1 autre programme grossit aussi ; Gagarine interviendra près du seuil.')
        n, titre, detail = d.phrase_etat(self.etat(fuites=[{'pid': 1, 'nom': 'node'}]), 60, 6000, 10)
        self.assertEqual(titre, 'node grossit sans cesse.')
        self.assertEqual(detail, 'Fuite de mémoire probable : Gagarine interviendra si la RAM approche du seuil.')
        detail = d.phrase_etat(self.etat(fuites=self.FUITES + [{'pid': 2, 'nom': 'x'}]), 60, 6000, 10)[2]
        self.assertTrue(detail.startswith('2 autres programmes grossissent aussi'))

    def test_processeur_sature(self):
        n, titre, detail = d.phrase_etat(self.etat(), 60, 6000, 94.4)
        self.assertEqual((n, titre), ('vigilance', 'Le processeur est saturé (94 %) : le poste peut ralentir.'))
        self.assertIn('n’agit pas sur le processeur', detail)

    def test_calme(self):
        self.assertEqual(d.phrase_etat(self.etat(), 67.6, 4915, 30),
                         ('calme', 'Le poste respire : 4,8 Go libres, processeur calme.', 'Gagarine mesure la RAM chaque seconde.'))
        self.assertIn('processeur occupé', d.phrase_etat(self.etat(), 67.6, 4915, 60)[1])
        self.assertEqual(d.phrase_etat(self.etat(), 67.6, 4915, None)[1], 'Le poste respire : 4,8 Go libres.')

    def test_sans_donnees_ou_cœur_ancien(self):
        self.assertEqual(d.phrase_etat(), ('inconnu', 'Lecture de l’état du poste…', ''))
        self.assertEqual(d.phrase_etat(None, 50, 4096, None), ('calme', 'Le poste respire : 4,0 Go libres.', ''))
        ancien = d.lire_etat(faux('etat', '--json', mode='v13'))      # cœur 1.3 : pas de processeur
        self.assertEqual(d.phrase_etat(ancien, 50, 4096, None)[1], 'python3 est figé : il attend votre décision.')
        ancien['figes'], ancien['fuites'] = [], []
        self.assertEqual(d.phrase_etat(ancien, 50, 4096, None)[0], 'calme')

    def test_phrases_courtes(self):
        """Titres et détails tiennent en une ligne d'environ 90 caractères."""
        cas = [self.etat(service={'etat': 'inactive'}), self.etat(figes=[{'nom': 'python3', 'depuis': 0}]),
               self.etat(figes=[{}, {}]), self.etat(fuites=self.FUITES)]
        phrases = [d.phrase_etat(e, 60, 6000, 10, maintenant=86000) for e in cas]
        phrases += [d.phrase_etat(self.etat(), p, 1000, c) for p, c in ((96, 10), (88, 10), (60, 95), (60, 10))]
        for _, titre, detail in phrases:
            self.assertLessEqual(len(titre), 90, titre)
            self.assertLessEqual(len(detail), 92, detail)


class Compteurs150(unittest.TestCase):
    """Compteurs vivants de la barre latérale et état du service (1.5.0)."""

    def test_ram(self):
        self.assertEqual(d.compteur_ram(67.6), ('67 %', None))
        self.assertEqual(d.compteur_ram(84.9, {'vigilance': 85}), ('84 %', None))     # arrondi vers le bas : pas de « 85 % » calme
        self.assertEqual(d.compteur_ram(85.0, {'vigilance': 85}), ('85 %', 'vigilance'))
        self.assertEqual(d.compteur_ram(96.2, {}), ('96 %', 'alerte'))
        self.assertEqual(d.compteur_ram(None), (None, None))

    def test_programmes_et_file(self):
        self.assertEqual(d.compteur_programmes({'figes': [{}]}), ('1 figé', 'alerte'))
        self.assertEqual(d.compteur_programmes({'figes': [{}, {}]}), ('2 figés', 'alerte'))
        self.assertEqual(d.compteur_programmes({'figes': []}), (None, None))
        self.assertEqual(d.compteur_programmes(None), (None, None))
        self.assertEqual(d.compteur_file({'en_cours': 2, 'travaux': []}), ('2 en cours', None))
        self.assertEqual(d.compteur_file({'en_cours': None, 'travaux': [{'etat': 'en cours'}, {'etat': 'en attente'}]}),
                         ('1 en cours', None))      # cœur 1.2 : compté dans les travaux
        self.assertEqual(d.compteur_file({'en_cours': 0, 'travaux': [{'etat': 'en attente'}]}), (None, None))
        self.assertEqual(d.compteur_file(None), (None, None))

    def test_du_faux(self):
        etat, file = d.lire_etat(faux('etat', '--json')), d.lire_file(faux('file', '--etat', '--json'))
        c = d.compteurs_navigation(67.6, etat, file)
        self.assertEqual(list(c), ['apercu', 'programmes', 'file', 'journal', 'reglages'])
        self.assertEqual(c['programmes'], ('1 figé', 'alerte'))
        self.assertEqual(c['file'], ('2 en cours', None))
        self.assertEqual(c['journal'], (None, None))
        self.assertEqual(d.compteur_file(d.lire_file(faux('file', '--etat', '--json', mode='v12'))), ('2 en cours', None))
        vide = d.compteurs_navigation(None, d.lire_etat(faux('etat', '--json', mode='vide')),
                                      d.lire_file(faux('file', '--etat', '--json', mode='vide')))
        self.assertEqual(set(vide.values()), {(None, None)})

    def test_etat_du_service(self):
        self.assertEqual(d.etat_service(None), ('eteint', 'État du service inconnu', '', False))
        actif = {'service': {'etat': 'active', 'actif_au_demarrage': True}}
        self.assertEqual(d.etat_service(actif), ('', 'Service actif', 'Lancé au démarrage du poste', False))
        actif['service']['actif_au_demarrage'] = None
        self.assertEqual(d.etat_service(actif)[2], '')
        self.assertEqual(d.etat_service({'service': {'etat': 'inactive'}}),
                         ('alerte', 'Service arrêté', 'La RAM n’est pas protégée.', True))


class Instruments150(unittest.TestCase):
    """Géométrie des instruments (1.5.0) : cœurs P et E, étiquettes de l'échelle."""

    def test_cœurs_hybrides(self):
        plan = d.disposition_coeurs(14, 500, ['P'] * 4 + ['E'] * 10, 2)
        barres = {i: (x, w) for i, x, w in plan['barres']}
        self.assertEqual([i for i, _, _ in plan['barres']], list(range(14)))      # les P d'abord
        self.assertEqual([g[0] for g in plan['groupes']], ['P', 'E'])
        self.assertEqual(len(plan['paires']), 2)                                   # 2 cœurs P à deux fils
        largeur_e = barres[4][1]
        for x0, x1 in plan['paires']:
            self.assertGreater(x1 - x0, 1.9 * largeur_e)                           # un cœur P est deux fois plus large
        self.assertAlmostEqual(barres[1][0] - sum(barres[0]), 2)                   # deux fils d'un cœur : collés
        self.assertGreater(barres[2][0] - sum(barres[1]), 2)                       # deux cœurs : séparés
        self.assertGreater(barres[4][0] - sum(barres[3]), barres[5][0] - sum(barres[4]))   # P et E : plus loin
        self.assertLessEqual(max(x + w for _, x, w in plan['barres']), 500 + 1e-6)

    def test_cœurs_p_sans_second_fil(self):
        plan = d.disposition_coeurs(6, 400, ['P', 'P', 'E', 'E', 'E', 'E'], None)
        barres = {i: w for i, _, w in plan['barres']}
        self.assertAlmostEqual(barres[0], 1.5 * barres[2])
        self.assertEqual(plan['paires'], [])

    def test_cœurs_egaux_sans_types(self):
        for types in (None, [], ['P', 'E']):      # absents, ou incohérents avec le nombre de cœurs
            plan = d.disposition_coeurs(8, 400, types, 2)
            self.assertEqual(len({round(w, 6) for _, _, w in plan['barres']}), 1)
            self.assertEqual(plan['groupes'], [])
        self.assertEqual(d.disposition_coeurs(4, 2000)['barres'][0][2], 36.0)     # pas de barres démesurées
        self.assertEqual(d.disposition_coeurs(0, 400), {'barres': [], 'groupes': [], 'paires': []})

    def test_enveloppe_haute(self):
        points = [(x * 0.5, 100 - (x % 3) * 10) for x in range(40)]      # 40 mesures sur 20 px, en dents de scie
        env = d.enveloppe_haute(points, 3)
        self.assertLessEqual(len(env), 9)
        self.assertEqual(env[0], points[0])
        self.assertEqual(env[-1], points[-1])
        self.assertIn(80, [y for _, y in env])                             # le plus haut de chaque tranche est gardé
        self.assertEqual(d.enveloppe_haute([(0, 1), (1, 2)]), [(0, 1), (1, 2)])

    def test_etiquettes_sans_chevauchement(self):
        self.assertEqual(d.placer_etiquettes([(100, 20), (300, 20)], 500), [90, 290])     # centrées
        g = d.placer_etiquettes([(425, 14), (475, 14), (490, 14)], 500, 4)               # 85, 95, 98 sur 500 px
        self.assertEqual(g[0], 418)
        self.assertGreaterEqual(g[2], g[1] + 14 + 4)
        self.assertLessEqual(g[2] + 14, 500)
        self.assertEqual(d.placer_etiquettes([(0, 10)], 100), [0])


if __name__ == '__main__':
    unittest.main()
