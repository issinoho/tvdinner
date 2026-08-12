"""YouTube video metadata via YouTube's own public oEmbed endpoint (no
API key needed, unlike TMDB). mpv already plays a plain youtube.com/
youtu.be URL directly through its built-in yt-dlp hook, so this module
only supplies what the 'i' overlay needs to show something better than a
bare title: the video's own title/uploader/thumbnail, always available
for free for any public video. Its (title, year)-guessing (used, if
--tmdb-api-token is set, for a further TMDB lookup -- see cli.py's
YouTube branch of main()) is shared with localfile.py's own filename
guessing, see movietitle.py.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_YOUTUBE_HOSTNAMES = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_OEMBED_URL = "https://www.youtube.com/oembed"


def is_youtube_url(url: str) -> bool:
    return urllib.parse.urlsplit(url).hostname in _YOUTUBE_HOSTNAMES


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
