"""Apple AirPlay support.

A purely optional dependency (see pyproject.toml's "airplay" extra, `pip
install tvdinner[airplay]`) -- this module degrades to
airplay_available() returning False rather than raising ImportError at
load time, matching chromecast.py. Unlike pychromecast, pyatv has no
synchronous API at all (confirmed via pyatv's own FAQ), so this module
owns a dedicated background thread running its own asyncio event loop
for as long as the AirPlay device picker is open (AirPlaySession),
rather than a throwaway asyncio.run() per call -- pyatv's scan/pair/
connect calls all share objects (scanned device configs, an in-progress
pairing handler, an open AppleTV connection) that are tied to the loop
that created them. Every public function below schedules a coroutine
onto that loop and delivers the result back via a callback invoked on
the loop's own background thread -- the same "call straight back into
cli.py's nonlocal-mutating closures from a background thread" pattern
chromecast.py's discover_chromecasts already relies on (python-mpv's
IPC calls are thread-safe).

Unlike Chromecast, a never-before-paired device needs a one-time
pairing step (a PIN shown on the device, typed into tvdinner) before it
can stream anything -- see device_needs_pairing/begin_pairing/
submit_pairing_pin below. Credentials from a successful pairing are an
opaque string (pyatv's own format) that cli.py persists via
load_airplay_credentials/save_airplay_credentials so pairing only
happens once per device, following the same non-raising load_*/save_*
JSON convention as bookmarks.py/favorites.py.

Confirmed live against pyatv 0.18: AirPlayStream.play_url() does not
return until playback ends (it's a single long-held HTTP request to the
device for the whole play duration), so play_url() below only awaits
the initial pyatv.connect() (the real "did casting start" signal,
analogous to Chromecast's cast.wait()) and then lets play_url() run to
completion in the background as a tracked, cancellable task -- stopped
early via stream.close() (which cancels that task) from stop_playing().

Confirmed live against two real third-party (non-Apple) AirPlay 2 TVs
(a Samsung and a Roku, both on the same LAN): discovery, pairing (real
PIN read off each device's own screen), credential persistence, and
pyatv.connect() all worked correctly end to end. Actual playback did
not, on either device -- pyatv's AirPlayV2.play_url() sends the initial
/play command (which the Roku visibly acknowledged with an on-screen
"connecting" indicator) followed by several PUT /setProperty calls
Apple's own client also sends (isInterestedInDateRange,
actionAtItemEnd, forward/reverseEndTime) that, unlike /play itself,
aren't wrapped in pyatv's own allow_error=True -- both TVs returned
"501 Not Implemented" for these, which pyatv raises as a real
exception. This is a pyatv-vs-third-party-receiver protocol gap (pyatv
is developed primarily against genuine Apple hardware), not a
tvdinner-side bug -- the same category of thing as chromecast.py's own
"a receiver that can't decode a given stream will simply fail to cast
it" caveat. play_url() below already treats it as a background,
log-only failure rather than crashing, which is the right behavior
either way; genuine Apple TV/HomePod hardware was not available to
confirm the fully-working case.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    import pyatv
    from pyatv.const import PairingRequirement, Protocol
except ImportError:
    pyatv = None
    PairingRequirement = None
    Protocol = None

logger = logging.getLogger(__name__)


def airplay_available() -> bool:
    return pyatv is not None


if sys.platform == "win32":
    DEFAULT_AIRPLAY_CREDENTIALS_PATH = (
        Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "airplay_credentials.json"
    )
else:
    DEFAULT_AIRPLAY_CREDENTIALS_PATH = Path.home() / ".config" / "tvdinner" / "airplay_credentials.json"


def load_airplay_credentials(path: Path) -> tuple[dict[str, str], list[str]]:
    """Load saved AirPlay pairing credentials, keyed by device
    identifier -- {"<identifier>": "<opaque credentials string>", ...}.
    A missing file is not an error -- it just means no device has been
    paired yet. Malformed JSON, or a malformed individual entry, is
    reported as a warning string rather than raising."""
    if not path.is_file():
        return {}, []

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"Could not read AirPlay credentials file {path}: {exc}"]

    if not isinstance(data, dict):
        return {}, [f"AirPlay credentials file {path} must contain a JSON object"]

    credentials: dict[str, str] = {}
    warnings: list[str] = []
    for identifier, value in data.items():
        if isinstance(identifier, str) and isinstance(value, str):
            credentials[identifier] = value
        else:
            warnings.append(f"Ignoring malformed AirPlay credentials entry {identifier!r} in {path}")
    return credentials, warnings


def save_airplay_credentials(path: Path, credentials: dict[str, str]) -> None:
    """Write AirPlay pairing credentials back to their JSON file.
    Creates the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(credentials, indent=2) + "\n")


@dataclass
class AirPlayDevice:
    """One discovered AirPlay device, for cli.py's device-picker list.
    Unlike chromecast.py's CastDevice, this holds no live pyatv object --
    a scanned pyatv.interface.BaseConfig can't safely outlive the loop
    that produced it across threads, so AirPlaySession keeps the real
    config (and any open connection) keyed by `identifier` internally,
    and every function below takes the device back in just to look
    those up."""

    name: str
    identifier: str


class AirPlaySession:
    """Owns a dedicated background thread running its own asyncio event
    loop for as long as the AirPlay device picker is open, plus the live
    pyatv objects (scanned configs, open AppleTV connections, an
    in-progress pairing handler) that must run on that loop. Created in
    cli.py's open_airplay_picker, closed in close_airplay_picker -- same
    lifecycle as chromecast_stop_discovery."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._configs: dict[str, object] = {}
        self._connections: dict[str, object] = {}

    def _submit(self, coro, on_done: Callable[[object, Exception | None], None] | None) -> None:
        async def _runner():
            try:
                result = await coro
            except Exception as exc:  # noqa: BLE001 -- reported back to the caller, not swallowed
                logger.warning("AirPlay operation failed: %s", exc)
                if on_done is not None:
                    on_done(None, exc)
            else:
                if on_done is not None:
                    on_done(result, None)

        asyncio.run_coroutine_threadsafe(_runner(), self._loop)

    def close(self) -> None:
        """Stop the background loop/thread. Any devices still actively
        casting should be stopped via stop_playing() first -- this just
        tears down the scanning/pairing machinery, matching
        chromecast_stop_discovery's scope (it doesn't stop an active
        cast either; disconnecting is a separate, explicit user action).

        Confirmed live: quitting tvdinner while a scan (pyatv.scan()'s
        5-second mDNS wait) was still in flight left it trying to touch
        the loop after close() had already torn it down, surfacing as a
        "RuntimeError: Event loop is closed" traceback on exit -- fixed
        by cancelling every outstanding task on the loop first and
        letting that settle before stopping/closing it."""

        async def _cancel_all_tasks() -> None:
            current = asyncio.current_task()
            tasks = [task for task in asyncio.all_tasks(loop=self._loop) if task is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        future = asyncio.run_coroutine_threadsafe(_cancel_all_tasks(), self._loop)
        try:
            future.result(timeout=2)
        except Exception:  # noqa: BLE001 -- best-effort; still proceed to stop/close the loop either way
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
        self._loop.close()


def discover_airplay_devices(
    session: AirPlaySession, on_devices_found: Callable[[list[AirPlayDevice]], None]
) -> None:
    """Start a bounded background scan, calling `on_devices_found` once
    with the full list of discovered AirPlay-capable devices. Unlike
    chromecast.py's discover_chromecasts (which streams results in one
    at a time as mDNS responses arrive), pyatv.scan() is a single
    bounded-timeout call that returns everything at once -- so the
    picker shows "Scanning..." then all devices appearing together,
    rather than trickling in."""

    async def _scan():
        configs = await pyatv.scan(session._loop, protocol=Protocol.AirPlay)
        devices = []
        for config in configs:
            service = config.get_service(Protocol.AirPlay)
            if service is None:
                continue
            session._configs[config.identifier] = config
            devices.append(AirPlayDevice(name=config.name, identifier=config.identifier))
        return devices

    def _on_done(devices, error):
        on_devices_found(devices if devices is not None else [])

    session._submit(_scan(), _on_done)


def device_needs_pairing(session: AirPlaySession, device: AirPlayDevice, credentials: str | None) -> bool:
    """True if `device` must be paired (a PIN shown on the device, typed
    into tvdinner) before it can stream anything. Per pyatv's own rule,
    pairing must only be attempted if the service reports it as Optional
    or Mandatory -- and it's skipped entirely if credentials are already
    on hand, since pyatv.connect() is the real test of whether they're
    still valid."""
    if credentials:
        return False
    config = session._configs.get(device.identifier)
    if config is None:
        return False
    service = config.get_service(Protocol.AirPlay)
    return service is not None and service.pairing in (PairingRequirement.Optional, PairingRequirement.Mandatory)


def begin_pairing(
    session: AirPlaySession, device: AirPlayDevice, on_done: Callable[[object | None, str | None], None]
) -> None:
    """Start pairing with `device` -- the PIN is shown on the device
    itself; the caller collects it (see cli.py's PIN-entry prompt) and
    passes it to submit_pairing_pin along with the opaque `handler`
    object this hands back via `on_done(handler, error)`."""
    config = session._configs.get(device.identifier)
    if config is None:
        on_done(None, "Device is no longer available")
        return

    async def _begin():
        handler = await pyatv.pair(config, Protocol.AirPlay, session._loop)
        await handler.begin()
        return handler

    def _on_done(handler, exc):
        on_done(handler, None if exc is None else str(exc))

    session._submit(_begin(), _on_done)


def submit_pairing_pin(
    session: AirPlaySession, handler: object, pin: str, on_done: Callable[[str | None, str | None], None]
) -> None:
    """Finish an in-progress pairing (started via begin_pairing) with
    the PIN the user typed in, handing back `on_done(credentials,
    error)` -- `credentials` is an opaque string to persist via
    save_airplay_credentials and pass to play_url from then on."""

    async def _finish():
        handler.pin(pin)
        await handler.finish()
        credentials = handler.service.credentials if handler.has_paired else None
        await handler.close()
        return credentials

    def _on_done(credentials, exc):
        if exc is not None:
            on_done(None, str(exc))
        elif credentials is None:
            on_done(None, "Pairing failed -- check the PIN and try again")
        else:
            on_done(credentials, None)

    session._submit(_finish(), _on_done)


def cancel_pairing(session: AirPlaySession, handler: object) -> None:
    """Best-effort cleanup if the user backs out of the PIN prompt
    without finishing pairing."""

    async def _cancel():
        await handler.close()

    session._submit(_cancel(), None)


def play_url(
    session: AirPlaySession,
    device: AirPlayDevice,
    credentials: str | None,
    url: str,
    on_done: Callable[[str | None], None],
) -> None:
    """Tell `device` to fetch and play `url` independently -- the exact
    URL tvdinner itself is already playing, not a proxied or transcoded
    copy. Unlike chromecast.py's cast_url, there's no title to pass --
    pyatv's AirPlay play_url() implementation doesn't support any
    display-metadata kwargs. `on_done(error)` fires once the connection
    is established (or fails) -- the real "did casting start" signal,
    since play_url() itself does not return until playback ends (see
    module docstring); that long-running call is left to finish in the
    background, cancellable via stop_playing()."""
    config = session._configs.get(device.identifier)
    if config is None:
        on_done("Device is no longer available")
        return

    async def _run() -> None:
        # on_done fires as soon as pyatv.connect() itself settles (the
        # real "did casting start" signal) -- not when this coroutine as
        # a whole finishes, since play_url() runs for the entire play
        # duration (see module docstring).
        try:
            if credentials:
                config.set_credentials(Protocol.AirPlay, credentials)
            atv = await pyatv.connect(config, session._loop)
        except Exception as exc:  # noqa: BLE001 -- reported back via on_done
            on_done(str(exc))
            return
        session._connections[device.identifier] = atv
        on_done(None)
        try:
            await atv.stream.play_url(url)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 -- background playback error, just logged
            logger.warning("AirPlay playback on %s ended with an error: %s", device.name, exc)
        finally:
            session._connections.pop(device.identifier, None)
            atv.close()

    asyncio.run_coroutine_threadsafe(_run(), session._loop)


def stop_playing(session: AirPlaySession, device: AirPlayDevice) -> None:
    """Stop whatever `device` is currently playing and close the
    connection, handing the device back to its own home screen --
    mirrors chromecast.py's stop_casting()."""

    def _do_stop():
        atv = session._connections.pop(device.identifier, None)
        if atv is None:
            return
        try:
            atv.stream.close()
        except Exception as exc:  # noqa: BLE001 -- best-effort stop
            logger.warning("Error stopping AirPlay playback on %s: %s", device.name, exc)
        atv.close()

    session._loop.call_soon_threadsafe(_do_stop)
