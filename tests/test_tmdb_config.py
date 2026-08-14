from tvdinner.tmdb_config import clear_tmdb_token, load_tmdb_token, save_tmdb_token


def test_load_tmdb_token_missing_file_is_not_an_error(tmp_path):
    token, warnings = load_tmdb_token(tmp_path / "does-not-exist.json")
    assert token is None
    assert warnings == []


def test_load_tmdb_token_parses_a_saved_token(tmp_path):
    path = tmp_path / "tmdb_token.json"
    path.write_text('{"tmdb_api_token": "secret-token"}')

    token, warnings = load_tmdb_token(path)
    assert token == "secret-token"
    assert warnings == []


def test_load_tmdb_token_warns_on_malformed_json(tmp_path):
    path = tmp_path / "tmdb_token.json"
    path.write_text("{not valid json")

    token, warnings = load_tmdb_token(path)
    assert token is None
    assert len(warnings) == 1


def test_load_tmdb_token_warns_on_non_object_json(tmp_path):
    path = tmp_path / "tmdb_token.json"
    path.write_text('["not", "an", "object"]')

    token, warnings = load_tmdb_token(path)
    assert token is None
    assert len(warnings) == 1


def test_load_tmdb_token_warns_on_non_string_token(tmp_path):
    path = tmp_path / "tmdb_token.json"
    path.write_text('{"tmdb_api_token": 12345}')

    token, warnings = load_tmdb_token(path)
    assert token is None
    assert len(warnings) == 1


def test_save_tmdb_token_round_trips_through_load_tmdb_token(tmp_path):
    path = tmp_path / "nested" / "tmdb_token.json"

    save_tmdb_token(path, "secret-token")
    loaded, warnings = load_tmdb_token(path)

    assert loaded == "secret-token"
    assert warnings == []


def test_save_tmdb_token_overwrites_a_previous_one(tmp_path):
    path = tmp_path / "tmdb_token.json"
    save_tmdb_token(path, "old-token")
    save_tmdb_token(path, "new-token")

    loaded, _ = load_tmdb_token(path)
    assert loaded == "new-token"


def test_clear_tmdb_token_removes_an_existing_file(tmp_path):
    path = tmp_path / "tmdb_token.json"
    save_tmdb_token(path, "secret-token")

    removed = clear_tmdb_token(path)

    assert removed is True
    assert not path.exists()


def test_clear_tmdb_token_missing_file_is_not_an_error(tmp_path):
    removed = clear_tmdb_token(tmp_path / "does-not-exist.json")
    assert removed is False
