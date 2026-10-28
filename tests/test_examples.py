"""Golden tests: the example outputs in examples/ are what the scripts write.

Each script is run on its example input with a throwaway cache directory
(the bundled sea hierarchy keeps it from reaching MarineRegions) and
the result must be byte-identical to the checked-in file. The outputs depend on
the boundary-layer versions, so a layer update shows up here first; after
checking the diff is intended, regenerate with::

    pytest tests/test_examples.py --update-examples
"""

import importlib.util
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.geo

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _script(name):
    spec = importlib.util.spec_from_file_location(f"_script_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cache_dir(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache


def _compare(produced: Path, expected: Path, update: bool):
    if update:
        shutil.copyfile(produced, expected)
        return
    assert expected.is_file(), f"{expected} is missing; run with --update-examples"
    assert produced.read_bytes() == expected.read_bytes(), (
        f"{expected.name} differs from what the script now writes; if the change "
        "is intended (e.g. a boundary-layer update) rerun with --update-examples"
    )


def test_geocode_example(request, data_dir, cache_dir, tmp_path):
    out = tmp_path / "geocode_output.tsv"
    argv = [str(EXAMPLES / "geocode_input.tsv"), str(out), "--type", "best",
            "--data-dir", str(data_dir), "--cache-dir", str(cache_dir)]
    assert _script("geocode").main(argv) == 0
    _compare(out, EXAMPLES / "geocode_output.tsv", request.config.getoption("--update-examples"))


@pytest.mark.parametrize("name", ["check_simple", "check_metalog"])
def test_check_example(request, data_dir, cache_dir, tmp_path, name):
    prefix = tmp_path / name
    argv = [str(EXAMPLES / f"{name}_input.tsv"), str(prefix),
            "--data-dir", str(data_dir), "--cache-dir", str(cache_dir)]
    assert _script("check").main(argv) == 0
    update = request.config.getoption("--update-examples")
    for suffix in ("verdicts", "hierarchy"):
        _compare(tmp_path / f"{name}_{suffix}.tsv", EXAMPLES / f"{name}_{suffix}.tsv", update)
