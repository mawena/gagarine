# Gagarine

Gagarine est un garde-RAM pour Linux : il empêche la mémoire vive d'un poste de se remplir au point de le figer. Il
réunit tout ce qui protège la mémoire du poste : le **garde** qui fige le programme qui s'emballe, une **file d'attente**
pour les travaux lourds, un **swap compressé en RAM**, une **interface graphique** et un **indicateur** dans la barre du
haut.

![Aperçu de l'interface de Gagarine](docs/captures/apercu.png)

## À quoi ça sert

Quand un programme grossit sans fin (fuite de mémoire, test qui s'emballe, rendu trop lourd), il remplit la RAM, puis le
swap. Le poste passe alors son temps à échanger avec le disque : l'écran ne répond plus pendant des minutes, et il faut
souvent forcer le redémarrage. C'est arrivé le 02/11/2026, avec deux travaux lourds lancés en même temps, puis deux fois
le 10/11/2026 : un test qui avait grossi jusqu'à 9 Go a rempli les 14 Go de RAM puis les 10 Go de swap, et le poste est
resté figé.

Gagarine agit sur trois plans :

- **avant** : la file d'attente fait passer les travaux lourds un par un, quand la RAM et les processeurs suffisent, et
  enferme chacun dans une limite mémoire du noyau ;
- **pendant** : il mesure la RAM chaque seconde et, à 95 %, **fige** le programme qui grossit le plus vite, le **bride**,
  le **range dans le swap**, puis l'**arrête** si la RAM ne redescend pas. Le bureau et les services critiques ne sont
  jamais touchés, et des notifications proposent « Arrêter » ou « Reprendre » ;
- **en réserve** : un swap compressé en RAM (zram) absorbe vite ce qui déborde, sans écrire sur le disque.

## Installation

Pour Ubuntu 24.04 (noyau 6.8, cgroup v2, systemd, GNOME), ou toute distribution Debian équivalente.

1. Télécharger `gagarine_<version>_all.deb` (et `SHA256SUMS`) depuis les
   [releases GitHub](https://github.com/mawena/gagarine/releases).
2. Vérifier la somme, puis installer :

   ```bash
   sha256sum -c SHA256SUMS --ignore-missing
   sudo apt install ./gagarine_<version>_all.deb
   ```

3. Vérifier que tout marche, sans risque :

   ```bash
   sudo gagarine essai
   ```

À l'installation :

- le service `gagarine.service` est activé et démarré (redémarré à la mise à jour, arrêté à la suppression) ;
- le swap compressé en RAM (zram) est créé et ses réglages du noyau appliqués, sans redémarrer ;
- l'application **Gagarine** apparaît dans les applications, et l'indicateur se lance à la prochaine ouverture de session
  (ou tout de suite avec `gagarine-indicateur &`).

Les réglages (`/etc/gagarine.conf`) sont conservés aux mises à jour. L'interface et l'indicateur demandent des paquets
recommandés (`python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`, `gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1`,
`pkexec`), installés d'office par `apt` ; s'il en manque un, ils le disent.

## Interface graphique

Lancer **Gagarine** depuis les applications, ou `gagarine-interface` (option `--page=apercu|programmes|file|journal|reglages`).
Cinq pages :

| Page | Contenu |
|---|---|
| **Aperçu** | jauge de la RAM avec les repères (vigilance, seuil, urgence), courbe des cinq dernières minutes, swap (zram et disque), état du service |
| **Programmes** | programmes figés, avec **Reprendre** et **Arrêter** ; les dix plus gros programmes (les programmes protégés sont signalés) |
| **File d'attente** | travaux en cours et en attente, **Annuler**, **Voir la sortie**, **Nouveau travail…** |
| **Journal** | tous les événements (garde, file, service), filtre par type et recherche |
| **Réglages** | seuils, délais, bridage, notifications, programmes protégés ; service (démarrer, arrêter, redémarrer) ; essai ; affichage de l'indicateur |

Les actions d'administrateur (réglages, service, essai en administrateur, programme d'un autre utilisateur) passent par
la fenêtre de mot de passe du système (polkit), une seule fois pour quelques minutes. L'interface reste fluide même quand
la RAM est pleine : tout ce qu'elle demande au système se fait en arrière-plan, avec un délai maximal.

| | |
|---|---|
| ![Programmes figés](docs/captures/programmes.png) | ![File d'attente](docs/captures/file.png) |
| ![Journal](docs/captures/journal.png) | ![Réglages](docs/captures/reglages.png) |

*Captures faites avec des données d'exemple.*

## Indicateur de la barre du haut

`gagarine-indicateur` affiche la RAM en pourcentage dans la barre du haut, avec une icône qui change selon le niveau
(normal, vigilance, alerte). Son menu montre la RAM, le swap et les travaux en file, et permet de **reprendre** ou
d'**arrêter** chaque programme figé, d'**ouvrir Gagarine** ou de **masquer l'indicateur**. Il se lance à l'ouverture de
session ; `gagarine-indicateur --masquer` ou `--afficher` (ou l'interrupteur des réglages) le désactive ou le réactive.

## File d'attente des travaux lourds

Un travail lourd (rendu vidéo, voix de synthèse, compilation, longue suite de tests, modèle local…) passe par la file :

```bash
gagarine file "rendu vidéo" 4 4 -- ffmpeg -i entree.mov sortie.mp4
#             nom           Go cœurs  commande (plusieurs : sh -c "a && b")
```

- Il attend son tour : aucun travail plus ancien dans la file, assez de RAM disponible, assez de cœurs libres
  (`--apres PID` attend aussi la fin d'un autre processus).
- Il tourne ensuite dans sa propre **limite mémoire**, imposée par le noyau : max(2 × la RAM annoncée, RAM annoncée
  + 2 Go), et 512 Mo de swap au plus. S'il la dépasse, seul ce travail est arrêté (code 137), et le poste n'a rien. Il
  faut donc annoncer la RAM qu'il lui faut vraiment.
- Le code de sortie est celui de la commande. Un travail lancé par un autre travail de la file passe directement.
- `gagarine file --etat` montre la file ; `gagarine file annuler PID|TICKET` annule un travail.

La file est commune à tout le poste : ses tickets (`/tmp/file-attente-travaux-lourds`) sont les mêmes que ceux de l'ancien
outil `file-attente` et de ses copies dans les projets, qui continuent de partager la même file.

## Utilisation en ligne de commande

| Commande | Effet |
|---|---|
| `gagarine etat [--json]` | RAM, swap, service, programmes figés, plus gros programmes, file, derniers événements |
| `gagarine reprendre PID` | relance un programme figé par Gagarine (il reste bridé) |
| `gagarine arreter PID` | arrête un programme figé par Gagarine |
| `gagarine file …` | file d'attente des travaux lourds (voir plus haut) |
| `gagarine journal [-n N] [--json]` | les derniers événements, tous journaux fusionnés |
| `gagarine reglages [--json]` | réglages en vigueur |
| `sudo gagarine reglages --ecrire CLE=VALEUR…` | modifie `/etc/gagarine.conf` (commentaires gardés) et recharge le service |
| `sudo gagarine service ACTION` | `demarrer`, `arreter`, `redemarrer`, `activer` ou `desactiver` le service |
| `gagarine essai` | auto-test sans risque : un programme d'essai grossit, il doit être figé, rangé dans le swap puis arrêté (refusé au-delà de 80 % de RAM ; avec `sudo` pour tester le mode administrateur) |
| `gagarine surveiller [--config FICHIER]` | lance le garde au premier plan (c'est ce que fait le service) |
| `gagarine version` | affiche la version |

Les sorties `--json` sont stables d'une version à l'autre : l'interface et l'indicateur s'en servent. Journal du service :
`journalctl -u gagarine -f`. Pages de manuel : `man gagarine`, `man gagarine-interface`, `man gagarine-indicateur`.

## Configuration

Fichier `/etc/gagarine.conf`, au format INI, section `[gagarine]` : à modifier depuis la page **Réglages**, avec
`sudo gagarine reglages --ecrire seuil=94`, ou à la main puis `sudo systemctl reload gagarine` (le service relit ses
réglages sans perdre son état).

| Clé | Défaut | Rôle |
|---|---|---|
| `seuil` | 95 | % de RAM utilisée qui déclenche l'action |
| `urgence` | 98 | % au-delà duquel le programme est arrêté sans attendre |
| `vigilance` | 85 | % au-delà duquel la mesure passe à 4 fois par seconde |
| `grace` | 10 | secondes minimum laissées après la pause avant d'arrêter si la RAM reste haute |
| `vidage_max` | 60 | secondes au plus laissées au noyau pour ranger le programme dans le swap |
| `delai_fige` | 600 | secondes de pause après lesquelles un programme sans décision est arrêté |
| `reste_si_repris` | 256 | Mo qu'un programme repris peut encore demander |
| `notifications` | oui | notifications sur les sessions graphiques ouvertes |
| `proteges` | (vide) | expression régulière de noms à protéger en plus |

Variables d'environnement (prioritaires sur le fichier) : `GAGARINE_CONFIG`, `GAGARINE_ETAT`, `GAGARINE_SEUIL`, etc. ;
`FILE_ATTENTE_DOSSIER` pour le dossier des tickets de la file.

### Swap compressé en RAM

Le paquet fournit un zram de la taille de la RAM, compressé en zstd (3 à 4 fois en moyenne, donc un quart à un tiers de
la RAM quand il est plein), utilisé **avant** le swap disque, qui reste en dernier recours et pour l'hibernation :

- `/usr/lib/systemd/zram-generator.conf.d/50-gagarine.conf` (taille, compression, priorité 100) ;
- `/usr/lib/sysctl.d/90-gagarine.conf` (`vm.swappiness = 180`, `vm.watermark_scale_factor = 125`,
  `vm.watermark_boost_factor = 0`, `vm.page-cluster = 0`, les réglages de Pop!_OS pour un swap en RAM).

Pour en changer, mettre un fichier dans `/etc/systemd/zram-generator.conf.d/` ou `/etc/sysctl.d/` (voir
`zram-generator.conf(5)` et `sysctl.d(5)`). État : `swapon --show`, `zramctl`, ou la page **Aperçu**.

## Fonctionnement détaillé du garde

1. **Mesure** : la RAM utilisée est `100 % − MemAvailable / MemTotal` (le cache récupérable compte comme libre), lue
   chaque seconde, quatre fois par seconde au-delà de 85 %.
2. **Choix du coupable** : le programme dont la mémoire (RAM + swap) a le plus augmenté sur 10 s, puis sur 60 s, à défaut
   le plus gros. Le bureau (gnome-shell, Xorg, le son, D-Bus, systemd…) et les services critiques sont protégés.
3. **Figer** : SIGSTOP. Le programme ne peut plus rien demander.
4. **Brider** : il est placé dans un scope systemd limité en mémoire. S'il est repris, il ne peut grossir que de 256 Mo.
5. **Ranger dans le swap** : en administrateur, `process_madvise` demande au noyau de pousser la mémoire du seul programme
   figé dans le swap, sans toucher aux autres (sur le poste, 850 Mo rangés en une seconde dans le zram).
6. **Arrêter** (SIGKILL) : si la RAM n'est pas redescendue sous le seuil une fois le rangement fini, si elle atteint 98 %, ou
   si le programme reste figé 10 minutes sans décision.
7. **Notifications** : sur chaque session graphique ouverte, avec les boutons « Arrêter » et « Reprendre ».

Le service est prévu pour ne jamais lâcher : jamais choisi par le noyau comme victime (`OOMScoreAdjust=-1000`),
prioritaire (processeur et disque), verrouillé en RAM, relancé en une seconde s'il s'arrête, et sans durcissement qui
bloquerait ses accès (`/proc`, cgroups, D-Bus, `process_madvise`, `runuser`, `notify-send`).

## Pourquoi pas systemd-oomd ou earlyoom ?

- **systemd-oomd** et **earlyoom** arrêtent un programme : le travail est perdu, parfois sur un mauvais choix. Gagarine
  **fige d'abord** : rien n'est perdu tant que la RAM redescend et que l'utilisateur reprend.
- Ils réagissent à la pression de la mémoire ou au manque de RAM et de swap ; Gagarine agit **avant** que le swap ne se
  remplisse, car c'est lui qui fige le poste.
- Gagarine choisit le programme qui **grossit le plus vite**, pas le plus gros, et le **bride** s'il est repris.
- Ils ne préviennent pas la saturation : la **file d'attente** de Gagarine évite de lancer deux travaux lourds à la fois.

Ils se complètent : on peut garder `systemd-oomd`.

## Structure du dépôt

```
gagarine/                 le programme (paquet Python)
  cli.py                  commandes, sorties texte et JSON
  garde.py                le garde : choix du coupable, figer, brider, ranger, arrêter
  file.py                 file d'attente des travaux lourds
  systeme.py              accès au système (/proc, cgroups, appels système, notifications)
  reglages.py journal.py essai.py version.py
  interface/              interface graphique (GTK 4 + libadwaita)
  indicateur/             indicateur de la barre du haut (GTK 3 + AyatanaAppIndicator)
bin/                      lanceurs gagarine, gagarine-interface, gagarine-indicateur
etc/gagarine.conf         réglages par défaut (conffile)
systemd/gagarine.service  unité systemd
data/                     bureau (.desktop, icônes, metainfo, polkit, autostart), zram et sysctl
man/                      pages de manuel
debian/                   paquet Debian
scripts/build-deb.sh      construction du .deb dans dist/
docs/                     conception de la 1.1.0, captures
tests/                    tests unitaires
Makefile                  cibles test, deb, clean
```

Le cœur n'utilise que la bibliothèque standard de Python ; seuls `interface/` et `indicateur/` utilisent PyGObject, et ils
ne parlent au cœur qu'à travers la ligne de commande (`--json`).

## Développement

```bash
make test    # python3 -m unittest discover -s tests -t .
make deb     # tests, dist/gagarine_<version>_all.deb, SHA256SUMS, validations, lintian
make clean
```

Depuis le dépôt, `bin/gagarine`, `bin/gagarine-interface` et `bin/gagarine-indicateur` utilisent le code du dépôt.
`GAGARINE_COMMANDE` désigne la commande `gagarine` qu'utilisent l'interface et l'indicateur (les tests leur donnent un
faux `gagarine`, dans `tests/donnees_interface/` et `tests/donnees_indicateur/`).

La version est dans `gagarine/version.py` (`VERSION`) et dans `debian/changelog` ; `make deb` refuse de construire si
elles diffèrent. Dépendances de construction : `debhelper`, `dpkg-dev`, `lintian`, `desktop-file-utils`, `appstream`.

## Licence

[MIT](LICENSE) © 2026 mawena
