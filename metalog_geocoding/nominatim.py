"""
Place-name search through OpenStreetMap's Nominatim, cached locally.

Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
asks for at most one request per second, an identifying User-Agent, and
caching. This client enforces all three for its process: one lock spaces
every request at least ``MIN_INTERVAL_S`` apart across threads, and every
answer, including "nothing found", goes into SQLite so a name is fetched once.
``wikidata.py`` reuses the same pacing and cache for Wikidata.

A network failure is never cached and never reads as "nothing found": it
raises ``LookupUnavailable``, the caller reports the name as not looked up,
and the next request tries again (the rule ``hierarchy.py`` follows too).

The cache keeps each answer as returned, minus fields nothing reads, so a
change to how results are filtered (``places.py``) needs no refetch.

Standard library plus ``requests`` (see ``metalog_geocoding/__init__.py``).
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import requests

DEFAULT_URL = "https://nominatim.openstreetmap.org/search"

#: Seconds between requests. The policy's ceiling is one per second; the
#: margin absorbs clock jitter.
MIN_INTERVAL_S = 1.1

#: Attempts per query, and the base for exponential backoff between them.
RETRIES = 3
BACKOFF_S = 2.0

#: Results per query. Ambiguous names (``Mary Lake``, ``Falcon``) have dozens;
#: the caller sees these as candidates, ranked by Nominatim's importance.
RESULT_LIMIT = 5

#: Outline simplification in degrees (~1 km). Distances are reported at
#: kilometre scale, and full-resolution country outlines run to megabytes.
POLYGON_THRESHOLD = 0.01

#: How long "nothing found" is trusted before the name is asked again, since
#: OpenStreetMap keeps adding features. Results that found something never
#: expire on their own; ``PlaceCache.forget`` refreshes one.
NEGATIVE_TTL_S = 90 * 24 * 3600

#: Fields kept from each result.
KEPT_FIELDS = (
    "osm_type",
    "osm_id",
    "category",
    "type",
    "addresstype",
    "place_rank",
    "importance",
    "display_name",
    "name",
    "namedetails",
    "boundingbox",
    "geojson",
)

#: Tables holding the answers. A new name whenever the request parameters
#: change what an answer contains, so that older answers are not read as new.
SEARCH_TABLE = "searches_v2"
LOOKUP_TABLE = "lookups_v1"

USER_AGENT = "metalog-geocoding (+https://github.com/grp-bork/metalog-geocoding)"
REFERER = "https://github.com/grp-bork/metalog-geocoding"


class LookupUnavailable(Exception):
    """An online source could not be reached or kept failing; nothing was cached."""


#: The name this exception had when Nominatim was the only source.
NominatimUnavailable = LookupUnavailable


def query_key(query: str) -> str:
    """Cache key for a query: case and whitespace do not change the answer."""
    return " ".join(query.split()).casefold()


class PlaceCache:
    """Online answers keyed by (table, query, country codes), held in SQLite.

    One file, one table per kind of request (``SEARCH_TABLE``,
    ``LOOKUP_TABLE``, ``wikidata.TABLE``).
    """

    def __init__(self, cache_file: Path):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        # Shared across the service's threads; self._lock serialises use.
        self._db = sqlite3.connect(self.cache_file, check_same_thread=False)
        self._lock = threading.Lock()
        self._tables: set[str] = set()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._table(SEARCH_TABLE)

    def _table(self, table: str) -> str:
        if table not in self._tables:
            if not table.replace("_", "").isalnum():
                raise ValueError(f"bad table name {table!r}")
            with self._lock:
                self._db.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} ("
                    " query TEXT NOT NULL, countrycodes TEXT NOT NULL,"
                    " fetched_at REAL NOT NULL, results TEXT NOT NULL,"
                    " PRIMARY KEY (query, countrycodes)) WITHOUT ROWID"
                )
                self._db.commit()
            self._tables.add(table)
        return table

    def __len__(self) -> int:
        with self._lock:
            return sum(
                self._db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in self._tables
            )

    def get(
        self,
        query: str,
        countrycodes: str = "",
        now: float | None = None,
        table: str = SEARCH_TABLE,
    ) -> Optional[list]:
        """The cached result list, or None if absent or an expired empty answer."""
        table = self._table(table)
        with self._lock:
            row = self._db.execute(
                f"SELECT fetched_at, results FROM {table} WHERE query = ? AND countrycodes = ?",
                (query_key(query), countrycodes),
            ).fetchone()
        if row is None:
            return None
        fetched_at, results = row[0], json.loads(row[1])
        now = time.time() if now is None else now
        if not results and now - fetched_at > NEGATIVE_TTL_S:
            return None
        return results

    def put(
        self,
        query: str,
        countrycodes: str,
        results: list,
        fetched_at: float | None = None,
        table: str = SEARCH_TABLE,
        fields: tuple | None = KEPT_FIELDS,
    ):
        """Store an answer (an empty list for "nothing found"), committed at once.

        ``fields`` limits what is kept of each result; None keeps it whole.
        """
        table = self._table(table)
        kept = results if fields is None else [{k: r[k] for k in fields if k in r} for r in results]
        with self._lock:
            self._db.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?, ?, ?, ?)",
                (
                    query_key(query),
                    countrycodes,
                    time.time() if fetched_at is None else fetched_at,
                    json.dumps(kept),
                ),
            )
            self._db.commit()

    def forget(self, query: str, countrycodes: str = "", table: str = SEARCH_TABLE):
        table = self._table(table)
        with self._lock:
            self._db.execute(
                f"DELETE FROM {table} WHERE query = ? AND countrycodes = ?",
                (query_key(query), countrycodes),
            )
            self._db.commit()

    def close(self):
        self._db.close()


class PacedSession:
    """
    GET JSON with retries, at most one request per ``min_interval_s``.

    The interval holds across threads: the lock is held across the request,
    so concurrent callers queue rather than each waiting out the interval and
    firing together.

    Args:
        session: a ``requests``-like object with ``get``; tests pass a fake.
    """

    def __init__(self, user_agent: str = USER_AGENT, session=None, min_interval_s: float = MIN_INTERVAL_S):
        self.headers = {"User-Agent": user_agent, "Referer": REFERER}
        self.session = session or requests.Session()
        self.min_interval_s = min_interval_s
        self._rate_lock = threading.Lock()
        self._last_request = 0.0
        #: Requests sent (not cache hits), for /health and tests.
        self.requests_sent = 0

    def get_json(self, url: str, params: dict, what: str, valid=None):
        """Parsed JSON for ``url``. Raises ``LookupUnavailable`` after the retries.

        ``valid(data)`` decides whether a 200 answer is usable; one that is
        not (Wikidata's error bodies under load) is retried like a 503.
        """
        last_error = "unknown"
        for attempt in range(RETRIES):
            if attempt:
                time.sleep(BACKOFF_S * (2 ** (attempt - 1)))
            with self._rate_lock:
                wait = self._last_request + self.min_interval_s - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                try:
                    response = self.session.get(url, params=params, headers=self.headers, timeout=30)
                except requests.RequestException as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue
                finally:
                    self._last_request = time.monotonic()
                    self.requests_sent += 1
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as exc:
                    last_error = f"malformed JSON: {exc}"
                    continue
                if valid is None or valid(data):
                    return data
                last_error = f"unusable answer: {str(data)[:200]}"
            else:
                last_error = f"HTTP {response.status_code}"
        raise LookupUnavailable(
            f"{what} failed after {RETRIES} attempts (last error: {last_error}); "
            f"not cached, so a later request retries"
        )


class NominatimClient:
    """
    Cached, rate-limited Nominatim search, and lookup by OSM id.

    Args:
        cache: where answers are kept.
        url: the search endpoint; ``/lookup`` is its sibling. A self-hosted
            Nominatim lifts the rate limit but this client keeps it regardless.
        user_agent: identifies the application, as the policy requires.
        session: a ``requests``-like object with ``get``; tests pass a fake.
    """

    def __init__(
        self,
        cache: PlaceCache,
        url: str = DEFAULT_URL,
        user_agent: str = USER_AGENT,
        session=None,
        min_interval_s: float = MIN_INTERVAL_S,
    ):
        self.cache = cache
        self.url = url
        self.lookup_url = url.rsplit("/", 1)[0] + "/lookup"
        self.http = PacedSession(user_agent, session, min_interval_s)

    @property
    def requests_sent(self) -> int:
        return self.http.requests_sent

    def cached(self, query: str, countrycodes: str = "") -> Optional[list]:
        """The cached answer, without touching the network."""
        return self.cache.get(query, countrycodes)

    def search(self, query: str, countrycodes: str = "") -> list:
        """Results for ``query``, from the cache or else from Nominatim.

        Raises:
            LookupUnavailable: the request failed; nothing was cached.
        """
        cached = self.cache.get(query, countrycodes)
        if cached is not None:
            return cached
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": RESULT_LIMIT,
            "polygon_geojson": 1,
            "polygon_threshold": POLYGON_THRESHOLD,
            # Every name tag, so a result can be checked against the query in
            # any language (Disko Bay is mapped as Qeqertarsuup Tunua).
            "namedetails": 1,
        }
        if countrycodes:
            params["countrycodes"] = countrycodes
        data = self.http.get_json(
            self.url, params, f"Nominatim search for {query!r}", lambda d: isinstance(d, list)
        )
        self.cache.put(query, countrycodes, data)
        return self.cache.get(query, countrycodes) or []

    def cached_lookup(self, osm_id: str) -> Optional[list]:
        return self.cache.get(osm_id, table=LOOKUP_TABLE)

    def lookup(self, osm_id: str) -> list:
        """The feature with OSM id ``osm_id`` (``R123``, ``W45``, ``N6``), with its outline."""
        cached = self.cached_lookup(osm_id)
        if cached is not None:
            return cached
        params = {
            "osm_ids": osm_id,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "polygon_threshold": POLYGON_THRESHOLD,
            "namedetails": 1,
        }
        data = self.http.get_json(
            self.lookup_url, params, f"Nominatim lookup of {osm_id}", lambda d: isinstance(d, list)
        )
        self.cache.put(osm_id, "", data, table=LOOKUP_TABLE)
        return self.cached_lookup(osm_id) or []
