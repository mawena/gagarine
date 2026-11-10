# Gagarine

Gagarine est un garde-RAM pour Linux : il empêche la mémoire vive d'un poste de se remplir au point de le figer.

## À quoi ça sert

Quand un programme grossit sans fin (fuite de mémoire, test qui s'emballe, rendu trop lourd), il remplit la RAM, puis le
swap. Le poste passe alors son temps à échanger avec le disque : l'écran ne répond plus pendant des minutes, et il faut
souvent forcer le redémarrage. C'est arrivé deux fois le 02/11/2026 et le 10/11/2026 : un test qui avait grossi jusqu'à
9 Go a rempli les 14 Go de RAM puis les 10 Go de swap, et le poste est resté figé.

Gagarine agit avant ce moment. Il mesure la RAM chaque seconde et, à 95 %, **fige** le programme qui grossit le plus vite,
le **bride**, le **range dans le swap**, puis l'**arrête** si la RAM ne redescend pas. Le bureau et les services critiques
ne sont jamais touchés, et des notifications proposent « Arrêter » ou « Reprendre ».

## Installation

Pour Ubuntu 24.04 (noyau 6.8, cgroup v2, systemd), ou toute distribution Debian équivalente.

1. Télécharger `gagarine_1.0.0_all.deb` (et `SHA256SUMS`) depuis les
   [releases GitHub](https://github.com/mawena/gagarine/releases).
2. Vérifier la somme, puis installer :

   ```bash
   sha256sum -c SHA256SUMS --ignore-missing
   sudo apt install ./gagarine_1.0.0_all.deb
   ```

3. Vérifier que tout marche, sans risque :

   ```bash
   sudo gagarine essai
   ```

Le service `gagarine.service` est activé et démarré à l'installation, redémarré à la mise à jour et arrêté à la
suppression. Les réglages (`/etc/gagarine.conf`) sont conservés aux mises à jour.

## Utilisation

| Commande | Effet |
|---|---|
| `gagarine surveiller [--config FICHIER]` | lance le démon (c'est ce que fait le service) |
| `gagarine etat` | RAM, état du service, programmes figés, derniers événements |
| `gagarine reprendre PID` | relance un programme figé (il reste bridé) |
| `gagarine arreter PID` | arrête un programme figé |
| `gagarine essai` | auto-test sans risque : un programme d'essai grossit, il doit être figé, rangé dans le swap puis arrêté, avec un verdict |
| `gagarine version` | affiche `gagarine 1.0.0` (aussi `--version`) |
| `gagarine -h` | aide |

Journal du service : `journalctl -u gagarine -f`. Page de manuel : `man gagarine`.

## Configuration

Fichier `/etc/gagarine.conf`, au format INI, section `[gagarine]`. Après une modification :
`sudo systemctl restart gagarine`.

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

Variables d'environnement (prioritaires sur le fichier) : `GAGARINE_CONFIG`, `GAGARINE_ETAT`, `GAGARINE_SEUIL`, etc.

## Fonctionnement détaillé

1. **Mesure** : la RAM utilisée est `100 % − MemAvailable / MemTotal` (le cache récupérable compte comme libre), lue
   chaque seconde, quatre fois par seconde au-delà de 85 %.
2. **Choix du coupable** : le programme dont la mémoire (RAM + swap) a le plus augmenté sur 10 s, puis sur 60 s, à défaut
   le plus gros. Le bureau (gnome-shell, Xorg, le son, D-Bus, systemd…) et les services critiques sont protégés.
3. **Figer** : SIGSTOP. Le programme ne peut plus rien demander.
4. **Brider** : il est placé dans un scope systemd limité en mémoire. S'il est repris, il ne peut grossir que de 256 Mo.
5. **Ranger dans le swap** : en administrateur, `process_madvise` demande au noyau de pousser la mémoire du seul programme
   figé dans le swap, sans toucher aux autres.
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

Ils se complètent : on peut garder `systemd-oomd`. Pour que le rangement dans le swap soit rapide, ajoutez un **swap
compressé en RAM** (zram) : `sudo apt install systemd-zram-generator`. Il évite d'écrire sur le disque et rend la RAM bien
plus vite (voir `zramctl(8)`).

## Structure du dépôt

```
bin/gagarine              le programme (Python 3, bibliothèque standard seule)
etc/gagarine.conf         réglages par défaut (conffile)
systemd/gagarine.service  unité systemd
man/gagarine.8            page de manuel
debian/                   paquet Debian
scripts/build-deb.sh      construction du .deb dans dist/
tests/                    tests unitaires
Makefile                  cibles test, deb, clean
```

## Développement

```bash
make test    # python3 -m unittest discover -s tests -v
make deb     # tests, dist/gagarine_<version>_all.deb, SHA256SUMS, lintian
make clean
```

La version est dans `bin/gagarine` (`VERSION`) et dans `debian/changelog` ; `make deb` refuse de construire si elles
diffèrent. Dépendances de construction : `debhelper`, `dpkg-dev`, `lintian`.

## Licence

[MIT](LICENSE) © 2026 mawena
