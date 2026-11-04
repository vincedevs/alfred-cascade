"""Jobs asking you questions, Alfred's tools for jobs, and sending email (never for real)."""

import asyncio

from alfred import mail
from alfred.agent_tools import agent_tools
from alfred.config import Config
from alfred.db import Database
from alfred.questions import Questions, is_yes


def test_yes_means_only_yes():
    assert is_yes("yes") and is_yes("Yes.") and is_yes(" yep ")
    assert not is_yes("no") and not is_yes("maybe") and not is_yes(None) and not is_yes("")


def test_an_answer_reaches_the_waiting_job(tmp_path):
    async def main():
        questions = Questions(Database(tmp_path / "alfred.db"))
        asking = asyncio.create_task(questions.ask(7, "clarify", "This year or five years?"))
        await asyncio.sleep(0.01)
        assert [q.text for q in questions.waiting()] == ["This year or five years?"]
        assert questions.answer(7, "five years") is not None
        assert await asking == "five years"
        assert questions.waiting() == []
        assert questions.answer(7, "again") is None  # nothing left to answer

    asyncio.run(main())


def test_questions_from_before_a_restart_expire(tmp_path):
    async def main():
        db = Database(tmp_path / "alfred.db")
        db.add_question(3, "approve", "May I send it?")
        assert Questions(db).waiting() == []

    asyncio.run(main())


def test_a_stopped_job_stops_asking(tmp_path):
    async def main():
        questions = Questions(Database(tmp_path / "alfred.db"))
        asking = asyncio.create_task(questions.ask(4, "approve", "May I?"))
        await asyncio.sleep(0.01)
        questions.cancel_for_task(4)
        await asyncio.wait([asking])
        assert asking.cancelled() and questions.waiting() == []

    asyncio.run(main())


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, message):
        FakeSMTP.sent.append((self.user, message["To"], message["Subject"]))


def setup(tmp_path, monkeypatch, **email):
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    config = Config(openai_api_key="sk-test", data_dir=tmp_path, **email)
    questions = Questions(Database(tmp_path / "alfred.db"))
    ask_user, send_email = agent_tools(1, config, questions)
    return questions, ask_user, send_email


def test_send_email_tool_sends_through_gmail(tmp_path, monkeypatch):
    async def main():
        _, _, send_email = setup(
            tmp_path, monkeypatch, email_address="me@gmail.com", email_app_password="abcd"
        )
        args = {"to": "maria@example.com", "subject": "Dinner", "body": "Moved to 8."}
        result = await send_email.handler(args)
        assert result["content"][0]["text"] == "Sent."
        assert FakeSMTP.sent == [("me@gmail.com", "maria@example.com", "Dinner")]

    asyncio.run(main())


def test_send_email_without_setup_fails_politely(tmp_path, monkeypatch):
    async def main():
        _, _, send_email = setup(tmp_path, monkeypatch)
        result = await send_email.handler({"to": "a@b.c", "subject": "s", "body": "b"})
        assert result.get("is_error") and "not set up" in result["content"][0]["text"]
        assert FakeSMTP.sent == []

    asyncio.run(main())


def test_ask_user_tool_returns_your_answer(tmp_path, monkeypatch):
    async def main():
        questions, ask_user, _ = setup(tmp_path, monkeypatch)
        questions.on_asked.append(lambda q: questions.answer(q.task_id, "the last five years"))
        result = await ask_user.handler({"question": "This year or five?"})
        assert "the last five years" in result["content"][0]["text"]

    asyncio.run(main())
