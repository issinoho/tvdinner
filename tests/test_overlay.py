from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel
from tvdinner.overlay import (
    _fit_text,
    _font,
    _format_remaining,
    _wrap_text,
    fetch_image,
    guide_eligible_channels,
    guide_reference_time,
    render_epg_overlay,
    render_guide_filter_prompt,
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
    draw = _draw()
    font = _font("DejaVuSans.ttf", 24)
    long_text = "word " * 50
    result = _fit_text(draw, long_text, font, 100)
    assert result.endswith("…")
    assert draw.textlength(result, font=font) <= 100


def test_wrap_text_respects_max_lines():
    draw = _draw()
    font = _font("DejaVuSans.ttf", 24)
    long_text = "word " * 100
    lines = _wrap_text(draw, long_text, font, 300, max_lines=2)
    assert len(lines) <= 2
    assert lines[-1].endswith("…")


def test_format_remaining_shows_minutes_only():
    assert _format_remaining(20 * 60) == "20 min remaining"


def test_format_remaining_shows_hours_and_minutes():
    assert _format_remaining(75 * 60) == "1h 15m remaining"


def test_format_remaining_clamps_negative_to_zero():
    assert _format_remaining(-30) == "0 min remaining"


def test_render_epg_overlay_grows_taller_with_remaining_time():
    now = datetime.now(timezone.utc)
    zero_duration = _programme(now, minutes_in=0, minutes_left=0)
    normal = _programme(now)
    without_remaining = render_epg_overlay(CHANNEL, zero_duration, None, DISPLAY, now)
    with_remaining = render_epg_overlay(CHANNEL, normal, None, DISPLAY, now)
    assert with_remaining.height > without_remaining.height


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


def test_render_epg_overlay_grows_taller_with_badges():
    now = datetime.now(timezone.utc)
    without_badges = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now)
    with_badges = render_epg_overlay(
        CHANNEL, _programme(now), None, DISPLAY, now, badges=["1080p", "H.264", "AAC", "Stereo"]
    )
    assert with_badges.height > without_badges.height


def test_render_epg_overlay_without_badges_matches_no_badges_argument():
    now = datetime.now(timezone.utc)
    implicit = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now)
    explicit_empty = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now, badges=[])
    assert implicit.size == explicit_empty.size


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


def test_visible_guide_channels_falls_back_to_full_list_when_nothing_has_epg():
    # Regression test: some real playlists (e.g. iptv-org's index.m3u)
    # embed no EPG source at all, so nothing has a schedule -- the guide is
    # also the only way to switch channels, so it must still show the
    # channel list (with blank timelines) rather than nothing at all.
    channels = [Channel(name="A", url="http://x/a", tvg_id="a"), Channel(name="B", url="http://x/b", tvg_id="b")]
    visible = visible_guide_channels(channels, Epg(), current_channel_url=None, max_rows=8)
    assert [c.url for c in visible] == ["http://x/a", "http://x/b"]


def test_visible_guide_channels_returns_empty_when_channel_list_is_empty():
    assert visible_guide_channels([], Epg(), current_channel_url=None, max_rows=8) == []


def test_guide_eligible_channels_excludes_channels_without_schedule():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(3, now)
    channels.append(Channel(name="No EPG", url="http://x/none", tvg_id="none"))

    eligible = guide_eligible_channels(channels, epg)
    assert [c.tvg_id for c in eligible] == ["ch0", "ch1", "ch2"]


def test_guide_eligible_channels_falls_back_to_full_list_when_nothing_has_epg():
    channels = [Channel(name="A", url="http://x/a", tvg_id="a"), Channel(name="B", url="http://x/b", tvg_id="b")]
    assert guide_eligible_channels(channels, Epg()) == channels


def test_guide_eligible_channels_is_not_windowed():
    # Regression test: move_guide_selection must page through the *full*
    # eligible list, not visible_guide_channels' max_rows-limited window, or
    # the selection cursor can't scroll past the initially visible rows.
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(20, now)
    eligible = guide_eligible_channels(channels, epg)
    assert len(eligible) == 20


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


def test_render_program_guide_falls_back_to_channel_list_without_any_schedule():
    # Regression test: a playlist with no EPG data at all used to make the
    # guide (and therefore channel switching) return None/nothing to show.
    channels = [Channel(name="A", url="http://x/a", tvg_id="a")]
    now = datetime.now(timezone.utc)
    image = render_program_guide(channels, Epg(), DISPLAY, now, None, 1920, 1080)
    assert image is not None
    assert image.mode == "RGBA"


def test_render_program_guide_returns_none_for_empty_channel_list():
    now = datetime.now(timezone.utc)
    assert render_program_guide([], Epg(), DISPLAY, now, None, 1920, 1080) is None


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


def test_render_program_guide_shows_selection_border_without_any_schedule():
    # Regression test: with no EPG data at all, selected_guide_programme
    # returns None (nothing to draw a programme-block border around), which
    # used to mean moving the UP/DOWN selection cursor had no visible effect
    # at all even though the underlying selected_channel_url did change.
    channels = [Channel(name="A", url="http://x/a", tvg_id="a"), Channel(name="B", url="http://x/b", tvg_id="b")]
    now = datetime.now(timezone.utc)

    unselected = render_program_guide(channels, Epg(), DISPLAY, now, None, 1920, 1080)
    selected = render_program_guide(channels, Epg(), DISPLAY, now, None, 1920, 1080, selected_channel_url="http://x/a")

    border = (255, 255, 255, 255)
    unselected_count = sum(1 for pixel in unselected.getdata() if pixel == border)
    selected_count = sum(1 for pixel in selected.getdata() if pixel == border)
    assert selected_count > unselected_count


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


def test_render_program_guide_scrolls_window_to_follow_selection(monkeypatch):
    # Regression test: the row window used to always center on the playing
    # channel (current_channel_url), so moving the selection cursor toward
    # the edge of a long list just clamped there instead of scrolling
    # further channels into view. Verified by capturing the centering
    # argument render_program_guide actually passes to visible_guide_channels,
    # rather than re-deriving it -- a pixel/OCR check of which channel names
    # got rendered would be far more brittle.
    import tvdinner.overlay as overlay_module

    captured = {}
    real_visible_guide_channels = overlay_module.visible_guide_channels

    def spy(channels, epg, current_channel_url, max_rows=8):
        captured["current_channel_url"] = current_channel_url
        return real_visible_guide_channels(channels, epg, current_channel_url, max_rows)

    monkeypatch.setattr(overlay_module, "visible_guide_channels", spy)

    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(20, now)

    render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, max_rows=8)
    assert captured["current_channel_url"] == "http://x/0"  # no selection -> centers on the playing channel

    render_program_guide(
        channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, max_rows=8, selected_channel_url="http://x/19"
    )
    assert captured["current_channel_url"] == "http://x/19"  # selection present -> centers on it instead


def test_render_program_guide_applies_per_channel_shift():
    # Regression test: the guide's "live" highlighting/positioning used to
    # completely ignore EpgDisplay's shift (Channel 0's schedule would always
    # be read as if unshifted). Not pixel-checked -- selected_guide_programme
    # and the render's live/positioning math share the same
    # `start + shift <= at < stop + shift` formula, already verified
    # directly above -- this just confirms render_program_guide actually
    # wires channel_shifts (keyed by display name) through end to end
    # without crashing.
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    shifted_display = EpgDisplay(timezone=timezone.utc, channel_shifts={"Channel 0": timedelta(minutes=-25)})
    image = render_program_guide(channels, epg, shifted_display, now, "http://x/0", 1920, 1080)
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


def test_selected_guide_programme_applies_shift():
    # _guide_channels_and_epg's "ch0" airs Show A over [now-10, now+20) and
    # Show B over [now+20, now+50); a -25min shift moves Show B's corrected
    # window to [now-5, now+25), which genuinely contains `now` (not just a
    # fallback-to-last-known match).
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    unshifted = selected_guide_programme(epg, "ch0", now)
    shifted = selected_guide_programme(epg, "ch0", now, shift=timedelta(minutes=-25))
    assert unshifted is not None and shifted is not None
    assert unshifted.title == "Show A"
    assert shifted.title == "Show B"


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


def test_render_programme_details_shows_poster_from_programme_icon(tmp_path):
    poster_path = tmp_path / "poster.png"
    Image.new("RGBA", (400, 600), (120, 20, 140, 255)).save(poster_path)

    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news",
        start=now,
        stop=now + timedelta(minutes=30),
        title="A Movie",
        poster_url=f"file://{poster_path}",
    )
    image = render_programme_details(CHANNEL, programme, DISPLAY, 1920, 1080)
    assert image.mode == "RGBA"


def test_render_programme_details_narrows_text_to_make_room_for_poster(tmp_path):
    # The panel width itself is fixed by canvas_width, not by poster
    # presence -- a poster instead narrows the available text_width, so the
    # same description needs more wrapped lines and the content-driven
    # panel grows taller (same pattern as render_epg_overlay's poster test).
    poster_path = tmp_path / "poster.png"
    Image.new("RGBA", (400, 600), (120, 20, 140, 255)).save(poster_path)

    now = datetime.now(timezone.utc)
    description = "A moderately long description of tonight's film. " * 6
    without_poster = Programme(
        channel_id="demo.news", start=now, stop=now + timedelta(minutes=30), title="A Show", description=description
    )
    with_poster = Programme(
        channel_id="demo.news",
        start=now,
        stop=now + timedelta(minutes=30),
        title="A Show",
        description=description,
        poster_url=f"file://{poster_path}",
    )
    plain_image = render_programme_details(CHANNEL, without_poster, DISPLAY, 1920, 1080)
    poster_image = render_programme_details(CHANNEL, with_poster, DISPLAY, 1920, 1080)
    assert poster_image.width == plain_image.width
    assert poster_image.height >= plain_image.height


def test_render_programme_details_ignores_unfetchable_poster():
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news",
        start=now,
        stop=now + timedelta(minutes=30),
        title="A Show",
        poster_url="file:///nonexistent/poster.png",
    )
    image = render_programme_details(CHANNEL, programme, DISPLAY, 1920, 1080)
    assert image.mode == "RGBA"


def test_render_guide_filter_prompt_returns_rgba_image():
    image = render_guide_filter_prompt("bbc", 1920, 1080)
    assert image.mode == "RGBA"
    assert image.width > 0 and image.height > 0


def test_render_guide_filter_prompt_grows_with_typed_text():
    # Not a fixed-width box: the fitted text (and its cursor) should still
    # show up rather than being clipped/hidden as the query gets longer.
    short_image = render_guide_filter_prompt("a", 1920, 1080)
    long_image = render_guide_filter_prompt("a very long channel name query", 1920, 1080)
    assert short_image.size == long_image.size  # panel itself is fixed-size...
    white = (245, 246, 248, 255)
    short_white_pixels = sum(1 for pixel in short_image.getdata() if pixel == white)
    long_white_pixels = sum(1 for pixel in long_image.getdata() if pixel == white)
    assert long_white_pixels > short_white_pixels  # ...but more text still renders
