# Place names to distances

`POST /place-distance` answers one question: **how far is each of these
coordinates from the place this name refers to?** It exists for coordinate
repair. A sample whose latitude and longitude were swapped, or lost a sign,
has up to eight candidate fixes; the one that lands in the named place is
probably right. The endpoint only measures. Deciding which fix to accept, and
how much evidence is enough, is the caller's job.

The request and response formats are in [api.md](api.md#post-place-distance).
The browser form at `GET /` has a **Test repairs** option that does this for
pasted coordinates (see [service/README.md](../service/README.md#the-browser-form)).
This page explains how a name becomes a geometry, why each step is there,
and how well it works on real metadata.

## From a name to candidate places

### 1. Readings of the name

Metadata names places loosely (`Southern Spain`, `Chilean`, `Loki´s Castle`,
`United States of America (USA)`), and no gazetteer finds most of those as
written. `place_names.interpretations()` lists the readings worth looking up,
in order:

1. the name as written, with Unicode, apostrophes and whitespace normalised;
2. without a trailing parenthetical;
3. a country adjective as its country (`Chilean` → `Chile`). A bare adjective
   is never itself the place, so this reading comes *first*: `Spanish` is
   Spain, not Spanish, Ontario;
4. without a leading compass or position word (`Southern Spain` → `Spain`,
   `NE Germany` → `Germany`). Only after the literal name, since `Western
   Australia` and `South Africa` are places in their own right;
5. for `X, Y`, X alone, after the pair itself.

The first reading that finds anything wins, and the response says which one
in `interpreted_as`. A substitution is never silent.

### 2. Sources, tried for each reading

| Step | Source | What it is good at |
| --- | --- | --- |
| Local | Natural Earth countries, territories and admin-1 units; IHO seas and oceans | Fast and offline. Agrees with `/check`, which uses the same layers. Two-letter codes are countries (`ES`), and in the US, Canada and Australia also states (`CA` is Canada and California). |
| Wikidata | `wbsearchentities` plus each hit's claims, English aliases and sitelinks | Deciding *which* place a name means: English names and aliases (`Lake Zug`, `Lake Tai`), and prominence (`Andes` is the range, not a town in Colombia). |
| Nominatim | OpenStreetMap search | Outlines of features Wikidata does not know, as a last resort. |

Local layers go first. Wikidata goes before Nominatim because Nominatim's
ranking does not pick the place a scientist means. In the first evaluation its
top answers for `Atlantic`, `Pacific`, `Andes`, `Patagonia` and `Caribbean`
were small towns of those names, and for `Lake Zug` a clothes shop.

### 3. Picking Wikidata items

A Wikidata hit counts if:

- it has a coordinate (P625);
- its label or one of its English aliases *is* the reading (see the name rule
  below), so `Himalaya` finds the Himalayas through their alias;
- its description does not name a non-place: a language, university, album,
  journal, vessel and similar (`Tibetan` is not the Tibetan language).

Hits are ranked by **sitelinks**, the number of Wikipedia and sister pages
about the item, not by Wikidata's search order (which puts Melbourne, Florida
ahead of Melbourne, Victoria). Hits with at least half the top item's
sitelinks are kept as further candidates, up to three, so a genuinely
ambiguous name such as `Stanley` shows its ambiguity.

### 4. A geometry for each Wikidata item

In order of preference:

1. the Natural Earth country or admin-1 unit carrying the item's QID;
2. a local layer under the item's label (`Atlantic` → the Atlantic Ocean →
   IHO's polygons, so seas agree with `/check`);
3. the Natural Earth geography region (mountain ranges, deserts, plateaus,
   ~1,000 features) or marine area (bays, gulfs, straits, smaller seas,
   306 features) carrying the QID. These two layers are optional; see
   [installation-and-data.md](installation-and-data.md);
4. the OpenStreetMap relation the item links to (P402), fetched by id;
5. a Nominatim result with the same name within 25 km of the item's
   coordinate;
6. the item's coordinate, as a point.

The joins by QID involve no name matching at all, which is why they are
preferred.

### 5. Filtering Nominatim

Nominatim matches fragments of a query, so its answers are filtered before
they become candidates:

- **Only places.** An allow-list of OSM classes: administrative and protected
  boundaries, `place`, `natural`, water and waterways, nature-related land use,
  nature reserves and parks, research institutes. An allow-list because the
  junk is open-ended: a car park called `Bay of Bengal`, a car-rental desk
  called `Chilean`, a restaurant called `Mekong River`.
- **The name rule** (also applied to Wikidata). One of the result's names, in
  any language, must have the same *distinctive* words as the reading.
  Feature words (*lake*, *bay*, *desert*, *desierto*, *bugt*, ...) may
  differ: `Atacama Desert` is `Desierto de Atacama`. But `Africa` is not
  `South Africa`, and `Northern Baffin Bay` is not a shop called Northern
  Store. A reading with a feature word needs a name with one, so `Sierra
  Nevada` is not the state of Nevada. Whole words only: a prefix rule would
  find `Taihu` for `Lake Tai`, but also `Indiana` for `Indian`.
- **Importance.** Results with less than a fifth of the best result's
  importance are dropped (Lake Erie, not also a pond of that name in Nigeria).

## Online lookups

Wikidata and Nominatim are queried live, by the service, never by the caller:

- **Rate limit.** Each source gets at most one request per 1.1 s across all
  threads of the process, as Nominatim's usage policy requires. Run online
  lookups in one instance only: independent containers would each keep their
  own pace.
- **Cache.** Every answer, including "nothing found", is kept in
  `place_cache.sqlite` in the cache directory. A name is fetched once. Empty
  answers are retried after 90 days, since both sources keep growing.
- **Failures.** A network failure, or an error body under load, is retried
  and then reported as `lookup_failed`. It is never cached, and never reads
  as "nothing found".
- **Budget.** One request may make at most
  `$METALOG_GEOCODING_PLACE_LOOKUPS_PER_REQUEST` online requests (default 20;
  a new name costs up to four). Names past it come back `pending`, so a large
  first batch cannot hold a worker for minutes. Send them again.
- **Licences.** Wikidata is CC0. OpenStreetMap is ODbL; the service keeps
  outlines in its cache and returns only distances, with an attribution string
  whenever an OSM outline contributed. Natural Earth is public domain.

## Reading the answer

- **`status`.** Only `resolved` carries candidates. `unresolved`, `pending`
  and `lookup_failed` are *no evidence* about a coordinate, never "far away".
- **Several candidates** mean the name is ambiguous (`Georgia`, `CA`,
  `Stanley`). With eight fixes to test, a hit on one of several same-named
  places can be chance. Narrow with the optional `country` hint when the
  study's country is known.
- **`geometry`** says what the distance is measured to. Inside a `polygon` is
  strong evidence. A `point` is a label position: a small distance to the
  point of a mountain range or a large bay says little, and a large one does
  not rule it out. Use a tolerance that fits the feature.
- **`area_km2`** says how much a hit is worth. Inside a 20,000 km² bay is
  strong; inside an ocean or a continent is weak, though it can still rule
  fixes out.
- **`interpreted_as`** differing from `name` means a broader place was used:
  `Southern Norway` is measured against all of Norway, `Northern Baffin Bay`
  against all of Baffin Bay.

## How well it works

### The evaluation

The rules above were developed on 771 distinct place names from an ENA
metadata scan. After each change, every name was resolved again and the
answers were reviewed: six Claude Sonnet agents, about 100 names each, judged
for every name whether the intended place is among the candidates (`correct`,
`correct_plus_junk`, `wrong`, `ambiguous`, `vague`) and, from the second
round on, whether the geometry suits the feature. Their findings drove the
changes; the rules are general and nothing in them is specific to these
names.

**The review is not part of the service.** At runtime every name, known or
new, goes through the same automatic steps. The review measured how often
those steps are right; it did not curate the answers. A new name gets the same
rules but no check of its particular answer.

| Round | Change | Resolved | Correct ¹ | Wrong | Ambiguous | Vague |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Local layers + filtered Nominatim | 560 | 452 (81%) | 38 | 44 | 26 |
| 2 | + Wikidata first, stricter name rule, two-letter codes | 625 | 528 (84%) | 37 | 42 | 18 |
| 3 | + aliases, sitelink ranking, Natural Earth regions and marine areas by QID | 626 | 536 (86%) | 27 | 47 | 16 |

¹ Including answers with a junk candidate alongside the right one (40, 5
and 11 in the three rounds).

Each round reviewed only the answers that had changed since the last one; the
others keep their earlier verdict. Geometry was judged from round 2 on: in
round 2, 75 answers were a point for a large feature (a range, a desert, a
sea), and after round 3's Natural Earth outlines 50 are. 108 of the 626
answers are points in all, which is right for vents, caves, stations and
springs.

Of the 27 wrong answers, most are names whose intended meaning is a large
feature or region that the sources rank below a same-named smaller place
(`North Atlantic` resolves to Nicaragua's Atlántico Norte, `Shengli` to a
township rather than the oil field), and a few are acronyms that happen to
match a place (`BATS` is a commune in France).

### Rerunning the evaluation

```bash
python scripts/evaluate_places.py place_names.txt review.tsv
```

resolves every name with the service's resolver and the same place cache,
and writes one row per name; its `candidates` column is what the reviewers
read. Online lookups are paced, so a first run over several hundred names
takes a while; later runs read the cache. `--offline` sends nothing and
reports uncached names as `pending`. To review only what a rule change
altered, compare the `candidates` column of two runs.

### What it cannot do

- **Acronyms and station names** that no gazetteer carries: `BATS`, `SCS`,
  `NSCS`, `WAP`, `SOLA`, `Ocean Station Papa`, drill sites such as `U1433`.
  A small curated table of oceanographic stations would cover the common ones.
- **Vague names** (`Subarctic`, `Boreal`, `Old World`, `High Arctic`,
  `Northern Hemisphere`) have no outline to measure against. They either stay
  unresolved or resolve to something broader.
- **Sub-regions** (`Southern Norway`, `Eastern Himalayas`) are measured
  against the whole parent; see `interpreted_as`.
- **Bays and gulfs** that neither Natural Earth nor OpenStreetMap outlines
  (`Kandalaksha Gulf`, `Sendai Bay`) are points. MarineRegions' gazetteer has
  these only as centre points too; its SeaVoX sea areas would have outlines.
- **145 of the 771 names** do not resolve: mostly local sites (reservoirs,
  springs, farms, research plots), acronyms, vague terms and the odd street
  address (`Broadway & 8th Street`).

### Possible next steps

- A **reviewed overrides file** for known names, built from the review
  verdicts: accepted and rejected candidates per name, so those answers come
  back marked as reviewed and new names as unreviewed.
- A **curated table of stations and acronyms**.
- **SeaVoX sea areas** (MarineRegions, CC-BY) for bays and gulfs below IHO
  level that Natural Earth lacks.
