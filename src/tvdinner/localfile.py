"""Best-effort movie-identity guessing for local file playback (a local
video file path given directly as tvdinner's `url` argument), so tmdb.py
has a (title, year) to search TMDB with. A local movie file's filename is
usually the only signal available at all -- unlike split_m3u_vod_items's
deliberate "don't guess" stance on M3U group-titles, guessing here is the
whole point (--title/--year on the CLI override a bad guess; see cli.py's
local-video-file branch of main()). The actual (title, year)-guessing is
shared with youtube.py's own title guessing (see movietitle.py) -- once a
filename's dot/underscore separators are normalized to spaces, it's the
same problem.
"""

from __future__ import annotations

import re
from pathlib import Path

from tvdinner.movietitle import guess_title_year

# Filename separators to normalize to spaces before searching for a year --
# scene-release dots/underscores, not the parens/brackets/dashes that
# legitimately group a year in a "Title (Year)" or "Title - Year" filename.
_SEPARATORS_RE = re.compile(r"[._]+")


def guess_movie_title_year(path: Path) -> tuple[str, str | None]:
    """(title, year) guessed from `path`'s filename alone -- see
    movietitle.guess_title_year for the actual guessing logic, applied
    here to the filename stem with dot/underscore scene-release
    separators normalized to spaces first (a video title, unlike a
    filename, needs no such normalization, hence youtube.py calling
    movietitle.guess_title_year directly instead of through this
    wrapper). Never reads the file itself (no embedded-metadata lookup
    here; that would require the file to actually be loaded by the
    player)."""
    normalized = _SEPARATORS_RE.sub(" ", path.stem)
    return guess_title_year(normalized)
