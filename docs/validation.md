# Validating coordinates against stated locations

`LocationChecker` (`metalog_geocoding/checker.py`) grades whether a sample's
coordinate agrees with the place it claims (`geographic_location`). It is used by
the service's `POST /check` (and so by `GeocodingClient`) and by
`scripts/check.py`. Grading is deliberately forgiving: it is meant to run as a
fail-closed gate on import, where a rule that fires on naming noise is unusable.

## Grades

| Grade | Meaning |
| --- | --- |
| `ok` | the coordinate does not contradict the stated location |
| `error` | it does |
| `uncheckable` | the sample sets `location_resolution` to `country` or `continent` (it is optional; when omitted the coordinate is treated as observed and graded): the coordinate was derived from the name and cannot contradict it |
| `no coordinates` | none, or unparseable |

There is no `warn`: a warning that fires on every import is one nobody reads. A
caller that fails on `error` alone must not see a new grade arrive as a pass, so the
vocabulary is fixed.

## Codes and reasons

Every verdict has a `code` and a `reason` for different readers. `code` is the rule
that fired (the `CODE_*` constants in `samples.py`, listed by `ERROR_CODES`):
`not_on_land`, `not_on_sea`, `unrelated_sea`, `claim_too_far`, `different_location`,
`unknown_place`,
`coordinate_out_of_range`, plus `derived_coordinates`, `accepted_exception`,
`no_coordinates`, `ok`. `reason` is the finding written for a human and names the
discrepancy:

> the sample states 'Botswana', but the coordinate is inside 'Zimbabwe'; the nearest
> land called 'Botswana' is 14 km away

**Compare and group on `code`; show `reason`.** The sentence differs for nearly every
sample and may be reworded.

## What earns `error`

- The coordinate cannot exist (checked before geocoding: `pyproj` returns NaN off
  the globe, which otherwise yields a plausible country with a NaN distance). The
  message asks whether latitude and longitude are swapped.
- The point is not on land or sea at all (neither within the fallback threshold).
- Direct hit on a different country, and the claimed country is more than
  `CLAIM_BORDER_M` (2 km) away (`different_location`).
- A land claim that names no country or territory Natural Earth maps
  (`unknown_place`), whether the point is a direct hit or a shoreline fallback.
- Shoreline fallback resolved it, but the claimed country is more than `CLAIM_NEAR_M`
  (100 km) away.
- The claimed sea and the geocoded sea are unrelated in the IHO hierarchy.

### Claim distance

`ClaimChecker` (`claims.py`) measures the geodesic distance from the point to the
nearest Natural Earth unit *belonging to* the claimed country (matched through
`country_aliases`). A country is not one polygon: Natural Earth splits out Curaçao,
Aruba and the Indian Ocean Territories, so measuring only to the unit carrying the
name asks how far the mainland is. It returns `None` for a name Natural Earth has
no unit for (a misspelling, an island, any sea name). For a land claim that is
`unknown_place`, on a direct hit and on a shoreline fallback alike: the name is the
problem, and the verdict must not depend on which side of the coastline the point
falls.

Territories that Natural Earth folds into a larger unit (Svalbard into Norway,
French Guiana into France, Christmas and Cocos Islands into one Indian Ocean
Territories unit) are listed in `containment.py`. The names are compatible, but
the claim is measured against the territory's own admin-1 polygon, so a Svalbard
sample near Oslo is 1,650 km from its claim, not 0 km.

- A shoreline fallback is not itself a disagreement: it means *nearest*, not *in*, so
  the geocoder's name is a guess and only the claim is worth testing. Near the claim
  passes; far from it fails.
- A direct hit is not a disagreement either when the two names are within
  `CLAIM_BORDER_M`: a border is a line with its own error (a river border, an Alpine
  valley). 2 km sits in an empty gap of the error distribution on real data: no
  direct-hit error lay between 2 and 5 km, and the next was at 14 km. Every case it
  forgives is an international land border.
- `CLAIM_NEAR_M` (100 km) forgives an imprecise *coordinate* that had to be snapped to
  a shore; `CLAIM_BORDER_M` forgives a *boundary*. 100 km clears every strait between
  genuine neighbours (Denmark/Sweden) and still sits far below the distances of real
  errors.
- **The two branches measure differently, on purpose.** The default counts every unit
  whose `SOVEREIGNT` is the claimed name (so a Curaçao point is 0 m from "the
  Netherlands"); the direct-hit branch uses `include_dependencies=False` so that
  dependencies do not forgive a wrong country at any threshold. There is deliberately no
  table excusing a dependency named by its sovereign.
- The check is not a country-adjacency test, and runs at every fallback distance: how
  far a point sits from land measures the coordinate's imprecision, not the claim's
  wrongness.

## Sea against sea: the IHO relatedness test

`sea_relatedness.py` decides marine disagreements as a hierarchy test, not a name
test. Every IHO sea resolves to its MarineRegions parent chain; two names are
related when one chain is a **tail** of the other, i.e. one is the other's ancestor
or the same place. `Chukchi Sea` claimed against a geocoded `Arctic Ocean` is the
author being more specific than the polygon; `Arabian Sea` against `Mozambique
Channel` is two unrelated siblings. The test never fetches; it reads `NAME`/`MRGID`
from the IHO layer and `MRGID` to chain from the bundled sea hierarchy. An
unresolvable name returns `None` and the caller falls through to generic paths. Three
things land there, fixed in the *vocabulary* rather than here: a sea with no IHO
polygon (Weddell Sea, Ross Sea; mapped to a containing sea by a containment file), a
spelling no gazetteer uses (aliases), and a missing cache entry. Parenthetical
qualifiers are stripped for lookup only (`Gulf of California (also known as the Sea of
Cortés)`).

### Vocabulary inputs

A default marine vocabulary is **bundled** in the package
(`metalog_geocoding/vocab/`, exposed as `metalog_geocoding.vocabulary.BUNDLED_MARINE_REGIONS`,
`BUNDLED_SEA_ALIASES`, `BUNDLED_SEA_CONTAINMENT`). `marine_regions.txt` has 175
names covering every IHO sea area in its common English spelling (`Barents Sea`
rather than IHO's `Barentsz Sea`). It is used by default; override any file with
your own, in the same format.

| File | Bundled default | Purpose |
| --- | --- | --- |
| marine regions vocabulary | `vocab/marine_regions.txt` | one sea name per line (blank lines and `#` comments skipped) |
| sea aliases | `vocab/sea_aliases.tsv` | other spellings of those names. Not cosmetic: a sea name not recognised is classified as *land* and geocoded against Natural Earth |
| sea containment | `vocab/sea_containment.tsv` | a sea IHO has no polygon for and the IHO sea that contains it |
| location exceptions | none (opt-in) | see below |

The service reads them from environment variables
([service/README.md](../service/README.md)): unset means the bundled default, a
path replaces it, and the empty string switches that input off. In the library,
pass your own paths to `load_marine_vocabulary()` / `load_containment()`. An
override must still express every IHO name (by an entry or an alias), or
start-up fails with `VocabularyCoverageError`. An alias is applied for
classifying and comparing only; reports show the name the sample actually states.

## The exceptions file

A `location_exceptions.tsv` (tab-separated, columns `latitude`, `longitude`,
`geographic_location`, `reason`; `#` comment lines allowed), read by
`location_exceptions.py`, is the last resort. It is **opt-in**: no file is
bundled and none is loaded unless you pass one (`--location-exceptions`, or
`$METALOG_GEOCODING_LOCATION_EXCEPTIONS` for the service). Each entry is a
`(latitude, longitude, geographic_location)` triple checked by hand against the
source and found correct, where **Natural Earth has no polygon for the
ground the sample is standing on** (a mangrove cay, a small inhabited island, a
reclaimed delta coast the 10 m coastline predates, or a water body IHO does not carry,
such as the Caspian). No distance threshold separates those from real errors (a
correct cay can sit 15 km from its country while real errors sit 12-23 km out to sea),
hence a list rather than a rule.

- Layered over the grade, not folded into it: `grade()` consults it only over `error`,
  so it cannot excuse an `uncheckable` or a missing coordinate, and an entry whose
  rule has stopped firing does nothing.
- Keyed on the coordinate (exact float, no tolerance) and the claim, not on a study.
- `reason` is mandatory and is the audit trail. The verdict comes back `ok` with the
  `ACCEPTED_PREFIX` on its reason and code `accepted_exception`.
- `unused()` reports stale entries.
- It publishes nothing: such coordinates resolve to no land, so there are no hierarchy rows.

Estuaries are deliberately not in it: coastal water geocodes cleanly as sea, so the fix is
to state the sea in the claim.
