#!/usr/bin/env python3
"""
Resolve a list of place names the way POST /place-distance does, for review.

    python scripts/evaluate_places.py names.txt review.tsv

``names.txt`` has one place name per line (blank lines and ``#`` lines are
skipped). ``review.tsv`` gets one row per name: ``name``, ``status``,
``interpreted_as``, ``n_candidates``, ``source`` / ``geometry`` /
``area_km2`` / ``kind`` / ``label`` of the first candidate, ``message``, and
``candidates``, every candidate as ``kind [geometry, area km2] label``
separated by `` || ``. That last column is what a reviewer reads to judge
whether the intended place is among the candidates; docs/place-distance.md
describes how the shipped rules were evaluated that way.

The resolver is the one the service builds, over the same layers and the same
place cache (``place_cache.sqlite`` in ``--cache-dir``). Online lookups are
paced at one request per 1.1 s per source, so a first run over a few hundred
new names takes a while; a second run over the same names reads the cache and
sends nothing. ``--offline`` never touches the network: names whose answer is
not cached come back ``pending``. The exit code is 0 whatever the statuses;
the summary goes to stderr.
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metalog_geocoding.vocabulary import (  # noqa: E402
    BUNDLED_MARINE_REGIONS,
    BUNDLED_SEA_ALIASES,
)

COLUMNS = (
    "name", "status", "interpreted_as", "n_candidates",
    "source", "geometry", "area_km2", "kind", "label", "message", "candidates",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve place names as /place-distance does and write a review table.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("names", type=Path, help="one place name per line")
    parser.add_argument("out", type=Path, help="review table to write (TSV)")
    parser.add_argument("--offline", action="store_true",
                        help="no network: names not in the cache come back pending")
    parser.add_argument("--no-wikidata", action="store_true", help="skip Wikidata")
    parser.add_argument("--no-nominatim", action="store_true", help="skip Nominatim")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="boundary layers (default: $METALOG_GEOCODING_DATA, else data/)")
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"),
                        help="result and place caches (default: cache/)")
    return parser


def read_names(path: Path) -> list[str]:
    """Distinct names in file order; blank and ``#`` lines skipped."""
    names = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            name = line.strip()
            if name and not name.startswith("#"):
                names.append(name)
    return list(dict.fromkeys(names))


def describe(candidate) -> str:
    area = "" if candidate.area_km2 is None else round(candidate.area_km2)
    return f"{candidate.kind} [{candidate.geometry_type}, {area} km2] {candidate.label}"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    names = read_names(args.names)

    # Imported late so --help and argument errors do not pay for the geo stack.
    from metalog_geocoding.geocoder import ReverseGeocoder
    from metalog_geocoding.nominatim import NominatimClient, PlaceCache
    from metalog_geocoding.paths import data_dir
    from metalog_geocoding.places import LookupBudget, PlaceResolver
    from metalog_geocoding.vocabulary import load_marine_vocabulary
    from metalog_geocoding.wikidata import WikidataClient

    geocoder = ReverseGeocoder(data_dir(args.data_dir, require=True), args.cache_dir)
    cache = PlaceCache(args.cache_dir / "place_cache.sqlite")
    nominatim = None if args.no_nominatim else NominatimClient(cache)
    wikidata = None if args.no_wikidata else WikidataClient(cache)
    resolver = PlaceResolver(
        geocoder, load_marine_vocabulary(BUNDLED_MARINE_REGIONS, BUNDLED_SEA_ALIASES),
        nominatim, wikidata,
    )

    counts: Counter = Counter()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(COLUMNS)
        for i, name in enumerate(names, 1):
            # A zero budget is how "offline" reaches the resolver: anything
            # not cached is pending rather than fetched.
            budget = LookupBudget(0) if args.offline else None
            res = resolver.resolve(name, budget=budget)
            first = res.candidates[0] if res.candidates else None
            counts[(res.status, first.source if first else "")] += 1
            writer.writerow([
                name, res.status, res.interpreted_as or "", len(res.candidates),
                first.source if first else "",
                first.geometry_type if first else "",
                "" if not first or first.area_km2 is None else round(first.area_km2),
                first.kind if first else "",
                first.label if first else "",
                res.message or "",
                " || ".join(describe(c) for c in res.candidates),
            ])
            if i % 50 == 0:
                print(f"  {i}/{len(names)} names", file=sys.stderr, flush=True)

    print(f"{len(names)} names:", file=sys.stderr)
    for (status, source), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {status}{' via ' + source if source else ''}", file=sys.stderr)
    sent = sum(c.requests_sent for c in (nominatim, wikidata) if c is not None)
    print(f"wrote {args.out} ({sent} online requests)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
