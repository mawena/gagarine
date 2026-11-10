# Conception de Gagarine 1.1.0 : file d'attente, interface graphique, code rangé

Rédigé le 10/11/2026. Ce document sert de référence commune aux différents chantiers de la version 1.1.0 : chacun s'y
tient, et les contrats (commandes, sorties JSON, fichiers) ne changent pas sans mise à jour ici.

## Pourquoi

Gagarine 1.0.0 empêche la RAM du poste de se remplir. Sur ce poste, une seconde protection existe à côté : la **file
d'attente des travaux lourds** (`~/.local/bin/file-attente`, en Node), qui fait passer les travaux lourds un par un et
enferme chacun dans une limite mémoire du noyau. Les deux outils se complètent mais vivent séparément, et tout se fait en
ligne de commande.

La 1.1.0 réunit tout dans Gagarine et donne une interface graphique à chaque fonction :

1. le **code est rangé** en paquet Python, un module par rôle, au lieu d'un seul fichier de 900 lignes ;
2. la **file d'attente** devient une commande de Gagarine (`gagarine file`), compatible avec l'outil actuel ;
3. une **interface graphique GNOME** (GTK 4 + libadwaita) montre et pilote tout : RAM, swap, programmes figés, file
   d'attente, journal, réglages, service, essai ;
4. un **indicateur dans la barre du haut** affiche la RAM en permanence et donne accès aux actions urgentes.

Version : **1.1.0** (fonctions nouvelles, aucune commande existante ne change de sens).

## Objectifs, non-objectifs, anti-objectifs

- Objectifs : tout ce que Gagarine sait faire se fait aussi à la souris ; la file d'attente actuelle (tickets, outils des
  projets) continue de marcher pendant et après le passage à Gagarine ; le cœur reste en bibliothèque standard seule.
- Non-objectifs : pas de serveur web, pas de démon supplémentaire en root, pas de nouvelle dépendance pour le cœur.
- Anti-objectifs (échec même si « ça marche ») : l'interface qui fige ou rame quand la RAM est pleine (c'est justement là
  qu'on en a besoin) ; une action d'administrateur sans demande de mot de passe ; une file qui casse les copies de
  `file-attente.cjs` des projets ; un service qui tombe à cause de l'interface.

## 1. Structure du code

```
gagarine/                       paquet Python
  __init__.py                   docstring seule (ne pas y mettre de code : plusieurs chantiers en dépendent)
  version.py                    VERSION = "1.1.0"
  reglages.py                   Reglages, lecture et écriture de gagarine.conf
  journal.py                    Journal (écriture JSONL, rotation) et lecture fusionnée des journaux
  systeme.py                    accès au système : /proc, cgroups, appels système, busctl, notifications
  garde.py                      Garde (décisions) et boucle de surveillance
  essai.py                      commande essai
  file.py                       file d'attente des travaux lourds
  cli.py                        analyse des commandes, sorties texte et JSON ; main(argv)
  interface/                    interface graphique GTK 4 + libadwaita (seule partie qui importe gi)
  indicateur/                   indicateur de la barre du haut, GTK 3 + AyatanaAppIndicator3 (processus à part)
bin/gagarine                    lanceur de la ligne de commande
bin/gagarine-interface          lanceur de l'interface
bin/gagarine-indicateur         lanceur de l'indicateur
data/                           fichiers de bureau : .desktop, icônes, metainfo, polkit, autostart
tests/                          tests unitaires (python3 -m unittest discover -s tests -t . depuis la racine)
```

- Installé dans `/usr/share/gagarine/gagarine/` (modules privés de l'application). Les trois lanceurs font tous la même
  chose : si le lanceur est dans `/usr/bin`, ajouter `/usr/share/gagarine` en tête de `sys.path` ; sinon (dépôt), ajouter
  le dossier parent de `bin/`. Puis importer et lancer.
- Le cœur (tout sauf `interface/` et `indicateur/`) n'importe que la bibliothèque standard. `interface/` et `indicateur/`
  ne parlent au cœur qu'à travers la ligne de commande (`gagarine … --json`) et la lecture des fichiers d'état : ils
  tournent sous l'utilisateur, le service en root.
- Les tests se lancent depuis la racine du dépôt : `python3 -m unittest discover -s tests -t .`. Un test qui a besoin de
  GTK ou d'un affichage est sauté proprement s'il n'y en a pas.

## 2. Nouvelles commandes du cœur

Toutes les sorties `--json` sont un seul objet JSON sur la sortie standard, en UTF-8, et ne perdent jamais de clé d'une
version à l'autre (on peut en ajouter).

### `gagarine etat --json`

```json
{
  "version": "1.1.0",
  "date": "2026-11-10T14:30:00+0000",
  "ram": {"pourcent": 75.2, "total_mo": 14600, "disponible_mo": 3620},
  "swap": [{"nom": "/dev/zram0", "type": "partition", "taille_mo": 15237, "utilise_mo": 0, "priorite": 100,
            "zram": true},
           {"nom": "/dev/nvme0n1p7", "type": "partition", "taille_mo": 10239, "utilise_mo": 6041, "priorite": -2,
            "zram": false}],
  "reglages": {"seuil": 95, "urgence": 98, "vigilance": 85, "grace": 10, "vidage_max": 60, "delai_fige": 600,
               "reste_si_repris": 256, "notifications": true, "proteges": ""},
  "fichier_reglages": "/etc/gagarine.conf",
  "service": {"etat": "active", "actif_au_demarrage": true},
  "dossier_etat": "/var/lib/gagarine",
  "figes": [{"pid": 4242, "nom": "python3", "depuis": 1762785000.5, "memoire_mo": 9100, "commande": "python3 -m pytest",
             "utilisateur": "charles-gamligo", "bride": true}],
  "plus_gros": [{"pid": 3100, "nom": "chrome", "rss_mo": 900, "swap_mo": 120, "utilisateur": "charles-gamligo",
                 "protege": false}],
  "file": {"travaux": 1}
}
```

`plus_gros` : les 10 programmes qui prennent le plus de mémoire (RAM + swap). `file` est rempli par le chantier de la
file (absent ou `{"travaux": 0}` avant). `service.etat` vaut ce que dit `systemctl is-active gagarine`.

### `gagarine journal [-n N] [--json]`

Les N derniers événements (50 par défaut), triés par date, fusionnés depuis : `/var/lib/gagarine/evenements.jsonl` (service),
`~/.local/state/gagarine/evenements.jsonl` (utilisateur : mode sans privilèges et file d'attente) et l'ancien
`~/.local/state/file-attente/evenements.jsonl`. Un fichier illisible ou une ligne invalide est ignoré. En JSON :
`{"evenements": [ {…ligne telle qu'écrite…}, … ]}` ; en texte, une ligne lisible par événement, heure locale.

### `gagarine reglages [--json]` et `gagarine reglages --ecrire CLE=VALEUR [CLE=VALEUR…]`

- Sans option : les réglages en vigueur et le fichier lu (`--json` : `{"fichier": …, "reglages": {…}}`).
- `--ecrire` (root seulement, sinon message clair et code 1) : valide chaque valeur avec les mêmes règles que la
  lecture (clé inconnue ou valeur invalide : refus de l'ensemble, message, code 2) ; modifie `/etc/gagarine.conf` **sur
  place** en gardant commentaires et ordre (remplace la ligne `cle = …` de la section `[gagarine]`, sinon l'ajoute à la
  fin de la section), par écriture atomique ; puis `systemctl reload gagarine` si le service tourne.
- Le démon relit ses réglages sur **SIGHUP** sans perdre son état (programmes figés, historique) ;
  `gagarine.service` reçoit `ExecReload=/bin/kill -HUP $MAINPID`.

### `gagarine service demarrer|arreter|redemarrer|activer|desactiver`

Root seulement : `systemctl start|stop|restart|enable --now|disable --now gagarine`, avec un message clair.

### Notifications

`notify-send` reçoit `--app-name=Gagarine`, `-i io.github.mawena.Gagarine` et
`-h string:desktop-entry:io.github.mawena.Gagarine` (en plus des boutons actuels).

## 3. File d'attente des travaux lourds (`gagarine file`)

Reprise de `~/.local/bin/file-attente` (Node, à lire en entier : c'est la référence du comportement), en Python, dans
`gagarine/file.py`.

### Commandes

- `gagarine file "<nom>" <Go de RAM> <cœurs libres> [--apres PID] -- <commande> [arguments…]` : attend son tour, puis lance
  la commande dans sa limite mémoire ; rend le code de sortie de la commande (128 + n si tuée par le signal n).
- `gagarine file --etat [--json]` (et `gagarine file etat`) : mémoire disponible, charge, travaux en file.
- `gagarine file annuler <PID|ticket>` : annule un travail en attente ou arrête un travail en cours (seulement un
  travail de la file, appartenant à l'utilisateur ; root : tous).

### Compatibilité (obligatoire)

- Même dossier de tickets : `/tmp/file-attente-travaux-lourds` (variable `FILE_ATTENTE_DOSSIER` pour le changer).
- Même nom de ticket : `<millisecondes>-<pid>.json` ; l'ordre de la file est l'ordre alphabétique des noms.
- Mêmes champs : `pid`, `demarrage` (champ 22 de `/proc/PID/stat`, en **chaîne**, comme l'outil Node), `nom`, `depuis`
  (ISO 8601). Champs ajoutés (ignorés par les anciens outils) : `ram`, `coeurs`, `commande`, `etat` (`"en attente"`
  puis `"en cours"`), `unite` (scope systemd), `sortie` (fichier de sortie, repris de la variable `GAGARINE_SORTIE`
  si elle existe), `source: "gagarine"`.
- Un ticket dont le processus est mort (ou dont `demarrage` ne correspond plus) est supprimé ; un ticket illisible (en
  cours d'écriture) compte comme vivant.
- Héritage : si `FILE_ATTENTE_TICKET` désigne un ticket existant, le travail passe directement (sous le ticket du parent).
- Les copies `file-attente.cjs` des projets et l'ancien outil continuent de partager la même file que `gagarine file`.

### Tour, limite, événements

- Son tour vient quand : aucun ticket vivant plus ancien ; `MemAvailable` ≥ RAM annoncée ; cœurs libres (`nproc` −
  charge sur 1 min) ≥ cœurs annoncés ; le processus `--apres` est terminé. Vérification toutes les 5 s ; un message
  (mêmes textes que l'outil Node) chaque fois que la raison d'attendre change.
- Lancement, si systemd utilisateur répond : `systemd-run --user --scope --quiet --unit=gagarine-travail-<ticket sans
  .json> -p MemoryMax=<max(2 × RAM, RAM + 2) Go, en Mo>M -p MemorySwapMax=512M -- commande` ; sinon lancement direct.
  Variables passées : `FILE_ATTENTE_TICKET`, `FILE_ATTENTE_LIMITE_GO`.
- Après : `Result` du scope ; `oom-kill` → message en français (dépassement de la limite, annoncer la vraie RAM),
  événement `limite`, code 137 ; puis `reset-failed`.
- SIGTERM/SIGINT pendant l'attente : ticket retiré, événement `travail-annule`, code 143/130. Pendant l'exécution : le
  scope est arrêté (`systemctl --user stop`), puis même chose.
- Journal : `~/.local/state/gagarine/evenements.jsonl` (même format et même rotation que le journal du service,
  `source: "file"`) : `travail-debut`, `travail-fin` (code, durée en s), `travail-annule`, `limite` (`declare`, `limite`,
  `commande`). Le hook des sessions Claude lit les actions `fige`, `arrete`, `limite`.
- `gagarine file --etat --json` :

```json
{"ram_disponible_go": 3.6, "charge": 2.1, "coeurs": 14, "dossier": "/tmp/file-attente-travaux-lourds",
 "travaux": [{"ticket": "1762785000000-1234.json", "position": 1, "nom": "rendu vidéo", "pid": 1234,
              "depuis": "2026-11-10T14:30:00.000Z", "etat": "en cours", "ram": 4, "coeurs": 4,
              "commande": "ffmpeg …", "unite": "gagarine-travail-1762785000000-1234.scope",
              "sortie": "/home/…/travaux/….log", "source": "gagarine"}]}
```

  Pour un ticket d'un ancien outil, les champs absents valent `null` et `source` vaut `"autre"`.
- `gagarine etat` (texte et JSON) montre aussi le nombre de travaux en file.

## 4. Interface graphique (`gagarine-interface`)

GTK 4.14 et libadwaita 1.5 (ceux d'Ubuntu 24.04), PyGObject. Identifiant : `io.github.mawena.Gagarine`. Une seule
instance (`Adw.Application`). Textes en français, conformes aux recommandations GNOME (HIG).

- **Fenêtre** : `Adw.ApplicationWindow`, `Adw.ToolbarView`, barre d'en-tête avec `Adw.ViewSwitcher` (et
  `Adw.ViewSwitcherBar` en fenêtre étroite, par `Adw.Breakpoint`), menu principal (À propos, raccourcis), notifications
  internes par `Adw.ToastOverlay`.
- **Vue d'ensemble** : jauge de la RAM (pourcentage, Go utilisés sur le total) avec les repères vigilance / seuil /
  urgence ; courbe des 5 dernières minutes (Cairo) ; swap ligne par ligne (zram et disque, utilisé / taille) ; état du
  service (actif, mode) ; nombre de programmes figés et de travaux en file, qui mènent aux pages correspondantes.
- **Programmes** : les programmes figés (nom, PID, mémoire, figé depuis, commande) avec **Reprendre** et **Arrêter**
  (confirmation) ; état vide clair ; puis les 10 plus gros programmes (lecture seule, « protégé » signalé).
- **File d'attente** : travaux en cours et en attente (position, RAM et cœurs annoncés, depuis, commande), **Annuler**
  (confirmation), **Voir la sortie** pour ceux qui ont un fichier de sortie, et **Nouveau travail…** (nom, RAM, cœurs,
  dossier, commande) : lance `gagarine file … -- sh -c "<commande>"` détaché de l'interface (nouvelle session), sortie
  dans `~/.local/state/gagarine/travaux/<horodatage>-<nom>.log`, transmise par la variable `GAGARINE_SORTIE`.
- **Journal** : événements fusionnés (`gagarine journal --json -n 200`), icône par type, heure locale, filtre par type
  et recherche.
- **Réglages** : `Adw.PreferencesPage` : seuils (%), délais (s), bridage (Mo), notifications, programmes protégés ;
  **Enregistrer** → `pkexec gagarine reglages --ecrire …`. Service : démarrer, arrêter, redémarrer
  (`pkexec gagarine service …`). Essai : **Lancer l'essai** (sans privilèges, ou en administrateur par `pkexec`), sortie
  affichée en direct et verdict. Indicateur : interrupteur « Afficher dans la barre du haut » (voir 5).
- **À propos** : `Adw.AboutDialog` (version, licence MIT, dépôt GitHub, mawena).
- **Données** : RAM lue directement dans `/proc/meminfo` chaque seconde (courbe fluide) ; le reste par `gagarine etat
  --json`, `gagarine file --etat --json` et `gagarine journal --json`, lancés en asynchrone (`Gio.Subprocess`) toutes
  les 2 à 3 s, jamais deux à la fois, avec un délai maximal : l'interface ne se bloque jamais, même RAM pleine. La
  commande `gagarine` utilisée peut être remplacée par la variable `GAGARINE_COMMANDE` (développement, tests).
- **Actions d'administrateur** : uniquement par `pkexec /usr/bin/gagarine …` ; politique polkit
  `data/polkit/io.github.mawena.gagarine.policy` (action `io.github.mawena.gagarine.administrer`,
  `org.freedesktop.policykit.exec.path` = `/usr/bin/gagarine`, `auth_admin_keep`, message en français). Reprendre /
  Arrêter un programme de l'utilisateur ne demande rien ; un programme d'un autre utilisateur passe par `pkexec`.
- **Bureau** : `data/applications/io.github.mawena.Gagarine.desktop`, icône originale
  `data/icons/hicolor/scalable/apps/io.github.mawena.Gagarine.svg` (et sa version symbolique), metainfo
  `data/metainfo/io.github.mawena.Gagarine.metainfo.xml` ; validés par `desktop-file-validate` et
  `appstreamcli validate --no-net`.
- **Captures** : `docs/captures/*.png` de chaque page, faites sans écran (Xvfb), pour le README et le metainfo.
- Une logique sans GTK (`interface/donnees.py` : lecture des JSON, mise en forme des durées, des tailles et des
  événements) est couverte par des tests unitaires.

## 5. Indicateur de la barre du haut (`gagarine-indicateur`)

GTK 3 + AyatanaAppIndicator3 (l'extension « Ubuntu AppIndicators » est active sur le poste), dans son propre processus
(GTK 3 et GTK 4 ne se mélangent pas).

- Étiquette « 75 % » à côté d'une icône symbolique qui change selon le niveau : normal, vigilance (≥ vigilance),
  alerte (≥ seuil, ou programme figé).
- Menu : RAM et swap ; programmes figés (un sous-menu par programme : Reprendre, Arrêter) ; nombre de travaux en file ;
  **Ouvrir Gagarine** (lance `gagarine-interface`) ; **Masquer l'indicateur**.
- Mise à jour toutes les 2 s, par les mêmes commandes JSON, en asynchrone.
- Démarrage automatique : `data/autostart/io.github.mawena.Gagarine.Indicateur.desktop` (installé dans
  `/etc/xdg/autostart/`). « Masquer » et l'interrupteur de l'interface écrivent
  `~/.config/autostart/io.github.mawena.Gagarine.Indicateur.desktop` avec `Hidden=true` (et l'enlèvent pour le
  réafficher) puis arrêtent ou lancent l'indicateur.

## 6. Paquet

Un seul paquet `gagarine` 1.1.0 : `Depends` du cœur, plus `systemd-zram-generator` ; `Recommends: python3-gi,
gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5), gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, pkexec`. Si une
dépendance graphique manque, `gagarine-interface` et `gagarine-indicateur` le disent clairement (nom du paquet à
installer). Pages de manuel à jour. `make deb` valide aussi le `.desktop` et le metainfo.

### Swap compressé en RAM (zram), fourni par le paquet

Tout ce qui protège la mémoire du poste vit dans Gagarine, y compris le swap (demande du 10/11/2026). Le paquet installe :

- `/usr/lib/systemd/zram-generator.conf.d/50-gagarine.conf` : `[zram0]`, `zram-size = ram`, `compression-algorithm =
  zstd`, `swap-priority = 100` (le dossier prévu pour les paquets ; un fichier de l'administrateur dans
  `/etc/systemd/zram-generator.conf.d/` passe devant) ;
- `/usr/lib/sysctl.d/90-gagarine.conf` : `vm.swappiness = 180`, `vm.watermark_scale_factor = 125`,
  `vm.watermark_boost_factor = 0`, `vm.page-cluster = 0` (réglages pour un swap en RAM, ceux de Pop!_OS) ;
- à l'installation, le zram est créé et les réglages appliqués tout de suite, sans redémarrer, sans toucher au swap
  disque existant (utilisé après le zram) ; à la suppression, les réglages du noyau reviennent aux valeurs par défaut
  au prochain démarrage ;
- `gagarine etat` (texte et JSON) signale les zones zram (déjà dans `swap[].zram`), l'interface les montre.

## 7. Ce qui est laissé au choix de chaque chantier

Le détail du code, des widgets et des messages, tant que les contrats ci-dessus sont tenus. Un chantier qui doit changer
un contrat le signale au lieu de le changer seul.
