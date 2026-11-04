"""Background jobs: Alfred hands a request to Claude and keeps talking.

Each job runs Claude through the Claude Agent SDK as its own asyncio task. Claude
works on its own: it searches, reads pages and writes notes until it has an answer.
Every job is recorded in the database, so after a restart unfinished jobs carry on
from where they were (the SDK can resume a Claude session by its ID). At most
MAX_JOBS_AT_ONCE run together (each is a Claude Code process of a few hundred MB); the
rest wait their turn.

What a job may do is decided by policy.py before every tool call. Shell commands also
run inside the operating system's sandbox (Seatbelt on macOS, bubblewrap on Linux):
they can only write in the workspace, can't read Alfred's own files or your secrets, and
can only reach package sites. If the sandbox isn't installed, jobs get no shell at all.
Every decision goes to the audit log.
"""

import asyncio
import json
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
from loguru import logger

from alfred.agent_tools import agent_tools_server
from alfred.audit import AuditLog
from alfred.config import PROJECT_ROOT, Config, load_prompt
from alfred.db import Database, Task
from alfred.policy import Guard
from alfred.questions import QUESTION_TIMEOUT, Questions

AGENT_TOOLS = ["WebSearch", "WebFetch", "Read", "Write", "Edit", "Glob", "Grep", "Bash"]
MAX_TURNS = 40  # a safety stop for a job that goes round in circles
MAX_JOBS_AT_ONCE = 2  # enough for a Raspberry Pi's memory
# The only sites shell commands can reach (for web pages, jobs use WebFetch instead).
PACKAGE_SITES = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]
SANDBOX = {
    "enabled": True,
    "allowUnsandboxedCommands": False,  # no way out of the sandbox
    "autoAllowBashIfSandboxed": False,  # every command still goes past policy.py
    "network": {"allowedDomains": PACKAGE_SITES},
}
RESUME_PROMPT = (
    "Alfred restarted while you were working on this. Carry on from where you were "
    "and give your final answer."
)

# run_agent(task_id, request, session_id) -> the answer. Tests pass a fake one.
AgentRunner = Callable[[int, str, str | None], Awaitable[str]]


class TaskRunner:
    def __init__(
        self,
        db: Database,
        config: Config,
        questions: Questions,
        audit: AuditLog,
        on_finished: Callable[[Task], None] | None = None,
        run_agent: AgentRunner | None = None,
        about_user: Callable[[], str] = lambda: "",
    ):
        self._db = db
        self._config = config
        self._questions = questions
        self._audit = audit
        self._guard = Guard(config.workspace_dir, questions, audit)
        # Called with each job that finishes: the chime, and the live announcement while
        # a conversation is open (voice.py adds and removes that one).
        self.on_finished: list[Callable[[Task], None]] = [on_finished] if on_finished else []
        self._run_agent = run_agent or self._run_claude
        self._about_user = about_user  # what Alfred knows about you, for jobs' instructions
        self._slots = asyncio.Semaphore(MAX_JOBS_AT_ONCE)
        self._tools = AGENT_TOOLS if sandbox_installed() else AGENT_TOOLS[:-1]  # no Bash
        self._jobs: dict[int, asyncio.Task] = {}
        self._waiting: set[int] = set()  # jobs someone is waiting on (see wait)
        self._closing = False

    @property
    def busy(self) -> bool:
        """True while any job is running."""
        return bool(self._jobs)

    def start(self, request: str) -> int:
        task_id = self._db.add_task(request)
        logger.info(f"Task {task_id} started: {request}")
        self._launch(task_id, request, session_id=None)
        return task_id

    def resume_unfinished(self) -> int:
        """Restart jobs that were still running when Alfred last stopped."""
        unfinished = self._db.running_tasks()
        for task in unfinished:
            logger.info(f"Task {task.id} resumed after restart: {task.request}")
            self._launch(task.id, task.request, task.session_id)
        return len(unfinished)

    async def wait(self, task_id: int, timeout: float) -> Task | None:
        """Wait up to `timeout` seconds for a job, then return its record either way.
        A job that finishes while someone waits for it skips the "finished" callback:
        its result is being handed over directly."""
        job = self._jobs.get(task_id)
        if job:
            self._waiting.add(task_id)
            try:
                await asyncio.wait([job], timeout=timeout)
            finally:
                self._waiting.discard(task_id)
        return self._db.get_task(task_id)

    async def stop(self, task_id: int) -> bool:
        job = self._jobs.get(task_id)
        if not job:
            return False
        job.cancel()
        await asyncio.wait([job])
        return True

    def begin_shutdown(self) -> None:
        """Call the moment shutdown starts. Ctrl+C also reaches Claude's own process, so a
        job may end with an error before close() runs; from now on that isn't a failure."""
        self._closing = True

    async def close(self) -> None:
        """Shutting down: interrupt jobs but leave them 'running' so they resume next time."""
        self._closing = True
        for job in self._jobs.values():
            job.cancel()
        if self._jobs:
            await asyncio.wait(self._jobs.values())

    def _launch(self, task_id: int, request: str, session_id: str | None) -> None:
        self._jobs[task_id] = asyncio.create_task(
            self._run(task_id, request, session_id), name=f"task-{task_id}"
        )

    async def _run(self, task_id: int, request: str, session_id: str | None) -> None:
        try:
            if self._slots.locked():
                logger.info(f"Task {task_id} waits for one of the other jobs to finish")
            async with self._slots:
                result = await self._run_agent(task_id, request, session_id)
            status = "done"
        except asyncio.CancelledError:
            if self._closing:
                raise  # not stopped by you: leave it to resume
            status, result = "stopped", "Stopped at your request."
        except Exception as error:
            if self._closing:
                logger.info(f"Task {task_id} interrupted by shutdown; it resumes next start")
                return
            logger.exception(f"Task {task_id} failed")
            status, result = "failed", f"{type(error).__name__}: {error}"
        finally:
            self._jobs.pop(task_id, None)
            self._questions.cancel_for_task(task_id)

        self._db.finish_task(task_id, status, result)
        logger.info(f"Task {task_id} {status}: {result[:200]}")
        if task_id not in self._waiting:
            task = self._db.get_task(task_id)
            for listener in list(self.on_finished):
                listener(task)

    async def _run_claude(self, task_id: int, request: str, session_id: str | None) -> str:
        options = self._options(task_id, session_id)
        prompt = RESUME_PROMPT if session_id else request
        outcome: ResultMessage | None = None

        tool_calls: dict[str, ToolUseBlock] = {}  # so each result can be matched to its call

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                self._db.set_session(task_id, message.data["session_id"])
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        tool_calls[block.id] = block
                        logger.debug(f"Task {task_id} uses {block.name}: {str(block.input)[:200]}")
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id in tool_calls:
                        self._record_result(task_id, tool_calls.pop(block.tool_use_id), block)
            elif isinstance(message, ResultMessage):
                outcome = message

        if outcome is None:
            raise RuntimeError("Claude stopped without a result")
        logger.info(
            f"Task {task_id}: {outcome.num_turns} turns, {outcome.duration_ms / 1000:.0f} s, "
            f"about ${outcome.total_cost_usd or 0:.3f}"
        )
        if outcome.is_error:
            raise RuntimeError(f"{outcome.subtype}: {outcome.errors or outcome.result}")
        return outcome.result or "Done, but Claude gave no answer."

    def _record_result(self, task_id: int, call: ToolUseBlock, result: ToolResultBlock) -> None:
        """The audit log has the decision; this adds what actually happened."""
        if call.id in self._guard.refused:
            self._guard.refused.discard(call.id)
            return  # refused before it ran: the decision line says it all
        if result.is_error:
            detail = str(result.content)[:300]
            logger.info(f"Task {task_id}: {call.name} failed: {detail[:150]}")
            self._audit.record(task_id, call.name, call.input, "failed", detail)
        else:
            self._audit.record(task_id, call.name, call.input, "ran", "")

    def _options(self, task_id: int, session_id: str | None) -> ClaudeAgentOptions:
        config, workspace = self._config, self._config.workspace_dir
        guard = HookMatcher(  # policy.py checks every tool call, and may wait for your answer
            hooks=[self._guard.hook_for(task_id)], timeout=QUESTION_TIMEOUT + 60
        )
        return ClaudeAgentOptions(
            model=config.agent_model,
            system_prompt=load_prompt("agent") + self._about_user(),
            tools=self._tools,  # the built-in tools Claude can see, plus Alfred's own:
            mcp_servers={"alfred": agent_tools_server(task_id, config, self._questions)},
            hooks={"PreToolUse": [guard]},
            can_use_tool=refuse_unchecked,
            sandbox=SANDBOX,
            settings=json.dumps({"permissions": {"deny": secret_reads(config.data_dir)}}),
            env=job_environment(workspace),
            cwd=workspace,
            max_budget_usd=config.task_budget_usd,
            max_turns=MAX_TURNS,
            setting_sources=[],  # ignore your personal Claude Code settings
            resume=session_id,
        )


def sandbox_installed() -> bool:
    """Shell commands need the operating system's sandbox: Seatbelt (built into macOS), or
    bubblewrap and socat on Linux (`sudo apt install bubblewrap socat`)."""
    needed = ["sandbox-exec"] if sys.platform == "darwin" else ["bwrap", "socat"]
    missing = [tool for tool in needed if not shutil.which(tool)]
    if missing:
        logger.warning(f"Sandbox tools missing ({', '.join(missing)}): jobs get no shell")
    return not missing


async def refuse_unchecked(tool: str, _tool_input: dict, _context) -> PermissionResultDeny:
    """policy.py answers before Claude would ever ask for permission; if something still
    asks, it is refused rather than run."""
    logger.warning(f"Refused {tool}: it skipped the policy check")
    return PermissionResultDeny(message="Not allowed.")


def secret_reads(data_dir: Path) -> list[str]:
    """Places sandboxed commands may not even read ("//" starts an absolute path): your
    personal folders, secrets, and Alfred's own files."""
    home_secrets = (
        "Documents Desktop Downloads Pictures Movies Music .ssh .aws .gnupg .config "
        "Library/Keychains Library/Mail Library/Messages"
    ).split()
    return [
        f"Read(/{PROJECT_ROOT}/**)",  # Alfred's code and your .env
        f"Read(/{data_dir / 'alfred.db'})",
        *(f"Read(~/{folder}/**)" for folder in home_secrets),
    ]


def job_environment(workspace: Path) -> dict[str, str]:
    """Tools like uv and npm keep caches in your home folder, which sandboxed commands
    can't write to; point them inside the workspace instead. Also drop Alfred's own Python
    environment from PATH, so jobs' commands find the normal tools."""
    cache = workspace / ".cache"
    alfred_venv = str(PROJECT_ROOT / ".venv")
    path = os.pathsep.join(
        folder
        for folder in os.environ.get("PATH", "").split(os.pathsep)
        if not folder.startswith(alfred_venv)
    )
    return {
        "PATH": path,
        "XDG_CACHE_HOME": str(cache),
        "UV_CACHE_DIR": str(cache / "uv"),
        "npm_config_cache": str(cache / "npm"),
        "MPLCONFIGDIR": str(cache / "matplotlib"),
    }
