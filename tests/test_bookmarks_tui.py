from tvdinner.bookmarks import Bookmark
from tvdinner.bookmarks_tui import initial_tvtimes_flags, strip_wrapping_quotes


def test_strip_wrapping_quotes_strips_matching_single_quotes():
    # The exact mistake that motivated this: copy-pasting a shell-quoted
    # example URL (as shown in this project's own docs) into a context
    # that isn't a shell and never strips the quotes -- a bookmark's URL
    # field, or a launcher/script.
    assert strip_wrapping_quotes("'hdhomerun://192.168.0.11'") == "hdhomerun://192.168.0.11"


def test_strip_wrapping_quotes_strips_matching_double_quotes():
    assert strip_wrapping_quotes('"hdhomerun://192.168.0.11"') == "hdhomerun://192.168.0.11"


def test_strip_wrapping_quotes_leaves_unquoted_text_unchanged():
    assert strip_wrapping_quotes("hdhomerun://192.168.0.11") == "hdhomerun://192.168.0.11"


def test_strip_wrapping_quotes_leaves_mismatched_quotes_unchanged():
    assert strip_wrapping_quotes("'hdhomerun://192.168.0.11\"") == "'hdhomerun://192.168.0.11\""


def test_strip_wrapping_quotes_leaves_a_single_quote_character_unchanged():
    assert strip_wrapping_quotes("'") == "'"


def test_strip_wrapping_quotes_handles_empty_string():
    assert strip_wrapping_quotes("") == ""


def test_initial_tvtimes_flags_light_up_only_tvtimes_rows():
    bookmarks = [
        Bookmark(name="Provider", url="https://example.com/playlist.m3u"),
        Bookmark(name="Home", url="tvtimes://tv.example.com?token=secret"),
        Bookmark(name="Home TLS", url="tvtimess://tv.example.com?token=secret"),
        Bookmark(name="Panel", url="xtream://user:pass@host:8080"),
    ]
    assert initial_tvtimes_flags(bookmarks) == [False, True, True, False]


def test_initial_tvtimes_flags_handles_an_empty_list():
    assert initial_tvtimes_flags([]) == []
