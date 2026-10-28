"""
Are a claimed sea and a geocoded sea the same water at different levels?

A **hierarchy** test. Every IHO sea resolves to a MarineRegions parent chain,
most specific first (``Gulf of Riga; Baltic Sea; Atlantic Ocean``), and two
names are related when one chain is a tail of the other: one is the other's
ancestor, or the same place. ``Chukchi Sea`` claimed against ``Arctic Ocean``
geocoded is merely more specific, and passes. ``Arabian Sea`` against
``Mozambique Channel`` are siblings under the Indian Ocean, ~5,000 km apart,
and fail.

This is not an alias table. ``country_aliases.py`` decides whether two *names*
mean the same place; spellings of one sea (``East China Sea`` / ``Eastern
China Sea``) belong in the marine vocabulary's aliases. The one spelling gap
closed here is IHO name vs gazetteer name for the *same* MRGID, which is an
identity. Nor is it ``containment.py``, which is a hand-written table for land;
here containment is read off the gazetteer chain.

No network. The IHO layer supplies ``NAME`` -> ``MRGID``, the sea hierarchy
cache supplies ``MRGID`` -> chain, and ``hierarchy.py`` (the only component
that fetches) is never called. A name with no chain on disk is unresolvable
and yields ``None`` -- neither accept nor reject -- so the caller decides on
other grounds. Treating an unknown sea as agreement would accept anything.

Seas with no IHO polygon (``Weddell Sea``, ``Ross Sea``, ``Levantine Sea``)
have no MRGID. An optional containment list (``vocab/sea_containment.tsv`` or
the caller's own) names the IHO sea containing each, giving it a chain of its
own (itself, then its container's chain) so it takes part in the ordinary test.
"""

import re
from pathlib import Path
from typing import Iterable, Optional

from .country_aliases import normalise

#: Separator MarineRegions chains use, as written by ``SeaHierarchyCache``.
CHAIN_SEPARATOR = ";"

#: Parentheticals are documentation, not part of the name: "Gulf of California
#: (also known as the Sea of Cortes)" is the Gulf of California. Stripped only
#: for lookup; nothing is rewritten.
RE_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")


def _key(name: str) -> str:
    """Comparison key for a sea name: the same normalisation country names use."""
    return normalise(name)


def _split_chain(chain: str) -> list[str]:
    """Split a ``"; "``-separated MarineRegions chain, most specific first."""
    return [part.strip() for part in chain.split(CHAIN_SEPARATOR) if part.strip()]


class SeaRelatedness:
    """
    Accept a sea-vs-sea disagreement when one name is the other's ancestor.

    Args:
        hierarchy: MRGID -> ``"; "``-separated parent chain, as in
            ``SeaHierarchyCache.cache`` (pass the geocoder's own).
        sea_names: ``(NAME, MRGID)`` pairs from the IHO layer. Optional: without
            them, only names the gazetteer itself uses resolve, so an IHO
            spelling such as ``Barentsz Sea`` (gazetteer: ``Barentszee``) would
            be unresolvable.
        containment: claimed sea -> the IHO sea whose polygon contains it, for
            seas IHO carries no polygon for. Optional; without it those names
            are unresolvable.
    """

    def __init__(
        self,
        hierarchy: dict[int, str],
        sea_names: Optional[Iterable[tuple[str, int]]] = None,
        containment: Optional[dict[str, str]] = None,
    ):
        self._by_mrgid: dict[int, tuple[str, ...]] = {}
        self._by_name: dict[str, tuple[str, ...]] = {}
        # Comparison keys are normalised past readability ("gulf of aqaba").
        # Keep the first spelling each key arrived under, gazetteer before IHO,
        # for format_chain().
        self._display: dict[str, str] = {}

        for mrgid, chain in hierarchy.items():
            parts = _split_chain(chain)
            if not parts:
                continue
            keys = tuple(_key(part) for part in parts)
            for key, part in zip(keys, parts):
                self._display.setdefault(key, part)
            self._by_mrgid[int(mrgid)] = keys
            # Every level of a chain is itself a resolvable name, with the tail
            # from that level as its chain. Oceans only appear this way: they
            # head no row of their own.
            for i, key in enumerate(keys):
                self._by_name.setdefault(key, keys[i:])

        for name, mrgid in sea_names or ():
            chain = self._by_mrgid.get(int(mrgid))
            if not chain:
                continue  # no cached hierarchy for this polygon: unresolvable
            for variant in (name, RE_PARENTHETICAL.sub("", name)):
                key = _key(variant)
                if key:
                    self._by_name.setdefault(key, chain)
                    self._display.setdefault(key, name)

        # Last, so a sea with its own polygon wins over a containment entry.
        for claimed, container in (containment or {}).items():
            key = _key(claimed)
            if not key or key in self._by_name:
                continue
            container_chain = self._by_name.get(_key(container))
            if container_chain:
                self._by_name[key] = (key,) + container_chain
                self._display.setdefault(key, claimed)

    def format_chain(self, chain: Optional[Iterable[str]]) -> str:
        """Render a chain of comparison keys as readable names, most specific first.

        For reports only. A key with no recorded spelling is shown as the key.
        """
        if not chain:
            return ""
        return f"{CHAIN_SEPARATOR} ".join(
            self._display.get(key, key) for key in chain
        )

    def chain(self, name: str) -> Optional[tuple[str, ...]]:
        """The parent chain for a sea name (most specific first), or None."""
        if not name:
            return None
        chain = self._by_name.get(_key(name))
        if chain is None:
            chain = self._by_name.get(_key(RE_PARENTHETICAL.sub("", name)))
        return chain

    def chain_for_result(self, mrgid=None, ocean: str = "", sea: str = "") -> Optional[tuple[str, ...]]:
        """
        The chain for a geocoded result, preferring what the geocoder recorded.

        Uses the result's MRGID, then its ``ocean`` chain, and only then the
        sea name.
        """
        if mrgid is not None:
            try:
                chain = self._by_mrgid.get(int(mrgid))
            except (TypeError, ValueError):
                chain = None
            if chain:
                return chain
        if ocean:
            parts = _split_chain(ocean)
            if parts:
                return tuple(_key(part) for part in parts)
        return self.chain(sea)

    def related(
        self, claimed: str, found: str, ocean: str = "", mrgid=None
    ) -> Optional[bool]:
        """
        Is ``claimed`` the same water as ``found``, at some level of the tree?

        Args:
            claimed: the sample's ``geographic_location``
            found: the sea the geocoder returned
            ocean: the geocoder's ``"; "``-separated chain for that sea
            mrgid: the geocoder's MRGID for that sea

        Returns:
            True when one chain is a tail of the other (one name is the other's
            ancestor, or the same place), False when neither is, and None when
            either name has no chain on disk (no opinion).
        """
        claimed_chain = self.chain(claimed)
        found_chain = self.chain_for_result(mrgid=mrgid, ocean=ocean, sea=found)
        if not claimed_chain or not found_chain:
            return None
        shorter, longer = sorted((claimed_chain, found_chain), key=len)
        return longer[-len(shorter):] == shorter


def from_files(
    iho_parquet: Path, hierarchy_tsv: Path, containment_tsv: Optional[Path] = None
) -> SeaRelatedness:
    """
    Build a ``SeaRelatedness`` straight from the two files, for ad-hoc use.

    Reads only the ``NAME``/``MRGID`` columns of the IHO parquet, so it does not
    pay for the 140 MB of geometry. Code that already has a geocoder should pass
    ``sea_geocoder.hierarchy_cache.cache`` and ``sea_geocoder.seas`` instead.
    """
    import pandas as pd

    hierarchy: dict[int, str] = {}
    with open(hierarchy_tsv, "r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        if header != ["mrgid", "hierarchy"]:
            raise ValueError(f"unexpected header in {hierarchy_tsv}: {header}")
        for line in handle:
            if not line.strip():
                continue
            mrgid, chain = line.rstrip("\n").split("\t")[:2]
            hierarchy[int(mrgid)] = chain

    names = pd.read_parquet(iho_parquet, columns=["NAME", "MRGID"])
    containment = load_containment(containment_tsv) if containment_tsv else None
    return SeaRelatedness(hierarchy, zip(names["NAME"], names["MRGID"]), containment)


def load_containment(path: Path) -> dict[str, str]:
    """
    Read ``sea_containment.tsv``: claimed sea -> the IHO sea containing it.

    A default is bundled as ``vocab/sea_containment.tsv``. A missing file is not
    an error: it yields no containment, and those seas stay unresolvable.
    """
    mapping: dict[str, str] = {}
    if not Path(path).exists():
        return mapping
    with open(path, "r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        if header[:2] != ["claimed", "iho_sea"]:
            raise ValueError(f"unexpected header in {path}: {header}")
        for line in handle:
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 2 and fields[0].strip() and fields[1].strip():
                mapping[fields[0].strip()] = fields[1].strip()
    return mapping
