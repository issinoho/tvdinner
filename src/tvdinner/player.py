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
import threading
import time
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
# See _on_mpv_log -- dropped outright rather than leveled, since it's
# ffmpeg's own per-NAL-unit chatter, not a per-message log line meant
# for a human.
_DOLBY_VISION_RPU_WARNING = "Multiple Dolby Vision RPUs found in one AU. Skipping previous."

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

# How long to keep the real stderr fd redirected *after* each file-loaded
# event (see _suppress_hwdec_probe_stderr) -- the dlopen probing itself is
# near-instant, but it doesn't happen when the redirect for that file is put
# in place, it happens once mpv actually starts decoding that file's first
# frame, which for a live network stream can trail the start of loading by
# much more than a couple of seconds (confirmed live: a slow-starting stream
# competing for bandwidth with a large simultaneous EPG download pushed it
# well past an earlier fixed post-construction timer). This is just a safety
# buffer past that event, not the primary wait.
_HWDEC_PROBE_STDERR_POST_LOAD_SECONDS = 2.0

# Ceiling on how long to keep stderr redirected if a given file never
# actually loads (e.g. a stream that never connects) -- past this, restore
# unconditionally so a dead stream doesn't leave the terminal silently
# redirected indefinitely.
_HWDEC_PROBE_STDERR_MAX_SUPPRESS_SECONDS = 20.0


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


def capture_recording_thumbnail(
    video_path: Path, *, seek_seconds: float = 5.0, timeout_seconds: float = 15.0
) -> bytes | None:
    """Grab a single JPEG-encoded frame from `video_path` (a saved
    recording) a few seconds in, via a short-lived, windowless mpv
    instance using the vo=image driver. Reuses libmpv -- already a hard
    requirement -- rather than shelling out to a standalone mpv/ffmpeg
    CLI binary, which isn't guaranteed to exist on every platform
    tvdinner ships to: the Windows build bundles only libmpv-2.dll, not
    a full mpv.exe (see windows/tvdinner_entry.py and
    .github/workflows/release.yml's libmpv download step).

    Returns None on any failure -- missing/corrupt file, timeout, no
    frame produced -- rather than raising, so a thumbnail generation
    failure never surfaces as anything worse than a placeholder image
    to whatever's calling this (see overlay.py's history browser
    thumbnail support). Confirmed live that both a wait_for_playback
    timeout and a missing input file return/raise promptly rather than
    hanging, so this is safe to call from a background thread without
    its own extra watchdog."""
    with tempfile.TemporaryDirectory(prefix="tvdinner-thumb-") as tmpdir:
        player = mpv.MPV(
            vo="image",
            vo_image_outdir=tmpdir,
            vo_image_format="jpg",
            frames=1,
            start=seek_seconds,
            really_quiet=True,
        )
        try:
            player.play(str(video_path))
            player.wait_for_playback(timeout=timeout_seconds)
        except Exception as exc:
            logger.warning("Could not capture thumbnail for %s: %s", video_path, exc)
        finally:
            player.terminate()

        produced = sorted(Path(tmpdir).glob("*.jpg"))
        if not produced:
            return None
        return produced[0].read_bytes()

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
    hdr: str | None = None  # e.g. "HDR10", "HDR10+", "Dolby Vision", "HLG"
    audio_codec: str | None = None  # e.g. "AAC"
    audio_channels: str | None = None  # e.g. "Stereo", "5.1"


def _hdr_label(video_params: dict) -> str | None:
    """The OSD's HDR quality badge text, from mpv's video-params. Dolby
    Vision and HDR10+ both use the same "pq" (SMPTE ST 2084) transfer
    function as static HDR10 -- gamma alone can't tell them apart, so
    this checks two more specific signals, in order:
      - Dolby Vision: mpv reports colormatrix as the literal string
        "dolbyvision" instead of a normal YCbCr matrix name whenever DV
        metadata is present (confirmed live against a real DV profile
        8.1 stream).
      - HDR10+: video-params/scene-max-r (also -g/-b, checking one is
        enough) is only ever populated from real SMPTE ST2094-40
        dynamic metadata, per mpv's own manual -- unlike max-pq-y/
        avg-pq-y, which are mpv's own per-frame peak-detection stats
        and get populated for *any* PQ content regardless of whether
        the source actually carries dynamic metadata, so those can't
        be used as the signal.
    Anything else with a "pq" transfer is plain static HDR10; "hlg" is
    HLG; anything else isn't HDR at all.
    """
    gamma = video_params.get("gamma")
    if gamma == "hlg":
        return "HLG"
    if gamma != "pq":
        return None
    if video_params.get("colormatrix") == "dolbyvision":
        return "Dolby Vision"
    if video_params.get("scene-max-r") is not None:
        return "HDR10+"
    return "HDR10"


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
            # libmpv defaults to software decoding and a fast/basic scaler
            # unless told otherwise -- unlike the standalone mpv binary, it
            # doesn't auto-load the user's own mpv.conf, so these are the
            # only place either setting can come from. auto-safe enables
            # hardware decoding only where mpv itself considers it safe
            # (falling back to software otherwise), and gpu-hq is mpv's own
            # bundled high-quality-scaling profile (ewa_lanczos, sigmoid
            # upscaling, debanding, etc.).
            "hwdec": "auto-safe",
            "profile": "gpu-hq",
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

        if sys.platform != "win32":
            self._suppress_hwdec_probe_stderr()

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
        if prefix == "ffmpeg/video" and _DOLBY_VISION_RPU_WARNING in text:
            # ffmpeg's own HEVC parser logs this once per duplicate RPU
            # NAL unit it finds within a single access unit -- confirmed
            # live against a real 4K Dolby Vision file (Mission:
            # Impossible - The Final Reckoning) that this fires dozens of
            # times a second right at playback start and has no bearing
            # on actual decode correctness (video/HDR played back fine
            # regardless): ffmpeg already recovers by discarding the
            # stale RPU, this is purely informational. Dropped outright
            # rather than downgraded to debug, same as the py_kb_ event-
            # loop warning above -- at hundreds of lines per session it
            # would otherwise drown out everything else in the log file.
            return
        logger.log(_MPV_LOG_LEVEL_TO_PYTHON.get(level, logging.INFO), "mpv[%s] %s", prefix, text.rstrip())

    def _suppress_hwdec_probe_stderr(self) -> None:
        """hwdec=auto-safe makes mpv probe every hardware-decode backend
        compiled into ffmpeg on first use, and on a machine missing the
        proprietary NVIDIA stack, two of those probes (CUDA's and VDPAU's
        own dlopen wrappers) print straight to the process's real stderr
        fd via a bare fprintf -- confirmed live that neither line ever
        reaches _on_mpv_log or the log file, so there's no way to catch or
        re-level them from Python's logging side; the only way to keep
        them off the terminal is redirecting the fd itself. Harmless
        either way (playback falls back to software decoding without
        issue), but alarming to see unprompted on every launch.

        Redirecting fd 2 wholesale also silently ate cli.py's own
        `print(..., file=sys.stderr)` progress messages (e.g. "Loading EPG
        data...") when they landed inside the same window -- confirmed
        live. `sys.stderr` is CPython's io wrapper *around* fd 2 at
        startup, not a separate channel, so redirecting the fd redirects
        both. Fixed by giving Python's own sys.stderr a fresh duplicate fd
        that stays connected to the real terminal for the duration, and
        only swinging the raw, numbered fd 2 (what native code's fprintf
        always targets directly, bypassing Python's io layer entirely) at
        /dev/null.

        An earlier version of this only redirected once, around the very
        first file, on the assumption that ffmpeg caches a failed hwdec
        probe for the rest of the process. Confirmed live that's false (at
        least on some ffmpeg/driver combinations): a plain channel switch,
        long after the first file's redirect had already been restored,
        re-triggered the exact same raw CUDA/VDPAU probe lines. So this now
        re-arms on every file, via a persistent `start-file` handler,
        rather than running once from Player() construction -- `start-file`
        fires before mpv begins loading that entry, comfortably ahead of
        the probe. Restoration is tied to that same file's `file-loaded`
        event (plus a short buffer), not a fixed delay, since the probe
        fires once mpv actually starts decoding, which for a live network
        stream can trail the start of loading by much more than a couple
        of seconds (confirmed live: a slow-starting stream competing for
        bandwidth with a large simultaneous EPG download pushed it well
        past a flat post-construction timer this used to use).
        _HWDEC_PROBE_STDERR_MAX_SUPPRESS_SECONDS is a per-file ceiling in
        case a given file never loads at all, so a dead stream doesn't
        leave the terminal silently redirected indefinitely.

        `_generation` guards against two redirect cycles racing each other
        when a new file starts loading before the previous one's restore
        has run (e.g. a fast reconnect): each redirect claims the next
        generation number, and a restore callback only acts if that number
        is still the active one, so a late callback from a superseded
        cycle can't tear down a newer cycle's redirect out from under it."""
        state = {"redirected": False, "generation": 0, "fd2_restore_copy": None, "original_stderr": None}

        def _redirect() -> int:
            if state["redirected"]:
                return state["generation"]
            try:
                devnull_fd = os.open(os.devnull, os.O_WRONLY)
                fd2_restore_copy = os.dup(2)
                python_stderr_copy = os.dup(2)
            except OSError:
                return state["generation"]
            try:
                os.dup2(devnull_fd, 2)
            except OSError:
                os.close(devnull_fd)
                os.close(fd2_restore_copy)
                os.close(python_stderr_copy)
                return state["generation"]
            os.close(devnull_fd)

            state["original_stderr"] = sys.stderr
            sys.stderr = os.fdopen(python_stderr_copy, "w", closefd=True)
            state["fd2_restore_copy"] = fd2_restore_copy
            state["redirected"] = True
            state["generation"] += 1
            return state["generation"]

        def _restore(generation: int) -> None:
            if not state["redirected"] or generation != state["generation"]:
                return
            state["redirected"] = False
            try:
                sys.stderr.close()
            except OSError:
                pass
            sys.stderr = state["original_stderr"]
            try:
                os.dup2(state["fd2_restore_copy"], 2)
                os.close(state["fd2_restore_copy"])
            except OSError:
                pass

        def _on_start_file(_event=None) -> None:
            generation = _redirect()
            fallback_timer = threading.Timer(_HWDEC_PROBE_STDERR_MAX_SUPPRESS_SECONDS, _restore, args=(generation,))
            fallback_timer.daemon = True
            fallback_timer.start()

        def _on_file_loaded(_event=None) -> None:
            generation = state["generation"]
            post_load_timer = threading.Timer(_HWDEC_PROBE_STDERR_POST_LOAD_SECONDS, _restore, args=(generation,))
            post_load_timer.daemon = True
            post_load_timer.start()

        self._mpv.event_callback("start-file")(_on_start_file)
        self._mpv.event_callback("file-loaded")(_on_file_loaded)

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

    def seek_to(self, seconds: float) -> None:
        """Jump directly to an absolute position in the current file --
        used for chapter-skip (see cli.py's UP/DOWN rewiring for a VOD
        item with chapters), distinct from play()'s own `start` option,
        which only applies at load time, before mpv has anything loaded
        yet. Swallows mpv.ShutdownError the same way playback_position()
        does -- a keypress landing right as playback ends/quits is
        equally "nothing to seek in", not a real error."""
        try:
            self._mpv.seek(seconds, reference="absolute", precision="exact")
        except mpv.ShutdownError:
            pass

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
        # Guards mpv.ShutdownError the same way playback_position() does --
        # confirmed live that _playback_position_autosave_loop's periodic
        # timer can fire this right as mpv's core is torn down mid-quit
        # (a real Plex session's log showed the resulting traceback), which
        # is an expected race on shutdown, not a real error. False is as
        # good a "paused" answer as any for a player that's already gone.
        try:
            return bool(self._mpv.pause)
        except mpv.ShutdownError:
            return False

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
            hdr = _hdr_label(video_params)

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
        data synchronously, so the temp file is removed immediately after.

        Deliberately calls the raw `overlay-add` command with every
        numeric argument stringified, rather than python-mpv's own
        overlay_add() convenience wrapper (which passes them as native
        Python ints/node values). Confirmed live: for the Plex browser's
        'i'-key "selected item" details popup specifically, the wrapper's
        int-typed call reported success (no exception, no mpv-side error)
        but silently composited nothing -- while an otherwise-identical
        `overlay-add` with every argument passed as a string, sent either
        via this same ctypes command() call or via a completely separate
        raw IPC connection, rendered correctly in that exact same
        situation. Root cause not fully understood (nothing else in this
        app that already called the old int-typed wrapper -- the Plex
        browser panel itself, its item-menu popup -- was ever observed to
        fail the same way), but the string-argument form is confirmed
        reliable everywhere it's been tested, so this applies it
        unconditionally rather than only for the one call site that
        exposed the bug."""
        data = _to_premultiplied_bgra(image)
        width, height = image.size
        fd, path = tempfile.mkstemp(suffix=".bgra")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            self._mpv.command(
                "overlay-add", str(overlay_id), str(x), str(y), path, "0", "bgra", str(width), str(height), str(width * 4)
            )
        finally:
            os.unlink(path)

    def clear_overlay(self, overlay_id: int = 0) -> None:
        self._mpv.command("overlay-remove", str(overlay_id))

    def on_key_press(self, keydef: str, callback: Callable[[], None]) -> None:
        """Run `callback` whenever `keydef` is pressed in the mpv window."""
        self._mpv.on_key_press(keydef)(callback)

    def synthesize_key_press(self, keydef: str) -> None:
        """Inject `keydef` into mpv's own input dispatch, exactly as if it
        had actually been pressed -- whatever's currently bound to it
        (which changes constantly as views open/close throughout this
        app) fires exactly as it would for a real key press. Used to make
        one physical key a permanent alias for another that's bound and
        unbound all over cli.py (e.g. a remote's GO_BACK button aliasing
        ESC), without having to duplicate every single one of that key's
        binding sites."""
        self._mpv.command("keypress", keydef)

    def unbind_key(self, keydef: str) -> None:
        """Remove a previously registered on_key_press binding, restoring
        mpv's own default behavior for that key (e.g. LEFT/RIGHT seeking).
        Works the same regardless of whether the binding was made via
        on_key_press or on_key_press_or_hold -- both register under the
        same keydef-derived name internally."""
        self._mpv.unregister_key_binding(keydef)

    def on_key_press_or_hold(
        self, keydef: str, on_press: Callable[[], None], on_hold: Callable[[], None], hold_seconds: float = 0.5
    ) -> None:
        """Like on_key_press, but distinguishes a quick tap from a
        press-and-hold: `on_press` fires if `keydef` is released within
        `hold_seconds`, `on_hold` fires if it's still held once that
        elapses -- never both. Confirmed live (see CLAUDE.md) against a
        real IR/BLE air-mouse remote that this is a genuine, reliable
        signal: a tap reports key-down immediately followed by key-up
        ~100ms later with no repeat events at all, while a real hold
        reports key-down, a stream of repeat events roughly every 25ms
        starting ~200ms in, then key-up only on actual release -- so
        `hold_seconds`'s default comfortably separates the two on real
        hardware without misfiring on an ordinary tap.

        Unlike on_key_press (built on python-mpv's own simplified
        on_key_press, which only ever sees a bare "pressed" event and
        has no notion of release at all), this goes through the lower-
        level key_binding API directly to get at key-up.

        Neither callback fires at all for a release with no matching
        press (state 'u' arriving with no prior 'd') -- confirmed live
        this can genuinely happen (a 'c' "logical cancellation" suffix
        on the up event, e.g. from a key binding elsewhere stealing
        focus mid-press), and firing on_press retroactively for it would
        be wrong at least as often as it'd be right.
        """
        pressed_at: list[float] = []

        @self._mpv.key_binding(keydef)
        def _handler(state, name=None, char=None, *_):
            if state[0] == "d":
                pressed_at[:] = [time.monotonic()]
            elif state[0] == "u" and pressed_at:
                held_for = time.monotonic() - pressed_at[0]
                pressed_at.clear()
                (on_hold if held_for >= hold_seconds else on_press)()

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

    def on_playback_ended(self, callback: Callable[[], None]) -> None:
        """Run `callback` whenever the current file plays through to a real
        natural end -- the same 'end-file' event on_playback_error reads,
        just filtered on reason=EOF instead of reason=ERROR. Confirmed
        live that this reason is specific to an actual end-of-file: a
        channel/VOD switch (play() on a new URL) reports reason=STOP, and
        quit_playback() reports reason=QUIT, so this never fires for
        either -- only when there's genuinely nothing left to play.
        cli.py uses this to offer the next episode of a Plex show."""
        @self._mpv.event_callback("end-file")
        def _handler(event):
            if event.data.reason == mpv.MpvEventEndFile.EOF:
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

    def stop(self) -> None:
        """Unload whatever's currently playing and return mpv to its idle
        state (its own "Drop files or URLs to play here" placeholder) --
        unlike quit_playback()/quit() below, this does not exit mpv or
        tvdinner itself. Harmless to call when nothing is loaded."""
        self._mpv.command("stop")

    def quit_playback(self) -> None:
        """Ask mpv to shut down cleanly -- the same effect as its own
        default 'q' binding or the window's close button. Unlike quit()
        below (called once, from cli.py's own shutdown-cleanup path,
        only after wait_for_playback() has already returned), this is
        safe to call at any time, including from a key-binding callback
        while playback is still ongoing: going through mpv's own command
        dispatch to request the shutdown, rather than calling quit()'s
        mpv.terminate() directly from there, is what lets that request
        unblock wait_for_playback() on the caller's own thread normally
        and run cli.py's existing shutdown/cleanup path (save playback
        position, stop background threads, quit() itself) exactly as it
        already does for every other way of quitting."""
        self._mpv.command("quit")

    def quit(self) -> None:
        self._mpv.terminate()
