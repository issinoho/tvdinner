from __future__ import annotations

from tvdinner.tvtimes import (
    TvtimesFeed,
    is_tvtimes_url,
    parse_tvtimes_url,
    tvtimes_epg_url,
    tvtimes_playlist_url,
)


def test_is_tvtimes_url_matches_both_schemes():
    assert is_tvtimes_url("tvtimes://tv.example.com?token=abc")
    assert is_tvtimes_url("tvtimess://tv.example.com?token=abc")
    assert not is_tvtimes_url("https://tv.example.com/api/exports/playlist.m3u?token=abc")
    assert not is_tvtimes_url("xtream://user:pass@panel.example.com:8080")


def test_parse_tvtimes_url_http_and_https_schemes():
    assert parse_tvtimes_url("tvtimes://192.168.1.5:8888?token=abc") == TvtimesFeed(
        base_url="http://192.168.1.5:8888", token="abc"
    )
    assert parse_tvtimes_url("tvtimess://tv.example.com?token=abc") == TvtimesFeed(
        base_url="https://tv.example.com", token="abc"
    )


def test_parse_tvtimes_url_keeps_a_reverse_proxy_base_path():
    feed = parse_tvtimes_url("tvtimess://example.com/tv/?token=abc")
    assert feed == TvtimesFeed(base_url="https://example.com/tv", token="abc")
    assert tvtimes_playlist_url(feed) == "https://example.com/tv/api/exports/playlist.m3u?token=abc"


def test_parse_tvtimes_url_rejects_missing_pieces():
    # a malformed tvtimes:// URL is a usage error, not something to fall
    # back to treating as a direct stream
    assert parse_tvtimes_url("tvtimes://tv.example.com") is None  # no token
    assert parse_tvtimes_url("tvtimes://tv.example.com?token=") is None
    assert parse_tvtimes_url("tvtimes://?token=abc") is None  # no host
    assert parse_tvtimes_url("https://tv.example.com?token=abc") is None  # wrong scheme


def test_playlist_and_epg_urls_are_the_two_export_feeds():
    feed = TvtimesFeed(base_url="https://tv.example.com", token="s3cret")
    assert tvtimes_playlist_url(feed) == "https://tv.example.com/api/exports/playlist.m3u?token=s3cret"
    assert tvtimes_epg_url(feed) == "https://tv.example.com/api/exports/epg.xml?token=s3cret"


def test_token_is_url_encoded_into_the_query():
    feed = parse_tvtimes_url("tvtimess://tv.example.com?token=a%2Fb%20c")
    assert feed is not None and feed.token == "a/b c"
    assert tvtimes_playlist_url(feed).endswith("?token=a%2Fb%20c")
