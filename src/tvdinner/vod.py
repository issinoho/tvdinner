"""Video-on-demand (movies) support.

A VodItem is deliberately not a Channel: it has no scheduled airing, no EPG
programme, and no place in the time-grid guide (tvdinner.overlay's
render_program_guide) -- it belongs in its own browsing list instead (see
render_vod_browser). Sources: an M3U playlist's group-tagged entries
(split_m3u_vod_items below), Xtream's VOD API (tvdinner.xtream.load_xtream_vod),
and a Stalker portal's VOD API (tvdinner.stalker.load_stalker_vod).
"""

from __future__ import annotations

from dataclasses import dataclass

from tvdinner.m3u import Channel, Playlist


@dataclass
class VodChapter:
    start_seconds: float
    title: str | None = None


@dataclass
class VodItem:
    title: str
    url: str
    group_title: str | None = None
    poster_url: str | None = None
    year: str | None = None
    rating: str | None = None
    description: str | None = None  # a synopsis/plot summary, when the source provides one (currently only Plex does)
    director: str | None = None  # when the source provides one -- Plex's own metadata, or TMDB for a local file/YouTube video
    # Wide hero/backdrop art for the full-screen 'i' info overlay (see
    # overlay.render_vod_info_overlay) -- set from tmdb.MovieMetadata.
    # backdrop_url (cli.py's local-file/YouTube branches) or Plex's own
    # `art` field (plex.resolve_plex_playable); Xtream/Stalker/a bare
    # M3U --vod-group entry supply no such field and leave this unset,
    # getting that overlay's plain card-with-poster layout instead.
    backdrop_url: str | None = None
    # Title-treatment logo composited in the hero's top-right corner (see
    # overlay.render_vod_info_overlay's hero path) -- set from
    # tmdb.MovieMetadata.logo_url, either directly (cli.py's local-file/
    # YouTube branches) or via cli.py's background TMDB-enrichment thread
    # (_enrich_vod_hero_art_in_background), which also covers Plex/
    # Xtream/Stalker/M3U items since none of those sources ever supply a
    # title logo of their own (unlike backdrop_url, which Plex does
    # supply itself).
    logo_url: str | None = None
    # Whether `rating` came from TMDB specifically (cli.py's local-file/
    # YouTube branches, via tmdb.fetch_movie_metadata_cached) as opposed
    # to a source's own native rating (Plex's audienceRating, an Xtream
    # panel's own `rating` field) -- render_vod_info_overlay only draws
    # the TMDB attribution logo TMDB's API terms require when this is
    # True, so a non-TMDB rating is never misattributed to them.
    rating_is_tmdb: bool = False
    # Plex's own playback position for this item, in seconds, when Plex
    # reports it as in-progress (plex.resolve_plex_playable, from the
    # same `viewOffset` field render_plex_browser's watched/in-progress
    # badge is driven by -- see plex._leaf_watch_status). cli.py's
    # select_plex_node only ever falls back to this when its own
    # playback_positions store has no entry for this item's URL yet --
    # e.g. progress made watching in Plex's own apps -- never overriding
    # a resume position tvdinner already knows about itself. Every other
    # source leaves this unset; there's nowhere else to source it from.
    resume_seconds: float | None = None
    # Plex's own ratingKey for this item -- only ever set by
    # plex.resolve_plex_playable, since it's what plex.report_plex_timeline
    # needs to tell Plex's own session/timeline API which item this is.
    # cli.py uses this field's presence (rather than tracking "is this a
    # Plex session" separately) to decide whether a given VodItem is one
    # it should report playback state for at all -- every other source
    # leaves it unset, so nothing else is ever mistakenly reported.
    rating_key: str | None = None
    # The show's own name (Plex's `grandparentTitle`) for a TV episode --
    # only ever set by plex.resolve_plex_playable, and only for an
    # episode, never a movie. `title` above is the *episode's* own title
    # for one of these, which is useless for a title-logo lookup; cli.py's
    # _enrich_vod_hero_art_in_background uses this field's presence to
    # search TMDB's /search/tv (tmdb.fetch_tv_logo_cached) instead of
    # /search/movie. Every other source leaves this unset.
    series_title: str | None = None
    # Chapter markers, start-of-file first -- only ever set by
    # plex.resolve_plex_playable, from Plex's own `Chapter` metadata array
    # (present when the source file has real embedded chapters, e.g. a
    # Blu-ray/DVD rip; absent for most streamed/transcoded content). Every
    # other source leaves this unset -- Xtream/Stalker/a bare M3U
    # --vod-group entry have no chapter concept of their own, and a local
    # file/YouTube video's TMDB enrichment doesn't supply one either.
    chapters: list[VodChapter] | None = None


def split_m3u_vod_items(playlist: Playlist, vod_groups: set[str]) -> tuple[list[VodItem], list[Channel]]:
    """Pull channels whose group-title matches one of `vod_groups`
    (case-insensitive exact match against Channel.groups) out of an M3U
    playlist and return them as VodItems, leaving the rest as ordinary
    channels. If `vod_groups` is empty this is a no-op -- `([], playlist.channels)`
    -- preserving today's exact M3U behavior unless the user opts a group in
    via --vod-group; we don't guess which groups are "movies" from group
    names, since playlist group-title conventions vary too much to infer
    reliably (see tvg_id: not a safe thing to infer structure from)."""
    if not vod_groups:
        return [], playlist.channels

    needles = {g.lower() for g in vod_groups}
    vod_items: list[VodItem] = []
    channels: list[Channel] = []
    for channel in playlist.channels:
        if any(g.lower() in needles for g in channel.groups):
            vod_items.append(
                VodItem(
                    title=channel.name,
                    url=channel.url,
                    group_title=channel.group_title,
                    poster_url=channel.tvg_logo,
                )
            )
        else:
            channels.append(channel)
    return vod_items, channels
