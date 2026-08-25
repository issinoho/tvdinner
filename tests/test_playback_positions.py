import json
import time
from datetime import timedelta

from tvdinner.playback_positions import (
    load_playback_positions,
    playback_position_timestamps_path_for,
    save_playback_positions,
)


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


def test_save_playback_positions_keeps_remote_vod_urls(tmp_path):
    # A VOD resume key is a remote stream URL, not a local file -- there's
    # no file on disk to check Path.exists() against, so it must never be
    # pruned just because it "doesn't exist" locally (see
    # save_playback_positions's own docstring). It's still subject to
    # max_age-based pruning -- see the dedicated tests for that below --
    # but a fresh save (nothing recorded as stale yet) always keeps it.
    path = tmp_path / "positions.json"
    url = "http://panel.example.com/movie/user/pass/123.mp4"

    save_playback_positions(path, {url: 812.4})
    loaded, _ = load_playback_positions(path)

    assert loaded == {url: 812.4}


def test_save_playback_positions_keeps_https_vod_urls_too(tmp_path):
    path = tmp_path / "positions.json"
    url = "https://panel.example.com/movie/user/pass/123.mp4"

    save_playback_positions(path, {url: 42.0})
    loaded, _ = load_playback_positions(path)

    assert loaded == {url: 42.0}


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


def test_save_playback_positions_prunes_a_remote_url_untouched_past_max_age(tmp_path):
    path = tmp_path / "positions.json"
    url = "http://panel.example.com/movie/user/pass/123.mp4"
    stale_time = time.time() - timedelta(days=91).total_seconds()
    playback_position_timestamps_path_for(path).write_text(json.dumps({url: stale_time}))

    save_playback_positions(path, {url: 812.4}, max_age=timedelta(days=90))
    loaded, _ = load_playback_positions(path)

    assert loaded == {}


def test_save_playback_positions_keeps_a_remote_url_touched_within_max_age(tmp_path):
    path = tmp_path / "positions.json"
    url = "http://panel.example.com/movie/user/pass/123.mp4"
    recent_time = time.time() - timedelta(days=1).total_seconds()
    playback_position_timestamps_path_for(path).write_text(json.dumps({url: recent_time}))

    save_playback_positions(path, {url: 812.4}, max_age=timedelta(days=90))
    loaded, _ = load_playback_positions(path)

    assert loaded == {url: 812.4}


def test_save_playback_positions_touched_key_refreshes_its_timestamp(tmp_path):
    # A key resumed/updated (touched_key) is never expired, no matter how
    # old its previously recorded timestamp was -- resuming an item is
    # exactly the case max_age exists to not interfere with.
    path = tmp_path / "positions.json"
    url = "http://panel.example.com/movie/user/pass/123.mp4"
    stale_time = time.time() - timedelta(days=91).total_seconds()
    playback_position_timestamps_path_for(path).write_text(json.dumps({url: stale_time}))

    save_playback_positions(path, {url: 900.0}, touched_key=url, max_age=timedelta(days=90))
    loaded, _ = load_playback_positions(path)

    assert loaded == {url: 900.0}


def test_save_playback_positions_does_not_expire_a_remote_url_seen_for_the_first_time(tmp_path):
    # No prior timestamp recorded (e.g. upgrading from a version that
    # predates this file, or simply the first save of a new key) must be
    # stamped as touched now, not treated as already-expired -- otherwise
    # a max_age of 0 would immediately prune every existing resume
    # position on the next save, which would be indistinguishable from
    # data loss to the user.
    path = tmp_path / "positions.json"
    url = "http://panel.example.com/movie/user/pass/123.mp4"

    save_playback_positions(path, {url: 812.4}, max_age=timedelta(days=90))
    loaded, _ = load_playback_positions(path)

    assert loaded == {url: 812.4}


def test_save_playback_positions_drops_timestamp_for_a_key_no_longer_present(tmp_path):
    path = tmp_path / "positions.json"
    url = "http://panel.example.com/movie/user/pass/123.mp4"

    save_playback_positions(path, {url: 812.4})
    save_playback_positions(path, {})  # e.g. the item finished and was popped

    timestamps = json.loads(playback_position_timestamps_path_for(path).read_text())
    assert timestamps == {}


def test_save_playback_positions_leaves_local_recording_timestamps_untracked(tmp_path):
    # A local recording is pruned by file existence alone (see
    # test_save_playback_positions_prunes_deleted_files) -- it should
    # never show up in the sibling timestamps file, which only tracks
    # remote (VOD) keys.
    path = tmp_path / "positions.json"
    recording = tmp_path / "Show_20260726-200000.ts"
    recording.write_bytes(b"x")

    save_playback_positions(path, {str(recording): 843.2})

    timestamps = json.loads(playback_position_timestamps_path_for(path).read_text())
    assert timestamps == {}
