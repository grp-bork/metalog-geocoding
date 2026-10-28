"""Country-name equivalences for comparing metadata against geocoded results.

Sample metadata and boundary datasets name the same country differently
("United States" vs Natural Earth's "United States of America",
"Congo-Brazzaville" vs "Republic of the Congo"). Without this table, a
comparison reports mismatches that are only spelling.

Not applied to the geocoder's output (``land.py`` publishes one name per
country); only used to decide whether two names refer to the same country.

Add a group when a real mismatch turns out to be a naming difference. Groups are
symmetric - every member matches every other member.
"""

import re
import unicodedata

#: Names that refer to the same country. The first entry is the preferred form.
ALIAS_GROUPS: list[tuple[str, ...]] = [
    ("United States", "United States of America", "USA", "U.S.A.", "US"),
    ("United Kingdom", "Great Britain", "UK", "Britain"),
    ("Russia", "Russian Federation"),
    # Bare "Korea" is ambiguous, but in sample metadata it means South Korea
    # (3,601 samples in microbeatlas, none in the North).
    ("South Korea", "Republic of Korea", "Korea, Republic of", "Korea South", "Korea"),
    (
        "North Korea",
        "Democratic People's Republic of Korea",
        "Dem. Rep. Korea",
        "Korea, Democratic People's Republic of",
    ),
    ("Czechia", "Czech Republic"),
    ("Laos", "Lao PDR", "Lao People's Democratic Republic"),
    (
        "Republic of the Congo",
        "Republic of Congo",
        "Congo-Brazzaville",
        "Congo, Republic of the",
        "Congo",
    ),
    (
        "Democratic Republic of the Congo",
        "Congo-Kinshasa",
        "DR Congo",
        "Congo, Democratic Republic of the",
        "Zaire",
    ),
    ("Palestine", "Palestinian Territories", "State of Palestine", "Palestinian Territory"),
    ("Tanzania", "United Republic of Tanzania"),
    ("Vietnam", "Viet Nam"),
    ("Myanmar", "Burma"),
    ("Cabo Verde", "Cape Verde"),
    ("Côte d'Ivoire", "Ivory Coast", "Cote d'Ivoire"),
    ("Eswatini", "Swaziland"),
    ("North Macedonia", "Macedonia", "FYROM", "Former Yugoslav Republic of Macedonia"),
    ("Netherlands", "The Netherlands", "Holland"),
    ("Timor-Leste", "East Timor"),
    ("Bolivia", "Plurinational State of Bolivia"),
    ("Venezuela", "Bolivarian Republic of Venezuela"),
    ("Iran", "Islamic Republic of Iran", "Iran (Islamic Republic of)"),
    ("Syria", "Syrian Arab Republic"),
    ("Moldova", "Republic of Moldova"),
    ("Brunei", "Brunei Darussalam"),
    ("Micronesia", "Federated States of Micronesia"),
    ("Turkey", "Türkiye", "Turkiye"),
    ("Falkland Islands", "Malvinas", "Falkland Islands (Malvinas)"),
    ("Faroe Islands", "Faeroe Islands"),
    ("Hong Kong", "Hong Kong SAR", "Hong Kong S.A.R.", "Hong Kong SAR China"),
    ("Macao", "Macau", "Macao SAR", "Macao S.A.R."),
    ("Central African Republic", "Central African Rep."),
    ("Western Sahara", "W. Sahara"),
    ("South Sudan", "S. Sudan"),
    ("Bosnia and Herzegovina", "Bosnia and Herz."),
    ("Dominican Republic", "Dominican Rep."),
    ("Equatorial Guinea", "Eq. Guinea"),
]


def normalise(name: str) -> str:
    """Casefold, strip accents and punctuation, and drop a leading "the"."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(name))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^\w\s]", "", stripped).casefold()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return re.sub(r"^the\s+", "", cleaned)


_LOOKUP: dict[str, str] = {}
for _group in ALIAS_GROUPS:
    _key = normalise(_group[0])
    for _member in _group:
        _LOOKUP[normalise(_member)] = _key


def canonical(name: str) -> str:
    """Return a comparison key: the group's preferred form, or the name itself."""
    key = normalise(name)
    return _LOOKUP.get(key, key)


def same_country(a: str, b: str) -> bool:
    """True when two country names refer to the same country."""
    if not a or not b:
        return False
    return canonical(a) == canonical(b)
