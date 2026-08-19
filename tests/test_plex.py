import json

import pytest
import requests

from tvdinner.plex import (
    PlexCreds,
    PlexNode,
    is_plex_url,
    list_plex_libraries,
    list_plex_node_children,
    load_plex_client_id,
    parse_plex_url,
    redact_plex_url,
    report_plex_timeline,
    resolve_plex_playable,
    search_plex,
    search_plex_by_year,
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
                "summary": "A hacker discovers reality is a simulation.",
                "thumb": "/library/metadata/10/thumb/123",
                "audienceRating": 8.7,
                "Media": [{"Part": [{"key": "/library/parts/10/123/file.mkv"}]}],
                "Director": [{"tag": "Lana Wachowski"}, {"tag": "Lilly Wachowski"}],
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


_EMPTY_METADATA = {"MediaContainer": {"Metadata": []}}


def _fake_get_for(
    sections=_SECTIONS,
    movie_items=_MOVIE_ITEMS,
    show_items=_SHOW_ITEMS,
    season_items=_SEASON_ITEMS,
    episode_items=_EPISODE_ITEMS,
    movie_detail=_MOVIE_DETAIL,
    no_part_detail=_NO_PART_DETAIL,
    search_result=_SEARCH_RESULT,
    on_deck=_EMPTY_METADATA,
    year_movies=_EMPTY_METADATA,
    year_shows=_EMPTY_METADATA,
    year_episodes=_EMPTY_METADATA,
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
        if path == "/library/onDeck":
            return _FakeResponse(on_deck)
        if path == "/library/all":
            year_type = (params or {}).get("type")
            if year_type == "1":
                return _FakeResponse(year_movies)
            if year_type == "2":
                return _FakeResponse(year_shows)
            if year_type == "4":
                return _FakeResponse(year_episodes)
            raise AssertionError(f"unexpected /library/all type: {year_type}")
        raise AssertionError(f"unexpected path: {path}")

    return fake_get


def test_list_plex_libraries_prepends_continue_watching_row(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_libraries(_CREDS)

    assert error is None
    assert nodes[0] == PlexNode(
        rating_key="continue_watching", title="On Deck", kind="continue_watching", subtitle="In progress & up next"
    )


def test_list_plex_libraries_keeps_only_movie_and_show_sections(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_libraries(_CREDS)

    assert error is None
    assert [n.kind for n in nodes] == ["continue_watching", "library_movie", "library_show"]
    assert nodes[1] == PlexNode(rating_key="1", title="Movies", kind="library_movie", subtitle="Movies")
    assert nodes[2].subtitle == "TV Shows"


def test_list_plex_libraries_includes_thumb_url_when_present(monkeypatch):
    sections = {
        "MediaContainer": {
            "Directory": [{"key": "1", "type": "movie", "title": "Movies", "thumb": "/library/sections/1/composite"}]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(sections=sections))

    nodes, error = list_plex_libraries(_CREDS)

    assert error is None
    assert nodes[1].thumb_url == "http://panel.example.com:32400/library/sections/1/composite?X-Plex-Token=tok12345678"


def test_list_plex_libraries_falls_back_to_composite_when_no_thumb(monkeypatch):
    # A library section with no thumb of its own -- confirmed live
    # against a real server that a folder-only library returns neither
    # field, but Plex's docs describe `composite` as the auto-generated
    # 4-poster collage a section can have instead.
    sections = {
        "MediaContainer": {
            "Directory": [{"key": "1", "type": "movie", "title": "Movies", "composite": "/library/sections/1/composite/456"}]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(sections=sections))

    nodes, error = list_plex_libraries(_CREDS)

    assert error is None
    assert nodes[1].thumb_url == "http://panel.example.com:32400/library/sections/1/composite/456?X-Plex-Token=tok12345678"


def test_list_plex_libraries_prefers_thumb_over_composite(monkeypatch):
    sections = {
        "MediaContainer": {
            "Directory": [
                {
                    "key": "1",
                    "type": "movie",
                    "title": "Movies",
                    "thumb": "/library/sections/1/thumb/789",
                    "composite": "/library/sections/1/composite/456",
                }
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(sections=sections))

    nodes, error = list_plex_libraries(_CREDS)

    assert error is None
    assert nodes[1].thumb_url == "http://panel.example.com:32400/library/sections/1/thumb/789?X-Plex-Token=tok12345678"


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
    assert [n.kind for n in nodes] == ["continue_watching", "library_movie", "library_show"]


def test_list_plex_node_children_continue_watching_lists_movies_and_episodes(monkeypatch):
    on_deck = {
        "MediaContainer": {
            "Metadata": [
                {"type": "movie", "ratingKey": "10", "title": "The Matrix", "viewOffset": 2040000, "duration": 8160000},
                {
                    "type": "episode",
                    "ratingKey": "40",
                    "title": "Pilot",
                    "grandparentTitle": "Breaking Bad",
                    "parentIndex": 1,
                    "index": 1,
                },
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(on_deck=on_deck))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="continue_watching", title="On Deck", kind="continue_watching"))

    assert error is None
    assert [(n.kind, n.title) for n in nodes] == [("movie", "The Matrix"), ("episode", "Pilot")]
    assert nodes[0].watch_progress == pytest.approx(0.25)
    assert nodes[1].subtitle == "Breaking Bad · S01E01"


def test_list_plex_node_children_continue_watching_skips_shows(monkeypatch):
    # Confirmed against Plex's own docs/behavior: onDeck never returns a
    # show itself, only movies and the specific next-up episode -- this
    # guards that assumption regardless.
    on_deck = {"MediaContainer": {"Metadata": [{"type": "show", "ratingKey": "20", "title": "Breaking Bad"}]}}
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(on_deck=on_deck))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="continue_watching", title="On Deck", kind="continue_watching"))

    assert error is None
    assert nodes == []


def test_list_plex_node_children_continue_watching_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.plex.requests.get", fail_get)

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="continue_watching", title="On Deck", kind="continue_watching"))

    assert nodes == []
    assert "Could not reach Plex server" in error


def test_list_plex_node_children_movie_library_formats_year_and_duration(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    matrix = next(n for n in nodes if n.title == "The Matrix")
    assert matrix.kind == "movie"
    assert matrix.subtitle == "1999 · 2h 16m"
    no_year = next(n for n in nodes if n.title == "No Year Movie")
    assert no_year.subtitle is None


def test_list_plex_node_children_movie_subtitle_includes_content_rating_and_audience_score(monkeypatch):
    movie_items = {
        "MediaContainer": {
            "Metadata": [
                {
                    "ratingKey": "10",
                    "title": "The Matrix",
                    "year": 1999,
                    "contentRating": "R",
                    "audienceRating": 8.7,
                    "duration": 8160000,
                }
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(movie_items=movie_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    assert nodes[0].subtitle == "1999 · R · ★ 8.7 · 2h 16m"


@pytest.mark.parametrize(
    "video_resolution, expected_badge",
    [("1080", "1080p"), ("720", "720p"), ("480", "480p"), ("sd", "SD"), ("4k", "4K")],
)
def test_list_plex_node_children_movie_subtitle_includes_resolution_badge(monkeypatch, video_resolution, expected_badge):
    movie_items = {
        "MediaContainer": {
            "Metadata": [
                {
                    "ratingKey": "10",
                    "title": "The Matrix",
                    "Media": [{"videoResolution": video_resolution}],
                }
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(movie_items=movie_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    assert nodes[0].subtitle == expected_badge


def test_list_plex_node_children_movie_subtitle_omits_resolution_without_media(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    matrix = next(n for n in nodes if n.title == "The Matrix")
    assert matrix.subtitle == "1999 · 2h 16m"


def test_list_plex_node_children_episode_subtitle_includes_resolution_badge(monkeypatch):
    episode_items = {
        "MediaContainer": {
            "Metadata": [
                {
                    "ratingKey": "40",
                    "title": "Pilot",
                    "parentIndex": 1,
                    "index": 1,
                    "duration": 3480000,
                    "Media": [{"videoResolution": "sd"}],
                }
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(episode_items=episode_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="30", title="Season 1", kind="season"))

    assert error is None
    assert nodes[0].subtitle == "S01E01 · SD · 58m"


def test_list_plex_node_children_movie_includes_thumb_url_when_present(monkeypatch):
    movie_items = {
        "MediaContainer": {
            "Metadata": [
                {"ratingKey": "10", "title": "The Matrix", "year": 1999, "thumb": "/library/metadata/10/thumb/123"}
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(movie_items=movie_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    assert nodes[0].thumb_url == "http://panel.example.com:32400/library/metadata/10/thumb/123?X-Plex-Token=tok12345678"


def test_list_plex_node_children_movie_thumb_url_is_none_without_a_thumb(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    assert nodes[0].thumb_url is None


def test_list_plex_node_children_movie_watched_from_view_count(monkeypatch):
    movie_items = {
        "MediaContainer": {"Metadata": [{"ratingKey": "10", "title": "The Matrix", "viewCount": 2}]}
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(movie_items=movie_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    assert nodes[0].watched is True
    assert nodes[0].watch_progress is None


def test_list_plex_node_children_movie_in_progress_from_view_offset(monkeypatch):
    movie_items = {
        "MediaContainer": {
            "Metadata": [{"ratingKey": "10", "title": "The Matrix", "viewOffset": 2040000, "duration": 8160000}]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(movie_items=movie_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    assert nodes[0].watched is False
    assert nodes[0].watch_progress == pytest.approx(0.25)


def test_list_plex_node_children_movie_unwatched_by_default(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="1", title="Movies", kind="library_movie"))

    assert error is None
    matrix = next(n for n in nodes if n.title == "The Matrix")
    assert matrix.watched is False
    assert matrix.watch_progress is None


def test_list_plex_node_children_episode_in_progress_from_view_offset(monkeypatch):
    episode_items = {
        "MediaContainer": {
            "Metadata": [
                {
                    "ratingKey": "40",
                    "title": "Pilot",
                    "parentIndex": 1,
                    "index": 1,
                    "viewOffset": 1740000,
                    "duration": 3480000,
                }
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(episode_items=episode_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="30", title="Season 1", kind="season"))

    assert error is None
    assert nodes[0].watched is False
    assert nodes[0].watch_progress == pytest.approx(0.5)


def test_list_plex_node_children_show_library_lists_shows(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="2", title="TV Shows", kind="library_show"))

    assert error is None
    assert nodes == [PlexNode(rating_key="20", title="Breaking Bad", kind="show", subtitle="2008")]


def test_list_plex_node_children_show_subtitle_includes_content_rating_and_audience_score(monkeypatch):
    show_items = {
        "MediaContainer": {
            "Metadata": [
                {"ratingKey": "20", "title": "Breaking Bad", "year": 2008, "contentRating": "TV-MA", "audienceRating": 9.4}
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(show_items=show_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="2", title="TV Shows", kind="library_show"))

    assert error is None
    assert nodes[0].subtitle == "2008 · TV-MA · ★ 9.4"


def test_list_plex_node_children_show_lists_seasons(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="20", title="Breaking Bad", kind="show"))

    assert error is None
    assert nodes == [PlexNode(rating_key="30", title="Season 1", kind="season", subtitle=None)]


def test_list_plex_node_children_show_watched_from_leaf_count_rollup(monkeypatch):
    show_items = {
        "MediaContainer": {"Metadata": [{"ratingKey": "20", "title": "Breaking Bad", "leafCount": 8, "viewedLeafCount": 8}]}
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(show_items=show_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="2", title="TV Shows", kind="library_show"))

    assert error is None
    assert nodes[0].watched is True
    assert nodes[0].watch_progress is None


def test_list_plex_node_children_show_in_progress_from_leaf_count_rollup(monkeypatch):
    show_items = {
        "MediaContainer": {"Metadata": [{"ratingKey": "20", "title": "Breaking Bad", "leafCount": 8, "viewedLeafCount": 2}]}
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(show_items=show_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="2", title="TV Shows", kind="library_show"))

    assert error is None
    assert nodes[0].watched is False
    assert nodes[0].watch_progress == pytest.approx(0.25)


def test_list_plex_node_children_season_watched_from_leaf_count_rollup(monkeypatch):
    season_items = {
        "MediaContainer": {"Metadata": [{"ratingKey": "30", "title": "Season 1", "leafCount": 4, "viewedLeafCount": 4}]}
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(season_items=season_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="20", title="Breaking Bad", kind="show"))

    assert error is None
    assert nodes[0].watched is True


def test_list_plex_node_children_season_lists_episodes_with_sxxexx_subtitle(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="30", title="Season 1", kind="season"))

    assert error is None
    assert nodes == [PlexNode(rating_key="40", title="Pilot", kind="episode", subtitle="S01E01 · 58m")]


def test_list_plex_node_children_episode_includes_thumb_url_when_present(monkeypatch):
    episode_items = {
        "MediaContainer": {
            "Metadata": [
                {
                    "ratingKey": "40",
                    "title": "Pilot",
                    "parentIndex": 1,
                    "index": 1,
                    "thumb": "/library/metadata/40/thumb/1",
                }
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(episode_items=episode_items))

    nodes, error = list_plex_node_children(_CREDS, PlexNode(rating_key="30", title="Season 1", kind="season"))

    assert error is None
    assert nodes[0].thumb_url == "http://panel.example.com:32400/library/metadata/40/thumb/1?X-Plex-Token=tok12345678"


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
    assert item.rating == "8.7"
    assert item.description == "A hacker discovers reality is a simulation."
    assert item.poster_url == "http://panel.example.com:32400/library/metadata/10/thumb/123?X-Plex-Token=tok12345678"
    assert item.director == "Lana Wachowski, Lilly Wachowski"
    assert item.rating_key == "10"
    assert item.backdrop_url is None


def test_resolve_plex_playable_includes_backdrop_url_when_plex_has_art(monkeypatch):
    detail = {"MediaContainer": {"Metadata": [{**_MOVIE_DETAIL["MediaContainer"]["Metadata"][0], "art": "/library/metadata/10/art/456"}]}}
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(movie_detail=detail))

    item, error = resolve_plex_playable(_CREDS, PlexNode(rating_key="10", title="The Matrix", kind="movie"))

    assert error is None
    assert item.backdrop_url == "http://panel.example.com:32400/library/metadata/10/art/456?X-Plex-Token=tok12345678"


def test_resolve_plex_playable_leaves_director_none_when_plex_has_no_director_field(monkeypatch):
    detail = {"MediaContainer": {"Metadata": [{**_MOVIE_DETAIL["MediaContainer"]["Metadata"][0]}]}}
    del detail["MediaContainer"]["Metadata"][0]["Director"]
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(movie_detail=detail))

    item, error = resolve_plex_playable(_CREDS, PlexNode(rating_key="10", title="The Matrix", kind="movie"))

    assert error is None
    assert item.director is None


def test_resolve_plex_playable_includes_resume_seconds_from_view_offset(monkeypatch):
    detail = {"MediaContainer": {"Metadata": [{**_MOVIE_DETAIL["MediaContainer"]["Metadata"][0], "viewOffset": 2040000}]}}
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(movie_detail=detail))

    item, error = resolve_plex_playable(_CREDS, PlexNode(rating_key="10", title="The Matrix", kind="movie"))

    assert error is None
    assert item.resume_seconds == pytest.approx(2040.0)


def test_resolve_plex_playable_leaves_resume_seconds_none_without_view_offset(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    item, error = resolve_plex_playable(_CREDS, PlexNode(rating_key="10", title="The Matrix", kind="movie"))

    assert error is None
    assert item.resume_seconds is None


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


def test_search_plex_includes_thumb_url_when_present(monkeypatch):
    search_result = {
        "MediaContainer": {
            "Hub": [
                {
                    "type": "movie",
                    "Metadata": [
                        {
                            "type": "movie",
                            "ratingKey": "10",
                            "title": "The Matrix",
                            "thumb": "/library/metadata/10/thumb/123",
                        }
                    ],
                }
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(search_result=search_result))

    nodes, error = search_plex(_CREDS, "matrix")

    assert error is None
    assert nodes[0].thumb_url == "http://panel.example.com:32400/library/metadata/10/thumb/123?X-Plex-Token=tok12345678"


def test_search_plex_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.plex.requests.get", fail_get)

    nodes, error = search_plex(_CREDS, "breaking")

    assert nodes == []
    assert "Could not reach Plex server" in error


_YEAR_MOVIES = {
    "MediaContainer": {
        "Metadata": [
            {"ratingKey": "10", "title": "The Matrix", "year": 1999},
            {"ratingKey": "11", "title": "American Beauty", "year": 1999},
        ]
    }
}

_YEAR_SHOWS = {"MediaContainer": {"Metadata": [{"ratingKey": "20", "title": "Zeta", "year": 1999}]}}


_YEAR_EPISODES = {
    "MediaContainer": {
        "Metadata": [
            {
                "ratingKey": "40",
                "title": "Balance of Power",
                "grandparentTitle": "Red Dwarf",
                "parentIndex": 1,
                "index": 3,
            }
        ]
    }
}


def test_search_plex_by_year_combines_and_sorts_movies_shows_and_episodes(monkeypatch):
    monkeypatch.setattr(
        "tvdinner.plex.requests.get",
        _fake_get_for(year_movies=_YEAR_MOVIES, year_shows=_YEAR_SHOWS, year_episodes=_YEAR_EPISODES),
    )

    nodes, error = search_plex_by_year(_CREDS, "1999")

    assert error is None
    assert [n.title for n in nodes] == ["American Beauty", "Balance of Power", "The Matrix", "Zeta"]
    assert [n.kind for n in nodes] == ["movie", "episode", "movie", "show"]
    episode = next(n for n in nodes if n.kind == "episode")
    assert episode.subtitle == "Red Dwarf · S01E03"


def test_search_plex_by_year_queries_episodes_by_air_date_range_not_year(monkeypatch):
    # Confirmed live against a real Plex server: an episode's top-level
    # `year` field is always null (only the show's premiere year is
    # populated there), so Plex's plain year= filter -- which works
    # fine for movies/shows -- silently matches nothing for episodes.
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        path = url.removeprefix(_CREDS.base_url)
        if path != "/library/all":
            raise AssertionError(f"unexpected path: {path}")
        if (params or {}).get("type") == "4":
            captured["params"] = params
        return _FakeResponse(_EMPTY_METADATA)

    monkeypatch.setattr("tvdinner.plex.requests.get", fake_get)

    search_plex_by_year(_CREDS, "1988")

    assert captured["params"] == {
        "type": "4",
        "originallyAvailableAt>>": "1988-01-01",
        "originallyAvailableAt<<": "1988-12-31",
    }


def test_search_plex_by_year_sorts_case_insensitively(monkeypatch):
    year_movies = {
        "MediaContainer": {
            "Metadata": [
                {"ratingKey": "10", "title": "zebra"},
                {"ratingKey": "11", "title": "Apple"},
            ]
        }
    }
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for(year_movies=year_movies))

    nodes, error = search_plex_by_year(_CREDS, "1999")

    assert error is None
    assert [n.title for n in nodes] == ["Apple", "zebra"]


def test_search_plex_by_year_returns_empty_list_with_no_error_when_nothing_matches(monkeypatch):
    monkeypatch.setattr("tvdinner.plex.requests.get", _fake_get_for())

    nodes, error = search_plex_by_year(_CREDS, "1899")

    assert nodes == []
    assert error is None


def test_search_plex_by_year_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.plex.requests.get", fail_get)

    nodes, error = search_plex_by_year(_CREDS, "1999")

    assert nodes == []
    assert "Could not reach Plex server" in error


def test_report_plex_timeline_sends_state_time_and_duration(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse({})

    monkeypatch.setattr("tvdinner.plex.requests.get", fake_get)

    ok, error = report_plex_timeline(
        _CREDS,
        client_id="client-abc",
        session_id="session-xyz",
        rating_key="10",
        state="playing",
        position_seconds=61.0,
        duration_seconds=8160.0,
    )

    assert ok is True
    assert error is None
    assert captured["url"] == "http://panel.example.com:32400/:/timeline"
    assert captured["params"] == {
        "ratingKey": "10",
        "key": "/library/metadata/10",
        "state": "playing",
        "time": "61000",
        "duration": "8160000",
    }
    assert captured["headers"]["X-Plex-Token"] == "tok12345678"
    assert captured["headers"]["X-Plex-Client-Identifier"] == "client-abc"
    assert captured["headers"]["X-Plex-Session-Identifier"] == "session-xyz"


def test_report_plex_timeline_sends_platform_product_and_device_headers(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _FakeResponse({})

    monkeypatch.setattr("tvdinner.plex.requests.get", fake_get)

    report_plex_timeline(
        _CREDS,
        client_id="client-abc",
        session_id="session-xyz",
        rating_key="10",
        state="playing",
        position_seconds=0.0,
        duration_seconds=1.0,
    )

    headers = captured["headers"]
    # Product/Device are always the app's own identity -- there's no
    # sensible device *type* to report for a desktop app (see
    # report_plex_timeline's own comment). Platform/Device-Name are
    # environment-dependent (the actual OS name and this machine's own
    # hostname), so just check they're populated with *something* other
    # than silently missing.
    assert headers["X-Plex-Product"] == "tvdinner"
    assert headers["X-Plex-Device"] == "tvdinner"
    assert headers["X-Plex-Platform"]
    assert headers["X-Plex-Device-Name"]


def test_plex_platform_names_maps_darwin_to_macos():
    from tvdinner.plex import _PLEX_PLATFORM_NAMES

    assert _PLEX_PLATFORM_NAMES["Darwin"] == "macOS"


def test_report_plex_timeline_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.plex.requests.get", fail_get)

    ok, error = report_plex_timeline(
        _CREDS,
        client_id="client-abc",
        session_id="session-xyz",
        rating_key="10",
        state="stopped",
        position_seconds=0.0,
        duration_seconds=8160.0,
    )

    assert ok is False
    assert "Could not report playback state" in error


def test_load_plex_client_id_creates_and_persists_a_new_id(tmp_path):
    path = tmp_path / "plex_client_id.json"

    client_id = load_plex_client_id(path)

    assert client_id
    assert path.is_file()
    assert json.loads(path.read_text()) == {"client_id": client_id}


def test_load_plex_client_id_reuses_an_existing_id(tmp_path):
    path = tmp_path / "plex_client_id.json"
    path.write_text(json.dumps({"client_id": "existing-id"}))

    assert load_plex_client_id(path) == "existing-id"


def test_load_plex_client_id_regenerates_on_malformed_file(tmp_path):
    path = tmp_path / "plex_client_id.json"
    path.write_text("not json")

    client_id = load_plex_client_id(path)

    assert client_id
    assert json.loads(path.read_text()) == {"client_id": client_id}
