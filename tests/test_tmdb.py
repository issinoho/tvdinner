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
def _clear_director_caches(monkeypatch):
    monkeypatch.setattr(tmdb, "_director_cache", {})
    monkeypatch.setattr(tmdb, "_director_in_flight", set())


@pytest.fixture(autouse=True)
def _clear_backdrop_caches(monkeypatch):
    monkeypatch.setattr(tmdb, "_backdrop_cache", {})
    monkeypatch.setattr(tmdb, "_backdrop_in_flight", set())


@pytest.fixture(autouse=True)
def _clear_logo_caches(monkeypatch):
    monkeypatch.setattr(tmdb, "_logo_cache", {})
    monkeypatch.setattr(tmdb, "_logo_in_flight", set())


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


def _fake_get_dispatch(search_results, credits_crew):
    """Like _fake_get_for, but also answers /movie/{id}/credits -- needed
    for anything that fetches a director, since that's always a second,
    separate request after the search one."""

    def fake_get(url, params=None, headers=None, timeout=None):
        assert headers["Authorization"].startswith("Bearer ")
        if url.endswith("/credits"):
            return _FakeResponse({"crew": credits_crew})
        return _FakeResponse({"results": search_results})

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


def test_is_movie_category_falls_back_to_channel_group_title():
    # A genre-only <category> (e.g. Pluto TV's "70s Cinema" tagging its
    # programmes "Drama"/"Thriller", never the word "movie") should still
    # count as a movie when the channel's own M3U group-title says so.
    assert tmdb.is_movie_category("Drama", "Movies")
    assert tmdb.is_movie_category(None, "Movies")
    assert not tmdb.is_movie_category("Drama", "USA")
    assert not tmdb.is_movie_category("Drama", None)


def test_strip_embedded_year_removes_an_exact_trailing_year():
    assert tmdb._strip_embedded_year("Confessions of a Driving Instructor (1977)", "1977") == "Confessions of a Driving Instructor"
    # No year known -- can't tell whether a trailing parenthetical is a year, so left alone.
    assert tmdb._strip_embedded_year("Some Title (1977)", None) == "Some Title (1977)"
    # Year known but doesn't match what's in the title -- not our embedded-year case, leave it.
    assert tmdb._strip_embedded_year("Some Title (1977)", "1980") == "Some Title (1977)"
    # No embedded year at all -- unchanged.
    assert tmdb._strip_embedded_year("Some Title", "1977") == "Some Title"


def test_search_movie_strips_an_embedded_year_from_the_query_before_searching(monkeypatch):
    # SiliconDust's HDHomeRun XMLTV export (among others) bakes the year
    # into <title> -- searching TMDB with that still attached routinely
    # returns zero results, so it must be stripped from the outbound query.
    seen_params = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen_params.update(params)
        return _FakeResponse({"results": [{"vote_average": 6.1, "release_date": "1977-01-01"}]})

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    ok, rating = tmdb._search_movie_rating("Confessions of a Driving Instructor (1977)", "1977", "token")
    assert ok is True
    assert rating == 6.1
    assert seen_params == {"query": "Confessions of a Driving Instructor"}


def test_search_movie_never_sends_year_as_a_hard_api_filter(monkeypatch):
    # Confirmed live: TMDB's /search/movie treats `year` as a strict
    # server-side filter (zero results, not a preference), and a guide
    # provider's release year routinely differs from TMDB's own by a year
    # -- e.g. HDHomeRun/Gracenote said 1977 for "Confessions of a Driving
    # Instructor", TMDB's release_date is 1976-09-01. Sending `year` would
    # zero out an otherwise-correct match and cache it as a permanent
    # negative, so it must never be sent as a request param -- only used
    # to pick the best candidate from a title-only search's results.
    seen_params = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen_params.update(params)
        return _FakeResponse({"results": [{"vote_average": 4.7, "release_date": "1976-09-01"}]})

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    ok, rating = tmdb._search_movie_rating("Confessions of a Driving Instructor", "1977", "token")
    assert ok is True
    assert rating == 4.7
    assert "year" not in seen_params


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
    assert tmdb.rating_for("Some Movie", "Drama", "1974", "Movies") == 7.6
    assert tmdb.rating_for("Some Movie", "Drama", "1974", "USA") is None


def test_fetch_movie_backdrop_cached_writes_and_reuses_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"backdrop_path": "/wide.jpg", "release_date": "1974"}]))
    backdrop_url = tmdb.fetch_movie_backdrop_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert backdrop_url == f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"

    def fail_get(*args, **kwargs):
        raise AssertionError("should not hit the network on a warm cache")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    backdrop_url_again = tmdb.fetch_movie_backdrop_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert backdrop_url_again == f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"


def test_fetch_movie_backdrop_cached_none_without_backdrop_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"release_date": "1974"}]))
    assert tmdb.fetch_movie_backdrop_cached("Some Movie", "1974", "token", cache_dir=tmp_path) is None


def test_fetch_movie_backdrop_cached_negative_caches_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([]))
    backdrop_url = tmdb.fetch_movie_backdrop_cached("No Such Movie", None, "token", cache_dir=tmp_path)
    assert backdrop_url is None

    def fail_get(*args, **kwargs):
        raise AssertionError("a cached negative result should not re-hit the network")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    backdrop_url_again = tmdb.fetch_movie_backdrop_cached("No Such Movie", None, "token", cache_dir=tmp_path)
    assert backdrop_url_again is None


def test_fetch_movie_backdrop_cached_does_not_cache_network_failure(tmp_path, monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    backdrop_url = tmdb.fetch_movie_backdrop_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert backdrop_url is None

    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"backdrop_path": "/wide.jpg", "release_date": "1974"}]))
    backdrop_url_after_recovery = tmdb.fetch_movie_backdrop_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert backdrop_url_after_recovery == f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"


def test_prefetch_backdrop_populates_cache_and_clears_in_flight(monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"backdrop_path": "/wide.jpg", "release_date": "1974"}]))
    tmdb.prefetch_backdrop([("Some Movie", "1974")], "token")
    assert tmdb.cached_backdrop("Some Movie", "1974") == f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"
    assert ("Some Movie", "1974") not in tmdb._backdrop_in_flight


def test_prefetch_backdrop_skips_already_cached_or_in_flight_keys(monkeypatch):
    def fail_get(*args, **kwargs):
        raise AssertionError("should not fetch a key that's already cached or in flight")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)

    tmdb._backdrop_cache[("Cached Movie", "1974")] = f"{tmdb.TMDB_BACKDROP_BASE}/cached.jpg"
    tmdb._backdrop_in_flight.add(("In Flight Movie", "1974"))

    tmdb.prefetch_backdrop([("Cached Movie", "1974"), ("In Flight Movie", "1974")], "token")


def test_prefetch_backdrop_calls_on_fetched_once_the_key_lands_in_cache(monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"backdrop_path": "/wide.jpg", "release_date": "1974"}]))
    fetched_keys = []
    tmdb.prefetch_backdrop([("Some Movie", "1974")], "token", on_fetched=fetched_keys.append)
    assert fetched_keys == [("Some Movie", "1974")]


def test_prefetch_backdrop_does_not_call_on_fetched_for_an_already_cached_key(monkeypatch):
    def fail_get(*args, **kwargs):
        raise AssertionError("should not fetch an already-cached key")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    tmdb._backdrop_cache[("Cached Movie", "1974")] = f"{tmdb.TMDB_BACKDROP_BASE}/cached.jpg"
    fetched_keys = []

    tmdb.prefetch_backdrop([("Cached Movie", "1974")], "token", on_fetched=fetched_keys.append)

    assert fetched_keys == []


def test_backdrop_for_gates_on_movie_category(monkeypatch):
    tmdb._backdrop_cache[("Some Movie", "1974")] = f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"
    assert tmdb.backdrop_for("Some Movie", "Movie", "1974") == f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"
    assert tmdb.backdrop_for("Some Movie", "News", "1974") is None
    assert tmdb.backdrop_for("Some Movie", None, "1974") is None
    assert tmdb.backdrop_for("Some Movie", "Drama", "1974", "Movies") == f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"
    assert tmdb.backdrop_for("Some Movie", "Drama", "1974", "USA") is None


def _fake_get_for_logo_search(search_result, images_payload):
    """Answers /search/movie with a single result and /movie/{id}/images
    with `images_payload` -- the two-request round trip
    _search_movie_logo (via _search_movie then _best_logo_path) makes
    when the match has an id."""

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/images"):
            return _FakeResponse(images_payload)
        return _FakeResponse({"results": [search_result] if search_result is not None else []})

    return fake_get


def test_best_logo_path_prefers_english(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert url.endswith("/movie/1/images")
        return _FakeResponse(
            {
                "logos": [
                    {"file_path": "/small-en.png", "width": 300, "iso_639_1": "en"},
                    {"file_path": "/large-fr.png", "width": 1000, "iso_639_1": "fr"},
                    {"file_path": "/large-en.png", "width": 500, "iso_639_1": "en"},
                ]
            }
        )

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    assert tmdb._best_logo_path(1, "token") == "/large-en.png"


def test_best_logo_path_falls_back_to_another_language_when_no_english_one_exists(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        lambda *a, **k: _FakeResponse({"logos": [{"file_path": "/only-option.png", "width": 500, "iso_639_1": "fr"}]}),
    )
    assert tmdb._best_logo_path(1, "token") == "/only-option.png"


def test_best_logo_path_none_on_empty_logos_list(monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", lambda *a, **k: _FakeResponse({"logos": []}))
    assert tmdb._best_logo_path(1, "token") is None


def test_best_logo_path_none_on_request_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    assert tmdb._best_logo_path(1, "token") is None


def test_search_movie_logo_uses_the_best_logo_path_when_match_has_an_id(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for_logo_search(
            {"id": 1, "release_date": "1974"}, {"logos": [{"file_path": "/logo.png", "width": 500, "iso_639_1": "en"}]}
        ),
    )
    ok, logo_url = tmdb._search_movie_logo("Some Movie", "1974", "token")
    assert ok is True
    assert logo_url == f"{tmdb.TMDB_LOGO_BASE}/logo.png"


def test_search_movie_logo_none_when_match_has_no_id(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert not url.endswith("/images")
        return _FakeResponse({"results": [{"release_date": "1974"}]})

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    ok, logo_url = tmdb._search_movie_logo("Some Movie", "1974", "token")
    assert ok is True
    assert logo_url is None


def test_fetch_movie_logo_cached_writes_and_reuses_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for_logo_search(
            {"id": 1, "release_date": "1974"}, {"logos": [{"file_path": "/logo.png", "width": 500, "iso_639_1": "en"}]}
        ),
    )
    logo_url = tmdb.fetch_movie_logo_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert logo_url == f"{tmdb.TMDB_LOGO_BASE}/logo.png"

    def fail_get(*args, **kwargs):
        raise AssertionError("should not hit the network on a warm cache")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    logo_url_again = tmdb.fetch_movie_logo_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert logo_url_again == f"{tmdb.TMDB_LOGO_BASE}/logo.png"


def test_fetch_movie_logo_cached_negative_caches_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([]))
    logo_url = tmdb.fetch_movie_logo_cached("No Such Movie", None, "token", cache_dir=tmp_path)
    assert logo_url is None

    def fail_get(*args, **kwargs):
        raise AssertionError("a cached negative result should not re-hit the network")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    logo_url_again = tmdb.fetch_movie_logo_cached("No Such Movie", None, "token", cache_dir=tmp_path)
    assert logo_url_again is None


def test_fetch_movie_logo_cached_does_not_cache_network_failure(tmp_path, monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    logo_url = tmdb.fetch_movie_logo_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert logo_url is None

    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for_logo_search(
            {"id": 1, "release_date": "1974"}, {"logos": [{"file_path": "/logo.png", "width": 500, "iso_639_1": "en"}]}
        ),
    )
    logo_url_after_recovery = tmdb.fetch_movie_logo_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert logo_url_after_recovery == f"{tmdb.TMDB_LOGO_BASE}/logo.png"


def test_prefetch_logo_populates_cache_and_clears_in_flight(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for_logo_search(
            {"id": 1, "release_date": "1974"}, {"logos": [{"file_path": "/logo.png", "width": 500, "iso_639_1": "en"}]}
        ),
    )
    tmdb.prefetch_logo([("Some Movie", "1974")], "token")
    assert tmdb.cached_logo("Some Movie", "1974") == f"{tmdb.TMDB_LOGO_BASE}/logo.png"
    assert ("Some Movie", "1974") not in tmdb._logo_in_flight


def test_prefetch_logo_skips_already_cached_or_in_flight_keys(monkeypatch):
    def fail_get(*args, **kwargs):
        raise AssertionError("should not fetch a key that's already cached or in flight")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)

    tmdb._logo_cache[("Cached Movie", "1974")] = f"{tmdb.TMDB_LOGO_BASE}/cached.png"
    tmdb._logo_in_flight.add(("In Flight Movie", "1974"))

    tmdb.prefetch_logo([("Cached Movie", "1974"), ("In Flight Movie", "1974")], "token")


def test_prefetch_logo_calls_on_fetched_once_the_key_lands_in_cache(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for_logo_search(
            {"id": 1, "release_date": "1974"}, {"logos": [{"file_path": "/logo.png", "width": 500, "iso_639_1": "en"}]}
        ),
    )
    fetched_keys = []
    tmdb.prefetch_logo([("Some Movie", "1974")], "token", on_fetched=fetched_keys.append)
    assert fetched_keys == [("Some Movie", "1974")]


def test_prefetch_logo_does_not_call_on_fetched_for_an_already_cached_key(monkeypatch):
    def fail_get(*args, **kwargs):
        raise AssertionError("should not fetch an already-cached key")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    tmdb._logo_cache[("Cached Movie", "1974")] = f"{tmdb.TMDB_LOGO_BASE}/cached.png"
    fetched_keys = []

    tmdb.prefetch_logo([("Cached Movie", "1974")], "token", on_fetched=fetched_keys.append)

    assert fetched_keys == []


def test_logo_for_gates_on_movie_category(monkeypatch):
    tmdb._logo_cache[("Some Movie", "1974")] = f"{tmdb.TMDB_LOGO_BASE}/logo.png"
    assert tmdb.logo_for("Some Movie", "Movie", "1974") == f"{tmdb.TMDB_LOGO_BASE}/logo.png"
    assert tmdb.logo_for("Some Movie", "News", "1974") is None
    assert tmdb.logo_for("Some Movie", None, "1974") is None
    assert tmdb.logo_for("Some Movie", "Drama", "1974", "Movies") == f"{tmdb.TMDB_LOGO_BASE}/logo.png"
    assert tmdb.logo_for("Some Movie", "Drama", "1974", "USA") is None


def test_fetch_movie_metadata_cached_returns_poster_overview_and_rating(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for(
            [
                {
                    "title": "His Girl Friday",
                    "release_date": "1940-01-11",
                    "poster_path": "/abc123.jpg",
                    "overview": "A newspaper editor tries to keep his ace reporter ex-wife.",
                    "vote_average": 7.988,
                }
            ]
        ),
    )
    metadata = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert metadata.title == "His Girl Friday"
    assert metadata.year == "1940"
    assert metadata.poster_url == f"{tmdb.TMDB_POSTER_BASE}/abc123.jpg"
    assert metadata.overview == "A newspaper editor tries to keep his ace reporter ex-wife."
    assert metadata.rating == "8.0"


def test_fetch_movie_metadata_cached_returns_backdrop_url(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for(
            [
                {
                    "title": "His Girl Friday",
                    "release_date": "1940-01-11",
                    "poster_path": "/abc123.jpg",
                    "backdrop_path": "/wide456.jpg",
                    "overview": "A newspaper editor tries to keep his ace reporter ex-wife.",
                    "vote_average": 7.988,
                }
            ]
        ),
    )
    metadata = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert metadata.backdrop_url == f"{tmdb.TMDB_BACKDROP_BASE}/wide456.jpg"


def test_fetch_movie_metadata_cached_backdrop_url_none_without_backdrop_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for([{"title": "Some Movie", "release_date": "1940", "poster_path": None, "overview": None, "vote_average": None}]),
    )
    metadata = tmdb.fetch_movie_metadata_cached("Some Movie", "1940", "token", cache_dir=tmp_path)
    assert metadata.backdrop_url is None


def test_fetch_movie_metadata_cached_returns_none_for_zero_results(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([]))
    assert tmdb.fetch_movie_metadata_cached("Some Obscure Local Title", None, "token", cache_dir=tmp_path) is None


def test_fetch_movie_metadata_cached_returns_none_on_request_failure_and_does_not_cache_it(tmp_path, monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    assert tmdb.fetch_movie_metadata_cached("Some Movie", "1940", "token", cache_dir=tmp_path) is None

    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for([{"title": "Some Movie", "release_date": "1940", "poster_path": None, "overview": None, "vote_average": None}]),
    )
    metadata = tmdb.fetch_movie_metadata_cached("Some Movie", "1940", "token", cache_dir=tmp_path)
    assert metadata.title == "Some Movie"
    assert metadata.poster_url is None
    assert metadata.overview is None
    assert metadata.rating is None


def test_fetch_movie_metadata_cached_reuses_disk_cache_without_hitting_network(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for([{"title": "His Girl Friday", "release_date": "1940", "poster_path": "/abc.jpg", "overview": "x", "vote_average": 8.0}]),
    )
    first = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)

    def fail_get(*args, **kwargs):
        raise AssertionError("should not hit the network on a warm cache")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    second = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert second == first


def test_fetch_movie_metadata_cached_caches_negative_result_without_hitting_network_again(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([]))
    assert tmdb.fetch_movie_metadata_cached("No Such Movie", None, "token", cache_dir=tmp_path) is None

    def fail_get(*args, **kwargs):
        raise AssertionError("a cached negative result should not re-hit the network")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    assert tmdb.fetch_movie_metadata_cached("No Such Movie", None, "token", cache_dir=tmp_path) is None


def test_fetch_movie_director_returns_the_director_name(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_dispatch([], [{"job": "Director", "name": "Howard Hawks"}, {"job": "Writer", "name": "Charles Lederer"}]),
    )
    assert tmdb._fetch_movie_director(3085, "token") == "Howard Hawks"


def test_fetch_movie_director_joins_multiple_directors(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_dispatch([], [{"job": "Director", "name": "Lana Wachowski"}, {"job": "Director", "name": "Lilly Wachowski"}]),
    )
    assert tmdb._fetch_movie_director(603, "token") == "Lana Wachowski, Lilly Wachowski"


def test_fetch_movie_director_returns_none_when_no_director_credited(monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_dispatch([], [{"job": "Writer", "name": "Someone"}]))
    assert tmdb._fetch_movie_director(1, "token") is None


def test_fetch_movie_director_returns_none_on_request_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    assert tmdb._fetch_movie_director(1, "token") is None


def test_best_backdrop_path_prefers_the_highest_resolution_textless_backdrop(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert url.endswith("/movie/1/images")
        return _FakeResponse(
            {
                "backdrops": [
                    {"file_path": "/small-textless.jpg", "width": 1280, "iso_639_1": None},
                    {"file_path": "/large-textless.jpg", "width": 3840, "iso_639_1": None},
                    {"file_path": "/large-with-text.jpg", "width": 4096, "iso_639_1": "en"},
                ]
            }
        )

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    assert tmdb._best_backdrop_path(1, "/fallback.jpg", "token") == "/large-textless.jpg"


def test_best_backdrop_path_falls_back_to_a_language_tagged_backdrop_when_no_textless_one_exists(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        lambda *a, **k: _FakeResponse({"backdrops": [{"file_path": "/only-option.jpg", "width": 1920, "iso_639_1": "en"}]}),
    )
    assert tmdb._best_backdrop_path(1, "/fallback.jpg", "token") == "/only-option.jpg"


def test_best_backdrop_path_falls_back_on_empty_backdrops_list(monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", lambda *a, **k: _FakeResponse({"backdrops": []}))
    assert tmdb._best_backdrop_path(1, "/fallback.jpg", "token") == "/fallback.jpg"


def test_best_backdrop_path_falls_back_on_request_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    assert tmdb._best_backdrop_path(1, "/fallback.jpg", "token") == "/fallback.jpg"


def test_search_movie_backdrop_uses_the_best_backdrop_path_when_match_has_an_id(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/images"):
            return _FakeResponse({"backdrops": [{"file_path": "/sharper.jpg", "width": 3840, "iso_639_1": None}]})
        return _FakeResponse({"results": [{"id": 1, "backdrop_path": "/default.jpg", "release_date": "1974"}]})

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    ok, backdrop_url = tmdb._search_movie_backdrop("Some Movie", "1974", "token")
    assert ok is True
    assert backdrop_url == f"{tmdb.TMDB_BACKDROP_BASE}/sharper.jpg"


def test_search_movie_backdrop_skips_images_lookup_when_match_has_no_id(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert not url.endswith("/images")
        return _FakeResponse({"results": [{"backdrop_path": "/default.jpg", "release_date": "1974"}]})

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    ok, backdrop_url = tmdb._search_movie_backdrop("Some Movie", "1974", "token")
    assert ok is True
    assert backdrop_url == f"{tmdb.TMDB_BACKDROP_BASE}/default.jpg"


def test_fetch_movie_metadata_cached_uses_the_best_backdrop_path(tmp_path, monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/images"):
            return _FakeResponse({"backdrops": [{"file_path": "/sharper.jpg", "width": 3840, "iso_639_1": None}]})
        if url.endswith("/credits"):
            return _FakeResponse({"crew": []})
        return _FakeResponse(
            {"results": [{"id": 1, "title": "Some Movie", "backdrop_path": "/default.jpg", "release_date": "1974"}]}
        )

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    metadata = tmdb.fetch_movie_metadata_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert metadata.backdrop_url == f"{tmdb.TMDB_BACKDROP_BASE}/sharper.jpg"


def test_fetch_movie_metadata_cached_uses_the_best_logo_path(tmp_path, monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/images"):
            return _FakeResponse({"logos": [{"file_path": "/wordmark.png", "width": 500, "iso_639_1": "en"}]})
        if url.endswith("/credits"):
            return _FakeResponse({"crew": []})
        return _FakeResponse(
            {"results": [{"id": 1, "title": "Some Movie", "backdrop_path": "/default.jpg", "release_date": "1974"}]}
        )

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    metadata = tmdb.fetch_movie_metadata_cached("Some Movie", "1974", "token", cache_dir=tmp_path)
    assert metadata.logo_url == f"{tmdb.TMDB_LOGO_BASE}/wordmark.png"


def test_fetch_movie_metadata_cached_logo_url_none_when_match_has_no_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for([{"title": "Some Movie", "release_date": "1940", "poster_path": None, "overview": None, "vote_average": None}]),
    )
    metadata = tmdb.fetch_movie_metadata_cached("Some Movie", "1940", "token", cache_dir=tmp_path)
    assert metadata.logo_url is None


def test_fetch_movie_metadata_cached_includes_director_when_match_has_an_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_dispatch(
            [{"id": 3085, "title": "His Girl Friday", "release_date": "1940", "poster_path": None, "overview": None, "vote_average": None}],
            [{"job": "Director", "name": "Howard Hawks"}],
        ),
    )
    metadata = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert metadata.director == "Howard Hawks"


def test_fetch_movie_metadata_cached_director_none_when_match_has_no_id(tmp_path, monkeypatch):
    # No "id" key at all in the search result -- there's nothing to fetch
    # credits for, so this must not attempt a second request.
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for([{"title": "Some Movie", "release_date": "1940", "poster_path": None, "overview": None, "vote_average": None}]),
    )
    metadata = tmdb.fetch_movie_metadata_cached("Some Movie", "1940", "token", cache_dir=tmp_path)
    assert metadata.director is None


def test_fetch_movie_metadata_cached_refetches_a_pre_director_cache_entry(tmp_path, monkeypatch):
    # Regression test: confirmed live against a real on-disk cache entry
    # written before the director field existed (title/poster/rating all
    # present, no "director" key at all) -- MovieMetadata(**payload) would
    # silently default director to None forever, indistinguishable from a
    # genuine "TMDB has no director" negative, even though a fresh fetch
    # resolves one right away.
    stale_path = tmdb.cache_path_for(tmp_path, tmdb._metadata_cache_source_key("His Girl Friday", "1940"), suffix=".json")
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text(json.dumps({"title": "His Girl Friday", "year": "1940", "poster_url": None, "overview": None, "rating": "7.4"}))

    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_dispatch(
            [{"id": 3085, "title": "His Girl Friday", "release_date": "1940"}],
            [{"job": "Director", "name": "Howard Hawks"}],
        ),
    )
    metadata = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert metadata.director == "Howard Hawks"

    # The re-fetch should have overwritten the stale entry with a complete
    # one, so a second call is now a genuine cache hit (no network).
    def fail_get(*args, **kwargs):
        raise AssertionError("should be a real warm-cache hit now that the entry has been refreshed")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    second = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert second.director == "Howard Hawks"


def test_fetch_movie_metadata_cached_refetches_a_pre_backdrop_cache_entry(tmp_path, monkeypatch):
    # Same staleness bug as the director migration above, for backdrop_url:
    # a real on-disk entry written before that field existed has "director"
    # but no "backdrop_url" key at all, so MovieMetadata(**payload) would
    # silently default it to None forever.
    stale_path = tmdb.cache_path_for(tmp_path, tmdb._metadata_cache_source_key("His Girl Friday", "1940"), suffix=".json")
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text(
        json.dumps({"title": "His Girl Friday", "year": "1940", "poster_url": None, "overview": None, "rating": "7.4", "director": "Howard Hawks"})
    )

    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_for([{"title": "His Girl Friday", "release_date": "1940", "backdrop_path": "/wide.jpg", "vote_average": 7.4}]),
    )
    metadata = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert metadata.backdrop_url == f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"

    def fail_get(*args, **kwargs):
        raise AssertionError("should be a real warm-cache hit now that the entry has been refreshed")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    second = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert second.backdrop_url == f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg"


def test_fetch_movie_metadata_cached_refetches_a_pre_logo_cache_entry(tmp_path, monkeypatch):
    # Same staleness bug as the director/backdrop_url migrations above,
    # for logo_url: a real on-disk entry written before that field
    # existed has "director" and "backdrop_url" but no "logo_url" key at
    # all, so MovieMetadata(**payload) would silently default it to None
    # forever.
    stale_path = tmdb.cache_path_for(tmp_path, tmdb._metadata_cache_source_key("His Girl Friday", "1940"), suffix=".json")
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text(
        json.dumps(
            {
                "title": "His Girl Friday",
                "year": "1940",
                "poster_url": None,
                "overview": None,
                "rating": "7.4",
                "director": "Howard Hawks",
                "backdrop_url": f"{tmdb.TMDB_BACKDROP_BASE}/wide.jpg",
            }
        )
    )

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/images"):
            return _FakeResponse({"logos": [{"file_path": "/wordmark.png", "width": 500, "iso_639_1": "en"}]})
        if url.endswith("/credits"):
            return _FakeResponse({"crew": []})
        return _FakeResponse({"results": [{"id": 1, "title": "His Girl Friday", "release_date": "1940"}]})

    monkeypatch.setattr(tmdb.requests, "get", fake_get)
    metadata = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert metadata.logo_url == f"{tmdb.TMDB_LOGO_BASE}/wordmark.png"

    def fail_get(*args, **kwargs):
        raise AssertionError("should be a real warm-cache hit now that the entry has been refreshed")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    second = tmdb.fetch_movie_metadata_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert second.logo_url == f"{tmdb.TMDB_LOGO_BASE}/wordmark.png"


def test_fetch_movie_metadata_cached_negative_result_is_not_treated_as_stale(tmp_path, monkeypatch):
    # A genuine negative match (payload is None) has no "director" key to
    # be missing -- must stay a real cache hit, not get reinterpreted as
    # pre-director-field staleness.
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([]))
    assert tmdb.fetch_movie_metadata_cached("No Such Movie", None, "token", cache_dir=tmp_path) is None

    def fail_get(*args, **kwargs):
        raise AssertionError("a genuine negative result should still be a warm cache hit")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    assert tmdb.fetch_movie_metadata_cached("No Such Movie", None, "token", cache_dir=tmp_path) is None


def test_fetch_movie_director_cached_writes_and_reuses_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_dispatch([{"id": 3085, "release_date": "1940"}], [{"job": "Director", "name": "Howard Hawks"}]),
    )
    first = tmdb.fetch_movie_director_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert first == "Howard Hawks"

    def fail_get(*args, **kwargs):
        raise AssertionError("should not hit the network on a warm cache")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    second = tmdb.fetch_movie_director_cached("His Girl Friday", "1940", "token", cache_dir=tmp_path)
    assert second == "Howard Hawks"


def test_fetch_movie_director_cached_negative_caches_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([]))
    assert tmdb.fetch_movie_director_cached("No Such Movie", None, "token", cache_dir=tmp_path) is None

    def fail_get(*args, **kwargs):
        raise AssertionError("a cached negative result should not re-hit the network")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)
    assert tmdb.fetch_movie_director_cached("No Such Movie", None, "token", cache_dir=tmp_path) is None


def test_prefetch_director_populates_cache_and_clears_in_flight(monkeypatch):
    monkeypatch.setattr(
        tmdb.requests,
        "get",
        _fake_get_dispatch([{"id": 3085, "release_date": "1940"}], [{"job": "Director", "name": "Howard Hawks"}]),
    )
    tmdb.prefetch_director([("His Girl Friday", "1940")], "token")
    assert tmdb.cached_director("His Girl Friday", "1940") == "Howard Hawks"
    assert ("His Girl Friday", "1940") not in tmdb._director_in_flight


def test_prefetch_director_skips_already_cached_or_in_flight_keys(monkeypatch):
    def fail_get(*args, **kwargs):
        raise AssertionError("should not fetch a key that's already cached or in flight")

    monkeypatch.setattr(tmdb.requests, "get", fail_get)

    tmdb._director_cache[("Cached Movie", "1974")] = "Some Director"
    tmdb._director_in_flight.add(("In Flight Movie", "1974"))

    tmdb.prefetch_director([("Cached Movie", "1974"), ("In Flight Movie", "1974")], "token")


def test_director_for_gates_on_movie_category(monkeypatch):
    tmdb._director_cache[("Some Movie", "1974")] = "Some Director"
    assert tmdb.director_for("Some Movie", "Movie", "1974") == "Some Director"
    assert tmdb.director_for("Some Movie", "News", "1974") is None
    assert tmdb.director_for("Some Movie", None, "1974") is None


def test_prefetch_ratings_and_prefetch_director_use_independent_caches(monkeypatch):
    # The whole point of keeping these as two separate caches (see
    # tmdb._director_cache's module-level comment) -- prefetching a
    # rating must never populate the director cache, or vice versa.
    monkeypatch.setattr(tmdb.requests, "get", _fake_get_for([{"vote_average": 7.6, "release_date": "1974"}]))
    tmdb.prefetch_ratings([("Some Movie", "1974")], "token")
    assert tmdb.cached_rating("Some Movie", "1974") == 7.6
    assert tmdb.cached_director("Some Movie", "1974") is None
