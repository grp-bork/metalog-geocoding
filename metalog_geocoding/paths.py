"""Where the boundary layers live.

The parquet layers (~275 MB) are not in the package or the service image; a
deployment mounts them as a volume. Resolution order, first hit wins:

1. an explicit argument from the caller (e.g. a ``--data-dir`` flag),
2. ``$METALOG_GEOCODING_DATA``,
3. ``./data``, relative to the current working directory.

In the base install so a caller without the geo stack can still resolve it.
"""

import os
from pathlib import Path

#: Environment variable naming the data directory.
DATA_DIR_ENV = "METALOG_GEOCODING_DATA"

#: Fallback, relative to the current working directory.
DEFAULT_DATA_DIR = Path("data")

#: The layers a full lookup needs, listed here so a missing volume fails at
#: startup rather than on the first lookup that needs it.
REQUIRED_LAYERS = (
    "ne_10m_admin_0_countries.parquet",
    "ne_10m_admin_1_states_provinces.parquet",
    "World_Seas_IHO_v3.parquet",
    "Longhurst_world_v4_2010.parquet",
    # GSHHG, for distance to coast (metalog_geocoding/coast.py)
    "GSHHS_f_L1.parquet",
    "GSHHS_f_L5.parquet",
    "GSHHS_f_names.parquet",  # scripts/build_landmass_names.py (GeoNames)
)


class DataDirectoryNotFound(FileNotFoundError):
    """The boundary layers are not where the resolution order said they'd be."""


def data_dir(explicit: "str | os.PathLike[str] | None" = None, *, require: bool = False) -> Path:
    """Resolve the directory holding the boundary layers.

    Args:
        explicit: A path from the caller, which always wins. ``None`` falls
            through to the environment and then to ``./data``.
        require: Check that the directory exists and holds every layer,
            raising `DataDirectoryNotFound` naming what is missing. Off by
            default because `scripts/download_data.py` points at a directory
            it is about to populate.

    Returns:
        The directory, as given (not made absolute).
    """
    if explicit is not None:
        path = Path(explicit)
    else:
        env = os.environ.get(DATA_DIR_ENV)
        path = Path(env) if env else DEFAULT_DATA_DIR

    if require:
        if not path.is_dir():
            raise DataDirectoryNotFound(
                f"boundary layer directory not found: {path.absolute()}\n"
                f"Set ${DATA_DIR_ENV} to the mounted volume, pass --data-dir, "
                f"or create the ./data symlink.\n"
                f"Run: python scripts/download_data.py"
            )
        missing = [name for name in REQUIRED_LAYERS if not (path / name).exists()]
        if missing:
            raise DataDirectoryNotFound(
                f"boundary layers missing from {path.absolute()}:\n"
                + "".join(f"  {name}\n" for name in missing)
                + "Run: python scripts/download_data.py"
            )
    return path
