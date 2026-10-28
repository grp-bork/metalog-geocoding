"""Fallback geocoding using nearest polygon search.

Candidates are ranked by planar distance in degrees; the reported distance is
always geodesic (WGS84).
"""

from typing import Optional, Tuple
import warnings
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points
from pyproj import Geod

# Distance threshold in meters
DISTANCE_THRESHOLD_M = 10000  # 10 km (default)
DISTANCE_THRESHOLD_POLAR_M = 50000  # 50 km (for polar regions)
POLAR_LATITUDE_THRESHOLD = -60.0  # South of 60°S uses larger threshold

# Inland offset for the second land-lookup attempt, in meters
INLAND_OFFSET_M = 1000  # 1 km


class FallbackGeocoder:
    """
    Handles fallback geocoding by finding nearest polygons.

    Uses geodesic distance calculations (accurate for lat/lon coordinates)
    and returns results with distance information.
    """

    def __init__(self, countries_gdf: gpd.GeoDataFrame, seas_gdf: gpd.GeoDataFrame):
        """
        Initialize fallback geocoder with preloaded geodataframes.

        Args:
            countries_gdf: GeoDataFrame of country polygons (from land geocoder)
                          (Natural Earth admin-0 countries)
            seas_gdf: GeoDataFrame of sea polygons (from sea geocoder)
        """
        self.countries = countries_gdf
        self.seas = seas_gdf

        # Initialize geodesic distance calculator (WGS84)
        self.geod = Geod(ellps='WGS84')

        # Build spatial index for fast land detection
        if hasattr(countries_gdf, 'sindex'):
            self.countries_sindex = countries_gdf.sindex
        else:
            self.countries_sindex = None

    def find_nearest_land(
        self,
        lat: float,
        lon: float
    ) -> Optional[dict]:
        """
        Find nearest land location within threshold.

        Uses adaptive thresholds:
        - 10 km default
        - 50 km for polar regions (south of 60°S)

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Dict describing the nearest land, or None if none within threshold:
            {
                'adjusted_lat': float,  # Coordinates offset 100m inland from nearest shoreline
                'adjusted_lon': float,
                'distance_m': float,    # Distance from original point to nearest shoreline
                'adm0': Series          # The Natural Earth admin-0 row that was nearest
            }

            ``adm0`` is the answer; the adjusted point is only a way to reach
            admin-1, which needs a point inside a polygon. On a narrow key or
            spit the 100 m nudge can land back in the water, so when re-querying
            the adjusted point fails, read the row instead (see
            ``NaturalEarthLandGeocoder.describe``).
        """
        point = Point(lon, lat)

        # Adaptive threshold based on latitude
        threshold_m = DISTANCE_THRESHOLD_POLAR_M if lat < POLAR_LATITUDE_THRESHOLD else DISTANCE_THRESHOLD_M

        # Use spatial index with buffer to find candidate polygons
        # Buffer size depends on threshold: ~50km for polar, ~10km otherwise
        search_buffer = 0.5 if lat < POLAR_LATITUDE_THRESHOLD else 0.1
        search_bounds = (
            lon - search_buffer,
            lat - search_buffer,
            lon + search_buffer,
            lat + search_buffer
        )

        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='.*Geometry is in a geographic CRS.*')

            # Use spatial index to find candidate polygons
            if self.countries_sindex:
                possible_idx = sorted(
                    int(i) for i in self.countries_sindex.intersection(search_bounds)
                )
            else:
                # Fallback: use all polygons (slow)
                possible_idx = list(range(len(self.countries)))

            if not possible_idx:
                return None

            # Calculate distance only to candidate polygons (much faster!)
            candidates = self.countries.iloc[possible_idx]
            distances = candidates.geometry.distance(point)

        # Find closest country among candidates
        min_pos = distances.argmin()
        nearest_geom = candidates.iloc[min_pos].geometry

        # Get nearest point on boundary using shapely
        nearest_pt_land, nearest_pt_original = nearest_points(
            nearest_geom, point
        )

        # Calculate geodesic distance in meters
        distance_m = self._calculate_distance(
            lat, lon, nearest_pt_land.y, nearest_pt_land.x
        )

        if distance_m > threshold_m:
            return None

        # Calculate bearing from original point to nearest shoreline point
        azimuth, _, _ = self.geod.inv(lon, lat, nearest_pt_land.x, nearest_pt_land.y)

        # Extend 100m beyond the shoreline point to get inside the polygon
        # We move in the same direction (original -> shoreline -> inland)
        adjusted_lon, adjusted_lat, _ = self.geod.fwd(
            nearest_pt_land.x,
            nearest_pt_land.y,
            azimuth,
            100  # 100m beyond shoreline
        )

        return {
            'adjusted_lat': adjusted_lat,
            'adjusted_lon': adjusted_lon,
            'distance_m': round(distance_m, 2),
            'adm0': candidates.iloc[min_pos],
        }

    def find_nearest_sea(
        self,
        lat: float,
        lon: float
    ) -> Optional[dict]:
        """
        Find nearest sea location within threshold.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Dict with fallback result or None if no sea within threshold:
            {
                'nearest_lat': float,
                'nearest_lon': float,
                'distance_m': float,
                'mrgid': int,
                'sea_name': str
            }
        """
        point = Point(lon, lat)

        # Calculate distance to all seas (vectorized)
        # Note: Using geographic CRS distance only for nearest polygon identification
        # Actual distance is calculated geodesically below
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='.*Geometry is in a geographic CRS.*')
            # Also catch the "invalid value encountered in distance" warning
            warnings.filterwarnings('ignore', message='.*invalid value encountered in distance.*')
            distances = self.seas.geometry.distance(point)

        # Check if we have valid distances
        if distances.isna().all():
            warnings.warn(
                f"All distance calculations returned NaN for coordinate ({lat}, {lon}). "
                f"This may indicate invalid geometries in the sea data."
            )
            return None

        # Check if there are some NaN distances (problematic but not all)
        nan_count = distances.isna().sum()
        if nan_count > 0:
            warnings.warn(
                f"Found {nan_count} invalid distance calculations for coordinate ({lat}, {lon}). "
                f"Continuing with valid geometries."
            )

        # Find closest sea (returns index label, not position)
        min_idx = distances.idxmin()

        # Validate that we got a valid index (check for NaN)
        if pd.isna(min_idx):
            return None

        # Get the row and geometry
        try:
            row = self.seas.loc[min_idx]
            nearest_geom = row.geometry
        except (KeyError, AttributeError):
            return None

        # Validate geometry before using it - must be a BaseGeometry instance
        if not isinstance(nearest_geom, BaseGeometry):
            return None

        if nearest_geom.is_empty or not nearest_geom.is_valid:
            return None

        # Get nearest point on boundary
        try:
            nearest_pt_sea, _ = nearest_points(
                nearest_geom, point
            )
        except Exception as e:
            # If shapely operations fail, skip this geometry
            sea_name = self._extract_sea_name(row)
            mrgid = self._extract_mrgid(row)
            error_msg = str(e) if str(e) else type(e).__name__
            warnings.warn(
                f"Failed to find nearest point for coordinate ({lat}, {lon}). "
                f"Sea: {sea_name} (MRGID: {mrgid}, index: {min_idx}). "
                f"Error: {error_msg}. "
                f"Geometry type: {nearest_geom.geom_type}, "
                f"is_valid: {nearest_geom.is_valid}, "
                f"is_empty: {nearest_geom.is_empty}"
            )
            return None

        # Calculate geodesic distance in meters
        distance_m = self._calculate_distance(
            lat, lon, nearest_pt_sea.y, nearest_pt_sea.x
        )

        if distance_m > DISTANCE_THRESHOLD_M:
            return None

        # Extract sea info (row already retrieved above)
        mrgid = self._extract_mrgid(row)
        sea_name = self._extract_sea_name(row)

        return {
            'nearest_lat': nearest_pt_sea.y,
            'nearest_lon': nearest_pt_sea.x,
            'distance_m': round(distance_m, 2),
            'mrgid': mrgid,
            'sea_name': sea_name
        }

    def is_on_land(self, lat: float, lon: float) -> bool:
        """
        Check if a point is on land.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            True if point is contained in any country polygon, False otherwise
        """
        point = Point(lon, lat)

        # Use spatial index if available for faster lookup
        if self.countries_sindex:
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='.*Geometry is in a geographic CRS.*')
                possible_idx = sorted(
                    int(i) for i in self.countries_sindex.intersection(point.bounds)
                )

            if not possible_idx:
                return False

            possible_polygons = self.countries.iloc[possible_idx]

            # Check if any polygon contains the point
            for _, row in possible_polygons.iterrows():
                if row.geometry.contains(point):
                    return True
            return False
        else:
            # Fallback: check all polygons (slower)
            return any(self.countries.geometry.contains(point))

    def calculate_inland_offset(
        self,
        lat: float,
        lon: float,
        distance_m: float = INLAND_OFFSET_M
    ) -> Tuple[float, float]:
        """
        Calculate coordinates offset inland from shoreline.

        Strategy depends on whether the point is on land or in water:
        - If ON LAND: Move away from nearest shoreline (further inland)
        - If IN WATER: Move towards nearest shoreline (onto land)

        Args:
            lat: Latitude of point
            lon: Longitude of point
            distance_m: Distance to offset in meters (default 1km)

        Returns:
            Tuple of (offset_lat, offset_lon)
        """
        point = Point(lon, lat)

        # Check if point is on land
        on_land = self.is_on_land(lat, lon)

        # Use spatial index with buffer to find candidate polygons
        # Buffer by ~10km in degrees (roughly 0.1 degrees at equator)
        search_buffer = 0.1  # ~10km buffer
        search_bounds = (
            lon - search_buffer,
            lat - search_buffer,
            lon + search_buffer,
            lat + search_buffer
        )

        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='.*Geometry is in a geographic CRS.*')

            # Use spatial index to find candidate polygons
            if self.countries_sindex:
                possible_idx = sorted(
                    int(i) for i in self.countries_sindex.intersection(search_bounds)
                )
            else:
                # Fallback: use all polygons (slow)
                possible_idx = list(range(len(self.countries)))

            if not possible_idx:
                # No land nearby, use a larger search radius
                search_buffer = 1.0  # ~100km buffer
                search_bounds = (
                    lon - search_buffer,
                    lat - search_buffer,
                    lon + search_buffer,
                    lat + search_buffer
                )
                if self.countries_sindex:
                    possible_idx = sorted(
                        int(i) for i in self.countries_sindex.intersection(search_bounds)
                    )
                else:
                    possible_idx = list(range(len(self.countries)))

            # Calculate distance only to candidate polygons
            candidates = self.countries.iloc[possible_idx]

            if len(candidates) == 0:
                # No land found nearby - can't calculate offset
                # Just return original coordinates (offset will have no effect)
                return (lat, lon)

            distances = candidates.geometry.distance(point)

        # Find closest country among candidates
        min_pos = distances.argmin()
        nearest_geom = candidates.iloc[min_pos].geometry

        # Get nearest point on shoreline (country boundary)
        nearest_pt_shoreline, _ = nearest_points(nearest_geom, point)

        # Calculate bearing from point to nearest shoreline point
        azimuth, _, _ = self.geod.inv(lon, lat, nearest_pt_shoreline.x, nearest_pt_shoreline.y)

        if on_land:
            # Point is on land: move AWAY from shoreline (add 180 degrees)
            azimuth = (azimuth + 180) % 360

        # If in water, keep azimuth as-is (move TOWARDS shoreline)

        # Move specified distance in the calculated direction
        offset_lon, offset_lat, _ = self.geod.fwd(
            lon, lat, azimuth, distance_m
        )

        return (offset_lat, offset_lon)

    def _calculate_distance(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float
    ) -> float:
        """
        Calculate geodesic distance between two points in meters.

        Args:
            lat1, lon1: First point
            lat2, lon2: Second point

        Returns:
            Distance in meters
        """
        _, _, distance = self.geod.inv(lon1, lat1, lon2, lat2)
        return abs(distance)

    def _extract_mrgid(self, row) -> int:
        """Extract MRGID from row."""
        for col in ["mrgid", "MRGID", "ID", "id", "OBJECTID"]:
            if col in row.index and row[col]:
                try:
                    return int(row[col])
                except (ValueError, TypeError):
                    continue
        return 0

    def _extract_sea_name(self, row) -> str:
        """Extract sea name from row."""
        for col in ["name", "NAME", "sea_name", "SEA_NAME", "label", "LABEL", "geoname", "GEONAME"]:
            if col in row.index and row[col]:
                return str(row[col])
        return "Unknown Sea"
