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
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

import requests

from tvdinner.epg import atomic_write_bytes, cache_path_for

logger = logging.getLogger(__name__)

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_POSTER_BASE = "https://image.tmdb.org/t/p/w500"

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


def is_movie_category(category: str | None) -> bool:
    if not category:
        return False
    lowered = category.lower()
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


def _search_movie(title: str, year: str | None, api_token: str, timeout: float = 10.0) -> tuple[bool, dict | None]:
    """(ok, result). ok=False means the request/parse itself failed --
    never cached, so a transient outage is retried next session rather
    than permanently poisoned (there's no retry/backoff anywhere in this
    codebase, so "don't cache failures" is the whole mitigation). ok=True
    with result=None means TMDB was reached fine and had zero results -- a
    genuine no-match, which IS cached by callers. `result` is TMDB's own
    /search/movie result dict for the best match, unmodified -- callers
    (_search_movie_rating, fetch_movie_metadata_cached) pick out whatever
    field(s) they need."""
    params = {"query": title}
    if year:
        params["year"] = year
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
    cache_dir: Path = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> float | None:
    """The only function in this module that does network I/O. Always
    called from a background thread (see prefetch_ratings) -- never from
    an overlay.py render function."""
    hit, cached = _load_cached_rating(cache_dir, title, year, max_age)
    if hit:
        return cached
    ok, rating = _search_movie_rating(title, year, api_token)
    if ok:
        _save_cached_rating(cache_dir, title, year, rating)
    return rating


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
    cache_dir: Path = DEFAULT_TMDB_CACHE_DIR,
    max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
) -> str | None:
    """The director counterpart to fetch_movie_rating_cached above, for
    the guide's programme-details popup (see cli.py's show_selected_details)
    rather than the grid's bulk-prefetched rating badges. Always called
    from a background thread (see prefetch_director) -- never from an
    overlay.py render function."""
    hit, cached = _load_cached_director(cache_dir, title, year, max_age)
    if hit:
        return cached
    ok, match = _search_movie(title, year, api_token)
    director = _fetch_movie_director(match["id"], api_token) if ok and match is not None and match.get("id") is not None else None
    if ok:
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


def _movie_metadata_from_result(result: dict, fallback_title: str, director: str | None = None) -> MovieMetadata:
    poster_path = result.get("poster_path")
    vote_average = result.get("vote_average")
    release_year = str(result.get("release_date") or "")[:4]
    return MovieMetadata(
        title=str(result.get("title") or fallback_title),
        year=release_year if release_year.isdigit() else None,
        poster_url=f"{TMDB_POSTER_BASE}{poster_path}" if poster_path else None,
        overview=str(result.get("overview")) if result.get("overview") else None,
        rating=f"{vote_average:.1f}" if isinstance(vote_average, (int, float)) else None,
        director=director,
    )


def _metadata_cache_source_key(title: str, year: str | None) -> str:
    return f"tmdb-movie-metadata:{title.strip().lower()}:{year or ''}"


def _load_cached_metadata(cache_dir: Path, title: str, year: str | None, max_age: timedelta) -> tuple[bool, MovieMetadata | None]:
    """(hit, metadata) -- see _load_cached_rating's docstring for the
    hit/miss/negative-result contract, identical here."""
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
    cache_dir: Path = DEFAULT_TMDB_CACHE_DIR,
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
    that as a failure -- director is a bonus field, not load-bearing."""
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
        metadata = _movie_metadata_from_result(match, title, director)
    _save_cached_metadata(cache_dir, title, year, metadata)
    return metadata


def cached_rating(title: str, year: str | None) -> float | None:
    """Pure, non-blocking, in-memory-only read -- safe to call from a
    render function. Returns None both for "not fetched yet" and
    "fetched, no TMDB match"."""
    return _ratings_cache.get(_cache_key(title, year))


def rating_for(title: str, category: str | None, year: str | None) -> float | None:
    """Convenience wrapper combining the movie-category gate with the
    cache read -- what render functions should actually call."""
    return cached_rating(title, year) if is_movie_category(category) else None


def prefetch_ratings(
    movies: Iterable[RatingKey],
    api_token: str,
    cache_dir: Path = DEFAULT_TMDB_CACHE_DIR,
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


def director_for(title: str, category: str | None, year: str | None) -> str | None:
    """Convenience wrapper combining the movie-category gate with the
    cache read -- what render functions should actually call."""
    return cached_director(title, year) if is_movie_category(category) else None


def prefetch_director(
    movies: Iterable[RatingKey],
    api_token: str,
    cache_dir: Path = DEFAULT_TMDB_CACHE_DIR,
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
