# metalog-geocoding service

A small Flask wrapper around `metalog_geocoding.checker.LocationChecker`. This
page covers deployment; the endpoints are documented in
[docs/api.md](../docs/api.md), and the grading policy in
[docs/validation.md](../docs/validation.md) and
[docs/location-hierarchy.md](../docs/location-hierarchy.md). The service adds
no policy of its own.

## Why a service

With the boundary layers (Natural Earth, IHO Sea Areas, Longhurst) and the
GSHHG coastline loaded, a process uses over 1 GB of RSS. The service keeps that
on one host, so callers need only the base install (`requests` and the
standard library) and never load a polygon. Callers find it through
`$METALOG_GEOCODING_URL` or a `--geocoding-url` flag, both handled by
`metalog_geocoding/client.py`.

## Design constraints

- **Stateless.** The service never writes to a database. `/check` returns
  hierarchy rows; the caller stores them.
- **Layers load once, at startup.** Building `LocationChecker` raises
  `VocabularyCoverageError` if the configured vocabulary cannot express every
  IHO sea name, and that stops the process before it binds a port, rather than
  let it publish sea names the caller does not expect. A missing data
  directory fails the same way.
- **No authentication and no TLS.** The network is the boundary: bind the port
  to an internal interface, and add authentication before adding any public
  route.
- **Single worker.** Each worker would load its own ~1.2 GB of boundary
  layers. Run
  `gunicorn --workers 1` (the Dockerfile does) and scale with independent
  containers, each with its own cache directory.

## Running locally

From the repository root, with `pip install -e '.[geo,service]'`:

```bash
export METALOG_GEOCODING_DATA=./data
export METALOG_GEOCODING_CACHE_DIR=./cache
# Optional overrides of the bundled vocabulary:
# export METALOG_GEOCODING_MARINE_REGIONS=/path/to/marine_regions.txt
# export METALOG_GEOCODING_SEA_ALIASES=/path/to/sea_aliases.tsv
# export METALOG_GEOCODING_SEA_CONTAINMENT=/path/to/sea_containment.tsv
# export METALOG_GEOCODING_LOCATION_EXCEPTIONS=/path/to/location_exceptions.tsv

python service/app.py            # dev server on 0.0.0.0:8007
# or, as in production:
gunicorn --workers 1 --threads 4 --timeout 600 --bind 0.0.0.0:8007 --chdir service wsgi:app
```

Unset, the three vocabulary variables use the files bundled in
`metalog_geocoding/vocab/`. Set to a path, that file replaces the bundled one,
and a missing file is a startup error. Set to the empty string, that input is
switched off (with no marine vocabulary, classification is by regex and sea
names are published in IHO's spelling). The location-exceptions file has no
default. See `service/config.py`.

## Docker

```bash
docker compose -f service/docker-compose.yml up -d
```

The stack builds from the repository root, mounts the boundary layers
read-only and a writable cache directory, publishes port 8007 and
health-checks `/health`. Override the two volume paths with
`METALOG_GEOCODING_DATA` and `METALOG_GEOCODING_CACHE`. To replace the bundled
vocabulary, uncomment the example volume and variables in the compose file.

## The browser form

`GET /` (e.g. `http://localhost:8007/`) serves a page for pasting coordinates
and seeing where they land: a Leaflet map, the raw geocode, the verdict, and
the `location_hierarchy` rows the coordinate would contribute. It is static
HTML in `service/static/` that calls `/geocode`, `/check` and `/place-distance` like any other
client, and takes its field vocabularies from `GET /options`.

The paste box takes:

- One coordinate per line, separated by tab, comma or spaces. The `Order`
  selector switches between `lat, lon` and `lon, lat`; a swapped pair is a
  common mistake, and the out-of-range error message suggests it.
- Optional text after the two numbers, used as that row's
  `geographic_location`; the form field applies to rows without one.
- `#` comments. Clicking the map appends a coordinate.

`Lookup` picks the layer: `land + sea` runs both side by side; `auto`
classifies `geographic_location` the way `/check` does.

**Test repairs** also lists, for each row with a `geographic_location`, the
coordinate's swapped and sign-flipped versions (up to eight) and how far each
is from the place the name refers to (`POST /place-distance`). A version inside
a polygon is green, one within 25 km of a point or line amber, and each is a
small marker on the map. A note names the version when exactly one lands
inside, but the page decides nothing. Place outlines are not drawn, since the
API returns distances only. A name new to the service may come back
`pending` while it is looked up; the page asks again for about half a minute.

Two caveats:

- **Lookups from the form are written to the result caches** like any other,
  and a new place name under Test repairs is looked up online (Wikidata,
  Nominatim) and cached.
  Point `METALOG_GEOCODING_CACHE_DIR` away from a version-controlled `cache/`
  if that matters.
- **Leaflet and the map tiles are fetched by the browser** from the internet.
  Without access the map is blank and the tables still work.

## Calling it

```python
from metalog_geocoding import GeocodingClient, Sample

client = GeocodingClient()            # $METALOG_GEOCODING_URL, else localhost:8007
result = client.check([
    Sample(id="SAMEA123", latitude="55.079", longitude="10.238",
           geographic_location="Kattegat", location_resolution="exact_location"),
])
for verdict in result.verdicts:
    print(verdict.sample_id, verdict.grade, verdict.code, verdict.reason)
result.hierarchy                      # rows for the caller to store
```

Branch on `code`, display `reason`. A 200 means every sample got a verdict,
even if all of them are `error`; `GeocodingRequestError` (HTTP 4xx) means the
request was malformed; `GeocodingUnavailable` means there is no answer at all,
and a fail-closed caller must stop rather than treat it as a pass. Keep
response changes additive, so hand-written clients that read keys with
`.get()` keep working. Details: [docs/api.md](../docs/api.md).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `METALOG_GEOCODING_DATA` | `./data` | Directory holding the boundary-layer parquet files (Natural Earth, IHO, Longhurst, GSHHG). |
| `METALOG_GEOCODING_CACHE_DIR` | `cache` | Directory for the SQLite result caches. Must be writable. |
| `METALOG_GEOCODING_MARINE_REGIONS` | bundled `vocab/marine_regions.txt` | Sea-name vocabulary. Unset: bundled; path: that file (missing is a startup error); empty: none (regex classification). |
| `METALOG_GEOCODING_SEA_ALIASES` | bundled `vocab/sea_aliases.tsv` | Other spellings of vocabulary sea names. Same rules. |
| `METALOG_GEOCODING_SEA_CONTAINMENT` | bundled `vocab/sea_containment.tsv` | Seas IHO has no polygon for, mapped to the IHO sea containing them. Same rules. |
| `METALOG_GEOCODING_LOCATION_EXCEPTIONS` | none | Coordinates verified by hand where Natural Earth has no polygon for the ground. `GET /health` reports the entry count. |
| `METALOG_GEOCODING_NOMINATIM_URL` | OSM's public Nominatim | Search endpoint for `/place-distance`. Empty: boundary layers only. Lookups are paced at one per 1.1 s per process, so enable it in one instance only. |
| `METALOG_GEOCODING_WIKIDATA_URL` | Wikidata's API | Decides which place a name means for `/place-distance`. Empty: no Wikidata. Paced like Nominatim. |
| `METALOG_GEOCODING_PLACE_LOOKUPS_PER_REQUEST` | `20` | Online requests per `/place-distance` call; names past it come back `pending`. |
| `METALOG_GEOCODING_HOST` | `0.0.0.0` | Dev server bind host (gunicorn uses `--bind`). |
| `METALOG_GEOCODING_PORT` | `8007` | Dev server bind port. |

Callers read `METALOG_GEOCODING_URL` (default `http://localhost:8007`); an
explicit `--geocoding-url` outranks it.
