Name:           tvdinner
Version:        0.1.0
Release:        53%{?dist}
Summary:        IPTV player with M3U/XMLTV EPG integration

License:        MIT
URL:            https://github.com/issinoho/tvdinner
Source0:        %{name}-%{version}.tar.gz

# The automatic python dependency generator adds a versioned Requires
# for every entry in pyproject.toml's dependencies, scanned straight
# from the wheel metadata:
#  - python-mpv has no Fedora/RHEL RPM equivalent at any version, so
#    its Requires can never be satisfied on any Fedora system.
#  - pillow/requests do have real Fedora packages, but pyproject.toml's
#    floors (Pillow>=10, requests>=2.31) are just "whatever was current
#    when written", not a real API requirement (tvdinner only calls
#    long-stable Image/ImageDraw/ImageFont/ImageFilter/ImageOps and
#    requests.get APIs) -- so on older Fedora releases whose packaged
#    versions sit below those floors (e.g. Fedora 38: pillow 9.5,
#    requests 2.28), this generated Requires is stricter than
#    necessary and blocks an otherwise-fine install.
# Exclude all three; the manual, unversioned Requires below (mpv is
# still required by name; pillow/requests are satisfied by whatever
# version the distro ships) remain the real constraint.
%global __requires_exclude ^python3.*dist\\((python-mpv|pillow|requests)\\)

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  pyproject-rpm-macros

Requires:       mpv
Requires:       python3-pillow
Requires:       python3-requests

%description
tvdinner plays IPTV streams from M3U playlists using mpv, with a
TiviMate-style on-screen EPG overlay and a full program guide sourced
from XMLTV data (auto-discovered from the playlist, or an explicit
URL), including timezone-aware scheduling and a configurable
clock-correction shift for feeds with incorrect times.

Note: the python-mpv PyPI package (tvdinner's Python binding to mpv)
has no Fedora/RHEL RPM equivalent, so it is deliberately not listed as
a Requires here -- install it separately before running tvdinner, with:
    sudo pip install --prefix=/usr python-mpv
(add --break-system-packages if pip refuses with an "externally
managed environment" error). Two more-obvious-looking commands don't
work, both silently:
  - 'pip install --user ...': the installed /usr/bin/tvdinner script's
    shebang is '#!/usr/bin/python3 -sP', and -s specifically skips
    user site-packages.
  - plain 'sudo pip install ...' (no --prefix): on distros that
    redirect unmanaged pip installs away from dnf/rpm-owned
    directories, this lands in /usr/local/lib/pythonX.Y/site-packages,
    which some systems' system Python (e.g. Fedora 38) never searches
    at all -- --prefix=/usr installs directly into the dnf-owned
    site-packages tvdinner's own shebang actually searches.

%prep
%autosetup -n %{name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
install -Dm644 debian/%{name}.1 %{buildroot}%{_mandir}/man1/%{name}.1

%files
%{_bindir}/%{name}
%{python3_sitelib}/%{name}/
%{python3_sitelib}/%{name}-%{version}*.dist-info/
%{_mandir}/man1/%{name}.1*
%doc README.md
%license LICENSE

%changelog
* Thu Jul 30 2026 Iain Smith <iain@issinoho.com> - 0.1.0-53
- Fix a URL pasted with wrapping shell quotes (e.g. copy-pasting the
  doc examples' 'hdhomerun://192.168.1.50' literally, quotes and
  all, into a bookmark) silently breaking scheme detection -- a
  leading/trailing quote isn't valid in a URL scheme, so it fell
  through to being treated as an unplayable raw stream with no
  clear error. main()'s url/--epg arguments and the bookmarks
  form's URL/EPG fields now strip one matching pair of wrapping
  quotes

* Thu Jul 30 2026 Iain Smith <iain@issinoho.com> - 0.1.0-52
- Fix bookmarks logging a selected/added/edited xtream:// or
  stalker:// bookmark's URL unredacted -- the plaintext password or
  MAC address was written straight to the log file, even though
  playback itself has always redacted the same URL. Also updates
  the bookmarks form/table's stale "M3U URL" label now that
  bookmarks accept any of the URL forms tvdinner does

* Thu Jul 30 2026 Iain Smith <iain@issinoho.com> - 0.1.0-51
- Add EPG support for HDHomeRun tuners: fetches guide data
  automatically from SiliconDust's XMLTV API (real XMLTV, parsed
  with the same code path as any other feed) when the device
  reports a paid HDHomeRun DVR guide subscription; devices without
  one simply show no guide data, same as any other inaccessible EPG
  source

* Wed Jul 29 2026 Iain Smith <iain@issinoho.com> - 0.1.0-50
- Add native HDHomeRun support: URL now also accepts an
  hdhomerun://host[:port] tuner address, fetching the device's
  channel lineup directly (no login -- HDHomeRun has no
  authentication -- and no separate M3U export step). No EPG
  support for HDHomeRun sources yet

* Wed Jul 29 2026 Iain Smith <iain@issinoho.com> - 0.1.0-49
- Add an 'o' key to toggle picture-in-picture: shrinks the window to
  a small, always-on-top, borderless corner window (bottom-right,
  25% size) so playback keeps going while other apps are used,
  closing any open guide/browser overlay first; press again to
  restore. Relies on the window manager honoring mpv's placement
  request -- confirmed working on GNOME/Mutter

* Wed Jul 29 2026 Iain Smith <iain@issinoho.com> - 0.1.0-48
- Add native Stalker Portal (Ministra) support: URL now also accepts
  a stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF login
  (stalkers:// for https), logging in with the given MAC, fetching
  the live channel list, and resolving each channel's playable
  stream URL up front via the portal's create_link call. Portal
  genres map onto the existing group-title machinery, so the guide/
  filter/favorites/recording/bookmarks all work unchanged. No EPG
  support for Stalker sources yet. The MAC is never written to the
  log file (always shown redacted)

* Wed Jul 29 2026 Iain Smith <iain@issinoho.com> - 0.1.0-47
- Add native Xtream Codes support: URL now also accepts an
  xtream://username:password@host:port login (xtreams:// for https),
  fetching the live channel list and pointing EPG loading at the
  panel's own xmltv.php export directly, with no separate M3U
  export step needed. Panel categories map onto the existing
  group-title machinery, so the guide/filter/favorites/recording/
  bookmarks all work unchanged. Credentials are never written to
  the log file (always shown redacted)

* Mon Jul 27 2026 Iain Smith <iain@issinoho.com> - 0.1.0-46
- Wire a remote's dedicated play/pause button (mpv reports it as
  PLAY/PAUSE/PLAYPAUSE) to the same pause/resume-live-TV logic as
  the 'p' key, instead of falling through to mpv's plain default
  binding
- Resume recordings from the last played position instead of
  always starting from the beginning, autosaving position
  periodically during playback and on channel/recording switch

* Mon Jul 27 2026 Iain Smith <iain@issinoho.com> - 0.1.0-45
- Add the 'p' (pause/resume live TV) keybinding to the in-app '?'
  keyboard-shortcuts overlay, which had never listed it

* Mon Jul 27 2026 Iain Smith <iain@issinoho.com> - 0.1.0-44
- Add a 'p' key to pause/resume live TV with rewind/fast-forward:
  resuming continues from the paused position rather than jumping
  back to live, auto-resuming after --live-buffer-minutes (default
  10) if left paused that long

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-43
- Add the app's logo mark to the guide/browser/help header bars, for
  a consistent brand identity across the app and the marketing site

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-42
- Fix scheduled recordings comparing raw EPG times against real time:
  the "already ended" check and the poll loop that starts/stops
  recordings ignored any per-channel --epg-shifts correction, which
  could reject a programme as already ended when it hadn't started
  yet, or record hours off from the real air time
- Surface missed scheduled recordings (a schedule conflict, or its
  channel no longer being in the playlist) instead of silently
  dropping them: an on-screen notification plus a "Missed" section
  in the 'u' scheduled view

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-41
- Add a 'u' key to browse all upcoming scheduled recordings:
  date-grouped, soonest first, marking whichever entry is currently
  recording; ENTER cancels the selected one
- Add a '?' keybinding-cheat-sheet overlay listing every binding

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-40
- Show a dedicated overlay when 'i' is pressed during recording
  playback, instead of stale live-channel EPG info: the recording's
  own label, recorded date, and a playback-progress bar

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-39
- Add a 'd' key to delete recordings from the recordings browser.
  Two-step confirm since deletes are permanent: the first 'd' arms a
  confirmation, a second 'd' on the same still-selected recording
  deletes it

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-38
- Strip characters our bundled font can't render from EPG/channel
  text: some IPTV playlists append decorative circled-letter Unicode
  badges to channel names that DejaVuSans has no glyph for, which
  used to show up as a visible empty-box artifact right after the
  channel name

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-37
- Show a red "R" badge in the guide for scheduled recordings, so
  what's queued to record is visible at a glance without opening its
  details popup

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-36
- Add a recordings browser ('w' key) to watch back past recordings:
  lists previously saved recordings (r-key or scheduled) grouped by
  date, newest first. UP/DOWN/PGUP/PGDWN move the selection, ENTER
  plays it back, ESC closes

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-35
- Add EPG-scheduled recording ('s' key on programme details): a
  background poll thread switches to the scheduled channel
  (single-tuner style, interrupting current viewing) and starts/stops
  recording automatically at the programme's start/stop time.
  Persisted to --schedule-file so a schedule survives a restart, as
  long as tvdinner is running again by record time

* Sun Jul 26 2026 Iain Smith <iain@issinoho.com> - 0.1.0-34
- Add a manual recording toggle ('r' key): dumps the current stream's
  raw bytes to disk via mpv's stream-record (no re-encoding), saved
  under --record-dir (platform-aware default) as
  <channel>_<timestamp>.ts

* Sat Jul 25 2026 Iain Smith <iain@issinoho.com> - 0.1.0-33
- Add an "EPG Refresh" checkbox column to the bookmarks table: SPACE
  toggles it on the highlighted row (unchecked by default, not
  persisted between sessions), and launching a bookmark with it
  checked runs tvdinner with --refresh-epg-cache

* Sat Jul 25 2026 Iain Smith <iain@issinoho.com> - 0.1.0-32
- Add a self-contained macOS app (tvdinner-<version>.dmg, built via
  PyInstaller), bundling a Homebrew-built libmpv so there's no
  separate Python or mpv install step. Since a double-clicked app has
  no terminal to pass a URL argument to, launching it prompts for the
  M3U/stream URL instead, remembering the last one used. Unsigned for
  now, so Gatekeeper requires right-click > Open on first launch.
  Also adds macOS-idiomatic (~/Library/...) default paths for the EPG
  shifts, favorites, bookmarks, and log files, alongside the existing
  Windows/Linux ones

* Sat Jul 25 2026 Iain Smith <iain@issinoho.com> - 0.1.0-31
- Add a self-contained Windows installer (tvdinner-setup-<version>.exe,
  built via PyInstaller + Inno Setup), bundling a pre-built mpv so
  there's no separate Python or mpv install step on Windows anymore.
  Unsigned for now, so Windows SmartScreen will warn on first run

* Sat Jul 25 2026 Iain Smith <iain@issinoho.com> - 0.1.0-30
- Fix a guide crash on a narrow shifted programme block clipped by
  the visible window's edge (a width check didn't account for the
  rectangle's own inward padding)
- Add backup/restore for configuration files: 'tvdinner backup
  [PATH]' writes EPG shifts, favorites, and bookmarks into a single
  zip archive; 'tvdinner restore PATH' extracts one back onto disk,
  prompting for confirmation unless -y/--yes is given

* Sat Jul 25 2026 Iain Smith <iain@issinoho.com> - 0.1.0-29
- Add logging to the bookmarks feature: its own --log-file/--no-log,
  every action logged, and the same log setting carried into a
  launched bookmark's playback session so it all lands in one file.
  configure_logging is now idempotent for a given path to make that
  safe; save failures are now caught and logged instead of crashing
  the TUI

* Sat Jul 25 2026 Iain Smith <iain@issinoho.com> - 0.1.0-28
- Add an optional default channel field to bookmarks (e.g. "CNN"),
  forwarded as --channel when a bookmark is launched; old bookmark
  files without it keep loading fine

* Sat Jul 25 2026 Iain Smith <iain@issinoho.com> - 0.1.0-27
- Add a bookmarks feature: 'tvdinner bookmarks' opens an interactive
  terminal table of saved playlists (description, M3U URL, optional
  EPG URL) -- add/edit/delete entries, and ENTER launches tvdinner
  with the selected one directly. Saved to
  ~/.config/tvdinner/bookmarks.json

* Fri Jul 24 2026 Iain Smith <iain@issinoho.com> - 0.1.0-26
- Show the favorite heart marker on the EPG banner overlay too, not
  just the guide grid; toggling a favorite now redraws the banner
  immediately if it's currently showing
- Now licensed under MIT (previously all-rights-reserved)

* Fri Jul 24 2026 Iain Smith <iain@issinoho.com> - 0.1.0-25
- Fix 'h' (favorite toggle) staying unbound for the rest of the
  session after using the guide filter even once -- its restoration
  was missing from finish_filter_input alongside g/i/z

* Fri Jul 24 2026 Iain Smith <iain@issinoho.com> - 0.1.0-24
- Add a Favorites feature, persisted per feed: 'h' toggles the guide's
  selected (or currently-playing) channel as a favorite, shown with a
  heart in the guide; 'v' toggles a favorites-only guide view. New
  --favorites flag, mirroring --epg-shifts

* Fri Jul 24 2026 Iain Smith <iain@issinoho.com> - 0.1.0-23
- Add a Stretch aspect ratio (cycled with 'z') that fills the window
  exactly using mpv's keepaspect=no, distorting the image if needed,
  rather than a fixed ratio that still letterboxes

* Fri Jul 24 2026 Iain Smith <iain@issinoho.com> - 0.1.0-22
- Show a channel's group in the guide overlay: a small muted line
  under its name (joined with " · " for channels tagged under several
  groups at once), so groups are visible in the guide itself rather
  than only via --list

* Fri Jul 24 2026 Iain Smith <iain@issinoho.com> - 0.1.0-21
- Add group-based filtering to the guide: the 'f' text filter now
  also matches a channel's group(s) (including semicolon-compound
  group-title values like "Movies;Series"), not just its name

* Fri Jul 24 2026 Iain Smith <iain@issinoho.com> - 0.1.0-20
- Strip trailing decorative symbols (e.g. a circled-letter marker some
  playlist generators append to a channel's name) before EPG
  name-fallback matching, so a channel whose real name is otherwise
  identical to the EPG's own display name isn't silently left without
  a schedule

* Thu Jul 23 2026 Iain Smith <iain@issinoho.com> - 0.1.0-19
- Add --refresh-epg-cache to force a one-off EPG re-download for this
  run while still refreshing the on-disk cache with the result
  (unlike --no-epg-cache, which never reads or writes one)

* Thu Jul 23 2026 Iain Smith <iain@issinoho.com> - 0.1.0-18
- Stream-parse XMLTV (ElementTree.iterparse) instead of building a
  full DOM (ElementTree.fromstring) to cut EPG load memory use: a real
  ~500MB US EPG feed previously peaked at ~5GB RSS and settled at
  ~4.3GB after parsing; now peaks at ~1.2GB and settles at ~0.75GB,
  with identical parsed output and no change in parse time

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-17
- Include the packaging release number in __version__: -v and the
  startup log line both read it, but it was stuck at the bare upstream
  "0.1.0" and never reflected which packaged build was actually
  running

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-16
- Add file logging for startup/shutdown, every user action (guide
  open/close, filter, channel switch, EPG shift, aspect ratio,
  programme details), and any warning/error (playback failures, EPG/
  playlist fetch/parse/cache failures, image fetch/decode failures).
  Logged to ~/.cache/tvdinner/tvdinner.log by default (%%LOCALAPPDATA%%
  on Windows); configurable via --log-file/--no-log

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-15
- Keep the window/input alive when a channel fails to play: a dead or
  rejected stream previously left mpv with no video track and thus no
  window at all, silently stranding the app with no way to pick
  another channel. force_window keeps the window up regardless, and a
  new failure hook shows "Failed to play <channel>" and reopens the
  guide instead

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-14
- Print EPG load progress to stderr: "Loading EPG data..." when a
  fetch/parse starts, and a loaded ("N channels")/not-available result
  line when it finishes, for both --list and the background load
  during playback

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-13
- Speed up EPG startup: playback no longer blocks on EPG fetch/parse
  (loaded in a background thread and swapped in once ready), the
  on-disk cache now stores the parsed EPG alongside the raw bytes so a
  cache hit skips re-parsing too, and merge() only re-sorts schedules
  actually touched by the merged source

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-12
- Cache downloaded EPG data on disk (default: ~/.cache/tvdinner/epg),
  refreshed once a day by default, so startup with a large XMLTV feed
  doesn't re-download and re-parse it every time; a stale cache is
  used as a fallback if a refresh attempt fails. New --epg-cache-hours
  and --no-epg-cache flags control this

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-11
- Fix EPG data not matching for many real playlist/guide combinations:
  fall back to the tvg-id with a trailing '@SD'/'@HD'/etc. feed tag
  stripped (iptv-org's own playlists append one to disambiguate
  multiple feeds of one channel), then to a normalized display-name
  match (some XMLTV providers prefix every name with their own source
  tag, e.g. "PLUTO - 00s Replay"), before giving up

* Tue Jul 21 2026 Iain Smith <iain@issinoho.com> - 0.1.0-10
- Add key bindings for IR/BLE air-mouse remotes (e.g. nRF-based USB
  dongles): ENTER (their OK/center button) shows the EPG overlay
  outside the guide, and MENU toggles the full program guide

* Tue Jul 21 2026 Iain Smith <iain@issinoho.com> - 0.1.0-9
- Show a programme's release year (from XMLTV's <date> element) in
  the EPG banner, program guide timeline cells, and programme details
  popup, e.g. "The Lady From Shanghai (1948)"

* Mon Jul 20 2026 Iain Smith <iain@issinoho.com> - 0.1.0-8
- Fix Windows portability gaps: bundle the DejaVu fonts as package
  data instead of reading from an OS font directory (drops the
  dejavu-sans-fonts Requires, now redundant), use %%APPDATA%% for the
  EPG shift config path on Windows, and only apply the X11/Wayland
  gpu_context override on Linux -- it's a hard mpv option error, not a
  graceful no-op, on Windows builds of libmpv. Confirmed working
  end-to-end via a plain pip install on Windows.

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-7
- Correct the python-mpv install note again: plain 'sudo pip install'
  (no --user) isn't enough either -- it lands in
  /usr/local/lib/python3.11/site-packages, which this system's Python
  never searches (confirmed on Fedora 38). 'sudo pip install
  --prefix=/usr python-mpv' installs directly into the dnf-owned
  site-packages tvdinner's shebang actually searches, and is confirmed
  working end-to-end on a real Fedora 38 VM.

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-6
- Correct the python-mpv install note: the installed console-script's
  shebang is '#!/usr/bin/python3 -sP', and -s specifically excludes
  user site-packages, so 'pip install --user python-mpv' silently
  doesn't work -- needs a system-wide 'sudo pip install python-mpv'
  instead (found by actually testing an install on Fedora 38)

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-5
- Also exclude the auto-generated python3dist(pillow)/(requests)
  Requires, not just python-mpv -- their pyproject.toml version
  floors are stricter than tvdinner actually needs, and blocked
  install on Fedora 38 (ships pillow 9.5, requests 2.28) even though
  the code works fine with those versions

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-4
- Exclude the automatically-generated python3dist(python-mpv)
  Requires -- it's scanned straight from pyproject.toml's
  dependencies and can never be satisfied, since no Fedora/RHEL
  package provides python-mpv (install it separately via pip)

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-3
- Fix %%build/%%install to use %%pyproject_wheel/%%pyproject_install
  instead of %%py3_build/%%py3_install -- this project has no setup.py
  (pyproject.toml/PEP 517 only), so the legacy macros' implicit
  'python3 setup.py build' failed with ENOENT

* Sat Jul 18 2026 Iain Smith <iain@issinoho.com> - 0.1.0-2
- Add -v/--version flag to report the tvdinner package version

* Sat Jul 18 2026 Iain Smith <iain@issinoho.com> - 0.1.0-1
- Initial RPM packaging, tracking the .deb package's feature set:
  M3U playback via mpv, XMLTV EPG overlay and full program guide with
  channel-name filtering, per-channel EPG time-shift correction
  (config file and live keybinding), aspect ratio cycling.
