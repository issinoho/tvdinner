import time

import pytest

from tvdinner.airplay import (
    AirPlayDevice,
    AirPlaySession,
    airplay_available,
    load_airplay_credentials,
    save_airplay_credentials,
)

pytestmark = pytest.mark.skipif(not airplay_available(), reason="pyatv is not installed")


def test_airplay_device_is_a_plain_name_plus_identifier():
    device = AirPlayDevice(name="Living Room TV", identifier="AA:BB:CC:DD:EE:FF")
    assert device.name == "Living Room TV"
    assert device.identifier == "AA:BB:CC:DD:EE:FF"


def test_load_airplay_credentials_missing_file_is_not_an_error(tmp_path):
    credentials, warnings = load_airplay_credentials(tmp_path / "does-not-exist.json")
    assert credentials == {}
    assert warnings == []


def test_load_airplay_credentials_parses_valid_entries(tmp_path):
    path = tmp_path / "airplay_credentials.json"
    path.write_text('{"AA:BB:CC:DD:EE:FF": "opaque-credentials-string"}')

    credentials, warnings = load_airplay_credentials(path)
    assert credentials == {"AA:BB:CC:DD:EE:FF": "opaque-credentials-string"}
    assert warnings == []


def test_load_airplay_credentials_warns_on_malformed_json(tmp_path):
    path = tmp_path / "airplay_credentials.json"
    path.write_text("[not valid json")

    credentials, warnings = load_airplay_credentials(path)
    assert credentials == {}
    assert len(warnings) == 1


def test_load_airplay_credentials_warns_on_non_object_json(tmp_path):
    path = tmp_path / "airplay_credentials.json"
    path.write_text('["not", "an", "object"]')

    credentials, warnings = load_airplay_credentials(path)
    assert credentials == {}
    assert len(warnings) == 1


def test_load_airplay_credentials_skips_malformed_entry_with_a_warning(tmp_path):
    path = tmp_path / "airplay_credentials.json"
    path.write_text('{"good-device": "creds", "bad-device": 123}')

    credentials, warnings = load_airplay_credentials(path)
    assert credentials == {"good-device": "creds"}
    assert len(warnings) == 1


def test_save_airplay_credentials_round_trips_through_load(tmp_path):
    path = tmp_path / "nested" / "airplay_credentials.json"
    credentials = {"device-a": "creds-a", "device-b": "creds-b"}

    save_airplay_credentials(path, credentials)
    loaded, warnings = load_airplay_credentials(path)

    assert loaded == credentials
    assert warnings == []


def test_airplay_session_runs_coroutines_on_its_own_thread_and_closes_cleanly():
    session = AirPlaySession()
    try:
        assert session._thread.is_alive()

        results = []

        async def _work():
            return 42

        def _on_done(result, error):
            results.append((result, error))

        session._submit(_work(), _on_done)

        for _ in range(100):
            if results:
                break
            time.sleep(0.01)
        assert results == [(42, None)]
    finally:
        session.close()

    assert not session._thread.is_alive()
