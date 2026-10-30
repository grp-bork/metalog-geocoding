"""The Nominatim client's cache and rate limit, against a fake session."""

import threading
import time

import pytest
import requests

from metalog_geocoding.nominatim import (
    NEGATIVE_TTL_S,
    NominatimClient,
    NominatimUnavailable,
    PlaceCache,
)

DISKO = {
    "osm_type": "relation",
    "osm_id": 1,
    "category": "natural",
    "type": "bay",
    "display_name": "Qeqertarsuup Tunua",
    "importance": 0.5,
    "geojson": {"type": "Point", "coordinates": [-52.1, 69.15]},
    "licence": "dropped",
}


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    """Answers from a queue; records each request's time and parameters."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((time.monotonic(), params, headers))
        response = self.responses.pop(0) if self.responses else FakeResponse(200, [])
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr("metalog_geocoding.nominatim.BACKOFF_S", 0.0)


def client(tmp_path, session, interval=0.0):
    return NominatimClient(
        PlaceCache(tmp_path / "places.sqlite"), session=session, min_interval_s=interval
    )


def test_an_answer_is_fetched_once(tmp_path):
    session = FakeSession(FakeResponse(200, [DISKO]))
    nominatim = client(tmp_path, session)
    first = nominatim.search("Disko Bay")
    again = nominatim.search("  disko   BAY ")
    assert len(session.calls) == 1
    assert first == again
    assert first[0]["display_name"] == "Qeqertarsuup Tunua"
    assert "licence" not in first[0]


def test_the_request_identifies_the_application(tmp_path):
    session = FakeSession(FakeResponse(200, []))
    client(tmp_path, session).search("Disko Bay")
    _, params, headers = session.calls[0]
    assert "metalog-geocoding" in headers["User-Agent"]
    assert params["q"] == "Disko Bay"
    assert params["polygon_geojson"] == 1


def test_nothing_found_is_cached_until_it_expires(tmp_path):
    session = FakeSession(FakeResponse(200, []))
    nominatim = client(tmp_path, session)
    assert nominatim.search("Nowhere In Particular") == []
    assert nominatim.cached("Nowhere In Particular") == []
    assert len(session.calls) == 1
    later = time.time() + NEGATIVE_TTL_S + 1
    assert nominatim.cache.get("Nowhere In Particular", now=later) is None


def test_a_found_place_does_not_expire(tmp_path):
    nominatim = client(tmp_path, FakeSession(FakeResponse(200, [DISKO])))
    nominatim.search("Disko Bay")
    assert nominatim.cache.get("Disko Bay", now=time.time() + 10 * NEGATIVE_TTL_S)


def test_a_failure_is_not_cached_and_raises(tmp_path):
    session = FakeSession(
        FakeResponse(503), requests.ConnectionError("down"), FakeResponse(429)
    )
    nominatim = client(tmp_path, session)
    with pytest.raises(NominatimUnavailable):
        nominatim.search("Disko Bay")
    assert len(session.calls) == 3
    assert nominatim.cached("Disko Bay") is None


def test_a_retry_that_succeeds_is_cached(tmp_path):
    session = FakeSession(FakeResponse(502), FakeResponse(200, [DISKO]))
    nominatim = client(tmp_path, session)
    assert nominatim.search("Disko Bay")[0]["osm_id"] == 1
    assert nominatim.cached("Disko Bay")


def test_country_codes_are_part_of_the_key(tmp_path):
    session = FakeSession(FakeResponse(200, [DISKO]), FakeResponse(200, []))
    nominatim = client(tmp_path, session)
    nominatim.search("Lara")
    assert nominatim.search("Lara", "ve") == []
    assert session.calls[1][1]["countrycodes"] == "ve"


def test_threads_are_spaced_by_the_interval(tmp_path):
    session = FakeSession()
    nominatim = client(tmp_path, session, interval=0.05)
    threads = [
        threading.Thread(target=nominatim.search, args=(f"place {i}",)) for i in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    times = sorted(t for t, _, _ in session.calls)
    assert len(times) == 5
    assert min(b - a for a, b in zip(times, times[1:])) >= 0.045
