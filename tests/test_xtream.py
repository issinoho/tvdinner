import logging

import requests

from tvdinner.xtream import (
    XtreamCreds,
    is_xtream_url,
    load_xtream_playlist,
    load_xtream_vod,
    parse_xtream_url,
    redact_xtream_url,
    xtream_epg_url,
)


def test_is_xtream_url_recognizes_both_schemes():
    assert is_xtream_url("xtream://user:pass@host:8080")
    assert is_xtream_url("xtreams://user:pass@host:8080")
    assert not is_xtream_url("http://example.com/playlist.m3u")
    assert not is_xtream_url("/local/playlist.m3u")


def test_parse_xtream_url_basic():
    creds = parse_xtream_url("xtream://myuser:mypass@panel.example.com:8080")
    assert creds == XtreamCreds(
        base_url="http://panel.example.com:8080", username="myuser", password="mypass", output="ts"
    )


def test_parse_xtream_url_xtreams_scheme_uses_https():
    creds = parse_xtream_url("xtreams://myuser:mypass@panel.example.com:443")
    assert creds.base_url == "https://panel.example.com:443"


def test_parse_xtream_url_output_query_param_overrides_default():
    creds = parse_xtream_url("xtream://myuser:mypass@panel.example.com:8080?output=m3u8")
    assert creds.output == "m3u8"


def test_xtream_epg_url_is_deterministic_from_creds():
    creds = XtreamCreds(base_url="http://panel.example.com:8080", username="myuser", password="mypass", output="ts")
    assert xtream_epg_url(creds) == "http://panel.example.com:8080/xmltv.php?username=myuser&password=mypass"


def test_parse_xtream_url_percent_encoded_credentials_are_decoded():
    creds = parse_xtream_url("xtream://user%40x:p%40ss@panel.example.com:8080")
    assert creds.username == "user@x"
    assert creds.password == "p@ss"


def test_parse_xtream_url_rejects_wrong_scheme():
    assert parse_xtream_url("http://myuser:mypass@panel.example.com:8080") is None


def test_parse_xtream_url_rejects_missing_credentials():
    assert parse_xtream_url("xtream://panel.example.com:8080") is None


def test_parse_xtream_url_rejects_missing_host():
    assert parse_xtream_url("xtream://user:pass@") is None


def test_redact_xtream_url_masks_password():
    redacted = redact_xtream_url("xtream://myuser:mypass@panel.example.com:8080")
    assert redacted == "xtream://myuser:***@panel.example.com:8080"
    assert "mypass" not in redacted


def test_redact_xtream_url_leaves_non_xtream_urls_unchanged():
    url = "http://example.com/playlist.m3u"
    assert redact_xtream_url(url) == url


_CREDS = XtreamCreds(base_url="http://panel.example.com:8080", username="myuser", password="mypass")

_HANDSHAKE_OK = {"user_info": {"auth": 1, "status": "Active"}, "server_info": {}}

_CATEGORIES = [
    {"category_id": "1", "category_name": "News"},
    {"category_id": "2", "category_name": "Movies"},
]

_STREAMS = [
    {
        "stream_id": 101,
        "name": "BBC News",
        "category_id": "1",
        "epg_channel_id": "bbcnews.uk",
        "stream_icon": "http://panel.example.com/logos/bbc.png",
    },
    {
        "stream_id": 102,
        "name": "No Category Channel",
    },
    {
        "stream_id": 103,
        "name": "Dual Category Channel",
        "category_ids": [1, 2],
    },
]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


_VOD_CATEGORIES = [
    {"category_id": "10", "category_name": "Action"},
]

_VOD_STREAMS = [
    {
        "stream_id": 201,
        "name": "The Matrix",
        "category_id": "10",
        "stream_icon": "http://panel.example.com/covers/matrix.png",
        "rating": "8.7",
        "container_extension": "mkv",
    },
    {
        "stream_id": 202,
        "name": "No Category Movie",
    },
]


def _fake_get_for(
    handshake=_HANDSHAKE_OK,
    categories=_CATEGORIES,
    streams=_STREAMS,
    vod_categories=_VOD_CATEGORIES,
    vod_streams=_VOD_STREAMS,
):
    def fake_get(url, params=None, timeout=None):
        action = (params or {}).get("action")
        if action is None:
            return _FakeResponse(handshake)
        if action == "get_live_categories":
            return _FakeResponse(categories)
        if action == "get_live_streams":
            return _FakeResponse(streams)
        if action == "get_vod_categories":
            return _FakeResponse(vod_categories)
        if action == "get_vod_streams":
            return _FakeResponse(vod_streams)
        raise AssertionError(f"unexpected action: {action}")

    return fake_get


def test_load_xtream_playlist_maps_streams_to_channels(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())

    playlist, error = load_xtream_playlist(_CREDS)

    assert error is None
    assert playlist.epg_url == "http://panel.example.com:8080/xmltv.php?username=myuser&password=mypass"
    assert len(playlist.channels) == 3

    bbc = playlist.channels[0]
    assert bbc.name == "BBC News"
    assert bbc.url == "http://panel.example.com:8080/live/myuser/mypass/101.ts"
    assert bbc.tvg_id == "bbcnews.uk"
    assert bbc.tvg_logo == "http://panel.example.com/logos/bbc.png"
    assert bbc.group_title == "News"

    uncategorized = playlist.channels[1]
    assert uncategorized.group_title is None
    assert uncategorized.tvg_id is None

    dual = playlist.channels[2]
    assert dual.groups == ["News", "Movies"]


def test_load_xtream_playlist_honors_output_override(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())
    creds = XtreamCreds(base_url=_CREDS.base_url, username=_CREDS.username, password=_CREDS.password, output="m3u8")

    playlist, error = load_xtream_playlist(creds)

    assert error is None
    assert playlist.channels[0].url == "http://panel.example.com:8080/live/myuser/mypass/101.m3u8"


def test_load_xtream_playlist_reports_invalid_credentials(monkeypatch):
    monkeypatch.setattr(
        "tvdinner.xtream.requests.get", _fake_get_for(handshake={"user_info": {"auth": 0}})
    )

    playlist, error = load_xtream_playlist(_CREDS)

    assert playlist is None
    assert error == "Invalid Xtream username or password"


def test_load_xtream_playlist_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.xtream.requests.get", fail_get)

    playlist, error = load_xtream_playlist(_CREDS)

    assert playlist is None
    assert "Could not reach Xtream server" in error


def test_load_xtream_playlist_expired_account_still_loads_with_warning(monkeypatch, caplog):
    monkeypatch.setattr(
        "tvdinner.xtream.requests.get",
        _fake_get_for(handshake={"user_info": {"auth": 1, "status": "Expired"}}),
    )

    with caplog.at_level(logging.WARNING):
        playlist, error = load_xtream_playlist(_CREDS)

    assert error is None
    assert len(playlist.channels) == 3
    assert any("Expired" in record.message for record in caplog.records)


def test_load_xtream_vod_maps_streams_to_vod_items(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())

    items, error = load_xtream_vod(_CREDS)

    assert error is None
    assert len(items) == 2

    matrix = items[0]
    assert matrix.title == "The Matrix"
    assert matrix.url == "http://panel.example.com:8080/movie/myuser/mypass/201.mkv"
    assert matrix.group_title == "Action"
    assert matrix.poster_url == "http://panel.example.com/covers/matrix.png"
    assert matrix.rating == "8.7"

    uncategorized = items[1]
    assert uncategorized.title == "No Category Movie"
    assert uncategorized.group_title is None
    # No container_extension given -- falls back to "mp4".
    assert uncategorized.url == "http://panel.example.com:8080/movie/myuser/mypass/202.mp4"


def test_load_xtream_vod_reports_invalid_credentials(monkeypatch):
    monkeypatch.setattr(
        "tvdinner.xtream.requests.get", _fake_get_for(handshake={"user_info": {"auth": 0}})
    )

    items, error = load_xtream_vod(_CREDS)

    assert items == []
    assert error == "Invalid Xtream username or password"


def test_load_xtream_vod_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.xtream.requests.get", fail_get)

    items, error = load_xtream_vod(_CREDS)

    assert items == []
    assert "Could not reach Xtream server" in error
