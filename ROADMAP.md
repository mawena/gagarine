# Feuille de route — Gagarine

Gagarine empêche la RAM d'un poste Linux de se remplir au point de le figer : il la mesure chaque seconde et, à 95 %,
fige le programme qui grossit le plus vite, le range dans le swap, puis l'arrête si la RAM ne redescend pas.
Il reprend le garde-RAM écrit le 10/11/2026 pour le poste, après deux redémarrages forcés dus à un test qui prenait 9 Go.

## Phase 1 — Fondations

- [x] Dépôt git, git flow (`main`, `develop`), dépôt distant en SSH
- [x] Licence MIT, `.gitignore`, feuille de route

## Phase 2 — Programme `gagarine`

- [x] Mesure de la RAM chaque seconde, quatre fois par seconde au-delà du seuil de vigilance
- [x] Choix du coupable : croissance sur 10 s puis 60 s, à défaut le plus gros ; programmes protégés jamais touchés
- [x] Figer (SIGSTOP), brider (il ne peut plus grossir s'il est repris), ranger dans le swap (`process_madvise` en administrateur, `memory.reclaim` sinon)
- [x] Arrêt : RAM toujours au-dessus du seuil une fois le rangement fini, urgence à 98 %, figé depuis 10 min sans décision
- [x] Notifications sur les sessions graphiques ouvertes, avec les boutons « Arrêter » et « Reprendre »
- [x] Fichier de réglages `/etc/gagarine.conf`
- [x] Commandes `surveiller`, `etat`, `reprendre`, `arreter`, `essai`, `version`
- [x] Journal des événements (une ligne JSON par événement), lisible par les outils du poste

## Phase 3 — Tests

- [x] Tests unitaires des décisions, sur des mesures simulées
- [x] Essai réel sans privilèges : seuil abaissé, programme d'essai qui grossit (`gagarine essai`)

## Phase 4 — Paquet Debian

- [x] Service systemd système : administrateur, jamais arrêté par le noyau, verrouillé en RAM, prioritaire
- [x] Paquet `.deb` : installation, service activé, réglages conservés aux mises à jour, sommes SHA-256, lintian
- [x] Page de manuel `gagarine(8)`
- [x] README complet

## Phase 5 — Première version

- [x] CHANGELOG, release git flow, tag, `main`, `develop` et tags poussés
- [x] Release GitHub avec le `.deb` et `SHA256SUMS`

## Phase 6 — Mise en place sur le poste

- [x] Installation du `.deb` (`sudo apt install ./gagarine_<version>_all.deb`, par l'utilisateur)
- [x] Retrait du garde-RAM sous le compte utilisateur, hook des sessions Claude et consignes mis à jour
- [x] Vérification du service système (`sudo gagarine essai` : figé, rangé dans le swap en 1 s, arrêté)
- [x] Swap compressé en RAM (zram de la taille de la RAM, zstd, avant le swap disque) et réglages du noyau associés

## Phase 7 — Code rangé et commandes pour l'interface (1.1.0)

Conception : [docs/conception-1.1.0.md](docs/conception-1.1.0.md).

- [x] Paquet Python `gagarine/` (un module par rôle), lanceur `bin/gagarine`, tests adaptés, comportement inchangé
- [x] `gagarine etat --json`, `gagarine journal [-n N] [--json]`
- [x] `gagarine reglages [--json]` et `--ecrire CLE=VALEUR` (sur place, commentaires gardés), rechargement sur SIGHUP
- [x] `gagarine service demarrer|arreter|redemarrer|activer|desactiver`
- [x] Notifications rattachées à l'application (nom, icône, entrée de bureau)

## Phase 8 — File d'attente des travaux lourds

- [x] `gagarine file "<nom>" <Go> <cœurs> [--apres PID] -- commande`, limite mémoire du noyau, codes de sortie
- [x] Compatibilité totale avec l'outil `file-attente` et les copies des projets (mêmes tickets, même dossier)
- [x] `gagarine file --etat [--json]`, `gagarine file annuler`, journal des travaux
- [x] Tests de la file

## Phase 9 — Interface graphique

- [x] Fenêtre GTK 4 + libadwaita : vue d'ensemble (jauge, courbe, swap, service)
- [x] Programmes figés (Reprendre, Arrêter) et plus gros programmes
- [x] File d'attente (travaux, Annuler, Voir la sortie, Nouveau travail)
- [x] Journal (filtre, recherche)
- [x] Réglages, service et essai, par `pkexec` et une politique polkit
- [x] Icône, entrée de bureau, metainfo, À propos, captures d'écran

## Phase 10 — Indicateur de la barre du haut

- [x] Indicateur GTK 3 + AyatanaAppIndicator : RAM en %, icône selon le niveau, menu d'actions
- [x] Démarrage automatique, masquer ou réafficher (aussi depuis l'interface)

## Phase 11 — Paquet et documentation 1.1.0

- [x] Paquet : nouveaux fichiers, dépendances recommandées, validation du `.desktop` et du metainfo, lintian
- [x] Swap compressé en RAM (zram) et réglages du noyau fournis par le paquet, appliqués à l'installation
- [x] README (file d'attente, interface, indicateur, captures), pages de manuel, CHANGELOG
- [x] Release git flow 1.1.0, release GitHub avec le `.deb`, « About » GitHub à jour

## Phase 12 — Mise en place de la 1.1.0 sur le poste

- [x] Installation du `.deb` 1.1.0 (par l'utilisateur)
- [x] `~/.local/bin/file-attente` renvoie vers `gagarine file` ; hook et consignes des sessions Claude à jour
- [x] Plus rien d'éparpillé hors de Gagarine : ancien garde-RAM, script et réglages zram de `/etc` (remplacés par ceux du paquet) retirés
- [x] Essai réel de la file sur le poste : ordre, attente, annulation, ancien outil dans la même file
- [x] Installation du correctif 1.1.1 (par l'utilisateur)

## Phase 13 — Socle de la 1.2.0

Conception : [docs/conception-1.2.0.md](docs/conception-1.2.0.md).

- [x] Réglages de la 1.2.0 (anticipation, pression, fin de travail, ntfy, correction des annonces, ménage, blocage Claude)
- [x] Alertes sur le téléphone (ntfy), commandes apportées par des extensions

## Phase 14 — File : mémoire apprise et fin de travail

- [x] Pic mémoire de chaque travail, mémoire des travaux, correction des annonces trop basses, `gagarine file appris`
- [x] Notification de fin de travail (bureau et ntfy)

## Phase 15 — Garde : agir plus tôt, historique

- [x] Anticipation (temps avant saturation) et pression mémoire du noyau
- [x] Historique de 7 jours, `gagarine historique`
- [x] Alertes ntfy des programmes figés ou arrêtés ; journal lisible pour les nouveaux événements

## Phase 16 — Ménage des inactifs

- [x] Démons Gradle et Kotlin, modèles LM Studio et Ollama, serveurs de développement ; `gagarine menage`
- [x] Ménage automatique (minuteur utilisateur)

## Phase 17 — Hook Claude bloquant, plafonds des applications

- [x] `gagarine hook-claude` : refuse les travaux lourds hors de la file et donne la commande corrigée
- [x] `gagarine applications` : plafond mémoire par application du bureau

## Phase 18 — Mises à jour par apt

- [x] Dépôt APT signé sur GitHub Pages, `scripts/publier-apt.sh`
- [x] Source APT et clé installées par le paquet

## Phase 19 — Interface et indicateur 1.2.0

- [x] Réglages, historique sur la courbe, ménage, applications, mémoire apprise, travaux passés, journal
- [x] Indicateur : ménage

## Phase 20 — Paquet, documentation et release 1.2.0

- [x] Paquet (nouveaux fichiers, minuteur utilisateur, source APT), pages de manuel, README, CHANGELOG
- [x] Release 1.2.0, release GitHub, dépôt APT publié

## Phase 21 — Mise en place de la 1.2.0 sur le poste

- [x] Installation (par l'utilisateur), puis mises à jour par `apt upgrade`
- [x] Hook des sessions Claude remplacé par `gagarine hook-claude`
- [ ] Scripts vidéo de Cofidesk, Cofidesk-gpu et Report passés par `gagarine file` (après les vidéos en cours)

## Phase 22 — Socle de la 1.3.0

Conception : [docs/conception-1.3.0.md](docs/conception-1.3.0.md).

- [x] Réglages `file_paralleles`, `fuite_seuil`, `fuite_notifier` ; extension `ligne`

## Phase 23 — File plus rapide

- [x] Plusieurs travaux à la fois quand la mémoire et les cœurs suffisent (`file_paralleles`), sans famine
- [x] Priorités (`--priorite urgent|normal|fond`, `gagarine file priorite`)
- [x] Fuite d'un lancement à l'autre (tendance de la mémoire apprise)

## Phase 24 — Fuites repérées pendant qu'un programme tourne

- [x] Pente sur 5 minutes, événement `fuite`, notification (et ntfy), `fuites` dans `etat --json`

## Phase 25 — État du poste dans Claude Code

- [x] `gagarine ligne-etat [--avec COMMANDE]`

## Phase 26 — Interface, paquet et release 1.3.0

- [x] Interface et indicateur (priorités, travaux en parallèle, fuites, nouveaux réglages)
- [ ] Paquet, pages de manuel, README, CHANGELOG, site ; release 1.3.0, GitHub et dépôt APT
- [ ] Mise en place : ligne d'état de Claude Code branchée sur `gagarine ligne-etat`
