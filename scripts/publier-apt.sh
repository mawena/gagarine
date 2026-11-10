#!/usr/bin/env bash
# Publie un ou plusieurs .deb dans le dépôt APT signé de Gagarine (branche gh-pages,
# https://mawena.github.io/gagarine/apt). Idempotent ; les anciennes versions restent dans pool/.
# Usage : scripts/publier-apt.sh dist/gagarine_<version>_all.deb...
# Variables : GAGARINE_APT_CLE (empreinte de la clé, défaut ci-dessous), GAGARINE_APT_PUSH=0 (ne pas pousser).
set -euo pipefail

CLE="${GAGARINE_APT_CLE:-FE66B5A900CBE8EBFB8FB079FCA7E8E0C5513B93}"
RACINE="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
[ $# -ge 1 ] || { echo "usage : $0 fichier.deb..." >&2; exit 2; }
for f in "$@"; do [ -f "$f" ] || { echo "introuvable : $f" >&2; exit 2; }; done
DEBS=()
for f in "$@"; do DEBS+=("$(readlink -f "$f")"); done

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
cp "${DEBS[@]}" "$APT/pool/main/"
cp "$RACINE/data/apt/gagarine.gpg" "$APT/gagarine.gpg"

cat > "$WT/index.html" <<'H'
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Gagarine, dépôt APT</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem">
<h1>Gagarine, dépôt APT</h1>
<p>Garde-RAM pour Linux. Ce dépôt signé permet de mettre Gagarine à jour avec <code>apt upgrade</code>.</p>
<h2>Installation</h2>
<pre>sudo curl -fsSL https://mawena.github.io/gagarine/apt/gagarine.gpg -o /usr/share/keyrings/gagarine.gpg
sudo curl -fsSL https://raw.githubusercontent.com/mawena/gagarine/main/data/apt/gagarine.sources -o /etc/apt/sources.list.d/gagarine.sources
sudo apt update
sudo apt install gagarine</pre>
<p>Ensuite, <code>sudo apt update &amp;&amp; sudo apt upgrade</code> suffit. Les paquets sont aussi sur la page des
<a href="https://github.com/mawena/gagarine/releases">releases GitHub</a>.</p>
</body></html>
H

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

cd "$WT"
git add -A
if git diff --cached --quiet; then
  echo "Dépôt APT déjà à jour."
else
  VERS="$(for d in "${DEBS[@]}"; do basename "$d"; done | tr '\n' ' ')"
  git -c user.name="$(git -C "$RACINE" config user.name)" -c user.email="$(git -C "$RACINE" config user.email)" \
    commit -q -m "feat(apt): publication de ${VERS% }"
fi
if [ "${GAGARINE_APT_PUSH:-1}" = 1 ]; then git push -q origin gh-pages; echo "gh-pages poussée."; fi
