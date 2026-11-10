# Journal des modifications

Toutes les modifications notables de ce projet sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le projet respecte le
[versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

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
