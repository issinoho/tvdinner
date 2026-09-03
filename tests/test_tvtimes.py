from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from tvdinner.history import HistoryEntry
from tvdinner.schedule import WATCHLIST_SOURCE, ScheduledRecording
from tvdinner.tvtimes import (
    MAX_DEVICE_NAME,
    TvtimesFeed,
    WatchlistEntry,
    channel_id_from_stream_url,
    fetch_tvtimes_favourites,
    fetch_tvtimes_watchlist,
    post_watch_events,
    parse_favourites,
    watch_events_payload,
    is_tvtimes_url,
    parse_tvtimes_url,
    parse_watchlist,
    tvtimes_epg_url,
    tvtimes_favourites_url,
    tvtimes_playlist_url,
    tvtimes_watchlist_url,
    watchlist_schedule_updates,
)


def test_is_tvtimes_url_matches_both_schemes():
    assert is_tvtimes_url("tvtimes://tv.example.com?token=abc")
    assert is_tvtimes_url("tvtimess://tv.example.com?token=abc")
    assert not is_tvtimes_url("https://tv.example.com/api/exports/playlist.m3u?token=abc")
    assert not is_tvtimes_url("xtream://user:pass@panel.example.com:8080")


def test_parse_tvtimes_url_http_and_https_schemes():
    assert parse_tvtimes_url("tvtimes://192.168.1.5:8888?token=abc") == TvtimesFeed(
        base_url="http://192.168.1.5:8888", token="abc"
    )
    assert parse_tvtimes_url("tvtimess://tv.example.com?token=abc") == TvtimesFeed(
        base_url="https://tv.example.com", token="abc"
    )


def test_parse_tvtimes_url_keeps_a_reverse_proxy_base_path():
    feed = parse_tvtimes_url("tvtimess://example.com/tv/?token=abc")
    assert feed == TvtimesFeed(base_url="https://example.com/tv", token="abc")
    assert tvtimes_playlist_url(feed) == "https://example.com/tv/api/exports/playlist.m3u?token=abc"


def test_parse_tvtimes_url_rejects_missing_pieces():
    # a malformed tvtimes:// URL is a usage error, not something to fall
    # back to treating as a direct stream
    assert parse_tvtimes_url("tvtimes://tv.example.com") is None  # no token
    assert parse_tvtimes_url("tvtimes://tv.example.com?token=") is None
    assert parse_tvtimes_url("tvtimes://?token=abc") is None  # no host
    assert parse_tvtimes_url("https://tv.example.com?token=abc") is None  # wrong scheme


def test_playlist_and_epg_urls_are_the_two_export_feeds():
    feed = TvtimesFeed(base_url="https://tv.example.com", token="s3cret")
    assert tvtimes_playlist_url(feed) == "https://tv.example.com/api/exports/playlist.m3u?token=s3cret"
    assert tvtimes_epg_url(feed) == "https://tv.example.com/api/exports/epg.xml?token=s3cret"


def test_token_is_url_encoded_into_the_query():
    feed = parse_tvtimes_url("tvtimess://tv.example.com?token=a%2Fb%20c")
    assert feed is not None and feed.token == "a/b c"
    assert tvtimes_playlist_url(feed).endswith("?token=a%2Fb%20c")


# --- watchlist feed ------------------------------------------------------


def _entry(url="http://tv/api/exports/stream/c1?token=t", start_hour=20, title="Film"):
    start = datetime(2026, 9, 3, start_hour, 0, tzinfo=timezone.utc)
    return WatchlistEntry(
        channel_url=url,
        channel_name="BBC One",
        title=title,
        start=start,
        stop=start + timedelta(hours=1),
    )


def test_tvtimes_watchlist_url_is_the_third_export_feed():
    feed = TvtimesFeed(base_url="https://tv.example.com", token="s3cret")
    assert tvtimes_watchlist_url(feed) == (
        "https://tv.example.com/api/exports/watchlist.json?token=s3cret"
    )


def test_parse_watchlist_skips_malformed_rows_without_losing_the_batch():
    payload = [
        {
            "channel_url": "http://tv/stream/1",
            "channel_name": "BBC One",
            "title": "The News",
            "start": "2026-09-03T20:00:00+00:00",
            "stop": "2026-09-03T21:00:00+00:00",
        },
        {"channel_url": "http://tv/stream/2"},  # no times
        {"start": "2026-09-03T20:00:00+00:00", "stop": "x"},  # unparseable
        "not a dict",
        {
            "channel_url": "",  # blank url is useless for matching a channel
            "start": "2026-09-03T20:00:00+00:00",
            "stop": "2026-09-03T21:00:00+00:00",
        },
    ]
    entries = parse_watchlist(payload)
    assert [e.title for e in entries] == ["The News"]
    assert entries[0].start == datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)


def test_parse_watchlist_tolerates_a_non_list_body():
    assert parse_watchlist({"error": "nope"}) == []


def test_watchlist_updates_schedules_new_airings():
    updated, added, removed = watchlist_schedule_updates([], [_entry(), _entry(start_hour=22)])
    assert (added, removed) == (2, 0)
    assert len(updated) == 2
    assert all(r.source == WATCHLIST_SOURCE for r in updated)


def test_watchlist_updates_are_idempotent_across_polls():
    entries = [_entry()]
    first, added, _ = watchlist_schedule_updates([], entries)
    assert added == 1
    second, added, removed = watchlist_schedule_updates(first, entries)
    assert (added, removed) == (0, 0)
    assert [r.id for r in second] == [r.id for r in first]  # same entry, not re-created


def test_watchlist_updates_drop_entries_that_left_the_feed():
    scheduled, _, _ = watchlist_schedule_updates([], [_entry(), _entry(start_hour=22)])
    kept, added, removed = watchlist_schedule_updates(scheduled, [_entry()])
    assert (added, removed) == (0, 1)
    assert [r.start.hour for r in kept] == [20]


def test_watchlist_updates_never_touch_a_hand_made_recording():
    manual = ScheduledRecording.create(
        "http://tv/api/exports/stream/other?token=t",
        "ITV",
        "Something I picked myself",
        datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc),
    )
    kept, added, removed = watchlist_schedule_updates([manual], [_entry()])
    assert (added, removed) == (1, 0)
    assert manual in kept  # not ours to reconcile, even though it's not in the feed


def test_watchlist_updates_do_not_duplicate_an_airing_already_scheduled_by_hand():
    entry = _entry()
    manual = ScheduledRecording.create(
        entry.channel_url, entry.channel_name, entry.title, entry.start, entry.stop
    )
    kept, added, removed = watchlist_schedule_updates([manual], [entry])
    assert (added, removed) == (0, 0)
    assert kept == [manual]


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


def test_fetch_tvtimes_watchlist_returns_entries(monkeypatch):
    feed = TvtimesFeed(base_url="https://tv.example.com", token="t")
    seen: list[str] = []

    def fake_get(url, timeout=None):
        seen.append(url)
        return _FakeResponse(
            [
                {
                    "channel_url": "https://tv.example.com/api/exports/stream/c1?token=t",
                    "channel_name": "BBC One",
                    "title": "The News",
                    "start": "2026-09-03T20:00:00+00:00",
                    "stop": "2026-09-03T21:00:00+00:00",
                }
            ]
        )

    monkeypatch.setattr("tvdinner.tvtimes.requests.get", fake_get)
    entries, error = fetch_tvtimes_watchlist(feed)
    assert error is None
    assert [e.title for e in entries] == ["The News"]
    assert seen == ["https://tv.example.com/api/exports/watchlist.json?token=t"]


def test_fetch_tvtimes_watchlist_reports_errors_without_raising(monkeypatch):
    feed = TvtimesFeed(base_url="https://tv.example.com", token="t")

    def boom(url, timeout=None):
        raise requests.ConnectionError("host is down")

    monkeypatch.setattr("tvdinner.tvtimes.requests.get", boom)
    entries, error = fetch_tvtimes_watchlist(feed)
    assert entries == []
    assert error is not None and "host is down" in error

    monkeypatch.setattr("tvdinner.tvtimes.requests.get", lambda url, timeout=None: _FakeResponse("<html>"))
    entries, error = fetch_tvtimes_watchlist(feed)
    assert entries == []
    assert error is not None and "non-JSON" in error


# --- watch-state reporting -----------------------------------------------


_FEED = TvtimesFeed(base_url="https://tv.example.com", token="t")


def _history(kind="channel", url=None, title="The News", minutes_ago=60, length=60):
    end = datetime(2026, 9, 3, 21, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago)
    return HistoryEntry(
        kind=kind,
        title=title,
        url=url if url is not None else "https://tv.example.com/api/exports/stream/c-1?token=t",
        playlist_source="tvtimes",
        started_at=end - timedelta(minutes=length),
        ended_at=end,
        channel_name="BBC One",
    )


def test_channel_id_is_recovered_from_this_feeds_stream_url():
    assert (
        channel_id_from_stream_url(_FEED, "https://tv.example.com/api/exports/stream/abc?token=t")
        == "abc"
    )
    # a different tvtimes server, or a different source entirely
    assert channel_id_from_stream_url(_FEED, "https://other.example.com/api/exports/stream/abc") is None
    assert channel_id_from_stream_url(_FEED, "http://provider.example/live/u/p/5.ts") is None
    assert channel_id_from_stream_url(_FEED, "https://tv.example.com/api/exports/stream/") is None


def test_payload_only_includes_channel_watches_from_this_feed():
    entries = [
        _history(),
        _history(kind="vod", url="https://tv.example.com/api/exports/stream/c-9?token=t"),
        _history(kind="recording", url="/home/me/rec.ts"),
        _history(url="http://provider.example/live/u/p/5.ts"),  # another source
    ]
    events = watch_events_payload(_FEED, entries, device="living room")
    assert [e["channel_id"] for e in events] == ["c-1"]
    assert events[0]["device"] == "living room"
    assert events[0]["title"] == "The News"


def test_payload_trims_to_the_resend_window():
    recent, ancient = _history(minutes_ago=10), _history(minutes_ago=60 * 24 * 30)
    since = datetime(2026, 9, 3, 21, 0, tzinfo=timezone.utc) - timedelta(days=7)
    events = watch_events_payload(_FEED, [recent, ancient], since=since)
    assert len(events) == 1
    assert events[0]["ended_at"] == recent.ended_at.isoformat()


def test_post_watch_events_reports_stored_count(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def fake_post(url, json=None, timeout=None):
        seen.append((url, json))
        return _FakeResponse({"stored": 2, "skipped": 1})

    monkeypatch.setattr("tvdinner.tvtimes.requests.post", fake_post)
    stored, error = post_watch_events(_FEED, watch_events_payload(_FEED, [_history(), _history(minutes_ago=200)]))
    assert (stored, error) == (2, None)
    assert seen[0][0] == "https://tv.example.com/api/exports/watch-events?token=t"
    assert len(seen[0][1]["events"]) == 2


def test_post_watch_events_never_raises(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise requests.ConnectionError("down")

    monkeypatch.setattr("tvdinner.tvtimes.requests.post", boom)
    stored, error = post_watch_events(_FEED, [{"channel_id": "c-1"}])
    assert stored == 0
    assert error is not None and "down" in error


def test_post_watch_events_skips_the_request_when_there_is_nothing_to_send(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise AssertionError("should not post an empty batch")

    monkeypatch.setattr("tvdinner.tvtimes.requests.post", boom)
    assert post_watch_events(_FEED, []) == (0, None)


# --- favourites sync -----------------------------------------------------


def test_favourites_url_is_the_fourth_export_feed():
    assert tvtimes_favourites_url(_FEED) == (
        "https://tv.example.com/api/exports/favourites.json?token=t"
    )


def test_parse_favourites_takes_names_and_ignores_junk():
    payload = [
        {"channel_id": "c-1", "channel_name": "BBC One"},
        {"channel_id": "c-2", "channel_name": "ITV"},
        {"channel_id": "c-3"},  # no name
        {"channel_id": "c-4", "channel_name": ""},
        "not a dict",
    ]
    assert parse_favourites(payload) == {"BBC One", "ITV"}
    assert parse_favourites({"error": "nope"}) == set()


def test_fetch_favourites_reports_errors_without_raising(monkeypatch):
    monkeypatch.setattr(
        "tvdinner.tvtimes.requests.get",
        lambda url, timeout=None: _FakeResponse([{"channel_name": "BBC One"}]),
    )
    names, error = fetch_tvtimes_favourites(_FEED)
    assert (names, error) == ({"BBC One"}, None)

    def boom(url, timeout=None):
        raise requests.ConnectionError("down")

    monkeypatch.setattr("tvdinner.tvtimes.requests.get", boom)
    names, error = fetch_tvtimes_favourites(_FEED)
    assert names == set()
    assert error is not None and "down" in error


def test_watch_events_payload_truncates_an_over_long_device_name():
    # tvtimes validates device against a varchar(120); an over-long one
    # would 422 the whole batch rather than just losing the label.
    events = watch_events_payload(_FEED, [_history()], device="x" * 200)
    assert events, "expected the entry to qualify"
    assert events[0]["device"] == "x" * MAX_DEVICE_NAME


def test_watch_events_payload_leaves_a_normal_device_name_alone():
    events = watch_events_payload(_FEED, [_history()], device="living room")
    assert events[0]["device"] == "living room"
