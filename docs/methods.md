# Methods

How each answer is produced. Output fields are listed in [usage.md](usage.md#output-fields).

`ReverseGeocoder` (`metalog_geocoding/geocoder.py`) holds the control flow; the
other modules are lookups.

| Module | Role |
| --- | --- |
| `land.py` (`NaturalEarthLandGeocoder`) | point-in-polygon against Natural Earth admin-0 and admin-1; smallest containing polygon wins |
| `sea.py` (`SeaGeocoder`) | point-in-polygon against IHO Sea Areas; smallest by area wins |
| `longhurst.py` | the 54 Longhurst provinces, then a 10 km nearest-province search |
| `fallback.py` | nearest-polygon search, geodesic via `pyproj.Geod(ellps='WGS84')` |
| `coast.py` (`CoastDistance`) | signed distance to GSHHG coastline |
| `cache.py`, `hierarchy.py` | result caches; the sea-to-ocean hierarchy walk |

## Land

### Why Natural Earth

Natural Earth is public domain, so annotations derived from it can be
redistributed under any licence, including ODbL; GADM does not permit
redistribution. The cost is small: on ~37,000 land coordinates the direct hit rate
was 88.4 % against GADM's 90.5 %, and after the fallback ladder the gap was 0.28 %,
all offshore or low-precision points. Natural Earth has no admin-2 and no local
type designations.

### The adjustment ladder

`geocode(..., "land")` tries up to three coordinates and returns the first that
yields a country:

1. the original point;
2. a 1 km offset from `calculate_inland_offset()`: away from the shoreline if the
   point is already on land, toward it if it is in water (`fallback_distance_m = 1000`);
3. the nearest land polygon within a threshold (10 km; 50 km south of 60°S, where
   coastal data is sparse). The result is read from the admin-0 row that was
   measured, so a narrow spit or key still resolves. Only the country-level
   fields are published in this case: nothing was inside an admin-1 polygon, so
   the nearest country is a measurement where the subdivision would be a guess.

Results from steps 2-3 carry `is_fallback`, `fallback_distance_m`, `original_lat`
and `original_lon`. If everything is beyond the threshold the result is `unknown`.

### Continents

`continent_political` = admin-0 `CONTINENT`, then `SEVEN_SEAS_CONTINENTS` for the
nine units Natural Earth labels "Seven seas (open ocean)", then
`POLITICAL_CONTINENTS` keyed by country code.

`continent` (geographic) = the admin-1 `iso_3166_2` looked up in the merged
override tables (`geographic_overrides.py`, `natural_earth_continents.py`), else
the same admin-0 `CONTINENT`. The geographic split exists only where a table
entry does: Russia (all federal subjects), Egypt (Sinai), the United States
(Hawaii), France, Spain, Portugal, Chile (Easter Island and Salas y Gómez, the part
of Valparaíso region west of 100°W) and the Caribbean Netherlands.

There is deliberately no spatial join against separate continent polygons:
remote islands fall into the gaps. Reading the continent off the country polygon
has none.

`NaturalEarthLandGeocoder.__init__` asserts that the "Seven seas" unit set and the
Russian federal-subject coverage still match the tables, so a Natural Earth
upgrade fails loudly.

### Country names

Natural Earth's name fields disagree: `ADMIN` is formal, `BRK_NAME` abbreviates
34 units, `NAME_LONG` is the common form for 255 of 258. `NAME_LONG` is
published, with `COUNTRY_NAME_OVERRIDES` (`land.py`) for the units where it is
formal or abbreviated (the two Congos are deliberately left alone). An assertion
at start-up fails if an upgrade introduces an abbreviation the table does not
cover. `country_code` uses `ISO_A3_EH`, falling back to `ADM0_A3` (plain `ISO_A3`
is `-99` for 22 units including France and Norway).

`country_aliases.py` is separate: it decides whether two *names* denote the same
country (for validation only; it never rewrites output).

## Sea

Direct point-in-polygon against IHO Sea Areas v3, choosing the **smallest polygon
by area** when several contain the point (the most specific sea). If none does,
the nearest sea polygon within a flat 10 km is used (no polar widening).

IHO v3's Gulf of Finland follows the Neva into Lakes Ladoga and Onega, so
`cut_gulf_of_finland_lakes()` severs it at St Petersburg after every load and
drops the lake piece; it raises if the polygon stops splitting that way.

### Sea to ocean hierarchy

`SeaHierarchyCache.get_ocean(mrgid)` walks MarineRegions `partof` relations
upward (max depth 10, one request per second) until it reaches a major ocean,
producing a chain like `Gulf of Riga; Baltic Sea; Atlantic Ocean`. The chains for
all 101 IHO v3 polygons ship with the package (`metalog_geocoding/vocab/sea_hierarchy.tsv`),
so lookups are offline whatever the cache directory holds. The walk runs only for an
MRGID missing from that file (a new IHO edition); its result is written to
`sea_hierarchy.tsv` in the cache directory and read back over the bundled chains. This
is the **only** network access in the package.

Two failures are distinct:

- `UnmappedSeaError`: the API answered but the chain does not reach a major
  ocean. Add the topmost MRGID to `SEA_TO_OCEAN_FALLBACK` in `hierarchy.py`
  (look it up at `https://www.marineregions.org/mrgid/<MRGID>`).
- `HierarchyLookupError`: MarineRegions was unreachable after retries with
  exponential backoff. Retry; do not add a fallback entry.

`cache/sea_cache.sqlite` stores the chain next to the `mrgid`, but the stored chain
is not trusted on read: `ocean` is re-derived from the cached `mrgid` on every
cache hit, never fetching, so a corrected hierarchy takes effect without
invalidating the cache.

### Spelling

IHO and the bundled marine vocabulary spell about ten of the 101 seas differently
(`Barentsz Sea`/`Barents Sea`, `Japan Sea`/`Sea of Japan`, ...). When a
vocabulary is in use (`MarineVocabulary`, bundled by default), `published_sea()` rewrites the
`sea` output to the vocabulary's spelling; `mrgid` remains the identity.
`check_vocabulary_covers_iho()` fails before geocoding if an IHO name has neither
an entry nor an alias. With the vocabulary switched off, IHO's own spellings are used.

## Longhurst provinces

Every successful sea result also gets `longhurst_code`, `longhurst_province` and
`longhurst_biome`, looked up against the **original** coordinate (not a
fallback-snapped one). If no province contains the point, the nearest within 10 km
is used and `longhurst_distance_m` is set. `ProvDescr` is `"Biome - Name (ALTCODE)"`;
only a trailing bare four-letter uppercase group is stripped as the code, since
`(West)` and `(East)` are part of the name.

The province polygons stop at the coastline and exclude inland water, so a valid
IHO sea can have none (the Caspian, Amazon river stations, the Antarctic ice
sheet); beyond 10 km they are left blank rather than snapped to a distant province.

## Distance to coast

Every land and sea result gets a signed `coast_distance_km` and related fields
(see [usage.md](usage.md#distance-to-coast-land-and-sea-results)), measured at the
original coordinate against GSHHG 2.3.7 (Wessel & Smith 1996,
doi:10.1029/96JB00104; LGPL v3). Only distances are published, never geometry.

- **Coast** is any GSHHG L1 polygon (continents and islands, no size cutoff) or L5
  polygon (the Antarctic ice front).
- **Signed**: positive at sea, negative on land. A point inside an L1/L5 polygon
  measures to that polygon's boundary, lakes counted as land. GSHHG's sea reaches up
  tidal estuaries, so a point at Hamburg port is about -9 km (to the Elbe), not
  -100 km.
- **IHO and Natural Earth outrank GSHHG** on which side of the shoreline a point is
  when they agree: a sea result IHO hit directly where Natural Earth has no country, or
  a land result Natural Earth hit directly where IHO has no sea, is published as
  `0.0` if GSHHG puts it on the other side. A fallback result keeps GSHHG's sign.
- **Small land counts only nearby.** Land under 1 km² counts only within 30 km
  (`SMALL_LAND_RADIUS_M`, `MIN_DISTANT_LANDMASS_KM2`); beyond that the nearest
  landmass of at least 1 km² is used, so a mid-ocean station is not measured to a
  lone rock. The 30 km window follows the scale at which island-mass effects were
  measured (Gove et al. 2016, doi:10.1038/ncomms10581; Weigelt et al. 2013,
  doi:10.1073/pnas.1306309110 characterise marine islands > 1 km²); it is a
  measurement window, not a claim that the effect stops there.
- **No distance to "the nearest continent"**: GSHHG's continent is a numbering
  convention (Ireland and Sicily are islands), which cannot separate shelf islands
  from oceanic ones. `coast_landmass_area_km2` is published instead, so readers apply
  their own size cutoff.
- **Precision is 0.1 km.** The coordinate's own precision and GSHHG's shoreline error
  (about 50-500 m, worse on reclaimed coasts) both add to the error.
- **Exact nearest point, not degree-ranked.** The nearest polygon in degrees is not
  the nearest in metres at these distances and latitudes. `_ChunkIndex` takes a
  geodesic upper bound, collects every chunk whose box could hold a closer point
  (spherical box on the smallest WGS84 radius, wrapping at ±180°), and measures them
  in an azimuthal-equidistant projection centred on the sample. It agrees with a
  brute-force search over all vertices.
- **Shapefile seams are not coast.** Polygons crossing 180° are split into `-E`/`-W`
  halves closed along the meridian, and Antarctica is closed down to -90°;
  segments lying on those seams are dropped.
- **Antarctica is repaired at load time** (`_close_to_pole`): both halves are
  invalid as shipped. The on-land test is `covered_by`, so seam points count as land.
- **GSHHG over OpenStreetMap:** within 10 km of shore the two coastlines agree to
  2 km for 90 % of points, and OSM needs about 2.9 GB peak memory against 1.1 GB.

### Where the nearest coast is (sea points)

`coast_continent`, `coast_country`, `coast_admin_1` (+ codes) give the place of the
*nearest* coast, in the `admin` scheme's vocabulary (so they join to land samples)
but under separate keys so that counting "samples per country" does not include
water off that country. A point 4.4 km from Poland and 7.7 km from Germany is
Poland. The coast point is placed by Natural Earth at the point (or the nearest
country within `COAST_MATCH_M`, 5 km); failing that by the landmass's GeoNames
country, mapped to Natural Earth. `coast_country_source` says which.

### What a land point's coast faces

For land points the nearest coast's country is not reported; instead
`coast_sea`/`coast_ocean`/`coast_mrgid` name the IHO sea containing (or nearest to) the
GSHHG coast point and its ocean. Inland of a tidal estuary this is the sea at the
mouth (Hamburg: North Sea).

### Landmass names

`data/GSHHS_f_names.parquet` is built by `scripts/build_landmass_names.py`.
Natural Earth's named `Island` regions win, each assigned to the largest GSHHG
polygon inside it; otherwise the best GeoNames island, islet or atoll feature
inside the polygon or within 2 km is used, skipping points in lakes. About 97 % of
polygons of 100 km² or more are named. The country comes from the chosen GeoNames
feature, resolved to a Natural Earth `ADM0_A3` (including territories Natural Earth
folds into a sovereign) and an admin-1 where unique; an unnamed polygon borrows the
country of the nearest GeoNames island within 30 km. Roughly half a dozen of the
~300 landmasses above 1,000 km² still carry a minor islet's name.

## Caching

The result caches are SQLite databases (`land_cache.sqlite`, `sea_cache.sqlite`), one
row per coordinate with the result as a JSON object of strings:

```sql
CREATE TABLE results (lat REAL, lon REAL, data TEXT, PRIMARY KEY (lat, lon)) WITHOUT ROWID;
-- -40.55  142.5  {"mrgid": "4276", "sea": "Great Australian Bight", ...}
```

Lookups go to the database, so memory does not grow with the cache, and each
result is committed as it is added, so a process killed mid-batch keeps what it
finished. A TSV cache from earlier versions (`land_cache.tsv`
in long format: `lat`, `lon`, `level`, `name`) is imported once when the database
beside it does not exist yet.

- **Keys are the coordinate exactly as given** (`DEFAULT_DECIMALS = None`). Rounding is
  an opt-in (`ReverseGeocoder(..., cache_decimals=)`): at 2 decimals (~1.1 km) the key
  can cross a shoreline (about 1 % of land coordinates change answer), while the useful
  deduplication comes from repeated *identical* coordinates. Changing it invalidates
  existing entries.
- **Keys are stored as REAL**, an IEEE double, so a full-precision coordinate is found
  again exactly; no string formatting sits between the key and the database.
- `round_coord` calls `float()` on both arguments because `Decimal` and `float` of
  the same value do not hash alike.
- **Negative caching.** A total failure is stored as `fallback_tried = True` so it is
  not retried. On read, an entry is trusted only if it holds real data
  (`country`/`country_code` for land, `ocean`/`sea` for sea) or `fallback_tried`.
  The cache stores everything as strings; `is_fallback` (bool), the distance and
  `original_*` fields (float) and `mrgid`/`coast_mrgid` (int) are converted back on
  read, so a cached result has the same types as a fresh one. A sea coordinate with no
  Longhurst province is marked `longhurst_tried`, which lets old entries be topped up
  in place. `coast_flag` is the "already computed" marker for the coast fields, so
  changing any coast field means deleting the `coast_*` fields from every entry and re-running.
- A warm run reproduces a cold run byte for byte; to test that, delete the two
  result caches, re-run and diff.
- Delete `cache/*.sqlite*` to reset results (and any old `cache/*_cache.tsv`, or it is imported again); delete `data/*.pkl` to rebuild the IHO pickle.
