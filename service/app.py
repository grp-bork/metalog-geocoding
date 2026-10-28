"""
HTTP wrapper around metalog_geocoding's LocationChecker.

The boundary layers take over 1 GB of RSS. Serving them from one process lets
callers check coordinates with only the base install
(``metalog_geocoding.client``), which finds the service through
``$METALOG_GEOCODING_URL`` or ``--geocoding-url``.

Design constraints (see service/README.md and docs/api.md):

* **Stateless.** ``/check`` returns hierarchy rows; the caller stores them.
* **Layers load once, at import time.** Any failure there -- a missing data
  directory (``DataDirectoryNotFound``) or a vocabulary that cannot express
  every IHO sea name (``VocabularyCoverageError``) -- must stop the process
  before it binds a port, rather than serve sea names the caller cannot store.
* **No authentication, no TLS.** The network is the boundary: keep the port
  internal, and add authentication before adding any public route.
* **Single worker.** Each worker would load its own copy of the boundary
  layers (~1.2 GB), so run gunicorn with ``--workers 1`` and scale with independent containers,
  each with its own cache directory. ``--threads 4`` is safe, since every
  geocoding call and cache save holds ``_LOCK``, and it keeps ``/health``
  answering while a batch runs.
"""

import os
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, request

# Lets ``python service/app.py`` run from a checkout; a no-op once installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metalog_geocoding
from metalog_geocoding.checker import LocationChecker, Sample, coordinate_error
from metalog_geocoding.location_exceptions import load_location_exceptions
from metalog_geocoding.geocoder import ReverseGeocoder
from metalog_geocoding.hierarchy_table import (
    ARTIFICIAL_NO_LOCATION,
    DERIVED_RESOLUTIONS,
    FULL_HIERARCHY_RESOLUTIONS,
    HIERARCHY_COLUMNS,
)
from metalog_geocoding.land import COUNTRY_NAME_OVERRIDES
from metalog_geocoding.coast import COAST_SOURCE
from metalog_geocoding.paths import data_dir
from metalog_geocoding.sea_relatedness import load_containment
from metalog_geocoding.vocabulary import load_marine_vocabulary

from config import Config

# Load the layers once, at import time. Exceptions here are meant to stop the
# process (see the module docstring).

CONFIG = Config()
print(f"metalog-geocoding service starting with {CONFIG}", file=sys.stderr)

DATA_DIR = data_dir(require=True)
GEOCODER = ReverseGeocoder(DATA_DIR, CONFIG.cache_dir)
# The library loads the coastline lazily; load it now so a broken layer stops
# startup and no request pays the ~5 s load.
GEOCODER.coast

MARINE = load_marine_vocabulary(CONFIG.marine_regions, CONFIG.sea_aliases)
CONTAINMENT = (
    load_containment(CONFIG.sea_containment) if CONFIG.sea_containment else None
)
# from_geocoder() raises VocabularyCoverageError if the vocabulary cannot
# express every IHO sea name; left to propagate on purpose.
EXCEPTIONS = load_location_exceptions(CONFIG.location_exceptions)
CHECKER = LocationChecker.from_geocoder(
    GEOCODER, MARINE, CONTAINMENT, exceptions=EXCEPTIONS
)

print(
    f"metalog-geocoding service ready: data_dir={DATA_DIR}, "
    f"vocabulary={'loaded (' + str(len(MARINE)) + ' names)' if MARINE else 'none (regex classification)'}, "
    f"location_exceptions={len(EXCEPTIONS) if EXCEPTIONS else 0}",
    file=sys.stderr,
)

# Serialises geocoding and cache saves across threads within the one worker;
# the geocoder and its SQLite connections are shared, not thread-safe.
_LOCK = threading.Lock()


# What the browser form offers, derived from the package's constants and the
# loaded layers so the JavaScript holds no copy of any vocabulary.

#: location_resolution values that publish no hierarchy rows. The library never
#: needs to list them, but the form does, so a user can pick one and see that
#: it contributes nothing.
NON_QUALIFYING_RESOLUTIONS = ("unknown", "in orbit")

#: Lookups /geocode accepts. "auto" classifies geographic_location with the
#: same expected_type() that /check applies to every sample.
GEOCODE_TYPES = ("auto", "best", "land", "sea", "both")


def _resolution_options() -> list[dict]:
    """Every location_resolution, with what the table publishes for it."""
    return (
        [
            {"value": value, "hierarchy": "full"}
            for value in sorted(FULL_HIERARCHY_RESOLUTIONS)
        ]
        + [
            {"value": value, "hierarchy": "truncated"}
            for value in sorted(DERIVED_RESOLUTIONS)
        ]
        + [
            {"value": value, "hierarchy": "none"}
            for value in NON_QUALIFYING_RESOLUTIONS
        ]
    )


def _place_names() -> tuple[list[str], list[str]]:
    """The sea and country names a geographic_location can be checked against.

    Seas are in the marine vocabulary's spelling (what the checker compares
    on), or IHO's when no vocabulary is configured. Countries go through the
    same COUNTRY_NAME_OVERRIDES the geocoder publishes with.
    """
    if MARINE is not None:
        seas = sorted(MARINE.names)
    else:
        seas = sorted({str(name) for name in GEOCODER.sea_geocoder.seas["NAME"]})
    adm0 = GEOCODER.land_geocoder.adm0
    countries = sorted(
        {
            COUNTRY_NAME_OVERRIDES.get(a3) or name
            for a3, name in zip(adm0["ADM0_A3"], adm0["NAME_LONG"])
            if isinstance(name, str)
        }
    )
    return seas, countries


def _parse_coordinates(coordinates) -> tuple[list[tuple[float, float]], str | None]:
    """Validate a request's ``coordinates`` array into (lat, lon) floats.

    Shared by /check and /geocode. Only shape is checked: a well-formed but
    impossible coordinate (latitude -115) is reported per coordinate by
    coordinate_error() in a 200 response, not rejected as a bad request.
    """
    parsed: list[tuple[float, float]] = []
    for i, entry in enumerate(coordinates):
        if (
            not isinstance(entry, (list, tuple))
            or len(entry) != 2
            or isinstance(entry[0], bool)
            or isinstance(entry[1], bool)
        ):
            return [], (
                f"coordinates[{i}] must be a [latitude, longitude] pair, got {entry!r}"
            )
        lat, lon = entry
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            return [], f"coordinates[{i}] must be numeric, got {entry!r}"
        parsed.append((float(lat), float(lon)))
    return parsed, None


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    @app.get("/")
    def index():
        """The browser form: static HTML that calls /geocode and /check."""
        return app.send_static_file("index.html")

    @app.get("/options")
    def options():
        """The vocabularies the form's fields offer, so the page holds no copy."""
        seas, countries = _place_names()
        return jsonify(
            {
                "version": metalog_geocoding.__version__,
                "location_resolutions": _resolution_options(),
                "artificial_no_location": sorted(ARTIFICIAL_NO_LOCATION),
                "geocode_types": list(GEOCODE_TYPES),
                "seas": seas,
                "countries": countries,
                "has_vocabulary": MARINE is not None,
            }
        )

    @app.post("/geocode")
    def geocode():
        """Reverse geocode coordinates and return the raw result, ungraded.

        ``type`` is "land", "sea", "both", "best" (ReverseGeocoder.geocode_best)
        or "auto" (classify ``geographic_location`` the way /check does).
        """
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _bad_request("request body must be a JSON object")

        coordinates = payload.get("coordinates")
        if not isinstance(coordinates, list):
            return _bad_request("missing or non-array 'coordinates' key")
        parsed_coords, coord_error = _parse_coordinates(coordinates)
        if coord_error:
            return _bad_request(coord_error)

        requested = payload.get("type", "auto")
        if requested not in GEOCODE_TYPES:
            return _bad_request(
                f"'type' must be one of {', '.join(GEOCODE_TYPES)}, got {requested!r}"
            )
        geographic_location = payload.get("geographic_location", "") or ""
        if requested == "auto":
            types = [CHECKER.expected_type(geographic_location)]
        elif requested == "both":
            types = ["land", "sea"]
        else:
            types = [requested]

        results = []
        with _LOCK:
            try:
                for lat, lon in parsed_coords:
                    # An impossible coordinate is reported per entry, not as
                    # a 400, so the rest of the batch still gets answers.
                    invalid = coordinate_error(lat, lon)
                    for loc_type in types:
                        entry = {
                            "latitude": lat,
                            "longitude": lon,
                            "requested_type": loc_type,
                        }
                        if invalid:
                            entry["error"] = invalid
                        else:
                            entry["result"] = (
                                GEOCODER.geocode_best(lat, lon)
                                if loc_type == "best"
                                else GEOCODER.geocode(lat, lon, loc_type)
                            )
                        results.append(entry)
            finally:
                # Keep whatever was resolved even if a lookup raised.
                GEOCODER.save_caches()

        return jsonify({"results": results})

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "version": metalog_geocoding.__version__,
                "layers_loaded": GEOCODER is not None and CHECKER is not None,
                "vocabulary_size": len(MARINE) if MARINE is not None else None,
                # Zero where a file was expected means the mount is missing.
                "location_exceptions": len(EXCEPTIONS) if EXCEPTIONS is not None else 0,
                # Provenance of the `coast` scheme's distances.
                "coast_source": COAST_SOURCE,
            }
        )

    @app.post("/check")
    def check():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _bad_request("request body must be a JSON object")

        coordinates = payload.get("coordinates")
        if not isinstance(coordinates, list):
            return _bad_request("missing or non-array 'coordinates' key")

        parsed_coords, coord_error = _parse_coordinates(coordinates)
        if coord_error:
            return _bad_request(coord_error)

        samples_in = payload.get("samples")
        if not isinstance(samples_in, list):
            return _bad_request("missing or non-array 'samples' key")

        samples: list[Sample] = []
        for i, raw in enumerate(samples_in):
            if not isinstance(raw, dict):
                return _bad_request(f"samples[{i}] must be an object")
            sample_id = raw.get("id")
            if not sample_id:
                return _bad_request(f"samples[{i}] is missing a non-empty 'id'")
            coord_index = raw.get("coord")
            if coord_index is None:
                # No index: the coordinate is absent or impossible, so it is
                # graded without geocoding. Keeping the sample preserves one
                # verdict per input sample.
                lat = raw.get("latitude", "")
                lon = raw.get("longitude", "")
            else:
                if not isinstance(coord_index, int) or isinstance(coord_index, bool):
                    return _bad_request(
                        f"samples[{i}] ('{sample_id}'): 'coord' must be an integer index"
                    )
                if not (0 <= coord_index < len(parsed_coords)):
                    return _bad_request(
                        f"samples[{i}] ('{sample_id}'): coord index {coord_index} is out "
                        f"of range for {len(parsed_coords)} coordinates"
                    )
                lat, lon = parsed_coords[coord_index]
            samples.append(
                Sample(
                    id=str(sample_id),
                    latitude=lat,
                    longitude=lon,
                    geographic_location=raw.get("geographic_location", "") or "",
                    location_resolution=raw.get("location_resolution", "") or "",
                    artificial=raw.get("artificial", "") or "",
                )
            )

        with _LOCK:
            try:
                batch = CHECKER.check_batch(samples)
            finally:
                # Save completed work even if check_batch() raised.
                GEOCODER.save_caches()

        verdicts = [
            {
                "id": verdict.sample_id,
                "grade": verdict.grade,
                # `code` is stable and meant for comparison; `reason` is for
                # display.
                "code": verdict.code,
                "reason": verdict.reason,
            }
            for verdict in batch.verdicts
        ]
        return jsonify(
            {
                "verdicts": verdicts,
                "location_hierarchy": batch.hierarchy,
            }
        )

    return app


def _bad_request(message: str):
    # A malformed request, as distinct from a sample grading "error" (a 200)
    # and from the service being unavailable (5xx or no connection).
    return jsonify({"error": message}), 400


app = create_app()


if __name__ == "__main__":
    # Development server; production uses gunicorn (see service/README.md).
    # Binds 0.0.0.0 because callers are on other hosts.
    host = os.environ.get("METALOG_GEOCODING_HOST", "0.0.0.0")
    port = int(os.environ.get("METALOG_GEOCODING_PORT", "8007"))
    app.run(host=host, port=port)
