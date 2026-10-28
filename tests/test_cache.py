"""The result cache: keys, storage, the TSV import, and the type contract.

A defect here is silent: the file looks healthy while the answers drift.
"""

from decimal import Decimal

import pytest

from metalog_geocoding.cache import DEFAULT_DECIMALS, GeoCache, round_coord


def test_the_default_is_not_to_round():
    """Keys are the coordinate exactly as given: rounding to 2 dp (~1.1 km) is
    enough to cross a shoreline and change the answer."""
    assert DEFAULT_DECIMALS is None
    assert round_coord(38.028333, -122.123456) == (38.028333, -122.123456)


def test_decimal_and_float_keys_are_the_same_key():
    """psycopg2 hands back ``numeric`` columns as Decimal, and Decimal and
    float of the same value do not hash alike -- so an un-normalised Decimal
    would miss every entry the geocoder itself wrote."""
    assert round_coord(Decimal("52.5"), Decimal("13.4")) == (52.5, 13.4)
    assert hash(round_coord(Decimal("52.5"), Decimal("13.4"))) == hash((52.5, 13.4))


def test_explicit_rounding_still_works_as_an_escape_hatch():
    assert round_coord(38.028333, -122.123456, 2) == (38.03, -122.12)


def test_a_full_precision_coordinate_survives_a_reopen(tmp_path):
    """Keys are stored as REAL, an IEEE double, so a full-precision coordinate
    is found again exactly after the database is closed and reopened."""
    lat, lon = 38.02833333333333, -122.12345678901234
    path = tmp_path / "land_cache.sqlite"

    cache = GeoCache(path)
    cache.add(lat, lon, {"country": "United States", "country_code": "USA"})
    cache.close()

    reloaded = GeoCache(path)
    assert reloaded.lookup(lat, lon) == {
        "country": "United States",
        "country_code": "USA",
    }
    assert reloaded.lookup(38.0283333333333, lon) is None, "no implicit rounding"


def test_tiny_coordinates_round_trip(tmp_path):
    path = tmp_path / "cache.sqlite"
    cache = GeoCache(path)
    cache.add(1e-07, -2.5e-08, {"country": "nowhere"})
    cache.close()
    assert GeoCache(path).lookup(1e-07, -2.5e-08) == {"country": "nowhere"}


def test_an_entry_is_committed_without_save(tmp_path):
    """A worker killed mid-batch (OOM, request timeout) must keep the
    coordinates it finished, so add() commits rather than waiting for save()."""
    path = tmp_path / "cache.sqlite"
    cache = GeoCache(path)
    cache.add(1.0, 2.0, {"country": "Denmark"})
    # A second connection sees it while the first is still open, unsaved.
    assert GeoCache(path).lookup(1.0, 2.0) == {"country": "Denmark"}


def test_add_replaces_the_whole_entry(tmp_path):
    cache = GeoCache(tmp_path / "cache.sqlite")
    cache.add(1.0, 2.0, {"country": "Denmark", "name_1": "Syddanmark"})
    cache.add(1.0, 2.0, {"country": "Sweden"})
    assert cache.lookup(1.0, 2.0) == {"country": "Sweden"}
    assert len(cache) == 1


def test_a_nan_coordinate_is_not_stored(tmp_path):
    """NaN never equals itself, so such an entry could never be found again."""
    cache = GeoCache(tmp_path / "cache.sqlite")
    cache.add(float("nan"), 2.0, {"country": "nowhere"})
    assert len(cache) == 0


def test_types_are_restored_on_read(tmp_path):
    """Everything is stored as strings; one field comes back as a bool, six as
    floats and the MRGIDs as ints, the types a fresh result has. A new numeric
    field missing from those lists comes back as a string."""
    path = tmp_path / "cache.sqlite"
    cache = GeoCache(path)
    cache.add(
        1.0,
        2.0,
        {
            "country": "Denmark",
            "is_fallback": True,
            "fallback_distance_m": 1234.5,
            "original_lat": 1.5,
            "coast_distance_km": 12.345,
            "coast_landmass_area_km2": 83900.0,
            "mrgid": 4276,
            "coast_mrgid": 2350,
            "longhurst_code": "NADR",
        },
    )
    cache.close()

    got = GeoCache(path).lookup(1.0, 2.0)
    assert got["is_fallback"] is True
    assert got["fallback_distance_m"] == pytest.approx(1234.5)
    assert got["original_lat"] == pytest.approx(1.5)
    assert got["coast_distance_km"] == pytest.approx(12.345)
    assert got["coast_landmass_area_km2"] == pytest.approx(83900.0)
    assert got["mrgid"] == 4276 and type(got["mrgid"]) is int
    assert got["coast_mrgid"] == 2350 and type(got["coast_mrgid"]) is int
    assert got["longhurst_code"] == "NADR"


def test_is_fallback_false_reads_back_as_false(tmp_path):
    """The string ``"False"`` would be truthy and make every cached direct hit
    look like a fallback."""
    cache = GeoCache(tmp_path / "cache.sqlite")
    cache.add(1.0, 2.0, {"country": "Denmark", "is_fallback": False})
    assert cache.lookup(1.0, 2.0)["is_fallback"] is False


def test_a_missing_coordinate_is_none_not_an_empty_dict(tmp_path):
    """Callers test the result for truth. Returning {} for a miss and {} for a
    cached total failure would make the two indistinguishable."""
    cache = GeoCache(tmp_path / "cache.sqlite")
    assert cache.lookup(1.0, 2.0) is None


def test_an_old_tsv_cache_is_imported_once(tmp_path):
    """A TSV cache beside a database that does not exist yet is imported, with
    malformed lines skipped (such files were hand-edited) and full-precision
    keys intact. Once the database exists the TSV is no longer read."""
    tsv = tmp_path / "land_cache.tsv"
    tsv.write_text(
        "lat\tlon\tlevel\tname\n"
        "not-a-number\t2.0\tcountry\tJunk\n"
        "\n"
        "1.0\t2.0\tcountry\tDenmark\n"
        "1.0\t2.0\tis_fallback\tFalse\n"
        "38.02833333333333\t-122.12345678901234\tcountry\tUnited States\n"
        "1e-07\t-2.5e-08\tcountry\tnowhere\n"
    )
    db = tmp_path / "land_cache.sqlite"
    cache = GeoCache(db)
    assert len(cache) == 3
    assert cache.lookup(1.0, 2.0) == {"country": "Denmark", "is_fallback": False}
    assert cache.lookup(38.02833333333333, -122.12345678901234) == {
        "country": "United States"
    }
    assert cache.lookup(1e-07, -2.5e-08) == {"country": "nowhere"}
    cache.close()

    tsv.write_text("lat\tlon\tlevel\tname\n5.0\t6.0\tcountry\tLater\n")
    assert GeoCache(db).lookup(5.0, 6.0) is None
