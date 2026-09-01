import stat
import sys

import pytest

from tvdinner.bookmarks import (
    Bookmark,
    BookmarkError,
    bookmark_to_dict,
    find_bookmark,
    load_bookmarks,
    remove_bookmark,
    save_bookmarks,
    upsert_bookmark,
)


def test_load_bookmarks_missing_file_is_not_an_error(tmp_path):
    bookmarks, warnings = load_bookmarks(tmp_path / "does-not-exist.json")
    assert bookmarks == []
    assert warnings == []


def test_load_bookmarks_parses_valid_entries(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_text(
        '[{"name": "My Provider", "url": "https://example.com/playlist.m3u", '
        '"epg": "https://example.com/guide.xml"}]'
    )

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == [
        Bookmark(name="My Provider", url="https://example.com/playlist.m3u", epg="https://example.com/guide.xml")
    ]
    assert warnings == []


def test_load_bookmarks_defaults_missing_epg_to_none(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_text('[{"name": "No EPG", "url": "test.m3u"}]')

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == [Bookmark(name="No EPG", url="test.m3u", epg=None)]
    assert warnings == []


def test_load_bookmarks_parses_channel_field(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_text('[{"name": "News", "url": "news.m3u", "channel": "CNN"}]')

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == [Bookmark(name="News", url="news.m3u", channel="CNN")]
    assert warnings == []


def test_load_bookmarks_defaults_missing_channel_to_none_for_old_files(tmp_path):
    # Bookmarks saved before the channel field existed shouldn't be treated
    # as malformed -- just missing the (optional) field entirely.
    path = tmp_path / "bookmarks.json"
    path.write_text('[{"name": "Old Entry", "url": "old.m3u", "epg": null}]')

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == [Bookmark(name="Old Entry", url="old.m3u", channel=None)]
    assert warnings == []


def test_load_bookmarks_parses_tmdb_api_token_field(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_text('[{"name": "Movies", "url": "movies.m3u", "tmdb_api_token": "secret-token"}]')

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == [Bookmark(name="Movies", url="movies.m3u", tmdb_api_token="secret-token")]
    assert warnings == []


def test_load_bookmarks_defaults_missing_tmdb_api_token_to_none_for_old_files(tmp_path):
    # Bookmarks saved before the tmdb_api_token field existed shouldn't be
    # treated as malformed -- just missing the (optional) field entirely.
    path = tmp_path / "bookmarks.json"
    path.write_text('[{"name": "Old Entry", "url": "old.m3u"}]')

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == [Bookmark(name="Old Entry", url="old.m3u", tmdb_api_token=None)]
    assert warnings == []


def test_load_bookmarks_warns_on_malformed_json(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_text("[not valid json")

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == []
    assert len(warnings) == 1


def test_load_bookmarks_warns_on_non_array_json(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_text('{"not": "an array"}')

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == []
    assert len(warnings) == 1


def test_load_bookmarks_skips_malformed_entry_with_a_warning(tmp_path):
    path = tmp_path / "bookmarks.json"
    path.write_text('[{"name": "Good", "url": "good.m3u"}, {"name": "Missing URL"}]')

    bookmarks, warnings = load_bookmarks(path)
    assert bookmarks == [Bookmark(name="Good", url="good.m3u", epg=None)]
    assert len(warnings) == 1


def test_save_bookmarks_round_trips_through_load_bookmarks(tmp_path):
    path = tmp_path / "nested" / "bookmarks.json"
    bookmarks = [
        Bookmark(
            name="A",
            url="https://a.example.com/list.m3u",
            epg="https://a.example.com/guide.xml",
            channel="CNN",
            tmdb_api_token="secret-token",
        ),
        Bookmark(name="B", url="b.m3u"),
    ]

    save_bookmarks(path, bookmarks)
    loaded, warnings = load_bookmarks(path)

    assert loaded == bookmarks
    assert warnings == []


def test_save_bookmarks_restricts_file_permissions(tmp_path):
    # A bookmark's own url can carry an Xtream/Stalker login's
    # credentials or a Plex token, and tmdb_api_token is a real one --
    # this file shouldn't be left world-readable.
    path = tmp_path / "bookmarks.json"
    save_bookmarks(path, [Bookmark(name="A", url="xtream://user:pass@host:80")])
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_bookmarks_preserves_order(tmp_path):
    path = tmp_path / "bookmarks.json"
    bookmarks = [Bookmark(name=f"Entry {i}", url=f"{i}.m3u") for i in range(5)]

    save_bookmarks(path, bookmarks)
    loaded, _ = load_bookmarks(path)

    assert [b.name for b in loaded] == [f"Entry {i}" for i in range(5)]


def test_save_bookmarks_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "bookmarks.json"
    save_bookmarks(path, [Bookmark(name="A", url="a.m3u")])

    assert [p.name for p in tmp_path.iterdir()] == ["bookmarks.json"]


def test_save_bookmarks_overwrites_atomically(tmp_path):
    path = tmp_path / "bookmarks.json"
    save_bookmarks(path, [Bookmark(name="A", url="a.m3u")])
    save_bookmarks(path, [Bookmark(name="B", url="b.m3u"), Bookmark(name="C", url="c.m3u")])

    loaded, warnings = load_bookmarks(path)
    assert [b.name for b in loaded] == ["B", "C"]
    assert warnings == []


def test_bookmark_to_dict_is_the_on_disk_shape():
    bookmark = Bookmark(name="A", url="a.m3u", epg="g.xml", channel="CNN", tmdb_api_token="tok")
    assert bookmark_to_dict(bookmark) == {
        "name": "A",
        "url": "a.m3u",
        "epg": "g.xml",
        "channel": "CNN",
        "tmdb_api_token": "tok",
    }


def _sample_bookmarks() -> list[Bookmark]:
    return [
        Bookmark(name="First", url="1.m3u"),
        Bookmark(name="Second", url="2.m3u"),
        Bookmark(name="Third", url="3.m3u"),
    ]


def test_find_bookmark_by_exact_name():
    bookmarks = _sample_bookmarks()
    assert find_bookmark(bookmarks, "Second") == (1, bookmarks[1])


def test_find_bookmark_by_one_based_index():
    bookmarks = _sample_bookmarks()
    assert find_bookmark(bookmarks, "1") == (0, bookmarks[0])
    assert find_bookmark(bookmarks, "3") == (2, bookmarks[2])


def test_find_bookmark_numeric_key_out_of_range_is_none():
    assert find_bookmark(_sample_bookmarks(), "0") is None
    assert find_bookmark(_sample_bookmarks(), "4") is None


def test_find_bookmark_numeric_key_is_never_matched_as_a_name():
    bookmarks = [Bookmark(name="2", url="two.m3u"), Bookmark(name="Other", url="other.m3u")]
    # "2" resolves as the 2nd position, not the row literally named "2".
    assert find_bookmark(bookmarks, "2") == (1, bookmarks[1])


def test_find_bookmark_unknown_name_is_none():
    assert find_bookmark(_sample_bookmarks(), "Nope") is None


def test_upsert_bookmark_appends_a_new_row():
    bookmarks = _sample_bookmarks()
    new = Bookmark(name="Fourth", url="4.m3u")

    updated, replaced = upsert_bookmark(bookmarks, new)

    assert replaced is False
    assert [b.name for b in updated] == ["First", "Second", "Third", "Fourth"]
    assert bookmarks == _sample_bookmarks()  # input not mutated


def test_upsert_bookmark_duplicate_name_raises_without_replace():
    with pytest.raises(BookmarkError, match="already exists"):
        upsert_bookmark(_sample_bookmarks(), Bookmark(name="Second", url="new.m3u"))


def test_upsert_bookmark_replace_swaps_in_place_keeping_position():
    bookmarks = _sample_bookmarks()
    updated, replaced = upsert_bookmark(
        bookmarks, Bookmark(name="Second", url="new.m3u", epg="g.xml"), replace=True
    )

    assert replaced is True
    assert [b.name for b in updated] == ["First", "Second", "Third"]
    assert updated[1] == Bookmark(name="Second", url="new.m3u", epg="g.xml")


def test_remove_bookmark_by_name():
    updated, removed = remove_bookmark(_sample_bookmarks(), "Second")

    assert removed.name == "Second"
    assert [b.name for b in updated] == ["First", "Third"]


def test_remove_bookmark_by_index():
    updated, removed = remove_bookmark(_sample_bookmarks(), "1")

    assert removed.name == "First"
    assert [b.name for b in updated] == ["Second", "Third"]


def test_remove_bookmark_no_match_raises():
    with pytest.raises(BookmarkError, match="No bookmark matches"):
        remove_bookmark(_sample_bookmarks(), "Nope")
