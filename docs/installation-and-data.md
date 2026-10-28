# Installation and data

## Install profiles

The package is `metalog-geocoding` (Python >= 3.11).

| Install | Adds | Size | For |
| --- | --- | --- | --- |
| `metalog-geocoding` | client, cache, MarineRegions hierarchy walk, naming tables, path resolution | 0.2 MB + `requests` | anything that only calls the service or reads a hierarchy back |
| `metalog-geocoding[geo]` | geopandas, shapely, pyproj, pandas, numpy, pyarrow | ~353 MB installed | computing anything: the service, batch runs, scripts |
| `metalog-geocoding[convert]` | pyogrio (shapefile/GeoJSON reading) | +0 today | `scripts/download_data.py` |
| `metalog-geocoding[scripts]` | tqdm | small | the `scripts/` harness |
| `metalog-geocoding[service]` | flask, gunicorn | small | running the HTTP service |
| `metalog-geocoding[test]` | pytest | small | the test suite |

`import metalog_geocoding` works without the geo stack. The geocoder classes
(`ReverseGeocoder`, `LandGeocoder`, `NaturalEarthLandGeocoder`, `SeaGeocoder`,
`LonghurstGeocoder`, `FallbackGeocoder`) are resolved lazily, and asking for
one without `[geo]` raises an error naming the extra. `[convert]` currently
costs nothing because geopandas 1.x already requires pyogrio; it is kept as the
record of what `download_data.py` needs.

For a development checkout:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # == pip install -e .[geo,convert,scripts]
```

Memory: loading the boundary layers takes about 660 MB RSS (roughly 1.2 GB with
the coastline index loaded, which the library does lazily and the service
eagerly). The polygons, not the Python wrappers, account for most of it.

## Boundary layers

Setting up `data/` takes four steps: download the sources by hand, convert them,
build the landmass names, and optionally pre-build the IHO pickle. Nothing is
downloaded automatically; some of the pages need a browser.

### 1. Download

| Download | From | Put in `data/` |
| --- | --- | --- |
| Natural Earth 10m *Admin 0 - Countries* and *Admin 1 - States, Provinces* (shapefile zips) | [naturalearthdata.com](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/) | extract both: `ne_10m_admin_0_countries.shp`, `ne_10m_admin_1_states_provinces.shp` (+ `.shx`, `.dbf`, `.prj`, `.cpg`) |
| IHO Sea Areas v3 | [marineregions.org/downloads.php](https://www.marineregions.org/downloads.php) | extract: `World_Seas_IHO_v3.shp` (+ sidecars) |
| Longhurst Provinces v4 | [marineregions.org/downloads.php](https://www.marineregions.org/downloads.php) | extract: `Longhurst_world_v4_2010.shp` (+ sidecars) |
| GSHHG 2.3.7 shapefiles, `gshhg-shp-2.3.7.zip` (~150 MB) | [soest.hawaii.edu/pwessel/gshhg](https://www.soest.hawaii.edu/pwessel/gshhg/) | extract into `data/gshhg/`, so that `data/gshhg/GSHHS_shp/f/GSHHS_f_L1.shp` exists |

For step 3 you also need, anywhere on disk: GeoNames `allCountries.zip` (~400 MB, 1.8 GB unpacked)
and `countryInfo.txt` from [download.geonames.org/export/dump](https://download.geonames.org/export/dump/),
and Natural Earth's 10m physical *Geography Regions* polygons
(`ne_10m_geography_regions_polys.shp`).

`python scripts/download_data.py` with nothing in place prints these
instructions and lists what is missing.

### 2. Convert

```bash
python scripts/download_data.py
```

converts whatever is present to GeoParquet and skips any parquet newer than its
source, so it is safe to run again. (A Natural Earth `.geojson` of the same name is
accepted in place of the shapefile.)

| File | Source | Size |
| --- | --- | --- |
| `ne_10m_admin_0_countries.parquet` | Natural Earth admin-0 (258 units) | ~7 MB |
| `ne_10m_admin_1_states_provinces.parquet` | Natural Earth admin-1 (4,596 units) | ~21 MB |
| `World_Seas_IHO_v3.parquet` | MarineRegions IHO Sea Areas v3 (101 polygons) | ~140 MB |
| `Longhurst_world_v4_2010.parquet` | MarineRegions Longhurst Provinces v4 (54 provinces) | ~3 MB |
| `GSHHS_f_L1.parquet` | GSHHG 2.3.7 full-resolution land (179,837 polygons) | ~98 MB |
| `GSHHS_f_L5.parquet` | GSHHG Antarctic ice front | ~2.5 MB |
| `GSHHS_f_L2.parquet` | GSHHG lakes; read only by step 3 | ~9 MB |

### 3. Build the landmass names

`GSHHS_f_names.parquet` (~5 MB) names each GSHHG landmass and gives its country,
so that `coast_landmass_name` and the nearest coast's country can be reported. It
is required: without it the data directory check fails. It is built from the
step 2 output (GSHHG L1, L2, L5 and both Natural Earth layers), so run it after
that:

```bash
python scripts/build_landmass_names.py \
    --geonames path/to/allCountries.zip \
    --country-info path/to/countryInfo.txt \
    --ne-regions path/to/ne_10m_geography_regions_polys.shp \
    --islands-cache path/to/geonames_islands.parquet   # optional
```

Reading `allCountries.zip` takes about 3 minutes; `--islands-cache` keeps the
extracted island features so that a rebuild takes under a minute. GeoNames is not
needed at run time. See [methods.md](methods.md#distance-to-coast).

Once the parquet files exist, the shapefiles, `data/gshhg/`, the GeoNames dump and
`GSHHS_f_L2.parquet` are no longer needed at run time; `download_data.py` treats a
layer whose parquet exists as present.

### 4. The IHO pickle (optional)

On first load the sea geocoder writes `data/World_Seas_IHO_v3.pkl` (~150 MB),
which loads faster and with a lower memory peak than the parquet. It is used
only when newer than the parquet, and any failure to read or write it falls
back to the parquet silently. The service mounts `data/` read-only and so never
writes it. To have it there, load the sea layer once with write access before
starting the service:

```bash
python -c "from metalog_geocoding import SeaGeocoder; SeaGeocoder('data', 'cache')"
```

The pickle is tied to the geopandas/pandas/shapely versions that wrote it. If the
service starts slowly after an image upgrade, delete it and build it again.

### Replacing a layer

The result caches record answers, not the data version they came from. After
replacing any layer (a new Natural Earth release, a rebuilt
`GSHHS_f_names.parquet`), delete `cache/*.sqlite*` *and* any old
`cache/*_cache.tsv` (otherwise the TSV is imported again). Re-running
`download_data.py` writes a parquet newer than the pickle, so the pickle is
ignored until it is rebuilt (step 4).

### Map layers (optional)

The simplified GeoJSON map layers are built from the same `data/` by
`scripts/build_geojson.py`; see [map-layers.md](map-layers.md).

## Where the data directory is found

First hit wins:

1. an explicit path (`--data-dir`, or the `data_dir` argument to `ReverseGeocoder`),
2. `$METALOG_GEOCODING_DATA`,
3. `./data`.

```python
from metalog_geocoding.paths import data_dir
data_dir(require=True)   # raises DataDirectoryNotFound naming any missing layer
```

`require=True` checks every layer in `paths.REQUIRED_LAYERS` up front, so a
missing mount fails at startup rather than on the first coastal sample. The
layers are not part of the package or of the service image; mount them as a
volume.

## Result caches

`cache/land_cache.sqlite` and `cache/sea_cache.sqlite` are described in
[methods.md](methods.md#caching). The sea-to-ocean chains for all 101 IHO seas ship
with the package, so the pipeline needs no network, even with an empty cache directory.
