#!/usr/bin/env bash
# Publie un ou plusieurs .deb dans le dépôt APT signé de Gagarine (branche gh-pages,
# https://mawena.github.io/gagarine/apt), avec la page d'accueil du site (dossier site/ du dépôt, captures de
# docs/captures, icône). Idempotent ; les anciennes versions restent dans pool/.
# Usage : scripts/publier-apt.sh dist/gagarine_<version>_all.deb...   (sans paquet : met seulement le site à jour)
# Variables : GAGARINE_APT_CLE (empreinte de la clé, défaut ci-dessous), GAGARINE_APT_PUSH=0 (ne pas pousser).
set -euo pipefail

CLE="${GAGARINE_APT_CLE:-FE66B5A900CBE8EBFB8FB079FCA7E8E0C5513B93}"
RACINE="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
for f in "$@"; do [ -f "$f" ] || { echo "introuvable : $f" >&2; exit 2; }; done
DEBS=()
for f in "$@"; do DEBS+=("$(readlink -f "$f")"); done
if [ ${#DEBS[@]} -eq 0 ] && ! git -C "$RACINE" rev-parse -q --verify refs/remotes/origin/gh-pages >/dev/null; then
  echo "usage : $0 fichier.deb...   (la première publication demande au moins un paquet)" >&2; exit 2
fi

cd "$RACINE"
git fetch -q origin gh-pages 2>/dev/null || true
TMP="$(mktemp -d)"
WT="$TMP/pages"
trap 'git -C "$RACINE" worktree remove --force "$WT" 2>/dev/null || true; rm -rf "$TMP"' EXIT

if git rev-parse -q --verify refs/remotes/origin/gh-pages >/dev/null; then
  git worktree add -q -B gh-pages "$WT" origin/gh-pages
else
  git worktree add -q --detach "$WT" HEAD
  git -C "$WT" checkout -q --orphan gh-pages
  git -C "$WT" rm -rq --cached . 2>/dev/null || true
  find "$WT" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
fi

APT="$WT/apt"
mkdir -p "$APT/pool/main"
touch "$WT/.nojekyll"
[ ${#DEBS[@]} -eq 0 ] || cp "${DEBS[@]}" "$APT/pool/main/"
cp "$RACINE/data/apt/gagarine.gpg" "$APT/gagarine.gpg"
cp "$RACINE/data/apt/gagarine.sources" "$APT/gagarine.sources"

# Site : page d'accueil, feuille de style, script, installateur, icône et captures de l'interface
for f in "$RACINE"/site/*; do cp "$f" "$WT/"; done
cp "$RACINE/data/icons/hicolor/scalable/apps/io.github.mawena.Gagarine.svg" "$WT/icone.svg"
rm -rf "$WT/captures"; mkdir -p "$WT/captures"
cp "$RACINE"/docs/captures/*.png "$WT/captures/"

# Idempotence : les index ne sont régénérés et signés que si les paquets ou la clé ont changé (Date change à chaque
# fois) ; un changement du site seul est publié sans toucher aux index.
git -C "$WT" add -A
if git -C "$WT" diff --cached --quiet && [ -f "$APT/dists/stable/InRelease" ]; then
  echo "Dépôt APT et site déjà à jour."
  exit 0
fi
if ! git -C "$WT" diff --cached --quiet -- apt/pool apt/gagarine.gpg || [ ! -f "$APT/dists/stable/InRelease" ]; then
(
  cd "$APT"
  D=dists/stable/main
  mkdir -p "$D/binary-all" "$D/binary-amd64"
  dpkg-scanpackages --multiversion pool/main /dev/null > "$D/binary-all/Packages" 2>/dev/null
  : > "$D/binary-amd64/Packages"   # le paquet est « all » ; index vide pour que apt trouve les deux
  for a in all amd64; do gzip -9nc "$D/binary-$a/Packages" > "$D/binary-$a/Packages.gz"; done
  rm -f dists/stable/Release dists/stable/InRelease dists/stable/Release.gpg
  apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=Gagarine \
    -o APT::FTPArchive::Release::Label=Gagarine \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Codename=stable \
    -o APT::FTPArchive::Release::Components=main \
    -o APT::FTPArchive::Release::Architectures="all amd64" \
    -o APT::FTPArchive::Release::Description="Gagarine, garde-RAM Linux" \
    release dists/stable > "$TMP/Release"
  mv "$TMP/Release" dists/stable/Release
  gpg --batch --yes --local-user "$CLE" --clearsign -o dists/stable/InRelease dists/stable/Release
  gpg --batch --yes --local-user "$CLE" --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
)
fi

cd "$WT"
git add -A
if git diff --cached --quiet; then
  echo "Dépôt APT déjà à jour."
else
  VERS="$(for d in "${DEBS[@]}"; do basename "$d"; done | tr '\n' ' ')"
  if [ -n "${VERS% }" ]; then MESSAGE="feat(apt): publication de ${VERS% }"; else MESSAGE="docs(site): page d'accueil mise à jour"; fi
  git -c user.name="$(git -C "$RACINE" config user.name)" -c user.email="$(git -C "$RACINE" config user.email)" \
    commit -q -m "$MESSAGE"
fi
if [ "${GAGARINE_APT_PUSH:-1}" = 1 ]; then git push -q origin gh-pages; echo "gh-pages poussée."; fi
