import logging

import requests

from tvdinner.series import SeriesNode
from tvdinner.xtream import (
    XtreamCreds,
    is_xtream_url,
    list_xtream_series_children,
    load_xtream_playlist,
    load_xtream_vod,
    parse_xtream_url,
    redact_xtream_url,
    resolve_xtream_series_episode,
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


_SERIES_CATEGORIES = [
    {"category_id": "20", "category_name": "Drama"},
]

_SERIES = [
    {
        "series_id": 301,
        "name": "Breaking Bad",
        "cover": "http://panel.example.com/covers/bb.png",
        "rating": "9.5",
        "releaseDate": "2008-01-20",
    },
    {
        "series_id": 302,
        "name": "No Rating Show",
        "rating": "0",
    },
]

# get_series_info&series_id=301: a whole season/episode tree in one call.
_SERIES_INFO_301 = {
    "seasons": [
        {"season_number": 1, "cover": "http://panel.example.com/covers/bb-s1.png"},
    ],
    "episodes": {
        "1": [
            {"id": "5001", "title": "Pilot", "episode_num": 1, "container_extension": "mkv"},
            {"id": "5002", "title": "Cat's in the Bag...", "episode_num": "2"},
        ],
        "2": [
            {"id": "5003", "title": "Seven Thirty-Seven", "episode_num": 1},
        ],
    },
}


def _fake_get_for(
    handshake=_HANDSHAKE_OK,
    categories=_CATEGORIES,
    streams=_STREAMS,
    vod_categories=_VOD_CATEGORIES,
    vod_streams=_VOD_STREAMS,
    series_categories=_SERIES_CATEGORIES,
    series=_SERIES,
    series_info=None,
):
    series_info = series_info if series_info is not None else {"301": _SERIES_INFO_301}

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
        if action == "get_series_categories":
            return _FakeResponse(series_categories)
        if action == "get_series":
            return _FakeResponse(series)
        if action == "get_series_info":
            return _FakeResponse(series_info.get(params.get("series_id"), {}))
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


# --- TV series browsing (list_xtream_series_children / resolve_xtream_series_episode) ---


def test_list_xtream_series_children_root_returns_categories(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())

    nodes, error = list_xtream_series_children(_CREDS, None)

    assert error is None
    assert [(n.id, n.title, n.kind) for n in nodes] == [("20", "Drama", "category")]


def test_list_xtream_series_children_root_reports_invalid_credentials(monkeypatch):
    monkeypatch.setattr(
        "tvdinner.xtream.requests.get", _fake_get_for(handshake={"user_info": {"auth": 0}})
    )

    nodes, error = list_xtream_series_children(_CREDS, None)

    assert nodes == []
    assert error == "Invalid Xtream username or password"


def test_list_xtream_series_children_category_returns_series(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())
    category = SeriesNode(id="20", title="Drama", kind="category")

    nodes, error = list_xtream_series_children(_CREDS, category)

    assert error is None
    bb, no_rating = nodes
    assert (bb.id, bb.title, bb.kind) == ("301", "Breaking Bad", "series")
    assert bb.poster_url == "http://panel.example.com/covers/bb.png"
    assert bb.rating == "9.5"
    assert bb.year == "2008"
    # rating "0" is treated as "no rating".
    assert no_rating.rating is None


def test_list_xtream_series_children_series_returns_seasons_sorted(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())
    series = SeriesNode(id="301", title="Breaking Bad", kind="series")

    nodes, error = list_xtream_series_children(_CREDS, series)

    assert error is None
    assert [(n.id, n.title, n.kind, n.subtitle) for n in nodes] == [
        ("301:1", "Season 1", "season", "2 episodes"),
        ("301:2", "Season 2", "season", "1 episode"),
    ]
    assert nodes[0].season_number == 1
    assert nodes[0].series_title == "Breaking Bad"
    assert nodes[0].poster_url == "http://panel.example.com/covers/bb-s1.png"


def test_list_xtream_series_children_season_returns_episodes_with_built_urls(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())
    season = SeriesNode(id="301:1", title="Season 1", kind="season", series_title="Breaking Bad")

    nodes, error = list_xtream_series_children(_CREDS, season)

    assert error is None
    pilot, second = nodes
    assert (pilot.title, pilot.kind, pilot.season_number, pilot.episode_number) == ("Pilot", "episode", 1, 1)
    assert pilot.subtitle == "S01E01"
    assert pilot.series_title == "Breaking Bad"
    assert pilot.url == "http://panel.example.com:8080/series/myuser/mypass/5001.mkv"
    # episode_num given as the string "2"; no container_extension -> falls back to "mp4".
    assert second.episode_number == 2
    assert second.url == "http://panel.example.com:8080/series/myuser/mypass/5002.mp4"


def test_list_xtream_series_children_rejects_malformed_season_id(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())
    season = SeriesNode(id="301:notanumber", title="Bad", kind="season")

    nodes, error = list_xtream_series_children(_CREDS, season)

    assert nodes == []
    assert "Malformed season id" in error


def test_list_xtream_series_children_episode_node_has_no_children(monkeypatch):
    monkeypatch.setattr("tvdinner.xtream.requests.get", _fake_get_for())
    episode = SeriesNode(id="5001", title="Pilot", kind="episode")

    nodes, error = list_xtream_series_children(_CREDS, episode)

    assert nodes == []
    assert "no further items" in error


def test_resolve_xtream_series_episode_builds_vod_item_from_the_node():
    episode = SeriesNode(
        id="5001",
        title="Pilot",
        kind="episode",
        year="2008",
        rating="9.5",
        series_title="Breaking Bad",
        season_number=1,
        episode_number=1,
        url="http://panel.example.com:8080/series/myuser/mypass/5001.mkv",
    )

    item, error = resolve_xtream_series_episode(_CREDS, episode)

    assert error is None
    assert item.title == "Pilot"
    assert item.url == "http://panel.example.com:8080/series/myuser/mypass/5001.mkv"
    assert item.series_title == "Breaking Bad"
    assert (item.season_number, item.episode_number) == (1, 1)


def test_resolve_xtream_series_episode_reports_a_missing_url():
    episode = SeriesNode(id="5001", title="Pilot", kind="episode")

    item, error = resolve_xtream_series_episode(_CREDS, episode)

    assert item is None
    assert "no playable URL" in error
