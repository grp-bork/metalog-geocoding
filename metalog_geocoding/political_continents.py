"""Political continent mapping for transcontinental countries.

For countries that span multiple continents, this mapping defines the
"political" or "main" continent based on where the capital city or
primary administrative territory is located.

For countries not in this mapping, Natural Earth's own CONTINENT is used.
"""

# Mapping of country code (GID_0) to political continent
# Based on capital city location or primary administrative territory
POLITICAL_CONTINENTS = {
    "ARM": "Asia",  # Capital: Yerevan
    "ATF": "Antarctica",  # French Southern Territories - primarily Antarctic
    "AZE": "Asia",  # Capital: Baku
    "BES": "North America",  # Caribbean islands
    "BRA": "South America",  # Capital: Brasília (Fernando de Noronha, etc.)
    "CHL": "South America",  # Capital: Santiago (Easter Island in Oceania)
    "COL": "South America",  # Capital: Bogotá
    "CRI": "North America",  # Capital: San José
    "CYP": "Europe",  # Geographic location in Middle East, but politically Europe
    "DNK": "Europe",  # Capital: Copenhagen (Greenland in North America)
    "EGY": "Africa",  # Capital: Cairo
    "ESP": "Europe",  # Capital: Madrid (Ceuta/Melilla in Africa)
    "FRA": "Europe",  # Capital: Paris (overseas territories on all continents)
    "GBR": "Europe",  # Capital: London (overseas territories globally)
    "GEO": "Asia",  # Capital: Tbilisi
    "GRD": "North America",  # Capital: St. George's
    "IDN": "Asia",  # Capital: Jakarta
    "JPN": "Asia",  # Capital: Tokyo (Pacific islands in Oceania)
    "KAZ": "Asia",  # Capital: Astana (formerly Nur-Sultan)
    "NLD": "Europe",  # Capital: Amsterdam (Caribbean territories)
    "NOR": "Europe",  # Capital: Oslo (Svalbard, etc.)
    "PRT": "Europe",  # Capital: Lisbon (Madeira in Africa region)
    "RUS": "Europe",  # Capital: Moscow
    "SHN": "Africa",  # Saint Helena - Atlantic Ocean, closer to Africa
    "TUR": "Asia",  # Capital: Ankara (in Anatolia)
    "UMI": "Oceania",  # US Minor Outlying Islands - primarily Pacific
    "USA": "North America",  # Capital: Washington DC (Hawaii in Oceania, Alaska bridge)
}
