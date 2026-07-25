from tvdinner.bookmarks import Bookmark, load_bookmarks, save_bookmarks


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
            name="A", url="https://a.example.com/list.m3u", epg="https://a.example.com/guide.xml", channel="CNN"
        ),
        Bookmark(name="B", url="b.m3u"),
    ]

    save_bookmarks(path, bookmarks)
    loaded, warnings = load_bookmarks(path)

    assert loaded == bookmarks
    assert warnings == []


def test_save_bookmarks_preserves_order(tmp_path):
    path = tmp_path / "bookmarks.json"
    bookmarks = [Bookmark(name=f"Entry {i}", url=f"{i}.m3u") for i in range(5)]

    save_bookmarks(path, bookmarks)
    loaded, _ = load_bookmarks(path)

    assert [b.name for b in loaded] == [f"Entry {i}" for i in range(5)]
