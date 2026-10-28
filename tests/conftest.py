"""Shared fixtures.

The suite mirrors the package's split (see ``metalog_geocoding/__init__.py``):

* **stdlib-only tests** use the base-install modules and must never import
  geopandas, directly or indirectly; they are the executable check that the
  base install really is geo-free.
* **``@pytest.mark.geo`` tests** open the real boundary layers. They need the
  ``[geo]`` extra and the parquet files, and skip when either is missing.

Nothing here writes to the repository's ``cache/``; anything that saves a
cache uses ``tmp_path``.
"""

import importlib.util
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--update-examples",
        action="store_true",
        default=False,
        help="rewrite the checked-in outputs in examples/ instead of comparing",
    )


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


GEO_INSTALLED = _has("geopandas") and _has("shapely") and _has("pyproj")


@pytest.fixture(scope="session")
def data_dir():
    """The boundary-layer directory, or skip."""
    if not GEO_INSTALLED:
        pytest.skip("needs the [geo] extra")
    from metalog_geocoding.paths import DataDirectoryNotFound, data_dir as resolve

    try:
        return resolve(require=True)
    except DataDirectoryNotFound as exc:
        pytest.skip(f"boundary layers are not available: {exc}")


@pytest.fixture(scope="session")
def geocoder(data_dir, tmp_path_factory):
    """A ReverseGeocoder over the real layers, with an empty cache directory.

    The sea hierarchy ships with the package and covers all 101 IHO MRGIDs, so
    no sea lookup goes to the network. The result caches start empty, so every
    geo test computes its answer from the polygons rather than reading back a
    stored one.

    Session-scoped: loading the layers costs ~1.3 s and ~660 MB.
    """
    from metalog_geocoding.geocoder import ReverseGeocoder

    return ReverseGeocoder(data_dir, tmp_path_factory.mktemp("cache"))


@pytest.fixture(scope="session")
def vocabulary():
    """The marine vocabulary bundled with the package."""
    from metalog_geocoding.vocabulary import (
        BUNDLED_MARINE_REGIONS,
        BUNDLED_SEA_ALIASES,
        load_marine_vocabulary,
    )

    return load_marine_vocabulary(BUNDLED_MARINE_REGIONS, BUNDLED_SEA_ALIASES)


@pytest.fixture(scope="session")
def containment():
    from metalog_geocoding.sea_relatedness import load_containment
    from metalog_geocoding.vocabulary import BUNDLED_SEA_CONTAINMENT

    return load_containment(BUNDLED_SEA_CONTAINMENT)


@pytest.fixture(scope="session")
def checker(geocoder, vocabulary, containment):
    """A LocationChecker over the real layers and vocabulary, without an
    exceptions file, so tests see the grading rules alone."""

    from metalog_geocoding.checker import LocationChecker

    return LocationChecker.from_geocoder(geocoder, vocabulary, containment)


@pytest.fixture(scope="session")
def checker_with_exceptions(geocoder, vocabulary, containment, tmp_path_factory):
    """The same checker plus a one-entry exceptions file written for the test."""
    from metalog_geocoding.checker import LocationChecker
    from metalog_geocoding.location_exceptions import load_location_exceptions

    path = tmp_path_factory.mktemp("exceptions") / "location_exceptions.tsv"
    path.write_text(
        "latitude\tlongitude\tgeographic_location\treason\n"
        "16.82383\t-88.10582\tBelize\tTwin Cays, a mangrove cay absent from Natural Earth 10m\n",
        encoding="utf-8",
    )
    return LocationChecker.from_geocoder(
        geocoder,
        vocabulary,
        containment,
        exceptions=load_location_exceptions(path),
    )
