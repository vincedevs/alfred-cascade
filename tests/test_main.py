"""Ctrl+C ends Alfred without a scary-looking (and harmless) "Event loop is closed"."""

import sys
from types import SimpleNamespace

from alfred import __main__ as alfred_main


def test_the_closed_loop_message_is_hidden(monkeypatch):
    reported = []
    monkeypatch.setattr(sys, "__unraisablehook__", reported.append)
    closed = SimpleNamespace(exc_value=RuntimeError("Event loop is closed"))
    alfred_main.quiet_closed_loop_noise(closed)
    assert reported == []

    other = SimpleNamespace(exc_value=ValueError("something else"))
    alfred_main.quiet_closed_loop_noise(other)
    assert reported == [other]  # anything else is still reported
