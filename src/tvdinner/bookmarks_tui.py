"""Interactive curses picker for tvdinner's saved bookmarks (`tvdinner
bookmarks`): a table of saved playlists you can add to, edit, delete, and
select from to launch playback -- see tvdinner.bookmarks for the on-disk
format this reads/writes.

Themed after classic DOS-era blue file managers like XTree Gold: navy
background, white/cyan text, double-line box borders, and a gold
selection bar (see _init_theme -- the real XTree Gold's own selection
bar was actually cyan, not gold, per a reference screenshot; gold here
is a deliberate nod to the product's name rather than a literal
recreation). Falls back to the original monochrome look on a terminal
without color support.
"""

from __future__ import annotations

import curses
import logging
from pathlib import Path
from typing import NamedTuple

from tvdinner.bookmarks import Bookmark, load_bookmarks, save_bookmarks
from tvdinner.plex import redact_plex_url
from tvdinner.stalker import redact_stalker_url
from tvdinner.xtream import redact_xtream_url

logger = logging.getLogger(__name__)

_HELP_LINE = "ENTER play   SPACE refresh EPG   a add   e edit   d delete   q quit"
_REFRESH_HEADER = "EPG Refresh"
_TMDB_HEADER = "TMDB"

_PAIR_NORMAL = 1
_PAIR_ACCENT = 2
_PAIR_SELECTED = 3
_PAIR_WARNING = 4

_BOX_TL, _BOX_TR, _BOX_BL, _BOX_BR = "╔", "╗", "╚", "╝"
_BOX_H, _BOX_V = "═", "║"


class Theme(NamedTuple):
    normal: int
    accent: int
    dim: int
    selected: int
    warning: int


def _init_theme(stdscr) -> Theme:
    """Curses attributes for the bookmarks TUI, computed once and
    threaded through every draw function instead of each one hardcoding
    curses.A_* constants directly. Falls back to the original
    monochrome, attribute-only look on a terminal without color
    support (has_colors() is false e.g. over some SSH/tmux configs)."""
    if not curses.has_colors():
        return Theme(
            normal=curses.A_NORMAL,
            accent=curses.A_BOLD,
            dim=curses.A_DIM,
            selected=curses.A_REVERSE,
            warning=curses.A_BOLD,
        )
    curses.start_color()
    curses.init_pair(_PAIR_NORMAL, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(_PAIR_ACCENT, curses.COLOR_CYAN, curses.COLOR_BLUE)
    curses.init_pair(_PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(_PAIR_WARNING, curses.COLOR_WHITE, curses.COLOR_RED)
    stdscr.bkgd(" ", curses.color_pair(_PAIR_NORMAL))
    return Theme(
        normal=curses.color_pair(_PAIR_NORMAL),
        accent=curses.color_pair(_PAIR_ACCENT) | curses.A_BOLD,
        dim=curses.color_pair(_PAIR_ACCENT),
        selected=curses.color_pair(_PAIR_SELECTED) | curses.A_BOLD,
        warning=curses.color_pair(_PAIR_WARNING) | curses.A_BOLD,
    )


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


def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int) -> None:
    """addstr, but tolerant of writing into the terminal's bottom-right
    corner -- curses raises there on some terminals since the cursor
    can't advance past it after the write, which is otherwise a routine
    crash for any full-screen text UI."""
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def _draw_box(stdscr, theme: Theme, top: int, bottom: int, width: int) -> None:
    """Double-line box border (XTree Gold's signature panel look, see
    module docstring) spanning rows `top`..`bottom` and the full
    terminal width. Interior content is written by the caller at
    columns 1..width-2, rows top+1..bottom-1."""
    if bottom <= top or width < 2:
        return
    _safe_addstr(stdscr, top, 0, _BOX_TL + _BOX_H * (width - 2) + _BOX_TR, theme.accent)
    for y in range(top + 1, bottom):
        _safe_addstr(stdscr, y, 0, _BOX_V, theme.accent)
        _safe_addstr(stdscr, y, width - 1, _BOX_V, theme.accent)
    _safe_addstr(stdscr, bottom, 0, _BOX_BL + _BOX_H * (width - 2) + _BOX_BR, theme.accent)


def _edit_field(stdscr, theme: Theme, y: int, label: str, initial: str = "") -> str | None:
    """A single-line text editor occupying row `y`, prefixed with `label`.
    Returns the entered text on Enter, or None if cancelled with ESC.

    Uses get_wch() rather than getch() -- some playlists append decorative
    non-ASCII characters to a channel's display name (e.g. m3u4u.com-style
    circled-letter badges like "BBC One Ⓐ"), which a bookmark's saved
    --channel value needs to reproduce exactly for select_channel's
    exact-match branch to find it (its substring fallback only kicks in
    when there's a single ambiguous match, which a decorated regional
    variant's name often isn't). getch() decodes multi-byte UTF-8 input
    one raw byte at a time -- each byte individually fails the
    32 <= ch < 127 printable check and gets silently dropped, so non-ASCII
    text could never actually be entered here at all, no matter how it was
    typed or pasted. get_wch() hands back a properly-decoded single
    character string for regular input (still an int for function/special
    keys, same as getch()), so this only needs a str/int branch, not a
    manual UTF-8 reassembly."""
    height, width = stdscr.getmaxyx()
    input_x = len(label) + 1
    input_width = max(1, width - input_x - 1)
    text = list(initial)
    pos = len(text)

    curses.curs_set(1)
    try:
        while True:
            _safe_addstr(stdscr, y, 0, " " * (width - 1), theme.normal)
            _safe_addstr(stdscr, y, 0, label, theme.accent)
            # Keep the cursor's position within the field visible even if
            # the entered text is longer than the available width.
            view_start = max(0, pos - input_width + 1)
            visible = "".join(text)[view_start : view_start + input_width]
            _safe_addstr(stdscr, y, input_x, visible, theme.normal)
            stdscr.move(y, input_x + (pos - view_start))
            stdscr.refresh()

            ch = stdscr.get_wch()
            if ch in (curses.KEY_ENTER, "\n", "\r"):
                return "".join(text)
            if ch == "\x1b":  # ESC
                return None
            if ch in (curses.KEY_BACKSPACE, "\x7f", "\x08"):
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
            elif isinstance(ch, str) and ch.isprintable():
                text.insert(pos, ch)
                pos += 1
    finally:
        curses.curs_set(0)


def _prompt_bookmark_form(stdscr, theme: Theme, initial: Bookmark | None = None) -> Bookmark | None:
    """Prompt for name/url/epg/channel/tmdb_api_token in sequence,
    pre-filled from `initial` when editing. Cancelling (ESC) at any field
    abandons the whole form. The token is shown in plain text here (same
    as every other field) -- only _draw_table's summary row hides it,
    since this form is never rendered anywhere but the user's own
    terminal."""
    stdscr.erase()
    title = "Edit bookmark" if initial else "Add bookmark"
    _safe_addstr(stdscr, 0, 0, title, theme.accent)
    _safe_addstr(stdscr, 1, 0, "ESC at any point cancels", theme.dim)

    name = _edit_field(stdscr, theme, 3, "Description: ", initial.name if initial else "")
    if name is None:
        return None
    url = _edit_field(stdscr, theme, 4, "URL: ", initial.url if initial else "")
    if url is None:
        return None
    epg = _edit_field(stdscr, theme, 5, "EPG URL (optional): ", (initial.epg or "") if initial else "")
    if epg is None:
        return None
    channel = _edit_field(
        stdscr, theme, 6, "Default channel (optional): ", (initial.channel or "") if initial else ""
    )
    if channel is None:
        return None
    tmdb_api_token = _edit_field(
        stdscr, theme, 7, "TMDB API token (optional): ", (initial.tmdb_api_token or "") if initial else ""
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


def _save_bookmarks_safely(stdscr, theme: Theme, path: Path, bookmarks: list[Bookmark]) -> bool:
    """save_bookmarks, but tolerant of a write failure (e.g. disk full,
    permission denied) -- logs and shows it briefly rather than crashing
    the whole TUI with an uncaught exception."""
    try:
        save_bookmarks(path, bookmarks)
        return True
    except OSError as exc:
        logger.warning("Could not save bookmarks to %s: %s", path, exc)
        height, width = stdscr.getmaxyx()
        _safe_addstr(
            stdscr, height - 2, 1, f"Warning: could not save bookmarks: {exc}"[: width - 3], theme.warning
        )
        stdscr.refresh()
        stdscr.timeout(2000)
        stdscr.getch()
        stdscr.timeout(-1)
        return False


def _confirm(stdscr, theme: Theme, message: str) -> bool:
    height, width = stdscr.getmaxyx()
    y = height - 2
    _safe_addstr(stdscr, y, 1, " " * (width - 2), theme.normal)
    _safe_addstr(stdscr, y, 1, f"{message} (y/n)", theme.warning)
    stdscr.refresh()
    while True:
        ch = stdscr.getch()
        if ch in (ord("y"), ord("Y")):
            return True
        if ch in (ord("n"), ord("N"), 27):
            return False


def _draw_table(stdscr, theme: Theme, bookmarks: list[Bookmark], refresh_flags: list[bool], index: int) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    _safe_addstr(stdscr, 0, 0, "tvdinner bookmarks", theme.accent)
    _safe_addstr(stdscr, 1, 0, _HELP_LINE[: width - 1], theme.dim)
    _draw_box(stdscr, theme, 2, height - 1, width)

    content_width = max(1, width - 2)
    if not bookmarks:
        _safe_addstr(stdscr, 4, 2, "No bookmarks yet -- press 'a' to add one.", theme.normal)
        return

    name_width = max(10, min(28, content_width // 4))
    chan_width = max(6, min(12, content_width // 8))
    refresh_width = len(_REFRESH_HEADER)
    tmdb_width = len(_TMDB_HEADER)
    url_width = max(10, content_width - name_width - chan_width - refresh_width - tmdb_width - 8)
    header = (
        f"{'Description':<{name_width}} {'Channel':<{chan_width}} "
        f"{_REFRESH_HEADER:<{refresh_width}} {_TMDB_HEADER:<{tmdb_width}} {'URL'}"
    )
    _safe_addstr(stdscr, 3, 1, header[:content_width], theme.accent)
    last_row = height - 2
    for row, bookmark in enumerate(bookmarks):
        y = row + 4
        if y >= last_row:
            _safe_addstr(stdscr, last_row, 1, "(more below)", theme.dim)
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
        attr = theme.selected if row == index else theme.normal
        padded = f"{line:<{content_width}}"
        _safe_addstr(stdscr, y, 1, padded[:content_width], attr)


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
        theme = _init_theme(stdscr)
        index = 0
        while True:
            index = max(0, min(index, len(bookmarks) - 1)) if bookmarks else 0
            _draw_table(stdscr, theme, bookmarks, refresh_flags, index)
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
                new_bookmark = _prompt_bookmark_form(stdscr, theme)
                if new_bookmark is not None:
                    bookmarks.append(new_bookmark)
                    refresh_flags.append(False)
                    _save_bookmarks_safely(stdscr, theme, path, bookmarks)
                    index = len(bookmarks) - 1
                    logger.info(
                        "Bookmark added: '%s' (%s)",
                        new_bookmark.name,
                        redact_plex_url(redact_stalker_url(redact_xtream_url(new_bookmark.url))),
                    )
            elif ch == ord("e") and bookmarks:
                edited = _prompt_bookmark_form(stdscr, theme, initial=bookmarks[index])
                if edited is not None:
                    bookmarks[index] = edited
                    _save_bookmarks_safely(stdscr, theme, path, bookmarks)
                    logger.info(
                        "Bookmark edited: '%s' (%s)", edited.name, redact_plex_url(redact_stalker_url(redact_xtream_url(edited.url)))
                    )
            elif ch in (ord("d"), curses.KEY_DC) and bookmarks:
                if _confirm(stdscr, theme, f"Delete '{bookmarks[index].name}'?"):
                    deleted = bookmarks[index]
                    del bookmarks[index]
                    del refresh_flags[index]
                    _save_bookmarks_safely(stdscr, theme, path, bookmarks)
                    logger.info("Bookmark deleted: '%s'", deleted.name)

    curses.wrapper(_main)
    return selected[0]
