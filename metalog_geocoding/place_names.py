"""
Readings of a free-text place name, most literal first.

Sample metadata names places loosely: ``Southern Spain``, ``Chilean``,
``Loki´s Castle``, ``United States of America (USA)``. A gazetteer finds none
of those as written. ``interpretations()`` lists the names worth looking up, in
the order to try them; the caller stops at the first that resolves and reports
which one it used, so a reading is never silently substituted.

The literal name always comes first: ``Western Australia`` and ``South
Africa`` are places in their own right, and stripping their qualifier would
turn them into different ones.

Standard library only (see ``metalog_geocoding/__init__.py``).
"""

import re
import unicodedata

#: Apostrophe look-alikes that sample metadata uses for ``'`` (``Loki´s``).
APOSTROPHES = str.maketrans({"´": "'", "’": "'", "‘": "'", "`": "'", "ʼ": "'"})

#: A trailing parenthetical, which restates or annotates the name
#: (``United States of America (USA)``).
RE_PARENTHETICAL = re.compile(r"\s*\([^()]*\)\s*$")

#: A leading compass or position qualifier: ``Southern Spain``, ``NE Germany``,
#: ``Southwestern Gulf of Mexico``. Compound forms first: alternation takes the
#: first that fits, and ``North`` alone would leave ``East Atlantic`` of
#: ``North-East Atlantic``.
RE_QUALIFIER = re.compile(
    r"^(?:"
    r"north[- ]?east(?:ern)?|north[- ]?west(?:ern)?|"
    r"south[- ]?east(?:ern)?|south[- ]?west(?:ern)?|"
    r"central[- ]east(?:ern)?|central[- ]west(?:ern)?|"
    r"northern|southern|eastern|western|"
    r"north|south|east|west|central|upper|lower|outer|inner|mid|"
    r"maritime|coastal|continental|high|greater|sub|"
    r"ne|nw|se|sw"
    r")[- ]+(?=\S)",
    re.IGNORECASE,
)

#: Adjectives that name a country. Only unambiguous ones: ``American`` could
#: be the continent or the country, so it is left to the gazetteer.
COUNTRY_ADJECTIVES = {
    "argentine": "Argentina",
    "argentinian": "Argentina",
    "australian": "Australia",
    "austrian": "Austria",
    "brazilian": "Brazil",
    "canadian": "Canada",
    "chilean": "Chile",
    "chinese": "China",
    "colombian": "Colombia",
    "danish": "Denmark",
    "dutch": "Netherlands",
    "finnish": "Finland",
    "french": "France",
    "german": "Germany",
    "greek": "Greece",
    "indian": "India",
    "indonesian": "Indonesia",
    "italian": "Italy",
    "japanese": "Japan",
    "korean": "South Korea",
    "malaysian": "Malaysia",
    "mexican": "Mexico",
    "norwegian": "Norway",
    "peruvian": "Peru",
    "polish": "Poland",
    "portuguese": "Portugal",
    "russian": "Russia",
    "spanish": "Spain",
    "swedish": "Sweden",
    "swiss": "Switzerland",
    "taiwanese": "Taiwan",
    "thai": "Thailand",
    "turkish": "Turkey",
    "vietnamese": "Vietnam",
    "ethiopian": "Ethiopia",
    "icelandic": "Iceland",
    "malagasy": "Madagascar",
    "mongolian": "Mongolia",
}

#: Adjectives that name a continent or region. ``Arctic`` and ``Antarctic``
#: are not here: they are names in their own right.
REGION_ADJECTIVES = {
    "african": "Africa",
    "alaskan": "Alaska",
    "amazonian": "Amazon",
    "andean": "Andes",
    "asian": "Asia",
    "californian": "California",
    "caribbean": "Caribbean Sea",
    "european": "Europe",
    "himalayan": "Himalayas",
    "mediterranean": "Mediterranean Sea",
    "midwestern": "Midwestern United States",
    "north american": "North America",
    "patagonian": "Patagonia",
    "saharan": "Sahara",
    "scandinavian": "Scandinavia",
    "siberian": "Siberia",
    "south american": "South America",
    "tibetan": "Tibet",
}

ADJECTIVES = {**COUNTRY_ADJECTIVES, **REGION_ADJECTIVES}

#: An upwelling is named after its current (``Humboldt Upwelling System`` is
#: the Humboldt Current); bare ``Humboldt`` would be a county in California.
RE_UPWELLING = re.compile(r"(?:[- ]+coastal)?[- ]+upwelling$", re.IGNORECASE)

#: Words that are no place on their own once an adjective is stripped:
#: ``Canadian Archipelago`` is not any archipelago.
FEATURE_WORDS = frozenset(
    "archipelago coast highlands lowlands mountains plain plains plateau islands lakes park zoo".split()
)

#: Abbreviations written for a feature word (``Šumava Mts.``), as whole words.
ABBREVIATIONS = {"mts": "Mountains", "mt": "Mount"}

#: Trailing words that describe a place rather than name it: ``Amundsen Sea
#: Polynya`` is in the Amundsen Sea. Dropped one at a time from the end, so
#: ``Yellow Sea Large Marine Ecosystem`` is tried as ``Yellow Sea``.
#: Longer phrases first, since alternation takes the first that fits.
RE_DESCRIPTOR = re.compile(
    r"[- ]+(?:"
    r"large marine ecosystem|ecosystems?|coastal waters|waters|continental shelf|"
    r"hydrothermal vent fields?|vent fields?|"
    r"polynya|area|system|region|coastal"
    r")$",
    re.IGNORECASE,
)

#: Separators between several places in one name: ``Southern Ocean and
#: Atlantic Ocean``, ``Snake/Columbia``. A hyphen is one only between two
#: capitalised words (``hyphen_splits``).
RE_SEPARATOR = re.compile(r"\s+and\s+|\s*/\s*")

#: A hyphen between two capitalised words of three letters or more:
#: ``Saigon-Dong Nai``, but not ``Banyuls-sur-Mer``, ``Time-series`` or ``I-8``.
#: Words that begin a hyphenated name rather than end a place (``Saint-Malo``).
MAX_PART_WORDS = 4

NAME_PREFIXES = frozenset("saint sainte san santa santo sao st ste port fort mount cape lake".split())

RE_HYPHEN = re.compile(r"(?<![\w'])([A-Z][^\W\d_]{2,})-(?=[A-Z][^\W\d_]{2,}\b)")


def clean(name: str) -> str:
    """The name with Unicode, apostrophes and whitespace normalised.

    Case and accents are kept: they are part of the name, and gazetteers
    match on them loosely already.
    """
    text = unicodedata.normalize("NFC", str(name or "")).translate(APOSTROPHES)
    return re.sub(r"\s+", " ", text).strip(" \t,;.")


def interpretations(name: str) -> list[str]:
    """Names to look up for ``name``, most literal first, without duplicates.

    1. the name as written (cleaned);
    2. without a trailing parenthetical;
    3. a country or region adjective as its place (``Chilean`` -> ``Chile``,
       ``North American`` -> ``North America``), placed before the literal
       because an adjective alone is never the place;
    4. with abbreviations spelt out (``Šumava Mts.`` -> ``Šumava Mountains``);
    5. without a leading qualifier (``Southern Spain`` -> ``Spain``), and that
       again for its adjective (``Southern Chilean``... -> ``Chile``);
    6. without a leading adjective, first as within its place (``Argentine
       Patagonia`` -> ``Patagonia, Argentina`` -> ``Patagonia``); the bare
       remainder is skipped when it is a single feature word (``Canadian
       Archipelago`` is not every archipelago);
    7. without trailing descriptors, one at a time (``Amundsen Sea Polynya``
       -> ``Amundsen Sea``), reading an upwelling as its current
       (``Humboldt Upwelling System`` -> ``Humboldt Current``);
    8. for ``X, Y``, X alone (``Ningbo, China`` -> ``Ningbo``), after the pair
       itself, which a gazetteer reads as X within Y.

    A name that joins several places (``Southern Ocean and Atlantic Ocean``)
    is split by ``parts()``, which the caller tries when no reading resolves.
    """
    out: list[str] = []

    def add(candidate: str) -> None:
        candidate = clean(candidate)
        if candidate and candidate.casefold() not in {c.casefold() for c in out}:
            out.append(candidate)

    literal = clean(name)
    bare = RE_PARENTHETICAL.sub("", literal)
    # A bare adjective is never itself the place meant: ``Spanish`` is Spain,
    # not Spanish, Ontario. Its country goes first.
    place = ADJECTIVES.get(bare.casefold())
    if place:
        add(place)
    add(literal)
    add(bare)
    expanded = re.sub(
        r"\b(\w+)\b\.?",
        lambda m: ABBREVIATIONS.get(m.group(1).casefold(), m.group(0)),
        bare,
    )
    add(expanded)

    # Only the name as written: a reading derived here (``South Korea``) must
    # not lose its own qualifier.
    written = list(dict.fromkeys([literal, bare, expanded]))

    for reading in written:
        stripped = RE_QUALIFIER.sub("", reading)
        if stripped != reading:
            add(stripped)
            place = ADJECTIVES.get(stripped.casefold())
            if place:
                add(place)

    for reading in written:
        first, _, rest = reading.partition(" ")
        place = ADJECTIVES.get(first.casefold())
        if not place or not rest:
            continue
        # ``Andean Colombian``: the last adjective is the place qualified.
        rest_place = ADJECTIVES.get(rest.casefold())
        if rest_place:
            add(rest_place)
            continue
        add(f"{rest}, {place}")
        if not set(rest.casefold().split()) <= FEATURE_WORDS:
            add(rest)

    for reading in written:
        shorter = RE_DESCRIPTOR.sub("", reading)
        while shorter != reading and shorter:
            reading = shorter
            current = RE_UPWELLING.sub(" Current", reading)
            add(current)
            if current != reading:
                break
            shorter = RE_DESCRIPTOR.sub("", reading)

    if "," in bare:
        add(bare.split(",", 1)[0])

    return out


def parts(name: str) -> list[str]:
    """The places in a name that joins several, or [] if it names one.

    ``Southern Ocean and Atlantic Ocean`` -> ``Southern Ocean``, ``Atlantic
    Ocean``; ``Ganges-Brahmaputra-Meghna Delta`` -> ``Ganges``,
    ``Brahmaputra``, ``Meghna Delta``. Each part is a name in its own right,
    with its own readings.
    """
    bare = RE_PARENTHETICAL.sub("", clean(name))
    pieces = []
    for piece in RE_SEPARATOR.split(bare):
        pieces += RE_HYPHEN.sub(r"\1\0", piece).split("\0")
    pieces = [clean(p) for p in pieces]
    # A part of five words or more describes a site rather than names a place
    # (``Canadian High Arctic Research Station Experimental and Reference Area``).
    if any(p.casefold() in NAME_PREFIXES or len(p.split()) > MAX_PART_WORDS for p in pieces):
        return []
    pieces = list(dict.fromkeys(p for p in pieces if p))
    return pieces if len(pieces) > 1 else []
