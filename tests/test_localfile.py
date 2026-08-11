from pathlib import Path

from tvdinner.localfile import guess_movie_title_year


def test_guess_movie_title_year_parses_title_and_parenthesized_year():
    assert guess_movie_title_year(Path("His Girl Friday (1940).webm")) == ("His Girl Friday", "1940")


def test_guess_movie_title_year_parses_dotted_scene_release_name():
    assert guess_movie_title_year(Path("Movie.Title.2020.1080p.BluRay.x264-GROUP.mkv")) == (
        "Movie Title",
        "2020",
    )


def test_guess_movie_title_year_parses_dash_separated_year():
    assert guess_movie_title_year(Path("Movie Title - 1999 - BluRay.mp4")) == ("Movie Title", "1999")


def test_guess_movie_title_year_parses_bracketed_year():
    assert guess_movie_title_year(Path("Movie Title [2001].mkv")) == ("Movie Title", "2001")


def test_guess_movie_title_year_returns_none_year_when_no_year_found():
    assert guess_movie_title_year(Path("Just A Movie Title.mp4")) == ("Just A Movie Title", None)


def test_guess_movie_title_year_does_not_mistake_resolution_for_a_year():
    assert guess_movie_title_year(Path("Some.Movie.1080p.WEB-DL.mkv")) == ("Some Movie 1080p WEB-DL", None)


def test_guess_movie_title_year_falls_back_to_whole_stem_when_title_would_be_empty():
    assert guess_movie_title_year(Path("(1940).webm")) == ("1940", "1940")
