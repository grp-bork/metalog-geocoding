# Development

## Tests

```bash
pip install -e '.[test]'
pytest                  # ~205 tests, ~15 s with the geo stack and layers
pytest -m "not geo"     # ~133 tests needing neither the geo stack nor the layers
```

`tests/test_examples.py` runs both scripts on `examples/*_input.tsv` and requires
byte-identical output to the checked-in files, so it also notices a
boundary-layer update changing an answer. After checking that the change is
intended, regenerate the outputs with `pytest tests/test_examples.py --update-examples`
and commit them.

The suite lives in `tests/` and has two halves, mirroring the package's own split:

- **stdlib-only**: packaging, cache, samples, vocabulary, hierarchy table, sea
  relatedness, client. Runs anywhere the base install runs and never imports
  geopandas. `tests/test_packaging.py` asserts, in a subprocess, that
  `import metalog_geocoding` leaves `sys.modules` free of all six geo modules, that
  asking for a geo name raises an error naming the extra, and that an `ImportError`
  from the package's own code is *not* disguised as a missing extra.
- **`@pytest.mark.geo`**: the real layers. They skip themselves when the extra or the
  parquet files are absent, so `pytest` is green on a bare checkout.

Fixture decisions in `tests/conftest.py`: the geo fixtures use an empty temp cache
directory (the bundled sea hierarchy keeps a sea lookup from calling MarineRegions),
so the result caches start empty, so every geo test computes its answer from the
polygons and nothing writes to `cache/`. `--strict-markers` is on, so an unregistered
marker fails rather than silently running a `geo` test in the bare-install job.

`testpaths = ["tests"]` in `pyproject.toml` keeps pytest from collecting stray
`test_*.py` scripts elsewhere (which may geocode at import time); do not remove it.
The geo tests use the bundled vocabulary and write a small exceptions file to a temp
directory, so they need nothing outside this repository.

The bundled `vocab/*.txt` / `vocab/*.tsv` files are package data
(`package-data` in `pyproject.toml`); `check_vocabulary_covers_iho()` guarantees at
start-up that they still express every IHO area.

## Continuous integration

`.github/workflows/ci.yml` defines:

- `bare-install`: installs without the extra, asserts the base install imports with no
  geo stack, that the base surface is reachable and that a geo name raises an error
  naming `[geo]`; runs `pytest -m "not geo"` in an environment where geopandas is
  genuinely absent.
- `geo-install`: installs `.[geo]` on `python:3.14-trixie` and prints the resolve, so a
  dependency that stops shipping a wheel for the target Python is noticed before the
  build silently starts compiling.
- `citation`: validates `CITATION.cff` with `cffconvert --validate`; GitHub and Zenodo
  silently ignore an invalid file.

## Releasing

Each GitHub release is archived on Zenodo, which mints a version DOI and reads its
metadata from `CITATION.cff`. The concept DOI
[10.5281/zenodo.23734816](https://doi.org/10.5281/zenodo.23734816) always resolves to
the latest release.

1. Bump `__version__` in `metalog_geocoding/__init__.py` and `version` in
   `CITATION.cff` to the same value, and retitle `Unreleased` in `CHANGELOG.md` to
   that version and date; commit and push, and wait for CI to pass.
2. Tag and publish a GitHub release, with that version's changelog section as the
   release notes; Zenodo archives releases, not bare tags:
   `git tag -a vX.Y.Z -m "metalog-geocoding X.Y.Z" && git push origin vX.Y.Z && gh release create vX.Y.Z`
3. Once Zenodo has archived it, look up the new version DOI on the Zenodo record and
   update the version and DOI in the example citation in the README's "Citation and
   attribution" section; commit and push.

## Package layout

```
metalog_geocoding/
  geocoder.py                ReverseGeocoder: orchestration and caching          [geo]
  land.py  sea.py  longhurst.py  fallback.py  coast.py                           [geo]
  checker.py                 LocationChecker: grading policy + hierarchy rows    [geo]
  claims.py                  ClaimChecker: distance to the claimed country       [geo]
  cache.py                   SQLite result cache (one row per coordinate)
  hierarchy.py               sea -> ocean chain (MarineRegions API)
  hierarchy_table.py         location_hierarchy row builders and levels
  vocabulary.py              marine vocabulary, published spellings, coverage check
  vocab/                     bundled default vocabulary (package data):
                             marine_regions.txt, sea_aliases.tsv, sea_containment.tsv,
                             sea_hierarchy.tsv
  sea_relatedness.py         sea-vs-sea relatedness test
  location_exceptions.py     exceptions-file loader
  country_aliases.py  containment.py
  political_continents.py  geographic_overrides.py  natural_earth_continents.py
  samples.py                 Sample / Verdict / distinct_coordinates
  client.py                  GeocodingClient
  paths.py                   where the boundary layers live
service/                     Flask wrapper, Dockerfile, browser form
scripts/                     standalone harness; not part of the package
tests/  cache/  data/ (not in git)
```

Modules marked `[geo]` need the extra; the rest are standard library plus `requests`.

### The base-install invariant

`import metalog_geocoding` must work without the geo stack. `__init__.py` resolves
`ReverseGeocoder`, `LandGeocoder`, `NaturalEarthLandGeocoder`, `SeaGeocoder`,
`LonghurstGeocoder` and `FallbackGeocoder` through a module `__getattr__`;
everything else is imported eagerly. A module belongs in the eagerly imported set only
if it imports nothing beyond the standard library and `requests`. A stray top-level
`import geopandas` would break nothing on a machine that has geopandas, which is why
the `bare-install` CI job exists.

The checker's policy modules (`samples.py`, `hierarchy_table.py`, `location_exceptions.py`,
`vocabulary.py`) are standard-library-only on purpose: a client builds a request and
reads a response without loading a polygon, and `distinct_coordinates()` is the dedup
both request and server run, so the body and the server's work are cut by one rule.

### Why the geo stack stays

The `[geo]` extra is about 353 MB installed (pyarrow 120, pandas 71, pyogrio 70, numpy
32, pyproj 17, shapely 7, geopandas 3, including 57 MB of bytecode). Replacing geopandas
with bare shapely is feasible but would save install size, not memory: the parsed IHO
polygons (~430 MB) dominate resident memory whatever library holds them, and the port
would rewrite the modules where the subtle correctness lives (area tie-break, geodesic
fallback, the Natural Earth assertions, claim distances). Do not re-parse the 140 MB IHO
WKB column at process start: the `.pkl` cache in `sea.py` avoids a large transient peak.

## Conventions and gotchas

- Public API is `(lat, lon)`; Shapely and GeoPandas are `(lon, lat)`. Every
  `Point(...)` is `Point(lon, lat)`; check the order first when a result looks mirrored.
- Natural Earth, IHO and Longhurst loaders pass explicit `columns=[...]` to `read_parquet`
  (`ADM0_COLUMNS`, `ADM1_COLUMNS` in `land.py`, and lists in `sea.py`, `longhurst.py`).
  Row selections copy whatever is loaded, so the narrow lists save ~160 MB of memory.
  **If you use a new column, add it to the list**: the failure is a `KeyError` at startup.
- Cache keys are the coordinate exactly as given; see [methods.md](methods.md#caching).
- `save_caches()` must be called after batch work.
- The sea geocoder silently swallows pickle read/write failures and falls back to the
  parquet; a slow start-up usually means a stale or unwritable `.pkl`.
- All geometry is in EPSG:4326; `warnings.filterwarnings` around `distance()` suppresses
  the expected geographic-CRS warnings.
- Compare verdicts on `code`, not `reason` ([validation.md](validation.md)).
- Do not reintroduce a boundary source without checking that its licence permits
  redistribution of derived annotations; that constraint drives the design more than
  accuracy does. Do not build map layers or any geometry product from GSHHG.
- A new Natural Earth release can trip the start-up assertions (seven-seas unit set,
  Russian federal subjects, country-name abbreviations); that is intended.

## Dependencies

All dependency bounds are lower bounds. The service image resolves entirely to binary
wheels (no GDAL source build); keep checking that when a dependency moves. `pandas`
has no upper bound: pandas 2.3 and 3.x produce byte-identical outputs and caches.
