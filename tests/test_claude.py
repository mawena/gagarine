"""Tests du hook des sessions Claude (gagarine/claude.py) : analyse des commandes, sortie JSON, repli sur erreur."""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gagarine import claude as cl
from gagarine.cli import main

# (commande, nom de la fiche attendue ou None si libre)
LOURDES = [
    ('pytest', 'tests'),
    ('pytest -x tests/', 'tests'),
    ('python3 -m pytest -q', 'tests'),
    ('python3.12 -m unittest discover -s tests', 'tests'),
    ('cd x && make deb', 'make-deb'),
    ('make', 'make'),
    ('make -j8', 'make'),
    ('make -C build test', 'tests'),
    ('make all', 'make-all'),
    ('cargo build --release', 'cargo-build'),
    ('cargo +nightly test', 'cargo-test'),
    ('cargo t', 'cargo-test'),
    ('cargo clippy --all-targets', 'cargo-clippy'),
    ('./gradlew assembleDebug', 'gradle'),
    ('gradle build', 'gradle'),
    ('flutter build apk', 'flutter-build'),
    ('flutter test', 'flutter-test'),
    ('npm run build', 'build-npm'),
    ('npm test', 'test-npm'),
    ('npm --prefix web run build', 'build-npm'),
    ('pnpm run test:unit', 'test-pnpm'),
    ('yarn build', 'build-yarn'),
    ('npx vite build', 'build-vite'),
    ('ffmpeg -i a.mp4 b.mp4', 'ffmpeg'),
    ('nice -n 10 ffmpeg -i a.mp4 b.mp4', 'ffmpeg'),
    ('ionice -c3 nice -n19 ffmpeg -i a b', 'ffmpeg'),
    ('soffice --headless --convert-to pdf a.docx', 'conversion-documents'),
    ('libreoffice --convert-to=pdf a.odt', 'conversion-documents'),
    ('whisper audio.wav --model small', 'whisper'),
    ('ollama run llama3', 'modele-ollama'),
    ('lms load qwen', 'modele-lmstudio'),
    ('docker build -t x .', 'docker-build'),
    ('docker compose build', 'docker-build'),
    ('go test ./...', 'go-test'),
    ('mvn package', 'maven'),
    ('dotnet build', 'dotnet-build'),
    ('xvfb-run -a npm start', 'xvfb'),
    ('bash -c "cargo test"', 'cargo-test'),
    ('sh -lc "cd x && make test"', 'tests'),
    ('FOO=1 BAR=2 make test | tee log', 'tests'),
    ('env LANG=C make test', 'tests'),
    ('timeout 60 sudo -u bob python3 -m pytest -x', 'tests'),
    ('time make deb', 'make-deb'),
    ('echo début; ffmpeg -i a b; echo fin', 'ffmpeg'),
    ('echo "$(make test)"', 'tests'),
    ('x=`cargo build`', 'cargo-build'),
    ('(cd app && npm run build)', 'build-npm'),
    ('false || make test', 'tests'),
    ('make test > sortie.log 2>&1', 'tests'),
    ('if true; then pytest; fi', 'tests'),
]

LIBRES = [
    'ls -la',
    'echo pytest',
    'grep cargo src',
    'git commit -m "make test"',
    "git commit -m 'cargo build'",
    'cat Makefile',
    'pytest --version',
    'pytest --help',
    'ffmpeg -version --help',
    'make -h',
    'cargo --version',
    'make clean',
    'make install',
    'make -n deb',
    './gradlew --stop',
    './gradlew --status',
    'cargo fmt',
    'cargo doc',
    'npm install',
    'npm run lint',
    'yarn install',
    'ffprobe a.mp4',
    'docker ps',
    'go fmt ./...',
    'ollama list',
    'soffice --version',
    'gagarine file "tests" 2 2 -- make test',
    'gagarine file "voix" 4 4 -- ffmpeg -i a b',
    'file-attente "tests" 2 2 -- pytest',
    'cd x && gagarine file "deb" 2 4 -- make deb',
    'gagarine file t 1 1 -- bash -c "cargo test && make deb"',
    'cat <<EOF\nmake test\nEOF',
    'echo "make test" # cargo build',
    'python3 script.py',
    'python3 -m http.server',
    'sudo apt install make',
    "echo 'cargo test'",
    'bash -c "echo make test"',
    "cmd 'guillemet jamais fermé",
    'echo $(unterminated',
    '',
]


class TestAnalyse(unittest.TestCase):
    def test_commandes_lourdes(self):
        for commande, nom in LOURDES:
            with self.subTest(commande=commande):
                v = cl.verdict(commande)
                self.assertTrue(v['lourd'], v)
                self.assertEqual(v['travaux'][0]['nom'], nom)

    def test_commandes_libres(self):
        for commande in LIBRES:
            with self.subTest(commande=commande):
                self.assertFalse(cl.verdict(commande)['lourd'])

    def test_une_cinquantaine_de_cas(self):
        self.assertGreaterEqual(len(LOURDES) + len(LIBRES), 50)

    def test_commande_corrigee(self):
        cas = {
            'pytest -x': 'gagarine file "tests" 2 2 -- pytest -x',
            'cd x && make deb': 'cd x && gagarine file "make-deb" 2 4 -- make deb',
            'nice -n 10 ffmpeg -i a b': 'gagarine file "ffmpeg" 2 4 -- nice -n 10 ffmpeg -i a b',
            'FOO=1 make test | tee log': 'gagarine file "tests" 2 2 -- env FOO=1 make test | tee log',
            'make test && make deb': 'gagarine file "tests" 2 2 -- make test && gagarine file "make-deb" 2 4 -- make deb',
            '(cd app && npm run build)': '(cd app && gagarine file "build-npm" 3 2 -- npm run build)',
            'bash -c "cargo test"': 'gagarine file "cargo-test" 4 4 -- bash -c "cargo test"',
            'echo "$(make test)"': 'echo "$(gagarine file "tests" 2 2 -- make test)"',
            'make test > out.log 2>&1': 'gagarine file "tests" 2 2 -- make test > out.log 2>&1',
        }
        for commande, attendu in cas.items():
            with self.subTest(commande=commande):
                self.assertEqual(cl.verdict(commande)['corrigee'], attendu)

    def test_la_commande_corrigee_est_libre(self):
        for commande, _ in LOURDES:
            with self.subTest(commande=commande):
                self.assertFalse(cl.verdict(cl.verdict(commande)['corrigee'])['lourd'], commande)

    def test_commande_illisible_ne_bloque_pas(self):
        v = cl.verdict('make test "')
        self.assertFalse(v['lourd'])

    def test_heredoc_n_est_pas_du_code(self):
        self.assertFalse(cl.verdict('cat > a.sh <<\'EOF\'\nmake test\nEOF\n')['lourd'])
        self.assertTrue(cl.verdict('cat <<EOF > a\nx\nEOF\nmake test')['lourd'])

    def test_decoupage_des_redirections(self):
        jetons, _ = cl.decouper('make test 2>&1 | tee a.log; echo x >>b')
        self.assertEqual([j.genre for j in jetons],
                         ['mot', 'mot', 'redir', 'mot', 'op', 'mot', 'mot', 'op', 'mot', 'mot', 'redir', 'mot'])


class FauxCtx:
    def __init__(self, env, service=0):
        self.env = env
        self.config = None
        self.appels = []
        self.service = service

    def executer(self, cmd):
        self.appels.append(cmd)
        return self.service, '', ''


def lancer_hook(entree, ctx, texte_stdin=None):
    sortie = io.StringIO()
    stdin = io.StringIO(texte_stdin if texte_stdin is not None else json.dumps(entree))
    with mock.patch('sys.stdin', stdin), redirect_stdout(sortie):
        code = cl.commande_hook_claude([], ctx)
    return code, sortie.getvalue()


class TestHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dossier = Path(self.tmp.name)
        (self.dossier / 'file').mkdir()
        conf = self.dossier / 'gagarine.conf'
        conf.write_text('[gagarine]\n')
        self.env = {'GAGARINE_CONFIG': str(conf), 'GAGARINE_ETAT': str(self.dossier), 'FILE_ATTENTE_DOSSIER':
                    str(self.dossier / 'file'), 'HOME': str(self.dossier)}
        # poste tranquille : 8 Go libres, charge faible
        for cible, valeur in (('lire_ram_disponible', 8.0), ('lire_charge', 0.5)):
            p = mock.patch.object(cl, cible, return_value=valeur)
            p.start()
            self.addCleanup(p.stop)

    def bash(self, commande):
        return {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': commande}}

    def test_refus_avec_la_commande_corrigee(self):
        code, sortie = lancer_hook(self.bash('cd x && make deb'), FauxCtx(self.env))
        self.assertEqual(code, 0)
        s = json.loads(sortie)['hookSpecificOutput']
        self.assertEqual(s['hookEventName'], 'PreToolUse')
        self.assertEqual(s['permissionDecision'], 'deny')
        self.assertIn('cd x && gagarine file "make-deb" 2 4 -- make deb', s['permissionDecisionReason'])
        self.assertEqual(set(s), {'hookEventName', 'permissionDecision', 'permissionDecisionReason'})

    def ecrire_appris(self, travaux):
        (self.dossier / 'travaux.json').write_text(json.dumps(travaux))

    def test_refus_avec_la_ram_apprise(self):
        """La file connaît « make-deb » (pic 6,3 Go) : 1,2 × 6,3 = 7,56 -> 8 Go, au lieu des 2 Go fixes."""
        self.ecrire_appris({'make-deb': {'executions': 2, 'pics_go': [5.0, 6.3], 'annonce_go': 2}})
        _, sortie = lancer_hook(self.bash('cd x && make deb'), FauxCtx(self.env))
        raison = json.loads(sortie)['hookSpecificOutput']['permissionDecisionReason']
        self.assertIn('cd x && gagarine file "make-deb" 8 4 -- make deb', raison)

    def test_ram_apprise_arrondie_au_demi_go_superieur(self):
        self.ecrire_appris({'tests': {'executions': 1, 'pics_go': [1.0], 'annonce_go': 2}})
        _, sortie = lancer_hook(self.bash('pytest'), FauxCtx(self.env))
        self.assertIn('gagarine file "tests" 1.5 2 -- pytest',
                      json.loads(sortie)['hookSpecificOutput']['permissionDecisionReason'])

    def test_travail_inconnu_ou_memoire_abimee_garde_la_valeur_fixe(self):
        for contenu in ('{"autre": {"pics_go": [3]}}', 'pas du json', '[]'):
            with self.subTest(contenu=contenu):
                (self.dossier / 'travaux.json').write_text(contenu)
                _, sortie = lancer_hook(self.bash('make deb'), FauxCtx(self.env))
                self.assertIn('gagarine file "make-deb" 2 4 -- make deb',
                              json.loads(sortie)['hookSpecificOutput']['permissionDecisionReason'])

    def test_tester_avec_la_ram_apprise(self):
        self.ecrire_appris({'tests': {'executions': 1, 'pics_go': [3.0], 'annonce_go': 2}})
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            cl.commande_hook_claude(['--tester', 'pytest'], FauxCtx(self.env))
        self.assertIn('gagarine file "tests" 4 2 -- pytest', sortie.getvalue())

    def test_commande_legere_poste_tranquille_sortie_vide(self):
        self.assertEqual(lancer_hook(self.bash('ls'), FauxCtx(self.env)), (0, ''))

    def test_commande_deja_en_file(self):
        self.assertEqual(lancer_hook(self.bash('gagarine file "t" 2 2 -- make test'), FauxCtx(self.env)), (0, ''))

    def test_reglage_claude_bloquer_desactive(self):
        Path(self.env['GAGARINE_CONFIG']).write_text('[gagarine]\nclaude_bloquer = non\n')
        self.assertEqual(lancer_hook(self.bash('make test'), FauxCtx(self.env)), (0, ''))

    def test_variable_d_environnement_desactive(self):
        self.env['GAGARINE_CLAUDE_BLOQUER'] = 'non'
        self.assertEqual(lancer_hook(self.bash('make test'), FauxCtx(self.env)), (0, ''))

    def test_hors_bash_jamais_bloque(self):
        entree = {'tool_name': 'Write', 'tool_input': {'command': 'make test', 'file_path': 'x'}}
        self.assertEqual(lancer_hook(entree, FauxCtx(self.env)), (0, ''))

    def test_erreurs_d_entree_sortie_vide(self):
        for texte in ('', 'pas du json', 'null', '[]', '{"tool_name": "Bash"}', '{"tool_name": "Bash", "tool_input": 3}',
                      '{"tool_name": "Bash", "tool_input": {"command": 12}}'):
            with self.subTest(texte=texte):
                self.assertEqual(lancer_hook(None, FauxCtx(self.env), texte), (0, ''))

    def test_erreur_interne_ne_bloque_pas(self):
        with mock.patch.object(cl, 'verdict', side_effect=RuntimeError('boum')):
            self.assertEqual(lancer_hook(self.bash('make test'), FauxCtx(self.env)), (0, ''))

    def test_poste_charge_rappel(self):
        with mock.patch.object(cl, 'lire_ram_disponible', return_value=3.2):
            _, sortie = lancer_hook(self.bash('ls'), FauxCtx(self.env))
        s = json.loads(sortie)['hookSpecificOutput']
        self.assertEqual(s['hookEventName'], 'PreToolUse')
        self.assertNotIn('permissionDecision', s)
        self.assertTrue(s['additionalContext'].startswith('Poste chargé : 3.2 Go de RAM disponibles, charge 0.50 pour '))

    def test_service_arrete_prevenu(self):
        _, sortie = lancer_hook(self.bash('ls'), FauxCtx(self.env, service=3))
        self.assertIn('Le service Gagarine ne tourne pas', json.loads(sortie)['hookSpecificOutput']['additionalContext'])

    def test_refus_prime_sur_le_rappel(self):
        with mock.patch.object(cl, 'lire_ram_disponible', return_value=1.0):
            _, sortie = lancer_hook(self.bash('pytest'), FauxCtx(self.env, service=3))
        self.assertEqual(json.loads(sortie)['hookSpecificOutput']['permissionDecision'], 'deny')


class TestRappels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def test_seuils_de_charge(self):
        self.assertEqual(cl.message_charge(8.0, 3.0, 14, []), '')
        self.assertEqual(cl.message_charge(4.0, 10.5, 14, []), '')            # 0,75 × 14 = 10,5 : pas strictement au-dessus
        self.assertIn('charge 10.60 pour 14 cœurs', cl.message_charge(8.0, 10.6, 14, []))
        self.assertIn('3.9 Go', cl.message_charge(3.9, 0.1, 14, []))
        self.assertIn('travaux lourds dans la file : a ; b', cl.message_charge(8.0, 0.1, 14, ['a', 'b']))
        self.assertEqual(cl.message_charge(None, 0.1, 14, ['a']), '')

    def test_travaux_en_cours(self):
        (self.d / 'a.json').write_text(json.dumps({'pid': os.getpid(), 'nom': 'vivant'}))
        (self.d / 'b.json').write_text(json.dumps({'pid': 2 ** 22 + 12345, 'nom': 'mort'}))
        (self.d / 'c.json').write_text('pas du json')
        (self.d / 'd.json').write_text(json.dumps({'nom': 'sans pid'}))
        self.assertEqual(cl.travaux_en_cours(self.d), ['vivant'])
        self.assertEqual(cl.travaux_en_cours(self.d / 'absent'), [])

    def test_evenements_recents(self):
        maintenant = time.mktime((2026, 11, 10, 12, 0, 0, 0, 0, 0))
        t = lambda m: time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(maintenant - m * 60))
        j = self.d / 'evenements.jsonl'
        lignes = [
            {'date': t(45), 'action': 'fige', 'nom': 'trop vieux', 'ram': 96},
            {'date': t(20), 'action': 'fige', 'nom': 'pytest', 'ram': 96.1, 'pid': 42, 'commande': 'python3   -m\n pytest'},
            {'date': t(10), 'action': 'arrete', 'nom': 'ffmpeg', 'raison': 'urgence', 'commande': 'ffmpeg'},
            {'date': t(5), 'action': 'limite', 'nom': 'tests', 'limite': 4, 'declare': 1, 'commande': 'make test'},
            {'date': t(1), 'action': 'travail-debut', 'nom': 'autre'},
        ]
        j.write_text('\n'.join(json.dumps(x) for x in lignes) + '\nligne cassée\n')
        m = cl.message_evenements([j, self.d / 'absent.jsonl'], maintenant)
        self.assertNotIn('trop vieux', m)
        self.assertNotIn('autre', m)
        self.assertIn('« pytest » FIGÉ par Gagarine (RAM à 96.1 %), processus 42 ; commande : python3 -m pytest', m)
        self.assertIn('« ffmpeg » ARRÊTÉ par Gagarine (urgence)', m)
        self.assertIn('« tests » ARRÊTÉ par la file d\'attente (dépassement de sa limite de 4 Go, annoncé 1 Go)', m)
        self.assertIn(' — ', m)
        self.assertEqual(cl.message_evenements([self.d / 'absent.jsonl'], maintenant), '')

    def test_trois_evenements_au_plus(self):
        maintenant = time.time()
        j = self.d / 'e.jsonl'
        j.write_text('\n'.join(json.dumps({'date': time.strftime('%Y-%m-%dT%H:%M:%S+0000', time.gmtime(maintenant - i)),
                                           'action': 'fige', 'nom': f'n{i}', 'ram': 96}) for i in range(1, 6)) + '\n')
        m = cl.message_evenements([j], maintenant)
        self.assertEqual(m.count('FIGÉ'), 3)
        self.assertIn('n1', m)
        self.assertNotIn('n5', m)


class TestLigneDeCommande(unittest.TestCase):
    def test_tester(self):
        sortie = io.StringIO()
        vide = tempfile.TemporaryDirectory()
        self.addCleanup(vide.cleanup)
        with redirect_stdout(sortie), mock.patch.dict(os.environ, {'GAGARINE_ETAT': vide.name}):
            self.assertEqual(main(['hook-claude', '--tester', 'cd x && make test']), 0)
        self.assertEqual(sortie.getvalue().splitlines(),
                         ['lourd : make test', 'commande corrigée : cd x && gagarine file "tests" 2 2 -- make test'])
        sortie = io.StringIO()
        with redirect_stdout(sortie), mock.patch.dict(os.environ, {'GAGARINE_ETAT': vide.name}):
            main(['hook-claude', '--tester', 'echo pytest'])
        self.assertTrue(sortie.getvalue().startswith('libre : '))

    def test_tester_sans_commande(self):
        with mock.patch('sys.stderr', io.StringIO()):
            self.assertEqual(cl.commande_hook_claude(['--tester'], FauxCtx({})), 2)

    def test_temps_de_reponse(self):
        """L'analyse d'une commande compliquée reste très en dessous des 150 ms du hook."""
        longue = ' && '.join(['cd x', 'FOO=1 nice -n 5 make test | tee "a b.log"', 'echo "$(ls; cargo --version)"'] * 20)
        debut = time.perf_counter()
        for _ in range(20):
            cl.verdict(longue)
        self.assertLess((time.perf_counter() - debut) / 20, 0.05)

    def test_extension_chargee_par_cli(self):
        from gagarine.cli import charger_extensions
        self.assertIn('hook-claude', {c for _, cmds, _ in charger_extensions() for c in cmds})


if __name__ == '__main__':
    unittest.main()
