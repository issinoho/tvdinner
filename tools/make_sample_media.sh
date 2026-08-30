#!/usr/bin/env bash
# Generate the throwaway sample streams tools/fake_xtream_panel.py serves
# for /live, /movie and /series URLs. Only tools/drive_series_browser.py
# needs these; the pytest integration test never fetches a stream.
#
# Gitignored on purpose -- they're a few MB of ffmpeg test pattern, not
# source. Regenerate any time.
set -euo pipefail
cd "$(dirname "$0")"

common=(-f lavfi -i "testsrc=size=640x360:rate=25:duration=90"
        -f lavfi -i "sine=frequency=330:duration=90"
        -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest)

# MPEG-TS for /live (linear, no moov -- plays as a stream)
ffmpeg -y "${common[@]}" -f mpegts sample.ts

# faststart MP4 for /movie and /series (seekable, like a real VOD file)
ffmpeg -y "${common[@]}" -movflags +faststart sample.mp4

echo "wrote $(pwd)/sample.ts and $(pwd)/sample.mp4"
