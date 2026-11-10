#!/usr/bin/env bash
# Construit dist/gagarine_<version>_all.deb : tests, paquet, sommes SHA-256, lintian.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Une seule version partout : debian/changelog et gagarine version
deb_version="$(dpkg-parsechangelog -S Version)"
cli_version="$(python3 bin/gagarine version | awk '{print $NF}')"
if [[ "$deb_version" != "$cli_version" ]]; then
  echo "Versions différentes : debian/changelog=$deb_version, gagarine=$cli_version" >&2
  exit 1
fi

make clean >/dev/null
make test
dpkg-buildpackage -us -uc -b
make clean >/dev/null

mkdir -p dist
mv -f "../gagarine_${deb_version}_all.deb" dist/
rm -f "../gagarine_${deb_version}_"*.buildinfo "../gagarine_${deb_version}_"*.changes
(cd dist && sha256sum ./*.deb | sed 's| \./| |' > SHA256SUMS)

lintian --fail-on error "dist/gagarine_${deb_version}_all.deb"
echo "Paquet : dist/gagarine_${deb_version}_all.deb"
