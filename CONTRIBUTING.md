# Contributing to tvdinner

Thanks for considering a contribution. This is a solo-maintained
project, so response times are best-effort — but bug reports, small
fixes, and well-scoped features are genuinely welcome.

This file covers contributing to the *code*. If you're looking for how
to *use* tvdinner instead, see the
[wiki](https://github.com/issinoho/tvdinner/wiki).

By participating, you're expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Getting started

```
git clone https://github.com/issinoho/tvdinner.git
cd tvdinner
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Or with [`uv`](https://docs.astral.sh/uv/) (faster, no venv activation
needed):

```
uv run pytest
```

A single file or single test:

```
uv run pytest tests/test_xtream.py
uv run pytest tests/test_xtream.py::test_load_xtream_vod_maps_streams_to_vod_items
```

`pytest` is the only automated check — there's no linter/formatter
configured (no ruff/mypy/black config in `pyproject.toml`). Match the
style of the surrounding code rather than reformatting things (see
"Code style" below).

## Before you dive in

**Read [`CLAUDE.md`](CLAUDE.md) first** — it's this repo's own
architecture guide (written for an AI coding agent, but just as useful
for a human), and covers things a skim of the code won't make obvious:
how `cli.py`'s single giant interactive session is structured, why VOD
is a deliberately separate model from a live channel, the two
background-timing idioms used throughout, and the versioning process.
Getting the shape of a change right up front saves a lot of back and
forth in review.

## Code style

No formal style guide, but a few conventions this codebase is
consistent about:

- **Comment density matches intent, not habit.** Comments explain
  *why*, especially where the reasoning was non-obvious or was
  discovered the hard way (a bug that looked reasonable but wasn't,
  a platform quirk, a deliberate trade-off) — not what the code
  obviously already says.
- **Match existing naming/idiom** in whichever file you're touching
  rather than introducing a new pattern for something already solved
  elsewhere. If you're adding a new browser-style view, a new
  persisted-state file, or a new background timer, copy the shape of
  the most similar existing one — `CLAUDE.md` names which one to copy
  for several common cases.
- **One test file per source module** (`tests/test_x.py` for
  `src/tvdinner/x.py`).

### Testing conventions

- A network-backed loader (`xtream.py`, `stalker.py`, `hdhomerun.py`,
  `plex.py`) is tested by monkeypatching `tvdinner.<module>.requests.get`
  with a fake that dispatches on the request's `action`/`type` params
  (or, for `plex.py`, the request path) — copy the existing
  `_fake_get_for` pattern in `tests/test_xtream.py`/`test_stalker.py`/
  `test_plex.py` rather than reaching for a mocking library.
- An `overlay.py` `render_*` function is tested by asserting on the
  returned `PIL.Image` directly (mode, size, pixel counts for things
  like a selection border, height comparisons for things like
  date/group grouping) — there are no golden-image/snapshot tests.
- **`cli.py`'s interactive closures (browser open/close/select,
  keybindings) are deliberately *not* unit-tested**, for any source —
  they're validated live instead, by driving mpv's own IPC socket with
  synthetic keypresses against a real stream/server. If your change
  touches `play_stream()` or anything nested inside it, say in your PR
  how you exercised it (a description of manual testing is fine — a
  maintainer may ask for more detail on a non-trivial change).

## Submitting a change

1. Fork the repo and branch off `master`.
2. Make your change, with tests where the conventions above call for
   them.
3. `pytest` should pass locally. Every PR also runs the same suite via
   GitHub Actions (`.github/workflows/test.yml`, Python 3.10 and 3.12)
   — keep an eye on that check once you open the PR.
4. Open a pull request describing what changed and why. Reference any
   related issue.

Don't worry about version bumps, `CHANGELOG.md`, or packaging files
(`debian/changelog`, `rpm/tvdinner.spec`) — that's a maintainer step
done at release time, covered in `CLAUDE.md`.

## Reporting bugs / requesting features

Open a [GitHub issue](https://github.com/issinoho/tvdinner/issues).
For a bug, include your platform, how you're running tvdinner (which
source type — M3U, Xtream, Stalker, HDHomeRun, Plex, local file,
YouTube), and the relevant bit of `~/.cache/tvdinner/tvdinner.log`
(`%LOCALAPPDATA%\tvdinner\tvdinner.log` on Windows) if there's an
error — credentials embedded in a source URL are already redacted
there, but double-check before pasting a log excerpt anywhere public.

## Questions

The [wiki](https://github.com/issinoho/tvdinner/wiki) covers how to
*use* every source and feature in depth; this file and `CLAUDE.md`
cover how to work *on* the codebase itself. If something's unclear in
either, opening an issue about the documentation itself is a
legitimate contribution too.
