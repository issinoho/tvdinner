from tvdinner.m3u import Channel, Playlist
from tvdinner.vod import split_m3u_vod_items

_PLAYLIST = Playlist(
    channels=[
        Channel(name="BBC News", url="http://x/news.m3u8", group_title="News"),
        Channel(
            name="The Matrix",
            url="http://x/matrix.mp4",
            group_title="Movies",
            tvg_logo="http://x/matrix.png",
        ),
        Channel(name="Die Hard", url="http://x/diehard.mp4", group_title="Movies;Action"),
    ]
)


def test_no_vod_groups_is_a_no_op():
    items, channels = split_m3u_vod_items(_PLAYLIST, set())
    assert items == []
    assert channels == _PLAYLIST.channels


def test_matching_group_is_pulled_out_as_vod_items():
    items, channels = split_m3u_vod_items(_PLAYLIST, {"Movies"})

    assert [c.name for c in channels] == ["BBC News"]
    assert [i.title for i in items] == ["The Matrix", "Die Hard"]

    matrix = items[0]
    assert matrix.url == "http://x/matrix.mp4"
    assert matrix.group_title == "Movies"
    assert matrix.poster_url == "http://x/matrix.png"


def test_match_is_case_insensitive():
    items, channels = split_m3u_vod_items(_PLAYLIST, {"movies"})
    assert len(items) == 2
    assert len(channels) == 1


def test_multi_group_channel_matches_on_any_group():
    items, channels = split_m3u_vod_items(_PLAYLIST, {"Action"})
    assert [i.title for i in items] == ["Die Hard"]
    assert [c.name for c in channels] == ["BBC News", "The Matrix"]


def test_no_matching_group_leaves_all_channels_in_place():
    items, channels = split_m3u_vod_items(_PLAYLIST, {"Sports"})
    assert items == []
    assert channels == _PLAYLIST.channels
