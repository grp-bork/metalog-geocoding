# metalog-geocoding

An offline reverse geocoder. Given `(latitude, longitude)` it returns either a
**land** location (continent, country, first-level administrative region;
[Natural Earth](https://www.naturalearthdata.com/)) or an **ocean/sea**
(IHO Sea Areas plus the Longhurst biogeochemical province;
[MarineRegions](https://www.marineregions.org/)), falls back to the nearest
polygon for near-shore points, and measures the signed **distance to the
coast** ([GSHHG](https://www.soest.hawaii.edu/pwessel/gshhg/)). Point-in-polygon
lookups need no network access.

Developed for the [Metalog](https://metalog.embl.de) metagenomics database to
annotate sample coordinates; every data source
was chosen so that the derived annotations can be redistributed.

## Features

- Land: geographic and political continent, country, ISO code, admin-1 region
- Sea: ocean chain, sea name, MarineRegions MRGID, Longhurst province
- Shoreline fallback (nearest land or sea within 10 km; 50 km south of 60°S for land)
- Signed distance to coast, with the nearest landmass's name, area and country
- Results cached in SQLite, keyed on the exact coordinate
- Optional validation of coordinates against a stated place name
- Optional HTTP service (Flask) with a browser form
- Simplified GeoJSON map layers for every level of the hierarchy

## Data sources and licences

| Source | Used for | Licence |
| --- | --- | --- |
| [Natural Earth](https://www.naturalearthdata.com/) 10m admin-0 and admin-1 | continent, country, admin-1 | Public domain |
| [MarineRegions](https://www.marineregions.org/) IHO Sea Areas v3 (DOI 10.14284/323) | sea and ocean | CC-BY 4.0 |
| [MarineRegions](https://www.marineregions.org/) Longhurst Provinces v4 (2010) | biogeochemical province | CC-BY 4.0 |
| [GSHHG](https://www.soest.hawaii.edu/pwessel/gshhg/) 2.3.7, full resolution, L1/L2/L5 | distance to coast | LGPL v3 |
| [GeoNames](https://www.geonames.org/) (build time only) | landmass names, islet countries | CC-BY 4.0 |

Notes:

- MarineRegions confirmed that all of its products are CC-BY 4.0. The
  CC-BY-NC-SA shown on some older DOI records is out of date.
- GSHHG is used only to compute distances; **only the derived distances are
  output, never GSHHG geometry**. The map-layer builder therefore does not
  produce any layer from it.
- Attribution is required for the CC-BY sources: credit MarineRegions wherever
  sea, ocean or Longhurst values are shown, and geonames.org wherever a
  landmass name or a GeoNames-derived coast country is shown.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # == pip install -e '.[geo,scripts]'
# download the eight data files into downloads/ (see the link below), then:
python scripts/setup_data.py           # writes data/; ends with "Ready."
rm -r downloads                        # no longer needed
```

The downloads (Natural Earth, MarineRegions, GSHHG, GeoNames; MarineRegions
needs a browser) and the exact path each must have are listed in
[docs/installation-and-data.md](docs/installation-and-data.md). Running
`setup_data.py` before downloading anything prints the same list.

## Quick start

Two scripts, one per question. Both take a TSV with a header; examples with
checked-in outputs are in [examples/](examples/README.md).

```bash
# What is at this latitude/longitude?  (best | land | sea | both)
python scripts/geocode.py examples/geocode_input.tsv out.tsv --type best

# Does this latitude/longitude agree with the stated location name?
python scripts/check.py examples/check_simple_input.tsv out_prefix   # -> out_prefix_verdicts.tsv, out_prefix_hierarchy.tsv
```

Python (coordinate order is `(lat, lon)` throughout):

```python
from pathlib import Path
from metalog_geocoding import ReverseGeocoder

geocoder = ReverseGeocoder(data_dir=Path("data"), cache_dir=Path("cache"))
geocoder.geocode(lat=62.03, lon=129.73, location_type="land")   # Sakha (Yakutia), Russia
geocoder.geocode(lat=-40.55, lon=142.50, location_type="sea")   # Great Australian Bight
geocoder.save_caches()
```

Service (browser form at `/`, JSON API):

```bash
pip install -e '.[geo,service]'
python service/app.py                    # http://localhost:8007
```

## Documentation

| Document | Contents |
| --- | --- |
| [docs/installation-and-data.md](docs/installation-and-data.md) | Which install you need, the data downloads and where each goes, the setup script |
| [docs/usage.md](docs/usage.md) | Batch CLI, Python API, output fields |
| [docs/methods.md](docs/methods.md) | Land lookup and fallback ladder, continents, country names, sea lookup and hierarchy, Longhurst, distance to coast, caching |
| [docs/location-hierarchy.md](docs/location-hierarchy.md) | The long-format `location_hierarchy` table: schemes, levels, SQL |
| [docs/validation.md](docs/validation.md) | Grading a coordinate against a stated place: grades, codes, claim distances, sea relatedness, exceptions file |
| [docs/api.md](docs/api.md) | HTTP API reference of the service |
| [service/README.md](service/README.md) | Running and deploying the service |
| [docs/map-layers.md](docs/map-layers.md) | Building GeoJSON map layers |
| [docs/development.md](docs/development.md) | Package layout, tests, CI, conventions and gotchas |

## Citation and attribution

If you use the output, cite the underlying data:

- Natural Earth (public domain; attribution appreciated).
- Flanders Marine Institute, IHO Sea Areas v3 and Longhurst Provinces v4,
  via [MarineRegions](https://www.marineregions.org/) (CC-BY 4.0); Longhurst,
  *Ecological Geography of the Sea*.
- Wessel, P. and Smith, W. H. F. (1996). A global, self-consistent, hierarchical,
  high-resolution shoreline database. *J. Geophys. Res.* 101(B4):8741-8743,
  [doi:10.1029/96JB00104](https://doi.org/10.1029/96JB00104) (wherever distances to coast are shown).
- GeoNames, [geonames.org](https://www.geonames.org/) (CC-BY 4.0), for landmass names.

## License

The code is released under the MIT License (see `LICENSE`). The data layers it
reads and the annotations derived from them keep their own licences, listed
above; check them before redistributing either.
