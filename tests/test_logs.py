"""Old logs are deleted at startup."""

import os
import time

import pytest

from alfred import logs


def test_logs_older_than_two_weeks_are_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    old, new, other = tmp_path / "alfred-old.log", tmp_path / "alfred-new.log", tmp_path / "x.txt"
    for path in (old, new, other):
        path.write_text("...")
    month_ago = time.time() - 30 * 86400
    os.utime(old, (month_ago, month_ago))
    os.utime(other, (month_ago, month_ago))
    assert logs.delete_old_logs() == 1
    assert not old.exists() and new.exists() and other.exists()


def test_library_messages_reach_our_log():
    import logging

    from loguru import logger

    from alfred.logs import capture_library_logs

    if type(logger).__name__ != "Logger":
        pytest.skip("needs the real loguru")
    seen = []
    sink = logger.add(lambda message: seen.append(message.record["message"]), level="DEBUG")
    capture_library_logs("mcp")
    logging.getLogger("mcp.client.streamable_http").info("GET stream disconnected")
    logger.remove(sink)
    assert seen == ["[mcp] GET stream disconnected"]
