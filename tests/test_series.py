from tvdinner.series import SeriesNode


def test_container_is_true_for_category_series_and_season():
    for kind in ("category", "series", "season"):
        assert SeriesNode(id="x", title="X", kind=kind).container is True


def test_container_is_false_for_an_episode():
    assert SeriesNode(id="x", title="X", kind="episode").container is False


def test_optional_fields_default_to_none():
    node = SeriesNode(id="1", title="Some Show", kind="series")
    assert node.poster_url is None
    assert node.subtitle is None
    assert node.year is None
    assert node.rating is None
    assert node.season_number is None
    assert node.episode_number is None
    assert node.series_title is None
    assert node.url is None
