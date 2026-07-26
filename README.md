# tvdinner

A command-line IPTV player. Plays streams from an M3U/M3U8 playlist (or
a direct stream URL) using `mpv`, with a TiviMate-style on-screen EPG
overlay and a full program guide sourced from XMLTV data — auto-discovered
from the playlist, or an explicit URL — including timezone-aware
scheduling and configurable clock-correction shifts for feeds that
report incorrect times.

Primarily developed for and packaged on Linux (`.deb`/`.rpm`, see
below); also packaged for Windows as a self-contained installer (see
[Windows installer](#windows-installer)) and for macOS as a
self-contained app (see [macOS app](#macos-app)), both bundling their
own mpv so nothing else needs installing first.

## Screenshots

The full program guide — channels down the left, a timeline across the
top, and a live "now" marker:

![Program guide](screenshots/guide.png)

The on-screen EPG banner, shown on channel switch or with `i` — current
programme, poster art, stream quality badges, and a favorited channel's
heart marker:

![EPG banner overlay](screenshots/epg-overlay.png)

Full programme details for the guide's selected show, poster art
included:

![Programme details popup](screenshots/programme-details.png)

Filtering the guide by name or group:

![Guide filter prompt](screenshots/guide-filter.png)

`tvdinner bookmarks` — an interactive picker for saved playlists:

![Bookmarks picker](screenshots/bookmarks.png)

Every option, from `tvdinner --help`:

![tvdinner --help output](screenshots/help.png)

## Requirements

- Linux (developed against Ubuntu 26.04+), Windows, or macOS 11 (Big
  Sur) or later
- `mpv` on Linux (the Windows installer and macOS app both bundle their
  own; only needed separately if running from source there)
- Python 3.10+ (not needed at all with the Windows installer or macOS
  app, which bundle their own)

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

### Windows installer

Download `tvdinner-setup-<version>.exe` from the
[latest release](https://github.com/issinoho/tvdinner/releases/latest)
and run it. It bundles a pre-built mpv (see
[windows/THIRD_PARTY_NOTICES.txt](windows/THIRD_PARTY_NOTICES.txt) for
its license) and everything else tvdinner needs -- there's no separate
Python or mpv install step. It's unsigned, so Windows SmartScreen will
show an "unrecognized app" warning on first run; click "More info" then
"Run anyway" to proceed. An optional install step adds tvdinner to your
`PATH` so you can run `tvdinner` from any Command Prompt (open a new
one after installing for this to take effect).

The per-channel EPG shift file (`--epg-shifts`) defaults to
`%APPDATA%\tvdinner\epg_shifts.json` on Windows, rather than the
`~/.config/...` path used on Linux (similarly for `--favorites` and
`--bookmarks-file`).

### From source, on Windows

For development, or if you'd rather not use the installer:

1. Install Python 3.10+ from [python.org](https://www.python.org/) (or
   the Microsoft Store), and `mpv` -- e.g. via
   [Chocolatey](https://chocolatey.org/) (`choco install mpv`) or a
   [libmpv build](https://sourceforge.net/projects/mpv-player-windows/files/libmpv/)
   with `mpv-2.dll` placed somewhere on `PATH`.
2. `pip install .` from a checkout of this repository (a PyPI release
   isn't published yet).
3. Run `tvdinner` from the same shell/venv.

### macOS app

Requires macOS 11 (Big Sur) or later.

Download `tvdinner-<version>.dmg` from the
[latest release](https://github.com/issinoho/tvdinner/releases/latest),
open it, and drag `tvdinner.app` wherever you like. It bundles a
Homebrew-built libmpv (see
[macos/THIRD_PARTY_NOTICES.txt](macos/THIRD_PARTY_NOTICES.txt) for its
license) and everything else tvdinner needs -- no separate Python or
mpv install step. It's unsigned/unnotarized, so Gatekeeper will refuse
to open it with a plain double-click ("tvdinner.app is damaged and
can't be opened" or similar); right-click (or Control-click) the app
and choose "Open" instead, then confirm in the dialog that appears --
this only has to be done once. If that still doesn't work, clear the
quarantine flag from Terminal: `xattr -cr /path/to/tvdinner.app`.

Since there's no terminal to pass a URL argument to when double-clicked,
launching the app instead prompts for the M3U playlist URL/path or a
direct stream URL each time, pre-filled with whichever one you used
last.

The per-channel EPG shift file (`--epg-shifts`) defaults to
`~/Library/Application Support/tvdinner/epg_shifts.json` on macOS
(similarly for `--favorites` and `--bookmarks-file`), and the log file
defaults to `~/Library/Logs/tvdinner/tvdinner.log`.

### From source, on macOS

For development, or if you'd rather not use the app bundle:

1. Install Python 3.10+ (e.g. via [python.org](https://www.python.org/)
   or `brew install python`), and `mpv` via
   [Homebrew](https://brew.sh/): `brew install mpv`.
2. `pip install .` from a checkout of this repository (a PyPI release
   isn't published yet).
3. Run `tvdinner <url>` from the same shell/venv, same as on Linux.

## Usage

```
tvdinner [OPTIONS] URL
tvdinner bookmarks [--bookmarks-file PATH]
tvdinner backup [PATH] [--epg-shifts PATH] [--favorites PATH] [--bookmarks-file PATH]
tvdinner restore PATH [--epg-shifts PATH] [--favorites PATH] [--bookmarks-file PATH] [-y]
```

`URL` may be an M3U/M3U8 playlist (http(s) or a local file path) or a
direct video/audio stream URL. If it looks like a playlist, playback
starts on the channel given by `--channel`, or the first channel
otherwise — use the program guide (see Keybindings below) to switch
channels without restarting.

`tvdinner bookmarks` opens an interactive terminal table of saved
playlists instead: `a` adds one (description, M3U URL, optional EPG URL,
optional default channel e.g. `CNN`), `e` edits the selected one, `d`
deletes it (with confirmation), `SPACE` toggles that row's "EPG Refresh"
checkbox (unchecked by default, and not remembered between sessions),
and `ENTER` launches tvdinner with it, exactly as if its URL/`--epg`/
`--channel` had been typed directly -- adding `--refresh-epg-cache` too
if the checkbox was checked. Saved to `~/.config/tvdinner/bookmarks.json`
by default (`%APPDATA%\tvdinner\bookmarks.json` on Windows,
`~/Library/Application Support/tvdinner/bookmarks.json` on macOS).

`tvdinner backup` writes the EPG shifts, favorites, and bookmarks files
into a single compressed archive for offline storage or moving to
another machine (default filename: `tvdinner-backup-<timestamp>.zip` in
the current directory; the EPG cache and log file are deliberately left
out, since they're disposable, not configuration). `tvdinner restore`
extracts a backup archive back onto disk, overwriting the current
files — it prompts for confirmation unless `-y`/`--yes` is given.

### Options

| Option | Description |
| --- | --- |
| `-c`, `--channel CHANNEL` | Channel name (or 1-based index) to play; defaults to the first channel in the playlist. |
| `--list` | List channels in the playlist and exit without playing. |
| `--epg URL` | XMLTV EPG URL or local file, overriding any EPG source discovered in the M3U playlist. |
| `--tz NAME` | IANA timezone for displaying EPG times, e.g. `Europe/London` (default: system local timezone). |
| `--time-shift SHIFT` | Correct EPG feed clock errors, e.g. `+1h`, `-30m`, or minutes as a plain integer. Applies to any channel without its own override in `--epg-shifts`. |
| `--epg-shifts PATH` | JSON file mapping a channel's display name (as shown by `--list`) to a per-channel EPG time-shift override, for feeds where different channels are off by different amounts (default: `~/.config/tvdinner/epg_shifts.json` on Linux, `%APPDATA%\tvdinner\epg_shifts.json` on Windows). See below. |
| `--favorites PATH` | JSON file storing favorited channels per playlist (see the `h` keybinding below), keyed by the playlist URL/path so different feeds don't share one favorites list (default: `~/.config/tvdinner/favorites.json` on Linux, `%APPDATA%\tvdinner\favorites.json` on Windows). |
| `--record-dir PATH` | Directory to save `r`-key recordings into (see Keybindings below); default: `~/Videos/tvdinner` on Linux, `%USERPROFILE%\Videos\tvdinner` on Windows. |
| `--schedule-file PATH` | JSON file storing EPG-scheduled recordings (see the `s` guide keybinding below), default: `~/.config/tvdinner/schedule.json` on Linux, `%APPDATA%\tvdinner\schedule.json` on Windows. tvdinner must still be running when a scheduled recording's time arrives -- there's no background service. |
| `--epg-cache-hours HOURS` | How long a downloaded EPG is reused from disk before re-fetching (default: 24). |
| `--no-epg-cache` | Always re-download the EPG instead of using a cached copy, and don't write one either. |
| `--refresh-epg-cache` | Force a fresh EPG download for this run, ignoring any existing cached copy no matter its age, then refresh the on-disk cache with it (unlike `--no-epg-cache`, later runs still benefit from the cache). |
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
| `f` | Open a text-entry dialog to filter the program guide's channel list by name or group (as shown by `--list`, case-insensitive substring match against either); ENTER applies it, ESC cancels (guide only). |
| `c` | Clear any active guide filter and show every channel again (guide only). |
| `h` | Toggle the selected guide channel as a favorite (or the currently-playing one if the guide isn't open), saving to `--favorites` immediately; favorited channels show a heart next to their name in the guide. |
| `v` | Toggle showing only favorited channels in the guide (guide only). |
| `ESC` | Close the programme details popup, or cancel an in-progress guide filter query. |
| `z` | Cycle the video's display aspect ratio (Auto, 4:3, 16:9, 2.35:1, 1:1, Stretch — fills the window exactly, distorting the image if needed). |
| `r` | Toggle recording the current stream to disk as a raw copy (no re-encoding), saved under `--record-dir` as `<channel>_<timestamp>.ts`. |
| `s` | While programme details are shown (guide only): schedule that programme to record automatically, switching channels and starting/stopping the recording at its start/stop time even if you're watching something else -- press again to cancel. Saved to `--schedule-file`; only fires while tvdinner is running. A scheduled programme shows a small red "R" badge in the guide. |
| `w` | Browse past recordings from `--record-dir`, grouped by date -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to play it back, `d` twice to permanently delete the selected one (the first press just arms the confirmation), `ESC` to close. |

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT — see [LICENSE](LICENSE).
