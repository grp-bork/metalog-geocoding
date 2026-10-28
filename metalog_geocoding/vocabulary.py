"""
Which place names are seas, and how each of them is spelled.

Kept in one place so every caller classifies and spells a location the same
way. Standard library only, so reading a name back does not need the geo
stack.
"""

import csv
import re
from pathlib import Path

#: Fallback classifier when no marine-region list is supplied.
RE_MARINE = re.compile(r"^Gulf of |Ocean$|Sea$")

#: A trailing "(...)" on a sea name. A parenthetical is documentation, not part
#: of the name, and no vocabulary entry ends in one, so stripping cannot turn
#: one published name into another.
RE_TRAILING_PARENTHETICAL = re.compile(r"\s*\([^()]*\)$")


class VocabularyCoverageError(Exception):
    """An IHO sea name the vocabulary can neither publish nor alias.

    Raised rather than exiting, so a script can exit non-zero and a service
    can refuse to start.
    """


class MarineVocabulary:
    """Which place names are seas, and how the vocabulary spells each of them.

    ``marine_regions.txt`` is the vocabulary, one name per line; ``#`` lines
    are comments.

    ``sea_aliases.tsv`` maps other spellings to those names (IHO's ``Barentsz
    Sea``, the gazetteer's ``Barentszee``, a colloquial ``Sea of Cortez``).
    Not cosmetic: a name this class does not recognise is classified as
    **land** and geocoded against Natural Earth.

    ``canonical()`` is for classifying and comparing only; reports should
    show the name as the sample states it.
    """

    def __init__(self, names: set[str], aliases: dict[str, str] | None = None):
        self.names = names
        #: every spelling, case-folded, to the one name the vocabulary publishes
        self._lookup = {name.casefold(): name for name in names}
        for variant, published in (aliases or {}).items():
            # An alias must point at a vocabulary name; others are ignored.
            if published in names:
                self._lookup.setdefault(variant.casefold(), published)

    def __len__(self) -> int:
        return len(self.names)

    def canonical(self, claimed: str) -> str | None:
        """The vocabulary spelling of ``claimed``, or None if it is not a sea.

        Tries the whole name first, so a published entry always beats a strip,
        then the name with a trailing parenthetical removed.
        """
        if not claimed:
            return None
        published = self._lookup.get(claimed.casefold())
        if published is None:
            bare = RE_TRAILING_PARENTHETICAL.sub("", claimed).strip()
            if bare != claimed:
                published = self._lookup.get(bare.casefold())
        return published


#: The default vocabulary shipped inside the package: every IHO name in a
#: common English spelling, other spellings of those names, and seas IHO has
#: no polygon for. Callers pass these paths explicitly; nothing loads them
#: implicitly, so a run without a vocabulary stays possible.
BUNDLED_VOCABULARY_DIR = Path(__file__).parent / "vocab"
BUNDLED_MARINE_REGIONS = BUNDLED_VOCABULARY_DIR / "marine_regions.txt"
BUNDLED_SEA_ALIASES = BUNDLED_VOCABULARY_DIR / "sea_aliases.tsv"
BUNDLED_SEA_CONTAINMENT = BUNDLED_VOCABULARY_DIR / "sea_containment.tsv"


def load_marine_vocabulary(
    names_path: Path | None, aliases_path: Path | None = None
) -> MarineVocabulary | None:
    """Read marine_regions.txt, and sea_aliases.tsv if it was given."""
    if names_path is None:
        return None

    with open(names_path, "r", encoding="utf-8") as handle:
        names = set()
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                names.add(line)

    aliases = {}
    if aliases_path is not None:
        with open(aliases_path, "r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                variant, published = row["variant"].strip(), row["published"].strip()
                if variant and published:
                    aliases[variant] = published

    return MarineVocabulary(names, aliases)


def published_sea(result: dict, marine: "MarineVocabulary | None") -> str:
    """The sea name to publish for a geocoded result.

    IHO and the vocabulary spell ten of the 101 seas differently (IHO's
    ``Barentsz Sea`` / ``Barents Sea``, ``Japan Sea`` / ``Sea of Japan``), and
    output is keyed on the vocabulary's names, so publish those.

    Only the published name changes: ``mrgid`` stays the identity and the
    hierarchy chains keep the gazetteer's spelling.
    """
    sea = result.get("sea") or ""
    if not sea or marine is None:
        return sea
    return marine.canonical(sea) or sea


def check_vocabulary_covers_iho(sea_layer, marine: "MarineVocabulary | None") -> None:
    """Raise if the geocoder can return a sea name the vocabulary cannot express.

    Any of the 101 IHO polygons can be a correct answer, so every IHO name
    needs a vocabulary entry or an alias. Raises rather than warns because
    the output is keyed on vocabulary names (e.g. a database foreign key);
    the fix is one line in ``sea_aliases.tsv``.

    Takes the layer as an argument (reading only ``NAME``), so this module
    stays free of the geo stack.
    """
    if marine is None:
        return
    missing = sorted({name for name in sea_layer["NAME"] if not marine.canonical(name)})
    if missing:
        raise VocabularyCoverageError(
            "IHO sea names that the marine vocabulary cannot express, so the "
            "geocoder could publish a value outside the vocabulary:\n  "
            + "\n  ".join(missing)
            + "\nAdd each one to sea_aliases.tsv, mapping it to the name "
            "marine_regions.txt publishes."
        )


def expected_type(geographic_location: str, marine: "MarineVocabulary | None") -> str:
    """Does this place name describe a marine region or a land location?"""
    if not geographic_location:
        return "land"
    if marine is not None:
        return "sea" if marine.canonical(geographic_location) else "land"
    return "sea" if RE_MARINE.search(geographic_location) else "land"
