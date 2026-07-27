from tvdinner.playback_positions import load_playback_positions, save_playback_positions


def test_load_playback_positions_missing_file_is_not_an_error(tmp_path):
    positions, warnings = load_playback_positions(tmp_path / "does-not-exist.json")
    assert positions == {}
    assert warnings == []


def test_save_and_load_round_trips(tmp_path):
    path = tmp_path / "positions.json"
    recording = tmp_path / "Show_20260726-200000.ts"
    recording.write_bytes(b"x")

    save_playback_positions(path, {str(recording): 843.2})
    loaded, warnings = load_playback_positions(path)

    assert warnings == []
    assert loaded == {str(recording): 843.2}


def test_save_playback_positions_prunes_deleted_files(tmp_path):
    path = tmp_path / "positions.json"
    still_here = tmp_path / "Still Here_20260726-200000.ts"
    still_here.write_bytes(b"x")
    gone = str(tmp_path / "Gone_20260101-100000.ts")  # never created on disk

    save_playback_positions(path, {str(still_here): 100.0, gone: 200.0})
    loaded, _ = load_playback_positions(path)

    assert loaded == {str(still_here): 100.0}


def test_load_playback_positions_warns_on_malformed_json(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text("{not valid json")

    positions, warnings = load_playback_positions(path)
    assert positions == {}
    assert len(warnings) == 1


def test_load_playback_positions_warns_on_non_object_json(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text('["not", "an", "object"]')

    positions, warnings = load_playback_positions(path)
    assert positions == {}
    assert len(warnings) == 1


def test_load_playback_positions_skips_malformed_entry_but_keeps_others(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text('{"/a/good.ts": 12.5, "/a/bad.ts": "not a number", "/a/bool.ts": true}')

    positions, warnings = load_playback_positions(path)
    assert positions == {"/a/good.ts": 12.5}
    assert len(warnings) == 2
