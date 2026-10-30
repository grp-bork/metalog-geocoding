"""
How far each of several coordinates is from a named place.

Built for coordinate repair: a caller holding a broken coordinate (latitude
and longitude swapped, a sign lost) generates the candidate fixes and asks
which of them lands in the place the sample names. This module answers with a
distance per coordinate and per candidate place; deciding which fix to accept
is the caller's.

A name resolves through its ``place_names.interpretations()`` in order. For
each reading, the boundary layers the geocoder already holds are tried first,
then Nominatim:

1. a Natural Earth country (through ``country_aliases``) or a territory in
   ``containment.CONTAINED_IN``, against its own polygon;
2. a Natural Earth admin-1 unit -- every unit of that name, since ``Georgia``
   and ``Southern`` name several;
3. a sea in the marine vocabulary: every IHO polygon published under that
   name or with it in its MarineRegions chain, so ``Atlantic Ocean`` is all
   of its seas;
4. Nominatim, keeping only results that are places (``ACCEPTED_FEATURES``)
   and whose names contain the query's words (``name_matches``). Nominatim
   matches fragments, so without both checks ``Chilean`` is a car-rental
   desk and ``Northern Baffin Bay`` a shop called Northern Store.

Local layers go first because they agree with ``/check`` and because
OpenStreetMap often maps a sea only as a label point, which is useless for
distances.

Every candidate is reported, not one guess: with eight fixes to test and
fifty-seven lakes called ``Mary Lake``, the caller needs to know a match could
be chance. ``area_km2`` says how much a hit inside the place is worth.
"""

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pyproj import Geod
from shapely import clip_by_rect
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

from .containment import CONTAINED_IN
from .country_aliases import canonical, normalise
from .nominatim import LookupUnavailable, NominatimClient
from .place_names import interpretations
from .wikidata import WikidataClient

#: Nominatim results kept as candidates, as OSM ``category`` -> accepted
#: ``type`` values (None: every type). An allow-list because the junk is
#: open-ended: restaurants, cafés, schools, shops and streets are named after
#: places without being them, and every class has some.
ACCEPTED_FEATURES = {
    "boundary": {"administrative", "protected_area", "national_park", "region", "census"},
    "place": None,
    "natural": None,
    "water": None,
    "waterway": None,
    "geological": None,
    "landuse": {"forest", "meadow", "grass", "reservoir", "basin", "salt_pond", "farmland", "orchard", "vineyard"},
    "leisure": {"nature_reserve", "park"},
    "amenity": {"research_institute"},
}

#: Words a name may lack and still match: ``Lake Tanatar`` is mapped as
#: ``Tanatar``, ``Atacama Desert`` as ``Desierto de Atacama``. Only words
#: that say what kind of feature it is, never which one.
GENERIC_WORDS = frozenset(
    "the of de del la le les los las el di da do das dos and "
    "ocean lake lago lac river rio sea bay gulf strait fjord fiord inlet harbour harbor "
    "estuary lagoon laguna reservoir spring springs creek stream delta "
    "mountain mountains mts mt mount range desert plateau basin valley glacier "
    "island islands isle peninsula forest park reserve national nature region area "
    "province county city district state prefecture "
    # The same in the languages OSM most often names these features in.
    "desierto deserto deserti bahia baia golfo golfe isla ilha ile isola "
    "lagoa mar mer meer see bucht bugt fiume fleuve riviere rivier fluss "
    "monte montes montagne montagnes gebirge cordillera sierra".split()
)

#: Nominatim results kept relative to the best one's importance.
MIN_RELATIVE_IMPORTANCE = 0.2

#: How near an OSM feature must be to a Wikidata item's point to lend it its
#: outline. A point is a label position, often well inside a large feature.
WIKIDATA_OSM_MATCH_M = 25_000

#: Wikidata items kept beside the most prominent one if they have at least this
#: share of its sitelinks (Stanley in Durham, the Falklands and Hong Kong).
WIKIDATA_RIVAL_SHARE = 0.5
WIKIDATA_MAX_CANDIDATES = 3

#: Descriptions of Wikidata items that have a coordinate but are not places.
RE_NOT_A_PLACE = re.compile(
    r"\b(language|dialect|university|college|school|company|album|song|film|"
    r"journal|band|team|club|railway station|airport|hotel|restaurant|"
    r"scientific article|video game|ship|vessel)\b",
    re.IGNORECASE,
)

#: The optional Natural Earth layers that give Wikidata items outlines, and
#: the spelling of their columns (the marine layer's are lower case).
REGIONS_LAYER = "ne_10m_geography_regions_polys.parquet"
MARINE_LAYER = "ne_10m_geography_marine_polys.parquet"
OPTIONAL_OUTLINE_LAYERS = {
    REGIONS_LAYER: ("natural_earth_region", "WIKIDATAID", "NAME_EN", "NAME", "FEATURECLA"),
    MARINE_LAYER: ("natural_earth_marine", "wikidataid", "name_en", "name", "featurecla"),
}

#: Countries whose admin-1 postal codes stand alone in sample metadata.
POSTAL_COUNTRIES = frozenset({"USA", "CAN", "AUS"})

#: Attribution required for answers derived from OpenStreetMap.
OSM_ATTRIBUTION = (
    "Place geometry from OpenStreetMap via Nominatim, "
    "(c) OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)"
)

#: A bare two-letter capitalised name is a country code (``ES``, ``NZ``), as
#: sample sheets abbreviate a country column.
RE_ISO_A2 = re.compile(r"^[A-Z]{2}$")

STATUS_RESOLVED = "resolved"
#: Nothing found under any reading: not evidence against a coordinate.
STATUS_UNRESOLVED = "unresolved"
#: Needs a Nominatim lookup this request had no budget left for; ask again.
STATUS_PENDING = "pending"
#: Nominatim could not be reached; ask again later.
STATUS_LOOKUP_FAILED = "lookup_failed"

_GEOD = Geod(ellps="WGS84")


def _geodesic_area_m2(polygon) -> float:
    """Area in square metres. IHO's Arctic Ocean overshoots the pole by 4e-14
    degrees, for which pyproj returns NaN, so clip to the globe first."""
    minx, miny, maxx, maxy = polygon.bounds
    if miny < -90 or maxy > 90:
        polygon = clip_by_rect(polygon, -180, -90, 180, 90)
    return abs(_GEOD.geometry_area_perimeter(polygon)[0])


def _words(name: str) -> set[str]:
    return set(normalise(name).split())


def _result_names(result: dict) -> list[str]:
    """Every name a Nominatim result carries, in any language."""
    names = [result.get("name") or ""]
    names += list((result.get("namedetails") or {}).values())
    return [n for n in names if n]


def name_matches(query: str, names: list[str]) -> bool:
    """Is one of ``names`` the query, up to feature words?

    The distinctive words (those not in ``GENERIC_WORDS``) must be the same
    on both sides: ``Atacama Desert`` is ``Desierto de Atacama``, but
    ``Africa`` is not ``South Africa`` and ``Northern Baffin Bay`` is not
    ``Northern Store``. Accents, case and punctuation are ignored. A query of
    feature words only (``Lake``) must match whole. A query with a feature
    word needs a name with one, so ``Sierra Nevada`` is not the state of
    ``Nevada`` and ``Blue Mountains`` not a hamlet called ``Blue``. Whole
    words only: a prefix
    rule would find ``Taihu`` for ``Lake Tai``, but also ``Indiana`` for
    ``Indian``.
    """
    wanted = _words(query)
    distinctive = wanted - GENERIC_WORDS
    for name in names:
        words = _words(name)
        if distinctive and wanted != distinctive and words == words - GENERIC_WORDS:
            continue
        if distinctive and distinctive == words - GENERIC_WORDS:
            return True
        if not distinctive and wanted == words:
            return True
    return False


@dataclass
class Candidate:
    """One place a name may refer to, as one or more geometries."""

    source: str  # natural_earth_admin0 | natural_earth_admin1 | iho | osm
    id: str
    label: str
    kind: str
    parts: list
    #: Alpha-3, as ``/geocode`` publishes it; None for OpenStreetMap places.
    country_code: Optional[str] = None
    #: ISO 3166-1 alpha-2, for the caller's ``country`` hint.
    iso_a2: Optional[str] = field(default=None, repr=False)
    _area_km2: Optional[float] = field(default=None, repr=False)
    _area_done: bool = field(default=False, repr=False)

    @property
    def geometry_type(self) -> str:
        """``polygon``, ``line`` or ``point``: what a distance is measured to."""
        kinds = {p.geom_type for p in self.parts}
        if kinds & {"Polygon", "MultiPolygon"}:
            return "polygon"
        if kinds & {"LineString", "MultiLineString", "LinearRing"}:
            return "line"
        return "point"

    @property
    def area_km2(self) -> Optional[float]:
        """Geodesic area of the polygons; None for a point or a line."""
        if not self._area_done:
            polygons = [p for p in self.parts if p.geom_type in ("Polygon", "MultiPolygon")]
            if polygons:
                self._area_km2 = sum(_geodesic_area_m2(p) for p in polygons) / 1e6
            self._area_done = True
        return self._area_km2

    def distance_m(self, lat: float, lon: float) -> float:
        """Geodesic metres from the point to the place; 0 inside a polygon."""
        point = Point(lon, lat)
        best = None
        for part in self.parts:
            if part.geom_type in ("Polygon", "MultiPolygon") and part.covers(point):
                return 0.0
            nearest, _ = nearest_points(part, point)
            _, _, metres = _GEOD.inv(lon, lat, nearest.x, nearest.y)
            if best is None or metres < best:
                best = metres
        return best

    def as_dict(self) -> dict:
        area = self.area_km2
        return {
            "source": self.source,
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "geometry": self.geometry_type,
            "area_km2": None if area is None else round(area, 1),
            "country_code": self.country_code,
        }


@dataclass
class Resolution:
    name: str
    status: str
    interpreted_as: Optional[str] = None
    candidates: list = field(default_factory=list)
    message: Optional[str] = None


class LookupBudget:
    """How many Nominatim requests one call may still make."""

    def __init__(self, remaining: int):
        self.remaining = remaining


class PlaceResolver:
    """
    Resolve place names to candidate geometries, and measure distances to them.

    Args:
        geocoder: a ``ReverseGeocoder``, whose admin-0, admin-1 and IHO layers
            are reused rather than loaded again.
        vocabulary: the marine vocabulary, for sea names and their spellings.
        nominatim: OSM search and outlines; None skips OSM.
        wikidata: which place a name means; None skips Wikidata. With neither,
            names resolve from the local layers only.
    """

    def __init__(
        self,
        geocoder,
        vocabulary=None,
        nominatim: Optional[NominatimClient] = None,
        wikidata: Optional[WikidataClient] = None,
    ):
        self.vocabulary = vocabulary
        self.nominatim = nominatim
        self.wikidata = wikidata
        land = geocoder.land_geocoder
        sea = geocoder.sea_geocoder
        #: Wikidata QID -> the Natural Earth admin unit carrying it ...
        self._by_qid: dict[str, Candidate] = {}
        #: ... and the geography region or marine area (optional layers).
        self._outlines: dict[str, Candidate] = {}

        country_names = {}
        country_codes = {}
        self._admin0: dict[str, Candidate] = {}
        for _, row in land.adm0.iterrows():
            name = land._country_name(row)
            if not name:
                continue
            a3 = land._str(row.get("ADM0_A3"))
            country_names[a3] = name
            country_codes[a3] = land._country_code(row)
            key = canonical(name)
            if key in self._admin0:
                self._admin0[key].parts.append(row.geometry)
            else:
                self._admin0[key] = Candidate(
                    "natural_earth_admin0", a3, name, "country", [row.geometry],
                    land._country_code(row),
                )
            qid = land._str(row.get("WIKIDATAID"))
            if qid:
                self._by_qid.setdefault(qid, self._admin0[key])

        self._admin1: dict[str, list[Candidate]] = {}
        #: Two-letter state codes, in the countries where they stand alone.
        self._by_postal: dict[str, list[Candidate]] = {}
        by_code = {}
        for _, row in land.adm1.iterrows():
            name = land._str(row.get("name")) or land._str(row.get("name_en"))
            code = land._str(row.get("adm1_code"))
            if not name or not code:
                continue
            iso = land._str(row.get("iso_3166_2")) or ""
            a3 = land._str(row.get("adm0_a3"))
            country = country_names.get(a3, "")
            candidate = Candidate(
                "natural_earth_admin1", code,
                f"{name}, {country}" if country else name,
                (land._str(row.get("type_en")) or "admin-1").lower(),
                [row.geometry], country_codes.get(a3), iso.split("-")[0] or None,
            )
            by_code[code] = candidate
            qid = land._str(row.get("wikidataid"))
            if qid:
                self._by_qid.setdefault(qid, candidate)
            postal = land._str(row.get("postal"))
            if postal and a3 in POSTAL_COUNTRIES:
                self._by_postal.setdefault(postal, []).append(candidate)
            keys = {normalise(name)}
            english = land._str(row.get("name_en"))
            if english:
                keys.add(normalise(english))
            for key in keys:
                self._admin1.setdefault(key, []).append(candidate)

        # Alpha-2 codes per country, read off its admin-1 units' ISO 3166-2
        # prefixes (the admin-0 columns carry alpha-3 only).
        prefixes: dict[str, collections.Counter] = {}
        for candidate in by_code.values():
            if candidate.iso_a2 and len(candidate.iso_a2) == 2 and candidate.iso_a2.isalpha():
                prefixes.setdefault(candidate.country_code, collections.Counter())[candidate.iso_a2] += 1
        # A code goes to the country with most units under it: Sint Maarten's
        # few units also carry NL-, and must not take "NL" from the Netherlands.
        self._by_iso_a2: dict[str, Candidate] = {}
        best: dict[str, int] = {}
        for candidate in self._admin0.values():
            counts = prefixes.get(candidate.country_code)
            if counts and candidate.source == "natural_earth_admin0":
                code, n = counts.most_common(1)[0]
                candidate.iso_a2 = code
                if n > best.get(code, 0):
                    best[code] = n
                    self._by_iso_a2[code] = candidate

        for claimed, territory in CONTAINED_IN.items():
            own = by_code.get(territory.adm1_code)
            if own is not None:
                self._admin0[canonical(claimed)] = Candidate(
                    "natural_earth_admin1", own.id, claimed, "territory",
                    own.parts, own.country_code, own.iso_a2,
                )

        self._load_outlines(geocoder.data_dir)

        # IHO polygons per published sea name, including every name in the
        # polygon's MarineRegions chain.
        self._seas: dict[str, Candidate] = {}
        chains = sea.hierarchy_cache.cache
        for _, row in sea.seas.iterrows():
            names = [row["NAME"]]
            chain = chains.get(int(row["MRGID"])) if row.get("MRGID") else None
            if chain:
                names += [part.strip() for part in chain.split(";") if part.strip()]
            for raw in dict.fromkeys(names):
                published = self._published_sea(raw)
                if published in self._seas:
                    self._seas[published].parts.append(row.geometry)
                else:
                    self._seas[published] = Candidate(
                        "iho", published, published,
                        "ocean" if published.endswith("Ocean") else "sea",
                        [row.geometry],
                    )
                if raw == row["NAME"]:
                    # The polygon's own MRGID identifies the most specific name.
                    self._seas[published].id = str(int(row["MRGID"]))

    def _load_outlines(self, data_dir) -> None:
        """Natural Earth geography regions (ranges, deserts, plateaus) and marine
        polygons (bays, gulfs, straits, small seas) by QID, for the optional
        layers that are there."""
        import geopandas as gpd

        for layer, (source, qid_col, name_en, name_col, class_col) in OPTIONAL_OUTLINE_LAYERS.items():
            path = Path(data_dir) / layer
            if not path.exists():
                continue
            rows = gpd.read_parquet(path, columns=[qid_col, name_en, name_col, class_col, "geometry"])
            for _, row in rows.iterrows():
                qid = row[qid_col]
                if isinstance(qid, str) and qid and qid not in self._outlines:
                    name = row[name_en] if isinstance(row[name_en], str) else str(row[name_col]).title()
                    self._outlines[qid] = Candidate(
                        source, qid, name, str(row[class_col]).lower(), [row.geometry],
                    )

    def _published_sea(self, name: str) -> str:
        if self.vocabulary is not None:
            return self.vocabulary.canonical(name) or name
        return name

    def _local(self, reading: str, country: Optional[str]) -> list[Candidate]:
        country = (country or "").upper()
        found = []
        admin0 = self._admin0.get(canonical(reading))
        code = RE_ISO_A2.match(reading)
        if admin0 is None and code:
            admin0 = self._by_iso_a2.get(reading)
        if admin0 is not None:
            found.append(admin0)
        seen = {(c.source, c.id) for c in found}
        # An admin-1 unit inside a country already found adds nothing (the
        # State of Mexico for "Mexico"); a territory is its own admin-1
        # polygon, listed once.
        inside = {c.country_code for c in found if c.source == "natural_earth_admin0"}
        admin1 = list(self._admin1.get(normalise(reading), []))
        if code:
            # "CA" is Canada or California; both are candidates.
            admin1 += self._by_postal.get(reading, [])
        for candidate in admin1:
            if (candidate.source, candidate.id) in seen or candidate.country_code in inside:
                continue
            if not country or candidate.iso_a2 == country:
                found.append(candidate)
                seen.add((candidate.source, candidate.id))
        sea = self._seas.get(self._published_sea(reading))
        if sea is not None and (self.vocabulary is None or self.vocabulary.canonical(reading)):
            found.append(sea)
        return found

    @staticmethod
    def _osm_candidates(reading: str, results: list) -> list[Candidate]:
        """Nominatim results that are places called ``reading``, as candidates.

        Kept only if at least ``MIN_RELATIVE_IMPORTANCE`` of the best kept
        result's importance: Lake Erie, not also a pond of that name in Nigeria.
        """
        kept = []
        for r in results:
            accepted = ACCEPTED_FEATURES.get(r.get("category"), set())
            if accepted is not None and r.get("type") not in accepted:
                continue
            if not r.get("geojson") or not name_matches(reading, _result_names(r)):
                continue
            try:
                geometry = shape(r["geojson"])
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if geometry.is_empty:
                continue
            kept.append((r, geometry))
        importances = [r.get("importance") for r, _ in kept if r.get("importance")]
        floor = MIN_RELATIVE_IMPORTANCE * max(importances) if importances else 0.0
        return [
            Candidate(
                "osm",
                f"{r.get('osm_type')}/{r.get('osm_id')}",
                r.get("display_name") or r.get("name") or "",
                f"{r.get('category')}:{r.get('type')}",
                [geometry],
            )
            for r, geometry in kept
            if (r.get("importance") or floor) >= floor
        ]

    def _wikidata_candidates(self, reading, hits, country, budget) -> list[Candidate]:
        """Wikidata items called ``reading``, most prominent first.

        An item counts if it has a coordinate, its label or one of its English
        aliases is the reading (``name_matches``), and its description does not
        name a non-place (``NOT_A_PLACE``). Items are ranked by sitelinks, not by
        Wikidata's search order (which puts Melbourne, Florida before Melbourne,
        Victoria); those with at least ``WIKIDATA_RIVAL_SHARE`` of the top
        item's sitelinks are kept as further candidates, up to
        ``WIKIDATA_MAX_CANDIDATES``.
        """
        matching = []
        for hit in hits:
            if not hit.get("coord"):
                continue
            if RE_NOT_A_PLACE.search(hit.get("description") or ""):
                continue
            names = [hit.get("label") or "", hit.get("match") or ""] + list(hit.get("aliases") or [])
            if name_matches(reading, names):
                matching.append(hit)
        if not matching:
            return []
        matching.sort(key=lambda h: -(h.get("sitelinks") or 0))
        top = matching[0].get("sitelinks") or 0
        kept = [h for h in matching if (h.get("sitelinks") or 0) >= WIKIDATA_RIVAL_SHARE * top]
        out = []
        for hit in kept[:WIKIDATA_MAX_CANDIDATES]:
            candidate = self._wikidata_geometry(reading, hit, country, budget)
            if all((candidate.source, candidate.id) != (c.source, c.id) for c in out):
                out.append(candidate)
        return out

    def _wikidata_geometry(self, reading, hit, country, budget) -> Candidate:
        """A candidate for one Wikidata item, with the best geometry available.

        In order: the Natural Earth admin unit carrying the item's QID; a
        local layer under the item's label (``Atlantic`` is IHO's Atlantic
        Ocean, which wins over Natural Earth's so that seas agree with
        ``/check``); the Natural Earth region or marine area carrying the QID;
        the OSM relation the item links to; a Nominatim result with that name
        within ``WIKIDATA_OSM_MATCH_M`` of the item; else its point.
        """
        by_qid = self._by_qid.get(hit["id"])
        if by_qid is not None:
            return by_qid
        lat, lon = hit["coord"]
        label = hit.get("label") or reading
        local = self._local(label, country) if label != reading else []
        if len(local) == 1:
            return local[0]
        outline = self._outlines.get(hit["id"])
        if outline is not None:
            return outline
        description = hit.get("description") or ""
        candidate = Candidate(
            "wikidata", hit["id"],
            f"{label} ({description})" if description else label,
            "wikidata", [Point(lon, lat)],
        )
        if self.nominatim is None:
            return candidate
        geometry = None
        if hit.get("osm_relation"):
            results = self._online(
                lambda: self.nominatim.lookup(f"R{hit['osm_relation']}"),
                self.nominatim.cached_lookup(f"R{hit['osm_relation']}"), budget, 1,
            )
            for r in results:
                try:
                    geometry = shape(r["geojson"]) if r.get("geojson") else None
                except (ValueError, TypeError, KeyError, AttributeError):
                    geometry = None
                if geometry is not None and not geometry.is_empty:
                    candidate.kind = f"{r.get('category')}:{r.get('type')}"
                    candidate.id = f"{hit['id']} (relation/{hit['osm_relation']})"
                    break
                geometry = None
        if geometry is None:
            countrycodes = (country or "").lower()
            results = self._online(
                lambda: self.nominatim.search(reading, countrycodes),
                self.nominatim.cached(reading, countrycodes), budget, 1,
            )
            for osm in self._osm_candidates(reading, results):
                if osm.distance_m(lat, lon) <= WIKIDATA_OSM_MATCH_M:
                    geometry = osm.parts[0]
                    candidate.kind = osm.kind
                    candidate.id = f"{hit['id']} ({osm.id})"
                    break
        if geometry is not None:
            candidate.parts = [geometry]
        return candidate

    @staticmethod
    def _online(fetch, cached, budget, cost):
        """``cached`` if there is one, else ``fetch()`` within the budget.

        Raises ``_OutOfBudget``; lets ``LookupUnavailable`` through.
        """
        if cached is not None:
            return cached
        if budget is not None:
            if budget.remaining < cost:
                raise _OutOfBudget()
            budget.remaining -= cost
        return fetch()

    def resolve(self, name: str, country: Optional[str] = None, budget: Optional[LookupBudget] = None) -> Resolution:
        """Candidates for ``name`` from the first reading that finds any.

        For each reading: the local layers, then Wikidata (which place), then
        Nominatim alone. ``country`` (ISO 3166-1 alpha-2) narrows admin-1 units
        and Nominatim to one country; a country or sea name is not narrowed.
        ``budget`` caps the online requests; None means no cap.
        """
        readings = interpretations(name)
        if not readings:
            return Resolution(name, STATUS_UNRESOLVED, message="empty place name")
        countrycodes = (country or "").lower()
        try:
            for reading in readings:
                local = self._local(reading, country)
                if local:
                    return Resolution(name, STATUS_RESOLVED, reading, local)
                if self.wikidata is not None:
                    hits = self._online(
                        lambda: self.wikidata.search(reading),
                        self.wikidata.cached(reading), budget, 2,
                    )
                    chosen = self._wikidata_candidates(reading, hits, country, budget)
                    if chosen:
                        return Resolution(name, STATUS_RESOLVED, reading, chosen)
                if self.nominatim is not None:
                    results = self._online(
                        lambda: self.nominatim.search(reading, countrycodes),
                        self.nominatim.cached(reading, countrycodes), budget, 1,
                    )
                    found = self._osm_candidates(reading, results)
                    if found:
                        return Resolution(name, STATUS_RESOLVED, reading, found)
        except _OutOfBudget:
            return Resolution(
                name, STATUS_PENDING,
                message="needs an online lookup and this request's budget is spent; ask again",
            )
        except LookupUnavailable as exc:
            return Resolution(name, STATUS_LOOKUP_FAILED, message=str(exc))
        return Resolution(
            name, STATUS_UNRESOLVED,
            message=f"no place found for any reading: {', '.join(readings)}",
        )


class _OutOfBudget(Exception):
    """A lookup is needed and the request's budget is spent."""
