"""
Grade a sample's coordinate against the place name it claims, and build the
``location_hierarchy`` rows it contributes.

This is the *policy* half of the library: the geocoder says where a point is,
this says whether the sample is consistent and what the fact table records.
Every caller (service, importer, batch scan) should go through it, so that one
sample is never graded two ways.

Severity grading
----------------
``error``       The coordinate is nowhere near the stated place.
``uncheckable`` ``location_resolution`` is ``country`` or ``continent``, so the
                coordinates were derived from the place name rather than
                observed. Comparing them back to that name is circular.
``ok``          Agrees.

There is deliberately no ``warn``: a fail-closed importer must be able to
answer "did this import cleanly?", and a warning that fires on every import is
one nobody reads.
"""

import collections

from .claims import CLAIM_BORDER_M, CLAIM_NEAR_M, ClaimChecker
from .fallback import (
    DISTANCE_THRESHOLD_M,
    DISTANCE_THRESHOLD_POLAR_M,
    POLAR_LATITUDE_THRESHOLD,
)
from .containment import is_contained_in
from .country_aliases import same_country
from .location_exceptions import ACCEPTED_PREFIX, LocationExceptions
from .hierarchy_table import (
    ARTIFICIAL_NO_LOCATION,
    DERIVED_RESOLUTIONS,
    QUALIFYING_RESOLUTIONS,
    hierarchy_rows_for,
    hierarchy_sort_key,
)
from .samples import (
    CODE_ACCEPTED_EXCEPTION,
    CODE_CLAIM_TOO_FAR,
    CODE_DERIVED_COORDINATES,
    CODE_DIFFERENT_LOCATION,
    CODE_NO_COORDINATES,
    CODE_NOT_ON_LAND,
    CODE_NOT_ON_SEA,
    CODE_OK,
    CODE_OUT_OF_RANGE,
    CODE_UNKNOWN_PLACE,
    CODE_UNRELATED_SEA,
    ERROR_CODES,
    NO_COORDINATES,
    BatchResult,
    Sample,
    Verdict,
    coordinate_error,
    distinct_coordinates,
)
from .sea_relatedness import SeaRelatedness
from .vocabulary import (
    check_vocabulary_covers_iho,
    expected_type,
    published_sea,
)


#: Re-exported so a caller has one import for the whole checking surface.
#: They live in ``samples`` because a client of the service needs them without
#: the geo stack.
__all__ = [
    "ACCEPTED_PREFIX",
    "LocationChecker",
    "Sample",
    "Verdict",
    "BatchResult",
    "NO_COORDINATES",
    "ERROR_CODES",
    "CODE_ACCEPTED_EXCEPTION",
    "CODE_CLAIM_TOO_FAR",
    "CODE_DERIVED_COORDINATES",
    "CODE_DIFFERENT_LOCATION",
    "CODE_NO_COORDINATES",
    "CODE_NOT_ON_LAND",
    "CODE_NOT_ON_SEA",
    "CODE_OK",
    "CODE_OUT_OF_RANGE",
    "CODE_UNKNOWN_PLACE",
    "CODE_UNRELATED_SEA",
    "coordinate_error",
    "distinct_coordinates",
    "matches",
]


def _named(value: str) -> str:
    """Quote a place name for a message, or say plainly that there is none.

    An empty ``geographic_location`` matches nothing and fails; "the sample
    states ''" would read like a geocoder bug rather than missing metadata.
    """
    return f"'{value}'" if value else "no location"


def _distance(metres: float) -> str:
    """A rounded distance for a message: metres below 1 km, km above.

    The sentence conveys scale ("a border" vs "the wrong continent"); exact
    figures belong in a separate report column.
    """
    if metres < 1000:
        return f"{metres:,.0f} m"
    return f"{metres / 1000:,.0f} km"


def matches(expected: str, result: dict, loc_type: str, marine=None) -> bool:
    """
    Does the geocoded result agree with the stated place name?

    The sea name is compared in the vocabulary's spelling, not IHO's, because
    that is the spelling the geocoder publishes. Comparing against IHO's raw
    name would reject the geocoder's own answer (``Barents Sea`` vs IHO's
    ``Barentsz Sea``).
    """
    if loc_type == "land":
        return same_country(expected, result.get("country", ""))
    ocean_chain = result.get("ocean", "") or ""
    sea_name = published_sea(result, marine)
    return (
        expected.lower() == sea_name.lower()
        or expected.lower() in ocean_chain.lower()
    )



class LocationChecker:
    """
    One place that decides whether a coordinate agrees with its stated location.

    Constructed around a live ``ReverseGeocoder``; the vocabulary and the IHO
    relatedness test are optional, and each one absent only makes the checker
    more forgiving, never wrong in a new way:

    - without ``vocabulary``, sea names are classified by regex and published in
      IHO's own spelling;
    - without ``relatedness``, a sea-against-sea disagreement falls through to
      the generic paths instead of being decided by the gazetteer chain;
    - without ``exceptions``, coordinates verified by hand against Natural
      Earth's gaps are graded ``error`` (the one input whose absence makes the
      checker stricter).

    The vocabulary is verified against the IHO layer on construction, so a
    service or run cannot start if it could publish a sea name the vocabulary
    lacks.
    """

    def __init__(
        self,
        geocoder,
        vocabulary=None,
        relatedness=None,
        *,
        exceptions: LocationExceptions | None = None,
        measure_claims: bool = True,
        verify_vocabulary: bool = True,
    ):
        self.geocoder = geocoder
        self.vocabulary = vocabulary
        self.relatedness = relatedness
        # Verified-correct coordinates; can only clear an error. See
        # location_exceptions.py.
        self.exceptions = exceptions
        # Without it no fallback is graded and every direct-hit disagreement
        # is an error. Only a caller that reads none of those grades should
        # turn it off.
        self.claims = ClaimChecker(geocoder.land_geocoder) if measure_claims else None
        if verify_vocabulary:
            check_vocabulary_covers_iho(geocoder.sea_geocoder.seas, vocabulary)

    @classmethod
    def from_geocoder(cls, geocoder, vocabulary=None, containment=None, **kwargs):
        """Build the relatedness test from what the geocoder already loaded.

        Uses the sea hierarchy cache and the IHO layer already in memory. Never
        fetches: an MRGID missing from the cache stays unresolvable.
        """
        sea_layer = geocoder.sea_geocoder.seas
        relatedness = SeaRelatedness(
            geocoder.sea_geocoder.hierarchy_cache.cache,
            zip(sea_layer["NAME"], sea_layer["MRGID"]),
            containment,
        )
        return cls(geocoder, vocabulary, relatedness, **kwargs)

    # -- small delegations, so a caller never needs the vocabulary in hand ----

    def expected_type(self, geographic_location: str) -> str:
        return expected_type(geographic_location, self.vocabulary)

    def published_sea(self, result: dict) -> str:
        return published_sea(result, self.vocabulary)

    def claim_distance(
        self, claimed: str, sample: Sample, include_dependencies: bool = True
    ) -> float | None:
        """
        Metres from the sample's coordinate to the claimed country, or None.

        None means no measurement (no ``ClaimChecker``, a name Natural Earth
        has no unit for, or no coordinate); an absent measurement neither
        creates nor clears a grade.

        ``include_dependencies`` is passed to ``ClaimChecker.distance_m``.
        """
        if self.claims is None:
            return None
        coord = sample.coordinate
        if coord is None:
            return None
        return self.claims.distance_m(
            claimed, coord[0], coord[1], include_dependencies=include_dependencies
        )

    # -- the two questions callers actually ask -------------------------------

    def check(self, sample: Sample, results: dict | None = None) -> Verdict:
        """Grade one sample, geocoding it unless ``results`` already has it."""
        coord = sample.coordinate
        if coord is None:
            return Verdict(sample.id, NO_COORDINATES, code=CODE_NO_COORDINATES)

        lat, lon = coord
        invalid = coordinate_error(lat, lon)
        if invalid:
            return Verdict(
                sample.id,
                "error",
                invalid,
                code=CODE_OUT_OF_RANGE,
                coordinate=coord,
            )

        loc_type = self.expected_type(sample.geographic_location)
        key = (lat, lon, loc_type)
        if results is not None and key in results:
            result = results[key]
        else:
            result = self.geocoder.geocode(lat, lon, loc_type)

        grade, code, reason = self.grade(sample, result, loc_type)
        return Verdict(
            sample.id,
            grade,
            reason,
            code=code,
            result=result,
            loc_type=loc_type,
            coordinate=coord,
        )

    def hierarchy_rows(self, lat: float, lon: float, resolution: str, result: dict):
        """The rows one (coordinate, resolution) block contributes."""
        return hierarchy_rows_for(lat, lon, resolution, result, self.vocabulary)

    def check_batch(self, samples, *, progress=None) -> BatchResult:
        """
        Grade many samples and build their hierarchy rows in one pass.

        Geocodes once per distinct ``(latitude, longitude, type)``. Resolution
        only changes how much of the result is published, so hierarchy rows are
        built per distinct ``(coordinate, resolution)`` block: the same point
        recorded at "city" and at "country" yields two blocks.

        ``progress`` is called as ``progress(done, total)`` after each geocode.
        """
        samples = list(samples)

        # 1. one geocode per distinct (lat, lon, type)
        todo: dict[tuple[float, float, str], None] = {}
        for sample in samples:
            coord = sample.coordinate
            if coord is None or coordinate_error(*coord):
                continue
            todo[(*coord, self.expected_type(sample.geographic_location))] = None

        results: dict[tuple[float, float, str], dict] = {}
        for done, key in enumerate(todo, 1):
            results[key] = self.geocoder.geocode(*key)
            if progress is not None:
                progress(done, len(todo))

        # 2. which blocks earn hierarchy rows
        #
        # Check `artificial` before location_resolution: a control/mock may
        # carry an ordinary resolution that says nothing about its coordinate.
        resolutions_by_key = collections.defaultdict(set)
        for sample in samples:
            if sample.artificial in ARTIFICIAL_NO_LOCATION:
                continue
            # A blank resolution is "not modelled", not "unknown": full hierarchy.
            if (sample.location_resolution
                    and sample.location_resolution not in QUALIFYING_RESOLUTIONS):
                continue
            coord = sample.coordinate
            if coord is None or coordinate_error(*coord):
                continue
            key = (*coord, self.expected_type(sample.geographic_location))
            resolutions_by_key[key].add(sample.location_resolution)

        # One point may be labelled as sea by one sample and as land by
        # another, so a block can be built from both results. Both publish the
        # ``coast`` scheme; a shared key is kept once (primary key), and sea is
        # built first so its value wins regardless of sample order.
        hierarchy, seen = [], set()
        for (lat, lon, loc_type), used in sorted(
            resolutions_by_key.items(), key=lambda item: item[0][2] != "sea"
        ):
            result = results.get((lat, lon, loc_type))
            if result is None or result.get("type") == "unknown":
                continue  # unresolved coordinate publishes nothing
            for resolution in used:
                for row in self.hierarchy_rows(lat, lon, resolution, result):
                    pk = (row["latitude"], row["longitude"], row.get("location_resolution"),
                          row["scheme"], row["level"], row["key"])
                    if pk not in seen:
                        seen.add(pk)
                        hierarchy.append(row)
        hierarchy.sort(key=hierarchy_sort_key)

        verdicts = [self.check(sample, results) for sample in samples]
        return BatchResult(verdicts, hierarchy, results, len(resolutions_by_key))

    # -- the grading itself ---------------------------------------------------

    def grade(self, sample: Sample, result: dict, loc_type: str) -> tuple[str, str, str]:
        """
        Return (severity, code, reason) for one sample's geocode.

        ``code`` is the rule that fired; branch or group on it. ``reason`` is a
        human-readable sentence naming the claim, where the coordinate landed
        and the distance between them; its wording may change.

        The rules are in ``_grade``; this layers the exceptions file over them,
        and only over ``error``, so an exception can only clear a rejection. An
        entry whose rule no longer fires does nothing and is reported by
        ``exceptions.unused()``.
        """
        severity, code, reason = self._grade(sample, result, loc_type)
        if severity != "error" or self.exceptions is None:
            return severity, code, reason
        coord = sample.coordinate
        if coord is None:
            return severity, code, reason
        accepted = self.exceptions.accepts(
            coord[0], coord[1], sample.geographic_location
        )
        if accepted is None:
            return severity, code, reason
        return "ok", CODE_ACCEPTED_EXCEPTION, f"{ACCEPTED_PREFIX}{accepted}"

    def _grade(self, sample: Sample, result: dict, loc_type: str) -> tuple[str, str, str]:
        """
        The grading rules themselves, before the exceptions file is applied.

        The distance from the point to the claimed country (``self.claims``)
        decides both disagreement branches at different scales: on a shoreline
        fallback the claim passes within ``CLAIM_NEAR_M``; on a direct hit it
        passes within ``CLAIM_BORDER_M`` (a border, not a different place).
        ``self.relatedness`` decides sea-vs-sea disagreements through the IHO
        hierarchy.
        """
        expected = sample.geographic_location
        resolution = sample.location_resolution
        # Only for the search radius in the not-on-land message. check()
        # rejects a sample without a coordinate before grading.
        lat = sample.coordinate[0] if sample.coordinate else 0.0

        # Compare on the canonical spelling, so aliases ("Sea of Cortez") match.
        if self.vocabulary is not None:
            expected = self.vocabulary.canonical(expected) or expected

        # Messages quote the name exactly as the sample states it, never the
        # canonical substitute, so the reader can find it in their metadata.
        stated = _named(sample.geographic_location)

        if result.get("type") == "unknown":
            # Nothing of the expected kind within the fallback threshold. An
            # error at every resolution, derived ones included: a country
            # centroid in the sea is a bad centroid. The message names the
            # search radius (wider south of 60 S) so it can be checked on a map.
            if loc_type == "land":
                radius = (
                    DISTANCE_THRESHOLD_POLAR_M
                    if lat < POLAR_LATITUDE_THRESHOLD
                    else DISTANCE_THRESHOLD_M
                )
                # Name the distance to the claim as the direct-hit message
                # does, so the reader sees the scale of the error either way.
                detail = ""
                if self.claims is not None and sample.geographic_location:
                    claim_distance = self.claim_distance(
                        expected, sample, include_dependencies=False
                    )
                    if claim_distance is not None:
                        detail = (
                            f"; the nearest land called {stated} is "
                            f"{_distance(claim_distance)} away"
                        )
                    elif self.claim_distance(expected, sample) is None:
                        detail = (
                            f"; {stated} is not a country or territory that "
                            f"Natural Earth maps either"
                        )
                return "error", CODE_NOT_ON_LAND, (
                    f"the sample states {stated}, but the coordinate is not on "
                    f"land: there is none at all within {_distance(radius)} of it"
                    f"{detail}"
                )
            return "error", CODE_NOT_ON_SEA, (
                f"the sample states the sea {stated}, but the coordinate is not in "
                f"any IHO sea, nor within {_distance(DISTANCE_THRESHOLD_M)} of one"
            )

        # The coordinate was derived from the name it would be checked against.
        derived = (
            f"location_resolution is '{resolution}', so the coordinates were "
            f"derived from {stated} rather than observed and cannot contradict it"
        )

        if matches(expected, result, loc_type, self.vocabulary):
            if resolution in DERIVED_RESOLUTIONS:
                return "uncheckable", CODE_DERIVED_COORDINATES, derived
            return "ok", CODE_OK, ""

        if resolution in DERIVED_RESOLUTIONS:
            return "uncheckable", CODE_DERIVED_COORDINATES, derived

        # The geocoder's answer, sea names in the published spelling (as in
        # matches()).
        found = result.get("country") or self.published_sea(result) or ""

        # The claim is a territory inside the returned unit that Natural Earth
        # has no admin-0 polygon for. See containment.py. The names agree, but
        # the point must also be in the territory, not elsewhere in the unit
        # (Svalbard, not Oslo); otherwise fall through and report the distance.
        if is_contained_in(expected, found):
            inside = self.claim_distance(expected, sample, include_dependencies=False)
            limit = CLAIM_NEAR_M if result.get("is_fallback") else CLAIM_BORDER_M
            if inside is None or inside <= limit:
                return "ok", CODE_OK, ""

        # Sea vs sea: related (one contains the other in the IHO hierarchy) or
        # different water. A verdict either way is terminal; an unrelated pair
        # is wrong however short the fallback hop, since a sea fallback is at
        # most 10 km and seas are much larger.
        if loc_type == "sea" and self.relatedness is not None:
            related = self.relatedness.related(
                expected, found, ocean=result.get("ocean", ""), mrgid=result.get("mrgid")
            )
            if related is True:
                return "ok", CODE_OK, ""
            if related is False:
                return "error", CODE_UNRELATED_SEA, (
                    f"the sample states the sea {stated}, but the coordinate is in "
                    f"{_named(found)} -- different water, not a wider or narrower "
                    f"name for the same water: neither contains the other in the "
                    f"IHO hierarchy"
                )
            # None: a name with no resolvable chain (a sea without an IHO
            # polygon and no containment entry, an unknown spelling, or an
            # MRGID missing from the hierarchy cache). No opinion, so fall
            # through to the generic paths below.

        # Natural Earth has no unit or territory of that name, so no coordinate
        # could match it: a problem with the name, not with the coordinate.
        # Decided before the fallback branch so that the verdict does not
        # depend on which side of the coastline the point falls.
        if (
            loc_type == "land"
            and self.claims is not None
            and self.claim_distance(expected, sample) is None
        ):
            if result.get("is_fallback"):
                where = f"the land nearest the coordinate is {_named(found)}"
            else:
                where = f"the coordinate is inside {_named(found)}"
            if not sample.geographic_location:
                return "error", CODE_UNKNOWN_PLACE, (
                    f"the sample states no location, so the coordinate has "
                    f"nothing to agree with; {where}"
                )
            return "error", CODE_UNKNOWN_PLACE, (
                f"the sample states {stated}, which is not a country or territory "
                f"that Natural Earth maps, so no coordinate can match it; {where}"
            )

        if result.get("is_fallback"):
            # A fallback names the *nearest* unit, not one the point is in, so
            # only the claim is tested: reject when the claimed country is far
            # from the point. This applies at every fallback distance; how far
            # the point is from land measures the coordinate's imprecision, not
            # whether the claim is wrong (a point 1 km off one coast can be
            # labelled with a country on another continent).
            claim_distance = self.claim_distance(expected, sample)
            if claim_distance is not None and claim_distance > CLAIM_NEAR_M:
                # Both distances: how wrong the name is, and how imprecise the
                # coordinate is.
                nearest = _named(found)
                hop = result.get("fallback_distance_m")
                return "error", CODE_CLAIM_TOO_FAR, (
                    f"the sample states {stated}, but the coordinate is "
                    f"{_distance(claim_distance)} from the nearest land belonging to "
                    f"{stated}"
                    + (
                        f"; the land nearest the coordinate is {nearest}, "
                        f"{_distance(hop)} away"
                        if nearest != "no location" and hop is not None
                        else ""
                    )
                )

            # The claim is near the point: not evidence against the sample.
            return "ok", CODE_OK, ""

        # A direct hit is evidence against the claim, unless the claimed
        # country is within CLAIM_BORDER_M (a border disagreement, not a
        # different place). Measured without dependencies: otherwise a point
        # inside Curacao would be zero metres from the Netherlands and forgiven
        # at any threshold.
        border_distance = self.claim_distance(
            expected, sample, include_dependencies=False
        )
        if border_distance is not None and border_distance <= CLAIM_BORDER_M:
            return "ok", CODE_OK, (
                f"the coordinate is inside {_named(found)}, but {stated} is only "
                f"{_distance(border_distance)} away: a border, not a different place"
            )

        # Name both places and the distance between them: 14 km suggests a
        # misplaced site, 9,000 km a scrambled column.
        detail = ""
        # border_distance is None only for a name Natural Earth knows solely as
        # a sovereign of other units (e.g. Kashmir).
        if loc_type == "land" and border_distance is not None:
            detail = (
                f"; the nearest land called {stated} is "
                f"{_distance(border_distance)} away"
            )
        return "error", CODE_DIFFERENT_LOCATION, (
            f"the sample states {stated}, but the coordinate is inside "
            f"{_named(found)}{detail}"
        )
