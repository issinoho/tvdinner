from tvdinner.redact import redact_resource_url, stable_credential_key


def test_redacts_xtream_live_stream_path():
    url = "http://panel.example.com:8080/live/myuser/hunter2/12345.ts"
    assert redact_resource_url(url) == "http://panel.example.com:8080/live/myuser/hu***/12345.ts"


def test_redacts_xtream_movie_stream_path():
    url = "http://panel.example.com:8080/movie/myuser/hunter2/999.mp4"
    assert redact_resource_url(url) == "http://panel.example.com:8080/movie/myuser/hu***/999.mp4"


def test_redacts_xtream_series_stream_path():
    url = "http://panel.example.com:8080/series/myuser/hunter2/1/2/999.mp4"
    assert redact_resource_url(url) == "http://panel.example.com:8080/series/myuser/hu***/1/2/999.mp4"


def test_redacts_xtream_short_password_entirely():
    url = "http://panel.example.com:8080/live/myuser/ab/12345.ts"
    assert redact_resource_url(url) == "http://panel.example.com:8080/live/myuser/***/12345.ts"


def test_redacts_plex_token_query_param():
    url = "http://192.168.0.218:32400/library/parts/10/123/file.mkv?X-Plex-Token=abcdefgh12345678"
    assert redact_resource_url(url) == "http://192.168.0.218:32400/library/parts/10/123/file.mkv?X-Plex-Token=abcd***"


def test_redacts_plex_token_case_insensitively():
    url = "http://192.168.0.218:32400/thumb?x-plex-token=abcdefgh"
    assert redact_resource_url(url) == "http://192.168.0.218:32400/thumb?x-plex-token=abcd***"


def test_redacts_short_plex_token_entirely():
    url = "http://192.168.0.218:32400/thumb?X-Plex-Token=ab"
    assert redact_resource_url(url) == "http://192.168.0.218:32400/thumb?X-Plex-Token=***"


def test_redacts_query_password_param():
    url = "http://panel.example.com/xmltv.php?username=myuser&password=hunter2secret"
    assert redact_resource_url(url) == "http://panel.example.com/xmltv.php?username=myuser&password=hunt***"


def test_redacts_mac_query_param():
    url = "http://portal.example.com/stream.ts?mac=AA:BB:CC:DD:EE:FF"
    assert redact_resource_url(url) == "http://portal.example.com/stream.ts?mac=AA:B***"


def test_redacts_token_and_ticket_query_params():
    # A tvtimes "Play" link (and many IPTV panels) grant access with a
    # ?token= / ?ticket= rather than a ?password=.
    jwt = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJhYmMifQ.sig"
    assert redact_resource_url(f"https://tv.example/exports/play/1/stream?ticket={jwt}") == (
        "https://tv.example/exports/play/1/stream?ticket=eyJh***"
    )
    assert redact_resource_url("https://host/playlist.m3u?token=SECRETVALUE") == (
        "https://host/playlist.m3u?token=SECR***"
    )


def test_redacts_http_basic_auth_userinfo():
    assert redact_resource_url("http://user:hunter2@host/x.m3u") == "http://user:***@host/x.m3u"


def test_leaves_ordinary_urls_unchanged():
    url = "https://example.com/playlist.m3u8"
    assert redact_resource_url(url) == url


def test_leaves_local_file_paths_unchanged():
    path = "/home/user/Movies/some_movie.mp4"
    assert redact_resource_url(path) == path


def test_leaves_youtube_urls_unchanged():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert redact_resource_url(url) == url


def test_stable_credential_key_hashes_an_xtream_login_url():
    url = "xtream://myuser:hunter2@panel.example.com:8080"
    key = stable_credential_key(url)
    assert key != url
    assert "hunter2" not in key
    assert key.startswith("xtream:")


def test_stable_credential_key_hashes_a_stalker_login_url():
    url = "stalker://host:8080/c/?mac=AA:BB:CC:DD:EE:FF"
    key = stable_credential_key(url)
    assert key != url
    assert "AA:BB:CC:DD:EE:FF" not in key
    assert key.startswith("stalker:")


def test_stable_credential_key_hashes_a_plex_login_url():
    url = "plex://192.168.0.218:32400?X-Plex-Token=abcdefgh12345678"
    key = stable_credential_key(url)
    assert key != url
    assert "abcdefgh12345678" not in key
    assert key.startswith("plex:")


def test_stable_credential_key_is_deterministic():
    url = "xtream://myuser:hunter2@panel.example.com:8080"
    assert stable_credential_key(url) == stable_credential_key(url)


def test_stable_credential_key_differs_for_different_credentials():
    a = stable_credential_key("xtream://myuser:hunter2@panel.example.com:8080")
    b = stable_credential_key("xtream://myuser:different@panel.example.com:8080")
    assert a != b


def test_stable_credential_key_leaves_m3u_urls_unchanged():
    url = "https://example.com/playlist.m3u"
    assert stable_credential_key(url) == url


def test_stable_credential_key_leaves_local_file_paths_unchanged():
    path = "/home/user/playlists/local.m3u"
    assert stable_credential_key(path) == path


def test_stable_credential_key_leaves_hdhomerun_urls_unchanged():
    url = "hdhomerun://192.168.1.50"
    assert stable_credential_key(url) == url
