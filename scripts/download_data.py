#!/usr/bin/env python3
"""
Set up the boundary layers: print download instructions for whatever is
missing, and convert what is present to GeoParquet.

1. Natural Earth admin-0 and admin-1 (public domain)
2. MarineRegions IHO Sea Areas v3 (CC-BY 4.0)
3. MarineRegions Longhurst Provinces v4 (CC-BY 4.0)
4. GSHHG 2.3.7 full-resolution shorelines (LGPL v3; publish derived distances
   only, never the geometry)

Files go in the data directory (``--data-dir``, else $METALOG_GEOCODING_DATA,
else data/). Run it again after downloading; it is idempotent.
"""

import argparse
import sys
from pathlib import Path

# Standalone runs import the package from the repo root; an installed
# package makes this a no-op.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Aliased: `data_dir` is a parameter name throughout this module.
from metalog_geocoding.paths import REQUIRED_LAYERS, data_dir as resolve_data_dir

NE_STEMS = ("ne_10m_admin_0_countries", "ne_10m_admin_1_states_provinces")


def ne_source(data_dir: Path, stem: str) -> Path:
    """The Natural Earth source file: the shapefile as shipped, else a GeoJSON."""
    shp = data_dir / f"{stem}.shp"
    geojson = data_dir / f"{stem}.geojson"
    return geojson if geojson.exists() and not shp.exists() else shp


def show_marineregions_instructions(data_dir: Path):
    """Show instructions for downloading IHO Sea Areas from MarineRegions."""
    print("\n" + "=" * 70)
    print("2. MARINEREGIONS IHO SEA AREAS (Required)")
    print("=" * 70)
    print("\nRequired: Ocean and sea boundaries with MRGID codes")
    print("Size: ~100 MB")
    print("Purpose: Identifies which ocean/sea a coordinate is in")
    print("\nMANUAL DOWNLOAD STEPS:")
    print("\n  1. Go to: https://www.marineregions.org/downloads.php")
    print("\n  2. Scroll down to find 'IHO Sea Areas'")
    print("     (Look for 'World Seas - IHO Sea Areas, version 3')")
    print("\n  3. Click the download button/link")
    print("     File: World_Seas_IHO_v3.zip or similar")
    print("\n  4. You may need to accept terms of use")
    print(f"\n  5. Extract the ZIP file to: {data_dir.absolute()}")
    print("\n  You should see these files after extraction:")
    print("     - World_Seas_IHO_v3.shp")
    print("     - World_Seas_IHO_v3.shx")
    print("     - World_Seas_IHO_v3.dbf")
    print("     - World_Seas_IHO_v3.prj")
    print("\nALTERNATIVE:")
    print("  If the above link doesn't work, try:")
    print("  https://marineregions.org/sources.php")
    print("  and search for 'IHO Sea Areas'")


def show_longhurst_instructions(data_dir: Path):
    """Show instructions for downloading Longhurst Provinces from MarineRegions."""
    print("\n" + "=" * 70)
    print("3. MARINEREGIONS LONGHURST PROVINCES (Required)")
    print("=" * 70)
    print("\nRequired: biogeochemical provinces, the ecological axis for marine samples")
    print("Size: ~3 MB")
    print("Purpose: assigns each marine coordinate one of 54 Longhurst provinces")
    print("\nMANUAL DOWNLOAD STEPS:")
    print("\n  1. Go to: https://www.marineregions.org/downloads.php")
    print("\n  2. Find 'Longhurst Provinces'")
    print("     (Look for 'Longhurst Biogeochemical Provinces, version 4')")
    print("\n  3. Click the download button/link")
    print(f"\n  4. Extract the ZIP file to: {data_dir.absolute()}")
    print("\n  Only these four are needed (.sbn/.sbx are an ESRI index GDAL ignores,")
    print("  .shp.xml is metadata kept for provenance):")
    print("     - Longhurst_world_v4_2010.shp")
    print("     - Longhurst_world_v4_2010.shx")
    print("     - Longhurst_world_v4_2010.dbf")
    print("     - Longhurst_world_v4_2010.prj")


def show_gshhg_instructions(data_dir: Path):
    """Show instructions for downloading the GSHHG shorelines."""
    print("\n" + "=" * 70)
    print("4. GSHHG 2.3.7 SHORELINES (Required)")
    print("=" * 70)
    print("\nRequired: full-resolution coastline, for distance to coast/continent")
    print("Size: ~150 MB zip; ~110 MB of parquet after conversion")
    print("Licence: LGPL v3. Publish only the derived distances, never the")
    print("         geometry -- do not build map layers from it.")
    print("\nDOWNLOAD STEPS:")
    print("\n  1. Download gshhg-shp-2.3.7.zip from:")
    print("     https://www.soest.hawaii.edu/pwessel/gshhg/")
    print(f"\n  2. Extract it into: {(data_dir / 'gshhg').absolute()}")
    print("     Only GSHHS_shp/f/GSHHS_f_L1, _L2 and _L5 are used (L5 is the")
    print("     Antarctic ice front; L6, the grounding line, is not).")
    print("\n  3. This script converts them to GeoParquet automatically")
    print("\n  4. Then build the landmass names (GSHHS_f_names.parquet) once:")
    print("     download allCountries.zip + countryInfo.txt from")
    print("     https://download.geonames.org/export/dump/ (CC-BY 4.0) and")
    print("     ne_10m_geography_regions_polys from naturalearthdata.com, then run")
    print("     python scripts/build_landmass_names.py --geonames ... --country-info ... --ne-regions ...")


def show_naturalearth_instructions(data_dir: Path):
    """Show instructions for downloading Natural Earth admin boundaries."""
    print("\n" + "=" * 70)
    print("1. NATURAL EARTH ADMIN BOUNDARIES (Required)")
    print("=" * 70)
    print("\nRequired: land boundaries - continent, country and admin-1")
    print("Size: ~20 MB zipped")
    print("Purpose: continent + country + admin-1 for all land lookups")
    print("Licence: public domain, so the derived annotations are redistributable")
    print("\nMANUAL DOWNLOAD STEPS:")
    print("\n  1. Download the 10m cultural vectors from:")
    print("     https://www.naturalearthdata.com/downloads/10m-cultural-vectors/")
    print("       - Admin 0 - Countries")
    print("       - Admin 1 - States, Provinces")
    print(f"\n  2. Extract both zips into: {data_dir.absolute()}")
    print("     Expected file names (a .geojson of the same name also works):")
    print("       - ne_10m_admin_0_countries.shp (+ .shx, .dbf, .prj, .cpg)")
    print("       - ne_10m_admin_1_states_provinces.shp (+ .shx, .dbf, .prj, .cpg)")
    print("\n  3. This script converts them to GeoParquet automatically")


def convert_to_parquet(data_dir: Path):
    """Convert downloaded GeoJSON/shapefiles to GeoParquet for faster loading."""
    print("\n" + "=" * 70)
    print("CONVERTING TO GEOPARQUET FORMAT")
    print("=" * 70)
    print("\nConverting geographic data to GeoParquet for 3x faster startup...")

    try:
        import geopandas as gpd
    except ImportError:
        print("\n✗ ERROR: geopandas not installed")
        print("  Install with: pip install geopandas")
        return False

    conversions = []

    # Find IHO Sea Areas shapefile
    print("2. Looking for IHO Sea Areas shapefile...")
    iho_shp = data_dir / "World_Seas_IHO_v3.shp"
    if iho_shp.exists():
        iho_parquet = data_dir / "World_Seas_IHO_v3.parquet"
        conversions.append(("IHO Sea Areas", iho_shp, iho_parquet))
    else:
        print("  ✗ No IHO Sea Areas shapefile found")

    # Find Longhurst Provinces shapefile
    print("3. Looking for Longhurst Provinces shapefile...")
    longhurst_shp = data_dir / "Longhurst_world_v4_2010.shp"
    if longhurst_shp.exists():
        conversions.append(
            (
                "Longhurst Provinces",
                longhurst_shp,
                data_dir / "Longhurst_world_v4_2010.parquet",
            )
        )
    else:
        print("  \u2717 No Longhurst Provinces shapefile found")

    # Find Natural Earth shapefiles (or GeoJSON)
    print("4. Looking for Natural Earth files...")
    for ne_stem, ne_label in zip(
        NE_STEMS,
        ["Natural Earth countries (admin-0)", "Natural Earth states/provinces (admin-1)"],
    ):
        ne_file = ne_source(data_dir, ne_stem)
        if ne_file.exists():
            conversions.append((ne_label, ne_file, data_dir / f"{ne_stem}.parquet"))
        else:
            print(f"  ✗ No {ne_stem}.shp or .geojson found")

    # Find GSHHG full-resolution shapefiles (extracted under gshhg/, converted
    # into the data directory itself beside the other layers).
    print("5. Looking for GSHHG shapefiles...")
    for level in (1, 2, 5):
        gshhg_shp = data_dir / "gshhg" / "GSHHS_shp" / "f" / f"GSHHS_f_L{level}.shp"
        if gshhg_shp.exists():
            conversions.append(
                (f"GSHHG L{level}", gshhg_shp, data_dir / f"GSHHS_f_L{level}.parquet")
            )
        else:
            print(f"  ✗ No {gshhg_shp.relative_to(data_dir)} found")

    if not conversions:
        print("\n⚠ No shapefiles found to convert")
        return False

    # Perform conversions
    print(f"\nConverting {len(conversions)} file(s)...\n")
    success_count = 0

    for name, shp_path, parquet_path in conversions:
        print(f"  Converting {name}...")
        print(f"    From: {shp_path.name}")
        print(f"    To:   {parquet_path.name}")

        try:
            # Check if parquet already exists and is newer
            if parquet_path.exists():
                shp_mtime = shp_path.stat().st_mtime
                parquet_mtime = parquet_path.stat().st_mtime
                if parquet_mtime >= shp_mtime:
                    print(f"    ✓ Already up-to-date (skipping)")
                    success_count += 1
                    continue

            # Read shapefile and convert to parquet
            print(f"    Reading shapefile...", end=" ", flush=True)
            gdf = gpd.read_file(shp_path)
            print(f"({len(gdf)} features)")

            print(f"    Writing parquet...", end=" ", flush=True)
            gdf.to_parquet(parquet_path)

            # Get file sizes for comparison
            shp_size = shp_path.stat().st_size / (1024 * 1024)  # MB
            parquet_size = parquet_path.stat().st_size / (1024 * 1024)  # MB

            print(f"Done!")
            print(
                f"    Size: {shp_size:.1f} MB → {parquet_size:.1f} MB "
                f"({parquet_size/shp_size*100:.0f}% of original)"
            )
            print(f"    ✓ Success\n")
            success_count += 1

        except Exception as e:
            print(f"\n    ✗ ERROR: {e}\n")

    print("=" * 70)
    if success_count == len(conversions):
        print(f"✓ Successfully converted {success_count}/{len(conversions)} file(s)")
        print("  Startup time should now be 15-30x faster!")
    else:
        print(f"⚠ Converted {success_count}/{len(conversions)} file(s)")
    print("=" * 70)

    return success_count > 0


def check_parquet_files(data_dir: Path):
    """Check if parquet files exist. Returns True if all exist."""
    return all((data_dir / name).exists() for name in REQUIRED_LAYERS)


def verify_files(data_dir: Path, check_parquet: bool = False):
    """Verify that required files are present."""
    print("\n" + "=" * 70)
    print("FILE VERIFICATION")
    print("=" * 70)

    required_files = {
        "Natural Earth admin-0 countries": ne_source(data_dir, NE_STEMS[0]),
        "Natural Earth admin-1 states/provinces": ne_source(data_dir, NE_STEMS[1]),
        "IHO Sea Areas": data_dir / "World_Seas_IHO_v3.shp",
        "Longhurst Provinces": data_dir / "Longhurst_world_v4_2010.shp",
        **{
            f"GSHHG L{level}": data_dir / "gshhg" / "GSHHS_shp" / "f" / f"GSHHS_f_L{level}.shp"
            for level in (1, 2, 5)
        },
    }

    all_found = True
    print(f"\nChecking data directory: {data_dir.absolute()}\n")

    for name, file_path in required_files.items():
        # A converted layer counts as present: the source may be deleted.
        parquet = data_dir / f"{file_path.stem}.parquet"
        if file_path.exists() or parquet.exists():
            print(f"  ✓ {name}: FOUND")
            print(f"    File: {(file_path if file_path.exists() else parquet).name}")
        else:
            print(f"  ✗ {name}: NOT FOUND")
            print(f"    Expected: {file_path.name}")
            all_found = False

    # Check for parquet files if requested
    if check_parquet and all_found:
        print("\nChecking for optimized GeoParquet files:\n")
        parquet_files = {
            "Natural Earth admin-0 countries": data_dir / "ne_10m_admin_0_countries.parquet",
            "Natural Earth admin-1 states/provinces": data_dir / "ne_10m_admin_1_states_provinces.parquet",
            "IHO Sea Areas": data_dir / "World_Seas_IHO_v3.parquet",
            "Longhurst Provinces": data_dir / "Longhurst_world_v4_2010.parquet",
            **{f"GSHHG L{level}": data_dir / f"GSHHS_f_L{level}.parquet" for level in (1, 2, 5)},
        }

        has_parquet = False
        for name, file_path in parquet_files.items():
            if file_path.exists():
                print(f"  ✓ {name} (GeoParquet): FOUND")
                has_parquet = True
            else:
                print(f"  ✗ {name} (GeoParquet): NOT FOUND")

        if not has_parquet:
            print(
                "\n💡 Tip: Run with --convert to create fast-loading GeoParquet files"
            )

    print("\n" + "=" * 70)
    if all_found:
        print("✓ SUCCESS! All required files are present!")
        print("=" * 70)
        print("\nYou can now run:")
        print("  python scripts/geocode.py input.tsv output.tsv")
        print("  python scripts/check.py input.tsv out_prefix")
    else:
        print("⚠ MISSING FILES - Please download the missing datasets above")
        print("=" * 70)

    return all_found


def main():
    """Main script - idempotent data setup."""
    parser = argparse.ArgumentParser(
        description="Idempotent setup for geographic data - automatically converts to GeoParquet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory holding the boundary layers (default: $METALOG_GEOCODING_DATA, else data/)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify files, don't show download instructions or convert",
    )
    parser.add_argument(
        "--convert",
        action="store_true",
        help="Force conversion even if parquet files exist",
    )
    args = parser.parse_args()
    # Resolved rather than defaulting to Path("data"), so
    # $METALOG_GEOCODING_DATA is honoured.
    args.data_dir = resolve_data_dir(args.data_dir)

    # Create data directory
    args.data_dir.mkdir(parents=True, exist_ok=True)

    if args.verify_only:
        # Just verify
        verify_files(args.data_dir, check_parquet=True)
    elif args.convert:
        # Convert whatever sources are present; each missing one is reported
        convert_to_parquet(args.data_dir)
    else:
        # Idempotent behavior: check and auto-convert if needed
        print("\n" + "=" * 70)
        print("GEOGRAPHIC DATA SETUP")
        print("=" * 70)

        # A dataset counts as present if either its source or its parquet is
        # there, since sources may be deleted once converted.
        sources = [
            *(ne_source(args.data_dir, stem) for stem in NE_STEMS),
            args.data_dir / "World_Seas_IHO_v3.shp",
            args.data_dir / "Longhurst_world_v4_2010.shp",
        ]

        files_found = all(
            source.exists() or source.with_suffix(".parquet").exists()
            for source in sources
        ) and all(
            (args.data_dir / "gshhg" / "GSHHS_shp" / "f" / f"GSHHS_f_L{level}.shp").exists()
            or (args.data_dir / f"GSHHS_f_L{level}.parquet").exists()
            for level in (1, 2, 5)
        )

        if not files_found:
            # Data files missing - show download instructions
            print("\nRequired data files not found. Manual download required.")
            print("Total download size: ~200 MB")
            print("\nPlease follow the instructions below to download each dataset.")

            show_naturalearth_instructions(args.data_dir)
            show_marineregions_instructions(args.data_dir)
            show_longhurst_instructions(args.data_dir)
            show_gshhg_instructions(args.data_dir)

            # Verify what's already present
            verify_files(args.data_dir, check_parquet=True)

            print("\nAfter downloading, run this script again to auto-convert to GeoParquet:")
            print("  python scripts/download_data.py")
        else:
            # Data files exist - check if parquet files exist
            parquet_exist = check_parquet_files(args.data_dir)

            if not parquet_exist:
                # Auto-convert to parquet
                print("\nData files found. Converting to GeoParquet format...")
                convert_to_parquet(args.data_dir)
                names = args.data_dir / "GSHHS_f_names.parquet"
                if not names.exists():
                    print(f"\nStill missing: {names.name}. Build it with")
                    print("  python scripts/build_landmass_names.py --geonames ... --country-info ... --ne-regions ...")
                    print("(see docs/installation-and-data.md)")
            else:
                # Everything is ready
                print("\n✓ All data files ready (including GeoParquet)")
                verify_files(args.data_dir, check_parquet=True)


if __name__ == "__main__":
    main()
