"""Sea-against-sea disagreement as a hierarchy test, not a name test.

Two seas are related when one's chain is a tail of the other's (ancestor or
same place); siblings that merely share an ocean are not.
"""

import pytest

from metalog_geocoding.sea_relatedness import SeaRelatedness, load_containment

#: MRGID -> the "; "-separated chain, i.e. the contents of sea_hierarchy.tsv.
HIERARCHY = {
    1906: "Chukchi Sea; Arctic Ocean",
    2353: "Norwegian Sea; North Atlantic Ocean; Atlantic Ocean",
    2374: "Kattegat; Baltic Sea; North Atlantic Ocean; Atlantic Ocean",
    4276: "Great Australian Bight; Indian Ocean",
    3314: "Mozambique Channel; Indian Ocean",
    8478: "Arabian Sea; Indian Ocean",
    2350: "Barentszee; Arctic Ocean",
}

#: (NAME, MRGID) from the IHO layer. Note Barentsz Sea / Barentszee: the same
#: MRGID under two spellings, which is an identity, not an alias.
SEA_NAMES = [
    ("Chukchi Sea", 1906),
    ("Norwegian Sea", 2353),
    ("Kattegat", 2374),
    ("Great Australian Bight", 4276),
    ("Mozambique Channel", 3314),
    ("Arabian Sea", 8478),
    ("Barentsz Sea", 2350),
]


@pytest.fixture
def rel():
    return SeaRelatedness(HIERARCHY, SEA_NAMES)


def test_an_ancestor_is_related(rel):
    """A claim more specific than the polygon the point landed in."""
    assert rel.related("Chukchi Sea", "Arctic Ocean", mrgid=1906) is True


def test_the_same_place_is_related(rel):
    assert rel.related("Norwegian Sea", "Norwegian Sea", mrgid=2353) is True


def test_a_descendant_is_related_too(rel):
    """Tail-of-the-other, in either direction: which side is more specific is
    not the question being asked."""
    assert rel.related("Arctic Ocean", "Chukchi Sea", mrgid=1906) is True


def test_two_siblings_are_not_related(rel):
    """Arabian Sea against Mozambique Channel: both in the Indian Ocean, and
    5,000 km apart. Sharing an ancestor is not being one."""
    assert rel.related("Arabian Sea", "Mozambique Channel", mrgid=3314) is False


def test_a_deep_chain_relates_at_every_level(rel):
    chain_mrgid = 2374
    for claimed in ("Kattegat", "Baltic Sea", "North Atlantic Ocean", "Atlantic Ocean"):
        assert rel.related(claimed, "Kattegat", mrgid=chain_mrgid) is True, claimed


def test_a_known_rejection_stays_a_rejection(rel):
    """IHO files the Barents Sea under the Arctic, so ``Atlantic Ocean``
    claimed against ``Barentsz Sea`` found is genuinely unrelated."""
    assert rel.related("Atlantic Ocean", "Barentsz Sea", mrgid=2350) is False


def test_an_iho_spelling_resolves_through_its_mrgid(rel):
    """``Barentsz Sea`` (IHO) and ``Barentszee`` (gazetteer) share an MRGID,
    so they are identical without an alias entry."""
    assert rel.chain("Barentsz Sea") == rel.chain("Barentszee")


def test_an_unknown_name_has_no_opinion(rel):
    """None, not False. The caller falls through to its generic paths rather
    than inventing an error from a name nothing can place."""
    assert rel.related("Some Sea Nobody Named", "Arctic Ocean", mrgid=1906) is None
    assert rel.related("Chukchi Sea", "", ocean="", mrgid=None) is None


def test_a_trailing_parenthetical_is_stripped(rel):
    """``Gulf of California (also known as the Sea of Cortes)`` resolves to
    the Gulf of California."""
    rel = SeaRelatedness(
        {1: "Gulf of California; North Pacific Ocean; Pacific Ocean"},
        [("Gulf of California", 1)],
    )
    assert rel.related("Gulf of California (also known as the Sea of Cortes)",
                       "Pacific Ocean", mrgid=1) is True


def test_the_geocoded_side_prefers_the_recorded_chain_over_the_name(rel):
    """The geocoder already carries the point's own chain, so the geocoded side
    is never re-derived from a name that might be spelled differently."""
    assert rel.chain_for_result(mrgid=2374) == rel.chain("Kattegat")
    assert rel.chain_for_result(ocean="Kattegat; Baltic Sea; North Atlantic Ocean; Atlantic Ocean") == (
        rel.chain("Kattegat")
    )


def test_an_ocean_resolves_although_it_heads_no_row_of_its_own(rel):
    """Oceans are only ever the last link of somebody else's chain -- every
    level of a chain has to be resolvable as a name in its own right."""
    assert rel.chain("Atlantic Ocean") == ("atlantic ocean",)
    assert rel.chain("Indian Ocean") is not None


def test_containment_covers_a_sea_with_no_polygon(rel):
    """The Weddell Sea has no IHO polygon at all. Containment is parentage, not
    spelling, and it lives in its own file for that reason."""
    rel = SeaRelatedness(
        {1: "Southern Ocean"},
        [("Southern Ocean", 1)],
        containment={"Weddell Sea": "Southern Ocean"},
    )
    assert rel.related("Weddell Sea", "Southern Ocean", mrgid=1) is True


def test_a_real_polygon_beats_a_containment_entry():
    """The polygon is measured; the table is asserted. A hand-written entry
    must never override a sea that has boundaries of its own."""
    rel = SeaRelatedness(
        {1: "Chukchi Sea; Arctic Ocean", 2: "Baltic Sea; Atlantic Ocean"},
        [("Chukchi Sea", 1), ("Baltic Sea", 2)],
        containment={"Chukchi Sea": "Baltic Sea"},
    )
    assert rel.chain("Chukchi Sea") == ("chukchi sea", "arctic ocean")


def test_format_chain_renders_the_gazetteer_spelling(rel):
    """Comparison keys are case-folded; display uses the gazetteer spelling."""

    assert rel.format_chain(rel.chain("Kattegat")).startswith("Kattegat; Baltic Sea")


def test_load_containment_reads_the_two_column_file(tmp_path):
    path = tmp_path / "sea_containment.tsv"
    path.write_text("claimed\tiho_sea\nWeddell Sea\tSouthern Ocean\nRoss Sea\tSouthern Ocean\n")
    assert load_containment(path) == {
        "Weddell Sea": "Southern Ocean",
        "Ross Sea": "Southern Ocean",
    }


def test_load_containment_rejects_an_unexpected_header(tmp_path):
    """Parentage read out of the wrong columns would silently invert every
    relationship in the file, so the header is checked rather than assumed."""
    path = tmp_path / "sea_containment.tsv"
    path.write_text("iho_sea\tclaimed\nSouthern Ocean\tWeddell Sea\n")
    with pytest.raises(ValueError):
        load_containment(path)


def test_load_containment_treats_a_missing_file_as_no_containment(tmp_path):
    """Not an error: the seas it would have covered simply stay unresolvable,
    which is a verdict this module is allowed to reach."""
    assert load_containment(tmp_path / "absent.tsv") == {}
