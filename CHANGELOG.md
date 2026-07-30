# Changelog

All notable changes to tvdinner are documented in this file.

## 0.1.0-78 - Fri, 31 Jul 2026

- Add a `t` keybinding to toggle subtitles on/off, for streams that carry a subtitle track of their own (e.g. UK DVB broadcasts commonly do) but that mpv doesn't auto-select. Reports "No subtitles available" if the current channel has none; mpv's own default `j`/`J` keys still cycle between multiple tracks/languages

## 0.1.0-77 - Fri, 31 Jul 2026

- Improve washed-out channel logos in the guide: logos are now cropped to their own visible content before fitting into the tile (some assets carry a lot of dead transparent padding around the actual mark), and the tile's background is picked adaptively (light or dark) based on the logo's own average luminance, so a pale logo meant for a dark background no longer looks like a blank white square

## 0.1.0-76 - Fri, 31 Jul 2026

- Fix the EPG banner's channel logo staying blank forever on HDHomeRun -- it was cached once at startup, before the background EPG fetch that HDHomeRun logos depend on had finished, and never recomputed afterwards. The guide (which recomputes logos on every render) was unaffected; the banner now does the same

## 0.1.0-75 - Fri, 31 Jul 2026

- Revert the 0.1.0-74 picture-in-picture fullscreen-restore attempt -- confirmed live it didn't actually fix the keybindings-unresponsive bug (the window manager still never handed keyboard focus back), and mpv has no way to force it. Leaving PiP now always restores a normal windowed state again, as it did before 0.1.0-71

## 0.1.0-74 - Thu, 30 Jul 2026

- Fix keybindings going unresponsive after leaving picture-in-picture -- sending the window manager a fullscreen request bundled together with simultaneous border/ontop/window-scale/geometry resets left some window managers with mpv reporting `fullscreen=True` internally but never actually regaining keyboard focus. The normal window state is now restored first, with the fullscreen request (if any) sent last

## 0.1.0-73 - Thu, 30 Jul 2026

- Fall back to the EPG's own channel icon for logos: HDHomeRun's `lineup.json` has no per-channel logo field at all, so the guide showed no logo whatsoever for those channels. SiliconDust's own XMLTV export already includes a real per-channel icon, which was being parsed but never used for display -- now used as a fallback whenever a channel has no logo of its own, at no extra network cost since it rides on the EPG document already fetched/cached

## 0.1.0-72 - Thu, 30 Jul 2026

- Fix leaving picture-in-picture always dropping to a windowed state instead of restoring full screen -- now that full screen is the default (0.1.0-71), the PiP toggle remembers whatever fullscreen state was active before entering PiP and restores it on the way back out

## 0.1.0-71 - Thu, 30 Jul 2026

- Default to starting full screen, with a new `--disable-full-screen` flag to start in a normal window instead

## 0.1.0-70 - Thu, 30 Jul 2026

- The default channel on launch (no `--channel` given) now also prefers HD, matching the guide's HD-first ordering (0.1.0-69) -- previously it still started on the raw playlist's first channel, which could be the non-HD variant of the same channel the guide itself opened on

## 0.1.0-69 - Thu, 30 Jul 2026

- Sort HD channels to the top of the program guide -- detected by a case-insensitive "HD" word match against the channel's display name, so it works the same across M3U, Xtream, Stalker, and HDHomeRun sources with no per-source changes. Only affects guide browsing order; channel selection, `--list`, and the channel played on launch are unaffected

## 0.1.0-68 - Thu, 30 Jul 2026

- Fix the HDHomeRun duplicate-name disambiguation (0.1.0-66) breaking EPG matching for the channels it disambiguated -- the EPG feed still lists them under their plain, undisambiguated name, so appending `(<GuideNumber>)` to the display name made them vanish from the guide entirely instead of showing (duplicated) EPG data. `tvg_name` now carries the original name so EPG lookup still finds them

## 0.1.0-67 - Thu, 30 Jul 2026

- Fix the XMLTV parser dropping a channel's name variants when the feed lists more than one `<channel>` element under the same id (e.g. SiliconDust's HDHomeRun export, one block per SD/HD simulcast of a station) -- a later block silently overwrote an earlier one's display-names, breaking the name-based EPG match for any channel whose only surviving name didn't match. Blocks sharing an id are now merged instead of overwritten

## 0.1.0-66 - Thu, 30 Jul 2026

- Fix duplicate HDHomeRun `GuideName` entries colliding in favorites: some lineups list the same channel twice under different `GuideNumber`s, and since favorites/`--epg-shifts`/the guide's favorites-only filter all identify a channel by display name, an undisambiguated duplicate meant favoriting one row silently favorited both. The `GuideNumber` is now appended to a name only when it's actually ambiguous

## 0.1.0-65 - Thu, 30 Jul 2026

- Add VOD (movies) support: browse and play on-demand movies with the new 'm' key, independent of the time-grid EPG guide, which was never the right place for content with no scheduled airing. Sourced from Xtream and Stalker panels' native VOD APIs (fetched automatically alongside live channels) and from plain M3U playlists via a new `--vod-group` flag (opt-in, no effect by default) that pulls named group-titles out of the channel list into the new browser. Movies resume from their last position like recordings do

## 0.1.0-64 - Thu, 30 Jul 2026

- Fix a URL pasted with wrapping shell quotes (e.g. copy-pasting the doc examples' `'hdhomerun://192.168.1.50'` literally, quotes and all, into a bookmark) silently breaking scheme detection -- a leading/trailing quote isn't valid in a URL scheme, so it fell through to being treated as an unplayable raw stream with no clear error. `main()`'s `url`/`--epg` arguments and the bookmarks form's URL/EPG fields now strip one matching pair of wrapping quotes

## 0.1.0-63 - Thu, 30 Jul 2026

- Fix bookmarks logging a selected/added/edited `xtream://` or `stalker://` bookmark's URL unredacted -- the plaintext password or MAC address was written straight to the log file, even though playback itself has always redacted the same URL. Also updates the bookmarks form/table's stale "M3U URL" label now that bookmarks accept any of the URL forms tvdinner does

## 0.1.0-62 - Thu, 30 Jul 2026

- Add EPG support for HDHomeRun tuners: fetches guide data automatically from SiliconDust's XMLTV API (real XMLTV, parsed with the same code path as any other feed) when the device reports a paid HDHomeRun DVR guide subscription; devices without one simply show no guide data, same as any other inaccessible EPG source

## 0.1.0-61 - Wed, 29 Jul 2026

- Add native HDHomeRun support: URL now also accepts an `hdhomerun://host[:port]` tuner address, fetching the device's channel lineup directly (no login -- HDHomeRun has no authentication -- and no separate M3U export step). No EPG support for HDHomeRun sources yet

## 0.1.0-60 - Wed, 29 Jul 2026

- Add an `o` key to toggle picture-in-picture: shrinks the window to a small, always-on-top, borderless corner window (bottom-right, 25% size) so playback keeps going while other apps are used, closing any open guide/browser overlay first; press again to restore. Relies on the window manager honoring mpv's placement request -- confirmed working on GNOME/Mutter

## 0.1.0-59 - Wed, 29 Jul 2026

- Add native Stalker Portal (Ministra) support: URL now also accepts a `stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF` login (`stalkers://` for https), logging in with the given MAC, fetching the live channel list, and resolving each channel's playable stream URL up front via the portal's `create_link` call. Portal genres map onto the existing group-title machinery, so the guide/filter/favorites/recording/bookmarks all work unchanged. No EPG support for Stalker sources yet. The MAC is never written to the log file (always shown redacted)

## 0.1.0-58 - Wed, 29 Jul 2026

- Add native Xtream Codes support: URL now also accepts an `xtream://username:password@host:port` login (`xtreams://` for https), fetching the live channel list and pointing EPG loading at the panel's own `xmltv.php` export directly, with no separate M3U export step needed. Panel categories map onto the existing group-title machinery, so the guide/filter/favorites/recording/bookmarks all work unchanged. Credentials are never written to the log file (always shown redacted)

## 0.1.0-57 - Mon, 27 Jul 2026

- Wire a remote's dedicated play/pause button (mpv reports it as PLAY/PAUSE/PLAYPAUSE) to the same pause/resume-live-TV logic as the 'p' key, instead of falling through to mpv's plain default binding
- Resume recordings from the last played position instead of always starting from the beginning, autosaving position periodically during playback and on channel/recording switch

## 0.1.0-56 - Mon, 27 Jul 2026

- Add the 'p' (pause/resume live TV) keybinding to the in-app '?' keyboard-shortcuts overlay, which had never listed it

## 0.1.0-55 - Mon, 27 Jul 2026

- Add a 'p' key to pause/resume live TV with rewind/fast-forward: resuming continues from the paused position rather than jumping back to live, auto-resuming after --live-buffer-minutes (default 10) if left paused that long

## 0.1.0-54 - Sun, 26 Jul 2026

- Add the app's logo mark to the guide/browser/help header bars, for a consistent brand identity across the app and the marketing site

## 0.1.0-53 - Sun, 26 Jul 2026

- Fix scheduled recordings comparing raw EPG times against real time: the "already ended" check and the poll loop that starts/stops recordings ignored any per-channel --epg-shifts correction, which could reject a programme as already ended when it hadn't started yet, or record hours off from the real air time
- Surface missed scheduled recordings (a schedule conflict, or its channel no longer being in the playlist) instead of silently dropping them: an on-screen notification plus a "Missed" section in the 'u' scheduled view

## 0.1.0-52 - Sun, 26 Jul 2026

- Add a 'u' key to browse all upcoming scheduled recordings: date-grouped, soonest first, marking whichever entry is currently recording; ENTER cancels the selected one
- Add a '?' keybinding-cheat-sheet overlay listing every binding

## 0.1.0-51 - Sun, 26 Jul 2026

- Show a dedicated overlay when 'i' is pressed during recording playback, instead of stale live-channel EPG info: the recording's own label, recorded date, and a playback-progress bar

## 0.1.0-50 - Sun, 26 Jul 2026

- Add a 'd' key to delete recordings from the recordings browser. Two-step confirm since deletes are permanent: the first 'd' arms a confirmation, a second 'd' on the same still-selected recording deletes it

## 0.1.0-49 - Sun, 26 Jul 2026

- Strip characters our bundled font can't render from EPG/channel text: some IPTV playlists append decorative circled-letter Unicode badges to channel names that DejaVuSans has no glyph for, which used to show up as a visible empty-box artifact right after the channel name

## 0.1.0-48 - Sun, 26 Jul 2026

- Show a red "R" badge in the guide for scheduled recordings, so what's queued to record is visible at a glance without opening its details popup

## 0.1.0-47 - Sun, 26 Jul 2026

- Add a recordings browser ('w' key) to watch back past recordings: lists previously saved recordings (r-key or scheduled) grouped by date, newest first. UP/DOWN/PGUP/PGDWN move the selection, ENTER plays it back, ESC closes

## 0.1.0-46 - Sun, 26 Jul 2026

- Add EPG-scheduled recording ('s' key on programme details): a background poll thread switches to the scheduled channel (single-tuner style, interrupting current viewing) and starts/stops recording automatically at the programme's start/stop time. Persisted to --schedule-file so a schedule survives a restart, as long as tvdinner is running again by record time

## 0.1.0-45 - Sun, 26 Jul 2026

- Add a manual recording toggle ('r' key): dumps the current stream's raw bytes to disk via mpv's stream-record (no re-encoding), saved under --record-dir (platform-aware default) as `<channel>_<timestamp>.ts`

## 0.1.0-44 - Sat, 25 Jul 2026

- Add an "EPG Refresh" checkbox column to the bookmarks table: SPACE toggles it on the highlighted row (unchecked by default, not persisted between sessions), and launching a bookmark with it checked runs tvdinner with --refresh-epg-cache

## 0.1.0-43 - Sat, 25 Jul 2026

- Add a self-contained macOS app (`tvdinner-<version>.dmg`, built via PyInstaller), bundling a Homebrew-built libmpv so there's no separate Python or mpv install step. Since a double-clicked app has no terminal to pass a URL argument to, launching it prompts for the M3U/stream URL instead, remembering the last one used. Unsigned for now, so Gatekeeper requires right-click > Open on first launch. Also adds macOS-idiomatic (~/Library/...) default paths for the EPG shifts, favorites, bookmarks, and log files, alongside the existing Windows/Linux ones

## 0.1.0-42 - Sat, 25 Jul 2026

- Add a self-contained Windows installer (`tvdinner-setup-<version>.exe`, built via PyInstaller + Inno Setup), bundling a pre-built mpv so there's no separate Python or mpv install step on Windows anymore. Unsigned for now, so Windows SmartScreen will warn on first run

## 0.1.0-41 - Sat, 25 Jul 2026

- Fix a guide crash on a narrow shifted programme block clipped by the visible window's edge (a width check didn't account for the rectangle's own inward padding)
- Add backup/restore for configuration files: 'tvdinner backup [PATH]' writes EPG shifts, favorites, and bookmarks into a single zip archive; 'tvdinner restore PATH' extracts one back onto disk, prompting for confirmation unless -y/--yes is given

## 0.1.0-40 - Sat, 25 Jul 2026

- Add logging to the bookmarks feature: its own --log-file/--no-log, every action logged, and the same log setting carried into a launched bookmark's playback session so it all lands in one file. configure_logging is now idempotent for a given path to make that safe; save failures are now caught and logged instead of crashing the TUI

## 0.1.0-39 - Sat, 25 Jul 2026

- Add an optional default channel field to bookmarks (e.g. "CNN"), forwarded as --channel when a bookmark is launched; old bookmark files without it keep loading fine

## 0.1.0-38 - Sat, 25 Jul 2026

- Add a bookmarks feature: 'tvdinner bookmarks' opens an interactive terminal table of saved playlists (description, M3U URL, optional EPG URL) -- add/edit/delete entries, and ENTER launches tvdinner with the selected one directly. Saved to ~/.config/tvdinner/bookmarks.json

## 0.1.0-37 - Fri, 24 Jul 2026

- Show the favorite heart marker on the EPG banner overlay too, not just the guide grid; toggling a favorite now redraws the banner immediately if it's currently showing
- Now licensed under MIT (previously all-rights-reserved)

## 0.1.0-36 - Fri, 24 Jul 2026

- Fix 'h' (favorite toggle) staying unbound for the rest of the session after using the guide filter even once -- its restoration was missing from finish_filter_input alongside g/i/z

## 0.1.0-35 - Fri, 24 Jul 2026

- Add a Favorites feature, persisted per feed: 'h' toggles the guide's selected (or currently-playing) channel as a favorite, shown with a heart in the guide; 'v' toggles a favorites-only guide view. New --favorites flag, mirroring --epg-shifts

## 0.1.0-34 - Fri, 24 Jul 2026

- Add a Stretch aspect ratio (cycled with 'z') that fills the window exactly using mpv's keepaspect=no, distorting the image if needed, rather than a fixed ratio that still letterboxes

## 0.1.0-33 - Fri, 24 Jul 2026

- Show a channel's group in the guide overlay: a small muted line under its name (joined with " · " for channels tagged under several groups at once), so groups are visible in the guide itself rather than only via --list

## 0.1.0-32 - Fri, 24 Jul 2026

- Add group-based filtering to the guide: the 'f' text filter now also matches a channel's group(s) (including semicolon-compound group-title values like "Movies;Series"), not just its name

## 0.1.0-31 - Fri, 24 Jul 2026

- Strip trailing decorative symbols (e.g. a circled-letter marker some playlist generators append to a channel's name) before EPG name-fallback matching, so a channel whose real name is otherwise identical to the EPG's own display name isn't silently left without a schedule

## 0.1.0-30 - Thu, 23 Jul 2026

- Add --refresh-epg-cache to force a one-off EPG re-download for this run while still refreshing the on-disk cache with the result (unlike --no-epg-cache, which never reads or writes one)

## 0.1.0-29 - Thu, 23 Jul 2026

- Stream-parse XMLTV (ElementTree.iterparse) instead of building a full DOM (ElementTree.fromstring) to cut EPG load memory use: a real ~500MB US EPG feed previously peaked at ~5GB RSS and settled at ~4.3GB after parsing; now peaks at ~1.2GB and settles at ~0.75GB, with identical parsed output and no change in parse time

## 0.1.0-28 - Wed, 22 Jul 2026

- Include the packaging release number in __version__: -v and the startup log line both read it, but it was stuck at the bare upstream "0.1.0" and never reflected which packaged build was actually running

## 0.1.0-27 - Wed, 22 Jul 2026

- Add file logging for startup/shutdown, every user action (guide open/close, filter, channel switch, EPG shift, aspect ratio, programme details), and any warning/error (playback failures, EPG/ playlist fetch/parse/cache failures, image fetch/decode failures). Logged to ~/.cache/tvdinner/tvdinner.log by default (%LOCALAPPDATA% on Windows); configurable via --log-file/--no-log

## 0.1.0-26 - Wed, 22 Jul 2026

- Keep the window/input alive when a channel fails to play: a dead or rejected stream previously left mpv with no video track and thus no window at all, silently stranding the app with no way to pick another channel. force_window keeps the window up regardless, and a new failure hook shows "Failed to play `<channel>`" and reopens the guide instead

## 0.1.0-25 - Wed, 22 Jul 2026

- Print EPG load progress to stderr: "Loading EPG data..." when a fetch/parse starts, and a loaded ("N channels")/not-available result line when it finishes, for both --list and the background load during playback

## 0.1.0-24 - Wed, 22 Jul 2026

- Speed up EPG startup: playback no longer blocks on EPG fetch/parse (loaded in a background thread and swapped in once ready), the on-disk cache now stores the parsed EPG alongside the raw bytes so a cache hit skips re-parsing too, and merge() only re-sorts schedules actually touched by the merged source

## 0.1.0-23 - Wed, 22 Jul 2026

- Cache downloaded EPG data on disk (default: ~/.cache/tvdinner/epg), refreshed once a day by default, so startup with a large XMLTV feed doesn't re-download and re-parse it every time; a stale cache is used as a fallback if a refresh attempt fails. New --epg-cache-hours and --no-epg-cache flags control this

## 0.1.0-22 - Wed, 22 Jul 2026

- Fix EPG data not matching for many real playlist/guide combinations: fall back to the tvg-id with a trailing '@SD'/'@HD'/etc. feed tag stripped (iptv-org's own playlists append one to disambiguate multiple feeds of one channel), then to a normalized display-name match (some XMLTV providers prefix every name with their own source tag, e.g. "PLUTO - 00s Replay"), before giving up

## 0.1.0-21 - Tue, 21 Jul 2026

- Add key bindings for IR/BLE air-mouse remotes (e.g. nRF-based USB dongles): ENTER (their OK/center button) shows the EPG overlay outside the guide, and MENU toggles the full program guide

## 0.1.0-20 - Tue, 21 Jul 2026

- Show a programme's release year (from XMLTV's `<date>` element) in the EPG banner, program guide timeline cells, and programme details popup, e.g. "The Lady From Shanghai (1948)"

## 0.1.0-19 - Mon, 20 Jul 2026

- Fix Windows portability gaps: bundle the DejaVu fonts as package data instead of reading /usr/share/fonts/truetype/dejavu (drops the fonts-dejavu-core dependency, now redundant), use %APPDATA% for the EPG shift config path on Windows, and only apply the X11/Wayland gpu_context override on Linux -- it's a hard mpv option error, not a graceful no-op, on Windows builds of libmpv. Confirmed working end-to-end via a plain pip install on Windows.
- Add a GitHub Actions workflow to build and publish .deb/.rpm release packages automatically on version tag pushes

## 0.1.0-18 - Sat, 18 Jul 2026

- Show a "X min remaining" / "Xh Ym remaining" line under the EPG overlay's progress bar, indicating how much running time is left in the current programme

## 0.1.0-17 - Sat, 18 Jul 2026

- Add -v/--version flag to report the tvdinner package version

## 0.1.0-16 - Sat, 18 Jul 2026

- Add video/audio quality badges (resolution, codecs, fps, HDR, channel layout) to the OSD banner, read from mpv's stream info
- Fix the OSD banner skipping entirely when there's no EPG data -- quality badges are independent of EPG availability and now show regardless

## 0.1.0-15 - Sat, 18 Jul 2026

- Add channel-name filtering to the program guide: 'f' opens a text-entry dialog (case-insensitive substring match, confirmed with ENTER, cancelled with ESC), 'c' clears an active filter (both guide only)

## 0.1.0-14 - Sat, 18 Jul 2026

- Add PGUP/PGDWN keybinding to page the program guide's channel selection a full page at a time (guide only)

## 0.1.0-13 - Sat, 18 Jul 2026

- Fix guide channel switching for EPG-less playlists: show a visible selection border even when a channel has no schedule to draw a programme block around, and let the selection cursor scroll the guide window past the initially visible rows instead of clamping at the edge

## 0.1.0-12 - Sat, 18 Jul 2026

- Bind guide/OSD keys ('i', 'g', navigation) even when a playlist has no discoverable EPG source at all, reporting "no data" via a brief OSD message instead of silently doing nothing

## 0.1.0-11 - Fri, 17 Jul 2026

- Anchor the program guide overlay to the bottom of the screen instead of vertically centering it

## 0.1.0-10 - Fri, 17 Jul 2026

- Add '[' / ']' keybinding to nudge the selected guide channel's EPG shift by 1 minute and persist it immediately to --epg-shifts, so corrections can be dialled in live instead of hand-editing JSON

## 0.1.0-9 - Fri, 17 Jul 2026

- Key --epg-shifts overrides by channel display name instead of tvg_id: real playlists commonly have distinct channels (e.g. an East/West regional pair) sharing one tvg_id for EPG mapping, so a tvg_id-keyed shift couldn't target just one of them

## 0.1.0-8 - Fri, 17 Jul 2026

- Per-channel EPG time-shift overrides via --epg-shifts (a JSON file mapping tvg_id to a shift), for feeds where different channels are off by different clock amounts; --time-shift remains the default for channels without an override
- Fix program guide live-highlighting and block positioning to actually apply the configured shift (previously only the OSD banner's now/next text did)

## 0.1.0-7 - Fri, 17 Jul 2026

- Show movie poster art (from XMLTV per-programme `<icon>` data) in the program guide's programme details popup ('i' on a selected guide programme)

## 0.1.0-6 - Fri, 17 Jul 2026

- Show channel logos on a light rounded tile instead of straight on the dark panels, so dark line-art logos (common in real provider feeds, e.g. TCM's) don't render as effectively invisible

## 0.1.0-5 - Fri, 17 Jul 2026

- Suppress a benign python-mpv key-binding race warning that could print an alarming (but harmless) traceback during normal use

## 0.1.0-4 - Fri, 17 Jul 2026

- Fix program guide highlighting matching by tvg_id instead of the channel's unique URL: real-world playlists often have several distinct channels (quality tiers, backup servers) sharing one tvg_id for EPG mapping, which made every such row light up together instead of just the intended one

## 0.1.0-3 - Fri, 17 Jul 2026

- Default to the first playlist channel on startup instead of prompting interactively
- Program guide: tuned channel now shown with a quiet edge stripe only, instead of a full-row tint that could look like two rows were highlighted at once alongside the selection cursor

## 0.1.0-2 - Fri, 17 Jul 2026

- Program guide: select programmes with UP/DOWN, switch channels with ENTER, show full details with 'i' (guide only)
- Movie poster support in the EPG overlay, sourced from XMLTV per-programme `<icon>` data
- Bind 'z' to cycle video aspect ratio (Auto/4:3/16:9/2.35:1/1:1)
- Restore standard window borders/decorations
- Guide layout fixes: compact canvas-anchored fonts and row heights, full window width, timeline paging with LEFT/RIGHT
- Fix a crash where switching channels could tear down the whole player (wait_for_playback end-file handling)

## 0.1.0-1 - Fri, 17 Jul 2026

- Initial release: M3U playback via mpv, XMLTV EPG overlay and full program guide, aspect ratio cycling.
