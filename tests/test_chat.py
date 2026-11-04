"""Google Chat messages: asked first, sent only after a yes, never for real."""

import asyncio
import json

import pytest

from alfred import chat as chat_module
from alfred.audit import AuditLog
from alfred.chat import CHAT_JOB, Chat
from alfred.config import SECRET_SETTINGS, ConfigError, load_config
from alfred.db import Database
from alfred.questions import Questions

FAMILY = "https://chat.googleapis.com/v1/spaces/FAMILY/messages?key=k&token=t"
WORK = "https://chat.googleapis.com/v1/spaces/WORK/messages?key=k&token=t"


def test_spaces_come_from_settings_and_stay_secret():
    config = load_config(
        {
            "OPENAI_API_KEY": "sk-test",
            "ALFRED_GOOGLE_CHAT_FAMILY": FAMILY,
            "ALFRED_GOOGLE_CHAT_WORK_TEAM": f" {WORK} ",
            "ALFRED_GOOGLE_CHAT_EMPTY": "",
        }
    )
    assert config.google_chat_spaces == {"family": FAMILY, "work team": WORK}
    assert "google_chat_spaces" in SECRET_SETTINGS
    with pytest.raises(ConfigError):
        load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_GOOGLE_CHAT_X": "https://evil.example"})


def make(tmp_path, spaces):
    questions = Questions(Database(tmp_path / "alfred.db"))
    audit_path = tmp_path / "audit.jsonl"
    chat = Chat(spaces, questions, AuditLog(audit_path))
    notes = []
    chat.on_done.append(notes.append)
    return chat, questions, notes, audit_path


def test_which_space_you_mean(tmp_path):
    chat, *_ = make(tmp_path, {"family": FAMILY, "default": WORK})
    assert chat.space("Family chat") == "family"
    assert chat.space("") == "default"
    assert chat.space("school") is None
    only, *_ = make(tmp_path, {"family": FAMILY})
    assert only.space("") == "family"


def test_sent_only_after_a_yes(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(chat_module, "post", lambda url, text: sent.append((url, text)))

    async def main():
        chat, questions, notes, audit_path = make(tmp_path, {"family": FAMILY})
        sending = chat.start_sending("On my way!", "family")
        await asyncio.sleep(0.01)
        assert questions.waiting()[0].text == 'Shall I send "On my way!" to the family chat?'
        assert chat.start_sending("Another", "family") is None  # one at a time
        questions.answer(CHAT_JOB, "yes")
        await sending
        assert sent == [(FAMILY, "On my way!")]
        assert "was sent" in notes[-1]
        outcomes = [json.loads(line)["outcome"] for line in audit_path.read_text().splitlines()]
        assert outcomes == ["approved", "sent"]
        assert "key=" not in audit_path.read_text()  # the address is never written down

    asyncio.run(main())


def test_a_no_or_a_failure_sends_nothing(tmp_path, monkeypatch):
    def broken(url, text):
        raise OSError("network down")

    monkeypatch.setattr(chat_module, "post", broken)

    async def main():
        chat, questions, notes, audit_path = make(tmp_path, {"family": FAMILY})
        sending = chat.start_sending("Hi", "family")
        await asyncio.sleep(0.01)
        questions.answer(CHAT_JOB, "no")
        await sending
        assert "not sent (declined)" in notes[-1]

        sending = chat.start_sending("Hi", "family")
        await asyncio.sleep(0.01)
        questions.answer(CHAT_JOB, "yes")
        await sending
        assert "failed (OSError)" in notes[-1]
        outcomes = [json.loads(line)["outcome"] for line in audit_path.read_text().splitlines()]
        assert outcomes == ["declined", "approved", "failed"]

    asyncio.run(main())
