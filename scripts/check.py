#!/usr/bin/env python3
"""
Check coordinates against the location each sample states for itself.

    python scripts/check.py input.tsv out_prefix

Writes ``out_prefix_verdicts.tsv`` and ``out_prefix_hierarchy.tsv``. This is
the same LocationChecker the service runs, built the same way, so a verdict
here is the verdict ``POST /check`` would return.

Input columns: ``latitude``, ``longitude`` and ``geographic_location`` are
required. Optional: ``id`` (default: the 1-based row number),
``location_resolution`` and ``artificial``; an absent column is the same as a
blank one (an observed coordinate with a full hierarchy; a normal sample). Any
other column is ignored.

The verdicts file has ``grade`` (ok / error / uncheckable / no coordinates),
``code`` (the rule that fired, what to compare on) and ``reason`` (the
human-readable sentence). The hierarchy file is the ``location_hierarchy``
rows the samples contribute; it has no ``location_resolution`` column if the
input had none. The exit code is 0 whatever the grades; the summary goes to
stderr.

To geocode coordinates with no stated location, use scripts/geocode.py.
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metalog_geocoding.hierarchy_table import HIERARCHY_COLUMNS  # noqa: E402
from metalog_geocoding.samples import Sample  # noqa: E402
from metalog_geocoding.vocabulary import (  # noqa: E402
    BUNDLED_MARINE_REGIONS,
    BUNDLED_SEA_ALIASES,
    BUNDLED_SEA_CONTAINMENT,
)

REQUIRED = ("latitude", "longitude", "geographic_location")
OPTIONAL = ("location_resolution", "artificial")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grade coordinates against their stated geographic_location.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("out_prefix", type=Path, help="writes <prefix>_verdicts.tsv and <prefix>_hierarchy.tsv")
    parser.add_argument("--marine-regions", type=Path, default=BUNDLED_MARINE_REGIONS,
                        help="sea vocabulary, one name per line (default: bundled)")
    parser.add_argument("--sea-aliases", type=Path, default=BUNDLED_SEA_ALIASES,
                        help="other spellings of those names (default: bundled)")
    parser.add_argument("--sea-containment", type=Path, default=BUNDLED_SEA_CONTAINMENT,
                        help="seas IHO has no polygon for (default: bundled)")
    parser.add_argument("--no-vocabulary", action="store_true",
                        help="switch off all three vocabulary files")
    parser.add_argument("--location-exceptions", type=Path, default=None,
                        help="verified-correct coordinates (default: none)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="boundary layers (default: $METALOG_GEOCODING_DATA, else data/)")
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"),
                        help="result caches (default: cache/)")
    return parser


def read_samples(path: Path) -> tuple[list[str], list[dict], list[Sample]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames or []
        rows = list(reader)
    missing = [c for c in REQUIRED if c not in fields]
    if missing:
        raise SystemExit(f"error: {path} is missing required column(s): {', '.join(missing)}")
    samples = [
        Sample.from_mapping(
            {k: (v or "").strip() for k, v in row.items() if k is not None},
            id=(row.get("id") or "").strip() or str(i),
        )
        for i, row in enumerate(rows, 1)
    ]
    return fields, rows, samples


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    fields, rows, samples = read_samples(args.input)

    # Imported late so --help and argument errors do not pay for the geo stack.
    from metalog_geocoding.checker import LocationChecker
    from metalog_geocoding.geocoder import ReverseGeocoder
    from metalog_geocoding.location_exceptions import load_location_exceptions
    from metalog_geocoding.paths import data_dir
    from metalog_geocoding.sea_relatedness import load_containment
    from metalog_geocoding.vocabulary import load_marine_vocabulary

    geocoder = ReverseGeocoder(data_dir(args.data_dir, require=True), args.cache_dir)
    if args.no_vocabulary:
        marine, containment = None, None
    else:
        marine = load_marine_vocabulary(args.marine_regions, args.sea_aliases)
        containment = load_containment(args.sea_containment) if args.sea_containment else None
    exceptions = load_location_exceptions(args.location_exceptions)
    checker = LocationChecker.from_geocoder(geocoder, marine, containment, exceptions=exceptions)

    try:
        batch = checker.check_batch(samples)
    finally:
        geocoder.save_caches()

    prefix = str(args.out_prefix)
    Path(prefix).parent.mkdir(parents=True, exist_ok=True)
    present = [c for c in ("id",) + REQUIRED + OPTIONAL if c in fields]
    # `id` is always written: it is the row number when the input has none.
    out_cols = ["id"] + [c for c in present if c != "id"]
    with open(f"{prefix}_verdicts.tsv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(out_cols + ["grade", "code", "reason", "claim_type"])
        for sample, row, verdict in zip(samples, rows, batch.verdicts):
            values = [sample.id if c == "id" else (row.get(c) or "") for c in out_cols]
            writer.writerow(values + [verdict.grade, verdict.code, verdict.reason, verdict.loc_type])

    hier_cols = [c for c in HIERARCHY_COLUMNS
                 if c != "location_resolution" or "location_resolution" in fields]
    with open(f"{prefix}_hierarchy.tsv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(hier_cols)
        for row in batch.hierarchy:
            writer.writerow([row.get(c, "") for c in hier_cols])

    counts = Counter(v.grade for v in batch.verdicts)
    print(f"{len(samples)} samples: "
          + ", ".join(f"{g} {n}" for g, n in sorted(counts.items())), file=sys.stderr)
    print(f"wrote {prefix}_verdicts.tsv and {prefix}_hierarchy.tsv "
          f"({len(batch.hierarchy)} hierarchy rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
