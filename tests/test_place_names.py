"""Readings of free-text place names; standard library only."""

from metalog_geocoding.place_names import clean, interpretations, parts


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


def test_a_region_adjective_names_its_region():
    assert interpretations("North American")[0] == "North America"
    assert interpretations("Andean")[0] == "Andes"
    assert interpretations("Andean Colombian") == ["Andean Colombian", "Colombia"]


def test_a_leading_adjective_is_read_within_its_place():
    assert interpretations("Argentine Patagonia") == [
        "Argentine Patagonia", "Patagonia, Argentina", "Patagonia",
    ]


def test_a_bare_feature_word_is_not_a_reading():
    """``Canadian Archipelago`` is not every archipelago."""
    assert interpretations("Canadian Archipelago") == [
        "Canadian Archipelago", "Archipelago, Canada",
    ]
    assert interpretations("Colombian Park Zoo")[-1] == "Park Zoo, Colombia"


def test_more_leading_qualifiers_are_stripped():
    assert interpretations("Maritime Antarctica")[-1] == "Antarctica"
    assert interpretations("High Arctic")[-1] == "Arctic"
    assert interpretations("Sub-Arctic")[-1] == "Arctic"
    assert interpretations("Subarctic") == ["Subarctic"]


def test_trailing_descriptors_are_dropped_one_at_a_time():
    assert interpretations("Amundsen Sea Polynya") == ["Amundsen Sea Polynya", "Amundsen Sea"]
    assert interpretations("Hainich region") == ["Hainich region", "Hainich"]


def test_an_upwelling_is_read_as_its_current():
    """Not bare ``Humboldt``, a county in California."""
    assert interpretations("Humboldt Upwelling System") == [
        "Humboldt Upwelling System", "Humboldt Current",
    ]
    assert interpretations("Benguela Coastal Upwelling System")[-1] == "Benguela Current"
    assert interpretations("Yellow Sea Large Marine Ecosystem")[-1] == "Yellow Sea"


def test_an_abbreviated_feature_word_is_spelt_out():
    assert interpretations("Šumava Mts.") == ["Šumava Mts", "Šumava Mountains"]


def test_a_name_joining_several_places_has_parts():
    assert parts("Southern Ocean and Atlantic Ocean") == ["Southern Ocean", "Atlantic Ocean"]
    assert parts("Ganges-Brahmaputra-Meghna Delta") == ["Ganges", "Brahmaputra", "Meghna Delta"]
    assert parts("Saigon-Dong Nai") == ["Saigon", "Dong Nai"]
    assert parts("Snake/Columbia") == ["Snake", "Columbia"]


def test_a_hyphenated_name_is_one_place():
    assert parts("Bay of Banyuls-sur-Mer") == []
    assert parts("Aix-en-Provence") == []
    assert parts("Saint-Malo") == []
    assert parts("Time-series") == []
    assert parts("Amundsen Sea") == []


def test_a_long_part_is_a_site_description_not_places():
    assert parts("Canadian High Arctic Research Station Experimental and Reference Area") == []
