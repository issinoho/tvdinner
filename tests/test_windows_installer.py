"""The Windows installer's protocol-handler registration.

Windows has no equivalent of `tvdinner default-handler` (that verb is
Linux-only), so the installer is the *only* thing that makes tvtimes'
"Play" and "Open in tvdinner" buttons work there. It shipped without any
[Registry] section at all, which meant those links did nothing at all on
Windows: the browser handed the URL to the shell, the shell had never
heard of the scheme, and it was dropped silently.

These are text assertions rather than a real install -- CI has no Windows
registry to inspect -- so they guard the thing that actually went wrong:
a scheme the app claims on Linux but the installer forgets on Windows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ISS = Path(__file__).resolve().parents[1] / "windows" / "tvdinner.iss"
DESKTOP = Path(__file__).resolve().parents[1] / "data" / "tvdinner.desktop"

SCHEMES = ("tvdinner", "tvtimes", "tvtimess")


@pytest.fixture(scope="module")
def iss() -> str:
    return ISS.read_text()


@pytest.mark.parametrize("scheme", SCHEMES)
def test_the_installer_registers_each_url_scheme(iss: str, scheme: str) -> None:
    key = rf"Software\Classes\{scheme}"
    assert f'Subkey: "{key}"' in iss, f"{scheme}: no ProgID key"
    # Windows only treats a key as a protocol handler when this value exists,
    # empty or not -- without it the shell ignores the whole registration.
    assert re.search(
        rf'Subkey: "{re.escape(key)}";[^\n]*ValueName: "URL Protocol"', iss
    ), f"{scheme}: missing the 'URL Protocol' marker value"


@pytest.mark.parametrize("scheme", SCHEMES)
def test_each_scheme_opens_the_exe_with_the_url_as_one_argument(iss: str, scheme: str) -> None:
    command = rf"Software\Classes\{scheme}\shell\open\command"
    line = next((ln for ln in iss.splitlines() if f'Subkey: "{command}"' in ln), None)
    assert line is not None, f"{scheme}: no shell\\open\\command"
    # Both must be quoted: the install path contains spaces (Program Files),
    # and an unquoted %1 splits a URL on the first space it happens to carry.
    assert '""{app}\\{#MyAppExeName}""' in line, f"{scheme}: exe path not quoted"
    assert '""%1""' in line, f"{scheme}: %1 not quoted"


@pytest.mark.parametrize("scheme", SCHEMES)
def test_uninstalling_removes_each_scheme(iss: str, scheme: str) -> None:
    key = rf"Software\Classes\{scheme}"
    block = [ln for ln in iss.splitlines() if f'Subkey: "{key}";' in ln]
    assert any("uninsdeletekey" in ln for ln in block), (
        f"{scheme}: would be left behind after uninstall"
    )


def test_the_installer_claims_every_scheme_the_desktop_entry_does() -> None:
    # The two must not drift: a scheme tvtimes emits is useless on whichever
    # platform forgot to claim it, and Linux is where they get added first.
    desktop = DESKTOP.read_text()
    claimed = set(re.findall(r"x-scheme-handler/([a-z]+)", desktop))
    assert claimed == set(SCHEMES), f"desktop entry claims {claimed}, installer covers {SCHEMES}"


def test_associations_are_flagged_so_explorer_refreshes(iss: str) -> None:
    assert "ChangesAssociations=yes" in iss


def test_taking_over_m3u_stays_opt_in(iss: str) -> None:
    # .m3u is contested (VLC, MPC, Winamp); claiming it silently would be rude.
    # The URL schemes are ours alone and are deliberately *not* behind a task.
    assert re.search(r'Name: "assocm3u";[^\n]*Flags: unchecked', iss)
    for line in iss.splitlines():
        if r"Software\Classes\.m3u" in line:
            assert "Tasks: assocm3u" in line, f"unconditional .m3u grab: {line}"
