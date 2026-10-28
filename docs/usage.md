# Usage

Three ways to use the geocoder: the command-line scripts, the Python API, and the HTTP
service ([service/README.md](../service/README.md), [api.md](api.md)). The first
two need `[geo]` and the boundary layers ([installation-and-data.md](installation-and-data.md)).

## Command-line scripts

Two scripts, one per question. Both read a TSV with a header, deduplicate
coordinates before looking anything up, and save the caches even if they fail
part-way. Options common to both: `--data-dir` (default
`$METALOG_GEOCODING_DATA`, else `data/`) and `--cache-dir` (default `cache/`).
Worked examples with checked-in outputs are in `examples/`
([examples/README.md](../examples/README.md)).

### Geocoding: what is at this coordinate?

```bash
python scripts/geocode.py input.tsv output.tsv [--type best|land|sea|both]
```

**Input**: `latitude` and `longitude` are required. Every other column is
passed through unchanged, in order, before the result columns.

| `--type` | Behaviour |
| --- | --- |
| `best` (default) | Look up land and sea and keep one result, via `ReverseGeocoder.geocode_best`: a direct land hit, else a direct sea hit, else the fallback that snapped over the shorter distance. The `type` column says which won |
| `land` | `geocode(lat, lon, "land")` only |
| `sea` | `geocode(lat, lon, "sea")` only |
| `both` | Both lookups side by side, columns prefixed `land_` and `sea_` |

**Output** columns, in a fixed order: the input columns, `error`, `type`
(not for `both`), then the main fields (`continent` ... `country_code`,
`name_1`; `ocean`, `sea`, `mrgid`), the fallback fields (`is_fallback`,
`fallback_distance_m`, `original_lat`, `original_lon`), the `longhurst_*` and
`coast_*` fields, then anything else sorted. A coordinate that is blank gets
empty results; one that cannot exist (latitude 95) gets a message in `error`
and no lookup; one that resolves to nothing has `type = unknown`.

### Checking: does the coordinate agree with the stated location?

```bash
python scripts/check.py input.tsv out_prefix
```

This is the service's `LocationChecker` (the grading policy is in
[validation.md](validation.md)) run over a file. **Input**: `latitude`,
`longitude` and `geographic_location` are required; `id` (default: the 1-based
row number), `location_resolution` and `artificial` are optional, and an absent
column means blank (an observed coordinate with a full hierarchy; an ordinary
sample).

It writes two files:

- `out_prefix_verdicts.tsv`: `id`, the input columns that were present, `grade`
  (`ok`, `error`, `uncheckable`, `no coordinates`), `code` (the rule that fired;
  compare on this), `reason` (a human-readable sentence) and `claim_type`
  (`land` or `sea`, what the stated name asked for).
- `out_prefix_hierarchy.tsv`: the `location_hierarchy` rows the samples
  contribute (see [location-hierarchy.md](location-hierarchy.md)), without the
  `location_resolution` column when the input had none.

Vocabulary options: `--marine-regions`, `--sea-aliases` and `--sea-containment`
default to the files bundled with the package; `--no-vocabulary` switches all
three off; `--location-exceptions` names an optional exceptions file (default
none). The exit code is 0 whatever the grades; a count per grade goes to stderr.

## Python API

```python
from pathlib import Path
from metalog_geocoding import ReverseGeocoder

geocoder = ReverseGeocoder(data_dir=Path("data"), cache_dir=Path("cache"))

result = geocoder.geocode(lat=62.03, lon=129.73, location_type="land")
# {'type': 'land', 'continent': 'Asia', 'continent_political': 'Europe',
#  'country': 'Russia', 'country_code': 'RUS', 'iso_3166_2': 'RU-SA',
#  'name_1': 'Sakha (Yakutia)', 'type_1': 'Republic', 'is_fallback': False, ...}

result = geocoder.geocode(lat=-40.55, lon=142.50, location_type="sea")
# {'type': 'sea', 'sea': 'Great Australian Bight',
#  'ocean': 'Great Australian Bight; Indian Ocean', 'mrgid': 4276,
#  'longhurst_code': 'AUSW', 'longhurst_biome': 'Coastal', ...}

results = geocoder.geocode_batch([(52.52, 13.40, "land"), (55.00, 21.00, "sea")])
geocoder.save_caches()      # land, sea and sea-hierarchy caches
```

- `location_type` is required and is `"land"` or `"sea"`; there is no automatic
  land/sea detection at this level.
- Coordinates are `(lat, lon)`, although Shapely internally uses `(lon, lat)`.
- If neither the direct lookup nor the fallback succeeds the result is
  `{'type': 'unknown', 'is_fallback': False}`.
- Call `save_caches()` after batch work (both scripts do it in a `finally`).
- Coordinates are validated before geocoding by `LocationChecker`; the bare
  `ReverseGeocoder` does not range-check, so an impossible coordinate yields
  nonsense. Use the checker or the service when input is untrusted.

`location_resolution` is optional for grading and hierarchy rows: a sample that
omits it (or leaves it blank) is treated as an observed coordinate (full
hierarchy, and its rows carry no `location_resolution` key). Set it to
`country` / `continent` only when the coordinate was derived from the name.
`LocationChecker` takes a marine vocabulary; the package bundles a default one
(`metalog_geocoding.vocabulary.BUNDLED_MARINE_REGIONS`, `BUNDLED_SEA_ALIASES`,
`BUNDLED_SEA_CONTAINMENT`, see [validation.md](validation.md#vocabulary-inputs)).

For grading samples and producing hierarchy rows use `LocationChecker`
([validation.md](validation.md), [location-hierarchy.md](location-hierarchy.md)); for the
service use `GeocodingClient` ([api.md](api.md)).

## Output fields

### Land

| Field | Description |
| --- | --- |
| `continent` | **Geographic** continent; Russia is split into Europe and Asia. Use for environmental samples |
| `continent_political` | **Political** continent; all of Russia is Europe. Use for human samples |
| `country` | Country name (Natural Earth `NAME_LONG`, with a small override table) |
| `country_code` | Three-letter code (`ISO_A3_EH`, falling back to `ADM0_A3`) |
| `iso_3166_2` | Code of the admin-1 unit containing the point (`RU-SA`, `US-HI`) |
| `name_1` / `type_1` | First administrative level and its designation |

Natural Earth has no deeper levels, so there is no `name_2`. The continent
vocabulary is Africa, Antarctica, Asia, Europe, North America, Oceania, South America.

### Sea

| Field | Description |
| --- | --- |
| `sea` | Most specific IHO sea area containing the point |
| `ocean` | Chain, most specific first: `Gulf of Riga; Baltic Sea; Atlantic Ocean` |
| `mrgid` | MarineRegions Gazetteer ID of the sea area |
| `longhurst_code` | Four-letter Longhurst province code (`NASW`) |
| `longhurst_province` | Province name |
| `longhurst_biome` | `Polar`, `Westerlies`, `Trades` or `Coastal` |
| `longhurst_distance_m` | Only when the nearest-province search (<= 10 km) was used |

For just the ocean take the last element: `ocean.split(";")[-1].strip()`. IHO
areas name a body of water; Longhurst provinces are ecological units, and the
two are independent lookups, so a point can have one without the other.

### Distance to coast (land and sea results)

| Field | Description |
| --- | --- |
| `coast_flag` | `ok`; marks the lookup as done |
| `coast_distance_km` | **Signed** geodesic distance to the nearest sea coast, islands included, to 0.1 km: positive at sea, negative on land |
| `coast_landmass_name` | Name of the landmass the nearest coast belongs to (absent for unnamed islets); on land, the island or continent the point is on |
| `coast_landmass_area_km2` | Area of that landmass, 3 significant figures |
| `coast_continent`, `coast_country`, `coast_admin_1` (+ `coast_country_code`, `coast_adm1_code`, `coast_country_source`) | **Sea results only.** Where the nearest coast is |
| `coast_sea`, `coast_ocean`, `coast_mrgid` | **Land results only.** The water the nearest coast faces |

Details and caveats in [methods.md](methods.md#distance-to-coast).

### Fallback

| Field | Description |
| --- | --- |
| `is_fallback` | `True` if the result came from an adjusted coordinate |
| `fallback_distance_m` | Geodesic distance in metres from the original point to the location used |
| `original_lat` / `original_lon` | The original query coordinates |

## Limitations

- Check `is_fallback` before treating a near-shore result as exact.
- Disputed territories follow Natural Earth's boundaries.
- Antarctica has a continent but no administrative detail.
- Hong Kong (`HKG`) and Macao (`MAC`) are their own units, not part of `CHN`.
- Turkey and Kazakhstan are not split geographically (both are entirely `Asia`);
  add `TR-*`/`KZ-*` entries to `metalog_geocoding/geographic_overrides.py` if needed.
- Natural Earth's `SOVEREIGNT` field is deliberately not emitted: it records
  Palestine's sovereign as Israel.
- All geometry is handled in EPSG:4326 without reprojection; reported fallback
  distances are geodesic (WGS84 via `pyproj`).
