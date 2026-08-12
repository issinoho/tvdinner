from tvdinner import movietitle


def test_guess_title_year_prefers_text_before_the_year():
    # The "Title (Year) junk-after" convention -- everything after the
    # year (scene-release tags, in a filename's case) is discarded.
    assert movietitle.guess_title_year("Nosferatu (1922) Full Movie") == ("Nosferatu", "1922")


def test_guess_title_year_falls_back_to_text_after_the_year_when_nothing_precedes_it():
    # The "Year - Title - junk-after" convention some archive channels
    # (and yt-dlp downloads of them) use -- confirmed live against a real
    # upload/download of youtube.com/watch?v=wEx-z1TYPKU. Not every
    # uploader wraps the year in parens either.
    assert movietitle.guess_title_year("1940 - His Girl Friday - Cary Grant and Rosalind Russell") == (
        "His Girl Friday - Cary Grant and Rosalind Russell",
        "1940",
    )


def test_guess_title_year_returns_none_when_no_year_present():
    assert movietitle.guess_title_year("My Vacation Vlog Day 3") == ("My Vacation Vlog Day 3", None)


def test_guess_title_year_handles_year_at_the_end():
    assert movietitle.guess_title_year("Sita Sings the Blues (2008)") == ("Sita Sings the Blues", "2008")


def test_guess_title_year_falls_back_to_the_whole_text_when_both_sides_are_empty():
    assert movietitle.guess_title_year("(1940)") == ("1940", "1940")


def test_title_search_candidates_splits_off_cast_and_tagline_text():
    # A real archive-channel title (confirmed live against
    # youtube.com/watch?v=wEx-z1TYPKU, once its leading "1940 - " year is
    # stripped by guess_title_year) -- searching TMDB with the whole
    # remainder finds nothing, but the first segment alone does.
    assert movietitle.title_search_candidates(
        "His Girl Friday - Cary Grant and Rosalind Russell - Ex-lovers become headline hunters"
    ) == [
        "His Girl Friday",
        "His Girl Friday - Cary Grant and Rosalind Russell - Ex-lovers become headline hunters",
    ]


def test_title_search_candidates_single_candidate_when_nothing_to_split():
    assert movietitle.title_search_candidates("Nosferatu Full Movie") == ["Nosferatu Full Movie"]


def test_title_search_candidates_splits_on_pipe_too():
    assert movietitle.title_search_candidates("Metropolis | Full Movie | Classic Sci-Fi") == [
        "Metropolis",
        "Metropolis | Full Movie | Classic Sci-Fi",
    ]


def test_title_search_candidates_does_not_split_on_a_bare_colon():
    # A colon is a legitimate movie-subtitle separator ("Mission:
    # Impossible"), unlike " - "/"|", so it's never treated as noise.
    assert movietitle.title_search_candidates("Mission: Impossible") == ["Mission: Impossible"]


def test_guess_title_year_and_title_search_candidates_combine_for_a_real_yt_dlp_filename():
    # End-to-end regression: a yt-dlp download of the real archive-channel
    # upload above (its title plus a trailing " [videoID]", yt-dlp's
    # default output template) -- confirmed live that naively searching
    # TMDB with the whole year-stripped remainder finds nothing, but
    # chaining guess_title_year -> title_search_candidates's first
    # segment does.
    title, year = movietitle.guess_title_year(
        "1940 - His Girl Friday - Cary Grant and Rosalind Russell - "
        "Ex-lovers become headline hunters [wEx-z1TYPKU]"
    )
    assert year == "1940"
    assert movietitle.title_search_candidates(title)[0] == "His Girl Friday"
