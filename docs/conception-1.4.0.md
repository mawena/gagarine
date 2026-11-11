# Conception de Gagarine 1.4.0 : le processeur

Rédigé le 11/11/2026, à la demande de l'utilisateur (« j'aimerais aussi voir les trucs sur le cpu, pas juste la RAM »). Les
choix ci-dessous sont les miens : l'utilisateur m'a laissé décider de la conception. Ce document est la référence commune des
chantiers de la 1.4.0. Les contrats des versions précédentes restent valables
([1.1.0](conception-1.1.0.md), [1.2.0](conception-1.2.0.md), [1.3.0](conception-1.3.0.md)). On peut ajouter des champs aux
JSON, jamais en retirer.

## Pourquoi

Gagarine ne montre que la mémoire. Pourtant, le poste ralentit aussi quand le processeur sature : rendus vidéo, tests,
compilations, onglets de navigateur. Aujourd'hui, pour savoir qui occupe les 14 cœurs, s'ils chauffent ou à quelle fréquence
ils tournent, il faut ouvrir un autre outil. La 1.4.0 rend tout cela visible, partout où Gagarine montre déjà la RAM :

- la ligne de commande ;
- l'interface ;
- l'indicateur de la barre du haut ;
- la ligne d'état des sessions Claude ;
- l'historique ;
- la file d'attente.

Version : **1.4.0** : des fonctions nouvelles, rien d'incompatible.

## Ce que la 1.4.0 ne fait pas

- **Gagarine n'agit jamais sur un programme à cause du processeur** : il ne fige pas, ne bride pas et ne baisse pas la
  priorité (renice). Ses actions restent réservées à la RAM. Un rendu qui prend 13 cœurs fait son travail.
- **Aucune nouvelle notification, ni alerte ntfy**, sur le processeur (surchauffe, saturation). On pourra en ajouter plus tard
  si l'utilisateur le demande.
- **Les règles de la file d'attente ne changent pas** : elle choisit toujours avec la charge et les cœurs libres de la 1.3.0.
- **Aucun nouveau réglage.**
- **Le service ne doit rien coûter de visible.** Il mesure toutes les 2 s, en moins de 10 ms, sans jamais attendre. Une
  erreur de mesure du processeur n'interrompt jamais la protection de la RAM : elle est attrapée et écrite une seule fois
  dans le journal système.

## 1. Mesure (`gagarine/processeur.py`, nouveau module)

Ce module contient des fonctions pures qui lisent des textes. Les lectures du système sont des fonctions minces que les
tests remplacent. Il n'importe aucun autre module de Gagarine, pour rester léger.

- **Utilisation** : elle vient de `/proc/stat`, lignes `cpu` et `cpuN`.
  - total = user + nice + system + idle + iowait + irq + softirq + steal. guest et guest_nice ne sont pas ajoutés : ils sont
    déjà comptés dans user et nice.
  - occupé = total − idle − iowait.
  - % = Δoccupé / Δtotal entre deux relevés, pour l'ensemble des cœurs et pour chaque cœur logique (`cpu0`…`cpuN`, dans
    l'ordre).
- **Par programme** : `utime + stime` de `/proc/PID/stat`.
  - Couper après la dernière `) ` : le nom peut contenir des espaces et des parenthèses.
  - Le noyau additionne déjà les fils d'exécution (threads) du programme.
  - **Cœurs utilisés** = Δticks / (`SC_CLK_TCK` × Δt).
  - **Pourcentage du processeur** = cœurs utilisés / nombre de cœurs logiques × 100.
  - Tous les utilisateurs sont comptés : `/proc/PID/stat` est lisible par tous.
  - Les threads du noyau (PPid 2, et le PID 2) sont gardés et marqués `noyau: true`. `kswapd0` qui travaille dit que le
    poste échange avec le swap.
  - Pas de `cutime` : il compterait deux fois le temps des programmes enfants.
  - Un programme apparu entre deux relevés compte depuis son démarrage (`starttime`) ou n'est pas compté. Le choix revient au
    chantier, à noter dans les précisions.
- **Température** : on garde le premier capteur trouvé, dans cet ordre.
  1. hwmon `coretemp`, libellé « Package id 0 » ;
  2. hwmon `k10temp`, « Tctl » puis « Tdie » ;
  3. hwmon `zenpower`, « Tdie » ;
  4. hwmon `cpu_thermal` ou `soc_thermal`, `temp1` ;
  5. zone thermique de type `x86_pkg_temp`, puis `TCPU`, puis `cpu-thermal` ;
  6. sinon `null`.

  La valeur est en °C, avec une décimale. `temperature_critique` vient de `tempN_crit` du même capteur, sinon de `null`. Sur
  le poste, c'est coretemp, à 110 °C. **Chaud** = température ≥ critique − 15, ou ≥ 90 °C si la critique est inconnue.
- **Fréquence** : moyenne des `scaling_cur_freq` de `/sys/devices/system/cpu/cpu*/cpufreq/`, convertie de kHz en MHz et
  arrondie. Le maximum est le plus grand `cpuinfo_max_freq`. Les deux valent `null` si le système ne donne pas la fréquence.
- **Types de cœurs** : sur les processeurs hybrides d'Intel, `/sys/devices/cpu_core/cpus` (P, performants) et
  `/sys/devices/cpu_atom/cpus` (E, économes) sont au format `0-3,8`. Le module en tire la liste `types` (`"P"`/`"E"` par cœur
  logique), ou `null` si le processeur n'est pas hybride. Sur le poste : 0-3 → P, 4-13 → E.
- **Modèle** : premier `model name` de `/proc/cpuinfo`, sinon `Hardware`, sinon `null`.
- **Charge** : les trois nombres de `/proc/loadavg`.
- **Pression** : `some avg10` de `/proc/pressure/cpu`, en %, ou 0.0 si le noyau ne la donne pas.

### L'objet `processeur` (contrat)

```json
{
  "t": 1762876400.12, "date": "2026-11-11T16:53:20+0000", "source": "service", "intervalle_s": 2.0,
  "pourcent": 23.4, "nb_coeurs": 14, "par_coeur": [12.0, 40.5, "… un nombre par cœur logique"],
  "types": ["P", "P", "P", "P", "E", "…"], "modele": "Intel(R) Core(TM) Ultra 7 155U",
  "charge": [2.41, 1.98, 1.5], "pression": 0.03,
  "temperature": 72.0, "temperature_critique": 110.0, "chaud": false,
  "frequence_mhz": 1850, "frequence_max_mhz": 4800,
  "plus_actifs": [{"pid": 4242, "nom": "ffmpeg", "coeurs": 6.21, "pourcent": 44.4, "utilisateur": "charles-gamligo",
                   "noyau": false}]
}
```

- `t` : instant du relevé, en secondes depuis l'époque. Les lecteurs l'utilisent pour juger la fraîcheur, sans analyser
  `date`.
- `source` : `"service"` si la mesure vient du service, `"directe"` si elle a été prise par la commande elle-même.
- `pourcent`, `par_coeur` : une décimale.
- `plus_actifs` : au plus 10 programmes, du plus actif au moins actif, en ne gardant que ceux qui utilisent au moins
  0,01 cœur. Deux décimales pour `coeurs`, une pour `pourcent`.
- Tout champ illisible vaut `null`. Une erreur ne doit jamais faire échouer tout l'objet.

## 2. Le service publie la mesure (`gagarine/garde.py`, `systemd/gagarine.service`)

- Toutes les 2 s, dans la boucle du garde et après le travail pour la RAM, le service écrit l'objet `processeur` dans
  `processeur.json`, dans son dossier d'exécution. L'écriture est atomique (fichier temporaire puis renommage) et le fichier
  est en 0644. La mesure couvre le temps écoulé depuis le relevé précédent, soit environ 2 s.
- **Dossier d'exécution** :
  - Unité systemd : `RuntimeDirectory=gagarine` et `RuntimeDirectoryMode=0755`, ce qui donne `/run/gagarine`, en mémoire.
  - Le service écrit dans `$RUNTIME_DIRECTORY`, sinon dans `$GAGARINE_DOSSIER_EXECUTION` (tests, développement), sinon dans
    `/run/gagarine` s'il est administrateur. Sinon, il n'écrit rien.
- **Lecteurs** : ils lisent `$GAGARINE_DOSSIER_EXECUTION/processeur.json`, sinon `/run/gagarine/processeur.json`. La mesure
  est **fraîche** si `t` date de 6 s au plus. Une mesure trop ancienne, absente ou illisible n'est pas utilisée.
- Coût : tous les `/proc/PID/stat` sont lus une fois toutes les 2 s, soit environ 500 petits fichiers. Le service garde
  `pid → ticks` du relevé précédent. Les pids disparus sont oubliés.

## 3. Historique (`gagarine/historique.py`, mesure par minute du garde)

- Chaque ligne par minute ajoute ces champs :
  - `cpu` : % moyen depuis la mesure précédente, par Δ de `/proc/stat` entre les deux minutes (`null` à la première) ;
  - `charge` : charge sur 1 min ;
  - `temperature` ;
  - `actifs` : `[[nom, cœurs], …]`, les 3 noms les plus actifs de la minute. Les Δticks par pid depuis la minute précédente
    sont **additionnés par nom** : les 20 processus `cc1` d'une compilation forment une seule ligne.
- `historique --json` (contrat) :
  - Chaque point ajoute `cpu`, `cpu_max`, `charge` et `temperature`.
    - Pas court : `cpu_max` = `cpu`.
    - Pas long (10 min) : `cpu` = moyenne, `cpu_max` = plus haute minute, `charge` = moyenne, `temperature` = plus haute
      valeur.
  - Au niveau supérieur, `actifs` = `[{nom, pic_coeurs, minutes, cpu_min}]`, au plus 10. `cpu_min` est la somme des cœurs
    sur les minutes, en « minutes de processeur ». La liste est triée par `cpu_min` décroissant.
  - Les anciennes lignes, sans ces champs, donnent `null` et sont ignorées dans les moyennes.
- `historique` (texte) ajoute deux lignes :
  - « Processeur : 18 % en moyenne, au plus 96 % (14:32) ; 72 °C au plus » ;
  - « Les plus actifs : ffmpeg (42 min de processeur), … ».

## 4. Ligne de commande (`gagarine/cli.py`)

- `etat --json` ajoute `processeur` : la mesure du service si elle est fraîche, sinon une mesure directe sur 0,5 s
  (`source: "directe"`), sinon `null`.
- `etat` (texte) ajoute deux lignes après la RAM :
  - « Processeur : 23 % (charge 2,41 pour 14 cœurs) · 72 °C · 1,9 GHz » ;
  - « Plus actifs : ffmpeg 6,2 cœurs, firefox 0,8 cœur, … », avec les 3 plus actifs.
- **Nouvelle commande `gagarine processeur [--json]`**, apportée par une extension `processeur` chargée à la demande.
  - `--json` donne l'objet `processeur`, avec la même source que pour `etat`.
  - Le texte montre :
    - le modèle et les cœurs (« 2 cœurs performants (4 fils) et 10 économes », ou « 14 cœurs ») ;
    - le % total ;
    - une barre par cœur (`cpu0  P  ██████░░░░  62 %`) ;
    - la température (et « chaud » s'il y a lieu) ;
    - la fréquence (actuelle / maximum) ;
    - la charge et la pression ;
    - les 10 plus actifs.

## 5. File d'attente (`gagarine/file.py`, `gagarine/journal.py`)

- `file --etat --json` : chaque travail **en cours** ajoute `coeurs_utilises`, un nombre décimal, ou `null`.
  - Source : Δ `usage_usec` de `cpu.stat`, dans le cgroup de son scope.
  - Intervalle : depuis le relevé précédent, gardé dans `$XDG_RUNTIME_DIR/gagarine/cpu-travaux.json`, s'il a entre 0,5 et
    30 s. Sinon, la commande prend une mesure de 0,3 s.
  - Le texte de `--etat` ajoute « · 6,2 cœurs » au travail en cours.
- L'événement `travail-fin` ajoute :
  - `cpu_s` : le dernier `usage_usec` relevé du scope, en secondes, lu pendant le travail comme le pic mémoire ;
  - `coeurs_moyens` : `cpu_s` / durée.

  Les deux valent `null` si le scope est illisible. La ligne lisible du journal ajoute « 5,4 cœurs en moyenne ».
- Les règles pour faire passer un travail ne changent pas.

## 6. Ligne d'état des sessions Claude (`gagarine/ligne.py`)

- La ligne devient `RAM 77 % · CPU 23 % · file …` : la partie « CPU » se place juste après la RAM.
- Elle lit la mesure fraîche du service, sans mesure directe : la ligne doit rester rapide. Sans mesure fraîche, la partie
  CPU est absente.
- Couleurs : vert au-dessous de 70 %, jaune de 70 à 89 %, rouge à 90 % et plus.
- Si `chaud` : on ajoute la température en rouge (`CPU 97 % 98 °C`).

## 7. Interface (`gagarine/interface/`)

- **Mesure dans le processus** : la source lit `/proc/stat` chaque seconde, avec la RAM. Elle garde :
  - le % total ;
  - le % de chaque cœur ;
  - une file de valeurs pour la courbe des 5 dernières minutes.

  Le reste vient de `etat --json` (`processeur`) : modèle, types de cœurs, température, fréquence, charge, pression, plus
  actifs. Avec un cœur 1.3 (sans `processeur`), le groupe montre seulement ce que la source mesure elle-même.
- **Aperçu** : un groupe « Processeur » juste après « Mémoire vive ».
  - En tête : le % en grand, le détail (« charge 2,4 · 14 cœurs · 1,9 GHz ») et une pastille d'état. Elle dit « Calme » sous
    50 %, « Occupé » de 50 à 89 %, et « Saturé » à 90 % et plus, en moyenne sur 10 s.
  - Un dessin **Cœurs** : une barre verticale par cœur logique, P d'abord puis E, avec une petite étiquette P/E quand le
    processeur est hybride. La couleur suit la charge, comme la jauge.
  - Une légende avec le modèle, la répartition P/E, la température (orange si `chaud`), la fréquence et la pression.
- **Courbe** : une seconde ligne pour le processeur, d'une autre couleur, sur le même axe 0–100 %, avec une légende
  « RAM — Processeur ». Sur 24 h et 7 j, elle utilise `cpu` des points. Un nouveau groupe « Les plus actifs de la période »
  (`actifs`) se place à côté de « Les plus gros de la période ».
- **Programmes** : un groupe « Les plus actifs », avec les 10 de `plus_actifs`.
  - Titre : le nom.
  - Sous-titre : « PID · utilisateur », avec « noyau » s'il y a lieu.
  - À droite : « 1,4 cœur · 10 % ».
- **File d'attente** : un travail en cours affiche « processeur : 6,2 cœurs ». Un travail passé affiche « 5,4 cœurs en
  moyenne ».
- **Captures** : elles sont mises à jour dans `docs/captures/`.

## 8. Indicateur (`gagarine/indicateur/`)

- Étiquette de la barre du haut : « RAM 77 % · CPU 23 % ».
  - Le CPU est mesuré dans le processus, avec `/proc/stat` chaque seconde, en moyenne glissante sur 3 s pour que le chiffre
    ne saute pas.
  - L'icône garde le niveau de la RAM.
- Menu :
  - Une ligne « Processeur : 23 % · charge 2,4 · 72 °C » après la RAM et le swap.
  - Une ligne « Plus actif : ffmpeg (6,2 cœurs) », si un programme utilise au moins 0,5 cœur.
- L'infobulle ajoute le processeur.

## Répartition des fichiers

| Chantier | Fichiers |
|---|---|
| Cœur (1, 2, 3, 4) | `gagarine/processeur.py` (nouveau), `gagarine/garde.py`, `gagarine/historique.py`, `gagarine/cli.py`, `gagarine/systeme.py`, `systemd/gagarine.service`, `tests/test_processeur.py` (nouveau), `tests/test_garde*.py`, `tests/test_historique.py`, `tests/test_cli.py`, `tests/test_commandes.py`, `tests/test_systeme.py`, `tests/test_paquet.py` (pour l'unité seulement) |
| File et ligne (5, 6) | `gagarine/file.py`, `gagarine/journal.py`, `gagarine/ligne.py`, `tests/test_file*.py`, `tests/test_journal*.py`, `tests/test_ligne.py` |
| Interface (7, 8) | `gagarine/interface/`, `gagarine/indicateur/`, `tests/test_interface*.py`, `tests/test_indicateur*.py`, `tests/donnees_*/`, `docs/captures/` |
| Assemblage | `debian/`, `man/`, `README.md`, `CHANGELOG.md`, `site/`, `docs/`, `data/metainfo/`, `gagarine/version.py`, `ROADMAP.md` |

- Personne ne touche `gagarine/reglages.py`, `gagarine/ntfy.py`, `etc/gagarine.conf` ni `tests/commun.py`.
- Chaque chantier lit les contrats ci-dessus, pas le code des autres :
  - la ligne d'état lit `processeur.json` elle-même ;
  - l'interface et l'indicateur analysent `/proc/stat` eux-mêmes, comme ils le font déjà pour `/proc/meminfo`.

## Précisions apportées pendant les chantiers

- **Mesure** (`processeur.py`) :
  - Un programme apparu entre deux relevés n'est compté qu'au relevé suivant. Un numéro repris par un autre programme
    (heure de démarrage différente) n'est jamais confondu avec l'ancien.
  - Le premier relevé n'a rien à comparer : `pourcent`, `par_coeur`, `plus_actifs` et `intervalle_s` valent `null`. La
    mesure directe fait deux relevés à 0,5 s d'écart.
  - Champ ajouté : `coeurs_performants`, nombre de cœurs physiques P (par `topology/core_id`), pour le texte « 2 cœurs
    performants (4 fils) » ; `null` hors processeur hybride. `chaud` vaut `false`, jamais `null`, si la température est
    inconnue.
  - `types` vaut `null` si les listes P et E ne couvrent pas exactement les cœurs logiques.
  - Un capteur devenu illisible est recherché de nouveau au relevé suivant. Pour une zone thermique, la critique vient du
    `trip_point_N_temp` de type `critical`.
  - Une mesure datée de plus de 6 s dans le futur est refusée, comme une trop ancienne. Si `$RUNTIME_DIRECTORY` contient
    plusieurs chemins séparés par `:`, le premier est pris.
  - Coût d'un relevé : environ 27 ms sur le poste chargé (environ 500 lectures de `/proc/PID/stat`), plus que les 10 ms
    visées ; à surveiller.
- **Service** (`garde.py`) :
  - La mesure se fait toutes les 2 s sur l'horloge du garde, à la fin de chaque tour, même quand le tour de RAM sort tôt.
  - Une erreur est écrite une seule fois sur stderr (« erreur (processeur) : … ») ; le garde continue d'essayer.
  - `Garde(..., mesureur=None, dossier_cpu=None)` : sans mesureur, rien n'est mesuré ; sans dossier, rien n'est écrit.
- **Historique** :
  - Par minute, `cpu` est le pourcentage moyen depuis la ligne précédente (différence des totaux de `/proc/stat`) ;
    `charge` est celle du dernier relevé ; `actifs` additionne les ticks par nom sur la minute (3 noms au plus, au moins
    0,01 cœur). Les quatre clés sont toujours écrites, à `null` si absentes.
  - En JSON, au pas long, `cpu` et `charge` sont des moyennes, `cpu_max` et `temperature` des maxima ; les `null` sont
    ignorés. `actifs` : 10 au plus, triés par `cpu_min` décroissant puis par nom.
  - Texte : « Processeur : 18 % en moyenne, au plus 96 % (HH:MM) ; 72 °C au plus », puis « Les plus actifs : nom (N min
    de processeur), … » (5 noms au plus). Rien s'il n'y a pas de données du processeur.
- **Commandes** :
  - `etat` : deux lignes (résumé, plus actifs) juste après la RAM, omises si la mesure n'a pas de pourcentage.
    « (chaud) » suit la température si besoin. En JSON, `processeur` est toujours présent, `null` si rien n'est lisible.
  - `gagarine processeur` : une barre de 10 caractères par cœur (`cpu0  P  ██████░░░░  62 %`), puis température,
    fréquence, charge, pression et plus actifs (« nom (processus PID, utilisateur) : 6,2 cœurs · 44 % », deux décimales
    sous 0,1 cœur).
- **File** :
  - `cpu.stat` est lu par `Poste.cpu_cgroup(chemin)`, qui rend `usage_usec` ou `None`.
  - Seuls les travaux « en cours » avec un `unite` sont mesurés. Les autres ont `coeurs_utilises: null` : travaux en
    attente, tickets d'anciens outils, travail hérité, scope sans cgroup.
  - Le relevé précédent est `{"t", "travaux": {ticket: usage_usec}}`. Il est écrit de façon atomique, dossier en 0700, et
    seulement si `XDG_RUNTIME_DIR` existe. Sans lui, la commande prend toujours une mesure de 0,3 s.
  - Une seule pause de 0,3 s couvre tous les travaux.
  - Valent `null` : un travail absent du relevé précédent, ou un compteur qui recule.
  - Toute mesure de la commande écrit un nouveau relevé.
  - `Releveur` garde le plus haut `usage_usec` vu, comme le pic mémoire. `cpu_s` et `coeurs_moyens` ont 2 décimales.
    `coeurs_moyens` est absent si la durée est nulle, et un travail très court peut n'avoir aucun `cpu_s`.
  - Textes : une décimale, sans « ,0 », « cœur » au singulier sous 2 (« 0,8 cœur », « 1 cœur », « 5,4 cœurs »).
- **Ligne d'état** :
  - Le dossier est `GAGARINE_DOSSIER_EXECUTION` s'il est défini, sinon `/run/gagarine` (exclusif, comme pour le service).
  - La mesure est fraîche si −1 ≤ maintenant − `t` ≤ 6 s.
  - Le fichier est ignoré si ce n'est pas un objet, ou si `t` ou `pourcent` n'est pas un nombre (un booléen est refusé).
  - La température n'apparaît que si `chaud` vaut exactement `true` ; elle est arrondie à l'entier.
  - Couleurs selon le pourcentage affiché, arrondi.
- **Interface** :
  - Pastille Calme / Occupé / Saturé : moyenne des mesures locales des 10 dernières secondes (sous 50 %, 50 à 89 %,
    90 % et plus). Les barres des cœurs ont les mêmes seuils (normal, orange, rouge).
  - Le grand pourcentage et les barres viennent de `/proc/stat`, lu par l'interface ; le `pourcent` du service ne sert
    que tant que la mesure locale n'existe pas. Les cœurs P passent avant les E si `types` a une entrée par cœur.
  - La température est suivie de « (chaud) », en orange, d'après le booléen `chaud` du service, sans recalcul. La
    répartition s'appuie sur `coeurs_performants` : « 2 cœurs performants (P, 4 fils) et 10 économes (E) ».
  - Courbe : le processeur est un trait violet sans remplissage, sur le même axe 0–100 % que la RAM ; légende
    « RAM — Processeur » dans le dessin ; les points sans `cpu` sont ignorés. Le résumé ajoute « processeur au plus X %
    (heure) » ; les plus actifs de la période : 5 lignes.
  - Programmes, « Les plus actifs » : 10 lignes au plus ; « Le poste est calme » s'il n'y a aucun programme ; groupe
    masqué si le service n'a pas d'objet `processeur` (cœur 1.3). Avec un cœur 1.3, l'Aperçu garde le groupe
    Processeur avec la seule mesure locale, sans bandeau d'erreur.
  - Nombre de cœurs : une décimale, deux sous 0,1 (« 0,04 cœur »), comme `gagarine processeur`.
  - File : « processeur : 6,2 cœurs » pour un travail en cours, « 5,4 cœurs en moyenne » pour un travail passé.
- **Indicateur** :
  - `/proc/stat` lu chaque seconde ; le CPU affiché couvre les 3 dernières secondes (4 relevés). Jusqu'au deuxième
    relevé, l'étiquette est « RAM 77 % » seule. Largeur réservée : « RAM 100 % · CPU 100 % ».
  - Menu : « Processeur : 23 % · charge 2,4 · 72 °C » après la RAM et le swap ; « Plus actif » seulement à 0,5 cœur ou
    plus. Infobulle : « Gagarine : RAM normale · processeur 23 % », redessinée quand l'icône change ou toutes les 10 s.
    L'icône suit la RAM seule.
