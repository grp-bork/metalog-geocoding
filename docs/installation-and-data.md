# Installation and data

Three steps: install the package, download eight files into `downloads/`, and
run `python scripts/setup_data.py`. It writes what the geocoder needs into
`data/`. When it prints `Ready.`, the geocoder, the scripts and the service all
work, and `downloads/` can be deleted.

Two directories, so the downloads never mix with what the geocoder loads:

| Directory | Who writes it | What's in it | Keep it? |
| --- | --- | --- | --- |
| `downloads/` | you, by hand | the eight downloads, about 1 GB unpacked | no; delete it after `Ready.` |
| `data/` | `setup_data.py` | eight files, ~415 MB, loaded by the geocoder | yes; this is what the service mounts |

## 1. Install

Python >= 3.11. From a checkout:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # == pip install -e '.[geo,scripts]'
```

That is the install for running the geocoder on your own machine, and the one
the rest of this page assumes. Install something else only in these cases:

| You want to | Install |
| --- | --- |
| Geocode or check coordinates here (scripts or Python) | `pip install -r requirements.txt` |
| Also run the HTTP service | `pip install -e '.[geo,scripts,service]'`, or use the Docker image ([service/README.md](../service/README.md)) |
| Also run the tests | add `test`: `pip install -e '.[geo,scripts,test]'` |
| Only send coordinates to a service someone else runs | `pip install .` (no extras; no data needed) |

The extras exist because the geometry stack is large. Computing anything needs
geopandas, shapely, pyproj, pandas, numpy and pyarrow (~353 MB installed), but
a program that only calls the service (`metalog_geocoding.client`) or reads a
hierarchy table back needs none of it. So the base package is pure Python plus
`requests`, and everything else is opt-in:

| Extra | Adds | Needed for |
| --- | --- | --- |
| `geo` | geopandas, shapely, pyproj, pandas, numpy, pyarrow | any lookup, and `setup_data.py` |
| `scripts` | tqdm | progress bars in `scripts/geocode.py` |
| `service` | flask, gunicorn | `service/app.py` |
| `test` | pytest | the test suite |

Without `geo`, `import metalog_geocoding` still works; asking for a geocoder
class (`ReverseGeocoder`, `SeaGeocoder`, ...) raises an error that says to
install `[geo]`.

Memory: the loaded layers take about 660 MB RSS, or about 1.2 GB once the
coastline index is loaded (the library loads it on the first coastal lookup,
the service at startup).

## 2. Download the data

Nothing is downloaded automatically, and the MarineRegions files need a
browser (there is a short form to fill in). Put everything in `downloads/` in
the repository (or anywhere else, and pass `--downloads-dir`).

| # | Download | From | It must end up at |
| --- | --- | --- | --- |
| 1 | Natural Earth 10m *Admin 0 – Countries*, shapefile zip | [Natural Earth, cultural vectors](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/) | `downloads/ne_10m_admin_0_countries.shp` |
| 2 | Natural Earth 10m *Admin 1 – States, Provinces*, shapefile zip | same page | `downloads/ne_10m_admin_1_states_provinces.shp` |
| 3 | Natural Earth 10m *Geography Regions* polygons (under *Labels*) | [Natural Earth, physical vectors](https://www.naturalearthdata.com/downloads/10m-physical-vectors/) | `downloads/ne_10m_geography_regions_polys.shp` |
| 3a | Natural Earth 10m *Marine Areas* polygons (under *Labels*), optional | same page | `downloads/ne_10m_geography_marine_polys.shp` |
| 4 | MarineRegions *IHO Sea Areas*, version 3, shapefile | [marineregions.org/downloads.php](https://www.marineregions.org/downloads.php) | `downloads/World_Seas_IHO_v3.shp` |
| 5 | MarineRegions *Longhurst Provinces*, version 4 | same page | `downloads/Longhurst_world_v4_2010.shp` |
| 6 | GSHHG 2.3.7 shapefiles, `gshhg-shp-2.3.7.zip` (~150 MB) | [soest.hawaii.edu/pwessel/gshhg](https://www.soest.hawaii.edu/pwessel/gshhg/) | `downloads/gshhg/GSHHS_shp/f/GSHHS_f_L1.shp` |
| 7 | GeoNames `allCountries.zip` (~400 MB) | [download.geonames.org/export/dump](https://download.geonames.org/export/dump/) | `downloads/geonames/allCountries.zip` (leave it zipped) |
| 8 | GeoNames `countryInfo.txt` | same page | `downloads/geonames/countryInfo.txt` |

Files 1–5 are zips: extract each straight into `downloads/`, keeping the files
that come with the `.shp` (`.shx`, `.dbf`, `.prj`, `.cpg`). Extract the GSHHG
zip into `downloads/gshhg/` instead, so that its `GSHHS_shp/` folder lands
there. When you are done, `downloads/` looks like this:

```
downloads/
├── ne_10m_admin_0_countries.shp          (+ .shx .dbf .prj .cpg)
├── ne_10m_admin_1_states_provinces.shp   (+ .shx .dbf .prj .cpg)
├── ne_10m_geography_regions_polys.shp    (+ .shx .dbf .prj .cpg)
├── World_Seas_IHO_v3.shp                 (+ .shx .dbf .prj)
├── Longhurst_world_v4_2010.shp           (+ .shx .dbf .prj)
├── gshhg/
│   └── GSHHS_shp/f/GSHHS_f_L1.shp ...    (the script reads L1, L2 and L5)
└── geonames/
    ├── allCountries.zip
    └── countryInfo.txt
```

What each is for: Natural Earth admin-0/1 gives the country and region on
land, IHO the sea, Longhurst the biogeochemical province, and GSHHG the
distance to the coast. Files 3, 7 and 8 are used once, by the setup script, to
name each GSHHG landmass (`coast_landmass_name`) and give it a country; they
are not read at run time. Licences are listed in the [README](../README.md#data-sources-and-licences).

## 3. Run the setup script

```bash
python scripts/setup_data.py
```

It reads the files above from `downloads/` and writes everything the geocoder
loads into `data/`. A first run takes a minute or two. To use other locations,
pass `--downloads-dir` and `--data-dir` (or set `$METALOG_GEOCODING_DATA`; see
[below](#where-the-data-directory-is-found)).

1. **Check the downloads.** It prints a checklist; for anything missing it
   says where to get it and the path it must have, then stops. Fix those and
   run it again.
2. **Convert to GeoParquet**, which loads much faster than shapefiles:

   | Reads from `downloads/` | Writes to `data/` |
   | --- | --- |
   | `ne_10m_admin_0_countries.shp` | `ne_10m_admin_0_countries.parquet` (~7 MB) |
   | `ne_10m_admin_1_states_provinces.shp` | `ne_10m_admin_1_states_provinces.parquet` (~21 MB) |
   | `World_Seas_IHO_v3.shp` | `World_Seas_IHO_v3.parquet` (~140 MB) |
   | `Longhurst_world_v4_2010.shp` | `Longhurst_world_v4_2010.parquet` (~3 MB) |
   | `gshhg/GSHHS_shp/f/GSHHS_f_L1.shp` | `GSHHS_f_L1.parquet` (~98 MB) |
   | `gshhg/GSHHS_shp/f/GSHHS_f_L5.shp` | `GSHHS_f_L5.parquet` (~2.5 MB) |
   | `ne_10m_geography_regions_polys.shp` | `ne_10m_geography_regions_polys.parquet` (~3 MB), optional: outlines of mountain ranges, deserts and plateaus for `POST /place-distance`; without it those places are Wikidata points |
   | `ne_10m_geography_marine_polys.shp` | `ne_10m_geography_marine_polys.parquet` (~1 MB), optional: outlines of bays, gulfs, straits and smaller seas for `POST /place-distance` |

3. **Name the landmasses.** Runs `scripts/build_landmass_names.py`, which reads
   the parquet files from step 2 plus, from `downloads/`,
   `ne_10m_geography_regions_polys.shp`, `geonames/` and the GSHHG lakes
   (`GSHHS_f_L2.shp`), and writes `data/GSHHS_f_names.parquet` (~5 MB). It
   also writes `downloads/geonames/geonames_islands.parquet`, so a rebuild
   skips the slow read of `allCountries.zip`.
4. **Write `data/World_Seas_IHO_v3.pkl`** (~150 MB), a copy of the IHO layer that
   loads faster and with less memory than the parquet. The geocoder would
   otherwise write it on first use, which it cannot do when `data/` is mounted
   read-only, as in the service.
5. **Check `data/`** and print `Ready.`

Every step skips outputs that are newer than their inputs, so running it again
is cheap; `--force` redoes steps 2 and 3. `--verify-only` runs only step 5.

A Natural Earth `.geojson` in place of the `.shp` (same name, other extension)
is accepted too.

### Cleaning up

Once it has printed `Ready.`, `data/` holds everything the geocoder reads:

```
data/
├── ne_10m_admin_0_countries.parquet
├── ne_10m_admin_1_states_provinces.parquet
├── World_Seas_IHO_v3.parquet
├── World_Seas_IHO_v3.pkl
├── Longhurst_world_v4_2010.parquet
├── GSHHS_f_L1.parquet
├── GSHHS_f_L5.parquet
├── GSHHS_f_names.parquet
├── ne_10m_geography_regions_polys.parquet   (optional)
└── ne_10m_geography_marine_polys.parquet    (optional)
```

Delete all of `downloads/`. Running `setup_data.py` again afterwards still
works: a download whose output is in `data/` counts as done. Keep
`downloads/` only if you expect to rebuild with a newer GeoNames dump or
Natural Earth release, since the landmass names are rebuilt from the
downloads, not from `data/`.

A `data/` directory set up before this layout may also hold shapefiles,
`gshhg/`, `geonames/` and `GSHHS_f_L2.parquet`. None of these is read at run
time any more; delete them.

## Updating a layer

Download the new file to its path in `downloads/` and run `setup_data.py`
again; it rebuilds whatever depends on it. Rebuilding the landmass names needs
`downloads/geonames/`, `downloads/gshhg/` and the Geography Regions shapefile
to be there too. Then delete the result caches,
`cache/*.sqlite*` *and* any `cache/*_cache.tsv` (otherwise the TSV is imported
again): they record answers, not the data version they came from.

The IHO pickle depends on the geopandas/pandas/shapely versions that wrote it.
If the service starts slowly after an upgrade, delete `World_Seas_IHO_v3.pkl`
and run `setup_data.py` again.

## Where the data directory is found

First hit wins:

1. an explicit path (`--data-dir`, or the `data_dir` argument to `ReverseGeocoder`),
2. `$METALOG_GEOCODING_DATA`,
3. `./data`, relative to the current directory.

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

## Map layers

The simplified GeoJSON map layers for the browser are a separate build from the
same `data/`, needed only if you want to draw maps; see
[map-layers.md](map-layers.md).
