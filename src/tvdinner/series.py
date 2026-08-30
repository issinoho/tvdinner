"""TV series (show -> season -> episode) browsing tree.

Xtream Codes exposes a Series API alongside its VOD (movies) API -- a
completely separate category for TV shows with a season/episode
structure, not covered by tvdinner.xtream.load_xtream_vod (movies-only).
Unlike VOD, a series library can be large and its full episode tree
expensive to fetch eagerly, so it's browsed lazily instead:
tvdinner.xtream.list_xtream_series_children lists one level at a time
(mirroring tvdinner.plex.list_plex_node_children), and
tvdinner.xtream.resolve_xtream_series_episode resolves a leaf episode
node into a playable VodItem only once the user actually selects it
(mirroring tvdinner.plex.resolve_plex_playable).

This node type is deliberately source-agnostic so a second source can
supply its own listing/resolve functions without the model or cli.py's
Series browser changing: Stalker Portal has a matching 'series' item
type, kept on the series-stalker-wip branch until it can be verified
against a real portal. cli.py's Series browser drives whatever source is
present through this one shared node type, never branching on which it
is.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SeriesNode:
    """One row in a TV series browsing tree -- a category, series,
    season, or episode. Mirrors plex.PlexNode's container/leaf role, but
    is designed to be shared between independent sources rather than
    owned by one: each source module supplies its own listing/resolve
    functions producing and consuming this same type (only
    tvdinner.xtream does today; see the module docstring).

    `id` is whatever the owning source needs to fetch this node's
    children or resolve it to a playable file -- a category id, a series
    id, a "<series_id>:<season_number>" composite for a season, or the
    raw episode id (Xtream) for an episode. Never parsed by anything
    outside the source module that produced it."""

    id: str
    title: str
    kind: str  # "category" | "series" | "season" | "episode"
    poster_url: str | None = None
    # Pre-formatted at fetch time (same principle plex.PlexNode.subtitle
    # already uses), since the source module knows what it just fetched
    # and the browser/render code shouldn't need to re-derive it: e.g.
    # "24 series" (category row), "3 seasons" (series row), "Season 2 ·
    # 10 episodes" (season row), "S02E04 · <episode title>" (episode row).
    subtitle: str | None = None
    year: str | None = None
    rating: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    series_title: str | None = None  # episode/season nodes only -- the show's own title
    # An episode's playable stream URL. Set eagerly when the node is
    # listed for Xtream (deterministic, same shape as a VOD movie's URL).
    # A source that needs a round trip to build this (e.g. a Stalker-style
    # create_link) would leave it None and do that work in its own
    # resolve_*_series_episode -- which is why cli.py always goes through
    # a resolve step for a leaf even though Xtream's is a no-op.
    url: str | None = None

    @property
    def container(self) -> bool:
        return self.kind in ("category", "series", "season")
