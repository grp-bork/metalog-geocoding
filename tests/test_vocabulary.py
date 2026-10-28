"""The marine vocabulary: classification, canonical spelling, and the coverage
check (``check_vocabulary_covers_iho``).

A sea name the vocabulary does not recognise is classified as *land*, and the
coordinate is geocoded against Natural Earth.
"""

import pytest

from metalog_geocoding.vocabulary import (
    MarineVocabulary,
    VocabularyCoverageError,
    check_vocabulary_covers_iho,
    expected_type,
    load_marine_vocabulary,
    published_sea,
)

NAMES = {"Baltic Sea", "Gulf of California", "Barents Sea", "Sea of Japan", "Indian Ocean"}
ALIASES = {
    "Barentsz Sea": "Barents Sea",
    "Japan Sea": "Sea of Japan",
    "Sea of Cortez": "Gulf of California",
}


@pytest.fixture
def marine():
    return MarineVocabulary(NAMES, ALIASES)


def test_a_name_in_the_vocabulary_is_its_own_canonical_form(marine):
    assert marine.canonical("Baltic Sea") == "Baltic Sea"


def test_matching_ignores_case(marine):
    assert marine.canonical("baltic sea") == "Baltic Sea"


def test_an_alias_resolves_to_the_published_spelling(marine):
    """IHO's ``Barentsz Sea`` is published as the vocabulary's ``Barents Sea``,
    the spelling a downstream foreign key expects."""
    assert marine.canonical("Barentsz Sea") == "Barents Sea"
    assert marine.canonical("Japan Sea") == "Sea of Japan"


def test_a_trailing_parenthetical_is_stripped_for_lookup(marine):
    """Otherwise such a name is not recognised and takes the *land* path."""
    assert marine.canonical("Gulf of California (also known as the Sea of Cortes)") == (
        "Gulf of California"
    )


def test_a_published_entry_beats_a_strip(marine):
    """Whole-name lookup runs first, so a vocabulary entry that legitimately
    contains a parenthetical is never mangled into something else."""
    vocab = MarineVocabulary({"Gulf of Cadiz (Golfo de Cadiz)", "Gulf of Cadiz"})
    assert vocab.canonical("Gulf of Cadiz (Golfo de Cadiz)") == "Gulf of Cadiz (Golfo de Cadiz)"


def test_an_unknown_name_is_not_a_sea(marine):
    assert marine.canonical("Germany") is None
    assert marine.canonical("") is None


def test_an_alias_pointing_outside_the_vocabulary_is_ignored():
    """An alias is only another spelling of a name the vocabulary publishes."""
    vocab = MarineVocabulary({"Baltic Sea"}, {"Some Sea": "Not In The Vocabulary"})
    assert vocab.canonical("Some Sea") is None


def test_expected_type_uses_the_vocabulary_when_there_is_one(marine):
    assert expected_type("Baltic Sea", marine) == "sea"
    assert expected_type("Germany", marine) == "land"
    assert expected_type("", marine) == "land"


def test_expected_type_falls_back_to_the_regex_without_a_vocabulary():
    """The service runs without a vocabulary when none is configured."""
    assert expected_type("Baltic Sea", None) == "sea"
    assert expected_type("Gulf of Mexico", None) == "sea"
    assert expected_type("Indian Ocean", None) == "sea"
    assert expected_type("Germany", None) == "land"


def test_the_regex_fallback_misses_what_the_vocabulary_catches(marine):
    """Why aliases are not cosmetic: without them, a variant spelling is
    classified as land."""
    assert expected_type("Barentsz Sea", None) == "sea"       # ends in "Sea"
    assert expected_type("Sea of Cortez", None) == "land"     # does not
    assert expected_type("Sea of Cortez", marine) == "sea"


def test_published_sea_rewrites_the_output_spelling(marine):
    assert published_sea({"sea": "Barentsz Sea"}, marine) == "Barents Sea"


def test_published_sea_leaves_an_unknown_name_alone(marine):
    """Better to publish an unexpected name than to blank a real result; the
    coverage check below normally stops one reaching here."""
    assert published_sea({"sea": "Some New Sea"}, marine) == "Some New Sea"


def test_published_sea_without_a_vocabulary_is_the_identity():
    assert published_sea({"sea": "Barentsz Sea"}, None) == "Barentsz Sea"
    assert published_sea({}, None) == ""


def test_coverage_check_accepts_a_layer_the_vocabulary_covers(marine):
    layer = {"NAME": ["Baltic Sea", "Barentsz Sea", "Japan Sea"]}
    check_vocabulary_covers_iho(layer, marine)  # must not raise


def test_coverage_check_refuses_a_layer_it_cannot_express(marine):
    """Refuses rather than warns: the output is loaded into a database with a
    foreign key on these names, so a run that continues produces rows that
    cannot be loaded."""
    layer = {"NAME": ["Baltic Sea", "Some Unmapped Sea"]}
    with pytest.raises(VocabularyCoverageError) as excinfo:
        check_vocabulary_covers_iho(layer, marine)
    assert "Some Unmapped Sea" in str(excinfo.value)
    assert "sea_aliases.tsv" in str(excinfo.value), "the error must say how to fix it"


def test_coverage_check_is_a_no_op_without_a_vocabulary(marine):
    """Nothing to check against; a deployment without a vocabulary must still
    start."""
    check_vocabulary_covers_iho({"NAME": ["Anything At All"]}, None)


def test_coverage_check_raises_rather_than_exiting_the_process():
    """A long-lived service must refuse to start, not have its interpreter
    killed from inside a constructor."""
    with pytest.raises(VocabularyCoverageError):
        check_vocabulary_covers_iho({"NAME": ["Nope"]}, MarineVocabulary({"Baltic Sea"}))
    assert issubclass(VocabularyCoverageError, Exception)
    assert not issubclass(VocabularyCoverageError, SystemExit)


def test_loading_skips_comments_and_blank_lines(tmp_path):
    """Comments document stripped parentheticals and must not be read as names."""

    names = tmp_path / "marine_regions.txt"
    names.write_text(
        "# Gulf of California: also known as the Sea of Cortes\n"
        "Gulf of California\n"
        "\n"
        "Baltic Sea\n"
    )
    aliases = tmp_path / "sea_aliases.tsv"
    aliases.write_text("variant\tpublished\nSea of Cortez\tGulf of California\n")

    vocab = load_marine_vocabulary(names, aliases)
    assert len(vocab) == 2
    assert vocab.canonical("Sea of Cortez") == "Gulf of California"
    assert vocab.canonical("# Gulf of California: also known as the Sea of Cortes") is None


def test_loading_nothing_gives_nothing():
    assert load_marine_vocabulary(None) is None
