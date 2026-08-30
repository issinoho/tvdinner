"""Integration test: tvdinner.xtream's real functions against a fake
Xtream panel over real HTTP -- no requests monkeypatching. Complements
the unit tests in test_xtream.py by exercising the actual request/JSON/
query-param plumbing (e.g. that get_series's &category_id= filter really
reaches the server).

The fake panel lives in tools/fake_xtream_panel.py (on sys.path via
tests/conftest.py) and is modelled on the documented Xtream API shapes.
"""

import threading

import pytest

import fake_xtream_panel as panel
from tvdinner.xtream import (
    XtreamCreds,
    list_xtream_series_children,
    load_xtream_playlist,
    load_xtream_vod,
    resolve_xtream_series_episode,
)
from tvdinner.series import SeriesNode


@pytest.fixture(scope="module")
def creds():
    server = panel.make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield XtreamCreds(base_url=f"http://{host}:{port}", username="test", password="test")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_load_xtream_playlist_over_real_http(creds):
    playlist, error = load_xtream_playlist(creds)
    assert error is None
    assert len(playlist.channels) == 1
    assert playlist.channels[0].url == f"{creds.base_url}/live/test/test/1001.ts"
    assert playlist.epg_url == f"{creds.base_url}/xmltv.php?username=test&password=test"


def test_load_xtream_vod_is_empty_but_not_an_error(creds):
    items, error = load_xtream_vod(creds)
    assert error is None
    assert items == []


def test_series_root_lists_categories(creds):
    nodes, error = list_xtream_series_children(creds, None)
    assert error is None
    assert [(n.id, n.title, n.kind) for n in nodes] == [
        ("10", "Drama", "category"),
        ("11", "Comedy", "category"),
    ]


def test_series_category_filter_reaches_the_server(creds):
    # The monkeypatched unit test can't prove &category_id= is actually
    # sent; here the fake panel returns a different list per category.
    drama, _ = list_xtream_series_children(creds, SeriesNode(id="10", title="Drama", kind="category"))
    comedy, _ = list_xtream_series_children(creds, SeriesNode(id="11", title="Comedy", kind="category"))
    assert [n.title for n in drama] == ["The Sample Detectives", "Testing In The Dark"]
    assert [n.title for n in comedy] == ["Regression Road"]


def test_series_rows_carry_year_and_rating(creds):
    nodes, _ = list_xtream_series_children(creds, SeriesNode(id="10", title="Drama", kind="category"))
    detectives, untested = nodes
    assert (detectives.kind, detectives.year, detectives.rating) == ("series", "2019", "8.4")
    # rating "0" from the panel is treated as "no rating".
    assert untested.rating is None


def test_series_lists_seasons_sorted_with_episode_counts(creds):
    series = SeriesNode(id="500", title="The Sample Detectives", kind="series")
    nodes, error = list_xtream_series_children(creds, series)
    assert error is None
    assert [(n.id, n.title, n.subtitle) for n in nodes] == [
        ("500:1", "Season 1", "3 episodes"),
        ("500:2", "Season 2", "2 episodes"),
    ]
    assert nodes[0].season_number == 1
    assert nodes[0].series_title == "The Sample Detectives"


def test_season_lists_episodes_with_deterministic_urls(creds):
    season = SeriesNode(id="500:1", title="Season 1", kind="season", series_title="The Sample Detectives")
    nodes, error = list_xtream_series_children(creds, season)
    assert error is None
    titles = [n.title for n in nodes]
    assert titles == ["The Empty Stub", "A Flaky Witness", "Teardown"]
    assert nodes[0].subtitle == "S01E01"
    assert nodes[0].url == f"{creds.base_url}/series/test/test/90001.mp4"
    # per-episode container_extension is honoured
    assert nodes[2].url == f"{creds.base_url}/series/test/test/90003.mkv"
    assert (nodes[2].season_number, nodes[2].episode_number) == (1, 3)


def test_resolve_episode_builds_a_playable_vod_item(creds):
    season = SeriesNode(id="500:1", title="Season 1", kind="season", series_title="The Sample Detectives")
    episodes, _ = list_xtream_series_children(creds, season)

    item, error = resolve_xtream_series_episode(creds, episodes[2])
    assert error is None
    assert item.title == "Teardown"
    assert item.url == f"{creds.base_url}/series/test/test/90003.mkv"
    assert item.series_title == "The Sample Detectives"
    assert (item.season_number, item.episode_number) == (1, 3)


def test_unknown_series_id_yields_no_seasons_without_erroring(creds):
    # get_series_info for an unknown id returns {} -- a series with no
    # seasons, not a hard failure.
    nodes, error = list_xtream_series_children(creds, SeriesNode(id="999999", title="Ghost", kind="series"))
    assert error is None
    assert nodes == []


def test_episode_node_has_no_children(creds):
    nodes, error = list_xtream_series_children(creds, SeriesNode(id="90001", title="The Empty Stub", kind="episode"))
    assert nodes == []
    assert "no further items" in error


def test_invalid_credentials_are_reported(creds):
    bad = XtreamCreds(base_url=creds.base_url, username="test", password="wrong")
    nodes, error = list_xtream_series_children(bad, None)
    assert nodes == []
    assert error == "Invalid Xtream username or password"
