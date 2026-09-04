"""Guides that already carry their clock correction.

Reported live: TCM US West had a +3h shift set in tvtimes *and* a matching
--epg-shifts entry here. tvtimes corrects times on export, so both applied
and the guide sat 3 hours in the past -- tvdinner showed "The Killing"
(which really aired at 05:55) while tvtimes correctly showed "Paths of
Glory" at 07:30. Every programme appeared exactly one shift late.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tvdinner.epg import Epg, EpgDisplay, load_epg_for_playlist, parse_xmltv
from tvdinner.m3u import Channel, Playlist

TVTIMES = """<?xml version="1.0" encoding="UTF-8"?>
<tv generator-info-name="tvtimes">
  <channel id="c1"><display-name>TCM US West</display-name></channel>
  <programme start="20260904073000 +0000" stop="20260904091500 +0000" channel="c1">
    <title>Paths of Glory</title>
  </programme>
</tv>"""

PLAIN = TVTIMES.replace(' generator-info-name="tvtimes"', "")
OTHER = TVTIMES.replace('"tvtimes"', '"some-other-grabber"')


def test_the_generator_is_read_off_the_feed():
    assert parse_xmltv(TVTIMES).generator == "tvtimes"
    assert parse_xmltv(PLAIN).generator is None


def test_a_tvtimes_guide_is_flagged_as_already_corrected():
    assert parse_xmltv(TVTIMES).times_already_corrected is True


def test_any_other_guide_is_not():
    # The default has to stay "correct it ourselves" -- every other source
    # ships raw provider times.
    assert parse_xmltv(PLAIN).times_already_corrected is False
    assert parse_xmltv(OTHER).times_already_corrected is False
    assert Epg().times_already_corrected is False


def test_the_generator_name_is_matched_loosely():
    loose = TVTIMES.replace('"tvtimes"', '"  TVtimes  "')
    assert parse_xmltv(loose).times_already_corrected is True


def test_shifts_apply_normally_to_an_ordinary_guide():
    display = EpgDisplay(channel_shifts={"TCM US West": timedelta(hours=3)})
    assert display.shift_for("TCM US West") == timedelta(hours=3)


def test_shifts_are_suppressed_for_a_pre_corrected_guide():
    display = EpgDisplay(channel_shifts={"TCM US West": timedelta(hours=3)})
    display.guide_already_corrected = True
    assert display.shift_for("TCM US West") == timedelta()


def test_the_default_shift_is_suppressed_too():
    # --time-shift applies to every channel without an override, so it
    # double-shifts a pre-corrected guide exactly the same way.
    display = EpgDisplay(default_shift=timedelta(hours=3))
    display.guide_already_corrected = True
    assert display.shift_for("anything") == timedelta()


def test_suppressing_does_not_discard_the_stored_shift():
    # It's keyed by channel name, and the same channel watched direct from
    # its provider still needs it.
    display = EpgDisplay(channel_shifts={"TCM US West": timedelta(hours=3)})
    display.guide_already_corrected = True
    assert display.shift_for("TCM US West") == timedelta()
    display.guide_already_corrected = False
    assert display.shift_for("TCM US West") == timedelta(hours=3)


REAL_CASE = """<?xml version="1.0" encoding="UTF-8"?>
<tv generator-info-name="tvtimes">
  <channel id="c1"><display-name>TCM US West</display-name></channel>
  <programme start="20260904055500 +0000" stop="20260904071500 +0000" channel="c1">
    <title>The Killing</title>
  </programme>
  <programme start="20260904073000 +0000" stop="20260904091500 +0000" channel="c1">
    <title>Paths of Glory</title>
  </programme>
</tv>"""


def _now_playing(display: EpgDisplay, epg: Epg, at: datetime) -> str | None:
    current, _next = display.now_and_next(epg, "c1", at, channel_name="TCM US West")
    return current.title if current else None


def test_the_reported_double_shift_end_to_end():
    # 09:05, the moment from the report. tvtimes says Paths of Glory.
    at = datetime(2026, 9, 4, 9, 5, tzinfo=timezone.utc)
    epg = parse_xmltv(REAL_CASE)
    display = EpgDisplay(channel_shifts={"TCM US West": timedelta(hours=3)})

    # Without the fix the stored +3 lands on top of tvtimes' own, and the
    # guide picks the programme from three hours earlier.
    assert _now_playing(display, epg, at) == "The Killing"

    display.guide_already_corrected = epg.times_already_corrected
    assert _now_playing(display, epg, at) == "Paths of Glory"


# --- through the real loader ------------------------------------------------
#
# The unit tests above pass parse_xmltv's own Epg around. The app never does:
# load_epg_for_playlist builds a *fresh* Epg and merges each source into it,
# so a field the merge forgets never reaches the guide that gets rendered.
# That is exactly how the first attempt at this fix shipped broken -- every
# test passed and nothing changed on screen.


def _playlist() -> Playlist:
    return Playlist(
        channels=[Channel(name="TCM US West", url="http://x/s", tvg_id="c1")],
        epg_url="http://guide.example/epg.xml",
    )


def _loader(monkeypatch, *bodies: str) -> None:
    xml = iter(bodies)
    monkeypatch.setattr(
        "tvdinner.epg.load_epg",
        lambda source, **kw: parse_xmltv(next(xml)),
    )


def test_the_generator_survives_the_real_loading_path(monkeypatch):
    _loader(monkeypatch, TVTIMES)
    epg = load_epg_for_playlist(_playlist())
    assert epg is not None
    assert epg.times_already_corrected is True


def test_an_ordinary_guide_still_reports_not_corrected(monkeypatch):
    _loader(monkeypatch, PLAIN)
    epg = load_epg_for_playlist(_playlist())
    assert epg is not None
    assert epg.times_already_corrected is False


def test_merging_a_raw_source_alongside_tvtimes_keeps_shifts_working(monkeypatch):
    # Suppression is whole-guide, so one raw feed in the mix has to win --
    # otherwise the channels the shifts exist for silently stop being shifted.
    _loader(monkeypatch, TVTIMES, PLAIN)
    playlist = _playlist()
    playlist.epg_url = "http://a/epg.xml,http://b/epg.xml"
    epg = load_epg_for_playlist(playlist)
    assert epg is not None
    assert epg.times_already_corrected is False
