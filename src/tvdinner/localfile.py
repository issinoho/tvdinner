"""Best-effort movie-identity guessing for local file playback (a local
video file path given directly as tvdinner's `url` argument), so tmdb.py
has a (title, year) to search TMDB with. A local movie file's filename is
usually the only signal available at all -- unlike split_m3u_vod_items's
deliberate "don't guess" stance on M3U group-titles, guessing here is the
whole point (--title/--year on the CLI override a bad guess; see cli.py's
local-video-file branch of main()).
"""

from __future__ import annotations

import re
from pathlib import Path

# A 19xx/20xx year, optionally wrapped in parens/brackets -- e.g. "His Girl
# Friday (1940).webm", "Movie.Title.2020.1080p.BluRay.x264-GROUP.mkv",
# "Movie Title - 1999 - BluRay". Resolution/codec tags (1080p, 2160p, x264,
# h265...) never collide with this since none of them start with "19"/"20".
_YEAR_RE = re.compile(r"[\(\[]?((?:19|20)\d{2})[\)\]]?")

# Filename separators to normalize to spaces before searching for a year --
# scene-release dots/underscores, not the parens/brackets/dashes that
# legitimately group a year in a "Title (Year)" or "Title - Year" filename.
_SEPARATORS_RE = re.compile(r"[._]+")

# Trimmed off both the title (leftover grouping punctuation once the year's
# been cut away) and the whole-stem fallback (e.g. a bare "(1940).webm").
_STRIP_CHARS = " -()[]"


def guess_movie_title_year(path: Path) -> tuple[str, str | None]:
    """(title, year) guessed from `path`'s filename alone -- year is None
    if no plausible one is found, in which case the whole cleaned-up stem
    is used as the title. Never reads the file itself (no embedded-
    metadata lookup here; that would require the file to actually be
    loaded by the player)."""
    normalized = _SEPARATORS_RE.sub(" ", path.stem)

    match = _YEAR_RE.search(normalized)
    if match is None:
        return normalized.strip(), None

    title = normalized[: match.start()].strip(_STRIP_CHARS)
    return (title or normalized.strip(_STRIP_CHARS)), match.group(1)
