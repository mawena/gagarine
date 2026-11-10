PYTHON ?= python3

.PHONY: test deb clean

# Tests unitaires (décisions sur des mesures simulées, sans toucher au poste)
test:
	$(PYTHON) -m unittest discover -s tests -v

# Construit dist/gagarine_<version>_all.deb avec SHA256SUMS et lintian
deb:
	scripts/build-deb.sh

clean:
	rm -rf dist build debian/gagarine debian/.debhelper debian/files debian/*.substvars debian/*.debhelper debian/*.debhelper.log debian/debhelper-build-stamp
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
