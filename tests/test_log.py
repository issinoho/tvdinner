import logging

import tvdinner.log as log_module
from tvdinner.log import close_logging, configure_logging


def _added_handlers(root, before):
    return [h for h in root.handlers if h not in before]


def _cleanup(root, before):
    for handler in _added_handlers(root, before):
        root.removeHandler(handler)
        handler.close()


def test_configure_logging_writes_to_the_given_file(tmp_path):
    log_path = tmp_path / "test.log"
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging(log_path)
        logging.getLogger("tvdinner.test").info("hello")
        for handler in _added_handlers(root, before):
            handler.flush()
        assert log_path.is_file()
        assert "hello" in log_path.read_text()
    finally:
        _cleanup(root, before)


def test_configure_logging_is_idempotent_for_the_same_path(tmp_path):
    # tvdinner bookmarks configures logging itself, then re-enters main()
    # (which also calls this) when a bookmark is launched -- calling twice
    # for the same file must not attach a second handler and double-write
    # every line.
    log_path = tmp_path / "test.log"
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging(log_path)
        configure_logging(log_path)
        assert len(_added_handlers(root, before)) == 1
    finally:
        _cleanup(root, before)


def test_configure_logging_none_is_a_noop(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    configure_logging(None)
    assert _added_handlers(root, before) == []


def test_close_logging_detaches_and_closes_the_handler_for_that_path(tmp_path):
    log_path = tmp_path / "test.log"
    root = logging.getLogger()
    before = list(root.handlers)
    configure_logging(log_path)
    assert len(_added_handlers(root, before)) == 1

    close_logging(log_path)

    assert _added_handlers(root, before) == []


def test_close_logging_allows_the_file_to_be_deleted_afterward(tmp_path):
    # The whole point: on Windows, a still-open FileHandler keeps its
    # target locked, so deleting a log file this same process just wrote
    # to (see cli.py's hard-reset command) needs the handler actually
    # closed first, not just detached.
    log_path = tmp_path / "test.log"
    root = logging.getLogger()
    before = list(root.handlers)
    configure_logging(log_path)
    close_logging(log_path)
    log_path.unlink()  # would raise on Windows if the handler were still open
    assert not log_path.exists()


def test_close_logging_is_a_noop_for_a_path_never_configured(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    close_logging(tmp_path / "never-configured.log")
    assert _added_handlers(root, before) == []


def test_configure_logging_rotates_to_a_backup_once_max_bytes_is_exceeded(tmp_path, monkeypatch):
    monkeypatch.setattr(log_module, "MAX_LOG_BYTES", 200)
    log_path = tmp_path / "test.log"
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging(log_path)
        logger = logging.getLogger("tvdinner.test")
        for _ in range(50):
            logger.info("x" * 20)
        assert (tmp_path / "test.log.1").is_file()
        assert log_path.stat().st_size <= 200
    finally:
        _cleanup(root, before)


def test_configure_logging_keeps_only_one_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(log_module, "MAX_LOG_BYTES", 200)
    log_path = tmp_path / "test.log"
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging(log_path)
        logger = logging.getLogger("tvdinner.test")
        for _ in range(200):
            logger.info("x" * 20)
        assert (tmp_path / "test.log.1").is_file()
        assert not (tmp_path / "test.log.2").exists()
    finally:
        _cleanup(root, before)


def test_close_logging_none_is_a_noop():
    root = logging.getLogger()
    before = list(root.handlers)
    close_logging(None)
    assert _added_handlers(root, before) == []
