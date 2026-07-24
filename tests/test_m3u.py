from tvdinner.m3u import Channel, parse_m3u

SAMPLE = """#EXTM3U x-tvg-url="http://epg.example.com/guide.xml"
#EXTINF:-1 tvg-id="news.us" tvg-name="News Channel" tvg-logo="http://logo/news.png" group-title="News",News Channel HD
http://stream.example.com/news.m3u8
#EXTINF:-1 tvg-id="" group-title="Movies",Movie Channel, Extra
http://stream.example.com/movies.m3u8
"""


def test_parses_epg_url_from_header():
    playlist = parse_m3u(SAMPLE)
    assert playlist.epg_url == "http://epg.example.com/guide.xml"


def test_parses_channels_with_attributes():
    playlist = parse_m3u(SAMPLE)
    assert len(playlist.channels) == 2

    first = playlist.channels[0]
    assert first.name == "News Channel HD"
    assert first.url == "http://stream.example.com/news.m3u8"
    assert first.tvg_id == "news.us"
    assert first.tvg_logo == "http://logo/news.png"
    assert first.group_title == "News"


def test_name_with_comma_is_preserved():
    playlist = parse_m3u(SAMPLE)
    second = playlist.channels[1]
    assert second.name == "Movie Channel, Extra"
    assert second.tvg_id is None


def test_empty_playlist():
    playlist = parse_m3u("#EXTM3U\n")
    assert playlist.channels == []
    assert playlist.epg_url is None


def test_channel_groups_splits_semicolon_compound_group_title():
    # Some playlist generators tag one channel under several categories at
    # once via a semicolon-separated group-title (e.g. "Movies;Series").
    channel = Channel(name="X", url="http://x", group_title="Movies;Series")
    assert channel.groups == ["Movies", "Series"]


def test_channel_groups_empty_when_no_group_title():
    channel = Channel(name="X", url="http://x")
    assert channel.groups == []
