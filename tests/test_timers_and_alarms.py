"""Timers and alarms: kinds, snooze, extend, the ringer, "stop"/"snooze", and the
system backup (with fake system commands: nothing is really scheduled)."""

import asyncio
import plistlib
import sqlite3

from alfred import alarm_backup
from alfred.alarm_backup import BACKUP_DELAY, AlarmBackup
from alfred.announcer import due_note, quick_ringing_answer
from alfred.audit import AuditLog
from alfred.config import Config
from alfred.db import Database
from alfred.questions import Questions
from alfred.reminders import Reminders
from alfred.ringer import Ringer
from alfred.tasks import TaskRunner


class Clock:
    now = 1_800_000_000.0

    def __call__(self):
        return self.now


class FakeBackup:
    def __init__(self):
        self.scheduled, self.cancelled, self.kept = {}, [], None

    def schedule(self, alarm_id, due_at):
        self.scheduled[alarm_id] = due_at

    def cancel(self, alarm_id):
        self.cancelled.append(alarm_id)

    def clear_old(self, keep):
        self.kept = keep


def make(tmp_path, backup=None):
    db = Database(tmp_path / "alfred.db")
    config = Config(openai_api_key="sk-test", data_dir=tmp_path)
    tasks = TaskRunner(db, config, Questions(db), AuditLog(config.audit_path))
    clock = Clock()
    return Reminders(db, config, tasks, now=clock, backup=backup), clock


def test_a_timer_counts_down_and_can_be_extended(tmp_path):
    reminders, clock = make(tmp_path)
    timer = reminders.add("pasta", clock.now + 8 * 60, kind="timer")
    assert reminders.left(timer) == "about 8 minutes left"
    timer = reminders.extend(timer.id, 5)
    assert reminders.left(timer) == "about 13 minutes left"
    clock.now += 13 * 60 - 20
    assert reminders.left(timer) == "20 seconds left"
    heard = []
    reminders.on_due.append(heard.append)
    clock.now += 20
    reminders.check()
    assert heard[0].kind == "timer" and "pasta timer is done" in due_note(heard[0], reminders)
    assert reminders.extend(timer.id, 5) is None  # finished timers can't be extended


def test_alarms_have_a_system_backup(tmp_path):
    backup = FakeBackup()
    reminders, clock = make(tmp_path, backup)
    alarm = reminders.add("alarm", clock.now + 3600, kind="alarm", repeat="daily")
    assert backup.scheduled == {alarm.id: clock.now + 3600}
    reminders.add("tea", clock.now + 60)  # plain reminders have no backup
    assert len(backup.scheduled) == 1

    clock.now += 3600
    reminders.check()  # Alfred rings it himself: the backup is cancelled first...
    assert backup.cancelled == [alarm.id]
    assert backup.scheduled[alarm.id] == clock.now + 24 * 3600  # ...and set for tomorrow

    snoozed = reminders.snooze(alarm.id)
    assert snoozed.kind == "alarm" and snoozed.due_at == clock.now + 9 * 60
    assert reminders.cancel(snoozed.id) and backup.cancelled[-1] == snoozed.id

    reminders.catch_up()
    assert backup.kept == {alarm.id}


def test_old_databases_get_the_kind_column(tmp_path):
    path = tmp_path / "alfred.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE reminders (id INTEGER PRIMARY KEY, text TEXT NOT NULL, due_at REAL "
        "NOT NULL, repeat TEXT NOT NULL, run_job INTEGER NOT NULL, status TEXT NOT NULL, "
        "unsaid INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)"
    )
    old.execute("INSERT INTO reminders VALUES (1, 'tea', 1, 'none', 0, 'waiting', 0, 1)")
    old.commit()
    old.close()
    db = Database(path)
    assert db.get_reminder(1).kind == "reminder"


def test_stop_and_snooze_are_heard_while_ringing():
    for said in ("Stop", "Okay, stop it", "Turn it off", "Thanks, I'm up"):
        assert quick_ringing_answer(said) == "stop", said
    assert quick_ringing_answer("Snooze") == "snooze"
    assert quick_ringing_answer("Snooze please") == "snooze"
    assert quick_ringing_answer("Alright, could you stop the timer") == "stop"
    not_about_the_ringing = (
        "What time is it?",
        "Tell me about the weather in Manila today please",
        "Turn off the lights",
        "Cancel tomorrow's reminder",
    )
    for said in not_about_the_ringing:
        assert quick_ringing_answer(said) is None, said


class FakeEngine:
    def __init__(self):
        self.played, self.queued = 0, 0.0

    def play(self, pcm):
        self.played += 1

    def queued_playback_seconds(self):
        return self.queued

    def stop_playback(self):
        pass


def test_the_ringer_repeats_until_stopped_and_waits_for_alfred(monkeypatch):
    from alfred import ringer as ringer_module

    monkeypatch.setattr(ringer_module, "GAP_SECONDS", 0.01)

    async def main():
        engine = FakeEngine()
        ringer = Ringer(engine, b"\0\0" * 16, b"\0\0" * 16, 16_000)
        ringer.ring("alarm", alarm_id=4)
        await asyncio.sleep(0.05)
        assert ringer.ringing and ringer.alarm_id == 4 and engine.played >= 2
        engine.queued = 1.0  # Alfred is speaking: the ringing waits
        played = engine.played
        await asyncio.sleep(0.05)
        assert engine.played == played
        assert ringer.stop() and not ringer.ringing
        assert not ringer.stop()  # nothing left to stop

    asyncio.run(main())


def test_backup_commands(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(alarm_backup, "run", lambda command: commands.append(command) or True)
    monkeypatch.setattr(alarm_backup, "LAUNCH_AGENTS", tmp_path)
    sound = tmp_path / "alarm.wav"

    mac = AlarmBackup(sound, platform="darwin")
    mac.schedule(3, 1_800_000_000.0)
    job = plistlib.loads((tmp_path / "com.alfred.alarm.3.plist").read_bytes())
    assert "afplay" in job["ProgramArguments"][2]
    assert commands[-1][:2] == ["launchctl", "bootstrap"]
    mac.clear_old(keep=set())  # no longer waiting: removed
    assert commands[-1][:2] == ["launchctl", "bootout"]
    assert not (tmp_path / "com.alfred.alarm.3.plist").exists()

    linux = AlarmBackup(sound, platform="linux")
    linux.available = True
    linux.schedule(5, 1_800_000_000.0)
    assert commands[-1][:3] == ["systemd-run", "--user", "--unit=alfred-alarm-5"]
    assert "aplay" in commands[-1][-1]
    linux.cancel(5)
    assert commands[-1] == ["systemctl", "--user", "stop", "alfred-alarm-5.timer"]
    listed = "Sun 2026-11-01 15:25:00 PST 4min left - - alfred-alarm-7.timer alfred-alarm-7.service"
    monkeypatch.setattr(alarm_backup, "output", lambda command: listed)
    linux.clear_old(keep=set())  # alarm 7 is no longer waiting: its backup goes
    assert commands[-1] == ["systemctl", "--user", "stop", "alfred-alarm-7.timer"]
    assert BACKUP_DELAY > 0


class Params:
    async def result_callback(self, result):
        self.result = result


def test_the_router_tools(tmp_path):
    from alfred.tools import timer_and_alarm_tools

    async def main():
        reminders, clock = make(tmp_path)
        engine = FakeEngine()
        ringer = Ringer(engine, b"\0\0", b"\0\0", 16_000)
        set_timer, set_alarm, add_to_timer, stop_ringing, snooze_alarm = timer_and_alarm_tools(
            reminders, ringer
        )
        params = Params()
        await set_timer(params, minutes=10, name="tea")
        assert params.result["kind"] == "timer" and params.result["left"] == "about 10 minutes left"
        await add_to_timer(params, params.result["reminder_id"], 5)
        assert params.result["left"] == "about 15 minutes left"
        await set_alarm(params, at="2027-02-01 06:30", repeat="weekdays")
        assert params.result["kind"] == "alarm"
        await snooze_alarm(params)
        assert params.result == {"error": "no alarm is ringing"}
        ringer.ring("alarm", alarm_id=params_id(reminders))
        await snooze_alarm(params)
        assert params.result["kind"] == "alarm" and not ringer.ringing
        await stop_ringing(params)
        assert params.result["already"].startswith("snoozed until")  # just answered

    asyncio.run(main())


def params_id(reminders):
    return next(r.id for r in reminders.upcoming() if r.kind == "alarm")


def test_the_router_does_not_contradict_a_quick_stop_or_snooze(tmp_path):
    """Alfred snoozes at once; the router's snooze_alarm arrives a moment later."""
    from alfred.tools import timer_and_alarm_tools

    async def main():
        reminders, _ = make(tmp_path)
        ringer = Ringer(FakeEngine(), b"\0\0", b"\0\0", 16_000)
        *_, stop_ringing, snooze_alarm = timer_and_alarm_tools(reminders, ringer)
        ringer.ring("alarm", alarm_id=1)
        ringer.stop()
        ringer.answered("snoozed until 2:17 PM")  # what announcer.py does
        params = Params()
        await snooze_alarm(params)
        assert params.result["already"] == "snoozed until 2:17 PM"
        await stop_ringing(params)
        assert params.result["already"] == "snoozed until 2:17 PM"
        assert len(reminders.upcoming()) == 0  # not snoozed a second time

    asyncio.run(main())


def test_snooze_on_a_timer_stops_it(tmp_path):
    from alfred.tools import timer_and_alarm_tools

    async def main():
        reminders, _ = make(tmp_path)
        ringer = Ringer(FakeEngine(), b"\0\0", b"\0\0", 16_000)
        *_, snooze_alarm = timer_and_alarm_tools(reminders, ringer)
        ringer.ring("timer")
        params = Params()
        await snooze_alarm(params)
        assert params.result["stopped"] and not ringer.ringing

    asyncio.run(main())


def test_an_alarm_in_30_seconds(tmp_path):
    from alfred.tools import timer_and_alarm_tools

    async def main():
        reminders, clock = make(tmp_path)
        _, set_alarm, *_ = timer_and_alarm_tools(reminders, None)
        params = Params()
        await set_alarm(params, in_minutes=0.5)
        alarm = reminders.upcoming()[0]
        assert alarm.kind == "alarm" and alarm.due_at == clock.now + 30

    asyncio.run(main())
