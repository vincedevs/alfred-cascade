"""The rules for what a background job may do. Plain code, no AI: a web page that tells
Claude "ignore your instructions" cannot change them.

Before every tool call a job makes, decide() puts it in one of three groups:

    allow  runs straight away (web search, files in the workspace, sandboxed commands)
    ask    pauses the job until you say yes out loud (sending email, installing system
           tools, anything these rules don't recognise)
    deny   never runs (files outside the workspace, secrets, sudo, payments and locks)

Guard turns those decisions into the SDK's PreToolUse hook: it asks you when needed and
writes every decision to the audit log.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from alfred.audit import AuditLog
from alfred.questions import Questions, is_yes

WEB_TOOLS = {"WebSearch", "WebFetch"}
FILE_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit"}
OUR_SAFE_TOOLS = {"mcp__alfred__ask_user"}  # asking you a question is always fine

# Shell commands. The sandbox already keeps them inside the workspace and off the web
# (package sites aside); these rules catch the rest.
# A .env file is a path part: "cat .env", "~/x/.env.local"; but not "process.env" in code.
ENV_FILE = r"(?:^|[\s/'\"=:<>])\.env(?:\.[\w-]+)?\b"
FORBIDDEN_COMMANDS = re.compile(ENV_FILE + r"|\bsudo\b|\.ssh\b|\bkeychain\b|security\s+find-", re.I)
ASK_COMMANDS = re.compile(
    r"\b(brew|apt|apt-get|port|snap)\s+(install|upgrade|remove|uninstall)\b"
    r"|\bnpm\s+(install|i)\s+(-g|--global)\b"
    r"|\bgit\s+push\b"
    r"|\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b",  # downloading and running a script
    re.I,
)
# Alfred has no tools for money or locks; if one ever appears, it is refused.
MONEY_OR_LOCKS = {"pay", "payment", "purchase", "buy", "checkout", "order", "transfer"}
MONEY_OR_LOCKS |= {"lock", "unlock", "door"}
PREVIEW_WORDS = 12  # how much of an email's body Alfred reads out


@dataclass(frozen=True)
class Decision:
    action: str  # "allow" | "ask" | "deny"
    reason: str  # for deny: why; for ask: the action, in words Alfred reads out


def decide(tool: str, tool_input: dict, workspace: Path) -> Decision:
    if tool in WEB_TOOLS or tool in OUR_SAFE_TOOLS:
        return Decision("allow", "reading or asking")
    if tool in FILE_TOOLS:
        return _decide_file(tool, tool_input, workspace)
    if tool == "Bash":
        return _decide_command(tool_input)
    if tool == "mcp__alfred__send_email":
        return Decision("ask", _describe_email(tool_input))
    if MONEY_OR_LOCKS & set(re.split(r"[\W_]+", tool.lower())):  # whole words of the name
        return Decision("deny", "payments and locks are not allowed")
    return Decision("ask", f"use the tool {tool} with {str(tool_input)[:150]}")


def _describe_email(tool_input: dict) -> str:
    """What Alfred reads out: the exact recipient and subject, and how the email begins."""
    to, subject = tool_input.get("to", "?"), tool_input.get("subject", "")
    words = str(tool_input.get("body", "")).split()
    gist = " ".join(words[:PREVIEW_WORDS]) + ("..." if len(words) > PREVIEW_WORDS else "")
    return f"send an email to {to} with the subject: {subject}. It begins: {gist}"


def _decide_file(tool: str, tool_input: dict, workspace: Path) -> Decision:
    # A file search's pattern is a path too ("/Users/me/**"); Grep's main pattern is text.
    pattern = tool_input.get("pattern" if tool == "Glob" else "glob") or ""
    if pattern.startswith(("/", "~")) or ".." in pattern:
        return Decision("deny", "file searches must stay inside the workspace folder")
    raw = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    path = (workspace / raw).resolve() if raw else workspace.resolve()
    if path.name.startswith(".env"):
        return Decision("deny", "secret files are off limits")
    if not path.is_relative_to(workspace.resolve()):
        return Decision("deny", f"{path} is outside the workspace folder")
    return Decision("allow", "a file in the workspace")


def _decide_command(tool_input: dict) -> Decision:
    command = tool_input.get("command", "")
    if tool_input.get("dangerouslyDisableSandbox"):
        return Decision("deny", "commands must run in the sandbox")
    if FORBIDDEN_COMMANDS.search(command):
        return Decision("deny", "that command touches secrets or needs admin rights")
    if ASK_COMMANDS.search(command):
        return Decision("ask", f"run this command: {command[:200]}")
    return Decision("allow", "a command in the sandbox")


class Guard:
    """Checks every tool call a job makes (the SDK's PreToolUse hook)."""

    def __init__(self, workspace: Path, questions: Questions, audit: AuditLog):
        self._workspace = workspace
        self._questions = questions
        self._audit = audit
        self._declined: set[tuple[int, str]] = set()  # (job, action) you already said no to
        self.refused: set[str] = set()  # tool calls refused here (their results aren't logged)

    def hook_for(self, task_id: int):
        async def pre_tool_use(hook_input: dict, tool_use_id, _context) -> dict:
            allowed, reason = await self.check(
                task_id, hook_input["tool_name"], hook_input.get("tool_input") or {}
            )
            if not allowed:
                self.refused.add(tool_use_id or hook_input.get("tool_use_id", ""))
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow" if allowed else "deny",
                    "permissionDecisionReason": reason,
                }
            }

        return pre_tool_use

    async def check(self, task_id: int, tool: str, tool_input: dict) -> tuple[bool, str]:
        """Returns (may it run, the reason Claude is told)."""
        decision = decide(tool, tool_input, self._workspace)
        allowed, outcome = decision.action == "allow", decision.action
        if decision.action == "ask":
            allowed, outcome = await self._ask(task_id, decision.reason)
        reason = decision.reason if allowed else _refusal(outcome, decision.reason)
        self._audit.record(task_id, tool, tool_input, outcome, decision.reason)
        if not allowed:
            logger.info(f"Task {task_id}: {tool} refused ({outcome}): {decision.reason}")
        return allowed, reason

    async def _ask(self, task_id: int, action: str) -> tuple[bool, str]:
        if (task_id, action) in self._declined:
            return False, "declined before"
        answer = await self._questions.ask(task_id, "approve", f"May I {action}?")
        if is_yes(answer):
            return True, "approved"
        self._declined.add((task_id, action))
        return False, "no answer" if answer is None else "declined"


def _refusal(outcome: str, reason: str) -> str:
    if outcome == "deny":
        return f"Not allowed: {reason}."
    if outcome == "no answer":
        return "The user did not answer in time, so this was not done. Do not try again."
    return "The user said no. Do not try this again; finish and say it was not done."
