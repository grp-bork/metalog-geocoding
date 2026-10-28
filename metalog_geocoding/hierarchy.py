"""Sea-to-ocean hierarchy from the MarineRegions gazetteer, cached locally.

Maps an IHO MRGID to its chain, such as "Gulf of Riga; Baltic Sea; Atlantic
Ocean". The chains for all 101 IHO v3 polygons ship with the package
(``vocab/sea_hierarchy.tsv``), so a lookup never needs the network. Only an
MRGID missing from it (a new IHO edition) walks MarineRegions ``partof``
relations upward, and the result is written to ``sea_hierarchy.tsv`` in the
cache directory. This is the only component that touches the network.
"""

from pathlib import Path
from typing import Optional
import time
import requests


class UnmappedSeaError(Exception):
    """Raised when a sea's ocean cannot be determined from API or fallback."""

    pass


class HierarchyLookupError(Exception):
    """Raised when MarineRegions could not be reached, as opposed to answering.

    Distinct from UnmappedSeaError because the fix for that one is a
    SEA_TO_OCEAN_FALLBACK entry; a network failure must be retried, never
    hard-coded.
    """

    pass


#: Attempts per API call before giving up, and the base for exponential backoff.
API_RETRIES = 3
API_BACKOFF_S = 2.0


#: Chains for every IHO v3 MRGID, shipped with the package.
BUNDLED_HIERARCHY = Path(__file__).parent / "vocab" / "sea_hierarchy.tsv"


# Top-level water bodies that end a chain, by MRGID (MarineRegions IHO Sea
# Areas). The Mediterranean and Black Sea are treated as top level.
MAJOR_OCEANS = {
    1902: "Atlantic Ocean",
    1903: "Pacific Ocean",
    1904: "Indian Ocean",
    1905: "Mediterranean Sea",
    1906: "Arctic Ocean",
    1907: "Southern Ocean",
    3319: "Black Sea",
}

# Chains for seas whose gazetteer hierarchy does not reach a MAJOR_OCEANS
# entry. Add one when you hit UnmappedSeaError (never for HierarchyLookupError).
# Format: MRGID -> full chain, semicolon-separated.
SEA_TO_OCEAN_FALLBACK = {
    # Includes the North Atlantic level, like every other North Atlantic sea.
    2401: "Baltic Sea; North Atlantic Ocean; Atlantic Ocean",
    4331: "South China and Eastern Archipelagic Seas; Pacific Ocean",
    # The Turkish Straits system: MarineRegions gives the Marmara no `partof`
    # that reaches a major ocean, and IHO files it with the Mediterranean.
    3369: "Sea of Marmara; Mediterranean Sea",
    # An arm of the Black Sea, which is itself one of MAJOR_OCEANS here.
    3320: "Sea of Azov; Black Sea",
}

# Chains that *replace* one the gazetteer does return, because it is wrong for
# our purposes. Keep these few, each with a reason. Applied on cache load as
# well as on lookup, so a stale cached chain is corrected. The result is the one
# hierarchy used both for grading and for the published `ocean` column.
HIERARCHY_OVERRIDES = {
    # IHO files the Norwegian Sea under the Arctic; the literature and sample
    # metadata call it Atlantic. Placed under the North Atlantic basin like the
    # other North Atlantic marginal seas.
    2353: "Norwegian Sea; North Atlantic Ocean; Atlantic Ocean",
    # The gazetteer hangs the Gulf of Aqaba directly off the Indian Ocean,
    # making it a sibling of the Red Sea it is part of.
    4263: "Gulf of Aqaba; Red Sea; Indian Ocean",
}


class SeaHierarchyCache:
    """Sea-to-ocean hierarchy: the bundled chains, then any fetched since."""

    def __init__(self, cache_file: Path):
        """
        Initialize hierarchy cache.

        Args:
            cache_file: Path to a TSV of chains fetched beyond the bundled ones
                (cache/sea_hierarchy.tsv). Read if present, written only when a
                lookup had to go to MarineRegions.
        """
        self.cache_file = Path(cache_file)
        self.cache: dict[int, str] = {}  # mrgid -> full hierarchy chain
        self.last_api_call = 0.0  # For rate limiting
        self._fetched = False  # whether anything came from the API
        self._load_cache(BUNDLED_HIERARCHY)
        self._load_cache(self.cache_file)

    def _load_cache(self, path: Path):
        """Load chains from a TSV file, over any already loaded."""
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if not lines:
                return

            # Parse header
            header = lines[0].strip().split("\t")
            if header != ["mrgid", "hierarchy"]:
                return  # Invalid format

            # Parse data rows
            for line in lines[1:]:
                if not line.strip():
                    continue

                values = line.strip().split("\t")
                if len(values) < 2:
                    continue

                try:
                    mrgid = int(values[0])
                    hierarchy = values[1]
                    # Correct a stale chain cached before its override existed.
                    self.cache[mrgid] = HIERARCHY_OVERRIDES.get(mrgid, hierarchy)
                except (ValueError, IndexError):
                    continue
        except Exception:
            pass  # Ignore load errors, will rebuild cache

    def save_cache(self):
        """Save the chains to the cache file, if any were fetched."""
        if not self._fetched:
            return  # everything came from the bundled file

        # Ensure parent directory exists
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.cache:
            return  # Don't create empty file

        # Sort by MRGID for consistent ordering
        sorted_entries = sorted(self.cache.items())

        with open(self.cache_file, "w", encoding="utf-8") as f:
            # Write header
            f.write("mrgid\thierarchy\n")

            # Write data rows
            for mrgid, hierarchy in sorted_entries:
                f.write(f"{mrgid}\t{hierarchy}\n")

    def get_ocean(self, mrgid: int) -> str:
        """
        Get full hierarchy chain for a given MRGID.

        Args:
            mrgid: MarineRegions Gazetteer ID

        Returns:
            Full hierarchy chain (e.g., "Gulf of Riga; Baltic Sea; Atlantic Ocean")
            For major oceans, returns just the ocean name (e.g., "Atlantic Ocean")

        Raises:
            UnmappedSeaError: If sea's ocean cannot be determined from API or fallback
        """
        # An override wins over everything, including MAJOR_OCEANS.
        if mrgid in HIERARCHY_OVERRIDES:
            chain = HIERARCHY_OVERRIDES[mrgid]
            self.cache[mrgid] = chain
            return chain

        # Check if it's already a major ocean
        if mrgid in MAJOR_OCEANS:
            ocean = MAJOR_OCEANS[mrgid]
            self.cache[mrgid] = ocean
            return ocean

        # Check cache
        if mrgid in self.cache:
            return self.cache[mrgid]

        # Query API to build full hierarchy chain
        result = self._query_api_for_ocean(mrgid)

        if result["hierarchy"]:
            self.cache[mrgid] = result["hierarchy"]
            self._fetched = True
            # Save cache after each new entry (in case of crashes)
            self.save_cache()
            return result["hierarchy"]

        # If API failed, report the topmost unresolved MRGID (not the original query)
        topmost_mrgid = result["topmost_mrgid"]
        topmost_name = result["topmost_name"] or "Unknown"

        # Unmapped sea - raise error so user can add to fallback
        raise UnmappedSeaError(
            f"Cannot determine ocean for MRGID {mrgid}. "
            f"API query failed to reach a major ocean at the topmost level: "
            f"MRGID {topmost_mrgid} ({topmost_name}). "
            f"Please investigate this MRGID at https://www.marineregions.org/mrgid/{topmost_mrgid} "
            f"and add it to SEA_TO_OCEAN_FALLBACK in metalog_geocoding/hierarchy.py"
        )

    def _query_api_for_ocean(self, mrgid: int) -> dict:
        """
        Query MarineRegions API to build full hierarchy chain.

        Follows 'partof' relationships up the hierarchy until finding a major ocean,
        collecting all names along the way. Also checks fallback mapping during traversal.

        Args:
            mrgid: MarineRegions Gazetteer ID

        Returns:
            Dict with keys:
            - "hierarchy": Full chain (e.g., "Gulf of Riga; Baltic Sea; Atlantic Ocean") or None
            - "topmost_mrgid": The topmost MRGID that couldn't be resolved (only if hierarchy is None)
            - "topmost_name": The name of the topmost MRGID (only if hierarchy is None)
        """
        visited = set()  # Prevent infinite loops
        current_mrgid = mrgid
        max_depth = 10  # Prevent infinite loops
        hierarchy_chain = []  # List of names from specific to general
        topmost_mrgid = mrgid
        topmost_name = None

        for _ in range(max_depth):
            if current_mrgid in visited:
                break  # Circular reference

            visited.add(current_mrgid)

            # Check if we've reached a major ocean
            if current_mrgid in MAJOR_OCEANS:
                # Add ocean name and return full chain
                hierarchy_chain.append(MAJOR_OCEANS[current_mrgid])
                return {
                    "hierarchy": "; ".join(hierarchy_chain),
                    "topmost_mrgid": None,
                    "topmost_name": None,
                }

            # A level already in the cache (e.g. a basin such as "South
            # Pacific Ocean", which is not in MAJOR_OCEANS) ends the walk
            # without further requests.
            if current_mrgid in self.cache:
                hierarchy_chain.append(self.cache[current_mrgid])
                return {
                    "hierarchy": "; ".join(hierarchy_chain),
                    "topmost_mrgid": None,
                    "topmost_name": None,
                }

            # Check if this level is in the fallback mapping
            if current_mrgid in SEA_TO_OCEAN_FALLBACK:
                # Use fallback hierarchy for this level and append to what we've collected
                fallback_chain = SEA_TO_OCEAN_FALLBACK[current_mrgid]
                hierarchy_chain.append(fallback_chain)
                return {
                    "hierarchy": "; ".join(hierarchy_chain),
                    "topmost_mrgid": None,
                    "topmost_name": None,
                }

            # Get name for current MRGID
            name = self._get_name_from_api(current_mrgid)
            if name:
                hierarchy_chain.append(name)
                topmost_name = name  # Track the name of the topmost level we reach

            # Update topmost MRGID as we traverse up
            topmost_mrgid = current_mrgid

            # Query API for parent relationships
            parent_mrgid = self._get_parent_from_api(current_mrgid)

            if parent_mrgid is None:
                break  # No parent found

            current_mrgid = parent_mrgid

        # If we exited without finding an ocean, check if last MRGID is known
        if current_mrgid in MAJOR_OCEANS:
            hierarchy_chain.append(MAJOR_OCEANS[current_mrgid])
            return {
                "hierarchy": "; ".join(hierarchy_chain),
                "topmost_mrgid": None,
                "topmost_name": None,
            }

        # Check fallback one more time for the final MRGID
        if current_mrgid in SEA_TO_OCEAN_FALLBACK:
            fallback_chain = SEA_TO_OCEAN_FALLBACK[current_mrgid]
            hierarchy_chain.append(fallback_chain)
            return {
                "hierarchy": "; ".join(hierarchy_chain),
                "topmost_mrgid": None,
                "topmost_name": None,
            }

        # Failed to resolve - return the topmost level information
        return {
            "hierarchy": None,
            "topmost_mrgid": topmost_mrgid,
            "topmost_name": topmost_name,
        }

    def _get_json(self, url: str) -> Optional[object]:
        """
        GET a MarineRegions REST URL, retrying transient failures.

        Returns:
            Parsed JSON, or None when the API answered 404 - which is the API
            saying "no such relation", a real answer.

        Raises:
            HierarchyLookupError: the API could not be reached, or kept failing.
                Never None, so a network failure cannot read as "no parent".
        """
        last_error = "unknown"

        for attempt in range(API_RETRIES):
            self._rate_limit()
            try:
                response = requests.get(url, timeout=10)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        last_error = f"malformed JSON: {exc}"
                elif response.status_code == 404:
                    return None  # a real answer: nothing here
                else:
                    last_error = f"HTTP {response.status_code}"

            if attempt < API_RETRIES - 1:
                time.sleep(API_BACKOFF_S * (2 ** attempt))

        raise HierarchyLookupError(
            f"MarineRegions unreachable after {API_RETRIES} attempts: {url} "
            f"(last error: {last_error}). This is a transient failure, not a "
            f"missing hierarchy - do NOT add anything to SEA_TO_OCEAN_FALLBACK "
            f"for it; retry when the service is reachable."
        )

    def _get_parent_from_api(self, mrgid: int) -> Optional[int]:
        """
        Get parent MRGID from MarineRegions API.

        Args:
            mrgid: MarineRegions Gazetteer ID

        Returns:
            Parent MRGID or None
        """
        url = f"https://www.marineregions.org/rest/getGazetteerRelationsByMRGID.json/{mrgid}/partof/"
        data = self._get_json(url)

        # API returns a list of parent relationships; take the first "partof"
        if isinstance(data, list) and data and "MRGID" in data[0]:
            return int(data[0]["MRGID"])

        return None

    def _get_name_from_api(self, mrgid: int) -> Optional[str]:
        """
        Get place name from MarineRegions API.

        Args:
            mrgid: MarineRegions Gazetteer ID

        Returns:
            Place name or None
        """
        url = f"https://www.marineregions.org/rest/getGazetteerRecordByMRGID.json/{mrgid}/"
        data = self._get_json(url)

        if isinstance(data, dict) and "preferredGazetteerName" in data:
            return data["preferredGazetteerName"]

        return None

    def _rate_limit(self):
        """Enforce 1 request per second rate limit."""
        current_time = time.time()
        time_since_last_call = current_time - self.last_api_call

        if time_since_last_call < 1.0:
            sleep_time = 1.0 - time_since_last_call
            time.sleep(sleep_time)

        self.last_api_call = time.time()
