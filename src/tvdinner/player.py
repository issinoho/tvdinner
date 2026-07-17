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
            # Prefer X11 (via XWayland where needed) over native Wayland.
            # mpv draws no client-side decorations of its own and relies
            # entirely on the compositor for them under Wayland; compositors
            # that don't support server-side decorations (e.g. GNOME/Mutter)
            # leave the window completely borderless. Mutter (and most other
            # window managers) decorate XWayland clients normally, so this
            # restores a standard title bar/border. Falls back to Wayland/
            # auto if no X11 display is available at all.
            "gpu_context": "x11egl,x11vk,wayland,waylandvk,auto",
            **mpv_options,
        }
        self._mpv = mpv.MPV(**options)

    def play(self, url: str, title: str | None = None) -> None:
        if title:
            self._mpv.title = title
        self._mpv.play(url)

    def set_video_aspect(self, ratio: str | None) -> None:
        """Override the video's display aspect ratio (e.g. '4:3', '16:9',
        '2.35:1'). Pass None to restore automatic detection from the
        container/stream (mpv's video-aspect-override=no)."""
        self._mpv.video_aspect_override = ratio or "no"

    def osd_size(self) -> tuple[int, int] | None:
        """The current on-screen render size (i.e. the window/OSD size that
        overlay-add positions and scales against) -- not the decoded video's
        raw resolution, which stays fixed even as the window is resized.
        None if not yet known (e.g. immediately after play(), before mpv has
        connected to the stream)."""
        width, height = self._mpv.osd_width, self._mpv.osd_height
        return (width, height) if width and height else None

    def on_resize(self, callback: Callable[[], None]) -> None:
        """Run `callback` whenever the window/OSD is resized."""
        def handler(_name, _value):
            callback()

        self._mpv.observe_property("osd-width", handler)
        self._mpv.observe_property("osd-height", handler)

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

    def unbind_key(self, keydef: str) -> None:
        """Remove a previously registered on_key_press binding, restoring
        mpv's own default behavior for that key (e.g. LEFT/RIGHT seeking)."""
        self._mpv.unregister_key_binding(keydef)

    def wait_for_playback(self) -> None:
        """Block until the user quits mpv (closes the window, presses q,
        etc.) -- not just until the current file/stream ends. python-mpv's
        own wait_for_playback() only waits for a single 'end-file' event,
        but switching channels via play() generates exactly that event for
        the *previous* stream, which would otherwise make this return (and
        the caller tear the whole player down) on every channel switch."""
        while not self._mpv.core_shutdown:
            try:
                self._mpv.wait_for_playback()
            except KeyError:
                # python-mpv race: unregister_key_binding() (unbind_key) can
                # delete a binding's handler entry while an in-flight
                # keypress for that same binding is still being dispatched
                # on mpv's event thread, which surfaces here as a KeyError.
                # It isn't a real end-of-playback event, so keep waiting.
                continue
            except mpv.ShutdownError:
                return

    def quit(self) -> None:
        self._mpv.terminate()
