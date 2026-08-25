"""TMDB-sourced star ratings for movie programmes.

Ratings are matched by (title, year) against TMDB's search/movie endpoint
and cached both on disk (long-lived, since a vote average barely moves day
to day) and in an in-memory dict for the app's lifetime. Everything here is
opt-in: with no API token, nothing in this module is ever called.

Render functions (overlay.py) only ever call the pure, non-blocking
`rating_for`/`cached_rating` -- fetching happens exclusively via
`prefetch_ratings`, which spawns background threads, so a guide/details
render is never the thing waiting on a network round-trip.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

import requests

from tvdinner.epg import atomic_write_bytes, cache_path_for

logger = logging.getLogger(__name__)

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_POSTER_BASE = "https://image.tmdb.org/t/p/w500"
# w1280 (not the much larger "original") -- wide enough to cover a 4K hero
# backdrop (overlay.py's _render_vod_info_hero) without the multi-MB
# download an "original" backdrop can be.
TMDB_BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"
# w500 -- a title-logo wordmark composited at a small corner size (see
# overlay.py's _render_epg_hero/_render_vod_info_hero) never needs
# backdrop-grade resolution.
TMDB_LOGO_BASE = "https://image.tmdb.org/t/p/w500"

if sys.platform == "win32":
    DEFAULT_TMDB_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "tvdinner" / "tmdb_cache"
else:
    DEFAULT_TMDB_CACHE_DIR = Path.home() / ".cache" / "tvdinner" / "tmdb"

# A vote_average genuinely doesn't move much day to day, and there's no
# retry/backoff logic anywhere in this codebase for outbound HTTP calls --
# a long cache TTL is the whole mitigation for not hammering TMDB's rate
# limits, not something layered on top of it.
DEFAULT_TMDB_CACHE_MAX_AGE = timedelta(days=30)

RatingKey = tuple[str, str | None]

# Distinguishes "no backdrop_path override given" from "override given as
# None" (a genuine "no backdrop" result) in _movie_metadata_from_result's
# optional backdrop_path param below -- None itself is a meaningful value
# there, so it can't double as the "not provided" default.
_UNSET = object()

# Deliberately conservative and English-centric -- a miss just means no
# badge is shown, same philosophy as channel_logos.py's exact-match-only
# approach (never show a wrong one).
_MOVIE_CATEGORY_KEYWORDS = ("movie", "film", "cinema")

# Process-lifetime, module-level caches -- mirrors overlay.py's own
# _logo_cache precedent. Tests should monkeypatch these to fresh
# dict/set instances to avoid cross-test leakage.
_ratings_cache: dict[RatingKey, float | None] = {}
_in_flight: set[RatingKey] = set()

# Separate from the two caches above -- deliberately never bulk-prefetched
# for every movie visible in the guide grid the way ratings are (see
# prefetch_ratings), since that would double the request volume for
# every visible movie just to show a field only the single, deliberately-
# opened programme-details popup ever displays. Populated lazily, one
# item at a time, only when that popup actually opens (see cli.py's
# show_selected_details).
_director_cache: dict[RatingKey, str | None] = {}
_director_in_flight: set[RatingKey] = set()

# Same single-item, lazy-populate-on-open shape as the director cache
# above (see its own comment) -- for the guide's live-channel "now
# playing" hero (cli.py's show_epg_overlay, when the current programme
# is movie-category), not the whole grid.
_backdrop_cache: dict[RatingKey, str | None] = {}
_backdrop_in_flight: set[RatingKey] = set()

# Same single-item, lazy-populate-on-open shape as the backdrop cache
# above -- a fourth, independently-cached field for the same hero
# overlays (see overlay.py's _render_epg_hero/_render_vod_info_hero),
# just the title-treatment logo instead of the wide backdrop photo.
_logo_cache: dict[RatingKey, str | None] = {}
_logo_in_flight: set[RatingKey] = set()


def is_movie_category(category: str | None, group_title: str | None = None) -> bool:
    """True if either the EPG programme's own <category> tag(s) or the
    channel's M3U group-title say "movie" -- some feeds (e.g. Pluto TV's
    themed movie channels relayed through m3u4u) only ever tag a
    programme's actual genre (Drama, Thriller, ...) in <category>, never
    the word "movie" itself, even though the channel is unambiguously
    movie-only per its own group-title="Movies". group_title is optional
    (and ignored when absent) so every existing call site -- most of
    which only ever had a programme's category to check -- keeps working
    unchanged."""
    return _has_movie_keyword(category) or _has_movie_keyword(group_title)


def _has_movie_keyword(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in _MOVIE_CATEGORY_KEYWORDS)


def _cache_key(title: str, year: str | None) -> RatingKey:
    return (title.strip(), year)


def _tmdb_cache_source_key(title: str, year: str | None) -> str:
    # Not a real URL -- cache_path_for just hashes whatever string it's
    # given (see its own docstring in epg.py), same as channel_logos.py
    # reusing it for a non-XML payload.
    return f"tmdb-movie-rating:{title.strip().lower()}:{year or ''}"


def _load_cached_rating(cache_dir: Path, title: str, year: str | None, max_age: timedelta) -> tuple[bool, float | None]:
    """(hit, rating). hit=False means no usable entry (missing or expired)
    -- caller should fetch. rating=None with hit=True is a cached negative
    result (TMDB has no match for this title/year)."""
    path = cache_path_for(cache_dir, _tmdb_cache_source_key(title, year), suffix=".json")
    if not path.is_file():
        return False, None
    age = timedelta(seconds=time.time() - path.stat().st_mtime)
    if age >= max_age:
        return False, None
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False, None
    return True, payload.get("rating")


def _save_cached_rating(cache_dir: Path, title: str, year: str | None, rating: float | None) -> None:
    path = cache_path_for(cache_dir, _tmdb_cache_source_key(title, year), suffix=".json")
    try:
        atomic_write_bytes(path, json.dumps({"rating": rating}).encode())
    except OSError:
        pass  # best-effort, same tolerance as the rest of this module's disk cache


def _strip_embedded_year(title: str, year: str | None) -> str:
    """Some XMLTV feeds (e.g. SiliconDust's HDHomeRun cloud guide) already
    bake the year into <title> for movies -- the exact same real-world
    pattern overlay.py's _title_with_year works around for display (see
    its own comment), using the identical "ends with '(<year>)'" check.
    Left in, a query like "Confessions of a Driving Instructor (1977)"
    routinely returns zero results from TMDB's search endpoint (it wants
    the bare title; the separate `year` param already narrows by year),
    which then gets cached as a permanent negative for every rating/
    director/backdrop lookup on that programme. Only strips an exact
    "(<year>)" suffix -- never a general trailing parenthetical -- so a
    genuinely year-less title with unrelated parens is untouched."""
    if year and title.endswith(f"({year})"):
        return title[: -(len(year) + 3)].rstrip()
    return title


def _search_movie(title: str, year: str | None, api_token: str, timeout: float = 10.0) -> tuple[bool, dict | None]:
    """(ok, result). ok=False means the request/parse itself failed --
    never cached, so a transient outage is retried next session rather
    than permanently poisoned (there's no retry/backoff anywhere in this
    codebase, so "don't cache failures" is the whole mitigation). ok=True
    with result=None means TMDB was reached fine and had zero results -- a
    genuine no-match, which IS cached by callers. `result` is TMDB's own
    /search/movie result dict for the best match, unmodified -- callers
    (_search_movie_rating, fetch_movie_metadata_cached) pick out whatever
    field(s) they need.

    Deliberately never sends `year` as a search filter, even though it's
    known here -- confirmed live that TMDB's /search/movie treats it as a
    hard server-side filter (zero results, not just deprioritized) rather
    than a preference, and a guide provider's own release year routinely
    differs from TMDB's by a year (confirmed live: Gracenote/HDHomeRun's
    <date> said 1977 for "Confessions of a Driving Instructor", TMDB's own
    release_date is 1976-09-01) -- filtering server-side on that would
    silently zero out an otherwise-correct match and cache it as a
    permanent negative. `year`, when given, is used purely to pick the
    best candidate below, after a title-only search."""
    params = {"query": _strip_embedded_year(title, year)}
    try:
        response = requests.get(
            f"{TMDB_API_BASE}/search/movie",
            params=params,
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        # Deliberately logs only the title -- never the token, headers, or
        # params, matching this codebase's redact-before-logging norm for
        # every other credential (see cli.py's redact_xtream_url etc.).
        logger.warning("TMDB movie lookup failed for %r: %s", title, exc)
        return False, None

    results = payload.get("results") or []
    if not results:
        return True, None

    match = next((r for r in results if year and str(r.get("release_date", ""))[:4] == year), results[0])
    return True, match


def _fetch_movie_director(movie_id: int, api_token: str, timeout: float = 10.0) -> str | None:
    """The crew entry/entries with job="Director" for a TMDB movie id, from
    /movie/{id}/credits -- a separate request from _search_movie, since
    /search/movie's own result objects carry no crew information at all.
    Joins co-directors with ", " (rare, but real -- e.g. the Wachowskis).
    None on any failure (network, parse, or genuinely no director credited)
    -- same "never cache a transient failure" reasoning as _search_movie,
    left to the caller."""
    try:
        response = requests.get(
            f"{TMDB_API_BASE}/movie/{movie_id}/credits",
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TMDB credits lookup failed for movie id %d: %s", movie_id, exc)
        return None

    crew = payload.get("crew") or []
    directors = [member.get("name") for member in crew if member.get("job") == "Director" and member.get("name")]
    return ", ".join(directors) if directors else None


def _best_backdrop_path(movie_id: int, fallback_path: str | None, api_token: str, timeout: float = 10.0) -> str | None:
    """The highest-resolution, textless backdrop TMDB has for a movie, via
    /movie/{id}/images -- /search/movie's own backdrop_path is just
    whichever single one TMDB happened to mark as the default, which is
    often far from the largest or best one actually available (confirmed
    live: several older/lower-popularity titles had a sharper backdrop
    sitting one call away). "Textless" (iso_639_1 is None, i.e. no
    burned-in title/language text) is preferred since this app's own
    title/rating/description text is composited on top of whatever comes
    back -- a backdrop with someone else's text baked in would visually
    clash with ours. Falls back to `fallback_path` (the /search/movie
    result's own backdrop_path) on any request failure, an empty
    backdrops list, or (defensively) a candidate with no file_path at
    all -- this is purely a best-effort upgrade, never a regression over
    what the caller already had."""
    try:
        response = requests.get(
            f"{TMDB_API_BASE}/movie/{movie_id}/images",
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TMDB images lookup failed for movie id %d: %s", movie_id, exc)
        return fallback_path

    backdrops = payload.get("backdrops") or []
    if not backdrops:
        return fallback_path

    textless = [b for b in backdrops if b.get("iso_639_1") is None]
    best = max(textless or backdrops, key=lambda b: b.get("width") or 0)
    return best.get("file_path") or fallback_path


def _best_logo_path(movie_id: int, api_token: str, timeout: float = 10.0) -> str | None:
    """The highest-resolution English title-logo TMDB has for a movie, via
    the same /movie/{id}/images endpoint _best_backdrop_path uses --
    /search/movie's own result has no logo_path field at all, so unlike
    the backdrop there's no fallback_path to hand back on failure.

    Opposite preference from _best_backdrop_path: a logo's entire point
    is its burned-in title text, so an English (iso_639_1 == "en") one
    is preferred; if TMDB has none, any language beats no logo at all
    rather than giving up. Returns None on any request failure or an
    empty logos list.

    TMDB's logos frequently include .svg entries (confirmed live: e.g.
    "Friends" only has an SVG one) -- Pillow has no SVG rasterizer at
    all, so fetch_image would just silently fail to decode one later,
    making the whole lookup a no-op logo despite resolving a URL just
    fine. Filtered out here, before the width comparison, rather than
    tolerated as a fetch failure downstream."""
    try:
        response = requests.get(
            f"{TMDB_API_BASE}/movie/{movie_id}/images",
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TMDB images lookup failed for movie id %d: %s", movie_id, exc)
        return None

    logos = [logo for logo in (payload.get("logos") or []) if not str(logo.get("file_path")).endswith(".svg")]
    if not logos:
        return None

    english = [logo for logo in logos if logo.get("iso_639_1") == "en"]
    best = max(english or logos, key=lambda logo: logo.get("width") or 0)
    return best.get("file_path")


def _search_movie_rating(title: str, year: str | None, api_token: str, timeout: float = 10.0) -> tuple[bool, float | None]:
    """(ok, rating) -- the vote_average out of _search_movie's best match."""
    ok, match = _search_movie(title, year, api_token, timeout)
    if not ok:
        return False, None
    if match is None:
        return True, None
    vote_average = match.get("vote_average")
    return True, float(vote_average) if isinstance(vote_average, (int, float)) else None


def fetch_movie_rating_cached(
    title: str,
    year: str | None,
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> float | None:
    """The only function in this module that does network I/O. Always
    called from a background thread (see prefetch_ratings) -- never from
    an overlay.py render function. `cache_dir=None` (see --no-tmdb-cache)
    skips both the disk read and the write, always hitting the network."""
    if cache_dir is not None:
        hit, cached = _load_cached_rating(cache_dir, title, year, max_age)
        if hit:
            return cached
    ok, rating = _search_movie_rating(title, year, api_token)
    if ok and cache_dir is not None:
        _save_cached_rating(cache_dir, title, year, rating)
    return rating


def _search_movie_backdrop(title: str, year: str | None, api_token: str, timeout: float = 10.0) -> tuple[bool, str | None]:
    """(ok, backdrop_url) -- the best backdrop_path out of _search_movie's
    match (see _best_backdrop_path), resolved to a full image URL."""
    ok, match = _search_movie(title, year, api_token, timeout)
    if not ok:
        return False, None
    if match is None:
        return True, None
    backdrop_path = match.get("backdrop_path")
    if match.get("id") is not None:
        backdrop_path = _best_backdrop_path(match["id"], backdrop_path, api_token, timeout)
    return True, f"{TMDB_BACKDROP_BASE}{backdrop_path}" if backdrop_path else None


def _backdrop_cache_source_key(title: str, year: str | None) -> str:
    return f"tmdb-movie-backdrop:{title.strip().lower()}:{year or ''}"


def _load_cached_backdrop(cache_dir: Path, title: str, year: str | None, max_age: timedelta) -> tuple[bool, str | None]:
    """(hit, backdrop_url) -- see _load_cached_rating's docstring for the
    hit/miss/negative-result contract, identical here."""
    path = cache_path_for(cache_dir, _backdrop_cache_source_key(title, year), suffix=".json")
    if not path.is_file():
        return False, None
    age = timedelta(seconds=time.time() - path.stat().st_mtime)
    if age >= max_age:
        return False, None
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False, None
    return True, payload.get("backdrop_url")


def _save_cached_backdrop(cache_dir: Path, title: str, year: str | None, backdrop_url: str | None) -> None:
    path = cache_path_for(cache_dir, _backdrop_cache_source_key(title, year), suffix=".json")
    try:
        atomic_write_bytes(path, json.dumps({"backdrop_url": backdrop_url}).encode())
    except OSError:
        pass  # best-effort, same tolerance as the rest of this module's disk cache


def fetch_movie_backdrop_cached(
    title: str,
    year: str | None,
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> str | None:
    """The backdrop-art counterpart to fetch_movie_rating_cached above,
    for the guide's live-channel "now playing" hero (cli.py's
    show_epg_overlay, when the current programme is movie-category) --
    a separate, independently-cached TMDB search from a VOD item's own
    tmdb.MovieMetadata.backdrop_url (fetch_movie_metadata_cached), since
    a live channel's "current programme" is a fresh EPG lookup every
    render, not a stored VodItem to rebind in place the way cli.py's
    _enrich_vod_hero_art_in_background does. Always called from a
    background thread (see prefetch_backdrop) -- never from an
    overlay.py render function. `cache_dir=None` (see --no-tmdb-cache)
    skips both the disk read and the write, always hitting the network."""
    if cache_dir is not None:
        hit, cached = _load_cached_backdrop(cache_dir, title, year, max_age)
        if hit:
            return cached
    ok, backdrop_url = _search_movie_backdrop(title, year, api_token)
    if ok and cache_dir is not None:
        _save_cached_backdrop(cache_dir, title, year, backdrop_url)
    return backdrop_url


def _search_movie_logo(title: str, year: str | None, api_token: str, timeout: float = 10.0) -> tuple[bool, str | None]:
    """(ok, logo_url) -- the best logo_path out of _search_movie's match
    (see _best_logo_path), resolved to a full image URL."""
    ok, match = _search_movie(title, year, api_token, timeout)
    if not ok:
        return False, None
    if match is None:
        return True, None
    logo_path = _best_logo_path(match["id"], api_token, timeout) if match.get("id") is not None else None
    return True, f"{TMDB_LOGO_BASE}{logo_path}" if logo_path else None


def _logo_cache_source_key(title: str, year: str | None) -> str:
    return f"tmdb-movie-logo:{title.strip().lower()}:{year or ''}"


def _load_cached_logo(cache_dir: Path, title: str, year: str | None, max_age: timedelta) -> tuple[bool, str | None]:
    """(hit, logo_url) -- see _load_cached_rating's docstring for the
    hit/miss/negative-result contract, identical here.

    Also a miss if a genuinely cached (non-None) logo_url ends in .svg --
    _best_logo_path used to be able to pick one of those before it
    started filtering them out (Pillow can't decode SVG at all), so a
    real entry written before that fix would otherwise keep serving an
    undecodable URL, silently, for the rest of its max_age. Cheap enough
    to check on every load that it doesn't need its own cache-format
    migration/versioning."""
    path = cache_path_for(cache_dir, _logo_cache_source_key(title, year), suffix=".json")
    if not path.is_file():
        return False, None
    age = timedelta(seconds=time.time() - path.stat().st_mtime)
    if age >= max_age:
        return False, None
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False, None
    logo_url = payload.get("logo_url")
    if logo_url and logo_url.endswith(".svg"):
        return False, None
    return True, logo_url


def _save_cached_logo(cache_dir: Path, title: str, year: str | None, logo_url: str | None) -> None:
    path = cache_path_for(cache_dir, _logo_cache_source_key(title, year), suffix=".json")
    try:
        atomic_write_bytes(path, json.dumps({"logo_url": logo_url}).encode())
    except OSError:
        pass  # best-effort, same tolerance as the rest of this module's disk cache


def fetch_movie_logo_cached(
    title: str,
    year: str | None,
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> str | None:
    """The title-logo counterpart to fetch_movie_backdrop_cached above --
    same independently-cached-from-MovieMetadata reasoning, same "always
    called from a background thread (see prefetch_logo)" contract, same
    `cache_dir=None` (--no-tmdb-cache) bypass."""
    if cache_dir is not None:
        hit, cached = _load_cached_logo(cache_dir, title, year, max_age)
        if hit:
            return cached
    ok, logo_url = _search_movie_logo(title, year, api_token)
    if ok and cache_dir is not None:
        _save_cached_logo(cache_dir, title, year, logo_url)
    return logo_url


def _search_tv(name: str, year: str | None, api_token: str, timeout: float = 10.0) -> tuple[bool, dict | None]:
    """The /search/tv counterpart to _search_movie above -- same (ok,
    result) contract, same reasoning for never sending `year` as a
    server-side filter (just used here to pick the best candidate, via
    first_air_date instead of movie's release_date)."""
    params = {"query": _strip_embedded_year(name, year)}
    try:
        response = requests.get(
            f"{TMDB_API_BASE}/search/tv",
            params=params,
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TMDB TV lookup failed for %r: %s", name, exc)
        return False, None

    results = payload.get("results") or []
    if not results:
        return True, None

    match = next((r for r in results if year and str(r.get("first_air_date", ""))[:4] == year), results[0])
    return True, match


def _best_tv_logo_path(tv_id: int, api_token: str, timeout: float = 10.0) -> str | None:
    """The /tv/{id}/images counterpart to _best_logo_path above -- same
    English-preferred/any-language-fallback/max-width selection, same
    .svg exclusion (confirmed live: "Friends" only has an SVG logo on
    TMDB, which Pillow can't decode -- see _best_logo_path's own
    docstring)."""
    try:
        response = requests.get(
            f"{TMDB_API_BASE}/tv/{tv_id}/images",
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("TMDB images lookup failed for TV id %d: %s", tv_id, exc)
        return None

    logos = [logo for logo in (payload.get("logos") or []) if not str(logo.get("file_path")).endswith(".svg")]
    if not logos:
        return None

    english = [logo for logo in logos if logo.get("iso_639_1") == "en"]
    best = max(english or logos, key=lambda logo: logo.get("width") or 0)
    return best.get("file_path")


def _search_tv_logo(name: str, year: str | None, api_token: str, timeout: float = 10.0) -> tuple[bool, str | None]:
    """(ok, logo_url) -- the best logo_path out of _search_tv's match (see
    _best_tv_logo_path), resolved to a full image URL."""
    ok, match = _search_tv(name, year, api_token, timeout)
    if not ok:
        return False, None
    if match is None:
        return True, None
    logo_path = _best_tv_logo_path(match["id"], api_token, timeout) if match.get("id") is not None else None
    return True, f"{TMDB_LOGO_BASE}{logo_path}" if logo_path else None


def _tv_logo_cache_source_key(name: str, year: str | None) -> str:
    # Distinct prefix from _logo_cache_source_key's "tmdb-movie-logo:" --
    # a show and a movie that happen to share a title/year must never
    # collide on the same disk-cache entry.
    return f"tmdb-tv-logo:{name.strip().lower()}:{year or ''}"


def _load_cached_tv_logo(cache_dir: Path, name: str, year: str | None, max_age: timedelta) -> tuple[bool, str | None]:
    """(hit, logo_url) -- see _load_cached_rating's docstring for the
    hit/miss/negative-result contract, identical here. Also a miss for a
    cached .svg logo_url -- see _load_cached_logo's own docstring
    (confirmed live via "Friends", whose only TMDB logo was an SVG)."""
    path = cache_path_for(cache_dir, _tv_logo_cache_source_key(name, year), suffix=".json")
    if not path.is_file():
        return False, None
    age = timedelta(seconds=time.time() - path.stat().st_mtime)
    if age >= max_age:
        return False, None
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False, None
    logo_url = payload.get("logo_url")
    if logo_url and logo_url.endswith(".svg"):
        return False, None
    return True, logo_url


def _save_cached_tv_logo(cache_dir: Path, name: str, year: str | None, logo_url: str | None) -> None:
    path = cache_path_for(cache_dir, _tv_logo_cache_source_key(name, year), suffix=".json")
    try:
        atomic_write_bytes(path, json.dumps({"logo_url": logo_url}).encode())
    except OSError:
        pass  # best-effort, same tolerance as the rest of this module's disk cache


def fetch_tv_logo_cached(
    name: str,
    year: str | None,
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> str | None:
    """The TV-show counterpart to fetch_movie_logo_cached above, for a
    Plex TV episode's own show name (cli.py's
    _enrich_vod_hero_art_in_background, via VodItem.series_title) --
    always called from a background thread, never from an overlay.py
    render function. No in-memory cache/prefetch split the way the
    movie logo has (_logo_cache/prefetch_logo) -- this is only ever a
    one-shot lookup already running on a background thread, same
    reasoning fetch_movie_metadata_cached itself relies on. Same
    `cache_dir=None` (--no-tmdb-cache) bypass as every other fetch here."""
    if cache_dir is not None:
        hit, cached = _load_cached_tv_logo(cache_dir, name, year, max_age)
        if hit:
            return cached
    ok, logo_url = _search_tv_logo(name, year, api_token)
    if ok and cache_dir is not None:
        _save_cached_tv_logo(cache_dir, name, year, logo_url)
    return logo_url


def _director_cache_source_key(title: str, year: str | None) -> str:
    return f"tmdb-movie-director:{title.strip().lower()}:{year or ''}"


def _load_cached_director(cache_dir: Path, title: str, year: str | None, max_age: timedelta) -> tuple[bool, str | None]:
    """(hit, director) -- see _load_cached_rating's docstring for the
    hit/miss/negative-result contract, identical here."""
    path = cache_path_for(cache_dir, _director_cache_source_key(title, year), suffix=".json")
    if not path.is_file():
        return False, None
    age = timedelta(seconds=time.time() - path.stat().st_mtime)
    if age >= max_age:
        return False, None
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False, None
    return True, payload.get("director")


def _save_cached_director(cache_dir: Path, title: str, year: str | None, director: str | None) -> None:
    path = cache_path_for(cache_dir, _director_cache_source_key(title, year), suffix=".json")
    try:
        atomic_write_bytes(path, json.dumps({"director": director}).encode())
    except OSError:
        pass  # best-effort, same tolerance as the rest of this module's disk cache


def fetch_movie_director_cached(
    title: str,
    year: str | None,
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> str | None:
    """The director counterpart to fetch_movie_rating_cached above, for
    the guide's programme-details popup (see cli.py's show_selected_details)
    rather than the grid's bulk-prefetched rating badges. Always called
    from a background thread (see prefetch_director) -- never from an
    overlay.py render function. Same `cache_dir=None` (--no-tmdb-cache)
    bypass as every other fetch here."""
    if cache_dir is not None:
        hit, cached = _load_cached_director(cache_dir, title, year, max_age)
        if hit:
            return cached
    ok, match = _search_movie(title, year, api_token)
    director = _fetch_movie_director(match["id"], api_token) if ok and match is not None and match.get("id") is not None else None
    if ok and cache_dir is not None:
        _save_cached_director(cache_dir, title, year, director)
    return director


@dataclass
class MovieMetadata:
    """Everything render_vod_info_overlay (overlay.py) knows how to show
    for a VodItem, sourced from a single TMDB /search/movie match --
    built by fetch_movie_metadata_cached for local-file playback (see
    cli.py's local-video-file branch of main()), where -- unlike
    Xtream/Stalker/Plex -- there's no provider API to supply any of
    this."""

    title: str
    year: str | None
    poster_url: str | None
    overview: str | None
    rating: str | None
    # Defaulted so MovieMetadata(**payload) still loads an on-disk cache
    # entry written before this field existed (see _load_cached_metadata).
    director: str | None = None
    # Wide hero/backdrop art (overlay.py's _render_vod_info_hero), separate
    # from poster_url's portrait poster -- same defaulting-for-old-cache-
    # entries reasoning as director above.
    backdrop_url: str | None = None
    # Title-treatment logo composited in the hero's top-right corner
    # (overlay.py's _render_epg_hero/_render_vod_info_hero) -- same
    # defaulting-for-old-cache-entries reasoning as director/backdrop_url
    # above.
    logo_url: str | None = None


def _movie_metadata_from_result(
    result: dict,
    fallback_title: str,
    director: str | None = None,
    backdrop_path: str | None = _UNSET,
    logo_path: str | None = None,
) -> MovieMetadata:
    poster_path = result.get("poster_path")
    if backdrop_path is _UNSET:
        backdrop_path = result.get("backdrop_path")
    vote_average = result.get("vote_average")
    release_year = str(result.get("release_date") or "")[:4]
    return MovieMetadata(
        title=str(result.get("title") or fallback_title),
        year=release_year if release_year.isdigit() else None,
        poster_url=f"{TMDB_POSTER_BASE}{poster_path}" if poster_path else None,
        overview=str(result.get("overview")) if result.get("overview") else None,
        rating=f"{vote_average:.1f}" if isinstance(vote_average, (int, float)) else None,
        director=director,
        backdrop_url=f"{TMDB_BACKDROP_BASE}{backdrop_path}" if backdrop_path else None,
        logo_url=f"{TMDB_LOGO_BASE}{logo_path}" if logo_path else None,
    )


def _metadata_cache_source_key(title: str, year: str | None) -> str:
    return f"tmdb-movie-metadata:{title.strip().lower()}:{year or ''}"


def _load_cached_metadata(cache_dir: Path, title: str, year: str | None, max_age: timedelta) -> tuple[bool, MovieMetadata | None]:
    """(hit, metadata) -- see _load_cached_rating's docstring for the
    hit/miss/negative-result contract, identical here.

    Also a miss if a *found, positive* match's payload predates the
    `director`, `backdrop_url`, or `logo_url` fields (each added after
    this cache format originally shipped): confirmed live that a real
    on-disk entry from before the `director` change has no "director"
    key at all, so MovieMetadata(**payload) would silently default it to
    None forever -- a stale schema masquerading as a genuine "TMDB has no
    director for this" negative, for up to max_age, even though a fresh
    fetch would find one right away. Same reasoning applies to
    `backdrop_url`/`logo_url`. A negative match (payload is None -- no
    TMDB result at all) has no such fields to be missing and is exempt,
    same as it always was. Also a miss if `logo_url` is a cached .svg --
    see _load_cached_logo's own docstring (confirmed live via "Friends",
    whose only TMDB logo was an SVG Pillow can't decode)."""
    path = cache_path_for(cache_dir, _metadata_cache_source_key(title, year), suffix=".json")
    if not path.is_file():
        return False, None
    age = timedelta(seconds=time.time() - path.stat().st_mtime)
    if age >= max_age:
        return False, None
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False, None
    if payload is not None and ("director" not in payload or "backdrop_url" not in payload or "logo_url" not in payload):
        return False, None
    if payload is not None and str(payload.get("logo_url")).endswith(".svg"):
        return False, None
    return True, MovieMetadata(**payload) if payload is not None else None


def _save_cached_metadata(cache_dir: Path, title: str, year: str | None, metadata: MovieMetadata | None) -> None:
    path = cache_path_for(cache_dir, _metadata_cache_source_key(title, year), suffix=".json")
    try:
        atomic_write_bytes(path, json.dumps(asdict(metadata) if metadata is not None else None).encode())
    except OSError:
        pass  # best-effort, same tolerance as the rest of this module's disk cache


def fetch_movie_metadata_cached(
    title: str,
    year: str | None,
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> MovieMetadata | None:
    """The poster/synopsis/rating/director counterpart to
    fetch_movie_rating_cached above, for a local file's guessed (title,
    year) (see cli.py's mpv command and localfile.guess_movie_title_year)
    rather than a guide programme's. Always called from a background
    thread -- never from an overlay.py render function. Returns None both
    for "TMDB reached, no match" and for a request failure (never cached
    either way -- see _search_movie's own docstring). A found match with
    no director credited (or whose separate /credits lookup itself fails)
    still returns full metadata with director=None, rather than treating
    that as a failure -- director is a bonus field, not load-bearing.
    `cache_dir=None` (see --no-tmdb-cache) skips both the disk read and
    the write, always hitting the network."""
    if cache_dir is not None:
        hit, cached = _load_cached_metadata(cache_dir, title, year, max_age)
        if hit:
            return cached
    ok, match = _search_movie(title, year, api_token)
    if not ok:
        return None
    if match is None:
        metadata = None
    else:
        director = _fetch_movie_director(match["id"], api_token) if match.get("id") is not None else None
        backdrop_path = (
            _best_backdrop_path(match["id"], match.get("backdrop_path"), api_token) if match.get("id") is not None else _UNSET
        )
        logo_path = _best_logo_path(match["id"], api_token) if match.get("id") is not None else None
        metadata = _movie_metadata_from_result(match, title, director, backdrop_path, logo_path)
    if cache_dir is not None:
        _save_cached_metadata(cache_dir, title, year, metadata)
    return metadata


def cached_rating(title: str, year: str | None) -> float | None:
    """Pure, non-blocking, in-memory-only read -- safe to call from a
    render function. Returns None both for "not fetched yet" and
    "fetched, no TMDB match"."""
    return _ratings_cache.get(_cache_key(title, year))


def rating_for(title: str, category: str | None, year: str | None, group_title: str | None = None) -> float | None:
    """Convenience wrapper combining the movie-category gate with the
    cache read -- what render functions should actually call."""
    return cached_rating(title, year) if is_movie_category(category, group_title) else None


def prefetch_ratings(
    movies: Iterable[RatingKey],
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> None:
    """Spawn one daemon thread per (title, year) not already cached or
    already in flight. Safe to call on every guide render tick with the
    full set of currently-visible movie keys -- duplicates are always a
    no-op, so a fast scroll never piles up redundant fetches.

    No lock is needed: this function only ever runs on the caller's own
    thread and is the only code that ever adds to `_in_flight`, so the
    add-if-absent check here can't race with itself. Each spawned thread
    only ever writes `_ratings_cache[key]` and discards that same `key`
    from `_in_flight` -- a single dict/set mutation per key, atomic under
    the GIL, and no two threads ever touch the same key.
    """
    for title, year in movies:
        key = _cache_key(title, year)
        if key in _ratings_cache or key in _in_flight:
            continue
        _in_flight.add(key)

        def _fetch(title: str = title, year: str | None = year, key: RatingKey = key) -> None:
            try:
                rating = fetch_movie_rating_cached(title, year, api_token, cache_dir, max_age)
                _ratings_cache[key] = rating
            finally:
                _in_flight.discard(key)

        threading.Thread(target=_fetch, daemon=True).start()


def cached_director(title: str, year: str | None) -> str | None:
    """Pure, non-blocking, in-memory-only read -- safe to call from a
    render function. Returns None both for "not fetched yet" and
    "fetched, no director credited"."""
    return _director_cache.get(_cache_key(title, year))


def director_for(title: str, category: str | None, year: str | None, group_title: str | None = None) -> str | None:
    """Convenience wrapper combining the movie-category gate with the
    cache read -- what render functions should actually call."""
    return cached_director(title, year) if is_movie_category(category, group_title) else None


def prefetch_director(
    movies: Iterable[RatingKey],
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> None:
    """The single-item counterpart to prefetch_ratings, for the guide's
    programme-details popup -- takes the same Iterable[RatingKey] shape
    for symmetry, but callers should only ever pass the one currently-open
    programme's key, not every visible grid movie (see _director_cache's
    own module-level comment for why)."""
    for title, year in movies:
        key = _cache_key(title, year)
        if key in _director_cache or key in _director_in_flight:
            continue
        _director_in_flight.add(key)

        def _fetch(title: str = title, year: str | None = year, key: RatingKey = key) -> None:
            try:
                director = fetch_movie_director_cached(title, year, api_token, cache_dir, max_age)
                _director_cache[key] = director
            finally:
                _director_in_flight.discard(key)

        threading.Thread(target=_fetch, daemon=True).start()


def cached_backdrop(title: str, year: str | None) -> str | None:
    """Pure, non-blocking, in-memory-only read -- safe to call from a
    render function. Returns None both for "not fetched yet" and
    "fetched, no backdrop art"."""
    return _backdrop_cache.get(_cache_key(title, year))


def backdrop_for(title: str, category: str | None, year: str | None, group_title: str | None = None) -> str | None:
    """Convenience wrapper combining the movie-category gate with the
    cache read -- what render functions should actually call."""
    return cached_backdrop(title, year) if is_movie_category(category, group_title) else None


def prefetch_backdrop(
    movies: Iterable[RatingKey],
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
    on_fetched: Callable[[RatingKey], None] | None = None,
) -> None:
    """The backdrop counterpart to prefetch_director -- same single-item-
    only semantics (callers should only ever pass the one currently-
    showing programme's key, not every visible grid movie).

    `on_fetched`, when given, is called (from the background thread, once
    per key actually fetched -- not for a key that was already cached or
    already in flight, since then nothing changed for this call to react
    to) after that key's result has landed in the cache. Unlike rating/
    director, which just add a supplementary field to an already-drawn
    banner on their next show, a backdrop arriving switches the *entire*
    overlay layout from banner to full-bleed hero -- worth reacting to
    immediately rather than waiting for the next unrelated redraw. See
    cli.py's show_epg_overlay, the only caller that passes this."""
    for title, year in movies:
        key = _cache_key(title, year)
        if key in _backdrop_cache or key in _backdrop_in_flight:
            continue
        _backdrop_in_flight.add(key)

        def _fetch(title: str = title, year: str | None = year, key: RatingKey = key) -> None:
            try:
                backdrop_url = fetch_movie_backdrop_cached(title, year, api_token, cache_dir, max_age)
                _backdrop_cache[key] = backdrop_url
            finally:
                _backdrop_in_flight.discard(key)
            if on_fetched is not None:
                on_fetched(key)

        threading.Thread(target=_fetch, daemon=True).start()


def cached_logo(title: str, year: str | None) -> str | None:
    """Pure, non-blocking, in-memory-only read -- safe to call from a
    render function. Returns None both for "not fetched yet" and
    "fetched, no logo art"."""
    return _logo_cache.get(_cache_key(title, year))


def logo_for(title: str, category: str | None, year: str | None, group_title: str | None = None) -> str | None:
    """Convenience wrapper combining the movie-category gate with the
    cache read -- what render functions should actually call."""
    return cached_logo(title, year) if is_movie_category(category, group_title) else None


def prefetch_logo(
    movies: Iterable[RatingKey],
    api_token: str,
    cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
    on_fetched: Callable[[RatingKey], None] | None = None,
) -> None:
    """The title-logo counterpart to prefetch_backdrop above -- same
    single-item-only semantics and `on_fetched` contract. See cli.py's
    show_epg_overlay, which fires this alongside prefetch_backdrop for
    the same key."""
    for title, year in movies:
        key = _cache_key(title, year)
        if key in _logo_cache or key in _logo_in_flight:
            continue
        _logo_in_flight.add(key)

        def _fetch(title: str = title, year: str | None = year, key: RatingKey = key) -> None:
            try:
                logo_url = fetch_movie_logo_cached(title, year, api_token, cache_dir, max_age)
                _logo_cache[key] = logo_url
            finally:
                _logo_in_flight.discard(key)
            if on_fetched is not None:
                on_fetched(key)

        threading.Thread(target=_fetch, daemon=True).start()
