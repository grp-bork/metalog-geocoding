"""Sea-based reverse geocoding using IHO Sea Areas data."""

from pathlib import Path
from typing import Optional
import pickle
import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import Point

from .hierarchy import SeaHierarchyCache

#: Columns read from the IHO layer. Every row selection copies whatever is
#: loaded, so add a column here before using it. ``ID`` is the fallback in
#: ``_extract_mrgid`` should a row lack ``MRGID``.
IHO_COLUMNS = ["NAME", "ID", "MRGID", "geometry"]

#: IHO v3's Gulf of Finland (MRGID 2407) follows the Neva upstream into Lake
#: Ladoga and, via the Svir, Lake Onega, so a lake point would geocode as the
#: Baltic. A thin box across the Neva at St Petersburg severs the polygon, and
#: the piece holding the lakes is dropped.
GULF_OF_FINLAND_MRGID = 2407
NEVA_CUT = (30.30, 59.85, 30.32, 60.0)  # (min lon, min lat, max lon, max lat)
GULF_OF_FINLAND_SEED = Point(25.0, 59.8)
LADOGA_ONEGA = [Point(31.5, 60.8), Point(35.5, 61.7)]


def cut_gulf_of_finland_lakes(seas: gpd.GeoDataFrame) -> None:
    """Drop Lakes Ladoga and Onega from the Gulf of Finland polygon, in place.

    Raises if the polygon no longer splits as expected, so a reshaped IHO
    edition fails loudly rather than losing part of the Gulf.
    """
    rows = seas.index[seas["MRGID"] == GULF_OF_FINLAND_MRGID]
    if len(rows) != 1:
        raise RuntimeError(f"expected one IHO polygon with MRGID {GULF_OF_FINLAND_MRGID}, found {len(rows)}")
    parts = shapely.get_parts(seas.at[rows[0], "geometry"].difference(shapely.box(*NEVA_CUT)))
    gulf = [p for p in parts if p.contains(GULF_OF_FINLAND_SEED)]
    lakes = [p for p in parts if all(p.contains(lake) for lake in LADOGA_ONEGA)]
    if len(parts) != 2 or len(gulf) != 1 or len(lakes) != 1:
        raise RuntimeError(
            "the IHO Gulf of Finland no longer splits at the Neva into the gulf and "
            "Lakes Ladoga/Onega; revisit NEVA_CUT in metalog_geocoding/sea.py"
        )
    seas.at[rows[0], "geometry"] = gulf[0]


class SeaGeocoder:
    """Reverse geocoder for sea locations (oceans, seas, MRGIDs)."""

    def __init__(self, data_dir: Path, cache_dir: Path):
        """
        Load IHO Sea Areas data and hierarchy cache.

        Args:
            data_dir: Directory containing IHO Sea Areas data files
            cache_dir: Directory for caching ocean hierarchy lookups

        Note:
            Caches the parsed layer as a ``.pkl`` beside the parquet, used only
            when newer than it. The pickle holds the layer as shipped; the
            spatial index is rebuilt and the Gulf of Finland cut applied after
            every load.
        """
        self.data_dir = Path(data_dir)

        # Find IHO parquet file
        self.iho_path = self._find_iho_file()

        # Initialize hierarchy cache with MarineRegions API
        cache_file = Path(cache_dir) / "sea_hierarchy.tsv"
        self.hierarchy_cache = SeaHierarchyCache(cache_file)

        # Load data
        self._load_iho()

    def _find_iho_file(self) -> Path:
        """Find IHO Sea Areas parquet file in data directory."""
        iho_file = self.data_dir / "World_Seas_IHO_v3.parquet"

        if not iho_file.exists():
            raise FileNotFoundError(
                f"IHO Sea Areas data not found: {iho_file}\n"
                f"Run: python scripts/setup_data.py"
            )

        return iho_file

    def _load_iho(self):
        """Load IHO Sea Areas polygons with pickle caching."""
        if not self.iho_path.exists():
            raise FileNotFoundError(
                f"IHO Sea Areas data not found: {self.iho_path}\n"
                "Expected: World_Seas_IHO_v3.parquet\n"
                "Run: python scripts/setup_data.py"
            )

        # Try pickle cache first
        iho_pickle = self.iho_path.with_suffix('.pkl')
        if iho_pickle.exists():
            # Check if pickle is newer than parquet
            if iho_pickle.stat().st_mtime > self.iho_path.stat().st_mtime:
                try:
                    with open(iho_pickle, 'rb') as f:
                        self.seas = pickle.load(f)
                    cut_gulf_of_finland_lakes(self.seas)
                    # Build spatial index (not pickled)
                    self.seas_sindex = self.seas.sindex
                    return
                except Exception:
                    # If pickle fails, fall back to parquet
                    pass

        # Load from parquet
        self.seas = gpd.read_parquet(self.iho_path, columns=IHO_COLUMNS)

        # Ensure CRS is WGS84
        if self.seas.crs is None:
            self.seas.set_crs("EPSG:4326", inplace=True)
        elif self.seas.crs.to_epsg() != 4326:
            self.seas = self.seas.to_crs("EPSG:4326")

        # Save to pickle cache for faster next load (layer as shipped, uncut).
        try:
            with open(iho_pickle, 'wb') as f:
                pickle.dump(self.seas, f, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            # If pickle save fails, just continue (not critical)
            pass

        cut_gulf_of_finland_lakes(self.seas)
        self.seas_sindex = self.seas.sindex

    def contains(self, lat: float, lon: float) -> bool:
        """True if any IHO sea polygon contains the point. Unlike ``query``,
        never consults the sea -> ocean hierarchy, so it cannot fetch."""
        point = Point(lon, lat)
        candidates = self.seas.geometry.iloc[list(self.seas_sindex.intersection(point.bounds))]
        return bool(candidates.contains(point).any())

    def nearest(self, lat: float, lon: float) -> dict:
        """The IHO sea containing the point (smallest, as in ``query``), else
        the nearest one by planar distance in degrees: ``{"sea", "mrgid"}``.

        Meant for points on a coastline, where the degree ranking is
        harmless. Like ``contains``, never fetches.
        """
        point = Point(lon, lat)
        candidates = self.seas.iloc[list(self.seas_sindex.intersection(point.bounds))]
        containing = candidates[candidates.contains(point)]
        if len(containing):
            row = containing.iloc[int(np.argmin(shapely.area(containing.geometry.values)))]
        else:
            row = self.seas.iloc[int(self.seas_sindex.nearest(point)[1][0])]
        return {"sea": self._extract_sea_name(row), "mrgid": self._extract_mrgid(row)}

    def query(self, lat: float, lon: float) -> Optional[dict]:
        """
        Query sea location information for given coordinates.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Dict with 'ocean', 'sea', 'mrgid' keys, or None if on land

        Note:
            Shapely/GeoPandas use (lon, lat) order, not (lat, lon)
        """
        # Create point (note: lon, lat order for Shapely)
        point = Point(lon, lat)

        # Use spatial index to find candidate polygons
        possible_matches_idx = list(self.seas_sindex.intersection(point.bounds))

        if not possible_matches_idx:
            return None

        possible_matches = self.seas.iloc[possible_matches_idx]

        # Find all polygons that contain the point
        containing_polygons = []
        for idx, row in possible_matches.iterrows():
            if row.geometry.contains(point):
                containing_polygons.append(row)

        if not containing_polygons:
            return None

        # If multiple polygons contain the point, choose the smallest one
        # (most specific sea area)
        if len(containing_polygons) > 1:
            smallest = min(containing_polygons, key=lambda x: x.geometry.area)
            selected = smallest
        else:
            selected = containing_polygons[0]

        # Extract MRGID and sea name
        mrgid = self._extract_mrgid(selected)
        sea_name = self._extract_sea_name(selected)

        if not mrgid and not sea_name:
            return None

        # Look up parent ocean from hierarchy cache (with API fallback)
        if mrgid:
            ocean = self.hierarchy_cache.get_ocean(mrgid)
        else:
            ocean = "Unknown Ocean"

        return {
            "ocean": ocean,
            "sea": sea_name if sea_name else "Unknown Sea",
            "mrgid": mrgid if mrgid else 0,
        }

    def _extract_mrgid(self, row) -> Optional[int]:
        """Extract MRGID from row, trying various column names."""
        for col in ["mrgid", "MRGID", "ID", "id", "OBJECTID"]:
            if col in row.index and row[col]:
                try:
                    return int(row[col])
                except (ValueError, TypeError):
                    continue
        return None

    def _extract_sea_name(self, row) -> Optional[str]:
        """Extract sea name from row, trying various column names."""
        for col in [
            "name",
            "NAME",
            "sea_name",
            "SEA_NAME",
            "label",
            "LABEL",
            "geoname",
            "GEONAME",
        ]:
            if col in row.index and row[col]:
                return str(row[col])
        return None
