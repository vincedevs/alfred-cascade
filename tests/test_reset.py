"""alfred-reset: deletes what Alfred keeps, never settings, never while he runs."""

import os
import sys

import pytest

from alfred import reset
from alfred.lock import only_one_alfred


@pytest.fixture
def alfred_home(tmp_path, monkeypatch):
    data = tmp_path / "alfred-data"
    (data / "workspace" / "www" / "snake").mkdir(parents=True)
    (data / "workspace" / "www" / "snake" / "index.html").write_text("<canvas>")
    (data / "workspace" / "notes.md").write_text("ramen")
    (data / "profile.md").write_text("- Is vegetarian.")
    (data / "alfred.db").write_text("db")
    logs = tmp_path / "logs"
    (logs / "pi").mkdir(parents=True)
    (logs / "alfred-1.log").write_text("log")
    (logs / "pi" / "alfred-pi.log").write_text("kept")
    home = tmp_path / "home"
    notes = reset.claude_notes(data / "workspace").relative_to(reset.Path.home())
    (home / notes).mkdir(parents=True)
    (home / ".claude" / "projects" / "-my-own-project").mkdir(parents=True)
    monkeypatch.setattr(reset, "LOG_DIR", logs)
    monkeypatch.setattr(reset.Path, "home", lambda: home)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ALFRED_DATA", str(data))
    return data, logs, home


def run(monkeypatch, *args):
    from alfred.config import load_config

    monkeypatch.setattr(reset, "load_config", lambda: load_config(dict(os.environ)))
    monkeypatch.setattr(sys, "argv", ["alfred-reset", *args])
    reset.main()


def test_only_the_web_pages(alfred_home, monkeypatch):
    data, _, _ = alfred_home
    run(monkeypatch, "web", "--yes")
    assert not (data / "workspace" / "www").exists()
    assert (data / "workspace" / "notes.md").exists() and (data / "profile.md").exists()


def test_everything_but_not_other_claude_projects_or_copied_logs(alfred_home, monkeypatch):
    data, logs, home = alfred_home
    run(monkeypatch, "--yes")
    assert [p.name for p in data.iterdir()] == ["alfred.lock"]  # only the reset's own lock
    assert not (logs / "alfred-1.log").exists() and (logs / "pi" / "alfred-pi.log").exists()
    projects = [p.name for p in (home / ".claude" / "projects").iterdir()]
    assert projects == ["-my-own-project"]  # your own Claude Code sessions stay


def test_refuses_while_alfred_runs(alfred_home, monkeypatch):
    data, _, _ = alfred_home
    running = only_one_alfred(data)
    with pytest.raises(SystemExit):
        run(monkeypatch, "--yes")
    assert (data / "profile.md").exists()
    running.close()


def test_asks_first(alfred_home, monkeypatch):
    data, _, _ = alfred_home
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    run(monkeypatch, "profile")
    assert (data / "profile.md").exists()
