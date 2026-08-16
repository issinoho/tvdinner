import json
import threading
import time
import urllib.parse

import pytest
import requests

from tvdinner.gdrive import (
    GdriveError,
    clear_gdrive_credentials,
    download_backup,
    load_gdrive_credentials,
    login,
    save_gdrive_credentials,
    upload_backup,
)

_CREDENTIALS = {"client_id": "id", "client_secret": "secret", "refresh_token": "refresh"}


def test_load_gdrive_credentials_missing_file_returns_none(tmp_path):
    credentials, warnings = load_gdrive_credentials(tmp_path / "missing.json")

    assert credentials is None
    assert warnings == []


def test_save_then_load_gdrive_credentials_round_trips(tmp_path):
    path = tmp_path / "gdrive_token.json"
    save_gdrive_credentials(path, "client-id", "client-secret", "refresh-token")

    credentials, warnings = load_gdrive_credentials(path)

    assert warnings == []
    assert credentials == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "refresh-token",
    }


def test_load_gdrive_credentials_malformed_json_returns_warning(tmp_path):
    path = tmp_path / "gdrive_token.json"
    path.write_text("not json")

    credentials, warnings = load_gdrive_credentials(path)

    assert credentials is None
    assert len(warnings) == 1


def test_load_gdrive_credentials_missing_fields_returns_warning(tmp_path):
    path = tmp_path / "gdrive_token.json"
    path.write_text('{"client_id": "x"}')

    credentials, warnings = load_gdrive_credentials(path)

    assert credentials is None
    assert len(warnings) == 1


def test_clear_gdrive_credentials_removes_existing_file(tmp_path):
    path = tmp_path / "gdrive_token.json"
    save_gdrive_credentials(path, "id", "secret", "refresh")

    assert clear_gdrive_credentials(path) is True
    assert not path.exists()


def test_clear_gdrive_credentials_missing_file_returns_false(tmp_path):
    assert clear_gdrive_credentials(tmp_path / "missing.json") is False


class _FakeResponse:
    def __init__(self, payload=None, headers=None, content=b""):
        self._payload = payload or {}
        self.headers = headers or {}
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _redirect_uri_from_auth_url(url):
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.parse_qs(parsed.query)["redirect_uri"][0]


def _open_that_hits_redirect(monkeypatch, **redirect_params):
    def fake_open(url):
        redirect_uri = _redirect_uri_from_auth_url(url)

        def hit_redirect():
            time.sleep(0.05)
            requests.get(redirect_uri, params=redirect_params, timeout=5)

        threading.Thread(target=hit_redirect, daemon=True).start()
        return True

    monkeypatch.setattr("tvdinner.gdrive.webbrowser.open", fake_open)


def test_login_completes_pkce_flow_and_returns_credentials(monkeypatch):
    _open_that_hits_redirect(monkeypatch, code="test-auth-code")

    def fake_post(url, data=None, timeout=None):
        assert url == "https://oauth2.googleapis.com/token"
        assert data["grant_type"] == "authorization_code"
        assert data["code"] == "test-auth-code"
        assert "code_verifier" in data
        return _FakeResponse({"refresh_token": "new-refresh-token"})

    monkeypatch.setattr("tvdinner.gdrive.requests.post", fake_post)

    credentials = login("client-id", "client-secret", open_browser=True, timeout_seconds=5)

    assert credentials == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "new-refresh-token",
    }


def test_login_raises_on_redirect_error(monkeypatch):
    _open_that_hits_redirect(monkeypatch, error="access_denied")

    with pytest.raises(GdriveError, match="access_denied"):
        login("client-id", "client-secret", open_browser=True, timeout_seconds=5)


def test_login_times_out_when_no_redirect_arrives(monkeypatch):
    monkeypatch.setattr("tvdinner.gdrive.webbrowser.open", lambda url: True)

    with pytest.raises(GdriveError, match="Timed out"):
        login("client-id", "client-secret", open_browser=True, timeout_seconds=0.2)


def test_login_raises_when_no_refresh_token_returned(monkeypatch):
    _open_that_hits_redirect(monkeypatch, code="test-auth-code")
    monkeypatch.setattr("tvdinner.gdrive.requests.post", lambda url, data=None, timeout=None: _FakeResponse({}))

    with pytest.raises(GdriveError, match="refresh token"):
        login("client-id", "client-secret", open_browser=True, timeout_seconds=5)


def test_upload_backup_creates_new_file_when_none_exists(monkeypatch):
    calls = []

    def fake_post(url, headers=None, data=None, timeout=None):
        if url == "https://oauth2.googleapis.com/token":
            return _FakeResponse({"access_token": "access-token"})
        if url == "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable":
            assert json.loads(data) == {"name": "tvdinner-backup.zip"}
            return _FakeResponse(headers={"Location": "https://upload.example/session"})
        raise AssertionError(f"unexpected POST {url}")

    def fake_get(url, headers=None, params=None, timeout=None):
        assert url == "https://www.googleapis.com/drive/v3/files"
        return _FakeResponse({"files": []})

    def fake_put(url, headers=None, data=None, timeout=None):
        calls.append((url, data))
        return _FakeResponse({})

    monkeypatch.setattr("tvdinner.gdrive.requests.post", fake_post)
    monkeypatch.setattr("tvdinner.gdrive.requests.get", fake_get)
    monkeypatch.setattr("tvdinner.gdrive.requests.put", fake_put)

    upload_backup(_CREDENTIALS, "tvdinner-backup.zip", b"zip-bytes")

    assert calls == [("https://upload.example/session", b"zip-bytes")]


def test_upload_backup_updates_existing_file(monkeypatch):
    def fake_post(url, headers=None, data=None, timeout=None):
        if url == "https://oauth2.googleapis.com/token":
            return _FakeResponse({"access_token": "access-token"})
        raise AssertionError(f"unexpected POST {url}")

    def fake_patch(url, headers=None, data=None, timeout=None):
        assert url == "https://www.googleapis.com/upload/drive/v3/files/file-123?uploadType=resumable"
        return _FakeResponse(headers={"Location": "https://upload.example/session2"})

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse({"files": [{"id": "file-123", "name": "tvdinner-backup.zip"}]})

    put_calls = []

    def fake_put(url, headers=None, data=None, timeout=None):
        put_calls.append((url, data))
        return _FakeResponse({})

    monkeypatch.setattr("tvdinner.gdrive.requests.post", fake_post)
    monkeypatch.setattr("tvdinner.gdrive.requests.patch", fake_patch)
    monkeypatch.setattr("tvdinner.gdrive.requests.get", fake_get)
    monkeypatch.setattr("tvdinner.gdrive.requests.put", fake_put)

    upload_backup(_CREDENTIALS, "tvdinner-backup.zip", b"zip-bytes")

    assert put_calls == [("https://upload.example/session2", b"zip-bytes")]


def test_upload_backup_raises_gdrive_error_on_network_failure(monkeypatch):
    def fake_post(url, headers=None, data=None, timeout=None):
        if url == "https://oauth2.googleapis.com/token":
            return _FakeResponse({"access_token": "access-token"})
        raise requests.RequestException("boom")

    monkeypatch.setattr("tvdinner.gdrive.requests.post", fake_post)
    monkeypatch.setattr("tvdinner.gdrive.requests.get", lambda *a, **k: _FakeResponse({"files": []}))

    with pytest.raises(GdriveError):
        upload_backup(_CREDENTIALS, "tvdinner-backup.zip", b"zip-bytes")


def test_download_backup_returns_file_contents(monkeypatch):
    def fake_post(url, data=None, timeout=None):
        return _FakeResponse({"access_token": "access-token"})

    def fake_get(url, headers=None, params=None, timeout=None):
        if url == "https://www.googleapis.com/drive/v3/files":
            return _FakeResponse({"files": [{"id": "file-123", "name": "tvdinner-backup.zip"}]})
        if url == "https://www.googleapis.com/drive/v3/files/file-123":
            assert params == {"alt": "media"}
            return _FakeResponse(content=b"zip-bytes")
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr("tvdinner.gdrive.requests.post", fake_post)
    monkeypatch.setattr("tvdinner.gdrive.requests.get", fake_get)

    data = download_backup(_CREDENTIALS, "tvdinner-backup.zip")

    assert data == b"zip-bytes"


def test_download_backup_raises_when_file_not_found(monkeypatch):
    monkeypatch.setattr("tvdinner.gdrive.requests.post", lambda *a, **k: _FakeResponse({"access_token": "t"}))
    monkeypatch.setattr("tvdinner.gdrive.requests.get", lambda *a, **k: _FakeResponse({"files": []}))

    with pytest.raises(GdriveError, match="No 'tvdinner-backup.zip' backup"):
        download_backup(_CREDENTIALS, "tvdinner-backup.zip")
