"""Renders a TiviMate-style EPG banner as a composited RGBA image: channel
logo, current programme with a live progress bar, description, and what's
next. The image itself is display-engine agnostic; player.py is responsible
for pushing it onto mpv's video output.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import logging
from datetime import datetime, timedelta
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel

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

DEFAULT_GUIDE_WINDOW_HOURS = 3.0

_logo_cache: dict[str, Image.Image | None] = {}


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


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    """Truncate `text` with an ellipsis so it fits within max_width pixels."""
    text = text.strip()
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
    words = text.split()
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
    text = _initials(name)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    draw.text(((size - tw) / 2 - left, (size - th) / 2 - top), text, font=font, fill=_WHITE)
    return image


def _decode_image(url: str) -> Image.Image | None:
    try:
        if url.startswith(("http://", "https://")):
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.content
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


def _logo_tile(logo: Image.Image, size: int) -> Image.Image:
    """Place a fetched channel logo on a light rounded tile, sized (size,
    size). Many real-world channel logos are dark line-art on a fully
    transparent background -- designed for a light UI/print -- and simply
    disappear when composited directly onto our dark panels. The fallback
    initials avatar isn't run through this since it already has its own
    (colored) background."""
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, size - 1, size - 1), radius=size * 0.18, fill=_LOGO_TILE_COLOR)
    inset = round(size * 0.14)
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


def render_epg_overlay(
    channel: Channel,
    current: Programme | None,
    upcoming: Programme | None,
    display: EpgDisplay,
    now: datetime,
    logo: Image.Image | None = None,
    canvas_width: int = 1920,
    badges: list[str] | None = None,
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
    name_text = _fit_text(measure, channel.name, name_font, text_width)

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
            draw.text((text_x_offset, y), name_text, font=name_font, fill=_MUTED)
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
) -> Image.Image | None:
    """Render a classic set-top-box style program guide: channels down the
    left, a timeline across the top, programme blocks sized by duration, and
    a live 'now' marker line (only drawn if `now` actually falls within the
    displayed window). Returns None only if `channels` is empty -- if none of
    them has an EPG schedule, the channel list itself is still shown (with
    blank timelines) so the guide remains usable for switching channels
    (see visible_guide_channels).

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
    title_font = _font("DejaVuSans-Bold.ttf", round(min(canvas_width * 0.0105, row_height * 0.34)))

    panel = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    corner_radius = panel_height * 0.025
    draw.rounded_rectangle((0, 0, panel_width - 1, panel_height - 1), radius=corner_radius, fill=_GRID_PANEL_COLOR)

    draw.rectangle((0, 0, panel_width - 1, header_height), fill=_GRID_HEADER_COLOR)
    draw.text(
        (round(panel_width * 0.015), header_height * 0.28),
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
        fetched_logo = fetch_image(channel.tvg_logo)
        logo_image = _logo_tile(fetched_logo, logo_size) if fetched_logo else _fallback_avatar(channel.name, logo_size)
        panel.alpha_composite(logo_image, (logo_margin, round(row_mid - logo_size / 2)))

        name_x = logo_margin + logo_size + logo_margin
        name_text = _fit_text(draw, channel.name, name_font, channel_col_width - name_x - 8)
        name_bbox = draw.textbbox((0, 0), name_text, font=name_font)
        draw.text((name_x, row_mid - (name_bbox[3] - name_bbox[1]) / 2 - name_bbox[1]), name_text, font=name_font, fill=_WHITE)

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)

        for programme in epg.schedule_for(channel.tvg_id, channel.tvg_name or channel.name):
            corrected_start = programme.start + shift
            corrected_stop = programme.stop + shift
            if corrected_stop <= window_start or corrected_start >= window_end:
                continue
            x0, x1 = x_for(corrected_start), x_for(corrected_stop)
            if x1 - x0 < 2:
                continue

            live = corrected_start <= now < corrected_stop
            block_pad = 2
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
                draw.text((text_x, y), programme.category, font=meta_font, fill=_ACCENT_COLOR)
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
