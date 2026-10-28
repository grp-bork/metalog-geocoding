# Map layers

`scripts/build_geojson.py` writes simplified GeoJSON for every level of the
[location hierarchy](location-hierarchy.md), for drawing maps. Each feature's `id` is
the value a table of annotations would carry, with names from the geocoder's own code,
so a map joins directly on a column without a lookup table.

| file | `id` | drawn at |
| --- | --- | --- |
| `continents.geojson` / `continents_political.geojson` | continent | global scale |
| `countries.geojson` | country code | global scale |
| `regions.geojson` | admin-1 `adm1_code` | zoomed in |
| `oceans.geojson` | ocean | global scale |
| `basins.geojson` | basin name | global scale |
| `seas.geojson` | MRGID | global scale |
| `longhurst_biomes.geojson` / `longhurst_provinces.geojson` | biome / Longhurst code | global scale |

```bash
python scripts/build_geojson.py -o geojson/
```

It needs the [boundary layers](installation-and-data.md#boundary-layers) and takes
about 40 s. Sea names use the bundled vocabulary; pass `--marine-regions` and
`--sea-aliases` to use your own spelling, which must match the vocabulary the
annotations were made with, or the map will not join on them. Rebuild after
replacing Natural Earth, IHO or Longhurst, or after changing the vocabulary. Each file gets a `.gz` beside it (for servers that serve
precompressed files; copy both or a stale `.gz` keeps being served). No layer is
built from GSHHG: its licence allowance covers derived distances only.

## How the layers are made small without gaps

- **Two levels of detail.** Global layers use `--world-tolerance` (0.1 degrees, 0.1-0.9 MB
  each); `regions.geojson` uses `--tolerance` (0.02 degrees, ~8 MB). Coordinates are
  rounded to a hundredth of the tolerance.
- **Simplified as a coverage** (`shapely.coverage_simplify`) once per scheme, and the
  coarser levels dissolved from it, so neighbours never open slivers and a country's
  outline is exactly its regions'.
- **Rebuilt into a clean coverage first** (`rebuild_coverage`), since none of the
  sources is one as used: IHO neighbours trace shared borders with different vertices
  (hairline gaps and overlaps), Longhurst has invalid polygons, and two Natural Earth
  units without admin-1 (Bir Tawil, the Cyprus buffer zone) overlap their neighbours.
  Borders are noded and polygonized, and each face goes to the smallest polygon covering
  most of it by *area* (a single test point near an overlap can land in the wrong polygon).
  Only thin faces (that vanish when shrunk by half the tolerance) are gaps, and go to the
  neighbour sharing the longest border.
- **Seas and Longhurst are grown** by a twentieth of the tolerance first, so a gap that
  opens onto the coast becomes an overlap (about 500 m at 0.1 degrees, invisible at that
  scale). Natural Earth land units already share borders and are not grown.
- **Small islands filled**: holes under `(2 x tolerance)^2` (IHO has ~200,000 islands);
  larger ones (Cyprus, Jamaica) stay cut out.
- **Basins** are their own IHO area, where they have one, plus every sea below them.
- Merged faces that touch themselves are repaired with `make_valid(method="structure")`.
  Snapping to the output grid leaves sub-pixel overlaps (<= 0.1 km²) along some region
  borders; every polygon stays valid.
- Natural Earth's Valparaíso region holds both the mainland and Easter Island, so
  `WESTERN_PART_OVERRIDES` makes only the part west of 100°W Oceania and the builder
  splits it there.

`tests/test_build_geojson.py` covers the reassembly on hand-made shapes.
