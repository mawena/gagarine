#!/bin/sh
# Hook PreToolUse (Bash) de toutes les sessions Claude Code du poste (installé par le paquet gagarine dans
# /usr/share/gagarine/claude/). Tout le travail est fait par « gagarine hook-claude » : rappels quand le poste est chargé,
# événements de la dernière demi-heure, service arrêté, et refus des travaux lourds lancés hors de la file.
# Si gagarine manque ou plante : on ne dit rien et on ne bloque rien.
command -v gagarine >/dev/null 2>&1 || { cat > /dev/null 2>&1; exit 0; }
exec gagarine hook-claude
