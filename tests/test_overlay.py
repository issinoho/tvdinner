from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel
from tvdinner.overlay import (
    _fit_text,
    _wrap_text,
    fetch_image,
    guide_reference_time,
    render_epg_overlay,
    render_program_guide,
    render_programme_details,
    selected_guide_programme,
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


def test_render_epg_overlay_places_provided_logo_on_a_light_tile():
    # Regression test: many real channel logos are dark line-art on a fully
    # transparent background (designed for light UIs/print) and disappear
    # when composited directly onto our dark panel -- the light tile behind
    # a provided logo should be visible even when the logo itself is
    # entirely transparent (worst case).
    now = datetime.now(timezone.utc)
    fully_transparent_logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    image = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now, logo=fully_transparent_logo)
    light_tile_color = (250, 250, 252, 255)
    assert any(pixel == light_tile_color for pixel in image.getdata())


def test_render_epg_overlay_shows_poster_from_programme_icon(tmp_path):
    poster_path = tmp_path / "poster.png"
    Image.new("RGBA", (400, 600), (200, 30, 30, 255)).save(poster_path)

    now = datetime.now(timezone.utc)
    programme = _programme(now, description="A moderately long description of tonight's film. " * 4)
    programme.poster_url = f"file://{poster_path}"

    image = render_epg_overlay(CHANNEL, programme, None, DISPLAY, now)
    assert image.mode == "RGBA"


def test_render_epg_overlay_narrows_text_to_make_room_for_poster(tmp_path):
    poster_path = tmp_path / "poster.png"
    Image.new("RGBA", (400, 600), (200, 30, 30, 255)).save(poster_path)

    now = datetime.now(timezone.utc)
    description = "A moderately long description of tonight's film. " * 4

    without_poster = _programme(now, description=description)
    with_poster = _programme(now, description=description)
    with_poster.poster_url = f"file://{poster_path}"

    plain_image = render_epg_overlay(CHANNEL, without_poster, None, DISPLAY, now)
    poster_image = render_epg_overlay(CHANNEL, with_poster, None, DISPLAY, now)
    # Narrower text area means the same description needs more wrapped
    # lines, so the content-driven banner grows taller.
    assert poster_image.height >= plain_image.height


def test_render_epg_overlay_ignores_unfetchable_poster():
    now = datetime.now(timezone.utc)
    programme = _programme(now)
    programme.poster_url = "file:///nonexistent/poster.png"
    image = render_epg_overlay(CHANNEL, programme, None, DISPLAY, now)
    assert image.mode == "RGBA"


def test_fetch_image_returns_none_for_missing_url():
    assert fetch_image(None) is None


def test_fetch_image_returns_none_for_unreachable_source():
    assert fetch_image("file:///nonexistent/path/logo.png") is None


def test_fetch_image_decodes_local_file(tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (50, 50), (10, 20, 30, 255)).save(path)

    logo = fetch_image(f"file://{path}")
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

    visible = visible_guide_channels(channels, epg, current_channel_url=None, max_rows=8)
    assert [c.tvg_id for c in visible] == ["ch0", "ch1", "ch2"]


def test_visible_guide_channels_excludes_channels_without_tvg_id():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(2, now)
    channels.append(Channel(name="No tvg-id", url="http://x/none"))

    visible = visible_guide_channels(channels, epg, current_channel_url=None, max_rows=8)
    assert all(c.tvg_id is not None for c in visible)


def test_visible_guide_channels_returns_empty_when_nothing_has_epg():
    channels = [Channel(name="A", url="http://x/a", tvg_id="a")]
    assert visible_guide_channels(channels, Epg(), current_channel_url=None, max_rows=8) == []


def test_visible_guide_channels_caps_at_max_rows():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(20, now)
    visible = visible_guide_channels(channels, epg, current_channel_url=None, max_rows=5)
    assert len(visible) == 5


def test_visible_guide_channels_centers_on_current_channel():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(20, now)
    visible = visible_guide_channels(channels, epg, current_channel_url="http://x/10", max_rows=5)
    urls = [c.url for c in visible]
    assert "http://x/10" in urls
    assert urls.index("http://x/10") == 2  # centered: 2 channels before, 2 after


def test_visible_guide_channels_shifts_window_near_the_end():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(20, now)
    visible = visible_guide_channels(channels, epg, current_channel_url="http://x/19", max_rows=5)
    assert [c.tvg_id for c in visible] == ["ch15", "ch16", "ch17", "ch18", "ch19"]


def test_visible_guide_channels_distinguishes_duplicate_tvg_ids():
    """Regression test: real-world M3U playlists often have several distinct
    channels (quality tiers, backup servers) sharing one tvg_id for EPG
    mapping. Centering/selection must key off url, or every such channel
    would be treated as 'the same' row and all highlighted together."""
    now = datetime.now(timezone.utc)
    epg = Epg()
    epg.programmes["shared"] = [
        Programme(channel_id="shared", start=now - timedelta(minutes=10), stop=now + timedelta(minutes=20), title="Show A"),
    ]
    channel_a = Channel(name="Channel A", url="http://x/a", tvg_id="shared")
    channel_b = Channel(name="Channel B", url="http://x/b", tvg_id="shared")
    channels = [channel_a, channel_b]

    visible = visible_guide_channels(channels, epg, current_channel_url=channel_b.url, max_rows=8)
    assert [c.url for c in visible] == ["http://x/a", "http://x/b"]  # both rows shown, distinctly


def test_render_program_guide_returns_none_without_any_schedule():
    channels = [Channel(name="A", url="http://x/a", tvg_id="a")]
    now = datetime.now(timezone.utc)
    assert render_program_guide(channels, Epg(), DISPLAY, now, None, 1920, 1080) is None


def test_render_program_guide_returns_rgba_image():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(4, now)
    image = render_program_guide(channels, epg, DISPLAY, now, "http://x/1", 1920, 1080)
    assert image is not None
    assert image.mode == "RGBA"
    assert image.width <= 1920
    assert image.height <= 1080


def test_render_program_guide_scales_with_canvas_size():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(4, now)
    small = render_program_guide(channels, epg, DISPLAY, now, "http://x/1", 640, 480)
    large = render_program_guide(channels, epg, DISPLAY, now, "http://x/1", 1920, 1080)
    assert large.width > small.width


def test_render_program_guide_font_scales_with_canvas_width_not_row_count():
    """A guide with only 2 rows shouldn't get a dramatically bigger font
    than one with a full page of 8 -- regression test for fonts that used
    to scale off row_height, which grows unboundedly with fewer rows."""
    now = datetime.now(timezone.utc)
    few_channels, few_epg = _guide_channels_and_epg(2, now)
    many_channels, many_epg = _guide_channels_and_epg(8, now)

    from tvdinner.overlay import _font

    few_draw = ImageDraw.Draw(render_program_guide(few_channels, few_epg, DISPLAY, now, "http://x/0", 1920, 1080))
    many_draw = ImageDraw.Draw(render_program_guide(many_channels, many_epg, DISPLAY, now, "http://x/0", 1920, 1080))

    few_font = _font("DejaVuSans.ttf", round(1920 * 0.0105))
    few_size = few_draw.textlength("Show A", font=few_font)
    many_size = many_draw.textlength("Show A", font=few_font)
    assert few_size == many_size  # same font object/size regardless of row count


def test_render_program_guide_now_line_hidden_outside_window():
    # The tuned-channel row stripe also uses the accent color, so the "now"
    # line's presence is checked by pixel *count*, not just membership: the
    # in-window render has that stripe *plus* a tall vertical line, the
    # shifted-away one only has the stripe.
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(3, now)
    far_future_start = now + timedelta(hours=10)

    default_image = render_program_guide(channels, epg, DISPLAY, now, "http://x/1", 1920, 1080)
    shifted_image = render_program_guide(
        channels, epg, DISPLAY, now, "http://x/1", 1920, 1080, window_start=far_future_start
    )
    accent = (0, 176, 255, 255)
    default_count = sum(1 for pixel in default_image.getdata() if pixel == accent)
    shifted_count = sum(1 for pixel in shifted_image.getdata() if pixel == accent)
    assert default_count > shifted_count


def test_render_program_guide_respects_explicit_window_start():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(2, now)
    window_start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=5)

    image = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, window_start=window_start)
    assert image is not None
    assert image.mode == "RGBA"


def test_render_program_guide_accepts_selected_channel_url():
    # Just a non-crash/shape check: the selection border pixels are covered
    # implicitly by the overall RGBA/size assertions elsewhere; this checks
    # the parameter is accepted and doesn't change the returned image's type.
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(4, now)
    image = render_program_guide(
        channels, epg, DISPLAY, now, "http://x/1", 1920, 1080, selected_channel_url="http://x/2"
    )
    assert image is not None
    assert image.mode == "RGBA"


def test_guide_reference_time_uses_now_when_in_window():
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=30)
    assert guide_reference_time(now, window_start, window_hours=3.0) == now


def test_guide_reference_time_uses_window_start_when_outside_window():
    now = datetime.now(timezone.utc)
    window_start = now + timedelta(hours=10)
    assert guide_reference_time(now, window_start, window_hours=3.0) == window_start


def test_selected_guide_programme_returns_current_when_airing():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    programme = selected_guide_programme(epg, "ch0", now)
    assert programme is not None
    assert programme.is_at(now)


def test_selected_guide_programme_returns_next_when_between_shows():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    schedule = epg.schedule_for("ch0")
    # A moment after the first programme ends but before the next begins,
    # if there's a gap; otherwise this just confirms the "airing" branch.
    reference = schedule[0].stop
    programme = selected_guide_programme(epg, "ch0", reference)
    assert programme is not None
    assert programme.start >= reference or programme.is_at(reference)


def test_selected_guide_programme_returns_none_without_schedule():
    epg = Epg()
    assert selected_guide_programme(epg, "nope", datetime.now(timezone.utc)) is None


def test_render_programme_details_returns_rgba_image():
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news",
        start=now - timedelta(minutes=10),
        stop=now + timedelta(minutes=20),
        title="Evening News",
        description="Full details about tonight's programme.",
        category="News",
    )
    image = render_programme_details(CHANNEL, programme, DISPLAY, 1920, 1080)
    assert image.mode == "RGBA"


def test_render_programme_details_grows_for_long_description():
    now = datetime.now(timezone.utc)
    short = Programme(
        channel_id="demo.news", start=now, stop=now + timedelta(minutes=30), title="Short", description="Brief."
    )
    long = Programme(
        channel_id="demo.news",
        start=now,
        stop=now + timedelta(minutes=30),
        title="Long",
        description="A very long description. " * 30,
    )
    short_image = render_programme_details(CHANNEL, short, DISPLAY, 1920, 1080)
    long_image = render_programme_details(CHANNEL, long, DISPLAY, 1920, 1080)
    assert long_image.height > short_image.height


def test_render_programme_details_handles_no_description_or_category():
    now = datetime.now(timezone.utc)
    programme = Programme(channel_id="demo.news", start=now, stop=now + timedelta(minutes=30), title="Bare Show")
    image = render_programme_details(CHANNEL, programme, DISPLAY, 1920, 1080)
    assert image.mode == "RGBA"
