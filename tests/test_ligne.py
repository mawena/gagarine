"""Tests de `gagarine ligne-etat` : parties, couleurs, --avec, entrées invalides, fichiers d'état abîmés, rapidité."""
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gagarine import ligne as li

MOI = os.getpid()


def entree(texte):
    return SimpleNamespace(buffer=io.BytesIO(texte.encode()), isatty=lambda: False, read=lambda: texte)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.racine = Path(self.tmp.name)
        self.etat = self.racine / 'etat'
        self.tickets = self.racine / 'tickets'
        self.etat.mkdir()
        self.tickets.mkdir()
        self.env = {'GAGARINE_ETAT': str(self.etat), 'FILE_ATTENTE_DOSSIER': str(self.tickets), 'HOME': str(self.racine),
                    'PATH': os.environ.get('PATH', '')}
        self.ram = 50.0
        self.execution = self.racine / 'run'
        self.execution.mkdir()
        self.env['GAGARINE_DOSSIER_EXECUTION'] = str(self.execution)
        p = mock.patch.object(li, 'lire_ram', lambda: self.ram)
        p.start()
        self.addCleanup(p.stop)

    def ticket(self, nom, **champs):
        from gagarine.systeme import demarrage
        t = {'nom': nom, 'pid': MOI, 'demarrage': demarrage(MOI), **champs}
        (self.tickets / f'{nom}.json').write_text(json.dumps(t))

    def evenement(self, nom, age_s=60, pid=None, action='fuite'):
        e = {'date': time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(time.time() - age_s)), 'action': action,
             'nom': nom}
        if pid is not None:
            e['pid'] = pid
        with open(self.etat / 'evenements.jsonl', 'a') as f:
            f.write(json.dumps(e) + '\n')

    def partie(self, couleurs=False):
        return li.partie_gagarine(self.env, couleurs)

    def lancer(self, args=(), texte='{}', env=None):
        sortie = io.StringIO()
        ctx = SimpleNamespace(env=env or self.env, config=None)
        with mock.patch.object(sys, 'stdin', entree(texte)), mock.patch.object(sys, 'stdout', sortie):
            code = li.commande_ligne_etat(list(args), ctx)
        return code, sortie.getvalue()


class TestParties(Base):
    def test_ram_seule(self):
        self.ram = 72.4
        self.assertEqual(self.partie(), 'RAM 72 %')

    def test_ram_illisible(self):
        self.ram = None
        self.assertEqual(self.partie(), 'RAM ?')

    def test_file_et_en_cours(self):
        self.ticket('a', source='gagarine', etat='en cours')
        self.ticket('b', source='gagarine', etat='en attente')
        self.ticket('c', source='gagarine', etat='en cours')
        self.assertEqual(self.partie(), 'RAM 50 % · file 3 (2 en cours)')

    def test_ticket_ancien_outil_compte_comme_travail(self):
        self.ticket('vieux')
        self.assertEqual(self.partie(), 'RAM 50 % · file 1 (1 en cours)')

    def test_ticket_d_un_processus_mort_ignore(self):
        (self.tickets / 'mort.json').write_text(json.dumps({'nom': 'mort', 'pid': 2 ** 22 + 5, 'demarrage': '1'}))
        self.assertEqual(self.partie(), 'RAM 50 %')

    def test_figes(self):
        (self.etat / 'figes.json').write_text(json.dumps({str(MOI): {'debut': '1'}, '4194999': {'debut': '1'}}))
        self.assertEqual(self.partie(), 'RAM 50 % · 1 figé')
        (self.etat / 'figes.json').write_text(json.dumps({str(MOI): {}, '1': {}}))
        self.assertEqual(self.partie(), 'RAM 50 % · 2 figés')

    def test_fuite_du_journal(self):
        self.evenement('python3', 120)
        self.assertEqual(self.partie(), 'RAM 50 % · fuite : python3')

    def test_fuite_trop_ancienne(self):
        self.evenement('python3', 3600)
        self.assertEqual(self.partie(), 'RAM 50 %')

    def test_autres_evenements_ignores(self):
        self.evenement('python3', 10, action='fuite-travail')
        self.evenement('node', 10, action='figer')
        self.assertEqual(self.partie(), 'RAM 50 %')

    def test_fuite_dont_le_processus_a_disparu(self):
        self.evenement('python3', 10, pid=2 ** 22 + 7)
        self.assertEqual(self.partie(), 'RAM 50 %')
        self.evenement('node', 10, pid=MOI)
        self.assertEqual(self.partie(), 'RAM 50 % · fuite : node')

    def test_fuites_json_formes_tolerees(self):
        for contenu in ([{'nom': 'a', 'pid': MOI}], {'fuites': [{'nom': 'a'}]}, {str(MOI): {'nom': 'a'}}, ['a']):
            (self.etat / 'fuites.json').write_text(json.dumps(contenu))
            self.assertEqual(self.partie(), 'RAM 50 % · fuite : a', contenu)

    def test_fuites_json_abime_ou_absent(self):
        for contenu in ('', '{pas du json', '42', 'null', '[1, null, {"pid": "x"}]', '{"fuites": 3}'):
            (self.etat / 'fuites.json').write_text(contenu)
            self.assertEqual(self.partie(), 'RAM 50 %', contenu)

    def test_figes_json_abime(self):
        for contenu in ('', '[', '[1]', '{"abc": {}}'):
            (self.etat / 'figes.json').write_text(contenu)
            self.assertEqual(self.partie(), 'RAM 50 %', contenu)

    def test_fuites_sans_doublon_et_limite_de_noms(self):
        (self.etat / 'fuites.json').write_text(json.dumps([{'nom': 'a', 'pid': MOI}]))
        self.evenement('a', 5, pid=MOI)
        self.evenement('b', 5)
        self.evenement('c', 5)
        self.assertEqual(self.partie(), 'RAM 50 % · fuite : a, b +1')

    def test_journal_corrompu(self):
        (self.etat / 'evenements.jsonl').write_text('pas du json "fuite"\n[1]\n{"action": "fuite"}\n')
        self.assertEqual(self.partie(), 'RAM 50 %')

    def test_toutes_les_parties(self):
        self.ram = 72
        self.ticket('a', source='gagarine', etat='en cours')
        (self.etat / 'figes.json').write_text(json.dumps({str(MOI): {}}))
        self.evenement('python3')
        self.assertEqual(self.partie(), 'RAM 72 % · file 1 (1 en cours) · 1 figé · fuite : python3')

    def test_une_source_qui_plante_n_empeche_pas_les_autres(self):
        with mock.patch.object(li, 'compter_file', side_effect=RuntimeError), \
                mock.patch.object(li, 'compter_figes', side_effect=RuntimeError):
            self.evenement('x')
            self.assertEqual(self.partie(), 'RAM 50 % · fuite : x')


class TestProcesseur(Base):
    def mesure(self, age=1.0, **champs):
        d = {'t': time.time() - age, 'pourcent': 23.4, 'chaud': False, 'temperature': 60.0, **champs}
        (self.execution / 'processeur.json').write_text(json.dumps(d))

    def test_cpu_juste_apres_la_ram(self):
        self.ram = 77
        self.mesure()
        self.ticket('a', source='gagarine', etat='en cours')
        self.assertEqual(self.partie(), 'RAM 77 % · CPU 23 % · file 1 (1 en cours)')

    def test_absent_sans_fichier(self):
        self.assertEqual(self.partie(), 'RAM 50 %')

    def test_absent_si_trop_ancien(self):
        self.mesure(age=6.5)
        self.assertEqual(self.partie(), 'RAM 50 %')

    def test_present_a_la_limite_de_fraicheur(self):
        self.mesure(age=5.5)
        self.assertEqual(self.partie(), 'RAM 50 % · CPU 23 %')

    def test_absent_si_illisible_ou_mal_forme(self):
        (self.execution / 'processeur.json').write_text('{pas du json')
        self.assertEqual(self.partie(), 'RAM 50 %')
        for champs in ({'pourcent': None}, {'pourcent': 'x'}, {'t': 'hier'}, {'t': None}, {'pourcent': True}):
            self.mesure(**champs)
            self.assertEqual(self.partie(), 'RAM 50 %', champs)
        (self.execution / 'processeur.json').write_text('[1, 2]')
        self.assertEqual(self.partie(), 'RAM 50 %')

    def test_mesure_dans_le_futur_lointain_ignoree(self):
        self.mesure(age=-100)
        self.assertEqual(self.partie(), 'RAM 50 %')

    def test_couleurs(self):
        for pct, code in ((0, '32'), (69.4, '32'), (70, '33'), (89, '33'), (89.4, '33'), (90, '31'), (100, '31')):
            self.mesure(pourcent=pct)
            self.assertIn(f'\x1b[{code}mCPU {round(pct)} %\x1b[0m', self.partie(True), pct)

    def test_chaud_ajoute_la_temperature_en_rouge(self):
        self.mesure(pourcent=97.2, chaud=True, temperature=98.0)
        self.assertEqual(self.partie(), 'RAM 50 % · CPU 97 % 98 °C')
        self.assertIn('\x1b[31m98 °C\x1b[0m', self.partie(True))

    def test_pas_chaud_pas_de_temperature(self):
        self.mesure(chaud=False, temperature=98.0)
        self.assertNotIn('°C', self.partie())

    def test_chaud_sans_temperature(self):
        self.mesure(chaud=True, temperature=None)
        self.assertEqual(self.partie(), 'RAM 50 % · CPU 23 %')

    def test_dossier_d_execution_par_defaut_sans_variable(self):
        del self.env['GAGARINE_DOSSIER_EXECUTION']
        with mock.patch.object(li, 'DOSSIER_EXECUTION', str(self.execution)):
            self.mesure()
            self.assertIn('CPU 23 %', self.partie())

    def test_variable_prioritaire_sur_run(self):
        autre = self.racine / 'autre'
        autre.mkdir()
        (autre / 'processeur.json').write_text(json.dumps({'t': time.time(), 'pourcent': 5}))
        self.mesure(pourcent=80)
        with mock.patch.object(li, 'DOSSIER_EXECUTION', str(autre)):
            self.assertIn('CPU 80 %', self.partie())

    def test_aucune_mesure_directe(self):
        with mock.patch.object(li, 'lire_json', wraps=li.lire_json) as lire:
            self.partie()
        for appel in lire.call_args_list:
            self.assertNotIn('/proc/', str(appel))

    def test_une_erreur_du_cpu_n_empeche_pas_les_autres_parties(self):
        with mock.patch.object(li, 'lire_processeur', side_effect=RuntimeError):
            self.evenement('x')
            self.assertEqual(self.partie(), 'RAM 50 % · fuite : x')

    def test_toutes_les_parties_dans_l_ordre(self):
        self.ram = 72
        self.mesure(pourcent=40)
        self.ticket('a', source='gagarine', etat='en cours')
        (self.etat / 'figes.json').write_text(json.dumps({str(MOI): {}}))
        self.evenement('python3')
        self.assertEqual(self.partie(), 'RAM 72 % · CPU 40 % · file 1 (1 en cours) · 1 figé · fuite : python3')

    def test_commande_complete(self):
        self.mesure(pourcent=95)
        self.assertEqual(self.lancer(['--sans-couleur'])[1], 'RAM 50 % · CPU 95 %\n')


class TestCouleurs(Base):
    def test_niveaux(self):
        for ram, code in ((50, '32'), (85, '33'), (90, '33'), (95, '31'), (99, '31')):
            self.ram = ram
            self.assertEqual(self.partie(True), f'\x1b[{code}mRAM {ram} %\x1b[0m')

    def test_seuils_des_reglages(self):
        conf = self.racine / 'g.conf'
        conf.write_text('[gagarine]\nvigilance = 60\nseuil = 70\n')
        self.env['GAGARINE_CONFIG'] = str(conf)
        self.ram = 65
        self.assertEqual(self.partie(True), '\x1b[33mRAM 65 %\x1b[0m')
        self.ram = 75
        self.assertEqual(self.partie(True), '\x1b[31mRAM 75 %\x1b[0m')

    def test_autres_parties_colorees(self):
        self.ticket('a', source='gagarine', etat='en cours')
        self.evenement('p')
        p = self.partie(True)
        self.assertIn('\x1b[36mfile 1 (1 en cours)\x1b[0m', p)
        self.assertIn('\x1b[31mfuite : p\x1b[0m', p)

    def test_sans_couleur(self):
        self.assertNotIn('\x1b', self.lancer(['--sans-couleur'])[1])
        self.assertIn('\x1b', self.lancer()[1])

    def test_no_color(self):
        self.env['NO_COLOR'] = '1'
        self.assertNotIn('\x1b', self.lancer()[1])


class TestAvec(Base):
    def script(self, corps):
        chemin = self.racine / f's{len(os.listdir(self.racine))}.sh'
        chemin.write_text('#!/bin/sh\n' + corps)
        chemin.chmod(chemin.stat().st_mode | stat.S_IXUSR)
        return str(chemin)

    def test_sortie_gardee_et_partie_ajoutee_a_la_derniere_ligne(self):
        cmd = self.script('printf "ligne 1\\nligne 2 \\n"')
        code, sortie = self.lancer(['--sans-couleur', '--avec', cmd])
        self.assertEqual(code, 0)
        self.assertEqual(sortie, 'ligne 1\nligne 2 │ RAM 50 %\n')

    def test_ansi_de_l_autre_conserve(self):
        cmd = self.script('printf "\\033[1mgras\\033[0m"')
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', cmd])[1], '\x1b[1mgras\x1b[0m │ RAM 50 %\n')

    def test_entree_transmise_telle_quelle(self):
        cmd = self.script('cat')
        texte = '{"model":{"display_name":"Opus"}, "x": "é"}'
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', cmd], texte)[1], texte + ' │ RAM 50 %\n')

    def test_arguments_de_la_commande(self):
        cmd = self.script('echo "$1-$2"') + ' un "deux mots"'
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', cmd])[1], 'un-deux mots │ RAM 50 %\n')

    def test_avec_colle(self):
        cmd = self.script('echo ok')
        self.assertEqual(self.lancer(['--sans-couleur', f'--avec={cmd}'])[1], 'ok │ RAM 50 %\n')

    def test_commande_en_echec_code(self):
        cmd = self.script('echo parasite; exit 3')
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', cmd]), (0, 'RAM 50 %\n'))

    def test_commande_introuvable(self):
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', '/non/existant/x']), (0, 'RAM 50 %\n'))
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', '"guillemet']), (0, 'RAM 50 %\n'))

    def test_sortie_vide(self):
        cmd = self.script('true')
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', cmd]), (0, 'RAM 50 %\n'))

    def test_delai_depasse(self):
        cmd = self.script('echo lent; sleep 5')
        debut = time.monotonic()
        code, sortie = self.lancer(['--sans-couleur', '--avec', cmd])
        self.assertLess(time.monotonic() - debut, 2.5)
        self.assertEqual((code, sortie), (0, 'RAM 50 %\n'))

    def test_delai_depasse_avec_petit_fils_qui_garde_le_tube(self):
        cmd = self.script('sleep 5 & wait')
        debut = time.monotonic()
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', cmd])[1], 'RAM 50 %\n')
        self.assertLess(time.monotonic() - debut, 2.5)


class TestRobustesse(Base):
    def test_entree_vide(self):
        self.assertEqual(self.lancer(['--sans-couleur'], '')[0:2], (0, 'RAM 50 %\n'))

    def test_json_invalide_transmis_quand_meme(self):
        cmd = os.path.join(self.racine, 'c.sh')
        Path(cmd).write_text('#!/bin/sh\ncat\n')
        os.chmod(cmd, 0o755)
        self.assertEqual(self.lancer(['--sans-couleur', '--avec', cmd], '{pas du json')[1], '{pas du json │ RAM 50 %\n')

    def test_terminal_sans_entree(self):
        sortie = io.StringIO()
        faux = SimpleNamespace(isatty=lambda: True)
        with mock.patch.object(sys, 'stdin', faux), mock.patch.object(sys, 'stdout', sortie):
            self.assertEqual(li.commande_ligne_etat(['--sans-couleur'], SimpleNamespace(env=self.env, config=None)), 0)
        self.assertEqual(sortie.getvalue(), 'RAM 50 %\n')

    def test_dossiers_absents(self):
        self.env['GAGARINE_ETAT'] = str(self.racine / 'nulle-part')
        self.env['FILE_ATTENTE_DOSSIER'] = str(self.racine / 'rien')
        self.assertEqual(self.lancer(['--sans-couleur'])[1], 'RAM 50 %\n')

    def test_erreur_interne_donne_au_moins_la_ram(self):
        with mock.patch.object(li, 'partie_gagarine', side_effect=RuntimeError):
            self.assertEqual(self.lancer(['--sans-couleur']), (0, 'RAM ?\n'))
        with mock.patch.object(li, 'lire_entree', side_effect=RuntimeError):
            self.assertEqual(self.lancer(['--sans-couleur']), (0, 'RAM 50 %\n'))

    def test_option_inconnue(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()):
            self.assertEqual(self.lancer(['--bidon'])[0], 2)

    def test_commande_declaree(self):
        self.assertIn('ligne-etat', li.COMMANDES)
        self.assertIn('ligne-etat', li.AIDE_COMMANDES)

    def test_temps_de_reponse(self):
        self.ticket('a', source='gagarine', etat='en cours')
        for _ in range(500):
            self.evenement('x', 5000)
        self.lancer(['--sans-couleur'])
        debut = time.perf_counter()
        for _ in range(10):
            self.lancer(['--sans-couleur'])
        self.assertLess((time.perf_counter() - debut) / 10, 0.1)


class TestVraiMeminfo(unittest.TestCase):
    def test_lire_ram_reelle(self):
        self.assertTrue(0 <= li.lire_ram() <= 100)


if __name__ == '__main__':
    unittest.main()
