import numpy as np

from alfred import audio_log
from alfred.audio_log import AudioActivityLog, dbfs


class Recorder:
    def __init__(self):
        self.lines = []

    def debug(self, message):
        self.lines.append(message)

    error = debug


def block(level_dbfs: float) -> bytes:
    """10 ms of a 440 Hz tone at roughly the given level."""
    amplitude = 32768 * 10 ** (level_dbfs / 20) * np.sqrt(2)
    t = np.arange(160) / 16_000
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.int16).tobytes()


def test_dbfs():
    assert dbfs(bytes(320)) == -100.0
    assert abs(dbfs(block(-20)) - -20) < 0.5


def test_timeline_shows_barge_in(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(audio_log, "logger", recorder)
    log = AudioActivityLog()
    quiet, loud = block(-80), block(-20)

    for _ in range(50):  # Alfred talks, room quiet
        log.add(quiet, loud, None, False)
    for _ in range(50):  # you talk over Alfred
        log.add(loud, loud, None, False)
    for _ in range(50):  # Alfred stops, you keep talking
        log.add(loud, quiet, None, False)

    timeline = [line.split(" (")[0] for line in recorder.lines if not line.startswith("audio")]
    assert timeline == [
        "speaker: Alfred talking",
        "mic: voice",
        "speaker: quiet",
    ]
    assert any("[while Alfred talks]" in line for line in recorder.lines)


def test_voice_is_judged_against_room_noise(monkeypatch):
    """With echo cancellation off the room is loud (-48 dBFS); that must not count as voice."""
    recorder = Recorder()
    monkeypatch.setattr(audio_log, "logger", recorder)
    log = AudioActivityLog()
    room, speech = block(-48), block(-25)

    for _ in range(300):  # 3 s of room noise
        log.add(room, bytes(320), None, False)
    assert not log.mic.on
    for _ in range(50):  # then someone speaks
        log.add(speech, bytes(320), None, False)
    assert log.mic.on
