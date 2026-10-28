"""
Geographic continents for overseas territories and transcontinental regions.

``GEOGRAPHIC_OVERRIDES`` maps ISO 3166-2 subdivision codes to the continent a
subdivision lies on, where that differs from its country's. ``land.py`` looks
it up with Natural Earth's admin-1 ``iso_3166_2``. Only some keys match a
Natural Earth admin-1 code; the rest name territories Natural Earth carries as
their own admin-0 unit with the right ``CONTINENT`` already, and are harmless.
Codes Natural Earth spells differently (Canary Islands, Caribbean Netherlands)
are covered by ``NE_ADM1_GEOGRAPHIC_EXTRA`` in ``natural_earth_continents.py``.

Based on: https://en.wikipedia.org/wiki/List_of_transcontinental_countries
"""

GEOGRAPHIC_OVERRIDES = {
    # United States - Oceania territories
    "US-HI": "Oceania",  # Hawaii (Polynesia)
    "US-AS": "Oceania",  # American Samoa (Polynesia)
    "US-GU": "Oceania",  # Guam (Micronesia)
    "US-MP": "Oceania",  # Northern Mariana Islands (Micronesia)

    # France - Caribbean (North America)
    "FR-GP": "North America",  # Guadeloupe (Caribbean) - ISO alpha code
    "FR-971": "North America",  # Guadeloupe (Caribbean) - INSEE code
    "FR-MQ": "North America",  # Martinique (Caribbean) - ISO alpha code
    "FR-972": "North America",  # Martinique (Caribbean) - INSEE code
    "FR-BL": "North America",  # Saint Barthélemy (Caribbean)
    "FR-MF": "North America",  # Saint Martin (Caribbean)
    "FR-PM": "North America",  # Saint Pierre and Miquelon (North Atlantic)
    "FR-977": "North America",  # Saint Barthélemy - INSEE code
    "FR-978": "North America",  # Saint Martin - INSEE code

    # France - South America
    "FR-GF": "South America",  # French Guiana (South American mainland)

    # France - Africa (Indian Ocean)
    "FR-RE": "Africa",  # Réunion (Indian Ocean) - ISO alpha code
    "FR-974": "Africa",  # Réunion - INSEE code
    "FR-YT": "Africa",  # Mayotte (Indian Ocean) - ISO alpha code
    "FR-976": "Africa",  # Mayotte - INSEE code

    # France - Oceania (Pacific)
    "FR-PF": "Oceania",  # French Polynesia (Polynesia)
    "FR-NC": "Oceania",  # New Caledonia (Melanesia)
    "FR-WF": "Oceania",  # Wallis and Futuna (Polynesia)

    # Portugal - Africa
    "PT-30": "Africa",  # Madeira (Atlantic islands off Africa)
    "PT-20": "Europe",  # Azores (on North American plate, but culturally/politically Europe)

    # Spain - Africa
    "ES-CN": "Africa",  # Canary Islands (Atlantic islands off Africa)
    "ES-CE": "Africa",  # Ceuta (North African coast)
    "ES-ML": "Africa",  # Melilla (North African coast)

    # Netherlands - Caribbean
    "AW": "North America",  # Aruba (Caribbean, Leeward Antilles)
    "CW": "North America",  # Curaçao (Caribbean, Leeward Antilles)
    "SX": "North America",  # Sint Maarten (Caribbean, Leeward Islands)
    "BQ-BO": "South America",  # Bonaire (on South American shelf)
    "BQ-SA": "North America",  # Saba (Caribbean, Leeward Islands)
    "BQ-SE": "North America",  # Sint Eustatius (Caribbean, Leeward Islands)

    # Denmark - North America
    "GL": "North America",  # Greenland (North American landmass)

    # Yemen - Africa
    "YE-SU": "Africa",  # Socotra (closer to Horn of Africa)

    # Australia - Asia (Indian Ocean territories)
    "CX": "Asia",  # Christmas Island (Indian Ocean, culturally SE Asian)
    "CC": "Asia",  # Cocos (Keeling) Islands (Indian Ocean, culturally SE Asian)

    # Egypt - Asia (Sinai Peninsula)
    "EG-SIN": "Asia",  # South Sinai Governorate
    "EG-JS": "Asia",  # North Sinai Governorate
    "EG-IS": "Africa",  # Ismailia (transcontinental, mostly Africa)
    "EG-SUZ": "Africa",  # Suez (transcontinental, mostly Africa)
}

# Subdivisions whose remote islands lie on another continent than the rest:
# the override applies only west of the given longitude. Natural Earth's
# Valparaíso holds the mainland and Juan Fernández (South America) as well as
# Easter Island and Salas y Gómez (Oceania).
WESTERN_PART_OVERRIDES = {
    "CL-VS": (-100.0, "Oceania"),
}

# Countries with overseas territories that may need an override (not read by
# the geocoder).
COUNTRIES_WITH_OVERSEAS_TERRITORIES = {
    "France",
    "United Kingdom",
    "United States",
    "Netherlands",
    "Portugal",
    "Spain",
    "Denmark",
    "Australia",
    "Chile",
    "Norway",
    "New Zealand",
}
