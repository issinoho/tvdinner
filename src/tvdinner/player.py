"""Playback engine wrapper around libmpv (via python-mpv).

Kept as a thin wrapper so a future GUI can reuse the same Player class and
embed mpv's video output into a widget by passing wid= through mpv_options,
instead of letting it open its own top-level window as it does today.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import warnings
from dataclasses import dataclass
from typing import Callable

import mpv
from PIL import Image, ImageChops

logger = logging.getLogger(__name__)

# The same python-mpv key-binding race documented on Player.wait_for_playback
# (unregister_key_binding deleting a handler entry while an in-flight keypress
# for that binding is still being dispatched) can also surface here: mpv.py's
# own event loop (_loop/_enqueue_exceptions) catches the resulting KeyError,
# logs this warning, and moves on to the next event -- it's already fully
# non-fatal (confirmed by reading mpv.py: the dispatch loop continues
# normally), so this only suppresses an alarming-looking but harmless
# traceback. The message pattern is specific to stale key-binding dispatch
# (missing dict keys for this race always look like 'py_kb_<hex>'), so a
# genuine error in unrelated event-loop code would still surface normally.
warnings.filterwarnings(
    "ignore",
    message=r"Unhandled exception on python-mpv event loop: 'py_kb_",
    category=RuntimeWarning,
)


_UHD_HEIGHT = 2160
_HDR_LABELS = {"pq": "HDR10", "hlg": "HLG"}


@dataclass
class StreamInfo:
    """Current video/audio stream quality, for the OSD's quality badges.
    Any field can be None -- mpv may not have probed that part of the
    stream yet (e.g. right after a channel switch), or the stream may not
    have that track at all (e.g. an audio-only stream has no resolution)."""

    resolution: str | None = None  # e.g. "1080p", "4K"
    video_codec: str | None = None  # e.g. "H.264"
    fps: str | None = None  # e.g. "29.97fps"
    hdr: str | None = None  # e.g. "HDR10", "HLG"
    audio_codec: str | None = None  # e.g. "AAC"
    audio_channels: str | None = None  # e.g. "Stereo", "5.1"


def _short_codec_name(raw: str | None) -> str | None:
    # mpv's codec properties are verbose/descriptive, e.g.
    # "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10" or "AAC (Advanced Audio
    # Coding)" -- badges just want the short name at the front of either.
    if not raw:
        return None
    return raw.split(" / ")[0].split(" (")[0].strip() or None


def _format_fps(fps: float) -> str | None:
    if not fps:
        return None
    text = f"{fps:.2f}".rstrip("0").rstrip(".")
    return f"{text}fps"


def _format_channels(channels: str | None) -> str | None:
    if not channels:
        return None
    # mpv reports layouts like "stereo", "mono", "5.1" -- numeric layouts
    # (already display-ready) are left alone, word ones get capitalized.
    return channels if channels[0].isdigit() else channels.capitalize()


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
            # Without this, a channel whose stream fails to open (dead
            # server, 403, etc.) leaves mpv with no video track and thus no
            # window at all -- and with no window, mpv can't receive any
            # more keypresses, silently stranding the app with no way to
            # pick another channel. Keeping the window up regardless of
            # whether anything is actually playing keeps input alive.
            "force_window": True,
        }
        if sys.platform.startswith("linux"):
            # Prefer X11 (via XWayland where needed) over native Wayland.
            # mpv draws no client-side decorations of its own and relies
            # entirely on the compositor for them under Wayland; compositors
            # that don't support server-side decorations (e.g. GNOME/Mutter)
            # leave the window completely borderless. Mutter (and most other
            # window managers) decorate XWayland clients normally, so this
            # restores a standard title bar/border. Falls back to Wayland/
            # auto if no X11 display is available at all. These context
            # names don't exist on non-Linux builds of libmpv at all --
            # passing them there is a hard mpv_set_option_string() error,
            # not a graceful skip, so this is Linux-only.
            options["gpu_context"] = "x11egl,x11vk,wayland,waylandvk,auto"
        options.update(mpv_options)
        self._mpv = mpv.MPV(**options)
        logger.info("mpv initialized (version=%s)", self._mpv.mpv_version)

    def play(self, url: str, title: str | None = None) -> None:
        if title:
            self._mpv.title = title
        self._mpv.play(url)

    def set_video_aspect(self, ratio: str | None) -> None:
        """Override the video's display aspect ratio (e.g. '4:3', '16:9',
        '2.35:1'). Pass None to restore automatic detection from the
        container/stream (mpv's video-aspect-override=no). Pass 'stretch'
        to fill the window/screen exactly, distorting the image if needed
        -- handled separately from a fixed ratio (mpv's keepaspect=no)
        since it needs to track the window's own, possibly-resized shape
        rather than a constant one."""
        self._mpv.keepaspect = ratio != "stretch"
        self._mpv.video_aspect_override = "no" if ratio in (None, "stretch") else ratio

    def osd_size(self) -> tuple[int, int] | None:
        """The current on-screen render size (i.e. the window/OSD size that
        overlay-add positions and scales against) -- not the decoded video's
        raw resolution, which stays fixed even as the window is resized.
        None if not yet known (e.g. immediately after play(), before mpv has
        connected to the stream)."""
        width, height = self._mpv.osd_width, self._mpv.osd_height
        return (width, height) if width and height else None

    def stream_info(self) -> StreamInfo | None:
        """Current video/audio stream quality (resolution, codecs, fps,
        HDR, channel layout) for the OSD's quality badges. None if mpv
        hasn't probed either track yet at all (e.g. immediately after
        play(), before the demuxer has connected)."""
        video_params = self._mpv.video_params
        audio_params = self._mpv.audio_params
        if video_params is None and audio_params is None:
            return None

        resolution = hdr = None
        if video_params:
            height = video_params.get("dh") or video_params.get("h")
            if height:
                resolution = "4K" if height >= _UHD_HEIGHT else f"{height}p"
            hdr = _HDR_LABELS.get(video_params.get("gamma"))

        return StreamInfo(
            resolution=resolution,
            video_codec=_short_codec_name(self._mpv.video_codec),
            fps=_format_fps(self._mpv.container_fps),
            hdr=hdr,
            audio_codec=_short_codec_name(self._mpv.audio_codec),
            audio_channels=_format_channels(audio_params.get("channels") if audio_params else None),
        )

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

    def on_playback_error(self, callback: Callable[[], None]) -> None:
        """Run `callback` whenever the current file fails to open/play (e.g.
        an unreachable or rejected stream) -- an 'end-file' event with
        reason=error, as opposed to one generated by a normal channel switch
        or the end of a stream."""
        @self._mpv.event_callback("end-file")
        def _handler(event):
            if event.data.reason == mpv.MpvEventEndFile.ERROR:
                callback()

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
