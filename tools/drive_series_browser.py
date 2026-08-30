"""Drive tvdinner's Series browser end-to-end against the fake Xtream
panel, with real mpv and real keypresses -- the manual "does it actually
work on screen" check that the pytest integration test can't do.

Needs an X/Wayland display, mpv, ffmpeg + xwd + xwininfo, and the sample
media (run tools/make_sample_media.sh first). Writes screenshots to
tools/shots/ and a copy of the run log next to this script.

    python tools/drive_series_browser.py
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PORT = 9977
# AF_UNIX sun_path caps near 108 chars, so the ipc socket must be short.
SOCK = "/tmp/tvd-drive-ipc.sock"
LOG = HERE / "drive.log"
SHOTS = HERE / "shots"

env = dict(os.environ, TVDINNER_IPC_SOCK=SOCK, FAKE_PANEL_VERBOSE="1")


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def wait_for(pred, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.3)
    raise SystemExit(f"timed out waiting for {what}")


def ipc(*cmd):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(SOCK)
        s.sendall((json.dumps({"command": list(cmd)}) + "\n").encode())
        s.settimeout(2)
        try:
            return s.recv(65536).decode().strip()
        except socket.timeout:
            return ""


def key(k):
    print(f"  -> keypress {k}")
    ipc("keypress", k)
    time.sleep(1.5)


def find_window():
    for line in sh(["xwininfo", "-root", "-tree"]).stdout.splitlines():
        low = line.lower()
        if ("mpv" in low or "tvdinner" in low) and "0x" in line:
            return line.strip().split()[0]
    return None


def shot(name):
    win = find_window()
    if not win:
        print(f"  !! no window for shot {name}")
        return
    xwd, png = SHOTS / f"{name}.xwd", SHOTS / f"{name}.png"
    sh(["xwd", "-id", win, "-out", str(xwd)])
    sh(["ffmpeg", "-y", "-i", str(xwd), "-update", "1", "-frames:v", "1", str(png)])
    xwd.unlink(missing_ok=True)
    print(f"  [shot] {png}")


def main():
    if not (HERE / "sample.mp4").exists() or not (HERE / "sample.ts").exists():
        sys.exit("missing sample media -- run tools/make_sample_media.sh first")

    SHOTS.mkdir(exist_ok=True)
    for f in SHOTS.glob("*"):
        f.unlink()
    LOG.unlink(missing_ok=True)
    Path(SOCK).unlink(missing_ok=True)

    panel = subprocess.Popen([sys.executable, str(HERE / "fake_xtream_panel.py"), str(PORT)], env=env)
    app = None
    try:
        time.sleep(1.0)
        app = subprocess.Popen(
            [
                sys.executable,
                str(HERE / "run_tvdinner_ipc.py"),
                f"xtream://test:test@127.0.0.1:{PORT}",
                "--disable-full-screen",
                "--no-update-check",
                "--no-online-logos",
                "--no-epg-cache",
                "--log-file",
                str(LOG),
            ],
            env=env,
        )

        wait_for(lambda: os.path.exists(SOCK), 30, "mpv ipc socket")
        wait_for(find_window, 30, "mpv window")
        time.sleep(5)
        shot("00_live")

        print("open series browser (l)")
        key("l")
        shot("01_categories")

        print("drill: Drama -> The Sample Detectives -> Season 1")
        key("ENTER")
        shot("02_series_list")
        key("ENTER")
        shot("03_seasons")
        key("ENTER")
        shot("04_episodes")

        print("select episode 3 (Teardown, .mkv)")
        key("DOWN")
        key("DOWN")
        key("ENTER")
        time.sleep(4)
        shot("05_playing_episode")

        print("info overlay on the playing episode (i)")
        key("i")
        shot("06_episode_info")

        log = LOG.read_text() if LOG.exists() else ""
        print("\n===== log signals =====")
        ok = "Playing series episode" in log
        for needle in ("Series browser opened", "Series browser closed", "Playing series episode", "Series error"):
            hits = [ln for ln in log.splitlines() if needle in ln]
            print("\n".join("  " + h for h in hits) or f"  (none) {needle}")
        print("\nPASS" if ok else "\nFAIL -- check tools/shots/ and the log above")
        return 0 if ok else 1
    finally:
        if app:
            try:
                ipc("quit")
            except OSError:
                pass
            time.sleep(1)
            app.terminate()
            try:
                app.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app.kill()
        panel.terminate()
        panel.wait()
        Path(SOCK).unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
