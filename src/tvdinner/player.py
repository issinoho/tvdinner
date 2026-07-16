"""Playback engine wrapper around libmpv (via python-mpv).

Kept as a thin wrapper so a future GUI can reuse the same Player class and
embed mpv's video output into a widget by passing wid= through mpv_options,
instead of letting it open its own top-level window as it does today.
"""

from __future__ import annotations

import os
import tempfile
from typing import Callable

import mpv
from PIL import Image, ImageChops


def _to_premultiplied_bgra(image: Image.Image) -> bytes:
    """mpv's overlay-add command requires raw BGRA bytes with premultiplied
    alpha (see `man mpv` overlay-add): each color component must already be
    scaled by alpha/255."""
    rgba = image.convert("RGBA")
    r, g, b, a = rgba.split()
    premultiplied = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a), ImageChops.multiply(r, a), a))
    return premultiplied.tobytes()


class Player:
    def __init__(self, **mpv_options):
        options = {
            "input_default_bindings": True,
            "input_vo_keyboard": True,
            "osc": True,
            **mpv_options,
        }
        self._mpv = mpv.MPV(**options)

    def play(self, url: str, title: str | None = None) -> None:
        if title:
            self._mpv.title = title
        self._mpv.play(url)

    def video_size(self) -> tuple[int, int] | None:
        """The decoded video's resolution, or None if not yet known (e.g.
        immediately after play() before the stream has connected)."""
        width, height = self._mpv.width, self._mpv.height
        return (width, height) if width and height else None

    def show_text(self, text: str, duration_ms: int = 5000) -> None:
        """Overlay text on the video output (mpv's OSD)."""
        self._mpv.show_text(text, str(duration_ms))

    def show_overlay(self, image: Image.Image, x: int = 0, y: int = 0, overlay_id: int = 0) -> None:
        """Composite an RGBA image onto the video output at (x, y). Calling
        again with the same overlay_id replaces it; mpv copies the pixel
        data synchronously, so the temp file is removed immediately after."""
        data = _to_premultiplied_bgra(image)
        width, height = image.size
        fd, path = tempfile.mkstemp(suffix=".bgra")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            self._mpv.overlay_add(overlay_id, x, y, path, 0, "bgra", width, height, width * 4)
        finally:
            os.unlink(path)

    def clear_overlay(self, overlay_id: int = 0) -> None:
        self._mpv.overlay_remove(overlay_id)

    def on_key_press(self, keydef: str, callback: Callable[[], None]) -> None:
        """Run `callback` whenever `keydef` is pressed in the mpv window."""
        self._mpv.on_key_press(keydef)(callback)

    def wait_for_playback(self) -> None:
        """Block until the current stream finishes or the user quits mpv."""
        self._mpv.wait_for_playback()

    def quit(self) -> None:
        self._mpv.terminate()
