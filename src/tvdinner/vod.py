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
class VodItem:
    title: str
    url: str
    group_title: str | None = None
    poster_url: str | None = None
    year: str | None = None
    rating: str | None = None
    description: str | None = None  # a synopsis/plot summary, when the source provides one (currently only Plex does)
    director: str | None = None  # when the source provides one -- Plex's own metadata, or TMDB for a local file/YouTube video
    # Whether `rating` came from TMDB specifically (cli.py's local-file/
    # YouTube branches, via tmdb.fetch_movie_metadata_cached) as opposed
    # to a source's own native rating (Plex's audienceRating, an Xtream
    # panel's own `rating` field) -- render_vod_info_overlay only draws
    # the TMDB attribution logo TMDB's API terms require when this is
    # True, so a non-TMDB rating is never misattributed to them.
    rating_is_tmdb: bool = False


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
