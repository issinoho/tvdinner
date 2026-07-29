import requests

from tvdinner.hdhomerun import (
    HdHomeRunTarget,
    is_hdhomerun_url,
    load_hdhomerun_playlist,
    parse_hdhomerun_url,
)


def test_is_hdhomerun_url_recognizes_scheme():
    assert is_hdhomerun_url("hdhomerun://192.168.1.50")
    assert not is_hdhomerun_url("http://192.168.1.50")
    assert not is_hdhomerun_url("xtream://user:pass@host:8080")


def test_parse_hdhomerun_url_default_port():
    target = parse_hdhomerun_url("hdhomerun://192.168.1.50")
    assert target == HdHomeRunTarget(base_url="http://192.168.1.50")


def test_parse_hdhomerun_url_explicit_port():
    target = parse_hdhomerun_url("hdhomerun://192.168.1.50:8080")
    assert target.base_url == "http://192.168.1.50:8080"


def test_parse_hdhomerun_url_rejects_wrong_scheme():
    assert parse_hdhomerun_url("http://192.168.1.50") is None


def test_parse_hdhomerun_url_rejects_missing_host():
    assert parse_hdhomerun_url("hdhomerun://") is None


_TARGET = HdHomeRunTarget(base_url="http://192.168.1.50")

_DISCOVER_OK = {
    "FriendlyName": "HDHomeRun CONNECT",
    "DeviceID": "1234ABCD",
    "LineupURL": "http://192.168.1.50/lineup.json",
}

_LINEUP = [
    {"GuideNumber": "7.1", "GuideName": "KGO-HD", "URL": "http://192.168.1.50:5004/auto/v7.1"},
    {"GuideNumber": "9.1", "GuideName": "No URL Channel"},
]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def _fake_get_for(discover=_DISCOVER_OK, lineup=_LINEUP):
    def fake_get(url, timeout=None):
        if url == f"{_TARGET.base_url}/discover.json":
            return _FakeResponse(discover)
        if url == _DISCOVER_OK["LineupURL"]:
            return _FakeResponse(lineup)
        raise AssertionError(f"unexpected URL: {url}")

    return fake_get


def test_load_hdhomerun_playlist_maps_channels(monkeypatch):
    monkeypatch.setattr("tvdinner.hdhomerun.requests.get", _fake_get_for())

    playlist, error = load_hdhomerun_playlist(_TARGET)

    assert error is None
    assert playlist.epg_url is None  # no DeviceAuth in _DISCOVER_OK -- no EPG source to try
    assert len(playlist.channels) == 1  # the entry with no URL is skipped

    channel = playlist.channels[0]
    assert channel.name == "KGO-HD"
    assert channel.url == "http://192.168.1.50:5004/auto/v7.1"
    assert channel.tvg_id == "7.1"
    assert channel.tvg_logo is None
    assert channel.group_title is None


def test_load_hdhomerun_playlist_sets_epg_url_from_device_auth(monkeypatch):
    discover_with_auth = {**_DISCOVER_OK, "DeviceAuth": "abc123token"}
    monkeypatch.setattr("tvdinner.hdhomerun.requests.get", _fake_get_for(discover=discover_with_auth))

    playlist, error = load_hdhomerun_playlist(_TARGET)

    assert error is None
    assert playlist.epg_url == "https://api.hdhomerun.com/api/xmltv?DeviceAuth=abc123token"


def test_load_hdhomerun_playlist_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.hdhomerun.requests.get", fail_get)

    playlist, error = load_hdhomerun_playlist(_TARGET)

    assert playlist is None
    assert "Could not reach HDHomeRun device" in error


def test_load_hdhomerun_playlist_reports_non_hdhomerun_device(monkeypatch):
    monkeypatch.setattr("tvdinner.hdhomerun.requests.get", _fake_get_for(discover={"unrelated": "response"}))

    playlist, error = load_hdhomerun_playlist(_TARGET)

    assert playlist is None
    assert "does not look like an HDHomeRun device" in error


def test_load_hdhomerun_playlist_reports_non_json_discover_response(monkeypatch):
    monkeypatch.setattr("tvdinner.hdhomerun.requests.get", _fake_get_for(discover=None))

    playlist, error = load_hdhomerun_playlist(_TARGET)

    assert playlist is None
    assert "does not look like an HDHomeRun device" in error


def test_load_hdhomerun_playlist_reports_lineup_fetch_failure(monkeypatch):
    def fake_get(url, timeout=None):
        if url == f"{_TARGET.base_url}/discover.json":
            return _FakeResponse(_DISCOVER_OK)
        raise requests.RequestException("connection reset")

    monkeypatch.setattr("tvdinner.hdhomerun.requests.get", fake_get)

    playlist, error = load_hdhomerun_playlist(_TARGET)

    assert playlist is None
    assert "Could not reach HDHomeRun device" in error
