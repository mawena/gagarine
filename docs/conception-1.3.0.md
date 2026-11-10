# Conception de Gagarine 1.3.0 : une file plus rapide, les fuites repérées, l'état du poste dans Claude Code

Rédigé le 10/11/2026, après validation par l'utilisateur. Référence commune des chantiers de la 1.3.0 ; les contrats des
versions précédentes ([1.1.0](conception-1.1.0.md), [1.2.0](conception-1.2.0.md)) restent valables. On peut ajouter des
champs aux JSON, jamais en retirer.

## Pourquoi

- **La file est trop lente** : elle fait passer un seul travail à la fois. Le 10/11, sept travaux attendaient, dont des
  tests de 5 s coincés derrière des travaux de plusieurs minutes, alors que la RAM et les cœurs auraient suffi.
- **Les fuites se voient trop tard** : le test « affinage commande » de Voix-là a pris 9 Go le matin du 10/11 et a de
  nouveau dépassé sa limite (4 Go) l'après-midi. Un programme qui grossit régulièrement se voit bien avant le seuil.
- **Les sessions Claude ne voient pas la charge** du poste avant de lancer quelque chose.

Version : **1.3.0** (fonctions nouvelles, rien d'incompatible).

## 0. Socle (fait avant les chantiers)

- Réglages (`gagarine/reglages.py`, `etc/gagarine.conf`) : `file_paralleles` (entier ≥ 1, 2 par défaut ; 1 = comme
  avant), `fuite_seuil` (Mo/min, 100 ; 0 = non), `fuite_notifier` (oui).
- Extension `ligne` déclarée dans `EXTENSIONS` (`gagarine/cli.py`).

## 1. File : plusieurs travaux à la fois, priorités (`gagarine/file.py`)

- **Besoin** d'un travail = son annonce (déjà corrigée par la mémoire apprise de la 1.2.0).
- Un travail en attente **démarre** quand toutes ces conditions sont réunies :
  1. moins de `file_paralleles` travaux en cours (tickets en cours de Gagarine, plus tout ticket d'un ancien outil qui a
     la main) ;
  2. `MemAvailable` − réserve ≥ besoin, où la réserve = 1 Go de marge + pour chaque travail en cours de Gagarine,
     `max(0, besoin − mémoire actuelle de son scope)` (ce qu'il peut encore prendre), + 2 Go par travail en cours d'un
     ancien outil (inconnu) ;
  3. cœurs libres (`nproc` − charge sur 1 min − cœurs des travaux lancés depuis moins de 60 s, que la charge ne montre
     pas encore) ≥ cœurs annoncés ;
  4. **ordre** : les tickets en attente sont rangés par priorité (`urgent`, `normal`, `fond`) puis par ancienneté ; un
     travail ne passe devant un travail plus haut dans ce rang que si ce dernier ne tient pas en ce moment, et jamais
     devant un travail qui attend depuis plus de 10 min (pas de famine) ;
  5. **anciens outils** : un ticket d'un ancien outil (`source: "autre"`, sans `ram`) qui attend n'est jamais doublé (il
     ne sait pas tourner à côté des autres) ; les anciens outils gardent leur règle (aucun ticket vivant plus ancien) ;
  6. `--apres PID` et l'héritage (`FILE_ATTENTE_TICKET`) inchangés.
- **Priorité** : `gagarine file --priorite urgent|normal|fond "<nom>" <Go> <cœurs> -- …` (normal par défaut) ;
  `gagarine file priorite PID|TICKET NIVEAU` change la priorité d'un travail en attente (le sien ; root : tous). Une
  priorité n'interrompt jamais un travail en cours. Champ `priorite` dans le ticket (ignoré par les anciens outils).
- `file --etat --json` : chaque travail ajoute `priorite`, `besoin_go`, et pour ceux qui attendent `motif` (texte
  court : « 2 travaux en cours », « mémoire : 1,8 Go libres, il en faut 4 », « derrière « rendu » (urgent) »…) ;
  niveau supérieur : `paralleles` (le réglage) et `en_cours` (nombre).
- Messages d'attente : mêmes textes qu'avant, plus les nouveaux motifs.

## 2. Fuites de mémoire

### Pendant qu'un programme tourne (`gagarine/garde.py`, chantier du garde)

- Pour chaque programme non protégé d'au moins 500 Mo (RAM + swap) : pente sur les 5 dernières minutes (mesures gardées
  toutes les 10 s) ; **fuite** si la pente atteint `fuite_seuil` Mo/min et que la mémoire a monté sur au moins 80 % des
  intervalles. Événement `fuite` : `pid`, `nom`, `pente_mo_min`, `memoire` (octets), `prevision_s` (temps avant que la
  RAM n'atteigne le seuil à cette pente, ou `null` si elle ne l'atteindrait pas), `utilisateur`, `commande`.
- Notification du bureau (et ntfy si réglé), si `fuite_notifier` : « python3 grossit de 210 Mo/min depuis 7 min :
  la RAM serait pleine vers 18:42 ». Une seule fois par programme toutes les 30 min. **Aucune action** : le garde agit
  comme avant (anticipation, seuil).
- `gagarine etat --json` : `fuites` = liste des fuites repérées en cours (`pid`, `nom`, `pente_mo_min`, `depuis`).
- `journal.py` : ligne lisible de `fuite` (« 210 Mo/min, RAM pleine dans ~12 min ») et de `fuite-travail`.

### D'un lancement à l'autre (`gagarine/file.py`, chantier de la file)

- Dans `travaux.json`, `tendance` : `hausse` si les 3 derniers pics (au moins) montent à chaque fois et que le dernier
  vaut au moins 1,3 × le plus ancien d'entre eux ; `limite` si les 2 derniers lancements ont été arrêtés par leur
  limite ; sinon `stable`.
- Au lancement d'un travail en `hausse` ou en `limite` : message (« « tests voix » : pic en hausse à chaque lancement
  (2,1 → 3,4 → 5,0 Go) : fuite probable ») et événement `fuite-travail` (`nom`, `tendance`, `pics_go`).
- `file appris --json` : `tendance` pour chaque travail.

## 3. L'état du poste dans la barre de Claude Code (`gagarine/ligne.py`, nouveau)

- `gagarine ligne-etat [--avec "COMMANDE"]` : lit sur l'entrée standard le JSON que Claude Code passe à sa ligne
  d'état, et affiche **une ligne courte**, par exemple `RAM 72 % · file 3 (2 en cours) · 1 figé · fuite : python3`.
  Seules les parties non nulles s'affichent ; couleurs ANSI selon le niveau (normal, vigilance, seuil).
- `--avec "COMMANDE"` : lance d'abord la ligne d'état existante (ex. `/usr/bin/claude-accounts statusline`) avec la même
  entrée (délai maximal 1 s), garde sa sortie, et ajoute la partie de Gagarine à la fin de sa dernière ligne (séparateur
  ` │ `). Si cette commande échoue ou dépasse le délai, seule la partie de Gagarine s'affiche.
- Moins de 100 ms (sans compter `--avec`) ; ne plante jamais (au pire : la RAM seule). Données : `/proc/meminfo`, dossier
  des tickets, `figes.json` (dossier d'état lisible), événements `fuite` de la dernière demi-heure.
- Mise en place sur le poste : `statusLine` de `~/.claude/settings.json` →
  `gagarine ligne-etat --avec "/usr/bin/claude-accounts statusline"`.

## 4. Interface et indicateur

- **File d'attente** : priorité de chaque travail, plusieurs travaux « en cours », motif d'attente, choix de la
  priorité dans « Nouveau travail… », action « Changer la priorité » sur un travail en attente.
- **Programmes** : badge « fuite » (pente, prévision) sur les programmes concernés ; section des fuites en cours.
- **File d'attente, Mémoire apprise** : tendance (flèche montante, « fuite probable »).
- **Journal** : événements `fuite` et `fuite-travail`.
- **Réglages** : `file_paralleles`, `fuite_seuil`, `fuite_notifier`.
- **Indicateur** : ligne « Fuite : python3 (+210 Mo/min) » quand il y en a une.

## Répartition des fichiers

| Chantier | Fichiers |
|---|---|
| File (1, 2 d'un lancement à l'autre) | `gagarine/file.py`, `gagarine/cli.py` (partie `file`), `tests/test_file*.py` |
| Garde (2 pendant) | `gagarine/garde.py`, `gagarine/systeme.py`, `gagarine/journal.py`, `tests/test_garde*.py`, `tests/test_journal*.py` |
| Ligne d'état (3) | `gagarine/ligne.py`, `tests/test_ligne*.py` |
| Interface (4) | `gagarine/interface/`, `gagarine/indicateur/`, `tests/test_interface*.py`, `tests/test_indicateur*.py`, `tests/donnees_*/`, `docs/captures/` |
| Assemblage | `debian/`, `man/`, `README.md`, `CHANGELOG.md`, `site/`, `docs/` |

Personne d'autre ne touche `gagarine/reglages.py`, `gagarine/ntfy.py`, `etc/gagarine.conf`, `tests/commun.py`.

## Précisions apportées pendant les chantiers

- **File** : un travail **seul** (aucun autre en cours) démarre dès que `MemAvailable` ≥ son besoin, sans la marge de 1 Go
  (sinon un travail dont le besoin atteint la mémoire disponible n'aurait jamais démarré, alors que l'ancienne règle le
  laissait partir) ; la marge et les réserves s'appliquent dès qu'un travail tourne. Avec `file_paralleles=1`, l'ancienne
  règle et ses textes sont gardés tels quels. La garde « jamais devant un travail qui attend depuis plus de 10 min » vaut
  pour un travail de rang plus haut (un travail `fond` qui attend ne bloque pas un `urgent`), et pas pour un travail
  lui-même retenu par un ticket d'ancien outil (pas d'interblocage). Un ticket de Gagarine 1.2 (sans `priorite`) ou
  illisible en attente est traité comme un ancien outil. Le ticket porte aussi `debut` (calcul des cœurs récents). Le
  champ `etat` d'un ticket Node reste `null` : seul `en_cours` compte le ticket Node qui a la main. `travaux.json` porte
  une liste `limites` (les lancements d'avant la 1.3.0 comptent comme non arrêtés).
- **Fuites en direct** : il faut au moins 290 s de mesures ; pente par moindres carrés ; un intervalle compte « en
  hausse » si la mémoire dépasse strictement la mesure précédente. `prevision_s` vaut 0 si la RAM est déjà au seuil,
  `null` si la RAM totale est inconnue ou si la prévision dépasse 24 h ; `Journal.ecrire` omet les champs `None`, donc
  `prevision_s` peut manquer dans l'événement (présent avec `null` dans `fuites.json` et `etat --json`). `fuites[].depuis`
  est l'instant du premier repérage. Une fuite qui se stabilise disparaît de `fuites` à la mesure suivante. Constantes
  `FUITE_*` en tête de `garde.py` (500 Mo, 80 %, 30 min). `fuites.json` est écrit à côté de `figes.json` (0644,
  atomique, seulement quand la liste change, vidé au démarrage du service).
- **Ligne d'état** : `--avec` découpe la commande avec `shlex` (pas de shell : ni tube ni redirection) et tue tout son
  groupe de processus au-delà de 1 s ; `--sans-couleur` et `NO_COLOR` retirent les couleurs.

