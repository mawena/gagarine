# Gagarine

Gagarine empêche la mémoire vive d'un poste Linux de se remplir au point de le figer.

Quand un programme grossit sans fin (fuite de mémoire, test qui s'emballe, rendu trop lourd), il remplit la RAM, puis le
swap, et le poste passe son temps à échanger avec le disque : l'écran ne répond plus pendant des minutes et il faut
souvent forcer le redémarrage. Gagarine mesure la RAM chaque seconde et, à 95 %, fige le programme qui grossit le plus
vite, le range dans le swap, puis l'arrête si la RAM ne redescend pas.

Projet en cours de construction : voir la [feuille de route](ROADMAP.md).

## Licence

[MIT](LICENSE) © 2026 mawena
