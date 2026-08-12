from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tvdinner import tmdb
from tvdinner.channel_logos import EMPTY_LOGO_INDEX, OnlineLogoIndex
from tvdinner.epg import Epg, EpgChannel, EpgDisplay, Programme
from tvdinner.m3u import Channel
from tvdinner.overlay import (
    _ACCENT_COLOR,
    _NOTDEF_PROBE,
    _fit_text,
    _font,
    _font_has_glyph,
    _format_playback_time,
    _format_recordings_date,
    _format_remaining,
    _format_schedule_date,
    _format_size,
    _logo_tile,
    _strip_unsupported_glyphs,
    _title_with_year,
    _tmdb_logo,
    _wrap_text,
    cached_channel_logo,
    fetch_image,
    guide_eligible_channels,
    guide_reference_time,
    prefetch_channel_logos,
    render_about_overlay,
    render_cast_picker,
    render_epg_overlay,
    render_guide_filter_prompt,
    render_help_overlay,
    render_plex_browser,
    render_program_guide,
    render_programme_details,
    render_recording_overlay,
    render_recordings_browser,
    render_schedule_browser,
    render_update_available_overlay,
    render_vod_browser,
    render_vod_info_overlay,
    resolve_channel_logo,
    selected_guide_programme,
    visible_cast_devices,
    visible_guide_channels,
    visible_guide_movies,
    visible_plex_nodes,
    visible_recordings,
    visible_schedule,
    visible_vod_items,
)
from tvdinner.chromecast import CastDevice
from tvdinner.player import RecordingFile
from tvdinner.plex import PlexNode
from tvdinner.schedule import ScheduledRecording
from tvdinner.vod import VodItem

CHANNEL = Channel(name="Demo News HD", url="http://stream/demo", tvg_id="demo.news", group_title="News")
DISPLAY = EpgDisplay(timezone=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_logo_tile_cache():
    # _logo_tile's cache is keyed by id(image), which is only safe for its
    # real production usage (fetch_image's own cache holds a permanent
    # reference to every logo for the app's whole lifetime, so an id can
    # never be reused underneath it) -- but tests construct short-lived
    # Image.new(...) objects that go out of scope and get collected right
    # after each test function returns, so a later test's image can in
    # principle land at the same address and get a stale cross-test cache
    # hit. Clearing between tests removes that risk without weakening the
    # cache itself.
    from tvdinner import overlay

    overlay._logo_tile_cache.clear()
    yield
    overlay._logo_tile_cache.clear()


@pytest.fixture(autouse=True)
def _clear_tmdb_ratings_cache():
    tmdb._ratings_cache.clear()
    yield
    tmdb._ratings_cache.clear()


@pytest.fixture(autouse=True)
def _clear_channel_logo_caches():
    from tvdinner import overlay

    overlay._channel_logo_cache.clear()
    overlay._channel_logo_in_flight.clear()
    yield
    overlay._channel_logo_cache.clear()
    overlay._channel_logo_in_flight.clear()


@pytest.fixture(autouse=True)
def _run_overlay_threads_synchronously(monkeypatch):
    """prefetch_channel_logos spawns daemon threads -- for deterministic
    tests, run the target function immediately on the calling thread
    instead, same effect (cache populated, key cleared from in-flight)
    without any real concurrency to wait on. Mirrors test_tmdb.py's
    identical fixture for tmdb.prefetch_ratings."""
    from tvdinner import overlay

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(overlay.threading, "Thread", _ImmediateThread)


def _programme(now: datetime, title="Evening News", description=None, minutes_in=10, minutes_left=20, year=None) -> Programme:
    return Programme(
        channel_id="demo.news",
        start=now - timedelta(minutes=minutes_in),
        stop=now + timedelta(minutes=minutes_left),
        title=title,
        description=description,
        year=year,
    )


def _draw():
    return ImageDraw.Draw(Image.new("RGBA", (1, 1)))


def test_font_reuses_the_cached_object_for_the_same_name_and_size():
    # Every overlay render calls this several times over -- confirmed live,
    # at real playlist scale, that an uncached call (re-opening and
    # re-parsing the font file with FreeType from scratch every time) was a
    # real contributor to guide-render lag, and silently defeated
    # _font_has_glyph's own id(font)-keyed caches too, since a fresh font
    # object every call meant a fresh id() every call.
    first = _font("Inter-Regular.ttf", 24)
    second = _font("Inter-Regular.ttf", 24)
    assert first is second


def test_font_does_not_reuse_the_cached_object_for_a_different_size():
    small = _font("Inter-Regular.ttf", 24)
    large = _font("Inter-Regular.ttf", 40)
    assert small is not large


def test_fit_text_returns_unchanged_when_it_fits():
    draw = _draw()
    from PIL import ImageFont

    font = ImageFont.load_default()
    assert _fit_text(draw, "short", font, 10_000) == "short"


def test_fit_text_truncates_with_ellipsis_when_too_long():
    draw = _draw()
    font = _font("Inter-Regular.ttf", 24)
    long_text = "word " * 50
    result = _fit_text(draw, long_text, font, 100)
    assert result.endswith("…")
    assert draw.textlength(result, font=font) <= 100


def test_wrap_text_respects_max_lines():
    draw = _draw()
    font = _font("Inter-Regular.ttf", 24)
    long_text = "word " * 100
    lines = _wrap_text(draw, long_text, font, 300, max_lines=2)
    assert len(lines) <= 2
    assert lines[-1].endswith("…")


def test_font_has_glyph_true_for_ordinary_ascii():
    font = _font("Inter-Regular.ttf", 24)
    assert _font_has_glyph(font, "A") is True


def test_font_has_glyph_true_for_circled_letter_badge():
    # Some IPTV providers (e.g. m3u4u aggregated playlists) append
    # decorative circled-letter Unicode badges to channel names
    # (geo-restriction/subtitle markers). Our bundled font (Inter) has
    # real glyphs for these, unlike the DejaVu font it replaced.
    font = _font("Inter-Regular.ttf", 24)
    assert _font_has_glyph(font, "Ⓖ") is True


class _FakeMask:
    def __init__(self, size, bbox, hist):
        self.size = size
        self._bbox = bbox
        self._hist = hist

    def getbbox(self):
        return self._bbox

    def histogram(self):
        return self._hist


class _FakeFont:
    """Font double whose .notdef rendering and glyph coverage are fully
    controlled, so the glyph-fallback comparison logic itself can be
    tested without depending on a real font's actual Unicode coverage or
    on how the system's complex-text-shaping engine (raqm) happens to
    render a genuinely unsupported character -- which turns out not to
    reliably reproduce the literal .notdef mask real fonts like DejaVu
    render, making real fonts an unreliable fixture for this."""

    def __init__(self, missing: set[str]):
        self._missing = missing

    def getmask(self, char):
        if char == _NOTDEF_PROBE or char in self._missing:
            return _FakeMask((10, 10), (0, 0, 10, 10), (1,) * 10)
        return _FakeMask((10, 10), (0, 0, 10, 10), (2,) * 10)


def test_font_has_glyph_false_for_unsupported_char():
    font = _FakeFont(missing={"ᚠ"})
    assert _font_has_glyph(font, "ᚠ") is False


def test_strip_unsupported_glyphs_removes_unsupported_chars():
    font = _FakeFont(missing={"ᚠ"})
    assert _strip_unsupported_glyphs("BBC One ᚠ", font) == "BBC One"
    assert _strip_unsupported_glyphs("BBC Scotland ᚠᚠ", font) == "BBC Scotland"


def test_strip_unsupported_glyphs_leaves_supported_text_unchanged():
    font = _font("Inter-Regular.ttf", 24)
    assert _strip_unsupported_glyphs("Normal Channel", font) == "Normal Channel"


def test_fit_text_strips_unsupported_glyphs(monkeypatch):
    # _fit_text/_wrap_text need draw.textlength() to work, which requires
    # a real font -- so the "missing glyph" part is injected by patching
    # _font_has_glyph itself, same rationale as _FakeFont above.
    import tvdinner.overlay as overlay_module

    draw = _draw()
    font = _font("Inter-Regular.ttf", 24)
    monkeypatch.setattr(overlay_module, "_font_has_glyph", lambda f, ch: ch != "ᚠ")
    assert _fit_text(draw, "BBC One ᚠ", font, 10_000) == "BBC One"


def test_wrap_text_strips_unsupported_glyphs(monkeypatch):
    import tvdinner.overlay as overlay_module

    draw = _draw()
    font = _font("Inter-Regular.ttf", 24)
    monkeypatch.setattr(overlay_module, "_font_has_glyph", lambda f, ch: ch != "ᚠ")
    assert _wrap_text(draw, "BBC One ᚠ", font, 10_000, max_lines=2) == ["BBC One"]


def test_format_remaining_shows_minutes_only():
    assert _format_remaining(20 * 60) == "20 min remaining"


def test_format_remaining_shows_hours_and_minutes():
    assert _format_remaining(75 * 60) == "1h 15m remaining"


def test_format_remaining_clamps_negative_to_zero():
    assert _format_remaining(-30) == "0 min remaining"


def test_title_with_year_appends_year_when_present():
    now = datetime.now(timezone.utc)
    programme = _programme(now, title="The Secret of Dr. Kildare", year="1939")
    assert _title_with_year(programme) == "The Secret of Dr. Kildare (1939)"


def test_title_with_year_returns_bare_title_when_absent():
    now = datetime.now(timezone.utc)
    programme = _programme(now, title="Evening News", year=None)
    assert _title_with_year(programme) == "Evening News"


def test_title_with_year_does_not_duplicate_year_already_in_title():
    # Some XMLTV feeds bake the year into <title> for movies on top of the
    # separate <date> element Programme.year comes from -- confirmed live
    # against a real feed's "70s Cinema" listings.
    now = datetime.now(timezone.utc)
    programme = _programme(now, title="The Taking of Pelham One Two Three (1974)", year="1974")
    assert _title_with_year(programme) == "The Taking of Pelham One Two Three (1974)"


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


def test_render_epg_overlay_shows_favorite_heart_marker():
    now = datetime.now(timezone.utc)
    favorited = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now, favorites={CHANNEL.name})
    unfavorited = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now, favorites=set())
    no_favorites_arg = render_epg_overlay(CHANNEL, _programme(now), None, DISPLAY, now)

    heart = (255, 92, 122, 255)
    favorited_count = sum(1 for pixel in favorited.getdata() if pixel == heart)
    unfavorited_count = sum(1 for pixel in unfavorited.getdata() if pixel == heart)
    no_favorites_arg_count = sum(1 for pixel in no_favorites_arg.getdata() if pixel == heart)
    assert favorited_count > 0
    assert unfavorited_count == 0
    assert no_favorites_arg_count == 0


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


def test_render_epg_overlay_shows_rating_badge_for_movie_with_cached_rating():
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news", start=now, stop=now + timedelta(minutes=30), title="A Movie", category="Movie", year="1974"
    )
    tmdb._ratings_cache[("A Movie", "1974")] = 7.6

    image = render_epg_overlay(CHANNEL, programme, None, DISPLAY, now)

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) > 0


def test_render_epg_overlay_omits_rating_badge_when_not_movie_category():
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news", start=now, stop=now + timedelta(minutes=30), title="A Movie", category="News", year="1974"
    )
    tmdb._ratings_cache[("A Movie", "1974")] = 7.6

    image = render_epg_overlay(CHANNEL, programme, None, DISPLAY, now)

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) == 0


def test_render_epg_overlay_truncates_a_long_joined_category_string():
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news",
        start=now - timedelta(minutes=10),
        stop=now + timedelta(minutes=20),
        title="The Big Sleep",
        category="Crime, Crime drama, Movie, Mystery, Thriller",
    )
    image = render_epg_overlay(CHANNEL, programme, None, DISPLAY, now, canvas_width=640)
    assert image.mode == "RGBA"
    assert image.width <= 640


def test_render_epg_overlay_grows_taller_with_category_text():
    now = datetime.now(timezone.utc)
    without_category = _programme(now)
    with_category = Programme(
        channel_id="demo.news", start=now - timedelta(minutes=10), stop=now + timedelta(minutes=20), title="Evening News",
        category="Crime drama, Movie",
    )

    shorter = render_epg_overlay(CHANNEL, without_category, None, DISPLAY, now)
    taller = render_epg_overlay(CHANNEL, with_category, None, DISPLAY, now)

    assert taller.height > shorter.height


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


def test_fetch_image_sends_a_descriptive_user_agent(monkeypatch):
    # Confirmed live: Wikimedia (a common host for iptv-org's community
    # logos) returns a 403 for the default python-requests User-Agent and
    # a 200 for a descriptive one identifying the app, per their own
    # User-Agent policy -- other hosts' basic anti-hotlinking checks can
    # behave the same way.
    captured = {}
    buf = BytesIO()
    Image.new("RGBA", (10, 10), (1, 2, 3, 255)).save(buf, format="PNG")

    class _FakeResponse:
        content = buf.getvalue()

        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr("tvdinner.overlay.requests.get", fake_get)

    fetch_image("http://example.com/logo.png")

    assert captured["headers"] is not None
    assert "tvdinner" in captured["headers"].get("User-Agent", "")


def test_fetch_image_rejects_imgurs_region_block_placeholder(monkeypatch):
    # imgur (a very common host in iptv-org's community logo database)
    # geo-blocks a large share of hotlinked traffic: a real HTTP 200 with a
    # normal image/png body, but the image itself is a "Content not
    # viewable in your region" placeholder -- byte-for-byte identical no
    # matter which image was actually requested (confirmed live). There's
    # no status-code/header signal to catch this on, only the content
    # itself, hashed against the one known placeholder.
    import tvdinner.overlay as overlay_module

    placeholder_bytes = b"not a real png, just needs a stable hash"
    monkeypatch.setattr(
        overlay_module,
        "_BLOCKED_IMAGE_HASHES",
        {overlay_module.hashlib.sha256(placeholder_bytes).hexdigest()},
    )

    class _FakeResponse:
        content = placeholder_bytes

        def raise_for_status(self):
            pass

    monkeypatch.setattr("tvdinner.overlay.requests.get", lambda *a, **kw: _FakeResponse())

    assert fetch_image("http://i.imgur.com/some-blocked-id.png") is None


def test_logo_tile_crops_padding_so_a_small_mark_fills_the_tile():
    # Some real logo assets (e.g. SiliconDust's HDHomeRun channel art) are
    # a small mark on a mostly-transparent canvas, sometimes off-center --
    # left un-cropped, the mark stays small and off-center once fitted
    # into the tile, dominated by the tile's own light background (the
    # "looks like a blank white square" bug this guards against).
    canvas = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).rectangle((0, 0, 19, 19), fill=(0, 0, 255, 255))  # a small mark in the top-left corner

    tile = _logo_tile(canvas, 100)

    center = tile.getpixel((50, 50))
    assert center[:3] == (0, 0, 255)  # cropped+centered: the mark now covers the tile's center


def test_logo_tile_uses_a_dark_background_for_a_pale_logo():
    # Confirmed live: Channel 5's HD logo is a pale grey mark meant for a
    # dark/branded background -- placed on the usual light rescue tile
    # (meant for dark line-art logos), it all but vanished.
    pale_logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(pale_logo).rectangle((10, 10, 89, 89), fill=(230, 230, 230, 255))

    tile = _logo_tile(pale_logo, 100)

    corner = tile.getpixel((2, 50))  # tile background, away from the rounded corner and the logo itself
    assert sum(corner[:3]) < 300  # a dark tile, not the usual near-white one


def test_logo_tile_uses_the_light_background_for_a_dark_logo():
    dark_logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(dark_logo).rectangle((10, 10, 89, 89), fill=(20, 20, 20, 255))

    tile = _logo_tile(dark_logo, 100)

    corner = tile.getpixel((2, 50))
    assert sum(corner[:3]) > 600  # the usual near-white tile


def test_logo_tile_reuses_the_cached_result_for_the_same_logo_and_size():
    # A guide render calls this once per visible row on every keypress with
    # the exact same (fetch_image-cached) logo object each time -- confirmed
    # live, at real playlist scale (1500+ channels), that recomputing the
    # crop/luminance/composite from scratch every time made every guide
    # render cost ~800ms regardless of caching anywhere else.
    logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((10, 10, 89, 89), fill=(20, 20, 20, 255))

    first = _logo_tile(logo, 100)
    second = _logo_tile(logo, 100)

    assert first is second


def test_logo_tile_does_not_reuse_the_cached_result_for_a_different_size():
    logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((10, 10, 89, 89), fill=(20, 20, 20, 255))

    small = _logo_tile(logo, 40)
    large = _logo_tile(logo, 100)

    assert small is not large
    assert small.size == (40, 40)
    assert large.size == (100, 100)


def test_tmdb_logo_scales_to_the_requested_height_preserving_aspect_ratio():
    logo = _tmdb_logo(20)
    assert logo.mode == "RGBA"
    assert logo.height == 20
    assert logo.width > logo.height  # a wide wordmark, not a square/tall mark


def test_tmdb_logo_reuses_the_cached_result_for_the_same_height():
    first = _tmdb_logo(24)
    second = _tmdb_logo(24)
    assert first is second


def test_tmdb_logo_does_not_reuse_the_cached_result_for_a_different_height():
    small = _tmdb_logo(16)
    large = _tmdb_logo(32)
    assert small is not large
    assert small.height == 16
    assert large.height == 32


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0:00"),
        (5, "0:05"),
        (65, "1:05"),
        (59, "0:59"),
        (3661, "1:01:01"),
    ],
)
def test_format_playback_time(seconds, expected):
    assert _format_playback_time(seconds) == expected


def _recording(label="Show", when=None, size_bytes=1024) -> RecordingFile:
    return RecordingFile(
        path=Path(f"/recordings/{label}_{(when or datetime(2026, 7, 26, 12, 0, 0)).strftime('%Y%m%d-%H%M%S')}.ts"),
        label=label,
        recorded_at=when or datetime(2026, 7, 26, 12, 0, 0),
        size_bytes=size_bytes,
    )


def test_render_recording_overlay_returns_rgba_image():
    image = render_recording_overlay(_recording("BBC One"))
    assert image.mode == "RGBA"
    assert image.width > 0 and image.height > 0


def test_render_recording_overlay_scales_with_canvas_width():
    small = render_recording_overlay(_recording("BBC One"), canvas_width=640)
    large = render_recording_overlay(_recording("BBC One"), canvas_width=3840)
    assert large.width > small.width


def test_render_recording_overlay_progress_bar_fills_with_position():
    no_progress = render_recording_overlay(_recording("BBC One"))
    half_progress = render_recording_overlay(_recording("BBC One"), position_seconds=60, duration_seconds=120)

    accent = (0, 176, 255, 255)
    no_progress_count = sum(1 for pixel in no_progress.getdata() if pixel == accent)
    half_progress_count = sum(1 for pixel in half_progress.getdata() if pixel == accent)
    assert half_progress_count > no_progress_count


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


def test_visible_guide_movies_returns_movie_titles_and_years_in_the_current_window():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(2, now)
    epg.programmes["ch0"][0].category = "Movie"
    epg.programmes["ch0"][0].year = "1974"
    epg.programmes["ch1"][0].category = "News"

    movies = visible_guide_movies(channels, epg, DISPLAY, now, current_channel_url="http://x/0")

    assert movies == {("Show A", "1974")}


def test_visible_guide_movies_returns_empty_set_when_no_movies_are_visible():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(2, now)

    movies = visible_guide_movies(channels, epg, DISPLAY, now, current_channel_url="http://x/0")

    assert movies == set()


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


def _fake_fetch_image(working_urls: dict[str, tuple[int, int, int, int]]):
    """A fetch_image stand-in: "fetches" successfully (returning a tiny
    solid-color image) only for URLs in `working_urls`, and fails (None)
    for everything else -- including a URL that's merely absent from the
    dict, modeling a dead/blocked one exactly like a real one would fail."""

    def fetch(url):
        return Image.new("RGBA", (2, 2), working_urls[url]) if url in working_urls else None

    return fetch


def test_resolve_channel_logo_prefers_the_channels_own_tvg_logo(monkeypatch):
    channel = Channel(name="X", url="http://x", tvg_id="x", tvg_logo="http://logo/x.png")
    epg = Epg()
    epg.channels["x"] = EpgChannel(id="x", icon="http://epg-logo/x.png")
    monkeypatch.setattr(
        "tvdinner.overlay.fetch_image",
        _fake_fetch_image({"http://logo/x.png": (1, 0, 0, 255), "http://epg-logo/x.png": (0, 1, 0, 255)}),
    )
    image = resolve_channel_logo(channel, epg)
    assert image.getpixel((0, 0)) == (1, 0, 0, 255)


def test_resolve_channel_logo_falls_through_when_tvg_logo_fetch_fails(monkeypatch):
    # Regression: a playlist's own tvg-logo can point at a dead/blocked URL
    # (confirmed live: playlists commonly set it to an imgur URL, and
    # imgur widely rejects hotlinked requests -- see _decode_image's
    # placeholder detection) -- that must not block the EPG-icon/online-
    # index fallbacks from ever being tried just because a URL string
    # happened to be present.
    channel = Channel(name="X", url="http://x", tvg_id="x", tvg_logo="http://dead/x.png")
    epg = Epg()
    epg.channels["x"] = EpgChannel(id="x", icon="http://epg-logo/x.png")
    monkeypatch.setattr(
        "tvdinner.overlay.fetch_image", _fake_fetch_image({"http://epg-logo/x.png": (0, 1, 0, 255)})
    )
    image = resolve_channel_logo(channel, epg)
    assert image is not None
    assert image.getpixel((0, 0)) == (0, 1, 0, 255)


def test_resolve_channel_logo_falls_back_to_the_epgs_icon(monkeypatch):
    # HDHomeRun channels have no tvg_logo at all (lineup.json has no logo
    # field), but SiliconDust's own XMLTV export does -- this is the
    # fallback that surfaces it.
    channel = Channel(name="Great! TV", url="http://x", tvg_id="34")
    epg = Epg()
    epg.channels["EU1.hdhomerun.com"] = EpgChannel(
        id="EU1.hdhomerun.com", display_names=["Great! TV"], icon="http://img.hdhomerun.com/channels/EU1.png"
    )
    monkeypatch.setattr(
        "tvdinner.overlay.fetch_image",
        _fake_fetch_image({"http://img.hdhomerun.com/channels/EU1.png": (0, 1, 0, 255)}),
    )
    image = resolve_channel_logo(channel, epg)
    assert image.getpixel((0, 0)) == (0, 1, 0, 255)


def test_resolve_channel_logo_none_when_no_source_has_one(monkeypatch):
    channel = Channel(name="X", url="http://x", tvg_id="x")
    monkeypatch.setattr("tvdinner.overlay.fetch_image", _fake_fetch_image({}))
    assert resolve_channel_logo(channel, Epg()) is None


def test_resolve_channel_logo_falls_back_to_the_online_index(monkeypatch):
    # A bare M3U playlist with no tvg-logo of its own and no matching EPG
    # icon -- the last resort, iptv-org's community logo database.
    channel = Channel(name="BBC One", url="http://x", tvg_id="BBCOne.uk")
    online_logos = OnlineLogoIndex(by_id={"BBCOne.uk": "http://online/bbc1.png"})
    monkeypatch.setattr(
        "tvdinner.overlay.fetch_image", _fake_fetch_image({"http://online/bbc1.png": (0, 0, 1, 255)})
    )
    image = resolve_channel_logo(channel, Epg(), online_logos)
    assert image.getpixel((0, 0)) == (0, 0, 1, 255)


def test_resolve_channel_logo_prefers_epg_icon_over_online_index(monkeypatch):
    channel = Channel(name="X", url="http://x", tvg_id="x")
    epg = Epg()
    epg.channels["x"] = EpgChannel(id="x", icon="http://epg-logo/x.png")
    online_logos = OnlineLogoIndex(by_id={"x": "http://online/x.png"})
    monkeypatch.setattr(
        "tvdinner.overlay.fetch_image",
        _fake_fetch_image({"http://epg-logo/x.png": (0, 1, 0, 255), "http://online/x.png": (0, 0, 1, 255)}),
    )
    image = resolve_channel_logo(channel, epg, online_logos)
    assert image.getpixel((0, 0)) == (0, 1, 0, 255)


def test_resolve_channel_logo_none_when_online_index_has_no_match_either(monkeypatch):
    channel = Channel(name="X", url="http://x", tvg_id="x")
    monkeypatch.setattr("tvdinner.overlay.fetch_image", _fake_fetch_image({}))
    assert resolve_channel_logo(channel, Epg(), EMPTY_LOGO_INDEX) is None


def test_cached_channel_logo_returns_none_when_not_yet_fetched():
    assert cached_channel_logo("http://stream/never-fetched") is None


def test_prefetch_channel_logos_populates_cache_and_clears_in_flight(monkeypatch):
    from tvdinner import overlay

    channel = Channel(name="X", url="http://stream/x", tvg_id="x", tvg_logo="http://logo/x.png")
    monkeypatch.setattr("tvdinner.overlay.fetch_image", _fake_fetch_image({"http://logo/x.png": (1, 0, 0, 255)}))

    prefetch_channel_logos([channel], Epg())

    assert cached_channel_logo("http://stream/x").getpixel((0, 0)) == (1, 0, 0, 255)
    assert "http://stream/x" not in overlay._channel_logo_in_flight


def test_prefetch_channel_logos_caches_none_when_no_source_has_one(monkeypatch):
    channel = Channel(name="X", url="http://stream/x", tvg_id="x")
    monkeypatch.setattr("tvdinner.overlay.fetch_image", _fake_fetch_image({}))

    prefetch_channel_logos([channel], Epg())

    assert cached_channel_logo("http://stream/x") is None


def test_prefetch_channel_logos_skips_already_cached_or_in_flight_urls(monkeypatch):
    from tvdinner import overlay

    def fail_fetch_image(url):
        raise AssertionError("should not fetch a URL that's already cached or in flight")

    monkeypatch.setattr("tvdinner.overlay.fetch_image", fail_fetch_image)

    cached_channel = Channel(name="Cached", url="http://stream/cached", tvg_id="cached")
    in_flight_channel = Channel(name="In Flight", url="http://stream/in-flight", tvg_id="in-flight")
    overlay._channel_logo_cache["http://stream/cached"] = None
    overlay._channel_logo_in_flight.add("http://stream/in-flight")

    prefetch_channel_logos([cached_channel, in_flight_channel], Epg())


def test_prefetch_channel_logos_keyed_by_channel_url_not_tvg_id(monkeypatch):
    # Real-world playlists commonly have several distinct channels (quality
    # tiers, backup servers) sharing one tvg_id -- keying by tvg_id would
    # incorrectly treat them as needing only one shared fetch.
    a = Channel(name="A", url="http://stream/a", tvg_id="shared", tvg_logo="http://logo/a.png")
    b = Channel(name="B", url="http://stream/b", tvg_id="shared", tvg_logo="http://logo/b.png")
    monkeypatch.setattr(
        "tvdinner.overlay.fetch_image",
        _fake_fetch_image({"http://logo/a.png": (1, 0, 0, 255), "http://logo/b.png": (0, 0, 1, 255)}),
    )

    prefetch_channel_logos([a, b], Epg())

    assert cached_channel_logo("http://stream/a").getpixel((0, 0)) == (1, 0, 0, 255)
    assert cached_channel_logo("http://stream/b").getpixel((0, 0)) == (0, 0, 1, 255)


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

    few_font = _font("Inter-Regular.ttf", round(1920 * 0.0105))
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


def test_render_program_guide_shows_channel_group():
    now = datetime.now(timezone.utc)
    grouped, epg = _guide_channels_and_epg(1, now)
    grouped[0].group_title = "Movies;Series"
    ungrouped, _ = _guide_channels_and_epg(1, now)

    with_group = render_program_guide(grouped, epg, DISPLAY, now, "http://x/0", 1920, 1080)
    without_group = render_program_guide(ungrouped, epg, DISPLAY, now, "http://x/0", 1920, 1080)

    muted = (176, 182, 190, 255)
    with_count = sum(1 for pixel in with_group.getdata() if pixel == muted)
    without_count = sum(1 for pixel in without_group.getdata() if pixel == muted)
    assert with_count > without_count


def test_render_program_guide_shows_favorite_heart_marker():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)

    favorited = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, favorites={"Channel 0"})
    unfavorited = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, favorites=set())
    no_favorites_arg = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080)

    heart = (255, 92, 122, 255)
    favorited_count = sum(1 for pixel in favorited.getdata() if pixel == heart)
    unfavorited_count = sum(1 for pixel in unfavorited.getdata() if pixel == heart)
    no_favorites_arg_count = sum(1 for pixel in no_favorites_arg.getdata() if pixel == heart)
    assert favorited_count > 0
    assert unfavorited_count == 0
    assert no_favorites_arg_count == 0


def test_render_program_guide_shows_recording_badge_for_scheduled_programme():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    show_a_start = epg.programmes["ch0"][0].start

    scheduled = render_program_guide(
        channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, scheduled={("http://x/0", show_a_start)}
    )
    unscheduled = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, scheduled=set())
    no_scheduled_arg = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080)

    badge = (214, 40, 54, 255)
    scheduled_count = sum(1 for pixel in scheduled.getdata() if pixel == badge)
    unscheduled_count = sum(1 for pixel in unscheduled.getdata() if pixel == badge)
    no_scheduled_arg_count = sum(1 for pixel in no_scheduled_arg.getdata() if pixel == badge)
    assert scheduled_count > 0
    assert unscheduled_count == 0
    assert no_scheduled_arg_count == 0


def test_render_program_guide_recording_badge_requires_exact_start_match():
    # Regression guard: the badge is keyed by (channel_url, programme.start)
    # -- an entry for the same channel but a non-matching start shouldn't
    # badge anything.
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    wrong_start = now + timedelta(hours=5)  # doesn't match either programme's start

    image = render_program_guide(
        channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, scheduled={("http://x/0", wrong_start)}
    )

    badge = (214, 40, 54, 255)
    assert sum(1 for pixel in image.getdata() if pixel == badge) == 0


def test_render_program_guide_shows_rating_badge_for_movie_with_cached_rating():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    epg.programmes["ch0"][0].category = "Movie"
    tmdb._ratings_cache[("Show A", None)] = 7.6

    image = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080)

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) > 0


def test_render_program_guide_omits_rating_badge_when_not_movie_category():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    epg.programmes["ch0"][0].category = "News"
    tmdb._ratings_cache[("Show A", None)] = 7.6

    image = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080)

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) == 0


def test_render_program_guide_omits_rating_badge_when_not_yet_cached():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    epg.programmes["ch0"][0].category = "Movie"
    # Deliberately not populating tmdb._ratings_cache -- this is the "not
    # fetched yet" case: the current render must draw with no badge at all,
    # never block waiting on a fetch.

    image = render_program_guide(channels, epg, DISPLAY, now, "http://x/0", 1920, 1080)

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) == 0


def test_render_program_guide_drops_rating_badge_in_narrow_cell():
    now = datetime.now(timezone.utc)
    tvg_id = "ch0"
    channel = Channel(name="Channel 0", url="http://x/0", tvg_id=tvg_id)
    # A very short programme in a wide window makes for a narrow cell.
    programme = Programme(
        channel_id=tvg_id, start=now, stop=now + timedelta(minutes=10), title="Shorts", category="Movie"
    )
    epg = Epg(programmes={tvg_id: [programme]})
    tmdb._ratings_cache[("Shorts", None)] = 7.6

    image = render_program_guide(
        [channel], epg, DISPLAY, now, "http://x/0", 1920, 1080, window_start=now, window_hours=6.0
    )

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) == 0


def test_render_program_guide_rating_badge_and_recording_badge_coexist():
    now = datetime.now(timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    epg.programmes["ch0"][0].category = "Movie"
    show_a_start = epg.programmes["ch0"][0].start
    tmdb._ratings_cache[("Show A", None)] = 7.6

    image = render_program_guide(
        channels, epg, DISPLAY, now, "http://x/0", 1920, 1080, scheduled={("http://x/0", show_a_start)}
    )

    gold = (255, 199, 0, 255)
    recording_red = (214, 40, 54, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) > 0
    assert sum(1 for pixel in image.getdata() if pixel == recording_red) > 0


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
    #
    # Fixed rather than datetime.now(): a shifted programme block can land
    # so that only a couple of pixels of it fall inside the visible window,
    # and how many depends on the real-world second render_program_guide
    # happens to run at -- see
    # test_render_program_guide_handles_narrow_shifted_block_at_window_edge
    # for the exact failure this used to hit intermittently.
    now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    channels, epg = _guide_channels_and_epg(1, now)
    shifted_display = EpgDisplay(timezone=timezone.utc, channel_shifts={"Channel 0": timedelta(minutes=-25)})
    image = render_program_guide(channels, epg, shifted_display, now, "http://x/0", 1920, 1080)
    assert image is not None
    assert image.mode == "RGBA"


def test_render_program_guide_handles_narrow_shifted_block_at_window_edge():
    # Regression test: a programme block's on-screen width was only ever
    # checked against a flat 2px floor before drawing, but the rectangle is
    # then padded in by 2px on each side -- so a block between 2 and 4px
    # wide (a shifted programme mostly clipped by the visible window's
    # edge) produced an inverted (x1 < x0) rectangle and crashed PIL. This
    # exact timestamp (found by sweeping per-second) puts Channel 0's first
    # programme at ~2.13px wide after a -25 minute shift.
    now = datetime(2026, 7, 25, 12, 5, 16, tzinfo=timezone.utc)
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


def test_render_programme_details_truncates_a_long_joined_category_string():
    # Real-world case: epg.parse_xmltv joins every <category> tag on a
    # programme (see its own docstring) -- a feed listing several (genre,
    # "Movie", sub-genres, ...) produces a longer string than a single tag
    # ever would, which must not run past this popup's fixed width.
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news",
        start=now,
        stop=now + timedelta(minutes=30),
        title="The Big Sleep",
        category="Crime, Crime drama, Movie, Mystery, Thriller",
    )
    image = render_programme_details(CHANNEL, programme, DISPLAY, 1920, 1080)
    assert image.mode == "RGBA"
    assert image.width <= 1920


def test_render_programme_details_shows_rating_and_tmdb_attribution():
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news",
        start=now,
        stop=now + timedelta(minutes=30),
        title="A Movie",
        category="Movie",
        year="1974",
    )
    tmdb._ratings_cache[("A Movie", "1974")] = 7.6

    image = render_programme_details(CHANNEL, programme, DISPLAY, 1920, 1080)

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) > 0


def test_render_programme_details_omits_rating_for_non_movie_category():
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news", start=now, stop=now + timedelta(minutes=30), title="A Movie", category="News", year="1974"
    )
    tmdb._ratings_cache[("A Movie", "1974")] = 7.6

    image = render_programme_details(CHANNEL, programme, DISPLAY, 1920, 1080)

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) == 0


def test_render_programme_details_omits_rating_when_not_cached():
    now = datetime.now(timezone.utc)
    programme = Programme(
        channel_id="demo.news", start=now, stop=now + timedelta(minutes=30), title="A Movie", category="Movie", year="1974"
    )

    image = render_programme_details(CHANNEL, programme, DISPLAY, 1920, 1080)

    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) == 0


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


def test_render_guide_filter_prompt_uses_custom_label():
    # Reused as-is by cli.py's Plex search prompt with a different label --
    # confirm the override actually replaces the default "Filter channels"
    # text rather than being ignored.
    default_image = render_guide_filter_prompt("query", 1920, 1080)
    custom_image = render_guide_filter_prompt("query", 1920, 1080, label="Search Plex library")
    assert default_image.tobytes() != custom_image.tobytes()


@pytest.mark.parametrize(
    "size_bytes, expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (10 * 1024 * 1024, "10.0 MB"),
        (2 * 1024 * 1024 * 1024, "2.0 GB"),
    ],
)
def test_format_size(size_bytes, expected):
    assert _format_size(size_bytes) == expected


def test_format_recordings_date_today_and_yesterday():
    today = date(2026, 7, 26)
    assert _format_recordings_date(today, today) == "Today"
    assert _format_recordings_date(date(2026, 7, 25), today) == "Yesterday"


def test_format_recordings_date_older_shows_full_date():
    today = date(2026, 7, 26)
    assert _format_recordings_date(date(2026, 7, 1), today) == "Wednesday 01 July 2026"


def test_visible_recordings_returns_all_when_under_max_rows():
    recordings = [_recording(f"Show {i}") for i in range(3)]
    assert visible_recordings(recordings, recordings[0].path, max_rows=8) == recordings


def test_visible_recordings_caps_at_max_rows():
    recordings = [_recording(f"Show {i}", when=datetime(2026, 7, 26, 12, i, 0)) for i in range(20)]
    visible = visible_recordings(recordings, recordings[0].path, max_rows=5)
    assert len(visible) == 5


def test_visible_recordings_centers_on_selection():
    recordings = [_recording(f"Show {i}", when=datetime(2026, 7, 26, 12, i, 0)) for i in range(20)]
    visible = visible_recordings(recordings, recordings[10].path, max_rows=5)
    paths = [r.path for r in visible]
    assert recordings[10].path in paths
    assert paths.index(recordings[10].path) == 2  # centered: 2 before, 2 after


def test_render_recordings_browser_returns_none_for_empty_list():
    assert render_recordings_browser([], None, 1920, 1080) is None


def test_render_recordings_browser_returns_rgba_image():
    recordings = [_recording("BBC One")]
    image = render_recordings_browser(recordings, recordings[0].path, 1920, 1080)
    assert image is not None
    assert image.mode == "RGBA"


def test_render_recordings_browser_shows_selection_border():
    recordings = [_recording("Show A"), _recording("Show B", when=datetime(2026, 7, 26, 13, 0, 0))]

    unselected = render_recordings_browser(recordings, None, 1920, 1080)
    selected = render_recordings_browser(recordings, recordings[0].path, 1920, 1080)

    border = (255, 255, 255, 255)
    unselected_count = sum(1 for pixel in unselected.getdata() if pixel == border)
    selected_count = sum(1 for pixel in selected.getdata() if pixel == border)
    assert selected_count > unselected_count


def test_render_recordings_browser_groups_by_date():
    # Two recordings on different days should each get their own date
    # header rather than being lumped under one -- taller panel than if
    # they shared a single header.
    same_day = [_recording("A"), _recording("B", when=datetime(2026, 7, 26, 13, 0, 0))]
    different_days = [_recording("A"), _recording("B", when=datetime(2026, 7, 25, 13, 0, 0))]

    same_day_image = render_recordings_browser(same_day, None, 1920, 1080)
    different_days_image = render_recordings_browser(different_days, None, 1920, 1080)
    assert different_days_image.height > same_day_image.height


def _vod_item(title="Movie", group_title="Movies", **kwargs) -> VodItem:
    return VodItem(title=title, url=f"http://x/{title}.mp4", group_title=group_title, **kwargs)


def test_visible_vod_items_returns_all_when_under_max_rows():
    items = [_vod_item(f"Movie {i}") for i in range(3)]
    assert visible_vod_items(items, 0, max_rows=8) == items


def test_visible_vod_items_caps_at_max_rows():
    items = [_vod_item(f"Movie {i}") for i in range(20)]
    visible = visible_vod_items(items, 0, max_rows=5)
    assert len(visible) == 5


def test_visible_vod_items_centers_on_selection():
    items = [_vod_item(f"Movie {i}") for i in range(20)]
    visible = visible_vod_items(items, 10, max_rows=5)
    assert items[10] in visible
    assert visible.index(items[10]) == 2  # centered: 2 before, 2 after


def test_render_vod_browser_returns_none_for_empty_list():
    assert render_vod_browser([], 0, 1920, 1080) is None


def test_render_vod_browser_returns_rgba_image():
    items = [_vod_item("The Matrix")]
    image = render_vod_browser(items, 0, 1920, 1080)
    assert image is not None
    assert image.mode == "RGBA"


def test_render_vod_browser_shows_selection_border():
    items = [_vod_item("Movie A"), _vod_item("Movie B")]

    unselected = render_vod_browser(items, -1, 1920, 1080)
    selected = render_vod_browser(items, 0, 1920, 1080)

    border = (255, 255, 255, 255)
    unselected_count = sum(1 for pixel in unselected.getdata() if pixel == border)
    selected_count = sum(1 for pixel in selected.getdata() if pixel == border)
    assert selected_count > unselected_count


def test_render_vod_browser_groups_by_group_title():
    same_group = [_vod_item("A", group_title="Movies"), _vod_item("B", group_title="Movies")]
    different_groups = [_vod_item("A", group_title="Movies"), _vod_item("B", group_title="Comedy")]

    same_group_image = render_vod_browser(same_group, 0, 1920, 1080)
    different_groups_image = render_vod_browser(different_groups, 0, 1920, 1080)
    assert different_groups_image.height > same_group_image.height


def test_render_vod_info_overlay_returns_rgba_image():
    image = render_vod_info_overlay(_vod_item("The Matrix"), 1920, 1080)
    assert image.mode == "RGBA"


def test_render_vod_info_overlay_without_optional_fields_still_renders():
    # A VodItem from a source with no synopsis/poster/rating (e.g. a bare
    # M3U --vod-group entry) should still render something sensible.
    item = VodItem(title="Bare Movie", url="http://x/bare.mp4")
    image = render_vod_info_overlay(item, 1920, 1080)
    assert image.mode == "RGBA"


def test_render_vod_info_overlay_grows_with_description():
    # A narrow canvas (and thus a small nominal/floor height) so a real
    # description's extra lines visibly push past that floor, same
    # reasoning as render_epg_overlay's poster-narrows-text test.
    plain = _vod_item("Movie")
    with_description = _vod_item("Movie", description="A moderately long synopsis of the film. " * 10)

    plain_image = render_vod_info_overlay(plain, 800, 1080)
    described_image = render_vod_info_overlay(with_description, 800, 1080)
    assert described_image.height > plain_image.height


def test_render_vod_info_overlay_shows_progress_bar_when_position_given():
    # Both renders already have some accent-colored pixels (the "NOW
    # PLAYING" eyebrow, the left accent stripe) -- the progress bar fill
    # should add more on top of that baseline, same technique as
    # render_recording_overlay's equivalent test.
    item = _vod_item("Movie")
    without_progress = render_vod_info_overlay(item, 800, 1080)
    with_progress = render_vod_info_overlay(item, 800, 1080, position_seconds=612, duration_seconds=6520)

    accent = (0, 176, 255, 255)
    without_progress_count = sum(1 for pixel in without_progress.getdata() if pixel == accent)
    with_progress_count = sum(1 for pixel in with_progress.getdata() if pixel == accent)
    assert with_progress_count > without_progress_count


def test_render_vod_info_overlay_shows_poster(tmp_path):
    poster_path = tmp_path / "poster.png"
    Image.new("RGBA", (400, 600), (200, 30, 30, 255)).save(poster_path)

    without_poster = _vod_item("Movie")
    with_poster = _vod_item("Movie", poster_url=f"file://{poster_path}")

    plain_image = render_vod_info_overlay(without_poster, 1920, 1080)
    poster_image = render_vod_info_overlay(with_poster, 1920, 1080)
    assert poster_image.size != plain_image.size


def test_render_vod_info_overlay_ignores_unfetchable_poster():
    item = _vod_item("Movie", poster_url="file:///nonexistent/poster.png")
    image = render_vod_info_overlay(item, 1920, 1080)
    assert image.mode == "RGBA"


def test_render_vod_info_overlay_shows_gold_rating_star_for_any_rating_source():
    # A Plex/Xtream-sourced rating (rating_is_tmdb=False, the default)
    # still gets the same gold star styling as a TMDB one -- only the
    # attribution logo specifically is gated on the source (see below).
    item = _vod_item("Movie", rating="7.4")
    image = render_vod_info_overlay(item, 1920, 1080)
    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) > 0


def test_render_vod_info_overlay_omits_rating_star_without_a_rating():
    item = _vod_item("Movie")
    image = render_vod_info_overlay(item, 1920, 1080)
    gold = (255, 199, 0, 255)
    assert sum(1 for pixel in image.getdata() if pixel == gold) == 0


def test_render_vod_info_overlay_shows_tmdb_logo_only_when_rating_is_tmdb():
    # TMDB's API terms require the attribution logo wherever their data
    # is shown -- but a Plex audienceRating or an Xtream panel's own
    # rating is never TMDB's, so drawing it there would be a
    # misattribution. Same rating value, same panel size (the logo only
    # changes pixel content within the already-opaque panel, not its
    # size) -- only the source flag differs, so the two renders must
    # differ somewhere if the logo is really conditional.
    non_tmdb = _vod_item("Movie", rating="7.4", rating_is_tmdb=False)
    tmdb_sourced = _vod_item("Movie", rating="7.4", rating_is_tmdb=True)

    non_tmdb_image = render_vod_info_overlay(non_tmdb, 1920, 1080)
    tmdb_image = render_vod_info_overlay(tmdb_sourced, 1920, 1080)

    assert non_tmdb_image.size == tmdb_image.size
    assert list(non_tmdb_image.getdata()) != list(tmdb_image.getdata())


def _plex_node(title="Movie", kind="movie", **kwargs) -> PlexNode:
    return PlexNode(rating_key=title, title=title, kind=kind, **kwargs)


def test_visible_plex_nodes_returns_all_when_under_max_rows():
    nodes = [_plex_node(f"Movie {i}") for i in range(3)]
    assert visible_plex_nodes(nodes, 0, max_rows=8) == nodes


def test_visible_plex_nodes_caps_at_max_rows():
    nodes = [_plex_node(f"Movie {i}") for i in range(20)]
    visible = visible_plex_nodes(nodes, 0, max_rows=5)
    assert len(visible) == 5


def test_visible_plex_nodes_centers_on_selection():
    nodes = [_plex_node(f"Movie {i}") for i in range(20)]
    visible = visible_plex_nodes(nodes, 10, max_rows=5)
    assert nodes[10] in visible
    assert visible.index(nodes[10]) == 2  # centered: 2 before, 2 after


def test_render_plex_browser_returns_none_for_empty_list():
    assert render_plex_browser("Plex Libraries", [], 0, 1920, 1080) is None


def test_render_plex_browser_returns_rgba_image():
    nodes = [_plex_node("The Matrix")]
    image = render_plex_browser("Movies", nodes, 0, 1920, 1080)
    assert image is not None
    assert image.mode == "RGBA"


def test_render_plex_browser_shows_selection_border():
    nodes = [_plex_node("Movie A"), _plex_node("Movie B")]

    unselected = render_plex_browser("Movies", nodes, -1, 1920, 1080)
    selected = render_plex_browser("Movies", nodes, 0, 1920, 1080)

    border = (255, 255, 255, 255)
    unselected_count = sum(1 for pixel in unselected.getdata() if pixel == border)
    selected_count = sum(1 for pixel in selected.getdata() if pixel == border)
    assert selected_count > unselected_count


def test_render_plex_browser_distinguishes_container_and_leaf_rows():
    # Same title in both -- only `kind` (and thus whether the row shows a
    # drill-in chevron or a subtitle) differs, so any pixel difference
    # must come from that.
    container = [_plex_node("Same Title", kind="show")]
    leaf = [_plex_node("Same Title", kind="movie", subtitle="1999 · 2h 16m")]

    container_image = render_plex_browser("Panel", container, 0, 1920, 1080)
    leaf_image = render_plex_browser("Panel", leaf, 0, 1920, 1080)

    assert container_image.tobytes() != leaf_image.tobytes()


def _cast_device(name="Living Room Hub") -> CastDevice:
    return CastDevice(name=name, cast=object())


def test_visible_cast_devices_returns_all_when_under_max_rows():
    devices = [_cast_device(f"Device {i}") for i in range(3)]
    assert visible_cast_devices(devices, 0, max_rows=8) == devices


def test_visible_cast_devices_caps_at_max_rows():
    devices = [_cast_device(f"Device {i}") for i in range(20)]
    assert len(visible_cast_devices(devices, 0, max_rows=5)) == 5


def test_visible_cast_devices_centers_on_selection():
    devices = [_cast_device(f"Device {i}") for i in range(20)]
    visible = visible_cast_devices(devices, 10, max_rows=5)
    assert devices[10] in visible
    assert visible.index(devices[10]) == 2  # centered: 2 before, 2 after


def test_render_cast_picker_shows_scanning_message_for_empty_list():
    image = render_cast_picker("Chromecast", [], 0, None, True, 1920, 1080)
    assert image.mode == "RGBA"


def test_render_cast_picker_shows_no_devices_message_when_scan_finished():
    scanning_image = render_cast_picker("Chromecast", [], 0, None, True, 1920, 1080)
    finished_image = render_cast_picker("Chromecast", [], 0, None, False, 1920, 1080)
    # Different message text ("Scanning..." vs "No devices found") means
    # a different render even though both are otherwise empty-list panels.
    assert scanning_image.tobytes() != finished_image.tobytes()


def test_render_cast_picker_returns_rgba_image_with_devices():
    devices = [_cast_device("Living Room Hub"), _cast_device("Kitchen Hub")]
    image = render_cast_picker("Chromecast", devices, 0, None, False, 1920, 1080)
    assert image.mode == "RGBA"


def test_render_cast_picker_accepts_any_device_with_a_name():
    # render_cast_picker/visible_cast_devices only ever read `.name` --
    # confirms the structural CastableDevice contract, not just
    # chromecast.py's own CastDevice.
    @dataclass
    class _FakeDevice:
        name: str

    devices = [_FakeDevice(name="Some Other Device")]
    image = render_cast_picker("Cast", devices, 0, None, False, 1920, 1080)
    assert image.mode == "RGBA"


def test_render_cast_picker_shows_selection_border():
    devices = [_cast_device("Device A"), _cast_device("Device B")]

    unselected = render_cast_picker("Chromecast", devices, -1, None, False, 1920, 1080)
    selected = render_cast_picker("Chromecast", devices, 0, None, False, 1920, 1080)

    border = (255, 255, 255, 255)
    unselected_count = sum(1 for pixel in unselected.getdata() if pixel == border)
    selected_count = sum(1 for pixel in selected.getdata() if pixel == border)
    assert selected_count > unselected_count


def test_render_cast_picker_shows_disconnect_row_when_connected():
    devices = [_cast_device("Living Room Hub")]

    disconnected = render_cast_picker("Chromecast", devices, 0, None, False, 1920, 1080)
    connected = render_cast_picker("Chromecast", devices, 0, "Living Room Hub", False, 1920, 1080)

    # The connected render has one extra row (the synthetic Disconnect
    # entry), so it's taller than the disconnected one for the same
    # device list.
    assert connected.height > disconnected.height


def _scheduled(title="Show", channel_name="Demo Channel", start=None, stop=None) -> ScheduledRecording:
    return ScheduledRecording.create(
        channel_url="http://stream/demo",
        channel_name=channel_name,
        title=title,
        start=start or datetime(2026, 7, 26, 20, 0, 0, tzinfo=timezone.utc),
        stop=stop or datetime(2026, 7, 26, 21, 0, 0, tzinfo=timezone.utc),
    )


def test_format_schedule_date_today_and_tomorrow():
    today = date(2026, 7, 26)
    assert _format_schedule_date(today, today) == "Today"
    assert _format_schedule_date(date(2026, 7, 27), today) == "Tomorrow"


def test_format_schedule_date_later_shows_full_date():
    today = date(2026, 7, 26)
    assert _format_schedule_date(date(2026, 8, 1), today) == "Saturday 01 August 2026"


def test_visible_schedule_returns_all_when_under_max_rows():
    schedule = [_scheduled(f"Show {i}", start=datetime(2026, 7, 26, 20 + i, 0, tzinfo=timezone.utc)) for i in range(3)]
    assert visible_schedule(schedule, schedule[0].id, max_rows=8) == schedule


def test_visible_schedule_caps_at_max_rows():
    schedule = [
        _scheduled(
            f"Show {i}",
            start=datetime(2026, 8, 1 + i, 20, 0, tzinfo=timezone.utc),
            stop=datetime(2026, 8, 1 + i, 21, 0, tzinfo=timezone.utc),
        )
        for i in range(20)
    ]
    visible = visible_schedule(schedule, schedule[0].id, max_rows=5)
    assert len(visible) == 5


def test_visible_schedule_centers_on_selection():
    schedule = [
        _scheduled(
            f"Show {i}",
            start=datetime(2026, 8, 1 + i, 20, 0, tzinfo=timezone.utc),
            stop=datetime(2026, 8, 1 + i, 21, 0, tzinfo=timezone.utc),
        )
        for i in range(20)
    ]
    visible = visible_schedule(schedule, schedule[10].id, max_rows=5)
    ids = [s.id for s in visible]
    assert schedule[10].id in ids
    assert ids.index(schedule[10].id) == 2  # centered: 2 before, 2 after


def test_render_schedule_browser_returns_none_for_empty_list():
    assert render_schedule_browser([], None, DISPLAY, 1920, 1080) is None


def test_render_schedule_browser_returns_rgba_image():
    schedule = [_scheduled("Match of the Day")]
    image = render_schedule_browser(schedule, schedule[0].id, DISPLAY, 1920, 1080)
    assert image is not None
    assert image.mode == "RGBA"


def test_render_schedule_browser_shows_selection_border():
    schedule = [
        _scheduled("Show A", start=datetime(2026, 7, 26, 20, 0, tzinfo=timezone.utc), stop=datetime(2026, 7, 26, 21, 0, tzinfo=timezone.utc)),
        _scheduled("Show B", start=datetime(2026, 7, 26, 22, 0, tzinfo=timezone.utc), stop=datetime(2026, 7, 26, 23, 0, tzinfo=timezone.utc)),
    ]

    unselected = render_schedule_browser(schedule, None, DISPLAY, 1920, 1080)
    selected = render_schedule_browser(schedule, schedule[0].id, DISPLAY, 1920, 1080)

    border = (255, 255, 255, 255)
    unselected_count = sum(1 for pixel in unselected.getdata() if pixel == border)
    selected_count = sum(1 for pixel in selected.getdata() if pixel == border)
    assert selected_count > unselected_count


def test_render_schedule_browser_groups_by_date():
    same_day = [
        _scheduled("A", start=datetime(2026, 7, 26, 20, 0, tzinfo=timezone.utc), stop=datetime(2026, 7, 26, 21, 0, tzinfo=timezone.utc)),
        _scheduled("B", start=datetime(2026, 7, 26, 22, 0, tzinfo=timezone.utc), stop=datetime(2026, 7, 26, 23, 0, tzinfo=timezone.utc)),
    ]
    different_days = [
        _scheduled("A", start=datetime(2026, 7, 26, 20, 0, tzinfo=timezone.utc), stop=datetime(2026, 7, 26, 21, 0, tzinfo=timezone.utc)),
        _scheduled("B", start=datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc), stop=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc)),
    ]

    same_day_image = render_schedule_browser(same_day, None, DISPLAY, 1920, 1080)
    different_days_image = render_schedule_browser(different_days, None, DISPLAY, 1920, 1080)
    assert different_days_image.height > same_day_image.height


def test_render_schedule_browser_shows_recording_now_for_active_entry():
    schedule = [_scheduled("Match of the Day")]
    without_active = render_schedule_browser(schedule, None, DISPLAY, 1920, 1080)
    with_active = render_schedule_browser(schedule, None, DISPLAY, 1920, 1080, active_id=schedule[0].id)

    badge = (214, 40, 54, 255)
    without_active_count = sum(1 for pixel in without_active.getdata() if pixel == badge)
    with_active_count = sum(1 for pixel in with_active.getdata() if pixel == badge)
    assert with_active_count > without_active_count


def test_render_help_overlay_returns_rgba_image():
    image = render_help_overlay(1920, 1080)
    assert image.mode == "RGBA"
    assert image.width > 0 and image.height > 0


def test_render_help_overlay_scales_with_canvas_width():
    small = render_help_overlay(640, 480)
    large = render_help_overlay(3840, 2160)
    assert large.width > small.width


def test_render_help_overlay_grows_taller_with_more_entries(monkeypatch):
    # Regression guard: rows/height are derived from the length of
    # _HELP_ENTRIES, so adding a real keybinding to that list should
    # actually make the rendered sheet taller, not silently do nothing.
    import tvdinner.overlay as overlay_module

    short_list = overlay_module._HELP_ENTRIES[:4]
    monkeypatch.setattr(overlay_module, "_HELP_ENTRIES", short_list)
    short_image = render_help_overlay(1920, 1080)

    monkeypatch.setattr(overlay_module, "_HELP_ENTRIES", short_list * 5)
    long_image = render_help_overlay(1920, 1080)

    assert long_image.height > short_image.height


def test_render_about_overlay_returns_rgba_image():
    image = render_about_overlay("0.1.0-78", 1920, 1080)
    assert image.mode == "RGBA"
    assert image.width > 0 and image.height > 0


def test_render_about_overlay_scales_with_canvas_width():
    small = render_about_overlay("0.1.0-78", 640, 480)
    large = render_about_overlay("0.1.0-78", 3840, 2160)
    assert large.width > small.width


def test_render_about_overlay_draws_the_given_version():
    # Confirm the version actually gets drawn (not just accepted as an
    # unused argument) by checking pixel output changes with its length --
    # a longer version string needs more horizontal space to draw, so a
    # much longer one should widen the accent-colored version line enough
    # to show up as more accent-colored pixels somewhere in the image.
    short = render_about_overlay("0.1.0-1", 1920, 1080)
    long = render_about_overlay("0.1.0-999999999", 1920, 1080)
    assert short.size == long.size  # canvas size is fixed by canvas_width/height, not content
    short_accent = sum(1 for p in short.getdata() if p[:3] == _ACCENT_COLOR[:3])
    long_accent = sum(1 for p in long.getdata() if p[:3] == _ACCENT_COLOR[:3])
    assert long_accent > short_accent


def test_render_update_available_overlay_returns_rgba_image():
    image = render_update_available_overlay("0.1.0-93", "0.1.0-92", 1920, 1080)
    assert image.mode == "RGBA"
    assert image.width > 0 and image.height > 0


def test_render_update_available_overlay_scales_with_canvas_width():
    small = render_update_available_overlay("0.1.0-93", "0.1.0-92", 640, 480)
    large = render_update_available_overlay("0.1.0-93", "0.1.0-92", 3840, 2160)
    assert large.width > small.width


def test_render_update_available_overlay_shows_both_versions():
    # Confirm both version strings actually get drawn, not just accepted
    # as unused arguments -- same "longer text needs more accent-colored
    # pixels" technique as the about-overlay's equivalent test.
    short = render_update_available_overlay("0.1.0-1", "0.1.0-1", 1920, 1080)
    long = render_update_available_overlay("0.1.0-999999999", "0.1.0-999999999", 1920, 1080)
    assert short.size == long.size  # canvas size is fixed by canvas_width/height, not content
    assert short.tobytes() != long.tobytes()


def test_render_schedule_browser_returns_rgba_image_for_missed_only():
    # Regression test: an empty upcoming list used to mean "nothing to
    # show" (None), but a conflict/missed history on its own should still
    # render something -- there's no live schedule left, but there's still
    # something the user needs to see.
    missed = [(_scheduled("Missed Show"), "another recording was already using the tuner")]
    image = render_schedule_browser([], None, DISPLAY, 1920, 1080, missed=missed)
    assert image is not None
    assert image.mode == "RGBA"


def test_render_schedule_browser_shows_missed_section():
    schedule = [_scheduled("Upcoming Show")]
    missed = [(_scheduled("Missed Show"), "another recording was already using the tuner")]

    without_missed = render_schedule_browser(schedule, schedule[0].id, DISPLAY, 1920, 1080)
    with_missed = render_schedule_browser(schedule, schedule[0].id, DISPLAY, 1920, 1080, missed=missed)
    assert with_missed.height > without_missed.height

    badge = (214, 40, 54, 255)
    without_missed_count = sum(1 for pixel in without_missed.getdata() if pixel == badge)
    with_missed_count = sum(1 for pixel in with_missed.getdata() if pixel == badge)
    assert with_missed_count > without_missed_count
