"""Renders a TiviMate-style EPG banner as a composited RGBA image: channel
logo, current programme with a live progress bar, description, and what's
next. The image itself is display-engine agnostic; player.py is responsible
for pushing it onto mpv's video output.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel

_FONT_DIR = "/usr/share/fonts/truetype/dejavu"

_PANEL_COLOR = (14, 16, 20, 225)
_ACCENT_COLOR = (0, 176, 255, 255)
_WHITE = (245, 246, 248, 255)
_MUTED = (176, 182, 190, 255)
_BAR_TRACK = (70, 74, 82, 255)

_MAX_DESCRIPTION_LINES = 4
_MAX_DETAILS_DESCRIPTION_LINES = 20  # generous, not a hard truncation like the small overlay's

_GRID_PANEL_COLOR = (10, 12, 16, 235)
_GRID_HEADER_COLOR = (22, 24, 30, 255)
_CELL_COLOR = (36, 40, 48, 255)
_CELL_LIVE_COLOR = (16, 68, 98, 255)
_ROW_DIVIDER = (48, 52, 60, 255)
_TUNED_ROW_TINT = (0, 176, 255, 40)
_SELECTION_BORDER_COLOR = (255, 255, 255, 255)

DEFAULT_GUIDE_WINDOW_HOURS = 3.0

_logo_cache: dict[str, Image.Image | None] = {}


def _font(name: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(f"{_FONT_DIR}/{name}", max(size, 8))
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
    except (requests.RequestException, OSError, ValueError):
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


def render_epg_overlay(
    channel: Channel,
    current: Programme | None,
    upcoming: Programme | None,
    display: EpgDisplay,
    now: datetime,
    logo: Image.Image | None = None,
    canvas_width: int = 1920,
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
    bar_h = max(4, round(nominal_height * 0.045))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    name_text = _fit_text(measure, channel.name, name_font, text_width)

    title_text = time_text = None
    description_lines: list[str] = []
    fraction = 0.0
    if current is not None:
        title_text = _fit_text(measure, current.title, title_font, text_width)
        start_local = display.to_local(current.start)
        stop_local = display.to_local(current.stop)
        time_text = f"{start_local.strftime('%H:%M')} – {stop_local.strftime('%H:%M')}"
        total_seconds = (current.stop - current.start).total_seconds()
        elapsed_seconds = (now - current.start).total_seconds()
        fraction = min(1.0, max(0.0, elapsed_seconds / total_seconds)) if total_seconds > 0 else 0.0
        if current.description:
            description_lines = _wrap_text(measure, current.description, small_font, text_width, _MAX_DESCRIPTION_LINES)

    next_text = None
    if upcoming:
        start = display.to_local(upcoming.start).strftime("%H:%M")
        next_text = _fit_text(measure, f"Next  ·  {upcoming.title} ({start})", small_font, text_width)

    def layout(draw: ImageDraw.ImageDraw | None) -> float:
        """Walk the content top-to-bottom, drawing onto `draw` if given,
        returning the y-offset (within the panel) after the last element."""
        y = padding * 0.6
        if draw:
            draw.text((text_x_offset, y), name_text, font=name_font, fill=_MUTED)
        y += nominal_height * 0.20

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

    logo_image = (logo.resize((logo_size, logo_size), Image.LANCZOS) if logo else None) or _fallback_avatar(
        channel.name, logo_size
    )
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


def visible_guide_channels(
    channels: list[Channel], epg: Epg, current_channel_id: str | None, max_rows: int = 8
) -> list[Channel]:
    """The page of channels a program guide should show: only channels with
    an EPG schedule (a real playlist can have thousands without one), in a
    window of at most `max_rows` centered on `current_channel_id`."""
    guide_channels = [c for c in channels if c.tvg_id and epg.schedule_for(c.tvg_id)]
    if not guide_channels:
        return []

    ids = [c.tvg_id for c in guide_channels]
    current_index = ids.index(current_channel_id) if current_channel_id in ids else 0
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


def selected_guide_programme(epg: Epg, channel_id: str, reference_time: datetime) -> Programme | None:
    """The programme a guide's selection cursor points to for a channel at
    a given reference time: whichever is airing then, else the next
    upcoming one, else the last known one -- so a selection is available
    whenever the channel has any schedule at all."""
    schedule = epg.schedule_for(channel_id)
    if not schedule:
        return None
    for programme in schedule:
        if programme.is_at(reference_time):
            return programme
    for programme in schedule:
        if programme.start >= reference_time:
            return programme
    return schedule[-1]


def render_program_guide(
    channels: list[Channel],
    epg: Epg,
    display: EpgDisplay,
    now: datetime,
    current_channel_id: str | None,
    canvas_width: int,
    canvas_height: int,
    window_start: datetime | None = None,
    window_hours: float = DEFAULT_GUIDE_WINDOW_HOURS,
    max_rows: int = 8,
    selected_channel_id: str | None = None,
) -> Image.Image | None:
    """Render a classic set-top-box style program guide: channels down the
    left, a timeline across the top, programme blocks sized by duration, and
    a live 'now' marker line (only drawn if `now` actually falls within the
    displayed window). Returns None if none of `channels` has any EPG
    schedule to show.

    `window_start` lets a caller page the timeline forward/back (e.g. via
    arrow keys); it defaults to `now` rounded down to the nearest half hour.

    `selected_channel_id` draws a focus border around that row's in-view
    programme (see guide_reference_time/selected_guide_programme), so a
    caller can let the user move a selection cursor and act on it (e.g.
    Enter to show full details).

    The row window is centered on `current_channel_id` (the channel being
    watched) rather than showing every channel, since a real playlist can
    have thousands of entries -- most without EPG data at all.
    """
    visible = visible_guide_channels(channels, epg, current_channel_id, max_rows)
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
        draw.text((x + 4, header_height * 0.15), display.to_local(tick).strftime("%H:%M"), font=time_font, fill=_MUTED)
        tick += timedelta(minutes=30)

    reference_time = guide_reference_time(now, window_start, window_hours)

    for row_index, channel in enumerate(visible):
        row_top = header_height + row_index * row_height
        row_bottom = row_top + row_height
        row_mid = row_top + row_height / 2

        selected_programme = (
            selected_guide_programme(epg, channel.tvg_id, reference_time)
            if channel.tvg_id == selected_channel_id
            else None
        )

        if channel.tvg_id == current_channel_id:
            draw.rectangle((0, row_top, panel_width - 1, row_bottom), fill=_TUNED_ROW_TINT)
            stripe_width = max(4, round(panel_width * 0.004))
            draw.rectangle((0, row_top, stripe_width, row_bottom), fill=_ACCENT_COLOR)

        logo_size = round(row_height * 0.68)
        logo_margin = round(row_height * 0.16)
        logo_image = fetch_image(channel.tvg_logo)
        logo_image = (logo_image.resize((logo_size, logo_size), Image.LANCZOS) if logo_image else None) or _fallback_avatar(
            channel.name, logo_size
        )
        panel.alpha_composite(logo_image, (logo_margin, round(row_mid - logo_size / 2)))

        name_x = logo_margin + logo_size + logo_margin
        name_text = _fit_text(draw, channel.name, name_font, channel_col_width - name_x - 8)
        name_bbox = draw.textbbox((0, 0), name_text, font=name_font)
        draw.text((name_x, row_mid - (name_bbox[3] - name_bbox[1]) / 2 - name_bbox[1]), name_text, font=name_font, fill=_WHITE)

        draw.line((0, row_bottom, panel_width, row_bottom), fill=_ROW_DIVIDER, width=1)

        for programme in epg.schedule_for(channel.tvg_id):
            if programme.stop <= window_start or programme.start >= window_end:
                continue
            x0, x1 = x_for(programme.start), x_for(programme.stop)
            if x1 - x0 < 2:
                continue

            live = programme.is_at(now)
            block_pad = 2
            draw.rectangle(
                (x0 + block_pad, row_top + block_pad, x1 - block_pad, row_bottom - block_pad),
                fill=_CELL_LIVE_COLOR if live else _CELL_COLOR,
            )
            title = _fit_text(draw, programme.title, title_font, (x1 - x0) - 12)
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
    channel, full title, time range, category, and the complete
    (generously wrapped, not aggressively truncated like the small banner's)
    description. Content-driven height, same two-pass approach as
    render_epg_overlay.
    """
    width = max(480, min(round(canvas_width * 0.6), canvas_width - 80))
    nominal_height = max(160, round(canvas_width * 0.15))
    margin = round(nominal_height * 0.08)
    padding = round(nominal_height * 0.12)
    logo_size = round(nominal_height * 0.5)
    text_x = padding * 2 + logo_size
    text_width = width - padding - text_x

    name_font = _font("DejaVuSans.ttf", round(nominal_height * 0.1))
    title_font = _font("DejaVuSans-Bold.ttf", round(nominal_height * 0.155))
    meta_font = _font("DejaVuSans.ttf", round(nominal_height * 0.095))
    body_font = _font("DejaVuSans.ttf", round(nominal_height * 0.09))

    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    name_text = _fit_text(measure, channel.name, name_font, text_width)
    title_lines = _wrap_text(measure, programme.title, title_font, text_width, 3)

    start_local = display.to_local(programme.start)
    stop_local = display.to_local(programme.stop)
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

    logo_image = (logo.resize((logo_size, logo_size), Image.LANCZOS) if logo else None) or _fallback_avatar(
        channel.name, logo_size
    )
    panel.alpha_composite(logo_image, (padding, padding))

    layout(panel_draw)

    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (margin, margin, margin + width - 1, margin + height - 1), radius=height * 0.06, fill=(0, 0, 0, 190)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=height * 0.04)))
    canvas.alpha_composite(panel, (margin, margin))

    return canvas
