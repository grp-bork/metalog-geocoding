"""End-to-end tests against the real boundary layers.

They need the ``[geo]`` extra and the parquet files and skip without them.
The geocoder starts with an empty result cache (see ``conftest.py``), so every
answer is computed from the polygons.
"""

import pytest

from metalog_geocoding.hierarchy_table import ATTRIBUTE_LEVEL, hierarchy_rows_for
from metalog_geocoding.location_exceptions import ACCEPTED_PREFIX
from metalog_geocoding.samples import (
    CODE_CLAIM_TOO_FAR,
    CODE_DIFFERENT_LOCATION,
    CODE_NOT_ON_LAND,
    CODE_OK,
    CODE_UNKNOWN_PLACE,
    Sample,
)

pytestmark = pytest.mark.geo


# -- land -------------------------------------------------------------------

def test_a_land_point_resolves_to_continent_country_and_admin_1(geocoder):
    result = geocoder.geocode(52.52, 13.405, "land")
    assert result["type"] == "land"
    assert result["country"] == "Germany"
    assert result["country_code"] == "DEU"
    assert result["continent"] == "Europe"
    assert result["name_1"] == "Berlin"
    assert result["iso_3166_2"] == "DE-BE"
    assert result["adm1_code"] == "DEU-1599"
    assert result["is_fallback"] is False


def test_only_valparaisos_polynesian_islands_are_oceania(geocoder):
    """Natural Earth's Valparaíso region spans Easter Island and the mainland
    city; only the part west of 100°W is Oceania."""
    easter_island = geocoder.geocode(-27.12, -109.35, "land")
    city = geocoder.geocode(-33.05, -71.6, "land")
    juan_fernandez = geocoder.geocode(-33.64, -78.83, "land")

    assert easter_island["iso_3166_2"] == city["iso_3166_2"] == juan_fernandez["iso_3166_2"] == "CL-VS"
    assert easter_island["continent"] == "Oceania"
    assert city["continent"] == "South America"
    assert juan_fernandez["continent"] == "South America"


def test_the_two_continents_disagree_where_they_should(geocoder):
    """Sakha is geographically Asia and politically Europe; Hawaii is
    geographically Oceania and politically North America."""
    sakha = geocoder.geocode(66.0, 129.0, "land")
    assert (sakha["continent"], sakha["continent_political"]) == ("Asia", "Europe")

    hawaii = geocoder.geocode(19.39, -155.25, "land")
    assert (hawaii["continent"], hawaii["continent_political"]) == ("Oceania", "North America")


def test_a_country_name_is_published_in_its_common_form(geocoder):
    """``NAME_LONG``, not the formal ``ADMIN``: "Russia", not "Russian
    Federation"; "United States", not "United States of America"."""
    assert geocoder.geocode(66.0, 129.0, "land")["country"] == "Russia"
    assert geocoder.geocode(19.39, -155.25, "land")["country"] == "United States"


def test_a_point_in_open_ocean_is_not_on_land_at_all(geocoder):
    """Past the fallback threshold there is no nearest-land answer to give, and
    inventing one would launder a bad coordinate into a confident wrong one."""
    assert geocoder.geocode(0.0, -140.0, "land")["type"] == "unknown"


def test_land_found_nearest_is_reported_even_when_the_nudge_misses(geocoder):
    """Mid-channel in a North Carolina estuary, 1,391 m from shore: the point
    nudged 100 m past the shoreline lands back in the water, so the answer is
    read off the polygon that was measured.

    No ``name_1``: the nearest *country* is a measurement, the subdivision
    would be a guess.
    """
    result = geocoder.geocode(34.640199, -77.109447, "land")
    assert result["type"] == "land"
    assert result["country"] == "United States"
    assert result["is_fallback"] is True
    assert round(result["fallback_distance_m"]) == 1391
    assert "name_1" not in result


def test_best_skips_the_sea_lookup_after_a_direct_land_hit(geocoder, monkeypatch):
    """A direct land hit wins whatever the sea says, and the sea lookup of an
    inland point (nearest-sea search) is the slow part of a "best" run."""
    def no_sea(lat, lon, location_type, _geocode=geocoder.geocode):
        assert location_type != "sea"
        return _geocode(lat, lon, location_type)

    monkeypatch.setattr(geocoder, "geocode", no_sea)
    result = geocoder.geocode_best(52.52, 13.405)
    assert result["type"] == "land"
    assert result["is_fallback"] is False


# -- sea --------------------------------------------------------------------

def test_a_sea_point_resolves_to_a_chain_an_mrgid_and_a_province(geocoder):
    result = geocoder.geocode(-34.5, 132.0, "sea")
    assert result["type"] == "sea"
    assert result["sea"] == "Great Australian Bight"
    assert result["mrgid"] == 4276
    assert result["ocean"] == "Great Australian Bight; Indian Ocean"
    assert result["longhurst_code"] == "SSTC"
    assert result["longhurst_biome"] == "Westerlies"


def test_the_four_deep_kattegat_chain_is_real_data_not_a_fixture(geocoder):
    """Two intermediate hops mean two rows keyed ``basin``, distinguishable
    only by ``level`` -- which is why ``level`` is in the primary key."""
    result = geocoder.geocode(56.7, 11.6, "sea")
    assert result["ocean"] == "Kattegat; Baltic Sea; North Atlantic Ocean; Atlantic Ocean"
    assert len(result["ocean"].split(";")) == 4


def test_the_smallest_containing_sea_wins(geocoder):
    """Several IHO polygons contain a point in the Kattegat -- the Baltic and
    the Atlantic among them. The most specific one is the answer."""
    assert geocoder.geocode(56.7, 11.6, "sea")["sea"] == "Kattegat"


def test_lakes_ladoga_and_onega_are_not_the_gulf_of_finland(geocoder):
    """IHO v3's Gulf of Finland runs up the Neva into both lakes; ``sea.py``
    cuts it at St Petersburg. Neva Bay, west of the cut, keeps its gulf."""
    seas = geocoder.sea_geocoder
    assert not seas.contains(60.8, 31.5)  # Ladoga
    assert not seas.contains(61.7, 35.5)  # Onega
    assert seas.query(59.95, 29.9)["sea"] == "Gulf of Finland"


# -- the closed network dependency -----------------------------------------

def test_every_iho_polygon_has_a_bundled_hierarchy(geocoder):
    """The MRGIDs the geocoder can return are a closed set, so the hierarchy
    shipped in the package must cover them all; otherwise a sea lookup reaches
    MarineRegions at runtime."""
    layer_mrgids = {int(m) for m in geocoder.sea_geocoder.seas["MRGID"]}
    cached = set(geocoder.sea_geocoder.hierarchy_cache.cache)
    assert layer_mrgids - cached == set(), "these MRGIDs would trigger a live API call"


def test_the_iho_layer_has_one_mrgid_per_polygon(geocoder):
    """101 polygons, 101 distinct MRGIDs. A repeated MRGID would make the
    hierarchy cache ambiguous."""
    mrgids = [int(m) for m in geocoder.sea_geocoder.seas["MRGID"]]
    assert len(mrgids) == len(set(mrgids))


# -- grading ----------------------------------------------------------------

def check(checker, lat, lon, location, resolution="site", artificial=""):
    return checker.check(
        Sample(
            id="t",
            latitude=str(lat),
            longitude=str(lon),
            geographic_location=location,
            location_resolution=resolution,
            artificial=artificial,
        )
    )


def test_a_coordinate_agreeing_with_its_country_is_ok(checker):
    assert check(checker, 52.52, 13.405, "Germany").grade == "ok"


def test_a_different_country_on_a_direct_hit_is_an_error(checker):
    assert check(checker, 52.52, 13.405, "France").grade == "error"


def test_a_border_within_the_tolerance_is_forgiven(checker):
    """A point in the St. Lawrence, inside Canada and 277 m from the United
    States. A border has its own error; at that range the sample disagrees
    with the boundary, not with the place."""
    assert check(checker, 45.0061, -74.7949, "United States").grade == "ok"


def test_a_border_between_one_and_two_kilometres_is_forgiven(checker):
    """A point in the Swiss Rhine valley 1,132 m from Liechtenstein: inside
    ``CLAIM_BORDER_M`` (2 km)."""
    assert check(checker, 47.186377, 9.500624, "Switzerland").grade == "ok"


def test_a_different_country_well_past_the_border_stays_an_error(checker):
    """A Botswana claim 13,960 m inside Zimbabwe stays an error: well past the
    2 km border tolerance."""
    assert check(checker, -18.07174, 25.4991, "Botswana").grade == "error"


def test_a_verified_coordinate_is_cleared_by_the_exceptions_file(
    checker, checker_with_exceptions
):
    """A ~1 km² mangrove cay in Belize that Natural Earth 10m has no polygon
    for, so the nearest land is 15.1 km away.

    Asserted against both checkers: the rule must still fire underneath, or
    the exception would be stale.
    """
    assert check(checker, 16.82383, -88.10582, "Belize").grade == "error"

    verdict = check(checker_with_exceptions, 16.82383, -88.10582, "Belize")
    assert verdict.grade == "ok"
    assert verdict.reason.startswith(ACCEPTED_PREFIX)
    assert "Twin Cays" in verdict.reason


def test_an_exception_does_not_forgive_a_different_claim(checker_with_exceptions):
    """The entry says Twin Cays is in Belize. It says nothing about a sample at
    the same point claiming Mexico, which stays an error."""
    assert check(checker_with_exceptions, 16.82383, -88.10582, "Mexico").grade == "error"


def test_an_exception_cannot_reach_an_impossible_coordinate(checker_with_exceptions):
    """Applied after grading, and ``check`` rejects an off-globe coordinate
    before grading runs -- so no entry can ever forgive one."""
    verdict = check(checker_with_exceptions, -176.62, 12.0, "Germany")
    assert verdict.grade == "error"
    assert "outside -90..90" in verdict.reason


def test_an_impossible_coordinate_is_rejected_before_the_geocoder(checker):
    """Checked first, because pyproj returns NaN off the globe -- otherwise
    this yields a plausible country with a NaN distance."""
    verdict = check(checker, -176.62, 12.0, "Germany")
    assert verdict.grade == "error"
    assert "outside -90..90" in verdict.reason


def test_a_country_resolution_is_uncheckable(checker):
    """The coordinate was derived from the place name, so it cannot contradict
    it."""
    verdict = check(checker, 51.0, 9.0, "Germany", resolution="country")
    assert verdict.grade == "uncheckable"


def test_a_sample_with_no_coordinates_is_graded_as_such(checker):
    assert checker.check(Sample(id="t", latitude="", longitude="")).grade == "no coordinates"


def test_land_claimed_where_there_is_only_open_ocean_is_an_error(checker):
    verdict = check(checker, 0.0, -140.0, "Germany")
    assert verdict.grade == "error"
    assert verdict.code == CODE_NOT_ON_LAND
    # The code is what a report groups by; the reason names the stated value.
    assert "Germany" in verdict.reason
    assert "not on land" in verdict.reason and "10 km" in verdict.reason


def test_a_direct_hit_error_names_both_places_and_the_distance(checker):
    """A Botswana claim 13,960 m inside Zimbabwe: the reason says which two
    places disagree and by how far."""
    verdict = check(checker, -18.07174, 25.4991, "Botswana")
    assert verdict.code == CODE_DIFFERENT_LOCATION
    assert "Botswana" in verdict.reason
    assert "Zimbabwe" in verdict.reason
    assert "14 km" in verdict.reason


def test_a_name_natural_earth_cannot_place_has_its_own_code(checker):
    """An unknown name is a problem with the name, not the coordinate, so it
    must not read like a known place at the wrong spot."""
    verdict = check(checker, 40.0, -100.0, "Atlantis")
    assert verdict.code == CODE_UNKNOWN_PLACE
    assert "Atlantis" in verdict.reason and "United States" in verdict.reason


def test_an_unknown_name_fails_on_either_side_of_the_coastline(checker):
    """Iriomote (an island of Japan): one point inside Natural Earth's Japan,
    one 1 km off its coastline. The name is unknown either way, so the verdict
    must not depend on the shoreline fallback."""
    assert check(checker, 24.3, 123.8, "Iriomote").code == CODE_UNKNOWN_PLACE
    offshore = check(checker, 24.407, 123.799, "Iriomote")
    assert offshore.result["is_fallback"]
    assert offshore.code == CODE_UNKNOWN_PLACE


def test_a_territory_is_checked_against_its_own_polygon(checker):
    """Svalbard is inside Natural Earth's Norway unit, but a Svalbard sample
    near Oslo is a wrong coordinate, and one in the US is a known name far
    away, not an unknown one."""
    assert check(checker, 78.2, 15.6, "Svalbard").code == CODE_OK
    near_oslo = check(checker, 60.0, 10.0, "Svalbard")
    assert near_oslo.code == CODE_DIFFERENT_LOCATION
    assert "1,650 km" in near_oslo.reason
    assert check(checker, 40.0, -100.0, "Svalbard").code == CODE_DIFFERENT_LOCATION
    assert check(checker, 48.8, 2.3, "French Guiana").code == CODE_DIFFERENT_LOCATION
    assert check(checker, 4.9, -52.3, "French Guiana").code == CODE_OK


def test_the_two_indian_ocean_territories_are_told_apart(checker):
    """One Natural Earth unit covers both islands, ~1,000 km apart."""
    assert check(checker, -12.18, 96.83, "Christmas Island").code == CODE_CLAIM_TOO_FAR
    assert check(checker, -12.18, 96.83, "Cocos Islands").code == CODE_OK


def test_common_spellings_match_natural_earth(checker):
    assert check(checker, 62.07, -7.31, "Faroe Islands").code == CODE_OK
    assert check(checker, 37.5, 127.0, "Korea").code == CODE_OK


def test_a_far_claim_on_a_fallback_names_both_distances(checker):
    """A Red Sea coordinate labelled Australia. The two distances answer
    different questions -- how imprecise the coordinate is (a short hop to the
    Saudi shore) and how wrong the name is (9,000 km) -- so both are reported."""
    verdict = check(checker, 22.3, 39.0, "Australia")
    assert verdict.code == CODE_CLAIM_TOO_FAR
    assert "Australia" in verdict.reason
    assert "Saudi Arabia" in verdict.reason


def test_a_claimed_ancestor_sea_is_accepted(checker, vocabulary):
    """A claim more specific than the polygon the point landed in is fine."""
    if vocabulary is None or vocabulary.canonical("Chukchi Sea") is None:
        pytest.skip("the vocabulary does not carry Chukchi Sea")
    # 72 N 168 W geocodes to the Arctic Ocean itself; Chukchi Sea is its child.
    assert check(checker, 72.0, -168.0, "Chukchi Sea").grade == "ok"


def test_a_sibling_sea_five_thousand_kilometres_away_is_rejected(checker, vocabulary):
    """Sharing an ancestor is not being one."""
    if vocabulary is None or vocabulary.canonical("Arabian Sea") is None:
        pytest.skip("the vocabulary does not carry Arabian Sea")
    verdict = check(checker, -20.0, 42.0, "Arabian Sea")   # Mozambique Channel
    assert verdict.grade == "error"


# -- the hierarchy, end to end ---------------------------------------------

def test_a_real_sea_coordinate_produces_a_loadable_block(geocoder, vocabulary):
    """Every row keyed, no key collision, no empty level -- the three things
    the Postgres primary key requires of this table."""
    result = geocoder.geocode(56.7, 11.6, "sea")
    rows = hierarchy_rows_for(56.7, 11.6, "exact_location", result, vocabulary)

    keys = [
        (r["latitude"], r["longitude"], r["location_resolution"], r["scheme"], r["level"], r["key"])
        for r in rows
    ]
    assert len(keys) == len(set(keys)), "primary key collision in a real block"
    assert all(isinstance(r["level"], int) for r in rows)

    marine = [r for r in rows if r["scheme"] == "marine" and r["level"] > 0]
    assert [(r["level"], r["key"]) for r in marine] == [
        (1, "ocean"), (2, "basin"), (3, "basin"), (4, "sea")
    ]
    assert marine[-1]["value"] == "Kattegat"


def test_a_real_land_coordinate_produces_a_loadable_block(geocoder):
    result = geocoder.geocode(52.52, 13.405, "land")
    rows = hierarchy_rows_for(52.52, 13.405, "site", result)
    by_key = {(r["level"], r["key"]): r["value"] for r in rows}
    assert by_key[(1, "continent")] == "Europe"
    assert by_key[(2, "country")] == "Germany"
    assert by_key[(3, "admin_1")] == "Berlin"
    assert by_key[(ATTRIBUTE_LEVEL, "country_code")] == "DEU"


def test_the_natural_earth_assertions_hold_for_the_shipped_layer(geocoder):
    """``NaturalEarthLandGeocoder.__init__`` asserts the "Seven seas" unit set,
    the Russian federal-subject coverage and the country-name abbreviations
    still match its tables; constructing the geocoder is the test."""
    assert geocoder.land_geocoder is not None


def test_a_block_claimed_as_both_land_and_sea_publishes_each_coast_key_once(checker):
    """A point labelled with the sea by one sample and the country by another
    is built from both results. Both publish the ``coast`` scheme; a
    duplicated key would violate the primary key."""
    samples = [
        Sample(id=i, latitude="48.5925", longitude="-123.4867",
               geographic_location=place, location_resolution="exact_location")
        for i, place in (("s", "Pacific Ocean"), ("l", "Canada"))
    ]
    rows = checker.check_batch(samples).hierarchy
    keys = [(r["scheme"], r["level"], r["key"]) for r in rows]
    assert len(keys) == len(set(keys))
    coast = {r["key"] for r in rows if r["scheme"] == "coast"}
    assert {"coast_country", "coast_ocean", "coast_distance_km"} <= coast
    assert {r["scheme"] for r in rows} >= {"admin", "marine"}


def test_a_batch_without_resolutions_gets_full_blocks_without_the_column(checker):
    """``location_resolution`` is optional end to end: unset, the coordinate is
    taken as observed and the rows do not carry the column."""
    batch = checker.check_batch([
        Sample(id="a", latitude="52.52", longitude="13.405", geographic_location="Germany"),
    ])
    assert batch.verdicts[0].grade == "ok"
    assert {r["key"] for r in batch.hierarchy} >= {"continent", "country", "admin_1"}
    assert all("location_resolution" not in r for r in batch.hierarchy)


# -- geocode_best: which of the land and sea results is kept ------------------


def test_best_prefers_a_direct_land_hit(geocoder):
    result = geocoder.geocode_best(52.52, 13.405)
    assert result["type"] == "land" and not result["is_fallback"]
    assert result["country"] == "Germany"


def test_best_takes_the_sea_in_open_water(geocoder):
    result = geocoder.geocode_best(0.0, -140.0)
    assert result["type"] == "sea" and not result["is_fallback"]


def test_best_takes_the_nearer_fallback_near_shore(geocoder):
    # Venice lagoon: inside neither polygon, so both layers answer by
    # fallback, and the nearer one (the sea, 137 m) is kept.
    lat, lon = 45.418, 12.382
    land = geocoder.geocode(lat, lon, "land")
    sea = geocoder.geocode(lat, lon, "sea")
    assert land["is_fallback"] and sea["is_fallback"]
    assert sea["fallback_distance_m"] < land["fallback_distance_m"]
    best = geocoder.geocode_best(lat, lon)
    # (not ``best == sea``: a cache hit returns mrgid as str, a fresh lookup as int)
    assert best["type"] == "sea"
    assert best["fallback_distance_m"] == sea["fallback_distance_m"]


def test_best_of_nothing_is_unknown(geocoder):
    # Every point on Earth is near land or sea, so "unknown" is unreachable with
    # the real layers; confirm open ocean does not produce it.

    assert geocoder.geocode_best(0.0, -140.0)["type"] != "unknown"
