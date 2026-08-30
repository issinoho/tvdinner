"""Launch ``tvdinner.cli.main()`` with mpv's ``input-ipc-server`` enabled
(and a fixed window geometry) so ``drive_series_browser.py`` can send
real keypresses through the app's actual ``on_key_press`` closures.

Diagnostic only -- the monkeypatch here never touches shipped code, and
``input_ipc_server`` is not something tvdinner itself should ever set.

    TVDINNER_IPC_SOCK=/tmp/x.sock python tools/run_tvdinner_ipc.py <url> [args...]
"""

import os
import sys

from tvdinner import player as _player_mod

_SOCK = os.environ["TVDINNER_IPC_SOCK"]
_orig_init = _player_mod.Player.__init__


def _patched_init(self, **mpv_options):
    mpv_options.setdefault("input_ipc_server", _SOCK)
    mpv_options.setdefault("geometry", "1280x720+40+40")
    _orig_init(self, **mpv_options)


_player_mod.Player.__init__ = _patched_init

from tvdinner.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
