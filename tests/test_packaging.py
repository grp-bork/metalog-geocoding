"""The base install must not import the geo stack.

A stray top-level ``import geopandas`` breaks nothing on a machine that has
geopandas. CI's ``bare-install`` job checks with geopandas genuinely absent;
these tests check where it is present, by inspecting ``sys.modules`` and by
blocking the geo stack. Each runs in a fresh interpreter, since pytest has
already imported much of the package by collection time.
"""

import subprocess
import sys
import textwrap

import pytest

#: Everything the base half must not pull in. numpy and pyarrow arrive through
#: geopandas and pandas, so their presence is the same defect.
GEO_MODULES = ["geopandas", "shapely", "pyproj", "pandas", "numpy", "pyarrow"]

#: The modules that make up the base half. Keep this in step with the list in
#: ``metalog_geocoding/__init__.py``; a module added here that imports the geo
#: stack fails the first test below.
BASE_MODULES = [
    "cache",
    "hierarchy",
    "paths",
    "country_aliases",
    "containment",
    "sea_relatedness",
    "vocabulary",
    "hierarchy_table",
    "samples",
    "client",
    "nominatim",
    "wikidata",
    "place_names",
]


def run_python(source: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        capture_output=True,
        text=True,
    )


def test_importing_the_package_loads_no_geo_module():
    """Checked even with geopandas installed."""
    result = run_python(
        f"""
        import sys
        import metalog_geocoding
        leaked = [m for m in {GEO_MODULES!r} if m in sys.modules]
        print(",".join(leaked))
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        f"import metalog_geocoding pulled in {result.stdout.strip()}; "
        "something in the base half grew a top-level geo import"
    )


def test_every_base_module_imports_without_the_geo_stack():
    """Each base module on its own, so the offender is named, not just detected."""
    result = run_python(
        f"""
        import importlib, sys
        offenders = []
        for name in {BASE_MODULES!r}:
            importlib.import_module("metalog_geocoding." + name)
            leaked = [m for m in {GEO_MODULES!r} if m in sys.modules]
            if leaked:
                offenders.append(name + " -> " + ",".join(leaked))
                break
        print("; ".join(offenders))
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", result.stdout.strip()


def test_base_surface_is_reachable_without_the_geo_stack():
    """The names a *client* of the service needs, in a bare interpreter."""
    result = run_python(
        """
        from metalog_geocoding import (
            ATTRIBUTE_LEVEL, GeocodingClient, GeocodingRequestError,
            GeocodingUnavailable, Sample, Verdict, distinct_coordinates,
            hierarchy_rows_for, published_sea, service_url,
        )
        print("ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


@pytest.mark.parametrize("name", ["ReverseGeocoder", "LocationChecker", "ClaimChecker"])
def test_asking_for_a_geo_name_without_the_extra_names_the_extra(name):
    """A caller without the extra is told to install it, not handed a bare
    ``ModuleNotFoundError``. A meta-path hook makes the geo stack unimportable
    in the child process."""
    result = run_python(
        f"""
        import sys

        class Blocker:
            def find_module(self, name, path=None):
                return None
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {GEO_MODULES!r}:
                    raise ModuleNotFoundError("No module named " + repr(name), name=name)
                return None

        sys.meta_path.insert(0, Blocker())
        import metalog_geocoding
        try:
            metalog_geocoding.{name}
        except ImportError as exc:
            print(str(exc))
        else:
            print("NO ERROR RAISED")
        """
    )
    assert result.returncode == 0, result.stderr
    message = result.stdout
    assert "NO ERROR RAISED" not in message
    assert "[geo]" in message, f"the error does not name the extra: {message!r}"
    assert name in message


def test_an_import_error_from_our_own_code_is_not_disguised_as_a_missing_extra():
    """Only an ImportError naming a geo dependency may be reworded into
    "install the extra"; a bug in our own imports must propagate untouched."""

    result = run_python(
        """
        import importlib
        import metalog_geocoding

        real = importlib.import_module

        def fake(name, package=None):
            if name == ".checker":
                # What a typo in one of our own relative imports looks like.
                raise ImportError("No module named 'metalog_geocoding.typo'",
                                  name="metalog_geocoding.typo")
            return real(name, package)

        importlib.import_module = fake
        try:
            metalog_geocoding.LocationChecker
        except ImportError as exc:
            print(str(exc))
        else:
            print("NO ERROR RAISED")
        """
    )
    assert result.returncode == 0, result.stderr
    message = result.stdout
    assert "NO ERROR RAISED" not in message
    assert "metalog_geocoding.typo" in message, (
        f"our own ImportError was swallowed: {message!r}"
    )
    assert "[geo]" not in message, (
        "a bug in this repo was reported as a missing extra, sending the "
        f"reader to pip for something pip cannot fix: {message!r}"
    )
