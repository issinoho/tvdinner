from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel
from tvdinner.overlay import (
    _fit_text,
    _wrap_text,
    fetch_logo,
    render_epg_overlay,
    render_program_guide,
    visible_guide_channels,
)

CHANNEL = Channel(name="Demo News HD", url="http://stream/demo", tvg_id="demo.news", group_title="News")
DISPLAY = EpgDisplay(timezone=timezone.utc)


def _programme(now: datetime, title="Evening News", description=None, minutes_in=10, minutes_left=20) -> Programme:
    return Programme(
        channel_id="demo.news",
        start=now - timedelta(minutes=minutes_in),
        stop=now + timedelta(minutes=minutes_left),
        title=title,
        description=description,
    )


def _draw():
    return ImageDraw.Draw(Image.new("RGBA", (1, 1)))


def test_fit_text_returns_unchanged_when_it_fits():
    draw = _draw()
    from PIL import ImageFont

    font = ImageFont.load_default()
    assert _fit_text(draw, "short", font, 10_000) == "short"


def test_fit_text_truncates_with_ellipsis_when_too_long():
    from PIL import ImageFont

    draw = _draw()
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    long_text = "word " * 50
    result = _fit_text(draw, long_text, font, 100)
    assert result.endswith("…")
    assert draw.textlength(result, font=font) <= 100


def test_wrap_text_respects_max_lines():
    from PIL import ImageFont

    draw = _draw()
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    long_text = "word " * 100
    lines = _wrap_text(draw, long_text, font, 300, max_lines=2)
    assert len(lines) <= 2
    assert lines[-1].endswith("…")


def test_render_epg_overlay_returns_rgba_image():
    now = datetime.now(timezone.utc)
    image = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now)
    assert image.mode == "RGBA"
    assert image.width > 0 and image.height > 0


def test_render_epg_overlay_scales_with_canvas_width():
    now = datetime.now(timezone.utc)
    small = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now, canvas_width=640)
    large = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now, canvas_width=3840)
    assert large.width > small.width


def test_render_epg_overlay_grows_taller_for_wrapped_description():
    now = datetime.now(timezone.utc)
    short_desc = render_epg_overlay(CHANNEL, _programme(now, description="Short."), None, DISPLAY, now)
    long_desc = render_epg_overlay(
        CHANNEL,
        _programme(now, description="A very long description. " * 20),
        None,
        DISPLAY,
        now,
    )
    assert long_desc.height > short_desc.height


def test_render_epg_overlay_handles_no_current_programme():
    now = datetime.now(timezone.utc)
    upcoming = _programme(now, title="Later Show", minutes_in=-30, minutes_left=60)
    image = render_epg_overlay(CHANNEL, None, upcoming, DISPLAY, now)
    assert image.mode == "RGBA"


def test_render_epg_overlay_handles_nothing_scheduled():
    now = datetime.now(timezone.utc)
    image = render_epg_overlay(CHANNEL, None, None, DISPLAY, now)
    assert image.mode == "RGBA"


def test_render_epg_overlay_uses_provided_logo():
    now = datetime.now(timezone.utc)
    logo = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    image = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now, logo=logo)
    assert image.mode == "RGBA"


def test_fetch_logo_returns_none_for_missing_url():
    assert fetch_logo(None) is None


def test_fetch_logo_returns_none_for_unreachable_source():
    assert fetch_logo("file:///nonexistent/path/logo.png") is None


def test_fetch_logo_decodes_local_file(tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (50, 50), (10, 20, 30, 255)).save(path)

    logo = fetch_logo(f"file://{path}")
    assert logo is not None
    assert logo.mode == "RGBA"
    assert logo.size == (50, 50)


def _guide_channels_and_epg(count: int, now: datetime) -> tuple[list[Channel], Epg]:
    channels = []
    epg = Epg()
    for i in range(count):
        tvg_id = f"ch{i}"
        channels.append(Channel(name=f"Channel {i}", url=f"http://x/{i}", tvg_id=tvg_id))
        epg.programmes[tvg_id] = [
            Programme(channel_id=tvg_id, start=now - timedelta(minutes=10), stop=now + timedelta(minutes=20), title="Show A"),
            Programme(channel_id=tvg_id, start=now + timedelta(minutes=20), stop=now + timedelta(minutes=50), title="Show B"),
        ]
    return channels, epg


def test_visible_guide_channels_excludes_channels_without_schedule():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(3, now)
    channels.append(Channel(name="No EPG", url="http://x/none", tvg_id="none"))

    visible = visible_guide_channels(channels, epg, current_channel_id=None, max_rows=8)
    assert [c.tvg_id for c in visible] == ["ch0", "ch1", "ch2"]


def test_visible_guide_channels_excludes_channels_without_tvg_id():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(2, now)
    channels.append(Channel(name="No tvg-id", url="http://x/none"))

    visible = visible_guide_channels(channels, epg, current_channel_id=None, max_rows=8)
    assert all(c.tvg_id is not None for c in visible)


def test_visible_guide_channels_returns_empty_when_nothing_has_epg():
    channels = [Channel(name="A", url="http://x/a", tvg_id="a")]
    assert visible_guide_channels(channels, Epg(), current_channel_id=None, max_rows=8) == []


def test_visible_guide_channels_caps_at_max_rows():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(20, now)
    visible = visible_guide_channels(channels, epg, current_channel_id=None, max_rows=5)
    assert len(visible) == 5


def test_visible_guide_channels_centers_on_current_channel():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(20, now)
    visible = visible_guide_channels(channels, epg, current_channel_id="ch10", max_rows=5)
    ids = [c.tvg_id for c in visible]
    assert "ch10" in ids
    assert ids.index("ch10") == 2  # centered: 2 channels before, 2 after


def test_visible_guide_channels_shifts_window_near_the_end():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(20, now)
    visible = visible_guide_channels(channels, epg, current_channel_id="ch19", max_rows=5)
    assert [c.tvg_id for c in visible] == ["ch15", "ch16", "ch17", "ch18", "ch19"]


def test_render_program_guide_returns_none_without_any_schedule():
    channels = [Channel(name="A", url="http://x/a", tvg_id="a")]
    now = datetime.now(timezone.utc)
    assert render_program_guide(channels, Epg(), DISPLAY, now, None, 1920, 1080) is None


def test_render_program_guide_returns_rgba_image():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(4, now)
    image = render_program_guide(channels, epg, DISPLAY, now, "ch1", 1920, 1080)
    assert image is not None
    assert image.mode == "RGBA"
    assert image.width <= 1920
    assert image.height <= 1080


def test_render_program_guide_scales_with_canvas_size():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(4, now)
    small = render_program_guide(channels, epg, DISPLAY, now, "ch1", 640, 480)
    large = render_program_guide(channels, epg, DISPLAY, now, "ch1", 1920, 1080)
    assert large.width > small.width
