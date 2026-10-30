"""Argument and column handling of scripts/geocode.py, scripts/check.py and scripts/evaluate_places.py.

Stdlib only: nothing here loads a layer. The scripts import the geo stack
inside main(), after argument parsing, which is what makes this possible.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _script(name):
    spec = importlib.util.spec_from_file_location(f"_script_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_geocode_column_order_is_fixed():
    geocode = _script("geocode")
    names = ["zeta", "coast_flag", "is_fallback", "sea", "type", "country", "longhurst_code", "alpha"]
    assert geocode.order_columns(names, "best") == [
        "type", "country", "sea", "is_fallback", "longhurst_code", "coast_flag", "alpha", "zeta"
    ]


def test_geocode_both_groups_land_before_sea():
    geocode = _script("geocode")
    names = ["sea_type", "land_country", "sea_sea", "land_type"]
    assert geocode.order_columns(names, "both") == ["land_type", "land_country", "sea_type", "sea_sea"]


def test_geocode_cells_are_deterministic():
    geocode = _script("geocode")
    assert geocode.fmt(None) == "" and geocode.fmt(True) == "True" and geocode.fmt(0.1) == "0.1"


def test_geocode_coordinate_parsing():
    geocode = _script("geocode")
    assert geocode.parse_coordinate({"latitude": "", "longitude": ""}) == (None, None)
    assert geocode.parse_coordinate({"latitude": "1", "longitude": "2"}) == ((1.0, 2.0), None)
    coord, err = geocode.parse_coordinate({"latitude": "95", "longitude": "2"})
    assert coord == (95.0, 2.0) and err
    assert geocode.parse_coordinate({"latitude": "x", "longitude": "2"})[1]


def test_check_requires_its_columns(tmp_path):
    check = _script("check")
    path = tmp_path / "in.tsv"
    path.write_text("latitude\tlongitude\n1\t2\n")
    with pytest.raises(SystemExit, match="geographic_location"):
        check.read_samples(path)


def test_check_defaults_id_to_the_row_number(tmp_path):
    check = _script("check")
    path = tmp_path / "in.tsv"
    path.write_text("latitude\tlongitude\tgeographic_location\n1\t2\tFrance\n3\t4\tItaly\n")
    _, _, samples = check.read_samples(path)
    assert [s.id for s in samples] == ["1", "2"]
    assert all(s.location_resolution == "" and s.artificial == "" for s in samples)


def test_evaluate_places_reads_distinct_names_in_order(tmp_path):
    evaluate = _script("evaluate_places")
    names = tmp_path / "names.txt"
    names.write_text("Disko Bay\n\n# a comment\nLake Zug\nDisko Bay\n  Andes  \n", encoding="utf-8")
    assert evaluate.read_names(names) == ["Disko Bay", "Lake Zug", "Andes"]


def test_evaluate_places_offline_flag_parses():
    evaluate = _script("evaluate_places")
    args = evaluate.build_parser().parse_args(["n.txt", "out.tsv", "--offline", "--no-wikidata"])
    assert args.offline and args.no_wikidata and not args.no_nominatim
