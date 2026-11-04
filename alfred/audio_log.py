"""Logs what the microphone and speaker are doing, for debugging.

Two kinds of DEBUG lines go to the log file:
  * a timeline: "mic: voice", "mic: quiet", "speaker: Alfred talking", "speaker: quiet".
    Barge-in time = from "mic: voice" while Alfred is talking to "speaker: quiet".
  * every 10 s, a summary: audio levels, device problems, dropped audio.

Levels are measured on what Alfred actually hears (after echo cancellation, if on).
"Voice" means clearly louder than the room's background noise, which is tracked
as it changes, so the timeline works with echo cancellation on or off.
"""

import time
from collections import deque

import numpy as np
from loguru import logger

WINDOW_BLOCKS = 5  # judge activity every 50 ms
VOICE_ABOVE_NOISE_DB = 10.0  # this much louder than the room counts as someone speaking
MIN_VOICE_DBFS = -60.0  # never call anything quieter than this a voice
NOISE_WINDOWS = 60  # room noise = the quietest moment of the last 3 s
SPEAKER_ON_DBFS = -50.0  # louder than this counts as Alfred talking
QUIET_WINDOWS = 4  # 200 ms below the threshold before we call it quiet
STATS_SECONDS = 10.0


def dbfs(pcm: bytes) -> float:
    """Loudness of 16-bit audio, in dB below full scale (silence is -100)."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    rms = np.sqrt(np.mean(samples**2)) if samples.size else 0.0
    return float(20 * np.log10(rms / 32768)) if rms > 0 else -100.0


class _Activity:
    """On/off with a short hold, so pauses between words don't flip it."""

    def __init__(self, name: str, threshold: float, on_text: str):
        self.name, self.threshold, self.on_text = name, threshold, on_text
        self.on = False
        self._quiet_windows = 0

    def update(self, level: float, context: str) -> None:
        if level > self.threshold:
            self._quiet_windows = 0
            if not self.on:
                self.on = True
                logger.debug(f"{self.name}: {self.on_text} ({level:.0f} dBFS){context}")
        elif self.on:
            self._quiet_windows += 1
            if self._quiet_windows >= QUIET_WINDOWS:
                self.on = False
                logger.debug(f"{self.name}: quiet{context}")


class _NoiseFloor:
    """The room's background level: the quietest moment of the last 3 s.
    Speech has pauses, so it barely moves this; a new steady noise is learned in 3 s."""

    def __init__(self):
        self._recent: deque[float] = deque(maxlen=NOISE_WINDOWS)

    def update(self, level: float) -> None:
        self._recent.append(level)

    @property
    def level(self) -> float:
        return min(self._recent, default=-100.0)

    @property
    def voice_threshold(self) -> float:
        return max(self.level + VOICE_ABOVE_NOISE_DB, MIN_VOICE_DBFS)


class AudioActivityLog:
    def __init__(self):
        self.noise = _NoiseFloor()
        self.mic = _Activity("mic", MIN_VOICE_DBFS, "voice")
        self.speaker = _Activity("speaker", SPEAKER_ON_DBFS, "Alfred talking")
        self._mic_levels: list[float] = []
        self._speaker_levels: list[float] = []
        self._reset_stats()

    def add(self, mic: bytes, speaker: bytes, device_status: str | None, dropped: bool) -> None:
        """Called for every 10 ms block."""
        self._mic_levels.append(dbfs(mic))
        self._speaker_levels.append(dbfs(speaker))
        self._blocks += 1
        self._dropped += dropped
        if device_status:
            self._device_problems += 1
            self._last_device_problem = device_status

        if len(self._mic_levels) >= WINDOW_BLOCKS:
            mic_level, speaker_level = max(self._mic_levels), max(self._speaker_levels)
            self._mic_peak = max(self._mic_peak, mic_level)
            self._speaker_peak = max(self._speaker_peak, speaker_level)
            self._mic_floor = min(self._mic_floor, mic_level)
            talking = " [while Alfred talks]" if self.speaker.on else ""
            self.noise.update(mic_level)
            self.mic.threshold = self.noise.voice_threshold
            self.speaker.update(speaker_level, "")
            self.mic.update(mic_level, talking)
            self._mic_levels.clear()
            self._speaker_levels.clear()

        if time.monotonic() - self._stats_started >= STATS_SECONDS:
            self.log_stats()

    def callback_failed(self, error: str) -> None:
        self._callback_errors += 1
        if self._callback_errors == 1:
            logger.error(f"Audio callback failed (more are counted in the stats): {error}")

    def log_stats(self) -> None:
        if self._blocks:
            problems = f", last '{self._last_device_problem}'" if self._device_problems else ""
            logger.debug(
                f"audio stats: {self._blocks} blocks, mic floor {self._mic_floor:.0f} / peak "
                f"{self._mic_peak:.0f} dBFS, room noise {self.noise.level:.0f} dBFS "
                f"(voice above {self.noise.voice_threshold:.0f}), "
                f"speaker peak {self._speaker_peak:.0f} dBFS, "
                f"device problems {self._device_problems}{problems}, "
                f"callback errors {self._callback_errors}, mic blocks dropped {self._dropped}"
            )
        self._reset_stats()

    def _reset_stats(self) -> None:
        self._stats_started = time.monotonic()
        self._blocks = self._dropped = self._device_problems = self._callback_errors = 0
        self._last_device_problem = ""
        self._mic_peak = self._speaker_peak = -100.0
        self._mic_floor = 0.0
