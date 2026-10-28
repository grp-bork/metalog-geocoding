#!/usr/bin/env python3
"""
Reverse geocode coordinates: what is at this latitude/longitude?

    python scripts/geocode.py input.tsv output.tsv [--type best|land|sea|both]

The input is a TSV with a header and the columns ``latitude`` and
``longitude``. Every other column is passed through unchanged, in order, ahead
of the result columns. A blank latitude or longitude passes the row through
with empty results; an impossible coordinate (latitude 95, say) gets an
``error`` message and no lookup.

``--type`` (default ``best``):

* ``best``  land or sea, whichever fits the point (ReverseGeocoder.geocode_best);
            the result keeps its ``type`` column
* ``land``  Natural Earth lookup only
* ``sea``   IHO sea + Longhurst lookup only
* ``both``  both lookups, columns prefixed ``land_`` and ``sea_``

To ask whether a coordinate *agrees with a stated location name*, use
scripts/check.py instead.
"""

import argparse
import csv
import sys
from pathlib import Path

# Standalone runs import the package from the repo root; an installed package
# makes this a no-op.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metalog_geocoding.samples import coordinate_error  # noqa: E402

try:  # declared in the `scripts` extra; plain stderr counts without it
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None

TYPES = ("best", "land", "sea", "both")

#: Result columns in the order they are written; whatever is not listed (or
#: prefixed as below) follows, sorted.
MAIN_COLUMNS = [
    "continent",
    "continent_political",
    "country",
    "country_code",
    "iso_3166_2",
    "adm1_code",
    "name_1",
    "type_1",
    "ocean",
    "sea",
    "mrgid",
]
FALLBACK_COLUMNS = ["is_fallback", "fallback_distance_m", "original_lat", "original_lon"]
PREFIX_GROUPS = ["longhurst_", "coast_"]


def column_rank(name: str) -> tuple:
    """Sort key giving the fixed column order described above."""
    if name == "type":
        return (0, 0, name)
    if name in MAIN_COLUMNS:
        return (1, MAIN_COLUMNS.index(name), name)
    if name in FALLBACK_COLUMNS:
        return (2, FALLBACK_COLUMNS.index(name), name)
    for i, prefix in enumerate(PREFIX_GROUPS):
        if name.startswith(prefix):
            return (3 + i, 0, name)
    return (9, 0, name)


def fmt(value) -> str:
    """Deterministic text for one cell: repr() for floats (shortest string that
    reads back identically), True/False for booleans, empty for None."""
    if value is None:
        return ""
    if isinstance(value, float):
        return repr(value)
    return str(value)


def flatten(result: dict, prefix: str = "") -> dict:
    return {f"{prefix}{key}": value for key, value in result.items()}


def parse_coordinate(row: dict):
    """(lat, lon) floats, None when either is blank, or an error string."""
    raw_lat = (row.get("latitude") or "").strip()
    raw_lon = (row.get("longitude") or "").strip()
    if not raw_lat or not raw_lon:
        return None, None
    try:
        lat, lon = float(raw_lat), float(raw_lon)
    except ValueError:
        return None, f"latitude/longitude are not numbers: {raw_lat!r}, {raw_lon!r}"
    if lat != lat or lon != lon:
        return None, "latitude/longitude are not numbers"
    return (lat, lon), coordinate_error(lat, lon)


def lookup(geocoder, lat: float, lon: float, kind: str) -> dict:
    if kind == "best":
        return flatten(geocoder.geocode_best(lat, lon))
    if kind == "both":
        return {
            **flatten(geocoder.geocode(lat, lon, "land"), "land_"),
            **flatten(geocoder.geocode(lat, lon, "sea"), "sea_"),
        }
    return flatten(geocoder.geocode(lat, lon, kind))


def order_columns(names, kind: str) -> list[str]:
    names = set(names)
    if kind != "both":
        return sorted(names, key=column_rank)
    ordered = []
    for prefix in ("land_", "sea_"):
        mine = [n for n in names if n.startswith(prefix)]
        ordered += sorted(mine, key=lambda n: column_rank(n[len(prefix):]))
    return ordered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reverse geocode coordinates from a TSV file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", type=Path, help="TSV with latitude and longitude columns")
    parser.add_argument("output", type=Path, help="output TSV")
    parser.add_argument("--type", choices=TYPES, default="best", dest="kind")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="boundary layers (default: $METALOG_GEOCODING_DATA, else data/)",
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("cache"), help="result caches (default: cache/)"
    )
    return parser


def read_input(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames or []
        rows = list(reader)
    missing = [c for c in ("latitude", "longitude") if c not in fields]
    if missing:
        raise SystemExit(f"error: {path} has no {' / '.join(missing)} column")
    if "error" in fields:
        raise SystemExit("error: the input already has a column named 'error'")
    return fields, rows


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    fields, rows = read_input(args.input)

    # Imported late so --help and argument errors do not pay for the geo stack.
    from metalog_geocoding.geocoder import ReverseGeocoder
    from metalog_geocoding.paths import data_dir

    geocoder = ReverseGeocoder(data_dir(args.data_dir, require=True), args.cache_dir)

    parsed = [parse_coordinate(row) for row in rows]
    distinct = list(dict.fromkeys(c for c, err in parsed if c is not None and err is None))
    print(f"{len(rows)} rows, {len(distinct)} distinct valid coordinates", file=sys.stderr)

    results: dict[tuple[float, float], dict] = {}
    try:
        iterator = tqdm(distinct, desc="Geocoding") if tqdm else distinct
        for i, (lat, lon) in enumerate(iterator, 1):
            results[(lat, lon)] = lookup(geocoder, lat, lon, args.kind)
            if tqdm is None and i % 100 == 0:
                print(f"  {i}/{len(distinct)}", file=sys.stderr)
    finally:
        geocoder.save_caches()

    result_names = {name for r in results.values() for name in r}
    clash = result_names & set(fields)
    if clash:
        raise SystemExit(f"error: input columns clash with result columns: {sorted(clash)}")
    result_cols = order_columns(result_names, args.kind)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(fields + ["error"] + result_cols)
        errors = 0
        for row, (coord, err) in zip(rows, parsed):
            errors += bool(err)
            result = results.get(coord, {}) if coord is not None and not err else {}
            writer.writerow(
                [row.get(f) or "" for f in fields]
                + [err or ""]
                + [fmt(result.get(c)) for c in result_cols]
            )

    print(f"wrote {args.output} ({len(rows)} rows, {errors} errors)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
