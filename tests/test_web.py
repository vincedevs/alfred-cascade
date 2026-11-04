"""Sharing jobs' web pages: only www/ is served, read-only, and only when turned on."""

import os
import urllib.error
import urllib.request

import pytest

from alfred.config import ConfigError, load_config
from alfred.web import WWW, job_text, start_web


def fetch(web, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{web.port}{path}", timeout=5) as reply:
            return reply.status, reply.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, ""


def test_off_unless_a_port_is_set(tmp_path):
    assert start_web(tmp_path, None) is None
    assert load_config({"OPENAI_API_KEY": "sk-test"}).web_port is None
    assert load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_WEB_PORT": "8080"}).web_port == 8080
    with pytest.raises(ConfigError):
        load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_WEB_PORT": "80"})


def test_serves_pages_and_nothing_else(tmp_path):
    (tmp_path / "secret.txt").write_text("private notes")
    game = tmp_path / WWW / "flappy-bird"
    game.mkdir(parents=True)
    (game / "index.html").write_text("<h1>Flappy</h1>")
    os.symlink(tmp_path / "secret.txt", tmp_path / WWW / "sneaky.txt")  # a job's trick

    web = start_web(tmp_path, 0)  # port 0: any free port
    try:
        assert fetch(web, "/flappy-bird/") == (200, "<h1>Flappy</h1>")
        status, listing = fetch(web, "/")
        assert status == 200 and "flappy-bird" in listing
        assert fetch(web, "/sneaky.txt")[0] == 404  # links out of www/ are refused
        assert fetch(web, "/../secret.txt")[0] == 404
        assert fetch(web, "/%2e%2e/secret.txt")[0] == 404
        assert f":{web.port}/" in web.address_text()
    finally:
        web.stop()


def test_jobs_are_told_where_pages_go():
    text = job_text(8080)
    assert "www/<short-name>/index.html" in text and ":8080/<short-name>/" in text
