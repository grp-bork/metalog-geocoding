"""Signed distance from a coordinate to the coast, and how big that land is.

Positive at sea, negative on land: a point 40 km inland is -40.0 km, one 40 km
offshore is 40.0 km, so land and sea samples share one axis.

Source: GSHHG 2.3.7 (Wessel & Smith 1996, doi:10.1029/96JB00104), full
resolution. "Coast" is the boundary of any level-1 polygon (continents and
islands, with no size cutoff) or level-5 polygon (the Antarctic ice front, which
is the ocean-land boundary seawater actually meets; level 6, the grounding line,
lies under the ice shelves).

There is no distance to the nearest *continent*: GSHHG's only notion of one is
numbering its largest polygons first, which puts shelf islands such as Ireland
in the same class as Maui. The landmass area is returned instead, so a reader
can apply whatever cutoff their question needs.

Licence: GSHHG is LGPL v3. Ship only the derived distances, never this
geometry; do not build map layers or other geometry products from it.

The distance is the exact geodesic (WGS84) distance to the nearest boundary
point. ``FallbackGeocoder``'s degree-space ranking is not reused because here
distances reach thousands of km and include polar points, where the
degree-nearest point is not the nearest. The search takes an upper bound from
any boundary vertex, collects every boundary chunk that could lie within that
geodesic radius, and measures them in an azimuthal-equidistant projection
centred on the sample, where distance from the origin is geodesic distance.
"""

import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import Geod, Transformer

from .hierarchy_table import COAST_SOURCE, GSHHG_RESOLUTION  # noqa: F401 (re-exported)

#: Parquet layers, converted from GSHHS_shp/f/ by scripts/setup_data.py.
COAST_LAYERS = {
    1: f"GSHHS_{GSHHG_RESOLUTION}_L1.parquet",
    5: f"GSHHS_{GSHHG_RESOLUTION}_L5.parquet",
}
GSHHG_COLUMNS = ["id", "level", "area", "geometry"]

#: Names and GeoNames countries for the polygons, built once by
#: scripts/build_landmass_names.py (GeoNames, CC-BY 4.0).
NAMES_LAYER = f"GSHHS_{GSHHG_RESOLUTION}_names.parquet"

#: Value of the ``coast_flag`` attribute. It has a single value but is always
#: written, because it is the "already computed" marker in the result caches;
#: a cached entry with any other value is recomputed
#: (``ReverseGeocoder._has_coast``).
FLAG_OK = "ok"

#: Land smaller than MIN_DISTANT_LANDMASS_KM2 counts as coast only within
#: SMALL_LAND_RADIUS_M: a tiny rock is the right "coast" beside a reef sample,
#: not for a station hundreds of kilometres out.
#:
#: 30 km: Gove et al. 2016, Nat Commun 7:10581, doi:10.1038/ncomms10581
#: (PMID 26881874), measured the island mass effect -- enhanced phytoplankton
#: near Pacific islands and atolls -- in sectors reaching ~26.16-29.43 km
#: offshore from each island's 30 m isobath, with ship transects to 20-30 km.
#: That is the window the effect was measured in, from the 30 m isobath
#: rather than the shoreline; it is not a finding that the effect ends there.
SMALL_LAND_RADIUS_M = 30_000
#: 1 km²: Weigelt et al. 2013, PNAS 110:15307, doi:10.1073/pnas.1306309110
#: (PMID 24003123), characterise "the world's marine islands >1 km²" (~98% of
#: total island area).
MIN_DISTANT_LANDMASS_KM2 = 1.0

#: Boundary rings are cut into runs of this many segments before indexing, so a
#: query near a continent tests a few hundred vertices rather than millions.
CHUNK_SEGMENTS = 128

#: Smallest radius of curvature of WGS84 (polar meridional, ~6,335 km), so the
#: candidate box computed on a sphere of this radius is never too small.
_MIN_RADIUS_M = 6_335_439.0
_BOX_MARGIN = 1.01


def _close_to_pole(geometry):
    """Repair the closure of a polygon that GSHHG closes at the South Pole.

    Antarctica is split at 0° and 180° into ``4-E`` and ``4-W``, each meant to
    be closed down to -90°. Both are invalid as shipped; ``4-E`` closes with a
    chord across the continent, so the Ross Ice Shelf and the polar plateau
    would test as not land. Rebuilt as the coastline between the two cut
    meridians, closed along them to the pole. The artificial edge is the one
    long jump between consecutive vertices once the pole vertices are removed.
    """
    xy = shapely.get_coordinates(geometry)[:-1]
    xy = xy[xy[:, 1] > -89.999]
    jumps = np.abs(np.diff(xy[:, 0], append=xy[:1, 0]))
    k = int(np.argmax(jumps))
    if jumps[k] < 90:
        raise AssertionError("expected one cut-meridian jump in a polygon closed at the pole")
    path = np.roll(xy, -(k + 1), axis=0)
    ring = np.vstack([path, [(path[-1, 0], -90.0), (path[0, 0], -90.0)]])
    # The coastline also crosses itself in a few places; repairing that leaves
    # holes of at most ~3e-4 deg², which are loops of the line, not lakes.
    repaired = shapely.make_valid(shapely.Polygon(ring), method="structure", keep_collapsed=False)
    if repaired.geom_type != "Polygon":
        raise AssertionError(f"repaired polar polygon is a {repaired.geom_type}")
    return shapely.Polygon(repaired.exterior)


def _boundary_chunks(geometries) -> tuple[list, np.ndarray]:
    """Cut polygon exteriors into short vertex runs, dropping artificial edges.

    The shapefiles close polygons that cross the antimeridian along the 180°
    meridian, and close Antarctica down to the pole. Those edges are seams of
    the file format, not coastline. A segment is a seam when both its ends lie
    on ±180°, or either end lies at -90°.

    Returns the chunks as (n, 2) coordinate arrays -- views into one contiguous
    array, so the coast is held once -- and, for each, the polygon it came
    from. GSHHG polygons have no holes (a lake is its own L2 polygon), so a
    polygon's coordinates are exactly its exterior ring.
    """
    if any(shapely.get_num_interior_rings(geometries)):
        raise AssertionError("GSHHG polygons are expected to have no interior rings")
    xy, index = shapely.get_coordinates(geometries, return_index=True)
    splits = np.flatnonzero(np.diff(index)) + 1
    del index

    chunks, owners = [], []
    for owner, coords in enumerate(np.split(xy, splits)):
        x, y = coords[:, 0], coords[:, 1]
        seam = ((np.abs(x[:-1]) == 180) & (np.abs(x[1:]) == 180)) | (
            (y[:-1] <= -89.999) | (y[1:] <= -89.999)
        )
        # Runs of consecutive kept segments; a run of n segments has n+1 points.
        breaks = np.flatnonzero(seam)
        starts = np.concatenate([[0], breaks + 1])
        ends = np.concatenate([breaks, [len(seam)]])
        for start, end in zip(starts, ends):
            for s in range(start, end, CHUNK_SEGMENTS):
                chunks.append(coords[s : min(s + CHUNK_SEGMENTS, end) + 1])
                owners.append(owner)
    return chunks, np.asarray(owners, dtype=np.int64)


class CoastDistance:
    """Geodesic distance to the GSHHG coast, and the size of the land it is."""

    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        layers = {}
        for level, name in COAST_LAYERS.items():
            path = data_dir / name
            if not path.exists():
                raise FileNotFoundError(
                    f"GSHHG layer not found: {path}\n"
                    f"Run: python scripts/setup_data.py"
                )
            layers[level] = gpd.read_parquet(path, columns=GSHHG_COLUMNS)

        land = np.concatenate(
            [np.asarray(layers[1].geometry.values), np.asarray(layers[5].geometry.values)]
        )
        polar = shapely.bounds(land)[:, 1] <= -89.999
        land[polar] = [_close_to_pole(g) for g in land[polar]]
        self._ids = np.concatenate([layers[1]["id"].to_numpy(), layers[5]["id"].to_numpy()])

        names_path = data_dir / NAMES_LAYER
        if not names_path.exists():
            raise FileNotFoundError(
                f"GSHHG landmass names not found: {names_path}\n"
                f"Run: python scripts/build_landmass_names.py"
            )
        names = pd.read_parquet(names_path, columns=["id", "name", "ne_adm0_a3", "ne_adm1_code"])
        self._names = {row.id: row for row in names.itertuples(index=False)}
        # GSHHG's own area, in km². A polygon split at the antimeridian carries
        # the area of the whole landmass on both halves, so Eurasia is 50.7 M
        # km² whichever half the nearest point is on.
        self._area_km2 = np.concatenate(
            [layers[1]["area"].to_numpy(), layers[5]["area"].to_numpy()]
        )

        self._land = shapely.STRtree(land)
        # Two searches over the same coordinate arrays: all land, and land of
        # at least MIN_DISTANT_LANDMASS_KM2 (see query()).
        chunks, owners = _boundary_chunks(land)
        self._coast = _ChunkIndex(chunks, owners)
        large = self._area_km2[owners] >= MIN_DISTANT_LANDMASS_KM2
        self._large_coast = _ChunkIndex(
            [c for c, keep in zip(chunks, large) if keep], owners[large]
        )
        self.geod = Geod(ellps="WGS84")

    def query(self, lat: float, lon: float) -> dict:
        """
        Signed distance to the coast for one coordinate.

        Returns:
            ``{"coast_flag": "ok", "coast_distance_km": ..., "coast_landmass_area_km2": ...,
            "coast_point": (lat, lon), "coast_landmass": row, "coast_landmass_name": ...}``.
            The distance is positive at sea and negative inside a GSHHG land
            polygon, lakes included: a lake is inland water, and its shore is
            not the coast. ``coast_point`` is the shoreline point measured to
            and ``coast_landmass`` its row in the names table (or None); the
            geocoder uses both to place the coast in a country and then drops
            them. ``coast_landmass_name`` is absent for an unnamed polygon.

            Which side of the shoreline a point is on is GSHHG's opinion here;
            ``ReverseGeocoder._add_coast`` overrides it with 0 where IHO and
            Natural Earth agree on the other side.

            The distance is rounded to 0.1 km, since GSHHG's shoreline is good
            to ~50-500 m. The area (3 significant figures) is that of the
            landmass the nearest coast belongs to, e.g. Maui 1,910 km² vs
            Ireland 83,900 km².

            At sea, if the nearest land is smaller than
            MIN_DISTANT_LANDMASS_KM2 and farther than SMALL_LAND_RADIUS_M, the
            nearest landmass of at least that size is used instead. On land the
            rule never applies: an islet's own shore is its coast.
        """
        # covered_by, not within: the boundary includes the seams (the South
        # Pole and the cut meridians inside Antarctica), which are land.
        on_land = bool(len(self._land.query(shapely.Point(lon, lat), predicate="covered_by")))
        distance_m, owner, point = self._coast.nearest(lat, lon, self.geod)
        if (
            not on_land
            and distance_m > SMALL_LAND_RADIUS_M
            and self._area_km2[owner] < MIN_DISTANT_LANDMASS_KM2
        ):
            distance_m, owner, point = self._large_coast.nearest(lat, lon, self.geod)
        distance_km = round(distance_m / 1000, 1)
        landmass = self._names.get(self._ids[owner])
        result = {
            "coast_flag": FLAG_OK,
            # + 0.0 turns the -0.0 of a point a few metres inland into 0.0
            "coast_distance_km": (-distance_km if on_land else distance_km) + 0.0,
            "coast_landmass_area_km2": float(f"{self._area_km2[owner]:.3g}"),
            "coast_point": point,
            "coast_landmass": landmass,
        }
        if landmass is not None and landmass.name:
            result["coast_landmass_name"] = landmass.name
        return result


class _ChunkIndex:
    """Exact geodesic nearest-boundary search over a set of boundary chunks."""

    def __init__(self, chunks: list, owners: np.ndarray):
        self.owners = owners
        # Per-chunk vertex arrays, kept as numpy so a query never goes back
        # through GEOS for coordinates. The tree indexes their bounding boxes
        # only: five vertices per chunk instead of a second copy of the coast.
        self.coords = chunks
        bounds = np.array([[c[:, 0].min(), c[:, 1].min(), c[:, 0].max(), c[:, 1].max()] for c in chunks])
        self.tree = shapely.STRtree(shapely.box(bounds[:, 0], bounds[:, 1], bounds[:, 2], bounds[:, 3]))

    def nearest(self, lat: float, lon: float, geod: Geod) -> tuple[float, int, tuple]:
        """Geodesic distance in metres to the nearest boundary point, the index
        of the polygon that point belongs to, and the point as ``(lat, lon)``."""
        point = shapely.Point(lon, lat)

        # 1. Upper bound: the geodesic distance to any boundary vertex will do;
        #    the chunk whose box is degree-nearest is a cheap, tight one.
        seed_chunk = int(self.tree.query_nearest(point)[0])
        seed = self.coords[seed_chunk]
        _, _, d = geod.inv(np.full(len(seed), lon), np.full(len(seed), lat), seed[:, 0], seed[:, 1])
        upper = float(np.min(d))

        # 2. Every chunk that can hold a point within `upper` of the sample.
        candidates = set()
        for box in _bounding_boxes(lat, lon, upper):
            candidates.update(int(i) for i in self.tree.query(shapely.box(*box)))

        # 3. Exact distance in an azimuthal-equidistant projection centred on
        #    the sample: there, distance from the origin is geodesic distance.
        to_aeqd = Transformer.from_proj(
            "EPSG:4326",
            f"+proj=aeqd +lat_0={lat} +lon_0={lon} +ellps=WGS84 +units=m",
            always_xy=True,
        )
        best, best_xy, best_chunk = math.inf, None, seed_chunk
        for i in candidates:
            xy = np.column_stack(to_aeqd.transform(self.coords[i][:, 0], self.coords[i][:, 1]))
            dist, nearest = _nearest_on_polyline(xy)
            if dist < best:
                best, best_xy, best_chunk = dist, nearest, i

        # 4. Report the geodesic distance to that point, measured the same way
        #    as every other distance in the table.
        owner = int(self.owners[best_chunk])
        if best_xy is None:  # cannot happen: the seed chunk is always a candidate
            k = int(np.argmin(d))
            return upper, owner, (float(seed[k, 1]), float(seed[k, 0]))
        plon, plat = to_aeqd.transform(best_xy[0], best_xy[1], direction="INVERSE")
        _, _, d = geod.inv(lon, lat, plon, plat)
        return min(float(d), upper), owner, (float(plat), float(plon))


def _nearest_on_polyline(xy: np.ndarray) -> tuple[float, np.ndarray]:
    """Distance from the origin to a polyline, and the nearest point on it."""
    a, b = xy[:-1], xy[1:]
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    t = np.where(denom > 0, -np.einsum("ij,ij->i", a, ab) / np.where(denom > 0, denom, 1), 0.0)
    t = np.clip(t, 0.0, 1.0)
    p = a + ab * t[:, None]
    d = np.hypot(p[:, 0], p[:, 1])
    k = int(np.argmin(d))
    return float(d[k]), p[k]


def _bounding_boxes(lat: float, lon: float, radius_m: float) -> list[tuple]:
    """Lon/lat boxes that together contain every point within `radius_m`.

    The spherical bounding-box construction (Matuschek), on a sphere of the
    smallest WGS84 radius of curvature so the box errs large. A disc that
    reaches a pole spans every longitude; one that crosses ±180° is returned as
    two boxes, which is what keeps a Fiji sample from missing land on the other
    side of the dateline.
    """
    theta = radius_m * _BOX_MARGIN / _MIN_RADIUS_M  # angular radius, radians
    phi = math.radians(lat)
    lat_min, lat_max = phi - theta, phi + theta
    if lat_max >= math.pi / 2 or lat_min <= -math.pi / 2:
        return [(-180.0, math.degrees(max(lat_min, -math.pi / 2)),
                 180.0, math.degrees(min(lat_max, math.pi / 2)))]
    dlon = math.degrees(math.asin(min(1.0, math.sin(theta) / math.cos(phi))))
    s, n = math.degrees(lat_min), math.degrees(lat_max)
    w, e = lon - dlon, lon + dlon
    if dlon >= 180:
        return [(-180.0, s, 180.0, n)]
    boxes = [(max(w, -180.0), s, min(e, 180.0), n)]
    if w < -180:
        boxes.append((w + 360, s, 180.0, n))
    if e > 180:
        boxes.append((-180.0, s, e - 360, n))
    return boxes
