"""Google Drive backup/restore for tvdinner's configuration archive (see
backup.py). Hand-rolled OAuth 2.0 PKCE "Desktop app" flow and Drive v3
REST calls via `requests` -- deliberately not the official
google-api-python-client, to avoid a new dependency for what's a handful
of REST calls used only by `tvdinner gdrive-login`/`backup
--gdrive`/`restore --gdrive`.

Credentials are stored as {client_id, client_secret, refresh_token} in
DEFAULT_GDRIVE_TOKEN_PATH -- never the short-lived access_token, since
refreshing on each use (infrequent, human-triggered commands) is simpler
than tracking expiry.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import requests

if sys.platform == "win32":
    DEFAULT_GDRIVE_TOKEN_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "gdrive_token.json"
else:
    DEFAULT_GDRIVE_TOKEN_PATH = Path.home() / ".config" / "tvdinner" / "gdrive_token.json"

DEFAULT_GDRIVE_BACKUP_NAME = "tvdinner-backup.zip"

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_DRIVE_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
_DRIVE_UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/drive/v3/files"
_SCOPE = "https://www.googleapis.com/auth/drive.file"


class GdriveError(Exception):
    """Raised for any Drive/OAuth failure -- network error, HTTP error
    status, or an OAuth redirect that never arrived or was denied."""


def load_gdrive_credentials(path: Path) -> tuple[dict[str, str] | None, list[str]]:
    """(credentials, warnings). A missing file just means not logged in
    yet -- not an error. `credentials` has client_id/client_secret/
    refresh_token when present."""
    if not path.is_file():
        return None, []
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"Could not read stored Google Drive credentials {path}: {exc}"]
    if not isinstance(data, dict):
        return None, [f"Google Drive credentials file {path} must contain a JSON object"]
    required = ("client_id", "client_secret", "refresh_token")
    if not all(isinstance(data.get(key), str) and data.get(key) for key in required):
        return None, [f"Google Drive credentials file {path} is missing required fields; ignoring"]
    return {key: data[key] for key in required}, []


def save_gdrive_credentials(path: Path, client_id: str, client_secret: str, refresh_token: str) -> None:
    """Persist Drive OAuth credentials, overwriting any previously stored
    ones. Creates the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}, indent=2
        )
        + "\n"
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort -- not every filesystem supports it


def clear_gdrive_credentials(path: Path) -> bool:
    """Remove the stored credentials file, if any. Returns whether a
    file was actually removed."""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    )
    return verifier, challenge


class _OAuthRedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's own naming convention
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.oauth_code = params.get("code", [None])[0]  # type: ignore[attr-defined]
        self.server.oauth_error = params.get("error", [None])[0]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if self.server.oauth_code:  # type: ignore[attr-defined]
            body = "<html><body><h1>tvdinner</h1><p>Signed in -- you can close this window.</p></body></html>"
        else:
            error = self.server.oauth_error or "unknown error"  # type: ignore[attr-defined]
            body = f"<html><body><h1>tvdinner</h1><p>Sign-in failed: {error}</p></body></html>"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 -- matches base class signature
        pass  # don't spam stdout with HTTP access logs for a one-shot local redirect


def login(
    client_id: str, client_secret: str, *, open_browser: bool = True, timeout_seconds: float = 180.0
) -> dict[str, str]:
    """Run the interactive OAuth 2.0 PKCE "Desktop app" flow: start a
    one-shot local redirect listener, open the consent page in a
    browser, wait for the redirect, and exchange the code for tokens.
    Returns {client_id, client_secret, refresh_token} -- ready to pass to
    `save_gdrive_credentials`. Raises GdriveError on failure or timeout."""
    verifier, challenge = _pkce_pair()
    server = http.server.HTTPServer(("127.0.0.1", 0), _OAuthRedirectHandler)
    server.oauth_code = None  # type: ignore[attr-defined]
    server.oauth_error = None  # type: ignore[attr-defined]
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"

    auth_url = _AUTH_ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
    )

    if open_browser:
        webbrowser.open(auth_url)
    print(f"Opening browser for Google sign-in. If it didn't open, visit:\n{auth_url}")

    server.timeout = timeout_seconds
    try:
        server.handle_request()
    finally:
        server.server_close()

    code = server.oauth_code  # type: ignore[attr-defined]
    if not code:
        error = server.oauth_error  # type: ignore[attr-defined]
        if error:
            raise GdriveError(f"Google sign-in failed: {error}")
        raise GdriveError("Timed out waiting for the Google sign-in redirect.")

    try:
        response = requests.post(
            _TOKEN_ENDPOINT,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GdriveError(f"Could not exchange the sign-in code for tokens: {exc}") from exc

    refresh_token = response.json().get("refresh_token")
    if not refresh_token:
        raise GdriveError(
            "Google did not return a refresh token. If you've signed in to this app before, revoke its "
            "access at https://myaccount.google.com/permissions and try again."
        )
    return {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}


def _access_token(credentials: dict[str, str]) -> str:
    """Exchange the stored refresh_token for a fresh access_token.
    Refreshed on every call rather than cached with an expiry, since
    Drive backup/restore are infrequent, human-triggered operations."""
    try:
        response = requests.post(
            _TOKEN_ENDPOINT,
            data={
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "refresh_token": credentials["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GdriveError(f"Could not refresh the Google Drive access token: {exc}") from exc
    token = response.json().get("access_token")
    if not token:
        raise GdriveError("Google did not return an access token.")
    return token


def _find_file_id(access_token: str, name: str) -> str | None:
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    query = f"name = '{escaped}' and trashed = false"
    try:
        response = requests.get(
            _DRIVE_FILES_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"q": query, "spaces": "drive", "fields": "files(id,name)"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GdriveError(f"Could not search Google Drive for '{name}': {exc}") from exc
    files = response.json().get("files", [])
    return files[0]["id"] if files else None


def upload_backup(credentials: dict[str, str], name: str, data: bytes) -> None:
    """Upload `data` to Drive as `name`, updating the existing file of
    that name (from an earlier `tvdinner backup --gdrive`) if one
    exists, or creating a new one. Uses the resumable upload protocol --
    two plain HTTP requests, no multipart/related body to hand-craft."""
    access_token = _access_token(credentials)
    file_id = _find_file_id(access_token, name)

    if file_id:
        session_url = f"{_DRIVE_UPLOAD_ENDPOINT}/{file_id}?uploadType=resumable"
        metadata: dict[str, Any] = {}
        start_request = requests.patch
    else:
        session_url = f"{_DRIVE_UPLOAD_ENDPOINT}?uploadType=resumable"
        metadata = {"name": name}
        start_request = requests.post

    try:
        start = start_request(
            session_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "application/zip",
            },
            data=json.dumps(metadata),
            timeout=30,
        )
        start.raise_for_status()
        upload_url = start.headers["Location"]
        upload = requests.put(upload_url, headers={"Content-Type": "application/zip"}, data=data, timeout=120)
        upload.raise_for_status()
    except requests.RequestException as exc:
        raise GdriveError(f"Could not upload backup to Google Drive: {exc}") from exc
    except KeyError as exc:
        raise GdriveError("Google Drive did not return an upload session URL.") from exc


def download_backup(credentials: dict[str, str], name: str) -> bytes:
    """Download the Drive file named `name` (as created by
    `upload_backup`). Raises GdriveError if no such file exists."""
    access_token = _access_token(credentials)
    file_id = _find_file_id(access_token, name)
    if not file_id:
        raise GdriveError(f"No '{name}' backup found in Google Drive.")
    try:
        response = requests.get(
            f"{_DRIVE_FILES_ENDPOINT}/{file_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"alt": "media"},
            timeout=60,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise GdriveError(f"Could not download backup from Google Drive: {exc}") from exc
    return response.content
