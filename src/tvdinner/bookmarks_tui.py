"""Interactive curses picker for tvdinner's saved bookmarks (`tvdinner
bookmarks`): a table of saved playlists you can add to, edit, delete, and
select from to launch playback -- see tvdinner.bookmarks for the on-disk
format this reads/writes.
"""

from __future__ import annotations

import curses
from pathlib import Path

from tvdinner.bookmarks import Bookmark, load_bookmarks, save_bookmarks

_HELP_LINE = "ENTER play   a add   e edit   d delete   q quit"


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
    """Prompt for name/url/epg in sequence, pre-filled from `initial` when
    editing. Cancelling (ESC) at any field abandons the whole form."""
    stdscr.erase()
    _safe_addstr(stdscr, 0, 0, "ESC at any point cancels", curses.A_DIM)

    name = _edit_field(stdscr, 2, "Description: ", initial.name if initial else "")
    if name is None:
        return None
    url = _edit_field(stdscr, 3, "M3U URL: ", initial.url if initial else "")
    if url is None:
        return None
    epg = _edit_field(stdscr, 4, "EPG URL (optional): ", (initial.epg or "") if initial else "")
    if epg is None:
        return None

    name, url, epg = name.strip(), url.strip(), epg.strip()
    if not name or not url:
        return None
    return Bookmark(name=name, url=url, epg=epg or None)


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


def _draw_table(stdscr, bookmarks: list[Bookmark], index: int) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    _safe_addstr(stdscr, 0, 0, "tvdinner bookmarks", curses.A_BOLD)
    _safe_addstr(stdscr, 1, 0, _HELP_LINE[: width - 1], curses.A_DIM)

    if not bookmarks:
        _safe_addstr(stdscr, 3, 2, "No bookmarks yet -- press 'a' to add one.")
        return

    name_width = max(10, min(30, width // 3))
    url_width = max(10, width - name_width - 3)
    for row, bookmark in enumerate(bookmarks):
        y = row + 3
        if y >= height - 1:
            _safe_addstr(stdscr, height - 1, 0, "(more below)", curses.A_DIM)
            break
        line = f"{bookmark.name[:name_width]:<{name_width}} {bookmark.url[:url_width]}"
        attr = curses.A_REVERSE if row == index else curses.A_NORMAL
        _safe_addstr(stdscr, y, 0, line[: width - 1], attr)


def run_bookmarks_tui(path: Path) -> Bookmark | None:
    """Show the interactive bookmarks table. Returns the Bookmark the user
    selected to launch (ENTER), or None if they quit ('q'/ESC) without
    selecting one. Add/edit/delete save to `path` immediately."""
    bookmarks, warnings = load_bookmarks(path)
    for warning in warnings:
        print(f"Warning: {warning}")

    selected: list[Bookmark | None] = [None]

    def _main(stdscr) -> None:
        curses.curs_set(0)
        index = 0
        while True:
            index = max(0, min(index, len(bookmarks) - 1)) if bookmarks else 0
            _draw_table(stdscr, bookmarks, index)
            stdscr.refresh()

            ch = stdscr.getch()
            if ch in (ord("q"), 27):
                return
            if ch == curses.KEY_UP and bookmarks:
                index = (index - 1) % len(bookmarks)
            elif ch == curses.KEY_DOWN and bookmarks:
                index = (index + 1) % len(bookmarks)
            elif ch in (curses.KEY_ENTER, 10, 13) and bookmarks:
                selected[0] = bookmarks[index]
                return
            elif ch == ord("a"):
                new_bookmark = _prompt_bookmark_form(stdscr)
                if new_bookmark is not None:
                    bookmarks.append(new_bookmark)
                    save_bookmarks(path, bookmarks)
                    index = len(bookmarks) - 1
            elif ch == ord("e") and bookmarks:
                edited = _prompt_bookmark_form(stdscr, initial=bookmarks[index])
                if edited is not None:
                    bookmarks[index] = edited
                    save_bookmarks(path, bookmarks)
            elif ch in (ord("d"), curses.KEY_DC) and bookmarks:
                if _confirm(stdscr, f"Delete '{bookmarks[index].name}'?"):
                    del bookmarks[index]
                    save_bookmarks(path, bookmarks)

    curses.wrapper(_main)
    return selected[0]
