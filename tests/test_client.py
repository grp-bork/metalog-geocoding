"""The client: where the service is, what goes on the wire, and how failures
are told apart.

No network. The transport is a stub session, because what matters here is the
request this builds and the exception it raises -- not requests' behaviour.
"""

import argparse
import json

import pytest
import requests

from metalog_geocoding import client as client_module
from metalog_geocoding.client import (
    DEFAULT_SERVICE_URL,
    SERVICE_URL_ENV,
    GeocodingClient,
    GeocodingRequestError,
    GeocodingUnavailable,
    service_url,
)
from metalog_geocoding.samples import Sample


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records the one request made, and returns whatever it was given."""

    def __init__(self, response=None, raises=None):
        self.response = response or FakeResponse(payload={"verdicts": [], "location_hierarchy": []})
        self.raises = raises
        self.calls = []

    def request(self, method, url, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return self.response


# -- where the service is ---------------------------------------------------

def test_the_default_is_localhost_8007(monkeypatch):
    monkeypatch.delenv(SERVICE_URL_ENV, raising=False)
    assert service_url() == DEFAULT_SERVICE_URL
    assert DEFAULT_SERVICE_URL.endswith(":8007")


def test_the_environment_beats_the_default(monkeypatch):
    monkeypatch.setenv(SERVICE_URL_ENV, "http://geo.internal:9000")
    assert service_url() == "http://geo.internal:9000"


def test_an_explicit_url_beats_the_environment(monkeypatch):
    monkeypatch.setenv(SERVICE_URL_ENV, "http://geo.internal:9000")
    assert service_url("http://elsewhere:1234") == "http://elsewhere:1234"


def test_a_trailing_slash_is_dropped(monkeypatch):
    """Otherwise every path becomes a double slash, which some proxies redirect
    and some reject."""
    monkeypatch.delenv(SERVICE_URL_ENV, raising=False)
    assert service_url("http://host:8007/") == "http://host:8007"


def test_an_empty_environment_variable_does_not_win(monkeypatch):
    """``METALOG_GEOCODING_URL=`` in a compose file is unset, not "the empty
    URL" -- falling through to it would produce ``/health`` as a whole URL."""
    monkeypatch.setenv(SERVICE_URL_ENV, "")
    assert service_url() == DEFAULT_SERVICE_URL


def test_the_argparse_flag_defaults_to_none_so_the_environment_still_wins(monkeypatch):
    """A flag defaulting to the literal URL would silently outrank the
    environment for every script that wired it up."""
    parser = argparse.ArgumentParser()
    client_module.add_argument(parser)
    args = parser.parse_args([])
    assert args.geocoding_url is None

    monkeypatch.setenv(SERVICE_URL_ENV, "http://from-env:9000")
    assert service_url(args.geocoding_url) == "http://from-env:9000"

    args = parser.parse_args(["--geocoding-url", "http://from-flag:1"])
    assert service_url(args.geocoding_url) == "http://from-flag:1"


# -- what goes on the wire --------------------------------------------------

def test_only_unique_coordinates_are_sent():
    """Geocoding depends only on the coordinate, so each one is sent once and
    samples refer to it by index."""
    session = FakeSession()
    GeocodingClient("http://x", session=session).check([
        Sample(id="a", latitude="1.0", longitude="2.0"),
        Sample(id="b", latitude="1.0", longitude="2.0"),
        Sample(id="c", latitude="3.0", longitude="4.0"),
    ])
    body = session.calls[0]["json"]
    assert body["coordinates"] == [[1.0, 2.0], [3.0, 4.0]]
    assert [s["coord"] for s in body["samples"]] == [0, 0, 1]


def test_a_sample_without_a_usable_coordinate_is_sent_with_no_index():
    """It never reaches the geocoder but still gets a verdict, so the response
    stays one-per-sample."""
    session = FakeSession()
    GeocodingClient("http://x", session=session).check([
        Sample(id="a", latitude="1.0", longitude="2.0"),
        Sample(id="b", latitude="", longitude=""),
        Sample(id="c", latitude="91.0", longitude="0.0"),
    ])
    body = session.calls[0]["json"]
    assert body["coordinates"] == [[1.0, 2.0]]
    assert "coord" not in body["samples"][1]
    assert body["samples"][1]["latitude"] == ""
    assert "coord" not in body["samples"][2], "an impossible coordinate is not geocodable"
    assert body["samples"][2]["latitude"] == "91.0"
    assert len(body["samples"]) == 3


def test_the_sample_fields_the_grader_needs_are_all_sent():
    session = FakeSession()
    GeocodingClient("http://x", session=session).check([
        Sample(id="a", latitude="1.0", longitude="2.0",
               geographic_location="Denmark", location_resolution="site",
               artificial="mock"),
    ])
    entry = session.calls[0]["json"]["samples"][0]
    assert entry["geographic_location"] == "Denmark"
    assert entry["location_resolution"] == "site"
    assert entry["artificial"] == "mock"


def test_an_unset_resolution_is_not_sent():
    session = FakeSession()
    GeocodingClient("http://x", session=session).check([
        Sample(id="a", latitude="1.0", longitude="2.0", geographic_location="Denmark"),
    ])
    assert "location_resolution" not in session.calls[0]["json"]["samples"][0]


def test_the_response_is_parsed_into_verdicts_and_hierarchy():
    session = FakeSession(FakeResponse(payload={
        "verdicts": [{
            "id": "a",
            "grade": "error",
            "code": "different_location",
            "reason": (
                "the sample states 'Botswana', but the coordinate is inside "
                "'Zimbabwe'; the nearest land called 'Botswana' is 14 km away"
            ),
        }],
        "location_hierarchy": [{"latitude": 1.0, "longitude": 2.0, "location_resolution": "site",
                                "scheme": "admin", "level": 1, "key": "continent", "value": "Europe"}],
    }))
    result = GeocodingClient("http://x", session=session).check(
        [Sample(id="a", latitude="1.0", longitude="2.0")]
    )
    assert result.verdicts[0].sample_id == "a"
    assert result.verdicts[0].grade == "error"
    # Callers branch on the code; the reason is the human-readable finding.
    assert result.verdicts[0].code == "different_location"
    assert "Zimbabwe" in result.verdicts[0].reason
    assert result.hierarchy[0]["value"] == "Europe"


def test_health_is_a_get():
    session = FakeSession(FakeResponse(payload={"status": "ok"}))
    assert GeocodingClient("http://x", session=session).health() == {"status": "ok"}
    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["url"] == "http://x/health"


# -- the three outcomes a fail-closed importer must keep apart --------------

def test_a_sample_grading_error_is_a_normal_result_not_an_exception():
    """The distinction the whole design rests on: bad *data* is a 200."""
    session = FakeSession(FakeResponse(payload={
        "verdicts": [{
            "id": "a",
            "grade": "error",
            "code": "not_on_land",
            "reason": (
                "the sample states 'Germany', but the coordinate is not on "
                "land: there is none at all within 10 km of it"
            ),
        }],
        "location_hierarchy": [],
    }))
    result = GeocodingClient("http://x", session=session).check(
        [Sample(id="a", latitude="1.0", longitude="2.0")]
    )
    assert result.verdicts[0].grade == "error"


@pytest.mark.parametrize("status", [400, 404, 422])
def test_a_4xx_is_a_caller_bug(status):
    """Retrying sends the same bad request again, so this must not be
    confused with a transient condition."""
    session = FakeSession(FakeResponse(status, payload={"error": "missing 'samples' key"}))
    with pytest.raises(GeocodingRequestError) as excinfo:
        GeocodingClient("http://x", session=session).health()
    assert "missing 'samples' key" in str(excinfo.value)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_5xx_is_the_absence_of_an_answer(status):
    session = FakeSession(FakeResponse(status, text="upstream is down"))
    with pytest.raises(GeocodingUnavailable):
        GeocodingClient("http://x", session=session).health()


@pytest.mark.parametrize(
    "exc",
    [requests.ConnectionError("refused"), requests.Timeout("too slow")],
)
def test_an_unreachable_service_is_unavailable_not_a_pass(exc):
    """For a fail-closed importer, a timeout that reads as "no problems found"
    is exactly how a bad coordinate gets in."""
    session = FakeSession(raises=exc)
    with pytest.raises(GeocodingUnavailable) as excinfo:
        GeocodingClient("http://x", session=session).health()
    assert SERVICE_URL_ENV in str(excinfo.value), "the error should say how to point it elsewhere"


def test_a_non_json_body_is_unavailable_rather_than_a_crash():
    """A proxy's HTML error page is the absence of an answer, not a parse bug
    to surface as ValueError three frames up."""
    session = FakeSession(FakeResponse(200, text="<html>gateway</html>"))
    with pytest.raises(GeocodingUnavailable):
        GeocodingClient("http://x", session=session).health()


def test_the_two_error_types_cannot_be_caught_by_one_except():
    """One is a bug, the other an outage; a shared base would let a single
    ``except`` merge them."""
    assert not issubclass(GeocodingUnavailable, GeocodingRequestError)
    assert not issubclass(GeocodingRequestError, GeocodingUnavailable)
    assert issubclass(GeocodingUnavailable, RuntimeError)
    assert issubclass(GeocodingRequestError, ValueError)


def test_the_default_timeout_is_generous():
    """A cold batch of thousands of new coordinates is slow; giving up early
    turns a slow answer into no answer."""

    session = FakeSession()
    GeocodingClient("http://x", session=session).health()
    assert session.calls[0]["timeout"] >= 300
