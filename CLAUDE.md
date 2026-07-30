# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Setup and tests (either works; `uv` is faster and doesn't require activating a venv):

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest
uv run pytest
```

Single file / single test:

```
uv run pytest tests/test_xtream.py
uv run pytest tests/test_xtream.py::test_load_xtream_vod_maps_streams_to_vod_items
```

There is no linter or type checker configured (no ruff/mypy config in `pyproject.toml`) — `pytest` is the only automated check.

Local package builds (Debian/Fedora/from-source instructions, including exact dependency lists, are in the README's Install section — don't duplicate them here). CI builds all four targets (`.deb`, `.rpm`, Windows installer, macOS `.dmg`) via `.github/workflows/release.yml`, triggered on `v*` tags.

## Architecture

**One big interactive session, not a class hierarchy.** `cli.py`'s `play_stream()` is the entire interactive runtime: every overlay/browser (program guide, recordings browser, scheduled-recordings browser, VOD browser, help cheat sheet) is a cluster of closures sharing `play_stream`'s local state via `nonlocal`, not a separate object. Each browser follows the same shape — `open_X`/`close_X`/`render_and_show_X`/`move_X_selection`/`play_selected_X`, wired to `player.on_key_press`/`unbind_key` only while that view is open — and each view's `toggle_X`/`open_X` closes every other currently-open view first (mutual exclusivity is enforced by hand at each call site, not by a shared "current view" variable). When adding a new browser-style view, copy this shape from the most recent one (the VOD browser) rather than inventing a new pattern.

**Every stream source normalizes into the same `Channel`/`Playlist` model** (`m3u.py`). `xtream.py`, `stalker.py`, and `hdhomerun.py` each speak a different login/API protocol but all produce a `Playlist` of `Channel`s, so the guide/favorites/EPG-shift/recording/scheduling/bookmarks code in `cli.py` needs zero source-specific branching — the only source-specific code is the `is_X_url`/`parse_X_url`/`load_X_playlist` dispatch in `main()`. Stalker in particular has no static per-channel URL (a `cmd` field must be exchanged for a real URL via `create_link`); that resolution happens once, up front, at load time, so the rest of the app never has to know.

**VOD is a deliberately separate model, not a `Channel`.** `vod.py`'s `VodItem` exists because a movie has no scheduled airing and doesn't belong in the time-grid guide. Sources: `xtream.load_xtream_vod`/`stalker.load_stalker_vod` (native VOD APIs, fetched non-fatally alongside the live playlist in `main()`) and `vod.split_m3u_vod_items` (pulls specific `group-title`s out of an M3U `Playlist` — opt-in via `--vod-group`, since group-title conventions vary too much to guess at reliably). The VOD browser reuses `playback_positions.py`'s resume store, keyed by stream URL instead of a local file path.

**All on-screen UI is a PIL-rendered RGBA image composited by mpv, never a native widget.** `overlay.py` contains every `render_*` function (guide, programme details, recording/VOD browsers, filter prompt, help sheet) — each returns a `PIL.Image`, which `cli.py` pushes via `player.show_overlay`/`clear_overlay` (`player.py` wraps mpv's `overlay-add` command). Adding a new visual element means adding a `render_*` function here, not a new UI framework dependency.

**JSON-file persistence is a consistent `load_*(path)`/`save_*(path, data)` pair per concern** (`favorites.py`, `bookmarks.py`, `schedule.py`, `playback_positions.py`, the EPG-shifts functions in `epg.py`). Loaders never raise on a missing or malformed file — they return an empty/default value plus a list of warning strings for the caller to print, so one bad entry never blocks startup. Follow this contract for any new persisted state.

**EPG times need per-channel correction before comparing to real time.** `epg.py`'s `EpgDisplay.shift_for`/`schedule_window` apply a feed's clock-correction shift (global `--time-shift` default plus optional per-channel overrides in `--epg-shifts`); raw XMLTV times are feed time, not corrected time, until passed through these. `tvg_id` is not a reliable unique key across channels — always identify a channel by URL, not `tvg_id`, when doing anything more than EPG lookup.

**Packaging duplicates the entry point per platform.** `windows/tvdinner_entry.py` and `macos/tvdinner_entry.py` exist only because PyInstaller needs a real analyzable script rather than the normal `console_scripts` entry point — both just call `tvdinner.cli.main()`. The macOS one additionally prompts for a URL via `tkinter` (no terminal when double-clicked) and patches `ctypes.util.find_library` so bundled `libmpv.dylib` is found instead of a system one, before `tvdinner.player` (which imports `mpv` at module load) is imported.

### Release versioning

A release bump touches **four files together**, in one commit separate from the feature/fix commit(s) it covers: `src/tvdinner/__init__.py` (`__version__`), `debian/changelog` (new top entry), `CHANGELOG.md` (new top entry, same text, backtick-formatted), and `rpm/tvdinner.spec` (`Release:` line **and** a `%changelog` entry) — note the RPM `Release` number and the `0.1.0-NN` suffix used everywhere else are two different counters that have drifted apart over time (check the current `Release:` value in the spec rather than assuming it matches `__version__`'s suffix). Tag as `v<version>` (lightweight tag, matching the `__init__.py` version) and push the tag — `release.yml` builds and publishes from `v*` tags.

### Testing conventions

One test file per source module (`tests/test_x.py` for `src/tvdinner/x.py`). Network-backed loaders (`xtream.py`, `stalker.py`, `hdhomerun.py`) are tested by monkeypatching `tvdinner.<module>.requests.get` with a fake dispatching on the request's `action`/`type` params — copy the existing `_fake_get_for` pattern in `tests/test_xtream.py`/`tests/test_stalker.py` rather than a mocking library. `overlay.py` render functions are tested by asserting on the returned `PIL.Image` directly (mode, size, pixel counts for things like selection borders, height comparisons for things like date/group grouping) — there are no golden-image/snapshot tests.
