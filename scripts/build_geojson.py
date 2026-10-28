#!/usr/bin/env python3
"""
Simplified GeoJSON for drawing maps of the location hierarchy.

Each feature's id is the value the geocoder publishes for that level (the
column names below are Metalog's download columns), and names come from the
same code that geocodes, so a map joins on the data without a lookup table:

    continents.geojson            id = continent (geographical)
    continents_political.geojson  id = continent (political: all of Russia is Europe)
    countries.geojson             id = location_country_code
    regions.geojson               id = location_region_code (Natural Earth adm1_code)
    oceans.geojson                id = location_ocean
    basins.geojson                id = the basin's name, the middle levels of location_hierarchy
    seas.geojson                  id = location_mrgid
    longhurst_biomes.geojson      id = longhurst_biome
    longhurst_provinces.geojson   id = longhurst_code

Regions are drawn zoomed in and simplified to `--tolerance`; every other layer
is drawn at global scale and simplified to `--world-tolerance`. Each scheme is
simplified as a coverage and the coarser levels are dissolved from it, so
shared borders stay shared and no slivers open between neighbours. IHO and
Longhurst are not clean coverages (neighbours trace a common border with
different vertices), so they are rebuilt into one first.

    python scripts/build_geojson.py -o geojson/

The sea names come from the bundled vocabulary unless `--marine-regions` and
`--sea-aliases` point elsewhere.
"""

import argparse
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely

from metalog_geocoding.geographic_overrides import WESTERN_PART_OVERRIDES
from metalog_geocoding.hierarchy_table import _marine_rows
from metalog_geocoding.land import NaturalEarthLandGeocoder
from metalog_geocoding.longhurst import LonghurstGeocoder
from metalog_geocoding.paths import data_dir as resolve_data_dir
from metalog_geocoding.sea import SeaGeocoder
from metalog_geocoding.vocabulary import (
    BUNDLED_MARINE_REGIONS,
    BUNDLED_SEA_ALIASES,
    load_marine_vocabulary,
)

# Coordinates are written on a grid this much finer than the simplification
GRID_PER_TOLERANCE = 100


def without_small_holes(geom, min_area: float):
    """Fill holes too small to draw: IHO's islands run into the hundreds of thousands."""
    polygons = [
        shapely.Polygon(part.exterior, [ring for ring in part.interiors if shapely.Polygon(ring).area >= min_area])
        for part in shapely.get_parts(geom)
    ]
    return shapely.multipolygons(polygons) if len(polygons) > 1 else polygons[0]


def rebuild_coverage(geoms: np.ndarray, tolerance: float, grow: bool = True) -> np.ndarray:
    """Turn overlapping, gappy polygons into a coverage, one polygon per input.

    The borders of the pre-simplified polygons are noded and polygonized, and
    each face goes to the smallest polygon covering most of it -- the
    geocoder's own tie-break for overlapping seas, and what gives a filled
    enclave back to its own unit. Coverage is measured by area, not at a
    point, because along a slight overlap a point near a face's edge can fall
    in the wrong polygon. A face covered by none is a gap if it is thinner
    than `tolerance` and goes to the neighbour it shares the most border with;
    otherwise it is land (or sea, for the land layer) and stays unowned.

    A gap between two seas that opens onto the coast forms no face, and would
    show as a line along their whole border. `grow` therefore widens every
    polygon by a twentieth of the tolerance first, turning such gaps into
    overlaps (and moving the coast out by as much). Natural Earth's land units
    already share their borders and are not grown.
    """
    geoms = np.array([without_small_holes(geom, (2 * tolerance) ** 2) for geom in geoms])
    simplified = shapely.simplify(geoms, tolerance / 10, preserve_topology=True)
    if grow:
        simplified = shapely.buffer(simplified, tolerance / 20, quad_segs=2)
    borders = shapely.union_all(shapely.boundary(simplified))
    faces = shapely.get_parts(shapely.polygonize(shapely.get_parts(borders)))
    face_areas = shapely.area(faces)
    areas = shapely.area(geoms)

    owner = np.full(len(faces), -1)
    face_idx, geom_idx = shapely.STRtree(simplified).query(faces, predicate="intersects")
    covered = shapely.area(shapely.intersection(faces[face_idx], simplified[geom_idx])) > face_areas[face_idx] / 2
    for f, g in zip(face_idx[covered], geom_idx[covered]):
        if owner[f] == -1 or areas[g] < areas[owner[f]]:
            owner[f] = g

    # A sliver vanishes when shrunk by half the tolerance. Area and 2 * area /
    # perimeter only pre-filter, since shrinking an ocean-sized face is slow and
    # a ragged coast is long for its area. The longest shared border picks
    # between the units a gap touches.
    candidate = (owner == -1) & (face_areas < 100 * tolerance**2) & (2 * face_areas / shapely.length(faces) < tolerance)
    unowned = np.flatnonzero(candidate)
    unowned = unowned[shapely.is_empty(shapely.buffer(faces[unowned], -tolerance / 2))]
    face_idx, geom_idx = shapely.STRtree(simplified).query_nearest(faces[unowned], max_distance=tolerance)
    shared = shapely.length(
        shapely.intersection(shapely.boundary(faces[unowned[face_idx]]), shapely.boundary(simplified[geom_idx]))
    )
    best_shared = np.full(len(faces), -1.0)
    for f, g, length in zip(unowned[face_idx], geom_idx, shared):
        if length > best_shared[f]:
            best_shared[f], owner[f] = length, g

    # a unit smaller than the tolerance can lose every face
    rebuilt = np.array(
        [
            union if not (union := shapely.coverage_union_all(faces[owner == i])).is_empty
            else shapely.Polygon()
            for i in range(len(geoms))
        ]
    )

    # merged faces can leave a ring touching itself (the Irish Sea at 0.1 degrees)
    invalid = ~shapely.is_valid(rebuilt)
    rebuilt[invalid] = shapely.make_valid(rebuilt[invalid], method="structure", keep_collapsed=False)
    return rebuilt


def dissolve(geoms: np.ndarray, keys: list) -> dict:
    """Union a simplified coverage by key; units without a key are left out."""
    groups = defaultdict(list)
    for geom, key in zip(geoms, keys):
        if key is not None:
            groups[key].append(geom)
    return {key: shapely.coverage_union_all(parts) for key, parts in groups.items()}


def write_layer(path: Path, tolerance: float, features: dict, properties: dict | None = None) -> None:
    """Write {id: geometry} with per-id properties, on a grid matching the tolerance."""
    ids = list(features)
    geoms = shapely.set_precision(np.array([features[i] for i in ids]), tolerance / GRID_PER_TOLERANCE)
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": feature_id,
                "properties": {"id": feature_id, **(properties or {}).get(feature_id, {})},
                "geometry": json.loads(shapely.to_geojson(geom)),
            }
            for feature_id, geom in zip(ids, geoms)
            if not geom.is_empty
        ],
    }
    text = json.dumps(collection, separators=(",", ":")).encode()
    path.write_bytes(text)
    # for nginx's gzip_static, which serves it as-is instead of compressing per request
    path.with_name(path.name + ".gz").write_bytes(gzip.compress(text, compresslevel=9, mtime=0))
    print(f"  {path.name}: {len(collection['features'])} features, "
          f"{path.stat().st_size / 1e6:.1f} MB", file=sys.stderr)


def first_by_area(geoms: np.ndarray, keys: list, values: list) -> dict:
    """For each key, the value of its largest unit."""
    best: dict = {}
    for area, key, value in sorted(zip(shapely.area(geoms), keys, values), key=lambda t: t[0]):
        best[key] = value
    return best


def build_land(data: Path, out: Path, tolerance: float, world_tolerance: float) -> None:
    land = NaturalEarthLandGeocoder(data)
    adm0 = land.adm0.set_index("ADM0_A3", drop=False)
    assert adm0.index.is_unique

    # Seven admin-0 units have no admin-1 unit. Two of them (Bir Tawil, the
    # Cyprus buffer zone) overlap their neighbours' admin-1 units, so the layer
    # is rebuilt like IHO; the smaller unit wins, as the admin-0 lookup does.
    with_adm1 = set(land.adm1["adm0_a3"])
    units = []
    for _, row in land.adm1.iterrows():
        a0 = adm0.loc[row["adm0_a3"]]
        if row["iso_3166_2"] in WESTERN_PART_OVERRIDES:
            # split at the cutoff so each side gets its own continent
            cutoff, _ = WESTERN_PART_OVERRIDES[row["iso_3166_2"]]
            units.append((a0, row, row.geometry.intersection(shapely.box(-180, -90, cutoff, 90)), cutoff - 1))
            units.append((a0, row, row.geometry.intersection(shapely.box(cutoff, -90, 180, 90)), cutoff + 1))
        else:
            units.append((a0, row, row.geometry, None))
    units += [(row, None, row.geometry, None) for code, row in adm0.iterrows() if code not in with_adm1]
    geoms = np.array([geom for _, _, geom, _ in units])
    described = [land.describe(a0, adm1, lon) for a0, adm1, _, lon in units]
    adm1_codes = [d.get("adm1_code") for d in described]

    rebuilt = rebuild_coverage(geoms, tolerance, grow=False)
    geoms = shapely.coverage_simplify(rebuilt, world_tolerance)
    region_geoms = shapely.coverage_simplify(rebuilt, tolerance)

    country_codes = [d["country_code"] for d in described]
    countries = {
        code: {"name": name, "continent_political": political}
        for code, (name, political) in first_by_area(
            geoms, country_codes, [(d["country"], d["continent_political"]) for d in described]
        ).items()
    }
    regions = {
        code: {"name": d.get("name_1"), "country_code": d["country_code"], "iso_3166_2": d.get("iso_3166_2")}
        for code, d in zip(adm1_codes, described)
        if code
    }

    write_layer(out / "continents.geojson", world_tolerance, dissolve(geoms, [d["continent"] for d in described]))
    write_layer(
        out / "continents_political.geojson",
        world_tolerance,
        dissolve(geoms, [d["continent_political"] for d in described]),
    )
    write_layer(out / "countries.geojson", world_tolerance, dissolve(geoms, country_codes), countries)
    write_layer(out / "regions.geojson", tolerance, dissolve(region_geoms, adm1_codes), regions)


def build_marine(data: Path, cache: Path, out: Path, tolerance: float, vocabulary) -> None:
    seas = SeaGeocoder(data, cache)
    iho = seas.seas.reset_index(drop=True)

    chains, gazetteer_chains = [], []
    for _, row in iho.iterrows():
        result = {"ocean": seas.hierarchy_cache.get_ocean(int(row["MRGID"])), "sea": row["NAME"]}
        levels = [r for r in _marine_rows(0, 0, "", result, vocabulary) if r["level"] > 0]
        chains.append([r["value"] for r in sorted(levels, key=lambda r: r["level"])])
        gazetteer_chains.append([part.strip() for part in reversed(result["ocean"].split(";"))])

    geoms = shapely.coverage_simplify(rebuild_coverage(iho.geometry.values, tolerance), tolerance)

    mrgids = [int(m) for m in iho["MRGID"]]
    write_layer(out / "oceans.geojson", tolerance, dissolve(geoms, [chain[0] for chain in chains]))

    # A basin is every sea below it plus its own polygon, if it has one: the
    # Eastern Mediterranean is the water between the Adriatic, Aegean and
    # Ionian as well as all three. Grouped by the gazetteer's spelling, which
    # is how location_hierarchy names the levels above the deepest.
    basin_names = {(depth, name) for chain in gazetteer_chains for depth, name in enumerate(chain[1:-1], start=2)}
    basins = defaultdict(list)
    for geom, chain in zip(geoms, gazetteer_chains):
        for depth, name in enumerate(chain[1:], start=2):
            if (depth, name) in basin_names:
                basins[(depth, name)].append(geom)
    write_layer(
        out / "basins.geojson",
        tolerance,
        {name: shapely.coverage_union_all(parts) for (_, name), parts in basins.items()},
        {name: {"level": depth} for depth, name in basins},
    )

    write_layer(
        out / "seas.geojson",
        tolerance,
        dict(zip(mrgids, geoms)),
        {
            mrgid: {"name": chain[-1] if len(chain) > 1 else None, "hierarchy": " > ".join(chain)}
            for mrgid, chain in zip(mrgids, chains)
        },
    )


def build_longhurst(data: Path, out: Path, tolerance: float) -> None:
    provinces = LonghurstGeocoder(data).provinces.reset_index(drop=True)
    described = [LonghurstGeocoder._describe(row) for _, row in provinces.iterrows()]

    geoms = shapely.coverage_simplify(
        rebuild_coverage(shapely.make_valid(provinces.geometry.values), tolerance), tolerance
    )

    write_layer(out / "longhurst_biomes.geojson", tolerance, dissolve(geoms, [d["longhurst_biome"] for d in described]))
    write_layer(
        out / "longhurst_provinces.geojson",
        tolerance,
        dict(zip([d["longhurst_code"] for d in described], geoms)),
        {
            d["longhurst_code"]: {"name": d["longhurst_province"], "biome": d["longhurst_biome"]}
            for d in described
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--out-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"))
    parser.add_argument("--marine-regions", type=Path, default=BUNDLED_MARINE_REGIONS)
    parser.add_argument("--sea-aliases", type=Path, default=BUNDLED_SEA_ALIASES)
    parser.add_argument("--tolerance", type=float, default=0.02, help="simplification of regions, in degrees")
    parser.add_argument(
        "--world-tolerance", type=float, default=0.1, help="simplification of the global layers, in degrees"
    )
    args = parser.parse_args()

    data = resolve_data_dir(args.data_dir, require=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    vocabulary = load_marine_vocabulary(args.marine_regions, args.sea_aliases)

    print("Land", file=sys.stderr)
    build_land(data, args.out_dir, args.tolerance, args.world_tolerance)
    print("Marine", file=sys.stderr)
    build_marine(data, args.cache_dir, args.out_dir, args.world_tolerance, vocabulary)
    print("Longhurst", file=sys.stderr)
    build_longhurst(data, args.out_dir, args.world_tolerance)


if __name__ == "__main__":
    main()
