"""Alfred's own tools for background jobs, run here inside Alfred (an in-process MCP
server). Claude sees them as mcp__alfred__ask_user and mcp__alfred__send_email.

Like every tool call, these pass policy.py first: asking is always allowed; sending an
email waits for your yes.
"""

import asyncio

from claude_agent_sdk import create_sdk_mcp_server, tool
from loguru import logger

from alfred.config import Config
from alfred.mail import send_email
from alfred.questions import Questions

NO_ANSWER = (
    "The user did not answer in time. Make the most reasonable assumption, carry on, "
    "and say which assumption you made."
)


def agent_tools(task_id: int, config: Config, questions: Questions) -> list:
    @tool(
        "ask_user",
        "Ask the user one short question out loud and wait for the answer. Only when the "
        "job is unclear and a wrong guess would waste the work. One question at a time.",
        {"question": str},
    )
    async def ask_user(args: dict) -> dict:
        answer = await questions.ask(task_id, "clarify", args["question"])
        return _text(f"The user said: {answer}" if answer else NO_ANSWER)

    @tool(
        "send_email",
        "Send an email from the user's Gmail account. The user is asked to approve it first.",
        {"to": str, "subject": str, "body": str},
    )
    async def send(args: dict) -> dict:
        try:
            await asyncio.to_thread(send_email, config, args["to"], args["subject"], args["body"])
        except Exception as error:
            logger.warning(f"Task {task_id}: email to {args['to']} failed: {error}")
            return _text(f"Sending failed: {error}", error=True)
        logger.info(f"Task {task_id}: email sent to {args['to']}")  # audit log: "ran"
        return _text("Sent.")

    return [ask_user, send]


def agent_tools_server(task_id: int, config: Config, questions: Questions):
    """The tools above as an MCP server for one job (the job's ID is built in)."""
    return create_sdk_mcp_server("alfred", tools=agent_tools(task_id, config, questions))


def _text(text: str, error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": text}]}
    return {**result, "is_error": True} if error else result
