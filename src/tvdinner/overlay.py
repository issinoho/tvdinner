"""Renders a TiviMate-style EPG banner as a composited RGBA image: channel
logo, current programme with a live progress bar, description, and what's
next. The image itself is display-engine agnostic; player.py is responsible
for pushing it onto mpv's video output.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import logging
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from tvdinner import __version__
from tvdinner.channel_logos import OnlineLogoIndex
from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel
from tvdinner.player import RecordingFile
from tvdinner.schedule import ScheduledRecording
from tvdinner.vod import VodItem

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
_FAVORITE_MARK = "♥ "  # heart suit, followed by a space before the channel name
_RECORDING_BADGE_COLOR = (214, 40, 54, 255)

DEFAULT_GUIDE_WINDOW_HOURS = 3.0

_logo_cache: dict[str, Image.Image | None] = {}
_app_logo_cache: dict[int, Image.Image] = {}


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


def _title_with_year(programme: Programme) -> str:
    return f"{programme.title} ({programme.year})" if programme.year else programme.title


def _font(name: str, size: int) -> ImageFont.ImageFont:
    # Bundled as package data (not read from an OS font directory) so
    # rendering looks identical everywhere, regardless of what fonts --
    # if any -- happen to be installed on the host.
    try:
        with importlib.resources.as_file(importlib.resources.files("tvdinner") / "fonts" / name) as path:
            return ImageFont.truetype(str(path), max(size, 8))
    except OSError:
        return ImageFont.load_default()


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
    .notdef placeholder. Some bundled fonts (e.g. DejaVuSans) draw a
    visible empty box ('tofu') for .notdef instead of leaving blank
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

    font = _font("DejaVuSans-Bold.ttf", round(size * 0.42))
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


def _decode_image(url: str) -> Image.Image | None:
    try:
        if url.startswith(("http://", "https://")):
            response = requests.get(url, headers=_IMAGE_REQUEST_HEADERS, timeout=10)
            response.raise_for_status()
            data = response.content
            if hashlib.sha256(data).hexdigest() in _BLOCKED_IMAGE_HASHES:
                logger.warning("Image %s returned a known region-block placeholder; treating as unavailable", url)
                return None
        else:
            path = url[len("file://"):] if url.startswith("file://") else url
            with open(path, "rb") as handle:
                data = handle.read()
        return Image.open(BytesIO(data)).convert("RGBA")
    except (requests.RequestException, OSError, ValueError) as exc:
        logger.warning("Could not fetch/decode image %s: %s", url, exc)
        return None


def fetch_image(url: str | None) -> Image.Image | None:
    """Fetch and decode an image (channel logo or programme poster), cached
    by URL. Returns None if there is no URL or it can't be fetched/decoded,
    so callers can fall back to a placeholder."""
    if not url:
        return None
    if url not in _logo_cache:
        _logo_cache[url] = _decode_image(url)
    return _logo_cache[url]


def _fit_within_box(image: Image.Image, width: int, height: int) -> Image.Image:
    """Resize `image` to fit within (width, height) without distorting its
    aspect ratio (e.g. a portrait movie poster inside a wider reserved box),
    centered on a transparent canvas of exactly that size."""
    fitted = ImageOps.contain(image, (width, height))
    box = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    box.alpha_composite(fitted.convert("RGBA"), ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return box


_LOGO_TILE_COLOR = (250, 250, 252, 255)
_LOGO_TILE_DARK_COLOR = (38, 40, 46, 255)
_LOGO_LIGHT_LUMINANCE_THRESHOLD = 200  # see _average_luminance -- calibrated against real logo assets


def _average_luminance(image: Image.Image) -> float:
    """Alpha-weighted average luminance (0-255) of `image`'s visible
    pixels -- fully transparent pixels don't count at all, and a mostly-
    transparent one counts proportionally less than an opaque one."""
    total_luminance = total_weight = 0.0
    for r, g, b, a in image.convert("RGBA").getdata():
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
    a plain white square."""
    bbox = logo.getbbox(alpha_only=True)
    if bbox:
        logo = logo.crop(bbox)
    tile_color = _LOGO_TILE_DARK_COLOR if _average_luminance(logo) >= _LOGO_LIGHT_LUMINANCE_THRESHOLD else _LOGO_TILE_COLOR
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, size - 1, size - 1), radius=size * 0.18, fill=tile_color)
    inset = round(size * 0.06)
    fitted = _fit_within_box(logo, size - 2 * inset, size - 2 * inset)
    tile.alpha_composite(fitted, (inset, inset))
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
    badges: list[str] | None = None,
    favorites: set[str] | None = None,
) -> Image.Image:
    """Compose the channel/EPG banner into a single RGBA image.

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

    name_font = _font("DejaVuSans-Bold.ttf", round(nominal_height * 0.13))
    title_font = _font("DejaVuSans-Bold.ttf", round(nominal_height * 0.17))
    meta_font = _font("DejaVuSans.ttf", round(nominal_height * 0.105))
    small_font = _font("DejaVuSans.ttf", round(nominal_height * 0.095))
    badge_font = _font("DejaVuSans-Bold.ttf", round(nominal_height * 0.08))
    bar_h = max(4, round(nominal_height * 0.045))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    is_favorite = favorites is not None and channel.name in favorites
    heart_width = round(measure.textlength(_FAVORITE_MARK, font=name_font)) if is_favorite else 0
    name_text = _fit_text(measure, channel.name, name_font, text_width - heart_width)

    title_text = time_text = remaining_text = None
    description_lines: list[str] = []
    fraction = 0.0
    if current is not None:
        title_text = _fit_text(measure, _title_with_year(current), title_font, text_width)
        start_local = display.to_local(current.start, channel_name=channel.name)
        stop_local = display.to_local(current.stop, channel_name=channel.name)
        time_text = f"{start_local.strftime('%H:%M')} – {stop_local.strftime('%H:%M')}"
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

    next_text = None
    if upcoming:
        start = display.to_local(upcoming.start, channel_name=channel.name).strftime("%H:%M")
        next_text = _fit_text(measure, f"Next  ·  {upcoming.title} ({start})", small_font, text_width)

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
            y += nominal_height * 0.155

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

    eyebrow_font = _font("DejaVuSans-Bold.ttf", round(nominal_height * 0.1))
    title_font = _font("DejaVuSans-Bold.ttf", round(nominal_height * 0.17))
    meta_font = _font("DejaVuSans.ttf", round(nominal_height * 0.105))
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


def visible_guide_channels(
    channels: list[Channel], epg: Epg, current_channel_url: str | None, max_rows: int = 8
) -> list[Channel]:
    """The page of channels a program guide should show: guide_eligible_channels,
    in a window of at most `max_rows` centered on `current_channel_url`.

    Centered/matched by URL, not tvg_id: real-world M3U playlists often have
    several distinct channels (different quality tiers, backup servers)
    sharing the same tvg_id for EPG mapping purposes, and tvg_id would then
    incorrectly identify all of them as "the same" row.
    """
    guide_channels = guide_eligible_channels(channels, epg)
    if not guide_channels:
        return []

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
    favorites: set[str] | None = None,
    scheduled: set[tuple[str, datetime]] | None = None,
    online_logos: OnlineLogoIndex | None = None,
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
    """
    visible = visible_guide_channels(channels, epg, selected_channel_url or current_channel_url, max_rows)
    if not visible:
        return None
    row_count = len(visible)

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
    header_title_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    time_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.0085, header_height * 0.34)))
    name_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.0105, row_height * 0.34)))
    group_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.0075, row_height * 0.22)))
    title_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.0105, row_height * 0.34)))
    recording_badge_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.008, row_height * 0.26)))
    recording_badge_radius = round(row_height * 0.16)

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
            if channel.url == selected_channel_url
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
        fetched_logo = resolve_channel_logo(channel, epg, online_logos)
        logo_image = _logo_tile(fetched_logo, logo_size) if fetched_logo else _fallback_avatar(channel.name, logo_size)
        panel.alpha_composite(logo_image, (logo_margin, round(row_mid - logo_size / 2)))

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

        for programme in epg.schedule_for(channel.tvg_id, channel.tvg_name or channel.name):
            corrected_start = programme.start + shift
            corrected_stop = programme.stop + shift
            if corrected_stop <= window_start or corrected_start >= window_end:
                continue
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

            if programme is selected_programme:
                draw.rectangle(
                    (x0 + block_pad, row_top + block_pad, x1 - block_pad, row_bottom - block_pad),
                    outline=_SELECTION_BORDER_COLOR,
                    width=max(2, round(row_height * 0.035)),
                )

        if channel.url == selected_channel_url and selected_programme is None:
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

    name_font = _font("DejaVuSans.ttf", round(nominal_height * 0.1))
    title_font = _font("DejaVuSans-Bold.ttf", round(nominal_height * 0.155))
    meta_font = _font("DejaVuSans.ttf", round(nominal_height * 0.095))
    body_font = _font("DejaVuSans.ttf", round(nominal_height * 0.09))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    name_text = _fit_text(measure, channel.name, name_font, text_width)
    title_lines = _wrap_text(measure, _title_with_year(programme), title_font, text_width, 3)

    start_local = display.to_local(programme.start, channel_name=channel.name)
    stop_local = display.to_local(programme.stop, channel_name=channel.name)
    time_text = f"{start_local.strftime('%a %d %b, %H:%M')} – {stop_local.strftime('%H:%M')}"

    description_lines = (
        _wrap_text(measure, programme.description, body_font, text_width, _MAX_DETAILS_DESCRIPTION_LINES)
        if programme.description
        else []
    )

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
        y += nominal_height * 0.16

        if programme.category:
            if draw:
                draw.text((text_x, y), _strip_unsupported_glyphs(programme.category, meta_font), font=meta_font, fill=_ACCENT_COLOR)
            y += nominal_height * 0.16

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


def render_guide_filter_prompt(text: str, canvas_width: int, canvas_height: int) -> Image.Image:
    """A small text-entry dialog overlaid on the program guide for typing a
    channel-name filter -- bound to 'f' (confirmed with ENTER, cancelled
    with ESC; see cli.py's guide filter-input keybinding). `text` is
    whatever's been typed so far, shown with a trailing cursor.
    """
    width = min(760, round(canvas_width * 0.42))
    height = round(canvas_height * 0.16)
    margin = round(height * 0.3)

    label_font = _font("DejaVuSans.ttf", round(height * 0.16))
    text_font = _font("DejaVuSans-Bold.ttf", round(height * 0.22))
    hint_font = _font("DejaVuSans.ttf", round(height * 0.13))

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

    panel_draw.text((padding, padding * 0.5), "Filter channels", font=label_font, fill=_MUTED)

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

    title_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    date_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.009, date_row_height * 0.5)))
    label_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))
    meta_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.008, entry_row_height * 0.24)))

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

    title_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    group_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.009, group_row_height * 0.5)))
    label_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))
    meta_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.008, entry_row_height * 0.24)))

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

    title_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.014, header_height * 0.5)))
    date_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.009, date_row_height * 0.5)))
    name_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.0105, entry_row_height * 0.3)))
    channel_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.0075, entry_row_height * 0.22)))
    meta_font = _font("DejaVuSans.ttf", round(min(canvas_width * 0.008, entry_row_height * 0.24)))

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
_HELP_ENTRIES: list[tuple[str, str]] = [
    ("i / ENTER", "Programme info (or recording progress)"),
    ("g / MENU", "Toggle program guide"),
    ("LEFT / RIGHT", "Page guide timeline"),
    ("UP / DOWN", "Move guide selection"),
    ("PGUP / PGDWN", "Page guide selection"),
    ("[ / ]", "Nudge this channel's EPG shift"),
    ("f", "Filter guide by name/group"),
    ("c", "Clear guide filter"),
    ("v", "Favorites-only guide view"),
    ("h", "Toggle favorite"),
    ("z", "Cycle aspect ratio"),
    ("r", "Toggle recording"),
    ("p / PLAYPAUSE", "Pause/resume live TV"),
    ("o", "Toggle picture-in-picture"),
    ("t", "Toggle subtitles"),
    ("s", "Schedule/cancel a recording"),
    ("w", "Browse past recordings"),
    ("d", "Delete recording (in browser)"),
    ("m", "Browse VOD movies"),
    ("u", "Browse scheduled recordings"),
    ("a", "Toggle about"),
    ("ESC", "Close popup / cancel"),
    ("?", "Toggle this help"),
]


def render_help_overlay(canvas_width: int = 1920, canvas_height: int = 1080) -> Image.Image:
    """A static keyboard-shortcuts cheat sheet (see the '?' keybinding in
    cli.py) listing every binding, so a new user can quickly orient
    themselves without reading the README. Unlike the EPG banner, this
    doesn't auto-hide -- it's meant to be read, not glanced at.
    """
    columns = 2
    rows = (len(_HELP_ENTRIES) + columns - 1) // columns

    width = min(1200, round(canvas_width * 0.65))
    row_height = max(30, round(canvas_height * 0.045))
    header_height = round(canvas_height * 0.08)
    padding = round(width * 0.03)
    col_width = (width - 2 * padding) / columns
    key_col_width = round(col_width * 0.34)

    title_font = _font("DejaVuSans-Bold.ttf", round(header_height * 0.42))
    key_font = _font("DejaVuSans-Bold.ttf", round(row_height * 0.4))
    desc_font = _font("DejaVuSans.ttf", round(row_height * 0.36))

    height = header_height + rows * row_height + round(padding * 0.6)

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

    for index, (key, description) in enumerate(_HELP_ENTRIES):
        col = index // rows
        row = index % rows
        x = padding + col * col_width
        row_mid = header_height + row * row_height + row_height / 2

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
    name_font = _font("DejaVuSans-Bold.ttf", round(width * 0.075))
    version_font = _font("DejaVuSans-Bold.ttf", round(width * 0.032))
    tagline_font = _font("DejaVuSans.ttf", round(width * 0.038))

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
