import gzip
import os
import pickle
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from zoneinfo import ZoneInfo

from tvdinner.epg import (
    Epg,
    EpgChannel,
    EpgDisplay,
    Programme,
    atomic_write_bytes,
    _fetch_bytes,
    _load_cached_parsed_epg,
    _parse_release_year,
    _parsed_cache_path_for,
    _save_cached_parsed_epg,
    cache_path_for,
    format_time_shift,
    load_channel_shifts,
    load_epg,
    normalize_name,
    parse_time_shift,
    parse_xmltv,
    parse_xmltv_time,
    resolve_epg_sources,
    resolve_timezone,
    save_channel_shifts,
)
from tvdinner.m3u import Channel, Playlist

SAMPLE_XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="news.us">
    <display-name>News Channel</display-name>
    <icon src="http://logo/news.png"/>
  </channel>
  <programme start="20260716180000 +0000" stop="20260716190000 +0000" channel="news.us">
    <title>Evening News</title>
    <desc>The day's headlines.</desc>
    <category>News</category>
    <icon src="http://posters/evening-news.jpg"/>
    <date>2020-05-04</date>
  </programme>
  <programme start="20260716190000 +0000" stop="20260716193000 +0000" channel="news.us">
    <title>Weather</title>
  </programme>
  <programme start="20260716120000" stop="20260716123000" channel="no.offset">
    <title>No Offset Show</title>
  </programme>
</tv>
"""


def test_parse_xmltv_time_with_offset():
    dt = parse_xmltv_time("20260716190000 +0100")
    assert dt == datetime(2026, 7, 16, 19, 0, 0, tzinfo=timezone(timedelta(hours=1)))


def test_parse_xmltv_time_negative_offset():
    dt = parse_xmltv_time("20260716190000 -0530")
    assert dt.utcoffset() == -timedelta(hours=5, minutes=30)


def test_parse_xmltv_time_missing_offset_assumes_utc():
    dt = parse_xmltv_time("20260716120000")
    assert dt.tzinfo == timezone.utc


def test_parse_xmltv_time_invalid_raises():
    with pytest.raises(ValueError):
        parse_xmltv_time("not-a-timestamp")


def test_parse_xmltv_builds_channels_and_sorted_programmes():
    epg = parse_xmltv(SAMPLE_XMLTV)

    assert "news.us" in epg.channels
    assert epg.channels["news.us"].name == "News Channel"
    assert epg.channels["news.us"].icon == "http://logo/news.png"

    schedule = epg.schedule_for("news.us")
    assert [p.title for p in schedule] == ["Evening News", "Weather"]
    assert schedule[0].poster_url == "http://posters/evening-news.jpg"
    assert schedule[1].poster_url is None  # "Weather" has no <icon>
    assert schedule[0].year == "2020"
    assert schedule[1].year is None  # "Weather" has no <date>
    assert schedule[0].category == "News"
    assert schedule[1].category is None  # "Weather" has no <category>

    no_offset = epg.schedule_for("no.offset")[0]
    assert no_offset.start.tzinfo == timezone.utc


def test_parse_xmltv_joins_multiple_category_tags():
    # XMLTV allows several <category> tags per programme -- a feed listing a
    # specific genre before "Movie" must not lose the "Movie" one, since
    # that's the only signal tmdb.is_movie_category has to go on.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <tv>
      <channel id="tcm.us"><display-name>TCM</display-name></channel>
      <programme start="20260716180000 +0000" stop="20260716200000 +0000" channel="tcm.us">
        <title>Sweet Smell of Success</title>
        <category>Crime drama</category>
        <category>Movie</category>
      </programme>
    </tv>
    """
    epg = parse_xmltv(xml)
    assert epg.schedule_for("tcm.us")[0].category == "Crime drama, Movie"


DUPLICATE_CHANNEL_ID_XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="EU21439.hdhomerun.com">
    <display-name>BBC1SCO</display-name>
    <display-name>BBC ONE SCOT</display-name>
    <icon src="http://logo/sd.png"/>
  </channel>
  <channel id="EU21439.hdhomerun.com">
    <display-name>BBC1SCO</display-name>
    <display-name>BBC 1 SCOT HD</display-name>
  </channel>
  <programme start="20260716180000 +0000" stop="20260716190000 +0000" channel="EU21439.hdhomerun.com">
    <title>Evening News</title>
  </programme>
</tv>
"""


def test_parse_xmltv_merges_display_names_for_duplicate_channel_ids():
    # SiliconDust's HDHomeRun XMLTV export (and possibly other providers)
    # emits one <channel> element per SD/HD simulcast of the same station,
    # all sharing one id -- a naive dict overwrite would silently drop the
    # first block's display-name variants, breaking the name-based
    # fallback match for any source that only knows the dropped spelling
    # (e.g. HDHomeRun's own SD GuideName "BBC ONE Scot" not matching if
    # only the HD block's "BBC 1 SCOT HD" survived).
    epg = parse_xmltv(DUPLICATE_CHANNEL_ID_XMLTV)

    channel = epg.channels["EU21439.hdhomerun.com"]
    assert channel.display_names == ["BBC1SCO", "BBC ONE SCOT", "BBC 1 SCOT HD"]
    assert channel.icon == "http://logo/sd.png"  # kept from whichever block had one

    assert epg.resolve_channel_id(None, "BBC ONE Scot") == "EU21439.hdhomerun.com"
    assert epg.resolve_channel_id(None, "BBC 1 Scot HD") == "EU21439.hdhomerun.com"
    assert len(epg.schedule_for(None, "BBC ONE Scot")) == 1


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2020-05-04", "2020"),
        ("1934", "1934"),
        ("199003", "1990"),
        (None, None),
        ("", None),
        ("not a year", None),
    ],
)
def test_parse_release_year(value, expected):
    assert _parse_release_year(value) == expected


def test_now_and_next():
    epg = parse_xmltv(SAMPLE_XMLTV)
    at = datetime(2026, 7, 16, 18, 30, tzinfo=timezone.utc)
    current, upcoming = epg.now_and_next("news.us", at)
    assert current.title == "Evening News"
    assert upcoming.title == "Weather"


def test_now_and_next_before_any_programme():
    epg = parse_xmltv(SAMPLE_XMLTV)
    at = datetime(2026, 7, 16, 17, 0, tzinfo=timezone.utc)
    current, upcoming = epg.now_and_next("news.us", at)
    assert current is None
    assert upcoming.title == "Evening News"


def test_now_and_next_after_all_programmes():
    epg = parse_xmltv(SAMPLE_XMLTV)
    at = datetime(2026, 7, 16, 20, 0, tzinfo=timezone.utc)
    current, upcoming = epg.now_and_next("news.us", at)
    assert current is None
    assert upcoming is None


def test_schedule_for_falls_back_to_tvg_id_with_feed_suffix_stripped():
    # iptv-org's own playlists append '@SD'/'@HD'/etc. to their canonical
    # channel id to disambiguate multiple feeds for one channel.
    epg = parse_xmltv(SAMPLE_XMLTV)
    schedule = epg.schedule_for("news.us@SD")
    assert [p.title for p in schedule] == ["Evening News", "Weather"]


def test_schedule_for_falls_back_to_normalized_display_name():
    epg = parse_xmltv(SAMPLE_XMLTV)
    # tvg_id doesn't match anything in this EPG at all, only the name does.
    schedule = epg.schedule_for("no-such-id", "News Channel")
    assert [p.title for p in schedule] == ["Evening News", "Weather"]


def test_schedule_for_name_fallback_strips_source_tag_prefix():
    # Real-world providers commonly prefix every display-name with their own
    # source tag (e.g. "PLUTO - 00s Replay"), which a plain name match would
    # never see past.
    epg = Epg()
    epg.channels["00sReplay.pluto"] = EpgChannel(id="00sReplay.pluto", display_names=["PLUTO - 00s Replay"])
    epg.programmes["00sReplay.pluto"] = [
        Programme(
            channel_id="00sReplay.pluto",
            start=parse_xmltv_time("20260716180000 +0000"),
            stop=parse_xmltv_time("20260716190000 +0000"),
            title="Shooter",
        )
    ]
    schedule = epg.schedule_for("00sReplay.us@SD", "00s Replay")
    assert [p.title for p in schedule] == ["Shooter"]


def test_schedule_for_prefers_exact_tvg_id_over_name_fallback():
    epg = parse_xmltv(SAMPLE_XMLTV)
    # A name that would resolve to a different channel shouldn't be used
    # when the tvg_id itself is already a valid match.
    schedule = epg.schedule_for("no.offset", "News Channel")
    assert [p.title for p in schedule] == ["No Offset Show"]


def test_normalize_name_strips_spaced_source_tag_prefix():
    assert normalize_name("PLUTO - 00s Replay") == "00s replay"


def test_normalize_name_does_not_strip_hyphenated_names():
    # No spaces around the hyphen, so this isn't a "TAG - Name" prefix.
    assert normalize_name("24-Hour News") == "24-hour news"


def test_normalize_name_strips_trailing_decorative_symbol():
    # Some playlist generators append a decorative marker (e.g. a circled
    # letter) that isn't part of the channel's real name.
    assert normalize_name("Buzzr Ⓖ") == "buzzr"


def test_normalize_name_does_not_strip_trailing_punctuation():
    # Only Symbol-category characters are stripped -- meaningful trailing
    # punctuation like a parenthetical qualifier is left alone.
    assert normalize_name("Channel (East)") == "channel (east)"


def test_schedule_for_returns_empty_when_nothing_matches():
    epg = parse_xmltv(SAMPLE_XMLTV)
    assert epg.schedule_for(None, None) == []


def test_icon_for_returns_the_matched_channels_icon():
    epg = parse_xmltv(SAMPLE_XMLTV)
    assert epg.icon_for("news.us") == "http://logo/news.png"


def test_icon_for_falls_back_to_normalized_display_name():
    epg = parse_xmltv(SAMPLE_XMLTV)
    assert epg.icon_for("no-such-id", "News Channel") == "http://logo/news.png"


def test_icon_for_returns_none_when_channel_has_no_icon():
    epg = parse_xmltv(SAMPLE_XMLTV)
    # "no.offset" (see SAMPLE_XMLTV) has no <channel> element at all, only
    # a <programme> referencing that id.
    assert epg.icon_for("no.offset") is None


def test_icon_for_returns_none_when_nothing_matches():
    epg = parse_xmltv(SAMPLE_XMLTV)
    assert epg.icon_for(None, None) is None
    assert epg.schedule_for("unknown.id", "Unknown Name") == []


def test_epg_display_converts_timezone():
    display = EpgDisplay(timezone=ZoneInfo("America/New_York"))
    moment = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    local = display.to_local(moment)
    # New York is UTC-4 under summer DST in July.
    assert local.hour == 14
    assert local.tzinfo.key == "America/New_York"


def test_epg_display_applies_shift_before_timezone_conversion():
    display = EpgDisplay(timezone=timezone.utc, default_shift=timedelta(hours=1))
    moment = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    assert display.to_local(moment) == datetime(2026, 7, 16, 19, 0, tzinfo=timezone.utc)


def test_epg_display_now_and_next_corrects_for_shift():
    # Feed's clock runs 1 hour fast: what it labels 19:00 is really 18:00.
    epg = parse_xmltv(SAMPLE_XMLTV)
    display = EpgDisplay(default_shift=timedelta(hours=-1))
    true_now = datetime(2026, 7, 16, 17, 30, tzinfo=timezone.utc)
    current, _ = display.now_and_next(epg, "news.us", true_now)
    assert current.title == "Evening News"


def test_epg_display_shift_for_uses_default_without_override():
    display = EpgDisplay(default_shift=timedelta(hours=2))
    assert display.shift_for("no.override") == timedelta(hours=2)
    assert display.shift_for(None) == timedelta(hours=2)


def test_epg_display_shift_for_uses_per_channel_override():
    # Keyed by display name, not tvg_id: real playlists often have several
    # distinct channels (e.g. an East/West regional pair) sharing one tvg_id
    # for EPG mapping, which a tvg_id-keyed override couldn't tell apart.
    display = EpgDisplay(
        default_shift=timedelta(hours=2),
        channel_shifts={"News Channel West": timedelta(hours=-1)},
    )
    assert display.shift_for("News Channel West") == timedelta(hours=-1)
    assert display.shift_for("News Channel East") == timedelta(hours=2)


def test_epg_display_to_local_respects_per_channel_override():
    display = EpgDisplay(
        timezone=timezone.utc,
        default_shift=timedelta(hours=1),
        channel_shifts={"News Channel West": timedelta(hours=-2)},
    )
    moment = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    assert display.to_local(moment, channel_name="News Channel West") == datetime(
        2026, 7, 16, 16, 0, tzinfo=timezone.utc
    )
    assert display.to_local(moment, channel_name="News Channel East") == datetime(
        2026, 7, 16, 19, 0, tzinfo=timezone.utc
    )
    assert display.to_local(moment) == datetime(2026, 7, 16, 19, 0, tzinfo=timezone.utc)  # no channel_name -> default


def test_epg_display_now_and_next_respects_per_channel_override():
    epg = parse_xmltv(SAMPLE_XMLTV)
    # Global default shift is wildly wrong for "News Channel", but there's a
    # per-channel override correcting it back to the feed's actual (unshifted) time.
    display = EpgDisplay(default_shift=timedelta(hours=5), channel_shifts={"News Channel": timedelta()})
    true_now = datetime(2026, 7, 16, 18, 30, tzinfo=timezone.utc)
    current, _ = display.now_and_next(epg, "news.us", true_now, channel_name="News Channel")
    assert current.title == "Evening News"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("+1h30m", timedelta(hours=1, minutes=30)),
        ("-45m", -timedelta(minutes=45)),
        ("2h", timedelta(hours=2)),
        ("90", timedelta(minutes=90)),
        ("-90", -timedelta(minutes=90)),
        ("", timedelta()),
    ],
)
def test_parse_time_shift(text, expected):
    assert parse_time_shift(text) == expected


def test_parse_time_shift_invalid():
    with pytest.raises(ValueError):
        parse_time_shift("garbage")


def test_resolve_timezone_unknown_raises():
    with pytest.raises(ValueError):
        resolve_timezone("Not/AZone")


def test_resolve_timezone_none_returns_none():
    assert resolve_timezone(None) is None


def test_merge_combines_channels_and_sorts_programmes():
    epg_a = parse_xmltv(SAMPLE_XMLTV)
    epg_b = Epg()
    epg_b.channels["other"] = epg_a.channels["news.us"]
    later = parse_xmltv_time("20260716200000 +0000")

    epg_b.programmes["news.us"] = [
        Programme(channel_id="news.us", start=later, stop=later + timedelta(minutes=30), title="Late Show")
    ]

    epg_a.merge(epg_b)
    titles = [p.title for p in epg_a.schedule_for("news.us")]
    assert titles == ["Evening News", "Weather", "Late Show"]


def test_resolve_epg_sources_override_wins():
    playlist = Playlist(epg_url="http://playlist-epg.example/guide.xml")
    assert resolve_epg_sources(playlist, override="http://override.example/guide.xml") == [
        "http://override.example/guide.xml"
    ]


def test_resolve_epg_sources_splits_comma_separated_playlist_urls():
    playlist = Playlist(epg_url="http://a.example/g.xml, http://b.example/g.xml")
    assert resolve_epg_sources(playlist) == [
        "http://a.example/g.xml",
        "http://b.example/g.xml",
    ]


def test_resolve_epg_sources_falls_back_to_per_channel_tvg_url():
    playlist = Playlist(
        channels=[
            Channel(name="A", url="http://a", tvg_url="http://a.example/g.xml"),
            Channel(name="B", url="http://b", tvg_url="http://a.example/g.xml"),
            Channel(name="C", url="http://c"),
        ]
    )
    assert resolve_epg_sources(playlist) == ["http://a.example/g.xml"]


def test_gzip_compressed_xmltv_is_decompressed(tmp_path):
    compressed = gzip.compress(SAMPLE_XMLTV.encode("utf-8"))
    path = tmp_path / "guide.xml.gz"
    path.write_bytes(compressed)

    epg = load_epg(str(path))
    assert epg is not None
    assert "news.us" in epg.channels


class _FakeResponse:
    """Mimics requests.get(..., stream=True)'s context-manager response --
    _fetch_bytes only ever pulls headers/iter_content from this, same
    shape as test_m3u.py's _FakeStreamResponse."""

    def __init__(self, content: bytes, content_length: int | None = None):
        self._content = content
        self.headers = {"Content-Length": str(content_length)} if content_length is not None else {}

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_cache_path_for_is_stable_and_url_specific():
    a = cache_path_for(Path("/cache"), "http://a.example/guide.xml")
    b = cache_path_for(Path("/cache"), "http://b.example/guide.xml")
    assert a != b
    assert a == cache_path_for(Path("/cache"), "http://a.example/guide.xml")


def test_atomic_write_bytes_writes_the_given_content(tmp_path):
    path = tmp_path / "cache.xml"
    atomic_write_bytes(path, b"hello world")
    assert path.read_bytes() == b"hello world"
    assert list(tmp_path.iterdir()) == [path]  # no leftover temp file


def test_atomic_write_bytes_never_corrupts_an_existing_file_on_failure(tmp_path, monkeypatch):
    # Regression: the parsed-EPG cache write used to go straight to the
    # real cache path -- a process killed mid-write (the background
    # EPG-loading thread is a daemon thread with no graceful-shutdown
    # handling) could truncate it, producing "Discarding unreadable
    # parsed-EPG cache ... Ran out of input" on the next run. Confirmed
    # live against a very large (300+ MB), slow-to-load feed.
    path = tmp_path / "cache.xml"
    path.write_bytes(b"original good data")

    def fail_replace(*args, **kwargs):
        raise OSError("simulated failure between the write and the rename")

    monkeypatch.setattr("tvdinner.epg.os.replace", fail_replace)

    with pytest.raises(OSError):
        atomic_write_bytes(path, b"new data that never finishes writing")

    assert path.read_bytes() == b"original good data"
    assert list(tmp_path.iterdir()) == [path]  # the orphaned temp file is cleaned up, not left behind


def test_load_epg_uses_fresh_cache_without_network_call(tmp_path, monkeypatch):
    url = "http://example.com/guide.xml"
    cache_path_for(tmp_path, url).write_bytes(SAMPLE_XMLTV.encode("utf-8"))

    def fail_get(*args, **kwargs):
        raise AssertionError("network should not be hit for a fresh cache")

    monkeypatch.setattr("tvdinner.epg.requests.get", fail_get)

    epg = load_epg(url, cache_dir=tmp_path, max_age=timedelta(hours=24))
    assert epg is not None
    assert "news.us" in epg.channels


def test_load_epg_refetches_and_updates_cache_when_stale(tmp_path, monkeypatch):
    url = "http://example.com/guide.xml"
    cache_path = cache_path_for(tmp_path, url)
    cache_path.write_bytes(b"<tv></tv>")  # stale placeholder content
    stale_time = time.time() - timedelta(hours=48).total_seconds()
    os.utime(cache_path, (stale_time, stale_time))

    monkeypatch.setattr(
        "tvdinner.epg.requests.get", lambda *a, **kw: _FakeResponse(SAMPLE_XMLTV.encode("utf-8"))
    )

    epg = load_epg(url, cache_dir=tmp_path, max_age=timedelta(hours=24))
    assert epg is not None
    assert "news.us" in epg.channels
    assert cache_path.read_bytes() == SAMPLE_XMLTV.encode("utf-8")


def test_load_epg_falls_back_to_stale_cache_when_fetch_fails(tmp_path, monkeypatch):
    url = "http://example.com/guide.xml"
    cache_path = cache_path_for(tmp_path, url)
    cache_path.write_bytes(SAMPLE_XMLTV.encode("utf-8"))
    stale_time = time.time() - timedelta(hours=48).total_seconds()
    os.utime(cache_path, (stale_time, stale_time))

    def fail_get(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr("tvdinner.epg.requests.get", fail_get)

    epg = load_epg(url, cache_dir=tmp_path, max_age=timedelta(hours=24))
    assert epg is not None  # stale cache used rather than losing EPG data entirely
    assert "news.us" in epg.channels


def test_load_epg_without_cache_dir_always_hits_network(tmp_path, monkeypatch):
    url = "http://example.com/guide.xml"
    calls = []
    monkeypatch.setattr(
        "tvdinner.epg.requests.get",
        lambda *a, **kw: calls.append(1) or _FakeResponse(SAMPLE_XMLTV.encode("utf-8")),
    )

    epg = load_epg(url)
    assert epg is not None
    assert calls == [1]


def test_save_and_load_cached_parsed_epg_round_trips_with_current_version(tmp_path):
    url = "http://example.com/guide.xml"
    cache_path_for(tmp_path, url).write_bytes(SAMPLE_XMLTV.encode("utf-8"))
    _save_cached_parsed_epg(url, tmp_path, parse_xmltv(SAMPLE_XMLTV))

    loaded = _load_cached_parsed_epg(url, tmp_path, timedelta(hours=24))
    assert loaded is not None
    assert "news.us" in loaded.channels


def test_load_cached_parsed_epg_discards_cache_written_by_a_different_version(tmp_path):
    # Regression: a parsed-EPG pickle from before a tvdinner upgrade must
    # never be trusted, even if Epg/Programme's fields haven't changed --
    # parse_xmltv's parsing *logic* can change (e.g. which XML bits
    # populate a field) without tripping the pickle-compat check, which
    # would otherwise silently keep serving programmes parsed by the old
    # code for up to a full cache window post-upgrade.
    url = "http://example.com/guide.xml"
    cache_path_for(tmp_path, url).write_bytes(SAMPLE_XMLTV.encode("utf-8"))
    data = pickle.dumps(("0.0.0-old", parse_xmltv(SAMPLE_XMLTV)), protocol=pickle.HIGHEST_PROTOCOL)
    atomic_write_bytes(_parsed_cache_path_for(tmp_path, url), data)

    assert _load_cached_parsed_epg(url, tmp_path, timedelta(hours=24)) is None


def test_fetch_bytes_reports_progress_with_known_content_length(monkeypatch):
    body = b"x" * 250
    monkeypatch.setattr(
        "tvdinner.epg.requests.get", lambda *a, **kw: _FakeResponse(body, content_length=len(body))
    )

    calls = []
    result = _fetch_bytes("http://example.com/guide.xml", on_progress=lambda downloaded, total: calls.append((downloaded, total)))

    assert result == body
    assert calls == [(250, 250)]  # the fake yields the whole body as one chunk


def test_fetch_bytes_reports_progress_with_unknown_total_when_no_content_length(monkeypatch):
    # A chunked-transfer response (common for large, dynamically generated
    # XMLTV feeds -- confirmed live) never sends Content-Length at all, so
    # `total` must come through as None rather than some guessed value.
    body = b"y" * 100
    monkeypatch.setattr("tvdinner.epg.requests.get", lambda *a, **kw: _FakeResponse(body))

    calls = []
    _fetch_bytes("http://example.com/guide.xml", on_progress=lambda downloaded, total: calls.append((downloaded, total)))

    assert calls == [(100, None)]


def test_load_channel_shifts_missing_file_is_not_an_error(tmp_path):
    shifts, warnings = load_channel_shifts(tmp_path / "does-not-exist.json")
    assert shifts == {}
    assert warnings == []


def test_load_channel_shifts_parses_valid_entries(tmp_path):
    path = tmp_path / "epg_shifts.json"
    path.write_text('{"BBC One": "+1h", "TCM US West": "-30m"}')

    shifts, warnings = load_channel_shifts(path)
    assert shifts == {"BBC One": timedelta(hours=1), "TCM US West": timedelta(minutes=-30)}
    assert warnings == []


def test_load_channel_shifts_warns_on_malformed_json(tmp_path):
    path = tmp_path / "epg_shifts.json"
    path.write_text("{not valid json")

    shifts, warnings = load_channel_shifts(path)
    assert shifts == {}
    assert len(warnings) == 1


def test_load_channel_shifts_warns_on_non_object_json(tmp_path):
    path = tmp_path / "epg_shifts.json"
    path.write_text('["not", "an", "object"]')

    shifts, warnings = load_channel_shifts(path)
    assert shifts == {}
    assert len(warnings) == 1


def test_load_channel_shifts_skips_bad_entries_with_a_warning(tmp_path):
    path = tmp_path / "epg_shifts.json"
    path.write_text('{"good.channel": "+1h", "bad.channel": "not a shift"}')

    shifts, warnings = load_channel_shifts(path)
    assert shifts == {"good.channel": timedelta(hours=1)}
    assert len(warnings) == 1
    assert "bad.channel" in warnings[0]


@pytest.mark.parametrize(
    "delta, expected",
    [
        (timedelta(hours=1, minutes=30), "+1h30m"),
        (timedelta(minutes=-45), "-45m"),
        (timedelta(hours=2), "+2h"),
        (timedelta(hours=-3), "-3h"),
        (timedelta(), "+0m"),
    ],
)
def test_format_time_shift(delta, expected):
    assert format_time_shift(delta) == expected


def test_format_time_shift_round_trips_through_parse_time_shift():
    for delta in (timedelta(hours=1, minutes=30), timedelta(minutes=-25), timedelta()):
        assert parse_time_shift(format_time_shift(delta)) == delta


def test_save_channel_shifts_round_trips_through_load_channel_shifts(tmp_path):
    path = tmp_path / "nested" / "epg_shifts.json"
    shifts = {"TCM US West": timedelta(hours=-3), "BBC One": timedelta(minutes=25)}

    save_channel_shifts(path, shifts)
    loaded, warnings = load_channel_shifts(path)

    assert loaded == shifts
    assert warnings == []
