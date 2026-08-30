# tools/

Dev-only helpers. Not shipped in any package, not imported by
`tvdinner.*` at runtime.

## Fake Xtream panel

`fake_xtream_panel.py` is a tiny `http.server` that answers just enough
of `player_api.php` (handshake, one live channel + EPG, empty VOD, and a
full TV-series tree) to drive tvdinner's Xtream code paths without a real
subscription. The JSON follows the documented Xtream API response shapes
— it pins *tvdinner's* side of the contract, not any particular real
panel.

Two consumers:

### 1. Automated — `tests/test_xtream_series_integration.py`

Imports `make_server()` and exercises `tvdinner.xtream`'s real functions
over real HTTP (no `requests` monkeypatching), walking
category → series → season → episode → resolve. Runs as part of the
normal `pytest` / `uv run pytest`; needs nothing extra.

### 2. Manual — `drive_series_browser.py`

Launches the real app (via `run_tvdinner_ipc.py`, which enables mpv's
`input-ipc-server`) against the fake panel and drives the actual Series
browser with real keypresses, screenshotting each step into `shots/`.

Needs an X/Wayland display, `mpv`, and `ffmpeg` + `xwd` + `xwininfo`,
plus the sample streams:

```
tools/make_sample_media.sh          # writes gitignored sample.ts / sample.mp4
python tools/drive_series_browser.py # prints PASS/FAIL, writes tools/shots/*.png
```

`run_tvdinner_ipc.py` only monkeypatches the launch — `input_ipc_server`
is a diagnostic option tvdinner itself never sets.
