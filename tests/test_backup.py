import zipfile

from tvdinner.backup import create_backup, restore_backup


def test_create_backup_includes_only_existing_files(tmp_path):
    epg_shifts = tmp_path / "config" / "epg_shifts.json"
    epg_shifts.parent.mkdir(parents=True)
    epg_shifts.write_text('{"BBC One": "+1h"}')
    favorites = tmp_path / "config" / "favorites.json"  # not created
    bookmarks = tmp_path / "config" / "bookmarks.json"
    bookmarks.write_text("[]")

    output = tmp_path / "backup.zip"
    included = create_backup(
        output,
        {"epg_shifts.json": epg_shifts, "favorites.json": favorites, "bookmarks.json": bookmarks},
    )

    assert set(included) == {"epg_shifts.json", "bookmarks.json"}
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"epg_shifts.json", "bookmarks.json"}
        assert archive.read("epg_shifts.json") == b'{"BBC One": "+1h"}'


def test_create_backup_creates_parent_directory(tmp_path):
    epg_shifts = tmp_path / "epg_shifts.json"
    epg_shifts.write_text("{}")

    output = tmp_path / "nested" / "backup.zip"
    create_backup(output, {"epg_shifts.json": epg_shifts})

    assert output.is_file()


def test_restore_backup_writes_files_and_creates_parent_directories(tmp_path):
    source_epg_shifts = tmp_path / "source" / "epg_shifts.json"
    source_epg_shifts.parent.mkdir(parents=True)
    source_epg_shifts.write_text('{"BBC One": "+1h"}')

    output = tmp_path / "backup.zip"
    create_backup(output, {"epg_shifts.json": source_epg_shifts})

    dest_epg_shifts = tmp_path / "restored" / "epg_shifts.json"
    restored, unknown = restore_backup(output, {"epg_shifts.json": dest_epg_shifts})

    assert restored == ["epg_shifts.json"]
    assert unknown == []
    assert dest_epg_shifts.read_text() == '{"BBC One": "+1h"}'


def test_restore_backup_overwrites_existing_file(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"new": true}')
    output = tmp_path / "backup.zip"
    create_backup(output, {"epg_shifts.json": source})

    dest = tmp_path / "epg_shifts.json"
    dest.write_text('{"old": true}')
    restore_backup(output, {"epg_shifts.json": dest})

    assert dest.read_text() == '{"new": true}'


def test_restore_backup_reports_unknown_entries(tmp_path):
    output = tmp_path / "backup.zip"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("epg_shifts.json", "{}")
        archive.writestr("some_future_file.json", "{}")

    dest = tmp_path / "epg_shifts.json"
    restored, unknown = restore_backup(output, {"epg_shifts.json": dest})

    assert restored == ["epg_shifts.json"]
    assert unknown == ["some_future_file.json"]


def test_restore_backup_round_trips_through_create_backup(tmp_path):
    sources = {
        "epg_shifts.json": tmp_path / "src" / "epg_shifts.json",
        "favorites.json": tmp_path / "src" / "favorites.json",
        "bookmarks.json": tmp_path / "src" / "bookmarks.json",
    }
    for name, path in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"contents of {name}")

    output = tmp_path / "backup.zip"
    create_backup(output, sources)

    destinations = {name: tmp_path / "dst" / name for name in sources}
    restored, unknown = restore_backup(output, destinations)

    assert set(restored) == set(sources)
    assert unknown == []
    for name, path in destinations.items():
        assert path.read_text() == f"contents of {name}"
