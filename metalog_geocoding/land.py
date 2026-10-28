"""Land-based reverse geocoding using Natural Earth boundaries.

Natural Earth is the land source because it is public domain, so annotations
derived from it can be redistributed (e.g. under ODbL). Any other boundary
source must allow the same; GADM, for example, forbids redistribution.

``NaturalEarthLandGeocoder`` is exported as ``LandGeocoder`` as well; it is what
``ReverseGeocoder`` and ``FallbackGeocoder`` consume.

Data
----
* ``data/ne_10m_admin_0_countries.parquet``  - 258 units, the country layer.
* ``data/ne_10m_admin_1_states_provinces.parquet`` - 4,596 units, used for
  ``name_1`` and for the geographic-continent overrides.

Both are converted from the Natural Earth 10m GeoJSON downloads. They load in
well under a second, so there is no pickle cache on this path.

Continent resolution
--------------------
``continent_political`` (human samples; all of Russia is Europe)
    admin-0 ``CONTINENT``, patched through ``SEVEN_SEAS_CONTINENTS`` for the
    nine open-ocean units, then ``POLITICAL_CONTINENTS`` where that table has
    an entry for the country code.

``continent`` (geographic; Russia splits Europe/Asia)
    admin-1 ``iso_3166_2`` looked up in the merged override table
    (``GEOGRAPHIC_OVERRIDES`` + ``RUSSIA_GEOGRAPHIC_CONTINENTS`` +
    ``NE_ADM1_GEOGRAPHIC_EXTRA``), else the same admin-0 ``CONTINENT`` as above.

There is deliberately no spatial join against separate continent polygons:
remote islands fall into the gaps between them. Reading the continent off the
country polygon already hit has no gaps.

Natural Earth's continent vocabulary is Africa / Antarctica / Asia / Europe /
North America / Oceania / South America, which is what the override tables use.
"""

from pathlib import Path
from typing import Optional
import warnings

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from .containment import validate_targets
from .geographic_overrides import GEOGRAPHIC_OVERRIDES, WESTERN_PART_OVERRIDES
from .political_continents import POLITICAL_CONTINENTS
from .natural_earth_continents import (
    SEVEN_SEAS_CONTINENTS,
    RUSSIA_GEOGRAPHIC_CONTINENTS,
    NE_ADM1_GEOGRAPHIC_EXTRA,
)

#: Natural Earth's placeholder value in the admin-0 ``CONTINENT`` column.
SEVEN_SEAS = "Seven seas (open ocean)"

#: Natural Earth's placeholder for "no code".
NE_NULL_CODE = "-99"

#: Units where Natural Earth's ``NAME_LONG`` is not the common English name
#: (formal, variant or abbreviated forms). ``_validate_tables`` asserts that no
#: other unit has an abbreviated ``NAME_LONG``, so a new Natural Earth release
#: cannot quietly publish e.g. "Turks and Caicos Is.".
COUNTRY_NAME_OVERRIDES = {
    "RUS": "Russia",                              # NAME_LONG: Russian Federation
    "CZE": "Czechia",                             # NAME_LONG: Czech Republic
    "LAO": "Laos",                                # NAME_LONG: Lao PDR
    "KOR": "South Korea",                         # NAME_LONG: Republic of Korea
    "FLK": "Falkland Islands",                    # NAME_LONG: Falkland Islands / Malvinas
    "PRK": "North Korea",                         # NAME_LONG: Dem. Rep. Korea
    "HMD": "Heard Island and McDonald Islands",   # NAME_LONG: Heard I. and ...
    "CNM": "Cyprus U.N. Buffer Zone",             # ADMIN is worse: "Cyprus No Mans Area"
    "CPV": "Cabo Verde",                          # NAME_LONG: Republic of Cabo Verde
    "SWZ": "Eswatini",                            # NAME_LONG: Kingdom of eSwatini
}

# The two Congos are deliberately NOT overridden: "Democratic Republic of the
# Congo" and "Republic of the Congo" only look formal, and are the standard
# forms that keep them apart. NE's short NAME abbreviates both.

ADM0_PARQUET = "ne_10m_admin_0_countries.parquet"
ADM1_PARQUET = "ne_10m_admin_1_states_provinces.parquet"

#: Columns read from the admin-0 layer: every attribute any lookup, validation
#: or dependent module (e.g. ``claims.py``'s ``SOVEREIGNT``) uses. Every row
#: selection copies whatever is loaded, so add a column here before using it
#: (a missing one fails with ``KeyError``).
ADM0_COLUMNS = [
    "ADM0_A3",      # country_code fallback; Seven-seas/abbreviation validation keys
    "ADMIN",        # _country_name fallback when NAME_LONG is absent
    "NAME_LONG",    # _country_name; abbreviation validation
    "CONTINENT",    # continent_political base; Seven-seas/blank-continent validation
    "ISO_A3_EH",    # country_code
    "SOVEREIGNT",   # claims.py: dependency-aware claim matching
    "geometry",
]

#: Columns read from the admin-1 layer (see ``ADM0_COLUMNS``).
ADM1_COLUMNS = [
    "adm0_a3",      # Russian federal-subject validation
    "adm1_code",    # adm1_code: unique and never blank, unlike iso_3166_2
    "iso_3166_2",   # geographic-continent override lookup
    "name",         # name_1
    "name_en",      # name_1 fallback
    "type_en",      # type_1
    "type",         # type_1 fallback
    "geometry",
]


def _merge_geographic_overrides() -> dict[str, str]:
    """Merge the three ISO 3166-2 keyed continent tables into one dict.

    They must not overlap - an overlap would mean two tables disagree about the
    same subdivision, which should be resolved in the tables, not silently here.
    """
    merged: dict[str, str] = {}
    for table in (
        GEOGRAPHIC_OVERRIDES,
        RUSSIA_GEOGRAPHIC_CONTINENTS,
        NE_ADM1_GEOGRAPHIC_EXTRA,
    ):
        clash = {
            k for k in table if k in merged and merged[k] != table[k]
        }
        if clash:
            raise ValueError(
                f"Conflicting geographic continent overrides for: {sorted(clash)}"
            )
        merged.update(table)
    return merged


#: ISO 3166-2 subdivision code -> geographic continent.
ADM1_GEOGRAPHIC_CONTINENTS = _merge_geographic_overrides()


class NaturalEarthLandGeocoder:
    """Reverse geocoder for land locations using Natural Earth boundaries."""

    def __init__(self, data_dir: Path, validate: bool = True):
        """
        Load the Natural Earth admin-0 and admin-1 layers.

        Args:
            data_dir: Directory containing the Natural Earth parquet files
            validate: Check the built-in tables still match the data (cheap;
                only turn it off if Natural Earth was updated on purpose)
        """
        self.data_dir = Path(data_dir)
        self._load()
        if validate:
            self._validate_tables()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self):
        """Load both Natural Earth layers and build their spatial indexes."""
        adm0_path = self.data_dir / ADM0_PARQUET
        adm1_path = self.data_dir / ADM1_PARQUET

        for path in (adm0_path, adm1_path):
            if not path.exists():
                raise FileNotFoundError(
                    f"Natural Earth data not found: {path}\n"
                    "Expected the 10m admin-0 and admin-1 layers as GeoParquet.\n"
                    "Run: python scripts/setup_data.py"
                )

        self.adm0 = self._read(adm0_path, ADM0_COLUMNS)
        self.adm1 = self._read(adm1_path, ADM1_COLUMNS)

        # Spatial indexes for fast point-in-polygon lookups
        self.adm0_sindex = self.adm0.sindex
        self.adm1_sindex = self.adm1.sindex

        # Polygon areas (degrees^2), precomputed so that picking the smallest
        # containing polygon costs nothing per query. Only used to rank
        # candidates, never reported, so the geographic CRS is fine.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=".*Geometry is in a geographic CRS.*"
            )
            self._adm0_area = self.adm0.geometry.area.to_numpy()
            self._adm1_area = self.adm1.geometry.area.to_numpy()

    @staticmethod
    def _read(path: Path, columns: list[str]) -> gpd.GeoDataFrame:
        """Read a GeoParquet file and force it to EPSG:4326.

        ``columns`` narrows the read to what this module and its callers use.
        """
        gdf = gpd.read_parquet(path, columns=columns)
        if gdf.crs is None:
            gdf.set_crs("EPSG:4326", inplace=True)
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        return gdf.reset_index(drop=True)

    def _validate_tables(self):
        """Fail loudly if Natural Earth no longer matches the built-in tables.

        Invariants:

        1. The set of admin-0 units with ``CONTINENT == "Seven seas (open
           ocean)"`` is exactly the set ``SEVEN_SEAS_CONTINENTS`` covers.
        2. Every Russian federal subject in the admin-1 layer has a geographic
           continent assignment.
        3. No admin-0 unit outside ``COUNTRY_NAME_OVERRIDES`` has an
           abbreviated ``NAME_LONG``, which would otherwise be published.
        4. Every ``CONTAINED_IN`` target is a name this class can publish, and no
           ``CONTAINED_IN`` key has become a Natural Earth unit in its own right.

        The tables were written against a specific Natural Earth release; if a
        newer release adds or removes units, silently falling back would put
        samples on the wrong continent or publish a bad name.
        """
        seven_seas = set(
            self.adm0.loc[self.adm0["CONTINENT"] == SEVEN_SEAS, "ADM0_A3"]
        )
        if seven_seas != set(SEVEN_SEAS_CONTINENTS):
            raise ValueError(
                "Natural Earth's 'Seven seas (open ocean)' units no longer match "
                "SEVEN_SEAS_CONTINENTS in metalog_geocoding/natural_earth_continents.py.\n"
                f"  in data, not in table: {sorted(seven_seas - set(SEVEN_SEAS_CONTINENTS))}\n"
                f"  in table, not in data: {sorted(set(SEVEN_SEAS_CONTINENTS) - seven_seas)}"
            )

        abbreviated = {
            a3: name
            for a3, name in zip(self.adm0["ADM0_A3"], self.adm0["NAME_LONG"])
            if isinstance(name, str) and "." in name and a3 not in COUNTRY_NAME_OVERRIDES
        }
        if abbreviated:
            raise ValueError(
                "Natural Earth NAME_LONG values contain abbreviations that "
                "COUNTRY_NAME_OVERRIDES does not cover, so they would be "
                f"published as-is: {abbreviated}"
            )

        validate_targets(
            {
                COUNTRY_NAME_OVERRIDES.get(a3) or name
                for a3, name in zip(self.adm0["ADM0_A3"], self.adm0["NAME_LONG"])
                if isinstance(name, str)
            }
        )

        blank_continent = self.adm0["CONTINENT"].isna() | (
            self.adm0["CONTINENT"].fillna("").str.strip() == ""
        )
        if blank_continent.any():
            missing = sorted(self.adm0.loc[blank_continent, "ADM0_A3"])
            raise ValueError(
                f"Natural Earth admin-0 units without a CONTINENT value: {missing}"
            )

        russia_codes = {
            code
            for code in self.adm1.loc[self.adm1["adm0_a3"] == "RUS", "iso_3166_2"]
            if isinstance(code, str) and code.startswith("RU-")
        }
        uncovered = russia_codes - set(RUSSIA_GEOGRAPHIC_CONTINENTS)
        if uncovered:
            raise ValueError(
                "Russian federal subjects with no geographic continent in "
                f"RUSSIA_GEOGRAPHIC_CONTINENTS: {sorted(uncovered)}"
            )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, lat: float, lon: float) -> Optional[dict[str, str]]:
        """
        Query land location information for given coordinates.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Dict with country and continent information, or None if the point
            is in no Natural Earth country polygon (i.e. at sea).
            Example: {
                'continent': 'Europe',
                'continent_political': 'Europe',
                'country': 'Germany',
                'country_code': 'DEU',
                'name_1': 'Berlin',
                'type_1': 'State',
                'iso_3166_2': 'DE-BE',
            }
        """
        # Public API is (lat, lon); Shapely is (lon, lat)
        point = Point(lon, lat)

        adm0 = self._contains(self.adm0, self.adm0_sindex, self._adm0_area, point)
        if adm0 is None:
            return None

        adm1 = self._contains(self.adm1, self.adm1_sindex, self._adm1_area, point)
        return self.describe(adm0, adm1, lon)

    def describe(self, adm0, adm1=None, lon: Optional[float] = None) -> dict[str, str]:
        """
        Build a result dict from the Natural Earth rows a point fell in.

        Separate from ``query`` so a caller that has an admin-0 row but no
        point inside it can still get a result: the shoreline fallback finds the
        nearest admin-0 polygon, and the point nudged onto it can land back in
        the water on a narrow key or spit.

        With ``adm1`` omitted the admin-1 fields (``name_1``, ``type_1``,
        ``iso_3166_2``) and the ISO 3166-2 continent override are absent: for a
        point outside every polygon the nearest country is a measurement, the
        subdivision would be a guess.

        ``lon`` decides overrides that cover only part of a subdivision
        (``WESTERN_PART_OVERRIDES``); without it they do not apply.
        """
        # Continent, with the nine open-ocean units mapped to a real continent
        continent_base = self._admin0_continent(adm0)

        result: dict[str, str] = {
            "continent": continent_base,
            "country": self._country_name(adm0),
            "country_code": self._country_code(adm0),
        }

        # Geographic continent: an ISO 3166-2 override wins over the country's
        # own continent (Hawaii, Sinai, French overseas departments, Russia).
        iso_3166_2 = self._str(adm1.get("iso_3166_2")) if adm1 is not None else None
        if iso_3166_2:
            result["iso_3166_2"] = iso_3166_2
            override = ADM1_GEOGRAPHIC_CONTINENTS.get(iso_3166_2)
            if iso_3166_2 in WESTERN_PART_OVERRIDES and lon is not None:
                cutoff, western = WESTERN_PART_OVERRIDES[iso_3166_2]
                override = western if lon < cutoff else None
            if override:
                result["continent"] = override

        # Political continent: the country's own continent, then the
        # capital-based table for transcontinental countries.
        political = continent_base
        country_code = result.get("country_code")
        if country_code and country_code in POLITICAL_CONTINENTS:
            political = POLITICAL_CONTINENTS[country_code]
        result["continent_political"] = political

        # Admin level 1 (Natural Earth has no deeper levels)
        if adm1 is not None:
            adm1_code = self._str(adm1.get("adm1_code"))
            if adm1_code:
                result["adm1_code"] = adm1_code
            name_1 = self._str(adm1.get("name")) or self._str(adm1.get("name_en"))
            if name_1:
                result["name_1"] = name_1
            type_1 = self._str(adm1.get("type_en")) or self._str(adm1.get("type"))
            if type_1:
                result["type_1"] = type_1

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _admin0_continent(self, adm0) -> Optional[str]:
        """Continent of an admin-0 row, with the open-ocean placeholder fixed."""
        continent = self._str(adm0.get("CONTINENT"))
        if continent == SEVEN_SEAS:
            return SEVEN_SEAS_CONTINENTS[self._str(adm0.get("ADM0_A3"))]
        return continent

    def _country_name(self, adm0) -> Optional[str]:
        """
        Common English country name for an admin-0 row.

        Natural Earth's name fields disagree on the everyday form: ``ADMIN``
        is formal ("United States of America"), ``BRK_NAME`` abbreviates
        ("Turks and Caicos Is."), and ``NAME_LONG`` is the common form for
        almost every unit, so it is the default. ``COUNTRY_NAME_OVERRIDES``
        covers the rest.
        """
        a3 = self._str(adm0.get("ADM0_A3"))
        if a3 and a3 in COUNTRY_NAME_OVERRIDES:
            return COUNTRY_NAME_OVERRIDES[a3]
        return self._str(adm0.get("NAME_LONG")) or self._str(adm0.get("ADMIN"))

    def _country_code(self, adm0) -> Optional[str]:
        """
        Three-letter country code for an admin-0 row.

        ``ISO_A3_EH`` is preferred, since Natural Earth's ``ISO_A3`` is ``-99``
        for some units including France and Norway. Where ``ISO_A3_EH`` is also
        ``-99`` (Kosovo, Somaliland, Northern Cyprus and other disputed or
        unclaimed units), ``ADM0_A3`` is used: always populated, but Natural
        Earth's own code space (e.g. ``KOS``), not ISO.

        A few small dependencies share their sovereign's code: Clipperton
        Island is ``FRA``, the Indian Ocean Territories are ``AUS``.
        """
        iso = self._str(adm0.get("ISO_A3_EH"))
        if iso and iso != NE_NULL_CODE:
            return iso
        return self._str(adm0.get("ADM0_A3"))

    @staticmethod
    def _str(value) -> Optional[str]:
        """Normalise a Natural Earth attribute to a non-empty string or None."""
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        return text or None

    @staticmethod
    def _contains(gdf, sindex, areas, point: Point):
        """
        Return the polygon row containing ``point``, or None.

        The spatial index does the point-in-polygon test itself
        (``predicate="within"``: the point is within the polygon), so only
        matching rows are ever materialised. Where more than one polygon
        contains the point the smallest wins, so an enclave beats the polygon it
        sits inside (Natural Earth's admin-0 layer overlaps for a few disputed
        units).
        """
        hits = sindex.query(point, predicate="within")
        if len(hits) == 0:
            return None
        if len(hits) == 1:
            return gdf.iloc[int(hits[0])]
        return gdf.iloc[int(hits[areas[hits].argmin()])]

    # ------------------------------------------------------------------
    # Compatibility
    # ------------------------------------------------------------------

    @property
    def countries(self) -> gpd.GeoDataFrame:
        """
        The admin-0 GeoDataFrame, for ``FallbackGeocoder``.

        ``FallbackGeocoder`` uses only ``.geometry.distance()``, ``.sindex``
        and ``.iloc`` on it.
        """
        return self.adm0


#: Short alias, exported by the package.
LandGeocoder = NaturalEarthLandGeocoder
