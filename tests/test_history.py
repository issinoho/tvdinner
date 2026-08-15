from datetime import datetime, timedelta, timezone

from tvdinner.history import HistoryEntry, append_history_entry, load_history


def _entry(**overrides) -> HistoryEntry:
    started_at = overrides.pop("started_at", datetime(2026, 8, 15, 20, 0, 0, tzinfo=timezone.utc))
    duration = overrides.pop("duration_seconds", 60.0)
    defaults = dict(
        kind="channel",
        title="BBC One",
        url="http://example.com/live/1.ts",
        playlist_source="http://example.com/playlist.m3u",
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration),
    )
    defaults.update(overrides)
    return HistoryEntry(**defaults)


def test_load_history_missing_file_is_not_an_error(tmp_path):
    entries, warnings = load_history(tmp_path / "does-not-exist.jsonl")
    assert entries == []
    assert warnings == []


def test_append_and_load_round_trips(tmp_path):
    path = tmp_path / "history.jsonl"
    entry = _entry()

    append_history_entry(path, entry)
    loaded, warnings = load_history(path)

    assert warnings == []
    assert len(loaded) == 1
    assert loaded[0].kind == "channel"
    assert loaded[0].title == "BBC One"
    assert loaded[0].url == "http://example.com/live/1.ts"
    assert loaded[0].playlist_source == "http://example.com/playlist.m3u"
    assert loaded[0].started_at == entry.started_at
    assert loaded[0].ended_at == entry.ended_at
    assert loaded[0].duration_seconds == 60.0


def test_append_multiple_entries_accumulates_as_separate_lines(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history_entry(path, _entry(title="BBC One"))
    append_history_entry(path, _entry(title="BBC Two"))

    loaded, warnings = load_history(path)

    assert warnings == []
    assert [entry.title for entry in loaded] == ["BBC One", "BBC Two"]
    assert len(path.read_text().splitlines()) == 2


def test_append_drops_entries_shorter_than_the_minimum_duration(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history_entry(path, _entry(duration_seconds=2.0))

    assert not path.exists()
    loaded, warnings = load_history(path)
    assert loaded == []
    assert warnings == []


def test_append_keeps_entries_at_or_above_the_minimum_duration(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history_entry(path, _entry(duration_seconds=5.0))

    loaded, _ = load_history(path)
    assert len(loaded) == 1


def test_playlist_source_none_round_trips(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history_entry(path, _entry(kind="vod", title="A Movie", playlist_source=None))

    loaded, warnings = load_history(path)
    assert warnings == []
    assert loaded[0].playlist_source is None


def test_load_history_skips_malformed_line_but_keeps_others(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history_entry(path, _entry(title="Good Entry"))
    with path.open("a") as f:
        f.write("not valid json\n")

    loaded, warnings = load_history(path)
    assert [entry.title for entry in loaded] == ["Good Entry"]
    assert len(warnings) == 1


def test_load_history_skips_blank_lines(tmp_path):
    path = tmp_path / "history.jsonl"
    append_history_entry(path, _entry(title="Good Entry"))
    with path.open("a") as f:
        f.write("\n")

    loaded, warnings = load_history(path)
    assert len(loaded) == 1
    assert warnings == []
