# Conception de la 1.5.0 — nouveau design de l'interface

Demande (11/11/2026) : « améliore le design, pas la peine de centrer horizontalement, fais de ton mieux pour avoir un
meilleur design, toujours avec GTK ». Les choix ci-dessous sont les miens ; ils restent dans GTK 4 et libadwaita.

## Pourquoi

Jusqu'à la 1.4.0, chaque page est une `Adw.PreferencesPage` : une colonne d'environ 570 px, centrée. Sur l'écran du poste
(1920 × 1200), plus de la moitié de la fenêtre reste vide. Les lignes longues (commandes, détails du journal) passent sur
trois lignes, et l'Aperçu demande de défiler pour voir la courbe. Toutes les pages se ressemblent : une pile de groupes
de réglages. Rien ne dit d'un coup d'œil si le poste va bien.

## Ce que fait la fenêtre (le sujet)

- **Pour qui** : la personne qui utilise ce poste de 14 cœurs et 14 Go. Elle lance des travaux lourds (rendus vidéo,
  suites de tests, sessions Claude) et doit savoir tout de suite si la machine tient.
- **Son rôle principal** : répondre d'un coup d'œil à trois questions. Le poste est-il en danger ? Qui le
  charge ? Qu'est-ce qui attend ? Ensuite, permettre d'agir : reprendre ou arrêter un programme figé, annuler un travail.
- **Le vocabulaire du sujet** : Gagarine, le cosmonaute. On y emprunte les instruments d'un poste de pilotage,
  échelles graduées et voyants, mais seulement là où ils portent une information. Tout le reste reste natif GNOME.

## Jetons

### Couleurs

Elles sont définies dans `gagarine/interface/styles/base.css` (et `base-sombre.css` pour le thème sombre).

| Nom | Clair | Sombre | Rôle |
|---|---|---|---|
| `gagarine_ram` | `#3584e4` | `#62a0ea` | série RAM (jauge, courbe) |
| `gagarine_cpu` | `#9141ac` | `#c061cb` | série processeur (cœurs, courbe) |
| `gagarine_calme` | `@success_color` | idem | niveau normal |
| `gagarine_vigilance` | `@warning_color` | idem | niveau de vigilance, fuite, processeur saturé |
| `gagarine_alerte` | `@error_color` | idem | seuil, urgence, programme figé, service arrêté |

- **Couleur = information** : le vert, l'ambre et le rouge servent seulement aux niveaux. Le bleu et le violet servent
  seulement aux deux séries. Tout le reste est neutre : fonds, cartes et textes de libadwaita.
- **Une couleur ne parle jamais seule** : chaque niveau a aussi son mot (Calme, Occupé, Saturé, « figé »…).
- Les dessins Cairo lisent ces couleurs par `widget.get_style_context().lookup_color('gagarine_ram')`, avec une valeur de
  secours, et ne les codent pas en dur.

### Typographie

- La police est celle du système (Ubuntu Sans sur le poste, Adwaita Sans ailleurs). Aucune police n'est embarquée.
- Les chiffres sont des instruments : chiffres tabulaires partout où une valeur change (`font-feature-settings:
  "tnum"`, ou la classe `numeric`).

| Classe | Taille | Graisse | Usage |
|---|---|---|---|
| `.lecture` | 40 px | 300 | grande valeur d'un instrument (67,6) |
| `.lecture-unite` | 16 px | 400, atténuée | unité ou complément d'une lecture (%, Go) |
| `.titre-etat` | 22 px | 700 | phrase d'état en tête de l'Aperçu |
| `.titre-section` | 15 px | 700 | titre de section |
| (défaut) | 10 pt | 400 | texte |
| `.etiquette` | 12 px | 600, atténuée | petit libellé de valeur, en casse normale (jamais en capitales) |

- Les phrases d'explication font au plus environ 90 caractères par ligne (`max-width-chars`).

### Mise en page

- **Navigation** : une barre latérale (`Adw.NavigationSplitView`) remplace les onglets du haut.
  - Chaque ligne a une icône, le nom de la page et un **compteur vivant** à droite (classe `.compteur`) :
    - Aperçu : « 67 % », la RAM, coloré selon le niveau ;
    - Programmes : « 1 figé » en rouge (sinon rien) ;
    - File d'attente : « 2 en cours » ;
    - Journal et Réglages : rien.
  - En bas de la barre, l'état du service : voyant et « Service actif », ou « Service arrêté » avec un bouton
    « Démarrer ».
  - En fenêtre étroite (`max-width: 640sp`), la barre se replie : le contenu s'affiche d'abord, et un bouton retour
    ouvre la liste des pages.
- **Pages** : `outils.PageLarge`, en pleine largeur et alignées à gauche, jamais centrées.
  - Marges de 28 px, ramenées à 14 px sous 600 sp de large.
  - 32 px entre deux sections.
  - Les sections se placent dans des **colonnes** (`PageLarge.colonnes(n)`), côte à côte au-delà de 1000 sp de page et
    empilées en dessous.
  - Chaque section (`outils.Section`) a un titre et une description à gauche, ses actions à droite, puis son corps.
- **Lignes** : les listes gardent les lignes de libadwaita (`Adw.ActionRow` dans une `boxed-list` ou un
  `Adw.PreferencesGroup`).
  - Avec la largeur gagnée, les détails tiennent sur une ligne ; s'ils sont trop longs, ils sont coupés avec « … » et
    affichés en entier dans une infobulle.
  - Les valeurs structurées passent en colonnes alignées (libellé et valeur) plutôt qu'en suites « a · b · c ».
- **Actions** :
  - Une seule action principale par section, en `suggested-action`.
  - Les actions destructives d'une ligne (Arrêter, Annuler, Décharger) sont des boutons plats au texte rouge
    (`flat` et `destructive-action`) ; seule la confirmation a le bouton rouge plein. Aujourd'hui, une file de cinq
    travaux montre cinq boutons rouges : c'est trop de bruit.

### Principes

1. **Un coup d'œil suffit** : le haut de l'Aperçu répond par une phrase, puis par deux instruments.
2. **La largeur sert** : plusieurs colonnes quand il y a la place, une seule sinon ; du texte lisible, jamais étiré sur
   1800 px.
3. **Une seule audace** : les instruments de l'Aperçu (échelle graduée de la RAM, cœurs du processeur) et le voyant de la
   phrase d'état. Tout le reste est calme et natif.
4. **Natif** : widgets libadwaita, thèmes clair et sombre, navigation au clavier, libellés accessibles des boutons à
   icône, animations de libadwaita seulement (aucune animation décorative).

## Pages

### Aperçu (chantier A)

```
┌ barre latérale ─┬──────────────────────────────────────────────────────────────────────────┐
│ ▣ Aperçu   67 % │ ● Le poste respire : 4,8 Go libres, processeur calme.                    │
│ ▣ Programmes    │   Gagarine mesure la RAM chaque seconde.                                 │
│   1 figé        ├────────────────────────────────────┬─────────────────────────────────────┤
│ ▣ File  2 en c. │ Mémoire vive               Calme   │ Processeur                   Occupé │
│ ▣ Journal       │ 67,6 %  10,1 Go sur 14,9 Go        │ 47 %  charge 2,4 · 1,9 GHz · 54 °C  │
│ ▣ Réglages      │ ┃┃┃┃┃┃┃┃┃┃┃┃┃┃┃┃┃┃┃┃┃░░░░▒▒▒▒▓│▓   │ ▮▮ ▮▮  ▮▮▮▮▮▮▮▮▮▮                   │
│                 │ 0    25    50    75   85  95 98    │ P      E                            │
│                 ├────────────────────────────────────┴─────────────────────────────────────┤
│                 │ RAM et processeur                                  [5 min][24 h][7 jours] │
│                 │ (courbe pleine largeur, environ 260 px de haut)                           │
│ ● Service actif │ ├ Swap ─────────────┬ Les plus gros de la période ┬ Les plus actifs ──────┤ │
└─────────────────┴──────────────────────────────────────────────────────────────────────────┘
```

- **Phrase d'état** (`donnees.phrase_etat(...)` → `(niveau, titre, detail)`, une fonction pure testée) : la première
  situation vraie de la liste ci-dessous l'emporte.
  1. Service arrêté (alerte) : « Le service est arrêté : la RAM n'est pas protégée. »
  2. Programme figé (alerte) : « python3 est figé : il attend votre décision. », avec un bouton qui mène aux
     Programmes.
  3. RAM au seuil ou plus (alerte).
  4. RAM en vigilance : « La RAM approche du seuil : Gagarine mesure quatre fois par seconde. »
  5. Fuite en cours (vigilance) : « node grossit sans cesse (+210 Mo/min). »
  6. Processeur saturé (vigilance) : la phrase précise que Gagarine n'agit pas pour le processeur.
  7. Sinon (calme) : « Le poste respire : 4,8 Go libres, processeur calme. »
- **Voyant** : un disque de 14 px de la couleur du niveau, avec un halo léger, avant la phrase. C'est un témoin de
  tableau de bord. Le niveau est aussi écrit en toutes lettres pour les lecteurs d'écran.
- **Instrument RAM** : échelle graduée en pleine largeur de sa colonne.
  - Petits traits tous les 5 %, grands traits et nombres tous les 25 %.
  - Zone de vigilance (de 85 % au seuil) en ambre léger, seuil et urgence marqués en rouge.
  - Remplissage dans la couleur RAM, qui passe en ambre puis en rouge selon le niveau, et un repère au niveau actuel.
  - Lecture `.lecture` à gauche, détail en `.lecture-unite`, niveau à droite.
- **Instrument processeur** : la même grammaire (lecture, détail, niveau), puis les barres des cœurs.
  - Les cœurs P sont dessinés plus larges que les E, et les deux fils d'un même cœur P sont groupés.
  - Les barres sont violettes, ambre au-delà de 50 %, rouges au-delà de 90 %.
  - Légende : modèle, répartition, température (« (chaud) »), fréquence, pression.
- **Courbe** sur toute la largeur. Dessous, en trois colonnes : swap ; plus gros ou incidents de la période ; plus actifs
  de la période.
- Le groupe « Gagarine » (service) quitte l'Aperçu : l'état du service est dans la barre latérale, et ses commandes dans
  Réglages.

### Programmes (chantier B)

- Deux colonnes au-delà de 1000 sp :
  - à gauche : Programmes figés, Fuites en cours, Les plus gros ;
  - à droite : Les plus actifs, Ménage, Applications du bureau.
- Une section vide ne disparaît pas : elle dit en une ligne ce qui se passe (« Aucun programme figé. Gagarine
  interviendra si la RAM atteint 95 %. »).
- Ligne de programme : nom, puis PID, utilisateur et commande sur une ligne, coupée avec infobulle. Valeur à droite en
  chiffres tabulaires. Actions plates.

### File d'attente (chantier B)

- En tête, à gauche, l'état de la file : « 2 en cours sur 3 possibles · 3,2 Go libres · charge 2,1 sur 14 cœurs ». À
  droite, « Nouveau travail… » (`suggested-action`).
- Travaux sur toute la largeur. Chaque ligne a :
  - le rang ;
  - le nom et la commande (une ligne, coupée) ;
  - des colonnes alignées : RAM annoncée, cœurs, processeur utilisé, durée ;
  - une pastille d'état : « En cours » en vert, « En attente » atténuée, avec le motif en sous-titre ;
  - la priorité, puis des actions plates (Voir la sortie, Priorité…, Annuler en rouge plat).
- Dessous, deux colonnes : Mémoire apprise, Travaux passés.

### Journal (chantier C)

- Barre d'outils : recherche, qui prend la largeur, puis filtre par type.
- Événements groupés par jour : en-têtes « Aujourd'hui », « Hier », « Lundi 9 novembre ».
- Chaque ligne a :
  - l'heure à gauche, dans une colonne fixe aux chiffres tabulaires, comme une frise ;
  - une icône teintée selon le type : figé ou arrêté en alerte, fuite en vigilance, repris en calme, travail en
    couleur d'accent, ménage et service en neutre ;
  - le titre, puis le détail sur une ligne, coupé avec infobulle.
- Toute la largeur.

### Réglages (chantier C)

- Les groupes de réglages en deux colonnes au-delà de 1000 sp, alignées à gauche :
  - à gauche : garde (seuils, anticipation, pression), délais et bridage, notifications ;
  - à droite : file, fuites, ménage, sessions Claude, programmes protégés, service et essai, affichage de l'indicateur.
- Les lignes de libadwaita (`Adw.SpinRow`, `Adw.SwitchRow`, `Adw.EntryRow`…) restent.

## Répartition des fichiers

| Chantier | Fichiers (personne d'autre ne les modifie) |
|---|---|
| Fondations (moi, avant les chantiers) | `gagarine/interface/styles/base.css`, `base-sombre.css`, chargement des styles dans `application.py`, `outils.PageLarge` et `outils.Section`, ce document |
| A — Fenêtre et Aperçu | `fenetre.py`, `application.py`, `apercu.py`, `graphiques.py`, `outils.py`, `donnees.py`, `source.py`, `styles/base*.css`, `styles/apercu*.css`, `tests/test_interface_gtk.py`, `tests/test_interface.py`, `tests/test_interface_processeur.py`, `tests/donnees_interface/` (faux gagarine et captures) |
| B — Programmes et File | `programmes.py`, `file_attente.py`, `styles/programmes*.css`, `styles/file*.css`, `tests/test_interface_design_programmes.py` (nouveau) |
| C — Journal et Réglages | `journal.py`, `reglages.py`, `styles/journal*.css`, `styles/reglages*.css`, `tests/test_interface_design_journal.py` (nouveau) |
| Assemblage (moi) | `debian/`, `README.md`, `CHANGELOG.md`, `site/`, `docs/`, `data/metainfo/`, `gagarine/version.py`, `ROADMAP.md`, captures finales |

- B et C ne modifient ni `donnees.py`, ni `outils.py`, ni les tests existants. Leurs fonctions pures nouvelles vivent
  dans leur module de page, et leurs tests dans leur nouveau fichier.
- Les tests existants de `test_interface_gtk.py` lisent des attributs des pages : B et C les gardent, ou disent lesquels
  ils ne peuvent pas garder.
- Chaque chantier peut lancer le script de captures pour juger son travail, toujours par la file. Il ne commite jamais
  `docs/captures/` : les captures finales sont faites une fois, à l'assemblage.

## Ce que la 1.5.0 ne fait pas

- Aucune nouvelle commande, aucun changement du service, de la file ni de la ligne d'état.
- Pas de police embarquée, pas de thème personnalisé de la fenêtre (fond, barre d'en-tête) : la fenêtre reste
  une fenêtre GNOME.

## Précisions apportées pendant les chantiers

- **Fenêtre** :
  - `Adw.NavigationSplitView`, barre latérale de 248 à 280 px (20 %). Elle se replie sous 640 sp, et le contenu
    s'affiche d'abord.
  - Le bandeau « service arrêté » n'apparaît qu'en fenêtre étroite : en fenêtre large, la barre latérale le dit déjà.
  - Compteur de la RAM arrondi vers le bas (84,9 donne « 84 % », jamais un « 85 % » calme). Il est neutre en calme,
    coloré en vigilance et en alerte.
- **Fondations ajoutées** :
  - `outils.ListeEncadree`, `outils.Voyant`, `outils.changer_classe` ;
  - `PageLarge.fixer_en_haut(barre)`, et la classe `etroite` sur `PageLarge.contenu` (posée par les signaux `apply` et
    `unapply`) ;
  - `button.texte-destructif`, le bouton plat au texte rouge, que libadwaita 1.5 n'a pas ;
  - les jetons `gagarine_{calme,vigilance,alerte}_fond` pour les aplats.
- **Couleurs des dessins** : `graphiques.Palette`, une étiquette cachée par jeton (`.sonde-*`), lue par `get_color()`
  (`lookup_color` est déprécié). Les couleurs suivent le thème clair ou sombre.
- **Accent** : sur le poste (Yaru-red-dark), `@accent_color` est rouge. Il n'est jamais utilisé pour une série ni pour un
  état normal ; le bleu des travaux du journal est fixe. Les `suggested-action` restent rouges : c'est le choix du système.
- **Aperçu** :
  - En 5 min, les colonnes du bas sont le swap, puis « Les plus gros en ce moment » et « Les plus actifs en ce moment »
    (lus dans `etat --json`). En 24 h et 7 jours, on y trouve les incidents et les plus gros de la période, puis les plus
    actifs de la période. La rangée n'est jamais vide.
  - Phrase d'état « inconnu » (voyant éteint) avant la première mesure. Le bouton « Voir les programmes » ne s'affiche que
    pour un programme figé, et il passe sous la phrase quand la page fait moins de 1000 sp.
  - Valeurs insécables (« 97 °C », « 7,5 Go libres ») : un morceau n'est jamais coupé en fin de ligne.
- **Instruments** :
  - Cœurs : un fil P a la largeur d'un cœur E, donc un cœur P à deux fils est deux fois plus large (1,5 fois sans second
    fil connu), avec une accolade sous chaque paire. Les fils d'un même cœur sont supposés numérotés à la suite.
  - Échelle de la RAM : les repères 85, 95 et 98 sont au-dessus de la bande, écartés pour ne pas se chevaucher, et une
    aiguille marque le niveau actuel.
  - Courbe : la RAM est toujours bleue, avec une aire en dégradé discret et le dernier point marqué. Sur 24 h et 7 jours,
    elle garde un point par tranche de 3 px, le plus haut, pour que les pics restent visibles. Textes en Pango, avec la
    police du système et des chiffres tabulaires.
  - Swap : barres neutres, et noms courts (« Compressé en RAM (zram) », « Partition sur disque »).
- **Journal** :
  - Le jour est porté par la première ligne de chaque jour (« Aujourd’hui », « Hier », « Lundi 9 novembre »,
    « Dimanche 1er novembre », l'année si elle diffère), en français écrit en dur. Le jour entre dans la signature de mise
    à jour : après minuit, « Aujourd’hui » devient « Hier ».
  - Cartes par jour dessinées par des bordures de ligne : un contour fin en clair, aucun en sombre.
  - Teintes : limite en alerte ; bridage impossible, fuite d'un travail et travail terminé avec un code non nul en
    vigilance (icône d'avertissement) ; travail annulé en bleu des travaux.
  - Recherche et filtre dans une barre fixe ; Échap vide la recherche. Infobulle seulement si le texte est coupé, et le
    texte complet est dans la description accessible.
  - Lignes gardées par clé (date, action, processus, nom, rang) : aucun clignotement.
- **Réglages** :
  - À gauche : Seuils, Agir avant le seuil, Délais et bridage, Programmes protégés, Notifications. À droite : File
    d'attente (avec les sessions Claude), Fuites, Ménage, Service, Essai, Barre du haut.
  - La barre d'enregistrement n'apparaît, fixe en bas, que s'il y a une modification. En étroit, le message passe
    au-dessus des boutons.
  - Service : voyant, puis « Démarrer » s'il est arrêté, sinon « Redémarrer » et « Arrêter » (plat, texte rouge). Les
    boutons inutiles sont masqués, et non plus grisés.
