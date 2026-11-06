"""Background jobs, tested with a fake agent: no Claude, no cost."""

import asyncio

from alfred.audit import AuditLog
from alfred.config import Config
from alfred.db import Database
from alfred.questions import Questions
from alfred.tasks import TaskRunner
from alfred.tools import job_tools


def make(tmp_path, agent, finished=None):
    db = Database(tmp_path / "alfred.db")
    config = Config(openai_api_key="sk-test", data_dir=tmp_path)
    on_finished = finished.append if finished is not None else None
    questions, audit = Questions(db), AuditLog(config.audit_path)
    runner = TaskRunner(db, config, questions, audit, on_finished=on_finished, run_agent=agent)
    runner.questions = questions  # for the tests below
    return db, runner


def tools_for(runner, db):
    return job_tools(runner, db, runner.questions)


async def quick_agent(task_id, request, session_id):
    return f"answer to: {request}"


async def slow_agent(task_id, request, session_id):
    await asyncio.sleep(60)
    return "too late"


async def broken_agent(task_id, request, session_id):
    raise RuntimeError("no network")


def test_job_finishes_and_calls_back(tmp_path):
    async def main():
        finished = []
        db, runner = make(tmp_path, quick_agent, finished)
        task_id = runner.start("find a keyboard")
        await asyncio.sleep(0.05)
        task = db.get_task(task_id)
        assert (task.status, task.result) == ("done", "answer to: find a keyboard")
        assert [t.id for t in finished] == [task_id]  # the chime would play
        assert [t.id for t in db.untold_results()] == [task_id]  # and Alfred tells you next time

    asyncio.run(main())


def test_waiting_for_a_quick_job_skips_the_chime(tmp_path):
    async def main():
        finished = []
        db, runner = make(tmp_path, quick_agent, finished)
        task = await runner.wait(runner.start("quick one"), timeout=5)
        assert task.status == "done"
        assert finished == []  # answered directly, so no chime

    asyncio.run(main())


def test_stop_marks_job_stopped(tmp_path):
    async def main():
        db, runner = make(tmp_path, slow_agent)
        task_id = runner.start("long research")
        await asyncio.sleep(0.05)
        assert await runner.stop(task_id)
        assert db.get_task(task_id).status == "stopped"
        assert not await runner.stop(task_id)  # nothing left to stop

    asyncio.run(main())


def test_failure_is_recorded(tmp_path):
    async def main():
        db, runner = make(tmp_path, broken_agent)
        task = await runner.wait(runner.start("anything"), timeout=5)
        assert task.status == "failed"
        assert "no network" in task.result

    asyncio.run(main())


def test_shutdown_leaves_jobs_to_resume(tmp_path):
    async def main():
        db, runner = make(tmp_path, slow_agent)
        task_id = runner.start("long research")
        await asyncio.sleep(0.05)
        db.set_session(task_id, "session-123")
        await runner.close()
        assert db.get_task(task_id).status == "running"  # not "stopped": you didn't ask

        resumed_with = []

        async def resuming_agent(task_id, request, session_id):
            resumed_with.append(session_id)
            return "finished after restart"

        _, new_runner = make(tmp_path, resuming_agent)
        assert new_runner.resume_unfinished() == 1
        await asyncio.sleep(0.05)
        assert resumed_with == ["session-123"]
        assert db.get_task(task_id).result == "finished after restart"

    asyncio.run(main())


class FakeParams:
    """Stands in for Pipecat's FunctionCallParams: just records the result."""

    def __init__(self):
        self.result = None

    async def result_callback(self, result):
        self.result = result


def test_tools_start_check_and_stop(tmp_path):
    async def main():
        db, runner = make(tmp_path, quick_agent)
        do_task, task_status, _, stop_task = tools_for(runner, db)

        params = FakeParams()
        await do_task(params, request="weather tomorrow")
        assert params.result["status"] == "done"
        assert db.untold_results() == []  # answered now, so not repeated later

        await task_status(params, task_id=0)
        assert params.result["jobs"][0]["request"] == "weather tomorrow"

        await stop_task(params, task_id=99)
        assert params.result == {"task_id": 99, "stopped": False}

    asyncio.run(main())


def test_error_during_shutdown_is_not_a_failure(tmp_path):
    """Ctrl+C also hits Claude's process, which then ends with an error. The job must resume."""

    async def main():
        interrupted = asyncio.Event()

        async def interrupted_agent(task_id, request, session_id):
            await interrupted.wait()
            raise RuntimeError("error_during_execution")  # what Claude reports after Ctrl+C

        db, runner = make(tmp_path, interrupted_agent)
        task_id = runner.start("long research")
        runner.begin_shutdown()
        interrupted.set()
        await asyncio.sleep(0.05)
        assert db.get_task(task_id).status == "running"

    asyncio.run(main())


def test_stopped_job_is_not_reported_again(tmp_path):
    async def main():
        db, runner = make(tmp_path, slow_agent)
        _, _, _, stop_task = tools_for(runner, db)
        task_id = runner.start("long research")
        await asyncio.sleep(0.05)
        await stop_task(FakeParams(), task_id=task_id)
        assert db.untold_results() == []

    asyncio.run(main())


def test_every_listener_hears_about_a_finished_job(tmp_path):
    async def main():
        chimes, announcements = [], []
        db, runner = make(tmp_path, quick_agent, chimes)
        runner.on_finished.append(announcements.append)  # what voice.py adds mid-conversation
        task_id = runner.start("anything")
        await asyncio.sleep(0.05)
        assert [t.id for t in chimes] == [task_id]
        assert [t.id for t in announcements] == [task_id]

    asyncio.run(main())


def test_answer_job_tool_answers_the_question(tmp_path):
    async def main():
        async def asking_agent(task_id, request, session_id):
            return await runner.questions.ask(task_id, "clarify", "Which year?")

        db, runner = make(tmp_path, asking_agent)
        _, task_status, answer_job, _ = tools_for(runner, db)
        task_id = runner.start("chart my spending")
        await asyncio.sleep(0.05)

        params = FakeParams()
        await task_status(params, task_id=task_id)
        waiting = params.result["questions_for_user"]
        assert waiting == [{"task_id": task_id, "question": "Which year?"}]
        await answer_job(params, task_id=task_id, answer="2026")
        await asyncio.sleep(0.05)
        assert db.get_task(task_id).result == "2026"
        await answer_job(params, task_id=task_id, answer="again")
        assert "already passed on" in params.result["note"].lower()

    asyncio.run(main())


def test_audit_log_records_what_happened(tmp_path):
    from types import SimpleNamespace

    db, runner = make(tmp_path, quick_agent)
    call = SimpleNamespace(id="t1", name="Bash", input={"command": "ls ~/Documents"})
    blocked = SimpleNamespace(is_error=True, content="Permission denied by sandbox")
    runner._record_result(1, call, blocked)
    runner._record_result(1, call, SimpleNamespace(is_error=False, content="ok"))
    log = (tmp_path / "audit.jsonl").read_text()
    assert '"outcome": "failed"' in log and "Permission denied by sandbox" in log
    assert '"outcome": "ran"' in log


def test_refused_calls_get_no_second_audit_line(tmp_path):
    from types import SimpleNamespace

    _, runner = make(tmp_path, quick_agent)
    runner._guard.refused.add("t2")
    call = SimpleNamespace(id="t2", name="Read", input={"file_path": "/etc/passwd"})
    runner._record_result(1, call, SimpleNamespace(is_error=True, content="refused"))
    assert not (tmp_path / "audit.jsonl").exists()


def test_answer_job_without_a_question_is_honest(tmp_path):
    async def main():
        db, runner = make(tmp_path, quick_agent)
        _, _, answer_job, _ = tools_for(runner, db)
        params = FakeParams()
        await answer_job(params, task_id=42, answer="yes")
        assert "nothing was passed on" in params.result["note"]

    asyncio.run(main())


def test_at_most_two_jobs_run_at_once(tmp_path):
    async def main():
        running, most = 0, 0
        release = asyncio.Event()

        async def counting_agent(task_id, request, session_id):
            nonlocal running, most
            running += 1
            most = max(most, running)
            await release.wait()
            running -= 1
            return "done"

        db, runner = make(tmp_path, counting_agent)
        ids = [runner.start(f"job {n}") for n in range(3)]
        await asyncio.sleep(0.05)
        assert most == 2 and runner.busy
        release.set()
        await asyncio.sleep(0.05)
        assert [db.get_task(i).status for i in ids] == ["done"] * 3

    asyncio.run(main())


def test_no_sandbox_means_no_shell(tmp_path, monkeypatch):
    import alfred.tasks as tasks_module

    monkeypatch.setattr(tasks_module.shutil, "which", lambda _tool: None)
    _, runner = make(tmp_path, quick_agent)
    assert "Bash" not in runner._tools and "WebSearch" in runner._tools


def test_task_status_finds_an_older_job_by_what_it_was(tmp_path):
    async def main():
        db, runner = make(tmp_path, quick_agent)
        _, task_status, _, _ = tools_for(runner, db)
        snake = db.add_task("make me a snake game")
        db.finish_task(snake, "done", "Saved: Snake game, link http://raspy.local:8080/snake/")
        for n in range(4):  # newer jobs push it out of the latest three
            db.add_task(f"weather in city {n}")
        params = FakeParams()
        await task_status(params, about="the snake game")
        (job,) = params.result["jobs"]
        assert job["task_id"] == snake
        assert job["result"].endswith("(no longer there)")  # its page isn't in www/
        await task_status(params)
        assert snake not in [job["task_id"] for job in params.result["jobs"]]

    asyncio.run(main())


def test_a_new_job_while_one_waits_for_an_answer_checks_first(tmp_path):
    """ "Okay, just send it", said as a job asked its question, started a second job."""

    async def main():
        db, runner = make(tmp_path, quick_agent)
        do_task = tools_for(runner, db)[0]
        asking = asyncio.create_task(runner.questions.ask(3, "clarify", "Which summary?"))
        await asyncio.sleep(0.01)

        params = FakeParams()
        await do_task(params, request="send the summary")
        assert params.result["not_started"] and "answer_job" in params.result["note"]
        assert db.recent_tasks(3) == []  # nothing started

        await do_task(params, request="send the summary")  # asked again: really a new job
        assert params.result["status"] == "done"
        runner.questions.answer(3, "the news")
        await asking

    asyncio.run(main())
