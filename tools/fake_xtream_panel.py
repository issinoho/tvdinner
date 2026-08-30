"""A fake Xtream Codes panel: just enough of ``player_api.php`` to drive
tvdinner's live/VOD/series code paths without a real subscription.

Two consumers:

* ``tests/test_xtream_series_integration.py`` imports :func:`make_server`
  and exercises ``tvdinner.xtream``'s real functions over real HTTP (no
  ``requests`` monkeypatching), walking the whole
  category -> series -> season -> episode -> resolve path.
* ``tools/drive_series_browser.py`` runs it as a subprocess and drives
  the actual mpv UI against it (see that script / ``tools/README.md``).

The JSON is modelled on the documented Xtream API response shapes
(``get_series_categories`` / ``get_series[&category_id=]`` /
``get_series_info&series_id=``). It is *not* a promise that any given
real panel matches -- panels vary; this only pins tvdinner's side of the
contract.

Stream URLs (``/live``, ``/movie``, ``/series``) serve ``sample.mp4`` /
``sample.ts`` from this directory when present (generate them with
``tools/make_sample_media.sh``); without them those routes return 404,
which is fine for the integration test since it never fetches a stream.

Usage:  python tools/fake_xtream_panel.py [port]   (default 9977)
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
SAMPLE_MP4 = HERE / "sample.mp4"
SAMPLE_TS = HERE / "sample.ts"

USER = "test"
PASSWORD = "test"

# --- live channels (minimal -- just enough for load_xtream_playlist) ---

LIVE_CATEGORIES = [{"category_id": "1", "category_name": "General"}]
LIVE_STREAMS = [
    {
        "stream_id": 1001,
        "name": "Test Channel One",
        "category_id": "1",
        "epg_channel_id": "test.one",
        "stream_icon": "",
    }
]

XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv generator-info-name="fake-xtream-panel">
  <channel id="test.one"><display-name>Test Channel One</display-name></channel>
  <programme start="20260101000000 +0000" stop="20260101235900 +0000" channel="test.one">
    <title>All Day Test Programme</title>
    <desc>Placeholder EPG entry.</desc>
  </programme>
</tv>
"""

# --- VOD (deliberately empty; the Series browser is what we exercise) ---

VOD_CATEGORIES: list[dict] = []
VOD_STREAMS: list[dict] = []

# --- TV series tree ---

SERIES_CATEGORIES = [
    {"category_id": "10", "category_name": "Drama", "parent_id": 0},
    {"category_id": "11", "category_name": "Comedy", "parent_id": 0},
]

# get_series (list rows) -- keyed by category for the &category_id= filter.
SERIES_BY_CATEGORY = {
    "10": [
        {
            "num": 1,
            "name": "The Sample Detectives",
            "series_id": 500,
            "cover": "",
            "plot": "Two detectives solve procedurally generated crimes.",
            "genre": "Drama",
            "releaseDate": "2019-03-01",
            "last_modified": "1610000000",
            "rating": "8.4",
            "rating_5based": 4.2,
            "category_id": "10",
        },
        {
            "num": 2,
            "name": "Testing In The Dark",
            "series_id": 501,
            "cover": "",
            "plot": "A lone QA engineer versus an untested codebase.",
            "genre": "Drama",
            "releaseDate": "2021-09-15",
            "last_modified": "1631700000",
            "rating": "0",
            "rating_5based": 0,
            "category_id": "10",
        },
    ],
    "11": [
        {
            "num": 1,
            "name": "Regression Road",
            "series_id": 502,
            "cover": "",
            "plot": "Sitcom about a house share of flaky integration tests.",
            "genre": "Comedy",
            "releaseDate": "2023-01-10",
            "last_modified": "1673300000",
            "rating": "7.1",
            "rating_5based": 3.5,
            "category_id": "11",
        }
    ],
}


def _episode(ep_id: int, num: int, title: str, ext: str = "mp4") -> dict:
    return {
        "id": str(ep_id),
        "episode_num": num,
        "title": title,
        "container_extension": ext,
        "info": {"plot": f"Episode {num}: {title}", "duration_secs": 12, "rating": 8},
        "custom_sid": "",
        "added": "1610000000",
        "season": 1,
        "direct_source": "",
    }


# get_series_info responses, keyed by series_id (str).
SERIES_INFO = {
    "500": {
        "info": {
            "name": "The Sample Detectives",
            "cover": "",
            "plot": "Two detectives solve procedurally generated crimes.",
            "genre": "Drama",
            "releaseDate": "2019-03-01",
            "rating": "8.4",
        },
        "seasons": [
            {
                "air_date": "2019-03-01",
                "episode_count": 3,
                "id": 1,
                "name": "Season 1",
                "season_number": 1,
                "cover": "",
                "cover_big": "",
            },
            {
                "air_date": "2020-04-01",
                "episode_count": 2,
                "id": 2,
                "name": "Season 2",
                "season_number": 2,
                "cover": "",
                "cover_big": "",
            },
        ],
        "episodes": {
            "1": [
                _episode(90001, 1, "The Empty Stub"),
                _episode(90002, 2, "A Flaky Witness"),
                _episode(90003, 3, "Teardown", ext="mkv"),
            ],
            "2": [
                _episode(90011, 1, "Return of the Fixture"),
                _episode(90012, 2, "The Golden Path"),
            ],
        },
    },
    "501": {
        "info": {"name": "Testing In The Dark", "plot": "", "releaseDate": "2021-09-15"},
        "seasons": [
            {"episode_count": 2, "id": 1, "name": "Season 1", "season_number": 1, "cover": "", "cover_big": ""}
        ],
        "episodes": {
            "1": [
                _episode(91001, 1, "Cold Start"),
                _episode(91002, 2, "Coverage Gap"),
            ]
        },
    },
    "502": {
        "info": {"name": "Regression Road", "plot": "", "releaseDate": "2023-01-10"},
        "seasons": [
            {"episode_count": 1, "id": 1, "name": "Season 1", "season_number": 1, "cover": "", "cover_big": ""}
        ],
        "episodes": {"1": [_episode(92001, 1, "Pilot (Again)")]},
    },
}

ALL_SERIES = [row for rows in SERIES_BY_CATEGORY.values() for row in rows]

_VERBOSE = bool(os.environ.get("FAKE_PANEL_VERBOSE"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if _VERBOSE:
            sys.stderr.write("fake-panel: " + (fmt % args) + "\n")

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, content_type="text/plain"):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_sample(self, path):
        # Real MPEG-TS for /live (linear, no moov, no seeking needed);
        # faststart MP4 for /movie and /series, with byte-range support
        # so mpv treats it as seekable like a real VOD endpoint.
        src = SAMPLE_TS if path.startswith("/live/") else SAMPLE_MP4
        if not src.exists():
            return self.send_error(404, f"no {src.name} -- run tools/make_sample_media.sh")
        ctype = "video/mp2t" if src is SAMPLE_TS else "video/mp4"
        data = src.read_bytes()

        total = len(data)
        rng = self.headers.get("Range")
        start, end = 0, total - 1
        if rng and rng.startswith("bytes="):
            lo, _, hi = rng[6:].partition("-")
            start = int(lo) if lo else 0
            end = min(int(hi), total - 1) if hi else total - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        try:
            self.wfile.write(data[start : end + 1])
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path.rstrip("/").endswith("/xmltv.php"):
            return self._text(XMLTV, "application/xml")
        if "/player_api.php" in path:
            return self._player_api(params)
        if path.startswith(("/live/", "/movie/", "/series/")):
            return self._stream_sample(path)
        self.send_error(404, f"no route for {path}")

    def _player_api(self, params):
        if params.get("username") != USER or params.get("password") != PASSWORD:
            return self._json({"user_info": {"auth": 0}})

        action = params.get("action")
        if action is None:
            return self._json(
                {
                    "user_info": {"auth": 1, "status": "Active", "username": USER},
                    "server_info": {"url": self.headers.get("Host", "127.0.0.1"), "port": "0"},
                }
            )
        if action == "get_live_categories":
            return self._json(LIVE_CATEGORIES)
        if action == "get_live_streams":
            return self._json(LIVE_STREAMS)
        if action == "get_vod_categories":
            return self._json(VOD_CATEGORIES)
        if action == "get_vod_streams":
            return self._json(VOD_STREAMS)
        if action == "get_series_categories":
            return self._json(SERIES_CATEGORIES)
        if action == "get_series":
            cat = params.get("category_id")
            return self._json(SERIES_BY_CATEGORY.get(cat, ALL_SERIES) if cat else ALL_SERIES)
        if action == "get_series_info":
            return self._json(SERIES_INFO.get(params.get("series_id"), {}))

        sys.stderr.write(f"fake-panel: UNHANDLED action={action!r} params={params!r}\n")
        return self._json([])


def make_server(port: int = 0, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """A not-yet-serving ThreadingHTTPServer. ``port=0`` lets the OS pick
    a free one -- read it back from ``server.server_address[1]``. The
    caller runs ``serve_forever()`` (typically on a daemon thread) and
    ``shutdown()``."""
    return ThreadingHTTPServer((host, port), Handler)


def base_url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}"


def xtream_url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"xtream://{USER}:{PASSWORD}@{host}:{port}"


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9977
    server = make_server(port)
    sys.stderr.write(f"fake-panel: {base_url(server)}  ({xtream_url(server)})\n")
    if not SAMPLE_MP4.exists() or not SAMPLE_TS.exists():
        sys.stderr.write("fake-panel: no sample media -- stream URLs will 404 (run tools/make_sample_media.sh)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
