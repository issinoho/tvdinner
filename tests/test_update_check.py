from datetime import datetime, timedelta, timezone

import requests

from tvdinner.update_check import (
    UpdateCheckState,
    UpdateInfo,
    _parse_version,
    check_for_update,
    is_newer,
    load_update_check_state,
    save_update_check_state,
    should_check_now,
)


def test_parse_version_parses_real_semver():
    assert _parse_version("1.0.0") == (1, 0, 0)
    assert _parse_version("v1.0.0") == (1, 0, 0)
    assert _parse_version("1.2.10") == (1, 2, 10)


def test_parse_version_parses_legacy_prefix_and_trailing_counter():
    assert _parse_version("0.1.0-92") == (0, 1, 0, 92)
    assert _parse_version("v0.1.0-92") == (0, 1, 0, 92)


def test_is_newer_compares_counter_numerically_not_lexicographically():
    # A plain string compare gets this backwards: "0.1.0-100" < "0.1.0-99"
    # lexicographically, but 100 > 99 numerically.
    assert is_newer("0.1.0-100", "0.1.0-99")
    assert not is_newer("0.1.0-99", "0.1.0-100")


def test_is_newer_single_digit_counters():
    assert is_newer("0.1.0-10", "0.1.0-9")
    assert not is_newer("0.1.0-9", "0.1.0-10")


def test_is_newer_same_version_is_not_newer():
    assert not is_newer("0.1.0-92", "0.1.0-92")
    assert not is_newer("1.0.0", "1.0.0")


def test_is_newer_handles_a_leading_v_on_either_side():
    assert is_newer("v0.1.0-93", "0.1.0-92")


def test_is_newer_falls_through_to_prefix_on_a_hypothetical_version_bump():
    assert is_newer("0.2.0-1", "0.1.0-999")


def test_is_newer_compares_real_semver_versions():
    assert is_newer("1.0.1", "1.0.0")
    assert is_newer("1.1.0", "1.0.9")
    assert not is_newer("1.0.0", "1.0.1")


def test_is_newer_across_the_1_0_0_cutover():
    # Regression test: confirmed live that comparing a real-semver "1.0.0"
    # release against a pre-1.0 "X.Y.Z-N" local version (or vice versa)
    # used to crash with "invalid literal for int() with base 10: ''",
    # since the old _parse_version required a trailing '-N' on every
    # version string, including a bare real-semver one with none.
    assert is_newer("1.0.0", "0.1.0-160")
    assert not is_newer("0.1.0-160", "1.0.0")


def test_should_check_now_never_checked_before():
    assert should_check_now(UpdateCheckState(), datetime.now(timezone.utc))


def test_should_check_now_just_checked():
    now = datetime.now(timezone.utc)
    state = UpdateCheckState(last_checked=now)
    assert not should_check_now(state, now)


def test_should_check_now_over_the_interval():
    now = datetime.now(timezone.utc)
    state = UpdateCheckState(last_checked=now - timedelta(hours=25))
    assert should_check_now(state, now)


def test_load_update_check_state_missing_file_is_not_an_error(tmp_path):
    state, warnings = load_update_check_state(tmp_path / "does-not-exist.json")
    assert state == UpdateCheckState()
    assert warnings == []


def test_load_update_check_state_malformed_json_returns_default_and_warning(tmp_path):
    path = tmp_path / "update_check.json"
    path.write_text("not json")

    state, warnings = load_update_check_state(path)

    assert state == UpdateCheckState()
    assert len(warnings) == 1
    assert str(path) in warnings[0]


def test_load_update_check_state_wrong_top_level_type_returns_default_and_warning(tmp_path):
    path = tmp_path / "update_check.json"
    path.write_text("[]")

    state, warnings = load_update_check_state(path)

    assert state == UpdateCheckState()
    assert len(warnings) == 1


def test_save_and_load_update_check_state_round_trips(tmp_path):
    path = tmp_path / "update_check.json"
    now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    save_update_check_state(path, UpdateCheckState(last_checked=now, skipped_version="0.1.0-93"))

    loaded, warnings = load_update_check_state(path)

    assert warnings == []
    assert loaded.last_checked == now
    assert loaded.skipped_version == "0.1.0-93"


def test_save_update_check_state_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "update_check.json"
    save_update_check_state(path, UpdateCheckState())
    assert path.is_file()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


_RELEASE_PAYLOAD = {
    "tag_name": "v0.1.0-93",
    "html_url": "https://github.com/issinoho/tvdinner/releases/tag/v0.1.0-93",
    "draft": False,
    "prerelease": False,
}


def test_check_for_update_reports_a_newer_version(monkeypatch):
    monkeypatch.setattr("tvdinner.update_check.requests.get", lambda *a, **kw: _FakeResponse(_RELEASE_PAYLOAD))

    info, error = check_for_update("0.1.0-92")

    assert error is None
    assert info == UpdateInfo(version="0.1.0-93", html_url=_RELEASE_PAYLOAD["html_url"])


def test_check_for_update_already_up_to_date(monkeypatch):
    monkeypatch.setattr("tvdinner.update_check.requests.get", lambda *a, **kw: _FakeResponse(_RELEASE_PAYLOAD))

    info, error = check_for_update("0.1.0-93")

    assert info is None
    assert error is None


def test_check_for_update_reports_network_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("tvdinner.update_check.requests.get", fail_get)

    info, error = check_for_update("0.1.0-92")

    assert info is None
    assert "Could not reach GitHub" in error


def test_check_for_update_reports_malformed_response(monkeypatch):
    monkeypatch.setattr("tvdinner.update_check.requests.get", lambda *a, **kw: _FakeResponse({"foo": "bar"}))

    info, error = check_for_update("0.1.0-92")

    assert info is None
    assert error is not None


_REAL_SEMVER_RELEASE_PAYLOAD = {
    "tag_name": "v1.0.0",
    "html_url": "https://github.com/issinoho/tvdinner/releases/tag/v1.0.0",
    "draft": False,
    "prerelease": False,
}


def test_check_for_update_reports_a_newer_real_semver_release_against_a_legacy_local_version(monkeypatch):
    # The actual live scenario that used to crash: GitHub's latest release
    # is now a bare real-semver tag, checked against a pre-1.0 local
    # version still installed on an unupgraded machine.
    monkeypatch.setattr("tvdinner.update_check.requests.get", lambda *a, **kw: _FakeResponse(_REAL_SEMVER_RELEASE_PAYLOAD))

    info, error = check_for_update("0.1.0-160")

    assert error is None
    assert info == UpdateInfo(version="1.0.0", html_url=_REAL_SEMVER_RELEASE_PAYLOAD["html_url"])


def test_check_for_update_real_semver_already_up_to_date(monkeypatch):
    monkeypatch.setattr("tvdinner.update_check.requests.get", lambda *a, **kw: _FakeResponse(_REAL_SEMVER_RELEASE_PAYLOAD))

    info, error = check_for_update("1.0.0")

    assert info is None
    assert error is None
