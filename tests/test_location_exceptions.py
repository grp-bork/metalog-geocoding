"""The exceptions file: what it accepts, what it refuses to load, what it reports.

Standard library only, so these run without the geo stack. The end-to-end
grading test lives in ``test_geo.py``.
"""

import pytest

from metalog_geocoding.location_exceptions import (
    ACCEPTED_PREFIX,
    LocationExceptionError,
    LocationExceptions,
    load_location_exceptions,
)

HEADER = "latitude\tlongitude\tgeographic_location\treason\n"


def write(tmp_path, body, header=HEADER):
    path = tmp_path / "location_exceptions.tsv"
    path.write_text(header + body, encoding="utf-8")
    return path


def test_a_verified_pair_is_accepted_and_carries_its_reason(tmp_path):
    path = write(tmp_path, "16.82383\t-88.10582\tBelize\tTwin Cays is absent from NE 10m\n")
    exceptions = load_location_exceptions(path)
    assert len(exceptions) == 1
    assert exceptions.accepts(16.82383, -88.10582, "Belize") == "Twin Cays is absent from NE 10m"


def test_a_different_claim_at_the_same_point_is_not_accepted(tmp_path):
    """An entry covers a coordinate *and* a claim; Belize at this point says
    nothing about Mexico."""
    path = write(tmp_path, "16.82383\t-88.10582\tBelize\tTwin Cays\n")
    exceptions = load_location_exceptions(path)
    assert exceptions.accepts(16.82383, -88.10582, "Mexico") is None


def test_the_claim_is_matched_case_folded_and_stripped(tmp_path):
    """Spelling, not a different claim."""
    path = write(tmp_path, "16.82383\t-88.10582\tBelize\tTwin Cays\n")
    exceptions = load_location_exceptions(path)
    assert exceptions.accepts(16.82383, -88.10582, "  belize ") is not None


def test_a_moved_coordinate_is_not_accepted(tmp_path):
    """Coordinates are matched as floats with no tolerance: an exception is
    about one point, and moving the point invalidates it. ``24.98596`` and
    ``24.985960000000002`` are different floats."""
    path = write(tmp_path, "59.61535\t24.985960000000002\tEstonia\tPrangli island\n")
    exceptions = load_location_exceptions(path)
    assert exceptions.accepts(59.61535, 24.985960000000002, "Estonia") is not None
    assert exceptions.accepts(59.61535, 24.98596, "Estonia") is None


def test_comment_lines_are_skipped(tmp_path):
    """The file may document itself in ``#`` comments, like marine_regions.txt."""
    path = write(
        tmp_path,
        "# a comment between entries\n16.82383\t-88.10582\tBelize\tTwin Cays\n",
        header="# leading comment\n" + HEADER,
    )
    exceptions = load_location_exceptions(path)
    assert len(exceptions) == 1


def test_unused_entries_are_reported(tmp_path):
    """A stale entry must be visible: the likeliest cause is an edited
    coordinate, which looks like the exception still working."""
    path = write(
        tmp_path,
        "16.82383\t-88.10582\tBelize\tTwin Cays\n36.852111\t52.606306\tIran\tCaspian\n",
    )
    exceptions = load_location_exceptions(path)
    assert len(exceptions.unused()) == 2
    exceptions.accepts(16.82383, -88.10582, "Belize")
    assert [entry[2] for entry in exceptions.unused()] == ["iran"]


def test_an_entry_with_no_reason_is_refused(tmp_path):
    """The reason is the audit trail; an unjustified exception is refused."""
    path = write(tmp_path, "16.82383\t-88.10582\tBelize\t\n")
    with pytest.raises(LocationExceptionError, match="reason"):
        load_location_exceptions(path)


def test_a_missing_column_is_refused_rather_than_loading_empty(tmp_path):
    """A file that silently loaded as empty would turn every exception back
    into an error."""
    path = write(
        tmp_path,
        "16.82383\t-88.10582\tBelize\n",
        header="latitude\tlongitude\tgeographic_location\n",
    )
    with pytest.raises(LocationExceptionError, match="reason"):
        load_location_exceptions(path)


def test_an_unparseable_coordinate_is_refused(tmp_path):
    path = write(tmp_path, "north\t-88.10582\tBelize\tTwin Cays\n")
    with pytest.raises(LocationExceptionError, match="unparseable coordinate"):
        load_location_exceptions(path)


def test_no_path_means_no_exceptions(tmp_path):
    assert load_location_exceptions(None) is None


def test_the_accepted_prefix_is_greppable():
    """Accepted verdicts are counted and re-audited by grepping this prefix."""

    assert ACCEPTED_PREFIX.endswith(": ")
    assert LocationExceptions({}).unused() == []
