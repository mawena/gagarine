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

- [ ] Installation du `.deb` (`sudo apt install ./gagarine_<version>_all.deb`, par l'utilisateur)
- [ ] Retrait du garde-RAM sous le compte utilisateur, hook des sessions Claude et consignes mis à jour
- [ ] Vérification du service système (`sudo gagarine essai`)
