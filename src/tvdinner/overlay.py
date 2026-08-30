"""Renders a TiviMate-style EPG banner as a composited RGBA image: channel
logo, current programme with a live progress bar, description, and what's
next. The image itself is display-engine agnostic; player.py is responsible
for pushing it onto mpv's video output.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Protocol as _TypingProtocol

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from tvdinner import __version__, tmdb
from tvdinner.channel_logos import OnlineLogoIndex
from tvdinner.epg import Epg, EpgDisplay, Programme, atomic_write_bytes, cache_path_for
from tvdinner.history import HistoryEntry
from tvdinner.m3u import Channel
from tvdinner.player import RecordingFile, StreamInfo, capture_video_thumbnail
from tvdinner.plex import PlexNode
from tvdinner.redact import redact_resource_url
from tvdinner.schedule import ScheduledRecording
from tvdinner.series import SeriesNode
from tvdinner.vod import VodChapter, VodItem

logger = logging.getLogger(__name__)

_PANEL_COLOR = (14, 16, 20, 225)
_ACCENT_COLOR = (0, 176, 255, 255)
_WHITE = (245, 246, 248, 255)
_MUTED = (176, 182, 190, 255)
_BAR_TRACK = (70, 74, 82, 255)
_BADGE_COLOR = (58, 62, 70, 255)

_MAX_DESCRIPTION_LINES = 4
_MAX_DETAILS_DESCRIPTION_LINES = 20  # generous, not a hard truncation like the small overlay's

_GRID_PANEL_COLOR = (10, 12, 16, 235)
_GRID_HEADER_COLOR = (22, 24, 30, 255)
_CELL_COLOR = (36, 40, 48, 255)
_CELL_LIVE_COLOR = (16, 68, 98, 255)
_ROW_DIVIDER = (48, 52, 60, 255)
_SELECTION_BORDER_COLOR = (255, 255, 255, 255)
_FAVORITE_COLOR = (255, 92, 122, 255)
_WATCHED_COLOR = (52, 199, 89, 255)
_FOLDER_BACK_COLOR = (196, 138, 22, 255)
_FOLDER_FRONT_COLOR = (255, 202, 58, 255)
_FOLDER_OUTLINE_COLOR = (150, 104, 15, 255)
_FAVORITE_MARK = "♥ "  # heart suit, followed by a space before the channel name
_RECORDING_BADGE_COLOR = (214, 40, 54, 255)
_RATING_STAR_COLOR = (255, 199, 0, 255)

# _render_vod_info_hero's full-bleed backdrop: kept translucent (not the
# near-opaque _PANEL_COLOR every other overlay uses) specifically so the
# paused/playing video stays visibly showing through it, per its own
# "classy, Netflix/Prime-style" brief -- unlike every other overlay here,
# which is meant to read as an opaque panel sitting on top of the video.
_HERO_BACKDROP_ALPHA = 140  # ~55% -- video remains clearly visible through it
_HERO_GRADIENT_MAX_ALPHA = 235  # near-opaque by the bottom row, for text legibility
_HERO_GRADIENT_LEAD_IN_FRACTION = 0.05  # extra fade-in space above the text block, as a fraction of canvas height

DEFAULT_GUIDE_WINDOW_HOURS = 3.0

if sys.platform == "win32":
    DEFAULT_IMAGE_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "tvdinner" / "image_cache"
else:
    DEFAULT_IMAGE_CACHE_DIR = Path.home() / ".cache" / "tvdinner" / "images"

# Channel logos and poster art essentially never change -- unlike EPG data
# (which has its own user-tunable --epg-cache-hours), there's no reason to
# ever make this configurable; same fixed-and-generous philosophy as
# tmdb.py's own on-disk rating cache. Confirmed live: without this, a large
# playlist (1000+ channels) re-fetches every single logo image over the
# network on every launch, not just the first one in a session -- prefetch_
# channel_logos's background threads already fixed the *within-session*
# cost, but every fresh process still started from empty caches.
DEFAULT_IMAGE_CACHE_MAX_AGE = timedelta(days=30)

_logo_cache: dict[str, Image.Image | None] = {}
_app_logo_cache: dict[int, Image.Image] = {}
_tmdb_logo_cache: dict[int, Image.Image] = {}
_logo_tile_cache: dict[tuple[int, int], Image.Image] = {}


def _app_logo(size: int) -> Image.Image:
    """tvdinner's own logo mark (the same one on the marketing site,
    docs/assets/logo-mark.svg), bundled as package-data PNG -- shown in
    the header bar of full-screen views (guide, recordings/schedule
    browsers, help overlay) for a consistent, recognizable brand mark
    across the app, not just the website."""
    cached = _app_logo_cache.get(size)
    if cached is not None:
        return cached
    with importlib.resources.as_file(importlib.resources.files("tvdinner") / "images" / "logo-mark.png") as path:
        image = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    _app_logo_cache[size] = image
    return image


def _tmdb_logo(height: int) -> Image.Image:
    """TMDB's own attribution wordmark (bundled as package-data PNG,
    sourced from https://www.themoviedb.org/about/logos-attribution --
    "every application that uses our data or images is required to
    properly attribute TMDB as the source"), shown at `height` pixels
    tall wherever a rating badge appears (guide grid, programme details,
    channel-switch banner) in place of the plain "TMDB" text this used to
    draw. Not square like _app_logo, so width is derived from the
    source's own aspect ratio rather than passed in."""
    cached = _tmdb_logo_cache.get(height)
    if cached is not None:
        return cached
    with importlib.resources.as_file(importlib.resources.files("tvdinner") / "images" / "tmdb-logo.png") as path:
        source = Image.open(path).convert("RGBA")
    width = max(1, round(source.width * height / source.height))
    image = source.resize((width, height), Image.LANCZOS)
    _tmdb_logo_cache[height] = image
    return image


def _title_with_year(programme: Programme, fallback_year: str | None = None) -> str:
    # Some XMLTV feeds already bake the year into <title> for movies (e.g.
    # "The Taking of Pelham One Two Three (1974)"), on top of the separate
    # <date> element Programme.year comes from -- appending unconditionally
    # would double it up to "... (1974) (1974)". Only append if the title
    # doesn't already end with that exact year.
    #
    # fallback_year -- a TMDB-sourced release year (tmdb.release_year_for)
    # -- is only ever used when the feed gave no <date> at all (confirmed
    # live: some feeds, e.g. a FastChannels-generated Plex TV guide, never
    # populate it for any programme). Only the live-channel "now playing"
    # hero/banner call sites pass one; the guide grid's per-cell titles and
    # the programme-details popup don't, so they're unaffected.
    year = programme.year or fallback_year
    if not year:
        return programme.title
    if programme.title.endswith(f"({year})"):
        return programme.title
    return f"{programme.title} ({year})"


_font_cache: dict[tuple[str, int], ImageFont.ImageFont] = {}


def _font(name: str, size: int) -> ImageFont.ImageFont:
    # Bundled as package data (not read from an OS font directory) so
    # rendering looks identical everywhere, regardless of what fonts --
    # if any -- happen to be installed on the host.
    #
    # Cached by (name, size): every overlay render (guide, EPG banner,
    # programme details, ...) calls this several times over, and an
    # uncached call means re-opening the font file and re-parsing it with
    # FreeType from scratch, every time -- confirmed live, at real
    # playlist scale, to be a real contributor to guide-render lag on top
    # of the bigger _logo_tile issue, since it also silently defeated
    # _font_has_glyph's own id(font)-keyed caches (a fresh font object
    # every call meant a fresh id() every call, so those never hit either).
    cache_key = (name, max(size, 8))
    cached = _font_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        with importlib.resources.as_file(importlib.resources.files("tvdinner") / "fonts" / name) as path:
            font = ImageFont.truetype(str(path), max(size, 8))
    except OSError:
        font = ImageFont.load_default()
    _font_cache[cache_key] = font
    return font


_notdef_signature_cache: dict[int, tuple] = {}
_glyph_supported_cache: dict[tuple[int, str], bool] = {}
_NOTDEF_PROBE = "\ue000"  # Private Use Area codepoint, unassigned in any real font


def _mask_signature(font, char: str) -> tuple:
    # font.getmask() returns a low-level ImagingCore, not a full Image, so
    # there's no .tobytes() -- (size, bbox, histogram) is cheap to compute
    # and just as good a fingerprint for "is this the same glyph bitmap".
    mask = font.getmask(char)
    return (mask.size, mask.getbbox(), tuple(mask.histogram()))


def _font_has_glyph(font, char: str) -> bool:
    """Whether `font` renders `char` with a real glyph rather than its
    .notdef placeholder. Some fonts draw a visible empty box ('tofu')
    for .notdef instead of leaving blank
    space, so a naive "is the mask non-empty" check can't tell a real
    glyph from a missing one -- this instead compares `char`'s rendered
    mask against a Private Use Area probe codepoint (U+E000), which is
    guaranteed unassigned in any real font and therefore always hits
    .notdef itself, whatever it looks like."""
    if char in " \n\t":
        return True  # never strip plain whitespace, even if its mask happens to be empty like a blank .notdef would be

    key = (id(font), char)
    cached = _glyph_supported_cache.get(key)
    if cached is not None:
        return cached

    notdef = _notdef_signature_cache.get(id(font))
    if notdef is None:
        notdef = _mask_signature(font, _NOTDEF_PROBE)
        _notdef_signature_cache[id(font)] = notdef

    result = _mask_signature(font, char) != notdef
    _glyph_supported_cache[key] = result
    return result


def _strip_unsupported_glyphs(text: str, font) -> str:
    """Drop characters `font` can't actually render (see _font_has_glyph)
    rather than showing whatever placeholder it substitutes -- e.g. some
    IPTV providers append decorative Unicode badge characters (circled
    letters marking geo-restriction, subtitles, etc.) to channel names
    that our bundled font has no real glyph for, which otherwise showed
    up as a visible empty-box artifact right after the channel name."""
    if not text:
        return text
    cleaned = "".join(ch for ch in text if _font_has_glyph(font, ch))
    return cleaned if cleaned == text else " ".join(cleaned.split())


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    """Truncate `text` with an ellipsis so it fits within max_width pixels."""
    text = _strip_unsupported_glyphs(text.strip(), font)
    if not text or draw.textlength(text, font=font) <= max_width:
        return text

    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid].rstrip() + "…"
        if draw.textlength(candidate, font=font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + "…") if lo > 0 else "…"


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float, max_lines: int) -> list[str]:
    """Word-wrap `text` to at most max_lines, ellipsizing any overflow."""
    words = _strip_unsupported_glyphs(text, font).split()
    lines: list[str] = []
    current: list[str] = []
    index = 0

    while index < len(words) and len(lines) < max_lines:
        current.append(words[index])
        if draw.textlength(" ".join(current), font=font) > max_width:
            current.pop()
            if current:
                lines.append(" ".join(current))
                current = []
                continue
            lines.append(_fit_text(draw, words[index], font, max_width))
            index += 1
            continue
        index += 1

    if current and len(lines) < max_lines:
        lines.append(" ".join(current))

    if index < len(words) and lines:
        lines[-1] = _fit_text(draw, lines[-1] + " …", font, max_width)

    return lines


def _draw_hdr_pill(
    measure: ImageDraw.ImageDraw, draw: ImageDraw.ImageDraw | None, x: float, y: float, text: str, reference_font
) -> float:
    """A single small, quietly-outlined tag for the HDR type (e.g.
    'HDR10+', 'Dolby Vision') on a hero overlay's year/rating metadata
    row -- deliberately not _draw_quality_badges' bolder *filled*-pill
    treatment, which both hero overlays otherwise skip entirely (see
    _render_epg_hero's own docstring) since a full row of badges would
    clash with a hero image already establishing its own visual
    identity; a single outlined tag reusing the row's own muted text
    color reads as part of that metadata line instead.

    `reference_font` is that row's own font (e.g. meta_font), passed
    for scale and vertical centering only -- the tag itself renders at
    a smaller, bolder size (matching _draw_quality_badges' scale for
    the same kind of short tag) rather than reference_font's own size,
    since drawing it at full row-text size/weight made it compete with
    the row's own text instead of reading as a quiet companion mark
    (caught in live review). Returns the tag's width (including its
    own trailing gap) so a caller can offset whatever it draws next on
    the same row."""
    font = _font("Inter-Bold.ttf", round(reference_font.size * 0.7))
    pad_x = font.size * 0.45
    pad_y = font.size * 0.22
    width = measure.textlength(text, font=font) + 2 * pad_x
    height = font.size + 2 * pad_y
    top = y + (reference_font.size - height) / 2
    if draw:
        draw.rounded_rectangle((x, top, x + width, top + height), radius=height * 0.3, outline=_MUTED, width=1)
        draw.text((x + pad_x, top + pad_y), text, font=font, fill=_MUTED)
    return width + reference_font.size * 0.5


def _draw_quality_badges(
    measure: ImageDraw.ImageDraw,
    draw: ImageDraw.ImageDraw | None,
    x: float,
    y: float,
    texts: list[str],
    font,
    max_x: float,
) -> float:
    """Draw a row of small pill-shaped quality badges (e.g. '1080p',
    'H.264', 'HDR10') left to right starting at (x, y), stopping (rather
    than wrapping) if a badge would run past max_x -- there are only ever
    a handful of short badges, so this is never expected to trigger.
    `measure` is used for text-width measurement even when `draw` is None
    (the layout-measurement pass), since row height doesn't depend on it
    but per-badge width does. Returns the row's height, 0 if `texts` is
    empty, so callers can advance their own layout cursor either way.
    """
    if not texts:
        return 0.0
    pad_x = font.size * 0.35
    pad_y = font.size * 0.22
    gap = font.size * 0.3
    row_height = font.size + 2 * pad_y

    cursor = x
    for text in texts:
        box_width = measure.textlength(text, font=font) + 2 * pad_x
        if cursor + box_width > max_x:
            break
        if draw:
            draw.rounded_rectangle(
                (cursor, y, cursor + box_width, y + row_height), radius=row_height * 0.25, fill=_BADGE_COLOR
            )
            draw.text((cursor + pad_x, y + pad_y), text, font=font, fill=_WHITE)
        cursor += box_width + gap

    return row_height


def _hero_tech_summary(stream_info: StreamInfo | None) -> str | None:
    """The hero variants' single compact technical-details line --
    container, video bitrate (audio_bitrate deliberately left out here:
    showing it unlabeled whenever video_bitrate happens to be
    unavailable -- common, see player.StreamInfo's own docstring --
    would read as the video's own bitrate, which it isn't), and track
    *counts* only, never a full per-track breakdown -- same restraint
    _draw_hdr_pill's own docstring already applies to the full
    quality-badge row on a hero image. None when stream_info is absent
    or has nothing at all to show."""
    if stream_info is None:
        return None
    parts = []
    if stream_info.container:
        parts.append(stream_info.container)
    if stream_info.video_bitrate:
        parts.append(stream_info.video_bitrate)
    audio_tracks = stream_info.audio_tracks
    if len(audio_tracks) > 1:
        parts.append(f"{len(audio_tracks)} audio tracks")
    elif len(audio_tracks) == 1:
        track = audio_tracks[0]
        label = " ".join(p for p in (track.language, track.channels) if p)
        if label:
            parts.append(label)
    if stream_info.subtitle_tracks:
        count = len(stream_info.subtitle_tracks)
        parts.append(f"{count} subtitle{'s' if count != 1 else ''}")
    return " · ".join(parts) or None


def _technical_detail_lines(stream_info: StreamInfo | None) -> list[str]:
    """Full per-track technical detail lines for the banner/card variants
    -- unlike the hero's single-line summary (_hero_tech_summary), these
    already show the full quality-badge row and have an opaque panel
    (not artwork) behind them, so there's room for container/bitrates on
    one line, then every audio track, then every subtitle's language.
    Empty list when stream_info is absent or has nothing to show."""
    if stream_info is None:
        return []
    lines = []
    summary_parts = []
    if stream_info.container:
        summary_parts.append(stream_info.container)
    if stream_info.video_bitrate:
        summary_parts.append(f"Video {stream_info.video_bitrate}")
    if stream_info.audio_bitrate:
        summary_parts.append(f"Audio {stream_info.audio_bitrate}")
    if summary_parts:
        lines.append(" · ".join(summary_parts))
    if stream_info.audio_tracks:
        track_texts = []
        for track in stream_info.audio_tracks:
            label = " ".join(p for p in (track.language, track.codec, track.channels) if p) or "Audio"
            track_texts.append(f"▶ {label}" if track.selected else label)
        lines.append("Audio: " + ", ".join(track_texts))
    if stream_info.subtitle_tracks:
        langs = [track.language or "Unknown" for track in stream_info.subtitle_tracks]
        lines.append("Subtitles: " + ", ".join(langs))
    return lines


def _initials(name: str) -> str:
    letters = "".join(word[0] for word in name.split() if word)[:2].upper()
    return letters or "?"


def _accent_for(seed: str) -> tuple[int, int, int, int]:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return (digest[0] % 156 + 60, digest[1] % 156 + 60, digest[2] % 156 + 60, 255)


def _fallback_avatar(name: str, size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size * 0.18, fill=_accent_for(name))

    font = _font("Inter-Bold.ttf", round(size * 0.42))
    text = _initials(_strip_unsupported_glyphs(name, font))
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    draw.text(((size - tw) / 2 - left, (size - th) / 2 - top), text, font=font, fill=_WHITE)
    return image


def _recording_icon(size: int) -> Image.Image:
    """A tile with a simple record glyph (ring + filled dot), matching the
    marketing site's own icon for this feature -- shown in place of a
    channel logo/avatar on the recording-playback overlay, since a local
    recording has no channel logo of its own."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size * 0.18, fill=_RECORDING_BADGE_COLOR)

    center = size / 2
    ring_radius = size * 0.24
    draw.ellipse(
        (center - ring_radius, center - ring_radius, center + ring_radius, center + ring_radius),
        outline=_WHITE,
        width=max(2, round(size * 0.045)),
    )
    dot_radius = size * 0.09
    draw.ellipse((center - dot_radius, center - dot_radius, center + dot_radius, center + dot_radius), fill=_WHITE)
    return image


_IMAGE_REQUEST_HEADERS = {
    # Some CDNs -- Wikimedia's most notably, confirmed live: 403 without
    # this, 200 with it -- reject the default python-requests User-Agent
    # outright as a basic anti-hotlinking/bot measure. A descriptive one
    # identifying the app (Wikimedia's own User-Agent policy asks for
    # exactly this) fixes it, and is good practice for any host regardless.
    "User-Agent": f"tvdinner/{__version__} (https://github.com/issinoho/tvdinner)"
}

# imgur (a very common host in iptv-org's community logo database -- see
# tvdinner.channel_logos) geo-blocks a large share of hotlinked i.imgur.com
# traffic: a real HTTP 200 with a normal image/png body, but the image
# itself is this "Content not viewable in your region" placeholder graphic
# -- byte-for-byte identical (confirmed live) no matter which image was
# actually requested. There's no header/status-code signal to catch this
# on, so the only reliable tell is the response's own content hash.
_BLOCKED_IMAGE_HASHES = {
    "faa24ec881e6040655c187a681d6dc496eb8aa41e1bd0652a180b3a40b457187",  # imgur's region-block placeholder
}


_RECORDING_THUMB_SCHEME = "tvdinner-recording-thumb://"


def recording_thumbnail_url(path: Path) -> str:
    """The pseudo-URL to put in a "recording"-kind HistoryEntry's
    image_url so it resolves (via _decode_image below) to a real frame
    captured from the recording itself, the same way a channel/VOD
    entry's image_url resolves to a fetched logo/poster -- keeps
    render_history_browser/cached_image/prefetch_images completely
    agnostic to where an entry's thumbnail actually comes from."""
    return f"{_RECORDING_THUMB_SCHEME}{path}"


def _recording_thumbnail(video_path: Path, cache_dir: Path) -> Image.Image | None:
    """_decode_image's recording_thumbnail_url branch: a disk-cached
    (forever -- a saved recording never changes once written, so unlike
    a remote image there's no DEFAULT_IMAGE_CACHE_MAX_AGE to honor)
    frame grabbed from the video itself. Returns None without spawning
    mpv at all if the recording has since been deleted (e.g. from the
    'w' recordings browser) -- a stale history entry pointing at a
    missing file is a normal, expected state, not an error."""
    cache_path = cache_path_for(cache_dir, recording_thumbnail_url(video_path), suffix=".jpg")
    if cache_path.is_file():
        try:
            return Image.open(BytesIO(cache_path.read_bytes())).convert("RGBA")
        except (OSError, ValueError):
            pass  # corrupt/unreadable cache entry -- fall through to regenerate
    if not video_path.is_file():
        return None
    data = capture_video_thumbnail(video_path)
    if data is None:
        return None
    try:
        atomic_write_bytes(cache_path, data)
    except OSError:
        pass  # best-effort, same tolerance as _decode_image's own remote-image cache write
    try:
        return Image.open(BytesIO(data)).convert("RGBA")
    except (OSError, ValueError):
        return None


_CHAPTER_THUMB_SCHEME = "tvdinner-chapter-thumb://"
# Originally 8s, then 15s (capture_video_thumbnail's own local-file
# default) -- both confirmed live to still be too tight. Grabbing a
# frame from a real, actively-streaming Plex item (a second connection
# to the same file the main session is already reading -- worse yet
# over a debrid remote) is inherently variable: one movie's chapters
# typically took 3.6-8.2s, but a TV episode from a different debrid
# source took 10.2-14.7s to *succeed*, with one attempt still timing
# out past 20s. A timeout here still just means the preview shows
# without a thumbnail, not an error, and every attempt is already
# serialized (see _chapter_thumb_lock) so a longer wait here costs
# queue-draining time under a burst, never correctness -- there's no
# reason to cap this anywhere close to what real debrid latency needs.
_CHAPTER_THUMB_TIMEOUT_SECONDS = 30.0
# Serializes every local-frame-grab-fallback capture (never the
# Plex-provided-thumb path -- that's just a cheap HTTP fetch, same as
# any other artwork, and doesn't need this) -- prefetch_images already
# spawns one thread per not-yet-cached chapter thumbnail, so browsing
# through several chapters (cli.py's own neighbor-prefetch, or just
# checking a few in a row) can easily have multiple of these in flight
# at once. Confirmed live this is actively harmful, not just wasteful:
# 11 concurrent grabs against the same real Plex item (itself already
# being streamed by the main session -- worse yet over a debrid remote)
# ALL timed out with zero frames produced, while the same 11 run one at
# a time succeeded all but once. Costs latency under a burst (each
# waits its turn rather than running in parallel), never correctness --
# every chapter's frame is disk-cached forever once it does succeed
# (see _chapter_thumbnail), so a slow first visit is a one-time cost.
_chapter_thumb_lock = threading.Lock()


def chapter_thumbnail_url(stream_url: str, seek_seconds: float) -> str:
    """A pseudo-URL for a locally-generated chapter thumbnail -- cli.py's
    chapter preview only ever uses this as a fallback when the chapter
    has no real Plex-generated VodChapter.thumb_url of its own (see that
    field's docstring). Built at render time from whichever item is
    actually playing right now, not stored on VodChapter itself, the
    same reasoning as recording_thumbnail_url above but keyed on a
    (stream, timestamp) pair instead of just a path, since one item can
    have many chapters."""
    return f"{_CHAPTER_THUMB_SCHEME}{seek_seconds}#{stream_url}"


def _chapter_thumbnail(stream_url: str, seek_seconds: float, cache_dir: Path, source_url: str) -> Image.Image | None:
    """_decode_image's chapter_thumbnail_url branch: a disk-cached
    (forever -- a chapter's own frame never changes, same reasoning as
    _recording_thumbnail) frame grabbed from the resolved stream at
    `seek_seconds`. `source_url` is the full original pseudo-URL (scheme
    included), used as the cache key so it stays unique per (stream,
    timestamp) pair -- `stream_url` alone would collide across every
    chapter of the same item."""
    cache_path = cache_path_for(cache_dir, source_url, suffix=".jpg")
    if cache_path.is_file():
        try:
            return Image.open(BytesIO(cache_path.read_bytes())).convert("RGBA")
        except (OSError, ValueError):
            pass  # corrupt/unreadable cache entry -- fall through to regenerate
    with _chapter_thumb_lock:
        data = capture_video_thumbnail(stream_url, seek_seconds=seek_seconds, timeout_seconds=_CHAPTER_THUMB_TIMEOUT_SECONDS)
    if data is None:
        return None
    try:
        atomic_write_bytes(cache_path, data)
    except OSError:
        pass  # best-effort, same tolerance as _decode_image's own remote-image cache write
    try:
        return Image.open(BytesIO(data)).convert("RGBA")
    except (OSError, ValueError):
        return None


def _decode_image(
    url: str,
    cache_dir: Path = DEFAULT_IMAGE_CACHE_DIR,
    max_age: timedelta = DEFAULT_IMAGE_CACHE_MAX_AGE,
) -> Image.Image | None:
    if url.startswith(_RECORDING_THUMB_SCHEME):
        return _recording_thumbnail(Path(url[len(_RECORDING_THUMB_SCHEME) :]), cache_dir)
    if url.startswith(_CHAPTER_THUMB_SCHEME):
        seek_str, _, stream_url = url[len(_CHAPTER_THUMB_SCHEME) :].partition("#")
        try:
            seek_seconds = float(seek_str)
        except ValueError:
            return None
        return _chapter_thumbnail(stream_url, seek_seconds, cache_dir, source_url=url)
    is_remote = url.startswith(("http://", "https://"))
    # Only a remote fetch is worth disk-caching -- a file:// (or bare
    # path) source is already a fast local read, and caching it as
    # *another* local file would be pure overhead.
    cache_path = cache_path_for(cache_dir, url, suffix=".img") if is_remote else None

    if cache_path is not None and cache_path.is_file():
        age = timedelta(seconds=time.time() - cache_path.stat().st_mtime)
        if age < max_age:
            try:
                return Image.open(BytesIO(cache_path.read_bytes())).convert("RGBA")
            except (OSError, ValueError):
                pass  # corrupt/unreadable cache entry -- fall through to a real fetch

    try:
        if is_remote:
            response = requests.get(url, headers=_IMAGE_REQUEST_HEADERS, timeout=10)
            response.raise_for_status()
            data = response.content
            if hashlib.sha256(data).hexdigest() in _BLOCKED_IMAGE_HASHES:
                logger.warning(
                    "Image %s returned a known region-block placeholder; treating as unavailable",
                    redact_resource_url(url),
                )
                return None
            if cache_path is not None:
                try:
                    atomic_write_bytes(cache_path, data)
                except OSError:
                    pass  # best-effort, same tolerance as tmdb.py's own disk cache
        else:
            path = url[len("file://"):] if url.startswith("file://") else url
            with open(path, "rb") as handle:
                data = handle.read()
        return Image.open(BytesIO(data)).convert("RGBA")
    except (requests.RequestException, OSError, ValueError) as exc:
        logger.warning("Could not fetch/decode image %s: %s", redact_resource_url(url), exc)
        return None


def fetch_image(
    url: str | None,
    cache_dir: Path = DEFAULT_IMAGE_CACHE_DIR,
    max_age: timedelta = DEFAULT_IMAGE_CACHE_MAX_AGE,
) -> Image.Image | None:
    """Fetch and decode an image (channel logo or programme poster) --
    cached in memory for the app's own lifetime as before, and now also
    on disk (for `max_age`, default 30 days) for any remote http(s) URL,
    so a fresh launch doesn't have to re-fetch every logo/poster over the
    network again just because the in-memory cache started empty (see
    DEFAULT_IMAGE_CACHE_MAX_AGE). A request/decode failure is never
    disk-cached, only ever the in-memory None already was -- a transient
    outage or a since-fixed dead URL gets retried next launch rather than
    permanently poisoned, same philosophy as tmdb.py's own cache. Returns
    None if there is no URL or it can't be fetched/decoded, so callers
    can fall back to a placeholder."""
    if not url:
        return None
    if url not in _logo_cache:
        _logo_cache[url] = _decode_image(url, cache_dir, max_age)
    return _logo_cache[url]


_image_in_flight: set[str] = set()


def cached_image(url: str | None) -> Image.Image | None:
    """Pure, non-blocking, in-memory-only read of fetch_image's own cache
    -- safe to call from a render function for a URL that's (or might be)
    still being fetched in the background (see prefetch_images). Returns
    None both for "no URL", "not fetched yet", and "fetched, unavailable"
    -- indistinguishable on purpose, since a render function only ever
    needs to know whether to draw a real thumbnail or a placeholder."""
    if not url:
        return None
    return _logo_cache.get(url)


def forget_failed_fetch(url: str | None) -> None:
    """Evict `url` from fetch_image's in-memory cache, but only if it
    resolved to a failure (None) -- a real, already-cached image is left
    alone. fetch_image otherwise caches a failure exactly as permanently
    as a success, for the life of the process (line above:
    `if url not in _logo_cache: ...` -- a present key, even one mapping
    to None, short-circuits every future attempt). That's fine for most
    of this app's fetches (an actually-missing poster/logo isn't going
    to start existing), but wrong for cli.py's chapter-thumbnail
    fallback: a local frame-grab against a live, contended remote stream
    (confirmed live, against a real debrid-backed Plex item) fails often
    enough that "never retry once it's failed even once" means a
    chapter that happened to fail its first attempt stays blank for the
    rest of the session no matter how many times it's revisited -- even
    though the *disk* cache (_chapter_thumbnail) only ever remembers a
    success, so a fresh attempt would very plausibly work. Called by
    cli.py right before each (re-)preview of a chapter, so a stale
    failure never blocks a genuine retry."""
    if url and url in _logo_cache and _logo_cache[url] is None:
        del _logo_cache[url]


def prefetch_images(urls: Iterable[str | None], on_resolved: Callable[[], None] | None = None) -> None:
    """Spawn one daemon thread per URL not already cached or in-flight,
    each calling fetch_image so the result lands in its own cache for
    cached_image to pick up -- same one-thread-per-not-yet-cached-item
    shape as prefetch_channel_logos below, generalized here for any
    view whose items resolve to a single, already-known image URL each
    (unlike a channel logo, which tries several candidate URLs in turn --
    see resolve_channel_logo -- so it keeps its own separate prefetch).
    Used by cli.py's history browser, for the same reason
    prefetch_channel_logos exists (see its own docstring, which
    confirmed this live for the guide grid): a render calling
    fetch_image synchronously, once per visible row, would make opening
    a browser for the first time (or scrolling to reveal rows never
    shown before) stall on network fetches.

    `on_resolved`, if given, is called -- on the fetching background
    thread, not the caller's -- once per URL this call actually spawned a
    fetch for, after that fetch finishes, regardless of outcome. Without
    this, a freshly-opened browser only ever shows newly-resolved images
    on whatever *later* render happens to come next."""
    for url in urls:
        if not url or url in _logo_cache or url in _image_in_flight:
            continue
        _image_in_flight.add(url)

        def _fetch(url: str = url) -> None:
            try:
                # Written explicitly (fetch_image already does this
                # itself for the real implementation) so a test's fake
                # fetch_image -- which returns an image without any
                # caching side effect of its own -- still lands in
                # _logo_cache the same way a real fetch would.
                _logo_cache[url] = fetch_image(url)
            finally:
                _image_in_flight.discard(url)
                if on_resolved is not None:
                    on_resolved()

        threading.Thread(target=_fetch, daemon=True).start()


def _fit_within_box(image: Image.Image, width: int, height: int) -> Image.Image:
    """Resize `image` to fit within (width, height) without distorting its
    aspect ratio (e.g. a portrait movie poster inside a wider reserved box),
    centered on a transparent canvas of exactly that size."""
    fitted = ImageOps.contain(image, (width, height))
    box = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    box.alpha_composite(fitted.convert("RGBA"), ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return box


def _cover_fill(image: Image.Image, width: int, height: int, zoom: float = 1.0) -> Image.Image:
    """Scale-and-crop `image` to exactly fill (width, height) with no
    letterboxing -- the inverse of _fit_within_box, which pads instead of
    cropping. For a full-bleed hero backdrop (_render_vod_info_hero),
    where empty bars around a source image that isn't exactly the
    canvas's own aspect ratio would look broken. Centered slightly above
    the vertical middle (0.5, 0.35), not dead center, since a movie
    backdrop's key art (faces, title treatment) tends to sit in the upper
    half.

    `zoom` (default 1.0, ImageOps.fit's own tight cover-crop) lets a
    caller show more of the source than a strict cover-fill would --
    below 1.0 widens the crop region read from the source before scaling
    up to (width, height), so a source whose aspect ratio is far from the
    target's (e.g. _plex_full_backdrop's portrait poster stretched across
    a landscape canvas) doesn't read as quite so zoomed in. Reimplements
    ImageOps.fit's own crop-box math by hand (rather than shrinking the
    target size and fitting twice, which would double-resample) so this
    stays a single resize; clamped to the source's own size, so a `zoom`
    low enough to ask for more pixels than the source has just falls back
    to the whole image along that axis."""
    if zoom >= 1.0:
        return ImageOps.fit(image.convert("RGBA"), (width, height), method=Image.LANCZOS, centering=(0.5, 0.35))
    image = image.convert("RGBA")
    src_width, src_height = image.size
    target_aspect = width / height
    if src_width / src_height > target_aspect:
        crop_height, crop_width = src_height, round(src_height * target_aspect)
    else:
        crop_width, crop_height = src_width, round(src_width / target_aspect)
    crop_width = min(src_width, round(crop_width / zoom))
    crop_height = min(src_height, round(crop_height / zoom))
    left = round((src_width - crop_width) * 0.5)
    top = round((src_height - crop_height) * 0.35)
    cropped = image.crop((left, top, left + crop_width, top + crop_height))
    return cropped.resize((width, height), Image.LANCZOS)


def _with_flat_alpha(image: Image.Image, alpha: int) -> Image.Image:
    """`image` (already RGBA) with every pixel's alpha replaced by a flat
    `alpha` -- fetched photo art has no real transparency of its own
    (_decode_image always converts to a fully opaque RGBA), so this is
    what makes a hero backdrop partially see-through over the live/paused
    video it's composited on top of, instead of fully occluding it."""
    result = image.copy()
    result.putalpha(alpha)
    return result


def _composite_title_logo(canvas: Image.Image, title_logo: Image.Image, canvas_width: int, canvas_height: int, padding: int) -> None:
    """Composites a TMDB title-treatment logo into the hero's top-right
    corner, in place on `canvas` -- shared by _render_epg_hero and
    _render_vod_info_hero. Scaled with ImageOps.contain (preserving
    aspect ratio, no padding box unlike _fit_within_box -- a logo's own
    transparent margin already varies per asset, so padding it again
    would shrink it unpredictably) to fit within a fixed max footprint.

    Backed by a soft, blurred scrim sized to the logo's bounding box (the
    same blur-for-legibility technique this file uses for floating panels
    elsewhere) rather than a shadow following the logo's own alpha shape
    -- confirmed live that a shape-following *black* shadow does nothing
    for a dark-colored logo (e.g. The Godfather's near-black wordmark
    still read as almost invisible against a dark backdrop corner, since
    a black shadow behind black art adds no contrast), and a flat black
    scrim has the same problem. The scrim's color instead adapts to the
    logo's own average lightness (over its opaque pixels only, via
    _average_opaque_lightness) -- white behind a dark logo, black behind
    a light one -- so contrast is guaranteed either way."""
    max_width = round(canvas_width * 0.22)
    max_height = round(canvas_height * 0.12)
    fitted = ImageOps.contain(title_logo.convert("RGBA"), (max_width, max_height))
    x = canvas_width - padding - fitted.width
    y = padding

    scrim_color = (255, 255, 255) if _average_opaque_lightness(fitted) < 128 else (0, 0, 0)
    scrim_margin = round(max_height * 0.35)
    scrim = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(scrim).rounded_rectangle(
        (x - scrim_margin, y - scrim_margin, x + fitted.width + scrim_margin, y + fitted.height + scrim_margin),
        radius=scrim_margin,
        fill=(*scrim_color, 180),
    )
    canvas.alpha_composite(scrim.filter(ImageFilter.GaussianBlur(radius=scrim_margin * 0.6)))
    canvas.alpha_composite(fitted, (x, y))


def _average_opaque_lightness(image: Image.Image) -> float:
    """The average grayscale lightness (0-255) of `image`'s opaque-ish
    pixels, weighted by each pixel's own alpha -- fully transparent
    pixels (a logo PNG's own margin) contribute nothing, so they can't
    skew the result toward whatever the fully-transparent color happens
    to be. Returns a neutral 128 for a fully transparent image (no
    opaque pixels to measure at all)."""
    gray = image.convert("L")
    weighted_sum = 0
    weight_total = 0
    for lightness, alpha in zip(gray.getdata(), image.getchannel("A").getdata()):
        weighted_sum += lightness * alpha
        weight_total += alpha
    return weighted_sum / weight_total if weight_total else 128.0


def _bottom_fade_gradient(width: int, height: int, fade_start_row: int, max_alpha: int) -> Image.Image:
    """An opaque-black image, fully transparent above `fade_start_row` and
    ramping linearly up to `max_alpha` by the very bottom row --
    composited on top of a hero backdrop so its title/synopsis text stays
    legible regardless of what's underneath (the same "fade to dark"
    treatment Netflix/Prime use behind their own hero text). Built as a
    1px-wide column and stretched with nearest-neighbor resampling (every
    row keeps its exact computed alpha, no blending across rows) rather
    than looping over all of `width`'s columns."""
    fade_start_row = max(0, min(height, fade_start_row))
    column = Image.new("L", (1, height), 0)
    span = max(1, height - fade_start_row)
    for y in range(fade_start_row, height):
        column.putpixel((0, y), round((y - fade_start_row) / span * max_alpha))
    alpha_channel = column.resize((width, height), Image.NEAREST)
    black = Image.new("RGBA", (width, height), (5, 6, 8, 255))
    black.putalpha(alpha_channel)
    return black


_LOGO_TILE_COLOR = (250, 250, 252, 255)
_LOGO_TILE_DARK_COLOR = (38, 40, 46, 255)
_LOGO_LIGHT_LUMINANCE_THRESHOLD = 200  # see _average_luminance -- calibrated against real logo assets


def _average_luminance(image: Image.Image) -> float:
    """Alpha-weighted average luminance (0-255) of `image`'s visible
    pixels -- fully transparent pixels don't count at all, and a mostly-
    transparent one counts proportionally less than an opaque one.

    Downsampled first: fetched logos are often a source asset's original
    resolution (500px+), and the average is scale-invariant, so summing
    every pixel of a large image in a pure-Python loop is pure overhead --
    confirmed live, at real playlist scale (1500+ channels), to be the
    single largest cost in a guide render, ~50ms per never-before-cached
    logo. thumbnail() preserves aspect ratio (no distortion) and mutates
    a copy (convert() above already made one), never the caller's image."""
    sample = image.convert("RGBA")
    if sample.width * sample.height > 48 * 48:
        sample.thumbnail((48, 48))
    total_luminance = total_weight = 0.0
    for r, g, b, a in sample.getdata():
        weight = a / 255
        total_luminance += (0.299 * r + 0.587 * g + 0.114 * b) * weight
        total_weight += weight
    return total_luminance / total_weight if total_weight else 0.0


def _logo_tile(logo: Image.Image, size: int) -> Image.Image:
    """Place a fetched channel logo on a rounded tile, sized (size, size).
    Many real-world channel logos are dark line-art on a fully transparent
    background -- designed for a light UI/print -- and simply disappear
    when composited directly onto our dark panels, so the tile is light by
    default. Some are the opposite, though: a near-white/pale mark meant
    for a dark or branded background (confirmed live: Channel 5's HD logo
    is a pale grey "5" that all but vanished on the same light tile) --
    for those, measured by the cropped logo's own average luminance, the
    tile switches to dark instead. The fallback initials avatar isn't run
    through this since it already has its own (colored) background.

    Cropped to its own visible (non-transparent) content first: some
    providers' logo assets carry a lot of dead transparent padding around
    the actual mark (e.g. SiliconDust's HDHomeRun channel art, often under
    40% real content on a 4:3 canvas) -- left in, that padding gets fitted
    into the tile right along with the logo, shrinking the visible mark
    down to a small smudge in a sea of tile background, i.e. looking like
    a plain white square.

    Cached by (fetched-image identity, size): a guide render calls this
    again for every visible row on every keypress (scrolling, opening
    programme details, ...) with the exact same `fetch_image`-cached logo
    object each time, so recomputing the crop/luminance/composite from
    scratch every time was pure waste -- confirmed live, at real playlist
    scale, to make every guide render cost ~800ms regardless of caching
    anywhere else, since this was redone for all 8 visible rows on every
    single render. `id(logo)` is a safe cache key here specifically
    because fetch_image's own _logo_cache holds a permanent reference to
    the same object for a given URL for the app's whole lifetime, so it
    can never be garbage-collected and have its id reused by something
    else while a stale entry for it still lives in this cache."""
    cache_key = (id(logo), size)
    cached = _logo_tile_cache.get(cache_key)
    if cached is not None:
        return cached

    bbox = logo.getbbox(alpha_only=True)
    if bbox:
        logo = logo.crop(bbox)
    tile_color = _LOGO_TILE_DARK_COLOR if _average_luminance(logo) >= _LOGO_LIGHT_LUMINANCE_THRESHOLD else _LOGO_TILE_COLOR
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, size - 1, size - 1), radius=size * 0.18, fill=tile_color)
    inset = round(size * 0.06)
    fitted = _fit_within_box(logo, size - 2 * inset, size - 2 * inset)
    tile.alpha_composite(fitted, (inset, inset))
    _logo_tile_cache[cache_key] = tile
    return tile


def _format_remaining(seconds: float) -> str:
    """Format the time left in the current programme, e.g. '45 min remaining'
    or '1h 15m remaining' -- clamped to 0 in case `now` drifts past `stop`
    between when the caller resolved current/upcoming and rendering."""
    total_minutes = max(0, round(seconds)) // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m remaining"
    return f"{minutes} min remaining"


def _format_playback_time(seconds: float) -> str:
    """Format a playback position/duration as 'M:SS', or 'H:MM:SS' past an
    hour -- the recording-playback overlay's progress readout."""
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def render_epg_overlay(
    channel: Channel,
    current: Programme | None,
    upcoming: Programme | None,
    display: EpgDisplay,
    now: datetime,
    logo: Image.Image | None = None,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
    badges: list[str] | None = None,
    favorites: set[str] | None = None,
    stream_info: StreamInfo | None = None,
) -> Image.Image:
    """The channel/EPG 'i'-key overlay. Dispatches to _render_epg_hero --
    the same full-bleed, Netflix/Prime-style treatment
    _render_vod_info_hero gives a VOD item -- whenever `current` is a
    movie-category programme (tmdb.is_movie_category) TMDB has backdrop
    art for. tmdb.backdrop_for itself is cache-only/non-blocking (the
    actual TMDB search is cli.py's job, via tmdb.prefetch_backdrop, the
    same "fetch in the background, read the cache from the render
    function" split every other TMDB-sourced field here already uses);
    once a URL comes back, fetching and decoding the image itself is a
    single blocking call here, same as this function's own poster_image
    fetch below -- both are a deliberate, occasional keypress, not a
    per-frame render, so blocking briefly on it is fine. Every other
    case -- no current programme, a non-movie one, or TMDB not
    configured/no match -- falls back to _render_epg_banner's ordinary
    compact banner, unchanged from before backdrop support existed.

    A title-treatment logo (tmdb.logo_for/tmdb.prefetch_logo -- the same
    cache-only-read/background-prefetch split as the backdrop above) is
    fetched here too, only ever reaching _render_epg_hero: the plain
    banner has no full-bleed art for a corner logo to sit over.

    `stream_info` (see Player.stream_info) reaches both variants: the
    hero gets only _hero_tech_summary's single compact line (plus its
    existing HDR pill via stream_info.hdr), the banner also gets
    _technical_detail_lines' full per-track breakdown alongside its
    existing `badges` row -- see those two functions' own docstrings for
    why the hero stays deliberately lighter."""
    if current is not None:
        backdrop_url = tmdb.backdrop_for(current.title, current.category, current.year, channel.group_title)
        backdrop_image = fetch_image(backdrop_url) if backdrop_url else None
        if backdrop_image is not None:
            title_logo_url = tmdb.logo_for(current.title, current.category, current.year, channel.group_title)
            title_logo_image = fetch_image(title_logo_url) if title_logo_url else None
            return _render_epg_hero(
                channel,
                current,
                upcoming,
                display,
                now,
                backdrop_image,
                canvas_width,
                canvas_height,
                badges,
                favorites,
                logo,
                title_logo_image,
                stream_info,
            )
    return _render_epg_banner(channel, current, upcoming, display, now, logo, canvas_width, badges, favorites, stream_info)


def _render_epg_banner(
    channel: Channel,
    current: Programme | None,
    upcoming: Programme | None,
    display: EpgDisplay,
    now: datetime,
    logo: Image.Image | None,
    canvas_width: int,
    badges: list[str] | None,
    favorites: set[str] | None,
    stream_info: StreamInfo | None = None,
) -> Image.Image:
    """Compose the channel/EPG banner into a single RGBA image --
    render_epg_overlay's fallback whenever there's no backdrop image to
    justify _render_epg_hero's full-bleed treatment instead.

    The banner spans the full width of the video (canvas_width), minus a
    small edge gap (`margin`) that also serves as the drop-shadow bleed --
    so callers should position it at x=0.

    Layout is computed in two passes against a fixed set of proportions
    (`nominal_height`): first to measure how much vertical space the content
    actually needs (a 2-line description pushes "Next" further down than a
    1-line one), then to draw onto a panel sized to fit that content -- so
    text never overlaps regardless of description length.

    `badges` are small quality indicators (e.g. "1080p", "H.264", "HDR10",
    "AAC", "5.1") shown in a row under the channel name -- see
    Player.stream_info, which the caller converts to display-ready strings.

    `favorites` is a set of favorited channel display names (see
    tvdinner.favorites) -- a small heart marker is drawn next to the
    channel name if it's a member, matching the guide's own marker.

    A movie's director, when available, is shown as a single
    (ellipsized, not wrapped -- this banner is meant to be glanceable)
    line -- see render_programme_details for the same preference order
    (the feed's own <credits> first, TMDB as a fallback).

    `stream_info` (see Player.stream_info), when given, adds
    _technical_detail_lines' full container/bitrate/per-track breakdown
    after the "Next" line -- see that function's own docstring.
    """
    nominal_height = max(140, round(canvas_width * 0.15))
    margin = round(nominal_height * 0.08)
    width = max(400, canvas_width - 2 * margin)
    padding = round(nominal_height * 0.12)
    logo_size = nominal_height - 2 * padding
    text_x_offset = padding * 2 + logo_size

    # A movie-poster-style image sourced from the EPG data (the current
    # programme's own <icon>, distinct from the channel logo), reserved on
    # the right edge. Sized off nominal_height (not the final, content-driven
    # `height` computed below) to avoid a circular dependency -- it would
    # otherwise need to know the final height before text_width (which
    # depends on it) can be measured.
    poster_image = fetch_image(current.poster_url) if current and current.poster_url else None
    poster_width = poster_height = 0
    poster_reserved_width = 0
    if poster_image is not None:
        poster_height = round(nominal_height * 0.9)
        poster_width = round(poster_height * 2 / 3)  # classic movie poster aspect ratio
        poster_reserved_width = poster_width + padding

    text_width = width - padding - text_x_offset - poster_reserved_width

    name_font = _font("Inter-Bold.ttf", round(nominal_height * 0.13))
    title_font = _font("Inter-Bold.ttf", round(nominal_height * 0.17))
    meta_font = _font("Inter-Regular.ttf", round(nominal_height * 0.105))
    small_font = _font("Inter-Regular.ttf", round(nominal_height * 0.095))
    badge_font = _font("Inter-Bold.ttf", round(nominal_height * 0.08))
    bar_h = max(4, round(nominal_height * 0.045))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    is_favorite = favorites is not None and channel.name in favorites
    heart_width = round(measure.textlength(_FAVORITE_MARK, font=name_font)) if is_favorite else 0
    name_text = _fit_text(measure, channel.name, name_font, text_width - heart_width)

    title_text = time_text = remaining_text = category_text = None
    rating_score_text = None
    description_lines: list[str] = []
    fraction = 0.0
    if current is not None:
        fallback_year = tmdb.release_year_for(current.title, current.category, current.year, channel.group_title)
        title_text = _fit_text(measure, _title_with_year(current, fallback_year), title_font, text_width)
        start_local = display.to_local(current.start, channel_name=channel.name)
        stop_local = display.to_local(current.stop, channel_name=channel.name)
        time_text = f"{start_local.strftime('%H:%M')} – {stop_local.strftime('%H:%M')}"
        if current.category:
            category_text = _fit_text(measure, _strip_unsupported_glyphs(current.category, meta_font), meta_font, text_width)
        rating = tmdb.rating_for(current.title, current.category, current.year, channel.group_title)
        rating_score_text = f"★ {rating:.1f}" if rating is not None else None
        # Same preference order as render_programme_details: the feed's own
        # <credits><director> (free, instant, exact) before TMDB's
        # cache-only fuzzy-matched fallback.
        director = current.director or tmdb.director_for(current.title, current.category, current.year, channel.group_title)
        director_lines = _wrap_text(measure, f"Directed by {director}", meta_font, text_width, 1) if director else []
        # current.start/stop are raw (unshifted) feed times, but `now` is the
        # real current time -- correct them by this channel's shift before
        # comparing, or the progress bar would be wrong for a shifted channel.
        shift = display.shift_for(channel.name)
        corrected_start = current.start + shift
        corrected_stop = current.stop + shift
        total_seconds = (corrected_stop - corrected_start).total_seconds()
        elapsed_seconds = (now - corrected_start).total_seconds()
        fraction = min(1.0, max(0.0, elapsed_seconds / total_seconds)) if total_seconds > 0 else 0.0
        if total_seconds > 0:
            remaining_text = _format_remaining(total_seconds - elapsed_seconds)
        if current.description:
            description_lines = _wrap_text(measure, current.description, small_font, text_width, _MAX_DESCRIPTION_LINES)

    # Right-aligned against time_text's own line (below) rather than a new
    # line of its own, same as render_programme_details -- reads as part of
    # the existing metadata row instead of a bolted-on element.
    if rating_score_text is not None:
        rating_bbox = measure.textbbox((0, 0), rating_score_text, font=meta_font)
        attribution_logo = _tmdb_logo(rating_bbox[3] - rating_bbox[1])
        rating_gap = round(nominal_height * 0.03)

    next_text = None
    if upcoming:
        start = display.to_local(upcoming.start, channel_name=channel.name).strftime("%H:%M")
        next_text = _fit_text(measure, f"Next  ·  {upcoming.title} ({start})", small_font, text_width)

    technical_lines = [
        _fit_text(measure, line, small_font, text_width) for line in _technical_detail_lines(stream_info)
    ]

    def layout(draw: ImageDraw.ImageDraw | None) -> float:
        """Walk the content top-to-bottom, drawing onto `draw` if given,
        returning the y-offset (within the panel) after the last element."""
        y = padding * 0.6
        if draw:
            if is_favorite:
                draw.text((text_x_offset, y), _FAVORITE_MARK, font=name_font, fill=_FAVORITE_COLOR)
            draw.text((text_x_offset + heart_width, y), name_text, font=name_font, fill=_MUTED)
        y += nominal_height * 0.20

        badge_row_height = _draw_quality_badges(
            measure, draw, text_x_offset, y, badges or [], badge_font, text_x_offset + text_width
        )
        if badge_row_height:
            y += badge_row_height + nominal_height * 0.06

        if current is None:
            if draw:
                draw.text((text_x_offset, y), "No programme information", font=meta_font, fill=_MUTED)
            y += nominal_height * 0.20
        else:
            if draw:
                draw.text((text_x_offset, y), title_text, font=title_font, fill=_WHITE)
            y += nominal_height * 0.22

            if draw:
                draw.text((text_x_offset, y), time_text, font=meta_font, fill=_MUTED)
                if rating_score_text is not None:
                    attribution_x = text_x_offset + text_width - attribution_logo.width
                    panel.alpha_composite(attribution_logo, (round(attribution_x), round(y)))
                    score_x = attribution_x - rating_gap - (rating_bbox[2] - rating_bbox[0]) - rating_bbox[0]
                    draw.text((score_x, y - rating_bbox[1]), rating_score_text, font=meta_font, fill=_RATING_STAR_COLOR)
            y += nominal_height * 0.155

            if category_text:
                if draw:
                    draw.text((text_x_offset, y), category_text, font=meta_font, fill=_ACCENT_COLOR)
                y += nominal_height * 0.14

            for line in director_lines:
                if draw:
                    draw.text((text_x_offset, y), line, font=meta_font, fill=_MUTED)
                y += nominal_height * 0.14

            if draw:
                draw.rounded_rectangle(
                    (text_x_offset, y, text_x_offset + text_width, y + bar_h), radius=bar_h / 2, fill=_BAR_TRACK
                )
                if fraction > 0:
                    draw.rounded_rectangle(
                        (text_x_offset, y, text_x_offset + text_width * fraction, y + bar_h),
                        radius=bar_h / 2,
                        fill=_ACCENT_COLOR,
                    )
            y += bar_h + nominal_height * 0.07

            if remaining_text:
                if draw:
                    draw.text((text_x_offset, y), remaining_text, font=small_font, fill=_MUTED)
                y += nominal_height * 0.13

            for line in description_lines:
                if draw:
                    draw.text((text_x_offset, y), line, font=small_font, fill=_MUTED)
                y += nominal_height * 0.13
            y += nominal_height * 0.04

        if next_text:
            if draw:
                draw.text((text_x_offset, y), next_text, font=small_font, fill=_MUTED)
            y += nominal_height * 0.15

        if technical_lines:
            y += nominal_height * 0.03
            for line in technical_lines:
                if draw:
                    draw.text((text_x_offset, y), line, font=small_font, fill=_MUTED)
                y += nominal_height * 0.13

        return y

    content_bottom = layout(None)
    height = max(nominal_height, round(content_bottom + padding * 0.6))

    # Everything is drawn in the panel's own local coordinate space (origin
    # at its top-left corner), then the whole panel is composited onto the
    # canvas once -- so layout() never needs to know about the drop-shadow
    # margin surrounding it.
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height * 0.12, fill=_PANEL_COLOR)
    accent_width = max(6, round(width * 0.008))
    panel_draw.rounded_rectangle((0, 0, accent_width, height - 1), radius=height * 0.02, fill=_ACCENT_COLOR)

    logo_image = _logo_tile(logo, logo_size) if logo else _fallback_avatar(channel.name, logo_size)
    panel.alpha_composite(logo_image, (padding, padding))

    if poster_image is not None:
        fitted_poster = _fit_within_box(poster_image, poster_width, poster_height)
        poster_x = width - padding - poster_width
        poster_y = round((height - poster_height) / 2)
        panel.alpha_composite(fitted_poster, (poster_x, poster_y))

    layout(panel_draw)

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1),
        radius=height * 0.12,
        fill=(0, 0, 0, 170),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.05)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def _render_epg_hero(
    channel: Channel,
    current: Programme,
    upcoming: Programme | None,
    display: EpgDisplay,
    now: datetime,
    backdrop_image: Image.Image,
    canvas_width: int,
    canvas_height: int,
    badges: list[str] | None,
    favorites: set[str] | None,
    logo: Image.Image | None = None,
    title_logo: Image.Image | None = None,
    stream_info: StreamInfo | None = None,
) -> Image.Image:
    """Full-bleed hero variant of the channel/EPG overlay, for a live
    channel currently airing a movie TMDB has backdrop art for -- the
    live-TV counterpart to _render_vod_info_hero, sharing its exact
    full-bleed-backdrop-plus-bottom-gradient technique (_cover_fill/
    _with_flat_alpha/_bottom_fade_gradient) and two-pass, bottom-anchored
    layout. Shows the same content _render_epg_banner does for `current`
    (title/year, time range, rating, category, director, live progress
    bar/remaining time) plus the channel name (with its favorite heart
    marker, in place of the VOD hero's plain "NOW PLAYING" eyebrow, since
    which channel this is stays relevant for live TV) and the "Next"
    line -- everything except the full quality-badge row (`badges`,
    accepted here only so both dispatch targets share one call site,
    never actually drawn), which would clash with a hero image already
    establishing its own visual identity. `stream_info` (see
    Player.stream_info), when given, is the exception to that -- but
    still deliberately light: stream_info.hdr draws a single small
    outlined tag (_draw_hdr_pill) right after the time range on its own
    row (see that function's own docstring), and _hero_tech_summary
    draws one compact extra line (container/bitrate/track counts, never
    a full breakdown) after the "Next" line.

    The channel logo, when given, is the one exception: a small mark
    (the same _logo_tile treatment _render_epg_banner uses, just much
    smaller) placed directly to the left of the channel-name eyebrow --
    a subtle "channel bug" sized to that line of text, not the banner's
    own prominent standalone tile, since it sits inside the already-dark
    bottom info panel rather than over the backdrop photo itself.

    `title_logo`, when given, is the movie's own TMDB title-treatment
    logo (distinct from `logo`, the channel mark above), composited via
    _composite_title_logo in the top-right corner over the backdrop
    photo itself -- the opposite corner from the bottom-left text block,
    so the two never compete for space.
    """
    padding = round(canvas_width * 0.045)
    bottom_margin = round(canvas_height * 0.07)
    text_width = min(round(canvas_width * 0.46), canvas_width - 2 * padding)

    eyebrow_font = _font("Inter-Bold.ttf", round(canvas_height * 0.02))
    title_font = _font("Inter-Bold.ttf", round(canvas_height * 0.056))
    meta_font = _font("Inter-Regular.ttf", round(canvas_height * 0.024))
    body_font = _font("Inter-Regular.ttf", round(canvas_height * 0.021))
    bar_h = max(4, round(canvas_height * 0.006))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    is_favorite = favorites is not None and channel.name in favorites
    eyebrow_text = (_FAVORITE_MARK + channel.name) if is_favorite else channel.name

    eyebrow_bbox = measure.textbbox((0, 0), eyebrow_text, font=eyebrow_font)
    eyebrow_text_height = eyebrow_bbox[3] - eyebrow_bbox[1]
    eyebrow_logo_size = round(eyebrow_text_height * 1.6)
    eyebrow_logo_gap = round(eyebrow_logo_size * 0.3)
    eyebrow_text_x = padding + eyebrow_logo_size + eyebrow_logo_gap if logo is not None else padding

    fallback_year = tmdb.release_year_for(current.title, current.category, current.year, channel.group_title)
    title_lines = _wrap_text(measure, _title_with_year(current, fallback_year), title_font, text_width, 2)

    start_local = display.to_local(current.start, channel_name=channel.name)
    stop_local = display.to_local(current.stop, channel_name=channel.name)
    time_text = f"{start_local.strftime('%H:%M')} – {stop_local.strftime('%H:%M')}"
    time_text_width = measure.textlength(time_text, font=meta_font)
    hdr_gap = round(canvas_width * 0.01)

    rating = tmdb.rating_for(current.title, current.category, current.year, channel.group_title)
    rating_score_text = f"★ {rating:.1f}" if rating is not None else None
    attribution_logo = None
    if rating_score_text is not None:
        rating_bbox = measure.textbbox((0, 0), rating_score_text, font=meta_font)
        attribution_logo = _tmdb_logo(rating_bbox[3] - rating_bbox[1])
        rating_gap = round(canvas_width * 0.01)

    category_text = _strip_unsupported_glyphs(current.category, meta_font) if current.category else None

    director = current.director or tmdb.director_for(current.title, current.category, current.year, channel.group_title)
    director_lines = _wrap_text(measure, f"Directed by {director}", meta_font, text_width, 2) if director else []

    description_lines = (
        _wrap_text(measure, current.description, body_font, text_width, _MAX_DESCRIPTION_LINES)
        if current.description
        else []
    )

    # Same shift-correction as _render_epg_banner -- current.start/stop
    # are raw feed times, `now` is real time.
    shift = display.shift_for(channel.name)
    corrected_start = current.start + shift
    corrected_stop = current.stop + shift
    total_seconds = (corrected_stop - corrected_start).total_seconds()
    elapsed_seconds = (now - corrected_start).total_seconds()
    fraction = min(1.0, max(0.0, elapsed_seconds / total_seconds)) if total_seconds > 0 else 0.0
    remaining_text = _format_remaining(total_seconds - elapsed_seconds) if total_seconds > 0 else None

    next_text = None
    if upcoming:
        start = display.to_local(upcoming.start, channel_name=channel.name).strftime("%H:%M")
        next_text = f"Next  ·  {upcoming.title} ({start})"

    hdr = stream_info.hdr if stream_info else None
    tech_summary = _hero_tech_summary(stream_info)

    def layout(draw: ImageDraw.ImageDraw | None, start_y: float) -> float:
        y = start_y
        if draw:
            if logo is not None:
                logo_y = y - eyebrow_bbox[1] + (eyebrow_text_height - eyebrow_logo_size) / 2
                canvas.alpha_composite(_logo_tile(logo, eyebrow_logo_size), (padding, round(logo_y)))
            draw.text(
                (eyebrow_text_x, y), eyebrow_text, font=eyebrow_font, fill=_FAVORITE_COLOR if is_favorite else _ACCENT_COLOR
            )
        y += eyebrow_font.size * 1.7

        for line in title_lines:
            if draw:
                draw.text((padding, y), line, font=title_font, fill=_WHITE)
            y += title_font.size * 1.15

        if draw:
            draw.text((padding, y), time_text, font=meta_font, fill=_MUTED)
            if hdr:
                _draw_hdr_pill(measure, draw, padding + time_text_width + hdr_gap, y, hdr, meta_font)
            if rating_score_text is not None:
                attribution_x = padding + text_width - attribution_logo.width
                canvas.alpha_composite(attribution_logo, (round(attribution_x), round(y)))
                score_x = attribution_x - rating_gap - (rating_bbox[2] - rating_bbox[0]) - rating_bbox[0]
                draw.text((score_x, y - rating_bbox[1]), rating_score_text, font=meta_font, fill=_RATING_STAR_COLOR)
        y += meta_font.size * 1.5

        if category_text:
            if draw:
                draw.text((padding, y), category_text, font=meta_font, fill=_ACCENT_COLOR)
            y += meta_font.size * 1.3

        for line in director_lines:
            if draw:
                draw.text((padding, y), line, font=meta_font, fill=_MUTED)
            y += meta_font.size * 1.3

        if draw:
            draw.rounded_rectangle((padding, y, padding + text_width, y + bar_h), radius=bar_h / 2, fill=_BAR_TRACK)
            if fraction > 0:
                draw.rounded_rectangle(
                    (padding, y, padding + text_width * fraction, y + bar_h), radius=bar_h / 2, fill=_ACCENT_COLOR
                )
        y += bar_h + canvas_height * 0.016

        if remaining_text:
            if draw:
                draw.text((padding, y), remaining_text, font=meta_font, fill=_MUTED)
            y += meta_font.size * 1.3

        if description_lines:
            y += canvas_height * 0.008
            for line in description_lines:
                if draw:
                    draw.text((padding, y), line, font=body_font, fill=_MUTED)
                y += body_font.size * 1.35

        if next_text:
            y += canvas_height * 0.012
            if draw:
                draw.text((padding, y), next_text, font=body_font, fill=_MUTED)
            y += body_font.size * 1.3

        if tech_summary:
            y += canvas_height * 0.008
            if draw:
                draw.text((padding, y), tech_summary, font=body_font, fill=_MUTED)
            y += body_font.size * 1.3

        return y

    content_height = layout(None, 0.0)
    content_top = max(padding, canvas_height - bottom_margin - content_height)
    gradient_start_row = max(0, round(content_top - canvas_height * _HERO_GRADIENT_LEAD_IN_FRACTION))

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    canvas.alpha_composite(_with_flat_alpha(_cover_fill(backdrop_image, canvas_width, canvas_height), _HERO_BACKDROP_ALPHA))
    canvas.alpha_composite(_bottom_fade_gradient(canvas_width, canvas_height, gradient_start_row, _HERO_GRADIENT_MAX_ALPHA))
    if title_logo is not None:
        _composite_title_logo(canvas, title_logo, canvas_width, canvas_height, padding)
    draw = ImageDraw.Draw(canvas)
    layout(draw, content_top)

    return canvas


def render_recording_overlay(
    recording: RecordingFile,
    canvas_width: int = 1920,
    position_seconds: float | None = None,
    duration_seconds: float | None = None,
) -> Image.Image:
    """A banner shown in place of the live EPG overlay (render_epg_overlay)
    while watching back a previously saved recording (see the 'w'
    recordings browser) -- a live channel's guide has nothing meaningful
    to show for local file playback, so this shows the recording's own
    label, when it was made, and a playback-progress bar instead.
    """
    nominal_height = max(140, round(canvas_width * 0.15))
    margin = round(nominal_height * 0.08)
    width = max(400, canvas_width - 2 * margin)
    padding = round(nominal_height * 0.12)
    icon_size = nominal_height - 2 * padding
    text_x_offset = padding * 2 + icon_size
    text_width = width - padding - text_x_offset

    eyebrow_font = _font("Inter-Bold.ttf", round(nominal_height * 0.1))
    title_font = _font("Inter-Bold.ttf", round(nominal_height * 0.17))
    meta_font = _font("Inter-Regular.ttf", round(nominal_height * 0.105))
    bar_h = max(4, round(nominal_height * 0.045))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    title_text = _fit_text(measure, recording.label, title_font, text_width)
    recorded_text = f"Recorded {recording.recorded_at.strftime('%a %d %b, %H:%M')}"

    fraction = 0.0
    progress_text = None
    if position_seconds is not None and duration_seconds:
        fraction = min(1.0, max(0.0, position_seconds / duration_seconds))
        progress_text = f"{_format_playback_time(position_seconds)} / {_format_playback_time(duration_seconds)}"

    def layout(draw: ImageDraw.ImageDraw | None) -> float:
        y = padding * 0.6
        if draw:
            draw.text((text_x_offset, y), "RECORDING PLAYBACK", font=eyebrow_font, fill=_ACCENT_COLOR)
        y += nominal_height * 0.16

        if draw:
            draw.text((text_x_offset, y), title_text, font=title_font, fill=_WHITE)
        y += nominal_height * 0.22

        if draw:
            draw.text((text_x_offset, y), recorded_text, font=meta_font, fill=_MUTED)
        y += nominal_height * 0.20

        if draw:
            draw.rounded_rectangle(
                (text_x_offset, y, text_x_offset + text_width, y + bar_h), radius=bar_h / 2, fill=_BAR_TRACK
            )
            if fraction > 0:
                draw.rounded_rectangle(
                    (text_x_offset, y, text_x_offset + text_width * fraction, y + bar_h),
                    radius=bar_h / 2,
                    fill=_ACCENT_COLOR,
                )
        y += bar_h + nominal_height * 0.07

        if progress_text:
            if draw:
                draw.text((text_x_offset, y), progress_text, font=meta_font, fill=_MUTED)
            y += nominal_height * 0.15

        return y

    content_bottom = layout(None)
    height = max(nominal_height, round(content_bottom + padding * 0.6))

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height * 0.12, fill=_PANEL_COLOR)
    accent_width = max(6, round(width * 0.008))
    panel_draw.rounded_rectangle((0, 0, accent_width, height - 1), radius=height * 0.02, fill=_ACCENT_COLOR)

    panel.alpha_composite(_recording_icon(icon_size), (padding, padding))

    layout(panel_draw)

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1),
        radius=height * 0.12,
        fill=(0, 0, 0, 170),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.05)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def render_vod_info_overlay(
    item: VodItem,
    canvas_width: int,
    canvas_height: int,
    position_seconds: float | None = None,
    duration_seconds: float | None = None,
    eyebrow: str = "NOW PLAYING",
    prefer_card: bool = False,
    stream_info: StreamInfo | None = None,
) -> Image.Image:
    """The 'i' key's "what am I watching" overlay for a VodItem. Dispatches
    to _render_vod_info_hero -- a full-bleed, Netflix/Prime-style treatment
    using the movie's own wide backdrop art -- whenever item.backdrop_url
    actually resolves to a real image (currently TMDB-sourced local-file/
    YouTube VOD only, see vod.VodItem.backdrop_url); every other source
    (Plex, Xtream, Stalker, a bare M3U --vod-group entry) falls back to
    _render_vod_info_card's plain poster-and-panel layout, unchanged from
    before backdrop support existed. `eyebrow` defaults to the "currently
    playing" framing every call site but one uses -- cli.py's Plex browser
    reuses this same overlay to show details for the currently *selected*
    (not necessarily playing) item, passing eyebrow="DETAILS" instead.
    `prefer_card` skips the hero dispatch (and its backdrop_image fetch)
    entirely, forcing the plain card layout even when a backdrop would
    otherwise resolve -- the Plex browser's own selected-item details
    popup already sits on top of its own full-screen poster backdrop (see
    overlay._plex_full_backdrop), so stacking the hero's own separate
    backdrop on top of *that* looked cluttered; the small card panel
    reads cleanly against it instead. item.logo_url, when present, is
    only ever fetched/used on the hero path (_render_vod_info_hero's
    top-right title logo) -- the card stays deliberately minimal.

    `stream_info` (see Player.stream_info) reaches both paths: the hero
    gets stream_info.hdr's single small HDR-type tag next to the year
    (as before) plus _hero_tech_summary's one compact line; the card
    gets _technical_detail_lines' full container/bitrate/per-track
    breakdown, which it had no equivalent of before -- see those two
    functions' own docstrings for why the hero stays lighter."""
    backdrop_image = None if prefer_card else (fetch_image(item.backdrop_url) if item.backdrop_url else None)
    if backdrop_image is not None:
        title_logo_image = fetch_image(item.logo_url) if item.logo_url else None
        return _render_vod_info_hero(
            item,
            canvas_width,
            canvas_height,
            backdrop_image,
            position_seconds,
            duration_seconds,
            eyebrow,
            title_logo_image,
            stream_info,
        )
    return _render_vod_info_card(
        item, canvas_width, canvas_height, position_seconds, duration_seconds, eyebrow, stream_info
    )


_CHAPTER_TICK_COLOR = (255, 255, 255, 190)


def _chapter_tick_positions(
    chapters: list[VodChapter], duration_seconds: float, bar_left: float, bar_width: float
) -> list[float]:
    """Absolute x-coordinates, one per chapter boundary, for tick marks
    drawn over a progress bar spanning [bar_left, bar_left + bar_width].
    Skips a chapter starting at/before 0 -- that's the bar's own left
    edge already, a tick there would just sit on top of the bar's
    rounded corner. Clamps every other position to the bar's own width
    so a chapter starting past duration_seconds (a malformed or stale
    Plex chapter list) never draws off the end."""
    return [
        bar_left + bar_width * min(1.0, max(0.0, chapter.start_seconds / duration_seconds))
        for chapter in chapters
        if chapter.start_seconds > 0
    ]


def _current_chapter_title(chapters: list[VodChapter] | None, position_seconds: float | None) -> str | None:
    """The title of whichever chapter `position_seconds` currently falls
    within -- the last chapter (chapters is start-of-file-first, see
    plex._chapters) whose start_seconds is at or before the current
    position, since a Chapter list is just a flat sequence of
    boundaries with no explicit end time of its own. None when there's
    no chapter data, no known position yet, or Plex never set a title
    (`tag`) for the current chapter."""
    if not chapters or position_seconds is None:
        return None
    current = None
    for chapter in chapters:
        if chapter.start_seconds <= position_seconds:
            current = chapter
        else:
            break
    return current.title if current else None


def _render_vod_info_hero(
    item: VodItem,
    canvas_width: int,
    canvas_height: int,
    backdrop_image: Image.Image,
    position_seconds: float | None,
    duration_seconds: float | None,
    eyebrow: str = "NOW PLAYING",
    title_logo: Image.Image | None = None,
    stream_info: StreamInfo | None = None,
) -> Image.Image:
    """Full-bleed hero variant of the 'i' key overlay: `backdrop_image`
    fills the whole screen at partial opacity (_HERO_BACKDROP_ALPHA) so the
    paused/playing video stays visibly showing through it, darkening into
    a near-opaque gradient toward the bottom where the title/metadata/
    synopsis sit -- the same content _render_vod_info_card shows, just
    without its poster thumbnail (the backdrop itself already establishes
    the movie's visual identity) or its floating panel/shadow (the
    backdrop already covers the full canvas, so there's no edge to shadow
    against).

    Two-pass layout like _render_vod_info_card: `layout` is measured once
    (`active_draw=None`) to get the text block's total height, so it can
    be bottom-anchored against `canvas_height` (unlike the card, whose
    canvas grows to fit its content, this canvas is always the full,
    fixed screen size) and so the gradient can start right above where
    the text actually begins, rather than at a fixed screen fraction that
    would either undercover a long synopsis or overdarken a short one.
    `layout` composites the TMDB attribution logo straight onto `canvas`
    during the real pass -- a name resolved late, exactly like
    _render_vod_info_card's own `panel` closure reference, since `canvas`
    isn't created until after the measurement pass below.

    `title_logo`, when given, is the movie/show's own TMDB title-
    treatment logo (distinct from the small TMDB attribution mark next
    to the rating), composited via _composite_title_logo in the
    top-right corner -- see _render_epg_hero's identical treatment.

    `stream_info` (see Player.stream_info), when given: stream_info.hdr
    draws a single small outlined tag (_draw_hdr_pill) right after the
    year on the year/rating row -- see that function's own docstring for
    why this is deliberately not the full _draw_quality_badges row --
    and _hero_tech_summary draws one compact extra line (container/
    bitrate/track counts, never a full breakdown) after the progress bar.
    """
    padding = round(canvas_width * 0.045)
    bottom_margin = round(canvas_height * 0.07)
    text_width = min(round(canvas_width * 0.46), canvas_width - 2 * padding)

    eyebrow_font = _font("Inter-Bold.ttf", round(canvas_height * 0.02))
    title_font = _font("Inter-Bold.ttf", round(canvas_height * 0.056))
    meta_font = _font("Inter-Regular.ttf", round(canvas_height * 0.024))
    body_font = _font("Inter-Regular.ttf", round(canvas_height * 0.021))
    bar_h = max(4, round(canvas_height * 0.006))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    title_lines = _wrap_text(measure, item.title, title_font, text_width, 2)
    description_lines = (
        _wrap_text(measure, item.description, body_font, text_width, _MAX_DESCRIPTION_LINES) if item.description else []
    )
    director_lines = _wrap_text(measure, f"Directed by {item.director}", meta_font, text_width, 2) if item.director else []

    rating_score_text = f"★ {item.rating}" if item.rating else None
    attribution_logo = None
    if rating_score_text is not None:
        rating_bbox = measure.textbbox((0, 0), rating_score_text, font=meta_font)
        if item.rating_is_tmdb:
            attribution_logo = _tmdb_logo(rating_bbox[3] - rating_bbox[1])
        rating_gap = round(canvas_width * 0.01)
    year_width = measure.textlength(item.year, font=meta_font) if item.year else 0
    hdr_gap = round(canvas_width * 0.01)

    fraction = 0.0
    progress_text = None
    if position_seconds is not None and duration_seconds:
        fraction = min(1.0, max(0.0, position_seconds / duration_seconds))
        progress_text = f"{_format_playback_time(position_seconds)} / {_format_playback_time(duration_seconds)}"
    chapter_title = _current_chapter_title(item.chapters, position_seconds) if progress_text else None
    chapter_gap = round(canvas_width * 0.012)

    hdr = stream_info.hdr if stream_info else None
    tech_summary = _hero_tech_summary(stream_info)

    def layout(draw: ImageDraw.ImageDraw | None, start_y: float) -> float:
        y = start_y
        if draw:
            draw.text((padding, y), eyebrow, font=eyebrow_font, fill=_ACCENT_COLOR)
        y += eyebrow_font.size * 1.7

        for line in title_lines:
            if draw:
                draw.text((padding, y), line, font=title_font, fill=_WHITE)
            y += title_font.size * 1.15

        if item.year or rating_score_text or hdr:
            if draw:
                if item.year:
                    draw.text((padding, y), item.year, font=meta_font, fill=_MUTED)
                if hdr:
                    hdr_x = padding + year_width + (hdr_gap if item.year else 0)
                    _draw_hdr_pill(measure, draw, hdr_x, y, hdr, meta_font)
                if rating_score_text is not None:
                    if attribution_logo is not None:
                        attribution_x = padding + text_width - attribution_logo.width
                        canvas.alpha_composite(attribution_logo, (round(attribution_x), round(y)))
                        score_x = attribution_x - rating_gap - (rating_bbox[2] - rating_bbox[0]) - rating_bbox[0]
                    else:
                        score_x = padding + text_width - (rating_bbox[2] - rating_bbox[0]) - rating_bbox[0]
                    draw.text((score_x, y - rating_bbox[1]), rating_score_text, font=meta_font, fill=_RATING_STAR_COLOR)
            y += meta_font.size * 1.5

        for line in director_lines:
            if draw:
                draw.text((padding, y), line, font=meta_font, fill=_MUTED)
            y += meta_font.size * 1.3

        if description_lines:
            y += canvas_height * 0.008
            for line in description_lines:
                if draw:
                    draw.text((padding, y), line, font=body_font, fill=_MUTED)
                y += body_font.size * 1.35

        if progress_text:
            y += canvas_height * 0.018
            if draw:
                draw.rounded_rectangle((padding, y, padding + text_width, y + bar_h), radius=bar_h / 2, fill=_BAR_TRACK)
                if fraction > 0:
                    draw.rounded_rectangle(
                        (padding, y, padding + text_width * fraction, y + bar_h), radius=bar_h / 2, fill=_ACCENT_COLOR
                    )
                if item.chapters and duration_seconds:
                    for tick_x in _chapter_tick_positions(item.chapters, duration_seconds, padding, text_width):
                        draw.line((tick_x, y, tick_x, y + bar_h), fill=_CHAPTER_TICK_COLOR, width=1)
            y += bar_h + canvas_height * 0.016
            if draw:
                draw.text((padding, y), progress_text, font=meta_font, fill=_MUTED)
                if chapter_title:
                    available = text_width - measure.textlength(progress_text, font=meta_font) - chapter_gap
                    if available > 0:
                        fitted = _fit_text(measure, chapter_title, meta_font, available)
                        fitted_w = measure.textlength(fitted, font=meta_font)
                        draw.text((padding + text_width - fitted_w, y), fitted, font=meta_font, fill=_MUTED)
            y += meta_font.size * 1.3

        if tech_summary:
            y += canvas_height * 0.008
            if draw:
                draw.text((padding, y), tech_summary, font=meta_font, fill=_MUTED)
            y += meta_font.size * 1.3

        return y

    content_height = layout(None, 0.0)
    content_top = max(padding, canvas_height - bottom_margin - content_height)
    gradient_start_row = max(0, round(content_top - canvas_height * _HERO_GRADIENT_LEAD_IN_FRACTION))

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    canvas.alpha_composite(_with_flat_alpha(_cover_fill(backdrop_image, canvas_width, canvas_height), _HERO_BACKDROP_ALPHA))
    canvas.alpha_composite(_bottom_fade_gradient(canvas_width, canvas_height, gradient_start_row, _HERO_GRADIENT_MAX_ALPHA))
    if title_logo is not None:
        _composite_title_logo(canvas, title_logo, canvas_width, canvas_height, padding)
    draw = ImageDraw.Draw(canvas)
    layout(draw, content_top)

    return canvas


def _render_vod_info_card(
    item: VodItem,
    canvas_width: int,
    canvas_height: int,
    position_seconds: float | None = None,
    duration_seconds: float | None = None,
    eyebrow: str = "NOW PLAYING",
    stream_info: StreamInfo | None = None,
) -> Image.Image:
    """A modal popup showing everything known about the VodItem currently
    playing, plus a playback-progress bar -- render_recording_overlay's
    "what am I watching" idea, combined with render_programme_details'
    poster+description+rating layout, for on-demand content that (unlike
    a plain recording) can have real metadata attached (currently Plex
    and, if --tmdb-api-token is given, a local file or YouTube video
    populate poster_url/rating/description; other VOD sources still get
    a sensible, poster-less rendering from whatever fields they do set).
    The rating row mirrors render_programme_details' own star-plus-logo
    styling exactly, except the TMDB attribution logo (required by their
    API terms whenever their data is shown) is only drawn when
    item.rating_is_tmdb is True -- a Plex/Xtream-sourced rating is never
    TMDB's, so it stays plain text instead of a misattributed logo.

    `stream_info` (see Player.stream_info), when given, adds
    _technical_detail_lines' full container/bitrate/per-track breakdown
    after the progress bar -- see that function's own docstring.

    render_vod_info_overlay's fallback whenever there's no backdrop image
    to justify _render_vod_info_hero's full-bleed treatment instead.
    """
    width = max(480, min(round(canvas_width * 0.7), canvas_width - 80))
    nominal_height = max(160, round(canvas_width * 0.15))
    margin = round(nominal_height * 0.08)
    padding = round(nominal_height * 0.12)

    # Reserved off nominal_height (not the final, content-driven `height`
    # below) to avoid a circular dependency -- see render_epg_overlay.
    poster_image = fetch_image(item.poster_url) if item.poster_url else None
    poster_width = poster_height = 0
    poster_reserved_width = 0
    if poster_image is not None:
        poster_height = round(nominal_height * 1.3)
        poster_width = round(poster_height * 2 / 3)  # classic movie poster aspect ratio
        poster_reserved_width = poster_width + padding

    text_width = width - 2 * padding - poster_reserved_width
    bar_h = max(4, round(nominal_height * 0.045))

    eyebrow_font = _font("Inter-Bold.ttf", round(nominal_height * 0.1))
    title_font = _font("Inter-Bold.ttf", round(nominal_height * 0.155))
    meta_font = _font("Inter-Regular.ttf", round(nominal_height * 0.095))
    body_font = _font("Inter-Regular.ttf", round(nominal_height * 0.09))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    title_lines = _wrap_text(measure, item.title, title_font, text_width, 2)
    description_lines = (
        _wrap_text(measure, item.description, body_font, text_width, _MAX_DETAILS_DESCRIPTION_LINES)
        if item.description
        else []
    )
    # Wrapped rather than a single fixed-height line -- unlike item.year,
    # this can run long (co-directed films join more than one name, see
    # tmdb._fetch_movie_director/plex.resolve_plex_playable).
    director_lines = _wrap_text(measure, f"Directed by {item.director}", meta_font, text_width, 2) if item.director else []

    # Right-aligned against the year's own line (below), same as
    # render_programme_details' rating-vs-time_text row -- reads as part
    # of the existing metadata row instead of a bolted-on element.
    rating_score_text = f"★ {item.rating}" if item.rating else None
    attribution_logo = None
    if rating_score_text is not None:
        rating_bbox = measure.textbbox((0, 0), rating_score_text, font=meta_font)
        if item.rating_is_tmdb:
            attribution_logo = _tmdb_logo(rating_bbox[3] - rating_bbox[1])
        rating_gap = round(nominal_height * 0.03)

    fraction = 0.0
    progress_text = None
    if position_seconds is not None and duration_seconds:
        fraction = min(1.0, max(0.0, position_seconds / duration_seconds))
        progress_text = f"{_format_playback_time(position_seconds)} / {_format_playback_time(duration_seconds)}"
    chapter_title = _current_chapter_title(item.chapters, position_seconds) if progress_text else None
    chapter_gap = round(nominal_height * 0.04)
    technical_lines = [
        _fit_text(measure, line, meta_font, text_width) for line in _technical_detail_lines(stream_info)
    ]

    def layout(draw: ImageDraw.ImageDraw | None) -> float:
        y = padding * 0.6
        if draw:
            draw.text((padding, y), eyebrow, font=eyebrow_font, fill=_ACCENT_COLOR)
        y += nominal_height * 0.16

        for line in title_lines:
            if draw:
                draw.text((padding, y), line, font=title_font, fill=_WHITE)
            y += nominal_height * 0.19

        if item.year or rating_score_text:
            if draw:
                if item.year:
                    draw.text((padding, y), item.year, font=meta_font, fill=_MUTED)
                if rating_score_text is not None:
                    if attribution_logo is not None:
                        attribution_x = padding + text_width - attribution_logo.width
                        panel.alpha_composite(attribution_logo, (round(attribution_x), round(y)))
                        score_x = attribution_x - rating_gap - (rating_bbox[2] - rating_bbox[0]) - rating_bbox[0]
                    else:
                        score_x = padding + text_width - (rating_bbox[2] - rating_bbox[0]) - rating_bbox[0]
                    draw.text((score_x, y - rating_bbox[1]), rating_score_text, font=meta_font, fill=_RATING_STAR_COLOR)
            y += nominal_height * 0.16

        for line in director_lines:
            if draw:
                draw.text((padding, y), line, font=meta_font, fill=_MUTED)
            y += nominal_height * 0.12

        if description_lines:
            y += nominal_height * 0.03
            for line in description_lines:
                if draw:
                    draw.text((padding, y), line, font=body_font, fill=_MUTED)
                y += nominal_height * 0.12

        if progress_text:
            y += nominal_height * 0.08
            if draw:
                draw.rounded_rectangle(
                    (padding, y, padding + text_width, y + bar_h), radius=bar_h / 2, fill=_BAR_TRACK
                )
                if fraction > 0:
                    draw.rounded_rectangle(
                        (padding, y, padding + text_width * fraction, y + bar_h),
                        radius=bar_h / 2,
                        fill=_ACCENT_COLOR,
                    )
                if item.chapters and duration_seconds:
                    for tick_x in _chapter_tick_positions(item.chapters, duration_seconds, padding, text_width):
                        draw.line((tick_x, y, tick_x, y + bar_h), fill=_CHAPTER_TICK_COLOR, width=1)
            y += bar_h + nominal_height * 0.07
            if draw:
                draw.text((padding, y), progress_text, font=meta_font, fill=_MUTED)
                if chapter_title:
                    available = text_width - measure.textlength(progress_text, font=meta_font) - chapter_gap
                    if available > 0:
                        fitted = _fit_text(measure, chapter_title, meta_font, available)
                        fitted_w = measure.textlength(fitted, font=meta_font)
                        draw.text((padding + text_width - fitted_w, y), fitted, font=meta_font, fill=_MUTED)
            y += nominal_height * 0.15

        if technical_lines:
            y += nominal_height * 0.03
            for line in technical_lines:
                if draw:
                    draw.text((padding, y), line, font=meta_font, fill=_MUTED)
                y += nominal_height * 0.12

        return y

    content_bottom = layout(None)
    height = max(nominal_height, round(content_bottom + padding * 0.6))
    if poster_image is not None:
        height = max(height, poster_height + round(padding * 1.2))

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height * 0.06, fill=_PANEL_COLOR)
    accent_width = max(6, round(width * 0.008))
    panel_draw.rounded_rectangle((0, 0, accent_width, height - 1), radius=height * 0.02, fill=_ACCENT_COLOR)

    if poster_image is not None:
        fitted_poster = _fit_within_box(poster_image, poster_width, poster_height)
        poster_x = width - padding - poster_width
        poster_y = round((height - poster_height) / 2)
        panel.alpha_composite(fitted_poster, (poster_x, poster_y))

    layout(panel_draw)

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), radius=height * 0.06, fill=(0, 0, 0, 190)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.04)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def render_skip_marker_overlay(kind: str, canvas_width: int = 1920, canvas_height: int = 1080) -> Image.Image:
    """The small "Skip Intro"/"Skip Credits" prompt for a Plex VOD item
    with intro/credits markers (see vod.VodMarker) -- shown by cli.py's
    marker-poll loop only while playback position is inside the relevant
    window, confirmed with ENTER (also 'j' as an unadvertised keyboard
    alias -- see cli.py's confirm_skip_marker) rather than skipped
    automatically (every other seek in the app, including chapter-skip,
    is already user-triggered only -- this isn't the first exception).
    ENTER is shown rather than 'j' since it's the one confirm key that
    works from both a keyboard and an IR/BLE air-mouse remote's OK
    button. `kind` is
    "intro" or "credits", controlling only the label text; the two share
    one render path since the visual treatment is identical. Sized to its
    own content and positioned by the caller (bottom-right corner, same
    "small self-contained panel with its own shadow margin" construction
    as render_guide_filter_prompt, just content-sized here instead of a
    fixed width -- there's no text input to reserve room for)."""
    label = "Skip Intro" if kind == "intro" else "Skip Credits"
    height = round(canvas_height * 0.06)
    label_font = _font("Inter-Bold.ttf", round(height * 0.4))
    hint_font = _font("Inter-Regular.ttf", round(height * 0.3))
    margin = round(height * 0.35)
    pad_x = round(height * 0.45)

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    hint_text = "  [ENTER]"
    label_width = measure.textlength(label, font=label_font)
    hint_width = measure.textlength(hint_text, font=hint_font)
    width = round(pad_x * 2 + label_width + hint_width)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=height * 0.18,
        fill=_PANEL_COLOR,
        outline=_ACCENT_COLOR,
        width=max(2, round(height * 0.03)),
    )

    label_bbox = panel_draw.textbbox((0, 0), label, font=label_font)
    label_y = (height - (label_bbox[3] - label_bbox[1])) / 2 - label_bbox[1]
    panel_draw.text((pad_x, label_y), label, font=label_font, fill=_WHITE)
    hint_bbox = panel_draw.textbbox((0, 0), hint_text, font=hint_font)
    hint_y = (height - (hint_bbox[3] - hint_bbox[1])) / 2 - hint_bbox[1]
    panel_draw.text((pad_x + label_width, hint_y), hint_text, font=hint_font, fill=_MUTED)

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), radius=height * 0.18, fill=(0, 0, 0, 170)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.05)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def render_up_next_overlay(
    title: str,
    subtitle: str | None,
    thumb_image: Image.Image | None,
    seconds_remaining: int,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
) -> Image.Image:
    """The "Up Next: <title>, playing in Ns" prompt for a Plex TV show's
    end-of-episode auto-play countdown (see cli.py's handle_playback_ended/
    _up_next_tick) -- letting the countdown reach zero plays the next
    episode; ESC cancels. `thumb_image`, when given (the next episode's
    own thumbnail, fetched by the caller via fetch_image, same as every
    other overlay image), sits to the left of the text; `subtitle` is the
    episode's own "S02E05"-style label. Bottom-right corner, same as
    render_skip_marker_overlay, so the two prompts share one visual
    language even though they're never actually shown at the same time
    (skip-credits only ever shows while the current episode is still
    playing; this only ever shows once it's already ended)."""
    height = round(canvas_height * 0.16)
    padding = round(height * 0.12)
    margin = round(height * 0.18)

    thumb_width = thumb_height = 0
    thumb_reserved_width = 0
    if thumb_image is not None:
        thumb_height = height - 2 * padding
        thumb_width = round(thumb_height * 16 / 9)  # episode screengrab aspect ratio
        thumb_reserved_width = thumb_width + padding

    text_width = max(220, round(canvas_width * 0.26))
    width = 2 * padding + thumb_reserved_width + text_width

    eyebrow_font = _font("Inter-Bold.ttf", round(height * 0.12))
    title_font = _font("Inter-Bold.ttf", round(height * 0.17))
    meta_font = _font("Inter-Regular.ttf", round(height * 0.13))
    countdown_font = _font("Inter-Bold.ttf", round(height * 0.12))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    title_lines = _wrap_text(measure, title, title_font, text_width, 2)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height * 0.08, fill=_PANEL_COLOR)

    text_x = padding
    if thumb_image is not None:
        fitted_thumb = _fit_within_box(thumb_image, thumb_width, thumb_height)
        panel.alpha_composite(fitted_thumb, (padding, padding))
        text_x = padding + thumb_reserved_width

    y = padding
    panel_draw.text((text_x, y), "UP NEXT", font=eyebrow_font, fill=_ACCENT_COLOR)
    y += eyebrow_font.size * 1.6
    for line in title_lines:
        panel_draw.text((text_x, y), line, font=title_font, fill=_WHITE)
        y += title_font.size * 1.15
    if subtitle:
        panel_draw.text((text_x, y), subtitle, font=meta_font, fill=_MUTED)

    countdown_text = f"Playing in {seconds_remaining}s  ·  ESC to cancel"
    panel_draw.text(
        (text_x, height - padding - countdown_font.size), countdown_text, font=countdown_font, fill=_MUTED
    )

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), radius=height * 0.08, fill=(0, 0, 0, 170)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.04)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def render_chapter_preview_overlay(
    title: str,
    thumb_image: Image.Image | None,
    canvas_width: int = 1920,
    canvas_height: int = 1080,
) -> Image.Image:
    """The chapter-scrub preview panel for a Plex VOD item with chapters
    (see vod.VodChapter) -- shown by cli.py's preview_chapter while
    UP/DOWN moves a preview cursor through the chapter list without
    seeking yet; ENTER commits, ESC cancels, or it auto-commits after a
    short idle timeout (see cli.py's _CHAPTER_PREVIEW_COMMIT_SECONDS).
    `title` is already resolved by the caller -- the chapter's own title,
    or a "Chapter N" fallback built from its 1-based position, since
    VodChapter.title can be None (see its own docstring) and this
    function has no chapter-list/index to fall back through itself, same
    as render_up_next_overlay taking an already-resolved title rather
    than an episode object. `thumb_image`, when given, is the chapter's
    own thumbnail (Plex's real one, or a locally-generated frame grab --
    see VodChapter.thumb_url/overlay.chapter_thumbnail_url); a plain
    placeholder box is drawn in its place while still loading (or when
    generation failed) rather than shrinking the panel, so committing/
    cancelling a preview never has to fight a resizing overlay.

    Same visual language as render_skip_marker_overlay/render_up_next_overlay
    (rounded panel, drop shadow, one shared corner) -- positioned by the
    caller; the hint line's two keys mirror render_up_next_overlay's own
    "action key · cancel key" phrasing."""
    height = round(canvas_height * 0.16)
    padding = round(height * 0.12)
    margin = round(height * 0.18)

    thumb_height = height - 2 * padding
    thumb_width = round(thumb_height * 16 / 9)
    thumb_reserved_width = thumb_width + padding

    text_width = max(220, round(canvas_width * 0.22))
    width = 2 * padding + thumb_reserved_width + text_width

    title_font = _font("Inter-Bold.ttf", round(height * 0.17))
    hint_font = _font("Inter-Regular.ttf", round(height * 0.13))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    title_lines = _wrap_text(measure, title, title_font, text_width, 2)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height * 0.08, fill=_PANEL_COLOR)

    thumb_box = (padding, padding, padding + thumb_width, padding + thumb_height)
    if thumb_image is not None:
        fitted_thumb = _fit_within_box(thumb_image, thumb_width, thumb_height)
        panel.alpha_composite(fitted_thumb, (padding, padding))
    else:
        panel_draw.rounded_rectangle(thumb_box, radius=thumb_width * 0.06, fill=_GRID_HEADER_COLOR)
    text_x = padding + thumb_reserved_width

    y = padding
    for line in title_lines:
        panel_draw.text((text_x, y), line, font=title_font, fill=_WHITE)
        y += title_font.size * 1.15

    hint_text = "[ENTER] Jump  ·  [ESC] Cancel"
    panel_draw.text((text_x, height - padding - hint_font.size), hint_text, font=hint_font, fill=_MUTED)

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), radius=height * 0.08, fill=(0, 0, 0, 170)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.04)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def guide_eligible_channels(channels: list[Channel], epg: Epg) -> list[Channel]:
    """The full, unwindowed list of channels a program guide can show: only
    those with an EPG schedule (a real playlist can have thousands without
    one) -- unless literally none of them has one (some real playlists embed
    no EPG source whatsoever), in which case every channel is eligible, so
    the guide still shows *something* to browse and select (with blank
    timelines) rather than nothing at all. A caller moving a selection
    cursor should page through this full list, not just visible_guide_channels'
    windowed page of it, or the cursor can't scroll past the visible rows.
    """
    guide_channels = [c for c in channels if epg.schedule_for(c.tvg_id, c.tvg_name or c.name)]
    return guide_channels if guide_channels else channels


def resolve_channel_logo(channel: Channel, epg: Epg, online_logos: OnlineLogoIndex | None = None) -> Image.Image | None:
    """`channel`'s logo image, trying each known source in turn until one
    actually fetches and decodes -- not just the first source with a
    non-empty URL string, since a listed URL can be dead/blocked without
    that being knowable until the fetch is actually attempted (confirmed
    live: playlists commonly set their own tvg-logo to an imgur URL, and
    imgur widely rejects hotlinked requests -- see _decode_image's
    placeholder detection -- which used to make that broken URL "win" and
    block every other source from ever being tried, even one with a
    working logo for the same channel).

    Order: the channel's own tvg_logo; whatever icon the loaded EPG's own
    channel data supplies (see Epg.icon_for -- e.g. HDHomeRun's
    lineup.json has no logo field at all, but its XMLTV export does); and
    finally, if `online_logos` is given, an exact match (never a fuzzy
    guess) against iptv-org's community logo database (see
    tvdinner.channel_logos) -- for the many bare M3U playlists that carry
    no logo, and whose EPG (if any) doesn't either."""
    name = channel.tvg_name or channel.name
    candidates = (
        channel.tvg_logo,
        epg.icon_for(channel.tvg_id, name),
        online_logos.lookup(channel.tvg_id, name) if online_logos is not None else None,
    )
    for url in candidates:
        image = fetch_image(url)
        if image is not None:
            return image
    return None


_channel_logo_cache: dict[str, Image.Image | None] = {}
_channel_logo_in_flight: set[str] = set()


def cached_channel_logo(channel_url: str) -> Image.Image | None:
    """Pure, non-blocking, in-memory-only read of a channel's already-
    resolved logo (see prefetch_channel_logos) -- safe to call from a
    render function. Returns None both for "not fetched yet" and
    "fetched, no logo found from any source"."""
    return _channel_logo_cache.get(channel_url)


def prefetch_channel_logos(
    channels: list[Channel],
    epg: Epg,
    online_logos: OnlineLogoIndex | None = None,
    on_resolved: Callable[[], None] | None = None,
) -> None:
    """Spawn one daemon thread per channel not already cached or
    in-flight, each resolving that channel's logo the same way
    resolve_channel_logo does. Every candidate resolve_channel_logo
    tries is a real network round trip (or, for a dead/hotlink-blocked
    URL, up to fetch_image's own 10s timeout) -- confirmed live that a
    guide render trying this synchronously, once per visible row, is
    what made opening the guide for the first time in a session (or
    scrolling to reveal channels never shown before) take several
    seconds. Safe to call on every guide render tick with the current
    page of channels, same as tmdb.prefetch_ratings -- duplicates are
    always a no-op.

    Keyed by channel.url, not tvg_id: real-world M3U playlists commonly
    have several distinct channels sharing one tvg_id, and tvg_id would
    then incorrectly treat them as needing only one shared fetch.

    `on_resolved`, if given, is called -- on the fetching background
    thread, not the caller's -- once per channel this call actually
    spawned a fetch for, after that one fetch finishes, regardless of
    whether it found a logo. Without this, a freshly-opened guide only
    ever showed newly-resolved logos on whatever *later* render happened
    to come next (a page down, a channel switch, ...) rather than as
    soon as they were actually ready -- confirmed live that leaving the
    guide untouched right after opening it left every row on its
    placeholder avatar indefinitely, even once every fetch had long
    since completed. See cli.py's render_and_show_guide for the
    debounced re-render this drives."""
    for channel in channels:
        url = channel.url
        if url in _channel_logo_cache or url in _channel_logo_in_flight:
            continue
        _channel_logo_in_flight.add(url)

        def _fetch(channel: Channel = channel, url: str = url) -> None:
            try:
                _channel_logo_cache[url] = resolve_channel_logo(channel, epg, online_logos)
            finally:
                _channel_logo_in_flight.discard(url)
                if on_resolved is not None:
                    on_resolved()

        threading.Thread(target=_fetch, daemon=True).start()


def visible_guide_channels(
    channels: list[Channel],
    epg: Epg,
    current_channel_url: str | None,
    max_rows: int = 8,
    current_channel_name: str | None = None,
) -> list[Channel]:
    """The page of channels a program guide should show: guide_eligible_channels,
    in a window of at most `max_rows` centered on `current_channel_url`.

    Centered/matched by URL, not tvg_id: real-world M3U playlists often have
    several distinct channels (different quality tiers, backup servers)
    sharing the same tvg_id for EPG mapping purposes, and tvg_id would then
    incorrectly identify all of them as "the same" row.

    URL alone isn't always unique either, though (confirmed live: some
    real playlists reuse the exact same stream URL for a channel's SD and
    HD listing, e.g. "Channel 5" and "Channel 5 HD" both pointing at one
    URL) -- `list.index` always returns the *first* matching row
    regardless of which one was actually selected, which used to make
    the guide's cursor get permanently stuck bouncing back to that first
    row instead of advancing past it. `current_channel_name`, when given,
    disambiguates: a URL+name pair is trusted to be unique (two genuinely
    different rows sharing both would be a meaningless, degenerate
    playlist entry), falling back to the first URL match when it's not
    given or doesn't resolve to an exact pair match."""
    guide_channels = guide_eligible_channels(channels, epg)
    if not guide_channels:
        return []

    current_index = 0
    if current_channel_name is not None:
        current_index = next(
            (i for i, c in enumerate(guide_channels) if c.url == current_channel_url and c.name == current_channel_name),
            -1,
        )
    if current_index == -1 or current_channel_name is None:
        urls = [c.url for c in guide_channels]
        current_index = urls.index(current_channel_url) if current_channel_url in urls else 0
    row_count = min(max_rows, len(guide_channels))
    start_index = max(0, min(current_index - row_count // 2, len(guide_channels) - row_count))
    return guide_channels[start_index : start_index + row_count]


def guide_reference_time(
    now: datetime, window_start: datetime, window_hours: float = DEFAULT_GUIDE_WINDOW_HOURS
) -> datetime:
    """The moment a guide selection cursor should point at: the real current
    time if the displayed window actually contains it, otherwise the start
    of whatever time range has been paged into view."""
    window_end = window_start + timedelta(hours=window_hours)
    return now if window_start <= now <= window_end else window_start


def selected_guide_programme(
    epg: Epg,
    channel_id: str | None,
    reference_time: datetime,
    shift: timedelta = timedelta(),
    name: str | None = None,
) -> Programme | None:
    """The programme a guide's selection cursor points to for a channel at
    a given reference time: whichever is airing then, else the next
    upcoming one, else the last known one -- so a selection is available
    whenever the channel has any schedule at all.

    `reference_time` is an absolute moment (real 'now', or a paged-to
    window_start); `shift` corrects this channel's raw (possibly wrong) feed
    times onto that same absolute timeline before comparing -- see
    EpgDisplay.shift_for. `name` is an optional fallback for EPG channel-id
    resolution when `channel_id` alone doesn't match -- see
    Epg.resolve_channel_id.
    """
    schedule = epg.schedule_for(channel_id, name)
    if not schedule:
        return None
    for programme in schedule:
        if programme.start + shift <= reference_time < programme.stop + shift:
            return programme
    for programme in schedule:
        if programme.start + shift >= reference_time:
            return programme
    return schedule[-1]


def _programmes_in_window(
    epg: Epg, channel: Channel, shift: timedelta, window_start: datetime, window_end: datetime
) -> list[Programme]:
    """The programmes render_program_guide would draw for this channel in
    this window -- factored out so visible_guide_movies can share the exact
    same visibility logic instead of risking the two drifting apart."""
    result = []
    for programme in epg.schedule_for(channel.tvg_id, channel.tvg_name or channel.name):
        corrected_start = programme.start + shift
        corrected_stop = programme.stop + shift
        if corrected_stop <= window_start or corrected_start >= window_end:
            continue
        result.append(programme)
    return result


def render_program_guide(
    channels: list[Channel],
    epg: Epg,
    display: EpgDisplay,
    now: datetime,
    current_channel_url: str | None,
    canvas_width: int,
    canvas_height: int,
    window_start: datetime | None = None,
    window_hours: float = DEFAULT_GUIDE_WINDOW_HOURS,
    max_rows: int = 8,
    selected_channel_url: str | None = None,
    selected_channel_name: str | None = None,
    favorites: set[str] | None = None,
    scheduled: set[tuple[str, datetime]] | None = None,
) -> Image.Image | None:
    """Render a classic set-top-box style program guide: channels down the
    left, a timeline across the top, programme blocks sized by duration, and
    a live 'now' marker line (only drawn if `now` actually falls within the
    displayed window). Returns None only if `channels` is empty -- if none of
    them has an EPG schedule, the channel list itself is still shown (with
    blank timelines) so the guide remains usable for switching channels
    (see visible_guide_channels).

    `favorites` is a set of favorited channel display names (see
    tvdinner.favorites) -- a small heart marker is drawn next to a row's
    name if it's a member.

    `scheduled` is a set of (channel_url, programme.start) pairs (see
    tvdinner.schedule.ScheduledRecording -- start is the raw, unshifted
    feed time, matching what's stored there) -- a small red "R" badge is
    drawn on a programme block if it's a member, so a scheduled recording
    is visible at a glance without opening its details popup.

    `window_start` lets a caller page the timeline forward/back (e.g. via
    arrow keys); it defaults to `now` rounded down to the nearest half hour.

    `selected_channel_url` draws a focus border around that row's in-view
    programme (see guide_reference_time/selected_guide_programme), so a
    caller can let the user move a selection cursor and act on it (e.g.
    Enter to show full details).

    Rows are matched by URL, not tvg_id -- a real playlist can have several
    distinct channels sharing one tvg_id for EPG mapping (quality tiers,
    backup servers), and tvg_id alone can't tell those rows apart.

    The row window is centered on `selected_channel_url` if given, else
    `current_channel_url` (the channel being watched), rather than showing
    every channel, since a real playlist can have thousands of entries --
    most without EPG data at all. Centering on the selection (once one
    exists) rather than always on the playing channel is what lets the
    window scroll/page as a caller moves the selection cursor past the
    currently visible rows.

    `selected_channel_name`, when given alongside `selected_channel_url`,
    disambiguates the rare real playlist where two distinct channels
    share the exact same stream URL (confirmed live: an SD/HD pair) --
    matched together rather than by URL alone, same reasoning as
    visible_guide_channels' own docstring.
    """
    visible = visible_guide_channels(
        channels, epg, selected_channel_url or current_channel_url, max_rows, selected_channel_name
    )
    if not visible:
        return None
    row_count = len(visible)

    def _is_selected_row(row: Channel) -> bool:
        return row.url == selected_channel_url and (selected_channel_name is None or row.name == selected_channel_name)

    # Full window width, minus a small edge gap (matching render_epg_overlay's
    # near-edge-to-edge treatment), rather than a fraction like 0.70 that left
    # a lot of unused space either side.
    side_gap = max(16, round(canvas_width * 0.02))
    panel_width = max(400, canvas_width - 2 * side_gap)

    # Compact, fixed-height rows (a consistent list-item size, like a real
    # STB guide), not `(a fixed panel height) / row_count` -- which would
    # otherwise stretch rows taller whenever fewer than max_rows channels
    # have EPG data. The panel's height instead follows from how many rows
    # are actually shown.
    row_height = round(canvas_height * 0.075)
    header_height = round(canvas_height * 0.07)
    panel_height = header_height + row_count * row_height
    margin = max(16, round(panel_height * 0.02))

    channel_col_width = round(panel_width * 0.22)
    grid_width = panel_width - channel_col_width

    if window_start is None:
        window_start = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 30)
    window_end = window_start + timedelta(hours=window_hours)
    window_seconds = (window_end - window_start).total_seconds()

    def x_for(moment: datetime) -> float:
        clamped = max(window_start, min(window_end, moment))
        return channel_col_width + (clamped - window_start).total_seconds() / window_seconds * grid_width

    # Anchored to canvas_width, the same reference render_epg_overlay's fonts
    # use, rather than row_height -- which would otherwise grow unboundedly
    # whenever few channels have EPG data (e.g. only 6 of 6 shown instead of
    # a full page of 8). row/header height are only a safety ceiling for the
    # opposite extreme (many rows, very little space each).
    header_title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    time_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0085, header_height * 0.34)))
    name_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0105, row_height * 0.34)))
    group_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0075, row_height * 0.22)))
    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.0105, row_height * 0.34)))
    recording_badge_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.008, row_height * 0.26)))
    recording_badge_radius = round(row_height * 0.16)
    rating_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.0075, row_height * 0.24)))
    hd_badge_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.006, row_height * 0.2)))

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = panel_height * 0.025
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    draw.text(
        (logo_margin + logo_size + logo_margin, header_height * 0.28),
        "Program Guide",
        font=header_title_font,
        fill=_WHITE,
    )

    tick = window_start
    while tick <= window_end:
        x = x_for(tick)
        draw.line((x, header_height * 0.55, x, header_height), fill=_ROW_DIVIDER, width=1)
        # No channel_id: these ticks label the shared absolute timeline the
        # grid is built on, not any one channel's (possibly shifted) view of it.
        draw.text((x + 4, header_height * 0.15), display.to_local(tick).strftime("%H:%M"), font=time_font, fill=_MUTED)
        tick += timedelta(minutes=30)

    reference_time = guide_reference_time(now, window_start, window_hours)

    for row_index, channel in enumerate(visible):
        row_top = header_height + row_index * row_height
        row_bottom = row_top + row_height
        row_mid = row_top + row_height / 2

        # Each channel can have its own clock-correction shift, keyed by
        # display name (see EpgDisplay.channel_shifts / load_channel_shifts);
        # programme.start/stop are raw feed times, corrected onto the shared
        # absolute timeline that `now`, `window_start`/`window_end`, and
        # `reference_time` are already on.
        shift = display.shift_for(channel.name)

        selected_programme = (
            selected_guide_programme(
                epg, channel.tvg_id, reference_time, shift=shift, name=channel.tvg_name or channel.name
            )
            if _is_selected_row(channel)
            else None
        )

        if channel.url == current_channel_url:
            # A quiet "currently playing" marker -- just the edge stripe, not
            # a full-row tint, so it doesn't read as a second highlighted row
            # alongside the (much more prominent) selection cursor border.
            stripe_width = max(4, round(panel_width * 0.004))
            draw.rectangle((0, row_top, stripe_width, row_bottom), fill=_ACCENT_COLOR)

        logo_size = round(row_height * 0.68)
        logo_margin = round(row_height * 0.16)
        # Cache-only (see cached_channel_logo/prefetch_channel_logos): a
        # blocking resolve_channel_logo call here, once per visible row,
        # used to be able to cost several real seconds on a guide with
        # never-before-shown channels. Any row not yet resolved just shows
        # the fallback avatar for now; the background fetch's result shows
        # up on the guide's next render (which happens on virtually every
        # keypress while it's open).
        fetched_logo = cached_channel_logo(channel.url)
        logo_image = _logo_tile(fetched_logo, logo_size) if fetched_logo else _fallback_avatar(channel.name, logo_size)
        logo_x = logo_margin
        logo_y = round(row_mid - logo_size / 2)
        panel.alpha_composite(logo_image, (logo_x, logo_y))

        if channel.is_hd:
            # A small corner sticker on the logo tile itself (like a
            # streaming app's thumbnail quality badge), not a third text
            # column -- the channel column is already tight (name, groups,
            # and the favorite heart all compete for it), and this is the
            # same "HD" convention already used to sort these channels to
            # the top of the guide (see Channel.is_hd).
            hd_pad_x = hd_badge_font.size * 0.3
            hd_pad_y = hd_badge_font.size * 0.15
            hd_text_w = draw.textlength("HD", font=hd_badge_font)
            hd_w = hd_text_w + 2 * hd_pad_x
            hd_h = hd_badge_font.size + 2 * hd_pad_y
            hd_x1 = logo_x + logo_size
            hd_y1 = logo_y + logo_size
            draw.rounded_rectangle((hd_x1 - hd_w, hd_y1 - hd_h, hd_x1, hd_y1), radius=hd_h * 0.25, fill=_BADGE_COLOR)
            draw.text((hd_x1 - hd_w + hd_pad_x, hd_y1 - hd_h + hd_pad_y), "HD", font=hd_badge_font, fill=_WHITE)

        name_x = logo_margin + logo_size + logo_margin
        name_max_width = channel_col_width - name_x - 8

        is_favorite = favorites is not None and channel.name in favorites
        heart_width = round(draw.textlength(_FAVORITE_MARK, font=name_font)) if is_favorite else 0

        name_text = _fit_text(draw, channel.name, name_font, name_max_width - heart_width)
        name_bbox = draw.textbbox((0, 0), name_text, font=name_font)
        name_height = name_bbox[3] - name_bbox[1]

        group_text = _fit_text(draw, " · ".join(channel.groups), group_font, name_max_width) if channel.groups else ""
        if group_text:
            group_bbox = draw.textbbox((0, 0), group_text, font=group_font)
            group_height = group_bbox[3] - group_bbox[1]
            line_gap = round(row_height * 0.04)
            block_top = row_mid - (name_height + line_gap + group_height) / 2
            name_y = block_top - name_bbox[1]
            if is_favorite:
                draw.text((name_x, name_y), _FAVORITE_MARK, font=name_font, fill=_FAVORITE_COLOR)
            draw.text((name_x + heart_width, name_y), name_text, font=name_font, fill=_WHITE)
            draw.text(
                (name_x, block_top + name_height + line_gap - group_bbox[1]),
                group_text,
                font=group_font,
                fill=_MUTED,
            )
        else:
            name_y = row_mid - name_height / 2 - name_bbox[1]
            if is_favorite:
                draw.text((name_x, name_y), _FAVORITE_MARK, font=name_font, fill=_FAVORITE_COLOR)
            draw.text((name_x + heart_width, name_y), name_text, font=name_font, fill=_WHITE)

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)

        for programme in _programmes_in_window(epg, channel, shift, window_start, window_end):
            corrected_start = programme.start + shift
            corrected_stop = programme.stop + shift
            x0, x1 = x_for(corrected_start), x_for(corrected_stop)
            block_pad = 2
            # The drawn rectangle is padded in by block_pad on each side, so
            # anything narrower than 2*block_pad would invert (x1 < x0) and
            # crash PIL -- not just "less than 2px", the bar that used to be
            # checked here.
            if x1 - x0 < 2 * block_pad:
                continue

            live = corrected_start <= now < corrected_stop
            draw.rectangle(
                (x0 + block_pad, row_top + block_pad, x1 - block_pad, row_bottom - block_pad),
                fill=_CELL_LIVE_COLOR if live else _CELL_COLOR,
            )
            title = _fit_text(draw, _title_with_year(programme), title_font, (x1 - x0) - 12)
            title_bbox = draw.textbbox((0, 0), title, font=title_font)
            draw.text(
                (x0 + 6, row_mid - (title_bbox[3] - title_bbox[1]) / 2 - title_bbox[1]),
                title,
                font=title_font,
                fill=_WHITE if live else _MUTED,
            )

            if scheduled is not None and (channel.url, programme.start) in scheduled:
                badge_radius = min(recording_badge_radius, (x1 - x0) / 2 - block_pad - 1)
                if badge_radius >= 4:
                    cx = x1 - block_pad - badge_radius - 2
                    cy = row_top + block_pad + badge_radius + 2
                    draw.ellipse(
                        (cx - badge_radius, cy - badge_radius, cx + badge_radius, cy + badge_radius),
                        fill=_RECORDING_BADGE_COLOR,
                    )
                    r_bbox = draw.textbbox((0, 0), "R", font=recording_badge_font)
                    draw.text(
                        (cx - (r_bbox[2] - r_bbox[0]) / 2 - r_bbox[0], cy - (r_bbox[3] - r_bbox[1]) / 2 - r_bbox[1]),
                        "R",
                        font=recording_badge_font,
                        fill=_WHITE,
                    )

            rating = tmdb.rating_for(programme.title, programme.category, programme.year, channel.group_title)
            if rating is not None:
                # Single-line badge, bottom-right (the "R" badge above
                # already owns the top-right corner): star+score always
                # gold, with a smaller muted "TMDB" attribution mark
                # appended on the *same* line rather than stacked above it
                # -- stacking would collide with the title text in a
                # normal-height row, since the title font alone already
                # takes up most of a guide row's height. Two-stage
                # graceful degradation by available width, same spirit as
                # the "R" badge's `if badge_radius >= 4` guard: drop the
                # attribution mark first if it wouldn't fit, then drop the
                # whole badge if even the bare score wouldn't.
                rating_text = f"★ {rating:.1f}"
                rating_bbox = draw.textbbox((0, 0), rating_text, font=rating_font)
                rating_w = rating_bbox[2] - rating_bbox[0]
                row_h = rating_bbox[3] - rating_bbox[1]
                attribution_logo = _tmdb_logo(row_h)
                attribution_w = attribution_logo.width
                attribution_gap = max(2, round(rating_font.size * 0.25))
                badge_pad = max(2, round(rating_font.size * 0.18))
                available = (x1 - x0) - 12

                show_attribution = available >= rating_w + attribution_gap + attribution_w + badge_pad * 2
                if show_attribution or available >= rating_w + badge_pad * 2:
                    content_w = rating_w + (attribution_gap + attribution_w if show_attribution else 0)
                    badge_x1 = x1 - block_pad - 2
                    badge_y1 = row_bottom - block_pad - 2
                    badge_x0 = badge_x1 - content_w - badge_pad * 2
                    badge_y0 = badge_y1 - row_h - badge_pad * 2
                    draw.rounded_rectangle((badge_x0, badge_y0, badge_x1, badge_y1), radius=3, fill=_BADGE_COLOR)
                    draw.text(
                        (badge_x0 + badge_pad - rating_bbox[0], badge_y0 + badge_pad - rating_bbox[1]),
                        rating_text,
                        font=rating_font,
                        fill=_RATING_STAR_COLOR,
                    )
                    if show_attribution:
                        panel.alpha_composite(
                            attribution_logo, (round(badge_x0 + badge_pad + rating_w + attribution_gap), round(badge_y0 + badge_pad))
                        )

            if programme is selected_programme:
                draw.rectangle(
                    (x0 + block_pad, row_top + block_pad, x1 - block_pad, row_bottom - block_pad),
                    outline=_SELECTION_BORDER_COLOR,
                    width=max(2, round(row_height * 0.035)),
                )

        if _is_selected_row(channel) and selected_programme is None:
            # This channel has no schedule at all to draw a programme block
            # (and therefore a border) around -- e.g. a playlist with no EPG
            # data whatsoever, where the guide falls back to a plain channel
            # list (see visible_guide_channels). Outline the whole row
            # instead, so the selection cursor is still visible when moved.
            border_width = max(2, round(row_height * 0.035))
            draw.rectangle(
                (
                    border_width // 2,
                    row_top + border_width // 2,
                    panel_width - border_width // 2,
                    row_bottom - border_width // 2,
                ),
                outline=_SELECTION_BORDER_COLOR,
                width=border_width,
            )

    if window_start <= now <= window_end:
        now_x = x_for(now)
        draw.line((now_x, header_height, now_x, panel_height), fill=_ACCENT_COLOR, width=3)

    canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + panel_width - 1, margin + panel_height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=panel_height * 0.015)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def visible_guide_movies(
    channels: list[Channel],
    epg: Epg,
    display: EpgDisplay,
    now: datetime,
    window_start: datetime | None = None,
    window_hours: float = DEFAULT_GUIDE_WINDOW_HOURS,
    max_rows: int = 8,
    current_channel_url: str | None = None,
    selected_channel_url: str | None = None,
) -> set[tuple[str, str | None]]:
    """The (title, year) keys of every movie programme render_program_guide
    would currently draw for these same arguments -- used by cli.py to
    decide what to background-fetch a TMDB rating for (see tvdinner.tmdb.
    prefetch_ratings). Shares visible_guide_channels/_programmes_in_window
    with render_program_guide's own draw loop so the two can never disagree
    about what "visible" means. Never does any I/O itself."""
    visible = visible_guide_channels(channels, epg, selected_channel_url or current_channel_url, max_rows)
    if not visible:
        return set()

    if window_start is None:
        window_start = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 30)
    window_end = window_start + timedelta(hours=window_hours)

    movies: set[tuple[str, str | None]] = set()
    for channel in visible:
        shift = display.shift_for(channel.name)
        for programme in _programmes_in_window(epg, channel, shift, window_start, window_end):
            if tmdb.is_movie_category(programme.category, channel.group_title):
                movies.add((programme.title, programme.year))
    return movies


def render_programme_details(
    channel: Channel,
    programme: Programme,
    display: EpgDisplay,
    canvas_width: int,
    canvas_height: int,
    logo: Image.Image | None = None,
) -> Image.Image:
    """A modal popup showing everything known about a single programme:
    channel, full title, time range, category, poster art (if the source
    data has any -- see render_epg_overlay), and the complete (generously
    wrapped, not aggressively truncated like the small banner's)
    description. Content-driven height, same two-pass approach as
    render_epg_overlay.
    """
    width = max(480, min(round(canvas_width * 0.7), canvas_width - 80))
    nominal_height = max(160, round(canvas_width * 0.15))
    margin = round(nominal_height * 0.08)
    padding = round(nominal_height * 0.12)
    logo_size = round(nominal_height * 0.5)
    text_x = padding * 2 + logo_size

    # Reserved off nominal_height (not the final, content-driven `height`
    # below) to avoid a circular dependency -- see render_epg_overlay.
    poster_image = fetch_image(programme.poster_url) if programme.poster_url else None
    poster_width = poster_height = 0
    poster_reserved_width = 0
    if poster_image is not None:
        poster_height = round(nominal_height * 1.3)
        poster_width = round(poster_height * 2 / 3)  # classic movie poster aspect ratio
        poster_reserved_width = poster_width + padding

    text_width = width - padding - text_x - poster_reserved_width

    name_font = _font("Inter-Regular.ttf", round(nominal_height * 0.1))
    title_font = _font("Inter-Bold.ttf", round(nominal_height * 0.155))
    meta_font = _font("Inter-Regular.ttf", round(nominal_height * 0.095))
    body_font = _font("Inter-Regular.ttf", round(nominal_height * 0.09))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    name_text = _fit_text(measure, channel.name, name_font, text_width)
    # Same cache-only TMDB fallback as _render_epg_hero/render_epg_overlay
    # (see _title_with_year's own docstring) -- cli.py's
    # show_selected_details kicks off tmdb.prefetch_release_year in the
    # background when this popup opens, same as prefetch_director below.
    fallback_year = tmdb.release_year_for(programme.title, programme.category, programme.year, channel.group_title)
    title_lines = _wrap_text(measure, _title_with_year(programme, fallback_year), title_font, text_width, 3)
    # XMLTV feeds can carry several <category> tags joined into one string
    # (see epg.parse_xmltv) -- long enough on some feeds (5+ genres) to run
    # past this popup's fixed width without truncating like this.
    category_text = (
        _fit_text(measure, _strip_unsupported_glyphs(programme.category, meta_font), meta_font, text_width)
        if programme.category
        else None
    )

    start_local = display.to_local(programme.start, channel_name=channel.name)
    stop_local = display.to_local(programme.stop, channel_name=channel.name)
    time_text = f"{start_local.strftime('%a %d %b, %H:%M')} – {stop_local.strftime('%H:%M')}"

    description_lines = (
        _wrap_text(measure, programme.description, body_font, text_width, _MAX_DETAILS_DESCRIPTION_LINES)
        if programme.description
        else []
    )

    # The feed's own <credits><director> (see epg.parse_xmltv), when it has
    # one, is a free, instant, exactly-matched source -- strictly better
    # than TMDB's fuzzy title/year search, so it's tried first. Falling
    # back to the cache-only TMDB read, same as the rating lookup below:
    # cli.py's show_selected_details kicks off tmdb.prefetch_director in
    # the background when this popup opens, so (unlike the grid's own
    # bulk-prefetched ratings) the very first view of a given movie often
    # shows no director yet from that path; a repeat view picks it up once
    # fetched.
    director = programme.director or tmdb.director_for(programme.title, programme.category, programme.year, channel.group_title)
    director_lines = _wrap_text(measure, f"Directed by {director}", meta_font, text_width, 2) if director else []

    # Right-aligned against time_text's own line (below) rather than a new
    # line of its own -- reads as part of the existing metadata row instead
    # of a bolted-on element. No narrow-width cutoff needed here, unlike the
    # guide grid's cell badge -- this popup is always wide enough.
    rating = tmdb.rating_for(programme.title, programme.category, programme.year, channel.group_title)
    rating_score_text = f"★ {rating:.1f}" if rating is not None else None
    if rating_score_text is not None:
        rating_bbox = measure.textbbox((0, 0), rating_score_text, font=meta_font)
        attribution_logo = _tmdb_logo(rating_bbox[3] - rating_bbox[1])
        rating_gap = round(nominal_height * 0.03)

    def layout(draw: ImageDraw.ImageDraw | None) -> float:
        y = padding * 0.6
        if draw:
            draw.text((text_x, y), name_text, font=name_font, fill=_MUTED)
        y += nominal_height * 0.16

        for line in title_lines:
            if draw:
                draw.text((text_x, y), line, font=title_font, fill=_WHITE)
            y += nominal_height * 0.19

        if draw:
            draw.text((text_x, y), time_text, font=meta_font, fill=_MUTED)
            if rating_score_text is not None:
                attribution_x = text_x + text_width - attribution_logo.width
                panel.alpha_composite(attribution_logo, (round(attribution_x), round(y)))
                score_x = attribution_x - rating_gap - (rating_bbox[2] - rating_bbox[0]) - rating_bbox[0]
                draw.text((score_x, y - rating_bbox[1]), rating_score_text, font=meta_font, fill=_RATING_STAR_COLOR)
        y += nominal_height * 0.16

        if category_text:
            if draw:
                draw.text((text_x, y), category_text, font=meta_font, fill=_ACCENT_COLOR)
            y += nominal_height * 0.16

        for line in director_lines:
            if draw:
                draw.text((text_x, y), line, font=meta_font, fill=_MUTED)
            y += nominal_height * 0.12

        if description_lines:
            y += nominal_height * 0.03
            for line in description_lines:
                if draw:
                    draw.text((text_x, y), line, font=body_font, fill=_MUTED)
                y += nominal_height * 0.12

        return y

    content_bottom = layout(None)
    height = max(nominal_height, round(content_bottom + padding * 0.6))

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height * 0.06, fill=_PANEL_COLOR)
    accent_width = max(6, round(width * 0.008))
    panel_draw.rounded_rectangle((0, 0, accent_width, height - 1), radius=height * 0.02, fill=_ACCENT_COLOR)

    logo_image = _logo_tile(logo, logo_size) if logo else _fallback_avatar(channel.name, logo_size)
    panel.alpha_composite(logo_image, (padding, padding))

    if poster_image is not None:
        fitted_poster = _fit_within_box(poster_image, poster_width, poster_height)
        poster_x = width - padding - poster_width
        poster_y = round((height - poster_height) / 2)
        panel.alpha_composite(fitted_poster, (poster_x, poster_y))

    layout(panel_draw)

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), radius=height * 0.06, fill=(0, 0, 0, 190)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.04)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def render_guide_filter_prompt(text: str, canvas_width: int, canvas_height: int, label: str = "Filter channels") -> Image.Image:
    """A small text-entry dialog -- overlaid on the program guide for
    typing a channel-name filter by default (bound to 'f', confirmed with
    ENTER, cancelled with ESC; see cli.py's guide filter-input
    keybinding), or reused as-is by cli.py's Plex library search prompt
    (bound to '/') with `label="Search Plex library"`. `text` is whatever
    has been typed so far, shown with a trailing cursor.
    """
    width = min(760, round(canvas_width * 0.42))
    height = round(canvas_height * 0.16)
    margin = round(height * 0.3)

    label_font = _font("Inter-Regular.ttf", round(height * 0.16))
    text_font = _font("Inter-Bold.ttf", round(height * 0.22))
    hint_font = _font("Inter-Regular.ttf", round(height * 0.13))

    padding = round(width * 0.05)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=height * 0.12,
        fill=_PANEL_COLOR,
        outline=_ACCENT_COLOR,
        width=max(2, round(height * 0.02)),
    )

    panel_draw.text((padding, padding * 0.5), label, font=label_font, fill=_MUTED)

    shown = _fit_text(panel_draw, f"{text}|", text_font, width - 2 * padding)
    panel_draw.text((padding, height * 0.4), shown, font=text_font, fill=_WHITE)

    panel_draw.text((padding, height * 0.74), "Enter to apply  ·  Esc to cancel", font=hint_font, fill=_MUTED)

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), radius=height * 0.12, fill=(0, 0, 0, 170)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.05)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def render_plex_item_menu(item_title: str, entries: list[str], selected_index: int, canvas_width: int, canvas_height: int) -> Image.Image:
    """The Plex browser's item context menu (hold ENTER on a movie/show/
    episode row -- see cli.py's open_plex_item_menu), offering a handful
    of fixed actions (e.g. "Play from Start", "Mark as Watched") against
    whichever item the menu was opened on. A short, fixed-size popup, not
    a browsable list -- modeled on render_guide_filter_prompt's compact
    centered-dialog shape (small rounded panel, accent outline, drop
    shadow) rather than render_cast_picker's full-width scrolling one,
    since `entries` is always just 2-3 items and never scrolls. The
    selected entry gets a filled accent-colored bar instead of the
    browser rows' outline-only selection border -- with no thumbnail or
    trailing detail sharing the row, an outline alone read as too subtle
    at this size when tuning this by eye. Always returns an image, never
    None, same as render_cast_picker (an empty `entries` list should
    never actually reach here -- see open_plex_item_menu's own kind
    check)."""
    width = min(560, round(canvas_width * 0.3))
    title_height = round(canvas_height * 0.05)
    entry_row_height = round(canvas_height * 0.06)
    height = title_height + len(entries) * entry_row_height
    margin = round(height * 0.15)

    title_font = _font("Inter-Bold.ttf", round(title_height * 0.4))
    entry_font = _font("Inter-Regular.ttf", round(entry_row_height * 0.34))

    padding = round(width * 0.06)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    corner_radius = height * 0.06
    panel_draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=corner_radius,
        fill=_PANEL_COLOR,
        outline=_ACCENT_COLOR,
        width=max(2, round(height * 0.01)),
    )

    title_text = _fit_text(panel_draw, item_title, title_font, width - 2 * padding)
    title_bbox = panel_draw.textbbox((0, 0), title_text, font=title_font)
    panel_draw.text(
        (padding, title_height / 2 - (title_bbox[3] - title_bbox[1]) / 2 - title_bbox[1]),
        title_text,
        font=title_font,
        fill=_MUTED,
    )

    y = title_height
    for index, entry in enumerate(entries):
        row_top = y
        row_bottom = row_top + entry_row_height
        row_mid = row_top + entry_row_height / 2
        is_selected = index == selected_index

        if is_selected:
            # Rounded at the very first entry row (a deliberately soft
            # edge right below the title, not a real panel-corner
            # alignment -- the title bar sits above it) and at the very
            # last one (which *does* align with the panel's own rounded
            # bottom corners, so it has to match that same radius to look
            # flush rather than clipped); square in between.
            bar_radius = corner_radius if row_top == title_height or row_bottom == height else 0
            panel_draw.rounded_rectangle((0, row_top, width - 1, row_bottom - 1), radius=bar_radius, fill=_ACCENT_COLOR)

        entry_text = _fit_text(panel_draw, entry, entry_font, width - 2 * padding)
        entry_bbox = panel_draw.textbbox((0, 0), entry_text, font=entry_font)
        panel_draw.text(
            (padding, row_mid - (entry_bbox[3] - entry_bbox[1]) / 2 - entry_bbox[1]),
            entry_text,
            font=entry_font,
            fill=_WHITE,
        )
        y = row_bottom

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), radius=corner_radius, fill=(0, 0, 0, 170)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.05)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def _format_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # unreachable, but keeps type checkers happy


def _format_recordings_date(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return day.strftime("%A %d %B %Y")


def visible_recordings(recordings: list[RecordingFile], selected_path: Path | None, max_rows: int = 8) -> list[RecordingFile]:
    """A windowed slice of `recordings` (already newest first -- see
    tvdinner.player.list_recordings) containing at most `max_rows` entries,
    scrolled to keep the one at `selected_path` in view -- mirrors
    visible_guide_channels' windowing so a long recordings list pages the
    same way the channel list does."""
    if len(recordings) <= max_rows:
        return recordings

    index = next((i for i, r in enumerate(recordings) if r.path == selected_path), 0)
    half = max_rows // 2
    start = max(0, min(index - half, len(recordings) - max_rows))
    return recordings[start : start + max_rows]


def render_recordings_browser(
    recordings: list[RecordingFile],
    selected_path: Path | None,
    canvas_width: int,
    canvas_height: int,
    max_rows: int = 8,
) -> Image.Image | None:
    """A date-grouped list of previously saved recordings (see the 'w'
    keybinding in cli.py), newest first -- a date header ("Today",
    "Yesterday", or the full date) above each day's entries, with a
    selection border on the row at `selected_path` so a caller can move a
    cursor and act on it (e.g. Enter to play). Returns None if `recordings`
    is empty; the caller is expected not to open this browser at all in
    that case (see cli.py's toggle_recordings_browser).

    Only entries within the windowed slice (see visible_recordings) get a
    date header -- if scrolling lands mid-day, that day's header is simply
    repeated at the top of the window, same as most real recordings UIs.
    """
    if not recordings:
        return None

    window = visible_recordings(recordings, selected_path, max_rows)

    today = datetime.now().date()
    rows: list[tuple[str, date] | tuple[str, RecordingFile]] = []
    last_date: date | None = None
    for recording in window:
        day = recording.recorded_at.date()
        if day != last_date:
            rows.append(("header", day))
            last_date = day
        rows.append(("entry", recording))

    side_gap = max(16, round(canvas_width * 0.02))
    panel_width = max(400, canvas_width - 2 * side_gap)

    header_height = round(canvas_height * 0.07)
    entry_row_height = round(canvas_height * 0.075)
    date_row_height = round(canvas_height * 0.045)

    panel_height = header_height + sum(date_row_height if kind == "header" else entry_row_height for kind, _ in rows)
    margin = max(16, round(panel_height * 0.02))

    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    date_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.009, date_row_height * 0.5)))
    label_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))
    meta_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.008, entry_row_height * 0.24)))

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = panel_height * 0.025
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    draw.text(
        (logo_margin + logo_size + logo_margin, header_height * 0.28), "Recordings", font=title_font, fill=_WHITE
    )

    padding = round(panel_width * 0.015)
    y = header_height
    for kind, item in rows:
        if kind == "header":
            row_bottom = y + date_row_height
            draw.text(
                (padding, y + (date_row_height - date_font.size) / 2),
                _format_recordings_date(item, today),
                font=date_font,
                fill=_ACCENT_COLOR,
            )
            draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
            y = row_bottom
            continue

        recording: RecordingFile = item
        row_top = y
        row_bottom = row_top + entry_row_height
        row_mid = row_top + entry_row_height / 2

        meta_text = f"{recording.recorded_at.strftime('%H:%M')} · {_format_size(recording.size_bytes)}"
        meta_width = draw.textlength(meta_text, font=meta_font)
        label_max_width = panel_width - 2 * padding - meta_width - padding

        label_text = _fit_text(draw, recording.label, label_font, label_max_width)
        label_bbox = draw.textbbox((0, 0), label_text, font=label_font)
        draw.text(
            (padding, row_mid - (label_bbox[3] - label_bbox[1]) / 2 - label_bbox[1]),
            label_text,
            font=label_font,
            fill=_WHITE,
        )

        meta_bbox = draw.textbbox((0, 0), meta_text, font=meta_font)
        draw.text(
            (panel_width - padding - meta_width, row_mid - (meta_bbox[3] - meta_bbox[1]) / 2 - meta_bbox[1]),
            meta_text,
            font=meta_font,
            fill=_MUTED,
        )

        if recording.path == selected_path:
            border_width = max(2, round(entry_row_height * 0.035))
            draw.rectangle(
                (
                    border_width // 2,
                    row_top + border_width // 2,
                    panel_width - border_width // 2,
                    row_bottom - border_width // 2,
                ),
                outline=_SELECTION_BORDER_COLOR,
                width=border_width,
            )

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
        y = row_bottom

    canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + panel_width - 1, margin + panel_height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=panel_height * 0.015)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


_HISTORY_KIND_LABELS: dict[str, str] = {"channel": "Channel", "vod": "Movie", "recording": "Recording"}


def _format_history_duration(seconds: float) -> str:
    total_seconds = round(seconds)
    if total_seconds < 60:
        return f"{total_seconds}s"
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def visible_history_entries(entries: list[HistoryEntry], selected_index: int, max_rows: int = 6) -> list[HistoryEntry]:
    """A windowed slice of `entries` (already newest first -- see
    cli.py's open_history_browser) containing at most `max_rows`,
    scrolled to keep `selected_index` in view -- mirrors
    visible_vod_items' index-based windowing (reusing the same
    _vod_window_start, despite the name: it's not actually VOD-specific)."""
    start = _vod_window_start(len(entries), selected_index, max_rows)
    return entries[start : start + max_rows]


def render_history_browser(
    entries: list[HistoryEntry],
    selected_index: int,
    canvas_width: int,
    canvas_height: int,
    max_rows: int = 6,
) -> Image.Image | None:
    """A date-grouped list of previously watched channels/VOD items/
    recordings (see the 'x' keybinding in cli.py), newest first -- a
    date header ("Today", "Yesterday", or the full date) above each
    day's entries, mirroring render_recordings_browser's own date
    grouping exactly (including reusing _format_recordings_date), plus a
    thumbnail per row (a VOD's poster, a channel's logo, a frame
    captured from a recording's own video -- see
    recording_thumbnail_url/_recording_thumbnail -- or a plain
    placeholder while that resolves or if it fails/the file's since
    been deleted) and a selection border on the row at `selected_index`.
    Returns None if `entries` is empty; the caller is expected not to
    open this browser at all in that case.

    Thumbnails are read via cached_image, never fetched here -- see
    cli.py's toggle_history_browser, which calls prefetch_images before
    the first render, so a cold cache draws a placeholder rather than
    blocking this render on network fetches.

    Only entries within the windowed slice (see visible_history_entries)
    get a date header -- if scrolling lands mid-day, that day's header
    is simply repeated at the top of the window, same as
    render_recordings_browser."""
    if not entries:
        return None

    window_start = _vod_window_start(len(entries), selected_index, max_rows)
    window = entries[window_start : window_start + max_rows]

    today = datetime.now().astimezone().date()
    rows: list[tuple[str, date] | tuple[str, int, HistoryEntry]] = []
    last_date: date | None = None
    for offset, entry in enumerate(window):
        day = entry.started_at.astimezone().date()
        if day != last_date:
            rows.append(("header", day))
            last_date = day
        rows.append(("entry", window_start + offset, entry))

    side_gap = max(16, round(canvas_width * 0.02))
    panel_width = max(400, canvas_width - 2 * side_gap)

    header_height = round(canvas_height * 0.07)
    # Taller than render_recordings_browser's entry rows -- a thumbnail
    # plus two lines of text (title, then kind/time/year/rating) needs
    # more vertical room than that browser's single text line.
    entry_row_height = round(canvas_height * 0.095)
    date_row_height = round(canvas_height * 0.045)

    panel_height = header_height + sum(
        date_row_height if kind == "header" else entry_row_height for kind, *_ in rows
    )
    margin = max(16, round(panel_height * 0.02))

    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    date_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.009, date_row_height * 0.5)))
    label_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.22)))
    meta_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0085, entry_row_height * 0.18)))

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = panel_height * 0.025
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    draw.text((logo_margin + logo_size + logo_margin, header_height * 0.28), "History", font=title_font, fill=_WHITE)

    padding = round(panel_width * 0.015)
    thumb_margin = round(entry_row_height * 0.12)
    thumb_size = entry_row_height - 2 * thumb_margin
    text_x = padding + thumb_size + padding

    y = header_height
    for row in rows:
        if row[0] == "header":
            _, day = row
            row_bottom = y + date_row_height
            draw.text(
                (padding, y + (date_row_height - date_font.size) / 2),
                _format_recordings_date(day, today),
                font=date_font,
                fill=_ACCENT_COLOR,
            )
            draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
            y = row_bottom
            continue

        _, index, entry = row
        row_top = y
        row_bottom = row_top + entry_row_height

        thumb = cached_image(entry.image_url)
        thumb_pos = (padding, row_top + thumb_margin)
        if thumb is not None:
            panel.alpha_composite(ImageOps.fit(thumb, (thumb_size, thumb_size), method=Image.LANCZOS), thumb_pos)
        else:
            draw.rounded_rectangle(
                (thumb_pos[0], thumb_pos[1], thumb_pos[0] + thumb_size, thumb_pos[1] + thumb_size),
                radius=thumb_size * 0.12,
                fill=_GRID_HEADER_COLOR,
            )

        duration_text = _format_history_duration(entry.duration_seconds)
        duration_width = draw.textlength(duration_text, font=meta_font)
        label_max_width = panel_width - text_x - padding - duration_width - padding

        label_text = _fit_text(draw, entry.title, label_font, label_max_width)
        label_top = row_top + thumb_margin
        draw.text((text_x, label_top), label_text, font=label_font, fill=_WHITE)
        draw.text(
            (panel_width - padding - duration_width, label_top + (label_font.size - meta_font.size) / 2),
            duration_text,
            font=meta_font,
            fill=_MUTED,
        )

        meta_parts = [_HISTORY_KIND_LABELS.get(entry.kind, entry.kind), entry.started_at.astimezone().strftime("%H:%M")]
        if entry.kind == "channel":
            # Omitted when it's identical to the title -- happens when
            # no EPG programme was found at record time, and title fell
            # back to the channel's own name (see cli.py's
            # _end_current_history_entry), so showing it twice would be
            # redundant ("Channel · BBC One · BBC One · 20:00").
            if entry.channel_name and entry.channel_name != entry.title:
                meta_parts.append(entry.channel_name)
        if entry.kind == "vod":
            if entry.year:
                meta_parts.append(entry.year)
            if entry.rating:
                meta_parts.append(f"★ {entry.rating}")
            if entry.director:
                meta_parts.append(entry.director)
        meta_text = _fit_text(draw, " · ".join(meta_parts), meta_font, panel_width - text_x - padding)
        meta_top = label_top + label_font.size + round(entry_row_height * 0.06)
        draw.text((text_x, meta_top), meta_text, font=meta_font, fill=_MUTED)

        if index == selected_index:
            border_width = max(2, round(entry_row_height * 0.035))
            draw.rectangle(
                (
                    border_width // 2,
                    row_top + border_width // 2,
                    panel_width - border_width // 2,
                    row_bottom - border_width // 2,
                ),
                outline=_SELECTION_BORDER_COLOR,
                width=border_width,
            )

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
        y = row_bottom

    canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + panel_width - 1, margin + panel_height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=panel_height * 0.015)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def _vod_window_start(total: int, selected_index: int, max_rows: int) -> int:
    if total <= max_rows:
        return 0
    half = max_rows // 2
    return max(0, min(selected_index - half, total - max_rows))


def visible_vod_items(items: list[VodItem], selected_index: int, max_rows: int = 8) -> list[VodItem]:
    """A windowed slice of `items` containing at most `max_rows` entries,
    scrolled to keep `selected_index` in view -- mirrors
    visible_recordings' windowing so a long VOD list pages the same way the
    recordings list does."""
    start = _vod_window_start(len(items), selected_index, max_rows)
    return items[start : start + max_rows]


def jump_to_letter_index(titles: list[str], current_index: int, letter: str) -> int | None:
    """Index of the next title -- searching forward from
    current_index + 1, wrapping around -- whose first character
    casefolds to `letter`, or None if `titles` is empty or nothing
    matches. Search starts *after* the current selection (not at it)
    so pressing the same letter repeatedly cycles through every match
    in list order, including wrapping back to the first one, with no
    extra "last letter pressed" state needed by any caller."""
    total = len(titles)
    if total == 0:
        return None
    needle = letter.casefold()
    for offset in range(1, total + 1):
        index = (current_index + offset) % total
        if titles[index][:1].casefold() == needle:
            return index
    return None


def render_vod_browser(
    items: list[VodItem],
    selected_index: int,
    canvas_width: int,
    canvas_height: int,
    max_rows: int = 8,
) -> Image.Image | None:
    """A group-title-grouped list of VOD movies (see the 'm' keybinding in
    cli.py) -- a group header above each group's entries, with a selection
    border on the row at `selected_index` so a caller can move a cursor and
    act on it (e.g. Enter to play). Returns None if `items` is empty; the
    caller is expected not to open this browser at all in that case (see
    cli.py's toggle_vod_browser).

    Only entries within the windowed slice (see visible_vod_items) get a
    group header -- if scrolling lands mid-group, that group's header is
    simply repeated at the top of the window, same as
    render_recordings_browser does for dates."""
    if not items:
        return None

    window_start = _vod_window_start(len(items), selected_index, max_rows)
    window = items[window_start : window_start + max_rows]

    rows: list[tuple[str, str] | tuple[str, int, VodItem]] = []
    last_group: str | None = None
    for offset, item in enumerate(window):
        group = item.group_title or "Movies"
        if group != last_group:
            rows.append(("header", group))
            last_group = group
        rows.append(("entry", window_start + offset, item))

    side_gap = max(16, round(canvas_width * 0.02))
    panel_width = max(400, canvas_width - 2 * side_gap)

    header_height = round(canvas_height * 0.07)
    entry_row_height = round(canvas_height * 0.075)
    group_row_height = round(canvas_height * 0.045)

    panel_height = header_height + sum(
        group_row_height if kind == "header" else entry_row_height for kind, *_ in rows
    )
    margin = max(16, round(panel_height * 0.02))

    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    group_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.009, group_row_height * 0.5)))
    label_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))
    meta_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.008, entry_row_height * 0.24)))

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = panel_height * 0.025
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    draw.text((logo_margin + logo_size + logo_margin, header_height * 0.28), "Movies", font=title_font, fill=_WHITE)

    padding = round(panel_width * 0.015)
    y = header_height
    for row in rows:
        if row[0] == "header":
            _, group = row
            row_bottom = y + group_row_height
            draw.text(
                (padding, y + (group_row_height - group_font.size) / 2),
                group,
                font=group_font,
                fill=_ACCENT_COLOR,
            )
            draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
            y = row_bottom
            continue

        _, index, item = row
        row_top = y
        row_bottom = row_top + entry_row_height
        row_mid = row_top + entry_row_height / 2

        meta_text = " · ".join(part for part in (item.year, item.rating) if part)
        meta_width = draw.textlength(meta_text, font=meta_font) if meta_text else 0
        label_max_width = panel_width - 2 * padding - meta_width - (padding if meta_text else 0)

        label_text = _fit_text(draw, item.title, label_font, label_max_width)
        label_bbox = draw.textbbox((0, 0), label_text, font=label_font)
        draw.text(
            (padding, row_mid - (label_bbox[3] - label_bbox[1]) / 2 - label_bbox[1]),
            label_text,
            font=label_font,
            fill=_WHITE,
        )

        if meta_text:
            meta_bbox = draw.textbbox((0, 0), meta_text, font=meta_font)
            draw.text(
                (panel_width - padding - meta_width, row_mid - (meta_bbox[3] - meta_bbox[1]) / 2 - meta_bbox[1]),
                meta_text,
                font=meta_font,
                fill=_MUTED,
            )

        if index == selected_index:
            border_width = max(2, round(entry_row_height * 0.035))
            draw.rectangle(
                (
                    border_width // 2,
                    row_top + border_width // 2,
                    panel_width - border_width // 2,
                    row_bottom - border_width // 2,
                ),
                outline=_SELECTION_BORDER_COLOR,
                width=border_width,
            )

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
        y = row_bottom

    canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + panel_width - 1, margin + panel_height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=panel_height * 0.015)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


_SERIES_CHEVRON = "›"


def render_series_browser(
    breadcrumb: str,
    nodes: list[SeriesNode],
    selected_index: int,
    canvas_width: int,
    canvas_height: int,
    max_rows: int = 8,
) -> Image.Image | None:
    """The TV series browser (Xtream today; see the 'l' keybinding in
    cli.py) -- one flat, windowed list at a time, `breadcrumb` as the
    panel's header title. cli.py pushes a new breadcrumb/list pair each
    time the user drills into a container row (a category, series, or
    season); ESC/LEFT pops back. A container row (SeriesNode.container)
    shows a trailing accent-colored chevron instead of a subtitle,
    signalling ENTER drills in rather than plays. Each row gets a
    thumbnail (SeriesNode.poster_url, resolved through the same
    cached_image/prefetch_images pipeline as a VOD poster or Plex
    thumbnail -- see cli.py's Series browser render call site), or a
    plain placeholder while that resolves. Returns None if `nodes` is
    empty; the caller is expected not to open this browser at all in
    that case (see cli.py's toggle_series_browser/open_series_browser).

    Deliberately a smaller cousin of render_plex_browser rather than a
    generalization of it: no favorites, no watched badge, no title-logo
    backdrop compositing (Xtream series listings have none of that data --
    retrofitting the Plex version would mean scattering isinstance
    checks through an already-large function for data that doesn't
    exist here). Returned as a bottom-anchored, tightly-cropped panel
    (like render_vod_browser), not a full canvas_width x canvas_height
    backdrop composite (unlike render_plex_browser) -- there's no poster
    to blow up into a full-bleed background without Plex's own
    already-resolved selected-item art."""
    if not nodes:
        return None

    window_start = _plex_window_start(len(nodes), selected_index, max_rows)
    window = nodes[window_start : window_start + max_rows]

    side_gap = max(16, round(canvas_width * 0.02))
    panel_width = max(400, canvas_width - 2 * side_gap)

    header_height = round(canvas_height * 0.07)
    entry_row_height = round(canvas_height * 0.075)

    panel_height = header_height + len(window) * entry_row_height
    margin = max(16, round(panel_height * 0.02))

    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    label_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))
    meta_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.008, entry_row_height * 0.24)))

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = panel_height * 0.025
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    header_text = _fit_text(draw, breadcrumb, title_font, panel_width - 2 * (logo_margin + logo_size + logo_margin))
    draw.text((logo_margin + logo_size + logo_margin, header_height * 0.28), header_text, font=title_font, fill=_WHITE)

    padding = round(panel_width * 0.015)
    thumb_margin = round(entry_row_height * 0.12)
    thumb_size = entry_row_height - 2 * thumb_margin
    text_x = padding + thumb_size + padding
    y = header_height
    for offset, node in enumerate(window):
        index = window_start + offset
        row_top = y
        row_bottom = row_top + entry_row_height
        row_mid = row_top + entry_row_height / 2

        thumb = cached_image(node.poster_url)
        thumb_pos = (padding, row_top + thumb_margin)
        if thumb is not None:
            panel.alpha_composite(ImageOps.fit(thumb, (thumb_size, thumb_size), method=Image.LANCZOS), thumb_pos)
        else:
            draw.rounded_rectangle(
                (thumb_pos[0], thumb_pos[1], thumb_pos[0] + thumb_size, thumb_pos[1] + thumb_size),
                radius=thumb_size * 0.12,
                fill=_GRID_HEADER_COLOR,
            )

        # Right-aligned meta: the subtitle (muted -- a count like "3
        # seasons" / "10 episodes" for a container, "S02E04" for an
        # episode) and, for a container, a trailing accent chevron
        # signalling ENTER drills in. A container keeps its subtitle
        # *and* gets the chevron -- the chevron doesn't replace it.
        chevron = _SERIES_CHEVRON if node.container else ""
        subtitle = node.subtitle or ""
        chevron_width = draw.textlength(chevron, font=meta_font) if chevron else 0
        subtitle_width = draw.textlength(subtitle, font=meta_font) if subtitle else 0
        chevron_gap = padding if chevron and subtitle else 0
        meta_width = subtitle_width + chevron_gap + chevron_width
        label_max_width = panel_width - text_x - padding - meta_width - (padding if meta_width else 0)

        label_text = _fit_text(draw, node.title, label_font, label_max_width)
        label_bbox = draw.textbbox((0, 0), label_text, font=label_font)
        draw.text(
            (text_x, row_mid - (label_bbox[3] - label_bbox[1]) / 2 - label_bbox[1]),
            label_text,
            font=label_font,
            fill=_WHITE,
        )

        right_edge = panel_width - padding
        if chevron:
            chevron_bbox = draw.textbbox((0, 0), chevron, font=meta_font)
            draw.text(
                (right_edge - chevron_width, row_mid - (chevron_bbox[3] - chevron_bbox[1]) / 2 - chevron_bbox[1]),
                chevron,
                font=meta_font,
                fill=_ACCENT_COLOR,
            )
            right_edge -= chevron_width + chevron_gap
        if subtitle:
            subtitle_bbox = draw.textbbox((0, 0), subtitle, font=meta_font)
            draw.text(
                (right_edge - subtitle_width, row_mid - (subtitle_bbox[3] - subtitle_bbox[1]) / 2 - subtitle_bbox[1]),
                subtitle,
                font=meta_font,
                fill=_MUTED,
            )

        if index == selected_index:
            border_width = max(2, round(entry_row_height * 0.035))
            draw.rectangle(
                (
                    border_width // 2,
                    row_top + border_width // 2,
                    panel_width - border_width // 2,
                    row_bottom - border_width // 2,
                ),
                outline=_SELECTION_BORDER_COLOR,
                width=border_width,
            )

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
        y = row_bottom

    canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + panel_width - 1, margin + panel_height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=panel_height * 0.015)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def _plex_window_start(total: int, selected_index: int, max_rows: int) -> int:
    if total <= max_rows:
        return 0
    half = max_rows // 2
    return max(0, min(selected_index - half, total - max_rows))


def visible_plex_nodes(nodes: list[PlexNode], selected_index: int, max_rows: int = 8) -> list[PlexNode]:
    """A windowed slice of `nodes` containing at most `max_rows` entries,
    scrolled to keep `selected_index` in view -- mirrors visible_vod_items'
    windowing."""
    start = _plex_window_start(len(nodes), selected_index, max_rows)
    return nodes[start : start + max_rows]


def visible_series_nodes(nodes: list[SeriesNode], selected_index: int, max_rows: int = 8) -> list[SeriesNode]:
    """A windowed slice of `nodes` containing at most `max_rows` entries,
    scrolled to keep `selected_index` in view -- mirrors visible_plex_nodes'
    windowing (reuses the same, source-agnostic _plex_window_start math)."""
    start = _plex_window_start(len(nodes), selected_index, max_rows)
    return nodes[start : start + max_rows]


_PLEX_CHEVRON = "›"

_PLEX_LIBRARY_KINDS = ("library_movie", "library_show", "continue_watching")

# Kept in sync with cli.py's own _PLEX_FAVORITABLE_KINDS -- a show is
# favorited as a whole, not per-season/episode.
_PLEX_FAVORITABLE_KINDS = ("movie", "show")

# PlexNode.kinds that get their own specific word in the header's
# item-count suffix (see _plex_count_suffix) -- "Season" deliberately
# capitalized, on request, unlike its lowercase siblings here.
_PLEX_COUNT_LABELS = {"movie": "movie", "show": "show", "episode": "episode", "season": "Season"}


def _plex_count_suffix(nodes: list[PlexNode]) -> str:
    """" (12 movies)"/" (4 shows)"/" (9 episodes)"/" (6 Seasons)" for a
    listing whose rows are all the same specific kind, appended to the
    Plex browser's header title (see render_plex_browser/
    render_plex_grid_browser) so a glance at the header says how big the
    current listing is, not just what it's called. No suffix at all for
    the root Plex Libraries/Continue-Watching listing (nodes.kind in
    _PLEX_LIBRARY_KINDS) -- on request, unlike every listing one level
    deeper, which always gets one. A listing that mixes leaf kinds
    (Continue Watching's own on-deck list freely mixes movie and episode
    nodes) has no single specific word that fits, so falls back to the
    generic "item"."""
    if not nodes:
        return ""
    kinds = {node.kind for node in nodes}
    if kinds <= set(_PLEX_LIBRARY_KINDS):
        return ""
    labels = {_PLEX_COUNT_LABELS.get(kind, "item") for kind in kinds}
    singular = next(iter(labels)) if len(labels) == 1 else "item"
    count = len(nodes)
    return f" ({count} {singular if count == 1 else singular + 's'})"


# render_plex_grid_browser's tile grid -- tuned by eye, not derived from
# anything. Kept in sync with cli.py's own _PLEX_GRID_COLUMNS/_PLEX_GRID_ROWS,
# which use these same numbers to size UP/DOWN/PGUP/PGDWN's grid-mode steps.
_PLEX_GRID_COLUMNS = 6
_PLEX_GRID_ROWS = 3


def _draw_plex_watch_badge(draw: ImageDraw.ImageDraw, x: float, y: float, width: float, height: float, node: PlexNode) -> None:
    """Plex's own watched/in-progress status as a corner badge over a
    thumbnail region -- shared by render_plex_browser's (square) list
    thumbnails and render_plex_grid_browser's (2:3 poster) tiles, hence
    taking `width`/`height` separately rather than one square `size`. A
    green checkmark badge in the bottom-right corner if fully watched
    (PlexNode.watched), or a thin progress bar along the bottom edge if
    partially watched (PlexNode.watch_progress) -- never both, see
    PlexNode's own docstring."""
    if node.watched:
        check_size = round(min(width, height) * 0.34)
        check_margin = round(min(width, height) * 0.06)
        check_cx = x + width - check_margin - check_size / 2
        check_cy = y + height - check_margin - check_size / 2
        draw.ellipse(
            (check_cx - check_size / 2, check_cy - check_size / 2, check_cx + check_size / 2, check_cy + check_size / 2),
            fill=_WATCHED_COLOR,
        )
        check_font = _font("Inter-Bold.ttf", round(check_size * 0.8))
        check_bbox = draw.textbbox((0, 0), "✓", font=check_font)
        draw.text(
            (
                check_cx - (check_bbox[2] - check_bbox[0]) / 2 - check_bbox[0],
                check_cy - (check_bbox[3] - check_bbox[1]) / 2 - check_bbox[1],
            ),
            "✓",
            font=check_font,
            fill=_WHITE,
        )
    elif node.watch_progress is not None:
        bar_height = max(2, round(height * 0.05))
        bar_top = y + height - bar_height
        draw.rectangle((x, bar_top, x + width, y + height), fill=(0, 0, 0, 160))
        fill_width = round(width * node.watch_progress)
        if fill_width > 0:
            draw.rectangle((x, bar_top, x + fill_width, y + height), fill=_WATCHED_COLOR)


# How strongly the selected item's poster shows through the panel's own
# dark background (see _draw_plex_backdrop) -- low enough that it reads
# as a tinted backdrop rather than competing with the sharp, fully-opaque
# tile/thumbnail art drawn on top of it.
_PLEX_BACKDROP_ALPHA = 120

# Kept in sync with cli.py's own _GUIDE_BOTTOM_MARGIN -- render_plex_browser/
# render_plex_grid_browser now position their own panel within a full-canvas
# image (see _plex_full_backdrop) instead of returning a tightly-cropped
# panel-only image for the caller to bottom-anchor, so the margin has to be
# applied on this side instead.
_PLEX_OVERLAY_BOTTOM_MARGIN = 40


def _plex_selected_poster(selected_node: PlexNode | None) -> Image.Image | None:
    """The currently selected node's own poster, if it's already resolved
    into the image cache (cached_image is cache-only/non-blocking, same as
    every other thumbnail here) -- the shared source for both
    _draw_plex_backdrop (the in-panel tinted backdrop) and
    _plex_full_backdrop (the full-screen one), so both draw from the exact
    same image and neither repeats the cache lookup.

    An episode's own thumbnail is a screengrab from the show itself --
    busier and more spoiler-y than the poster art everywhere else here,
    and confirmed live to look out of place blown up full-screen. For an
    episode, PlexNode.season_thumb_url (Plex's own `parentThumb` field,
    read straight off that episode's own metadata regardless of listing
    context -- see plex.py's _episode_node) is used instead, so the
    backdrop never goes more detailed than season artwork. This used to
    be threaded through as a separate `parent_node` argument (the
    immediately-enclosing nav frame's own selected node), which worked
    for a season's own episode listing but not Continue Watching's flat
    on-deck one (no season frame in between to walk up to at all) --
    reading it directly off the episode node itself instead fixes that
    for free."""
    if selected_node is None:
        return None
    if selected_node.kind == "episode" and selected_node.season_thumb_url:
        return cached_image(selected_node.season_thumb_url)
    return cached_image(selected_node.thumb_url)


def _draw_plex_backdrop(panel: Image.Image, panel_width: int, panel_height: int, corner_radius: float, poster: Image.Image | None) -> None:
    """Paints `panel`'s own rounded-rectangle background -- shared by
    render_plex_browser and render_plex_grid_browser. Always paints the
    same solid _GRID_PANEL_COLOR fill first (the whole prior look, and
    the fallback whenever there's no poster to show yet), then, if `poster`
    (see _plex_selected_poster) is available, blends a softened, cover-
    cropped blow-up of it on top at _PLEX_BACKDROP_ALPHA -- a Netflix/Plex-
    style tinted hero backdrop. Only ever visible in the gaps around the
    opaque tiles/thumbnails the caller draws on top of this afterward --
    those always win since they're drawn later. Mutates `panel` in place;
    `panel` must already be a fully transparent panel_width x panel_height
    RGBA image."""
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    if poster is None:
        return

    backdrop = _cover_fill(poster, panel_width, panel_height).filter(ImageFilter.GaussianBlur(radius=panel_height * 0.008))
    mask = Image.new("L", (panel_width, panel_height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_PLEX_BACKDROP_ALPHA)
    panel.paste(backdrop.convert("RGB"), (0, 0), mask)


# Four-corner wash for _plex_root_wash -- brightest (a touch of the
# brand's own accent blue mixed in) nearest the top-left corner where the
# logo sits, darkest at the opposite corner, so it reads as a gentle,
# deliberate vignette rather than a flat, dead fill. Kept subtle ("gentler
# color wash" per the user's own request, after the plain solid-black
# fallback read as broken/unbranded) -- nowhere near as saturated as
# _ACCENT_COLOR itself.
_PLEX_ROOT_WASH_CORNERS = ((26, 34, 48, 255), (14, 16, 22, 255), (12, 14, 19, 255), (7, 8, 11, 255))


_plex_root_wash_cache: dict[tuple[int, int], Image.Image] = {}


def _plex_root_wash(canvas_width: int, canvas_height: int) -> Image.Image:
    """_plex_full_backdrop's fallback whenever there's no selected item's
    poster to build a real hero from yet (e.g. the library root, where
    every row is a folder with no thumbnail of its own) -- previously a
    plain transparent canvas, which let mpv's own idle-screen logo (a big
    centered purple play icon) show through underneath and read as
    broken rather than intentional. A four-corner gradient (see
    _PLEX_ROOT_WASH_CORNERS), built the same cheap way _bottom_fade_gradient
    builds its own gradient -- a tiny source image upscaled with smooth
    resampling -- plus tvdinner's own logo mark in the top-left corner,
    much larger than the one in the panel's own header bar, so the app
    still has a clear, deliberate identity on screen even with no poster
    to lean on.

    Cached by (canvas_width, canvas_height) -- fully deterministic given
    just those two numbers, and confirmed live (profiling a real Plex
    browsing session) to cost ~15-20ms per call despite looking cheap,
    recomputed on every single arrow-key move since this is rebuilt from
    scratch on every render_and_show_plex call. Almost always a cache
    hit in practice: the canvas size only actually changes on a window
    resize, not on ordinary navigation. Returns a *copy* of the cached
    image, never the cached object itself -- callers (_plex_full_backdrop,
    when given a title_logo) composite directly onto whatever this
    returns, which would otherwise corrupt the cached entry for every
    later call at the same size."""
    key = (canvas_width, canvas_height)
    cached = _plex_root_wash_cache.get(key)
    if cached is None:
        corners = Image.new("RGBA", (2, 2))
        corners.putdata(_PLEX_ROOT_WASH_CORNERS)
        cached = corners.resize((canvas_width, canvas_height), Image.BILINEAR)

        logo_size = round(canvas_height * 0.14)
        logo_margin = round(canvas_height * 0.04)
        cached.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
        _plex_root_wash_cache[key] = cached
    return cached.copy()


_plex_panel_shadow_cache: dict[tuple[int, int, int, float], Image.Image] = {}


def _plex_panel_shadow(panel_width: int, panel_height: int, margin: int, corner_radius: float) -> Image.Image:
    """The blurred drop-shadow layer behind a Plex browser panel -- shared
    by render_plex_browser and render_plex_grid_browser, which previously
    each built this inline, identically. Shape-only (a solid rounded
    rectangle, blurred), with no dependency on the panel's actual content
    (posters, selection, row count beyond what's already baked into
    panel_height) -- cached by the four numbers that fully determine it.
    Confirmed live (profiling a real Plex browsing session) that this
    GaussianBlur over a near-fullscreen layer cost ~20ms on its own,
    recomputed on every single arrow-key move; panel_width/panel_height
    are almost always unchanged between consecutive renders (same
    window, same row/column count), so this is a cache hit for the
    overwhelming majority of navigation. Safe to return the cached image
    directly rather than a copy, unlike _plex_root_wash -- every caller
    only ever reads from this as an alpha_composite *source*, never
    composites anything onto it afterward."""
    key = (panel_width, panel_height, margin, corner_radius)
    cached = _plex_panel_shadow_cache.get(key)
    if cached is None:
        shadow = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (margin, margin, margin + panel_width - 1, margin + panel_height - 1),
            radius=corner_radius,
            fill=(0, 0, 0, 180),
        )
        cached = shadow.filter(ImageFilter.GaussianBlur(radius=panel_height * 0.015))
        _plex_panel_shadow_cache[key] = cached
    return cached


def _plex_full_backdrop(
    poster: Image.Image | None, canvas_width: int, canvas_height: int, title_logo: Image.Image | None = None
) -> Image.Image:
    """The Plex browser overlay's full-screen background -- the same
    _cover_fill full-bleed technique _render_epg_hero/_render_vod_info_hero
    use behind their own text, built from `poster` (see
    _plex_selected_poster). Blurred first, unlike those two: they
    composite real wide backdrop art at close to its native resolution,
    while this is a much smaller *portrait* poster stretched to cover a
    landscape canvas, which looks blocky at that scale without it.
    Unlike those two, fully opaque rather than blended at
    _HERO_BACKDROP_ALPHA -- they sit over live/paused video that's meant
    to stay visible through them, but this sits over mpv's own plain
    idle-screen logo whenever nothing is playing, which bled through
    visibly at that same partial opacity (confirmed live: most
    noticeable selecting a show, whose poster tends to have more plain/
    dark backgrounds than a movie's own busier art). Falls back to
    _plex_root_wash whenever there's no poster to build one from yet --
    always a full canvas_width x canvas_height opaque image either way,
    unlike before backdrop support existed, when the caller just left
    the canvas transparent. zoom=0.85 (rather than _cover_fill's own
    default tight crop) shows a bit more of the poster instead of
    blowing up a narrow sliver of it across the whole canvas -- less
    upscaling this way also means less blur is needed to hide it
    (confirmed live: the previous, tighter default read as too zoomed in
    and noticeably softer than intended). The blur radius itself
    (0.0015 * canvas_height) was likewise tuned live -- an earlier,
    higher multiplier (0.0045) read as noticeably blurry on a real
    screen rather than just taking the edge off upscale blockiness.
    `title_logo`, when given, is composited top-right via the same
    _composite_title_logo the hero overlays use -- see cli.py's
    render_and_show_plex for how it's resolved (a TMDB lookup keyed off
    the nearest movie/show ancestor in the nav stack, since a season/
    episode listing has no title of its own to search with)."""
    if poster is None:
        canvas = _plex_root_wash(canvas_width, canvas_height)
    else:
        canvas = _cover_fill(poster, canvas_width, canvas_height, zoom=0.85).filter(
            ImageFilter.GaussianBlur(radius=canvas_height * 0.0015)
        )
    if title_logo is not None:
        _composite_title_logo(canvas, title_logo, canvas_width, canvas_height, round(canvas_width * 0.045))
    return canvas


def render_up_next_backdrop(canvas_width: int, canvas_height: int) -> Image.Image:
    """The full-screen background behind the "Up Next" countdown card
    (cli.py's _start_up_next_countdown) -- nothing is actually playing at
    that point (the previous episode just ended, and the next one hasn't
    started yet), so without this mpv's own idle-screen logo would show
    through behind the countdown card. Reuses the exact same wallpaper
    the Plex library browser's root level falls back to (see
    _plex_full_backdrop/_plex_root_wash), for the same "still tvdinner,
    not a broken idle screen" reasoning that fallback was built for, and
    so the transition from browser/playback into this countdown doesn't
    jump to a visibly different background than the rest of a Plex
    session already uses. Public wrapper -- cli.py doesn't import
    underscore-prefixed names from this module (see help_tab_count for
    the same reasoning)."""
    return _plex_full_backdrop(None, canvas_width, canvas_height)


def _plex_grid_window_start(total: int, selected_index: int, columns: int, max_rows: int) -> int:
    """Like _plex_window_start, but scrolls by whole rows (columns items
    at a time) rather than one item at a time, so a grid page always
    starts at a row boundary."""
    per_page = columns * max_rows
    if total <= per_page:
        return 0
    selected_row = selected_index // columns
    half_rows = max_rows // 2
    first_row = max(0, min(selected_row - half_rows, -(-total // columns) - max_rows))
    return first_row * columns


def visible_plex_grid_nodes(nodes: list[PlexNode], selected_index: int, columns: int = _PLEX_GRID_COLUMNS, max_rows: int = _PLEX_GRID_ROWS) -> list[PlexNode]:
    """A windowed slice of `nodes` containing at most `columns * max_rows`
    entries, scrolled by whole rows to keep `selected_index` in view --
    mirrors visible_plex_nodes' windowing, for grid paging instead of
    list paging."""
    start = _plex_grid_window_start(len(nodes), selected_index, columns, max_rows)
    return nodes[start : start + columns * max_rows]


def _draw_folder_icon(draw: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    """A classic Windows-Explorer-style yellow folder glyph -- the
    thumbnail placeholder for a Plex library row, or the synthetic
    "On Deck" row (see _PLEX_LIBRARY_KINDS), neither of which
    ever has a thumb/composite of its own (see render_plex_browser).
    Distinct from the plain placeholder square shown for a movie/show/
    episode row still waiting on its own thumbnail fetch, since these
    rows genuinely have no thumbnail to ever resolve, unlike those."""
    tab_height = size * 0.16
    body_top = y + tab_height
    corner = size * 0.06
    draw.rounded_rectangle(
        (x + size * 0.04, y + tab_height * 0.55, x + size * 0.96, y + size * 0.92),
        radius=corner,
        fill=_FOLDER_BACK_COLOR,
    )
    draw.rounded_rectangle(
        (x + size * 0.08, y, x + size * 0.48, y + tab_height * 1.6), radius=corner * 0.6, fill=_FOLDER_FRONT_COLOR
    )
    draw.rounded_rectangle(
        (x + size * 0.04, body_top, x + size * 0.96, y + size * 0.96),
        radius=corner,
        fill=_FOLDER_FRONT_COLOR,
        outline=_FOLDER_OUTLINE_COLOR,
        width=max(1, round(size * 0.015)),
    )


def render_plex_browser(
    breadcrumb: str,
    nodes: list[PlexNode],
    selected_index: int,
    canvas_width: int,
    canvas_height: int,
    max_rows: int = 8,
    favorites: set[str] | None = None,
    title_logo_url: str | None = None,
) -> Image.Image | None:
    """A Plex library/show/season/episode browser (see the 'l' keybinding
    in cli.py) -- one flat, windowed list at a time, with `breadcrumb` as
    the panel's header title, followed by a context-sensitive item-count
    suffix appended from `nodes` itself -- see _plex_count_suffix. cli.py
    pushes a new breadcrumb/list pair onto its navigation stack each time
    the user drills into a container
    row; ESC pops back. A container row (PlexNode.container -- a library,
    show, or season) shows a trailing accent-colored chevron instead of a
    subtitle, signalling ENTER drills in rather than plays. Each row also
    gets a thumbnail (PlexNode.thumb_url, resolved through the same
    cached_image/prefetch_images pipeline as a VOD poster or channel
    logo -- see cli.py's Plex browser render call site -- or, while that
    resolves, a plain placeholder for a movie/show/episode, or a classic
    yellow folder glyph -- see _draw_folder_icon -- for a library row,
    since a library genuinely never has a thumbnail of its own to wait
    for unless Plex reports one immediately). Returns None if `nodes` is
    empty; the caller is expected not to open this browser at all in
    that case (see cli.py's toggle_plex_browser/open_plex_browser).

    `favorites` is a set of favorited movie/show PlexNode.rating_keys (see
    tvdinner.favorites) -- a small heart marker is drawn next to a
    favorited row's title, same convention as the guide's own favorite
    heart. Only ever set for a "movie" or "show" node (see
    _PLEX_FAVORITABLE_KINDS/cli.py's _PLEX_FAVORITABLE_KINDS) -- a
    library/season/episode row is never favoritable, so its rating_key is
    never checked against this set even if it happened to collide.

    A movie/episode/show/season row also shows Plex's own watched
    status straight from PlexNode.watched/watch_progress (see
    plex.py's _leaf_watch_status/_rollup_watch_status): a green
    checkmark badge in the thumbnail's corner if fully watched, or a
    thin progress bar along its bottom edge if partially watched.
    Never both -- see PlexNode's own docstring.

    `title_logo_url`, when it resolves to an already-cached image (via
    cached_image -- deliberately non-blocking, since this renders on
    every arrow-key press, unlike the hero overlays' occasional
    keypress), is composited into the full-screen backdrop's top-right
    corner -- see _plex_full_backdrop/cli.py's render_and_show_plex.

    The panel's own background is a tinted blow-up of the selected row's
    own poster once it's resolved -- see _draw_plex_backdrop. The returned
    image is always the full canvas_width x canvas_height, not just a
    tightly-cropped panel: the panel is bottom-anchored within it over a
    full-bleed version of that same poster -- see _plex_full_backdrop."""
    if not nodes:
        return None

    window_start = _plex_window_start(len(nodes), selected_index, max_rows)
    window = nodes[window_start : window_start + max_rows]

    side_gap = max(16, round(canvas_width * 0.02))
    panel_width = max(400, canvas_width - 2 * side_gap)

    header_height = round(canvas_height * 0.07)
    entry_row_height = round(canvas_height * 0.075)

    panel_height = header_height + len(window) * entry_row_height
    margin = max(16, round(panel_height * 0.02))

    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    label_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))
    meta_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.008, entry_row_height * 0.24)))

    selected_poster = _plex_selected_poster(nodes[selected_index] if 0 <= selected_index < len(nodes) else None)

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    corner_radius = panel_height * 0.025
    _draw_plex_backdrop(panel, panel_width, panel_height, corner_radius, selected_poster)
    draw = ImageDraw.Draw(panel)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    header_title = breadcrumb + _plex_count_suffix(nodes)
    header_text = _fit_text(draw, header_title, title_font, panel_width - 2 * (logo_margin + logo_size + logo_margin))
    draw.text((logo_margin + logo_size + logo_margin, header_height * 0.28), header_text, font=title_font, fill=_WHITE)

    padding = round(panel_width * 0.015)
    thumb_margin = round(entry_row_height * 0.12)
    thumb_size = entry_row_height - 2 * thumb_margin
    text_x = padding + thumb_size + padding
    y = header_height
    for offset, node in enumerate(window):
        index = window_start + offset
        row_top = y
        row_bottom = row_top + entry_row_height
        row_mid = row_top + entry_row_height / 2

        thumb = cached_image(node.thumb_url)
        thumb_pos = (padding, row_top + thumb_margin)
        if thumb is not None:
            panel.alpha_composite(ImageOps.fit(thumb, (thumb_size, thumb_size), method=Image.LANCZOS), thumb_pos)
        elif node.kind in _PLEX_LIBRARY_KINDS:
            _draw_folder_icon(draw, thumb_pos[0], thumb_pos[1], thumb_size)
        else:
            draw.rounded_rectangle(
                (thumb_pos[0], thumb_pos[1], thumb_pos[0] + thumb_size, thumb_pos[1] + thumb_size),
                radius=thumb_size * 0.12,
                fill=_GRID_HEADER_COLOR,
            )

        _draw_plex_watch_badge(draw, thumb_pos[0], thumb_pos[1], thumb_size, thumb_size, node)

        meta_text = _PLEX_CHEVRON if node.container else (node.subtitle or "")
        meta_width = draw.textlength(meta_text, font=meta_font) if meta_text else 0
        label_max_width = panel_width - text_x - padding - meta_width - (padding if meta_text else 0)

        is_favorite = (
            favorites is not None and node.kind in _PLEX_FAVORITABLE_KINDS and node.rating_key in favorites
        )
        heart_width = round(draw.textlength(_FAVORITE_MARK, font=label_font)) if is_favorite else 0

        label_text = _fit_text(draw, node.title, label_font, label_max_width - heart_width)
        label_bbox = draw.textbbox((0, 0), label_text, font=label_font)
        label_y = row_mid - (label_bbox[3] - label_bbox[1]) / 2 - label_bbox[1]
        if is_favorite:
            draw.text((text_x, label_y), _FAVORITE_MARK, font=label_font, fill=_FAVORITE_COLOR)
        draw.text((text_x + heart_width, label_y), label_text, font=label_font, fill=_WHITE)

        if meta_text:
            meta_bbox = draw.textbbox((0, 0), meta_text, font=meta_font)
            draw.text(
                (panel_width - padding - meta_width, row_mid - (meta_bbox[3] - meta_bbox[1]) / 2 - meta_bbox[1]),
                meta_text,
                font=meta_font,
                fill=_ACCENT_COLOR if node.container else _MUTED,
            )

        if index == selected_index:
            border_width = max(2, round(entry_row_height * 0.035))
            draw.rectangle(
                (
                    border_width // 2,
                    row_top + border_width // 2,
                    panel_width - border_width // 2,
                    row_bottom - border_width // 2,
                ),
                outline=_SELECTION_BORDER_COLOR,
                width=border_width,
            )

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
        y = row_bottom

    panel_canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    panel_canvas.alpha_composite(_plex_panel_shadow(panel_width, panel_height, margin, corner_radius))
    panel_canvas.alpha_composite(panel, (margin, margin))

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    canvas.alpha_composite(_plex_full_backdrop(selected_poster, canvas_width, canvas_height, cached_image(title_logo_url)))
    canvas.alpha_composite(
        panel_canvas,
        ((canvas_width - panel_canvas.width) // 2, max(0, canvas_height - panel_canvas.height - _PLEX_OVERLAY_BOTTOM_MARGIN)),
    )

    return canvas


_plex_grid_tiles_cache: dict[tuple, Image.Image] = {}


def _plex_tile_signature(node: PlexNode, favorites: set[str] | None) -> tuple:
    """Everything about `node` that affects its own tile's drawn pixels
    in render_plex_grid_browser's static tile layer -- not including
    whether it's the currently *selected* tile, which is deliberately
    left out of _plex_grid_tiles_cache's key entirely (see that
    function's own docstring). watched/watch_progress are included
    explicitly rather than trusting rating_key alone: confirmed live
    (cli.py's _mark_plex_item_watched/_mark_plex_item_unwatched) that
    PlexNode is a plain, unfrozen dataclass mutated in place for these
    two fields specifically, so a watched-status toggle changes nothing
    a rating_key-only key would notice."""
    thumb_ready = node.thumb_url is not None and cached_image(node.thumb_url) is not None
    is_favorite = favorites is not None and node.kind in _PLEX_FAVORITABLE_KINDS and node.rating_key in favorites
    return (node.rating_key, node.kind, node.title, node.watched, node.watch_progress, thumb_ready, is_favorite)


def render_plex_grid_browser(
    breadcrumb: str,
    nodes: list[PlexNode],
    selected_index: int,
    canvas_width: int,
    canvas_height: int,
    columns: int = _PLEX_GRID_COLUMNS,
    max_rows: int = _PLEX_GRID_ROWS,
    favorites: set[str] | None = None,
    title_logo_url: str | None = None,
) -> Image.Image | None:
    """The Plex browser's alternate view (see the 'g' keybinding in
    cli.py) -- the same underlying node list render_plex_browser shows as
    a scrolling row list, instead laid out as a `columns` x `max_rows`
    grid of large 2:3 poster tiles, windowed by whole rows (see
    visible_plex_grid_nodes) rather than one row at a time. Same None-
    for-empty-list contract, same favorites/watched-badge treatment
    (_draw_plex_watch_badge, shared with render_plex_browser) as that
    function -- see its own docstring for what favorites/watched/
    watch_progress mean here. Same `breadcrumb` + item-count-suffix
    header title too (_plex_count_suffix). A container tile (a library, show, or
    season) gets a small accent-colored chevron badge in its top-right
    corner instead of list view's trailing chevron column, since there's
    no room for a text column here -- shown once instead, at the right
    edge of the header bar, for whichever node is currently selected
    (same small font/right-alignment/chevron-or-subtitle content as a
    list view row's own trailing detail). Same selected-poster panel
    backdrop and full-canvas-sized return value as render_plex_browser --
    see _draw_plex_backdrop/_plex_full_backdrop. `title_logo_url` is the
    same title-logo passthrough render_plex_browser's own docstring
    describes -- see _plex_full_backdrop."""
    if not nodes:
        return None

    window_start = _plex_grid_window_start(len(nodes), selected_index, columns, max_rows)
    window = nodes[window_start : window_start + columns * max_rows]
    rows_used = max(1, -(-len(window) // columns))  # ceil division

    # Tile size is driven by canvas *height* (fitting exactly `max_rows`
    # rows), not width -- unlike every other panel here, a poster grid's
    # tile count per row is fixed regardless of window width, so sizing
    # from width first (like list view's panel_width does) would make
    # tiles taller as the window widens, eventually overflowing a shorter
    # canvas vertically. Confirmed live: deriving height from width instead
    # produced a 3-row grid taller than a 1080p canvas.
    header_height = round(canvas_height * 0.07)
    outer_margin_budget = canvas_height * 0.06
    tile_gap = round(canvas_height * 0.018)
    title_height = round(canvas_height * 0.035)
    available_height = canvas_height - header_height - outer_margin_budget
    poster_height = max(60.0, (available_height - tile_gap * (max_rows + 1)) / max_rows - title_height)
    tile_width = poster_height / 1.5  # 2:3 poster art, matching Plex's own
    tile_height = poster_height + title_height

    # Safety clamp: shrink tiles further if `columns` of them still
    # wouldn't fit the available width (e.g. an unusually narrow/portrait
    # window) -- height-driven sizing above never overflows vertically,
    # but says nothing about width on its own.
    side_gap = max(16, round(canvas_width * 0.02))
    available_width = max(400, canvas_width - 2 * side_gap)
    grid_width = columns * tile_width + tile_gap * (columns + 1)
    if grid_width > available_width:
        scale = available_width / grid_width
        tile_width *= scale
        poster_height *= scale
        title_height *= scale
        tile_height = poster_height + title_height
        tile_gap = round(tile_gap * scale)
        grid_width = columns * tile_width + tile_gap * (columns + 1)

    panel_width = round(grid_width)
    panel_height = round(header_height + tile_gap + rows_used * (tile_height + tile_gap))
    margin = max(16, round(panel_height * 0.02))

    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    label_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0095, title_height * 0.42)))
    badge_font = _font("Inter-Bold.ttf", round(tile_width * 0.11))
    meta_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.008, header_height * 0.24)))

    selected_node = nodes[selected_index] if 0 <= selected_index < len(nodes) else None
    selected_poster = _plex_selected_poster(selected_node)

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    corner_radius = panel_height * 0.02
    _draw_plex_backdrop(panel, panel_width, panel_height, corner_radius, selected_poster)
    draw = ImageDraw.Draw(panel)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))

    # Same trailing detail render_plex_browser shows at the right edge of
    # the selected row -- a chevron for a container, or its subtitle
    # (year/rating/resolution/duration) for a leaf -- shown once here for
    # the current selection instead of once per row, since a grid tile has
    # no room for its own subtitle text.
    meta_text = None
    if selected_node is not None:
        meta_text = _PLEX_CHEVRON if selected_node.container else (selected_node.subtitle or None)
    meta_width = draw.textlength(meta_text, font=meta_font) if meta_text else 0
    title_max_width = panel_width - 2 * (logo_margin + logo_size + logo_margin) - meta_width - (logo_margin if meta_text else 0)
    header_text = _fit_text(draw, breadcrumb + _plex_count_suffix(nodes), title_font, title_max_width)
    draw.text((logo_margin + logo_size + logo_margin, header_height * 0.28), header_text, font=title_font, fill=_WHITE)
    if meta_text:
        meta_bbox = draw.textbbox((0, 0), meta_text, font=meta_font)
        draw.text(
            (panel_width - logo_margin - meta_width, header_height / 2 - (meta_bbox[3] - meta_bbox[1]) / 2 - meta_bbox[1]),
            meta_text,
            font=meta_font,
            fill=_ACCENT_COLOR if selected_node.container else _MUTED,
        )

    # Split into a cacheable "static" layer (everything but the selection
    # border -- poster/placeholder, watch/favorite badges, container
    # chevron, title label) and an always-freshly-drawn selection border
    # on top of it. Moving the selection within the same visible page --
    # the overwhelmingly common case for arrow-key navigation -- changes
    # none of the static layer's pixels, only the border's position;
    # confirmed live (profiling a real Plex browsing session) that this
    # loop was the single largest cost in a render, dwarfing the
    # shadow/backdrop (see _plex_panel_shadow/_plex_root_wash, cached the
    # same way for the same reason). tile_gap/tile_width/tile_height are
    # already fully determined by canvas size/columns/max_rows (not by
    # which page is showing), so a resize is the only thing that changes
    # them.
    tiles_key = (
        tuple(_plex_tile_signature(node, favorites) for node in window),
        columns,
        round(tile_width),
        round(tile_height),
        round(tile_gap),
    )
    tiles_layer = _plex_grid_tiles_cache.get(tiles_key)
    if tiles_layer is None:
        tiles_layer = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
        tiles_draw = ImageDraw.Draw(tiles_layer)
        for offset, node in enumerate(window):
            col = offset % columns
            row = offset // columns
            tile_x = tile_gap + col * (tile_width + tile_gap)
            tile_y = header_height + tile_gap + row * (tile_height + tile_gap)
            poster_box = (round(tile_x), round(tile_y), round(tile_x + tile_width), round(tile_y + poster_height))

            thumb = cached_image(node.thumb_url)
            if thumb is not None:
                tiles_layer.alpha_composite(
                    ImageOps.fit(thumb, (poster_box[2] - poster_box[0], poster_box[3] - poster_box[1]), method=Image.LANCZOS),
                    (poster_box[0], poster_box[1]),
                )
            elif node.kind in _PLEX_LIBRARY_KINDS:
                icon_size = min(tile_width, poster_height) * 0.7
                icon_x = tile_x + (tile_width - icon_size) / 2
                icon_y = tile_y + (poster_height - icon_size) / 2
                _draw_folder_icon(tiles_draw, icon_x, icon_y, icon_size)
            else:
                tiles_draw.rounded_rectangle(poster_box, radius=tile_width * 0.06, fill=_GRID_HEADER_COLOR)

            _draw_plex_watch_badge(tiles_draw, tile_x, tile_y, tile_width, poster_height, node)

            is_favorite = favorites is not None and node.kind in _PLEX_FAVORITABLE_KINDS and node.rating_key in favorites
            if is_favorite:
                tiles_draw.text(
                    (tile_x + tile_width * 0.05, tile_y + tile_width * 0.03),
                    _FAVORITE_MARK.strip(),
                    font=badge_font,
                    fill=_FAVORITE_COLOR,
                )

            if node.container:
                chevron_size = round(tile_width * 0.22)
                chevron_margin = round(tile_width * 0.06)
                chevron_cx = tile_x + tile_width - chevron_margin - chevron_size / 2
                chevron_cy = tile_y + chevron_margin + chevron_size / 2
                tiles_draw.ellipse(
                    (chevron_cx - chevron_size / 2, chevron_cy - chevron_size / 2, chevron_cx + chevron_size / 2, chevron_cy + chevron_size / 2),
                    fill=_ACCENT_COLOR,
                )
                chevron_bbox = tiles_draw.textbbox((0, 0), _PLEX_CHEVRON, font=badge_font)
                tiles_draw.text(
                    (
                        chevron_cx - (chevron_bbox[2] - chevron_bbox[0]) / 2 - chevron_bbox[0],
                        chevron_cy - (chevron_bbox[3] - chevron_bbox[1]) / 2 - chevron_bbox[1],
                    ),
                    _PLEX_CHEVRON,
                    font=badge_font,
                    fill=_WHITE,
                )

            label_text = _fit_text(tiles_draw, node.title, label_font, tile_width * 0.96)
            label_bbox = tiles_draw.textbbox((0, 0), label_text, font=label_font)
            label_y = tile_y + poster_height + (title_height - (label_bbox[3] - label_bbox[1])) / 2 - label_bbox[1]
            tiles_draw.text(
                (tile_x + tile_width / 2 - tiles_draw.textlength(label_text, font=label_font) / 2, label_y),
                label_text,
                font=label_font,
                fill=_WHITE,
            )
        _plex_grid_tiles_cache[tiles_key] = tiles_layer
    panel.alpha_composite(tiles_layer)

    selected_offset = selected_index - window_start
    if 0 <= selected_offset < len(window):
        col = selected_offset % columns
        row = selected_offset // columns
        tile_x = tile_gap + col * (tile_width + tile_gap)
        tile_y = header_height + tile_gap + row * (tile_height + tile_gap)
        # A two-tone border -- a thin black ring immediately around
        # the poster, then a white ring further out still -- rather
        # than list view's plain _SELECTION_BORDER_COLOR alone.
        # Confirmed live: a real poster's own background is often
        # white/light itself (period movie art especially), which
        # made a plain white border blend straight into it and
        # vanish; putting the black ring closest to the poster and
        # the white ring outside *that* (against the panel's own
        # dark background) keeps both rings visible regardless of
        # the poster's own colors -- one plain white ring flush
        # against the poster (tried first) wasn't enough, since nothing
        # then separated white-on-white.
        border_width = max(3, round(tile_width * 0.022))
        inner_gap = round(border_width * 0.6)
        draw.rectangle(
            (
                tile_x - inner_gap,
                tile_y - inner_gap,
                tile_x + tile_width + inner_gap,
                tile_y + poster_height + inner_gap,
            ),
            outline=(0, 0, 0, 255),
            width=border_width,
        )
        outer_offset = inner_gap + border_width
        draw.rectangle(
            (
                tile_x - outer_offset,
                tile_y - outer_offset,
                tile_x + tile_width + outer_offset,
                tile_y + poster_height + outer_offset,
            ),
            outline=_SELECTION_BORDER_COLOR,
            width=border_width,
        )

    panel_canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    panel_canvas.alpha_composite(_plex_panel_shadow(panel_width, panel_height, margin, corner_radius))
    panel_canvas.alpha_composite(panel, (margin, margin))

    canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    canvas.alpha_composite(_plex_full_backdrop(selected_poster, canvas_width, canvas_height, cached_image(title_logo_url)))
    canvas.alpha_composite(
        panel_canvas,
        ((canvas_width - panel_canvas.width) // 2, max(0, canvas_height - panel_canvas.height - _PLEX_OVERLAY_BOTTOM_MARGIN)),
    )

    return canvas


_DISCONNECT_LABEL_COLOR = (255, 92, 122, 255)  # same red/pink as _FAVORITE_COLOR -- reused here for "this ends the cast"


class CastableDevice(_TypingProtocol):
    """Structural type for render_cast_picker/visible_cast_devices --
    they only ever read `.name`, so this covers chromecast.py's
    CastDevice without render_cast_picker needing to import it."""

    name: str


def _cast_window_start(total: int, selected_index: int, max_rows: int) -> int:
    if total <= max_rows:
        return 0
    half = max_rows // 2
    return max(0, min(selected_index - half, total - max_rows))


def visible_cast_devices(
    devices: list[CastableDevice], selected_index: int, max_rows: int = 8
) -> list[CastableDevice]:
    """A windowed slice of `devices` containing at most `max_rows`
    entries, scrolled to keep `selected_index` in view -- mirrors
    visible_vod_items'/visible_plex_nodes' windowing."""
    start = _cast_window_start(len(devices), selected_index, max_rows)
    return devices[start : start + max_rows]


def render_cast_picker(
    protocol_label: str,
    devices: list[CastableDevice],
    selected_index: int,
    connected_device_name: str | None,
    scanning: bool,
    canvas_width: int,
    canvas_height: int,
    max_rows: int = 8,
) -> Image.Image:
    """A flat device-picker for casting (see the 'k' keybinding in
    cli.py) -- modeled on render_vod_browser's single-list layout, but
    unlike that browser this always returns an image, never None: an
    empty device list still needs to show "Scanning..."/"No devices
    found" text rather than the picker simply not opening at all.

    `selected_index` is in the same combined space cli.py tracks: a
    synthetic "Disconnect" row (shown in red, whenever
    `connected_device_name` says a cast is already active) is row 0,
    with real devices following it -- both sides agreeing on "disconnect
    row first, if present" is what keeps them in lock-step without
    passing a pre-combined list across the module boundary."""
    has_disconnect_row = connected_device_name is not None
    row_labels = ([f"Disconnect (casting to {connected_device_name})"] if has_disconnect_row else []) + [
        d.name for d in devices
    ]
    is_message_only = not row_labels
    if is_message_only:
        row_labels = ["Scanning for devices..." if scanning else f"No {protocol_label} devices found"]

    side_gap = max(16, round(canvas_width * 0.02))
    panel_width = max(400, canvas_width - 2 * side_gap)
    header_height = round(canvas_height * 0.07)
    entry_row_height = round(canvas_height * 0.075)

    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    label_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))

    window_start = _cast_window_start(len(row_labels), selected_index, max_rows)
    window = row_labels[window_start : window_start + max_rows]
    panel_height = header_height + len(window) * entry_row_height
    margin = max(16, round(panel_height * 0.02))
    corner_radius = panel_height * 0.025

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)
    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    draw.text((logo_margin + logo_size + logo_margin, header_height * 0.28), protocol_label, font=title_font, fill=_WHITE)

    padding = round(panel_width * 0.015)
    y = header_height
    for offset, label in enumerate(window):
        index = window_start + offset
        is_disconnect_row = not is_message_only and has_disconnect_row and index == 0
        row_top = y
        row_bottom = row_top + entry_row_height
        row_mid = row_top + entry_row_height / 2

        label_text = _fit_text(draw, label, label_font, panel_width - 2 * padding)
        label_bbox = draw.textbbox((0, 0), label_text, font=label_font)
        fill = _DISCONNECT_LABEL_COLOR if is_disconnect_row else (_MUTED if is_message_only else _WHITE)
        draw.text(
            (padding, row_mid - (label_bbox[3] - label_bbox[1]) / 2 - label_bbox[1]),
            label_text,
            font=label_font,
            fill=fill,
        )

        if not is_message_only and index == selected_index:
            border_width = max(2, round(entry_row_height * 0.035))
            draw.rectangle(
                (
                    border_width // 2,
                    row_top + border_width // 2,
                    panel_width - border_width // 2,
                    row_bottom - border_width // 2,
                ),
                outline=_SELECTION_BORDER_COLOR,
                width=border_width,
            )

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
        y = row_bottom

    canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + panel_width - 1, margin + panel_height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=panel_height * 0.015)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def _format_schedule_date(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return day.strftime("%A %d %B %Y")


def visible_schedule(
    schedule: list[ScheduledRecording], selected_id: str | None, max_rows: int = 8
) -> list[ScheduledRecording]:
    """A windowed slice of `schedule` (already soonest-first -- see
    tvdinner.cli's schedule_list), containing at most `max_rows` entries,
    scrolled to keep the one at `selected_id` in view -- mirrors
    visible_recordings' windowing."""
    if len(schedule) <= max_rows:
        return schedule

    index = next((i for i, s in enumerate(schedule) if s.id == selected_id), 0)
    half = max_rows // 2
    start = max(0, min(index - half, len(schedule) - max_rows))
    return schedule[start : start + max_rows]


def render_schedule_browser(
    schedule: list[ScheduledRecording],
    selected_id: str | None,
    display: EpgDisplay,
    canvas_width: int,
    canvas_height: int,
    max_rows: int = 8,
    active_id: str | None = None,
    missed: list[tuple[ScheduledRecording, str]] | None = None,
) -> Image.Image | None:
    """A date-grouped list of upcoming scheduled recordings (see the 'u'
    keybinding in cli.py), soonest first -- a date header ("Today",
    "Tomorrow", or the full date) above each day's entries, with a
    selection border on the row at `selected_id` so a caller can move a
    cursor and act on it (e.g. Enter to cancel). Returns None if both
    `schedule` and `missed` are empty; the caller is expected not to open
    this browser at all in that case.

    `active_id` is the entry (if any) currently being recorded (see
    tvdinner.cli's active_schedule) -- shown with a "Recording now" marker
    in place of its start/stop time.

    `missed` is recent (title, reason) recordings that never actually ran
    (see tvdinner.cli's missed_schedule) -- e.g. a schedule conflict, or
    its channel no longer being in the playlist -- shown in their own
    section above the upcoming ones so a conflict isn't silent. These
    aren't part of the selectable/windowed list; there's no cursor action
    for them (nothing left to cancel), just an explanation.

    Times are shown in `display`'s local timezone, corrected by this
    channel's clock shift like the guide/details popup (EpgDisplay.to_local
    already applies it) -- entry.start/stop are raw/unshifted, same as a
    Programme's (see tvdinner.schedule.ScheduledRecording).
    """
    if not schedule and not missed:
        return None

    window = visible_schedule(schedule, selected_id, max_rows)

    today = datetime.now().date()
    rows: list[tuple[str, object]] = []

    if missed:
        rows.append(("missed_header", None))
        for entry, reason in missed:
            rows.append(("missed_entry", (entry, reason)))

    last_date: date | None = None
    for entry in window:
        day = display.to_local(entry.start, channel_name=entry.channel_name).date()
        if day != last_date:
            rows.append(("header", day))
            last_date = day
        rows.append(("entry", entry))

    side_gap = max(16, round(canvas_width * 0.02))
    panel_width = max(400, canvas_width - 2 * side_gap)

    header_height = round(canvas_height * 0.07)
    entry_row_height = round(canvas_height * 0.075)
    date_row_height = round(canvas_height * 0.045)

    def _row_height(kind: str) -> int:
        return date_row_height if kind in ("header", "missed_header") else entry_row_height

    panel_height = header_height + sum(_row_height(kind) for kind, _ in rows)
    margin = max(16, round(panel_height * 0.02))

    title_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    date_font = _font("Inter-Bold.ttf", round(min(canvas_width * 0.009, date_row_height * 0.5)))
    name_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))
    channel_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.0075, entry_row_height * 0.22)))
    meta_font = _font("Inter-Regular.ttf", round(min(canvas_width * 0.008, entry_row_height * 0.24)))

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = panel_height * 0.025
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    draw.text(
        (logo_margin + logo_size + logo_margin, header_height * 0.28),
        "Scheduled Recordings",
        font=title_font,
        fill=_WHITE,
    )

    padding = round(panel_width * 0.015)
    y = header_height
    for kind, item in rows:
        if kind == "header":
            row_bottom = y + date_row_height
            draw.text(
                (padding, y + (date_row_height - date_font.size) / 2),
                _format_schedule_date(item, today),
                font=date_font,
                fill=_ACCENT_COLOR,
            )
            draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
            y = row_bottom
            continue

        if kind == "missed_header":
            row_bottom = y + date_row_height
            draw.text(
                (padding, y + (date_row_height - date_font.size) / 2),
                "Missed",
                font=date_font,
                fill=_RECORDING_BADGE_COLOR,
            )
            draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
            y = row_bottom
            continue

        if kind == "missed_entry":
            missed_entry, reason = item
            row_top = y
            row_bottom = row_top + entry_row_height
            row_mid = row_top + entry_row_height / 2

            reason_text = _fit_text(draw, reason, meta_font, round(panel_width * 0.35))
            reason_width = draw.textlength(reason_text, font=meta_font)
            label_max_width = panel_width - 2 * padding - reason_width - padding

            title_text = _fit_text(draw, missed_entry.title, name_font, label_max_width)
            title_bbox = draw.textbbox((0, 0), title_text, font=name_font)
            title_height = title_bbox[3] - title_bbox[1]

            channel_text = _fit_text(draw, missed_entry.channel_name, channel_font, label_max_width)
            channel_bbox = draw.textbbox((0, 0), channel_text, font=channel_font)
            channel_height = channel_bbox[3] - channel_bbox[1]

            line_gap = round(entry_row_height * 0.04)
            block_top = row_mid - (title_height + line_gap + channel_height) / 2
            draw.text((padding, block_top - title_bbox[1]), title_text, font=name_font, fill=_MUTED)
            draw.text(
                (padding, block_top + title_height + line_gap - channel_bbox[1]),
                channel_text,
                font=channel_font,
                fill=_MUTED,
            )

            reason_bbox = draw.textbbox((0, 0), reason_text, font=meta_font)
            draw.text(
                (
                    panel_width - padding - reason_width,
                    row_mid - (reason_bbox[3] - reason_bbox[1]) / 2 - reason_bbox[1],
                ),
                reason_text,
                font=meta_font,
                fill=_RECORDING_BADGE_COLOR,
            )

            draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
            y = row_bottom
            continue

        entry: ScheduledRecording = item
        row_top = y
        row_bottom = row_top + entry_row_height
        row_mid = row_top + entry_row_height / 2

        is_active = entry.id == active_id
        if is_active:
            meta_text = "Recording now"
        else:
            start_local = display.to_local(entry.start, channel_name=entry.channel_name)
            stop_local = display.to_local(entry.stop, channel_name=entry.channel_name)
            meta_text = f"{start_local.strftime('%H:%M')}–{stop_local.strftime('%H:%M')}"
        meta_color = _RECORDING_BADGE_COLOR if is_active else _MUTED
        meta_width = draw.textlength(meta_text, font=meta_font)
        label_max_width = panel_width - 2 * padding - meta_width - padding

        title_text = _fit_text(draw, entry.title, name_font, label_max_width)
        title_bbox = draw.textbbox((0, 0), title_text, font=name_font)
        title_height = title_bbox[3] - title_bbox[1]

        channel_text = _fit_text(draw, entry.channel_name, channel_font, label_max_width)
        channel_bbox = draw.textbbox((0, 0), channel_text, font=channel_font)
        channel_height = channel_bbox[3] - channel_bbox[1]

        line_gap = round(entry_row_height * 0.04)
        block_top = row_mid - (title_height + line_gap + channel_height) / 2
        draw.text((padding, block_top - title_bbox[1]), title_text, font=name_font, fill=_WHITE)
        draw.text(
            (padding, block_top + title_height + line_gap - channel_bbox[1]),
            channel_text,
            font=channel_font,
            fill=_MUTED,
        )

        meta_bbox = draw.textbbox((0, 0), meta_text, font=meta_font)
        draw.text(
            (panel_width - padding - meta_width, row_mid - (meta_bbox[3] - meta_bbox[1]) / 2 - meta_bbox[1]),
            meta_text,
            font=meta_font,
            fill=meta_color,
        )

        if entry.id == selected_id:
            border_width = max(2, round(entry_row_height * 0.035))
            draw.rectangle(
                (
                    border_width // 2,
                    row_top + border_width // 2,
                    panel_width - border_width // 2,
                    row_bottom - border_width // 2,
                ),
                outline=_SELECTION_BORDER_COLOR,
                width=border_width,
            )

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)
        y = row_bottom

    canvas = Image.new("RGBA", (panel_width + margin * 2, panel_height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + panel_width - 1, margin + panel_height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=panel_height * 0.015)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


# Kept in sync with cli.py's actual keybindings by hand -- see the '?'
# keybinding there. Order here is display order (top-to-bottom, then
# wrapping to the next column), not necessarily most-to-least important.
_HELP_TABS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Guide",
        [
            ("g / MENU (hold)", "Toggle program guide"),
            ("b", "Switch to last watched channel"),
            ("LEFT / RIGHT", "Page guide timeline"),
            ("UP / DOWN", "Move guide selection"),
            ("PGUP / PGDWN", "Page guide selection"),
            ("[ / ]", "Nudge this channel's EPG shift"),
            ("f", "Filter guide by name/group"),
            ("c", "Clear guide filter"),
            ("v", "Favorites-only view"),
            ("h", "Toggle favorite"),
            ("s", "Schedule/cancel a recording"),
        ],
    ),
    (
        "Playback",
        [
            ("i / MENU", "Programme/file info"),
            ("p / ENTER", "Pause/resume live TV"),
            ("o", "Toggle picture-in-picture"),
            ("t", "Toggle subtitles"),
            ("j / J", "Cycle subtitle track forward/back"),
            ("z", "Cycle aspect ratio"),
            ("e", "Cycle sleep timer (Off/15/30/60/90m)"),
            ("[ / ] / { / }", "Adjust playback speed"),
            ("Ctrl++ / Ctrl+-", "Adjust audio sync"),
            ("Alt++ / Alt+-", "Zoom video (Alt+arrows to pan)"),
            ("BS", "Stop / quit"),
            ("k", "Cast to Chromecast"),
            ("a", "Toggle about"),
            ("ESC / GO_BACK", "Close popup / cancel"),
            ("?", "Toggle this help"),
        ],
    ),
    (
        "VOD & Chapters",
        [
            ("m", "Browse VOD movies"),
            ("l", "Browse TV series library (Xtream)"),
            ("UP / DOWN", "Preview next/previous chapter (Plex)"),
            ("ENTER / ESC", "Jump to previewed chapter / cancel"),
            ("j / ENTER", "Confirm Skip Intro/Credits prompt"),
            ("ESC", "Cancel the Up Next auto-play countdown"),
        ],
    ),
    (
        "Recording & History",
        [
            ("r", "Toggle recording"),
            ("w", "Browse past recordings"),
            ("d", "Delete recording (in browser)"),
            ("u", "Browse scheduled recordings"),
            ("x", "Browse watch history"),
        ],
    ),
    (
        "Plex",
        [
            ("l", "Browse Plex library"),
            ("ENTER (hold)", "Plex item menu (play/mark watched)"),
            ("/", "Search Plex library"),
            ("y", "Filter Plex by release year"),
            ("g", "Grid/list view"),
            ("h", "Toggle favorite (movie/show)"),
            ("v", "Favorites-only view"),
        ],
    ),
]
_HELP_MAX_TAB_ENTRIES = max(len(entries) for _, entries in _HELP_TABS)


def help_tab_count() -> int:
    """How many tabs render_help_overlay's tab_index can address -- cli.py
    uses this to wrap LEFT/RIGHT (see _prev_help_tab/_next_help_tab)
    without needing to import the private _HELP_TABS list itself."""
    return len(_HELP_TABS)


def render_help_overlay(canvas_width: int = 1920, canvas_height: int = 1080, tab_index: int = 0) -> Image.Image:
    """A keyboard-shortcuts cheat sheet (see the '?' keybinding in cli.py),
    grouped into category tabs cycled with LEFT/RIGHT while it's open (see
    cli.py's open_help_overlay/_prev_help_tab/_next_help_tab) -- so a new
    user can quickly orient themselves without reading the README, and a
    returning one can scan one category instead of a 37-entry wall of
    text. `tab_index` is trusted as already in range (cli.py wraps it via
    modulo, same convention as cycle_aspect_ratio/cycle_sleep_timer), same
    "caller's job to pass valid input" contract every other render
    function here already has. Unlike the EPG banner, this doesn't
    auto-hide -- it's meant to be read, not glanced at.

    The panel's own height is fixed across every tab -- computed from
    _HELP_MAX_TAB_ENTRIES (the *largest* tab), not whichever one is
    currently showing -- so switching tabs never visibly resizes the
    panel; a shorter tab's rows are vertically centered in that fixed
    space instead of left stranded at the top with dead space below.
    """
    _, entries = _HELP_TABS[tab_index]
    columns = 2
    max_rows = (_HELP_MAX_TAB_ENTRIES + columns - 1) // columns
    own_rows = (len(entries) + columns - 1) // columns

    width = min(1200, round(canvas_width * 0.65))
    row_height = max(30, round(canvas_height * 0.045))
    header_height = round(canvas_height * 0.08)
    tab_strip_height = round(canvas_height * 0.05)
    padding = round(width * 0.03)
    col_width = (width - 2 * padding) / columns
    key_col_width = round(col_width * 0.34)

    title_font = _font("Inter-Bold.ttf", round(header_height * 0.42))
    tab_font = _font("Inter-Bold.ttf", round(tab_strip_height * 0.36))
    hint_font = _font("Inter-Regular.ttf", round(tab_strip_height * 0.28))
    key_font = _font("Inter-Bold.ttf", round(row_height * 0.4))
    desc_font = _font("Inter-Regular.ttf", round(row_height * 0.36))

    grid_top = header_height + tab_strip_height
    height = grid_top + max_rows * row_height + round(padding * 0.6)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = height * 0.02
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)
    draw.rectangle((0, 0, width - 1, header_height), fill=_GRID_HEADER_COLOR)
    logo_size = round(header_height * 0.6)
    logo_margin = round((header_height - logo_size) / 2)
    panel.alpha_composite(_app_logo(logo_size), (logo_margin, logo_margin))
    draw.text(
        (logo_margin + logo_size + logo_margin, header_height * 0.3), "Keyboard Shortcuts", font=title_font, fill=_WHITE
    )

    # Tab strip: each tab's name as a pill, the current one filled in the
    # accent color, the rest just outlined/muted -- same "quiet unless
    # selected" language _draw_hdr_pill/render_skip_marker_overlay's pills
    # already use elsewhere in this file.
    pill_pad_x = tab_strip_height * 0.28
    pill_h = tab_strip_height * 0.62
    pill_y = header_height + (tab_strip_height - pill_h) / 2
    cursor_x = padding
    for index, (name, _) in enumerate(_HELP_TABS):
        pill_w = draw.textlength(name, font=tab_font) + 2 * pill_pad_x
        active = index == tab_index
        draw.rounded_rectangle(
            (cursor_x, pill_y, cursor_x + pill_w, pill_y + pill_h),
            radius=pill_h * 0.3,
            fill=_ACCENT_COLOR if active else None,
            outline=None if active else _MUTED,
            width=1,
        )
        text_bbox = draw.textbbox((0, 0), name, font=tab_font)
        text_y = pill_y + (pill_h - (text_bbox[3] - text_bbox[1])) / 2 - text_bbox[1]
        draw.text((cursor_x + pill_pad_x, text_y), name, font=tab_font, fill=_WHITE if active else _MUTED)
        cursor_x += pill_w + pill_pad_x

    hint_text = "LEFT / RIGHT to switch tabs"
    hint_width = draw.textlength(hint_text, font=hint_font)
    hint_bbox = draw.textbbox((0, 0), hint_text, font=hint_font)
    hint_y = header_height + (tab_strip_height - (hint_bbox[3] - hint_bbox[1])) / 2 - hint_bbox[1]
    draw.text((width - padding - hint_width, hint_y), hint_text, font=hint_font, fill=_MUTED)

    row_offset = round((max_rows - own_rows) / 2) * row_height

    for index, (key, description) in enumerate(entries):
        col = index // own_rows
        row = index % own_rows
        x = padding + col * col_width
        row_mid = grid_top + row_offset + row * row_height + row_height / 2

        key_text = _fit_text(draw, key, key_font, key_col_width - 8)
        key_bbox = draw.textbbox((0, 0), key_text, font=key_font)
        draw.text(
            (x, row_mid - (key_bbox[3] - key_bbox[1]) / 2 - key_bbox[1]), key_text, font=key_font, fill=_ACCENT_COLOR
        )

        desc_max_width = col_width - key_col_width - round(padding * 0.4)
        desc_text = _fit_text(draw, description, desc_font, desc_max_width)
        desc_bbox = draw.textbbox((0, 0), desc_text, font=desc_font)
        draw.text(
            (x + key_col_width, row_mid - (desc_bbox[3] - desc_bbox[1]) / 2 - desc_bbox[1]),
            desc_text,
            font=desc_font,
            fill=_MUTED,
        )

    margin = round(height * 0.04)
    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 190),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.02)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


_ABOUT_TAGLINE = "Live TV, a real program guide, and DVR -- fast, simple, yours."


def render_about_overlay(version: str, canvas_width: int = 1920, canvas_height: int = 1080) -> Image.Image:
    """A centered "about" card (see the 'a' keybinding in cli.py): logo,
    app name, version, and a one-line summary of what tvdinner does.
    Unlike the help overlay's dense list, this is meant to be a quick,
    good-looking glance rather than a reference -- short, centered, and
    with a soft glow behind the logo for a bit of polish."""
    width = min(760, round(canvas_width * 0.4))
    padding = round(width * 0.09)
    content_width = width - 2 * padding

    logo_size = round(width * 0.24)
    name_font = _font("Inter-Bold.ttf", round(width * 0.075))
    version_font = _font("Inter-Bold.ttf", round(width * 0.032))
    tagline_font = _font("Inter-Regular.ttf", round(width * 0.038))

    gap_logo_name = round(width * 0.045)
    gap_name_version = round(width * 0.015)
    gap_version_divider = round(width * 0.05)
    divider_height = max(2, round(width * 0.004))
    gap_divider_tagline = round(width * 0.05)
    tagline_line_height = round(tagline_font.size * 1.35)

    # A throwaway canvas just to measure the tagline's wrapped line count
    # up front, so the panel's height can be sized to its actual content.
    measure_draw = ImageDraw.Draw(Image.new("RGBA", (content_width, 10)))
    tagline_lines = _wrap_text(measure_draw, _ABOUT_TAGLINE, tagline_font, content_width, max_lines=3)

    height = (
        padding
        + logo_size
        + gap_logo_name
        + name_font.size
        + gap_name_version
        + version_font.size
        + gap_version_divider
        + divider_height
        + gap_divider_tagline
        + len(tagline_lines) * tagline_line_height
        + padding
    )

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = width * 0.04
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    center_x = width // 2
    y = padding

    glow_size = round(logo_size * 2.2)
    glow = Image.new("RGBA", (glow_size, glow_size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (glow_size * 0.15, glow_size * 0.15, glow_size * 0.85, glow_size * 0.85),
        fill=(*_ACCENT_COLOR[:3], 90),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=glow_size * 0.12))
    panel.alpha_composite(glow, (center_x - glow_size // 2, y + logo_size // 2 - glow_size // 2))
    panel.alpha_composite(_app_logo(logo_size), (center_x - logo_size // 2, y))
    y += logo_size + gap_logo_name

    def draw_centered(text: str, font, fill, top: int) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((center_x - (bbox[2] - bbox[0]) / 2 - bbox[0], top), text, font=font, fill=fill)

    draw_centered("tvdinner", name_font, _WHITE, y)
    y += name_font.size + gap_name_version

    draw_centered(version if version.startswith("v") else f"v{version}", version_font, _ACCENT_COLOR, y)
    y += version_font.size + gap_version_divider

    divider_width = round(width * 0.22)
    draw.rectangle((center_x - divider_width // 2, y, center_x + divider_width // 2, y + divider_height - 1), fill=_MUTED)
    y += divider_height + gap_divider_tagline

    for line in tagline_lines:
        draw_centered(line, tagline_font, _MUTED, y)
        y += tagline_line_height

    margin = round(height * 0.05)
    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 190),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.02)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas


def render_update_available_overlay(
    remote_version: str, current_version: str, canvas_width: int = 1920, canvas_height: int = 1080
) -> Image.Image:
    """A centered "update available" card (see tvdinner.update_check),
    shown once a background check finds a newer GitHub release --
    styled like render_about_overlay's card (rounded panel, drop-shadow,
    centered text) but without the logo glow, since this is a one-off
    notification rather than a settled "about" screen. 'y' opens the
    release page in a browser; 'n'/ESC dismisses -- both remember this
    version so it isn't shown again."""
    width = min(760, round(canvas_width * 0.4))
    padding = round(width * 0.09)

    eyebrow_font = _font("Inter-Bold.ttf", round(width * 0.04))
    title_font = _font("Inter-Bold.ttf", round(width * 0.075))
    subtitle_font = _font("Inter-Regular.ttf", round(width * 0.038))
    hint_font = _font("Inter-Regular.ttf", round(width * 0.032))

    gap_eyebrow_title = round(width * 0.05)
    gap_title_subtitle = round(width * 0.03)
    gap_subtitle_divider = round(width * 0.05)
    divider_height = max(2, round(width * 0.004))
    gap_divider_hint = round(width * 0.05)

    height = (
        padding
        + eyebrow_font.size
        + gap_eyebrow_title
        + title_font.size
        + gap_title_subtitle
        + subtitle_font.size
        + gap_subtitle_divider
        + divider_height
        + gap_divider_hint
        + hint_font.size
        + padding
    )

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = width * 0.04
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    center_x = width // 2
    y = padding

    def draw_centered(text: str, font, fill, top: int) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((center_x - (bbox[2] - bbox[0]) / 2 - bbox[0], top), text, font=font, fill=fill)

    draw_centered("UPDATE AVAILABLE", eyebrow_font, _ACCENT_COLOR, y)
    y += eyebrow_font.size + gap_eyebrow_title

    remote_label = remote_version if remote_version.startswith("v") else f"v{remote_version}"
    draw_centered(f"{remote_label} is available", title_font, _WHITE, y)
    y += title_font.size + gap_title_subtitle

    current_label = current_version if current_version.startswith("v") else f"v{current_version}"
    draw_centered(f"You have {current_label}", subtitle_font, _MUTED, y)
    y += subtitle_font.size + gap_subtitle_divider

    divider_width = round(width * 0.22)
    draw.rectangle((center_x - divider_width // 2, y, center_x + divider_width // 2, y + divider_height - 1), fill=_MUTED)
    y += divider_height + gap_divider_hint

    draw_centered("y  Open release page   ·   n / ESC  Dismiss", hint_font, _MUTED, y)

    margin = round(height * 0.05)
    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1),
        radius=corner_radius,
        fill=(0, 0, 0, 190),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.02)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas
