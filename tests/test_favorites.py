from tvdinner.favorites import load_favorites, save_favorites

FEED = "https://example.com/playlist.m3u"


def test_load_favorites_missing_file_is_not_an_error(tmp_path):
    favorites, warnings = load_favorites(tmp_path / "does-not-exist.json", FEED)
    assert favorites == set()
    assert warnings == []


def test_load_favorites_parses_valid_entries(tmp_path):
    path = tmp_path / "favorites.json"
    path.write_text(f'{{"{FEED}": ["BBC One", "Channel 4"]}}')

    favorites, warnings = load_favorites(path, FEED)
    assert favorites == {"BBC One", "Channel 4"}
    assert warnings == []


def test_load_favorites_returns_empty_for_unknown_feed(tmp_path):
    path = tmp_path / "favorites.json"
    path.write_text('{"https://other.example.com/list.m3u": ["Fox News"]}')

    favorites, warnings = load_favorites(path, FEED)
    assert favorites == set()
    assert warnings == []


def test_load_favorites_warns_on_malformed_json(tmp_path):
    path = tmp_path / "favorites.json"
    path.write_text("{not valid json")

    favorites, warnings = load_favorites(path, FEED)
    assert favorites == set()
    assert len(warnings) == 1


def test_load_favorites_warns_on_non_object_json(tmp_path):
    path = tmp_path / "favorites.json"
    path.write_text('["not", "an", "object"]')

    favorites, warnings = load_favorites(path, FEED)
    assert favorites == set()
    assert len(warnings) == 1


def test_load_favorites_warns_on_non_list_entry(tmp_path):
    path = tmp_path / "favorites.json"
    path.write_text(f'{{"{FEED}": "not a list"}}')

    favorites, warnings = load_favorites(path, FEED)
    assert favorites == set()
    assert len(warnings) == 1


def test_save_favorites_round_trips_through_load_favorites(tmp_path):
    path = tmp_path / "nested" / "favorites.json"
    favorites = {"BBC One", "Channel 4"}

    save_favorites(path, FEED, favorites)
    loaded, warnings = load_favorites(path, FEED)

    assert loaded == favorites
    assert warnings == []


def test_save_favorites_preserves_other_feeds(tmp_path):
    path = tmp_path / "favorites.json"
    save_favorites(path, "https://a.example.com/list.m3u", {"A Channel"})
    save_favorites(path, "https://b.example.com/list.m3u", {"B Channel"})

    a_favorites, _ = load_favorites(path, "https://a.example.com/list.m3u")
    b_favorites, _ = load_favorites(path, "https://b.example.com/list.m3u")
    assert a_favorites == {"A Channel"}
    assert b_favorites == {"B Channel"}


def test_save_favorites_can_remove_a_channel(tmp_path):
    path = tmp_path / "favorites.json"
    save_favorites(path, FEED, {"BBC One", "Channel 4"})
    save_favorites(path, FEED, {"BBC One"})

    loaded, _ = load_favorites(path, FEED)
    assert loaded == {"BBC One"}
