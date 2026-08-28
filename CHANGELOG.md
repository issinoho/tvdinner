# Changelog

All notable changes to tvdinner are documented in this file.

## 1.30.0 - Fri, 28 Aug 2026

- Show a context-sensitive item count in the Plex browser header -- "(x movies)"/"(x shows)"/"(x episodes)"/"(x Seasons)" for a listing one level below a library or Continue Watching, no suffix at all for the root library list itself

## 1.29.3 - Fri, 28 Aug 2026

- Fix chapter thumbnails showing a real but blank-looking frame for TV rips whose chapter markers land exactly on a fade-to-black transition cut -- the local frame grab now seeks a couple of seconds past a chapter's start instead of its exact boundary
- Raise the chapter-thumbnail capture timeout 15s -> 30s -- a slower debrid source measured 10-15s per successful grab, occasionally longer

## 1.29.2 - Fri, 28 Aug 2026

- Fix chapter thumbnails staying permanently blank for the rest of a session after failing to generate once -- a failed fetch was being cached exactly as permanently as a successful one, blocking any future retry even though a fresh attempt would often work

## 1.29.1 - Thu, 27 Aug 2026

- Fix chapter preview thumbnails almost never appearing in time -- the local-frame-grab fallback routinely took several seconds, longer than the preview stayed up for; neighboring chapters' thumbnails now prefetch in the background as you browse, so they're usually already warm by the time you get there

## 1.29.0 - Thu, 27 Aug 2026

- Chapter thumbnail scrub previews: UP/DOWN, while playing a Plex VOD item with real embedded chapters, now shows a small preview panel (thumbnail + title) for the next/previous chapter instead of seeking immediately -- ENTER jumps there, ESC cancels, or leave it a couple of seconds to jump there automatically. Thumbnails use Plex's own chapter thumbnail when it has one, falling back to a frame grabbed locally otherwise.

## 1.28.3 - Thu, 27 Aug 2026

- Security: stop leaking Xtream/Stalker/Plex credentials embedded in per-resource URLs into the log file (channel switches, VOD/Plex playback, image-fetch failures) -- only the top-level login URL was ever redacted before
- Security: favorites.json is no longer keyed by the raw Xtream/Stalker login URL (a real username/password sitting in the file); an existing file still keyed that way is migrated automatically and the old entry scrubbed
- Security: tmdb_token.json, bookmarks.json, favorites.json, playback_positions.json, and history.jsonl are now written with owner-only (0600) permissions, matching the Google Drive credentials file's existing behavior

## 1.28.2 - Thu, 27 Aug 2026

- Fix the Plex browser's "Added/Removed from favorites" message sometimes not showing (a favorite toggle's own redraw could eat into its short display time before the frame reached the screen)
- Fix the Plex browser's title logo showing the wrong movie/show after favoriting an item and switching to the favorites-only view -- it now matches the backdrop, which was always correct

## 1.28.1 - Thu, 27 Aug 2026

- Keep `g`/`h`/`v`/`l`/`y` (grid/list view, favorite, favorites-only, close, year filter) live in the Plex browser even while viewing a movie/show listing, instead of shadowing them for jump navigation like every other letter -- those five are too useful to lose at exactly the level where jump-nav is active

## 1.28.0 - Thu, 27 Aug 2026

- Alphabetical jump navigation: press a letter or digit in the VOD browser, or in the Plex browser while viewing a movie/show listing, to jump to the next title starting with it (press again to cycle through repeated matches). The VOD browser's list is now sorted alphabetically within each category, since it had no other meaningful order.

## 1.27.0 - Thu, 27 Aug 2026

- Press `i` again while the info overlay (EPG banner/hero, guide details popup, VOD info card, Plex DETAILS popup) is already showing to open the item's TMDB page in the default browser -- covers movies from any source and Plex shows/episodes (read straight from Plex's own metadata, no `--tmdb-api-token` needed)

## 1.26.2 - Wed, 26 Aug 2026

- Fix a Plex episode in a flat listing (search results, Continue Watching) playing the wrong show's theme/title-logo -- it now always uses its own show, not whatever was browsed earlier

## 1.26.1 - Wed, 26 Aug 2026

- Speed up the Plex grid browser's rendering on rapid navigation -- cache the panel shadow/backdrop wash and the static tile layer, so moving between shows on the same page no longer redraws everything from scratch

## 1.26.0 - Wed, 26 Aug 2026

- Play a Plex show's theme music while browsing its library page, matching the official Plex clients -- fades out on navigating away or picking something to actually watch; see `--no-plex-theme-music`

## 1.25.2 - Wed, 26 Aug 2026

- Show the Plex root wallpaper (instead of mpv's own idle-screen logo) behind the "Up Next" countdown card

## 1.25.1 - Wed, 26 Aug 2026

- Sharpen the Plex library browser's background wallpaper blur -- previous value read as noticeably blurry on a real screen

## 1.25.0 - Wed, 26 Aug 2026

- Add watching-activity stats to `tvdinner stats` -- total watch time this week/month/all-time, split by live channel/VOD/recording, plus the most-watched live channels this month and all-time

## 1.24.0 - Tue, 25 Aug 2026

- Add `--no-tmdb-cache`/`--refresh-tmdb-cache`, mirroring the existing EPG cache flags, so a bad cached TMDB match doesn't have to wait out its 30-day TTL
- Expire a VOD resume position after 90 days of nobody resuming or updating it, instead of keeping it forever

## 1.23.0 - Mon, 24 Aug 2026

- Tab the keyboard-shortcuts help overlay (`?`) by category (Guide, Playback, VOD & Chapters, Recording & History, Plex) instead of one 36-entry wall of text -- `LEFT`/`RIGHT` switches tabs while it's open

## 1.22.0 - Mon, 24 Aug 2026

- Add `--audio-passthrough`, `--audio-downmix-boost`, and `--loudness-normalization` flags -- launch-time audio tuning options, same shape as `--interpolation`/`--glsl-shader`

## 1.21.0 - Mon, 24 Aug 2026

- Add a sleep timer (`e` cycles Off/15/30/60/90 minutes) and document the playback-speed/audio-sync/zoom-pan controls mpv already provided by default but the app never mentioned anywhere

## 1.20.1 - Mon, 24 Aug 2026

- Let ENTER confirm the skip-intro/credits prompt, not just `j` -- an IR/BLE air-mouse remote's OK button sends ENTER, not an arbitrary letter, so the prompt was previously unreachable from one. ENTER's own "toggle pause" meaning is only shadowed while the prompt is showing, restored the moment it closes

## 1.20.0 - Mon, 24 Aug 2026

- Add richer technical detail to the 'i' overlay -- container, video/audio bitrate, and every audio/subtitle track (not just the one selected), for any source, not just Plex. Hero layouts get one compact line; the plain banner/card layouts get the full per-track breakdown

## 1.19.0 - Mon, 24 Aug 2026

- Add skip intro/credits for chaptered Plex VOD playback -- a small "Skip Intro"/"Skip Credits" prompt (confirmed with `j`) appears while playback is inside one of Plex's own intro/credits marker windows; `--no-skip-markers` disables it
- Add auto-play next episode for Plex TV shows -- an "Up Next" prompt with a cancellable countdown offers the next episode once the current one plays through to a genuine end; `ESC` cancels; `--no-autoplay-next-episode` disables it, `--autoplay-countdown-seconds` adjusts the countdown length

## 1.18.2 - Sun, 23 Aug 2026

- Fix a crash-worthy ERROR-level traceback when `is_paused` races mpv shutdown -- the periodic playback-position autosave loop could hit this right as mpv's core was torn down mid-quit; now returns `False` cleanly instead of raising

## 1.18.1 - Sun, 23 Aug 2026

- Suppress noisy "Multiple Dolby Vision RPUs found in one AU" ffmpeg warning spam in the log -- fires dozens of times a second on some Dolby Vision streams and has no bearing on actual decode correctness

## 1.18.0 - Sun, 23 Aug 2026

- Skip between chapters on UP/DOWN for chaptered VOD playback (Plex only, for items with real chapter markers) -- "previous" jumps to the current chapter's own start unless already within 5s of it, mirroring standard DVD/Blu-ray remote behavior. Falls back to mpv's default 60s seek for anything without chapters; `--no-chapter-skip` keeps the old default seek everywhere

## 1.17.1 - Sun, 23 Aug 2026

- Shrink the HDR-type tag on the hero `i`-key overlays to a smaller, bolder mark -- it previously rendered at the surrounding row's own text size/weight, competing with the time range/year next to it instead of reading as a quiet companion mark

## 1.17.0 - Sun, 23 Aug 2026

- Show HDR type (Dolby Vision/HDR10+/HDR10) as a small tag on the hero `i`-key overlays -- channel/EPG and VOD info -- next to the time range or year

## 1.16.3 - Sun, 23 Aug 2026

- Fix Dolby Vision/HDR10+ streams being mislabeled as plain HDR10 in the OSD quality badge -- the badge only checked mpv's gamma property (which is "pq" for static HDR10 and both dynamic-metadata formats alike); now also checks colormatrix for Dolby Vision and scene-max-r's presence (real dynamic HDR10+ metadata) before falling back to plain HDR10

## 1.16.2 - Sun, 23 Aug 2026

- Fix the Plex browser reopening on top of playback started from history -- selecting a Plex movie/episode from the history browser started playback correctly, but close_history_browser's own Plex-reopen logic (meant for backing out of history via ESC) fired regardless, popping the Plex browser back up over the now-playing video; play_selected_history_entry now suppresses that reopen whenever it's actually starting playback

## 1.16.1 - Sun, 23 Aug 2026

- Fix Plex chapter markers never showing up in tvdinner -- resolve_plex_playable fetched Plex's item metadata without the `includeChapters=1` param, which Plex silently requires to include the Chapter array at all, even for an item whose own chapterSource field proves it has real chapter data; chapters showed fine in Plex's own clients but never made it into tvdinner

## 1.16.0 - Sun, 23 Aug 2026

- Show chapter markers on the VOD info overlay's progress bar -- when Plex's own metadata carries real chapter markers (e.g. a Blu-ray/DVD rip), a tick mark now shows at each chapter boundary and the current chapter's title shows next to the time readout

## 1.15.4 - Sat, 22 Aug 2026

- Fix the guide's arrow keys getting stuck after a no-match filter -- render_and_show_guide had no fallback when the filter (or favorites-only view) emptied the eligible channel list, so every arrow-key press silently did nothing once that happened, permanently; now clears whichever filter emptied the list and falls back to what's left, same as the Plex browser's own favorites-only filter already did

## 1.15.3 - Sat, 22 Aug 2026

- Fix the guide's arrow-key scrolling getting stuck on duplicate URLs -- some real playlists reuse the exact same stream URL for a channel's SD and HD listing (e.g. "Channel 5" and "Channel 5 HD"), which the guide's selection tracking always resolved to the *first* matching row, so the cursor could never advance past such a pair; now also tracks each channel's name alongside its URL for position-finding

## 1.15.2 - Fri, 21 Aug 2026

- Fix the Plex browser's season/show poster backdrop staying blank for a Continue Watching episode -- the new `season_thumb_url` field was never added to the image-prefetch list, so it could never actually be fetched/decoded even though the URL itself resolved correctly (the title logo showed fine, just not the backdrop)

## 1.15.1 - Fri, 21 Aug 2026

- Exclude undecodable SVG logos from TMDB title-logo selection -- TMDB's logo images can be SVGs (e.g. "Friends"), which Pillow can't decode at all, silently resolving a logo that could never actually display; also made the on-disk logo caches self-healing for entries written before this fix
- Fix the season backdrop/title logo never showing for Continue Watching episodes -- both features relied on walking the Plex nav stack to find a season/show ancestor, which doesn't exist for the on-deck listing's flat episode rows; now read straight off each episode's own Plex metadata instead, which carries this regardless of listing context

## 1.15.0 - Fri, 21 Aug 2026

- Show the movie/show's TMDB title logo in the Plex browser's full-screen backdrop, visible while browsing rather than only once something is playing -- walks up the nav stack to the nearest movie/show ancestor for a season/episode listing, since those have no title of their own to search TMDB with
- Fix a regression where an on-deck (Continue Watching) episode's backdrop could go blank instead of showing its own thumbnail -- the season-artwork fallback is now only trusted when the immediately-enclosing node is actually a season

## 1.14.1 - Thu, 20 Aug 2026

- Bind MENU to the info overlay during bare local-file/YouTube playback -- an air-mouse remote's MENU button already worked for a live channel or Plex session, but was never bound at all here, silently falling through to mpv's own unused on-screen-select-script default instead

## 1.14.0 - Thu, 20 Aug 2026

- Show TMDB title logos for Plex TV episodes too -- searches TMDB's `/search/tv` by the show's own name (Plex's `grandparentTitle`), instead of `/search/movie` by the episode's own title, which was never going to match. Logo art only; Plex already supplies its own backdrop for TV content

## 1.13.1 - Thu, 20 Aug 2026

- Fix the TMDB title logo never showing for Plex playback -- `main()`'s Plex branch never passed the TMDB token into `play_stream()` at all, silently skipping the logo lookup every time; the VOD "now playing" popup also had no way to redraw itself once a background logo/backdrop lookup completed, the same class of bug already fixed for the live-channel EPG hero

## 1.13.0 - Thu, 20 Aug 2026

- Show a TMDB title-treatment logo in the hero overlays -- fetched and cached alongside the existing backdrop art, composited in the top-right corner of both the live-channel EPG hero and the VOD/Plex info hero, backed by a scrim that adapts to white or black depending on the logo's own average lightness so it stays legible regardless of color. Plex items now also get a best-effort TMDB logo lookup layered on top of Plex's own backdrop art

## 1.12.0 - Wed, 19 Aug 2026

- Show item details for the Plex browser's current selection on `i` -- poster, year, director, synopsis, rating, resolved without starting playback, using the compact card layout so it reads cleanly on top of the browser's own poster backdrop
- Fix `Player.show_overlay`/`clear_overlay` to call the raw mpv `overlay-add`/`overlay-remove` commands with stringified arguments -- python-mpv's own wrapper methods could silently no-op (no error, nothing composited) when called from a key-binding callback while nothing was playing

## 1.11.1 - Wed, 19 Aug 2026

- Treat all Plex movie/TV libraries as one virtual library when sorting the year filter -- every movie library merges into one alphabetical-by-film-name list, every TV library into one alphabetical-by-show (then numeric by season/episode) list, instead of grouping by individual library first

## 1.11.0 - Wed, 19 Aug 2026

- Add a long-press-ENTER item menu to the Plex browser -- "Play from Start" (bypasses any resume position), "Mark as Watched", "Mark as Unwatched" against the selected movie, show, or episode; a normal tap still plays/drills in exactly as before
- Fix the Plex browser's full-screen poster backdrop to be fully opaque, instead of letting mpv's idle-screen logo bleed through it

## 1.10.4 - Wed, 19 Aug 2026

- Replace the Plex browser's plain black root backdrop (which let mpv's own idle-screen logo show through) with a gentle gradient wash and tvdinner's own logo mark in the top-left corner

## 1.10.3 - Wed, 19 Aug 2026

- Polish the Plex Grid view: the header bar now shows the selected item's trailing detail (subtitle or drill-in chevron), same as a List view row's own; toggling between Grid and List with `g` now keeps the same item focused instead of jumping back to the first one

## 1.10.2 - Wed, 19 Aug 2026

- Group Plex year-filter results by library, then sort within it -- a movie library's results alphabetically by film name, a TV library's alphabetically by show then numerically by season and episode, instead of one flat alphabetical list mixing everything together

## 1.10.1 - Wed, 19 Aug 2026

- Sharpen the Plex browser's poster backdrop -- reduce the Gaussian blur radius on both the full-screen and in-panel backdrops

## 1.10.0 - Wed, 19 Aug 2026

- Add a full-screen poster backdrop to the Plex browser -- the currently selected movie/show's poster now fills the whole screen behind the browser panel (blurred and tinted, Netflix-style), in both Grid and List view

## 1.9.2 - Wed, 19 Aug 2026

- Prefix Plex season breadcrumbs with the show name, e.g. "2 Broke Girls - Season 3" instead of just "Season 3"

## 1.9.1 - Wed, 19 Aug 2026

- Make Grid view the default for the Plex browser (was List view)

## 1.9.0 - Wed, 19 Aug 2026

- Add a Grid view to the Plex browser -- press `g` to switch between the existing List view and a new poster-grid view; the choice persists as you navigate deeper into libraries, shows, and seasons. In Grid view, `LEFT`/`RIGHT` move across columns instead of `LEFT` going back a level, so `ESC`/`GO_BACK` is the way back there.

## 1.8.3 - Wed, 19 Aug 2026

- Make GO_BACK act like BS (stop and drop back into the library browser) while playing in a Plex session, instead of falling through to mpv's own default binding (cycle fullscreen/window mode)

## 1.8.2 - Wed, 19 Aug 2026

- Fix the Plex favorites-only view always reporting "All items" -- `toggle_plex_favorites_only` was checking the flag after it could already have been silently auto-reverted by `render_and_show_plex`'s own empty-view fallback, so it never recognized an auto-revert as distinct from an intentional toggle-off

## 1.8.1 - Wed, 19 Aug 2026

- Report the real base OS (`X-Plex-Platform`) and this machine's own hostname (`X-Plex-Device-Name`) to Plex instead of hardcoding both to "tvdinner" -- shown as Tautulli/Plex's Platform and Player columns

## 1.8.0 - Wed, 19 Aug 2026

- Report Plex playback as a real session (Now Playing / Tautulli) -- the same timeline API Plex's own clients use, so playback via tvdinner shows up in Plex's dashboard and updates its own watched status/resume position; on by default, `--no-plex-activity` disables it
- Rename the synthetic Plex root row from "Continue Watching" to "On Deck"
- Pausing now shows the same info overlay `i`/`MENU` would (EPG banner or poster/synopsis/progress card) instead of just a plain "Paused" toast, auto-hiding itself as usual or clearing immediately on resume

## 1.7.0 - Tue, 18 Aug 2026

- Add a synthetic Continue Watching row to the Plex library root, pulled from Plex's own server-wide `/library/onDeck` feed (movies left partway through, plus the next unwatched episode of any show you're partway through)
- Fix the Plex favorites-only view getting permanently stuck (unresponsive arrow keys/selection) whenever the filter emptied out an otherwise non-empty frame -- now falls back to the unfiltered list instead

## 1.6.1 - Tue, 18 Aug 2026

- Reset the Plex favorites-only filter when drilling into a show -- it was carrying through to that show's seasons, which are never favoritable, silently rendering an empty list that looked like ENTER doing nothing

## 1.6.0 - Tue, 18 Aug 2026

- Show Plex's own watched status in the Plex browser -- a green checkmark badge for fully watched movies/episodes/shows/seasons, or a thin progress bar for partially watched ones, from Plex's own `viewCount`/`viewOffset`/`leafCount`/`viewedLeafCount` fields
- Reopen the Plex browser after help/about/history/Chromecast/update-notice closes, if it was open before that overlay stole focus
- Add the missing `l`/`/`/`y` Plex bindings to the help sheet
- Fall back to Plex's own watch progress (`viewOffset`) when resuming playback, if tvdinner has no local resume position for the item yet

## 1.5.0 - Tue, 18 Aug 2026

- Add a movie/show-level favorites system to the Plex browser -- `h` toggles the selected movie or show as a favorite (never a season or episode), persisted to the same `--favorites` file as guide channels; `v` shrinks the current listing to favorites only, same key as the guide's own favorites-only view

## 1.4.0 - Tue, 18 Aug 2026

- In Plex sessions, `BS` now stops the current item and drops back into the library browser exactly where you left off, instead of quitting tvdinner entirely
- Remove the remote Record-button key binding (the raw hex key name `0x211246`) -- confirmed live that this button's signal never reaches X11/XWayland clients, which is what the app forces mpv onto for window decorations, so the binding was permanently unreachable in practice; the `r` key still toggles recording

## 1.3.0 - Tue, 18 Aug 2026

- Bind BS to stop playback and quit cleanly -- at least one real remote's dedicated "DEL"/STOP button reports as BS to mpv, which by default just resets playback speed; repurposed as the closest equivalent this always-something-loaded app has to a STOP button, still shadowed by text-entry prompts' own BS "delete last character"
- Bind a remote's Record button (reported as the raw hex key name `0x211246`, since mpv has no proper symbolic name for it) to toggle recording, alongside the existing `r` key

## 1.2.0 - Tue, 18 Aug 2026

- Bind MENU in Plex sessions (a permanent alias for `i`, since Plex has no guide to hold for) -- previously Plex had no MENU binding at all
- Alias GO_BACK to ESC everywhere -- a remote's dedicated back button now always does exactly whatever ESC currently would, via a real synthesized keypress rather than duplicating every ESC binding site

## 1.1.0 - Tue, 18 Aug 2026

- MENU now distinguishes a tap from a hold on IR/BLE air-mouse remotes -- tap shows the programme info overlay (what ENTER used to do), hold (0.5s+) toggles the guide (what a plain MENU press used to do). ENTER becomes the universal play/pause key everywhere nothing else has claimed it, reusing the existing live-TV/recording/VOD pause logic; also fixes two spots that never restored ENTER's base binding after closing (the Chromecast picker, the Plex browser)

## 1.0.1 - Mon, 17 Aug 2026

- Fix update check crashing on a real-semver release tag -- `_parse_version` required a trailing `-N` build-counter suffix on every version string (the pre-1.0 `X.Y.Z-N` scheme), so every user's background update check crashed as soon as GitHub's latest release became a bare "1.0.0" with no dash (confirmed live via a Windows crash report). Now correctly parses and orders both the new real-semver format and any still-installed pre-1.0 version

## 1.0.0 - Sun, 16 Aug 2026

- First 1.0 release. Switches to real semantic versioning (`MAJOR.MINOR.PATCH`) going forward, replacing the previous `0.1.0-NN` build-counter scheme -- `PATCH` for fixes, `MINOR` for new features, `MAJOR` for breaking changes. No functional change in this release; tvdinner has been stable and feature-complete for a while, this just names it accordingly

## 0.1.0-160 - Sun, 16 Aug 2026

- Show the channel logo as a subtle mark in the live-channel Netflix-style hero overlay, next to the channel name -- previously omitted entirely, on the reasoning that the banner's big standalone tile would clash with the hero backdrop; the logo now sits inside the hero's own dark bottom info panel instead, sized to match the channel-name text rather than a prominent tile

## 0.1.0-159 - Sun, 16 Aug 2026

- Pick the highest-resolution textless backdrop from TMDB for the hero treatment, instead of trusting whichever single one `/search/movie` happened to mark as the default -- confirmed live that the default is often far from the largest actually available, which visibly hurt the full-bleed hero on larger/4K displays

## 0.1.0-158 - Sun, 16 Aug 2026

- Never send year as a hard filter to TMDB's search endpoint -- TMDB's `/search/movie` treats it as a strict server-side filter rather than a hint, and a guide provider's own release year routinely differs from TMDB's by a year, which silently zeroed out an otherwise-correct match and cached it as a permanent negative; year is now only used client-side to pick the best candidate from a title-only search

## 0.1.0-157 - Sun, 16 Aug 2026

- Strip an embedded year from a programme's title before querying TMDB -- some XMLTV feeds (e.g. SiliconDust's HDHomeRun cloud guide) bake the year straight into `<title>` (e.g. "Confessions of a Driving Instructor (1977)"), which routinely returned zero results from TMDB's search and got cached as a permanent no-match, silently breaking rating/director/backdrop lookups for those programmes

## 0.1.0-156 - Sun, 16 Aug 2026

- Redraw the live-channel `i` overlay once its TMDB backdrop arrives -- the very first automatic show right after a channel switch could never win the race against the backdrop's own background fetch, so it always fell back to the plain banner until a later manual `i` press picked up the by-then-cached backdrop and switched to the full-bleed hero

## 0.1.0-155 - Sun, 16 Aug 2026

- Detect movie-category live programmes via the channel's M3U group-title too, not just the EPG programme's own `<category>` -- fixes themed movie channels (e.g. Pluto TV's "70s Cinema", relayed through m3u4u) whose EPG feed only ever tags genre (Drama, Thriller, ...), never the word "movie", so they never got a TMDB rating, director, or backdrop hero despite being movie-only channels

## 0.1.0-154 - Sun, 16 Aug 2026

- Show the same TMDB backdrop hero for live movie-category programmes -- pressing `i` while a channel is airing a movie now shows the same full-bleed Netflix/Prime-style treatment VOD already got, once TMDB has backdrop art for it

## 0.1.0-153 - Sun, 16 Aug 2026

- Match Xtream/Stalker/M3U VOD titles against TMDB for backdrop art, extending the full-screen `i` hero overlay to those sources too -- falls back to the existing card layout whenever TMDB isn't configured or has no match, and never overwrites the source's own title/poster/rating/description

## 0.1.0-152 - Sun, 16 Aug 2026

- Let `ENTER` replay a selected watch-history entry -- previously it only closed the browser; also fixes a crash closing the history browser outside a channel/EPG session (Plex, VOD, local file, YouTube)
- Show a full-screen, Netflix/Prime-style hero backdrop behind the VOD "now playing" overlay (`i`) when TMDB has backdrop art for the title (local-file/YouTube VOD with `--tmdb-api-token`) -- the paused/playing video stays visible through it
- Extend that backdrop hero to Plex movies/episodes too, via Plex's own `art` field -- no TMDB lookup needed

## 0.1.0-151 - Sun, 16 Aug 2026

- Include episodes in the Plex release-year filter -- matched by their own air date, not their show's premiere year, since Plex doesn't populate a top-level year field for episodes the same way it does for movies/shows

## 0.1.0-150 - Sun, 16 Aug 2026

- Add a Plex release-year filter across all libraries -- `y` opens a digit-only prompt; confirming shows every movie/show across every library released that year, sorted alphabetically, via two server-wide requests rather than looping over every library section
- Bind `LEFT` as an alias for `ESC` throughout the Plex browser (back a level, close, cancel search/year input)
- Show resolution badges (1080p, 4K, SD, ...) in the Plex browser's movie/episode rows
- Show a classic yellow folder icon for Plex library rows with no thumbnail of their own
- Accept lowercase `j`/`k` (as well as `J`/`K`) for bookmark reordering

## 0.1.0-149 - Sun, 16 Aug 2026

- Preserve Plex browser navigation position across playback -- `l` used to always reopen at the library root; it now reopens right where you left off
- Show poster/cover art in the Plex browser, fetched via the same thumbnail pipeline a VOD poster or channel logo already uses, with a fallback to Plex's own `composite` (auto-generated collage) field for library rows with no thumb of their own
- Show content rating and Plex's own audience score alongside year/duration in the Plex browser's movie/show rows
- Add `K`/`J` to reorder bookmarks in the picker, saved immediately

## 0.1.0-148 - Sun, 16 Aug 2026

- Show a real video frame as a recording's history thumbnail -- grabbed via a short-lived, windowless mpv instance (`vo=image`), reusing libmpv rather than a standalone mpv/ffmpeg CLI binary that isn't guaranteed to exist on every platform tvdinner ships to. Disk-cached and generated lazily via the same `fetch_image`/`cached_image`/`prefetch_images` pipeline a VOD poster or channel logo already uses

## 0.1.0-147 - Sun, 16 Aug 2026

- Pin the bookmarks TUI's palette to fixed RGB instead of following the terminal's own color theme -- a Dracula-themed terminal (confirmed live: Ptyxis) remaps curses' "blue"/"yellow" slots to a lavender purple and pale yellow, nowhere near the intended navy/gold XTree Gold look. Falls back to the previous theme-following behavior on a terminal that can't redefine colors
- Update the bookmarks screenshot in the README/website for the new theme

## 0.1.0-146 - Sun, 16 Aug 2026

- Theme the bookmarks TUI after XTree Gold -- navy background, white/cyan text, and double-line box borders match a real XTree Gold 3.0 screenshot's actual palette (its own selection bar was cyan, not gold, despite the product name); the selection bar here deliberately uses gold instead as a nod to the name. Falls back to the previous monochrome, attribute-only look on a terminal without color support

## 0.1.0-145 - Sun, 16 Aug 2026

- Bundle tvdinner's own OAuth client for Google Drive backup -- `tvdinner gdrive-login` now works with no arguments, using a bundled Desktop-app OAuth client (safe to ship: for installed apps the client secret isn't actually confidential, per RFC 8252 and Google's own guidance), removing the per-user Google Cloud Console setup that was previously required. `--client-id`/`--client-secret` still let a user bring their own client instead

## 0.1.0-144 - Sun, 16 Aug 2026

- Add Google Drive backup/restore support -- `tvdinner gdrive-login`/`gdrive-logout` plus a `--gdrive` flag on `backup`/`restore`, so the existing config backup archive can be stored in and restored from Google Drive instead of only a local file. Uses a hand-rolled OAuth 2.0 PKCE flow and Drive v3 REST calls (drive.file scope only, no new dependency)

## 0.1.0-143 - Sun, 16 Aug 2026

- Filter parsed EPG data to the playlist's own channels -- a feed can be far larger than what one playlist actually uses (measured live: a 915MB/12,835-channel/1.1M-programme feed against a 1,510-channel playlist only ever needed under 5% of it). Repeat launches see the biggest win: the parsed-EPG disk cache drops accordingly (364MB -> 26MB for that feed), and a warm-cache load went from unpickling that huge structure down to ~0.2s / ~130MB peak RSS

## 0.1.0-142 - Sat, 15 Aug 2026

- Show the actual programme, not just the channel, in watch history -- a `channel` entry's title is now looked up from EPG data (what was airing when you tuned in), falling back to the channel's own name when there's no EPG match. A programme's own EPG poster/year/director, when the feed tags them, now populate the history browser too, not just VOD entries

## 0.1.0-141 - Sat, 15 Aug 2026

- Add a watch history browser (`x` keybinding) -- lists `history.jsonl` entries newest first, grouped by day, each row showing a thumbnail (a VOD's poster, a channel's logo, or a placeholder), duration, and for movies year/rating/director when available. Read-only for now
- `history.jsonl` entries now capture `image_url`/`year`/`rating`/`director` for movies (older entries without these fields still load fine)
- Fix the guide filter's close handler not restoring the `b` (last channel) hotkey, silently dropping it for the rest of the session after filtering the guide once

## 0.1.0-140 - Sat, 15 Aug 2026

- Treat a bare `tvdinner` (no arguments at all) the same as `tvdinner bookmarks`, instead of argparse's "the following arguments are required: url" error
- Add a `b` ("back") hotkey to switch to the last watched channel -- repeated presses toggle back and forth between the two most recently watched channels

## 0.1.0-139 - Sat, 15 Aug 2026

- Add watch history logging -- every live channel, VOD item, or recording actually watched, with when and for how long, appended to `~/.config/tvdinner/history.jsonl`. Nothing reads this back yet; it's captured for possible future use. New `--history-file`/`--no-history` flags, included in `tvdinner hard-reset`'s deletion list (not backup/restore, matching playback positions/schedule's precedent)
- Add watch history's size/location to `tvdinner stats` output, alongside a new `--history-file` override

## 0.1.0-138 - Sat, 15 Aug 2026

- Fix hwdec CUDA/VDPAU probe errors (`Cannot load libcuda.so.1`, `Failed to open VDPAU backend...`) still leaking to the terminal on a channel switch, even after the previous release's fix -- that fix only ever redirected stderr once, around the very first file, on the assumption that ffmpeg caches a failed hwdec probe for the rest of the process; confirmed live that's false, since a plain channel switch re-triggered the exact same raw probe lines long after the first file's redirect had already been restored. Now re-arms the redirect on every file load (via mpv's `start-file` event), not just the first

## 0.1.0-137 - Sat, 15 Aug 2026

- Fix hwdec CUDA/VDPAU probe errors (`Cannot load libcuda.so.1`, `Failed to open VDPAU backend...`) printing to the terminal on machines without the proprietary NVIDIA stack -- the stderr redirect that hides them was restored on a fixed 3s timer from `Player()` construction rather than from when mpv actually starts decoding, so a live stream slow to start (e.g. competing for bandwidth with a large simultaneous EPG download) could still be mid-connect once the timer fired, letting the probe's raw fprintf lines through anyway. Now tied to the first `file-loaded` event (plus a short buffer), with a 20s fallback ceiling for a stream that never loads at all

## 0.1.0-136 - Sat, 15 Aug 2026

- List subcommands in `tvdinner --help` -- they aren't real argparse subparsers (the dispatch on `sys.argv[1]` runs ahead of `build_parser().parse_args()` so plain `tvdinner URL` can stay the default form without naming a subcommand), so argparse never listed them on its own; adds a "commands:" epilog instead

## 0.1.0-135 - Sat, 15 Aug 2026

- Add `tvdinner store-tmdb TOKEN`/`tvdinner clear-tmdb` for a global default TMDB token, used as a fallback whenever `--tmdb-api-token` isn't given directly (including via a bookmark's own saved token, which still always overrides the stored default). Wired into `tvdinner backup`/`restore`/`hard-reset` alongside `bookmarks.json`

## 0.1.0-134 - Fri, 14 Aug 2026

- Stop gating YouTube's TMDB lookup on the title carrying a year -- a real official-studio upload's title can have no year in it at all, which used to skip TMDB entirely despite the existing candidate-splitting logic being able to isolate the real movie name regardless. Now matches the local-file branch, which never gated on year presence to begin with

## 0.1.0-133 - Fri, 14 Aug 2026

- Fix director staying missing forever on a pre-existing TMDB metadata cache entry -- a cache entry written before director support existed had no `director` key at all, and was silently defaulting to `None` indistinguishable from a genuine "TMDB has no director" negative, for the rest of that entry's 30-day TTL. A cached positive match missing the key now triggers one re-fetch instead

## 0.1.0-132 - Fri, 14 Aug 2026

- Show director on the compact EPG banner overlay too -- same preference order as the guide details popup and VOD overlay (the feed's own `<credits>` first, TMDB as a fallback), rendered as a single ellipsized line to keep this banner glanceable

## 0.1.0-131 - Fri, 14 Aug 2026

- Read director from the EPG feed's own `<credits>` before falling back to TMDB -- some XMLTV feeds already tag a programme's director themselves (free, instant, and exactly matched, unlike TMDB's fuzzy title/year search); the TMDB lookup is now only attempted when a feed doesn't provide one

## 0.1.0-130 - Fri, 14 Aug 2026

- Fix bookmark text fields (Description, URL, EPG, Channel, TMDB token) silently dropping non-ASCII input -- the curses editor read one raw byte at a time, so a multi-byte UTF-8 character (e.g. a playlist's own decorative channel badge) never made it in no matter how it was typed or pasted
- Show movie director in the guide details popup and VOD overlay, when available -- sourced from Plex's own metadata, or a new TMDB `/movie/{id}/credits` lookup for a local file/YouTube video
- Add a small "HD" badge to the guide grid's channel logos, using the same `Channel.is_hd` match already driving the HD-first guide sort

## 0.1.0-129 - Fri, 14 Aug 2026

- Fix hwdec-probe stderr suppression swallowing our own progress prints -- redirecting fd 2 wholesale (see 0.1.0-128) also silently ate cli.py's own "Loading EPG data..." messages when they landed inside the same startup window. `sys.stderr` now gets a fresh duplicate fd that stays connected to the real terminal for the duration; only the raw, numbered fd 2 (what native code's fprintf targets directly) is redirected

## 0.1.0-128 - Fri, 14 Aug 2026

- Suppress raw CUDA/VDPAU hwdec-probe noise from the terminal -- on a machine without the proprietary NVIDIA stack, mpv's `hwdec=auto-safe` probing triggers two dlopen wrappers that print straight to the real stderr fd, bypassing mpv's own logging entirely. Redirected to `/dev/null` for a few seconds at startup rather than the terminal; log file output is unaffected

## 0.1.0-127 - Thu, 13 Aug 2026

- Add `--glsl-shader` (repeatable) for custom mpv shaders (Anime4K, FSRCNNX, etc.), layered on top of the always-on `gpu-hq` scaling profile
- Add `--interpolation` for motion-smoothed playback (mpv's `interpolation` + `video-sync=display-resample`); off by default since it only helps on displays whose refresh rate is a clean multiple of the video's frame rate
- Website: add a feature card reflecting hardware decoding/scaling support

## 0.1.0-126 - Thu, 13 Aug 2026

- Enable hardware decoding (`hwdec=auto-safe`) and mpv's high-quality scaling profile (`profile=gpu-hq`) -- libmpv defaults to software-only decoding and a fast/basic scaler, and unlike the standalone mpv binary never auto-loads the user's own mpv.conf, so neither setting had anywhere else to come from

## 0.1.0-125 - Thu, 13 Aug 2026

- Remove the "Stretch" aspect ratio option -- it set mpv's keepaspect=no on top of video-aspect-override=no, which is indistinguishable from Auto in practice since the window is always sized to the video's native aspect to begin with

## 0.1.0-124 - Thu, 13 Aug 2026

- Cap the log file at 5MB with one rotated backup -- it previously appended forever with nothing to remove or truncate old lines. Switches to RotatingFileHandler (5MB cap, one backup, `tvdinner.log.1`). `tvdinner stats` and `tvdinner hard-reset` now account for the rotated backup too, not just the live file

## 0.1.0-123 - Thu, 13 Aug 2026

- Default subtitle track selection to English -- toggling subtitles on picked whichever track mpv's track_list listed first, which for a YouTube video is alphabetical by language code (so Arabic sorted ahead of English); now prefers an en/eng-tagged track, falling back to the actual first track if none is tagged. Also documents mpv's existing default j/J cycle-sub-track keys in the help cheat sheet, since tvdinner never overrides them

## 0.1.0-122 - Thu, 13 Aug 2026

- Include the log file's path and size in `tvdinner stats` output -- rounds out the on-disk usage picture alongside the EPG/TMDB/image caches already reported

## 0.1.0-121 - Thu, 13 Aug 2026

- Add `tvdinner hard-reset` to delete all stored data -- deletes every file/directory tvdinner itself writes (bookmarks, favorites, EPG shifts, scheduled recordings, playback positions, update-check state, the EPG/TMDB/image caches, and the log file), reverting to exactly the state a fresh install would be in. Prompts for confirmation (listing every path first) unless `-y`/`--yes` is given. Deliberately never touches `--record-dir`: a recording is real media the user made, not disposable app state

## 0.1.0-120 - Thu, 13 Aug 2026

- Add `tvdinner stats` for on-disk cache usage, per bookmarked feed -- one table row per bookmark's EPG cache size, for whichever bookmarks have a deterministically knowable EPG source without a network fetch (an explicit saved `--epg` URL, or an Xtream login's own `xmltv.php` export). A bookmark relying on M3U auto-discovery or with no EPG at all is listed as unknown rather than guessed. A second table covers the caches every feed shares regardless of source: TMDB ratings/metadata, channel logos/poster art, and iptv-org's online logo database. No network calls; purely reads what's already on disk

## 0.1.0-119 - Wed, 12 Aug 2026

- Auto-refresh the guide once background channel-logo fetches land -- `prefetch_channel_logos` spawned background fetches but nothing ever triggered a re-render once they completed, so a freshly-opened, untouched guide stayed on placeholder avatars indefinitely, even once every logo had long since resolved, until some unrelated later render (paging, a channel switch, ...) happened to pick them up. `prefetch_channel_logos` gains an `on_resolved` callback, called once per channel after its fetch completes; the guide wires this to a debounced (0.3s) re-render, guarded against firing after the guide's been closed or covered by the details popup, and cancelled on close/shutdown

## 0.1.0-118 - Wed, 12 Aug 2026

- Add an on-disk cache for channel logos and poster art -- `fetch_image` only ever cached in memory, so every fresh tvdinner launch re-fetched every channel logo (and programme/VOD poster) over the network from scratch, confirmed live to noticeably slow guide population on a large playlist (1000+ channels), even though the recent background-prefetch work already fixed the within-session cost. Now also caches successfully-fetched remote images to disk for 30 days, mirroring the pattern already used for EPG XML and TMDB ratings. A failed fetch is never disk-cached, and a corrupt cache entry falls through to a real re-fetch. Local `file://` sources are untouched -- already a fast local read

## 0.1.0-117 - Wed, 12 Aug 2026

- Render TMDB-sourced VOD ratings with the gold star and attribution logo -- `render_vod_info_overlay`'s rating was plain text ("7.4"), unlike the guide's programme-details popup which shows a gold "★ 7.6" plus the TMDB attribution logo their API terms require. Now matches that styling -- but `VodItem.rating` comes from three different places (TMDB, via `cli.py`'s local-file/YouTube branches; Plex's own `audienceRating`; an Xtream panel's own `rating` field), and only the TMDB one may legitimately carry their logo. Adds `VodItem.rating_is_tmdb`, set only where the rating genuinely came from `tmdb.fetch_movie_metadata_cached`, so a Plex/Xtream rating still gets the gold star (for visual consistency) but never a misattributed logo

## 0.1.0-116 - Wed, 12 Aug 2026

- Fix leading-year local filenames, and share title-guessing with YouTube -- a filename like "1940 - His Girl Friday - Cary Grant and Rosalind Russell - Ex-lovers become headline hunters [wEx-z1TYPKU].webm" (a yt-dlp download's default naming) hit the same "empty text before the year falls back to the whole original string" bug already fixed for YouTube titles, and even once fixed, the local-file TMDB lookup never tried splitting off the chained cast/tagline noise the way the YouTube branch does. Extracts the shared logic into `movietitle.py`: `guess_title_year` now prefers text before the year but falls back to text after when nothing precedes it, and `title_search_candidates` is now used by both the local-file and YouTube TMDB lookups. Verified live against the real TMDB API with the exact filename above -- now resolves to His Girl Friday (1940) correctly

## 0.1.0-115 - Wed, 12 Aug 2026

- Try split-title candidates for a YouTube video's TMDB lookup -- naively searching TMDB with a video's whole year-stripped title finds nothing when that title chains cast names/tagline text onto the real movie name (confirmed live against a real upload, "1940 - His Girl Friday - Cary Grant and Rosalind Russell - Ex-lovers become headline hunters", that a plain search on the full remainder came up empty). `youtube.title_search_candidates` now tries the title's first ` - `/`|` segment first (usually just the movie name), falling back to the whole remainder for a movie whose real title happens to contain one of those separators

## 0.1.0-114 - Wed, 12 Aug 2026

- Add `i` overlay support for YouTube URLs, with optional TMDB metadata -- mpv already plays a plain `youtube.com`/`youtu.be` URL directly via its built-in yt-dlp hook; this reuses the VOD-session machinery built for local files so it also gets the `i` overlay. Title/uploader/thumbnail come free from YouTube's own public oEmbed endpoint, always tried; `--tmdb-api-token` additionally tries a TMDB lookup on that title, but only when it carries a year (or `--title`/`--year` force it), since an arbitrary YouTube title usually isn't a movie at all and a titleless search risks a wrong match. `--title`/`--year` now apply to YouTube playback too, not just local files

## 0.1.0-113 - Tue, 11 Aug 2026

- Replace `tvdinner mpv PATH` with plain local-file detection on the main command -- a local video file no longer needs its own subcommand: `tvdinner PATH` now tells it apart from a real M3U playlist by sniffing its first few KB for `#EXTM3U` rather than requiring `mpv` up front, and reuses the main command's own `--tmdb-api-token`/`--record-dir`/`--playback-positions-file`/etc. instead of a separate mini-parser. Adds `--title`/`--year` to the main options for overriding a bad filename guess

## 0.1.0-112 - Tue, 11 Aug 2026

- Add `tvdinner mpv PATH` to play a local video file directly, with no playlist/EPG/channel involved -- its movie identity is guessed from the filename (`Title (Year)`/scene-release conventions), and if `--tmdb-api-token` is given, looked up on TMDB in the background so the `i` overlay shows the same poster/synopsis/rating any other VOD source gets. Resume-from-position and `r`-key recording work the same as anywhere else

## 0.1.0-111 - Mon, 03 Aug 2026

- Background channel-logo fetching, and a "Loading guide..." message -- opening the program guide for the first time in a session (or scrolling to reveal channels never shown before) could take several real seconds, since `render_program_guide` resolved each visible row's logo synchronously (a real network round trip per candidate URL, or up to a 10s timeout for a dead/hotlink-blocked one). Measured live against a real 376-channel playlist: 1.75s for an 8-row guide on a cold cache. Logo resolution is now backgrounded the same way TMDB ratings already are (`prefetch_channel_logos`/`cached_channel_logo`, mirroring `tmdb.py`'s `prefetch_ratings`/`cached_rating`), dropping measured render time to ~180ms regardless of cache state. Also shows a "Loading guide..." OSD message the instant `g` is pressed, for whatever render time remains (large EPG feeds still cost real time to filter/lay out)

## 0.1.0-110 - Mon, 03 Aug 2026

- Strip leading "S1 E1" episode markers from EPG descriptions -- some XMLTV feeds prefix the `<desc>` text with a redundant season/episode marker; that info is already available structurally, so it's now dropped before display

## 0.1.0-109 - Sun, 02 Aug 2026

- Render TMDB's own logo instead of plain "TMDB" text for attribution -- bundles TMDB's official attribution wordmark (from their public logos-and-attribution page) as package-data PNG, replacing the plain "TMDB" text previously drawn next to every rating badge in the guide grid, programme-details popup, and channel-switch banner

## 0.1.0-108 - Sun, 02 Aug 2026

- Store a per-bookmark TMDB API token, editable but never shown in the table -- `Bookmark` gains an optional `tmdb_api_token` field, editable via a new form field in the add/edit TUI. The table itself never shows the token, only a `[x]`/`[ ]` presence indicator next to the existing EPG-refresh checkbox. Launching a bookmark that has one set now passes `--tmdb-api-token` through automatically, fully masked (not just partially redacted like the Xtream/Stalker/Plex credentials embedded in a bookmark's URL) in the launch log line

## 0.1.0-107 - Sun, 02 Aug 2026

- Show category and TMDB rating on the channel-switch EPG banner too -- it previously had neither; category and rating only showed once the guide was opened or `i` pressed for full details. Mirrors the programme-details popup's layout: rating + TMDB attribution right-aligned on the time-range line, category as its own accent-colored line below. Also fetches the current programme's rating in the background on channel switch, and fixes a latent overflow risk where a long joined category string could run past a popup's fixed width undrawn-truncated

## 0.1.0-106 - Sun, 02 Aug 2026

- Version-tag the parsed-EPG pickle cache to prevent stale post-upgrade data -- `_load_cached_parsed_epg` trusted a pickled `Epg` from a previous run as long as the raw XML cache was still fresh, with no check that the tvdinner version which wrote it still matches. A parsing-logic fix (e.g. the just-shipped `<category>` join fix) changes what a fresh parse produces without changing `Epg`/`Programme`'s fields at all, so the pickle-compat check never caught it -- confirmed live that upgrading past the category fix kept silently serving the old single-category `Programme` objects for the rest of the `--epg-cache-hours` window, making the fix look like it hadn't taken effect. Now pickles `(version, epg)` instead of a bare `Epg`, and treats a version mismatch the same as a corrupt pickle: discard and re-parse

## 0.1.0-105 - Sun, 02 Aug 2026

- Fix the XMLTV parser dropping all but the first `<category>` tag on a programme -- XMLTV allows several (a genre plus "Movie", commonly), but `elem.find("category")` only ever kept the first. For feeds that list the specific genre before "Movie" (confirmed live against epg.best's TCM feed: "Crime drama" then "Movie"), this silently broke `--tmdb-api-token`'s only signal for detecting a movie programme -- ratings never fetched, no matter how correct the token was. Now joins every `<category>` tag instead of keeping just the first. Also adds README/website documentation for `--tmdb-api-token`, which shipped undocumented in 0.1.0-102

## 0.1.0-104 - Sun, 02 Aug 2026

- Fix custom keybindings (`?`, `g`, etc.) silently doing nothing on Windows until the user manually clicked into the mpv window -- mpv's window never received OS keyboard focus on open there (Windows, unlike most Linux window managers under X11, doesn't auto-focus a newly created window for a background process), so the console tvdinner was launched from kept it. Now grabs foreground focus once, on the first file-loaded event, via the standard Alt-keypress-then-`SetForegroundWindow` workaround

## 0.1.0-103 - Sun, 02 Aug 2026

- Fix Windows build crashing on any full-screen overlay (guide, Plex browser, recordings/schedule browser, help sheet) -- the Windows PyInstaller spec bundled the font files but never `src/tvdinner/images/logo-mark.png`, so `_app_logo()` raised `FileNotFoundError` as soon as an overlay tried to draw its header logo

## 0.1.0-102 - Sun, 02 Aug 2026

- Add TMDB-sourced star ratings for movies in the EPG guide grid and details popup -- opt-in via `--tmdb-api-token`. Movie programmes are matched by category and looked up by title/year against TMDB's search API; ratings are fetched in background threads (never blocking guide rendering) and cached on disk and in memory, then shown as a gold star badge with the `TMDB` attribution mark their API terms require

## 0.1.0-101 - Sun, 02 Aug 2026

- Remove AirPlay casting support entirely -- deletes `airplay.py` and its test file, and removes every AirPlay integration point from `cli.py` (the `j` key binding, all picker/pairing-flow state and closures, shutdown cleanup). Chromecast itself is untouched and fully functional. Also removes the `airplay` extra from `pyproject.toml`, and AirPlay mentions from packaging files, the README, and the website

## 0.1.0-100 - Sun, 02 Aug 2026

- Cache guide-row logo tiles and fonts, fixing slow guide navigation -- at real playlist scale (1500+ channels), every arrow-key press re-rendered from scratch and cost 800ms-1400ms+, even for rows already scrolled past moments earlier, confirmed live against a real 1581-channel playlist and a 525MB/12,830-channel/1.1M-programme EPG feed. `_logo_tile()` and `_font()` are now both cached instead of recomputing/reloading from scratch on every render; steady-state render time dropped from ~861ms to ~350ms

## 0.1.0-99 - Sun, 02 Aug 2026

- Fix duplicated "(YEAR)" in EPG titles for feeds that already embed it -- `_title_with_year()` appended "(YEAR)" (from XMLTV's `<date>` element) unconditionally, but some feeds already bake the year into `<title>` itself for movies, confirmed live via a user report: "70s Cinema"'s 10:30 slot showed "The Taking of Pelham One Two Three (1974) (1974)". Now skips appending if the title already ends with that exact year

## 0.1.0-98 - Sun, 02 Aug 2026

- Remove macOS support entirely -- packaging never reached a working state despite three separate fix attempts (a run-loop-pump theory, forcing an invalid `--gpu-context` value, and correctly bundling MoltenVK's Vulkan ICD), the last of which fixed the original vo-init failure but surfaced a genuine three-way deadlock between mpv's own `force_window` vo creation and `python-mpv`'s synchronous property-setting calls, both needing the same main thread. Removes the `macos/` packaging directory, every darwin-specific code branch, the `build-macos` CI job, the README's macOS sections, and the website's macOS install card

## 0.1.0-97 - Sat, 01 Aug 2026

- Surface mpv's own error/log detail on playback failure, not just "reconnecting" -- a stream that stalls or fails deep inside mpv/ffmpeg (dead server, HTTP error, TLS/DNS failure, stalled read) previously only showed up in the log as an opaque "Playback error ... reconnecting" line, indistinguishable from the app just hanging. `Player` now forwards mpv's internal log (network/demuxer/ffmpeg messages) into our logger, `on_playback_error` logs mpv's own human-readable error reason, and playback-started is now logged unconditionally rather than only on the reconnect path

## 0.1.0-96 - Sat, 01 Aug 2026

- Fix macOS builds crashing on launch on real Sequoia hardware -- confirmed via a user's crash report ("Symbol not found: `_swift_coroFrameAlloc` ... built for macOS 26.0 which is newer than running OS"). `release.yml`'s macOS build now runs on `macos-15` specifically (not `macos-latest`, whose underlying OS version moves over time), so Homebrew's bundled mpv links against symbols that actually exist on the macOS version being targeted. Corrects the claimed minimum macOS version to 15 (Sequoia) to match

## 0.1.0-95 - Sat, 01 Aug 2026

- Fix macOS packaging -- the released `.app` was missing libmpv's own dependencies (ffmpeg, libass, and ~45 more), so it likely never actually played anything on any real Mac; now properly self-contained. Also adds a separate Intel `.dmg` alongside the existing Apple Silicon one (two native downloads, not a universal binary), and corrects the Gatekeeper unlock instructions for macOS Sequoia and later, which removed the old right-click -> Open bypass

## 0.1.0-94 - Sat, 01 Aug 2026

- Add AirPlay casting support, the deferred follow-up to Chromecast -- press `j` to cast whatever's playing to an AirPlay device on your LAN, with a one-time PIN-pairing prompt the first time (credentials cached after that). Confirmed live that discovery/pairing/connecting work, but playback compatibility with non-Apple AirPlay 2 receivers (e.g. some smart TVs) may vary due to a pyatv/receiver protocol gap -- see README for details

## 0.1.0-93 - Sat, 01 Aug 2026

- Check GitHub Releases for a newer version at startup (at most once every 24 hours) and show an on-screen notice -- `y` opens the release page in your browser, `n`/ESC dismisses. No silent self-update on any platform; `--no-update-check` disables checking

## 0.1.0-92 - Sat, 01 Aug 2026

- Show EPG loading progress on the player's own on-screen OSD, not just the terminal -- `Loading EPG data...` and periodic progress updates now appear over the video too, so it doesn't look like nothing's happening for anyone not watching the terminal while a large feed is still loading

## 0.1.0-91 - Sat, 01 Aug 2026

- Fix a permanent silent playback hang on HLS streams -- the ffmpeg-level `reconnect_at_eof` option (added for automatic reconnect) treated a segment finishing normally as a network error, causing mpv to hang forever with no window and no error on the large majority of real-world IPTV streams, which are delivered as HLS

## 0.1.0-90 - Sat, 01 Aug 2026

- Fix a crash on a second Ctrl-C during shutdown -- an interrupt landing mid-cleanup (e.g. while mpv is still closing) used to propagate as an unhandled traceback instead of exiting cleanly

## 0.1.0-89 - Sat, 01 Aug 2026

- Fix M3U playlist loading making two full requests instead of one -- could double load time (or make a slow redirect chain look like a hung terminal) since both requests independently paid for resolving the same redirects
- Report download progress for large EPG feeds (`Loading EPG data... (N MB downloaded)`) instead of downloading silently with no feedback until it finishes or fails
- Add Chromecast casting to the website's feature list (README already documented it)

## 0.1.0-88 - Fri, 31 Jul 2026

- Add Chromecast casting support: `k` opens a device picker (mDNS discovery, no pairing) for whatever's currently playing -- live channel, VOD, or Plex item -- and casts the stream URL directly to the selected device. Local playback pauses for the duration and resumes on disconnect (a row in the same picker). pychromecast is an optional extra (Python 3.11+), not a core dependency -- the app works identically without it installed

## 0.1.0-87 - Fri, 31 Jul 2026

- Fix `hdhomerun://` URLs discarding any path component -- broke against tuner-emulating servers (e.g. Dispatcharr) that namespace their HDHomeRun-compatible API under a sub-path instead of serving it at the root the way real hardware does
- Add a now-playing info overlay (`i` key) for VOD/Plex movies and episodes: poster, synopsis, rating, and playback progress, pulled from Plex's own metadata where available
- Document Plex support across `CLAUDE.md`, the README, and the website

## 0.1.0-86 - Fri, 31 Jul 2026

- Add Plex Media Server support: `plex://host:port?X-Plex-Token=...` (or `plexs://` for https), usable on the command line and via bookmarks. Browse libraries -> movies/shows -> seasons -> episodes with a new TUI overlay (`l` to open/reopen), search the whole server with `/`, and play directly (no transcode negotiation). Resume-on-reopen and reconnect-on-drop work for Plex playback too, reusing the existing VOD item machinery

## 0.1.0-85 - Fri, 31 Jul 2026

- Automatically retry a dropped live-channel or VOD stream with backoff (2s/5s/10s/20s/30s, 5 attempts) instead of immediately showing "Failed to play" and giving up; 30 seconds of stable playback after a reconnect resets the backoff. Also caps ffmpeg's own network-level reconnect delay and bounds mpv's network timeout, so a genuinely dead server surfaces and starts retrying promptly instead of stalling silently
- Fix launching tvdinner directly against a large non-playlist URL (e.g. a movie file) hanging indefinitely -- it was downloading and decoding the entire file as text just to determine it wasn't an M3U playlist before falling back to direct-stream playback

## 0.1.0-84 - Fri, 31 Jul 2026

- Replace the bundled DejaVu Sans font with Inter for a more modern look across the guide, overlays, and about screen. Inter also has real glyphs for the decorative circled-letter badges some IPTV playlists append to channel names, which used to need stripping. Tradeoff: unlike DejaVu, Inter has no Arabic, Hebrew, Georgian, or Armenian glyphs, so channel/programme names in those scripts won't render

## 0.1.0-83 - Fri, 31 Jul 2026

- Make EPG cache writes atomic -- quitting tvdinner while the background EPG-loading thread was still writing the cache (more likely with a very large feed) could truncate it mid-write, corrupting it for the next run ("Discarding unreadable parsed-EPG cache ... Ran out of input"). Cache writes now go to a temp file first, renamed into place only once complete

## 0.1.0-82 - Fri, 31 Jul 2026

- Fix a broken tvg-logo blocking every other logo fallback -- a playlist's own tvg-logo (commonly pointing at imgur, which widely rejects hotlinked requests right now) "won" over the EPG-icon and online-index fallbacks just for having a non-empty URL string, even when that URL didn't actually work. Each source is now tried in order until one actually fetches and decodes

## 0.1.0-81 - Fri, 31 Jul 2026

- Fix a large share of online-fallback channel logos (0.1.0-80) showing imgur's "Content not viewable in your region" placeholder instead of the real logo -- imgur, the largest host in the community logo database, geo-blocks a lot of hotlinked traffic with a real HTTP 200 and the same placeholder image every time, so there was no error to catch it on until now (the response is hashed and that one known placeholder rejected, falling back to the normal avatar instead)

## 0.1.0-80 - Fri, 31 Jul 2026

- Fall back to iptv-org's community channel/logo database for channels with no logo of their own or in their EPG -- common for bare M3U playlists. Matched exactly (tvg_id, then name/alt_name), never a fuzzy guess; on by default, `--no-online-logos` to opt out. Also fixes image fetches (logos and posters generally) getting a 403 from Wikipedia-hosted images due to a missing User-Agent

## 0.1.0-79 - Fri, 31 Jul 2026

- Add an `a` keybinding for an about overlay: logo, app name, version, and a one-line summary of what tvdinner does, styled to match the rest of the app rather than the help sheet's dense list

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
