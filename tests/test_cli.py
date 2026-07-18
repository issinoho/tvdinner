from datetime import datetime, timedelta, timezone

from tvdinner.cli import format_channel_line, now_and_next_text, select_channel, stream_quality_badges
from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel
from tvdinner.player import StreamInfo

CHANNEL = Channel(name="Demo News", url="http://stream/demo", tvg_id="demo.news", group_title="Test")


def _epg_with_current_and_next(now: datetime, description: str | None = None) -> Epg:
    epg = Epg()
    epg.programmes["demo.news"] = [
        Programme(
            channel_id="demo.news",
            start=now - timedelta(minutes=10),
            stop=now + timedelta(minutes=20),
            title="Live Test Broadcast",
            description=description,
        ),
        Programme(
            channel_id="demo.news",
            start=now + timedelta(minutes=20),
            stop=now + timedelta(minutes=50),
            title="Upcoming Test Show",
        ),
    ]
    return epg


def test_now_and_next_text_without_epg_returns_none():
    assert now_and_next_text(CHANNEL, None, None, datetime.now(timezone.utc)) == (None, None)


def test_now_and_next_text_without_tvg_id_returns_none():
    channel = Channel(name="No ID", url="http://stream/x")
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now)
    display = EpgDisplay()
    assert now_and_next_text(channel, epg, display, now) == (None, None)


def test_now_and_next_text_formats_current_and_upcoming():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now)
    display = EpgDisplay(timezone=timezone.utc)

    now_text, next_text = now_and_next_text(CHANNEL, epg, display, now)
    assert now_text.startswith("Now: Live Test Broadcast (")
    assert next_text.startswith("Next: Upcoming Test Show (")


def test_format_channel_line_includes_group_and_epg():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now)
    display = EpgDisplay(timezone=timezone.utc)

    line = format_channel_line(1, CHANNEL, 2, epg, display, now)
    assert line.startswith(" 1. Demo News [Test]")
    assert "Now: Live Test Broadcast" in line
    assert "Next: Upcoming Test Show" in line


def test_format_channel_line_without_epg_data():
    now = datetime.now(timezone.utc)
    line = format_channel_line(1, CHANNEL, 1, None, None, now)
    assert line == "1. Demo News [Test]"


def test_format_channel_line_does_not_include_description():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now, description="Should not appear in the compact list line.")
    display = EpgDisplay(timezone=timezone.utc)

    line = format_channel_line(1, CHANNEL, 1, epg, display, now)
    assert "Should not appear" not in line


def test_select_channel_by_index():
    channels = [CHANNEL, Channel(name="Other", url="http://stream/other")]
    assert select_channel(channels, "2").name == "Other"


def test_select_channel_by_name_substring():
    channels = [CHANNEL, Channel(name="Other", url="http://stream/other")]
    assert select_channel(channels, "demo") is CHANNEL


def test_select_channel_not_found():
    channels = [CHANNEL]
    assert select_channel(channels, "nope") is None


def test_stream_quality_badges_returns_empty_list_without_info():
    assert stream_quality_badges(None) == []


def test_stream_quality_badges_omits_missing_fields():
    info = StreamInfo(resolution="1080p", video_codec="H.264", audio_codec="AAC")
    assert stream_quality_badges(info) == ["1080p", "H.264", "AAC"]


def test_stream_quality_badges_includes_everything_present():
    info = StreamInfo(
        resolution="4K",
        video_codec="HEVC",
        fps="59.94fps",
        hdr="HDR10",
        audio_codec="AC-3",
        audio_channels="5.1",
    )
    assert stream_quality_badges(info) == ["4K", "HEVC", "59.94fps", "HDR10", "AC-3", "5.1"]
