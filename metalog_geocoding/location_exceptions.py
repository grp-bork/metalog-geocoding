"""
Coordinates verified by hand as correct where the grader says ``error``.

A ``location_exceptions.tsv`` entry says: this exact coordinate, claiming this
exact place, has been checked against the source and the geocoder is wrong
about it. Every other acceptance in ``checker.py`` is a rule that measures
something; this is a list.

Why a list rather than a rule
-----------------------------
The cases here are points where **Natural Earth has no polygon for the ground
the sample stands on**: a ~1 km2 mangrove cay, a small inhabited island, a
reclaimed delta coast newer than the 10m coastline, or a water body the IHO
layer does not carry (the Caspian has no IHO polygon, so a Caspian sample can
only correctly state the bordering country). Distance cannot separate them from
real errors: a correct cay sits 15 km from its country while wrong coordinates
sit 12-23 km offshore, and the delta is 40 km out. There is no threshold.

What an entry can do
--------------------
It can only turn ``error`` into ``ok``. It is applied after grading, so it
never reaches ``uncheckable``, ``no coordinates`` or an impossible coordinate.
An entry whose rule no longer fires does nothing; ``unused()`` reports it.
It publishes nothing: these coordinates resolve to no land, so they contribute
no ``location_hierarchy`` rows.

Keyed on ``(latitude, longitude, geographic_location)``, not on a study:
geography does not depend on who asked.

Standard library only, so a client of the service can read the file without
the geo stack.
"""

import csv
from pathlib import Path

#: Prefix on the verdict reason for a sample an exception cleared, so the set
#: can be counted and re-audited by grepping one string. The grade is plain
#: ``ok``: a second passing grade would complicate "did this import cleanly?".
ACCEPTED_PREFIX = "accepted by exception: "

#: Required columns. ``reason`` is mandatory free text: it is the audit trail.
COLUMNS = ("latitude", "longitude", "geographic_location", "reason")


class LocationExceptionError(Exception):
    """The exceptions file cannot be read as one.

    Raised rather than skipped: a file that silently loads as empty would turn
    every exception back into an error.
    """


class LocationExceptions:
    """Verified-correct ``(coordinate, claim)`` pairs, and which ones got used.

    Coordinates are matched as exact floats (the same key discipline as
    ``GeoCache``), so an entry must carry the coordinate exactly as the sample
    records it -- ``38.6666666666667``, not ``38.666667``. Strict on purpose:
    moving the point invalidates the exception, and ``unused()`` shows it.

    The claim is matched case-folded and stripped.
    """

    def __init__(self, entries: dict[tuple[float, float, str], str]):
        self._entries = entries
        self._used: set[tuple[float, float, str]] = set()

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def _key(lat: float, lon: float, claimed: str):
        return (float(lat), float(lon), (claimed or "").strip().casefold())

    def accepts(self, lat: float, lon: float, claimed: str) -> str | None:
        """The recorded reason if this coordinate and claim were verified, else None."""
        key = self._key(lat, lon, claimed)
        reason = self._entries.get(key)
        if reason is None:
            return None
        self._used.add(key)
        return reason

    def unused(self) -> list[tuple[float, float, str, str]]:
        """Entries that matched no sample, as ``(lat, lon, claim, reason)``.

        Not an error (the sample may be gone or fixed), but worth reporting:
        the likeliest cause is that the coordinate or claim was edited and the
        exception no longer covers the sample it was written for.
        """
        return sorted(
            (lat, lon, claimed, reason)
            for (lat, lon, claimed), reason in self._entries.items()
            if (lat, lon, claimed) not in self._used
        )


def load_location_exceptions(path: str | Path | None) -> LocationExceptions | None:
    """Read ``location_exceptions.tsv``; None when no path is given."""
    if path is None:
        return None
    path = Path(path)
    entries: dict[tuple[float, float, str], str] = {}
    with open(path, "r", encoding="utf-8") as handle:
        # ``#`` lines are comments, as in the other curation files.
        rows = (line for line in handle if not line.lstrip().startswith("#"))
        reader = csv.DictReader(rows, delimiter="\t")
        missing = [column for column in COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise LocationExceptionError(
                f"{path} is missing required column(s): {', '.join(missing)}. "
                f"Expected a tab-separated file with {', '.join(COLUMNS)}."
            )
        # Numbered over non-comment lines (entries, not file lines).
        for number, row in enumerate(reader, 1):
            if not (row.get("latitude") or "").strip():
                continue  # blank separator line
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (TypeError, ValueError) as exc:
                raise LocationExceptionError(
                    f"{path} entry {number}: unparseable coordinate "
                    f"({row.get('latitude')!r}, {row.get('longitude')!r})"
                ) from exc
            claimed = (row.get("geographic_location") or "").strip()
            reason = (row.get("reason") or "").strip()
            if not claimed or not reason:
                raise LocationExceptionError(
                    f"{path} entry {number}: geographic_location and reason are "
                    "both required -- an exception with no stated reason cannot "
                    "be re-audited."
                )
            entries[LocationExceptions._key(lat, lon, claimed)] = reason
    return LocationExceptions(entries)
