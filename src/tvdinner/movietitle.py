"""Shared (title, year)-guessing and TMDB search-candidate generation for
both a local video filename (localfile.py) and a YouTube video's own
title (youtube.py) -- fundamentally the same problem once a filename's
dot/underscore separators are normalized to spaces: pull a 19xx/20xx year
out of a piece of free text and, since real-world titles routinely chain
extra text (cast names, taglines, quality tags, a yt-dlp download's
trailing "[videoID]") onto the actual movie name, generate a couple of
TMDB search candidates rather than trusting the raw remainder verbatim.
"""

from __future__ import annotations

import re

# A 19xx/20xx year, optionally wrapped in parens/brackets -- e.g. "Title
# (Year)", "Title.Year.1080p...", "Year - Title - ...". Resolution/codec
# tags (1080p, 2160p, x264...) never collide with this since none of
# them start with "19"/"20".
_YEAR_RE = re.compile(r"[\(\[]?((?:19|20)\d{2})[\)\]]?")

# Punctuation left dangling once a year (or, for title_search_candidates
# below, a chained "<title> - <cast/tagline>" segment) has been cut out
# of the middle or start of a piece of text.
_STRIP_CHARS = " -()[]"

# A "<title> - <cast/tagline/tag>" separator -- many archive-channel
# video titles (and yt-dlp downloads of them, which append " [videoID]"
# to the filename) chain several of these after the real movie title,
# e.g. "1940 - His Girl Friday - Cary Grant and Rosalind Russell -
# Ex-lovers become headline hunters [wEx-z1TYPKU]" (confirmed live: this
# exact title/filename finds nothing on TMDB as a whole string, but its
# first segment, "His Girl Friday", finds it immediately). Not split on
# ":" -- that's a real movie-subtitle separator too often ("Mission:
# Impossible") to treat as noise the way a " - "/"|" chain usually is.
_SEGMENT_SPLIT_RE = re.compile(r"\s[-|·–—]\s")


def guess_title_year(text: str) -> tuple[str, str | None]:
    """(title, year) guessed from a piece of already-space-normalized
    free text -- year is None (and text returned unchanged, just
    trimmed) if no 19xx/20xx year is found anywhere in it.

    Prefers whatever text comes *before* the year -- the common "Title
    (Year) junk-after" convention (e.g. a scene-release filename,
    "Movie.Title.2020.1080p.BluRay.x264-GROUP") -- over what comes
    after; only falls back to the text *after* the year if there's
    nothing before it, i.e. the year comes first (the "Year - Title -
    junk-after" convention some archive channels, and yt-dlp downloads
    of them, use). Falls back to the whole text if somehow both sides
    are empty (e.g. the text is just a bare "(1940)")."""
    match = _YEAR_RE.search(text)
    if match is None:
        return text.strip(), None
    before = text[: match.start()].strip(_STRIP_CHARS)
    after = text[match.end() :].strip(_STRIP_CHARS)
    return (before or after or text.strip(_STRIP_CHARS)), match.group(1)


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
