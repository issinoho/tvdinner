import json

import requests

from tvdinner.channel_logos import EMPTY_LOGO_INDEX, OnlineLogoIndex, load_online_logo_index

_CHANNELS = [
    {"id": "BBCOne.uk", "name": "BBC One", "alt_names": ["BBC1", "BBC Television"]},
    {"id": "NoLogo.uk", "name": "No Logo Channel", "alt_names": []},
]

_LOGOS = [
    {"channel": "BBCOne.uk", "feed": None, "in_use": True, "url": "http://logos/bbc1.png"},
]


class _FakeResponse:
    """Mimics requests.get(..., stream=True)'s context-manager response --
    channel_logos.py's fetch goes through epg.py's shared _fetch_bytes,
    which only ever pulls headers/iter_content from this."""

    def __init__(self, payload):
        self._payload = payload
        self.headers = {}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_get_for(channels=_CHANNELS, logos=_LOGOS):
    def fake_get(url, timeout=None, **kwargs):
        if "channels.json" in url:
            return _FakeResponse(channels)
        if "logos.json" in url:
            return _FakeResponse(logos)
        raise AssertionError(f"unexpected URL: {url}")

    return fake_get


def test_load_online_logo_index_matches_by_id_name_and_alt_name(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.epg.requests.get", _fake_get_for())

    index = load_online_logo_index(tmp_path)

    assert index.lookup("BBCOne.uk") == "http://logos/bbc1.png"
    assert index.lookup(None, "BBC One") == "http://logos/bbc1.png"
    assert index.lookup(None, "BBC1") == "http://logos/bbc1.png"
    assert index.lookup(None, "bbc television") == "http://logos/bbc1.png"


def test_load_online_logo_index_falls_back_to_feed_suffix_stripped_tvg_id(tmp_path, monkeypatch):
    # iptv-org's own playlists append '@SD'/'@HD'/etc. to their canonical id.
    monkeypatch.setattr("tvdinner.epg.requests.get", _fake_get_for())

    index = load_online_logo_index(tmp_path)

    assert index.lookup("BBCOne.uk@HD") == "http://logos/bbc1.png"


def test_load_online_logo_index_no_match_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.epg.requests.get", _fake_get_for())

    index = load_online_logo_index(tmp_path)

    assert index.lookup("nonexistent.id", "Totally Made Up Channel") is None
    assert index.lookup(None, "No Logo Channel") is None  # a real channel, but with no logo entry


def test_load_online_logo_index_skips_entries_not_in_use(tmp_path, monkeypatch):
    logos = [{"channel": "BBCOne.uk", "feed": None, "in_use": False, "url": "http://logos/stale.png"}]
    monkeypatch.setattr("tvdinner.epg.requests.get", _fake_get_for(logos=logos))

    index = load_online_logo_index(tmp_path)

    assert index.lookup("BBCOne.uk") is None


def test_load_online_logo_index_prefers_primary_feed_over_a_regional_one(tmp_path, monkeypatch):
    logos = [
        {"channel": "BBCOne.uk", "feed": "Wales", "in_use": True, "url": "http://logos/wales.png"},
        {"channel": "BBCOne.uk", "feed": None, "in_use": True, "url": "http://logos/primary.png"},
    ]
    monkeypatch.setattr("tvdinner.epg.requests.get", _fake_get_for(logos=logos))

    index = load_online_logo_index(tmp_path)

    assert index.lookup("BBCOne.uk") == "http://logos/primary.png"


def test_load_online_logo_index_returns_empty_index_when_cache_dir_is_none():
    assert load_online_logo_index(None) is EMPTY_LOGO_INDEX


def test_load_online_logo_index_returns_empty_index_on_fetch_failure(tmp_path, monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr("tvdinner.epg.requests.get", fail_get)

    index = load_online_logo_index(tmp_path)
    assert index.by_id == {}
    assert index.lookup("BBCOne.uk") is None


def test_online_logo_index_lookup_prefers_tvg_id_over_name():
    index = OnlineLogoIndex(by_id={"a": "http://logos/a.png"}, by_name={"b": "http://logos/b.png"})
    assert index.lookup("a", "b") == "http://logos/a.png"
