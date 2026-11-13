# Journal des modifications

Toutes les modifications notables de ce projet sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le projet respecte le
[versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

## [1.5.1] - 2026-11-13

### Corrigé

- **Le ménage ne relance plus LM Studio.** `gagarine menage --json`, que l'indicateur lance toutes les 2 secondes,
  appelait `lms ps` dès qu'une ligne de commande citait LM Studio : une commande Bash, un `tail` d'un journal de
  `~/.lmstudio`, un éditeur, une session Claude lancée avec son message en argument, ou les restes de LM Studio pendant
  sa fermeture (`chrome_crashpad_handler`, le `node` interne). Or `lms` démarre LM Studio quand il ne tourne pas
  (`--run-as-service`, environ 360 Mo) : LM Studio revenait dans la seconde après chaque fermeture.
  - LM Studio n'est reconnu que par son vrai programme, d'après `/proc/<pid>/exe` : le processus principal de
    l'application (`lm-studio` sans `--type=`) ou le démon sans interface `llmster`. Une ligne de commande ne compte
    plus jamais.
  - `lms` n'est appelé que si son serveur interne répond : le verrou `~/.lmstudio/.internal/llmster-pid.lock` et
    `http-server.json` désignent la même instance vivante, et son port accepte une connexion. Un verrou périmé, resté
    après un arrêt brutal, ne suffit plus.
  - `lms ps` n'est plus appelé à chaque passage : la liste des modèles est gardée 60 s pour la même instance
    (`~/.local/state/gagarine/lmstudio.json`), et oubliée après un déchargement. Décharger revérifie tout juste avant.
  - Filet de sécurité : chaque appel à `lms` porte la variable `GAGARINE_APPEL_LMS`. Si une instance de LM Studio
    démarre malgré tout pendant l'appel, Gagarine l'arrête aussitôt avec sa famille (fils, crashpad, `node` interne) :
    elle est figée, puis reçoit SIGKILL (LM Studio ignore SIGTERM). C'est fait même si l'appel a été interrompu, au
    passage suivant, et c'est écrit au journal (événement `lmstudio-arrete`). Une instance ouverte par l'utilisateur
    n'est jamais touchée.

## [1.5.0] - 2026-11-11

Nouveau design de l'interface : elle prend toute la largeur de la fenêtre au lieu d'une colonne étroite et centrée, et
dit d'un coup d'œil si le poste va bien.

### Modifié

- **Navigation** : une barre latérale remplace les onglets du haut. Elle montre l'état de chaque page en direct (RAM en
  %, programmes figés, travaux en cours) et l'état du service. En fenêtre étroite, elle se replie derrière un bouton
  retour.
- **Pages en pleine largeur**, alignées à gauche, sur deux ou trois colonnes quand la fenêtre est large, et empilées
  quand elle est étroite.
- **Aperçu** :
  - une phrase d'état avec son voyant (« Le poste respire : 4,8 Go libres, processeur calme », « python3 est figé : il
    attend votre décision ») ;
  - une échelle graduée de la RAM avec ses zones de vigilance, de seuil et d'urgence ;
  - les cœurs du processeur, les cœurs performants dessinés plus larges ;
  - la courbe sur toute la largeur ;
  - les plus gros et les plus actifs en ce moment, ou sur la période.
- **Programmes** en deux colonnes ; les sections vides disent ce qui se passe.
- **File d'attente** :
  - les travaux en colonnes alignées (RAM annoncée, cœurs, processeur, durée), avec une pastille d'état et un motif
    d'attente lisible ;
  - la mémoire apprise et les travaux passés côte à côte.
- **Journal** : les événements sont groupés par jour, l'heure à gauche comme une frise, et l'icône est teintée selon la
  gravité. La recherche reste visible en haut.
- **Réglages** en deux colonnes ; la barre « Enregistrer » n'apparaît que s'il y a une modification.
- Les boutons destructifs des listes (Arrêter, Annuler, Décharger) sont plats au texte rouge ; seule la confirmation
  garde le bouton rouge plein.
- Couleurs et chiffres :
  - le vert, l'ambre et le rouge ne disent que l'état ; la RAM est en bleu et le processeur en violet, en thème clair
    comme en sombre ;
  - chiffres tabulaires pour toutes les valeurs qui changent.
- Captures d'écran refaites, en grande fenêtre, en thème clair et en fenêtre étroite.

### Corrigé

- La boîte « À propos » affichait la version 1.1.0.
- `GAGARINE_DOSSIER_EXECUTION`, quand il est défini, est le seul dossier où la mesure du processeur est lue : les tests
  ne lisent plus le service installé.

## [1.4.0] - 2026-11-11

Gagarine montre aussi le processeur : utilisation de chaque cœur, programmes les plus actifs, température et fréquence,
partout où il montrait déjà la RAM. Il n'agit jamais sur un programme à cause du processeur : ses actions restent
réservées à la mémoire.

### Ajouté

- **Mesure du processeur** par le service, toutes les 2 secondes (`/run/gagarine/processeur.json`) : utilisation totale
  et de chaque cœur, cœurs performants et économes des processeurs hybrides d'Intel, programmes les plus actifs (en
  cœurs utilisés, tous utilisateurs), température, fréquence, charge et pression du noyau.
- **`gagarine processeur [--json]`** : le détail, avec une barre par cœur ; `gagarine etat` et `etat --json` ajoutent le
  processeur (`processeur`).
- **Historique** : processeur moyen et maximal, charge, température et programmes les plus actifs de chaque minute
  (`cpu`, `cpu_max`, `charge`, `temperature`, `actifs`).
- **File d'attente** : cœurs utilisés par chaque travail en cours (`coeurs_utilises`), temps de processeur et cœurs en
  moyenne à la fin d'un travail (`cpu_s`, `coeurs_moyens` dans `travail-fin`).
- **Ligne d'état des sessions Claude Code** : `CPU 23 %` après la RAM, et la température quand le processeur chauffe.
- **Interface** : groupe « Processeur » dans l'aperçu (barres des cœurs, température, fréquence), courbe de la RAM et du
  processeur, programmes les plus actifs (en ce moment et sur la période), cœurs des travaux de la file.
- **Indicateur** : « RAM 77 % · CPU 23 % » dans la barre du haut, processeur et programme le plus actif dans le menu.

### Modifié

- Le service a un dossier d'exécution, `/run/gagarine` (`RuntimeDirectory`).
- Captures d'écran refaites, avec un nom d'utilisateur d'exemple.

## [1.3.0] - 2026-11-10

La file d'attente fait passer plusieurs travaux à la fois quand ils tiennent, les fuites de mémoire se voient avant de
remplir la RAM, et chaque session Claude Code affiche l'état du poste.

### Ajouté

- **File plus rapide** :
  - plusieurs travaux tournent en même temps quand la mémoire (en réservant ce que chaque travail en cours peut encore
    prendre) et les cœurs suffisent : réglage `file_paralleles`, 2 par défaut (1 = comme avant) ;
  - **priorités** : `gagarine file --priorite urgent|normal|fond …`, `gagarine file priorite PID|TICKET NIVEAU` ; un travail
    ne double un autre que si celui-ci ne tient pas, et jamais un travail qui attend depuis plus de 10 minutes ;
  - les tickets des anciens outils (copies `file-attente.cjs`) ne sont jamais doublés ;
  - `file --etat` dit pourquoi chaque travail attend (`motif`, `besoin_go`, `priorite`, `en_cours`, `paralleles`).
- **Fuites de mémoire** :
  - en direct : un programme qui grossit d'au moins `fuite_seuil` Mo/min (100) depuis 5 minutes est signalé, avec la
    pente et l'heure à laquelle la RAM serait pleine (événement `fuite`, notification et ntfy si `fuite_notifier`,
    `fuites` dans `etat`) ; aucune action sur le programme ;
  - d'un lancement à l'autre : la file signale un travail dont le pic monte à chaque lancement ou qui dépasse sa limite
    plusieurs fois (`tendance` dans `file appris`, événement `fuite-travail`).
- **Ligne d'état de Claude Code** : `gagarine ligne-etat [--avec "COMMANDE"]` affiche `RAM 72 % · file 3 (2 en cours)
  · 1 figé · fuite : python3`, à côté d'une ligne d'état existante.
- **Interface et indicateur** : priorités et motifs dans la file, travaux en parallèle, « Priorité… », fuites en cours
  et badge « fuite », tendances de la mémoire apprise, nouveaux réglages ; l'indicateur signale les fuites.

### Modifié

- La commande `gagarine` démarre plus vite (modules chargés à la demande) : le hook des sessions Claude et la ligne
  d'état répondent plus tôt.

## [1.2.2] - 2026-11-10

### Documentation

- Le README met l'installation par le dépôt APT en tête : en une ligne (`installer.sh` du site), pas à pas (clé,
  dépôt, `apt install`), vérification, mise à jour par `sudo apt update && sudo apt upgrade`, et le `.deb` des releases
  en dernier recours ; le lien du site https://mawena.github.io/gagarine/ est en haut de la page.

## [1.2.1] - 2026-11-10

### Corrigé

- `gagarine applications` montrait Chrome et VS Code deux fois : sous GNOME, ils tournent à la fois dans les scopes du
  lanceur du bureau (`app-gnome-…`) et dans les leurs (`app-com.google.Chrome-…`). Chaque application n'a plus qu'une
  ligne, avec sa mémoire et ses instances additionnées ; son plafond s'applique à toutes ses familles de scopes, quel
  que soit l'identifiant donné. Le JSON ajoute `ids` et `prefixes`.
- Les plafonds s'affichent à la française (« 1,5 Go » au lieu de « 1.5 Go »).

## [1.2.0] - 2026-11-10

Gagarine empêche maintenant les incidents à la source : un travail lourd ne peut plus échapper à la file depuis une
session Claude, une annonce trop basse est corrigée, les programmes inactifs sont rangés, et le garde agit avant 95 %
quand la mémoire s'emballe. Il se met à jour par `apt upgrade`.

### Ajouté

- **Mémoire apprise** par la file d'attente :
  - le pic mémoire de chaque travail (RAM + swap) est relevé et retenu, avec les 5 derniers pics ;
  - si un travail annonce bien moins que ce qu'il a déjà pris, la file le dit et corrige l'annonce, au plus à 60 % de la
    RAM (réglage `file_ajuster`) ;
  - `gagarine file appris [--json]` ; `pic_go` dans les événements de fin de travail.
- **Notification de fin de travail** : terminé, échoué ou arrêté par sa limite, pour les travaux d'au moins 30 s
  (réglages `notifications_fin`, `notification_fin_min`).
- **Alertes sur le téléphone** par ntfy :
  - programmes figés ou arrêtés, fin des travaux (réglage `ntfy`, vide par défaut) ;
  - `gagarine ntfy essai SUJET`.
- **Agir avant 95 %** :
  - **anticipation** : au-delà de la vigilance, le programme qui remplirait la RAM jusqu'au seuil en moins de 15 s est
    figé tout de suite (réglage `anticipation`) ;
  - **pression** : le garde agit aussi quand le noyau passe plus de 20 % de son temps bloqué faute de mémoire
    (`/proc/pressure/memory`, réglage `pression`) ;
  - le champ `declencheur` des événements dit ce qui a fait agir.
- **Historique** de 7 jours (une mesure par minute) :
  - `gagarine historique [--depuis 5min|1h|24h|7j] [--json]` : RAM, incidents, plus gros programmes, travaux ;
  - courbe sur 24 h et 7 jours dans l'interface.
- **Ménage des programmes inactifs** : démons Gradle et Kotlin, modèles LM Studio et Ollama, serveurs de développement.
  - `gagarine menage [--json]`, `gagarine menage arreter ID…` ;
  - ménage automatique toutes les 2 minutes par un minuteur utilisateur (réglages `menage_auto`, `menage_delai`) ;
  - les serveurs de développement ne sont jamais arrêtés tout seuls.
- **Plafond mémoire des applications du bureau** (`MemoryHigh`) : `gagarine applications [--json]`,
  `gagarine applications plafond ID GO|aucun`. L'application est freinée et rangée dans le swap, jamais arrêtée.
- **Hook des sessions Claude Code** : `gagarine hook-claude [--tester "CMD"]`.
  - Il donne les mêmes rappels que l'ancien hook du poste.
  - Il **refuse** les travaux lourds lancés hors de la file (tests, compilations, `ffmpeg`, `make deb`…) et donne la
    commande corrigée, avec la RAM apprise (réglage `claude_bloquer`).
- **Dépôt APT signé** sur GitHub Pages (`https://mawena.github.io/gagarine/apt`) : le paquet installe sa source et sa
  clé, puis `apt upgrade` suffit. Publication par `make apt` (`scripts/publier-apt.sh`).
- **Site** refait (https://mawena.github.io/gagarine/), avec un script d'installation en une ligne.
- **Interface** :
  - Aperçu : courbe sur 24 h ou 7 jours ;
  - Programmes : Ménage et Applications du bureau ;
  - File d'attente : Mémoire apprise et Travaux passés ;
  - Réglages : toutes les nouvelles clés, avec un essai ntfy ;
  - Journal : les nouveaux événements.
- **Indicateur** : entrée « Ménage » avec ses actions.

### Modifié

- Les commandes de `cli.py` peuvent venir de modules à part (extensions), ce qui garde chaque fonction dans son fichier.
- `gagarine journal` affiche les nouveaux champs : déclencheur, pic, annonce corrigée, ménage.

## [1.1.1] - 2026-11-10

### Corrigé

- `gagarine journal` affichait « (RAM 0.5 %) » pour un travail de la file : c'est la RAM **annoncée**, en Go. Les
  événements de la file s'affichent maintenant avec leurs unités : « 0.5 Go annoncés, 1 cœur, limite 2.5 Go »,
  « code 0, 40 s », « annulé en attente », « annoncé 4 Go, limite 8 Go dépassée ».
- Un travail annulé (ou arrêté en cours) par `gagarine file annuler` l'écrit maintenant dans sa sortie, au lieu de
  s'arrêter sans un mot.

## [1.1.0] - 2026-11-10

Gagarine réunit maintenant tout ce qui protège la mémoire du poste : le garde, la file d'attente des travaux lourds et le
swap compressé en RAM, avec une interface graphique et un indicateur dans la barre du haut.

### Ajouté

- **Interface graphique** `gagarine-interface` (GTK 4 + libadwaita), application « Gagarine » du bureau, en cinq pages :
  - Aperçu : jauge de la RAM avec ses repères, courbe des cinq dernières minutes, swap, état du service ;
  - Programmes : programmes figés avec Reprendre et Arrêter, dix plus gros programmes ;
  - File d'attente : travaux, Annuler, Voir la sortie, Nouveau travail ;
  - Journal : filtre et recherche ;
  - Réglages : réglages, service et essai.
  Les actions d'administrateur passent par polkit (`pkexec`), et l'interface reste fluide même RAM pleine.
- **Indicateur de la barre du haut** `gagarine-indicateur` (GTK 3 + AyatanaAppIndicator) :
  - la RAM en %, avec une icône selon le niveau ;
  - un menu pour reprendre ou arrêter les programmes figés et ouvrir Gagarine ;
  - lancé à l'ouverture de session, `--masquer` / `--afficher`.
- **File d'attente des travaux lourds** intégrée : `gagarine file "<nom>" <Go> <cœurs> [--apres PID] -- commande`,
  `gagarine file --etat [--json]`, `gagarine file annuler`.
  - Chaque travail attend son tour, puis tourne dans sa limite mémoire du noyau : max(2 × annoncé, annoncé + 2 Go), 512 Mo
    de swap, code 137 s'il la dépasse.
  - Les tickets sont compatibles avec l'outil `file-attente` et ses copies dans les projets.
  - Les travaux sont notés dans le journal.
- **Swap compressé en RAM** fourni par le paquet :
  - zram de la taille de la RAM, en zstd, utilisé avant le swap disque ;
  - réglages du noyau adaptés (`swappiness` 180, `page-cluster` 0, `watermark_scale_factor` 125,
    `watermark_boost_factor` 0) ;
  - appliqués dès l'installation, sans redémarrer.
- Commandes :
  - `gagarine etat --json` ;
  - `gagarine journal [-n N] [--json]`, qui fusionne les journaux du service, de l'utilisateur et de la file ;
  - `gagarine reglages [--json]` et `gagarine reglages --ecrire CLE=VALEUR…`, qui modifie `/etc/gagarine.conf` sur place en
    gardant les commentaires ;
  - `gagarine service demarrer|arreter|redemarrer|activer|desactiver`.
- Rechargement des réglages sans redémarrer le service (SIGHUP, `systemctl reload gagarine`), sans perdre son état.
- Notifications rattachées à l'application (nom, icône), icône originale, metainfo AppStream, pages de manuel
  `gagarine-interface(1)` et `gagarine-indicateur(1)`.

### Modifié

- Le code est rangé en paquet Python `gagarine/`, un module par rôle (installé dans `/usr/share/gagarine`), au lieu d'un
  seul fichier ; les commandes existantes ne changent pas.
- Le paquet dépend de `systemd-zram-generator` et recommande les bibliothèques de l'interface et de l'indicateur.

## [1.0.0] - 2026-11-10

Première version : un garde-RAM qui empêche la mémoire du poste de se remplir au point de le figer. Il reprend le
garde-RAM écrit le même jour pour le poste, après deux redémarrages forcés par un test qui avait grossi jusqu'à 9 Go.

### Ajouté

- **Surveillance de la RAM** chaque seconde, quatre fois par seconde au-delà de 85 % (`vigilance`) ; la RAM utilisée est
  `100 % − MemAvailable / MemTotal`, le cache récupérable comptant comme libre.
- **Choix du coupable** : le programme dont la mémoire (RAM + swap) a le plus augmenté sur 10 s, puis sur 60 s, à défaut
  le plus gros. Le bureau (gnome-shell, Xorg, le son, D-Bus…), systemd, les services critiques (réseau, connexion, apt,
  dpkg, Docker…) et Gagarine lui-même ne sont jamais touchés ; la clé `proteges` en ajoute d'autres.
- **À 95 % (`seuil`)** : le programme est **figé** (SIGSTOP), **bridé** (déplacé dans un scope systemd à lui : repris,
  il ne peut plus grossir que de 256 Mo, `reste_si_repris`), puis **rangé dans le swap** :
  - en administrateur, par `process_madvise(MADV_PAGEOUT)`, qui ne touche que ce programme ;
  - sinon, par `memory.reclaim` de son groupe d'origine.
- **Arrêt** (SIGKILL) si la RAM n'est pas redescendue sous le seuil une fois le rangement fini (10 s au moins, 60 s au
  plus : `grace`, `vidage_max`), si elle atteint 98 % (`urgence`), ou si le programme reste figé 10 min sans décision
  (`delai_fige`).
- **Notifications** sur chaque session graphique ouverte, avec les boutons « Arrêter » et « Reprendre ».
- **Commandes** : `surveiller [--config FICHIER]`, `etat`, `reprendre PID`, `arreter PID` (seulement pour un programme
  figé par Gagarine), `essai` (auto-test réel et sans risque : un programme d'essai grossit, il doit être figé, rangé dans
  le swap puis arrêté ; refusé au-delà de 80 % de RAM) et `version`.
- **Réglages** dans `/etc/gagarine.conf` (conservé aux mises à jour), remplaçables par `--config`, `GAGARINE_CONFIG` et
  les variables `GAGARINE_*`. Une valeur invalide est ignorée avec un avertissement, sans faire tomber le service.
- **Journal des événements** `/var/lib/gagarine/evenements.jsonl` (une ligne JSON par événement, en UTC, renommé au-delà
  de 512 Ko) et liste des programmes figés `figes.json`, gardée d'un redémarrage du service à l'autre.
- **Service systemd** `gagarine.service` : lancé au démarrage, jamais choisi par le noyau comme victime
  (`OOMScoreAdjust=-1000`), prioritaire, verrouillé en RAM, borné à 128 Mo sans swap, relancé en une seconde.
- **Paquet Debian** `gagarine_1.0.0_all.deb` (service activé à l'installation), page de manuel `gagarine(8)`, README et
  70 tests unitaires.
