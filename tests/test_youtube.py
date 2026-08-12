import requests

from tvdinner import youtube


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise requests.HTTPError("bad status")

    def json(self):
        return self._payload


def test_is_youtube_url_matches_common_forms():
    assert youtube.is_youtube_url("https://www.youtube.com/watch?v=wEx-z1TYPKU")
    assert youtube.is_youtube_url("https://youtube.com/watch?v=wEx-z1TYPKU")
    assert youtube.is_youtube_url("https://m.youtube.com/watch?v=wEx-z1TYPKU")
    assert youtube.is_youtube_url("https://youtu.be/wEx-z1TYPKU")
    assert youtube.is_youtube_url("https://www.youtube.com/shorts/wEx-z1TYPKU")


def test_is_youtube_url_false_for_other_urls():
    assert not youtube.is_youtube_url("https://example.com/video.mp4")
    assert not youtube.is_youtube_url("https://vimeo.com/12345")
    assert not youtube.is_youtube_url("http://stream.example.com/news.m3u8")
    assert not youtube.is_youtube_url("/home/user/Videos/movie.mkv")


def test_guess_title_year_extracts_a_parenthesized_year():
    assert youtube.guess_title_year("Nosferatu (1922) Full Movie") == ("Nosferatu Full Movie", "1922")


def test_guess_title_year_returns_none_when_no_year_present():
    assert youtube.guess_title_year("My Vacation Vlog Day 3") == ("My Vacation Vlog Day 3", None)


def test_guess_title_year_handles_a_bare_leading_year_with_no_brackets():
    # A real public-domain-archive channel's actual title format
    # (confirmed live against youtube.com/watch?v=wEx-z1TYPKU) -- not
    # every uploader wraps the year in parens.
    assert youtube.guess_title_year("1940 - His Girl Friday - Cary Grant and Rosalind Russell") == (
        "His Girl Friday - Cary Grant and Rosalind Russell",
        "1940",
    )


def test_guess_title_year_handles_year_at_the_end():
    assert youtube.guess_title_year("Sita Sings the Blues (2008)") == ("Sita Sings the Blues", "2008")


def test_title_search_candidates_splits_off_cast_and_tagline_text():
    # A real archive-channel title (confirmed live against
    # youtube.com/watch?v=wEx-z1TYPKU, once its leading "1940 - " year is
    # stripped by guess_title_year) -- searching TMDB with the whole
    # remainder finds nothing, but the first segment alone does.
    assert youtube.title_search_candidates(
        "His Girl Friday - Cary Grant and Rosalind Russell - Ex-lovers become headline hunters"
    ) == [
        "His Girl Friday",
        "His Girl Friday - Cary Grant and Rosalind Russell - Ex-lovers become headline hunters",
    ]


def test_title_search_candidates_single_candidate_when_nothing_to_split():
    assert youtube.title_search_candidates("Nosferatu Full Movie") == ["Nosferatu Full Movie"]


def test_title_search_candidates_splits_on_pipe_too():
    assert youtube.title_search_candidates("Metropolis | Full Movie | Classic Sci-Fi") == [
        "Metropolis",
        "Metropolis | Full Movie | Classic Sci-Fi",
    ]


def test_title_search_candidates_does_not_split_on_a_bare_colon():
    # A colon is a legitimate movie-subtitle separator ("Mission:
    # Impossible"), unlike " - "/"|", so it's never treated as noise.
    assert youtube.title_search_candidates("Mission: Impossible") == ["Mission: Impossible"]


def test_fetch_youtube_oembed_returns_title_author_and_thumbnail(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert url == youtube._OEMBED_URL
        assert params == {"url": "https://www.youtube.com/watch?v=wEx-z1TYPKU", "format": "json"}
        return _FakeResponse(
            {
                "title": "Big Buck Bunny",
                "author_name": "Blender Foundation",
                "thumbnail_url": "https://i.ytimg.com/vi/wEx-z1TYPKU/hqdefault.jpg",
            }
        )

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    info = youtube.fetch_youtube_oembed("https://www.youtube.com/watch?v=wEx-z1TYPKU")
    assert info.title == "Big Buck Bunny"
    assert info.author_name == "Blender Foundation"
    assert info.thumbnail_url == "https://i.ytimg.com/vi/wEx-z1TYPKU/hqdefault.jpg"


def test_fetch_youtube_oembed_returns_none_on_request_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(youtube.requests, "get", fail_get)
    assert youtube.fetch_youtube_oembed("https://www.youtube.com/watch?v=wEx-z1TYPKU") is None


def test_fetch_youtube_oembed_returns_none_on_non_ok_status(monkeypatch):
    # e.g. a private/deleted/age-restricted video -- oEmbed 401s/404s.
    monkeypatch.setattr(youtube.requests, "get", lambda *a, **k: _FakeResponse({}, status_ok=False))
    assert youtube.fetch_youtube_oembed("https://www.youtube.com/watch?v=wEx-z1TYPKU") is None


def test_fetch_youtube_oembed_returns_none_on_non_json_response(monkeypatch):
    class _BadJsonResponse:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(youtube.requests, "get", lambda *a, **k: _BadJsonResponse())
    assert youtube.fetch_youtube_oembed("https://www.youtube.com/watch?v=wEx-z1TYPKU") is None


def test_fetch_youtube_oembed_returns_none_when_title_missing(monkeypatch):
    monkeypatch.setattr(
        youtube.requests, "get", lambda *a, **k: _FakeResponse({"author_name": "Someone"})
    )
    assert youtube.fetch_youtube_oembed("https://www.youtube.com/watch?v=wEx-z1TYPKU") is None
