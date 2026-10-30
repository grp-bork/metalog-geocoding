"""
Place-name search through Wikidata, cached locally.

Wikidata knows places by their English names and aliases (``Lake Zug``,
``Lake Tai``, ``Lake Kinneret`` for the Sea of Galilee) and ranks a search by
how prominent each item is, so ``Andes`` is the mountain range, not a town in
Colombia. ``places.py`` uses it to decide *which* place a name is; the outline
comes from OpenStreetMap where the item links to one (property P402).

One answer per name: the ``wbsearchentities`` hits, each with its coordinate
(P625) and OSM relation (P402) fetched in one ``wbgetentities`` call. Paced
and cached like Nominatim (``nominatim.PacedSession``, ``nominatim.PlaceCache``),
and a failure is never cached. Wikidata is CC0.

Standard library plus ``requests`` (see ``metalog_geocoding/__init__.py``).
"""

from typing import Optional

from .nominatim import MIN_INTERVAL_S, USER_AGENT, PacedSession, PlaceCache

DEFAULT_URL = "https://www.wikidata.org/w/api.php"

#: Search hits per name.
RESULT_LIMIT = 5

#: Cache table; see ``nominatim.SEARCH_TABLE`` on renaming.
TABLE = "wikidata_v2"


def _first_value(claims: dict, prop: str):
    for statement in claims.get(prop, []):
        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
        if value is not None:
            return value
    return None


class WikidataClient:
    """
    Cached, rate-limited Wikidata place search.

    Args:
        cache: shared with Nominatim; answers go in their own table.
        url: the MediaWiki API endpoint.
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
        self.http = PacedSession(user_agent, session, min_interval_s)

    @property
    def requests_sent(self) -> int:
        return self.http.requests_sent

    def cached(self, query: str) -> Optional[list]:
        return self.cache.get(query, table=TABLE)

    def search(self, query: str) -> list[dict]:
        """Hits for ``query``, most prominent first.

        Each is ``{id, label, description, match, aliases, coord,
        osm_relation, sitelinks}``: ``match`` is the label or alias the search
        matched, ``aliases`` every English alias, ``coord`` a ``[lat, lon]``
        or None, ``osm_relation`` an id string or None, and ``sitelinks`` the
        number of Wikipedia (and sister) pages about it, a measure of how
        prominent it is.

        Raises:
            nominatim.LookupUnavailable: a request failed; nothing was cached.
        """
        cached = self.cached(query)
        if cached is not None:
            return cached
        found = self.http.get_json(
            self.url,
            {
                "action": "wbsearchentities",
                "search": query,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": RESULT_LIMIT,
                "format": "json",
            },
            f"Wikidata search for {query!r}",
            lambda d: isinstance(d, dict) and "search" in d,
        )
        hits = [
            {
                "id": h["id"],
                "label": h.get("label") or "",
                "description": h.get("description") or "",
                "match": (h.get("match") or {}).get("text") or "",
                "aliases": h.get("aliases") or [],
            }
            for h in found["search"]
            if h.get("id")
        ]
        if hits:
            entities = self.http.get_json(
                self.url,
                {
                    "action": "wbgetentities",
                    "ids": "|".join(h["id"] for h in hits),
                    "props": "claims|aliases|sitelinks",
                    "languages": "en",
                    "format": "json",
                },
                f"Wikidata entities for {query!r}",
                lambda d: isinstance(d, dict) and "entities" in d,
            )
            for hit in hits:
                entity = entities["entities"].get(hit["id"], {})
                hit["aliases"] = [a["value"] for a in entity.get("aliases", {}).get("en", []) if a.get("value")]
                hit["sitelinks"] = len(entity.get("sitelinks", {}))
                claims = entity.get("claims", {})
                coord = _first_value(claims, "P625")
                hit["coord"] = (
                    [coord["latitude"], coord["longitude"]]
                    if isinstance(coord, dict) and "latitude" in coord
                    else None
                )
                relation = _first_value(claims, "P402")
                hit["osm_relation"] = str(relation) if relation else None
        self.cache.put(query, "", hits, table=TABLE, fields=None)
        return hits
