"""The sea hierarchy ships with the package, so a lookup needs no network."""

from metalog_geocoding.hierarchy import BUNDLED_HIERARCHY, SeaHierarchyCache


def _no_network(*args, **kwargs):
    raise AssertionError("the hierarchy went to MarineRegions")


def test_an_empty_cache_directory_still_has_every_chain(tmp_path, monkeypatch):
    """A fresh cache directory (a scratch dir, a new deployment) must not send
    the first sea lookups to MarineRegions."""
    cache = SeaHierarchyCache(tmp_path / "sea_hierarchy.tsv")
    monkeypatch.setattr(cache, "_get_json", _no_network)
    assert len(cache.cache) == 101
    assert cache.get_ocean(2374) == "Kattegat; Baltic Sea; North Atlantic Ocean; Atlantic Ocean"


def test_nothing_is_written_unless_something_was_fetched(tmp_path):
    """The bundled chains are not copied into every cache directory."""
    path = tmp_path / "sea_hierarchy.tsv"
    cache = SeaHierarchyCache(path)
    cache.get_ocean(2374)
    cache.save_cache()
    assert not path.exists()


def test_a_fetched_chain_is_kept_in_the_cache_directory(tmp_path, monkeypatch):
    """An MRGID the bundled file lacks (a new IHO edition) is fetched once and
    then read back from the cache directory, over the bundled chains."""
    path = tmp_path / "sea_hierarchy.tsv"
    cache = SeaHierarchyCache(path)
    monkeypatch.setattr(
        cache,
        "_query_api_for_ocean",
        lambda mrgid: {"hierarchy": "New Sea; Pacific Ocean", "topmost_mrgid": None, "topmost_name": None},
    )
    assert cache.get_ocean(999999) == "New Sea; Pacific Ocean"
    assert path.exists()

    reloaded = SeaHierarchyCache(path)
    assert reloaded.cache[999999] == "New Sea; Pacific Ocean"
    assert len(reloaded.cache) == 102


def test_the_bundled_file_is_package_data():
    assert BUNDLED_HIERARCHY.parent.name == "vocab"
    assert BUNDLED_HIERARCHY.read_text().startswith("mrgid\thierarchy\n")
