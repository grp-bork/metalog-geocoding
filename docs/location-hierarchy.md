# The location hierarchy table

`LocationChecker` (and the service, via `POST /check`) can express the geocoding
of a coordinate as rows of a long-format table, `location_hierarchy`, meant to be
loaded into a relational database so that samples can be browsed and filtered by
place. One row per *fact*:

`latitude · longitude · [location_resolution ·] scheme · level · key · value`

`location_resolution` is **optional**: it appears in a row only when the sample
that produced it set one. A caller that does not model a resolution gets rows
without that column, and its coordinates are treated as observed (full
hierarchy). When loading into a table whose key includes `location_resolution`,
store a missing value as the empty string.

Long format because the branches are not the same depth: land is fixed at three
levels, sea runs one to four, and a wide table would need `sea_level_1..4` plus a
rule for which is which. `scheme` stops land and sea from having to share a
vocabulary: they are separate trees, not flavours of one tree. Longhurst is its
own scheme because it classifies the same water biogeochemically, not politically.

| scheme | level 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- |
| `admin` | `continent` | `country` | `admin_1` | |
| `marine` | `ocean` | `basin` or `sea` | `basin` or `sea` | `sea` |
| `longhurst` | `biome` | `province` | | |
| `coast` | `coast_continent` | `coast_country` | `coast_admin_1` | |

Marine levels come from reversing the gazetteer chain and numbering it. The key is
anchored at both ends: level 1 is always `ocean`, the deepest is always `sea`,
everything between is `basin` (keying positionally from the top would call the
Greenland Sea a basin in the two-deep chain `Greenland Sea; Arctic Ocean`).

Rows with `level = 0` are **attributes, not levels**: `country_code`,
`iso_3166_2`, `adm1_code`, `continent_political`, `mrgid`, `longhurst_code`,
`longhurst_distance_m`, `is_fallback`, `fallback_distance_m`, and the coast
attributes `coast_flag`, `coast_distance_km`, `coast_landmass_area_km2`,
`coast_landmass_name`, `coast_country_code`, `coast_adm1_code`,
`coast_country_source` (plus `coast_sea`, `coast_ocean`, `coast_mrgid` for land
points). `adm1_code` is Natural Earth's admin-1 identifier; unlike `iso_3166_2` it
is unique and never blank. The `coast` scheme holds attributes only, because GSHHG
is a separate source and not a marine level. Its keys are deliberately not
`country`/`admin_1` so that a "samples per country" query keyed on `country` does
not count water off a country.

## `level` is part of the primary key

A chain with more than one intermediate hop produces two rows keyed `basin`,
distinguishable only by level:

```
55.079  10.238  exact_location  marine  2  basin  North Atlantic Ocean
55.079  10.238  exact_location  marine  3  basin  Baltic Sea
```

So `level` joins the key, and since a Postgres primary key cannot contain a nullable
column, attributes use `0` rather than NULL (levels are numbered from 1):

```sql
CREATE TABLE location_hierarchy (
    latitude            numeric  NOT NULL,
    longitude           numeric  NOT NULL,
    location_resolution text     NOT NULL,
    scheme              text     NOT NULL,  -- admin | marine | longhurst | coast
    level               smallint NOT NULL,  -- 1 = broadest; 0 = attribute
    key                 text     NOT NULL,
    value               text     NOT NULL,
    PRIMARY KEY (latitude, longitude, location_resolution, scheme, level, key)
);
```

For reading it back by value, an index on `(key, value) INCLUDE (latitude,
longitude, location_resolution)` serves both facet lists and value-to-coordinates
lookups as index-only scans; coordinate-to-facts is served by the primary key prefix.

## Which samples get a hierarchy

Rows are keyed per `(coordinate, location_resolution)` block: the same point
recorded at `city` by one source and `country` by another earns two blocks. A
sample with no `location_resolution` (omitted or blank) forms its own block with
no resolution key.

- `site`, `city`, `region`, `village`, `exact_location`, **or no resolution at
  all**: the full hierarchy.
- `country`, `continent`: truncated to `continent`/`country` (+ `country_code`) and
  `continent`, because the coordinate is a country centre derived from the name and not
  a place a sample was taken.
- `unknown`, `in orbit`: nothing.
- **Artificial samples** (a control, mock, cultivation, cell-sorting run: the
  `artificial` field is non-empty) contribute nothing; their coordinate is where the
  work was done. `artificial` is tested **before** the coordinate and the resolution,
  since controls may omit coordinates under any resolution.

Rows are deduplicated and sorted, so two runs over the same data diff as no-ops.

## Properties

- Marine `ocean`/`sea` values are in the vocabulary's spelling (see
  [methods.md](methods.md#spelling)); `mrgid` stays the identity and the hierarchy chains
  keep the gazetteer's own spelling.
- Land points get a `coast` block (distance, landmass, the sea their coast faces);
  sea points get the nearest coast's place.
- No marine chain deeper than 4 occurs, and no admin result has a `name_1` without a
  `country`, so the table shape holds.

Producing rows: `metalog_geocoding.hierarchy_table` (row builders, `ATTRIBUTE_LEVEL`,
resolution vocabulary), `LocationChecker.check_batch()`, or `POST /check` ([api.md](api.md)).
Loading them into a database is up to the caller. The table was developed for the
Metalog database (https://metalog.embl.de).
