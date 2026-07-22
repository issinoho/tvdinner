# tvdinner

A command-line IPTV player. Plays streams from an M3U/M3U8 playlist (or
a direct stream URL) using `mpv`, with a TiviMate-style on-screen EPG
overlay and a full program guide sourced from XMLTV data — auto-discovered
from the playlist, or an explicit URL — including timezone-aware
scheduling and configurable clock-correction shifts for feeds that
report incorrect times.

Primarily developed for and packaged on Linux (`.deb`/`.rpm`, see
below); also runs on Windows from source via `pip` (see
[From source, on Windows](#from-source-on-windows)) — no native
Windows installer yet.

## Requirements

- Linux (developed against Ubuntu 26.04+) or Windows
- `mpv` (on Windows: `libmpv`/`mpv-2.dll` on `PATH`)
- Python 3.10+

## Install

### Debian/Ubuntu package

Build the `.deb` locally:

```
sudo apt install debhelper dh-python python3-all python3-setuptools pybuild-plugin-pyproject fakeroot lintian
dpkg-buildpackage -us -uc -b
sudo apt install ../tvdinner_<version>_all.deb
```

This pulls in `mpv`, `python3-mpv`, `python3-pil`, `python3-requests`,
and `fonts-dejavu-core` as dependencies, and installs the `tvdinner(1)`
man page.

### Fedora/RHEL/openSUSE package

Build **on the target distribution** (or in a `mock`/chroot matching it),
not on Debian/Ubuntu -- the spec relies on that distro's own
`python3-rpm-macros` package to resolve `%{python3_sitelib}` and
`%py3_build`/`%py3_install` correctly for its Python version:

```
sudo dnf install rpm-build python3-devel python3-setuptools python3-pip
git archive --format=tar.gz --prefix=tvdinner-0.1.0/ HEAD -o ~/rpmbuild/SOURCES/tvdinner-0.1.0.tar.gz
rpmbuild -bb rpm/tvdinner.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/tvdinner-0.1.0-1.*.noarch.rpm
```

This pulls in `mpv`, `python3-pillow`, `python3-requests`, and
`dejavu-sans-fonts` as dependencies. `python-mpv` (tvdinner's Python
binding to mpv) has no Fedora/RHEL RPM equivalent, so it's deliberately
left off the spec's `Requires` -- install it separately first, e.g.
`pip install --user python-mpv`.

A source RPM (`rpmbuild -bs rpm/tvdinner.spec`) can be built from
anywhere, including Debian/Ubuntu, since it doesn't execute `%build`/
`%install` -- only turning it into an installable binary RPM needs a
real RPM-based host.

### From source (virtualenv)

```
python3 -m venv .venv
.venv/bin/pip install .
```

`mpv` itself must still be installed separately via your package manager
(e.g. `sudo apt install mpv`).

### From source, on Windows

Not packaged as a native installer yet -- this runs tvdinner from a
plain Python install:

1. Install Python 3.10+ from [python.org](https://www.python.org/) (or
   the Microsoft Store), and `mpv` -- e.g. via
   [Chocolatey](https://chocolatey.org/) (`choco install mpv`) or a
   [libmpv build](https://sourceforge.net/projects/mpv-player-windows/files/libmpv/)
   with `mpv-2.dll` placed somewhere on `PATH`.
2. `pip install .` from a checkout of this repository (a PyPI release
   isn't published yet).
3. Run `tvdinner` from the same shell/venv.

The per-channel EPG shift file (`--epg-shifts`) defaults to
`%APPDATA%\tvdinner\epg_shifts.json` on Windows, rather than the
`~/.config/...` path used on Linux.

## Usage

```
tvdinner [OPTIONS] URL
```

`URL` may be an M3U/M3U8 playlist (http(s) or a local file path) or a
direct video/audio stream URL. If it looks like a playlist, playback
starts on the channel given by `--channel`, or the first channel
otherwise — use the program guide (see Keybindings below) to switch
channels without restarting.

### Options

| Option | Description |
| --- | --- |
| `-c`, `--channel CHANNEL` | Channel name (or 1-based index) to play; defaults to the first channel in the playlist. |
| `--list` | List channels in the playlist and exit without playing. |
| `--epg URL` | XMLTV EPG URL or local file, overriding any EPG source discovered in the M3U playlist. |
| `--tz NAME` | IANA timezone for displaying EPG times, e.g. `Europe/London` (default: system local timezone). |
| `--time-shift SHIFT` | Correct EPG feed clock errors, e.g. `+1h`, `-30m`, or minutes as a plain integer. Applies to any channel without its own override in `--epg-shifts`. |
| `--epg-shifts PATH` | JSON file mapping a channel's display name (as shown by `--list`) to a per-channel EPG time-shift override, for feeds where different channels are off by different amounts (default: `~/.config/tvdinner/epg_shifts.json` on Linux, `%APPDATA%\tvdinner\epg_shifts.json` on Windows). See below. |
| `--log-file PATH` | Where to log startup/shutdown, user actions, and warnings/errors (default: `~/.cache/tvdinner/tvdinner.log` on Linux, `%LOCALAPPDATA%\tvdinner\tvdinner.log` on Windows). |
| `--no-log` | Disable file logging entirely. |

### Examples

```
# List the channels in a playlist
tvdinner https://example.com/playlist.m3u --list

# Play a channel directly by name
tvdinner playlist.m3u --channel "BBC One"

# Play a direct stream URL
tvdinner https://example.com/stream.m3u8
```

### Per-channel EPG time-shift

Some feeds have different channels running off different clock
corrections (e.g. an East/West regional pair). `--epg-shifts` points to
a JSON file mapping each channel's display name to a shift string:

```json
{"BBC One": "+1h", "TCM US West": "-3h"}
```

Channels are keyed by display name rather than `tvg_id`, since
real-world playlists commonly have several distinct channels sharing
one `tvg_id` for EPG mapping. A missing file is not an error; malformed
entries are reported as warnings on startup and skipped. Shifts can also
be adjusted live from the program guide with the `[` / `]` keys (see
below), which write straight back to this file.

### Keybindings

In addition to `mpv`'s own default key bindings:

| Key | Action |
| --- | --- |
| `i` | Show the current/next programme info overlay (with video/audio quality badges: resolution, codecs, fps, HDR, channel layout); while the program guide is open, shows full details for the selected guide programme instead. |
| `g` / `MENU` | Toggle the full program guide (`MENU` is the button most IR/BLE air-mouse remotes send for their MENU key). |
| `LEFT` / `RIGHT` | Page the program guide's timeline back/forward by 30 minutes (guide only; otherwise these seek the video as usual). |
| `UP` / `DOWN` | Move the program guide's channel selection cursor (guide only). |
| `PGUP` / `PGDWN` | Move the program guide's channel selection cursor a full page at a time (guide only). |
| `ENTER` | While the guide is open: switch to the selected channel and close the guide (or, while typing a filter query, confirm it instead). Otherwise, same as `i` — shows the programme info overlay. Handy for IR/BLE air-mouse remotes (e.g. nRF-based USB dongles), whose OK/center button typically sends `ENTER`. |
| `[` / `]` | Nudge the selected guide channel's EPG shift back/forward by 1 minute, saving the change to `--epg-shifts` immediately (guide only). |
| `f` | Open a text-entry dialog to filter the program guide's channel list by name (case-insensitive substring match); ENTER applies it, ESC cancels (guide only). |
| `c` | Clear any active guide filter and show every channel again (guide only). |
| `ESC` | Close the programme details popup, or cancel an in-progress guide filter query. |
| `z` | Cycle the video's display aspect ratio (Auto, 4:3, 16:9, 2.35:1, 1:1). |

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

All rights reserved — see [debian/copyright](debian/copyright).
