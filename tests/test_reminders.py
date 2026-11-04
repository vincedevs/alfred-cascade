"""Reminders, with a fake clock: no waiting, no Claude."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from alfred.audit import AuditLog
from alfred.config import Config
from alfred.db import Database
from alfred.questions import Questions
from alfred.reminders import MISSED_WINDOW, Reminders, next_time
from alfred.tasks import TaskRunner


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


async def quick_agent(task_id, request, session_id):
    return f"briefing: {request}"


def make(tmp_path, quiet_hours=None):
    db = Database(tmp_path / "alfred.db")
    config = Config(openai_api_key="sk-test", data_dir=tmp_path, quiet_hours=quiet_hours)
    tasks = TaskRunner(
        db, config, Questions(db), AuditLog(config.audit_path), run_agent=quick_agent
    )
    clock = Clock()
    return db, Reminders(db, config, tasks, now=clock), clock


def test_a_reminder_goes_off_once_and_waits_to_be_said(tmp_path):
    _, reminders, clock = make(tmp_path)
    heard = []
    reminders.on_due.append(heard.append)
    reminders.add("stretch", clock.now + 120)
    reminders.check()
    assert heard == [] and not reminders.wake.is_set()
    clock.now += 120
    reminders.check()
    assert [r.text for r in heard] == ["stretch"] and reminders.wake.is_set()
    assert [r.text for r in reminders.unsaid()] == ["stretch"]
    reminders.mark_said([heard[0].id])
    assert reminders.unsaid() == [] and reminders.upcoming() == []
    reminders.check()
    assert len(heard) == 1  # never twice


def test_repeating_reminders_move_on(tmp_path):
    _, reminders, clock = make(tmp_path)
    reminder = reminders.add("take vitamins", clock.now, repeat="daily")
    reminders.check()
    upcoming = reminders.upcoming()
    assert upcoming[0].id == reminder.id and upcoming[0].due_at > clock.now
    assert [r.text for r in reminders.unsaid()] == ["take vitamins"]


def test_weekdays_skip_the_weekend():
    friday = datetime(2026, 10, 30, 8, 0)
    assert next_time(friday, "weekdays") == datetime(2026, 11, 2, 8, 0)  # Monday
    assert next_time(friday, "daily") == datetime(2026, 10, 31, 8, 0)
    assert next_time(friday, "weekly") == datetime(2026, 11, 6, 8, 0)


def test_missed_while_off_recent_ones_are_said_old_ones_kept(tmp_path):
    db, reminders, clock = make(tmp_path)
    recent = reminders.add("call Mum", clock.now - 3600)
    old = reminders.add("pay rent", clock.now - MISSED_WINDOW - 60)
    daily = reminders.add("stretch", clock.now - 3 * 86400, repeat="daily")
    reminders.catch_up()
    assert [r.id for r in reminders.unsaid()] == [recent.id]
    assert db.get_reminder(old.id).status == "missed"
    assert db.get_reminder(daily.id).due_at > clock.now  # not replayed, just moved on
    assert reminders.wake.is_set()


def test_cancel(tmp_path):
    _, reminders, clock = make(tmp_path)
    reminder = reminders.add("stretch", clock.now + 60)
    assert reminders.cancel(reminder.id) and not reminders.cancel(reminder.id)
    clock.now += 120
    reminders.check()
    assert reminders.unsaid() == []


def test_a_briefing_runs_a_job_and_speaks_up_with_the_result(tmp_path):
    async def main():
        db, reminders, clock = make(tmp_path)
        reminders.add("brief me on the weather", clock.now, run_job=True)
        reminders.check()
        assert reminders.unsaid() == []  # nothing to say yet: the job is running
        await asyncio.sleep(0.05)
        assert reminders.wake.is_set()
        assert db.untold_results()[0].result == "briefing: brief me on the weather"

    asyncio.run(main())


def test_at_night_a_briefing_waits_but_your_reminders_still_go_off(tmp_path, monkeypatch):
    async def main():
        _, reminders, clock = make(tmp_path)
        monkeypatch.setattr(Config, "is_quiet", lambda self, now=None: True)
        reminders.add("brief me", clock.now, run_job=True)
        reminders.check()
        await asyncio.sleep(0.05)
        assert not reminders.wake.is_set()  # the result waits for the next conversation
        reminders.add("take the pill", clock.now)
        reminders.check()
        assert reminders.wake.is_set()  # you chose the time

    asyncio.run(main())


def test_times_are_read_in_alfreds_time_zone(tmp_path):
    _, reminders, clock = make(tmp_path)
    clock.now = datetime(2026, 10, 31, 12, 0, tzinfo=ZoneInfo("Asia/Manila")).timestamp()
    due = reminders.due_time(at="2026-10-31 17:00")
    assert due - clock.now == 5 * 3600  # 5 pm Manila, wherever the computer thinks it is
    assert reminders.spoken(due) == "5:00 PM today"
    assert reminders.due_time(in_minutes=2) == clock.now + 120


def test_bad_times_are_refused(tmp_path):
    _, reminders, clock = make(tmp_path)
    clock.now = datetime(2026, 10, 31, 12, 0, tzinfo=ZoneInfo("Asia/Manila")).timestamp()
    for kwargs in ({}, {"at": "5pm"}, {"at": "2026-10-30 09:00"}):
        try:
            reminders.due_time(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted {kwargs}")
