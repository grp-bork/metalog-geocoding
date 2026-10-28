"""
Build ``location_hierarchy`` rows from a geocoded result.

One row per *fact*: ``latitude, longitude, location_resolution, scheme, level,
key, value``. Samples join to the table on ``(latitude, longitude,
location_resolution)``. Every tool that emits rows should use this module so
the output does not depend on which tool wrote it.

Standard library only: turns a result dict into rows and never geocodes.
"""

from .vocabulary import published_sea

#: Fields pulled out of the long-format export, and the sample attributes this
#: package needs to place and grade a coordinate.
WANTED = {
    "latitude",
    "longitude",
    "geographic_location",
    "location_resolution",
    "artificial",
    "study_code",
}

HIERARCHY_COLUMNS = [
    "latitude",
    "longitude",
    "location_resolution",
    "scheme",
    "level",
    "key",
    "value",
]

#: location_resolution values where the coordinate is derived from the place
#: name rather than measured, so it cannot contradict that name.
DERIVED_RESOLUTIONS = {"country", "continent"}

#: location_resolution values recorded at a real field observation, where the
#: geocoded result's full hierarchy is trustworthy top to bottom.
FULL_HIERARCHY_RESOLUTIONS = {"city", "exact_location", "region", "site", "village"}

#: Every resolution that publishes anything. The derived ones are truncated to
#: what a centroid supports ("country" -> continent, country, country_code;
#: "continent" -> continent). Others ("unknown", "in orbit") publish nothing.
QUALIFYING_RESOLUTIONS = FULL_HIERARCHY_RESOLUTIONS | DERIVED_RESOLUTIONS

#: ``artificial`` values (Metalog's convention) with no field location: the
#: coordinate is a workbench, and any location_resolution they carry describes
#: some other sample. They publish no hierarchy, and are checked before the
#: resolution is read.
ARTIFICIAL_NO_LOCATION = {"negative control", "mock"}

#: ``level`` for a row that is an *attribute* rather than a tier of the tree
#: (``country_code``, ``mrgid``, ``is_fallback``, ...). A sentinel rather than
#: NULL because ``level`` is part of the primary key: a marine chain with two
#: intermediate hops emits two ``basin`` rows (North Atlantic Ocean at level 2,
#: Baltic Sea at level 3, under Kattegat) distinguished only by level, and
#: Postgres forbids a nullable key column. Levels start at 1, so 0 is free.
ATTRIBUTE_LEVEL = 0

#: Provenance of the ``coast`` scheme, published once per table (SQL comments,
#: ``GET /health``), not per row. Here rather than in ``coast.py`` so it can be
#: read without the geo stack.
GSHHG_VERSION = "2.3.7"
GSHHG_RESOLUTION = "f"
COAST_SOURCE = (
    f"GSHHG {GSHHG_VERSION} (Wessel & Smith 1996, doi:10.1029/96JB00104), full "
    f"resolution, levels L1 (continents and islands, no size cutoff) + L5 "
    f"(Antarctic ice front); signed geodesic distance on the WGS84 ellipsoid "
    f"(pyproj) to the nearest shoreline point, in km to 0.1 km, positive at sea "
    f"and negative on land (lakes are land; 0 where IHO and Natural Earth "
    f"agree on the side GSHHG does not); at sea, land under 1 km² counts "
    f"only within 30 km (Weigelt et al. 2013, doi:10.1073/pnas.1306309110; "
    f"Gove et al. 2016, doi:10.1038/ncomms10581); landmass area is GSHHG's "
    f"own polygon area in km², to 3 significant figures; the nearest coast is "
    f"placed in a Natural Earth country/admin-1 at the coast point (within 5 km), "
    f"else by the GeoNames country of the landmass; landmass names from Natural "
    f"Earth geography regions and GeoNames (CC-BY 4.0, geonames.org)"
)


def _hierarchy_row(
    lat: float,
    lon: float,
    resolution: str,
    scheme: str,
    level: int,
    key: str,
    value,
) -> dict | None:
    """One fact row, or None if there is nothing to say.

    Never emits an empty value: a missing field (no admin-1, no Longhurst
    province) drops the row.
    """
    if value is None or value == "":
        return None
    row = {"latitude": lat, "longitude": lon}
    # Optional: a caller without a resolution model gets rows without the
    # column rather than a blank one.
    if resolution:
        row["location_resolution"] = resolution
    row.update(scheme=scheme, level=level, key=key, value=str(value))
    return row


def _admin_rows(lat: float, lon: float, resolution: str, result: dict) -> list[dict]:
    """The full admin hierarchy for a land result: continent/country/admin-1,
    plus the attributes that describe rather than place the point."""
    fields = [
        (1, "continent", result.get("continent")),
        (2, "country", result.get("country")),
        (3, "admin_1", result.get("name_1")),
        (ATTRIBUTE_LEVEL, "country_code", result.get("country_code")),
        (ATTRIBUTE_LEVEL, "iso_3166_2", result.get("iso_3166_2")),
        (ATTRIBUTE_LEVEL, "adm1_code", result.get("adm1_code")),
        (ATTRIBUTE_LEVEL, "continent_political", result.get("continent_political")),
        (ATTRIBUTE_LEVEL, "is_fallback", result.get("is_fallback")),
        (ATTRIBUTE_LEVEL, "fallback_distance_m", result.get("fallback_distance_m")),
    ]
    return [
        row
        for level, key, value in fields
        if (row := _hierarchy_row(lat, lon, resolution, "admin", level, key, value))
    ]


def _marine_rows(
    lat: float, lon: float, resolution: str, result: dict, marine
) -> list[dict]:
    """
    The marine hierarchy: the ``ocean`` chain, broadest first, numbered 1..n.

    ``ocean`` is stored most-specific-first, so it is reversed: level 1 is the
    ocean, level n the most specific place. Level 1 is keyed ``ocean``, level
    n (when n > 1) ``sea``, everything between ``basin``. A 1-deep chain gets
    a single ``ocean`` row.

    The deepest value uses the vocabulary's spelling (``published_sea()``);
    the other levels keep the gazetteer's spelling (see
    docs/location-hierarchy.md).
    """
    chain = (result.get("ocean") or "").strip()
    parts = [part.strip() for part in chain.split(";") if part.strip()]
    parts.reverse()
    n = len(parts)

    rows = []
    for i, name in enumerate(parts, start=1):
        if i == 1:
            key = "ocean"
        elif i == n:
            key = "sea"
        else:
            key = "basin"
        value = published_sea(result, marine) if i == n else name
        rows.append(_hierarchy_row(lat, lon, resolution, "marine", i, key, value))

    rows.append(_hierarchy_row(lat, lon, resolution, "marine", ATTRIBUTE_LEVEL, "mrgid", result.get("mrgid")))
    rows.append(
        _hierarchy_row(lat, lon, resolution, "marine", ATTRIBUTE_LEVEL, "is_fallback", result.get("is_fallback"))
    )
    rows.append(
        _hierarchy_row(
            lat, lon, resolution, "marine", ATTRIBUTE_LEVEL, "fallback_distance_m", result.get("fallback_distance_m")
        )
    )
    return [row for row in rows if row is not None]


def _longhurst_rows(lat: float, lon: float, resolution: str, result: dict) -> list[dict]:
    """Biome/province for a sea result, if it has one. A valid sea can have
    none (the Caspian, a river station, the ice sheet); see longhurst.py."""
    fields = [
        (1, "biome", result.get("longhurst_biome")),
        (2, "province", result.get("longhurst_province")),
        (ATTRIBUTE_LEVEL, "longhurst_code", result.get("longhurst_code")),
        (ATTRIBUTE_LEVEL, "longhurst_distance_m", result.get("longhurst_distance_m")),
    ]
    return [
        row
        for level, key, value in fields
        if (row := _hierarchy_row(lat, lon, resolution, "longhurst", level, key, value))
    ]


def _coast_rows(
    lat: float, lon: float, resolution: str, result: dict, marine=None
) -> list[dict]:
    """Signed distance to coast for a land or sea result (GSHHG; see
    metalog_geocoding/coast.py): positive at sea, negative on land.

    A scheme of its own because GSHHG is a separate source. Only distances
    are published, never GSHHG geometry (LGPL). What else it says depends on
    the side:

    - **Sea**: where the nearest coast is, as a tree (``coast_continent`` /
      ``coast_country`` / ``coast_admin_1``). Values are in the ``admin``
      vocabulary so they join to land samples, but under their own keys, so
      that ``country = Germany`` never returns water off Germany.
    - **Land**: the water that coast faces, as attributes: ``coast_ocean``
      (top of the IHO chain), ``coast_sea`` (vocabulary spelling; absent when
      the polygon is an ocean) and ``coast_mrgid``. The nearest coast's
      country is omitted for land points; it adds nothing to the point's own
      ``admin`` block.

    ``coast_flag`` is published once the lookup ran.
    """
    fields = [
        (1, "coast_continent", result.get("coast_continent")),
        (2, "coast_country", result.get("coast_country")),
        (3, "coast_admin_1", result.get("coast_admin_1")),
        (ATTRIBUTE_LEVEL, "coast_country_code", result.get("coast_country_code")),
        (ATTRIBUTE_LEVEL, "coast_adm1_code", result.get("coast_adm1_code")),
        (ATTRIBUTE_LEVEL, "coast_country_source", result.get("coast_country_source")),
    ]
    chain = [p.strip() for p in (result.get("coast_sea_chain") or "").split(";") if p.strip()]
    if chain:
        fields.append((ATTRIBUTE_LEVEL, "coast_ocean", chain[-1]))
        if len(chain) > 1:
            fields.append((ATTRIBUTE_LEVEL, "coast_sea", published_sea({"sea": result.get("coast_sea")}, marine)))
    fields += [
        (ATTRIBUTE_LEVEL, "coast_mrgid", result.get("coast_mrgid")),
        (ATTRIBUTE_LEVEL, "coast_landmass_name", result.get("coast_landmass_name")),
        (ATTRIBUTE_LEVEL, "coast_flag", result.get("coast_flag")),
        (ATTRIBUTE_LEVEL, "coast_distance_km", result.get("coast_distance_km")),
        (ATTRIBUTE_LEVEL, "coast_landmass_area_km2", result.get("coast_landmass_area_km2")),
    ]
    return [
        row
        for level, key, value in fields
        if (row := _hierarchy_row(lat, lon, resolution, "coast", level, key, value))
    ]


def _country_rows(lat: float, lon: float, resolution: str, result: dict) -> list[dict]:
    """Truncated admin hierarchy for a country centroid: continent, country
    and country_code only. Anything finer would claim a precision the
    centroid does not have."""
    fields = [
        (1, "continent", result.get("continent")),
        (2, "country", result.get("country")),
        (ATTRIBUTE_LEVEL, "country_code", result.get("country_code")),
    ]
    return [
        row
        for level, key, value in fields
        if (row := _hierarchy_row(lat, lon, resolution, "admin", level, key, value))
    ]


def _continent_rows(lat: float, lon: float, resolution: str, result: dict) -> list[dict]:
    """Truncated admin hierarchy for a continent-centroid coordinate: the
    continent and nothing else."""
    row = _hierarchy_row(lat, lon, resolution, "admin", 1, "continent", result.get("continent"))
    return [row] if row is not None else []


def hierarchy_rows_for(
    lat: float, lon: float, resolution: str, result: dict, marine=None
) -> list[dict]:
    """
    All the rows one (coordinate, resolution) block contributes.

    Resolution decides how much of the result is published: everything for a
    full-hierarchy resolution, a truncated block for "country"/"continent",
    nothing for anything else ("unknown", "in orbit"). No resolution (None or
    blank) means the caller does not model one; the full hierarchy is
    published.
    """
    if resolution == "country":
        return _country_rows(lat, lon, resolution, result)
    if resolution == "continent":
        return _continent_rows(lat, lon, resolution, result)
    if resolution and resolution not in FULL_HIERARCHY_RESOLUTIONS:
        return []
    if result.get("type") == "land":
        return _admin_rows(lat, lon, resolution, result) + _coast_rows(lat, lon, resolution, result, marine)
    if result.get("type") == "sea":
        return (
            _marine_rows(lat, lon, resolution, result, marine)
            + _longhurst_rows(lat, lon, resolution, result)
            + _coast_rows(lat, lon, resolution, result)
        )
    return []


def hierarchy_sort_key(row: dict):
    """Coordinate, resolution, scheme, level (numbered levels before
    attributes), key -- a stable order so reruns diff as no-ops."""
    level = row["level"]
    level_rank = (1, 0) if level == ATTRIBUTE_LEVEL else (0, level)
    return (
        row["latitude"],
        row["longitude"],
        row.get("location_resolution", ""),
        row["scheme"],
        level_rank,
        row["key"],
    )
