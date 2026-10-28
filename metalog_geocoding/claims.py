"""
How far a point is from the country a sample claims it is in.

One measurement decides both disagreement branches of the grader, at two very
different scales -- see ``CLAIM_NEAR_M`` and ``CLAIM_BORDER_M`` below.

Needs the geo stack (shapely, pyproj) and the loaded Natural Earth admin-0
layer, so it is imported lazily through the package's ``__getattr__``.
"""

from pyproj import Geod
from shapely.geometry import Point
from shapely.ops import nearest_points

from .containment import CONTAINED_IN
from .country_aliases import canonical

#: Not used for grading. Kept as a reminder of why no rule is gated on the
#: fallback distance: how far a point sits from land measures the coordinate's
#: imprecision, not whether the claim is wrong (a point 1 km off one coast can
#: be labelled with a country on another continent).
FALLBACK_SUSPECT_M = 5_000

#: How far the claimed country may be from the point before a shoreline
#: fallback is graded ``error``. A distance beats a country-adjacency test:
#: Denmark and Sweden do not touch, yet a point in the Oresund labelled either
#: is ordinary coastal ambiguity. 100 km clears every strait between genuine
#: neighbours (Oresund ~4 km, Gibraltar ~14 km, Otranto ~72 km); on real data
#: legitimate claims stay below ~80 km and wrong ones are thousands of km out.
CLAIM_NEAR_M = 100_000

#: How close the claimed country may be for a direct hit in a different
#: country to pass anyway. A border is a line on a map with its own error;
#: within this range the sample disagrees with the boundary, not the place
#: (e.g. a point in the St. Lawrence inside Canada, 277 m from the US).
#:
#: Two orders of magnitude below ``CLAIM_NEAR_M`` because it forgives a
#: boundary, not an imprecise coordinate. A point 5 km inside the wrong
#: country is still an error. On real data, direct-hit disagreements at
#: international land borders reach ~1.8 km and none fall between 2 and 5 km,
#: so the threshold sits in a gap.
CLAIM_BORDER_M = 2_000


class ClaimChecker:
    """
    Geodesic distance from a point to the nearest territory of the claimed country.

    Names are matched through ``country_aliases`` ("USA" finds "United
    States"). A claim that resolves to no Natural Earth unit returns ``None``,
    and the caller leaves the grade alone.

    By default "the claimed country" is every Natural Earth unit that belongs
    to it: the unit of that name plus every unit whose ``SOVEREIGNT`` is that
    name, since Natural Earth splits overseas territories (Curacao, Aruba,
    Christmas Island, ...) into their own units.

    A territory in ``containment.CONTAINED_IN`` (Svalbard, French Guiana, ...)
    is measured against its own admin-1 polygon, never the whole unit that
    contains it.
    """

    def __init__(self, land_geocoder):
        self.geod = Geod(ellps="WGS84")
        self._cache: dict[tuple[str, float, float, bool], float | None] = {}
        self.by_name: dict[str, list] = {}
        self.by_own_name: dict[str, list] = {}
        adm0 = land_geocoder.countries
        for _, row in adm0.iterrows():
            name = land_geocoder._country_name(row)
            if name:
                self.by_name.setdefault(canonical(name), []).append(row.geometry)
                self.by_own_name.setdefault(canonical(name), []).append(row.geometry)
            sovereign = row.get("SOVEREIGNT")
            if sovereign and canonical(sovereign) != canonical(name or ""):
                self.by_name.setdefault(canonical(sovereign), []).append(row.geometry)
        adm1 = land_geocoder.adm1.set_index("adm1_code").geometry
        for claimed, territory in CONTAINED_IN.items():
            geom = adm1[territory.adm1_code]
            self.by_name[canonical(claimed)] = [geom]
            self.by_own_name[canonical(claimed)] = [geom]

    def distance_m(
        self, claimed: str, lat: float, lon: float, include_dependencies: bool = True
    ) -> float | None:
        """
        Metres to the nearest territory of the claim, or None if unplaceable.

        ``include_dependencies`` chooses which polygons count as the claim:
        with it, also every unit whose ``SOVEREIGNT`` is the claimed name
        (Curacao under the Netherlands, Hong Kong under China); without it,
        only the units carrying that name.

        Include them when measuring a coordinate's imprecision (an offshore
        point near Curacao was plausibly logged as the Netherlands). Exclude
        them when measuring a border: the land vocabulary is flat, so a
        dependency and its sovereign are different values, and a point inside
        Curacao must not be zero metres from "the Netherlands".
        """
        if not claimed:
            return None
        memo_key = (claimed, lat, lon, include_dependencies)
        if memo_key in self._cache:
            return self._cache[memo_key]

        index = self.by_name if include_dependencies else self.by_own_name
        geoms = index.get(canonical(claimed))
        if not geoms:
            self._cache[memo_key] = None
            return None

        point = Point(lon, lat)
        distance = None
        for geom in geoms:
            if geom.contains(point):
                distance = 0.0
                break
            nearest, _ = nearest_points(geom, point)
            _, _, metres = self.geod.inv(lon, lat, nearest.x, nearest.y)
            if distance is None or metres < distance:
                distance = metres
        self._cache[memo_key] = distance
        return distance
