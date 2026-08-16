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
[Windows installer](#windows-installer)) that bundles its own mpv so
nothing else needs installing first.

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

- Linux (developed against Ubuntu 26.04+) or Windows
- `mpv` on Linux (the Windows installer bundles its own; only needed
  separately if running from source)
- Python 3.10+ (not needed at all with the Windows installer, which
  bundles its own)

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

## Usage

```
tvdinner [OPTIONS] URL
tvdinner                                                    (same as `tvdinner bookmarks`)
tvdinner bookmarks [--bookmarks-file PATH]
tvdinner backup [PATH] [--epg-shifts PATH] [--favorites PATH] [--bookmarks-file PATH] [--tmdb-token-file PATH] [--gdrive [--gdrive-filename NAME] [--gdrive-token-file PATH]]
tvdinner restore [PATH] [--epg-shifts PATH] [--favorites PATH] [--bookmarks-file PATH] [--tmdb-token-file PATH] [-y] [--gdrive [--gdrive-filename NAME] [--gdrive-token-file PATH]]
tvdinner gdrive-login [--client-id ID] [--client-secret SECRET] [--gdrive-token-file PATH]
tvdinner gdrive-logout [--gdrive-token-file PATH]
tvdinner stats [--bookmarks-file PATH] [--history-file PATH]
tvdinner store-tmdb TOKEN [--tmdb-token-file PATH]
tvdinner clear-tmdb [--tmdb-token-file PATH]
tvdinner hard-reset [--epg-shifts PATH] [--favorites PATH] [--bookmarks-file PATH] [--tmdb-token-file PATH] [--schedule-file PATH] [--playback-positions-file PATH] [--history-file PATH] [-y]
```

`URL` may be an M3U/M3U8 playlist (http(s) or a local file path), an
[Xtream Codes](#xtream-codes) login (`xtream://username:password@host:port`),
a [Stalker Portal](#stalker-portal) login
(`stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF`), an
[HDHomeRun](#hdhomerun) tuner (`hdhomerun://host[:port]`), a
[Plex Media Server](#plex-media-server) login
(`plex://host:port?X-Plex-Token=...`), a direct video/audio stream URL, a
local video file (e.g. a movie) to play directly -- see [Local
files](#local-files) below -- or a YouTube video URL -- see
[YouTube](#youtube) below. If it resolves to a channel list, playback
starts on the channel given by `--channel`, or the first channel otherwise
— use the program guide (see Keybindings below) to switch channels without
restarting. A Plex URL is different: there's no channel list, just a
library browser (see [Plex Media Server](#plex-media-server) below).

`tvdinner bookmarks` opens an interactive terminal table of saved
playlists instead -- as does running `tvdinner` with no arguments at
all, rather than argparse's usual "the following arguments are
required" error, since picking from what's already saved is the
natural thing to want with nothing else typed: `a` adds one
(description, URL -- anything the `URL` argument above accepts,
optional EPG URL, optional default channel e.g.
`CNN`, optional [TMDB API token](#tmdb-ratings)), `e` edits the selected
one, `d` deletes it (with confirmation), `SPACE` toggles that row's "EPG
Refresh" checkbox (unchecked by default, and not remembered between
sessions), and `ENTER` launches tvdinner with it, exactly as if its
URL/`--epg`/`--channel`/`--tmdb-api-token` had been typed directly --
adding `--refresh-epg-cache` too if the checkbox was checked. The table
itself never shows a saved token, only a `[x]`/`[ ]` indicator for
whether one is set. Saved to `~/.config/tvdinner/bookmarks.json` by
default (`%APPDATA%\tvdinner\bookmarks.json` on Windows).

`tvdinner backup` writes the EPG shifts, favorites, bookmarks, and
stored default TMDB token (see below) files into a single compressed
archive for offline storage or moving to another machine (default
filename: `tvdinner-backup-<timestamp>.zip` in the current directory;
the EPG cache and log file are deliberately left out, since they're
disposable, not configuration). `tvdinner restore` extracts a backup
archive back onto disk, overwriting the current files — it prompts for
confirmation unless `-y`/`--yes` is given. Add `--gdrive` to either
command to use Google Drive instead of/alongside a local file --
`tvdinner backup --gdrive` still writes the local archive too, then
uploads it; `tvdinner restore --gdrive` downloads it instead of taking
a local `PATH` (omit `PATH` in that case). See [Google Drive
backup](#google-drive-backup) below for one-time setup.

`tvdinner stats` prints a table of on-disk cache usage: one row per
[bookmarked](#usage) feed's EPG cache, for whichever bookmarks have a
deterministically knowable EPG source without fetching anything -- an
explicit saved EPG URL, or an Xtream login's own `xmltv.php` export --
plus the caches every feed shares regardless of source (TMDB
ratings/metadata, channel logos/poster art,
[iptv-org](https://github.com/iptv-org/api)'s online logo database, and
the log/[watch history](#watch-history) files). A bookmark relying on
M3U auto-discovery (`x-tvg-url`, which needs an actual playlist fetch
to resolve) or with no EPG at all
(Stalker, HDHomeRun without a DVR subscription, Plex) is listed as
unknown rather than guessed; its cache still counts toward the "other"
total. Nothing here is fetched over the network -- it only reads
whatever's already on disk.

`tvdinner hard-reset` deletes every file and directory tvdinner itself
writes -- bookmarks, favorites, EPG shifts, a stored default TMDB
token, scheduled recordings, playback positions, watch history,
update-check state, the EPG/TMDB/image caches, and the log file --
reverting it to exactly the state a fresh install would be in. It
prompts for confirmation (listing every path first) unless `-y`/`--yes`
is given, same as
`tvdinner restore`. **It never touches `--record-dir`** -- a recording
is real media you made, not disposable app state, so resetting
tvdinner has no business deleting it.

### Google Drive backup

`tvdinner backup --gdrive`/`tvdinner restore --gdrive` store/fetch the
backup archive in Google Drive instead of (or in addition to, for
backup) a local file, using an app-created file only -- tvdinner never
sees the rest of a Drive account's contents.

```
tvdinner gdrive-login
```

opens a browser for Google's sign-in/consent screen (using tvdinner's
own bundled OAuth client -- see below -- so there's no Google Cloud
Console setup needed), then stores the resulting credentials at
`~/.config/tvdinner/gdrive_token.json`
(`%APPDATA%\tvdinner\gdrive_token.json` on Windows): a refresh token
plus the client ID/secret, never the account password. Since the app
isn't Google-verified, the consent screen shows an "unverified app"
warning first -- click "Advanced" then "Go to tvdinner (unsafe)" to
proceed; this is normal for a small open-source tool and doesn't mean
anything is actually wrong (see below for why).

From then on:

```
tvdinner backup --gdrive     # writes the local archive, then uploads it
tvdinner restore --gdrive    # downloads it and restores, prompting first
```

Both default to a Drive file named `tvdinner-backup.zip`
(`--gdrive-filename NAME` to use a different one -- e.g. one per
machine); backing up again updates that same file rather than creating
a duplicate. `tvdinner gdrive-logout` removes the stored credentials
locally (it doesn't revoke Google's own record of the grant -- see
[myaccount.google.com/permissions](https://myaccount.google.com/permissions)
to do that).

If you'd rather not share tvdinner's bundled OAuth client's request
quota, bring your own: in [Google Cloud
Console](https://console.cloud.google.com/), create a project, enable
the **Google Drive API** for it (APIs & Services → Library), create an
OAuth client of type **Desktop app** under Credentials (or the newer
Google Auth Platform → Clients), then
`tvdinner gdrive-login --client-id ID --client-secret SECRET`
(only needed the first time, or after `gdrive-logout` -- a later
`gdrive-login` reuses whichever client is already stored if omitted).

*Why a bundled client secret is fine here:* for an OAuth "Desktop app"
client, the secret isn't actually confidential -- the app can't keep it
hidden from whoever's running it, so [RFC
8252](https://www.rfc-editor.org/rfc/rfc8252) (OAuth for Native Apps)
and Google's own docs both treat it as a public identifier rather than
something to protect. The real security boundary is PKCE plus each
user's own consent-screen approval, same as with any other installed-
app OAuth client.

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
| `--glsl-shader PATH` | A custom GLSL shader file (e.g. an Anime4K or FSRCNNX shader) to apply on top of mpv's own built-in high-quality scalers (hardware decoding and mpv's `gpu-hq` scaling profile are both always on). Repeatable to layer several, applied in the order given. Off by default: custom shaders can be significantly heavier on the GPU than the built-in scalers alone. |
| `--interpolation` | Smooth motion by interpolating between frames (mpv's `interpolation` plus `video-sync=display-resample`). Off by default: only actually helps when the display's refresh rate is a clean multiple of the video's frame rate, adds GPU cost, and changes how mpv times playback against audio. |
| `--playback-positions-file PATH` | JSON file remembering where you left off in each recording (see the `w` recordings browser), so reopening one resumes instead of starting over (default: `~/.config/tvdinner/playback_positions.json` on Linux, `%APPDATA%\tvdinner\playback_positions.json` on Windows). |
| `--history-file PATH` | JSONL file logging what's watched (channel/VOD/recording), when, and for how long -- browse it with the `x` keybinding (default: `~/.config/tvdinner/history.jsonl` on Linux, `%APPDATA%\tvdinner\history.jsonl` on Windows). See below. |
| `--no-history` | Don't record watch history. |
| `--epg-cache-hours HOURS` | How long a downloaded EPG is reused from disk before re-fetching (default: 24). |
| `--no-epg-cache` | Always re-download the EPG instead of using a cached copy, and don't write one either. |
| `--refresh-epg-cache` | Force a fresh EPG download for this run, ignoring any existing cached copy no matter its age, then refresh the on-disk cache with it (unlike `--no-epg-cache`, later runs still benefit from the cache). |
| `--no-online-logos` | Don't fall back to [iptv-org](https://github.com/iptv-org/api)'s community channel/logo database for channels with no logo of their own or in their EPG (common for bare M3U playlists) -- on by default, sharing `--epg-cache-hours`/`--no-epg-cache`/`--refresh-epg-cache`'s caching. |
| `--tmdb-api-token TOKEN` | TMDB v4 read-access Bearer token -- enables a gold star rating (e.g. `★ 7.6`) plus the required `TMDB` attribution mark on movie programmes in the guide grid and details popup; the details popup also shows the director, falling back to TMDB only when the EPG feed doesn't already tag one itself (see below). Movies only, matched by programme category. Ratings are fetched in the background and cached on disk for 30 days. Off by default; overrides any token saved via `tvdinner store-tmdb`. For a [local video file](#local-files), this instead enables the `i` overlay's poster/synopsis/rating/director. See below. |
| `--tmdb-token-file PATH` | Where `tvdinner store-tmdb`/`tvdinner clear-tmdb` read/write the default TMDB token (default: `~/.config/tvdinner/tmdb_token.json` on Linux, `%APPDATA%\tvdinner\tmdb_token.json` on Windows). |
| `--title TITLE` | [Local video file](#local-files) playback only: override the guessed movie title used for the `--tmdb-api-token` lookup. |
| `--year YEAR` | [Local video file](#local-files) playback only: override the guessed release year used for the `--tmdb-api-token` lookup. |
| `--no-update-check` | Don't check GitHub Releases for a newer tvdinner version at startup -- on by default, at most once every 24 hours, cached in a small local file so most launches don't touch the network at all. See below. |
| `--log-file PATH` | Where to log startup/shutdown, user actions, and warnings/errors (default: `~/.cache/tvdinner/tvdinner.log` on Linux, `%LOCALAPPDATA%\tvdinner\tvdinner.log` on Windows). Capped at 5MB with one rotated backup (`tvdinner.log.1`), so it never grows without bound. |
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

# Play a local movie file, with TMDB metadata for the 'i' overlay
tvdinner ~/Videos/'His Girl Friday (1940).webm' --tmdb-api-token TOKEN
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
libraries as a TUI overlay, each row showing its poster/cover art plus
year, content rating, and Plex's own audience score, all fetched from
Plex itself with no extra lookups: arrows/`PGUP`/`PGDWN` to move, `ENTER` to
drill in (library → show → season → episode) or play a movie/episode,
`ESC` to go back a level or close the browser, and `l` to reopen it
later -- right back where you left off, not the library root, even
after starting playback. Press `/` at any point to search the whole
server via Plex's own search API, not just whatever's currently on
screen. Playback is always
direct-play (the file's own container/codecs, streamed straight from
Plex) -- tvdinner never asks Plex to transcode, so a file mpv can't
decode on its own won't play here even if it would in Plex's own apps.
Once something's playing, `i` shows a poster/synopsis/rating/director/
progress overlay pulled from Plex's own metadata, and resuming/
reconnecting on a dropped connection works the same as any other
on-demand source (see `--playback-positions-file` below).

Finding your token: play anything in Plex Web, open your browser's dev
tools → Network tab, and look for `X-Plex-Token=...` in any request's
query string (or see
[Plex's own instructions](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)).
Like the Xtream Codes/Stalker Portal cases above, a `plex://` URL's token
is stored as plain text wherever the source URL itself is stored
(`bookmarks.json`, backup archives); it's shown redacted (first four
characters kept, the rest masked) in the log file.

### Local files

`URL` can also be a local video file, played directly -- no
playlist/EPG/channel/Plex library involved, just mpv pointed at a file
on disk:

```
tvdinner ~/Videos/'His Girl Friday (1940).webm'
```

It's told apart from a local M3U playlist by content, not extension (the
first few KB are sniffed for `#EXTM3U`), so a genuine playlist file still
loads as one as always. Since a local video file carries no provider
metadata of its own, tvdinner guesses its movie identity from the
filename -- a `19xx`/`20xx` year anywhere in it, in parens/brackets/
dashes/dots (e.g. `Title (Year).ext`, `Title.Year.1080p.BluRay.x264-
GROUP.mkv`, or a yt-dlp download's `Year - Title - Cast - Tagline
[videoID].ext`, all naming conventions real tools produce), plus
whatever text sits on the more informative side of it -- and, if
`--tmdb-api-token` is given, looks that guess up on
[TMDB](https://www.themoviedb.org/) in the background, trying a couple
of candidate search strings the same way a [YouTube](#youtube) title
does (see below) since a filename can chain the same kind of cast/
tagline noise onto the real title, so `i` shows the same poster/
synopsis/rating/progress overlay as a Plex or Xtream/Stalker VOD item
(see the `i` keybinding below) -- without a token, `i` still shows the
guessed title and playback progress, just without the TMDB-sourced
fields. `--title`/`--year` (see Options above) override a bad guess
without renaming the file. Resuming
(`--playback-positions-file`) and `r`-key recording (`--record-dir`) both
work the same as anywhere else. A local video file's path also works as
a [bookmark](#usage)'s URL, complete with its own saved TMDB token --
handy for a small, frequently-rewatched local collection.

### YouTube

`URL` can also be a plain YouTube video URL (`youtube.com/watch?v=...`,
`youtu.be/...`, or `youtube.com/shorts/...`) -- mpv already plays these
directly via its `ytdl_hook` script, which shells out to a separate
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (or `youtube-dl`) binary on
`PATH` to resolve the actual stream (`sudo apt install yt-dlp`, `sudo dnf
install yt-dlp`, or `pip install yt-dlp` -- not bundled by tvdinner or by
mpv itself, on any platform including the Windows installer), so this is
really about getting the `i` overlay working for them too:

```
tvdinner https://www.youtube.com/watch?v=wEx-z1TYPKU
```

Unlike a local file, a YouTube video's title/uploader/thumbnail are
fetched for free from YouTube's own public oEmbed endpoint (no API key,
no `--tmdb-api-token` needed) as soon as playback starts, in the
background -- `i` shows them once that lands (mpv's own window title,
set by its yt-dlp hook, is untouched either way). If `--tmdb-api-token`
is given, tvdinner additionally tries a TMDB lookup using that title;
`--title`/`--year` override it outright, otherwise the title itself is
tried as a couple of candidate search strings in turn -- its first
` - `/`|` segment (many archive-channel and official-studio titles
chain cast names/taglines/genre tags onto the real movie name this way,
e.g. "1940 - His Girl Friday - Cary Grant and Rosalind Russell - ..."
splits to just "His Girl Friday", and "McLintock! | FULL MOVIE | John
Wayne, Maureen O'Hara | Western Rancher Cowboy Comedy" splits to just
"McLintock!"), then the whole remainder unsplit as a broader fallback
for a movie whose real title happens to contain one of those
separators -- whether or not the title carries a year at all. A
successful TMDB match replaces the oEmbed poster/description with TMDB's
poster/synopsis/rating/director (falling back to YouTube's own thumbnail
if TMDB has no poster). Resuming
(`--playback-positions-file`) works the same as any other VOD source. A
YouTube URL also works as a [bookmark](#usage)'s URL.

### Casting

Press `k` at any point to cast whatever's currently playing (a live
channel, a VOD item, or a Plex movie/episode) to a Chromecast device on
your LAN: arrows to move, `ENTER` to connect, `ESC` to close. tvdinner
tells the device to fetch and play the same stream URL it's itself
playing -- it never proxies or transcodes the stream, so a codec/container
the device's receiver can't decode natively (raw MPEG-TS, the common
shape for a live IPTV channel URL, has only limited support on real
Chromecast hardware) may simply fail to cast even though it plays fine
locally. Local playback pauses for the duration of the cast (freeing up
local decode/bandwidth) and resumes automatically from the same position
once you disconnect -- reopen the picker with `k` while casting and a red
"Disconnect" entry appears above the device list.

Chromecast support is an **optional extra**, not installed by default.
`tvdinner[chromecast]` isn't a package name you can `pip install`
directly (there's no PyPI release yet -- see Install above), so install
it from a checkout the same way as the base install, just with the
extra added:

```
python3 -m venv .venv && .venv/bin/pip install ".[chromecast]"
```

This needs Python 3.11+ (pychromecast's own requirement -- tvdinner
itself still supports 3.10). On Debian/Ubuntu, `apt install
python3-pychromecast python3-zeroconf` works instead; on Fedora/RHEL/
openSUSE there's no distro package at all, so use
`sudo pip install --prefix=/usr pychromecast` (see the RPM spec's own
notes on why `--prefix=/usr` specifically, next to the same advice for
python-mpv). Without it installed, `k` shows a message saying so instead
of a device list -- every other feature works unaffected. Discovery uses
mDNS (UDP multicast); Windows may prompt for a firewall permission the
first time `k` is pressed.

### Update checks

tvdinner checks GitHub Releases for a newer version at startup, at most
once every 24 hours (cached locally, so most launches don't touch the
network at all). If a newer release is found, a card appears over the
video: `y` opens the release page in your browser so you can download
and install it your platform's normal way -- there's no silent
self-update on any platform (the three packages have too little in
common: a Windows install can safely self-upgrade in place, but
`.deb`/`.rpm` need root and have no hosted repo). `n` or `ESC` dismisses
the card instead. Either way, that specific version won't be shown again -- a
genuinely newer release still notifies normally. Disable checking
entirely with `--no-update-check`.

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

### TMDB ratings

`--tmdb-api-token` adds a gold star rating (e.g. `★ 7.6`) to movie
programmes in the guide grid and details popup, sourced from
[TMDB](https://www.themoviedb.org/) and matched by title/year against
the programme's category. Get a free token from TMDB: create an
account, then under
[Settings -> API](https://www.themoviedb.org/settings/api) request an
API key (any use case description is fine) and copy the "API Read
Access Token" (the long JWT-looking string, not the shorter "API Key")
-- that's the value `--tmdb-api-token` wants. Ratings are fetched in
background threads (never blocking guide rendering) and cached on disk
for 30 days, since a vote average barely moves day to day. Off by
default; the `TMDB` attribution mark shown alongside every rating is
required by TMDB's API terms.

The `i` overlay (both the compact current/next-programme banner and
the guide's full details popup, plus the VOD info overlay for a local
file or YouTube video) also shows the movie's director, when
available. Some EPG feeds already tag this themselves (XMLTV's
`<credits><director>`) -- that's used directly, for free, with no
token required. Only when a feed doesn't provide one does a
`--tmdb-api-token` fall back to a TMDB lookup, and unlike rating,
that fallback isn't bulk-fetched for every movie visible in the guide
grid -- only for the one programme currently shown in an `i` overlay,
so a fresh view sometimes shows no director yet that way; reopening
it picks it up once fetched.

Retyping `--tmdb-api-token` on every invocation gets old fast, so
there are two ways to save one instead, checked in this order:

1. **Per bookmark** (see `tvdinner bookmarks` above) -- like the
   Xtream/Stalker/Plex credentials above, it's stored as plain text in
   `bookmarks.json`, but unlike those it's never even partially shown
   in the log file (fully masked, not just redacted). Launching that
   bookmark applies its token the same as typing `--tmdb-api-token`
   directly would.
2. **A global default**, via `tvdinner store-tmdb TOKEN` -- applies to
   every invocation that doesn't otherwise specify one (directly or
   via a bookmark), stored as plain text in
   `~/.config/tvdinner/tmdb_token.json` by default
   (`%APPDATA%\tvdinner\tmdb_token.json` on Windows; override with
   `--tmdb-token-file`). `tvdinner clear-tmdb` removes it.

An explicit `--tmdb-api-token` (typed directly, or carried by a
launched bookmark) always overrides the global default.

### Watch history

tvdinner logs what you watch -- live channel, VOD item, or recording,
with when and for how long -- to `~/.config/tvdinner/history.jsonl`
(`%APPDATA%\tvdinner\history.jsonl` on Windows; override with
`--history-file`). Press `x` during playback to browse it: newest
first, grouped by day, with a thumbnail (a VOD's poster, a channel's
logo, or -- for a recording -- an actual frame grabbed from the video
itself the first time it's shown, then cached), duration, and -- for a
movie with `--tmdb-api-token` or Plex metadata available -- year,
rating, and director too. It's a read-only viewer for now, not a
launcher (see below).

One JSON object per line, oldest first:

```json
{"kind": "channel", "title": "BBC One", "url": "https://.../bbc1.m3u8", "playlist_source": "https://.../playlist.m3u", "started_at": "2026-08-15T20:00:00+00:00", "ended_at": "2026-08-15T20:41:12+00:00", "duration_seconds": 2472.0, "image_url": "https://.../bbc1-logo.png", "year": null, "rating": null, "rating_is_tmdb": false, "director": null}
```

`kind` is `channel`, `vod`, or `recording`; `playlist_source` is the
playlist/login/server it came from (`null` for a local file, YouTube
video, or bare direct-stream URL, none of which have one). `image_url`
is a VOD item's poster, a channel's own logo, or (for a `recording`) a
`tvdinner-recording-thumb://<path>` marker resolved to an actual frame
captured from that recording's own file when the history browser needs
it (`null` only when no image is available at all); `year`/`rating`/
`rating_is_tmdb`/`director` are only ever populated for a `vod` entry,
and only when the source actually supplied them. A watch under 5
seconds isn't recorded
at all, so flipping past a channel while browsing the guide doesn't
clutter the log. Reconnecting after a dropped stream doesn't start a
new entry -- it's still the same watch, just interrupted.

Disable entirely with `--no-history`; `tvdinner hard-reset` deletes it
along with everything else tvdinner stores. Not included in `tvdinner
backup`/`restore` -- like playback positions and the schedule, it's
accumulated data, not configuration to carry to a new machine.

### Keybindings

In addition to `mpv`'s own default key bindings:

| Key | Action |
| --- | --- |
| `i` | Show the current/next programme info overlay (with video/audio quality badges: resolution, codecs, fps, HDR, channel layout, and a movie's director when available -- see [TMDB ratings](#tmdb-ratings)); while the program guide is open, shows full details for the selected guide programme instead. While watching back a recording, shows its own label, recorded date, and playback progress instead of live EPG info. While playing a VOD/[Plex](#plex-media-server)/[local file](#local-files)/[YouTube](#youtube) video, shows its poster, synopsis, rating, director, and playback progress instead (Plex populates all of that; a local file or YouTube video gets it from a background lookup -- YouTube's own oEmbed always, TMDB additionally if `--tmdb-api-token` was given; other VOD sources show whatever fields they have). |
| `g` / `MENU` | Toggle the full program guide (`MENU` is the button most IR/BLE air-mouse remotes send for their MENU key). |
| `b` | Switch to the last watched channel (like a TV remote's "last channel" button) -- repeated presses toggle back and forth between the two, since every switch (guide or `b` itself) remembers whatever was playing right before it. No-op if nothing's been switched away from yet this session. |
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
| `z` | Cycle the video's display aspect ratio (Auto, 4:3, 16:9, 2.35:1, 1:1). |
| `p` / `PLAY` / `PAUSE` / `PLAYPAUSE` | Pause/resume live TV (the last three are the key names mpv reports for a remote's dedicated play/pause button). While paused, the stream keeps buffering in the background (up to `--live-buffer-minutes`, default 10) so resuming (manually or automatically once the limit's reached) continues from where you paused rather than jumping back to live -- use mpv's normal seek keys (`LEFT`/`RIGHT`, etc.) to rewind/fast-forward within that window. Recorded/played-back files just pause normally, with no time limit. |
| `r` | Toggle recording the current stream to disk as a raw copy (no re-encoding), saved under `--record-dir` as `<channel>_<timestamp>.ts`. |
| `o` | Toggle picture-in-picture: shrinks the window to a small, always-on-top, borderless corner window (bottom-right, ~25% size) so you can keep watching while using other apps; press again to restore. Closes any open guide/browser overlay first. Relies on the window manager honoring mpv's placement request -- confirmed working on GNOME/Mutter, but some Wayland compositors may only shrink/keep-on-top without actually relocating the window. |
| `t` | Toggle subtitles on/off, if the current stream has a subtitle track (e.g. many UK DVB broadcasts carry one). Reports "No subtitles available" if it doesn't. To pick a different subtitle track (e.g. a different language), use mpv's own default `j`/`J` keys to cycle through them. |
| `s` | While programme details are shown (guide only): schedule that programme to record automatically, switching channels and starting/stopping the recording at its start/stop time even if you're watching something else -- press again to cancel. Saved to `--schedule-file`; only fires while tvdinner is running. A scheduled programme shows a small red "R" badge in the guide. |
| `w` | Browse past recordings from `--record-dir`, grouped by date -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to play it back (resuming where you left off, if you didn't finish it last time -- see `--playback-positions-file`), `d` twice to permanently delete the selected one (the first press just arms the confirmation), `ESC` to close. |
| `u` | Browse upcoming scheduled recordings (see the `s` guide keybinding above), soonest first, marking whichever one is currently recording -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to cancel the selected one, `ESC` to close. Since only one recording can happen at a time, an overlapping schedule that never got a turn shows up here (and as an on-screen notification) under "Missed", with the reason why. |
| `l` | [Plex](#plex-media-server) sessions only: (re)open the library browser -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to drill into a library/show/season or play a movie/episode, `ESC` to go back a level (or close it, from the top level). |
| `/` | While the Plex library browser is open: search the whole server via Plex's own search API -- `ENTER` runs the search and shows results as a new browsable list, `ESC` cancels. |
| `k` | Open the [Chromecast](#casting) device picker for whatever's currently playing -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move, `ENTER` to connect, `ESC` to close. While already casting, reopening shows a red "Disconnect" entry above the device list. Requires the optional `pychromecast` extra -- see Casting below. |
| `x` | Browse [watch history](#watch-history) -- every channel/VOD item/recording actually watched, newest first, grouped by day, with a thumbnail (a VOD's poster, a channel's logo, or a frame grabbed from a recording's own video), duration, and (for movies) year/rating/director. `UP`/`DOWN`/`PGUP`/`PGDWN` to scroll, `ENTER`/`ESC` to close -- a read-only viewer, not a launcher. |
| `a` | Toggle an about card: logo, app name, version, and a one-line summary -- press again or `ESC` to close. |
| `y` / `n` | Only shown on the [update-available card](#update-checks) (appears automatically, at most once every 24 hours, when a newer release exists): `y` opens the release page in your browser, `n` (or `ESC`) dismisses it. Either way that version won't be shown again. |
| `?` | Toggle a keyboard-shortcuts cheat sheet listing every binding above -- press again or `ESC` to close. |

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT — see [LICENSE](LICENSE).
