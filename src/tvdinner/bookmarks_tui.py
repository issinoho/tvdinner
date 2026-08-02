"""Interactive curses picker for tvdinner's saved bookmarks (`tvdinner
bookmarks`): a table of saved playlists you can add to, edit, delete, and
select from to launch playback -- see tvdinner.bookmarks for the on-disk
format this reads/writes.
"""

from __future__ import annotations

import curses
import logging
from pathlib import Path

from tvdinner.bookmarks import Bookmark, load_bookmarks, save_bookmarks
from tvdinner.plex import redact_plex_url
from tvdinner.stalker import redact_stalker_url
from tvdinner.xtream import redact_xtream_url

logger = logging.getLogger(__name__)

_HELP_LINE = "ENTER play   SPACE refresh EPG   a add   e edit   d delete   q quit"
_REFRESH_HEADER = "EPG Refresh"
_TMDB_HEADER = "TMDB"


def strip_wrapping_quotes(text: str) -> str:
    """Strip a single matching pair of leading/trailing quote characters
    from `text`, e.g. "'hdhomerun://host'" -> "hdhomerun://host". Guards
    against the easy mistake of copy-pasting a shell-quoted example URL
    (this project's own docs show URLs single-quoted for shell safety,
    e.g. tvdinner 'hdhomerun://192.168.1.50') into a context that isn't a
    shell and never strips them -- this form field, or main()'s own `url`
    argument if a launcher/script does the same thing."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
    """addstr, but tolerant of writing into the terminal's bottom-right
    corner -- curses raises there on some terminals since the cursor
    can't advance past it after the write, which is otherwise a routine
    crash for any full-screen text UI."""
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def _edit_field(stdscr, y: int, label: str, initial: str = "") -> str | None:
    """A single-line text editor occupying row `y`, prefixed with `label`.
    Returns the entered text on Enter, or None if cancelled with ESC."""
    height, width = stdscr.getmaxyx()
    input_x = len(label) + 1
    input_width = max(1, width - input_x - 1)
    text = list(initial)
    pos = len(text)

    curses.curs_set(1)
    try:
        while True:
            _safe_addstr(stdscr, y, 0, " " * (width - 1))
            _safe_addstr(stdscr, y, 0, label, curses.A_BOLD)
            # Keep the cursor's position within the field visible even if
            # the entered text is longer than the available width.
            view_start = max(0, pos - input_width + 1)
            visible = "".join(text)[view_start : view_start + input_width]
            _safe_addstr(stdscr, y, input_x, visible)
            stdscr.move(y, input_x + (pos - view_start))
            stdscr.refresh()

            ch = stdscr.getch()
            if ch in (curses.KEY_ENTER, 10, 13):
                return "".join(text)
            if ch == 27:  # ESC
                return None
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                if pos > 0:
                    del text[pos - 1]
                    pos -= 1
            elif ch == curses.KEY_DC:  # Delete
                if pos < len(text):
                    del text[pos]
            elif ch == curses.KEY_LEFT:
                pos = max(0, pos - 1)
            elif ch == curses.KEY_RIGHT:
                pos = min(len(text), pos + 1)
            elif ch == curses.KEY_HOME:
                pos = 0
            elif ch == curses.KEY_END:
                pos = len(text)
            elif 32 <= ch < 127:
                text.insert(pos, chr(ch))
                pos += 1
    finally:
        curses.curs_set(0)


def _prompt_bookmark_form(stdscr, initial: Bookmark | None = None) -> Bookmark | None:
    """Prompt for name/url/epg/channel/tmdb_api_token in sequence,
    pre-filled from `initial` when editing. Cancelling (ESC) at any field
    abandons the whole form. The token is shown in plain text here (same
    as every other field) -- only _draw_table's summary row hides it,
    since this form is never rendered anywhere but the user's own
    terminal."""
    stdscr.erase()
    _safe_addstr(stdscr, 0, 0, "ESC at any point cancels", curses.A_DIM)

    name = _edit_field(stdscr, 2, "Description: ", initial.name if initial else "")
    if name is None:
        return None
    url = _edit_field(stdscr, 3, "URL: ", initial.url if initial else "")
    if url is None:
        return None
    epg = _edit_field(stdscr, 4, "EPG URL (optional): ", (initial.epg or "") if initial else "")
    if epg is None:
        return None
    channel = _edit_field(
        stdscr, 5, "Default channel (optional): ", (initial.channel or "") if initial else ""
    )
    if channel is None:
        return None
    tmdb_api_token = _edit_field(
        stdscr, 6, "TMDB API token (optional): ", (initial.tmdb_api_token or "") if initial else ""
    )
    if tmdb_api_token is None:
        return None

    name = name.strip()
    url = strip_wrapping_quotes(url.strip())
    epg = strip_wrapping_quotes(epg.strip())
    channel = channel.strip()
    tmdb_api_token = tmdb_api_token.strip()
    if not name or not url:
        return None
    return Bookmark(name=name, url=url, epg=epg or None, channel=channel or None, tmdb_api_token=tmdb_api_token or None)


def _save_bookmarks_safely(stdscr, path: Path, bookmarks: list[Bookmark]) -> bool:
    """save_bookmarks, but tolerant of a write failure (e.g. disk full,
    permission denied) -- logs and shows it briefly rather than crashing
    the whole TUI with an uncaught exception."""
    try:
        save_bookmarks(path, bookmarks)
        return True
    except OSError as exc:
        logger.warning("Could not save bookmarks to %s: %s", path, exc)
        height, width = stdscr.getmaxyx()
        _safe_addstr(stdscr, height - 1, 0, f"Warning: could not save bookmarks: {exc}"[: width - 1], curses.A_BOLD)
        stdscr.refresh()
        stdscr.timeout(2000)
        stdscr.getch()
        stdscr.timeout(-1)
        return False


def _confirm(stdscr, message: str) -> bool:
    height, width = stdscr.getmaxyx()
    y = height - 1
    _safe_addstr(stdscr, y, 0, " " * (width - 1))
    _safe_addstr(stdscr, y, 0, f"{message} (y/n)", curses.A_BOLD)
    stdscr.refresh()
    while True:
        ch = stdscr.getch()
        if ch in (ord("y"), ord("Y")):
            return True
        if ch in (ord("n"), ord("N"), 27):
            return False


def _draw_table(stdscr, bookmarks: list[Bookmark], refresh_flags: list[bool], index: int) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    _safe_addstr(stdscr, 0, 0, "tvdinner bookmarks", curses.A_BOLD)
    _safe_addstr(stdscr, 1, 0, _HELP_LINE[: width - 1], curses.A_DIM)

    if not bookmarks:
        _safe_addstr(stdscr, 3, 2, "No bookmarks yet -- press 'a' to add one.")
        return

    name_width = max(10, min(28, width // 4))
    chan_width = max(6, min(12, width // 8))
    refresh_width = len(_REFRESH_HEADER)
    tmdb_width = len(_TMDB_HEADER)
    url_width = max(10, width - name_width - chan_width - refresh_width - tmdb_width - 8)
    header = (
        f"{'Description':<{name_width}} {'Channel':<{chan_width}} "
        f"{_REFRESH_HEADER:<{refresh_width}} {_TMDB_HEADER:<{tmdb_width}} {'URL'}"
    )
    _safe_addstr(stdscr, 2, 0, header[: width - 1], curses.A_UNDERLINE)
    for row, bookmark in enumerate(bookmarks):
        y = row + 3
        if y >= height - 1:
            _safe_addstr(stdscr, height - 1, 0, "(more below)", curses.A_DIM)
            break
        channel_text = bookmark.channel or ""
        checkbox = "[x]" if refresh_flags[row] else "[ ]"
        # Presence only -- the token itself is never shown in the table,
        # only in the add/edit form (see _prompt_bookmark_form).
        tmdb_checkbox = "[x]" if bookmark.tmdb_api_token else "[ ]"
        line = (
            f"{bookmark.name[:name_width]:<{name_width}} "
            f"{channel_text[:chan_width]:<{chan_width}} "
            f"{checkbox:<{refresh_width}} "
            f"{tmdb_checkbox:<{tmdb_width}} "
            f"{bookmark.url[:url_width]}"
        )
        attr = curses.A_REVERSE if row == index else curses.A_NORMAL
        _safe_addstr(stdscr, y, 0, line[: width - 1], attr)


def run_bookmarks_tui(path: Path) -> tuple[Bookmark, bool] | None:
    """Show the interactive bookmarks table. Returns (Bookmark, refresh_epg)
    for the entry the user selected to launch (ENTER) -- refresh_epg is
    True if its "EPG Refresh" checkbox was checked (SPACE) in this
    session, always starting unchecked and never persisted -- or None if
    they quit ('q'/ESC) without selecting one. Add/edit/delete save to
    `path` immediately."""
    bookmarks, warnings = load_bookmarks(path)
    for warning in warnings:
        print(f"Warning: {warning}")
        logger.warning(warning)
    logger.info("Bookmarks opened: %d entries from %s", len(bookmarks), path)

    refresh_flags = [False] * len(bookmarks)
    selected: list[tuple[Bookmark, bool] | None] = [None]

    def _main(stdscr) -> None:
        curses.curs_set(0)
        index = 0
        while True:
            index = max(0, min(index, len(bookmarks) - 1)) if bookmarks else 0
            _draw_table(stdscr, bookmarks, refresh_flags, index)
            stdscr.refresh()

            ch = stdscr.getch()
            if ch in (ord("q"), 27):
                logger.info("Bookmarks closed")
                return
            if ch == curses.KEY_UP and bookmarks:
                index = (index - 1) % len(bookmarks)
            elif ch == curses.KEY_DOWN and bookmarks:
                index = (index + 1) % len(bookmarks)
            elif ch == ord(" ") and bookmarks:
                refresh_flags[index] = not refresh_flags[index]
            elif ch in (curses.KEY_ENTER, 10, 13) and bookmarks:
                selected[0] = (bookmarks[index], refresh_flags[index])
                logger.info(
                    "Bookmark selected: '%s' (%s) refresh_epg=%s",
                    bookmarks[index].name,
                    redact_plex_url(redact_stalker_url(redact_xtream_url(bookmarks[index].url))),
                    refresh_flags[index],
                )
                return
            elif ch == ord("a"):
                new_bookmark = _prompt_bookmark_form(stdscr)
                if new_bookmark is not None:
                    bookmarks.append(new_bookmark)
                    refresh_flags.append(False)
                    _save_bookmarks_safely(stdscr, path, bookmarks)
                    index = len(bookmarks) - 1
                    logger.info(
                        "Bookmark added: '%s' (%s)",
                        new_bookmark.name,
                        redact_plex_url(redact_stalker_url(redact_xtream_url(new_bookmark.url))),
                    )
            elif ch == ord("e") and bookmarks:
                edited = _prompt_bookmark_form(stdscr, initial=bookmarks[index])
                if edited is not None:
                    bookmarks[index] = edited
                    _save_bookmarks_safely(stdscr, path, bookmarks)
                    logger.info(
                        "Bookmark edited: '%s' (%s)", edited.name, redact_plex_url(redact_stalker_url(redact_xtream_url(edited.url)))
                    )
            elif ch in (ord("d"), curses.KEY_DC) and bookmarks:
                if _confirm(stdscr, f"Delete '{bookmarks[index].name}'?"):
                    deleted = bookmarks[index]
                    del bookmarks[index]
                    del refresh_flags[index]
                    _save_bookmarks_safely(stdscr, path, bookmarks)
                    logger.info("Bookmark deleted: '%s'", deleted.name)

    curses.wrapper(_main)
    return selected[0]
