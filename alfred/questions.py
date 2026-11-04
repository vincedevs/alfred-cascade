"""Questions a background job asks you: "may I send this email?" or "this year or the
last five?". The job waits, Alfred asks you out loud, and your answer lets it carry on.

A question is saved in the database while it waits. Nobody answering within
QUESTION_TIMEOUT seconds counts as "no"; so does Alfred restarting meanwhile.
"""

import asyncio
import time
from collections.abc import Callable

from loguru import logger

from alfred.db import Database, Question

QUESTION_TIMEOUT = 5 * 60  # seconds; a voice assistant shouldn't leave things hanging
YES = {"yes", "y", "yeah", "yep", "approved"}  # anything else is a no


def is_yes(answer: str | None) -> bool:
    return (answer or "").strip().lower().rstrip(".!") in YES


class Questions:
    def __init__(self, db: Database, timeout: float = QUESTION_TIMEOUT):
        self._db = db
        self._timeout = timeout
        self._waiting: dict[int, asyncio.Future] = {}  # question id -> its answer
        self._answered_at: dict[int, float] = {}  # job -> when you last answered it
        # Called with each new question: Alfred says it (voice.py), or wakes up to say it.
        self.on_asked: list[Callable[[Question], None]] = []
        self.new_question = asyncio.Event()  # __main__.py wakes Alfred up with this
        db.expire_waiting_questions()  # from before a restart: nobody is waiting for those

    async def ask(
        self, task_id: int, kind: str, text: str, timeout: float | None = None
    ) -> str | None:
        """kind: "approve" (yes or no) or "clarify" (any answer). None if nobody answered
        within `timeout` seconds (QUESTION_TIMEOUT unless given)."""
        timeout = timeout or self._timeout
        question = self._db.add_question(task_id, kind, text)
        answer = asyncio.get_running_loop().create_future()
        self._waiting[question.id] = answer
        logger.info(f"Task {task_id} asks: {text}")
        self.new_question.set()
        for listener in list(self.on_asked):
            listener(question)
        try:
            return await asyncio.wait_for(answer, timeout)
        except TimeoutError:
            logger.info(f"Task {task_id}: no answer within {timeout:.0f} s")
            self._db.close_question(question.id, "expired")
            return None
        finally:
            self._waiting.pop(question.id, None)

    def answer(self, task_id: int, text: str) -> Question | None:
        """Answer the job's oldest waiting question. None if it has none."""
        question = next((q for q in self.waiting() if q.task_id == task_id), None)
        if question is None:
            return None
        logger.info(f"Task {task_id}: you answered {text!r}")
        self._answered_at[task_id] = time.monotonic()
        self._db.close_question(question.id, "answered", text)
        if future := self._waiting.get(question.id):
            future.set_result(text)
        return question

    def answered_recently(self, task_id: int, seconds: float = 120) -> bool:
        return time.monotonic() - self._answered_at.get(task_id, -1e9) < seconds

    def waiting(self) -> list[Question]:
        return self._db.waiting_questions()

    def cancel_for_task(self, task_id: int) -> None:
        """The job ended (stopped, say) while asking: its questions no longer need answers."""
        for question in self.waiting():
            if question.task_id == task_id:
                self._db.close_question(question.id, "expired")
                if future := self._waiting.get(question.id):
                    future.cancel()
