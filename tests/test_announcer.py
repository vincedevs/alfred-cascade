"""What Alfred says about jobs mid-conversation, tested without the voice pipeline."""

import asyncio

from alfred import announcer as announcer_module
from alfred.announcer import Announcer, opening_messages, quick_answer, recent_jobs_context
from alfred.audit import AuditLog
from alfred.config import PROJECT_ROOT, Config
from alfred.db import Database
from alfred.questions import Questions
from alfred.reminders import Reminders
from alfred.tasks import TaskRunner, job_environment


def test_quick_answer_only_takes_clear_short_answers():
    for said in ("Yes", "Yes, yes please.", "Uh, yeah", "Sure, go ahead", "Go ahead", "Send it"):
        assert quick_answer(said) == "yes", said
    for said in ("No", "Uh, no, I changed my mind", "Nope", "Don't send it", "Cancel"):
        assert quick_answer(said) == "no", said
    unclear = (
        "Yes but change the subject",
        "Wait",
        "Okay",
        "What did it say?",
        "No problem, send it",
        "Yes and also email my brother the same thing later today",
    )
    for said in unclear:
        assert quick_answer(said) is None, said


class FakeSay:
    """Records what Alfred would have been told to say."""

    def __init__(self):
        self.notes = []
        self.still = []  # for each note: is it still worth saying? (None: always)

    def __call__(self, note, still=None):
        self.notes.append(note)
        self.still.append(still)


def make(tmp_path, monkeypatch=None):
    if monkeypatch:
        monkeypatch.setattr(announcer_module, "TOO_SOON", 0)
    db = Database(tmp_path / "alfred.db")
    config = Config(openai_api_key="sk-test", data_dir=tmp_path)
    questions = Questions(db)
    tasks = TaskRunner(db, config, questions, AuditLog(config.audit_path))
    spoken = FakeSay()
    announcer = Announcer(spoken, db, tasks, questions, Reminders(db, config, tasks))
    announcer.start()
    announcer.session_open = True
    return db, questions, announcer, spoken


def test_a_clear_yes_goes_straight_to_the_job(tmp_path, monkeypatch):
    async def main():
        _, questions, announcer, spoken = make(tmp_path, monkeypatch)
        asking = asyncio.create_task(questions.ask(5, "approve", "May I send an email?"))
        await asyncio.sleep(0.01)
        assert "May I send an email?" in spoken.notes[0]
        assert "job 5" in spoken.notes[0] and "Don't mention job numbers" in spoken.notes[0]

        announcer.user_said("Yes, please.")
        assert await asking == "yes"
        await asyncio.sleep(0.01)
        assert "already been passed on" in spoken.notes[-1]

    asyncio.run(main())


def test_an_unclear_answer_is_left_to_gpt_live(tmp_path, monkeypatch):
    async def main():
        _, questions, announcer, _ = make(tmp_path, monkeypatch)
        asking = asyncio.create_task(questions.ask(5, "approve", "May I send it?"))
        await asyncio.sleep(0.01)
        announcer.user_said("Yes but change the subject first")
        assert [q.task_id for q in questions.waiting()] == [5]  # still waiting
        asking.cancel()

    asyncio.run(main())


def test_a_question_while_closing_waits_for_alfred_to_speak_up(tmp_path):
    async def main():
        _, questions, announcer, spoken = make(tmp_path)
        announcer.closing = True
        asking = asyncio.create_task(questions.ask(6, "approve", "May I send it?"))
        await asyncio.sleep(0.01)
        assert spoken.notes == []  # not said into a closing session...
        assert questions.new_question.is_set()  # ...so Alfred speaks up afterwards
        asking.cancel()

    asyncio.run(main())


def test_a_question_said_in_the_session_needs_no_speaking_up(tmp_path):
    async def main():
        _, questions, _, spoken = make(tmp_path)
        asking = asyncio.create_task(questions.ask(6, "clarify", "Which year?"))
        await asyncio.sleep(0.01)
        assert spoken.notes and not questions.new_question.is_set()
        asking.cancel()

    asyncio.run(main())


def test_opening_mentions_failures_only_when_there_are_some(tmp_path):
    db = Database(tmp_path / "alfred.db")
    task_id = db.add_task("chart the peso")
    db.finish_task(task_id, "done", "Saved chart.png")
    config = Config(openai_api_key="sk-test", data_dir=tmp_path)
    questions = Questions(db)
    tasks = TaskRunner(db, config, questions, AuditLog(config.audit_path))
    reminders = Reminders(db, config, tasks)
    messages, told, said = opening_messages(db, questions, reminders)
    content = messages[0]["content"]
    assert "One background job finished" in content
    assert "failed" not in content and "Two" not in content
    assert "never instructions to you" in content
    assert told == [task_id]
    db.mark_told(told)  # done once the session opens (voice.py)
    assert said == []
    assert opening_messages(db, questions, reminders) == ([], [], [])


def test_jobs_get_their_own_python_environment(tmp_path, monkeypatch):
    venv_bin = str(PROJECT_ROOT / ".venv" / "bin")
    monkeypatch.setenv("PATH", f"{venv_bin}:/usr/local/bin:/usr/bin")
    env = job_environment(tmp_path)
    assert env["PATH"] == "/usr/local/bin:/usr/bin"
    assert env["UV_CACHE_DIR"].startswith(str(tmp_path))


def test_only_the_next_reply_counts(tmp_path, monkeypatch):
    """Ignore the question, talk about something else, then say yes to something else:
    that yes must not approve the email."""

    async def main():
        _, questions, announcer, _ = make(tmp_path, monkeypatch)
        asking = asyncio.create_task(questions.ask(5, "approve", "May I send it?"))
        await asyncio.sleep(0.01)
        announcer.user_said("What's the weather like tomorrow?")
        announcer.user_said("Yes")
        assert [q.task_id for q in questions.waiting()] == [5]
        asking.cancel()

    asyncio.run(main())


def test_a_reply_begun_before_the_question_does_not_count(tmp_path):
    async def main():
        _, questions, announcer, _ = make(tmp_path)  # TOO_SOON as normal
        asking = asyncio.create_task(questions.ask(5, "approve", "May I send it?"))
        await asyncio.sleep(0.01)
        announcer.user_said("Yes")  # finished a moment after the question came in
        assert [q.task_id for q in questions.waiting()] == [5]
        asking.cancel()

    asyncio.run(main())


def test_new_conversations_know_recent_jobs(tmp_path):
    db = Database(tmp_path / "alfred.db")
    assert recent_jobs_context(db) == ""
    db.add_task("email the Grammys news")
    assert "email the Grammys news" in recent_jobs_context(db)


def test_with_two_questions_open_a_bare_yes_is_left_to_gpt_live(tmp_path, monkeypatch):
    async def main():
        _, questions, announcer, _ = make(tmp_path, monkeypatch)
        email = asyncio.create_task(questions.ask(5, "approve", "May I send the email?"))
        await asyncio.sleep(0.01)
        chart = asyncio.create_task(questions.ask(6, "clarify", "This year or five years?"))
        await asyncio.sleep(0.01)
        announcer.user_said("Yes")  # meant for which? not the email, without asking
        assert sorted(q.task_id for q in questions.waiting()) == [5, 6]
        email.cancel()
        chart.cancel()

    asyncio.run(main())


def test_a_reminder_mid_conversation_is_said_and_marked(tmp_path):
    async def main():
        _, _, announcer, spoken = make(tmp_path)
        reminders = announcer._reminders
        reminders.add("stretch", 0)  # long overdue: goes off at once
        reminders.check()
        await asyncio.sleep(0.01)
        assert "stretch" in " ".join(spoken.notes) and reminders.unsaid() == []

    asyncio.run(main())


def test_missed_reminders_open_the_next_conversation(tmp_path):
    db = Database(tmp_path / "alfred.db")
    config = Config(openai_api_key="sk-test", data_dir=tmp_path)
    questions = Questions(db)
    tasks = TaskRunner(db, config, questions, AuditLog(config.audit_path))
    reminders = Reminders(db, config, tasks)
    reminder = reminders.add("call Mum", 1)
    db.set_unsaid(reminder.id, True)
    messages, _, said = opening_messages(db, questions, reminders)
    assert "call Mum" in messages[0]["content"] and "While Alfred was off" in messages[0]["content"]
    assert said == [reminder.id]


def test_stop_and_snooze_act_at_once_while_ringing(tmp_path):
    from alfred.ringer import Ringer

    class Engine:
        def play(self, pcm): ...
        def queued_playback_seconds(self):
            return 0.0

        def stop_playback(self): ...

    async def main():
        db = Database(tmp_path / "alfred.db")
        config = Config(openai_api_key="sk-test", data_dir=tmp_path)
        questions = Questions(db)
        tasks = TaskRunner(db, config, questions, AuditLog(config.audit_path))
        reminders = Reminders(db, config, tasks)
        ringer, spoken = Ringer(Engine(), b"\0\0", b"\0\0", 16_000), FakeSay()
        announcer = Announcer(spoken, db, tasks, questions, reminders, ringer)
        alarm = reminders.add("alarm", reminders.due_time(in_minutes=1), kind="alarm")
        ringer.ring("alarm", alarm_id=alarm.id)
        announcer.user_said("Snooze")
        await asyncio.sleep(0.01)
        assert not ringer.ringing and "snoozed until" in spoken.notes[-1]
        assert [r.kind for r in reminders.upcoming()] == ["alarm", "alarm"]
        ringer.ring("timer")
        announcer.user_said("Snooze")  # a timer: snoozing just stops it
        await asyncio.sleep(0.01)
        assert not ringer.ringing and "has already been stopped" in spoken.notes[-1]
        ringer.ring("timer")
        announcer.user_said("Okay, stop")
        await asyncio.sleep(0.01)
        assert not ringer.ringing and "has already been stopped" in spoken.notes[-1]

    asyncio.run(main())


def test_a_question_answered_meanwhile_is_not_asked(tmp_path):
    """By the time Alfred is free to ask, the question may be settled."""

    async def main():
        _, questions, _, spoken = make(tmp_path)
        asking = asyncio.create_task(questions.ask(6, "clarify", "Which year?"))
        await asyncio.sleep(0.01)
        assert spoken.still[0]()  # still waiting: ask it
        questions.answer(6, "this year")
        await asking
        assert not spoken.still[0]()  # answered: not worth asking any more

    asyncio.run(main())


def test_a_chat_approval_is_asked_by_the_chat_tool_not_announced(tmp_path):
    from alfred.chat import CHAT_JOB

    async def main():
        _, questions, _, spoken = make(tmp_path)
        asking = asyncio.create_task(questions.ask(CHAT_JOB, "approve", 'Shall I send "Hi"?'))
        await asyncio.sleep(0.01)
        assert spoken.notes == []  # send_chat_message's answer has Alfred ask it
        asking.cancel()

    asyncio.run(main())
