import json
from datetime import datetime, timezone

from tvdinner.schedule import ScheduledRecording, load_schedule, save_schedule

# Fixed, far from the real clock in both directions so these tests never
# depend on (or are flaky relative to) whatever day/time they actually run.
FAR_FUTURE_START = datetime(2099, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
FAR_FUTURE_STOP = datetime(2099, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
FAR_PAST_START = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
FAR_PAST_STOP = datetime(2000, 1, 1, 13, 0, 0, tzinfo=timezone.utc)


def _entry(start=FAR_FUTURE_START, stop=FAR_FUTURE_STOP, title="Live Test Broadcast"):
    return ScheduledRecording.create(
        channel_url="http://stream/demo",
        channel_name="Demo News",
        title=title,
        start=start,
        stop=stop,
    )


def test_load_schedule_missing_file_is_not_an_error(tmp_path):
    schedules, warnings = load_schedule(tmp_path / "does-not-exist.json")
    assert schedules == []
    assert warnings == []


def test_save_and_load_round_trips(tmp_path):
    path = tmp_path / "schedule.json"
    entry = _entry()

    save_schedule(path, [entry])
    loaded, warnings = load_schedule(path)

    assert warnings == []
    assert len(loaded) == 1
    assert loaded[0].id == entry.id
    assert loaded[0].channel_url == entry.channel_url
    assert loaded[0].channel_name == entry.channel_name
    assert loaded[0].title == entry.title
    assert loaded[0].start == entry.start
    assert loaded[0].stop == entry.stop


def test_load_schedule_drops_entries_whose_stop_time_has_passed(tmp_path):
    path = tmp_path / "schedule.json"
    expired = _entry(start=FAR_PAST_START, stop=FAR_PAST_STOP, title="Expired Show")
    upcoming = _entry(title="Upcoming Show")
    save_schedule(path, [expired, upcoming])

    loaded, warnings = load_schedule(path)

    assert warnings == []
    assert [s.title for s in loaded] == ["Upcoming Show"]


def test_load_schedule_warns_on_malformed_json(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text("{not valid json")

    schedules, warnings = load_schedule(path)
    assert schedules == []
    assert len(warnings) == 1


def test_load_schedule_warns_on_non_array_json(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text('{"not": "an array"}')

    schedules, warnings = load_schedule(path)
    assert schedules == []
    assert len(warnings) == 1


def test_load_schedule_skips_malformed_entry_but_keeps_others(tmp_path):
    path = tmp_path / "schedule.json"
    good = _entry(title="Good Entry")
    malformed = {"id": "abc"}  # missing required fields
    valid = {
        "id": good.id,
        "channel_url": good.channel_url,
        "channel_name": good.channel_name,
        "title": good.title,
        "start": good.start.isoformat(),
        "stop": good.stop.isoformat(),
    }
    path.write_text(json.dumps([malformed, valid]))

    schedules, warnings = load_schedule(path)
    assert len(warnings) == 1
    assert [s.title for s in schedules] == ["Good Entry"]


def test_save_schedule_preserves_list_order(tmp_path):
    path = tmp_path / "schedule.json"
    first = _entry(title="First")
    second = _entry(
        title="Second",
        start=datetime(2099, 1, 1, 14, 0, 0, tzinfo=timezone.utc),
        stop=datetime(2099, 1, 1, 15, 0, 0, tzinfo=timezone.utc),
    )

    save_schedule(path, [first, second])
    loaded, _ = load_schedule(path)

    assert [s.title for s in loaded] == ["First", "Second"]
