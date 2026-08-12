"""YouTube video metadata via YouTube's own public oEmbed endpoint (no
API key needed, unlike TMDB). mpv already plays a plain youtube.com/
youtu.be URL directly through its built-in yt-dlp hook, so this module
only supplies what the 'i' overlay needs to show something better than a
bare title: the video's own title/uploader/thumbnail, always available
for free for any public video, and (only if that title itself carries a
19xx/20xx year, and --tmdb-api-token is set) a further TMDB lookup for a
richer poster/synopsis/rating on the rare video that's actually a real
movie -- tried against a few candidate title strings (see
title_search_candidates), not just the raw video title verbatim, since
real archive-channel titles routinely chain cast/tagline text onto the
actual movie name that would otherwise sink the TMDB search entirely.
See cli.py's YouTube branch of main().
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_YOUTUBE_HOSTNAMES = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_OEMBED_URL = "https://www.youtube.com/oembed"

# A 19xx/20xx year embedded in the video's own title, optionally wrapped
# in parens/brackets -- e.g. "Nosferatu (1922) Full Movie" or, just as
# commonly for real public-domain-archive channels (confirmed live
# against a real upload), "1940 - His Girl Friday - ...". Same regex (and
# the same reasoning) as localfile._YEAR_RE: an arbitrary YouTube title
# (unlike a structured "Title (Year).ext" filename) usually isn't a movie
# at all, so a titleless/yearless TMDB search here would risk a wrong
# match far more than it would for a properly-named local movie file --
# requiring this same signal keeps the two equally conservative.
_YEAR_RE = re.compile(r"[\(\[]?((?:19|20)\d{2})[\)\]]?")


def is_youtube_url(url: str) -> bool:
    return urllib.parse.urlsplit(url).hostname in _YOUTUBE_HOSTNAMES


def guess_title_year(title: str) -> tuple[str, str | None]:
    """(title, year) -- year is None (and title returned unchanged) if no
    19xx/20xx year is found anywhere in it."""
    match = _YEAR_RE.search(title)
    if match is None:
        return title, None
    # Collapses whatever whitespace surrounded the removed year (e.g. a
    # mid-title year leaves a double space between the two halves) down
    # to a single space, then trims off whatever separator punctuation
    # was grouping it (parens/brackets already excluded by the match
    # itself, so this is just leftover dashes/colons -- e.g. a leading
    # "1940 - His Girl Friday" leaves a dangling "- " at the front).
    cleaned = re.sub(r"\s+", " ", title[: match.start()] + " " + title[match.end() :]).strip(" -:|")
    return (cleaned or title), match.group(1)


# A "<title> - <cast/tagline/tag>" separator -- many archive-channel
# video titles chain several of these after the real movie title, e.g.
# "His Girl Friday - Cary Grant and Rosalind Russell - Ex-lovers become
# headline hunters" (confirmed live: this exact title, once its leading
# year is stripped, finds nothing on TMDB as a whole string, but its
# first segment, "His Girl Friday", finds it immediately). Not split on
# ":" -- that's a real movie subtitle separator too often ("Mission:
# Impossible") to treat as noise the way a " - "/"|" chain usually is.
_SEGMENT_SPLIT_RE = re.compile(r"\s[-|·–—]\s")


def title_search_candidates(title: str) -> list[str]:
    """Ordered candidate strings to try searching TMDB with, most
    specific first: `title` split on the separator above (its first,
    presumably-just-the-movie-name segment), then `title` itself
    unsplit, as a broader fallback for a movie whose real title happens
    to contain one of those separators. A single-element list (just
    `title`) if there's nothing to split on. Deduplicated, order
    preserved."""
    segments = [s.strip() for s in _SEGMENT_SPLIT_RE.split(title) if s.strip()]
    candidates = [segments[0], title] if len(segments) > 1 else [title]
    seen: set[str] = set()
    ordered = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


@dataclass
class YoutubeInfo:
    title: str
    author_name: str | None
    thumbnail_url: str | None


def fetch_youtube_oembed(url: str, timeout: float = 10.0) -> YoutubeInfo | None:
    """The only function in this module that does network I/O -- always
    called from a background thread (see cli.py's YouTube branch of
    main()), never a render function. Returns None on any failure
    (private/deleted/age-restricted video, network error, malformed
    response, ...) -- a miss just means the 'i' overlay never gets past
    its placeholder title, same graceful-degradation philosophy as
    tmdb.py's own lookups."""
    try:
        response = requests.get(_OEMBED_URL, params={"url": url, "format": "json"}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("YouTube oEmbed lookup failed for %s: %s", url, exc)
        return None

    title = payload.get("title")
    if not isinstance(title, str) or not title:
        return None
    author_name = payload.get("author_name")
    thumbnail_url = payload.get("thumbnail_url")
    return YoutubeInfo(
        title=title,
        author_name=author_name if isinstance(author_name, str) else None,
        thumbnail_url=thumbnail_url if isinstance(thumbnail_url, str) else None,
    )
