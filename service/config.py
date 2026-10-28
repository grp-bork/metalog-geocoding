"""
Service configuration, read from environment variables at startup.

Every variable has a default that works in a checkout of this repo; see
service/README.md for the full list.
"""

import os
from pathlib import Path

from metalog_geocoding.vocabulary import (
    BUNDLED_MARINE_REGIONS,
    BUNDLED_SEA_ALIASES,
    BUNDLED_SEA_CONTAINMENT,
)

#: Read by metalog_geocoding.paths.data_dir(); listed here for reference.
DATA_DIR_ENV = "METALOG_GEOCODING_DATA"

#: The SQLite result caches. Written to, so in a container it needs a writable
#: volume.
CACHE_DIR_ENV = "METALOG_GEOCODING_CACHE_DIR"
DEFAULT_CACHE_DIR = "cache"

#: The vocabulary files described in docs/validation.md. Unset: the bundled
#: copies, and no location exceptions. A path: that file, which must exist.
#: Empty string: switched off (no vocabulary means regex classification and
#: sea names in IHO's spelling).
MARINE_REGIONS_ENV = "METALOG_GEOCODING_MARINE_REGIONS"
SEA_ALIASES_ENV = "METALOG_GEOCODING_SEA_ALIASES"
SEA_CONTAINMENT_ENV = "METALOG_GEOCODING_SEA_CONTAINMENT"
LOCATION_EXCEPTIONS_ENV = "METALOG_GEOCODING_LOCATION_EXCEPTIONS"

DEFAULT_MARINE_REGIONS = BUNDLED_MARINE_REGIONS
DEFAULT_SEA_ALIASES = BUNDLED_SEA_ALIASES
DEFAULT_SEA_CONTAINMENT = BUNDLED_SEA_CONTAINMENT
DEFAULT_LOCATION_EXCEPTIONS = None


def _optional_vocab_path(env_name: str, default: Path | None) -> Path | None:
    """Resolve one vocabulary file from ``env_name``, else ``default``.

    A set variable naming a missing file is an error; an empty one means none.
    """
    explicit = os.environ.get(env_name)
    if explicit is not None:
        if explicit == "":
            return None
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(
                f"${env_name} is set to {explicit!r} but that file does not exist"
            )
        return path
    return default


class Config:
    """Resolved once at import time; see app.py."""

    def __init__(self):
        self.cache_dir = Path(os.environ.get(CACHE_DIR_ENV, DEFAULT_CACHE_DIR))
        self.marine_regions = _optional_vocab_path(
            MARINE_REGIONS_ENV, DEFAULT_MARINE_REGIONS
        )
        self.sea_aliases = _optional_vocab_path(SEA_ALIASES_ENV, DEFAULT_SEA_ALIASES)
        self.sea_containment = _optional_vocab_path(
            SEA_CONTAINMENT_ENV, DEFAULT_SEA_CONTAINMENT
        )
        # Off unless configured: exceptions are specific to one deployment's
        # samples.
        self.location_exceptions = _optional_vocab_path(
            LOCATION_EXCEPTIONS_ENV, DEFAULT_LOCATION_EXCEPTIONS
        )

    def __repr__(self):
        return (
            f"Config(cache_dir={self.cache_dir}, "
            f"marine_regions={self.marine_regions}, "
            f"sea_aliases={self.sea_aliases}, "
            f"sea_containment={self.sea_containment}, "
            f"location_exceptions={self.location_exceptions})"
        )
