"""``Sample``, ``coordinate_error`` and the dedup a batch request is built on."""

import pytest

from metalog_geocoding.samples import (
    CODE_DIFFERENT_LOCATION,
    ERROR_CODES,
    NO_COORDINATES,
    Sample,
    Verdict,
    coordinate_error,
    distinct_coordinates,
)


def test_coordinates_are_kept_as_recorded():
    """Reports echo the value as written, not a round-tripped float."""
    sample = Sample(id="S1", latitude="55.0790", longitude="10.2380")
    assert sample.latitude == "55.0790"
    assert sample.coordinate == (55.079, 10.238)


@pytest.mark.parametrize("lat,lon", [("", ""), (None, None), ("n/a", "10.0"), ("", "10.0")])
def test_every_way_a_coordinate_can_be_missing_gives_none(lat, lon):
    """Absent, blank and unparseable all mean the same thing to callers."""
    assert Sample(id="S1", latitude=lat, longitude=lon).coordinate is None


def test_from_mapping_reads_a_pivoted_export_row():
    sample = Sample.from_mapping(
        {
            "latitude": "-34.5",
            "longitude": "132.0",
            "geographic_location": "Great Australian Bight",
            "location_resolution": "site",
            "artificial": "",
            "unrelated": "ignored",
        },
        id="S9",
    )
    assert sample.id == "S9"
    assert sample.coordinate == (-34.5, 132.0)
    assert sample.geographic_location == "Great Australian Bight"


@pytest.mark.parametrize(
    "lat,lon",
    [(91.0, 0.0), (-90.001, 0.0), (0.0, 180.5), (0.0, -181.0)],
)
def test_impossible_coordinates_are_rejected_before_the_geocoder(lat, lon):
    """pyproj returns NaN off the globe, so an impossible coordinate would
    otherwise yield a plausible country with a NaN distance."""
    assert coordinate_error(lat, lon) is not None


@pytest.mark.parametrize("lat,lon", [(90.0, 180.0), (-90.0, -180.0), (0.0, 0.0)])
def test_the_edges_of_the_globe_are_valid(lat, lon):
    assert coordinate_error(lat, lon) is None


def test_a_swapped_pair_is_named_as_such():
    """A lat/lon swap is the commonest cause of an out-of-range latitude, so
    the message suggests it."""
    message = coordinate_error(-176.62, 12.0)
    assert message is not None
    assert "swapped" in message


def test_distinct_coordinates_dedups_and_keeps_first_seen_order():
    """The request body is cut by this one function on both sides of the wire."""
    samples = [
        Sample(id="a", latitude="1.0", longitude="2.0"),
        Sample(id="b", latitude="3.0", longitude="4.0"),
        Sample(id="c", latitude="1.0", longitude="2.0"),
    ]
    assert distinct_coordinates(samples) == [(1.0, 2.0), (3.0, 4.0)]


def test_distinct_coordinates_drops_the_ungeocodable():
    """Absent and impossible coordinates are graded from the coordinate alone
    and never reach the geocoder, so they must not enlarge the batch."""
    samples = [
        Sample(id="a", latitude="1.0", longitude="2.0"),
        Sample(id="b", latitude="", longitude=""),
        Sample(id="c", latitude="91.0", longitude="0.0"),
    ]
    assert distinct_coordinates(samples) == [(1.0, 2.0)]


def test_the_no_coordinates_grade_is_a_shared_constant():
    """Callers match on this string; two spellings would be two grades."""
    assert NO_COORDINATES == "no coordinates"


def test_every_error_code_is_distinct_and_named():
    """``code`` is what reports group by and what a fail-closed importer
    branches on. Two codes sharing a string would silently merge two rules."""
    assert len(set(ERROR_CODES)) == len(ERROR_CODES)
    assert all(code and code.replace("_", "").isalnum() for code in ERROR_CODES)


def test_a_verdict_carries_both_halves_independently():
    """``reason`` is specific to the sample while ``code`` stays comparable. A
    verdict without a code parses with an empty one rather than failing."""
    verdict = Verdict(
        sample_id="a",
        grade="error",
        reason="the sample states 'Botswana', but the coordinate is inside 'Zimbabwe'",
        code=CODE_DIFFERENT_LOCATION,
    )
    assert verdict.code == "different_location"
    assert "Zimbabwe" in verdict.reason
    assert Verdict(sample_id="a", grade="error", reason="whatever").code == ""


def test_an_impossible_coordinate_says_what_is_wrong_with_it():
    """The message names the value, the range it broke, and the likeliest
    mistake."""

    message = coordinate_error(95.0, 9.0)
    assert "95.0" in message and "-90..90" in message and "swapped" in message
