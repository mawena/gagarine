#!/bin/sh
# Installe Gagarine depuis le dépôt APT unique de mawena : https://apt.mawena.cloud/
#   curl -fsSL https://mawena.github.io/gagarine/installer.sh | sudo sh
# Depuis la 1.5.3, Gagarine est publié dans le dépôt unique : ce script lance son installateur avec le paquet gagarine.
# Équivaut à : curl -fsSL https://apt.mawena.cloud/installer.sh | sudo sh -s -- gagarine
set -eu

INSTALLATEUR=https://apt.mawena.cloud/installer.sh

if [ "$(id -u)" -ne 0 ]; then
  echo "Gagarine : à lancer en administrateur, par exemple : curl -fsSL $INSTALLATEUR | sudo sh -s -- gagarine" >&2
  exit 1
fi
if ! command -v apt-get >/dev/null 2>&1; then
  echo "Gagarine : apt-get introuvable ; Gagarine s'installe sur Ubuntu ou une distribution Debian." >&2
  exit 1
fi

if command -v curl >/dev/null 2>&1; then curl -fsSL "$INSTALLATEUR" | sh -s -- gagarine
elif command -v wget >/dev/null 2>&1; then wget -qO- "$INSTALLATEUR" | sh -s -- gagarine
else echo "Gagarine : il faut curl ou wget." >&2; exit 1
fi
echo
echo "Gagarine est installé. Pour vérifier, sans risque : sudo gagarine essai"
