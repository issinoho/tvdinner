import gzip
from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from tvdinner.epg import (
    Epg,
    EpgDisplay,
    parse_time_shift,
    parse_xmltv,
    parse_xmltv_time,
    resolve_epg_sources,
    resolve_timezone,
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

    no_offset = epg.schedule_for("no.offset")[0]
    assert no_offset.start.tzinfo == timezone.utc


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


def test_epg_display_converts_timezone():
    display = EpgDisplay(timezone=ZoneInfo("America/New_York"))
    moment = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    local = display.to_local(moment)
    # New York is UTC-4 under summer DST in July.
    assert local.hour == 14
    assert local.tzinfo.key == "America/New_York"


def test_epg_display_applies_shift_before_timezone_conversion():
    display = EpgDisplay(timezone=timezone.utc, shift=timedelta(hours=1))
    moment = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    assert display.to_local(moment) == datetime(2026, 7, 16, 19, 0, tzinfo=timezone.utc)


def test_epg_display_now_and_next_corrects_for_shift():
    # Feed's clock runs 1 hour fast: what it labels 19:00 is really 18:00.
    epg = parse_xmltv(SAMPLE_XMLTV)
    display = EpgDisplay(shift=timedelta(hours=-1))
    true_now = datetime(2026, 7, 16, 17, 30, tzinfo=timezone.utc)
    current, _ = display.now_and_next(epg, "news.us", true_now)
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
    from tvdinner.epg import Programme

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
    from tvdinner.epg import load_epg

    compressed = gzip.compress(SAMPLE_XMLTV.encode("utf-8"))
    path = tmp_path / "guide.xml.gz"
    path.write_bytes(compressed)

    epg = load_epg(str(path))
    assert epg is not None
    assert "news.us" in epg.channels
