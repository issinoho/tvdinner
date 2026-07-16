"""Playback engine wrapper around libmpv (via python-mpv).

Kept as a thin wrapper so a future GUI can reuse the same Player class and
embed mpv's video output into a widget by passing wid= through mpv_options,
instead of letting it open its own top-level window as it does today.
"""

from __future__ import annotations

import mpv


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

    def wait_for_playback(self) -> None:
        """Block until the current stream finishes or the user quits mpv."""
        self._mpv.wait_for_playback()

    def quit(self) -> None:
        self._mpv.terminate()
