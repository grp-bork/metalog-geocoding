# Changelog

Changes that alter a verdict are listed under **Grading**: results produced by different
versions are not comparable there.

## Unreleased

### Other

- New endpoint `POST /place-distance`: the distance from each of several coordinates
  to every place a free-text name may refer to, for checking candidate repairs of a
  broken coordinate. Names resolve against the boundary layers first, then
  Wikidata (which place) with OpenStreetMap outlines, then OpenStreetMap's
  Nominatim, all cached and rate-limited server-side.

## 0.2.0 (2026-10-28)

### Grading

- Territories that Natural Earth folds into a larger unit (Svalbard, French Guiana,
  Guadeloupe, Martinique, Mayotte, Réunion, Tokelau, Christmas Island, Cocos Islands)
  are checked against their own polygon, not the whole unit. A Svalbard sample near
  Oslo is now `different_location`, and Christmas and Cocos Islands are told apart.
- New error code `unknown_place`: a land name that is not a country or territory
  Natural Earth maps. It used to be `different_location` on a direct hit and `ok` on a
  shoreline fallback; it is now an error either way.
- New aliases: `Faroe Islands` (Natural Earth: `Faeroe Islands`), and `Korea` for
  South Korea.

### Other

- `not_on_land` reasons name the distance to the claimed place, as `different_location`
  reasons already did.
- `geocode` with type `best` skips the sea lookup after a direct land hit, and the
  nearest-sea lookup is about 10× faster. Results are unchanged.
- `ERROR_CODES` now has seven entries.

## 0.1.0 (2026-10-28)

First public release. Archived on Zenodo:
[10.5281/zenodo.23734817](https://doi.org/10.5281/zenodo.23734817).
