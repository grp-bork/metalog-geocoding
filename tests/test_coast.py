"""Distance to coast (metalog_geocoding/coast.py).

The synthetic tests cover the silent failure modes of a nearest-coast search
-- ranking by degrees at high latitude, and a search box that stops at the
antimeridian -- on hand-checkable geometry, without the GSHHG layers. The
real-layer tests skip without the parquet.
"""

import pytest

pytestmark = pytest.mark.geo

# Skip before importing anything from the geo stack: the marker only takes
# effect once the module has been collected, so a top-level `import numpy`
# would fail collection in the bare install.
pytest.importorskip("geopandas")
import numpy as np  # noqa: E402
from pyproj import Geod  # noqa: E402

from metalog_geocoding.coast import (  # noqa: E402
    FLAG_OK,
    _bounding_boxes,
    _boundary_chunks,
    _ChunkIndex,
)

GEOD = Geod(ellps="WGS84")


def square(lon, lat, half=0.01):
    """Closed ring of a small square island, as a coordinate array."""
    return np.array(
        [
            (lon - half, lat - half),
            (lon + half, lat - half),
            (lon + half, lat + half),
            (lon - half, lat + half),
            (lon - half, lat - half),
        ]
    )


def geodesic_km(lat, lon, ring):
    _, _, d = GEOD.inv(np.full(len(ring), lon), np.full(len(ring), lat), ring[:, 0], ring[:, 1])
    return d.min() / 1000


def test_the_degree_nearest_island_is_not_the_nearest_at_high_latitude():
    """At 70°N a degree of longitude is a third of a degree of latitude, so the
    island 3° east is nearer than the one 1.5° north. Ranking in degrees picks
    the northern one; the search must not stop there."""
    east, north = square(3.0, 70.0), square(0.0, 71.5)
    index = _ChunkIndex([north, east], np.array([0, 1]))
    distance_m, owner, _ = index.nearest(70.0, 0.0, GEOD)
    assert distance_m / 1000 == pytest.approx(geodesic_km(70.0, 0.0, east), abs=0.5)
    assert distance_m / 1000 < geodesic_km(70.0, 0.0, north) - 40
    assert owner == 1, "the landmass reported is the one the distance was measured to"


def test_land_across_the_antimeridian_is_found():
    """A Fiji-like point at 179.9°E with land at 179.8°W (~32 km) and at 179.0°E
    (~96 km): in degrees the far one is nearer, and a box that does not wrap
    never sees the near one."""
    across, same_side = square(-179.8, -16.6), square(179.0, -16.6)
    index = _ChunkIndex([same_side, across], np.array([0, 1]))
    distance_m, owner, _ = index.nearest(-16.6, 179.9, GEOD)
    assert distance_m / 1000 == pytest.approx(geodesic_km(-16.6, 179.9, across), abs=0.5)
    assert distance_m < 40_000
    assert owner == 1


def test_the_nearest_point_can_be_inside_a_segment():
    """A long straight coast 10 km south of the point: the nearest point is
    the foot of the perpendicular, not either end of the segment."""
    coast = np.array([(-1.0, 0.0), (1.0, 0.0)])
    index = _ChunkIndex([coast], np.array([0]))
    lat = 10_000 / 110_574  # ~10 km north of the equator
    assert index.nearest(lat, 0.0, GEOD)[0] == pytest.approx(10_000, abs=5)


def test_a_disc_reaching_the_pole_spans_every_longitude():
    assert _bounding_boxes(89.0, 0.0, 300_000) == [(-180.0, pytest.approx(86.27, abs=0.1), 180.0, 90.0)]


def test_a_disc_crossing_the_antimeridian_is_two_boxes():
    boxes = _bounding_boxes(0.0, 179.5, 200_000)
    assert len(boxes) == 2
    assert any(b[2] == 180.0 for b in boxes) and any(b[0] == -180.0 for b in boxes)


def test_file_format_seams_are_not_coastline():
    """The shapefiles close split polygons along 180° and Antarctica down to
    -90°. Neither edge is a shore, and a point beside them must not measure
    its distance to one."""
    import shapely

    # A polygon split at the antimeridian: its east edge runs up 180°.
    split = shapely.Polygon([(179.0, 65.0), (180.0, 65.0), (180.0, 69.0), (179.0, 69.0)])
    # A pole cap: closed along -90°.
    cap = shapely.Polygon([(-180.0, -78.0), (180.0, -78.0), (180.0, -90.0), (-180.0, -90.0)])
    chunks, _ = _boundary_chunks(np.array([split, cap], dtype=object))
    segments = [
        (tuple(a), tuple(b)) for chunk in chunks for a, b in zip(chunk[:-1], chunk[1:])
    ]
    assert ((180.0, 65.0), (180.0, 69.0)) not in segments
    assert not any(a[1] == -90.0 or b[1] == -90.0 for a, b in segments), (
        "a cut meridian running down to the pole is a seam too"
    )
    assert ((179.0, 65.0), (180.0, 65.0)) in segments, "a real edge touching 180° stays"


# --------------------------------------------------------------------------
# The real GSHHG layers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def coast(data_dir):
    from metalog_geocoding.coast import CoastDistance

    return CoastDistance(data_dir)


def test_point_nemo_is_2688_km_from_land(coast):
    """The oceanic pole of inaccessibility, ~2,688 km from Ducie Island."""
    got = coast.query(-48.876667, -123.393333)
    assert got["coast_flag"] == FLAG_OK
    assert got["coast_distance_km"] == pytest.approx(2688, abs=2)
    assert got["coast_landmass_area_km2"] < 10, "Ducie is an atoll"


def test_a_tiny_islet_beyond_30_km_does_not_count(coast):
    """Mid-Atlantic GO-SHIP station: the nearest GSHHG land is a 0.125 km² islet
    off Fernando de Noronha; beyond 30 km only land of >= 1 km² counts, so the
    answer is Fernando de Noronha itself (18 km²)."""
    got = coast.query(3.80775, -25.8358)
    assert got["coast_landmass_area_km2"] >= 1
    assert got["coast_landmass_area_km2"] == 18.0


def test_a_tiny_islet_within_30_km_still_counts(coast):
    """Off Mo'orea: the nearest land is a 0.19 km² motu 3.4 km away, and near
    shore that is exactly the coast that matters."""
    got = coast.query(-17.48, -149.95)
    assert got["coast_landmass_area_km2"] < 1
    assert got["coast_distance_km"] < 30


def test_the_distance_is_rounded_to_a_tenth_of_a_kilometre(coast):
    """GSHHG's shoreline is good to ~50-500 m; metres would be noise."""
    got = coast.query(30.0, -40.0)
    assert got["coast_distance_km"] == round(got["coast_distance_km"], 1)


@pytest.mark.parametrize(
    "lat, lon, area_km2",
    [
        (53.24, -9.31, 83_900),  # Galway Bay: Ireland, a shelf island
        (37.87, 12.48, 25_500),  # off Marsala: Sicily
        (20.864, -156.139, 1_910),  # north shore of Maui
        (-76.0, 175.0, 13_900_000),  # Ross Sea: Antarctica's ice front (L5)
        (41.14, -8.67, 50_700_000),  # off Porto: Eurasia
    ],
)
def test_the_landmass_area_says_what_kind_of_coast_it_is(coast, lat, lon, area_km2):
    """The area separates an oceanic island (Maui) from a shelf island
    (Ireland), which a continent/island split cannot."""
    got = coast.query(lat, lon)
    assert got["coast_flag"] == FLAG_OK
    assert got["coast_landmass_area_km2"] == area_km2


def test_eurasia_has_one_area_on_both_sides_of_the_antimeridian(coast):
    """Split polygons carry the whole landmass's area on each half."""
    east = coast.query(64.5, 179.0)  # Gulf of Anadyr: nearest coast on 0-E
    west = coast.query(64.6, -174.5)  # off Chukotka's tip: nearest coast on 0-W
    across = coast.query(61.0, -178.0)  # west of 180°, nearest coast on 0-E
    assert {r["coast_landmass_area_km2"] for r in (east, west, across)} == {50_700_000}
    assert across["coast_distance_km"] == pytest.approx(208, abs=1)


def test_a_point_on_land_is_a_negative_distance(coast):
    """Zurich: 327 km from the Ligurian coast, inland."""
    got = coast.query(47.37, 8.54)
    assert got["coast_flag"] == FLAG_OK
    assert got["coast_distance_km"] == pytest.approx(-327.5, abs=1)
    assert got["coast_landmass_area_km2"] == 50_700_000


def test_the_caspian_is_inland(coast):
    """A lake is not the sea: the Caspian measures to the Black Sea, inland."""
    assert coast.query(42.0, 50.5)["coast_distance_km"] < -500


def test_the_pole_of_inaccessibility_is_2400_km_inland(coast):
    assert coast.query(46.28, 86.67)["coast_distance_km"] == pytest.approx(-2379, abs=2)


@pytest.mark.parametrize("lat, lon", [(-80.0, 30.0), (-82.0, 175.0), (-80.0, -60.0)])
def test_the_whole_of_antarctica_is_land(coast, lat, lon):
    """GSHHG closes Antarctica's east half with a chord from (180°, -77.9°) to
    (0°, -69.6°) that leaves the polar plateau and the Ross Ice Shelf outside
    it, and both halves run a cut meridian to the pole that is not a coast.
    The loader repairs both."""
    got = coast.query(lat, lon)
    assert got["coast_distance_km"] < -300


@pytest.mark.parametrize("lat, lon", [(-90.0, 0.0), (-89.999, 0.0), (-85.0, 180.0), (-85.0, 0.0)])
def test_a_point_on_a_seam_of_antarctica_is_land(coast, lat, lon):
    """The pole and the 0°/180° cut meridians are on the boundary of both
    halves, so the on-land test must be ``covered_by``, not ``within``."""
    assert coast.query(lat, lon)["coast_distance_km"] < -300


def test_an_islet_is_its_own_coast_from_inside(coast):
    """The 1 km² rule is for points at sea; on an islet the shore is right there."""
    import shapely

    islet = int(np.flatnonzero(coast._area_km2 < 0.1)[0])
    inside = shapely.point_on_surface(coast._land.geometries[islet])
    got = coast.query(inside.y, inside.x)
    assert got["coast_distance_km"] <= 0
    assert got["coast_landmass_area_km2"] < 1


def test_a_sea_result_carries_the_coast_fields(geocoder):
    got = geocoder.geocode(30.0, -40.0, "sea")
    assert got["coast_flag"] == FLAG_OK
    assert got["coast_distance_km"] > 1000
    assert got["coast_landmass_area_km2"] > 0


# --------------------------------------------------------------------------
# Where the nearest coast is: landmass name, country, admin-1
# --------------------------------------------------------------------------


def test_a_german_baltic_point_is_off_ruegen_in_germany(geocoder):
    got = geocoder.geocode(54.4412, 13.7264, "sea")
    assert got["coast_landmass_name"] == "Rügen Island"
    assert got["coast_country"] == "Germany"
    assert got["coast_country_code"] == "DEU"
    assert got["coast_admin_1"] == "Mecklenburg-Vorpommern"
    assert got["coast_country_source"] == "natural_earth"


def test_an_islet_natural_earth_lacks_takes_its_geonames_country(geocoder):
    """Umm al Maradim is Kuwaiti; Natural Earth has no polygon for it, and the
    nearest Natural Earth country is Saudi Arabia, 25 km away."""
    got = geocoder.geocode(28.68, 48.65, "sea")
    assert got["coast_landmass_name"] == "Jazīrat Umm al Marādim"
    assert got["coast_country"] == "Kuwait"
    assert got["coast_country_source"] == "geonames"


def test_the_nearest_coast_decides_the_country(geocoder):
    """Mouth of the Szczecin Lagoon: Poland's coast (Wolin) is nearer than
    Germany's, so this is Poland -- by design, not "within 10 km of Germany"."""
    got = geocoder.geocode(53.9593, 14.3217, "sea")
    assert got["coast_landmass_name"] == "Wolin"
    assert got["coast_country"] == "Poland"


def test_large_landmasses_take_natural_earths_name_not_an_islet_inside(coast):
    """GeoNames has no single feature for large islands such as Ireland, so its
    best match inside the polygon is a minor islet. Natural Earth's named
    islands win."""
    names = {row.id: row.name for row in coast._names.values()}
    assert names["26"] == "Ireland"
    assert names["10"] == "Madagascar"
    assert names["112-W"] == names["112-E"] == "Vanua Levu"


def test_the_antarctic_ice_front_is_in_antarctica(geocoder):
    """The ice front can sit well over 5 km from Natural Earth's Antarctic
    outline; Antarctica is one Natural Earth unit, so the landmass answers."""
    got = geocoder.geocode(-76.0, 175.0, "sea")
    assert got["coast_landmass_name"] == "Antarctica"
    assert got["coast_country"] == "Antarctica"


def test_iho_and_natural_earth_outrank_gshhg_on_which_side_of_the_shore(geocoder):
    """A Red Sea reef site: GSHHG maps it on a 1.85 km² reef flat, while IHO and
    Natural Earth both put it at sea. It is reported at the shore (0 km), on
    the landmass the point falls in."""
    got = geocoder.geocode(22.13, 39.01, "sea")
    assert got["coast_flag"] == "ok"
    assert got["coast_distance_km"] == 0.0
    assert got["coast_landmass_area_km2"] == 1.85
    assert got["coast_country"] == "Saudi Arabia"


def test_a_point_all_layers_put_on_land_stays_on_land(geocoder):
    """At 18.0, -102.0 IHO only reaches a sea by snapping 2.5 km and Natural
    Earth has it in Mexico, so GSHHG's side stands: a sea result with a
    negative (inland) distance."""

    got = geocoder.geocode(18.0, -102.0, "sea")
    assert got["coast_flag"] == "ok"
    assert got["coast_distance_km"] < 0


def test_a_land_result_carries_a_negative_distance_and_the_sea_it_faces(geocoder):
    """Berlin: ~170 km from the Baltic, on Eurasia. A land result names the
    water its nearest coast faces, not that coast's country."""
    got = geocoder.geocode(52.52, 13.405, "land")
    assert got["coast_flag"] == "ok"
    assert -200 < got["coast_distance_km"] < -100
    assert got["coast_landmass_area_km2"] == 50_700_000
    assert got["coast_sea"] == "Baltic Sea"
    assert got["coast_sea_chain"].endswith("Atlantic Ocean")
    assert "coast_country" not in got


def test_an_estuary_coast_faces_the_sea_at_its_mouth(geocoder):
    """GSHHG's sea runs up the Elbe to Hamburg; the nearest IHO sea is the
    North Sea at its mouth."""
    got = geocoder.geocode(53.54, 9.97, "land")
    assert got["coast_sea"] == "North Sea"
