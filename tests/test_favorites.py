import json
import stat
import sys

from tvdinner.favorites import load_favorites, remove_favorites_feed, save_favorites

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


def test_save_favorites_restricts_file_permissions(tmp_path):
    # feed is the raw playlist source string -- an Xtream/Stalker login
    # URL there carries real credentials -- so this file shouldn't be
    # left world-readable.
    path = tmp_path / "favorites.json"
    save_favorites(path, FEED, {"BBC One"})
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_favorites_preserves_other_feeds(tmp_path):
    path = tmp_path / "favorites.json"
    save_favorites(path, "https://a.example.com/list.m3u", {"A Channel"})
    save_favorites(path, "https://b.example.com/list.m3u", {"B Channel"})

    a_favorites, _ = load_favorites(path, "https://a.example.com/list.m3u")
    b_favorites, _ = load_favorites(path, "https://b.example.com/list.m3u")
    assert a_favorites == {"A Channel"}
    assert b_favorites == {"B Channel"}


def test_remove_favorites_feed_missing_file_is_a_no_op(tmp_path):
    remove_favorites_feed(tmp_path / "does-not-exist.json", FEED)  # must not raise


def test_remove_favorites_feed_deletes_the_key_not_just_the_list(tmp_path):
    path = tmp_path / "favorites.json"
    legacy_feed = "xtream://myuser:hunter2@panel.example.com:8080"
    save_favorites(path, legacy_feed, {"BBC One"})

    remove_favorites_feed(path, legacy_feed)

    data = json.loads(path.read_text())
    assert legacy_feed not in data
    assert "hunter2" not in path.read_text()  # the credential itself is gone, not just emptied


def test_remove_favorites_feed_preserves_other_feeds(tmp_path):
    path = tmp_path / "favorites.json"
    save_favorites(path, "https://a.example.com/list.m3u", {"A Channel"})
    save_favorites(path, "https://b.example.com/list.m3u", {"B Channel"})

    remove_favorites_feed(path, "https://a.example.com/list.m3u")

    a_favorites, _ = load_favorites(path, "https://a.example.com/list.m3u")
    b_favorites, _ = load_favorites(path, "https://b.example.com/list.m3u")
    assert a_favorites == set()
    assert b_favorites == {"B Channel"}


def test_remove_favorites_feed_missing_feed_is_a_no_op(tmp_path):
    path = tmp_path / "favorites.json"
    save_favorites(path, FEED, {"BBC One"})

    remove_favorites_feed(path, "https://not-a-real-feed.example.com/list.m3u")

    favorites, _ = load_favorites(path, FEED)
    assert favorites == {"BBC One"}


def test_save_favorites_can_remove_a_channel(tmp_path):
    path = tmp_path / "favorites.json"
    save_favorites(path, FEED, {"BBC One", "Channel 4"})
    save_favorites(path, FEED, {"BBC One"})

    loaded, _ = load_favorites(path, FEED)
    assert loaded == {"BBC One"}
