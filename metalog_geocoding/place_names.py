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
}


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
    3. a country adjective as its country (``Chilean`` -> ``Chile``), placed
       before the literal because an adjective alone is never the place;
    4. without a leading qualifier (``Southern Spain`` -> ``Spain``), and that
       again for its adjective (``Southern Chilean``... -> ``Chile``);
    5. for ``X, Y``, X alone (``Ningbo, China`` -> ``Ningbo``), after the pair
       itself, which a gazetteer reads as X within Y.
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
    country = COUNTRY_ADJECTIVES.get(bare.casefold())
    if country:
        add(country)
    add(literal)
    add(bare)

    # Only the name as written: a reading derived here (``South Korea``) must
    # not lose its own qualifier.
    written = list(dict.fromkeys([literal, bare]))

    for reading in written:
        stripped = RE_QUALIFIER.sub("", reading)
        if stripped != reading:
            add(stripped)
            country = COUNTRY_ADJECTIVES.get(stripped.casefold())
            if country:
                add(country)

    if "," in bare:
        add(bare.split(",", 1)[0])

    return out
