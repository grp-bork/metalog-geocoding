"""
HTTP client for the geocoding service.

Needs only the base install (``requests`` and the standard library), so a
caller never loads the boundary layers itself.

The base URL is resolved in one place, first hit wins:

1. an explicit ``base_url`` argument (e.g. a ``--geocoding-url`` flag),
2. ``$METALOG_GEOCODING_URL``,
3. ``http://localhost:8007``.

``add_argument()`` adds the flag to a script's argument parser.
"""

import os
from dataclasses import dataclass

import requests

from .samples import Sample, Verdict, distinct_coordinates

#: Where the service is. Set this in the environment of anything that calls it.
SERVICE_URL_ENV = "METALOG_GEOCODING_URL"

DEFAULT_SERVICE_URL = "http://localhost:8007"

#: Generous on purpose: a cold batch can geocode thousands of new coordinates,
#: and for a fail-closed caller a timeout means a refused import.
DEFAULT_TIMEOUT = 300


def service_url(explicit: str | None = None) -> str:
    """The service's base URL: explicit argument, then env, then localhost."""
    return (explicit or os.environ.get(SERVICE_URL_ENV) or DEFAULT_SERVICE_URL).rstrip("/")


class GeocodingUnavailable(RuntimeError):
    """The service could not be reached, or answered with a server error.

    Distinct from a sample grading ``error``: that is a result, this is the
    absence of one. A caller must never treat it as a pass.
    """


class GeocodingRequestError(ValueError):
    """The service rejected the request as malformed (HTTP 4xx).

    A bug in the caller; retrying will not help.
    """


@dataclass
class CheckResult:
    """What the service returned for one batch."""

    #: One per submitted sample, in submission order.
    verdicts: list[Verdict]
    #: ``location_hierarchy`` rows, deduplicated per (coordinate, resolution)
    #: block and sorted. The caller writes these; the service never does.
    hierarchy: list[dict]


class GeocodingClient:
    """A thin client over ``POST /check`` and ``GET /health``."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        session: "requests.Session | None" = None,
    ):
        self.base_url = service_url(base_url)
        self.timeout = timeout
        self.session = session or requests.Session()

    def health(self) -> dict:
        """Ask whether the service is up and which vocabulary it loaded.

        Call it at the start of a long run so an unreachable service stops the
        run before any work is done.
        """
        return self._get("/health")

    def check(self, samples) -> CheckResult:
        """Grade samples and get back the hierarchy rows they contribute.

        Sends each distinct coordinate once, with samples referencing them by
        index (many samples typically share a coordinate). The dedup is
        ``samples.distinct_coordinates()``, the same function the service
        runs, so both sides cut the request by one rule.
        """
        samples = list(samples)
        coordinates = distinct_coordinates(samples)
        index = {coord: i for i, coord in enumerate(coordinates)}

        payload_samples = []
        for sample in samples:
            coord = sample.coordinate
            entry = {
                "id": sample.id,
                "geographic_location": sample.geographic_location,
                "artificial": sample.artificial,
            }
            if sample.location_resolution:
                entry["location_resolution"] = sample.location_resolution
            # An absent or impossible coordinate is not in the deduped list.
            # Send it raw so the service grades it instead of the sample
            # silently disappearing from the response.
            if coord is not None and coord in index:
                entry["coord"] = index[coord]
            else:
                entry["latitude"] = sample.latitude
                entry["longitude"] = sample.longitude
            payload_samples.append(entry)

        body = self._post(
            "/check",
            {"coordinates": [list(c) for c in coordinates], "samples": payload_samples},
        )
        verdicts = [
            Verdict(
                sample_id=v.get("id", ""),
                grade=v.get("grade", ""),
                reason=v.get("reason", ""),
                # Branch on `code`, not on `reason`: the reason is a
                # per-sample sentence. See samples.py's CODE_* block.
                code=v.get("code", ""),
            )
            for v in body.get("verdicts", [])
        ]
        return CheckResult(verdicts, body.get("location_hierarchy", []))

    # -- transport ---------------------------------------------------------

    def _get(self, path: str) -> dict:
        return self._request("GET", path, None)

    def _post(self, path: str, payload: dict) -> dict:
        return self._request("POST", path, payload)

    def _request(self, method: str, path: str, payload: dict | None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method, url, json=payload, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise GeocodingUnavailable(
                f"could not reach the geocoding service at {url}: {exc}\n"
                f"Set ${SERVICE_URL_ENV} if it is somewhere other than "
                f"{DEFAULT_SERVICE_URL}."
            ) from exc

        if 400 <= response.status_code < 500:
            try:
                message = response.json().get("error", response.text)
            except ValueError:
                message = response.text
            raise GeocodingRequestError(f"{url} rejected the request: {message}")
        if response.status_code >= 500:
            raise GeocodingUnavailable(
                f"{url} returned {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise GeocodingUnavailable(
                f"{url} returned {response.status_code} with a non-JSON body"
            ) from exc


def add_argument(parser, flag: str = "--geocoding-url") -> None:
    """Add the service-URL flag to an argparse parser.

    Defaults to ``None`` so that ``service_url()`` still falls through to
    ``$METALOG_GEOCODING_URL`` when the flag is not passed.
    """
    parser.add_argument(
        flag,
        default=None,
        metavar="URL",
        help=(
            f"base URL of the geocoding service "
            f"(default: ${SERVICE_URL_ENV}, else {DEFAULT_SERVICE_URL})"
        ),
    )
