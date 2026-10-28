"""Main reverse geocoder combining land and sea lookups with caching."""

from pathlib import Path
from typing import Optional

from .cache import GeoCache, round_coord, DEFAULT_DECIMALS
from .land import LandGeocoder
from .sea import SeaGeocoder
from .longhurst import LonghurstGeocoder
from .fallback import FallbackGeocoder

#: How far the nearest coast point may sit from a Natural Earth country and
#: still be placed in it. A map-matching tolerance: GSHHG and Natural Earth
#: draw the same shoreline up to a few km apart. A larger gap usually means
#: Natural Earth lacks that islet and the nearest country is a different
#: landmass, so the landmass's GeoNames country is used instead.
COAST_MATCH_M = 5000

#: The coast hierarchy, from a Natural Earth result: (level, key, result field).
COAST_PLACE_FIELDS = [
    ("coast_continent", "continent"),
    ("coast_country", "country"),
    ("coast_admin_1", "name_1"),
    ("coast_country_code", "country_code"),
    ("coast_adm1_code", "adm1_code"),
]


class ReverseGeocoder:
    """
    Main reverse geocoder for both land and sea locations.

    Combines land and sea geocoders with TSV caching for performance.
    """

    def __init__(
        self,
        data_dir: Path,
        cache_dir: Path,
        cache_decimals: int | None = DEFAULT_DECIMALS,
    ):
        """
        Initialize land and sea geocoders and load caches.

        Args:
            data_dir: Directory containing data files
            cache_dir: Directory for cache files
            cache_decimals: Decimal places for cache keys, or None (the default)
                to key on the coordinate exactly as given. Rounding lets a
                nearby coordinate answer for this one, which near a shoreline is
                wrong.

        Expected data files:
            - data_dir/ne_10m_admin_0_countries.parquet and
              data_dir/ne_10m_admin_1_states_provinces.parquet (Natural Earth)
            - data_dir/World_Seas_IHO_v3.parquet (IHO Sea Areas)
            - data_dir/Longhurst_world_v4_2010.parquet (Longhurst Provinces)
            - data_dir/GSHHS_f_L{1,5}.parquet and GSHHS_f_names.parquet
              (GSHHG coast, for distance to coast; loaded on first use)

        Note:
            Lookups are offline. The only network access is the MarineRegions
            hierarchy walk in hierarchy.py, for MRGIDs not yet in its cache.
        """
        data_dir = Path(data_dir)
        cache_dir = Path(cache_dir)

        # Initialize geocoders (both find their data files automatically)
        self.land_geocoder = LandGeocoder(data_dir)
        self.sea_geocoder = SeaGeocoder(data_dir, cache_dir)
        self.longhurst_geocoder = LonghurstGeocoder(data_dir)
        self.data_dir = data_dir
        self._coast = None  # CoastDistance, loaded on first use

        # Initialize fallback geocoder
        self.fallback_geocoder = FallbackGeocoder(
            self.land_geocoder.countries, self.sea_geocoder.seas
        )

        # Initialize caches
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_decimals = cache_decimals
        self.land_cache = GeoCache(cache_dir / "land_cache.sqlite", cache_decimals)
        self.sea_cache = GeoCache(cache_dir / "sea_cache.sqlite", cache_decimals)


    def geocode(self, lat: float, lon: float, location_type: str) -> dict:
        """
        Reverse geocode a coordinate to location information with fallback support.

        Args:
            lat: Latitude (-90 to 90)
            lon: Longitude (-180 to 180)
            location_type: "land" or "sea" (required)

        Returns:
            Dict with location information:
            - type: "land", "sea", or "unknown"
            - is_fallback: True if result came from nearest neighbor search
            - fallback_distance_m: Distance to nearest location (if fallback)
            - original_lat/original_lon: Original coordinates (if fallback with offset)
            - For land: continent, continent_political, country, country_code,
              iso_3166_2, name_1 and type_1
            - For sea: ocean, sea, mrgid, plus longhurst_code,
              longhurst_province and longhurst_biome when the point falls in
              (or within 10 km of) a Longhurst province

        Example:
            >>> geocoder.geocode(52.52, 13.40, "land")
            {'type': 'land', 'is_fallback': False, 'continent': 'Europe',
             'continent_political': 'Europe', 'country': 'Germany',
             'country_code': 'DEU', 'name_1': 'Berlin', 'type_1': 'Land'}

            >>> geocoder.geocode(55.00, 21.00, "sea")
            {'type': 'sea', 'is_fallback': False,
             'ocean': 'Baltic Sea; North Atlantic Ocean; Atlantic Ocean',
             'sea': 'Baltic Sea', 'mrgid': 2401,
             'longhurst_code': 'NECS',
             'longhurst_province': 'NE Atlantic Shelves Province',
             'longhurst_biome': 'Coastal'}
        """
        lat_r, lon_r = round_coord(lat, lon, self.cache_decimals)

        if location_type == "land":
            # Check land cache (but only return if it has actual data OR fallback was tried)
            cached = self.land_cache.lookup(lat_r, lon_r)
            if cached:
                # Return if we have actual data
                if "country" in cached or "country_code" in cached:
                    # Top up entries that lack the coast fields, in place.
                    if not self._has_coast(cached):
                        self._add_coast(lat, lon, cached, "land")
                        self.land_cache.add(lat_r, lon_r, cached)
                    self._refresh_chain(cached, "coast_mrgid", "coast_sea_chain")
                    return {"type": "land", **cached}
                # Return if fallback was already attempted and failed
                if cached.get("fallback_tried"):
                    return {"type": "unknown", "is_fallback": False}

            # Try different coordinate adjustments in order. The fourth item
            # is the admin-0 row to read the answer off when the point itself
            # lands in no polygon; only step 3 has one.
            adjustments = [
                (lat, lon, False, 0.0, None),  # 1. Original coordinates (no adjustment)
            ]

            # 2. If direct query fails, try 1km inland offset
            offset_lat, offset_lon = self.fallback_geocoder.calculate_inland_offset(lat, lon)
            adjustments.append((offset_lat, offset_lon, True, 1000.0, None))

            # 3. Try nearest land fallback
            fallback_result = self.fallback_geocoder.find_nearest_land(lat, lon)
            if fallback_result:
                adjustments.append((
                    fallback_result["adjusted_lat"],
                    fallback_result["adjusted_lon"],
                    True,
                    fallback_result["distance_m"],
                    fallback_result["adm0"],
                ))

            # Try each adjustment until we get a country
            for adj_lat, adj_lon, is_fallback, distance_m, adm0 in adjustments:
                result = self.land_geocoder.query(adj_lat, adj_lon)
                if result is None and adm0 is not None:
                    # The nearest polygon was found, but the point nudged past
                    # its shoreline landed back in the water (a narrow key, a
                    # spit). Read the polygon directly; admin-1 stays absent.
                    result = self.land_geocoder.describe(adm0)
                if result and ("country" in result or "country_code" in result):
                    # Success - add fallback metadata if adjusted
                    result["is_fallback"] = is_fallback
                    if is_fallback:
                        result["fallback_distance_m"] = distance_m
                        result["original_lat"] = lat
                        result["original_lon"] = lon
                    self._add_coast(lat, lon, result, "land")
                    self.land_cache.add(lat_r, lon_r, result)
                    return {"type": "land", **result}

            # All attempts failed - cache failure to avoid retrying
            failed_result = {"is_fallback": False, "fallback_tried": True}
            self.land_cache.add(lat_r, lon_r, failed_result)
            return {"type": "unknown", "is_fallback": False}

        if location_type == "sea":
            # Check sea cache (but only return if it has actual data OR fallback was tried)
            cached = self.sea_cache.lookup(lat_r, lon_r)
            if cached:
                # Return if we have actual data
                if "ocean" in cached or "sea" in cached:
                    # The stored `ocean` chain is never trusted; see
                    # _refresh_ocean. Only the returned dict is patched.
                    self._refresh_ocean(cached)
                    # Top up entries lacking the Longhurst or coast fields in
                    # place, rather than discarding the cached result.
                    stale = False
                    if not self._has_longhurst(cached):
                        self._add_longhurst(lat, lon, cached)
                        stale = True
                    if not self._has_coast(cached):
                        self._add_coast(lat, lon, cached, "sea")
                        stale = True
                    if stale:
                        self.sea_cache.add(lat_r, lon_r, cached)
                    return {"type": "sea", **cached}
                # Return if fallback was already attempted and failed
                if cached.get("fallback_tried"):
                    return {"type": "unknown", "is_fallback": False}

            # Query sea geocoder (point-in-polygon)
            result = self.sea_geocoder.query(lat, lon)
            if result:
                result["is_fallback"] = False
                self._add_longhurst(lat, lon, result)
                self._add_coast(lat, lon, result, "sea")
                self.sea_cache.add(lat_r, lon_r, result)
                return {"type": "sea", **result}

            # Not in sea - try nearest sea fallback
            if location_type == "sea":
                fallback_result = self.fallback_geocoder.find_nearest_sea(lat, lon)
                if fallback_result:
                    # Use the ocean hierarchy to get the ocean name
                    ocean = self.sea_geocoder.hierarchy_cache.get_ocean(
                        fallback_result["mrgid"]
                    )

                    # Construct result from fallback data
                    nearest_result = {
                        "ocean": ocean,
                        "sea": fallback_result["sea_name"],
                        "mrgid": fallback_result["mrgid"],
                        "is_fallback": True,
                        "fallback_distance_m": fallback_result["distance_m"],
                        "original_lat": lat,
                        "original_lon": lon,
                    }
                    self._add_longhurst(lat, lon, nearest_result)
                    self._add_coast(lat, lon, nearest_result, "sea")
                    self.sea_cache.add(lat_r, lon_r, nearest_result)
                    return {"type": "sea", **nearest_result}
                else:
                    # Fallback failed (too far from sea) - cache this to avoid retrying
                    failed_result = {"is_fallback": False, "fallback_tried": True}
                    self.sea_cache.add(lat_r, lon_r, failed_result)

        # Cache unknown results based on the location type that was tried
        unknown_result = {"is_fallback": False, "fallback_tried": True}
        if location_type == "land":
            self.land_cache.add(lat_r, lon_r, unknown_result)
        else:  # sea
            self.sea_cache.add(lat_r, lon_r, unknown_result)

        return {"type": "unknown", "is_fallback": False}

    @staticmethod
    def _has_longhurst(result: dict) -> bool:
        """True if a Longhurst lookup has already been done for this result."""
        return "longhurst_code" in result or bool(result.get("longhurst_tried"))

    def _refresh_ocean(self, result: dict) -> None:
        """
        Re-derive `ocean` from the mrgid stored on a cached sea result, in place.

        `ocean` is a denormalised copy of the chain in the hierarchy cache,
        which can be corrected later without touching the coordinate cache.
        Re-deriving it on every read keeps a warm run identical to a cold one.

        Reads `hierarchy_cache.cache` directly rather than calling
        `get_ocean()`, because a cache read must never fetch. If the mrgid is
        not in the hierarchy cache, the stored chain is left alone.
        """
        self._refresh_chain(result, "mrgid", "ocean")

    def _refresh_chain(self, result: dict, mrgid_field: str, chain_field: str) -> None:
        """Re-derive ``result[chain_field]`` from ``result[mrgid_field]`` via the
        in-memory hierarchy cache, in place (see ``_refresh_ocean``)."""
        mrgid = result.get(mrgid_field)
        if not mrgid:
            return
        try:
            mrgid_int = int(mrgid)
        except (TypeError, ValueError):
            return
        chain = self.sea_geocoder.hierarchy_cache.cache.get(mrgid_int)
        if chain:
            result[chain_field] = chain

    def _add_longhurst(self, lat: float, lon: float, result: dict) -> dict:
        """
        Attach Longhurst province fields to a sea result, modifying it in place.

        Looked up at the original coordinate, not where the sea fallback
        snapped to: LonghurstGeocoder does its own 10 km nearest-province
        search, so a non-marine point gets no province rather than a distant
        one. Failures are marked (``longhurst_tried``) so they are not retried.
        """
        province = self.longhurst_geocoder.query(lat, lon)
        if province:
            result.update(province)
        else:
            result["longhurst_tried"] = True
        return result

    @staticmethod
    def _has_coast(result: dict) -> bool:
        """True if the signed distance to coast has been computed for this result.

        ``coast_flag`` is always written, so it doubles as the marker. Any
        value other than ``"ok"`` counts as not computed.
        """
        return result.get("coast_flag") == "ok"

    @property
    def coast(self):
        """The GSHHG distance-to-coast index, loaded on first use.

        The largest layer (several hundred MB of RSS); a run answering from
        cache never needs it, so it is loaded and imported lazily.
        """
        if self._coast is None:
            from .coast import CoastDistance

            self._coast = CoastDistance(self.data_dir)
        return self._coast

    def _add_coast(self, lat: float, lon: float, result: dict, side: str) -> dict:
        """
        Attach the signed distance to coast (negative on land), the landmass
        area and the place of the nearest coast to a land or sea result, in
        place. ``side`` is the result's type, ``"land"`` or ``"sea"``.

        Measured at the original coordinate, not where a fallback snapped to,
        so a fallback keeps GSHHG's sign (a sea sample 3 km inland reads -3.0).

        IHO and Natural Earth outrank GSHHG on which side of the shoreline a
        point is, when they agree: a direct sea hit where Natural Earth has no
        country, or a direct land hit where IHO has no sea, gets distance 0 if
        GSHHG puts it on the other side. GSHHG's shoreline can be off by a
        kilometre or more on reef flats.
        """
        found = self.coast.query(lat, lon)
        gshhg_land = found["coast_distance_km"] < 0
        if not result.get("is_fallback"):
            if side == "sea" and gshhg_land and self.land_geocoder.query(lat, lon) is None:
                found["coast_distance_km"] = 0.0
            elif side == "land" and found["coast_distance_km"] > 0 and not self.sea_geocoder.contains(lat, lon):
                found["coast_distance_km"] = 0.0
        point = found.pop("coast_point", None)
        landmass = found.pop("coast_landmass", None)
        result.update(found)
        if point is None:
            return result
        if side == "sea":
            result.update(self._coast_place(*point, landmass))
        else:
            result.update(self._coast_sea(*point))
        return result

    def _coast_sea(self, lat: float, lon: float) -> dict:
        """
        The IHO sea a land point's nearest coast faces, and its ocean chain.

        A land point's own place is already in its ``admin`` block, so its
        coast is described by the water. The sea is the IHO polygon at or
        nearest to the GSHHG coast point, so a point up a tidal estuary gets
        the sea at its mouth. The chain is refreshed from ``coast_mrgid`` on
        every cache read, as ``ocean`` is for a sea result.
        """
        nearest = self.sea_geocoder.nearest(lat, lon)
        out = {"coast_sea": nearest["sea"], "coast_mrgid": nearest["mrgid"]}
        chain = self.sea_geocoder.hierarchy_cache.cache.get(int(nearest["mrgid"]))
        if chain:
            out["coast_sea_chain"] = chain
        return out

    def _coast_place(self, lat: float, lon: float, landmass) -> dict:
        """
        Which country and admin-1 region the nearest coast point is in, in the
        same vocabulary as the ``admin`` scheme.

        1. Natural Earth at the point, or the nearest Natural Earth country
           within COAST_MATCH_M of it. First, because a landmass can be split
           between countries (Ireland, Hispaniola) and only the point says
           which side it is on.
        2. Otherwise the GeoNames country (and admin-1, where GeoNames' code
           names exactly one Natural Earth region) of the landmass, for islets
           Natural Earth does not have.
        3. Otherwise nothing.

        ``coast_country_source`` records which of the two answered.
        """
        land = self.land_geocoder
        place, source = land.query(lat, lon), "natural_earth"
        if place is None:
            nearest = self.fallback_geocoder.find_nearest_land(lat, lon)
            if nearest and nearest["distance_m"] <= COAST_MATCH_M:
                place = land.query(nearest["adjusted_lat"], nearest["adjusted_lon"])
                if place is None:
                    place = land.describe(nearest["adm0"])
        if place is None and landmass is not None and landmass.ne_adm0_a3:
            adm0 = self._adm0_by_code().get(landmass.ne_adm0_a3)
            adm1 = self._adm1_by_code().get(landmass.ne_adm1_code) if landmass.ne_adm1_code else None
            if adm0 is not None:
                place, source = land.describe(adm0, adm1, lon), "geonames"
        if place is None:
            return {}
        out = {key: place[field] for key, field in COAST_PLACE_FIELDS if place.get(field)}
        out["coast_country_source"] = source
        return out

    def _adm0_by_code(self) -> dict:
        if not hasattr(self, "_adm0_index"):
            adm0 = self.land_geocoder.adm0
            self._adm0_index = {code: adm0.iloc[i] for i, code in enumerate(adm0["ADM0_A3"])}
        return self._adm0_index

    def _adm1_by_code(self) -> dict:
        if not hasattr(self, "_adm1_index"):
            adm1 = self.land_geocoder.adm1
            self._adm1_index = {code: adm1.iloc[i] for i, code in enumerate(adm1["adm1_code"])}
        return self._adm1_index

    def geocode_best(self, lat: float, lon: float) -> dict:
        """
        Reverse geocode a coordinate without knowing whether it is land or sea.

        Looks the point up on both layers and keeps one result, which keeps its
        own ``type``: a direct (non-fallback) land hit wins, since a point
        inside a country polygon is on land; otherwise a direct sea hit; otherwise
        whichever fallback result snapped over the shorter distance
        (``fallback_distance_m``, land winning a tie); otherwise the unknown
        result, which carries ``type == "unknown"``.
        """
        land = self.geocode(lat, lon, "land")
        sea = self.geocode(lat, lon, "sea")

        def found(result: dict) -> bool:
            return result.get("type") in ("land", "sea")

        if found(land) and not land.get("is_fallback"):
            return land
        if found(sea) and not sea.get("is_fallback"):
            return sea
        candidates = [r for r in (land, sea) if found(r)]
        if candidates:
            # min() keeps the first of equal distances, so land wins a tie.
            return min(
                candidates,
                key=lambda r: r.get("fallback_distance_m", float("inf")),
            )
        return land

    def geocode_batch(self, coordinates: list[tuple[float, float, str]]) -> list[dict]:
        """
        Batch geocode multiple coordinates.

        Args:
            coordinates: List of (lat, lon, location_type) tuples
                        location_type must be "land" or "sea"

        Returns:
            List of geocode result dicts

        Example:
            >>> coords = [(52.52, 13.40, "land"), (55.00, 21.00, "sea")]
            >>> results = geocoder.geocode_batch(coords)
        """
        results = []
        for lat, lon, loc_type in coordinates:
            results.append(self.geocode(lat, lon, loc_type))
        return results

    def save_caches(self):
        """
        Persist caches to disk.

        Call this after batch processing to commit all cached results.
        Includes land cache, sea cache, and ocean hierarchy cache.
        """
        self.land_cache.save()
        self.sea_cache.save()
        self.sea_geocoder.hierarchy_cache.save_cache()

    def get_cache_stats(self) -> dict:
        """
        Get statistics about cache sizes.

        Returns:
            Dict with 'land_cache_size' and 'sea_cache_size'
        """
        return {
            "land_cache_size": len(self.land_cache),
            "sea_cache_size": len(self.sea_cache),
        }
