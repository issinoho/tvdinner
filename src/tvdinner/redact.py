"""Masking credentials embedded in a *resource* URL for logging/printing
-- as opposed to a login URL, which xtream.py's redact_xtream_url,
stalker.py's redact_stalker_url, and plex.py's redact_plex_url already
each handle for their own `xtream://`/`stalker://`/`plex://` scheme.

Channel.url/VodItem.url is source-agnostic by the time cli.py or
overlay.py want to log it (see CLAUDE.md: every live-channel source
normalizes into the same Channel/Playlist model) -- a caller holding one
of these doesn't generally know which backend produced it, so the three
existing scheme-specific redactors (which only fire for their own login
scheme) can't be reused here even though the same credentials end up
embedded in these URLs too:

  - Xtream: the username/password live in the *path* of every stream URL
    it hands out (http://host/live/USERNAME/PASSWORD/id.ts,
    .../movie/USERNAME/PASSWORD/id.ext, .../series/... the same way) --
    a completely different shape from the login URL's userinfo@host.
  - Plex: every resource URL (playable file, thumbnail, poster, backdrop,
    theme) carries `?X-Plex-Token=...`, the same live token as the login
    URL, just as a query param on an http(s):// URL instead of a
    plex://one.
  - A Stalker portal's own create_link response is otherwise opaque (the
    URL shape is whatever that specific portal returns), but the login
    URL's own mac= is sometimes echoed back into it -- masked generically
    below alongside password/token, on the chance it appears.

Best-effort: covers every shape this codebase's own sources are known to
produce, not a general-purpose URL sanitizer. Returns `url` unchanged if
none of these patterns match."""

from __future__ import annotations

import hashlib
import re
import urllib.parse

_XTREAM_PATH_CREDS_RE = re.compile(r"(/(?:live|movie|series)/[^/]+/)([^/]+)(/)")
# Credential-ish query parameters, masked wherever they appear in a URL that
# reaches a log line or an error message. Kept broad on purpose: an IPTV panel,
# a tvtimes "Play" link (?ticket=<jwt>), a Stalker create_link response and the
# like all use different names for "the thing that grants access".
_QUERY_CRED_RE = re.compile(
    r"(?:^|[?&])"
    r"(password|passwd|pwd|pass|token|ticket|auth|secret|sig|session|"
    r"api[-_]?key|access[-_]?token|X-Plex-Token|mac)"
    r"=([^&]+)",
    re.IGNORECASE,
)
# `scheme://user:pass@host` -- an M3U/XMLTV URL can carry HTTP basic-auth creds.
_USERINFO_RE = re.compile(r"://([^/:@\s]+):([^/@\s]+)@")


def redact_resource_url(url: str) -> str:
    def _mask_path(match: re.Match[str]) -> str:
        password = match.group(2)
        masked = f"{password[:2]}***" if len(password) > 2 else "***"
        return f"{match.group(1)}{masked}{match.group(3)}"

    url = _XTREAM_PATH_CREDS_RE.sub(_mask_path, url, count=1)
    url = _USERINFO_RE.sub(lambda m: f"://{m.group(1)}:***@", url)

    def _mask_query(match: re.Match[str]) -> str:
        value = match.group(2)
        masked = f"{value[:4]}***" if len(value) > 4 else "***"
        return match.group(0).replace(value, masked)

    return _QUERY_CRED_RE.sub(_mask_query, url)


# The login-URL schemes whose credentials a caller might use as an
# at-rest lookup key (favorites.json's feed, keyed by the raw source
# string given on the command line -- see cli.py's main()). Deliberately
# just the scheme names, not a dependency on xtream.py/stalker.py/
# plex.py's own is_xtream_url/is_stalker_url/is_plex_url -- this module
# stays a leaf with no knowledge of any specific source, same reasoning
# as redact_resource_url's own docstring above.
_CREDENTIAL_LOGIN_SCHEMES = ("xtream", "xtreams", "stalker", "stalkers", "plex", "plexs")


def stable_credential_key(source: str) -> str:
    """A version of `source` safe to persist as an at-rest lookup key --
    unlike redact_resource_url above (which keeps a human-legible partial
    mask, fine for a log line read once), a key must never collide
    between two genuinely different credentials, so for a login URL that
    carries one (xtream://, stalker://, plex:// -- see
    _CREDENTIAL_LOGIN_SCHEMES), this hashes the *whole* source string
    into a short, opaque, non-reversible key instead of masking just the
    credential portion. Returns `source` completely unchanged for
    anything else (an M3U playlist URL, a local file path, an
    hdhomerun:// URL, ...) -- the common case, so upgrading to this
    changes nothing for most users' saved keys at all.

    Hashing the whole string (not just the password/token) preserves the
    exact granularity favorites.json already had before this existed --
    it was keyed by an exact string match on the full source, so two
    feeds that differ only in host or port already got separate entries;
    hashing the whole string keeps that property while making the result
    non-reversible."""
    if urllib.parse.urlsplit(source).scheme not in _CREDENTIAL_LOGIN_SCHEMES:
        return source
    return f"{urllib.parse.urlsplit(source).scheme}:{hashlib.sha256(source.encode()).hexdigest()[:16]}"
