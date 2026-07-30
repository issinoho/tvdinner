import requests

from tvdinner.stalker import (
    StalkerCreds,
    is_stalker_url,
    load_stalker_playlist,
    load_stalker_vod,
    parse_stalker_url,
    redact_stalker_url,
)


def test_is_stalker_url_recognizes_both_schemes():
    assert is_stalker_url("stalker://host:8080/c/?mac=AA:BB:CC:DD:EE:FF")
    assert is_stalker_url("stalkers://host:8080/c/?mac=AA:BB:CC:DD:EE:FF")
    assert not is_stalker_url("http://example.com/playlist.m3u")
    assert not is_stalker_url("xtream://user:pass@host:8080")


def test_parse_stalker_url_appends_portal_php_when_missing():
    creds = parse_stalker_url("stalker://panel.example.com:8080/stalker_portal/c/?mac=AA:BB:CC:DD:EE:FF")
    assert creds == StalkerCreds(
        base_url="http://panel.example.com:8080",
        portal_path="/stalker_portal/c/portal.php",
        mac="AA:BB:CC:DD:EE:FF",
    )


def test_parse_stalker_url_keeps_explicit_php_path():
    creds = parse_stalker_url("stalker://panel.example.com:8080/portal.php?mac=AA:BB:CC:DD:EE:FF")
    assert creds.portal_path == "/portal.php"


def test_parse_stalker_url_defaults_path_when_missing():
    creds = parse_stalker_url("stalker://panel.example.com:8080?mac=AA:BB:CC:DD:EE:FF")
    assert creds.portal_path == "/portal.php"


def test_parse_stalker_url_stalkers_scheme_uses_https():
    creds = parse_stalker_url("stalkers://panel.example.com:443/c/?mac=AA:BB:CC:DD:EE:FF")
    assert creds.base_url == "https://panel.example.com:443"


def test_parse_stalker_url_optional_overrides():
    creds = parse_stalker_url(
        "stalker://panel.example.com:8080/c/?mac=AA:BB:CC:DD:EE:FF&serial=SN123&device_id=DEV456&stb_type=MAG254"
    )
    assert creds.serial == "SN123"
    assert creds.device_id == "DEV456"
    assert creds.stb_type == "MAG254"


def test_parse_stalker_url_rejects_missing_mac():
    assert parse_stalker_url("stalker://panel.example.com:8080/c/") is None


def test_parse_stalker_url_rejects_malformed_mac():
    assert parse_stalker_url("stalker://panel.example.com:8080/c/?mac=not-a-mac") is None


def test_parse_stalker_url_rejects_wrong_scheme():
    assert parse_stalker_url("http://panel.example.com:8080/c/?mac=AA:BB:CC:DD:EE:FF") is None


def test_redact_stalker_url_masks_all_but_first_two_octets():
    redacted = redact_stalker_url("stalker://panel.example.com:8080/c/?mac=AA:BB:CC:DD:EE:FF")
    assert redacted == "stalker://panel.example.com:8080/c/?mac=AA:BB:**:**:**:**"
    assert "CC:DD:EE:FF" not in redacted


def test_redact_stalker_url_leaves_non_stalker_urls_unchanged():
    url = "http://example.com/playlist.m3u"
    assert redact_stalker_url(url) == url


_CREDS = StalkerCreds(base_url="http://panel.example.com:8080", portal_path="/portal.php", mac="AA:BB:CC:DD:EE:FF")

_HANDSHAKE_OK = {"js": {"token": "abc123"}}
_GENRES = {"js": [{"id": "1", "title": "News"}, {"id": "2", "title": "Movies"}]}

_ALL_CHANNELS = {
    "js": {
        "data": [
            {
                "id": "101",
                "name": "BBC News",
                "cmd": "ffmpeg http://localhost/ch/101_",
                "tv_genre_id": "1",
                "logo": "/images/bbc.png",
                "xmltv_id": "bbcnews.uk",
            },
            {
                "id": "102",
                "name": "No Genre Channel",
                "cmd": "ffmpeg http://localhost/ch/102_",
            },
            {
                "id": "103",
                "name": "Absolute Logo Channel",
                "cmd": "ffmpeg http://localhost/ch/103_",
                "logo": "http://cdn.example.com/logo.png",
            },
        ]
    }
}

_CREATE_LINKS = {
    "ffmpeg http://localhost/ch/101_": {"js": {"cmd": "ffmpeg http://stream.example.com/101/index.m3u8"}},
    "ffmpeg http://localhost/ch/102_": {"js": {"cmd": "http://stream.example.com/102/index.m3u8"}},
    "ffmpeg http://localhost/ch/103_": {"js": {"cmd": "http://stream.example.com/103/index.m3u8"}},
}

_VOD_CATEGORIES = {"js": [{"id": "20", "title": "Action"}]}

_VOD_ORDERED_PAGE_1 = {
    "js": {
        "data": [
            {
                "id": "301",
                "name": "The Matrix",
                "cmd": "ffmpeg http://localhost/vod/301_",
                "category_id": "20",
                "screenshot_uri": "/images/matrix.jpg",
                "year": "1999",
            },
            {
                "id": "302",
                "name": "No Category Movie",
                "cmd": "ffmpeg http://localhost/vod/302_",
            },
        ],
        "total_items": 2,
    }
}

_VOD_CREATE_LINKS = {
    "ffmpeg http://localhost/vod/301_": {"js": {"cmd": "http://stream.example.com/vod/301/index.m3u8"}},
    "ffmpeg http://localhost/vod/302_": {"js": {"cmd": "http://stream.example.com/vod/302/index.m3u8"}},
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get_for(
    handshake=_HANDSHAKE_OK,
    genres=_GENRES,
    all_channels=_ALL_CHANNELS,
    create_links=None,
    ordered_pages=None,
    vod_categories=_VOD_CATEGORIES,
    vod_ordered_pages=None,
):
    create_links = create_links if create_links is not None else {**_CREATE_LINKS, **_VOD_CREATE_LINKS}
    vod_ordered_pages = vod_ordered_pages if vod_ordered_pages is not None else {1: _VOD_ORDERED_PAGE_1}

    def fake_get(url, params=None, headers=None, timeout=None):
        params = params or {}
        action = params.get("action")
        if action == "handshake":
            return _FakeResponse(handshake)
        if action == "get_profile":
            return _FakeResponse({"js": {}})
        if action == "get_genres":
            return _FakeResponse(genres)
        if action == "get_categories":
            return _FakeResponse(vod_categories)
        if action == "get_all_channels":
            return _FakeResponse(all_channels)
        if action == "get_ordered_list":
            pages = vod_ordered_pages if params.get("type") == "vod" else (ordered_pages or {})
            page = int(params.get("p", "1"))
            return _FakeResponse(pages.get(page, {"js": {"data": [], "total_items": 0}}))
        if action == "create_link":
            cmd = params.get("cmd")
            return _FakeResponse(create_links.get(cmd, {"js": {}}))
        raise AssertionError(f"unexpected action: {action}")

    return fake_get


def test_load_stalker_playlist_maps_channels(monkeypatch):
    monkeypatch.setattr("tvdinner.stalker.requests.get", _fake_get_for())

    playlist, error = load_stalker_playlist(_CREDS)

    assert error is None
    assert len(playlist.channels) == 3
    assert playlist.epg_url is None

    bbc = playlist.channels[0]
    assert bbc.name == "BBC News"
    assert bbc.url == "http://stream.example.com/101/index.m3u8"
    assert bbc.tvg_id == "bbcnews.uk"
    assert bbc.tvg_logo == "http://panel.example.com:8080/images/bbc.png"
    assert bbc.group_title == "News"

    no_genre = playlist.channels[1]
    assert no_genre.group_title is None
    assert no_genre.tvg_id is None

    absolute_logo = playlist.channels[2]
    assert absolute_logo.tvg_logo == "http://cdn.example.com/logo.png"


def test_load_stalker_playlist_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.stalker.requests.get", fail_get)

    playlist, error = load_stalker_playlist(_CREDS)

    assert playlist is None
    assert "Could not reach Stalker portal" in error


def test_load_stalker_playlist_reports_missing_token(monkeypatch):
    monkeypatch.setattr("tvdinner.stalker.requests.get", _fake_get_for(handshake={"js": {}}))

    playlist, error = load_stalker_playlist(_CREDS)

    assert playlist is None
    assert "no token returned" in error


def test_load_stalker_playlist_skips_channel_whose_create_link_fails(monkeypatch):
    broken_links = dict(_CREATE_LINKS)
    del broken_links["ffmpeg http://localhost/ch/102_"]  # create_link now returns no cmd for this one

    monkeypatch.setattr("tvdinner.stalker.requests.get", _fake_get_for(create_links=broken_links))

    playlist, error = load_stalker_playlist(_CREDS)

    assert error is None
    names = {channel.name for channel in playlist.channels}
    assert names == {"BBC News", "Absolute Logo Channel"}


def test_load_stalker_playlist_falls_back_to_paginated_ordered_list(monkeypatch):
    empty_all_channels = {"js": {"data": []}}
    ordered_pages = {
        1: {"js": {"data": [_ALL_CHANNELS["js"]["data"][0]], "total_items": 2}},
        2: {"js": {"data": [_ALL_CHANNELS["js"]["data"][1]], "total_items": 2}},
    }
    monkeypatch.setattr(
        "tvdinner.stalker.requests.get",
        _fake_get_for(all_channels=empty_all_channels, ordered_pages=ordered_pages),
    )

    playlist, error = load_stalker_playlist(_CREDS)

    assert error is None
    names = {channel.name for channel in playlist.channels}
    assert names == {"BBC News", "No Genre Channel"}


def test_load_stalker_vod_maps_items(monkeypatch):
    monkeypatch.setattr("tvdinner.stalker.requests.get", _fake_get_for())

    items, error = load_stalker_vod(_CREDS)

    assert error is None
    assert len(items) == 2

    matrix = next(item for item in items if item.title == "The Matrix")
    assert matrix.url == "http://stream.example.com/vod/301/index.m3u8"
    assert matrix.group_title == "Action"
    assert matrix.poster_url == "http://panel.example.com:8080/images/matrix.jpg"
    assert matrix.year == "1999"

    no_category = next(item for item in items if item.title == "No Category Movie")
    assert no_category.group_title is None


def test_load_stalker_vod_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.stalker.requests.get", fail_get)

    items, error = load_stalker_vod(_CREDS)

    assert items == []
    assert "Could not reach Stalker portal" in error


def test_load_stalker_vod_reports_missing_token(monkeypatch):
    monkeypatch.setattr("tvdinner.stalker.requests.get", _fake_get_for(handshake={"js": {}}))

    items, error = load_stalker_vod(_CREDS)

    assert items == []
    assert "no token returned" in error


def test_load_stalker_vod_skips_item_whose_create_link_fails(monkeypatch):
    broken_links = {**_CREATE_LINKS, **_VOD_CREATE_LINKS}
    del broken_links["ffmpeg http://localhost/vod/302_"]

    monkeypatch.setattr("tvdinner.stalker.requests.get", _fake_get_for(create_links=broken_links))

    items, error = load_stalker_vod(_CREDS)

    assert error is None
    titles = {item.title for item in items}
    assert titles == {"The Matrix"}
