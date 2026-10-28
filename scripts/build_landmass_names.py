#!/usr/bin/env python3
"""
Name the GSHHG landmasses, and say which country an islet belongs to.

GSHHG polygons carry only an id and an area. This joins them once to GeoNames
island features (CC-BY 4.0, https://www.geonames.org/) and Natural Earth's
named island regions, and writes ``data/GSHHS_f_names.parquet``: one row per
L1/L5 polygon with a name and/or a country and admin-1 code, resolved to
Natural Earth's ``ADM0_A3`` and ``adm1_code`` so answers use the ``admin``
scheme's vocabulary. GeoNames is needed only here, not at run time.

The country is a fallback (see ``ReverseGeocoder._coast_place``): Natural
Earth at the nearest coast point comes first, because a landmass can be split
between countries (Ireland, Hispaniola). The GeoNames country covers islets
Natural Earth lacks, where the nearest Natural Earth country can be the wrong
one.

Normally run by ``scripts/setup_data.py``. Every input defaults to its
place in the downloads directory (see docs/installation-and-data.md); the
GSHHG and Natural Earth parquet layers are read from the data directory, where
the output is written too:

    python scripts/build_landmass_names.py --downloads-dir downloads --data-dir data
"""

import argparse
import collections
import time
import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

sys.path.insert(0, str(Path(__file__).parent.parent))

from metalog_geocoding.paths import data_dir as resolve_data_dir

#: GeoNames feature codes that name a piece of land surrounded by water, best
#: first. ISLS (an archipelago) names a group, so it only labels a polygon that
#: no single-island feature does.
FEATURE_RANK = {
    "ISL": 0, "ATOL": 0, "ISLET": 1, "ISLM": 1, "ISLT": 1, "ISLF": 1, "ISLS": 2,
}

#: How far outside a polygon a GeoNames point may sit and still name it:
#: GSHHG's shoreline is good to ~50-500 m, and GeoNames points for small
#: islands are often placed just off them.
NEAR_M = 2000

#: An unnamed polygon -- no GeoNames point within NEAR_M -- borrows the country
#: of the nearest GeoNames island feature within this distance, but not its
#: name. Islets that close almost always share a sovereign; 30 km is the
#: coastal radius already used for the distance rule in coast.py.
NEARBY_COUNTRY_M = 30_000

#: The GSHHG continents have no GeoNames island feature; name them directly.
CONTINENT_NAMES = {
    "0-E": "Eurasia", "0-W": "Eurasia", "1": "Africa", "2": "North America",
    "3": "South America", "6": "Australia", "4-E": "Antarctica", "4-W": "Antarctica",
}

#: GeoNames countries that Natural Earth folds into another admin-0 unit.
#: Checked against the data: none of these ISO-3 codes is an NE ADM0_A3.
FOLDED_INTO = {
    "GP": "FRA", "MQ": "FRA", "GF": "FRA", "RE": "FRA", "YT": "FRA",
    "SJ": "NOR", "BQ": "NLD", "BV": "NOR", "TK": "NZL",
    "CC": "IOA", "CX": "IOA",  # Natural Earth's "Indian Ocean Territories"
}

GEONAMES_COLUMNS = [
    "geonameid", "name", "asciiname", "alternatenames", "latitude", "longitude",
    "feature_class", "feature_code", "country_code", "cc2", "admin1_code",
    "admin2_code", "admin3_code", "admin4_code", "population", "elevation",
    "dem", "timezone", "modification_date",
]


def read_islands(path: Path) -> pd.DataFrame:
    """Island features from a GeoNames dump (allCountries.zip or a country file)."""
    def frames(handle):
        for chunk in pd.read_csv(
            handle, sep="\t", header=None, names=GEONAMES_COLUMNS, dtype=str,
            quoting=3, keep_default_na=False, chunksize=1_000_000,
        ):
            yield chunk[(chunk.feature_class == "T") & chunk.feature_code.isin(FEATURE_RANK)]

    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            member = next(n for n in z.namelist() if n.endswith(".txt") and "readme" not in n.lower())
            with z.open(member) as raw:
                df = pd.concat(frames(io.TextIOWrapper(raw, encoding="utf-8")))
    else:
        df = pd.concat(frames(path))
    df = df.assign(
        latitude=df.latitude.astype(float),
        longitude=df.longitude.astype(float),
        n_alternate=df.alternatenames.str.count(",") + (df.alternatenames != ""),
        rank=df.feature_code.map(FEATURE_RANK),
    )
    # COPY's text format cannot carry these; no island name should contain them.
    bad = df.name.str.contains(r"[\t\n\\]", regex=True)
    if bad.any():
        print(f"  dropping {bad.sum()} names containing a tab, newline or backslash")
        df = df[~bad]
    return df.reset_index(drop=True)


def iso2_to_adm0(country_info: Path, adm0: pd.DataFrame) -> dict[str, str]:
    """GeoNames ISO-2 country -> Natural Earth ADM0_A3, via ISO-3."""
    iso3 = {}
    for line in country_info.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            fields = line.split("\t")
            iso3[fields[0]] = fields[1]
    ne_codes = set(adm0.ADM0_A3) | set(adm0.ISO_A3_EH)
    by_iso3 = {}
    for a3, eh in zip(adm0.ADM0_A3, adm0.ISO_A3_EH):
        by_iso3.setdefault(eh, a3)
        by_iso3[a3] = a3
    out = {}
    for iso2, i3 in iso3.items():
        if iso2 in FOLDED_INTO:
            assert i3 not in ne_codes, f"{iso2}/{i3} is in Natural Earth after all; drop it from FOLDED_INTO"
            out[iso2] = FOLDED_INTO[iso2]
        elif i3 in by_iso3:
            out[iso2] = by_iso3[i3]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=None,
                   help="where the converted layers are read and the names written "
                        "(default: $METALOG_GEOCODING_DATA, else data/)")
    p.add_argument("--downloads-dir", type=Path, default=Path("downloads"),
                   help="where the downloads are; the defaults below are relative to it "
                        "(default: downloads/)")
    p.add_argument("--geonames", type=Path, default=None,
                   help="GeoNames allCountries.zip (default: DOWNLOADS/geonames/allCountries.zip)")
    p.add_argument("--country-info", type=Path, default=None,
                   help="GeoNames countryInfo.txt (default: DOWNLOADS/geonames/countryInfo.txt)")
    p.add_argument("--ne-regions", type=Path, default=None,
                   help="Natural Earth ne_10m_geography_regions_polys.shp, public domain "
                        "(default: DOWNLOADS/ne_10m_geography_regions_polys.shp)")
    p.add_argument("--lakes", type=Path, default=None,
                   help="GSHHG lakes, GSHHS_f_L2.shp "
                        "(default: DOWNLOADS/gshhg/GSHHS_shp/f/GSHHS_f_L2.shp)")
    p.add_argument("--islands-cache", type=Path, default=None,
                   help="parquet to keep the extracted island features in; reading "
                        "allCountries.zip is most of the run time "
                        "(default: DOWNLOADS/geonames/geonames_islands.parquet)")
    args = p.parse_args()
    data = resolve_data_dir(args.data_dir)
    downloads = args.downloads_dir
    args.geonames = args.geonames or downloads / "geonames" / "allCountries.zip"
    args.country_info = args.country_info or downloads / "geonames" / "countryInfo.txt"
    args.ne_regions = args.ne_regions or downloads / "ne_10m_geography_regions_polys.shp"
    args.lakes = args.lakes or downloads / "gshhg" / "GSHHS_shp" / "f" / "GSHHS_f_L2.shp"
    args.islands_cache = args.islands_cache or downloads / "geonames" / "geonames_islands.parquet"

    import geopandas as gpd
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    l1 = gpd.read_parquet(data / "GSHHS_f_L1.parquet", columns=["id", "area", "geometry"])
    l5 = gpd.read_parquet(data / "GSHHS_f_L5.parquet", columns=["id", "area", "geometry"])
    l2 = gpd.read_file(args.lakes, columns=["geometry"])
    land = pd.concat([l1, l5], ignore_index=True)
    geoms = np.asarray(land.geometry.values)
    tree = shapely.STRtree(geoms)
    lakes = shapely.STRtree(np.asarray(l2.geometry.values))

    adm0 = pd.read_parquet(data / "ne_10m_admin_0_countries.parquet", columns=["ADM0_A3", "ISO_A3_EH"])
    adm1 = pd.read_parquet(data / "ne_10m_admin_1_states_provinces.parquet",
                           columns=["adm0_a3", "adm1_code", "gn_a1_code"])
    to_adm0 = iso2_to_adm0(args.country_info, adm0)
    # A GeoNames admin-1 code names a Natural Earth region only when exactly
    # one region carries it (35 codes are shared, e.g. PF.01).
    gn_counts = adm1.gn_a1_code.value_counts()
    to_adm1 = {
        code: row_code
        for code, row_code in zip(adm1.gn_a1_code, adm1.adm1_code)
        if isinstance(code, str) and code and gn_counts[code] == 1
    }

    # The cache stands in for allCountries.zip unless the zip is newer.
    if args.islands_cache.exists() and not (
        args.geonames.exists()
        and args.geonames.stat().st_mtime > args.islands_cache.stat().st_mtime
    ):
        isl = pd.read_parquet(args.islands_cache)
    else:
        print(f"reading {args.geonames} ...", flush=True)
        isl = read_islands(args.geonames)
        isl.to_parquet(args.islands_cache)
    print(f"  {len(isl):,} island features", flush=True)

    t0 = time.time()
    tick = lambda what: print(f"  [{time.time() - t0:5.0f}s] {what}", flush=True)
    pts = shapely.points(isl.longitude.to_numpy(), isl.latitude.to_numpy())
    in_lake = np.zeros(len(isl), dtype=bool)
    in_lake[lakes.query(pts, predicate="within")[0]] = True

    tick("lake test done")
    # Index the points and query with the polygons: GEOS prepares the query
    # geometry, so Eurasia's two million vertices are prepared once rather than
    # re-scanned per point (minutes slower the other way round).
    poly_idx, pt_idx = shapely.STRtree(pts).query(geoms, predicate="contains")
    tick("inside test done")
    owner = np.full(len(isl), -1)
    owner[pt_idx] = poly_idx
    match = np.where(owner >= 0, "inside", "")

    # Points in no polygon: the nearest polygon, if within NEAR_M -- but only
    # among polygons no point lies inside, since a polygon with an inside name
    # never uses a near one, and not the continents, which are named directly.
    # That restriction also keeps this fast, by never measuring to Eurasia.
    named_inside = set(owner[owner >= 0]) | set(np.flatnonzero(land.id.isin(CONTINENT_NAMES)))
    open_polys = np.array([j for j in range(len(geoms)) if j not in named_inside])
    open_tree = shapely.STRtree(geoms[open_polys])
    outside = np.flatnonzero((owner < 0) & ~in_lake)
    print(f"  {len(outside):,} points outside every polygon; "
          f"{len(open_polys):,} polygons still unnamed", flush=True)
    near_pt, near_k = open_tree.query_nearest(pts[outside], max_distance=0.05)
    tick(f"nearest query done: {len(near_pt):,} pairs")
    for i, k in zip(outside[near_pt], near_k):
        j = open_polys[k]
        boundary_pt = shapely.get_point(shapely.shortest_line(pts[i], geoms[j]), 1)
        _, _, d = geod.inv(isl.longitude.iat[i], isl.latitude.iat[i],
                           shapely.get_x(boundary_pt), shapely.get_y(boundary_pt))
        if d <= NEAR_M:
            owner[i], match[i] = j, "near"

    tick("near distances done")
    isl = isl.assign(owner=owner, match=match, in_lake=in_lake)
    cand = isl[(isl.owner >= 0) & ~isl.in_lake].copy()
    centroid = shapely.centroid(geoms)
    cand["centroid_deg"] = shapely.distance(
        shapely.points(cand.longitude.to_numpy(), cand.latitude.to_numpy()), centroid[cand.owner.to_numpy()]
    )
    # inside before near; island before islet before archipelago; the best-known
    # (most alternate names) first; then nearest the polygon's centre.
    cand = cand.sort_values(
        ["owner", "match", "rank", "n_alternate", "centroid_deg"],
        ascending=[True, True, True, False, True],  # "inside" < "near" alphabetically
    )
    n_candidates = cand.groupby("owner").size()
    best = cand.drop_duplicates("owner")

    rows = []
    for _, r in best.iterrows():
        gid = land.id.iat[r.owner]
        rows.append({
            "id": gid,
            "name": CONTINENT_NAMES.get(gid, r["name"]),
            "geonameid": "" if gid in CONTINENT_NAMES else r.geonameid,
            "gn_country": r.country_code,
            "gn_admin1": f"{r.country_code}.{r.admin1_code}" if r.admin1_code else "",
            "ne_adm0_a3": to_adm0.get(r.country_code, ""),
            "ne_adm1_code": to_adm1.get(f"{r.country_code}.{r.admin1_code}", ""),
            "match": r.match,
            "candidates": int(n_candidates[r.owner]),
            "n_alternate": int(r.n_alternate),
            "feature_code": r.feature_code,
            "area_km2": float(land["area"].iat[r.owner]),
        })
    for row in rows:
        row["name_source"] = "fixed" if row["id"] in CONTINENT_NAMES else "geonames"

    # Natural Earth's named islands win over GeoNames, which has no single
    # feature for many large islands (Ireland, Madagascar, Cuba), so its best
    # point inside them is some river or tidal islet. Natural Earth's outlines
    # are generalised and can hold several GSHHG polygons, so each name goes
    # only to the largest polygon inside it.
    regions = gpd.read_file(args.ne_regions)
    regions = regions[regions.FEATURECLA == "Island"].reset_index(drop=True)
    reps = shapely.point_on_surface(geoms)
    region_idx, poly_idx = shapely.STRtree(reps).query(
        np.asarray(regions.geometry.values), predicate="contains")
    by_id = {row["id"]: row for row in rows}
    claimed = {}
    areas = land["area"].to_numpy()
    for r in np.unique(region_idx):
        polys = poly_idx[region_idx == r]
        j = polys[np.argmax(areas[polys])]
        gid = land.id.iat[j]
        if gid in CONTINENT_NAMES:
            continue
        if gid in claimed:  # nested Natural Earth islands: keep the smaller region
            if regions.geometry.iat[r].area >= regions.geometry.iat[claimed[gid]].area:
                continue
        claimed[gid] = r
    for gid, r in claimed.items():
        name = regions.NAME_EN.iat[r]
        if gid in by_id:
            by_id[gid].update(name=name, name_source="natural_earth")
        else:
            rows.append({"id": gid, "name": name, "geonameid": "", "gn_country": "", "gn_admin1": "",
                         "ne_adm0_a3": "", "ne_adm1_code": "", "match": "region", "candidates": 0,
                         "n_alternate": 0, "feature_code": "", "name_source": "natural_earth",
                         "area_km2": float(areas[land.index[land.id == gid][0]])})
    print(f"  {len(claimed)} polygons named from {len(regions)} Natural Earth islands", flush=True)

    # A polygon split at the antimeridian is one landmass: both halves take
    # the better-attested name (Natural Earth's, else the half with more
    # candidates).
    halves = collections.defaultdict(list)
    for row in rows:
        if row["id"].endswith(("-E", "-W")):
            halves[row["id"][:-2]].append(row)
    land_ids = set(land.id)
    for base, pair in halves.items():
        if len(pair) == 1:
            other = base + ("-W" if pair[0]["id"].endswith("-E") else "-E")
            if other in land_ids:
                rows.append({**pair[0], "id": other, "match": "other half", "candidates": 0,
                             "area_km2": pair[0]["area_km2"]})
        if len(pair) == 2:
            best_half = max(pair, key=lambda r: (r["name_source"] == "natural_earth", r["candidates"]))
            for half in pair:
                for key in ("name", "name_source", "geonameid", "gn_country", "gn_admin1",
                            "ne_adm0_a3", "ne_adm1_code"):
                    half[key] = best_half[key]

    for gid, name in CONTINENT_NAMES.items():
        if gid not in {row["id"] for row in rows}:
            rows.append({"id": gid, "name": name, "geonameid": "", "gn_country": "", "gn_admin1": "",
                         "ne_adm0_a3": "", "ne_adm1_code": "", "match": "fixed", "candidates": 0,
                         "n_alternate": 0, "feature_code": "", "name_source": "fixed",
                         "area_km2": float(land.set_index("id")["area"][gid])})
    # Unnamed polygons: the country (not the name) of the nearest GeoNames
    # island feature within NEARBY_COUNTRY_M.
    named_ids = {row["id"] for row in rows} | set(CONTINENT_NAMES)
    unnamed = np.flatnonzero(~land.id.isin(named_ids).to_numpy())
    usable = isl[~isl.in_lake].reset_index(drop=True)
    usable_pts = shapely.points(usable.longitude.to_numpy(), usable.latitude.to_numpy())
    poly_k, pt_k = shapely.STRtree(usable_pts).query_nearest(geoms[unnamed], max_distance=0.35)
    nearby = 0
    for k, i in zip(poly_k, pt_k):
        j = unnamed[k]
        line = shapely.shortest_line(geoms[j], usable_pts[i])
        a, b = shapely.get_point(line, 0), shapely.get_point(line, 1)
        _, _, d = geod.inv(shapely.get_x(a), shapely.get_y(a), shapely.get_x(b), shapely.get_y(b))
        if d > NEARBY_COUNTRY_M:
            continue
        f = usable.iloc[i]
        rows.append({
            "id": land.id.iat[j], "name": "", "geonameid": "", "gn_country": f.country_code,
            "gn_admin1": f"{f.country_code}.{f.admin1_code}" if f.admin1_code else "",
            "ne_adm0_a3": to_adm0.get(f.country_code, ""),
            "ne_adm1_code": to_adm1.get(f"{f.country_code}.{f.admin1_code}", ""),
            "match": "nearby country", "candidates": 0, "n_alternate": 0, "feature_code": "",
            "name_source": "", "area_km2": float(land["area"].iat[j]),
        })
        nearby += 1
    print(f"  {nearby:,} unnamed polygons given the country of a GeoNames island "
          f"within {NEARBY_COUNTRY_M / 1000:.0f} km", flush=True)

    # query_nearest returns every point at a tied distance; one row per polygon,
    # and a named row always before a borrowed-country one.
    out = pd.DataFrame(rows).drop_duplicates("id", keep="first").sort_values("id")
    # Continents get their fixed name and no country: they span many, and the
    # geocoder never falls back to a landmass-level country for them.
    is_cont = out.id.isin(CONTINENT_NAMES)
    out.loc[is_cont, ["gn_country", "gn_admin1", "ne_adm0_a3", "ne_adm1_code"]] = ""
    out.loc[is_cont, "match"] = "fixed"
    # Antarctica is one Natural Earth unit, so unlike Eurasia it has a
    # landmass-level answer; the ice front can sit well over COAST_MATCH_M
    # from Natural Earth's Antarctic outline.
    out.loc[out.id.isin(["4-E", "4-W"]), "ne_adm0_a3"] = "ATA"

    path = data / "GSHHS_f_names.parquet"
    out.to_parquet(path, index=False)

    area = land.set_index("id")["area"]
    named_ids = set(out.id[out.name != ""])
    country_ids = set(out.id[out.ne_adm0_a3 != ""])
    print(f"\nwrote {path}: {len(out):,} rows for {len(land):,} polygons; "
          f"{len(named_ids):,} named, {len(country_ids):,} with a Natural Earth country "
          f"({area[area.index.isin(named_ids)].sum() / area.sum():.1%} of land area named)")
    print("  by match:", out.match.value_counts().to_dict())
    print("  by name source:", out.name_source.value_counts().to_dict())
    print("  with >1 candidate:", int((out.candidates > 1).sum()))
    unmapped = out[(out.gn_country != "") & (out.ne_adm0_a3 == "")].gn_country.value_counts()
    print("  GeoNames countries with no Natural Earth unit:", unmapped.to_dict())
    print("  with an NE admin-1:", int((out.ne_adm1_code != "").sum()))
    for size in (0, 1, 10, 100, 1000):
        sel = area[area >= size]
        print(f"  polygons >= {size} km² ({len(sel):,}): named {sel.index.isin(named_ids).mean():.1%}, "
              f"with a country {sel.index.isin(country_ids | set(CONTINENT_NAMES)).mean():.1%}")

if __name__ == "__main__":
    main()
