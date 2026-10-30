"""Readings of free-text place names; standard library only."""

from metalog_geocoding.place_names import clean, interpretations


def test_the_literal_name_comes_first():
    """``Western Australia`` is a state, not the west of Australia."""
    assert interpretations("Western Australia") == ["Western Australia", "Australia"]


def test_a_qualifier_is_stripped():
    assert interpretations("Southern Spain") == ["Southern Spain", "Spain"]
    assert interpretations("NE Germany") == ["NE Germany", "Germany"]
    assert interpretations("Southwestern Gulf of Mexico")[-1] == "Gulf of Mexico"
    assert interpretations("North-East Atlantic")[-1] == "Atlantic"


def test_a_country_adjective_names_its_country():
    assert interpretations("Chilean") == ["Chile", "Chilean"]
    assert interpretations("Korean") == ["South Korea", "Korean"]
    assert interpretations("Spanish")[0] == "Spain"
    assert interpretations("Southern Chilean")[-1] == "Chile"


def test_an_ambiguous_adjective_is_left_to_the_gazetteer():
    assert interpretations("American") == ["American"]


def test_a_parenthetical_is_dropped_after_the_literal():
    assert interpretations("United States of America (USA)") == [
        "United States of America (USA)",
        "United States of America",
    ]


def test_x_comma_y_tries_the_pair_then_x():
    assert interpretations("Ningbo, China") == ["Ningbo, China", "Ningbo"]


def test_apostrophes_and_whitespace_are_normalised():
    assert clean("  Loki´s   Castle ") == "Loki's Castle"
    assert interpretations("Loki’s Castle") == ["Loki's Castle"]


def test_a_word_that_merely_starts_like_a_qualifier_is_kept():
    assert interpretations("Northumberland") == ["Northumberland"]
    assert interpretations("Sweden") == ["Sweden"]


def test_an_empty_name_has_no_readings():
    assert interpretations("") == []
    assert interpretations("  ") == []
