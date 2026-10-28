"""Longhurst biogeochemical province lookup for marine coordinates.

Longhurst partitions the open ocean into 54 provinces grouped into four biomes
(Polar, Westerlies, Trades, Coastal). Unlike the IHO sea areas, which name a
body of water, these are ecological units.

The polygons stop at the coastline and exclude inland water, so a coordinate can
be a valid IHO sea but have no province: the Caspian Sea, Amazon river stations,
and points on the Antarctic ice sheet all fall outside. Those are left blank
rather than snapped to a distant province - see NEAREST_THRESHOLD_M.
"""

import re
import warnings
from pathlib import Path
from typing import Optional

import geopandas as gpd
from shapely.geometry import Point
from shapely.ops import nearest_points
from pyproj import Geod

#: Trailing alternate code in ProvDescr, e.g. "... Province (POLR)". Only a bare
#: four-letter uppercase group is an alternate code; "(West)" and "(East)" are
#: part of the province name and must survive.
RE_ALT_CODE = re.compile(r"\s*\([A-Z]{4}\)$")

#: How far a coordinate may sit outside every province and still be assigned the
#: nearest one. Matches the sea fallback in fallback.py: coastal provinces end at
#: a coarser shoreline than IHO's, so near-shore stations can miss by a few
#: hundred metres. Beyond this the point is treated as non-marine.
NEAREST_THRESHOLD_M = 10000  # 10 km

#: Columns read from the province layer, passed explicitly like the other
#: layers so an upstream schema change cannot add unread columns.
LONGHURST_COLUMNS = ["ProvCode", "ProvDescr", "geometry"]


class LonghurstGeocoder:
    """Point-in-polygon lookup against Longhurst Provinces v4 (2010)."""

    def __init__(self, data_dir: Path):
        """
        Load the Longhurst province polygons.

        Args:
            data_dir: Directory containing Longhurst_world_v4_2010.parquet
        """
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "Longhurst_world_v4_2010.parquet"

        if not self.path.exists():
            raise FileNotFoundError(
                f"Longhurst Provinces data not found: {self.path}\n"
                f"Run: python scripts/download_data.py"
            )

        self.provinces = gpd.read_parquet(self.path, columns=LONGHURST_COLUMNS)

        if self.provinces.crs is None:
            self.provinces.set_crs("EPSG:4326", inplace=True)
        elif self.provinces.crs.to_epsg() != 4326:
            self.provinces = self.provinces.to_crs("EPSG:4326")

        self.sindex = self.provinces.sindex
        self.geod = Geod(ellps="WGS84")

    def query(self, lat: float, lon: float) -> Optional[dict]:
        """
        Look up the Longhurst province containing a coordinate.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Dict with 'longhurst_code', 'longhurst_province', 'longhurst_biome'
            and (only when the nearest-province fallback was used)
            'longhurst_distance_m'. None when the point is not marine.

        Note:
            Shapely/GeoPandas use (lon, lat) order, not (lat, lon).
        """
        point = Point(lon, lat)

        # Provinces do not overlap, so the first containing polygon is the answer
        for idx in self.sindex.intersection(point.bounds):
            row = self.provinces.iloc[idx]
            if row.geometry.contains(point):
                return self._describe(row)

        return self._find_nearest(point, lat, lon)

    def _find_nearest(self, point: Point, lat: float, lon: float) -> Optional[dict]:
        """Snap to the nearest province, if one is within NEAREST_THRESHOLD_M."""
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=".*Geometry is in a geographic CRS.*"
            )
            distances = self.provinces.geometry.distance(point)

        if distances.isna().all():
            return None

        # Degree distance only ranks candidates; the reported distance is geodesic
        nearest_idx = distances.idxmin()
        boundary_pt, _ = nearest_points(self.provinces.geometry.loc[nearest_idx], point)
        _, _, distance_m = self.geod.inv(lon, lat, boundary_pt.x, boundary_pt.y)

        if not (distance_m <= NEAREST_THRESHOLD_M):  # also rejects NaN
            return None

        result = self._describe(self.provinces.loc[nearest_idx])
        result["longhurst_distance_m"] = round(distance_m, 2)
        return result

    @staticmethod
    def _describe(row) -> dict:
        """Split ProvDescr ("Biome - Name (ALT)") into biome and province name."""
        biome, _, name = str(row["ProvDescr"]).partition(" - ")
        return {
            "longhurst_code": str(row["ProvCode"]),
            "longhurst_province": RE_ALT_CODE.sub("", name).strip(),
            "longhurst_biome": biome.strip(),
        }
