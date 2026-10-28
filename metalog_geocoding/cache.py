"""Cache management for geocoding results in an SQLite database."""

import json
import math
import sqlite3
from pathlib import Path
from typing import Optional


#: Decimal places used for cache keys. ``None`` means no rounding: the
#: coordinate is the key exactly as given. Rounding saves almost nothing (the
#: dedup comes from repeated identical coordinates) and a coarse value changes
#: answers, since 2 dp (~1.1 km) is enough to cross a shoreline. Changing it
#: invalidates every existing entry.
DEFAULT_DECIMALS: int | None = None

#: Fields stored as strings and converted back to float on read.
FLOAT_FIELDS = frozenset(
    {
        "fallback_distance_m",
        "longhurst_distance_m",
        "coast_distance_km",
        "coast_landmass_area_km2",
        "original_lat",
        "original_lon",
    }
)

#: Fields stored as strings and converted back to int on read, so a cached
#: result has the same types as a freshly computed one.
INT_FIELDS = frozenset({"mrgid", "coast_mrgid"})


def round_coord(
    lat: float, lon: float, decimals: int | None = DEFAULT_DECIMALS
) -> tuple[float, float]:
    """
    Normalise coordinates into a cache key.

    Args:
        lat: Latitude
        lon: Longitude
        decimals: Number of decimal places, or None (the default) to use the
            coordinate exactly as given

    Returns:
        Tuple of (lat, lon) as plain floats

    Note:
        The float() calls are needed: database drivers return ``numeric``
        columns as Decimal, which does not hash like the equal float, so an
        un-normalised Decimal would miss every cached entry.
    """
    if decimals is None:
        return (float(lat), float(lon))
    return (round(float(lat), decimals), round(float(lon), decimals))


def read_tsv(path: Path) -> dict[tuple[float, float], dict[str, str]]:
    """
    Read a cache in the old long-format TSV (lat, lon, level, name).

    Malformed lines are skipped, since such files were hand-edited.
    """
    entries: dict[tuple[float, float], dict[str, str]] = {}
    with open(path, "r", encoding="utf-8") as f:
        next(f, None)  # header
        for line in f:
            values = line.rstrip("\n").split("\t")
            if len(values) < 4:
                continue
            try:
                key = (float(values[0]), float(values[1]))
            except ValueError:
                continue
            entries.setdefault(key, {})[values[2]] = values[3]
    return entries


class GeoCache:
    """
    Result cache keyed by (lat, lon), held in SQLite rather than in memory.

    One row per coordinate; the result is a JSON object of strings. SQLite's
    REAL is an IEEE double, so a float key is found again exactly, with no
    formatting step that could truncate it.
    """

    def __init__(self, cache_file: Path, decimals: int | None = DEFAULT_DECIMALS):
        """
        Open (or create) the cache database.

        Args:
            cache_file: Path to the SQLite file. If it does not exist yet and a
                TSV cache with the same stem does (``land_cache.tsv`` beside
                ``land_cache.sqlite``), that TSV is imported once.
            decimals: Decimal places for the coordinate key, or None (the
                default) for the coordinate exactly as given. Changing this
                invalidates every existing entry, since the keys no longer match.
        """
        self.cache_file = Path(cache_file)
        self.decimals = decimals

        is_new = not self.cache_file.exists()
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        # The service serialises access with its own lock, so sharing the
        # connection across its threads is safe.
        self._db = sqlite3.connect(self.cache_file, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS results ("
            " lat REAL NOT NULL, lon REAL NOT NULL, data TEXT NOT NULL,"
            " PRIMARY KEY (lat, lon)) WITHOUT ROWID"
        )
        self._db.commit()

        legacy = self.cache_file.with_suffix(".tsv")
        if is_new and legacy != self.cache_file and legacy.exists():
            self._import(read_tsv(legacy))

    def _import(self, entries: dict[tuple[float, float], dict[str, str]]):
        """Bulk-insert entries already in string form, and commit."""
        self._db.executemany(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
            (
                (*round_coord(lat, lon, self.decimals), json.dumps(levels))
                for (lat, lon), levels in entries.items()
            ),
        )
        self._db.commit()

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM results").fetchone()[0]

    def lookup(self, lat: float, lon: float) -> Optional[dict]:
        """
        Return cached result or None.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Dict mapping level to name, or None if not found
            Example: {'continent': 'Europe', 'country': 'Germany', 'admin_level_4': 'Berlin'}

        Note:
            Automatically converts 'is_fallback' to bool, distances to float and MRGIDs to int
        """
        row = self._db.execute(
            "SELECT data FROM results WHERE lat = ? AND lon = ?",
            round_coord(lat, lon, self.decimals),
        ).fetchone()
        if row is None:
            return None
        cached = json.loads(row[0])
        if not cached:
            return None

        # Create a new dict with type conversions
        result = {}
        for field, value in cached.items():
            if field == "is_fallback":
                # Convert string to bool
                result[field] = value == "True"
            elif field in FLOAT_FIELDS:
                # Convert string to float
                try:
                    result[field] = float(value)
                except (ValueError, TypeError):
                    result[field] = value
            elif field in INT_FIELDS:
                try:
                    result[field] = int(value)
                except (ValueError, TypeError):
                    result[field] = value
            else:
                # Keep as string
                result[field] = value

        return result

    def add(self, lat: float, lon: float, levels: dict):
        """
        Add or replace an entry, committed at once.

        Committing per entry is cheap in WAL mode with ``synchronous=NORMAL``
        (no fsync), against milliseconds to geocode the coordinate, and it
        means a worker killed mid-batch (OOM, request timeout) keeps every
        coordinate it finished.

        Args:
            lat: Latitude
            lon: Longitude
            levels: Dict mapping level to name
                   Example: {'continent': 'Europe', 'country': 'Germany'}

        Note:
            Automatically converts boolean and numeric values to strings for storage
        """
        key = round_coord(lat, lon, self.decimals)
        if math.isnan(key[0]) or math.isnan(key[1]):
            return  # NaN never equals itself, so it could never be found again

        # Convert all values to strings for cache storage
        string_levels = {
            field: str(value) if value is not None else ""
            for field, value in levels.items()
        }
        self._db.execute(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
            (*key, json.dumps(string_levels)),
        )
        self._db.commit()

    def save(self):
        """Nothing to do: add() commits. Kept so callers need not change."""

    def close(self):
        self._db.close()
