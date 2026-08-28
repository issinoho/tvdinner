# Security Policy

## Supported versions

tvdinner has no maintained release branches — only the **latest
release** is supported. Please upgrade before reporting anything;
there's a good chance it's already fixed.

## Reporting a vulnerability

Please **don't** open a public issue for a security report.

The preferred way is GitHub's own
[private vulnerability reporting](https://github.com/issinoho/tvdinner/security/advisories/new):
open the [Security tab](https://github.com/issinoho/tvdinner/security)
and click "Report a vulnerability". This reaches only the maintainer,
lets you attach details/reproduction steps privately, and keeps the
whole conversation off the public issue tracker until there's a fix.

If you'd rather not use that, email **iain@issinoho.com** instead.

This is a solo-maintained project, so there's no formal SLA — but a
genuine security report gets priority over everything else in the
queue. Expect an initial response within a few days.

## Before you report: known, by-design behavior

A few things that look like findings at first glance are actually
documented trade-offs, not bugs:

- **Source credentials are stored as plain text at rest.** An Xtream
  Codes password, a Stalker Portal MAC address, or a Plex token is
  saved unencrypted wherever you save the source itself — a
  [bookmark](https://github.com/issinoho/tvdinner/wiki/Backup-Restore-and-Bookmarks)'s
  `bookmarks.json`, `favorites.json` (keyed by feed), or a `tvdinner
  backup` archive. This is unavoidable for a tool that has to
  reconnect using that same credential later, and matches how most
  IPTV/media player clients handle it. What tvdinner *does* guarantee:
  a credential is always redacted before it reaches the log file (a
  masked `user:***@host` form for Xtream, a MAC with all but the first
  two octets masked for Stalker, a token with only its first four
  characters kept for Plex) — a log excerpt is always safe to paste
  into a bug report. If you find a spot where a credential reaches the
  log *unredacted*, that's a real report worth filing.
- **A TMDB API token is the one exception** — it's fully masked in the
  log (not just redacted), and never even partially shown in the
  bookmarks table UI.
- **Chromecast/casting sends the stream URL directly to the device**,
  not proxied through tvdinner — so anything on the same LAN that can
  already reach the stream URL can reach it the same way a Chromecast
  does. This is inherent to how Chromecast's own receiver app works,
  not something tvdinner adds.

If you're unsure whether something is a genuine vulnerability or one
of the above, report it anyway — that's a completely reasonable thing
to ask.

## Other security measures already in place

- [Dependabot](https://github.com/issinoho/tvdinner/security/dependabot)
  is configured for both this repo's GitHub Actions and its Python
  dependencies (see `.github/dependabot.yml`).
- Secret scanning and push protection are enabled on this repository.
