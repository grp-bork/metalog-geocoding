"""
The data the checker takes in and hands back.

Standard library only, so a client of the geocoding service can build a
request and read a response without the geo stack. ``distinct_coordinates()``
is the dedup both client and server run, so they agree on what was asked.
"""

from dataclasses import dataclass, field

#: Grade for a sample with no usable coordinate. Not an error: a coordinate is
#: optional, and without one there is nothing to contradict. Distinct from
#: ``uncheckable`` so the two can be counted separately.
NO_COORDINATES = "no coordinates"

#: ``Verdict.code`` values: the stable, machine-readable half of a verdict.
#:
#: ``reason`` is a per-sample sentence for a human and may be reworded; compare
#: and group on ``code``.
CODE_OUT_OF_RANGE = "coordinate_out_of_range"
CODE_NOT_ON_LAND = "not_on_land"
CODE_NOT_ON_SEA = "not_on_sea"
CODE_UNRELATED_SEA = "unrelated_sea"
CODE_CLAIM_TOO_FAR = "claim_too_far"
CODE_DIFFERENT_LOCATION = "different_location"
CODE_UNKNOWN_PLACE = "unknown_place"
CODE_DERIVED_COORDINATES = "derived_coordinates"
CODE_NO_COORDINATES = "no_coordinates"
CODE_ACCEPTED_EXCEPTION = "accepted_exception"
CODE_OK = "ok"

#: Every code an error verdict can carry, so a caller can assert it handles
#: them all.
ERROR_CODES = (
    CODE_OUT_OF_RANGE,
    CODE_NOT_ON_LAND,
    CODE_NOT_ON_SEA,
    CODE_UNRELATED_SEA,
    CODE_CLAIM_TOO_FAR,
    CODE_DIFFERENT_LOCATION,
    CODE_UNKNOWN_PLACE,
)


def coordinate_error(lat: float, lon: float) -> str | None:
    """Reject coordinates that cannot exist before they reach the geocoder.

    pyproj returns NaN off the globe, so an impossible coordinate would
    otherwise produce a plausible-looking result with a NaN distance.
    """
    if not -90 <= lat <= 90:
        return (
            f"the coordinate cannot exist: latitude {lat} is outside -90..90 "
            f"(are latitude and longitude swapped?)"
        )
    if not -180 <= lon <= 180:
        return f"the coordinate cannot exist: longitude {lon} is outside -180..180"
    return None


@dataclass(frozen=True)
class Sample:
    """The sample attributes needed to place and grade one coordinate.

    Coordinates are kept as recorded, so a report can echo the sample's own
    value. ``coordinate`` parses them and returns None if absent, blank or
    unparseable.
    """

    id: str = ""
    latitude: str | float | None = ""
    longitude: str | float | None = ""
    geographic_location: str = ""
    location_resolution: str = ""
    artificial: str = ""

    @classmethod
    def from_mapping(cls, fields, id: str = "") -> "Sample":
        """Build one from a dict of metadata items, e.g. a pivoted export row."""
        return cls(
            id=id,
            latitude=fields.get("latitude", ""),
            longitude=fields.get("longitude", ""),
            geographic_location=fields.get("geographic_location", ""),
            location_resolution=fields.get("location_resolution", ""),
            artificial=fields.get("artificial", ""),
        )

    @property
    def coordinate(self) -> tuple[float, float] | None:
        if self.latitude in (None, "") or self.longitude in (None, ""):
            return None
        try:
            return float(self.latitude), float(self.longitude)
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Verdict:
    """What the checker concluded about one sample.

    ``grade`` is the decision, ``code`` the rule that made it, and ``reason``
    a human-readable explanation (the stated value, where the coordinate
    landed, the distance between them). Compare on ``code``; show ``reason``.
    """

    sample_id: str
    grade: str
    reason: str = ""
    #: One of the ``CODE_*`` constants above. Stable across rewordings.
    code: str = ""
    #: The geocode this was graded against; empty when there was none.
    result: dict = field(default_factory=dict)
    #: "land" or "sea" -- which lookup the claimed name asked for, not what was
    #: found. Empty when the coordinate never reached the geocoder.
    loc_type: str = ""
    coordinate: tuple[float, float] | None = None


@dataclass
class BatchResult:
    """The outcome of checking many samples at once."""

    #: One per input sample, in input order.
    verdicts: list[Verdict]
    #: ``location_hierarchy`` rows, deduplicated by block and sorted -- so two
    #: runs over the same data diff as no-ops.
    hierarchy: list[dict]
    #: The geocodes, one per distinct (latitude, longitude, type).
    results: dict[tuple[float, float, str], dict]
    #: Distinct (coordinate, type) pairs that contributed hierarchy rows.
    blocks: int


def distinct_coordinates(samples) -> list[tuple[float, float]]:
    """The unique, geocodable coordinates in ``samples``, in first-seen order.

    This is what a batch request should carry: samples reference a
    coordinate, and only the coordinate is geocoded (real corpora repeat
    coordinates heavily). Impossible coordinates are left out; they are graded
    without the geocoder.
    """
    seen: dict[tuple[float, float], None] = {}
    for sample in samples:
        coord = sample.coordinate
        if coord is None or coordinate_error(*coord):
            continue
        seen.setdefault(coord, None)
    return list(seen)
