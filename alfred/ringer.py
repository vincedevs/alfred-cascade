"""The sound of a timer that's done or an alarm going off, repeated until you answer.

A timer beeps for up to TIMER_SECONDS; an alarm plays for up to ALARM_SECONDS. Saying
"stop" (or "snooze", for an alarm) silences it: announcer.py hears that at once, or the
model's stop_ringing tool does it. Echo cancellation removes the sound from what Alfred
hears, so he can still hear you over it. The sound waits while Alfred is speaking, so
it never chops up his words.
"""

import asyncio
import time

from loguru import logger

TIMER_SECONDS = 30
ALARM_SECONDS = 120
GAP_SECONDS = 1.0  # silence between repeats
ANSWERED_SECONDS = 15  # how long "stop"/"snooze" counts as just answered


class Ringer:
    def __init__(self, engine, timer_sound: bytes, alarm_sound: bytes, sample_rate: int):
        self._engine = engine
        self._sounds = {"timer": timer_sound, "alarm": alarm_sound}
        self._rate = sample_rate
        self._ringing: asyncio.Task | None = None
        self.alarm_id: int | None = None  # the alarm ringing now, for "snooze"
        self._answered: tuple[str, float] | None = None  # "stopped", "snoozed until 2:17 PM"

    @property
    def ringing(self) -> bool:
        return self._ringing is not None and not self._ringing.done()

    def ring(self, kind: str, alarm_id: int | None = None) -> None:
        """kind: "timer" or "alarm". Replaces whatever is ringing."""
        self.stop()
        self.alarm_id = alarm_id if kind == "alarm" else None
        limit = ALARM_SECONDS if kind == "alarm" else TIMER_SECONDS
        self._ringing = asyncio.create_task(self._repeat(self._sounds[kind], limit))

    def answered(self, what: str) -> None:
        """Remember how the ringing was just answered ("snoozed until 2:17 PM")."""
        self._answered = (what, time.monotonic())

    def just_answered(self) -> str | None:
        """How the ringing was answered, if that was a moment ago. The model often gets
        "stop" or "snooze" too, after announcer.py already did it."""
        if self._answered and time.monotonic() - self._answered[1] < ANSWERED_SECONDS:
            return self._answered[0]
        return None

    def stop(self) -> bool:
        """Silence it. True if something was ringing."""
        was_ringing = self.ringing
        if self._ringing:
            self._ringing.cancel()
            self._ringing = None
            self._engine.stop_playback()
        if was_ringing:
            logger.info("Ringing stopped")
        return was_ringing

    async def _repeat(self, sound: bytes, limit: float) -> None:
        length = len(sound) / 2 / self._rate  # 16-bit mono
        played = 0.0
        while played < limit:
            if self._engine.queued_playback_seconds() < 0.1:  # not while Alfred speaks
                self._engine.play(sound)
                await asyncio.sleep(length)
                played += length
            await asyncio.sleep(GAP_SECONDS)
            played += GAP_SECONDS
        logger.info("Nobody answered: ringing stopped by itself")
