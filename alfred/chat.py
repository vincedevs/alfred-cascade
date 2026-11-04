"""Messages to Google Chat: "tell the family chat I'm running late".

Each space is a Google Chat incoming webhook, set in .env as ALFRED_GOOGLE_CHAT_<NAME>
(config.py). Nothing is sent without your spoken yes: Alfred reads the message back and
asks, the same way a background job asks before sending an email (questions.py). The
question is asked as "job 0" so your answer finds its way here. Every message, sent or
not, goes to the audit log; webhook addresses carry a key, so they are never logged.
"""

import asyncio
import json
import urllib.request
from collections.abc import Callable

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

from alfred.audit import AuditLog
from alfred.questions import Questions, is_yes

CHAT_JOB = 0  # the "job" that asks about messages
APPROVAL_SECONDS = 60  # a message is a quick thing: no answer within a minute means no
SEND_SECONDS = 10


def post(url: str, text: str) -> None:
    """Send one message to a webhook (tests replace this)."""
    request = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json; charset=UTF-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=SEND_SECONDS) as response:
        response.read()


class Chat:
    def __init__(self, spaces: dict[str, str], questions: Questions, audit: AuditLog):
        self._spaces, self._questions, self._audit = spaces, questions, audit
        self._sending: asyncio.Task | None = None
        self.on_done: list[Callable[[str], None]] = []  # Alfred says how it went (announcer.py)

    @property
    def names(self) -> list[str]:
        return sorted(self._spaces)

    def space(self, name: str) -> str | None:
        """The space you mean: by name, or the only one, or the one called "default"."""
        name = name.strip().lower().removesuffix(" chat").strip()
        if name in self._spaces:
            return name
        if not name and len(self._spaces) == 1:
            return self.names[0]
        if not name and "default" in self._spaces:
            return "default"
        return None

    def start_sending(self, text: str, space: str) -> asyncio.Task | None:
        """Ask you, then send. None if a message is already waiting for your answer."""
        if self._sending and not self._sending.done():
            return None
        self._sending = asyncio.create_task(self._ask_then_send(text, space), name="chat")
        return self._sending

    async def _ask_then_send(self, text: str, space: str) -> None:
        details = {"space": space, "text": text}
        answer = await self._questions.ask(
            CHAT_JOB, "approve", f'Shall I send "{text}" to the {space} chat?', APPROVAL_SECONDS
        )
        if not is_yes(answer):
            outcome = "no answer" if answer is None else "declined"
            self._audit.record(CHAT_JOB, "send_chat_message", details, outcome, "")
            self._done(f"The message to the {space} chat was not sent ({outcome}).")
            return
        self._audit.record(CHAT_JOB, "send_chat_message", details, "approved", "")
        try:
            await asyncio.to_thread(post, self._spaces[space], text)
        except Exception as error:  # never log the address: it holds the key
            reason = f"{type(error).__name__} {getattr(error, 'code', '')}".strip()
            logger.warning(f"Google Chat message to {space} failed: {reason}")
            self._audit.record(CHAT_JOB, "send_chat_message", details, "failed", reason)
            self._done(f"Sending to the {space} chat failed ({reason}). Tell the user.")
            return
        logger.info(f"Sent a Google Chat message to {space}")
        self._audit.record(CHAT_JOB, "send_chat_message", details, "sent", "")
        self._done(f"The message was sent to the {space} chat. Say so in a few words.")

    def _done(self, note: str) -> None:
        for listener in list(self.on_done):
            listener(note)


def chat_tools(chat: Chat) -> list:
    """Alfred's send_chat_message tool, if any Google Chat space is set up."""
    if not chat.names:
        return []

    async def send_chat_message(params: FunctionCallParams, text: str, space: str = ""):
        """Send a message to a Google Chat space. Alfred reads it back and asks the user
        first; it is only sent after a clear yes.

        Args:
            text: The message, exactly as it should appear, written as the user (not about
                them). Keep it short and natural.
            space: Which chat space, by name. Leave empty for the usual one.
        """
        name = chat.space(space)
        if name is None:
            await params.result_callback(
                {"error": f"No chat space called {space!r}. Spaces: {', '.join(chat.names)}."}
            )
            return
        if chat.start_sending(text, name) is None:
            await params.result_callback(
                {"error": "Another message is still waiting for the user's answer."}
            )
            return
        await params.result_callback(
            {
                "space": name,
                "note": "Alfred is reading it back and asking the user now. Don't ask "
                "again yourself; the outcome is announced.",
            }
        )

    return [send_chat_message]
