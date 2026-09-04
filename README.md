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

This README covers install and the full command/options reference.
For a deeper walkthrough of each source (M3U, Xtream Codes, Stalker
Portal, HDHomeRun, Plex, local files, YouTube) and feature (program
guide, recording & scheduling, casting, TMDB ratings, backup, and
more), see the **[wiki](https://github.com/issinoho/tvdinner/wiki)**.

## Screenshots

The full program guide — channels down the left, a timeline across the
top, and a live "now" marker:

![Program guide](screenshots/guide.png)

A cinematic "now playing" hero for a movie you're watching, live or on
demand — TMDB backdrop art (or Plex's own), shown full-bleed and
translucent over the picture, with a TMDB title logo top-right,
rating, director, synopsis, and what's on next:

![Netflix-style backdrop hero overlay for a movie, with its TMDB title logo in the top-right corner](screenshots/backdrop-hero.png)

The on-screen EPG banner — shown on channel switch or with `i` for
anything that isn't a TMDB-backdrop-matched movie (see above) — current
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
and `fonts-dejavu-core` as dependencies, installs the `tvdinner(1)` man
page, and installs a desktop entry + icon so tvdinner shows up as an
opener for `.m3u`/`.m3u8` files (see [Desktop
integration](#desktop-integration-linux) below). `desktop-file-utils`
is a Recommends -- it just keeps the "Open With" menu's cache current.

### Fedora/RHEL/openSUSE package

Build **on the target distribution** (or in a `mock`/chroot matching it),
not on Debian/Ubuntu -- the spec relies on that distro's own
`python3-rpm-macros` package to resolve `%{python3_sitelib}` and
`%py3_build`/`%py3_install` correctly for its Python version:

```
sudo dnf install rpm-build python3-devel python3-setuptools python3-pip
git archive --format=tar.gz --prefix=tvdinner-1.0.0/ HEAD -o ~/rpmbuild/SOURCES/tvdinner-1.0.0.tar.gz
rpmbuild -bb rpm/tvdinner.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/tvdinner-1.0.0-1.*.noarch.rpm
```

This pulls in `mpv`, `python3-pillow`, `python3-requests`,
`dejavu-sans-fonts`, and `hicolor-icon-theme` as dependencies, and
installs a desktop entry + icon (see [Desktop
integration](#desktop-integration-linux) below). `python-mpv`
(tvdinner's Python binding to mpv) has no Fedora/RHEL RPM equivalent, so
it's deliberately left off the spec's `Requires` -- install it
separately first, e.g. `pip install --user python-mpv`.

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

### Desktop integration (Linux)

The `.deb` and `.rpm` install `/usr/share/applications/tvdinner.desktop`
(plus an icon), which registers tvdinner as an opener for `.m3u` /
`.m3u8` files **and for `tvdinner:` links** -- so it appears in a file
manager's *Open With* menu, in a browser's "what should I do with this
file" prompt, and a `tvdinner:<url>` link hands the URL straight to it
(no download). The entry is `Terminal=true`: tvdinner is a
keyboard-driven TUI, so the desktop launches it inside your terminal
emulator, with mpv's own video window alongside.

It does **not** make itself the default handler (an `.m3u` is just as
often a local music playlist), so the first double-click still shows an
application picker. To make tvdinner the default and skip that dialog:

```
tvdinner default-handler
```

That's a wrapper around `xdg-mime default tvdinner.desktop <the M3U MIME
types + x-scheme-handler/tvdinner>` — it writes your own
`~/.config/mimeapps.list` (no root, nothing system-wide) and verifies
the result. Undo it from a file manager's *Open With* dialog, or by
editing that file. The equivalent by hand:

```
xdg-mime default tvdinner.desktop audio/x-mpegurl audio/mpegurl application/x-mpegurl application/vnd.apple.mpegurl x-scheme-handler/tvdinner
xdg-mime query default audio/x-mpegurl      # confirm: tvdinner.desktop
```

Running from source (no package)? `tvdinner default-handler` also drops
a `~/.local/share/applications/tvdinner.desktop` for you when it can't
find an installed one. To do it by hand:

```
install -Dm644 data/tvdinner.desktop ~/.local/share/applications/tvdinner.desktop
install -Dm644 data/tvdinner.svg ~/.local/share/icons/hicolor/scalable/apps/tvdinner.svg
update-desktop-database ~/.local/share/applications
```

A plain `https://…/playlist.m3u` *link* is still the browser's call --
it typically downloads the file and then opens it through this entry;
there's no handler hook for `https` itself. A **`tvdinner:` link**
(`tvdinner:https://…/playlist.m3u`) *does* have a hook: after
`default-handler`, the browser hands it straight to tvdinner (one
remembered "Open tvdinner?" prompt), no file saved. tvtimes' "Play"
button emits one of these on desktop.

A **`tvtimes:` link** (`tvtimes://host?token=…`, or `tvtimess://` for
https) is hooked the same way, and hands over a whole
[tvtimes](#tvtimes) account -- its entire merged line-up and guide,
rather than the one channel a `tvdinner:` Play link carries. tvtimes'
**Settings → Export feeds** panel has an "Open in tvdinner" button that
emits one.

### Windows installer

Download `tvdinner-setup-<version>.exe` from the
[latest release](https://github.com/issinoho/tvdinner/releases/latest)
and run it. It bundles a pre-built mpv (see
[windows/THIRD_PARTY_NOTICES.txt](windows/THIRD_PARTY_NOTICES.txt) for
its license) and everything else tvdinner needs -- there's no separate
Python or mpv install step. The installer is built to be code-signed
via the [SignPath Foundation](https://signpath.org)'s free code
signing program for open source software (see
[CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)); until that's live,
releases stay unsigned and Windows SmartScreen will show an
"unrecognized app" warning on first run -- click "More info" then "Run
anyway" to proceed. An optional install step adds tvdinner to your
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
tvdinner bookmarks list [--json] [--bookmarks-file PATH]
tvdinner bookmarks add --name NAME --url URL [--epg URL] [--channel C] [--tmdb-api-token TOKEN] [--replace] [--json] [--bookmarks-file PATH]
tvdinner bookmarks edit NAME|INDEX [--name NAME] [--url URL] [--epg URL | --clear-epg] [--channel C | --clear-channel] [--tmdb-api-token TOKEN | --clear-tmdb-api-token] [--json] [--bookmarks-file PATH]
tvdinner bookmarks remove NAME|INDEX [--json] [--bookmarks-file PATH]
tvdinner backup [PATH] [--epg-shifts PATH] [--favorites PATH] [--bookmarks-file PATH] [--tmdb-token-file PATH] [--gdrive [--gdrive-filename NAME] [--gdrive-token-file PATH]]
tvdinner restore [PATH] [--epg-shifts PATH] [--favorites PATH] [--bookmarks-file PATH] [--tmdb-token-file PATH] [-y] [--gdrive [--gdrive-filename NAME] [--gdrive-token-file PATH]]
tvdinner gdrive-login [--client-id ID] [--client-secret SECRET] [--gdrive-token-file PATH] [--no-browser]
tvdinner gdrive-logout [--gdrive-token-file PATH]
tvdinner stats [--bookmarks-file PATH] [--history-file PATH]
tvdinner store-tmdb TOKEN [--tmdb-token-file PATH]
tvdinner clear-tmdb [--tmdb-token-file PATH]
tvdinner default-handler
tvdinner hard-reset [--epg-shifts PATH] [--favorites PATH] [--bookmarks-file PATH] [--tmdb-token-file PATH] [--schedule-file PATH] [--playback-positions-file PATH] [--history-file PATH] [-y]
```

`URL` may be an M3U/M3U8 playlist (http(s) or a local file path), an
[Xtream Codes](#xtream-codes) login (`xtream://username:password@host:port`),
a [Stalker Portal](#stalker-portal) login
(`stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF`), an
[HDHomeRun](#hdhomerun) tuner (`hdhomerun://host[:port]`), a
[Plex Media Server](#plex-media-server) login
(`plex://host:port?X-Plex-Token=...`), a [tvtimes](#tvtimes) account
(`tvtimes://host[:port]?token=...`), a direct video/audio stream URL, a
local video file (e.g. a movie) to play directly -- see [Local
files](#local-files) below -- or a YouTube video URL -- see
[YouTube](#youtube) below. If it resolves to a channel list, playback
starts on the channel given by `--channel`, or the first channel otherwise
— use the program guide (see Keybindings below) to switch channels without
restarting. A Plex URL is different: there's no channel list, just a
library browser (see [Plex Media Server](#plex-media-server) below, or
the [wiki page](https://github.com/issinoho/tvdinner/wiki/Plex-Media-Server)
for the full walkthrough).

`tvdinner bookmarks` opens an interactive terminal table of saved
playlists instead -- as does running `tvdinner` with no arguments at
all, rather than argparse's usual "the following arguments are
required" error, since picking from what's already saved is the
natural thing to want with nothing else typed: `a` adds one
(description, URL -- anything the `URL` argument above accepts,
optional EPG URL, optional default channel e.g.
`CNN`, optional [TMDB API token](#tmdb-ratings), optional tvtimes device
name), `e` edits the selected
one, `d` deletes it (with confirmation), `K`/`J` moves the selected row
up/down the list (saved immediately, same as add/edit/delete), `SPACE`
toggles that row's "EPG Refresh" checkbox (unchecked by default, and
not remembered between sessions), `t` toggles its "tvtimes" checkbox,
and `ENTER` launches tvdinner with it, exactly as if its
URL/`--epg`/`--channel`/`--tmdb-api-token` had been typed directly --
adding `--refresh-epg-cache` too if the checkbox was checked.

The **tvtimes** column only applies to a [tvtimes](#tvtimes) source. It
starts checked on any `tvtimes://`/`tvtimess://` row -- a bookmark you've
deliberately paired is one you generally want fully paired -- and is
greyed out on every other row, where `t` does nothing. Launching a
checked row adds `--record-watchlist`, `--report-watch-state` and
`--sync-favourites` in one go, the whole pairing described under
[Everything at once](https://github.com/issinoho/tvdinner/wiki/tvtimes#everything-at-once),
plus `--device-name` if the bookmark has one saved. That label is only
ever the one you typed -- never guessed from the hostname, which would
put a machine name into your account's watch history that you never asked
to send. Press `t` to opt a single launch back out; like "EPG Refresh",
the state isn't remembered between sessions.

Saved to `~/.config/tvdinner/bookmarks.json` by default
(`%APPDATA%\tvdinner\bookmarks.json` on Windows). A saved TMDB token is
never shown in the table (nor is it a column any more) -- only the
add/edit form shows it.

`tvdinner bookmarks list` / `add` / `edit` / `remove` manage that same
file **non-interactively**, for scripting or for another tool to
register a source (add a row whose `--url` is a merged M3U and `--epg`
its XMLTV, and that provider is one `ENTER` away in the picker). `edit`
and `remove` take either an exact bookmark name or its 1-based position
from `list`; `edit` leaves unnamed fields alone, and the `--clear-*`
flags (`--clear-epg`, `--clear-channel`, `--clear-tmdb-api-token`,
`--clear-device-name`) unset an optional one. Plain `list` masks any login credentials in
a bookmark's URL and hides its token (as the picker does); `list --json`
emits the raw `bookmarks.json` array — real URLs and tokens — for a
caller to consume, and `add` / `edit` / `remove` take `--json` to print
the affected row instead of a status line. `add` refuses a name that's
already taken unless `--replace`, which overwrites that row in place.

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

It also reports watching activity from that same [watch
history](#watch-history) log: total watch time this week, this month,
and all-time, broken down by live channel/VOD/recording, plus the
most-watched live channels this month and all-time (skipped if nothing
in the log is a live channel -- a Plex-only or VOD-only history has no
"top channels" to show).

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
anything is actually wrong (see below for why). tvdinner always prints
the sign-in URL as a fallback alongside trying to open it automatically;
add `--no-browser` to skip that automatic open attempt (useful if it'd
pick the wrong browser, or fail noisily on a machine with none
installed) and just print the URL. Either way, the flow itself needs a
browser's redirect to land back on `127.0.0.1` on this same machine, so
on a fully headless/SSH-only box you'll also need to forward the local
port the URL redirects to (e.g. `ssh -L <port>:localhost:<port>
user@host`, using the port from the printed URL's `redirect_uri`)
before opening the link elsewhere.

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
| `--vod-group GROUP` | An M3U group-title (exact match) to pull out of the guide/channel list and into the VOD movie browser (see the `m` keybinding below) instead -- repeatable to name several groups. Only affects plain M3U/local playlists; Xtream and Stalker panels expose VOD as a separate API and are always browsed this way when present. Has no effect by default, so existing M3U playlists behave exactly as before unless you opt a group in. |
| `--schedule-file PATH` | JSON file storing EPG-scheduled recordings (see the `s` guide keybinding below), default: `~/.config/tvdinner/schedule.json` on Linux, `%APPDATA%\tvdinner\schedule.json` on Windows. tvdinner must still be running when a scheduled recording's time arrives -- there's no background service. |
| `--record-watchlist` | [tvtimes](#tvtimes) source only: poll that account's watchlist every 15 minutes and schedule a recording for each upcoming airing anyone on it flagged -- set a reminder in the tvtimes web app (from your phone, say) and this box records it. Entries it added are withdrawn again when they leave the watchlist; recordings you scheduled by hand here are never touched. See below. |
| `--report-watch-state` | [tvtimes](#tvtimes) source only: report what you watch back to that account every 15 minutes, so its web guide can dim and tick watched programmes. Only live-channel watches from that source are sent (never a local file, YouTube or Plex), as plain start/stop intervals rather than programme references. Off by default -- this is the one thing the export token can write. See below. |
| `--device-name NAME` | Label this box in the watch state reported by `--report-watch-state` (e.g. `living room`), so a household with more than one player can tell them apart. |
| `--sync-favourites` | [tvtimes](#tvtimes) source only: star the channels anyone on that account has favourited there, once at startup. Additive and one-way -- it never removes a favourite you set here, so un-starring in tvtimes leaves this box's star in place. See below. |
| `--live-buffer-minutes MINUTES` | How long the `p` keybinding can pause a live channel before it resumes automatically (default: 10). |
| `--disable-full-screen` | Start in a normal window instead of full screen (the default). |
| `--glsl-shader PATH` | A custom GLSL shader file (e.g. an Anime4K or FSRCNNX shader) to apply on top of mpv's own built-in high-quality scalers (hardware decoding and mpv's `gpu-hq` scaling profile are both always on). Repeatable to layer several, applied in the order given. Off by default: custom shaders can be significantly heavier on the GPU than the built-in scalers alone. |
| `--interpolation` | Smooth motion by interpolating between frames (mpv's `interpolation` plus `video-sync=display-resample`). Off by default: only actually helps when the display's refresh rate is a clean multiple of the video's frame rate, adds GPU cost, and changes how mpv times playback against audio. |
| `--audio-passthrough` | Send the encoded audio bitstream (AC3/DTS/E-AC3/TrueHD) straight to an AVR/soundbar over S/PDIF or HDMI instead of decoding it here. Only takes effect when the output device actually supports the format; mpv falls back to normal decoding otherwise, same as leaving this off. |
| `--audio-downmix-boost` | Raise the center/surround channels' volume when downmixing surround audio to stereo, so dialogue and surround effects don't end up quiet relative to the front L/R channels the way a naive downmix leaves them (mpv's own `audio-normalize-downmix`). |
| `--loudness-normalization` | Even out volume across (and between) titles via ffmpeg's `loudnorm` filter. Off by default: adds a small amount of processing, and some listeners prefer a title's original dynamic range. |
| `--no-chapter-skip` | Keep `UP`/`DOWN` as mpv's default 60-second seek, even when playing a [Plex](#plex-media-server) VOD item with real chapter markers (on by default -- see the `UP`/`DOWN` keybinding above). |
| `--no-skip-markers` | Don't show the "Skip Intro"/"Skip Credits" prompt (on by default -- see the `j`/`ENTER` keybinding above). |
| `--no-autoplay-next-episode` | Don't offer the next episode of a [Plex](#plex-media-server) TV show when one finishes (on by default -- see the "Up Next" keybinding above). |
| `--autoplay-countdown-seconds SECONDS` | How long the "Up Next" prompt waits before playing the next episode on its own (default: 10). |
| `--playback-positions-file PATH` | JSON file remembering where you left off in each recording (see the `w` recordings browser) or VOD item, so reopening one resumes instead of starting over (default: `~/.config/tvdinner/playback_positions.json` on Linux, `%APPDATA%\tvdinner\playback_positions.json` on Windows). A recording's entry is dropped once the file itself is deleted; a VOD entry -- there being no file to check -- is instead dropped after 90 days of nobody resuming or updating it. |
| `--history-file PATH` | JSONL file logging what's watched (channel/VOD/recording), when, and for how long -- browse it with the `x` keybinding (default: `~/.config/tvdinner/history.jsonl` on Linux, `%APPDATA%\tvdinner\history.jsonl` on Windows). See below. |
| `--no-history` | Don't record watch history. |
| `--no-plex-activity` | [Plex](#plex-media-server) source only: don't report playback to the Plex server -- on by default, this is what makes tvdinner playback show up in Plex's own dashboard and third-party tools like Tautulli, and lets Plex update its own watched/resume status for the item. Reading Plex's own watched/resume status is unaffected either way. |
| `--no-plex-theme-music` | [Plex](#plex-media-server) source only: don't play a show's theme-music preview while browsing its library page -- on by default, matching the official Plex clients. Starts after a short pause on a show, fades out on navigating away or picking something to actually watch. |
| `--epg-cache-hours HOURS` | How long a downloaded EPG is reused from disk before re-fetching (default: 24). |
| `--no-epg-cache` | Always re-download the EPG instead of using a cached copy, and don't write one either. |
| `--refresh-epg-cache` | Force a fresh EPG download for this run, ignoring any existing cached copy no matter its age, then refresh the on-disk cache with it (unlike `--no-epg-cache`, later runs still benefit from the cache). |
| `--no-online-logos` | Don't fall back to [iptv-org](https://github.com/iptv-org/api)'s community channel/logo database for channels with no logo of their own or in their EPG (common for bare M3U playlists) -- on by default, sharing `--epg-cache-hours`/`--no-epg-cache`/`--refresh-epg-cache`'s caching. |
| `--tmdb-api-token TOKEN` | TMDB v4 read-access Bearer token -- enables a gold star rating (e.g. `★ 7.6`) plus the required `TMDB` attribution mark on movie programmes in the guide grid and details popup; the details popup also shows the director, falling back to TMDB only when the EPG feed doesn't already tag one itself (see below). Movies only, matched by programme category. Ratings are fetched in the background and cached on disk for 30 days. Off by default; overrides any token saved via `tvdinner store-tmdb`. For a [local video file](#local-files), this instead enables the `i` overlay's poster/synopsis/rating/director. See below. |
| `--tmdb-token-file PATH` | Where `tvdinner store-tmdb`/`tvdinner clear-tmdb` read/write the default TMDB token (default: `~/.config/tvdinner/tmdb_token.json` on Linux, `%APPDATA%\tvdinner\tmdb_token.json` on Windows). |
| `--no-tmdb-cache` | Always query TMDB instead of using a cached rating/metadata/artwork, and don't write one either -- same escape hatch as `--no-epg-cache`, for clearing a bad cached entry (e.g. a mismatched title) without waiting out the 30-day cache. |
| `--refresh-tmdb-cache` | Force a fresh TMDB lookup for whatever's fetched this run, ignoring any existing cached entry no matter its age, then refresh the on-disk cache with it (unlike `--no-tmdb-cache`, later runs still benefit from the cache). |
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

# Play your whole tvtimes line-up, guide included
tvdinner 'tvtimess://tv.example.com?token=abcdef123456'

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

If the panel exposes a VOD library or a TV-series library, those are
fetched alongside the live channels and browsed with the `m` (movies) and
`l` (series: categories → shows → seasons → episodes) keybindings — see the
keybindings table. The series tree is walked lazily, one level per drill-in,
so a large catalogue doesn't slow startup.

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
libraries as a TUI overlay -- plus a synthetic "On Deck" row
first, pulled from Plex's own server-wide on-deck feed (movies you left
partway through, and the next unwatched episode of any show you're
partway through), so picking up where you left off doesn't need
navigating into a specific library first. In On Deck an episode is shown
under its **season poster** rather than an episode screengrab, so it
reads as "the show you're mid-way through" at a glance; everywhere else
an episode keeps its own still. Each row shows its poster/cover art plus
year, content rating, Plex's own audience score, and (for a movie or
episode) resolution (e.g. "1080p", "4K", "SD"), all fetched from
Plex itself with no extra lookups: arrows/`PGUP`/`PGDWN` to move, `ENTER` to
drill in (library → show → season → episode) or play a movie/episode,
`ESC`/`LEFT` to go back a level or close the browser, and `l` to reopen it
later -- right back where you left off, not the library root, even
after starting playback. Press `/` at any point to search the whole
server via Plex's own search API, not just whatever's currently on
screen, or `y` to filter by release year instead -- every movie, show,
and individual episode (matched by its own air date, not the show's
premiere year) across every library released that year -- movies from
every movie library treated as one alphabetical-by-film-name list, TV
content from every TV library treated as one alphabetical-by-show
(then numeric by season/episode) list, movies always ahead of TV.
`h` favorites the selected movie or show (never a
season or episode -- nothing finer-grained than that), saving to
`--favorites` immediately alongside any guide channel favorites, shown
with the same heart marker as the guide; `v` shrinks the current listing
down to just favorited movies/shows. Playback is always
direct-play (the file's own container/codecs, streamed straight from
Plex) -- tvdinner never asks Plex to transcode, so a file mpv can't
decode on its own won't play here even if it would in Plex's own apps.
Once something's playing, `i` shows a poster/synopsis/rating/director/
progress overlay pulled from Plex's own metadata, and resuming/
reconnecting on a dropped connection works the same as any other
on-demand source (see `--playback-positions-file` below). The first
time you play something tvdinner has no local resume position for yet,
it falls back to Plex's own reported progress instead -- so picking up
a movie or episode you left partway through in Plex's own apps resumes
from there too, not just from where tvdinner itself last left off. The
same `i` overlay also shows a technical-details line/block --
container, video/audio bitrate, and every audio/subtitle track the
file has, not just whichever one happens to be selected.

When the source file has real embedded chapters (e.g. a Blu-ray/DVD
rip), they show as tick marks on the progress bar, and `UP`/`DOWN`
preview the next/previous chapter instead of doing a plain seek -- a
small panel shows its thumbnail (Plex's own, when it generated one, or
a frame grabbed on the fly otherwise) and title without seeking yet;
keep pressing `UP`/`DOWN` to move further through the chapter list,
`ENTER` to jump there immediately, `ESC` to cancel, or just stop
pressing keys for a couple of seconds to jump there automatically (see
the keybindings table below, and `--no-chapter-skip`). When Plex's own
intro/credits
detection has run on the item's library (a Plex Pass feature -- most
libraries don't have this enabled, so don't be surprised if it never
shows up), a small "Skip Intro"/"Skip Credits" prompt appears while
playback is inside one of those windows, confirmed with `j` or `ENTER`
(see `--no-skip-markers`). And when a TV episode plays through to a
real end, an "Up Next" prompt offers the next episode with a
cancellable countdown (`ESC` to cancel; see `--no-autoplay-next-episode`
and `--autoplay-countdown-seconds`).

Playback is also reported to Plex's server as a real session -- the same
timeline API Plex Web/mobile apps use -- so what's playing via tvdinner
shows up in Plex's own dashboard and in third-party tools like Tautulli,
and Plex's own watched status/resume position for the item gets updated
too. On by default; pass `--no-plex-activity` to turn it off (reading
Plex's own watched/resume status above is unaffected either way).

Browsing a show also plays a short loop of its theme music, the same
ambience the official Plex clients add to a show's library page --
starts after a brief pause on a show (so it doesn't fire while quickly
scrolling past it) and fades out on navigating away or picking
something to actually watch, via a second, fully separate audio-only
mpv instance so it never interferes with anything actually playing. On
by default; pass `--no-plex-theme-music` to turn it off.

Finding your token: play anything in Plex Web, open your browser's dev
tools → Network tab, and look for `X-Plex-Token=...` in any request's
query string (or see
[Plex's own instructions](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)).
Like the Xtream Codes/Stalker Portal cases above, a `plex://` URL's token
is stored as plain text wherever the source URL itself is stored
(`bookmarks.json`, backup archives); it's shown redacted (first four
characters kept, the rest masked) in the log file.

### tvtimes

[tvtimes](https://github.com/issinoho/tvtimes) is the companion
self-hosted TV-guide web app: it aggregates several IPTV/tuner sources
into one line-up and one clock-shift-corrected XMLTV guide, and publishes
both behind a single rotatable token. Point tvdinner at it and you get
that whole merged line-up, with its guide, as one source:

```
tvtimes://host[:port]?token=...
```

Use `tvtimess://` instead of `tvtimes://` if the server is served over
https (the usual case behind a reverse proxy). In tvtimes, turn the feeds
on under **Settings → Export feeds** and use its **Open in tvdinner**
button, which hands the whole URL over ready-made.

This is plain sugar, not a new protocol: the URL expands to that server's
two export feeds --
`<host>/api/exports/playlist.m3u?token=...` and
`<host>/api/exports/epg.xml?token=...` -- so everything downstream (the
program guide, favorites, recording, scheduling, bookmarks, the EPG
cache) behaves exactly as it does for any other M3U + XMLTV pair. A base
path is kept if tvtimes sits under a sub-path on your proxy, e.g.
`tvtimess://example.com/tv?token=...`.

The EPG URL is derived from the host you typed rather than the
`url-tvg=` header inside the playlist: tvtimes builds that header from
its own configured public origin, which needn't be the address this
machine reaches it on. An explicit `--epg` still wins over both.

#### Recording from the tvtimes watchlist

Add `--record-watchlist` and tvdinner polls that account's watchlist
every 15 minutes, scheduling a recording for each upcoming airing anyone
on it flagged:

```
tvdinner 'tvtimess://tv.example.com?token=...' --record-watchlist
```

So you press **Remind me** (or **Watch this title**) in the tvtimes web
app — from your phone, on the bus — and the box at home records it. The
watchlist is per user but the export token is per account, so a shared
household account records whatever *anyone* on it flagged, de-duplicated
per broadcast.

Entries tvdinner creates this way are tagged in `schedule.json` and
reconciled on every poll: un-watchlist something in tvtimes and its
recording disappears too. **Recordings you scheduled by hand from the
guide are never touched** — and if one already covers an airing the
watchlist also wants, no duplicate is added. Times come from the feed
already clock-shift corrected, so they line up with the guide.

The usual caveat applies: tvdinner has no background service, so it must
still be running when the recording's time arrives.

#### Reporting what you watched back

`--report-watch-state` sends what you watch on a tvtimes source back to
that account every 15 minutes, so its **web guide dims and ticks the
programmes you've already seen**:

```
tvdinner 'tvtimess://tv.example.com?token=...' --report-watch-state \
    --device-name 'living room'
```

Only live-channel watches from *this* tvtimes source are sent -- never a
local file, a YouTube video, a Plex episode, or a channel from a
different playlist. What goes over the wire is plain start/stop
intervals, not "programme X was watched": tvtimes works out which
programmes those cover by overlapping them against its own guide, so a
guide refresh or a corrected clock-shift changes the answer without
anything having to be re-reported.

The last week of history is resent on every tick rather than tracked as
"already sent". tvtimes de-duplicates, so a restart or a spell offline
catches up by itself with no local bookkeeping to fall out of step.
When a guide already carries its own clock corrections, tvdinner stops
applying yours to it -- a tvtimes export shifts times as it writes them,
so an `--epg-shifts` entry for the same channel would apply the
correction twice and leave the guide a whole shift in the past. The
stored shift isn't deleted, because it's keyed by channel name and you
still want it when watching that channel direct from its provider; it's
just not used for the corrected guide. `[`/`]` says so rather than
silently doing nothing. Detected from the feed's own
`generator-info-name`, so it covers a `tvtimes://` source and a one-off
**Play** hand-off alike.

`--device-name` labels the box, so a household with more than one player
can tell them apart -- save one on a bookmark (or pass it here) and
tvtimes records it against every interval that report carries. It's
truncated to 120 characters to match what tvtimes stores; an over-long
one would otherwise be rejected along with the whole batch of events.

This uses the same export token as everything else, which is the only
thing that token can *write* -- see tvtimes' own docs for what that
means.

#### Sharing favourites

`--sync-favourites` stars the channels anyone on the tvtimes account has
favourited there, so a star set in the web app shows up in tvdinner's
guide:

```
tvdinner 'tvtimess://tv.example.com?token=...' --sync-favourites
```

It runs once at startup and is **additive and one-way**: it never removes
a favourite you set here, so un-starring in tvtimes leaves this box's
star in place. That's deliberate -- `favorites.json` records only channel
names, with no note of where each came from, so a two-way reconcile
couldn't tell "removed upstream" from "added locally", and silently
deleting your own favourite is the worse failure. Un-star it here with
`h` if you want it gone.

#### Jumping back to the web guide

On **Windows** the installer registers the `tvdinner:`, `tvtimes:` and
`tvtimess:` URL schemes for you, which is what makes tvtimes' **Play** and
**Open in tvdinner** buttons work. Installers before 1.41 didn't -- those
links did nothing at all. Re-run the installer to fix an existing install.
`tvdinner default-handler` is Linux-only and doesn't cover this.

`T` (shift-t) opens the tvtimes web app for whatever's on the current
channel -- the reverse of tvtimes' own **Play** button. It's a *search*
URL (`/search?q=<title>`) rather than a link to the exact guide cell:
finding the thing by name is what you actually want from this end, and it
works without tvtimes' virtualised grid needing scroll-to-cell support.
With no EPG data for the channel it just opens tvtimes itself.

Every one of these rides the same export token, and the feeds behind them
are ordinary HTTP -- documented as OpenAPI at
[tvtimes Export API](https://issinoho.github.io/tvtimes/api/) if you want
to build something else against the same account.

Like the Xtream Codes/Stalker/Plex cases above, a `tvtimes://` URL's
token is stored as plain text wherever the source URL itself is stored
(`bookmarks.json`, backup archives); it's shown redacted in the log file.

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
required by TMDB's API terms. If a cached entry ever looks wrong (a
mismatched title, say), `--no-tmdb-cache`/`--refresh-tmdb-cache` clear
it without waiting out the 30 days -- see the table above.

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

In addition to `mpv`'s own default key bindings. Wherever `ENTER` is
listed below, the numpad's `KP_ENTER` works identically -- every guide/
browser/prompt that binds one binds the other alongside it.

| Key | Action |
| --- | --- |
| `i` | Show the current/next programme info overlay (with video/audio quality badges: resolution, codecs, fps, HDR, channel layout, and a movie's director when available -- see [TMDB ratings](#tmdb-ratings)); while the program guide is open, shows full details for the selected guide programme instead. While watching back a recording, shows its own label, recorded date, and playback progress instead of live EPG info. While playing a VOD/[Plex](#plex-media-server)/[local file](#local-files)/[YouTube](#youtube) video, shows its poster, synopsis, rating, director, and playback progress instead (Plex populates all of that; a local file or YouTube video gets it from a background lookup -- YouTube's own oEmbed always, TMDB additionally if `--tmdb-api-token` was given; other VOD sources show whatever fields they have). Also shows a technical-details line/block -- container, video/audio bitrate, and every audio/subtitle track the file has (not just whichever one is currently selected), for any source, not just Plex. While the Plex library browser is open and a movie or episode is selected, shows that item's own details ("DETAILS", no progress bar) instead of whatever's currently playing -- a show/season/library row has no single file to show details for, so `i` falls back to the normal playback-info behavior there. Pressing `i` again while this overlay is already showing opens the item's TMDB page in the default browser instead of re-showing the same info, when a TMDB match is known -- a movie (from any source that resolves one) or a Plex show/episode (read straight from Plex's own metadata, no `--tmdb-api-token` needed); otherwise it shows "No TMDB page available". |
| `g` | Toggle the full program guide. |
| `MENU` (tap) | Show the programme info overlay -- same as `i` (the button most IR/BLE air-mouse remotes send for their MENU key). In a [Plex](#plex-media-server) session (no guide to hold for) this is `MENU`'s only behavior, tap or hold alike. |
| `MENU` (hold, 0.5s+) | Toggle the full program guide -- same as `g`. |
| `b` | Switch to the last watched channel (like a TV remote's "last channel" button) -- repeated presses toggle back and forth between the two, since every switch (guide or `b` itself) remembers whatever was playing right before it. No-op if nothing's been switched away from yet this session. |
| `LEFT` / `RIGHT` | Page the program guide's timeline back/forward by 30 minutes (guide only). While the keyboard-shortcuts help (`?`) is open, switches its tabs instead. Otherwise, these seek the video as usual. |
| `UP` / `DOWN` | Move the program guide's channel selection cursor (guide only). Otherwise, for a [Plex](#plex-media-server) item with real chapter markers, previews the next/previous chapter instead (`UP` forward, `DOWN` back -- matching mpv's own sense for these keys) -- a small panel shows its thumbnail and title without seeking yet; falls back to mpv's default 60-second seek for anything without chapters, or if `--no-chapter-skip` is given. |
| `ENTER` / `ESC` (while a chapter preview is showing) | `ENTER` jumps to the previewed chapter immediately; `ESC` cancels with no seek. Left alone for a couple of seconds, it jumps there automatically. |
| `PGUP` / `PGDWN` | Move the program guide's channel selection cursor a full page at a time (guide only). |
| `ENTER` | While the guide, a browser, or a text-entry prompt is open: whatever that view's own `ENTER` does (switch to the selected channel and close the guide, confirm a filter/search query, play the selected recording/VOD item, connect to the selected Chromecast/Plex item, etc.). Otherwise, pauses/resumes -- see `p` above. |
| `[` / `]` | Nudge the selected guide channel's EPG shift back/forward by 1 minute, saving the change to `--epg-shifts` immediately (guide only). |
| `f` | Open a text-entry dialog to filter the program guide's channel list by name or group (as shown by `--list`, case-insensitive substring match against either); ENTER applies it, ESC cancels (guide only). |
| `c` | Clear any active guide filter and show every channel again (guide only). |
| `h` | Toggle the selected guide channel as a favorite (or the currently-playing one if the guide isn't open), saving to `--favorites` immediately; favorited channels show a heart next to their name in the guide. In a [Plex](#plex-media-server) session, toggles the selected movie or show in the library browser instead -- favorites are movie/show level only, never a season or episode. |
| `v` | Toggle showing only favorited channels in the guide, or favorited movies/shows in the Plex library browser. |
| `ESC` / `GO_BACK` | Close the programme details popup, or cancel an in-progress guide filter query (and, throughout the rest of the app, whatever else `ESC` currently closes/cancels -- a browser, an overlay, a text-entry prompt). `GO_BACK` is the key name mpv reports for a remote's dedicated back button; it's a permanent alias for `ESC`, always doing exactly whatever `ESC` currently would -- except in a [Plex](#plex-media-server) session with nothing open (just watching), where plain `ESC` has no meaning of its own and would otherwise fall through to mpv's own default binding (cycle fullscreen/window mode); there, `GO_BACK` instead acts like `BS`, stopping the current item and dropping back into the library browser. |
| `z` | Cycle the video's display aspect ratio (Auto, 4:3, 16:9, 2.35:1, 1:1). |
| `e` | Cycle a sleep timer: Off → 15 → 30 → 60 → 90 minutes → Off. Pauses playback when it fires (same as pressing `p`), one-shot, and stays running across a channel/VOD switch -- it's tied to the session, not whatever happens to be playing. |
| `[` / `]` / `{` / `}` | mpv's own defaults, not overridden here: adjust playback speed by ±10% / halve / double (outside the guide, where `[`/`]` mean something else -- see above). |
| `Ctrl++` / `Ctrl+-` | mpv's own default: adjust audio sync (delay/advance the audio relative to the video). |
| `Alt++` / `Alt+-` / `Alt` + arrows | mpv's own defaults: zoom the video in/out and pan it around once zoomed; `Alt+BS` resets both. |
| `BS` | Stop playback and quit tvdinner cleanly -- the closest equivalent this always-something-loaded, single-window app has to a remote's dedicated STOP/DEL button (confirmed live: at least one real remote's "DEL" button reports as `BS`). In a [Plex](#plex-media-server) session, `BS` instead stops the current item and drops back into the library browser exactly where you left off, rather than quitting -- there's always a browser to fall back into there, so "stop" means "stop this and pick something else." Shadowed by the guide filter/Plex search/Plex year text-entry prompts' own `BS` "delete last character" while one of those is open, and restored once it closes. |
| `p` / `PLAY` / `PAUSE` / `PLAYPAUSE` / `ENTER` | Pause/resume live TV (`PLAY`/`PAUSE`/`PLAYPAUSE` are the key names mpv reports for a remote's dedicated play/pause button; `ENTER` is the OK/center button most IR/BLE air-mouse remotes send, doubling as play/pause here -- shadowed by the guide's/every browser's own `ENTER` binding while one of those is open, and restored once it closes). While paused, the stream keeps buffering in the background (up to `--live-buffer-minutes`, default 10) so resuming (manually or automatically once the limit's reached) continues from where you paused rather than jumping back to live -- use mpv's normal seek keys (`LEFT`/`RIGHT`, etc.) to rewind/fast-forward within that window. Recorded/played-back files just pause normally, with no time limit. Pausing also shows the same info overlay `i`/`MENU` would (the EPG banner for a live channel, or the poster/synopsis/progress card for a recording/VOD/Plex item), so it's clear what's paused -- it auto-hides itself after a few seconds like always, leaving just the paused frame, or disappears immediately on resume if it's still up. |
| `r` | Toggle recording the current stream to disk as a raw copy (no re-encoding), saved under `--record-dir` as `<channel>_<timestamp>.ts`. |
| `o` | Toggle picture-in-picture: shrinks the window to a small, always-on-top, borderless corner window (bottom-right, ~25% size) so you can keep watching while using other apps; press again to restore. Closes any open guide/browser overlay first. Relies on the window manager honoring mpv's placement request -- confirmed working on GNOME/Mutter, but some Wayland compositors may only shrink/keep-on-top without actually relocating the window. |
| `t` | Toggle subtitles on/off, if the current stream has a subtitle track (e.g. many UK DVB broadcasts carry one). Reports "No subtitles available" if it doesn't. To pick a different subtitle track (e.g. a different language), use mpv's own default `j`/`J` keys to cycle through them -- except while the Skip Intro/Credits prompt below is showing, where `j` (and `ENTER`) confirm it instead, restored back to subtitle-cycling the moment the prompt closes. |
| `j` / `ENTER` | While a [Plex](#plex-media-server) VOD item's intro or credits marker window is showing a "Skip Intro"/"Skip Credits" prompt (bottom-right corner): confirms it, seeking straight to the end of that window. `ENTER` works from an IR/BLE air-mouse remote's OK button too (its own base "pause" meaning is only shadowed while the prompt is up, restored the instant it closes); `j` is an unadvertised keyboard-only alias. Never automatic -- the prompt just sits there until confirmed or the window passes. Requires the library's intro/credits detection (a Plex Pass feature) to have actually run; pass `--no-skip-markers` to turn the prompt off entirely. |
| `ESC` (Up Next) | While the "Up Next" countdown is showing after a [Plex](#plex-media-server) TV episode plays through to a real end: cancels it, leaving playback exactly where it is. Left alone, the next episode plays automatically once the countdown reaches zero -- see `--autoplay-countdown-seconds`/`--no-autoplay-next-episode` below. |
| `s` | While programme details are shown (guide only): schedule that programme to record automatically, switching channels and starting/stopping the recording at its start/stop time even if you're watching something else -- press again to cancel. Saved to `--schedule-file`; only fires while tvdinner is running. A scheduled programme shows a small red "R" badge in the guide. |
| `m` | Browse VOD movies pulled out of the playlist via `--vod-group` (plain M3U/local playlists only -- an [Xtream](#xtream-codes) or [Stalker](#stalker-portal) panel's own VOD API populates this automatically instead), grouped by group-title, alphabetical by title within each group -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to play it (resuming where you left off, if you didn't finish it last time -- see `--playback-positions-file`), any letter or digit to jump to the next title starting with it (press again to cycle to the next match), `ESC` to close. No-op with a "No VOD movies found" message if nothing qualifies. This is movies only -- an Xtream panel's TV series are browsed separately with `l`. |
| `w` | Browse past recordings from `--record-dir`, grouped by date -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to play it back (resuming where you left off, if you didn't finish it last time -- see `--playback-positions-file`), `d` twice to permanently delete the selected one (the first press just arms the confirmation), `ESC` to close. |
| `u` | Browse upcoming scheduled recordings (see the `s` guide keybinding above), soonest first, marking whichever one is currently recording -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to cancel the selected one, `ESC` to close. Since only one recording can happen at a time, an overlapping schedule that never got a turn shows up here (and as an on-screen notification) under "Missed", with the reason why. |
| `l` | (Re)open a browsable media library, for the two source types that have one. **[Plex](#plex-media-server):** the server's libraries -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move the selection, `ENTER` to drill into a library/show/season or play a movie/episode, `ESC`/`LEFT` to go back a level (or close it, from the top level). While showing a listing of movies or shows specifically (not the top-level library list, and not seasons/episodes), any letter or digit other than `g`/`h`/`v`/`l`/`y` jumps to the next title starting with it (press again to cycle to the next match) -- those five keep their own meanings below even at that level, so grid/list view, favoriting, closing, and the year filter are always one keypress away; reaching a title starting with one of those five still just takes an extra arrow press or two, or `/` search. **[Xtream](#xtream-codes):** the panel's TV series (categories → shows → seasons → episodes), fetched lazily one level at a time, and separate from VOD movies (`m`) -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move, `ENTER` to drill in or play the selected episode (resuming where you left off, if you didn't finish it last time -- see `--playback-positions-file`), `ESC`/`LEFT` to go back a level or close from the top. No-op with a "No TV series found" message if the panel exposes none. |
| `ENTER` (hold) | While the Plex library browser is open, on a movie, show, or episode: opens a small item menu -- "Play from Start" (bypasses any resume position; not shown for a show, which has no single file of its own), "Mark as Watched", "Mark as Unwatched". `UP`/`DOWN` moves, `ENTER` activates, `ESC`/`LEFT`/`GO_BACK` cancels without changing anything. Has no effect on a library/season row. A normal (short) `ENTER` tap is unaffected -- it still plays/drills in exactly as before. |
| `g` | While the Plex library browser is open: switch between Grid view (the default -- large poster tiles, `columns` at a time) and List view (a scrolling row per item), keeping whatever's currently selected in view. The chosen view persists as you browse -- drilling into a show/season, searching, filtering by year, etc. all stay in whichever view you last picked, until you press `g` again. In Grid view, `LEFT`/`RIGHT` move across columns instead of `LEFT` going back a level -- use `ESC`/`GO_BACK` for that there instead; List view is unaffected. |
| `/` | While the Plex library browser is open: search the whole server via Plex's own search API -- `ENTER` runs the search and shows results as a new browsable list, `ESC`/`LEFT` cancels. |
| `y` | While the Plex library browser is open: filter by release year (digits only) -- `ENTER` shows every movie/show/episode across every library released that year (an episode by its own air date, not its show's premiere year), as a new browsable list: every movie library treated as one alphabetical-by-film-name list, every TV library treated as one alphabetical-by-show (then numeric by season/episode) list, movies always ahead of TV; `ESC`/`LEFT` cancels. |
| `k` | Open the [Chromecast](#casting) device picker for whatever's currently playing -- `UP`/`DOWN`/`PGUP`/`PGDWN` to move, `ENTER` to connect, `ESC` to close. While already casting, reopening shows a red "Disconnect" entry above the device list. Requires the optional `pychromecast` extra -- see Casting below. |
| `x` | Browse [watch history](#watch-history) -- every channel/VOD item/recording actually watched, newest first, grouped by day, with a thumbnail (a VOD's poster, a channel's logo, or a frame grabbed from a recording's own video), duration, and (for movies) year/rating/director. `UP`/`DOWN`/`PGUP`/`PGDWN` to scroll, `ENTER`/`ESC` to close -- a read-only viewer, not a launcher. |
| `a` | Toggle an about card: logo, app name, version, and a one-line summary -- press again or `ESC` to close. |
| `T` | On a [tvtimes](#tvtimes) source, open the tvtimes web app for whatever's on this channel -- the reverse of tvtimes' own **Play** button. Uses its search page (`/search?q=<title>`) rather than linking to the exact guide cell, so it needs no deep-link support at the far end and finding the thing by name is what you want from here anyway. Falls back to opening tvtimes itself when there's no EPG data for the channel; reports "Not a tvtimes source" otherwise. |
| `y` / `n` | Only shown on the [update-available card](#update-checks) (appears automatically, at most once every 24 hours, when a newer release exists): `y` opens the release page in your browser, `n` (or `ESC`) dismisses it. Either way that version won't be shown again. |
| `?` | Toggle a keyboard-shortcuts cheat sheet, grouped into tabs (Guide, Playback, VOD & Chapters, Recording & History, Plex) -- `LEFT`/`RIGHT` switches tabs while it's open, press `?` again or `ESC` to close. |

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT — see [LICENSE](LICENSE).
