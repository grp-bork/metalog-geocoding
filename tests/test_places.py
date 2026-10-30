"""Place-name resolution and distances, against the real boundary layers.

Nominatim is a seeded cache plus a session that fails the test if it is
called, unless a test hands it answers.
"""

import pytest

from metalog_geocoding.nominatim import LOOKUP_TABLE, NominatimClient, PlaceCache
from metalog_geocoding.wikidata import TABLE as WIKIDATA_TABLE, WikidataClient
from metalog_geocoding.places import (
    name_matches,
    STATUS_LOOKUP_FAILED,
    STATUS_PENDING,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    LookupBudget,
    PlaceResolver,
)

pytestmark = pytest.mark.geo

DISKO_BAY = {
    "osm_type": "relation",
    "osm_id": 1,
    "category": "natural",
    "type": "bay",
    "display_name": "Qeqertarsuup Tunua",
    "name": "Qeqertarsuup Tunua",
    "namedetails": {"name": "Qeqertarsuup Tunua", "name:en": "Disko Bay", "name:da": "Disko Bugt"},
    "geojson": {
        "type": "Polygon",
        "coordinates": [[[-54, 68.5], [-50.5, 68.5], [-50.5, 70], [-54, 70], [-54, 68.5]]],
    },
}
CAR_PARK = {
    "osm_type": "way",
    "osm_id": 2,
    "category": "highway",
    "type": "service",
    "display_name": "Disko Bay Road",
    "geojson": {"type": "LineString", "coordinates": [[-1.5, 53.3], [-1.4, 53.3]]},
}


class NoNetwork:
    def get(self, *args, **kwargs):
        raise AssertionError("went to Nominatim")


class Down:
    def get(self, *args, **kwargs):
        import requests

        raise requests.ConnectionError("down")


@pytest.fixture
def nominatim(tmp_path):
    cache = PlaceCache(tmp_path / "places.sqlite")
    cache.put("Disko Bay", "", [CAR_PARK, DISKO_BAY])
    cache.put("Nowhere In Particular", "", [])
    # The literal reading goes to Nominatim before the qualifier is stripped.
    cache.put("Southern Spain", "", [])
    return NominatimClient(cache, session=NoNetwork(), min_interval_s=0)


@pytest.fixture
def resolver(geocoder, vocabulary, nominatim):
    return PlaceResolver(geocoder, vocabulary, nominatim)


def test_a_swapped_coordinate_is_far_from_the_country(resolver):
    res = resolver.resolve("Southern Spain")
    assert res.status == STATUS_RESOLVED
    assert res.interpreted_as == "Spain"
    (spain,) = res.candidates
    assert spain.distance_m(37.2, -3.6) == 0
    assert spain.distance_m(-3.6, 37.2) > 5_000_000


def test_every_unit_of_an_ambiguous_name_is_a_candidate(resolver):
    labels = {c.label for c in resolver.resolve("Georgia").candidates}
    assert labels == {"Georgia", "Georgia, United States"}


def test_a_country_hint_narrows_admin_1_units(resolver):
    labels = {c.label for c in resolver.resolve("Georgia", country="US").candidates}
    # The country Georgia is not narrowed: the hint is about admin-1 names.
    assert labels == {"Georgia", "Georgia, United States"}



def test_a_country_hint_drops_admin_1_units_elsewhere(geocoder, vocabulary):
    local_only = PlaceResolver(geocoder, vocabulary, None)
    (lara,) = local_only.resolve("Lara", country="VE").candidates
    assert lara.label == "Lara, Venezuela"
    assert local_only.resolve("Lara", country="NO").status == STATUS_UNRESOLVED


def test_a_two_letter_code_is_a_country(resolver):
    (spain,) = resolver.resolve("ES").candidates
    assert spain.label == "Spain" and spain.country_code == "ESP"
    (nz,) = resolver.resolve("NZ").candidates
    assert nz.label == "New Zealand"


def test_a_territory_is_its_own_polygon_once(resolver):
    (svalbard,) = resolver.resolve("Svalbard").candidates
    assert svalbard.distance_m(78.2, 15.6) == 0
    assert svalbard.distance_m(59.9, 10.7) > 1_000_000  # Oslo


def test_an_ocean_is_every_sea_in_its_chain(resolver):
    (atlantic,) = resolver.resolve("Atlantic Ocean").candidates
    assert atlantic.source == "iho"
    assert atlantic.distance_m(57.5, 20.0) == 0  # Baltic Sea
    assert atlantic.area_km2 > 50_000_000


def test_the_arctic_ocean_has_an_area(resolver):
    """IHO's polygon overshoots the pole, which made the area NaN."""
    (arctic,) = resolver.resolve("Arctic Ocean").candidates
    assert 10_000_000 < arctic.area_km2 < 20_000_000


def test_a_sea_alias_resolves_to_the_iho_polygon(resolver):
    (sea,) = resolver.resolve("Barentsz Sea").candidates
    assert sea.label == "Barents Sea"


def test_nominatim_answers_skip_streets(resolver):
    res = resolver.resolve("Disko Bay")
    (bay,) = res.candidates
    assert bay.source == "osm" and bay.id == "relation/1"
    assert bay.geometry_type == "polygon"
    assert bay.distance_m(69.2, -52.1) == 0
    assert bay.distance_m(-52.1, 69.2) > 10_000_000


def test_a_result_must_carry_the_querys_words():
    assert name_matches("Disko Bay", ["Qeqertarsuup Tunua", "Disko Bay"])
    assert name_matches("Atacama Desert", ["Desierto de Atacama"])
    assert name_matches("Loki's Castle", ["Lokis Castle"])
    assert name_matches("Peñahueca Lagoon", ["Laguna de Peñahueca"])
    assert not name_matches("Northern Baffin Bay", ["Northern Store"])
    assert not name_matches("Africa", ["South Africa"])
    assert not name_matches("Sierra Nevada", ["Nevada"])
    assert not name_matches("Blue Mountains", ["Blue"])
    assert name_matches("Kottenforst", ["Kottenforst"])
    assert not name_matches("Disko Bay", ["Disko Bay Road Association"])
    assert not name_matches("Indian", ["Indiana"])
    assert not name_matches("Lake", ["Lake Erie"])
    assert name_matches("Lake", ["lake"])
    assert not name_matches("", ["Lake Erie"])


def test_a_restaurant_named_after_a_place_is_not_a_candidate(geocoder, vocabulary, tmp_path):
    cache = PlaceCache(tmp_path / "places.sqlite")
    restaurant = {
        "osm_type": "node", "osm_id": 3, "category": "amenity", "type": "restaurant",
        "name": "Mekong River", "display_name": "Mekong River, Toronto",
        "geojson": {"type": "Point", "coordinates": [-79.5, 43.6]},
    }
    cache.put("Mekong River", "", [restaurant])
    client = NominatimClient(cache, session=NoNetwork(), min_interval_s=0)
    res = PlaceResolver(geocoder, vocabulary, client).resolve("Mekong River")
    assert res.status == STATUS_UNRESOLVED


def test_nothing_found_is_unresolved_not_far_away(resolver):
    res = resolver.resolve("Nowhere In Particular")
    assert res.status == STATUS_UNRESOLVED
    assert res.candidates == []


def test_an_uncached_name_past_the_budget_is_pending(resolver):
    res = resolver.resolve("Toolik Field Station", budget=LookupBudget(0))
    assert res.status == STATUS_PENDING


def test_an_unreachable_nominatim_is_reported_and_not_cached(geocoder, vocabulary, tmp_path, monkeypatch):
    monkeypatch.setattr("metalog_geocoding.nominatim.BACKOFF_S", 0.0)
    client = NominatimClient(PlaceCache(tmp_path / "p.sqlite"), session=Down(), min_interval_s=0)
    res = PlaceResolver(geocoder, vocabulary, client).resolve("Toolik Field Station")
    assert res.status == STATUS_LOOKUP_FAILED
    assert client.cached("Toolik Field Station") is None


def test_without_nominatim_only_local_layers_resolve(geocoder, vocabulary):
    resolver = PlaceResolver(geocoder, vocabulary, None)
    assert resolver.resolve("Chilean").status == STATUS_RESOLVED
    assert resolver.resolve("Disko Bay").status == STATUS_UNRESOLVED


ZUG_OUTLINE = {
    "osm_type": "relation", "osm_id": 1234, "category": "natural", "type": "water",
    "name": "Zugersee",
    "geojson": {"type": "Polygon", "coordinates": [[[8.4, 47.05], [8.55, 47.05], [8.55, 47.2], [8.4, 47.2], [8.4, 47.05]]]},
}


def _wikidata(cache, query, hits):
    cache.put(query, "", hits, table=WIKIDATA_TABLE, fields=None)


def test_wikidata_picks_the_place_and_osm_gives_its_outline(geocoder, vocabulary, tmp_path):
    cache = PlaceCache(tmp_path / "p.sqlite")
    _wikidata(cache, "Lake Zug", [
        {"id": "Q14422", "label": "Lake Zug", "description": "lake in Switzerland",
         "match": "Lake Zug", "aliases": [], "coord": [47.13, 8.48], "osm_relation": "1234"},
    ])
    cache.put("R1234", "", [ZUG_OUTLINE], table=LOOKUP_TABLE)
    resolver = PlaceResolver(
        geocoder, vocabulary,
        NominatimClient(cache, session=NoNetwork(), min_interval_s=0),
        WikidataClient(cache, session=NoNetwork(), min_interval_s=0),
    )
    (zug,) = resolver.resolve("Lake Zug").candidates
    assert zug.source == "wikidata"
    assert zug.geometry_type == "polygon"
    assert zug.distance_m(47.12, 8.5) == 0
    assert zug.distance_m(8.5, 47.12) > 3_000_000


def test_a_wikidata_label_that_is_a_local_place_uses_the_local_polygon(geocoder, vocabulary, tmp_path):
    cache = PlaceCache(tmp_path / "p.sqlite")
    _wikidata(cache, "Atlantic", [
        {"id": "Q97", "label": "Atlantic Ocean", "description": "ocean", "match": "Atlantic",
         "aliases": ["Atlantic"], "coord": [0.0, -25.0], "osm_relation": None},
    ])
    resolver = PlaceResolver(
        geocoder, vocabulary, None, WikidataClient(cache, session=NoNetwork(), min_interval_s=0),
    )
    (atlantic,) = resolver.resolve("Atlantic").candidates
    assert atlantic.source == "iho" and atlantic.label == "Atlantic Ocean"


def test_a_wikidata_hit_without_a_coordinate_or_matching_name_is_skipped(geocoder, vocabulary, tmp_path):
    cache = PlaceCache(tmp_path / "p.sqlite")
    _wikidata(cache, "Pixian", [
        {"id": "Q2", "label": "Pixian railway station", "description": "station",
         "match": "Pixian railway station", "aliases": [], "coord": [30.8, 103.9], "osm_relation": None},
        {"id": "Q3", "label": "Pixian", "description": "article", "match": "Pixian",
         "aliases": [], "coord": None, "osm_relation": None},
    ])
    resolver = PlaceResolver(
        geocoder, vocabulary, None, WikidataClient(cache, session=NoNetwork(), min_interval_s=0),
    )
    assert resolver.resolve("Pixian").status == STATUS_UNRESOLVED


def test_two_letter_codes_include_us_states(resolver):
    labels = {c.label for c in resolver.resolve("CA").candidates}
    assert labels == {"Canada", "California, United States"}
    (nl,) = resolver.resolve("NL").candidates[:1]
    assert nl.label == "Netherlands"


def test_a_state_inside_the_country_found_is_not_repeated(resolver):
    labels = [c.label for c in resolver.resolve("Mexico").candidates]
    assert labels[0] == "Mexico"
    assert not any(", Mexico" in label for label in labels)


def _hit(qid, label, description, coord, sitelinks, aliases=(), relation=None):
    return {"id": qid, "label": label, "description": description, "match": label,
            "aliases": list(aliases), "coord": coord, "osm_relation": relation,
            "sitelinks": sitelinks}


def _wikidata_only(geocoder, vocabulary, tmp_path, query, hits):
    cache = PlaceCache(tmp_path / "p.sqlite")
    _wikidata(cache, query, hits)
    return PlaceResolver(
        geocoder, vocabulary, None, WikidataClient(cache, session=NoNetwork(), min_interval_s=0),
    )


def test_wikidata_hits_are_ranked_by_prominence(geocoder, vocabulary, tmp_path):
    resolver = _wikidata_only(geocoder, vocabulary, tmp_path, "Melbourne", [
        _hit("Q949779", "Melbourne", "city in Florida", [28.1, -80.6], 40),
        _hit("Q3141", "Melbourne", "capital city of Victoria, Australia", [-37.8, 144.96], 230),
    ])
    (melbourne,) = resolver.resolve("Melbourne").candidates
    assert melbourne.id == "Q3141"


def test_rivals_of_similar_prominence_are_kept(geocoder, vocabulary, tmp_path):
    resolver = _wikidata_only(geocoder, vocabulary, tmp_path, "Stanley", [
        _hit("Q1", "Stanley", "town in County Durham", [54.87, -1.7], 30),
        _hit("Q2", "Stanley", "capital of the Falkland Islands", [-51.7, -57.85], 60),
        _hit("Q3", "Stanley", "hamlet", [40.0, -80.0], 5),
    ])
    assert [c.id for c in resolver.resolve("Stanley").candidates] == ["Q2", "Q1"]


def test_an_alias_names_the_item(geocoder, vocabulary, tmp_path):
    resolver = _wikidata_only(geocoder, vocabulary, tmp_path, "Midwest", [
        _hit("Q186545", "Midwestern United States", "US region", [42.0, -93.0], 80, aliases=["Midwest"]),
        _hit("Q1028671", "Midwest", "town in Wyoming", [43.4, -106.3], 10),
    ])
    assert resolver.resolve("Midwest").candidates[0].id == "Q186545"


def test_an_item_that_is_not_a_place_is_skipped(geocoder, vocabulary, tmp_path):
    resolver = _wikidata_only(geocoder, vocabulary, tmp_path, "Tibetan", [
        _hit("Q34271", "Tibetan", "language of Tibet", [31.0, 88.0], 150),
    ])
    assert resolver.resolve("Tibetan").status == STATUS_UNRESOLVED


def test_a_natural_earth_region_gives_a_wikidata_item_its_outline(geocoder, vocabulary, tmp_path):
    from metalog_geocoding.places import REGIONS_LAYER

    if not (geocoder.data_dir / REGIONS_LAYER).exists():
        pytest.skip("optional regions layer not installed")
    resolver = _wikidata_only(geocoder, vocabulary, tmp_path, "Andes", [
        _hit("Q5456", "Andes", "mountain range in South America", [-32.65, -70.01], 200),
    ])
    (andes,) = resolver.resolve("Andes").candidates
    assert andes.source == "natural_earth_region"
    assert andes.geometry_type == "polygon"
    assert andes.distance_m(-16.5, -68.15) == 0  # La Paz
    assert andes.distance_m(-68.15, -16.5) > 2_000_000  # Weddell Sea


def test_a_wikidata_country_is_its_natural_earth_polygon(geocoder, vocabulary, tmp_path):
    resolver = _wikidata_only(geocoder, vocabulary, tmp_path, "Mainland China", [
        _hit("Q148", "People's Republic of China", "country in East Asia", [35.0, 103.0], 300,
             aliases=["Mainland China"]),
    ])
    (china,) = resolver.resolve("Mainland China").candidates
    assert china.source == "natural_earth_admin0" and china.label == "China"


def test_a_natural_earth_marine_area_gives_a_wikidata_item_its_outline(geocoder, vocabulary, tmp_path):
    from metalog_geocoding.places import MARINE_LAYER

    if not (geocoder.data_dir / MARINE_LAYER).exists():
        pytest.skip("optional marine layer not installed")
    resolver = _wikidata_only(geocoder, vocabulary, tmp_path, "Disko Bay", [
        _hit("Q1135227", "Disko Bay", "bay in Greenland", [69.0, -52.0], 40),
    ])
    (disko,) = resolver.resolve("Disko Bay").candidates
    assert disko.source == "natural_earth_marine"
    assert disko.geometry_type == "polygon"
    assert disko.distance_m(69.0, -52.0) == 0
    assert disko.distance_m(-52.0, 69.0) > 5_000_000
