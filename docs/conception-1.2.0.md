# Conception de Gagarine 1.2.0 : empêcher les incidents à la source

Rédigé le 10/11/2026, après la validation par l'utilisateur des huit fonctions proposées. Référence commune des chantiers
de la 1.2.0 : chacun s'y tient ; un contrat (commande, JSON, fichier) ne change pas sans mise à jour ici. Les contrats de
la 1.1.0 ([conception-1.1.0.md](conception-1.1.0.md)) restent valables.

## Pourquoi

La 1.1.0 protège le poste quand la mémoire déborde. Les incidents de la semaine viennent pourtant de causes qu'on peut
arrêter plus tôt : un test lancé hors de la file (10/11), une RAM annoncée bien trop basse (1 Go pour 9 Go, 10/11), des
démons Gradle (7 Go, 08/11) et un modèle LM Studio (09/11) qui gardaient la mémoire sans rien faire. La 1.2.0 :

1. **bloque** les travaux lourds lancés hors de la file par les sessions Claude ;
2. **retient** la mémoire vraiment prise par chaque travail et corrige les annonces trop basses ;
3. **fait le ménage** des démons et modèles inactifs ;
4. **prévient** à la fin d'un travail, sur le bureau et sur le téléphone (ntfy) ;
5. **agit avant 95 %** quand un programme va remplir la RAM ou que le noyau peine déjà ;
6. garde un **historique** de 7 jours ;
7. se met à jour par **`apt upgrade`** (dépôt APT sur GitHub Pages) ;
8. **plafonne** la mémoire des applications du bureau (Chrome, VS Code…).

Version : **1.2.0** (fonctions nouvelles, rien d'incompatible).

## 0. Socle (fait avant les chantiers)

- **Réglages** (`gagarine/reglages.py`, `etc/gagarine.conf`, écriture par `reglages --ecrire`, JSON de `etat` et
  `reglages`) : `anticipation` (s, 15, 0 = non), `pression` (%, 20, 0 = non), `notifications_fin` (oui),
  `notification_fin_min` (s, 30), `ntfy` (sujet ou adresse, vide), `file_ajuster` (oui), `menage_auto` (oui),
  `menage_delai` (min, 30), `claude_bloquer` (oui). Les composants qui tournent sous l'utilisateur (file, ménage, hook
  Claude, applications) lisent aussi `/etc/gagarine.conf` (lisible par tous) avec `charger_reglages()`.
- **`gagarine/ntfy.py`** : `envoyer(cible, titre, texte, priorite, etiquettes)` et `envoyer_en_fond(…)` (fil à part,
  jamais bloquant, jamais d'exception). Priorités : `basse`, `normale`, `haute`, `urgente`.
- **Commandes en extensions** (`gagarine/cli.py`) : les modules `claude`, `menage`, `historique`, `applications` du
  paquet définissent `COMMANDES = {'nom': fonction(args, ctx)}` et `AIDE_COMMANDES` (lignes d'aide) ; `cli.py` les
  charge s'ils existent. **Aucun chantier autre que celui de la file ne modifie `cli.py`.**

## 1. Hook des sessions Claude qui bloque (`gagarine/claude.py`)

- `gagarine hook-claude` : lit sur l'entrée standard le JSON `PreToolUse` de Claude Code (`tool_name`,
  `tool_input.command`) et répond comme le hook actuel `~/.claude/hooks/ressources-poste.sh` (rappel quand le poste est
  chargé, événements des 30 dernières minutes, service arrêté) — **puis bloque** :
  si `claude_bloquer` et que la commande lance un travail lourd hors de la file, la réponse est
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": "…"}}` avec la commande corrigée (`gagarine file "<nom>" <Go> <cœurs> -- <commande>`).
- Travaux lourds reconnus (premier mot de chaque commande d'une chaîne `&&`, `;`, `|`, `$( )`, après `env`, `nice`,
  `ionice`, `timeout`, `time`, `sudo -u`, variables `A=b`) : `pytest`, `python -m pytest|unittest`, `cargo
  build|test|check|clippy|run|bench`, `gradle`/`./gradlew` (sauf `--stop`, `--status`), `flutter build|test`, `make`
  (cibles `test`, `deb`, `build`, `all` ou sans cible), `npm|pnpm|yarn run build|test` et `npm test`, `vite build`,
  `ffmpeg`, `soffice --convert-to`/`libreoffice --convert-to`, `whisper`, `ollama run`, `lms load`, `docker build`,
  `go build|test`, `mvn`, `dotnet build|test`, `xvfb-run`.
- Jamais bloqués : `--help`, `-h`, `--version`, `-V` ; une commande déjà passée par la file (`gagarine file … --`,
  `file-attente … --`) ; ce qui n'est pas Bash.
- `gagarine hook-claude --tester "<commande>"` : affiche le verdict (`lourd` ou `libre`, raison, commande corrigée)
  — pour les essais et le débogage.
- Doit répondre en moins de 150 ms ; ne jamais planter (en cas d'erreur : ne rien bloquer, sortie vide, code 0).
- `data/claude/ressources-poste.sh` : le hook du poste réduit à `exec gagarine hook-claude` (avec repli sur rien si
  `gagarine` manque) ; installé par le paquet dans `/usr/share/gagarine/claude/`.

## 2. Mémoire apprise par travail (`gagarine/file.py`, chantier de la file)

- Pendant un travail, la file relève toutes les 2 s le pic mémoire de son scope (`memory.peak` et, s'il existe,
  `memory.swap.peak` du cgroup du scope, lu par `ControlGroup`), et une dernière fois à la fin.
- `travail-fin` reçoit `pic_go` (RAM + swap au plus haut, en Go, 2 décimales) ; `limite` aussi.
- Mémoire des travaux : `~/.local/state/gagarine/travaux.json` (même dossier que le journal) :
  `{"<nom>": {"executions": 3, "pics_go": [6.1, 6.3, 5.9], "annonce_go": 1, "derniere": "<date ISO>"}}`
  (5 derniers pics). Un travail arrêté par sa limite compte au moins sa limite comme pic.
- Au lancement : si le plus haut des derniers pics dépasse l'annonce de plus de 10 %, message
  (« tests voix : annoncé 1 Go, il en a pris 6,3 Go la dernière fois ») ; si `file_ajuster`, l'annonce devient
  `min(arrondi au 0,5 Go supérieur de 1,2 × pic, 60 % de la RAM totale)` (au-delà : message conseillant de corriger
  la fuite) ; `travail-debut` reçoit `annonce_initiale` et `ajuste: true`.
- `gagarine file appris [--json]` : `{"travaux": [{"nom", "executions", "pic_go", "annonce_go", "derniere"}]}`, du
  plus récent au plus ancien.

## 3. Notification de fin de travail (chantier de la file et chantier du garde)

- Fin d'un travail d'au moins `notification_fin_min` s (si `notifications_fin`) : `notify-send` sur la session de
  l'utilisateur (`--app-name=Gagarine -i io.github.mawena.Gagarine`) — « « rendu vidéo » terminé en 12 min »,
  « « tests » a échoué (code 3) », « « tests » arrêté : limite mémoire dépassée » — et le même message par ntfy si
  `ntfy` est réglé.
- Le service envoie aussi par ntfy chaque programme figé ou arrêté (priorité haute), en plus des notifications du
  bureau existantes.

## 4. Agir avant 95 % (`gagarine/garde.py`, chantier du garde)

- **Anticipation** : au-delà de `vigilance`, si le programme qui grossit le plus vite, à son rythme des 10 dernières
  secondes, porterait la RAM au `seuil` dans moins de `anticipation` secondes, il est figé tout de suite.
- **Pression** : au-delà de `vigilance`, si `full avg10` de `/proc/pressure/memory` atteint `pression` %, Gagarine agit
  comme au seuil (programme qui grossit le plus vite, à défaut le plus gros).
- L'événement `fige` reçoit `declencheur` : `seuil`, `anticipation`, `pression` ou `urgence`. Mêmes protections
  qu'avant (programmes protégés jamais touchés) ; une croissance brève (moins de 10 s) ne suffit pas.
- `gagarine/journal.py` (chantier du garde) affiche les nouveaux champs : `declencheur`, `pic_go` de `travail-fin`,
  `ajuste`, et les événements `menage`.

## 5. Historique (`gagarine/historique.py` + `gagarine/garde.py`, chantier du garde)

- Le service écrit une mesure par minute dans `historique-AAAA-MM-JJ.jsonl` de son dossier d'état (gardé 8 jours) :
  `{"date", "ram", "disponible_mo", "swap_mo", "zram_mo", "pression", "gros": [["chrome", 2800], …]}` (3 plus gros).
- `gagarine historique [--depuis 5min|1h|24h|7j] [--json]` (24 h par défaut) :
  `{"depuis_s", "pas_s", "points": [{"date", "ram", "swap_mo", "zram_mo", "pression"}], "incidents": [événements
  fige/arrete/limite de la période], "gros": [{"nom", "pic_mo", "minutes"}], "travaux": [{"nom", "date", "duree",
  "code", "pic_go"}]}` ; au-delà de 24 h, points moyennés par 10 min (le maximum de `ram` gardé).

## 6. Ménage des inactifs (`gagarine/menage.py`, chantier du ménage)

- Candidats (au moins 300 Mo, temps processeur quasi nul depuis `inactif_depuis_s`) :
  `gradle` (java avec `GradleDaemon`), `kotlin` (`KotlinCompileDaemon`), `lmstudio` (modèles chargés dans LM Studio,
  déchargés par `lms unload --all`, seulement si LM Studio tourne déjà), `ollama` (modèles chargés selon
  `http://127.0.0.1:11434/api/ps`, déchargés par l'API), `serveur-dev` (node/vite/next/webpack en mode dev : proposés,
  **jamais** arrêtés tout seuls). Seulement les processus de l'utilisateur.
- `gagarine menage [--json]` : `{"auto": true, "delai_min": 30, "candidats": [{"id", "type", "nom", "pid",
  "memoire_mo", "inactif_depuis_s", "action", "auto"}]}` (`id` : PID, ou `lmstudio`, ou `ollama:<modèle>`).
- `gagarine menage arreter ID…` : arrête (SIGTERM, puis SIGKILL après 10 s) ou décharge.
- `gagarine menage auto` : fait le ménage automatique (si `menage_auto`, types autorisés, inactifs depuis plus de
  `menage_delai` min ; 5 min seulement si la RAM dépasse `vigilance`) ; lancé toutes les 2 min par le minuteur
  utilisateur `data/systemd-user/gagarine-menage.timer` / `.service`.
- Suivi de l'inactivité : `~/.local/state/gagarine/menage.json` (temps processeur vu à chaque passage).
- Événement `menage` dans le journal de l'utilisateur : `type`, `nom`, `pid`, `memoire_mo`, `auto`.

## 7. Plafond mémoire des applications du bureau (`gagarine/applications.py`, chantier du hook Claude)

- GNOME lance chaque application dans un scope `app-gnome-<id>-<pid>.scope` (snap : `snap.<nom>.<app>-….scope`).
- `gagarine applications [--json]` : `{"applications": [{"id", "nom", "prefixe", "instances", "memoire_mo",
  "plafond_go"}]}` — applications en cours (mémoire des scopes) et applications plafonnées.
- `gagarine applications plafond <id> <Go|aucun>` : écrit (ou retire)
  `~/.config/systemd/user/<prefixe>.scope.d/50-gagarine.conf` (`[Scope]` `MemoryHigh=<Go>G`), `systemctl --user
  daemon-reload`, et applique tout de suite aux instances ouvertes (`systemctl --user set-property --runtime <scope>
  MemoryHigh=…`). `MemoryHigh` freine et range dans le swap au-delà du plafond, sans arrêter l'application.
- Le chantier vérifie pour de vrai, sur systemd 255, que le dossier de préfixe s'applique bien à un scope transitoire.

## 8. Dépôt APT (`scripts/publier-apt.sh`, chantier APT)

- Dépôt signé sur GitHub Pages : `https://mawena.github.io/gagarine/apt` (branche `gh-pages`, dossier `apt/`) :
  `dists/stable/{Release,InRelease,Release.gpg}`, `dists/stable/main/binary-all/Packages{,.gz}` (et `binary-amd64`
  si apt l'exige), `pool/main/gagarine_<v>_all.deb`, clé publique `gagarine.gpg`.
- Clé de signature dédiée (ed25519, « Gagarine APT (mawena) », adresse noreply GitHub), dans le trousseau de
  l'utilisateur ; clé publique dans `data/apt/gagarine.gpg`.
- Le paquet installe `/etc/apt/sources.list.d/gagarine.sources` (deb822, `Signed-By:
  /usr/share/keyrings/gagarine.gpg`) et la clé : après une première installation à la main, `apt upgrade` suffit.
- `scripts/publier-apt.sh dist/gagarine_<v>_all.deb` : ajoute le paquet, régénère et signe les index, pousse
  `gh-pages`. Vérifié par un `apt-get update` sans root dans un dossier temporaire.

## 9. Interface et indicateur (chantier de l'interface)

- **Réglages** : les nouvelles clés (anticipation, pression, notifications de fin et durée minimale, ntfy avec un bouton
  « Envoyer un essai », correction des annonces, ménage automatique et délai, blocage des sessions Claude).
- **Aperçu** : la courbe passe de 5 min à 24 h ou 7 jours (`historique --json`), incidents marqués sur la courbe.
- **Programmes** : section **Ménage** (inactifs, mémoire, depuis quand, bouton Arrêter ou Décharger, état du ménage
  automatique) et section **Applications du bureau** (mémoire, plafond réglable, « aucun »).
- **File d'attente** : **Mémoire apprise** (`file appris --json`) et **Travaux passés** (`historique --json`, avec pic).
- **Journal** : nouveaux événements et champs (`menage`, `declencheur`, `pic_go`, `ajuste`).
- **Indicateur** : entrée « Ménage : N inactifs (X Go) » avec un sous-menu d'actions.

## 10. Paquet

Nouveaux fichiers installés : `data/claude/ressources-poste.sh` → `/usr/share/gagarine/claude/`,
`data/systemd-user/gagarine-menage.{service,timer}` → `/usr/lib/systemd/user/` (minuteur activé pour tous les
utilisateurs), `data/apt/gagarine.sources` → `/etc/apt/sources.list.d/`, `data/apt/gagarine.gpg` →
`/usr/share/keyrings/`. Pages de manuel à jour.

## Répartition des fichiers entre les chantiers

| Chantier | Fichiers |
|---|---|
| File (2, 3) | `gagarine/file.py`, `gagarine/cli.py`, `tests/test_file*.py` |
| Garde (3, 4, 5) | `gagarine/garde.py`, `gagarine/systeme.py`, `gagarine/journal.py`, `gagarine/historique.py`, `tests/test_garde*.py`, `tests/test_historique*.py`, `tests/test_journal*.py` |
| Ménage (6) | `gagarine/menage.py`, `data/systemd-user/`, `tests/test_menage*.py` |
| Hook Claude et applications (1, 7) | `gagarine/claude.py`, `gagarine/applications.py`, `data/claude/`, `tests/test_claude*.py`, `tests/test_applications*.py` |
| APT (8) | `scripts/publier-apt.sh`, `data/apt/`, `Makefile`, branche `gh-pages` |
| Interface (9) | `gagarine/interface/`, `gagarine/indicateur/`, `tests/test_interface*.py`, `tests/test_indicateur*.py`, `tests/donnees_interface/`, `tests/donnees_indicateur/` |
| Assemblage (10) | `debian/`, `man/`, `README.md`, `CHANGELOG.md`, `docs/` |

Personne d'autre ne touche `gagarine/reglages.py`, `gagarine/ntfy.py`, `etc/gagarine.conf` ni `tests/commun.py`.

## Précisions apportées pendant les chantiers

Les contrats ci-dessus restent valables ; voici ce que les chantiers ont précisé (ajouts seulement).

- **File** : `travail-debut` porte `annonce_initiale` et `ajuste: true` seulement quand l'annonce a été corrigée (la
  nouvelle annonce est alors dans `ram`) ; le ticket porte aussi `annonce_initiale`. Dans `travaux.json`, `annonce_go`
  est l'annonce demandée par l'appelant (pas la corrigée). Le pic est relevé toutes les 0,5 s (constante
  `INTERVALLE_PIC`) : à 2 s, un travail court pouvait passer à côté de son pic. Le dernier relevé après la fin échoue le
  plus souvent (le cgroup du scope disparaît avec lui) : le pic retenu peut sous-estimer de peu un pic des 0,5 dernières
  secondes.
- **Garde** : au seuil d'urgence, l'action est `arrete` (pas `fige`), qui porte alors `declencheur: "urgence"`. Points
  moyennés de l'historique (au-delà de 24 h) : `ram` est le maximum du seau de 10 min, les autres champs des moyennes ;
  `gros` : 10 entrées au plus, `minutes` = nombre de mesures où le programme était parmi les 3 plus gros.
- **Ménage** : `action` vaut `arreter` (processus) ou `decharger` (modèles) ; `pid` vaut `null` pour `lmstudio` et
  `ollama` ; le champ `auto` d'un candidat dit si son type peut être traité tout seul (tout sauf `serveur-dev`),
  indépendamment de `menage_auto` (le `auto` du niveau supérieur).
- **Applications** : sous GNOME, une même application peut avoir deux familles de scopes (`app-gnome-google\x2dchrome-…`
  et `app-com.google.Chrome-…`) : elles sont listées séparément, et l'id qui porte la mémoire est celui à plafonner. Les
  ids hors `app-gnome-` gardent leur préfixe `app-` (ex. `app-com.microsoft.VSCode`). Le champ `prefixe` du JSON inclut
  le tiret final, et le dossier est `<prefixe>.scope.d` (systemd l'exige). Le `daemon-reload` suffit à appliquer le
  plafond aux scopes ouverts ; `set-property --runtime` est gardé, sans danger.
- **ntfy** : `gagarine ntfy essai CIBLE` (extension `ntfy`) envoie un message d'essai à la cible donnée, même non
  enregistrée : code 0 si le serveur l'accepte, 1 sinon, 2 si la cible est invalide.
