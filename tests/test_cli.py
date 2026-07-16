from datetime import datetime, timedelta, timezone

from tvdinner.cli import (
    format_channel_line,
    format_osd_epg_text,
    now_and_next_text,
    select_channel,
)
from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel

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


def test_format_osd_epg_text_includes_channel_name_and_schedule():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now)
    display = EpgDisplay(timezone=timezone.utc)

    text = format_osd_epg_text(CHANNEL, epg, display, now)
    lines = text.splitlines()
    assert lines[0] == "Demo News"
    assert any(line.startswith("Now:") for line in lines)
    assert any(line.startswith("Next:") for line in lines)


def test_format_osd_epg_text_returns_none_when_nothing_scheduled():
    now = datetime.now(timezone.utc)
    display = EpgDisplay(timezone=timezone.utc)
    assert format_osd_epg_text(CHANNEL, Epg(), display, now) is None


def test_format_osd_epg_text_includes_current_programme_description():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now, description="Tonight's headlines from around the world.")
    display = EpgDisplay(timezone=timezone.utc)

    text = format_osd_epg_text(CHANNEL, epg, display, now)
    lines = text.splitlines()
    now_index = next(i for i, line in enumerate(lines) if line.startswith("Now:"))
    assert lines[now_index + 1] == "Tonight's headlines from around the world."


def test_format_osd_epg_text_omits_description_line_when_absent():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now)
    display = EpgDisplay(timezone=timezone.utc)

    text = format_osd_epg_text(CHANNEL, epg, display, now)
    lines = text.splitlines()
    now_index = next(i for i, line in enumerate(lines) if line.startswith("Now:"))
    assert lines[now_index + 1].startswith("Next:")


def test_format_osd_epg_text_truncates_long_description():
    now = datetime.now(timezone.utc)
    long_description = "x" * 500
    epg = _epg_with_current_and_next(now, description=long_description)
    display = EpgDisplay(timezone=timezone.utc)

    text = format_osd_epg_text(CHANNEL, epg, display, now)
    description_line = next(line for line in text.splitlines() if line.startswith("x"))
    assert len(description_line) < 500
    assert description_line.endswith("…")


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
