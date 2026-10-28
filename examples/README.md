# Examples

Small inputs for the two scripts, with their outputs checked in. The outputs are
also golden tests (`tests/test_examples.py`): the test reruns the scripts and
requires byte-identical results.

**The outputs depend on the boundary-layer versions** (Natural Earth, IHO Sea
Areas, Longhurst, GSHHG) and on the bundled sea vocabulary. When a layer is
updated the test fails; check that the differences are intended and regenerate
with `pytest tests/test_examples.py --update-examples`.

Each input has a `note` column saying what the row shows.

## Geocoding: `scripts/geocode.py`

| File | |
| --- | --- |
| `geocode_input.tsv` | latitude, longitude, note: an inland city, open ocean, a marginal sea, a water point needing a fallback (Venice lagoon), an island (Hawaii), Antarctica, an impossible latitude, a blank coordinate |
| `geocode_output.tsv` | the result of `--type best`: the input columns, `error`, `type`, then the result fields |

```bash
python scripts/geocode.py examples/geocode_input.tsv examples/geocode_output.tsv --type best
```

## Checking: `scripts/check.py`

`check_simple_input.tsv` has only the three required columns (plus `note`):
a correct country and sea, a wrong country (`different_location`), a point in
Liechtenstein within 2 km of the Swiss border labelled Switzerland (ok), a sea
named as an ancestor ocean (ok), an unrelated sea (`unrelated_sea`), coastal
water labelled with the country (ok via the shoreline fallback), and a land
point labelled with a sea (`not_on_sea`).

`check_metalog_input.tsv` adds `id`, `location_resolution` and `artificial`,
showing the Metalog model: `site`, `region` and `city` samples get a full
hierarchy; `country` and `continent` resolution are `uncheckable` (the
coordinate is a centroid) and get a truncated hierarchy; `unknown` resolution
gets none; `negative control` and `mock` samples contribute no hierarchy rows;
`cultivation` is not one of those and counts as a normal sample.

```bash
python scripts/check.py examples/check_simple_input.tsv examples/check_simple
python scripts/check.py examples/check_metalog_input.tsv examples/check_metalog
```

Each writes `<prefix>_verdicts.tsv` and `<prefix>_hierarchy.tsv`.

Note: a sample graded `error` still contributes hierarchy rows (`vienna-wrong`
in the Metalog example); an importer is expected to reject the template on
`error`, so those rows are never loaded.
