"""The coverage rebuild behind scripts/build_geojson.py, on hand-made squares."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.geo
shapely = pytest.importorskip("shapely")
np = pytest.importorskip("numpy")

spec = importlib.util.spec_from_file_location(
    "build_geojson", Path(__file__).parent.parent / "scripts" / "build_geojson.py"
)
build_geojson = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_geojson)


def test_overlaps_go_to_the_smaller_unit_and_enclosed_gaps_are_closed():
    """A ring with a hairline gap to the unit inside it, an overlapping
    neighbour, and an enclave: the result tiles without gap or overlap."""
    ring = shapely.box(0, 0, 3, 3).difference(shapely.box(1, 1, 2, 2))
    inner = shapely.box(1.0005, 1.0005, 1.9995, 1.9995)
    neighbour = shapely.box(2.999, 0, 4, 3)
    enclave = shapely.box(0.4, 0.4, 0.6, 0.6)
    geoms = np.array([ring, inner, neighbour, enclave])

    rebuilt = build_geojson.rebuild_coverage(geoms, 0.01)

    assert shapely.coverage_is_valid(rebuilt)
    # the outer coast grows by half a tenth of the tolerance
    assert shapely.area(shapely.union_all(rebuilt)) == pytest.approx(12, abs=0.01)
    assert shapely.area(rebuilt[1]) == pytest.approx(1, abs=3e-3)
    assert rebuilt[3].contains(enclave)
    assert shapely.area(rebuilt[3]) == pytest.approx(0.04, abs=1e-3)
    assert shapely.area(rebuilt[0]) + shapely.area(rebuilt[1]) + shapely.area(rebuilt[2]) == pytest.approx(12 - 0.04, abs=0.01)


def test_land_enclosed_by_seas_stays_land():
    """Land enclosed by seas becomes a face touching every one of them after
    polygonizing; it must not be treated as a gap and given to a sea."""

    land = shapely.box(1, 1, 9, 9)
    seas = [
        shapely.box(0, 0, 10, 1),
        shapely.box(0, 9, 10, 10),
        shapely.box(0, 1, 1, 9),
        shapely.box(9, 1, 10, 9),
    ]

    rebuilt = build_geojson.rebuild_coverage(np.array(seas), 0.01)

    assert not shapely.union_all(rebuilt).intersects(land.buffer(-0.5))
    assert [shapely.area(g) for g in rebuilt] == pytest.approx([10, 10, 8, 8], abs=0.02)


def test_holes_below_the_threshold_are_filled():
    island = shapely.box(0.5, 0.5, 0.51, 0.51)
    large = shapely.box(0.2, 0.2, 0.4, 0.4)
    sea = shapely.box(0, 0, 1, 1).difference(island).difference(large)

    filled = build_geojson.without_small_holes(sea, 0.001)

    assert len(filled.interiors) == 1
    assert shapely.Polygon(filled.interiors[0]).equals(large)
