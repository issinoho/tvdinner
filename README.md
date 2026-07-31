<img src="docs/assets/logo-mark.svg" alt="tvdinner logo" width="64" height="64">

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

`URL` may be an M3U/M3U8 playlist (http(s) or a local file path), an
[Xtream Codes](#xtream-codes) login (`xtream://username:password@host:port`),
a [Stalker Portal](#stalker-portal) login
(`stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF`), an
[HDHomeRun](#hdhomerun) tuner (`hdhomerun://host[:port]`), a
[Plex Media Server](#plex-media-server) login
(`plex://host:port?X-Plex-Token=...`), or a direct video/audio stream URL.
If it resolves to a channel list, playback starts on the channel given by
`--channel`, or the first channel otherwise — use the program guide (see
Keybindings below) to switch channels without restarting. A Plex URL is
different: there's no channel list, just a library browser (see
[Plex Media Server](#plex-media-server) below).

`tvdinner bookmarks` opens an interactive terminal table of saved
playlists instead: `a` adds one (description, URL -- anything the `URL`
argument above accepts, optional EPG URL, optional default channel e.g.
`CNN`), `e` edits the selected one, `d`
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
| `--live-buffer-minutes MINUTES` | How long the `p` keybinding can pause a live channel before it resumes automatically (default: 10). |
| `--disable-full-screen` | Start in a normal window instead of full screen (the default). |
| `--playback-positions-file PATH` | JSON file remembering where you left off in each recording (see the `w` recordings browser), so reopening one resumes instead of starting over (default: `~/.config/tvdinner/playback_positions.json` on Linux, `%APPDATA%\tvdinner\playback_positions.json` on Windows). |
| `--epg-cache-hours HOURS` | How long a downloaded EPG is reused from disk before re-fetching (default: 24). |
| `--no-epg-cache` | Always re-download the EPG instead of using a cached copy, and don't write one either. |
| `--refresh-epg-cache` | Force a fresh EPG download for this run, ignoring any existing cached copy no matter its age, then refresh the on-disk cache with it (unlike `--no-epg-cache`, later runs still benefit from the cache). |
| `--no-online-logos` | Don't fall back to [iptv-org](https://github.com/iptv-org/api)'s community channel/logo database for channels with no logo of their own or in their EPG (common for bare M3U playlists) -- on by default, sharing `--epg-cache-hours`/`--no-epg-cache`/`--refresh-epg-cache`'s caching. |
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

# Log into an Xtream Codes panel directly
tvdinner 'xtream://myuser:mypass@panel.example.com:8080'

# Log into a Stalker Portal directly
tvdinner 'stalker://panel.example.com:8080/c/?mac=AA:BB:CC:DD:EE:FF'

# Tune an HDHomeRun network tuner directly
tvdinner 'hdhomerun://192.168.1.50'

# Browse and play from a Plex Media Server
tvdinner 'plex://192.168.0.218:32400?X-Plex-Token=abcdef123456'
```

### Xtream Codes

Instead of an M3U URL, `URL` can be an Xtream Codes panel login:

```
xtream://username:password@host:port
```

Use `xtreams://` instead of `xtream://` if the panel is served over https.
tvdinner logs in, fetches the live channel list (mapping each panel
category to a channel group, same as an M3U `group-title`), and points EPG
loading at the panel's own XMLTV export (`xmltv.php`) — everything else
(the guide, favorites, EPG shifts, recording, scheduling, bookmarks) works
exactly as it does for an M3U playlist. Live stream URLs default to a `.ts`
container; add `?output=m3u8` if your panel needs that instead:

```
xtream://myuser:mypass@panel.example.com:8080?output=m3u8
```

Note that, like an M3U URL that happens to embed credentials in its query
string, an `xtream://` URL's username and password are stored as plain text
wherever the source URL itself is stored — `bookmarks.json`, `favorites.json`
(keyed by feed), and inside a `tvdinner backup` archive. They're never
written to the log file, which always shows a redacted `user:***@host`
form instead.

### Stalker Portal

`URL` can also be a Stalker Portal (also known as Ministra, or "Stalker
Middleware" -- the protocol MAG25x/26x set-top boxes speak) login:

```
stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF
```

Use `stalkers://` instead of `stalker://` if the portal is served over
https. The path is whatever your provider gave you (e.g. `/c/` or
`/stalker_portal/c/`, copied from a MAG box's settings screen) --
`portal.php` is appended automatically if it isn't already there.
Optional `&serial=`, `&device_id=`, and `&stb_type=` (default `MAG250`)
query params can be added for portals picky about device identification.

tvdinner logs in with the given MAC (there's no separate username/password
step), fetches the channel list, and resolves each channel's actual
playable stream URL up front via the portal's `create_link` call (each
channel's raw `cmd` field isn't directly playable). There is currently no
EPG/program-guide support for Stalker sources -- channels behave like any
other EPG-less playlist. Because Stalker Portal has no official spec and
many vendor forks behave slightly differently, some providers may need a
different `stb_type` or an adjusted portal path to work.

Like the Xtream Codes case above, a `stalker://` URL's MAC address is
stored as plain text wherever the source URL itself is stored
(`bookmarks.json`, `favorites.json`, backup archives); it's shown redacted
(all but the first two octets masked) in the log file.

### HDHomeRun

`URL` can also point directly at an [HDHomeRun](https://www.silicondust.com/)
network tuner on your LAN:

```
hdhomerun://host[:port]
```

e.g. `hdhomerun://192.168.1.50`. There's no login step -- HDHomeRun
devices have no authentication at all -- and no auto-discovery either:
give tvdinner the device's IP or hostname directly (found via your
router, or SiliconDust's own discovery tools). tvdinner fetches the
device's channel lineup and uses each channel's stream URL as-is.

If the device reports a paid [HDHomeRun DVR guide
subscription](https://info.hdhomerun.com/info/dvr), tvdinner also fetches
program guide data automatically from SiliconDust's XMLTV API -- no
further configuration needed. Without a subscription, the fetch simply
fails and channels behave like any other EPG-less playlist (the same
graceful "EPG data not available" you'd see for any inaccessible guide
source).

### Plex Media Server

`URL` can also point at a [Plex](https://www.plex.tv/) Media Server:

```
plex://host:port?X-Plex-Token=...
```

Use `plexs://` instead of `plex://` if the server is served over https. A
Plex source has no live channels or EPG at all -- it's a library browser
instead. On connecting, tvdinner lists the server's movie and TV-show
libraries as a TUI overlay: arrows/`PGUP`/`PGDWN` to move, `ENTER` to
drill in (library → show → season → episode) or play a movie/episode,
`ESC` to go back a level or close the browser, and `l` to reopen it later.
Press `/` at any point to search the whole server via Plex's own search
API, not just whatever's currently on screen. Playback is always
direct-play (the file's own container/codecs, streamed straight from
Plex) -- tvdinner never asks Plex to transcode, so a file mpv can't
decode on its own won't play here even if it would in Plex's own apps.
Once something's playing, `i` shows a poster/synopsis/rating/progress
overlay pulled from Plex's own metadata, and resuming/reconnecting on a
dropped connection works the same as any other on-demand source (see
`--playback-positions-file` below).

Finding your token: play anything in Plex Web, open your browser's dev
tools → Network tab, and look for `X-Plex-Token=...` in any request's
query string (or see
[Plex's own instructions](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)).
Like the Xtream Codes/Stalker Portal cases above, a `plex://` URL's token
is stored as plain text wherever the source URL itself is stored
(`bookmarks.json`, backup archives); it's shown redacted (first four
characters kept, the rest masked) in the log file.

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
| `i` | Show the current/next programme info overlay (with video/audio quality badges: resolution, codecs, fps, HDR, channel layout); while the program guide is open, shows full details for the selected guide programme instead. While watching back a recording, shows its own label, recorded date, and playback progress instead of live EPG info. While playing a VOD/[Plex](#plex-media-server) movie or episode, shows its poster, synopsis, rating, and playback progress instead (Plex populates all of that; other VOD sources show whatever fields they have). |
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
| `p` / `PLAY` / `PAUSE` / `PLAYPAUSE` | Pause/resume live TV (the last three are the key names mpv reports for a remote's dedicated play/pause button). While paused, the stream keeps buffering in the background (up to `--live-buffer-minutes`, default 10) so resuming (manually or automatically once the limit's reached) continues from where you paused rather than jumping back to live -- use mpv's normal seek keys (`LEFT`/`RIGHT`, etc.) to rewind/fast-forward within that window. Recorded/played-back files just pause normally, with no time limit. |
| `r` | Toggle recording the current stream to disk as a raw copy (no re-encoding), saved under `--record-dir` as `<channel>_<timestamp>.ts`. |
| `o` | Toggle picture-in-picture: shrinks the window to a small, always-on-top, borderless corner window (bottom-right, ~25% size) so you can keep watching while using other apps; press again to restore. Closes any open guide/browser overlay first. Relies on the window manager honoring mpv's placement request -- confirmed working on GNOME/Mutter, but some Wayland compositors may only shrink/keep-on-top without actually relocating the window. |
| `t` | Toggle subtitles on/off, if the current stream has a subtitle track (e.g. many UK DVB broadcasts carry one). Reports "No subtitles available" if it doesn't. To pick a different subtitle track (e.g. a different language), use mpv's own default `j`/`J` keys, left untouched. |
| `s` | While programme details are shown (guide only): schedule that programme to record automatically, switching channels and starting/stopping the recording at its start/stop time even if you're watching something else -- press again to cancel. Saved to `--schedule-file`; only fires while tvdinner is running. A scheduled programme shows a small red "R" badge in the guide. |
| `w` | Browse past recordings from `--record-dir`, grouped by date -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to play it back (resuming where you left off, if you didn't finish it last time -- see `--playback-positions-file`), `d` twice to permanently delete the selected one (the first press just arms the confirmation), `ESC` to close. |
| `u` | Browse upcoming scheduled recordings (see the `s` guide keybinding above), soonest first, marking whichever one is currently recording -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to cancel the selected one, `ESC` to close. Since only one recording can happen at a time, an overlapping schedule that never got a turn shows up here (and as an on-screen notification) under "Missed", with the reason why. |
| `l` | [Plex](#plex-media-server) sessions only: (re)open the library browser -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to drill into a library/show/season or play a movie/episode, `ESC` to go back a level (or close it, from the top level). |
| `/` | While the Plex library browser is open: search the whole server via Plex's own search API -- `ENTER` runs the search and shows results as a new browsable list, `ESC` cancels. |
| `a` | Toggle an about card: logo, app name, version, and a one-line summary -- press again or `ESC` to close. |
| `?` | Toggle a keyboard-shortcuts cheat sheet listing every binding above -- press again or `ESC` to close. |

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT — see [LICENSE](LICENSE).
