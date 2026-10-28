"""The location_hierarchy row builders.

The table's primary key is
``(latitude, longitude, location_resolution, scheme, level, key)``, so no row
may carry an empty ``level`` and no two rows may collide on that key.
"""

import pytest

from metalog_geocoding.hierarchy_table import (
    ATTRIBUTE_LEVEL,
    DERIVED_RESOLUTIONS,
    FULL_HIERARCHY_RESOLUTIONS,
    HIERARCHY_COLUMNS,
    QUALIFYING_RESOLUTIONS,
    hierarchy_rows_for,
    hierarchy_sort_key,
)

LAND = {
    "type": "land",
    "continent": "Europe",
    "country": "Denmark",
    "country_code": "DNK",
    "name_1": "Syddanmark",
    "iso_3166_2": "DK-83",
    "adm1_code": "DNK-3417",
    "continent_political": "Europe",
    "is_fallback": False,
}

KATTEGAT = {
    "type": "sea",
    "sea": "Kattegat",
    "mrgid": 2374,
    "ocean": "Kattegat; Baltic Sea; North Atlantic Ocean; Atlantic Ocean",
    "is_fallback": False,
    "longhurst_biome": "Coastal",
    "longhurst_province": "NE Atlantic Shelves Province",
    "longhurst_code": "NECS",
}


def keyed(rows):
    """The Postgres primary key of each row, as a tuple."""
    return [
        (r["latitude"], r["longitude"], r["location_resolution"], r["scheme"], r["level"], r["key"])
        for r in rows
    ]


def test_attribute_level_is_zero_not_none():
    """A primary-key column cannot be NULL, and levels are numbered from 1, so
    0 marks an attribute."""
    assert ATTRIBUTE_LEVEL == 0


def test_every_row_has_the_table_columns_and_nothing_else():
    rows = hierarchy_rows_for(55.0, 10.0, "site", LAND)
    assert rows
    for row in rows:
        assert set(row) == set(HIERARCHY_COLUMNS)


def test_no_row_ever_carries_an_empty_level():
    for result in (LAND, KATTEGAT):
        for row in hierarchy_rows_for(55.0, 10.0, "site", result):
            assert row["level"] is not None
            assert isinstance(row["level"], int)


def test_a_land_block_is_continent_country_admin1_plus_attributes():
    rows = hierarchy_rows_for(55.0, 10.0, "site", LAND)
    levels = {(r["level"], r["key"]): r["value"] for r in rows if r["scheme"] == "admin"}
    assert levels[(1, "continent")] == "Europe"
    assert levels[(2, "country")] == "Denmark"
    assert levels[(3, "admin_1")] == "Syddanmark"
    assert levels[(ATTRIBUTE_LEVEL, "country_code")] == "DNK"
    assert levels[(ATTRIBUTE_LEVEL, "adm1_code")] == "DNK-3417"
    assert levels[(ATTRIBUTE_LEVEL, "continent_political")] == "Europe"


def test_the_four_deep_kattegat_chain_needs_level_in_the_key():
    """A chain with more than one intermediate hop produces two rows keyed
    ``basin``, distinguishable only by level; without ``level`` in the key
    they would collide."""

    rows = hierarchy_rows_for(55.079, 10.238, "exact_location", KATTEGAT)
    marine = [r for r in rows if r["scheme"] == "marine" and r["level"] > 0]
    assert [(r["level"], r["key"], r["value"]) for r in marine] == [
        (1, "ocean", "Atlantic Ocean"),
        (2, "basin", "North Atlantic Ocean"),
        (3, "basin", "Baltic Sea"),
        (4, "sea", "Kattegat"),
    ]
    basins = [r for r in marine if r["key"] == "basin"]
    assert len({r["level"] for r in basins}) == 2
    assert len(set(keyed(rows))) == len(rows), "primary key collision"


def test_the_chain_is_anchored_at_both_ends_not_numbered_from_the_top():
    """Level 1 is always ``ocean`` and the deepest is always ``sea``. Keying
    positionally from the top would call the Greenland Sea a *basin* in the
    two-deep chain it actually has."""
    rows = hierarchy_rows_for(
        75.0, -5.0, "site",
        {"type": "sea", "sea": "Greenland Sea", "mrgid": 2356,
         "ocean": "Greenland Sea; Arctic Ocean"},
    )
    marine = [r for r in rows if r["scheme"] == "marine" and r["level"] > 0]
    assert [(r["level"], r["key"]) for r in marine] == [(1, "ocean"), (2, "sea")]


def test_a_one_deep_chain_gets_one_row_not_two_names_for_one_fact():
    """Level 1 and level n at once. Emitting a second ``sea`` row for the same
    level would be the same fact twice under two names."""
    rows = hierarchy_rows_for(
        0.0, -30.0, "site",
        {"type": "sea", "sea": "Atlantic Ocean", "ocean": "Atlantic Ocean"},
    )
    marine = [r for r in rows if r["scheme"] == "marine" and r["level"] > 0]
    assert [(r["level"], r["key"], r["value"]) for r in marine] == [
        (1, "ocean", "Atlantic Ocean")
    ]


def test_longhurst_is_its_own_scheme_not_a_marine_level():
    """It classifies the same water biogeochemically, not politically -- they
    are separate trees, not two flavours of one tree."""
    rows = hierarchy_rows_for(55.079, 10.238, "site", KATTEGAT)
    schemes = {r["scheme"] for r in rows}
    assert schemes == {"marine", "longhurst"}
    longhurst = {(r["level"], r["key"]): r["value"] for r in rows if r["scheme"] == "longhurst"}
    assert longhurst[(1, "biome")] == "Coastal"
    assert longhurst[(2, "province")] == "NE Atlantic Shelves Province"
    assert longhurst[(ATTRIBUTE_LEVEL, "longhurst_code")] == "NECS"


def test_a_sea_with_no_longhurst_province_still_gets_its_marine_rows():
    """A valid IHO sea can have no province -- the Caspian, a river station,
    the ice sheet. That must not suppress the marine hierarchy."""
    rows = hierarchy_rows_for(
        41.0, 51.0, "site",
        {"type": "sea", "sea": "Caspian Sea", "ocean": "Caspian Sea"},
    )
    assert {r["scheme"] for r in rows} == {"marine"}


def test_distance_to_coast_is_its_own_scheme_of_attributes():
    """GSHHG is a fourth source, not a marine level: attributes only, so the
    tree builder (level > 0) never sees it."""
    sea = {**KATTEGAT, "coast_flag": "ok", "coast_distance_km": 12.3, "coast_landmass_area_km2": 50700000.0}
    rows = hierarchy_rows_for(55.079, 10.238, "site", sea)
    coast = {(r["level"], r["key"]): r["value"] for r in rows if r["scheme"] == "coast"}
    assert coast == {
        (ATTRIBUTE_LEVEL, "coast_flag"): "ok",
        (ATTRIBUTE_LEVEL, "coast_distance_km"): "12.3",
        (ATTRIBUTE_LEVEL, "coast_landmass_area_km2"): "50700000.0",
    }


def test_the_coast_place_is_a_tree_under_its_own_keys():
    """Same vocabulary as admin, so the values join to land samples; own keys,
    so a key-only query for country = Germany never returns water."""
    sea = {**KATTEGAT, "coast_flag": "ok", "coast_distance_km": 5.5,
           "coast_continent": "Europe", "coast_country": "Germany",
           "coast_admin_1": "Mecklenburg-Vorpommern", "coast_country_code": "DEU",
           "coast_landmass_name": "Rügen Island", "coast_country_source": "natural_earth"}
    rows = [r for r in hierarchy_rows_for(54.44, 13.73, "site", sea) if r["scheme"] == "coast"]
    tree = [(r["level"], r["key"], r["value"]) for r in rows if r["level"] > 0]
    assert tree == [(1, "coast_continent", "Europe"), (2, "coast_country", "Germany"),
                    (3, "coast_admin_1", "Mecklenburg-Vorpommern")]
    assert not {r["key"] for r in rows} & {"country", "admin_1", "continent"}


def test_a_land_point_names_the_water_its_coast_faces():
    """Signed distance, and the sea and ocean of the nearest coast -- not its
    country. ``coast_sea`` is published in the vocabulary's spelling."""
    land = {"type": "land", "continent": "Europe", "country": "Norway",
            "coast_flag": "ok", "coast_distance_km": -12.0,
            "coast_sea": "Barentsz Sea", "coast_mrgid": 2399,
            "coast_sea_chain": "Barentsz Sea; Arctic Ocean"}

    class Vocab:
        def canonical(self, name):
            return {"Barentsz Sea": "Barents Sea"}.get(name)

    rows = hierarchy_rows_for(69.7, 30.0, "site", land, Vocab())
    coast = {(r["level"], r["key"]): r["value"] for r in rows if r["scheme"] == "coast"}
    assert coast == {
        (ATTRIBUTE_LEVEL, "coast_flag"): "ok",
        (ATTRIBUTE_LEVEL, "coast_distance_km"): "-12.0",
        (ATTRIBUTE_LEVEL, "coast_ocean"): "Arctic Ocean",
        (ATTRIBUTE_LEVEL, "coast_sea"): "Barents Sea",
        (ATTRIBUTE_LEVEL, "coast_mrgid"): "2399",
    }


def test_a_coast_facing_an_ocean_polygon_has_no_separate_sea():
    land = {"type": "land", "coast_flag": "ok", "coast_distance_km": -1.0,
            "coast_sea": "Arctic Ocean", "coast_mrgid": 1906, "coast_sea_chain": "Arctic Ocean"}
    keys = {r["key"] for r in hierarchy_rows_for(80.0, 20.0, "site", land) if r["scheme"] == "coast"}
    assert "coast_ocean" in keys and "coast_sea" not in keys


def test_a_country_centroid_never_earns_a_distance_to_coast():
    sea = {**KATTEGAT, "coast_flag": "ok", "coast_distance_km": 1.0, "coast_landmass_area_km2": 1.0}
    assert hierarchy_rows_for(55.079, 10.238, "country", sea) == []


def test_country_resolution_is_truncated_to_continent_country_and_code():
    """At ``country`` the coordinate is a population-weighted country centre,
    not a place a sample was taken -- so no admin-1, no marine, no Longhurst."""
    rows = hierarchy_rows_for(56.0, 10.0, "country", LAND)
    assert [(r["level"], r["key"]) for r in rows] == [
        (1, "continent"),
        (2, "country"),
        (ATTRIBUTE_LEVEL, "country_code"),
    ]


def test_continent_resolution_is_truncated_to_the_continent_alone():
    rows = hierarchy_rows_for(50.0, 10.0, "continent", LAND)
    assert [(r["level"], r["key"], r["value"]) for r in rows] == [(1, "continent", "Europe")]


def test_a_country_centroid_never_earns_a_marine_hierarchy():
    """Truncation is by resolution, so a country centre that happens to geocode
    to water still publishes nothing marine -- it would claim a precision the
    centroid does not have."""
    assert hierarchy_rows_for(55.079, 10.238, "country", KATTEGAT) == []


@pytest.mark.parametrize("resolution", ["unknown", "in orbit"])
def test_a_non_qualifying_resolution_publishes_nothing(resolution):
    assert hierarchy_rows_for(55.0, 10.0, resolution, LAND) == []
    assert resolution not in QUALIFYING_RESOLUTIONS


def test_an_unresolved_coordinate_publishes_nothing():
    assert hierarchy_rows_for(55.0, 10.0, "site", {}) == []
    assert hierarchy_rows_for(55.0, 10.0, "site", {"type": "land"}) == []


def test_the_resolution_vocabulary_is_partitioned():
    """Full-hierarchy and truncated resolutions must not overlap, or a block
    would be built two ways depending on which test ran first."""
    assert not (FULL_HIERARCHY_RESOLUTIONS & DERIVED_RESOLUTIONS)
    assert QUALIFYING_RESOLUTIONS == FULL_HIERARCHY_RESOLUTIONS | DERIVED_RESOLUTIONS


def test_the_sort_puts_numbered_levels_before_attributes():
    """An attribute describes a level rather than extending it, so it sorts
    after -- and the order has to be total, or two runs would not diff as
    no-ops."""
    rows = hierarchy_rows_for(55.079, 10.238, "exact_location", KATTEGAT)
    ordered = sorted(rows, key=hierarchy_sort_key)
    marine = [r for r in ordered if r["scheme"] == "marine"]
    levels = [r["level"] for r in marine]
    assert levels == [1, 2, 3, 4, ATTRIBUTE_LEVEL, ATTRIBUTE_LEVEL]


def test_sorting_is_stable_across_runs():
    rows = hierarchy_rows_for(55.079, 10.238, "exact_location", KATTEGAT)
    first = [hierarchy_sort_key(r) for r in sorted(rows, key=hierarchy_sort_key)]
    second = [hierarchy_sort_key(r) for r in sorted(reversed(rows), key=hierarchy_sort_key)]
    assert first == second


def test_the_published_spelling_is_used_for_the_deepest_element_only():
    """``mrgid`` stays the identity and the intermediate chain stays in the
    gazetteer's spelling; only the name a database has a foreign key on is
    rewritten."""
    from metalog_geocoding.vocabulary import MarineVocabulary

    marine = MarineVocabulary({"Barents Sea"}, {"Barentsz Sea": "Barents Sea"})
    rows = hierarchy_rows_for(
        75.0, 40.0, "site",
        {"type": "sea", "sea": "Barentsz Sea", "mrgid": 2374,
         "ocean": "Barentsz Sea; Arctic Ocean"},
        marine,
    )
    values = {(r["level"], r["key"]): r["value"] for r in rows if r["scheme"] == "marine"}
    assert values[(2, "sea")] == "Barents Sea"
    assert values[(1, "ocean")] == "Arctic Ocean"
    assert values[(ATTRIBUTE_LEVEL, "mrgid")] == "2374"


# -- location_resolution is optional ----------------------------------------

@pytest.mark.parametrize("resolution", [None, ""])
def test_no_resolution_publishes_the_full_hierarchy_without_the_column(resolution):
    """A caller with no resolution model gets everything an observed coordinate
    gets, and no ``location_resolution`` key it never sent."""
    for result in (LAND, KATTEGAT):
        rows = hierarchy_rows_for(1.0, 2.0, resolution, result)
        assert rows
        assert all("location_resolution" not in r for r in rows)
        full = hierarchy_rows_for(1.0, 2.0, "site", result)
        strip = [{k: v for k, v in r.items() if k != "location_resolution"} for r in full]
        assert rows == strip
    sorted(rows, key=hierarchy_sort_key)  # sortable without the column

