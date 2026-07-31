import requests

from tvdinner.plex import (
    PlexCreds,
    PlexNode,
    is_plex_url,
    list_plex_libraries,
    list_plex_node_children,
    parse_plex_url,
    redact_plex_url,
    resolve_plex_playable,
    search_plex,
)


def test_is_plex_url_recognizes_both_schemes():
    assert is_plex_url("plex://host:32400?X-Plex-Token=abc")
    assert is_plex_url("plexs://host:32400?X-Plex-Token=abc")
    assert not is_plex_url("http://example.com/playlist.m3u")
    assert not is_plex_url("xtream://user:pass@host:8080")


def test_parse_plex_url_builds_creds():
    creds = parse_plex_url("plex://192.168.0.218:32400?X-Plex-Token=abc123")
    assert creds == PlexCreds(base_url="http://192.168.0.218:32400", token="abc123")


def test_parse_plex_url_plexs_scheme_uses_https():
    creds = parse_plex_url("plexs://panel.example.com:32400?X-Plex-Token=abc123")
    assert creds.base_url == "https://panel.example.com:32400"


def test_parse_plex_url_rejects_missing_token():
    assert parse_plex_url("plex://192.168.0.218:32400") is None


def test_parse_plex_url_rejects_missing_host():
    assert parse_plex_url("plex://?X-Plex-Token=abc123") is None


def test_parse_plex_url_rejects_wrong_scheme():
    assert parse_plex_url("http://192.168.0.218:32400?X-Plex-Token=abc123") is None


def test_redact_plex_url_masks_token_keeping_first_four_chars():
    redacted = redact_plex_url("plex://192.168.0.218:32400?X-Plex-Token=abcdefgh12345678")
    assert redacted == "plex://192.168.0.218:32400?X-Plex-Token=abcd***"
    assert "efgh12345678" not in redacted


def test_redact_plex_url_masks_short_token_fully():
    redacted = redact_plex_url("plex://192.168.0.218:32400?X-Plex-Token=ab")
    assert redacted == "plex://192.168.0.218:32400?X-Plex-Token=***"


def test_redact_plex_url_leaves_non_plex_urls_unchanged():
    url = "http://example.com/playlist.m3u"
    assert redact_plex_url(url) == url


_CREDS = PlexCreds(base_url="http://panel.example.com:32400", token="tok12345678")

_SECTIONS = {
    "MediaContainer": {
        "Directory": [
            {"key": "1", "type": "movie", "title": "Movies"},
            {"key": "2", "type": "show", "title": "TV Shows"},
            {"key": "3", "type": "artist", "title": "Music"},
        ]
    }
}

_MOVIE_ITEMS = {
    "MediaContainer": {
        "Metadata": [
            {"ratingKey": "10", "title": "The Matrix", "year": 1999, "duration": 8160000},
            {"ratingKey": "11", "title": "No Year Movie"},
        ]
    }
}

_SHOW_ITEMS = {"MediaContainer": {"Metadata": [{"ratingKey": "20", "title": "Breaking Bad", "year": 2008}]}}

_SEASON_ITEMS = {"MediaContainer": {"Metadata": [{"ratingKey": "30", "title": "Season 1"}]}}

_EPISODE_ITEMS = {
    "MediaContainer": {
        "Metadata": [{"ratingKey": "40", "title": "Pilot", "parentIndex": 1, "index": 1, "duration": 3480000}]
    }
}

_MOVIE_DETAIL = {
    "MediaContainer": {
        "Metadata": [
            {
                "ratingKey": "10",
                "title": "The Matrix",
                "year": 1999,
                "Media": [{"Part": [{"key": "/library/parts/10/123/file.mkv"}]}],
            }
        ]
    }
}

_NO_PART_DETAIL = {"MediaContainer": {"Metadata": [{"ratingKey": "11", "title": "No Year Movie", "Media": []}]}}

_SEARCH_RESULT = {
    "MediaContainer": {
        # Confirmed live against a real Plex server: each Hub carries its
        # own `type`, but every Metadata item inside it also repeats that
        # same `type` on itself -- search_plex reads the per-item field.
        "Hub": [
            {
                "type": "movie",
                "Metadata": [{"type": "movie", "ratingKey": "10", "title": "The Matrix", "year": 1999, "duration": 8160000}],
            },
            {"type": "show", "Metadata": [{"type": "show", "ratingKey": "20", "title": "Breaking Bad", "year": 2008}]},
            {
                "type": "episode",
                "Metadata": [
                    {
                        "type": "episode",
                        "ratingKey": "40",
                        "title": "Pilot",
                        "grandparentTitle": "Breaking Bad",
                        "parentIndex": 1,
                        "index": 1,
                        "duration": 3480000,
                    }
                ],
            },
            {"type": "artist", "Metadata": [{"type": "artist", "ratingKey": "99", "title": "Some Band"}]},
        ]
    }
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get_for(
    sections=_SECTIONS,
    movie_items=_MOVIE_ITEMS,
    show_items=_SHOW_ITEMS,
    season_items=_SEASON_ITEMS,
    episode_items=_EPISODE_ITEMS,
    movie_detail=_MOVIE_DETAIL,
    no_part_detail=_NO_PART_DETAIL,
    search_result=_SEARCH_RESULT,
):
    def fake_get(url, params=None, headers=None, timeout=None):
        path = url.removeprefix(_CREDS.base_url)
        if path == "/library/sections":
            return _FakeResponse(sections)
        if path == "/library/sections/1/all":
            return _FakeResponse(movie_items)
        if path == "/library/sections/2/all":
            return _FakeResponse(show_items)
        if path == "/library/metadata/20/children":
            return _FakeResponse(season_items)
        if path == "/library/metadata/30/children":
            return _FakeResponse(episode_items)
        if path == "/library/metadata/10":
            return _FakeResponse(movie_detail)
        if path == "/library/metadata/11":
            return _FakeResponse(no_part_detail)
        if path == "/hubs/search":
            return _FakeResponse(search_result)
        raise AssertionError(f"unexpected path: {path}")

    return fake_get


def test_list_plex_libraries_keeps_only_movie_and_show_sections(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_libraries(_CREDS)

    assert error is None
    assert [n.kind for n in nodes] == ["library_movie", "library_show"]
    assert nodes[0] == PlexNode(rating_key="1", title="Movies", kind="library_movie", subtitle="Movies")
    assert nodes[1].subtitle == "TV Shows"


def test_list_plex_libraries_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.plex.requests.get", fail_get)

    nodes, error = list_plex_libraries(_CREDS)

    assert nodes == []
    assert "Could not reach Plex server" in error


def test_list_plex_node_children_root_lists_libraries(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, None)

    assert error is None
    assert [n.kind for n in nodes] == ["library_movie", "library_show"]


def test_list_plex_node_children_movie_library_formats_year_and_duration(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    matrix = next(n for n in nodes if n.title == "The Matrix")
    assert matrix.kind == "movie"
    assert matrix.subtitle == "1999 · 2h 16m"
    no_year = next(n for n in nodes if n.title == "No Year Movie")
    assert no_year.subtitle is None


def test_list_plex_node_children_show_library_lists_shows(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="2", title="TV Shows", kind="library_show"))

    assert error is None
    assert nodes == [PlexNode(rating_key="20", title="Breaking Bad", kind="show", subtitle="2008")]


def test_list_plex_node_children_show_lists_seasons(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="20", title="Breaking Bad", kind="show"))

    assert error is None
    assert nodes == [PlexNode(rating_key="30", title="Season 1", kind="season", subtitle=None)]


def test_list_plex_node_children_season_lists_episodes_with_sxxexx_subtitle(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="30", title="Season 1", kind="season"))

    assert error is None
    assert nodes == [PlexNode(rating_key="40", title="Pilot", kind="episode", subtitle="S01E01 · 58m")]


def test_list_plex_node_children_leaf_node_has_no_children():
    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="40", title="Pilot", kind="episode"))

    assert nodes == []
    assert "has no further items" in error


def test_list_plex_node_children_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.plex.requests.get", fail_get)

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert nodes == []
    assert "Could not reach Plex server" in error


def test_resolve_plex_playable_builds_direct_play_url(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    item, error = resolve_plex_playable(_CREDS, PlexNode(rating_key="10", title="The Matrix", kind="movie"))

    assert error is None
    assert item.title == "The Matrix"
    assert item.url == "http://panel.example.com:32400/library/parts/10/123/file.mkv?X-Plex-Token=tok12345678"
    assert item.year == "1999"


def test_resolve_plex_playable_reports_missing_part(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    item, error = resolve_plex_playable(_CREDS, PlexNode(rating_key="11", title="No Year Movie", kind="movie"))

    assert item is None
    assert "has no playable file" in error


def test_resolve_plex_playable_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.plex.requests.get", fail_get)

    item, error = resolve_plex_playable(_CREDS, PlexNode(rating_key="10", title="The Matrix", kind="movie"))

    assert item is None
    assert "Could not reach Plex server" in error


def test_search_plex_keeps_only_movie_show_episode_hubs(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = search_plex(_CREDS, "breaking")

    assert error is None
    assert [n.kind for n in nodes] == ["movie", "show", "episode"]
    episode = nodes[2]
    assert episode.subtitle == "Breaking Bad · S01E01 · 58m"


def test_search_plex_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.plex.requests.get", fail_get)

    nodes, error = search_plex(_CREDS, "breaking")

    assert nodes == []
    assert "Could not reach Plex server" in error
