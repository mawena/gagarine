# Journal des modifications

Toutes les modifications notables de ce projet sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le projet respecte le
[versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

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
