"""
Territories a sample may legitimately name that the geocoder can never return.

A **containment** table: it maps a claimed territory to the larger Natural
Earth unit a coordinate inside it resolves to, because Natural Earth has no
admin-0 polygon under the claimed name and the two strings could never agree.

Not an alias table. ``country_aliases.py`` relates two spellings of the same
country (``USA`` / ``United States``); containment relates a smaller place to a
larger one and is one-way: a Svalbard sample is in Norway, a Norway sample is
not in Svalbard. Nor does it run the other way: a sample claiming a sovereign
state for a point inside a separately mapped dependency (claimed ``China``,
found ``Hong Kong``) is a disagreement, since the land vocabulary is flat.

Each territory is also pinned to its own admin-1 polygon (``adm1_code``), so a
Svalbard claim is checked against Svalbard and not against all of Norway: a
"Svalbard" sample near Oslo is a wrong coordinate, and the Indian Ocean
Territories unit covers two islands ~1,000 km apart. ``ClaimChecker`` measures
claims against that polygon.

Each entry was established by geocoding a coordinate inside the territory.
``validate_targets()`` runs when ``NaturalEarthLandGeocoder`` loads, so a
Natural Earth release that adds one of these as its own unit, or drops or
moves its admin-1 polygon, fails loudly.

Keys follow the ``geographic_location`` vocabulary: Natural Earth spellings
where it has a unit, ISO 3166-1 spellings otherwise (hence ``Réunion``).
"""

from typing import NamedTuple, Optional

from .country_aliases import canonical, same_country


class Territory(NamedTuple):
    #: The admin-0 unit a coordinate inside the territory geocodes to.
    unit: str
    #: Natural Earth admin-1 ``adm1_code`` of the territory's own polygon.
    adm1_code: str


#: claimed territory -> where Natural Earth maps it.
CONTAINED_IN = {
    # Natural Earth folds Svalbard into Norway; there is no SJM unit.
    "Svalbard": Territory("Norway", "NOR-901"),
    # Tokelau resolves via a ~1 km fallback to New Zealand's nearest polygon.
    "Tokelau": Territory("New Zealand", "NZL-5472"),
    # Natural Earth carries one "Indian Ocean Territories" unit covering both
    # islands, which lie ~1,000 km apart; admin-1 tells them apart.
    "Christmas Island": Territory("Indian Ocean Territories", "IOA-2652"),
    "Cocos Islands": Territory("Indian Ocean Territories", "IOA-1928"),
    # Departements d'outre-mer: integral French territory, not dependencies.
    # Natural Earth maps all five inside the France polygon.
    "French Guiana": Territory("France", "FRA-2000"),
    "Guadeloupe": Territory("France", "FRA-4603"),
    "Martinique": Territory("France", "FRA-1442"),
    "Mayotte": Territory("France", "FRA-4602"),
    "Réunion": Territory("France", "FRA-4601"),
}

#: Same table keyed on the canonical form, so a legacy spelling ("Reunion"
#: without the accent) still resolves.
_BY_CANONICAL = {canonical(claimed): t for claimed, t in CONTAINED_IN.items()}


def territory(claimed: str) -> Optional[Territory]:
    """Return where Natural Earth maps the territory ``claimed``, or None."""
    if not claimed:
        return None
    return _BY_CANONICAL.get(canonical(claimed))


def containing_unit(claimed: str) -> Optional[str]:
    """
    Return the Natural Earth unit that contains ``claimed``, or None.

    Args:
        claimed: The ``geographic_location`` a sample states

    Returns:
        The unit a coordinate inside ``claimed`` geocodes to, or None if the
        claim is not one of the territories this table covers.
    """
    found = territory(claimed)
    return found.unit if found else None


def is_contained_in(claimed: str, found: str) -> bool:
    """
    True when ``claimed`` is a territory of the geocoded unit ``found``.

    Only says the names are compatible; whether the point is in the territory
    rather than elsewhere in the unit is measured by the caller (see
    ``ClaimChecker``). The comparison of ``found`` goes through
    ``country_aliases`` so that a spelling variant of the containing unit
    still matches.
    """
    unit = containing_unit(claimed)
    return bool(unit) and same_country(unit, found)


def validate_targets(published_names: set, adm1_units: dict | None = None) -> None:
    """
    Fail loudly if a containment entry no longer matches Natural Earth.

    Args:
        published_names: Every country name ``NaturalEarthLandGeocoder`` can
            return, i.e. NAME_LONG with COUNTRY_NAME_OVERRIDES applied.
        adm1_units: admin-1 ``adm1_code`` -> the published name of its admin-0
            unit. Omitted, the admin-1 pins are not checked.

    Raises:
        ValueError: if a target is missing, which means either a typo or a
            Natural Earth release that renamed the containing unit. Either way
            the entry would never match and the claim would be rejected.
    """
    missing = sorted({t.unit for t in CONTAINED_IN.values() if t.unit not in published_names})
    if missing:
        raise ValueError(
            "CONTAINED_IN targets that Natural Earth cannot publish, so those "
            f"claims would be rejected: {missing}"
        )

    # A key Natural Earth carries as its own unit is not a containment case
    # and must be removed.
    promoted = sorted({c for c in CONTAINED_IN if c in published_names})
    if promoted:
        raise ValueError(
            "CONTAINED_IN keys that Natural Earth now publishes as their own "
            f"units, so they are no longer containment cases: {promoted}"
        )

    if adm1_units is not None:
        # The polygon must exist and lie in the unit the name resolves to.
        wrong = sorted(
            f"{claimed} ({t.adm1_code})"
            for claimed, t in CONTAINED_IN.items()
            if adm1_units.get(t.adm1_code) != t.unit
        )
        if wrong:
            raise ValueError(
                "CONTAINED_IN admin-1 codes that are missing from Natural Earth "
                f"or no longer inside their containing unit: {wrong}"
            )
