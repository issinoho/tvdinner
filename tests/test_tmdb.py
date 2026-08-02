import json
import os
import time

import pytest
import requests

from tvdinner import tmdb


@pytest.fixture(autouse=True)
def _clear_ratings_caches(monkeypatch):
    monkeypatch.setattr(tmdb, "_ratings_cache", {})
    monkeypatch.setattr(tmdb, "_in_flight", set())


@pytest.fixture(autouse=True)
def _run_threads_synchronously(monkeypatch):
    """prefetch_ratings spawns daemon threads -- for deterministic tests we
    run the target function immediately on the calling thread instead of
    actually threading, same effect (cache populated, key cleared from
    _in_flight) without any real concurrency to wait on."""

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(tmdb.threading, "Thread", _ImmediateThread)


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise requests.HTTPError("bad status")

    def json(self):
        return self._payload


def _fake_get_for(results):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert headers["Authorization"].startswith("Bearer ")
        assert "api_key" not in (params or {})
        assert "token" not in (params or {})
        return _FakeResponse({"results": results})

    return fake_get


def test_is_movie_category_matches_common_spellings():
    assert tmdb.is_movie_category("Movie")
    assert tmdb.is_movie_category("movie")
    assert tmdb.is_movie_category("Film")
    assert tmdb.is_movie_category("Cinema")
    assert tmdb.is_movie_category("Sci-Fi Film")
    assert not tmdb.is_movie_category("News")
    assert not tmdb.is_movie_category("Sport")
    assert not tmdb.is_movie_category(None)
    assert not tmdb.is_movie_category("")


def test_search_movie_rating_returns_vote_average_for_first_result(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests, "get", _fake_get_for([{"vote_average": 7.6, "release_date": "1974-10-02"}])
    )
    ok, rating = tmdb._search_movie_rating("The Taking of Pelham One Two Three", None, "token")
    assert ok is True
    assert rating == 7.6


def test_search_movie_rating_prefers_exact_year_match_over_first_result(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for(
            [
                {"vote_average": 5.0, "release_date": "2009-01-01"},
                {"vote_average": 7.6, "release_date": "1974-10-02"},
            ]
        ),
    )
    ok, rating = tmdb._search_movie_rating("Some Remake", "1974", "token")
    assert ok is True
    assert rating == 7.6


def test_search_movie_rating_returns_ok_true_none_for_zero_results(monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([]))
    ok, rating = tmdb._search_movie_rating("Some Obscure Local Title", None, "token")
    assert ok is True
    assert rating is None


def test_search_movie_rating_returns_ok_false_on_request_exception(monkeypatch):
    def fake_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    ok, rating = tmdb._search_movie_rating("Anything", None, "token")
    assert ok is False
    assert rating is None


def test_search_movie_rating_returns_ok_false_on_non_json_response(monkeypatch):
    class _BadJsonResponse:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(tmdb.requests, "get", lambda *a, **k: _BadJsonResponse())
    ok, rating = tmdb._search_movie_rating("Anything", None, "token")
    assert ok is False
    assert rating is None


def test_search_movie_rating_sends_bearer_auth_header_not_api_key_query_param(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse({"results": []})

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    tmdb._search_movie_rating("Anything", None, "secret-token")
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in captured["params"].values()


def test_fetch_movie_rating_cached_writes_and_reuses_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"vote_average": 7.6, "release_date": "1974"}]))
    rating = tmdb.fetch_movie_rating_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert rating == 7.6

    def fail_get(*args, **kwargs):
        raise AssertionError("should not hit the network on a warm cache")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    rating_again = tmdb.fetch_movie_rating_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert rating_again == 7.6


def test_fetch_movie_rating_cached_negative_caches_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([]))
    rating = tmdb.fetch_movie_rating_cached("No Such Movie", None, "token", cache_dir=tmp_path)
    assert rating is None

    def fail_get(*args, **kwargs):
        raise AssertionError("a cached negative result should not re-hit the network")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    rating_again = tmdb.fetch_movie_rating_cached("No Such Movie", None, "token", cache_dir=tmp_path)
    assert rating_again is None


def test_fetch_movie_rating_cached_does_not_cache_network_failure(tmp_path, monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    rating = tmdb.fetch_movie_rating_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert rating is None

    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"vote_average": 7.6, "release_date": "1974"}]))
    rating_after_recovery = tmdb.fetch_movie_rating_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert rating_after_recovery == 7.6


def test_fetch_movie_rating_cached_expires_after_max_age(tmp_path, monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"vote_average": 7.6, "release_date": "1974"}]))
    tmdb.fetch_movie_rating_cached("Some Movie", "1974", "token", cache_dir=tmp_path, max_age=timedelta(days=30))

    path = tmdb.cache_path_for(tmp_path, tmdb._tmdb_cache_source_key("Some Movie", "1974"), suffix=".json")
    stale_time = time.time() - timedelta(days=31).total_seconds()
    os.utime(path, (stale_time, stale_time))

    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"vote_average": 8.2, "release_date": "1974"}]))
    rating = tmdb.fetch_movie_rating_cached("Some Movie", "1974", "token", cache_dir=tmp_path, max_age=timedelta(days=30))
    assert rating == 8.2


def test_prefetch_ratings_populates_cache_and_clears_in_flight(monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"vote_average": 7.6, "release_date": "1974"}]))
    tmdb.prefetch_ratings([("Some Movie", "1974")], "token")
    assert tmdb.cached_rating("Some Movie", "1974") == 7.6
    assert ("Some Movie", "1974") not in tmdb._in_flight


def test_prefetch_ratings_skips_already_cached_or_in_flight_keys(monkeypatch):
    def fail_get(*args, **kwargs):
        raise AssertionError("should not fetch a key that's already cached or in flight")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)

    tmdb._ratings_cache[("Cached Movie", "1974")] = 7.6
    tmdb._in_flight.add(("In Flight Movie", "1974"))

    tmdb.prefetch_ratings([("Cached Movie", "1974"), ("In Flight Movie", "1974")], "token")


def test_rating_for_gates_on_movie_category(monkeypatch):
    tmdb._ratings_cache[("Some Movie", "1974")] = 7.6
    assert tmdb.rating_for("Some Movie", "Movie", "1974") == 7.6
    assert tmdb.rating_for("Some Movie", "News", "1974") is None
    assert tmdb.rating_for("Some Movie", None, "1974") is None
