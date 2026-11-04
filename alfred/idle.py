"""When a conversation closes. Deepgram bills for every second it listens, silence included.

What counts is Alfred: his speaking, his tool calls, his looking something up. Speech he
never answers (a song's lyrics, a TV, people nearby) doesn't keep him awake. After about
`idle_seconds` without him, a soft warning tone plays; if nobody speaks within
WARNING_SECONDS, the session closes (and __main__.py plays the sleep sound). While a job
is running or waiting for your answer, or a timer or alarm rings, Alfred stays open longer,
up to KEEP_OPEN_SECONDS, so you hear the result or the question, or can say "stop".

Speaking after the warning holds the session open, once per quiet stretch, until Alfred
answers: he has ANSWER_SECONDS after you finish (RESCUE_SECONDS at most), or it closes.
During the warning, our own microphone check (audio_log.py) also counts: it hears you a
moment before Deepgram reports you speaking, which matters right at the deadline.

Told to go to sleep (greeting.py), the session closes as soon as
Alfred's goodbye ends: no warning tone, nothing said after it keeps it open, and it is
cancelled at once rather than closed politely (that takes about a second more).
"""

import asyncio
import time

from loguru import logger
from pipecat.frames.frames import EndFrame

WARNING_SECONDS = 5  # the warning tone plays this long before closing
KEEP_OPEN_SECONDS = 120  # most quiet time while a job is busy
TONE_SECONDS = 1.0  # the tone and its echo in the room aren't you speaking
ANSWER_SECONDS = 5  # after the warning, Alfred must answer this soon after you finish
RESCUE_SECONDS = 20  # and speech he doesn't answer holds it open this long at most
LISTEN_EVERY = 0.05
GOODBYE_STARTS_WITHIN = 1.5  # told to sleep and Alfred is quiet: wait this long for a goodbye
GOODBYE_PAUSE = 0.4  # this much quiet after the goodbye means it's over
GOODBYE_AT_MOST = 10.0  # close by then even if Alfred is still talking


def check_every(idle_seconds: float) -> float:
    """How often Pipecat should report quiet: early enough to warn before closing."""
    return max(idle_seconds - WARNING_SECONDS, 1.0)


class QuietTimer:
    """is_busy(): is a job running or waiting for an answer? on_closing(): called just
    before the session is closed."""

    def __init__(self, worker, engine, warning_sound, idle_seconds: float, is_busy, on_closing):
        self._worker, self._engine, self._warning_sound = worker, engine, warning_sound
        self._interval = check_every(idle_seconds)
        self._is_busy, self._on_closing = is_busy, on_closing
        self._quiet_since: float | None = None  # when the current quiet stretch began
        self._closing_soon: asyncio.Task | None = None  # set once the warning has played
        self._closing = False  # too late: the session is closing
        self._rescued_at: float | None = None  # you spoke after the warning (once per stretch)
        self._answer_by = 0.0  # ...and Alfred must answer by then
        self._you_are_mid_sentence = False  # between your turn starting and ending
        self._going_to_sleep = False  # you told Alfred to sleep

    def listen(self, worker, user_turns) -> None:
        """Hook into Pipecat: its quiet reports, and the moment you start speaking (not
        when you finish, which may be too late to stop a closing session)."""

        @worker.event_handler("on_idle_timeout")
        async def on_idle(_worker):
            await self.on_idle()

        @user_turns.event_handler("on_user_turn_started")
        async def on_user_started(_aggregator, _strategy):
            self.you_started()

    async def on_idle(self) -> None:
        """Pipecat calls this after every `check_every` seconds without Alfred."""
        if self._closing_soon:
            return
        now = time.monotonic()
        self._quiet_since = self._quiet_since or now - self._interval
        quiet = now - self._quiet_since
        if self._is_busy() and quiet < KEEP_OPEN_SECONDS:
            logger.info(
                f"Quiet for {quiet:.0f} s, but a job is busy or something rings: staying open"
            )
            return
        logger.info(f"Quiet for {quiet:.0f} s: warning tone, closing in {WARNING_SECONDS} s")
        self._engine.play(self._warning_sound)
        if self._you_are_mid_sentence:  # a long sentence, or background talk: let it end
            self._hold_for_answer()
        else:
            self._closing_soon = asyncio.create_task(self._close_after_warning())

    def activity(self) -> None:
        """Alfred spoke or got to work: start counting quiet again, and don't close."""
        self._quiet_since = None
        self._rescued_at = None
        if self._going_to_sleep:
            return  # his goodbye: the session closes after it
        if self._closing:
            logger.info("Alfred answered just after the session started closing")
        elif self._closing_soon:
            logger.info("Alfred answered: staying open")
            self.stop()

    def you_started(self) -> None:
        """You began speaking. Only matters after the warning: it holds the session open
        while Alfred answers."""
        self._you_are_mid_sentence = True
        if self._going_to_sleep:
            return
        if self._rescued_at is not None and not self._closing:  # Deepgram heard you too
            self._answer_by = max(self._answer_by, self._rescued_at + RESCUE_SECONDS)
        elif self._closing_soon and not self._closing:
            logger.info("Speech after the warning: waiting for Alfred to answer")
            self.stop()
            self._hold_for_answer()

    def you_said(self) -> None:
        """You finished speaking: if that was after the warning, Alfred has
        ANSWER_SECONDS to answer it."""
        self._you_are_mid_sentence = False
        if self._rescued_at is not None:
            limit = self._rescued_at + RESCUE_SECONDS
            self._answer_by = min(limit, time.monotonic() + ANSWER_SECONDS)

    def sleep_after_goodbye(self, said_goodnight: bool = False) -> None:
        """You told Alfred to sleep: close once he has said goodbye (said_goodnight: he
        just has), without the tone."""
        if self._closing or self._going_to_sleep:
            return
        logger.info("Told to go to sleep: closing after Alfred's goodbye")
        self.stop()
        self._going_to_sleep = True
        self._closing_soon = asyncio.create_task(self._close_after_goodbye(said_goodnight))

    def stop(self) -> None:
        if self._closing_soon:
            self._closing_soon.cancel()
            self._closing_soon = None

    def _hold_for_answer(self, heard_by_mic_only: bool = False) -> None:
        """Wait for Alfred to answer. Something only our microphone check heard gets
        ANSWER_SECONDS: if it's speech, Deepgram reports it well within that."""
        self._rescued_at = time.monotonic()
        wait = ANSWER_SECONDS if heard_by_mic_only else RESCUE_SECONDS
        self._answer_by = self._rescued_at + wait
        self._closing_soon = asyncio.create_task(self._close_unless_answered())

    async def _close_unless_answered(self) -> None:
        while time.monotonic() < self._answer_by:
            await asyncio.sleep(LISTEN_EVERY)
        logger.info("Alfred didn't answer what was heard (background audio?): closing")
        await self._close()

    async def _close_after_warning(self) -> None:
        started = time.monotonic()
        mic_went_quiet = False  # only a voice that starts after the tone has faded counts
        while time.monotonic() - started < WARNING_SECONDS:
            await asyncio.sleep(LISTEN_EVERY)
            if time.monotonic() - started < TONE_SECONDS:
                continue
            mic_went_quiet = mic_went_quiet or not self._engine.activity.mic.on
            if mic_went_quiet and self._you_are_speaking():
                logger.info("Voice on the microphone after the warning: waiting for Alfred")
                self._hold_for_answer(heard_by_mic_only=True)  # replaces this task
                return
        logger.info("No speech after the warning, closing the session")
        await self._close()

    async def _close_after_goodbye(self, said_goodnight: bool) -> None:
        """Alfred is often already saying goodnight when your words are transcribed, so
        the goodbye is what he is saying now; if he is quiet, it's whatever he says next."""
        speaker = self._engine.activity.speaker
        started = time.monotonic()
        heard, quiet_since = speaker.on or said_goodnight, None
        while time.monotonic() - started < GOODBYE_AT_MOST:
            await asyncio.sleep(LISTEN_EVERY)
            now = time.monotonic()
            if speaker.on:
                heard, quiet_since = True, None
                continue
            quiet_since = quiet_since or now
            if heard and now - quiet_since >= GOODBYE_PAUSE:
                break
            if not heard and now - started >= GOODBYE_STARTS_WITHIN:
                logger.info("No goodbye heard: going to sleep anyway")
                break
        await self._close(at_once=True)

    async def _close(self, at_once: bool = False) -> None:
        self._closing = True
        self._on_closing()
        if at_once:  # told to sleep: nothing left to hear
            await self._worker.cancel(reason="told to go to sleep")
        else:
            await self._worker.queue_frame(EndFrame())  # lets the pipeline finish cleanly

    def _you_are_speaking(self) -> bool:
        sound = self._engine.activity
        return self._rescued_at is None and sound.mic.on and not sound.speaker.on
