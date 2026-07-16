"""Renders a TiviMate-style EPG banner as a composited RGBA image: channel
logo, current programme with a live progress bar, description, and what's
next. The image itself is display-engine agnostic; player.py is responsible
for pushing it onto mpv's video output.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from tvdinner.epg import EpgDisplay, Programme
from tvdinner.m3u import Channel

_FONT_DIR = "/usr/share/fonts/truetype/dejavu"

_PANEL_COLOR = (14, 16, 20, 225)
_ACCENT_COLOR = (0, 176, 255, 255)
_WHITE = (245, 246, 248, 255)
_MUTED = (176, 182, 190, 255)
_BAR_TRACK = (70, 74, 82, 255)

_MAX_DESCRIPTION_LINES = 2

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


def fetch_logo(url: str | None) -> Image.Image | None:
    """Fetch and decode a channel logo, cached by URL. Returns None if there
    is no URL or it can't be fetched/decoded, so callers can fall back to an
    initials avatar."""
    if not url:
        return None
    if url not in _logo_cache:
        _logo_cache[url] = _decode_image(url)
    return _logo_cache[url]


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

    Layout is computed in two passes against a fixed set of proportions
    (`nominal_height`): first to measure how much vertical space the content
    actually needs (a 2-line description pushes "Next" further down than a
    1-line one), then to draw onto a panel sized to fit that content -- so
    text never overlaps regardless of description length.
    """
    width = max(560, min(920, round(canvas_width * 0.40)))
    nominal_height = round(width * 0.30)
    margin = round(nominal_height * 0.08)
    padding = round(nominal_height * 0.12)
    logo_size = nominal_height - 2 * padding
    text_x_offset = padding * 2 + logo_size
    text_width = width - padding - text_x_offset

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
