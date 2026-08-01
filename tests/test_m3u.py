from tvdinner.m3u import Channel, load_playlist, parse_m3u

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


def test_is_hd_matches_trailing_hd_word():
    assert Channel(name="BBC ONE HD", url="http://x").is_hd
    assert Channel(name="BBC TWO HD", url="http://x").is_hd
    assert Channel(name="Sky Sports Main Event HD", url="http://x").is_hd


def test_is_hd_is_case_insensitive():
    assert Channel(name="bbc one hd", url="http://x").is_hd


def test_is_hd_false_for_plain_channel():
    assert not Channel(name="BBC ONE", url="http://x").is_hd


def test_is_hd_does_not_match_hd_as_part_of_a_word():
    # "HD" must be its own word -- a channel literally named "HDNet" isn't
    # an HD variant of some other "Net" channel.
    assert not Channel(name="HDNet", url="http://x").is_hd


class _FakeStreamResponse:
    """Mimics requests.get(..., stream=True)'s context-manager response --
    iter_content() is the only thing load_playlist should ever pull from
    this. A real response's .encoding is often None (no charset in the
    Content-Type header), which is exactly what makes _fetch_text's
    "or 'utf-8'" fallback worth covering here."""

    def __init__(self, chunks, encoding=None):
        self._chunks = chunks
        self.encoding = encoding
        self.chunks_consumed = 0

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for chunk in self._chunks:
            self.chunks_consumed += 1
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_load_playlist_over_http_does_not_download_a_large_non_playlist_body(monkeypatch):
    # A direct stream URL (e.g. a multi-gigabyte VOD file) run through
    # load_playlist should be recognized as "not a playlist" from just its
    # first chunk, not by downloading the whole thing -- confirmed live
    # against a real multi-GB file that the old response.text-based check
    # hung for minutes doing exactly that.
    huge_body_chunks = [b"\x00\x01\x02\x03" * 1024 for _ in range(1000)]  # ~4MB, stands in for a much larger file
    probe = _FakeStreamResponse(huge_body_chunks)
    calls = []

    def fake_get(url, timeout=15, stream=False):
        calls.append(stream)
        assert stream, "load_playlist should only ever make a streaming request"
        return probe

    monkeypatch.setattr("tvdinner.m3u.requests.get", fake_get)

    result = load_playlist("http://example.com/movie.mp4")

    assert result is None
    assert probe.chunks_consumed == 1  # bailed out after the first chunk, not the other ~999
    assert calls == [True]  # only ever one request made, not a second full-body one


def test_load_playlist_over_http_still_parses_a_real_playlist(monkeypatch):
    # A real playlist is read to completion off the same streamed
    # response used to sniff it -- confirmed live that firing a second,
    # separate request for the full body (the old approach) doubled load
    # time against a real-world redirect chain, since both requests paid
    # for the same redirect resolution independently.
    probe = _FakeStreamResponse([SAMPLE.encode()])
    calls = []

    def fake_get(url, timeout=15, stream=False):
        calls.append(stream)
        return probe

    monkeypatch.setattr("tvdinner.m3u.requests.get", fake_get)

    playlist = load_playlist("http://example.com/playlist.m3u")

    assert playlist is not None
    assert [c.name for c in playlist.channels] == ["News Channel HD", "Movie Channel, Extra"]
    assert calls == [True]  # exactly one request, not two


def test_load_playlist_over_http_handles_a_playlist_split_across_chunks(monkeypatch):
    # The sniff-chunk boundary can land mid-playlist for a small enough
    # chunk size relative to a real file -- the rest of the streamed
    # response still needs to be read and joined correctly, not just the
    # first chunk.
    body = SAMPLE.encode()
    midpoint = len(body) // 2
    probe = _FakeStreamResponse([body[:midpoint], body[midpoint:]])

    monkeypatch.setattr("tvdinner.m3u.requests.get", lambda url, timeout=15, stream=False: probe)

    playlist = load_playlist("http://example.com/playlist.m3u")

    assert playlist is not None
    assert [c.name for c in playlist.channels] == ["News Channel HD", "Movie Channel, Extra"]


def test_load_playlist_over_http_empty_body_is_not_a_playlist(monkeypatch):
    probe = _FakeStreamResponse([])

    monkeypatch.setattr("tvdinner.m3u.requests.get", lambda url, timeout=15, stream=False: probe)

    assert load_playlist("http://example.com/empty.m3u") is None
