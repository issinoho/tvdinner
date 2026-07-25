import logging

from tvdinner.log import configure_logging


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
