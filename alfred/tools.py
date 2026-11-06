"""What Alfred's model (GPT-6 Luna) can do besides web search: background jobs (start,
check, answer, stop), memory (remember, recall, forget), and reminders, timers and alarms.

Each tool is a plain async function. Pipecat reads its name, docstring and arguments
to describe it to the model, and runs it here when the model calls it.
"""

from pathlib import Path

from pipecat.services.llm_service import FunctionCallParams

from alfred.db import Database, Reminder, Task
from alfred.memory import Memory, words_in
from alfred.questions import Questions
from alfred.reminders import SNOOZE_MINUTES, Reminders
from alfred.ringer import Ringer
from alfred.saved import mark_gone
from alfred.tasks import TaskRunner

QUICK_ANSWER_SECONDS = 12  # a job done within this is answered straight away


def job_tools(runner: TaskRunner, db: Database, questions: Questions) -> list:
    async def do_task(params: FunctionCallParams, request: str):
        """Hand a job to Alfred's background worker, which can research the web, read pages,
        write notes, write and run code, and send email (after the user approves). Use it for
        anything that needs several steps or more than a quick search. Quick jobs return the
        answer; longer ones keep running, and Alfred announces the result when they finish.

        Args:
            request: The job in full, with every detail and preference the user gave.
        """
        task_id = runner.start(request)
        task = await runner.wait(task_id, QUICK_ANSWER_SECONDS)
        if task.status == "running":
            await params.result_callback(
                {
                    "task_id": task_id,
                    "status": "running",
                    "note": "Still working. Tell the user in a few words that you're on "
                    "it; the result is announced when it's ready.",
                }
            )
        else:
            db.mark_told([task_id])
            await params.result_callback(_describe(task, runner.workspace))

    async def task_status(params: FunctionCallParams, task_id: int = 0, about: str = ""):
        """Check on background jobs: whether they are still running, and their results.

        Args:
            task_id: The job to check, or 0 for the most recent jobs.
            about: Or a few words to find a job by ("snake game"), when task_id is 0.
        """
        if task_id:
            tasks = [db.get_task(task_id)]
        else:
            tasks = db.find_tasks(words_in(about)) if about else db.recent_tasks(3)
        tasks = [task for task in tasks if task]
        db.mark_told([task.id for task in tasks if task.status != "running"])
        waiting = [{"task_id": q.task_id, "question": q.text} for q in questions.waiting()]
        await params.result_callback(
            {
                "jobs": [_describe(task, runner.workspace) for task in tasks],
                "questions_for_user": waiting,
            }
        )

    async def answer_job(params: FunctionCallParams, task_id: int, answer: str):
        """Pass the user's answer to a question a background job asked them.

        Args:
            task_id: The job that asked.
            answer: For a yes-or-no question, exactly "yes" (only if the user clearly said
                yes) or "no". Otherwise the user's answer in their own words.
        """
        question = questions.answer(task_id, answer)
        if question is None:
            if questions.answered_recently(task_id):  # Alfred passed it on (announcer.py)
                note = "Already passed on. Say nothing more about it unless asked."
            else:
                note = (
                    "This job has no question waiting, so nothing was passed on. Check "
                    "task_status and tell the user exactly what it says."
                )
            await params.result_callback({"task_id": task_id, "note": note})
        else:
            note = "Passed on. Acknowledge in a word or two; the outcome is announced later."
            await params.result_callback({"task_id": task_id, "passed_on": answer, "note": note})

    async def stop_task(params: FunctionCallParams, task_id: int):
        """Stop a background job that is still running.

        Args:
            task_id: The job to stop.
        """
        stopped = await runner.stop(task_id)
        db.mark_told([task_id])  # the user knows: no need to report it later
        await params.result_callback({"task_id": task_id, "stopped": stopped})

    return [do_task, task_status, answer_job, stop_task]


def memory_tools(memory: Memory) -> list:
    async def remember(params: FunctionCallParams, fact: str):
        """Remember a lasting fact about the user, when they ask you to or state a lasting
        preference ("I'm vegetarian").

        Args:
            fact: The fact as a short sentence about the user, e.g. "Is vegetarian."
        """
        new = memory.remember(fact)
        await params.result_callback({"remembered": fact, "already_known": not new})

    async def recall(params: FunctionCallParams, about: str = ""):
        """Look up what Alfred knows about the user, past conversations, and what past jobs
        found or made, and where they saved it ("what did you find about ramen?", "where's
        the snake game?"). A result's "Saved:" line has the link and folder.

        Args:
            about: What to look for, in a few words. Empty: just what Alfred knows about them.
        """
        found = memory.search(about) if about else []
        facts = memory.facts_about(about)
        await params.result_callback({"about_the_user": facts, "history": found})

    async def forget(params: FunctionCallParams, about: str):
        """Forget facts about the user, and past conversations, about something.

        Args:
            about: What to forget, in a few words, e.g. "my home address".
        """
        removed = memory.forget(about)
        await params.result_callback({"forgotten_facts": removed})

    return [remember, recall, forget]


def reminder_tools(reminders: Reminders, ringer: Ringer | None = None) -> list:
    async def set_reminder(
        params: FunctionCallParams,
        text: str,
        in_minutes: float = 0,
        at: str = "",
        repeat: str = "none",
        run_as_job: bool = False,
    ):
        """Set a reminder. Alfred speaks up when it is due, even without "Hey Alfred".

        Args:
            text: What to remind the user of ("stretch"), or with run_as_job, the job to run
                ("brief me on today's weather in Manila").
            in_minutes: Minutes from now. Use this or `at`.
            at: Local date and time as "YYYY-MM-DD HH:MM" (24-hour). Use this or in_minutes.
            repeat: "none", "daily", "weekdays" or "weekly".
            run_as_job: True to run `text` as a background job and say its result.
        """
        try:
            due = reminders.due_time(in_minutes, at)
            reminder = reminders.add(text, due, repeat, run_as_job)
        except ValueError as error:
            await params.result_callback({"error": str(error)})
            return
        await params.result_callback(_describe_reminder(reminder, reminders))

    async def list_reminders(params: FunctionCallParams):
        """List the reminders, timers and alarms still to come, with the time left on
        timers ("how long is left on the pasta timer?")."""
        upcoming = [_describe_reminder(r, reminders) for r in reminders.upcoming()]
        await params.result_callback({"reminders": upcoming})

    async def cancel_reminder(params: FunctionCallParams, reminder_id: int):
        """Cancel a reminder, timer or alarm (find its id with list_reminders).

        Args:
            reminder_id: The one to cancel.
        """
        await params.result_callback({"cancelled": reminders.cancel(reminder_id)})

    return [set_reminder, list_reminders, cancel_reminder] + timer_and_alarm_tools(
        reminders, ringer
    )


def timer_and_alarm_tools(reminders: Reminders, ringer: Ringer | None) -> list:
    async def set_timer(params: FunctionCallParams, minutes: float, name: str = "timer"):
        """Start a timer. When it's done it beeps and Alfred says so.

        Args:
            minutes: How long, in minutes (e.g. 0.5 for 30 seconds).
            name: What it's for, if the user said ("pasta"), else "timer".
        """
        await _add(params, reminders, name, in_minutes=minutes, kind="timer")

    async def set_alarm(
        params: FunctionCallParams, at: str = "", in_minutes: float = 0, repeat: str = "none"
    ):
        """Set an alarm. It rings until the user says stop or snooze, and it rings even
        during quiet hours. Give `at` for a clock time, or `in_minutes` for "in 30
        seconds" / "in 2 minutes".

        Args:
            at: Local date and time as "YYYY-MM-DD HH:MM" (24-hour).
            in_minutes: How soon, in minutes (0.5 for 30 seconds).
            repeat: "none", "daily", "weekdays" or "weekly" (with `at`).
        """
        when = {"at": at} if at else {"in_minutes": in_minutes}
        await _add(params, reminders, "alarm", **when, repeat=repeat, kind="alarm")

    async def add_to_timer(params: FunctionCallParams, reminder_id: int, minutes: float):
        """Add minutes to a running timer (find its id with list_reminders).

        Args:
            reminder_id: The timer.
            minutes: How many minutes to add.
        """
        timer = reminders.extend(reminder_id, minutes)
        found = _describe_reminder(timer, reminders) if timer else {"error": "no such timer"}
        await params.result_callback(found)

    async def stop_ringing(params: FunctionCallParams):
        """Stop a timer or alarm that's ringing ("stop", "I'm up", "turn it off")."""
        if ringer and ringer.stop():
            ringer.answered("stopped")
            await params.result_callback({"stopped": True})
        elif already := ringer and ringer.just_answered():
            await params.result_callback(_already(already))
        else:
            await params.result_callback({"stopped": False, "note": "Nothing is ringing."})

    async def snooze_alarm(params: FunctionCallParams, minutes: float = SNOOZE_MINUTES):
        """Snooze the alarm that's ringing: it rings again in a few minutes.

        Args:
            minutes: How long to snooze (9 unless the user said otherwise).
        """
        alarm_id = ringer.alarm_id if ringer and ringer.ringing else None
        if alarm_id is None and ringer and ringer.stop():  # a timer: timers don't snooze
            ringer.answered("stopped")
            await params.result_callback({"stopped": True, "note": "It was a timer: stopped."})
            return
        if alarm_id is None:
            already = ringer and ringer.just_answered()
            await params.result_callback(
                _already(already) if already else {"error": "no alarm is ringing"}
            )
            return
        ringer.stop()
        again = reminders.snooze(alarm_id, minutes)
        ringer.answered(f"snoozed until {reminders.spoken(again.due_at)}")
        await params.result_callback(_describe_reminder(again, reminders))

    return [set_timer, set_alarm, add_to_timer, stop_ringing, snooze_alarm]


def _already(done: str) -> dict:
    """The ringing was already stopped or snoozed a moment ago (announcer.py)."""
    return {"already": done, "note": "Already done. Don't say it again or contradict it."}


async def _add(params, reminders: Reminders, text: str, **when_and_kind) -> None:
    kind = when_and_kind.pop("kind")
    repeat = when_and_kind.pop("repeat", "none")
    try:
        due = reminders.due_time(**when_and_kind)
        added = reminders.add(text, due, repeat, kind=kind)
    except ValueError as error:
        await params.result_callback({"error": str(error)})
        return
    await params.result_callback(_describe_reminder(added, reminders))


def _describe_reminder(reminder: Reminder, reminders: Reminders) -> dict:
    described = {
        "reminder_id": reminder.id,
        "kind": reminder.kind,
        "text": reminder.text,
        "when": reminders.spoken(reminder.due_at),
        "repeat": reminder.repeat,
        "runs_a_job": reminder.run_job,
    }
    if reminder.kind == "timer":
        described["left"] = reminders.left(reminder)
    return described


def _describe(task: Task, workspace: Path) -> dict:
    """A job for the model, with anything it saved that has since gone marked (saved.py)."""
    return {
        "task_id": task.id,
        "request": task.request,
        "status": task.status,
        "result": mark_gone(task.result, workspace) if task.result else task.result,
    }
