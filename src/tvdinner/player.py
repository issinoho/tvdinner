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
from datetime import datetime
from pathlib import Path
from typing import Callable

import mpv
from PIL import Image, ImageChops

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    DEFAULT_RECORDINGS_DIR = Path(os.environ.get("USERPROFILE", Path.home())) / "Videos" / "tvdinner"
else:
    DEFAULT_RECORDINGS_DIR = Path.home() / "Videos" / "tvdinner"

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # mpv's win32 video-output backend always registers its window under
    # this class name (video/out/w32_common.c) -- there's no mpv property
    # that hands back the output window's handle (window-id is for
    # *embedding* mpv into a foreign window, the opposite direction), so
    # matching on this is the only way to find it from outside.
    _MPV_WINDOW_CLASS = "mpv"
    _VK_MENU = 0x12  # Alt
    _KEYEVENTF_KEYUP = 0x2

    def _find_own_window(class_name: str) -> int | None:
        """First top-level window in this process matching `class_name`."""
        pid = os.getpid()
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd, _lparam):
            owner_pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if owner_pid.value != pid:
                return True
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
            if buf.value == class_name:
                found.append(hwnd)
                return False
            return True

        ctypes.windll.user32.EnumWindows(_enum_proc, 0)
        return found[0] if found else None

    def _force_foreground_window(hwnd: int) -> None:
        """Windows silently ignores SetForegroundWindow from a process that
        isn't already foreground -- and unlike most Linux window managers
        under X11, Windows doesn't auto-focus a newly created window for a
        background process, so mpv's own window opens while the console
        tvdinner was launched from keeps keyboard focus. The documented
        exception is a process that has generated recent input itself;
        synthesizing a harmless Alt keypress first satisfies that check.
        This is the standard workaround (see SetForegroundWindow's "Remarks"
        in the Win32 API docs)."""
        ctypes.windll.user32.keybd_event(_VK_MENU, 0, 0, 0)
        ctypes.windll.user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
        ctypes.windll.user32.SetForegroundWindow(hwnd)

DEFAULT_LIVE_BUFFER_MINUTES = 10.0

# Bounds how long mpv will wait on a stalled network read before giving up
# and firing the end-file/ERROR event that cli.py's automatic-reconnect
# handler listens for -- without this, a connection that stops sending
# bytes (as opposed to one that's cleanly refused/rejected) can sit in
# mpv's internal buffering state indefinitely and never surface as an
# error at all, silently defeating reconnection.
_NETWORK_TIMEOUT_SECONDS = 15

# ffmpeg/lavf-level reconnection for the http(s) stream backend IPTV feeds
# use -- lets mpv itself recover from brief mid-stream network blips inside
# a single connection, without ever generating the end-file/ERROR event
# cli.py's reconnect handler reacts to. Complementary to, not a replacement
# for, that app-level handler: this only covers blips mpv's own stream
# layer can ride out; a server that's genuinely down still ends up
# exhausting this and reaching end-file/ERROR as before. reconnect_delay_max
# caps ffmpeg's own internal backoff for that -- confirmed live that without
# capping it, ffmpeg's default (120s) silently stacks underneath cli.py's
# own retry schedule, since mpv doesn't fire end-file/ERROR until ffmpeg
# gives up first, making a "dead server" take minutes to surface instead of
# cli.py's intended ~1 minute of backoff.
#
# Deliberately excludes reconnect_at_eof: confirmed live against a real
# HLS stream (AES-128-encrypted segments via a "crypto+https://" URL) that
# it causes a silent, permanent hang -- no window ever appears, no
# end-file/ERROR event ever fires, playback position never advances again,
# with no recovery even after 60+ seconds. reconnect_at_eof tells ffmpeg to
# treat a stream reaching EOF as an error needing reconnection, which is
# right for one genuinely continuous live connection but wrong for HLS:
# each segment is its own discrete HTTP download that's *supposed* to hit
# EOF normally once it finishes, and HLS -- not a single unbroken
# connection -- is how the large majority of real-world IPTV streams are
# actually delivered. reconnect_streamed/reconnect_on_network_error/
# reconnect_delay_max were each individually confirmed live to work
# correctly against that same stream with no such hang.
_STREAM_RECONNECT_OPTS = "reconnect_streamed=1,reconnect_on_network_error=1,reconnect_delay_max=2"

# mpv's own internal log (network/demuxer/ffmpeg messages -- TLS handshake
# failures, HTTP status codes, DNS errors, stalled-read warnings, lavf's own
# reconnect attempts) is otherwise invisible to us: without forwarding it,
# a stream that stalls or fails deep inside mpv/ffmpeg only ever surfaces
# here as an opaque 'end-file/ERROR' event (or, worse, nothing at all for
# as long as _STREAM_RECONNECT_OPTS's own reconnect attempts keep retrying
# under the hood), making a slow-dying stream indistinguishable from a
# genuine app hang. "info" is the threshold requested *from mpv* (mirrors
# roughly what mpv's own terminal output shows by default); the mapping
# below then re-levels each message for our logger, so "warn"/"error" still
# stand out and root's default INFO level (see log.py) doesn't get flooded
# with per-frame "v"/"debug"/"trace" chatter.
_MPV_LOG_REQUEST_LEVEL = "info"
_MPV_LOG_LEVEL_TO_PYTHON = {
    "fatal": logging.CRITICAL,
    "error": logging.ERROR,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "v": logging.DEBUG,
    "debug": logging.DEBUG,
    "trace": logging.DEBUG,
}

# mpv has no direct time-based back-buffer option -- these are byte sizes,
# generously assuming up to ~13 Mbps so `minutes` of real IPTV playback
# (almost always lower bitrate) comfortably fits. Confirmed empirically
# (not just from the docs) that pausing keeps mpv's demuxer cache filling
# in the background and resuming continues from the paused position
# rather than jumping to the live edge -- that behavior is what actually
# makes "pause live TV, then rewind/resume" work; cli.py's own pause
# timer is what precisely enforces the `minutes` limit, regardless of
# how accurate this byte estimate turns out to be for a given stream.
_LIVE_BUFFER_BYTES_PER_MINUTE = 100 * 1024 * 1024
_LIVE_BUFFER_FORWARD_HEADROOM_BYTES = 200 * 1024 * 1024


def live_buffer_mpv_options(minutes: float) -> dict:
    """mpv options sizing the demuxer cache to comfortably hold `minutes`
    of buffered live playback, for Player(**live_buffer_mpv_options(...))."""
    back_bytes = round(minutes * _LIVE_BUFFER_BYTES_PER_MINUTE)
    return {
        "demuxer_max_back_bytes": back_bytes,
        "demuxer_max_bytes": back_bytes + _LIVE_BUFFER_FORWARD_HEADROOM_BYTES,
        "demuxer_seekable_cache": "yes",
    }


@dataclass
class RecordingFile:
    path: Path
    label: str
    recorded_at: datetime  # naive local time -- matches cli.recording_filename's datetime.now()
    size_bytes: int


def list_recordings(directory: Path) -> list[RecordingFile]:
    """Previously saved recordings (see cli.recording_filename for the
    '<label>_<timestamp>.ts' naming this parses back), newest first. Any
    ".ts" file that doesn't match tvdinner's own naming pattern is skipped
    silently -- the directory may hold other things a user put there. A
    missing directory just means no recordings yet, not an error."""
    if not directory.is_dir():
        return []

    recordings = []
    for path in directory.glob("*.ts"):
        label, sep, timestamp_str = path.stem.rpartition("_")
        if not sep:
            continue
        try:
            recorded_at = datetime.strptime(timestamp_str, "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        try:
            size_bytes = path.stat().st_size
        except OSError:
            continue
        recordings.append(RecordingFile(path=path, label=label, recorded_at=recorded_at, size_bytes=size_bytes))

    recordings.sort(key=lambda r: r.recorded_at, reverse=True)
    return recordings

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

# Picture-in-picture: scale relative to the video's native resolution
# (mpv's window-scale, not a fixed pixel size), and a corner position 10px
# in from the screen's right/bottom edge (mpv --geometry's "-x-y" anchors
# to the far edge -- see `man mpv`).
_PIP_WINDOW_SCALE = 0.25
_PIP_GEOMETRY = "-10-10"


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
            "network_timeout": _NETWORK_TIMEOUT_SECONDS,
            "stream_lavf_o": _STREAM_RECONNECT_OPTS,
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
        self._mpv = mpv.MPV(log_handler=self._on_mpv_log, loglevel=_MPV_LOG_REQUEST_LEVEL, **options)
        logger.info("mpv initialized (version=%s)", self._mpv.mpv_version)

        if sys.platform == "win32":
            # Without this, tvdinner's own keybindings (?, g, etc. -- all
            # registered against mpv's video-output window) silently do
            # nothing until the user manually clicks that window, because it
            # never receives OS keyboard focus on open (see
            # _force_foreground_window). Only needs doing once: after the
            # window has focus for the first time, it keeps it like any
            # other window until something else steals it.
            self._focus_grabbed = False

            @self._mpv.event_callback("file-loaded")
            def _grab_window_focus(_event):
                if self._focus_grabbed:
                    return
                hwnd = _find_own_window(_MPV_WINDOW_CLASS)
                if hwnd:
                    self._focus_grabbed = True
                    _force_foreground_window(hwnd)

    def _on_mpv_log(self, level: str, prefix: str, text: str) -> None:
        logger.log(_MPV_LOG_LEVEL_TO_PYTHON.get(level, logging.INFO), "mpv[%s] %s", prefix, text.rstrip())

    def play(self, url: str, title: str | None = None, start: float | None = None) -> None:
        if title:
            self._mpv.title = title
        if start:
            # A per-file 'start' option on the loadfile command itself,
            # rather than seeking after the fact -- avoids a race with
            # mpv not having loaded the file yet right after play().
            self._mpv.loadfile(url, start=str(start))
        else:
            self._mpv.play(url)

    def set_video_aspect(self, ratio: str | None) -> None:
        """Override the video's display aspect ratio (e.g. '4:3', '16:9',
        '2.35:1'). Pass None to restore automatic detection from the
        container/stream (mpv's video-aspect-override=no)."""
        self._mpv.video_aspect_override = "no" if ratio is None else ratio

    @property
    def has_subtitle_track(self) -> bool:
        """Whether the current stream has at least one subtitle track --
        e.g. UK DVB broadcasts commonly carry one (dvb_subtitle), but mpv
        doesn't auto-select it (sid stays unset) the way it does for audio/
        video, so there's nothing to show until something explicitly picks
        one (see set_subtitles_enabled)."""
        return any(track.get("type") == "sub" for track in (self._mpv.track_list or []))

    @property
    def subtitles_enabled(self) -> bool:
        return bool(self._mpv.sub_visibility) and self._mpv.sid not in (False, None, "no")

    def set_subtitles_enabled(self, enabled: bool) -> None:
        """Turn subtitles on/off. Enabling selects a subtitle track only if
        nothing is already selected -- so toggling off and back on doesn't
        lose a track the user (or mpv's own cycle-sub key, 'j' by default
        and left alone here) had already picked. Prefers an English-tagged
        track over track_list's own order when picking -- confirmed live
        (a YouTube video with Arabic captions listed first) that track_list
        order is source-defined, not a language ranking, so picking
        blindly-first surfaces whatever language the source happened to
        list first rather than a sensible default. Falls back to the
        actual first subtitle track if none is tagged English (or none are
        tagged at all). Picks the track by its explicit id -- confirmed
        live that setting mpv's sid property to the "auto" pseudo-value
        (which does work as a startup default via --sid=auto) is a no-op
        at runtime through this property interface."""
        if enabled and self._mpv.sid in (False, None, "no"):
            subs = [track for track in (self._mpv.track_list or []) if track.get("type") == "sub"]
            first_sub = next(
                (track for track in subs if str(track.get("lang") or "").lower() in ("en", "eng")), None
            ) or (subs[0] if subs else None)
            if first_sub is not None:
                self._mpv.sid = first_sub["id"]
        self._mpv.sub_visibility = enabled

    def set_picture_in_picture(self, enabled: bool) -> None:
        """Toggle a small, always-on-top, borderless corner window, using
        mpv's own ontop/border/window-scale/geometry properties -- the same
        kind of direct property toggle set_video_aspect uses for aspect
        ratio. Disabling restores a normal, bordered, non-topmost window at
        the video's native size -- any fullscreen state from before
        enabling PiP is deliberately not restored (tried once; the window
        manager would report fullscreen=True but never actually hand
        keyboard focus back to the window, leaving every keybinding
        unresponsive until quit -- windowed is at least reliably usable).

        Window *position* relies on the window manager honoring mpv's
        --geometry request, which some Wayland compositors ignore for
        security/UX reasons (they, not the client, own window placement) --
        the window will still shrink and stay on top even if it doesn't
        actually relocate."""
        self._mpv.fullscreen = False
        self._mpv.ontop = enabled
        self._mpv.border = not enabled
        self._mpv.window_scale = _PIP_WINDOW_SCALE if enabled else 1.0
        self._mpv.geometry = _PIP_GEOMETRY if enabled else ""

    def start_recording(self, path: str) -> None:
        """Start dumping the current stream's raw incoming bytes to `path`
        (mpv's stream-record) -- a straight copy in whatever container/codec
        the source stream already uses, not a re-encode or re-mux."""
        self._mpv.stream_record = path

    def stop_recording(self) -> None:
        """Stop any recording started by start_recording()."""
        self._mpv.stream_record = ""

    @property
    def is_recording(self) -> bool:
        return bool(self._mpv.stream_record)

    def playback_position(self) -> tuple[float, float] | None:
        """Current playback position and total duration, in seconds -- for
        the recording-playback overlay's progress bar (a live channel has
        no fixed duration, so this is only meaningful for local file
        playback). None if either isn't known yet (e.g. immediately after
        play(), before mpv has probed the file) -- or if mpv's core has
        already shut down (e.g. the user just quit via its own default
        'q'), which is equally "not available", not a real error worth
        surfacing to a caller like cli.py's shutdown cleanup."""
        try:
            position, duration = self._mpv.time_pos, self._mpv.duration
        except mpv.ShutdownError:
            return None
        if position is None or duration is None:
            return None
        return (position, duration)

    @property
    def is_paused(self) -> bool:
        return bool(self._mpv.pause)

    def set_paused(self, paused: bool) -> None:
        """Pause or resume playback. For a live channel (with a generously
        sized demuxer cache -- see live_buffer_mpv_options), pausing keeps
        buffering in the background rather than stalling the connection,
        and resuming continues from the paused position instead of
        jumping to the live edge -- this is what actually implements
        'pause live TV, then rewind/resume'."""
        self._mpv.pause = paused

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
                # cli.py's own "Playback error ... reconnecting" warning names
                # the channel but not the cause -- mpv's error code is the
                # coarse "why" (e.g. "loading failed", "nothing to play")
                # available immediately, without waiting on the log_handler
                # messages (see _on_mpv_log) that carry the finer-grained one.
                logger.warning("mpv end-file error: %s", mpv.ErrorCode.human_readable(event.data.error))
                callback()

    def on_playback_started(self, callback: Callable[[], None]) -> None:
        """Run `callback` whenever a file/stream has finished loading and
        begun playing -- mpv's 'file-loaded' event. cli.py uses this as the
        signal that an automatic reconnect attempt (see on_playback_error)
        actually succeeded, as opposed to just having been sent."""
        @self._mpv.event_callback("file-loaded")
        def _handler(_event):
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
