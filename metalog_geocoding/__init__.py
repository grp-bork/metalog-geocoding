"""Reverse geocoder for land and sea locations, and the rules that grade one.

Importing this package must not require the geo stack: a caller that only
talks to the service or reads results back should not pay for geopandas (a
process with the boundary layers loaded uses ~660 MB RSS).

Names that need the geo stack are therefore resolved lazily through the module
``__getattr__`` below. ``import metalog_geocoding`` and
``from metalog_geocoding import Sample`` work without geopandas; asking for
``ReverseGeocoder`` without the extra raises an error naming the extra.

* base    -- ``cache``, ``hierarchy``, ``paths``, ``country_aliases``,
             ``containment``, ``sea_relatedness``, ``vocabulary``,
             ``location_exceptions``, ``hierarchy_table``, ``samples``,
             ``client`` and the override tables. Standard library plus
             ``requests``; enough to build a service request and read the
             response.
* ``geo`` -- ``geocoder``, ``land``, ``sea``, ``longhurst``, ``coast``,
             ``fallback``, ``claims``, ``checker``: everything that opens a
             polygon layer.

A module belongs in the base half only if it imports nothing beyond the
standard library and ``requests``.
"""

from .cache import GeoCache, round_coord
from .client import (
    DEFAULT_SERVICE_URL,
    SERVICE_URL_ENV,
    GeocodingClient,
    GeocodingRequestError,
    GeocodingUnavailable,
    service_url,
)
from .hierarchy import SeaHierarchyCache, UnmappedSeaError, HierarchyLookupError
from .location_exceptions import (
    ACCEPTED_PREFIX,
    LocationExceptionError,
    LocationExceptions,
    load_location_exceptions,
)
from .hierarchy_table import (
    ATTRIBUTE_LEVEL,
    HIERARCHY_COLUMNS,
    QUALIFYING_RESOLUTIONS,
    hierarchy_rows_for,
    hierarchy_sort_key,
)
from .paths import DataDirectoryNotFound, data_dir
from .samples import (
    CODE_ACCEPTED_EXCEPTION,
    CODE_CLAIM_TOO_FAR,
    CODE_DERIVED_COORDINATES,
    CODE_DIFFERENT_LOCATION,
    CODE_NO_COORDINATES,
    CODE_NOT_ON_LAND,
    CODE_NOT_ON_SEA,
    CODE_OK,
    CODE_OUT_OF_RANGE,
    CODE_UNKNOWN_PLACE,
    CODE_UNRELATED_SEA,
    ERROR_CODES,
    NO_COORDINATES,
    BatchResult,
    Sample,
    Verdict,
    coordinate_error,
    distinct_coordinates,
)
from .vocabulary import (
    MarineVocabulary,
    VocabularyCoverageError,
    expected_type,
    load_marine_vocabulary,
    published_sea,
)

#: Attribute name -> module it lives in, for everything that needs the ``geo``
#: extra. Kept as data rather than as a chain of imports in ``__getattr__`` so
#: that ``__dir__`` can list the lazy names without importing anything.
_GEO_EXPORTS = {
    "ReverseGeocoder": "geocoder",
    "LandGeocoder": "land",
    "NaturalEarthLandGeocoder": "land",
    "SeaGeocoder": "sea",
    "LonghurstGeocoder": "longhurst",
    "CoastDistance": "coast",
    "FallbackGeocoder": "fallback",
    "ClaimChecker": "claims",
    "LocationChecker": "checker",
}

_GEO_HINT = (
    "{name} needs the geo stack (geopandas, shapely, pyproj, pandas, pyarrow), "
    "which is not installed.\n"
    "Install it with:  pip install 'metalog-geocoding[geo]'\n"
    "The bare install carries the cache, the hierarchy walk, the naming tables "
    "and the request/response types only -- enough to read a hierarchy back or "
    "call the geocoding service, not to compute one."
)


def __getattr__(name: str):
    module_name = _GEO_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    try:
        module = importlib.import_module(f".{module_name}", __name__)
    except ImportError as exc:
        # Reword only a missing geo dependency; an ImportError from our own
        # code must not be disguised as a missing extra.
        missing = getattr(exc, "name", "") or ""
        if missing.split(".")[0] in {
            "geopandas",
            "shapely",
            "pyproj",
            "pandas",
            "numpy",
            "pyarrow",
        }:
            raise ImportError(_GEO_HINT.format(name=name)) from exc
        raise
    value = getattr(module, name)
    globals()[name] = value  # resolve once; later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_GEO_EXPORTS))


__all__ = [
    # geo
    "ReverseGeocoder",
    "LandGeocoder",
    "NaturalEarthLandGeocoder",
    "SeaGeocoder",
    "LonghurstGeocoder",
    "CoastDistance",
    "FallbackGeocoder",
    "ClaimChecker",
    "LocationChecker",
    # base
    "GeoCache",
    "round_coord",
    "SeaHierarchyCache",
    "UnmappedSeaError",
    "HierarchyLookupError",
    "data_dir",
    "DataDirectoryNotFound",
    "Sample",
    "Verdict",
    "BatchResult",
    "GeocodingClient",
    "GeocodingUnavailable",
    "GeocodingRequestError",
    "service_url",
    "SERVICE_URL_ENV",
    "DEFAULT_SERVICE_URL",
    "NO_COORDINATES",
    "ERROR_CODES",
    "CODE_ACCEPTED_EXCEPTION",
    "CODE_CLAIM_TOO_FAR",
    "CODE_DERIVED_COORDINATES",
    "CODE_DIFFERENT_LOCATION",
    "CODE_NO_COORDINATES",
    "CODE_NOT_ON_LAND",
    "CODE_NOT_ON_SEA",
    "CODE_OK",
    "CODE_OUT_OF_RANGE",
    "CODE_UNKNOWN_PLACE",
    "CODE_UNRELATED_SEA",
    "coordinate_error",
    "distinct_coordinates",
    "MarineVocabulary",
    "VocabularyCoverageError",
    "load_marine_vocabulary",
    "published_sea",
    "expected_type",
    "LocationExceptions",
    "LocationExceptionError",
    "load_location_exceptions",
    "ACCEPTED_PREFIX",
    "ATTRIBUTE_LEVEL",
    "HIERARCHY_COLUMNS",
    "QUALIFYING_RESOLUTIONS",
    "hierarchy_rows_for",
    "hierarchy_sort_key",
]

__version__ = "0.2.0"
