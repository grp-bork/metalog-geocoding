# HTTP API reference

The `service/` directory wraps the geocoder in a small Flask application. It
answers two questions about coordinates:

- **`POST /check`**: may this sample be accepted, and which `location_hierarchy`
  rows does its coordinate contribute? (grading)
- **`POST /geocode`**: what is at this point? (raw lookup, no grading)

This document describes the service version `0.2.0`. Examples were produced by
running the Flask test client against the real boundary layers with the
bundled marine vocabulary (see [Vocabulary](#vocabulary)).

> **Coordinate order is `(latitude, longitude)` everywhere in this API.**
> Every coordinate pair is `[lat, lon]`, never GIS-style `[lon, lat]` and never
> GeoJSON order. A swapped pair is the most common mistake; if latitude is
> outside -90..90 the service says so (`coordinate_out_of_range`), but a swap
> with both values inside range is silently a different place.

## Contents

1. [Overview and configuration](#overview-and-configuration)
2. [Endpoints](#endpoints): `GET /health`, `POST /check`, `POST /geocode`, `GET /options`, `GET /`
3. [Errors and status codes](#errors-and-status-codes)
4. [Grades and codes](#grades-and-codes)
5. [The location_hierarchy rows](#the-location_hierarchy-rows)
6. [Compatibility rules](#compatibility-rules)
7. [Python client](#python-client)

## Overview and configuration

| | |
| --- | --- |
| Default port | `8007` |
| Base URL | wherever you deploy it, e.g. `http://localhost:8007` |
| Request content type | `application/json` (POST bodies; the body must be a JSON object) |
| Response content type | `application/json` (except `GET /`, which is HTML, and unrouted paths, which return Flask's HTML 404/405 page) |
| Authentication / TLS | **None.** The service is built for an internal network. Do not expose it to the internet; put authentication in front of it first. |
| State | Stateless with respect to databases: it never writes one. It does add newly geocoded coordinates to its SQLite result caches in `$METALOG_GEOCODING_CACHE_DIR`. |
| Concurrency | Run with **one** gunicorn worker (the Dockerfile does). Each worker would load its own ~1.2 GB of boundary layers; geocoding is serialised by an in-process lock, and spare threads (`--threads 4`) keep `/health` answering while one batch runs and others wait for the lock. Scale out with independent containers, each with its own cache directory. |

The boundary layers (Natural Earth, IHO Sea Areas, Longhurst, GSHHG coast) are
loaded once at startup. If the data directory is incomplete, or the marine
vocabulary cannot express every IHO sea name, the process fails at start-up
and never binds a port.

### Environment variables

All configuration is by environment variable, read once at start-up.

| Variable | Default | Meaning |
| --- | --- | --- |
| `METALOG_GEOCODING_DATA` | `./data` | Directory holding the boundary-layer parquet files. Start-up fails if any required layer is missing. |
| `METALOG_GEOCODING_CACHE_DIR` | `cache` | Writable directory for the SQLite result caches (`land_cache.sqlite`, `sea_cache.sqlite`) and any sea-hierarchy chains fetched beyond the bundled ones (`sea_hierarchy.tsv`). Point it at a scratch directory if you do not want exploratory lookups added to your `cache/`; the bundled hierarchy keeps that offline. |
| `METALOG_GEOCODING_MARINE_REGIONS` | bundled `metalog_geocoding/vocab/marine_regions.txt` | Marine vocabulary: one sea name per line, `#` comments allowed. |
| `METALOG_GEOCODING_SEA_ALIASES` | bundled `vocab/sea_aliases.tsv` | Other spellings of vocabulary names. |
| `METALOG_GEOCODING_SEA_CONTAINMENT` | bundled `vocab/sea_containment.tsv` | Seas with no IHO polygon and the IHO sea that contains them. |
| `METALOG_GEOCODING_LOCATION_EXCEPTIONS` | none | Optional: `(latitude, longitude, geographic_location)` triples, verified by hand, accepted despite an `error` grade. Opt-in; there is no bundled file. |
| `METALOG_GEOCODING_HOST` | `0.0.0.0` | Bind address; used only by `python service/app.py` (dev entry point). |
| `METALOG_GEOCODING_PORT` | `8007` | Port; used only by `python service/app.py`. Under gunicorn the Dockerfile binds `0.0.0.0:8007` directly. |

For the four vocabulary variables: if the variable is **unset**, the default
above applies; if it is **set** to a path, that file replaces the default and
must exist or start-up fails; if it is set to the **empty string**, that input
is switched off (for the marine vocabulary: no vocabulary, classification by
regex).

### Vocabulary

The package bundles a default marine vocabulary (`metalog_geocoding/vocab/`):
`marine_regions.txt` (every IHO sea area under its common English name, e.g.
`Barents Sea` for IHO's `Barentsz Sea`), `sea_aliases.tsv` and
`sea_containment.tsv`. The service uses these unless you override them (see the
table above). With a vocabulary, a `geographic_location` is classified as a
sea by membership, aliases are honoured, and sea names are published in the
vocabulary's spelling. If you switch the marine vocabulary off (empty string),
the service still runs: a `geographic_location` is classified as a sea by the
regex `^Gulf of |Ocean$|Sea$`, and sea names are published in IHO's own
spelling. `GET /health` and `GET /options` report which mode is active. See
[validation.md](validation.md) for the file formats.

## Endpoints

### `GET /health`

Liveness and provenance. Takes no parameters.

**200 response**

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | string | Always `"ok"` if the service answers. |
| `version` | string | Package version. |
| `layers_loaded` | boolean | Always `true` for a running service (the process does not start otherwise). |
| `vocabulary_size` | integer or null | Number of names in the marine vocabulary; `null` if none is loaded. |
| `location_exceptions` | integer | Number of loaded location exceptions (`0` unless you configured a file). |
| `coast_source` | string | Provenance and method of the `coast` scheme's distances (GSHHG version, citation, rounding, sign convention). |

```bash
curl -s http://localhost:8007/health
```

```json
{
  "coast_source": "GSHHG 2.3.7 (Wessel & Smith 1996, doi:10.1029/96JB00104), full resolution, ... signed geodesic distance on the WGS84 ellipsoid (pyproj) to the nearest shoreline point, in km to 0.1 km, positive at sea and negative on land ...",
  "layers_loaded": true,
  "location_exceptions": 0,
  "status": "ok",
  "version": "0.2.0",
  "vocabulary_size": 175
}
```

(`coast_source` is abbreviated here.)

### `POST /check`

Grade a batch of samples against their stated locations and return the
`location_hierarchy` rows their coordinates contribute.

The request carries **unique coordinates once**, and each sample refers to a
coordinate by its index. This keeps the body small when many samples share a
coordinate (geocoding is a pure function of `(lat, lon)`).

**Request body**

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `coordinates` | array of `[latitude, longitude]` | yes (may be empty) | The distinct coordinates to geocode. Each entry must be an array of exactly two JSON numbers (booleans and strings are rejected). Range is *not* validated here; see "Impossible coordinates" below. |
| `samples` | array of objects | yes (may be empty) | The samples to grade. |

Each sample:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `id` | string (non-empty) | yes | Returned verbatim as the verdict's `id`. Not required to be unique. Non-strings are coerced with `str()`. |
| `coord` | integer | no | Zero-based index into `coordinates`. If present (not `null`), `latitude`/`longitude` are ignored. Must be an integer in range (booleans rejected). |
| `latitude`, `longitude` | number or string | only if no `coord` | Used when `coord` is absent or `null`. Use this for a sample whose coordinate is missing (send `""` or omit) or impossible; it is graded from the coordinate alone and never reaches the geocoder. Defaults to `""` (no coordinate). |
| `geographic_location` | string | no | The place the sample claims (a country, a sea, ...). Decides whether land or sea is expected, and what the coordinate is compared with. Default `""` (treated as land, no claim to contradict). |
| `location_resolution` | string | no | **Optional.** How the coordinate was recorded. See [location_resolution values](#location_resolution-values). If omitted or blank, the coordinate is treated as observed: it is graded and gets the full hierarchy. Setting it to `country` or `continent` marks the coordinate as derived from the name (truncated hierarchy, graded `uncheckable`). Default `""`. |
| `artificial` | string | no | Non-empty for a control, mock, cultivation, etc. `negative control` and `mock` contribute no hierarchy rows (their coordinate is where the work was done). Default `""`. |

**Response (200)**

| Field | Type | Meaning |
| --- | --- | --- |
| `verdicts` | array | **Exactly one entry per submitted sample, in submission order.** |
| `verdicts[].id` | string | The sample `id`. |
| `verdicts[].grade` | string | `ok`, `error`, `uncheckable` or `no coordinates`. See [Grades and codes](#grades-and-codes). |
| `verdicts[].code` | string | Stable machine-readable name of the rule that decided the grade. |
| `verdicts[].reason` | string | Human-readable finding; empty for plain `ok` and `no_coordinates`. |
| `location_hierarchy` | array | Deduplicated, sorted [hierarchy rows](#the-location_hierarchy-rows) for every `(coordinate, location_resolution)` block among the samples. Empty if no sample qualifies. |

```bash
curl -s -X POST http://localhost:8007/check \
  -H 'Content-Type: application/json' \
  -d '{
    "coordinates": [[47.37, 8.54]],
    "samples": [
      {"id": "S1", "coord": 0, "geographic_location": "Switzerland", "location_resolution": "site"},
      {"id": "S3", "coord": 0, "geographic_location": "France",      "location_resolution": "site"},
      {"id": "S4", "latitude": "", "longitude": "", "geographic_location": "France"},
      {"id": "S5", "coord": 0, "geographic_location": "Switzerland", "location_resolution": "country"},
      {"id": "S6", "latitude": 95, "longitude": 10, "geographic_location": "France"}
    ]
  }'
```

```json
{
  "verdicts": [
    {"id": "S1", "grade": "ok", "code": "ok", "reason": ""},
    {"id": "S3", "grade": "error", "code": "different_location",
     "reason": "the sample states 'France', but the coordinate is inside 'Switzerland'; the nearest land called 'France' is 76 km away"},
    {"id": "S4", "grade": "no coordinates", "code": "no_coordinates", "reason": ""},
    {"id": "S5", "grade": "uncheckable", "code": "derived_coordinates",
     "reason": "location_resolution is 'country', so the coordinates were derived from 'Switzerland' rather than observed and cannot contradict it"},
    {"id": "S6", "grade": "error", "code": "coordinate_out_of_range",
     "reason": "the coordinate cannot exist: latitude 95.0 is outside -90..90 (are latitude and longitude swapped?)"}
  ],
  "location_hierarchy": [
    {"latitude": 47.37, "longitude": 8.54, "location_resolution": "site", "scheme": "admin", "level": 1, "key": "continent", "value": "Europe"},
    {"latitude": 47.37, "longitude": 8.54, "location_resolution": "site", "scheme": "admin", "level": 2, "key": "country", "value": "Switzerland"},
    {"latitude": 47.37, "longitude": 8.54, "location_resolution": "site", "scheme": "admin", "level": 3, "key": "admin_1", "value": "Zürich"},
    {"latitude": 47.37, "longitude": 8.54, "location_resolution": "site", "scheme": "admin", "level": 0, "key": "country_code", "value": "CHE"},
    "... more rows, including the coast scheme ...",
    {"latitude": 47.37, "longitude": 8.54, "location_resolution": "country", "scheme": "admin", "level": 2, "key": "country", "value": "Switzerland"}
  ]
}
```

(The hierarchy is abbreviated and its row order is illustrative. Rows are
returned sorted, but treat the order as unspecified.)

A sea example: `{"id": "S7", "coord": 0,
"geographic_location": "Pacific Ocean"}` against a point in Switzerland
yields `grade: "error"`, `code: "not_on_sea"`.

**Impossible coordinates.** An out-of-range coordinate is not a malformed
request. Send it as the sample's own `latitude`/`longitude` (without `coord`),
as the Python client does; the sample's verdict is then a normal 200 result
with `code: "coordinate_out_of_range"` and the geocoder is never asked.
(Placing an out-of-range pair in `coordinates` is not what the client does and
its behaviour is not specified here.)

**Caches.** Each call may append newly resolved coordinates to the SQLite result caches,
even if the request later fails partway. A cold coordinate costs a
point-in-polygon lookup (and, for a sea never seen before, possibly a
MarineRegions request if the hierarchy cache lacks that sea; the shipped cache
covers all 101 IHO seas).

### `POST /geocode`

Reverse geocode coordinates and return the raw result, without grading.
Use this when there is no sample to grade.

**Request body**

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `coordinates` | array of `[latitude, longitude]` | yes | Same shape rules as for `/check`. |
| `type` | string | no, default `"auto"` | One of `auto`, `best`, `land`, `sea`, `both`. `best` returns one result (`ReverseGeocoder.geocode_best`): a direct land hit wins (the sea lookup is then skipped), else a direct sea hit, else whichever fallback snapped over the shorter distance, else the unknown result; the entry's `requested_type` is `best` and `result.type` says which won. `auto` classifies `geographic_location` the same way `/check` does (a sea name gives `sea`, anything else, including empty, gives `land`). `both` returns a land and a sea lookup per coordinate. |
| `geographic_location` | string | no | Only used when `type` is `auto`. |

**Response (200)**: `{"results": [...]}` with one entry per coordinate **per
looked-up type** (so two per coordinate for `both`), in request order, land
before sea.

| Field | Type | Meaning |
| --- | --- | --- |
| `latitude`, `longitude` | number | The coordinate as sent. |
| `requested_type` | string | `land` or `sea`: the lookup that was run. |
| `result` | object | The geocode (see below). Absent if `error` is present. |
| `error` | string | Present instead of `result` for an impossible coordinate. The request still returns 200, so one bad entry does not cost the rest of the batch. |

**The `result` object.** Keys vary with the lookup, and a key is omitted when
it has no value. A total failure is an object with little more than
`is_fallback: false` (and `fallback_tried: true`), without a `type`. Treat every
key as optional.

Land (`type: "land"`):

| Key | Meaning |
| --- | --- |
| `continent`, `continent_political` | Geographic and political continent. |
| `country`, `country_code` | Country name and ISO-3166 alpha-3 code (Natural Earth `ISO_A3_EH`, falling back to `ADM0_A3`). |
| `name_1`, `type_1`, `iso_3166_2`, `adm1_code` | First-level subdivision, its type, its ISO 3166-2 code (may be blank or non-unique) and Natural Earth's unique admin-1 identifier. |
| `is_fallback` | `true` if the point was not inside any land polygon and the answer comes from a nearby one (see below). |
| `fallback_distance_m`, `original_lat`, `original_lon` | Present on fallbacks: distance to the land used and the coordinate originally asked. |
| `coast_*` | Distance to coast and the water the nearest coast faces: `coast_flag`, `coast_distance_km` (negative on land), `coast_landmass_area_km2`, `coast_landmass_name`, `coast_sea`, `coast_mrgid`, `coast_sea_chain`. |

Sea (`type: "sea"`):

| Key | Meaning |
| --- | --- |
| `sea` | Sea name, in the vocabulary's spelling when a vocabulary is loaded. |
| `ocean` | Gazetteer chain, **most specific first**, `;`-separated, e.g. `"Mediterranean Sea - Western Basin; Mediterranean Sea"`. |
| `mrgid` | MarineRegions identifier of the sea (string). |
| `longhurst_code`, `longhurst_province`, `longhurst_biome`, `longhurst_distance_m` | Longhurst province, if any (see the `longhurst` scheme). |
| `is_fallback`, `fallback_distance_m`, `original_lat`, `original_lon` | As for land; the sea fallback searches within 10 km. |
| `coast_*` | Distance to coast and the place the nearest coast is in: `coast_flag`, `coast_distance_km`, `coast_landmass_*`, `coast_continent`, `coast_country`, `coast_admin_1`, `coast_country_code`, `coast_adm1_code`, `coast_country_source`. |

Land lookups try, in order: the point itself, a 1 km offset (inland if on land,
towards shore if in water), then the nearest land polygon within 10 km (50 km
south of 60°S). Sea lookups try the point itself, then the nearest sea within
10 km. Results from the later steps have `is_fallback: true`.

```bash
curl -s -X POST http://localhost:8007/geocode \
  -H 'Content-Type: application/json' \
  -d '{"coordinates": [[47.37, 8.54], [95, 10]], "type": "land"}'
```

```json
{
  "results": [
    {
      "latitude": 47.37, "longitude": 8.54, "requested_type": "land",
      "result": {
        "adm1_code": "CHE-176",
        "coast_distance_km": -327.5,
        "coast_flag": "ok",
        "coast_landmass_area_km2": 50700000.0,
        "coast_landmass_name": "Eurasia",
        "coast_mrgid": 3363,
        "coast_sea": "Ligurian Sea",
        "coast_sea_chain": "Ligurian Sea; Mediterranean Sea - Western Basin; Mediterranean Sea",
        "continent": "Europe",
        "continent_political": "Europe",
        "country": "Switzerland",
        "country_code": "CHE",
        "is_fallback": false,
        "iso_3166_2": "CH-ZH",
        "name_1": "Zürich",
        "type": "land",
        "type_1": "Canton"
      }
    },
    {
      "error": "the coordinate cannot exist: latitude 95.0 is outside -90..90 (are latitude and longitude swapped?)",
      "latitude": 95.0, "longitude": 10.0, "requested_type": "land"
    }
  ]
}
```

A sea lookup (`"type": "sea"`) at `[43.3, 5.4]`, a point 2.2 km inland that
resolved to the Mediterranean through the fallback:

```json
{"results": [{"latitude": 43.3, "longitude": 5.4, "requested_type": "sea",
  "result": {
    "coast_adm1_code": "FRA-5274", "coast_admin_1": "Bouches-du-Rhône",
    "coast_continent": "Europe", "coast_country": "France",
    "coast_country_code": "FRA", "coast_country_source": "natural_earth",
    "coast_distance_km": -2.2, "coast_flag": "ok",
    "coast_landmass_area_km2": 50700000.0, "coast_landmass_name": "Eurasia",
    "fallback_distance_m": 2235.57, "is_fallback": true,
    "longhurst_biome": "Westerlies", "longhurst_code": "MEDI",
    "longhurst_distance_m": 2554.83,
    "longhurst_province": "Mediterranean Sea, Black Sea Province",
    "mrgid": "4279",
    "ocean": "Mediterranean Sea - Western Basin; Mediterranean Sea",
    "original_lat": 43.3, "original_lon": 5.4,
    "sea": "Mediterranean Sea - Western Basin", "type": "sea"}}]}
```

Note that `coast_distance_km` keeps the sign GSHHG gives the *original*
point on a fallback result, so a sea sample reached only by snapping can read as
inland.

The raw `result` is the geocoder's internal shape and is less tightly specified
than the `/check` response; for a stable, flattened view use the
`location_hierarchy` rows from `/check`.

### `GET /options`

The vocabularies the browser form offers, served so that clients need not copy
them. Takes no parameters.

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | string | Package version. |
| `location_resolutions` | array of `{value, hierarchy}` | Every `location_resolution` and what it publishes: `hierarchy` is `full`, `truncated` or `none`. |
| `artificial_no_location` | array of string | `artificial` values that contribute no hierarchy (`mock`, `negative control`). |
| `geocode_types` | array of string | Values accepted for `/geocode`'s `type`. |
| `seas` | array of string | Sea names a `geographic_location` can be checked against: the marine vocabulary if loaded, else IHO's names. Sorted. |
| `countries` | array of string | Country names a lookup can return (Natural Earth `NAME_LONG` with the service's overrides). Sorted. |
| `has_vocabulary` | boolean | Whether a marine vocabulary is loaded. |

```bash
curl -s http://localhost:8007/options
```

```json
{
  "version": "0.2.0",
  "has_vocabulary": true,
  "geocode_types": ["auto", "best", "land", "sea", "both"],
  "artificial_no_location": ["mock", "negative control"],
  "location_resolutions": [
    {"value": "city", "hierarchy": "full"},
    {"value": "exact_location", "hierarchy": "full"},
    {"value": "region", "hierarchy": "full"},
    {"value": "site", "hierarchy": "full"},
    {"value": "village", "hierarchy": "full"},
    {"value": "continent", "hierarchy": "truncated"},
    {"value": "country", "hierarchy": "truncated"},
    {"value": "unknown", "hierarchy": "none"},
    {"value": "in orbit", "hierarchy": "none"}
  ],
  "seas": ["Adriatic Sea", "Aegean Sea", "Alboran Sea", "..."],
  "countries": ["Afghanistan", "Akrotiri", "Albania", "..."]
}
```

### `GET /`

The browser form (static HTML and JavaScript under `service/static/`). Returns
`text/html`. It is a client of this same API (`/geocode`, `/check`, `/options`)
and holds no logic of its own. Static assets are served under `/static/`.

## Errors and status codes

Three outcomes are deliberately distinct, and clients must not conflate them:

| Situation | How it appears |
| --- | --- |
| A sample is **bad** (wrong country, not on land, ...) | HTTP **200**, with `grade: "error"` in that sample's verdict. A normal result. |
| The **request is malformed** | HTTP **400**, body `{"error": "<message>"}`. A caller bug; retrying will not help. |
| The service is **unavailable** | Connection failure, timeout, or a 5xx. No answer was obtained; a caller that gates on grades must stop, not treat this as a pass. |

400 messages (all with the shape `{"error": "..."}`):

| Message | Cause |
| --- | --- |
| `request body must be a JSON object` | Body is missing, not valid JSON, or not an object. (The content type is not checked; the body just has to parse.) |
| `missing or non-array 'coordinates' key` | `coordinates` absent or not an array. |
| `coordinates[i] must be a [latitude, longitude] pair, got ...` | Entry is not a two-element array, or contains a boolean. |
| `coordinates[i] must be numeric, got ...` | An element is not a number (including numeric strings). |
| `missing or non-array 'samples' key` | `/check` only. |
| `samples[i] must be an object` | `/check` only. |
| `samples[i] is missing a non-empty 'id'` | `/check` only. |
| `samples[i] ('id'): 'coord' must be an integer index` | `/check` only. |
| `samples[i] ('id'): coord index N is out of range for M coordinates` | `/check` only. |
| `'type' must be one of auto, best, land, sea, both, got ...` | `/geocode` only. |

Other statuses: `404` for an unknown path and `405` for a wrong method are
Flask's default HTML pages, not JSON. An unexpected internal exception yields
Flask's default 500 page (HTML). Validation is all-or-nothing: the first
malformed element rejects the whole request.

## Grades and codes

`grade` is the decision. `code` says which rule made it. `reason` is the
sentence shown to a person.

**Compare on `code` (or `grade`); never match on `reason`.** `code` values are
stable and safe for programmatic use. `reason` is human text that names the
claim, the place the coordinate landed in and the deciding distance, so it
differs from sample to sample and may be reworded in any release.

| `grade` | Meaning |
| --- | --- |
| `ok` | The coordinate is consistent with the stated location (or there is nothing to contradict). |
| `error` | The coordinate contradicts the sample's data. This is the only grade a fail-closed consumer should treat as a failure. |
| `uncheckable` | The coordinate cannot contradict the claim, because it was derived from the place name (`location_resolution` of `country` or `continent`). |
| `no coordinates` | The sample records no usable coordinate. Not an error: no claim was made that could be wrong. |

There is no `warn` grade. Handle an unrecognised grade defensively: a
fail-closed client must not treat it as a pass.

| `code` | `grade` | Meaning |
| --- | --- | --- |
| `ok` | `ok` | Consistent. |
| `accepted_exception` | `ok` | Would be an `error`, but this exact `(latitude, longitude, geographic_location)` is in the location exceptions file (verified by hand). `reason` begins with `accepted by exception: `. |
| `derived_coordinates` | `uncheckable` | Coordinates derived from the place name. |
| `no_coordinates` | `no coordinates` | No usable coordinate. |
| `coordinate_out_of_range` | `error` | Latitude outside -90..90 or longitude outside -180..180. |
| `not_on_land` | `error` | A land location is claimed, but the point is not on land, nor within the fallback threshold of any. |
| `not_on_sea` | `error` | A sea is claimed, but the point is in no IHO sea nor within 10 km of one. |
| `unrelated_sea` | `error` | A sea is claimed and a different sea found, and the two are unrelated in the IHO/MarineRegions hierarchy (neither is an ancestor of the other). |
| `claim_too_far` | `error` | The point resolved only by a shoreline fallback, and the claimed country is more than 100 km away. |
| `different_location` | `error` | A direct hit in a different country from the claim, with the claimed country more than 2 km away. Borders are lines with their own error, so a point within 2 km of the claimed country passes. A territory Natural Earth folds into a larger unit (Svalbard, French Guiana, ...) is compared with its own polygon, so a Svalbard claim near Oslo lands here too. |
| `unknown_place` | `error` | The claimed land name (or no name at all) is not a country or territory Natural Earth maps (a misspelling, an island, a region), so no coordinate could match it, whether the point is inside a country or just off a coast. Fix the name (or add an alias), not the coordinate. |

The library exposes these as `CODE_*` constants in `metalog_geocoding/samples.py`
(`ERROR_CODES` lists the seven error codes). A client may see codes added in
future versions; handle unknown codes as you would any unrecognised value.

### Grading rules in brief

- Impossible coordinates are checked first.
- No usable coordinate gives `no coordinates`. `location_resolution` of
  `country` or `continent` gives `uncheckable`.
- A `geographic_location` that names a sea (by vocabulary, or by the regex
  fallback) is checked against the sea geocode; anything else, including an
  empty value, is checked against the land geocode.
- Claimed names are matched using country aliases (`Congo-Brazzaville` equals
  `Republic of the Congo`) and sea containment/alias tables, so spelling alone
  does not cause an error.

## The location_hierarchy rows

`/check` returns `location_hierarchy` as a long-format list. One row is one
fact about one coordinate block.

| Field | Type | Meaning |
| --- | --- | --- |
| `latitude`, `longitude` | number | The coordinate, as submitted. |
| `location_resolution` | string | **Present only when the sample set one**; the key is absent from the row otherwise. One coordinate recorded at two resolutions yields two blocks. |
| `scheme` | string | `admin`, `marine`, `longhurst` or `coast`. |
| `level` | integer | `1` is the broadest tier; larger is more specific. **`0` means an attribute, not a tier.** Never null. |
| `key` | string | The name of the tier or attribute. |
| `value` | string | **Always a string**, including numbers (`"2235.57"`) and booleans (`"True"`/`"False"`). Empty values are never emitted. |

The natural key is `(latitude, longitude, location_resolution, scheme, level, key)`
(treat an absent `location_resolution` as empty); it is unique within a response. `level` is part of the key because a deep marine
chain can have two rows with `key = basin` that differ only in level.

Rows are deduplicated per `(coordinate, location_resolution)` block and sorted.

### Schemes and levels

| scheme | level 1 | level 2 | level 3 | level 4 |
| --- | --- | --- | --- | --- |
| `admin` | `continent` | `country` | `admin_1` | none |
| `marine` | `ocean` | `basin` or `sea` | `basin` or `sea` | `sea` |
| `longhurst` | `biome` | `province` | none | none |
| `coast` | `coast_continent` | `coast_country` | `coast_admin_1` | none |

- `admin` comes from Natural Earth (land). It is fixed at three levels.
- `marine` comes from the IHO Sea Areas gazetteer chain, broadest first. Level
  1 is always `ocean`, the deepest is always `sea`, everything between is
  `basin`. A one-deep chain yields a single `ocean` row. Chains are 1 to 4 deep.
  The `sea` value uses the vocabulary's spelling; `ocean` and `basin` values use
  the gazetteer's.
- `longhurst` is the Longhurst biogeochemical province, a separate
  classification of the same water, not a marine level. Absent where the point
  has no province within 10 km (inland water, ice sheet).
- `coast` is distance to coast and where the coast is. See below.

### Level-0 attributes

| scheme | `key` | Meaning |
| --- | --- | --- |
| `admin` | `country_code` | ISO 3166-1 alpha-3 country code. |
| `admin` | `iso_3166_2` | ISO 3166-2 code; can be blank (then omitted) or non-unique. |
| `admin` | `adm1_code` | Natural Earth's admin-1 identifier; unique and never blank where an admin-1 exists. Use this as the region code. |
| `admin` | `continent_political` | Political continent (differs from `continent` for some transcontinental regions). |
| `admin`, `marine` | `is_fallback` | `"True"` if the answer came from a nearby polygon rather than the point itself. |
| `admin`, `marine` | `fallback_distance_m` | Distance in metres to the polygon used when `is_fallback`. |
| `marine` | `mrgid` | MarineRegions identifier of the sea. |
| `longhurst` | `longhurst_code` | Four-letter province code. |
| `longhurst` | `longhurst_distance_m` | Distance in metres to the province when it was found by the 10 km nearest search. |
| `coast` | `coast_flag` | Written whenever the lookup ran (`ok` in the examples). |
| `coast` | `coast_distance_km` | **Signed** distance to the nearest GSHHG shoreline, to 0.1 km: positive at sea, negative on land; lakes count as land. `0.0` where IHO and Natural Earth place a direct hit on the opposite side to GSHHG. |
| `coast` | `coast_landmass_area_km2` | GSHHG's area for the polygon the nearest shore point is on, to 3 significant figures. |
| `coast` | `coast_landmass_name` | Name of that landmass, where known. |
| `coast` | `coast_country_code`, `coast_adm1_code`, `coast_country_source` | For sea points: country and admin-1 codes of the nearest coast, and `natural_earth` or `geonames` for how it was placed. |
| `coast` | `coast_ocean`, `coast_sea`, `coast_mrgid` | For land points: the ocean, sea (omitted when the polygon is an ocean) and MRGID that the nearest coast faces. |

The `coast_*` keys deliberately differ from `country`/`admin_1`: the values
share the `admin` vocabulary and so join to land samples, but a key belongs to
one scheme, so that counting `country = Germany` does not start counting water
off Germany. For sea points, `coast_country` is the *nearest* coast's country; a
point close to a border is attributed to the nearer side.

Distances to coast: land under 1 km² counts as coast only within 30 km at sea.
Full method and citations are in the `coast_source` field of `/health`.
The coast is derived from GSHHG 2.3.7 (Wessel & Smith 1996,
doi:10.1029/96JB00104); please cite it wherever these distances appear.

### What a block contains

| `location_resolution` | Rows contributed |
| --- | --- |
| `city`, `exact_location`, `region`, `site`, `village` | Full hierarchy: `admin` + `coast` for a land result; `marine` + `longhurst` + `coast` for a sea result. |
| `country` | `continent`, `country`, `country_code` only (the coordinate is a country centre, not a place). |
| `continent` | `continent` only. |
| `unknown`, `in orbit` | Nothing. |
| empty or omitted | Full hierarchy, as for an observed coordinate; the rows carry no `location_resolution` key. |
| any, with `artificial` of `negative control` or `mock` | Nothing (tested before the coordinate). |

A coordinate that resolves nowhere, or a sample with no usable coordinate,
contributes nothing. Whether a sample graded `error` also contributes rows is
not specified; decide from the verdicts whether to use a block.

### location_resolution values

Listed by `GET /options`: `city`, `exact_location`, `region`, `site`,
`village` (full hierarchy); `country`, `continent` (truncated, and graded
`uncheckable`); `unknown`, `in orbit` (no hierarchy). Any other non-empty value
publishes no hierarchy. The field is optional: if a sample omits it (or leaves
it blank) the coordinate is treated as observed, with the full hierarchy.

## Compatibility rules

- **Responses change additively.** New keys may appear in any JSON object in a
  response (verdicts, hierarchy rows, `/health`, `/options`, `/geocode` results),
  and new `code` values, `location_resolution` options, schemes and
  `key`s may appear. **Clients must ignore keys they do not recognise.**
- Existing keys are not renamed or repurposed. `grade` values are not added
  without notice because a consumer that fails only on `error` would treat a new
  grade as a pass.
- Request shape: coordinates are `[lat, lon]` throughout; `/check` accepts the
  `coordinates` + `samples` + index form shown above.
- Do not rely on the order of `location_hierarchy` rows, the exact text of
  `reason`, or the exact set of keys inside a `/geocode` `result`.
- `verdicts` is always one-per-sample in request order, so a client can zip it
  against its own rows.

## Python client

`metalog_geocoding.client` needs only the base install (`requests` and the
standard library); it does not import any geo packages. Install with
`pip install .` (no extras).

```python
from metalog_geocoding.client import GeocodingClient, GeocodingRequestError, GeocodingUnavailable
from metalog_geocoding.samples import Sample

client = GeocodingClient()                       # URL resolved as below
client.health()                                  # raises if the service is down

samples = [
    Sample(id="S1", latitude=47.37, longitude=8.54,
           geographic_location="Switzerland", location_resolution="site"),
    Sample(id="S2", latitude="", longitude="", geographic_location="France"),
]
result = client.check(samples)
for v in result.verdicts:                        # one per sample, in order
    if v.grade == "error":
        print(v.sample_id, v.code, v.reason)
rows = result.hierarchy                          # list of dicts, one per fact
```

- `Sample` fields are `id`, `latitude`, `longitude`, `geographic_location`,
  `location_resolution` (optional; omitted from the request when unset),
  `artificial`; coordinates may be strings or floats and
  are kept as recorded. `Sample.coordinate` returns a float pair or `None`.
- `client.check()` builds the deduplicated `coordinates` list for you
  (`samples.distinct_coordinates()`: first-seen order, ungeocodable coordinates
  dropped), sends samples without a usable coordinate as `latitude`/`longitude`
  with no `coord`, and returns a `CheckResult` with `verdicts` (`Verdict`
  objects with `sample_id`, `grade`, `code`, `reason`) and `hierarchy`.
- The client wraps `/health` and `/check` only. For `/geocode` and `/options`,
  use any HTTP library.
- `GeocodingClient(base_url=None, timeout=300, session=None)`. The default
  timeout is 300 seconds because a cold batch can geocode thousands of new
  coordinates.

### Service URL resolution

First hit wins:

1. the explicit `base_url` argument (or a script's `--geocoding-url` flag),
2. the `METALOG_GEOCODING_URL` environment variable,
3. `http://localhost:8007`.

A trailing slash is stripped. `metalog_geocoding.client.add_argument(parser)`
adds a `--geocoding-url` option to an `argparse` parser with the default left as
`None`, so the environment variable still applies when the flag is omitted.
`client.service_url(explicit=None)` returns the resolved URL.

### Exceptions

| Exception | Base | Raised when | What to do |
| --- | --- | --- | --- |
| `GeocodingRequestError` | `ValueError` | The service returned HTTP 4xx. The message includes the service's `error` text. | A bug in the caller's request. Do not retry. |
| `GeocodingUnavailable` | `RuntimeError` | The service could not be reached (connection error, timeout), returned 5xx, or returned a non-JSON 2xx body. | No answer was obtained. Retry later; a fail-closed caller must stop rather than treat this as a pass. |

A sample grading `error` is **not** an exception: it is a normal return value.

```python
try:
    result = client.check(samples)
except GeocodingRequestError as exc:
    raise SystemExit(f"bug in request: {exc}")
except GeocodingUnavailable as exc:
    raise SystemExit(f"geocoding service unavailable, nothing was checked: {exc}")

failed = [v for v in result.verdicts if v.grade == "error"]
```

Call `client.health()` first in a long job so that a down service stops the run
before any work is done.

## Deployment notes

- Container: `docker compose -f service/docker-compose.yml up -d` (publishes
  `8007`; mounts the data directory read-only, the cache directory read-write
  and, optionally, vocabulary override files read-only). The image runs
  `gunicorn --workers 1 --threads 4 --timeout 600 --bind 0.0.0.0:8007 wsgi:app`.
- Local development: `python service/app.py` (Flask development server on
  `$METALOG_GEOCODING_HOST:$METALOG_GEOCODING_PORT`).
- Memory: about 1.2 GB resident once the boundary layers and the coast are
  loaded.
- Start-up takes a few seconds on a warm filesystem; use `GET /health` as the
  readiness probe.
