"""Continent lookup tables specific to the Natural Earth land geocoder.

Natural Earth is the land source because it is public domain, so derived
annotations can be redistributed (e.g. under ODbL). Nothing here is derived
from a source with a more restrictive licence: the Russia table was authored
from the standard geographic Europe/Asia convention.

Three tables live here:

* ``SEVEN_SEAS_CONTINENTS`` - the nine ``ne_10m_admin_0_countries`` units whose
  ``CONTINENT`` attribute is the non-continent value ``"Seven seas (open
  ocean)"``.
* ``RUSSIA_GEOGRAPHIC_CONTINENTS`` - Europe/Asia per Russian federal subject,
  keyed by the ISO 3166-2 code that Natural Earth's admin-1 layer carries in
  ``iso_3166_2``.
* ``NE_ADM1_GEOGRAPHIC_EXTRA`` - a handful of subdivisions where
  ``metalog_geocoding/geographic_overrides.py`` has an entry that Natural Earth's admin-1
  codes do not match (Canary Islands, Caribbean Netherlands).

All admin-1 keyed tables use the same key space as
``geographic_overrides.GEOGRAPHIC_OVERRIDES`` (ISO 3166-2), and
``metalog_geocoding/land.py`` merges them into one dict at import time.
"""

# ---------------------------------------------------------------------------
# 1. "Seven seas (open ocean)" admin-0 units
# ---------------------------------------------------------------------------
# Natural Earth files nine remote island units under CONTINENT == "Seven seas
# (open ocean)", which is not a continent. Each is mapped to a real continent
# here, keyed by ADM0_A3 (which equals ISO_A3_EH for all of them except
# Clipperton).
#
# Assignments follow UN M49 region groupings, except South Georgia and Heard
# Island, which are placed in Antarctica because both lie south of the
# Antarctic Convergence (M49 files South Georgia under the Americas).
# The British Indian Ocean Territory (Chagos) is Africa, following M49.
SEVEN_SEAS_CONTINENTS = {
    "ATF": "Antarctica",   # French Southern and Antarctic Lands (Kerguelen, Crozet)
    "SYC": "Africa",       # Seychelles
    "HMD": "Antarctica",   # Heard Island and McDonald Islands (sub-Antarctic)
    "SHN": "Africa",       # Saint Helena, Ascension and Tristan da Cunha
    "MUS": "Africa",       # Mauritius
    "IOT": "Africa",       # British Indian Ocean Territory (Chagos)
    "MDV": "Asia",         # Maldives
    "SGS": "Antarctica",   # South Georgia and the South Sandwich Islands
    "CLP": "North America",  # Clipperton Island (off Mexico, eastern Pacific)
}


# ---------------------------------------------------------------------------
# 2. Russia: geographic Europe / Asia per federal subject
# ---------------------------------------------------------------------------
# Convention used (the standard geographic one):
#   * the Ural Mountains watershed, then
#   * the Ural River down to the Caspian Sea, then
#   * the Caspian Sea, then
#   * the Greater Caucasus main watershed down to the Black Sea.
# (The alternative Kuma-Manych depression convention would move the whole North
# Caucasus into Asia; it is *not* used here.)
#
# Subjects that lie entirely on one side are unambiguous. Subjects that
# straddle the divide are assigned by where the bulk of their territory - and,
# where that is close, the administrative centre - falls. Those are marked
# BORDERLINE:
#
#   RU-BA   Bashkortostan - straddles the southern Urals; Ufa is west  -> Europe
#   RU-ORE  Orenburg      - straddles the Ural River; Orenburg is on it -> Europe
#   RU-CHE  Chelyabinsk   - straddles the Urals, bulk is east          -> Asia
#   RU-SVE  Sverdlovsk    - straddles the Urals, bulk is east
#                           (Yekaterinburg is on the Asian side)       -> Asia
#   RU-DA   Dagestan      - Caspian side of the Caucasus; north of the
#                           main watershed                            -> Europe
#   RU-CE / RU-IN / RU-SE / RU-KB / RU-KC / RU-AD
#                         - North Caucasus republics, north of the main
#                           watershed                                 -> Europe
#   RU-AST  Astrakhan     - Volga delta, west of the Ural River        -> Europe
#   RU-KO / RU-PER / RU-ARK / RU-NEN
#                         - reach the Urals but lie west of the crest  -> Europe
#   RU-TYU  Tyumen        - west of the Urals only in a thin strip     -> Asia
#   RU-X01~ Natural Earth's unnamed "Russia minor island" (0 km^2, in
#                           Baydaratskaya Bay east of the Ural axis)   -> Asia
#
# Bashkortostan is the one subject where a single Europe/Asia label is
# genuinely lossy at admin-1 resolution.
#
# Note on two NE quirks: Natural Earth swaps RU-MOS/RU-MOW relative to ISO
# (it labels RU-MOS the federal city and RU-MOW the oblast); both are Europe so
# it does not matter here. Natural Earth also files Crimea (UA-43) and
# Sevastopol (UA-40) under adm0 Russia; both are Europe either way and need no
# entry.
RUSSIA_GEOGRAPHIC_CONTINENTS = {
    # --- Europe: west of the Urals / Ural River / Caucasus watershed --------
    "RU-AD": "Europe",   # Adygea
    "RU-ARK": "Europe",  # Arkhangelsk
    "RU-AST": "Europe",  # Astrakhan          (BORDERLINE, Ural River)
    "RU-BA": "Europe",   # Bashkortostan      (BORDERLINE, southern Urals)
    "RU-BEL": "Europe",  # Belgorod
    "RU-BRY": "Europe",  # Bryansk
    "RU-CE": "Europe",   # Chechnya           (BORDERLINE, Caucasus)
    "RU-CU": "Europe",   # Chuvashia
    "RU-DA": "Europe",   # Dagestan           (BORDERLINE, Caucasus)
    "RU-IN": "Europe",   # Ingushetia         (BORDERLINE, Caucasus)
    "RU-IVA": "Europe",  # Ivanovo
    "RU-KB": "Europe",   # Kabardino-Balkaria (BORDERLINE, Caucasus)
    "RU-KC": "Europe",   # Karachay-Cherkessia (BORDERLINE, Caucasus)
    "RU-KDA": "Europe",  # Krasnodar Krai
    "RU-KGD": "Europe",  # Kaliningrad
    "RU-KIR": "Europe",  # Kirov
    "RU-KL": "Europe",   # Kalmykia
    "RU-KLU": "Europe",  # Kaluga
    "RU-KO": "Europe",   # Komi               (BORDERLINE, reaches the Urals)
    "RU-KOS": "Europe",  # Kostroma
    "RU-KR": "Europe",   # Karelia
    "RU-KRS": "Europe",  # Kursk
    "RU-LEN": "Europe",  # Leningrad oblast
    "RU-LIP": "Europe",  # Lipetsk
    "RU-ME": "Europe",   # Mari El
    "RU-MO": "Europe",   # Mordovia
    "RU-MOS": "Europe",  # Moscow (federal city, per NE's labelling)
    "RU-MOW": "Europe",  # Moscow oblast (per NE's labelling)
    "RU-MUR": "Europe",  # Murmansk
    "RU-NEN": "Europe",  # Nenets AO          (BORDERLINE, reaches the Urals)
    "RU-NGR": "Europe",  # Novgorod
    "RU-NIZ": "Europe",  # Nizhny Novgorod
    "RU-ORE": "Europe",  # Orenburg           (BORDERLINE, Ural River)
    "RU-ORL": "Europe",  # Oryol
    "RU-PER": "Europe",  # Perm Krai          (BORDERLINE, reaches the Urals)
    "RU-PNZ": "Europe",  # Penza
    "RU-PSK": "Europe",  # Pskov
    "RU-ROS": "Europe",  # Rostov
    "RU-RYA": "Europe",  # Ryazan
    "RU-SAM": "Europe",  # Samara
    "RU-SAR": "Europe",  # Saratov
    "RU-SE": "Europe",   # North Ossetia-Alania (BORDERLINE, Caucasus)
    "RU-SMO": "Europe",  # Smolensk
    "RU-SPE": "Europe",  # Saint Petersburg
    "RU-STA": "Europe",  # Stavropol Krai
    "RU-TA": "Europe",   # Tatarstan
    "RU-TAM": "Europe",  # Tambov
    "RU-TUL": "Europe",  # Tula
    "RU-TVE": "Europe",  # Tver
    "RU-UD": "Europe",   # Udmurtia
    "RU-ULY": "Europe",  # Ulyanovsk
    "RU-VGG": "Europe",  # Volgograd
    "RU-VLA": "Europe",  # Vladimir
    "RU-VLG": "Europe",  # Vologda
    "RU-VOR": "Europe",  # Voronezh
    "RU-YAR": "Europe",  # Yaroslavl

    # --- Asia: east of the Urals / Ural River ------------------------------
    "RU-AL": "Asia",     # Altai Republic
    "RU-ALT": "Asia",    # Altai Krai
    "RU-AMU": "Asia",    # Amur
    "RU-BU": "Asia",     # Buryatia
    "RU-CHE": "Asia",    # Chelyabinsk        (BORDERLINE, Urals)
    "RU-CHU": "Asia",    # Chukotka AO
    "RU-IRK": "Asia",    # Irkutsk
    "RU-KAM": "Asia",    # Kamchatka Krai
    "RU-KEM": "Asia",    # Kemerovo
    "RU-KGN": "Asia",    # Kurgan
    "RU-KHA": "Asia",    # Khabarovsk Krai
    "RU-KHM": "Asia",    # Khanty-Mansi AO
    "RU-KK": "Asia",     # Khakassia
    "RU-KYA": "Asia",    # Krasnoyarsk Krai
    "RU-MAG": "Asia",    # Magadan
    "RU-NVS": "Asia",    # Novosibirsk
    "RU-OMS": "Asia",    # Omsk
    "RU-PRI": "Asia",    # Primorsky Krai
    "RU-SA": "Asia",     # Sakha (Yakutia)
    "RU-SAK": "Asia",    # Sakhalin
    "RU-SVE": "Asia",    # Sverdlovsk         (BORDERLINE, Urals)
    "RU-TOM": "Asia",    # Tomsk
    "RU-TY": "Asia",     # Tuva
    "RU-TYU": "Asia",    # Tyumen             (BORDERLINE, Urals)
    "RU-YAN": "Asia",    # Yamalo-Nenets AO
    "RU-YEV": "Asia",    # Jewish AO
    "RU-ZAB": "Asia",    # Zabaykalsky Krai
    "RU-X01~": "Asia",   # NE's unnamed "Russia minor island" (BORDERLINE)
}


# ---------------------------------------------------------------------------
# 3. Subdivisions GEOGRAPHIC_OVERRIDES covers under codes NE does not use
# ---------------------------------------------------------------------------
# Natural Earth splits the Canary Islands into their two provinces and codes the
# Caribbean Netherlands as NL-BQ1..3 rather than BQ-*. Without these entries the
# islands would inherit Europe from their admin-0 unit.
NE_ADM1_GEOGRAPHIC_EXTRA = {
    # Canary Islands (GEOGRAPHIC_OVERRIDES has ES-CN, NE has the provinces)
    "ES-GC": "Africa",   # Las Palmas
    "ES-TF": "Africa",   # Santa Cruz de Tenerife
    # Caribbean Netherlands (GEOGRAPHIC_OVERRIDES has BQ-BO / BQ-SA / BQ-SE)
    "NL-BQ1": "South America",  # Bonaire (on the South American shelf)
    "NL-BQ2": "North America",  # Saba
    "NL-BQ3": "North America",  # Sint Eustatius
}

# Known gap: GEOGRAPHIC_OVERRIDES has YE-SU (Socotra
# -> Africa), but Natural Earth's admin-1 layer has no Socotra unit - the
# island sits inside YE-HD (Hadramawt). Overriding YE-HD would move mainland
# Hadramawt to Africa too, so Socotra stays Asia here.
