"""The Wikidata client's search, claims and cache, against a fake session."""

import pytest

from metalog_geocoding.nominatim import LookupUnavailable, PlaceCache
from metalog_geocoding.wikidata import WikidataClient

SEARCH = {
    "search": [
        {"id": "Q14422", "label": "Lake Zug", "description": "lake in Switzerland",
         "match": {"type": "label", "text": "Lake Zug"}},
        {"id": "Q1", "label": "Lake Zug Lodge", "description": "hotel"},
    ]
}
ENTITIES = {
    "entities": {
        "Q14422": {"claims": {
            "P625": [{"mainsnak": {"datavalue": {"value": {"latitude": 47.13, "longitude": 8.48}}}}],
            "P402": [{"mainsnak": {"datavalue": {"value": "1234"}}}],
        }},
        "Q1": {"claims": {}},
    }
}


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr("metalog_geocoding.nominatim.BACKOFF_S", 0.0)


def client(tmp_path, session):
    return WikidataClient(PlaceCache(tmp_path / "p.sqlite"), session=session, min_interval_s=0)


def test_hits_carry_their_coordinate_and_osm_relation(tmp_path):
    session = FakeSession(FakeResponse(200, SEARCH), FakeResponse(200, ENTITIES))
    hits = client(tmp_path, session).search("Lake Zug")
    assert hits[0]["coord"] == [47.13, 8.48]
    assert hits[0]["osm_relation"] == "1234"
    assert hits[1]["coord"] is None and hits[1]["osm_relation"] is None
    assert session.calls[1]["ids"] == "Q14422|Q1"


def test_an_answer_is_fetched_once(tmp_path):
    session = FakeSession(FakeResponse(200, SEARCH), FakeResponse(200, ENTITIES))
    wikidata = client(tmp_path, session)
    wikidata.search("Lake Zug")
    assert wikidata.search("lake zug")[0]["id"] == "Q14422"
    assert len(session.calls) == 2


def test_no_hits_needs_one_request_and_is_cached(tmp_path):
    session = FakeSession(FakeResponse(200, {"search": []}))
    wikidata = client(tmp_path, session)
    assert wikidata.search("Nowhere In Particular") == []
    assert wikidata.cached("Nowhere In Particular") == []
    assert len(session.calls) == 1


def test_a_failure_is_not_cached(tmp_path):
    session = FakeSession(*[FakeResponse(503)] * 3)
    wikidata = client(tmp_path, session)
    with pytest.raises(LookupUnavailable):
        wikidata.search("Lake Zug")
    assert wikidata.cached("Lake Zug") is None


def test_an_error_body_is_retried(tmp_path):
    busy = FakeResponse(200, {"error": {"code": "maxlag"}})
    session = FakeSession(busy, FakeResponse(200, SEARCH), FakeResponse(200, ENTITIES))
    assert client(tmp_path, session).search("Lake Zug")[0]["id"] == "Q14422"
    assert len(session.calls) == 3
