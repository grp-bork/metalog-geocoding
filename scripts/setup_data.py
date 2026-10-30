#!/usr/bin/env python3
"""
Set up the data directory in one go.

Download the sources by hand into the downloads directory (``--downloads-dir``,
default downloads/), at the paths listed in docs/installation-and-data.md, then
run this script. It writes what the geocoder loads into the data directory
(``--data-dir``, else $METALOG_GEOCODING_DATA, else data/). It

1. checks that every source is in place, and lists what is missing and where
   to get it if not;
2. converts the shapefiles to GeoParquet;
3. builds GSHHS_f_names.parquet (scripts/build_landmass_names.py) from GSHHG,
   Natural Earth and GeoNames;
4. writes World_Seas_IHO_v3.pkl, the fast-loading copy of the IHO layer;
5. checks that every layer the geocoder needs is there.

Each step skips outputs newer than their inputs, so it is safe to run again.
Once it prints "Ready.", the downloads directory can be deleted.
"""

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Standalone runs import the package from the repo root; an installed
# package makes this a no-op.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Aliased: `data_dir` is a parameter name throughout this module.
from metalog_geocoding.paths import (
    REQUIRED_LAYERS,
    DataDirectoryNotFound,
    data_dir as resolve_data_dir,
)

SCRIPTS = Path(__file__).parent

GSHHG_SHP = Path("gshhg/GSHHS_shp/f")
NE_CULTURAL = "https://www.naturalearthdata.com/downloads/10m-cultural-vectors/"
NE_PHYSICAL = "https://www.naturalearthdata.com/downloads/10m-physical-vectors/"
MARINEREGIONS = "https://www.marineregions.org/downloads.php"
GEONAMES = "https://download.geonames.org/export/dump/"


@dataclass
class Source:
    """One download: where it comes from, where it goes, what is made from it."""

    name: str
    url: str
    how: str
    #: The file this script reads, relative to the downloads directory.
    path: Path
    #: The file(s) made from it, in the data directory. Once all of them
    #: exist the download may be deleted, so it counts as present.
    outputs: tuple[str, ...]
    #: A missing optional download is reported but does not stop the setup.
    optional: bool = False


SOURCES = [
    Source(
        "Natural Earth 10m Admin 0 - Countries",
        NE_CULTURAL,
        "Download the 'Admin 0 - Countries' shapefile zip and extract it into DOWNLOADS/.",
        Path("ne_10m_admin_0_countries.shp"),
        ("ne_10m_admin_0_countries.parquet",),
    ),
    Source(
        "Natural Earth 10m Admin 1 - States, Provinces",
        NE_CULTURAL,
        "Download the 'Admin 1 - States, Provinces' shapefile zip and extract it into DOWNLOADS/.",
        Path("ne_10m_admin_1_states_provinces.shp"),
        ("ne_10m_admin_1_states_provinces.parquet",),
    ),
    Source(
        "Natural Earth 10m Geography Regions (physical)",
        NE_PHYSICAL,
        "Download the 'Geography Regions' polygons zip (Physical vectors, Labels) "
        "and extract it into DOWNLOADS/.",
        Path("ne_10m_geography_regions_polys.shp"),
        ("GSHHS_f_names.parquet", "ne_10m_geography_regions_polys.parquet"),
    ),
    Source(
        "Natural Earth 10m Marine Areas (physical, optional)",
        NE_PHYSICAL,
        "Download the 'Marine Areas' polygons zip (Physical vectors, Labels) "
        "and extract it into DOWNLOADS/.",
        Path("ne_10m_geography_marine_polys.shp"),
        ("ne_10m_geography_marine_polys.parquet",),
        optional=True,
    ),
    Source(
        "MarineRegions IHO Sea Areas v3",
        MARINEREGIONS,
        "Find 'IHO Sea Areas' (version 3), fill in the download form, choose the "
        "shapefile, and extract the zip into DOWNLOADS/.",
        Path("World_Seas_IHO_v3.shp"),
        ("World_Seas_IHO_v3.parquet",),
    ),
    Source(
        "MarineRegions Longhurst Provinces v4",
        MARINEREGIONS,
        "Find 'Longhurst Provinces', fill in the download form, and extract the zip into DOWNLOADS/.",
        Path("Longhurst_world_v4_2010.shp"),
        ("Longhurst_world_v4_2010.parquet",),
    ),
    Source(
        "GSHHG 2.3.7 shorelines (gshhg-shp-2.3.7.zip, ~150 MB)",
        "https://www.soest.hawaii.edu/pwessel/gshhg/",
        "Download gshhg-shp-2.3.7.zip and extract it into DOWNLOADS/gshhg/.",
        GSHHG_SHP / "GSHHS_f_L1.shp",
        ("GSHHS_f_L1.parquet", "GSHHS_f_L5.parquet", "GSHHS_f_names.parquet"),
    ),
    Source(
        "GeoNames allCountries.zip (~400 MB)",
        GEONAMES,
        "Download allCountries.zip into DOWNLOADS/geonames/ (do not unzip it).",
        Path("geonames/allCountries.zip"),
        ("GSHHS_f_names.parquet",),
    ),
    Source(
        "GeoNames countryInfo.txt",
        GEONAMES,
        "Download countryInfo.txt into DOWNLOADS/geonames/.",
        Path("geonames/countryInfo.txt"),
        ("GSHHS_f_names.parquet",),
    ),
]

#: Shapefile (relative to the downloads directory) -> the parquet made from it
#: in the data directory. GSHHG L2, the lakes, is read only by the names build.
CONVERSIONS = {
    Path("ne_10m_admin_0_countries.shp"): "ne_10m_admin_0_countries.parquet",
    Path("ne_10m_admin_1_states_provinces.shp"): "ne_10m_admin_1_states_provinces.parquet",
    Path("World_Seas_IHO_v3.shp"): "World_Seas_IHO_v3.parquet",
    Path("Longhurst_world_v4_2010.shp"): "Longhurst_world_v4_2010.parquet",
    # Optional: outlines of ranges, deserts, plateaus for /place-distance.
    Path("ne_10m_geography_regions_polys.shp"): "ne_10m_geography_regions_polys.parquet",
    Path("ne_10m_geography_marine_polys.shp"): "ne_10m_geography_marine_polys.parquet",
    **{GSHHG_SHP / f"GSHHS_f_L{level}.shp": f"GSHHS_f_L{level}.parquet" for level in (1, 5)},
}

#: The data-directory layers GSHHS_f_names.parquet is built from...
NAMES_INPUTS = (
    "GSHHS_f_L1.parquet", "GSHHS_f_L5.parquet",
    "ne_10m_admin_0_countries.parquet", "ne_10m_admin_1_states_provinces.parquet",
)

#: ...and the downloads it reads directly (build_landmass_names.py's defaults).
NAMES_DOWNLOADS = (
    Path("geonames/allCountries.zip"),
    Path("geonames/countryInfo.txt"),
    Path("ne_10m_geography_regions_polys.shp"),
    GSHHG_SHP / "GSHHS_f_L2.shp",
)


def source_file(downloads: Path, rel: Path) -> Path:
    """The file as found: a Natural Earth .geojson stands in for the .shp."""
    shp = downloads / rel
    geojson = shp.with_suffix(".geojson")
    return geojson if rel.name.startswith("ne_") and geojson.exists() and not shp.exists() else shp


def is_newer(output: Path, *inputs: Path) -> bool:
    """True if `output` exists and is at least as new as every existing input."""
    if not output.exists():
        return False
    return all(output.stat().st_mtime >= i.stat().st_mtime for i in inputs if i.exists())


def check_sources(downloads: Path, data_dir: Path) -> bool:
    """Print a checklist of the downloads; return True if nothing is missing."""
    print(f"Downloads directory: {downloads.absolute()}\n")
    missing = []
    for src in SOURCES:
        found = source_file(downloads, src.path)
        if found.exists():
            status = "found" if found.name == src.path.name else f"found {found.name}"
        elif all((data_dir / out).exists() for out in src.outputs):
            status = "not needed (already converted)"
        elif src.optional:
            status = "optional, not downloaded"
        else:
            status = "MISSING"
            missing.append(src)
        print(f"  [{'x' if status != 'MISSING' else ' '}] {src.path}  -- {status}")

    if not missing:
        return True

    print(f"\n{len(missing)} download(s) missing. DOWNLOADS below is {downloads.absolute()}\n")
    for src in missing:
        print(f"* {src.name}")
        print(f"    from: {src.url}")
        print(f"    how:  {src.how}")
        print(f"    then: DOWNLOADS/{src.path} must exist\n")
    print("Then run this script again.")
    return False


def convert(downloads: Path, data_dir: Path, force: bool) -> None:
    """Convert every shapefile whose parquet is missing or older than it."""
    import geopandas as gpd

    for rel, out_name in CONVERSIONS.items():
        src, out = source_file(downloads, rel), data_dir / out_name
        if not src.exists():
            # check_sources established the parquet is there, or it is optional
            state = "up to date (download deleted)" if out.exists() else "skipped (optional, not downloaded)"
            print(f"  {out_name}: {state}")
            continue
        if not force and is_newer(out, src):
            print(f"  {out_name}: up to date")
            continue
        print(f"  {out_name}: converting {src.relative_to(downloads)} ...", end=" ", flush=True)
        gdf = gpd.read_file(src)
        gdf.to_parquet(out)
        print(f"{len(gdf):,} features")


def build_names(downloads: Path, data_dir: Path, force: bool) -> None:
    """Run build_landmass_names.py if its output is missing or stale."""
    out = data_dir / "GSHHS_f_names.parquet"
    inputs = [data_dir / name for name in NAMES_INPUTS]
    inputs += [downloads / rel for rel in NAMES_DOWNLOADS]
    if not force and is_newer(out, *inputs):
        print(f"  {out.name}: up to date")
        return
    missing = [downloads / rel for rel in NAMES_DOWNLOADS if not (downloads / rel).exists()]
    if missing:
        # Only reachable with an existing names file whose downloads were deleted.
        print(f"  {out.name}: older than the layers it was built from, but keeping it,")
        print("  since rebuilding it needs " + ", ".join(str(p) for p in missing))
        return
    print(f"  {out.name}: building ...", flush=True)
    subprocess.run(
        [sys.executable, str(SCRIPTS / "build_landmass_names.py"),
         "--downloads-dir", str(downloads), "--data-dir", str(data_dir)],
        check=True,
    )


def build_iho_pickle(data_dir: Path) -> None:
    """Load the sea layer once, which writes World_Seas_IHO_v3.pkl beside the
    parquet if it is missing or older (see SeaGeocoder._load_iho)."""
    pkl = data_dir / "World_Seas_IHO_v3.pkl"
    if is_newer(pkl, data_dir / "World_Seas_IHO_v3.parquet"):
        print(f"  {pkl.name}: up to date")
        return
    print(f"  {pkl.name}: writing ...", flush=True)
    from metalog_geocoding import SeaGeocoder

    # The hierarchy cache it opens is not wanted here.
    with tempfile.TemporaryDirectory() as cache:
        SeaGeocoder(data_dir, Path(cache))
    if not pkl.exists():
        print(f"  could not write {pkl} (read-only?); the parquet is used instead")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--downloads-dir",
        type=Path,
        default=Path("downloads"),
        help="Directory holding the downloaded sources (default: downloads/)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory the geocoder loads its layers from, written here "
             "(default: $METALOG_GEOCODING_DATA, else data/)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only check that the finished layers are there; change nothing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild every parquet layer and the landmass names even if up to date",
    )
    args = parser.parse_args()
    # Resolved rather than defaulting to Path("data"), so
    # $METALOG_GEOCODING_DATA is honoured.
    data_dir = resolve_data_dir(args.data_dir)
    downloads = args.downloads_dir

    if not args.verify_only:
        data_dir.mkdir(parents=True, exist_ok=True)
        print("== 1. Downloads")
        if not check_sources(downloads, data_dir):
            sys.exit(1)
        print(f"\n== 2. Converting to GeoParquet, into {data_dir.absolute()}")
        convert(downloads, data_dir, args.force)
        print("\n== 3. Landmass names")
        build_names(downloads, data_dir, args.force)
        print("\n== 4. IHO pickle")
        build_iho_pickle(data_dir)

    print("\n== Checking the finished data directory")
    try:
        resolve_data_dir(data_dir, require=True)
    except DataDirectoryNotFound as e:
        print(e)
        sys.exit(1)
    for name in REQUIRED_LAYERS:
        print(f"  [x] {name}")
    print(f"\nReady. {data_dir.absolute()} has every layer the geocoder needs.")
    if not args.verify_only and downloads.exists():
        print(f"{downloads.absolute()} is no longer needed and can be deleted.")


if __name__ == "__main__":
    main()
