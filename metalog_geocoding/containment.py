"""
Territories a sample may legitimately name that the geocoder can never return.

A **containment** table: it maps a claimed territory to the larger Natural
Earth unit a coordinate inside it resolves to, because Natural Earth has no
polygon under the claimed name and the two strings could never agree.

Not an alias table. ``country_aliases.py`` relates two spellings of the same
country (``USA`` / ``United States``); containment relates a smaller place to a
larger one and is one-way: a Svalbard sample is in Norway, a Norway sample is
not in Svalbard. Nor does it run the other way: a sample claiming a sovereign
state for a point inside a separately mapped dependency (claimed ``China``,
found ``Hong Kong``) is a disagreement, since the land vocabulary is flat.

Each entry was established by geocoding a coordinate inside the territory.
``validate_targets()`` runs when ``NaturalEarthLandGeocoder`` loads, so a
Natural Earth release that adds one of these as its own unit fails loudly.

Keys follow the ``geographic_location`` vocabulary: Natural Earth spellings
where it has a unit, ISO 3166-1 spellings otherwise (hence ``Réunion``).
"""

from typing import Optional

from .country_aliases import canonical, same_country

#: claimed territory -> the Natural Earth unit a coordinate inside it resolves to.
CONTAINED_IN = {
    # Natural Earth folds Svalbard into Norway; there is no SJM unit.
    "Svalbard": "Norway",
    # Tokelau resolves via a ~1 km fallback to New Zealand's nearest polygon.
    "Tokelau": "New Zealand",
    # Natural Earth carries one "Indian Ocean Territories" unit covering both
    # islands, which lie ~1,000 km apart, so no rename can tell them apart.
    "Christmas Island": "Indian Ocean Territories",
    "Cocos Islands": "Indian Ocean Territories",
    # Departements d'outre-mer: integral French territory, not dependencies.
    # Natural Earth maps all five inside the France polygon.
    "French Guiana": "France",
    "Guadeloupe": "France",
    "Martinique": "France",
    "Mayotte": "France",
    "Réunion": "France",
}

#: Same table keyed on the canonical form, so a legacy spelling ("Reunion"
#: without the accent) still resolves.
_BY_CANONICAL = {canonical(claimed): unit for claimed, unit in CONTAINED_IN.items()}


def containing_unit(claimed: str) -> Optional[str]:
    """
    Return the Natural Earth unit that contains ``claimed``, or None.

    Args:
        claimed: The ``geographic_location`` a sample states

    Returns:
        The unit a coordinate inside ``claimed`` geocodes to, or None if the
        claim is not one of the territories this table covers.
    """
    if not claimed:
        return None
    return _BY_CANONICAL.get(canonical(claimed))


def is_contained_in(claimed: str, found: str) -> bool:
    """
    True when ``claimed`` is a territory inside the geocoded unit ``found``.

    The comparison of ``found`` goes through ``country_aliases`` so that a
    spelling variant of the containing unit still matches.
    """
    unit = containing_unit(claimed)
    return bool(unit) and same_country(unit, found)


def validate_targets(published_names: set) -> None:
    """
    Fail loudly if a containment target is not a name the geocoder can publish.

    Args:
        published_names: Every country name ``NaturalEarthLandGeocoder`` can
            return, i.e. NAME_LONG with COUNTRY_NAME_OVERRIDES applied.

    Raises:
        ValueError: if a target is missing, which means either a typo or a
            Natural Earth release that renamed the containing unit. Either way
            the entry would never match and the claim would be rejected.
    """
    missing = sorted({u for u in CONTAINED_IN.values() if u not in published_names})
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
