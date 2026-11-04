"""When a conversation closes: warning tone first, and staying open while a job is busy."""

import asyncio
from types import SimpleNamespace

from alfred import idle
from alfred.idle import QuietTimer, check_every


class Fake:
    def __init__(self, busy=False, waiting=()):
        self.played, self.frames, self.busy, self._waiting = [], [], busy, list(waiting)
        self.closing = False
        # stands in for the engine's microphone check (audio_log.py)
        self.activity = SimpleNamespace(
            mic=SimpleNamespace(on=False), speaker=SimpleNamespace(on=False)
        )

    def play(self, sound):
        self.played.append(sound)

    async def queue_frame(self, frame):
        self.frames.append(type(frame).__name__)

    async def cancel(self, reason=None):
        self.frames.append("cancel")

    def waiting(self):
        return self._waiting


def make(monkeypatch, **kwargs):
    monkeypatch.setattr(idle, "WARNING_SECONDS", 0.05)
    monkeypatch.setattr(idle, "TONE_SECONDS", 0.0)
    monkeypatch.setattr(idle, "LISTEN_EVERY", 0.005)
    monkeypatch.setattr(idle, "ANSWER_SECONDS", 0.05)
    monkeypatch.setattr(idle, "RESCUE_SECONDS", 0.2)
    fake = Fake(**kwargs)

    def on_closing():
        fake.closing = True

    def is_busy():
        return fake.busy or bool(fake.waiting())

    return fake, QuietTimer(fake, fake, "warning", 25.0, is_busy, on_closing)


def test_warns_then_closes(monkeypatch):
    async def main():
        fake, timer = make(monkeypatch)
        await timer.on_idle()
        assert fake.played == ["warning"] and fake.frames == []
        await asyncio.sleep(0.1)
        assert fake.frames == ["EndFrame"] and fake.closing

    asyncio.run(main())


def test_speaking_after_the_warning_keeps_it_open_if_alfred_answers(monkeypatch):
    async def main():
        fake, timer = make(monkeypatch)
        await timer.on_idle()
        timer.you_started()
        await asyncio.sleep(0.1)
        assert fake.frames == []  # held open while you speak
        timer.you_said()
        timer.activity()  # Alfred answers
        await asyncio.sleep(0.3)
        assert fake.frames == []

    asyncio.run(main())


def test_speech_alfred_never_answers_does_not_keep_it_open(monkeypatch):
    """Song lyrics or a TV: heard as speech, but Alfred doesn't answer."""

    async def main():
        fake, timer = make(monkeypatch)
        timer.you_started()  # before the warning: changes nothing
        timer.you_said()
        await timer.on_idle()
        assert fake.played == ["warning"]
        timer.you_started()  # after the warning: held open...
        await asyncio.sleep(0.02)
        timer.you_said()
        await asyncio.sleep(0.1)  # ...but nobody answered within ANSWER_SECONDS
        assert fake.frames == ["EndFrame"]

    asyncio.run(main())


def test_endless_background_talk_is_held_open_only_so_long(monkeypatch):
    async def main():
        fake, timer = make(monkeypatch)
        await timer.on_idle()
        for _ in range(12):  # line after line of lyrics, never answered
            timer.you_started()
            await asyncio.sleep(0.02)
            timer.you_said()
        await asyncio.sleep(0.1)
        assert fake.frames == ["EndFrame"]  # RESCUE_SECONDS at most

    asyncio.run(main())


def test_the_warning_waits_for_a_long_sentence_to_end(monkeypatch):
    async def main():
        fake, timer = make(monkeypatch)
        timer.you_started()  # you're still talking when the quiet time runs out
        await timer.on_idle()
        await asyncio.sleep(0.1)
        assert fake.frames == []
        timer.you_said()
        timer.activity()
        await asyncio.sleep(0.3)
        assert fake.frames == []

    asyncio.run(main())


def test_stays_open_while_a_job_is_busy(monkeypatch):
    async def main():
        fake, timer = make(monkeypatch, busy=True)
        await timer.on_idle()
        assert fake.played == [] and fake.frames == []

    asyncio.run(main())


def test_warning_comes_before_the_idle_time():
    assert check_every(25.0) == 20.0


def test_a_voice_on_the_microphone_saves_it_once(monkeypatch):
    """You start speaking right at the deadline, before Deepgram notices: stay open.
    But only until Alfred answers, so a TV can't keep Alfred open forever."""

    async def main():
        fake, timer = make(monkeypatch)
        await timer.on_idle()
        await asyncio.sleep(0.01)
        fake.activity.mic.on = True  # you start speaking after the tone
        await asyncio.sleep(0.02)
        timer.you_started()  # Deepgram notices a moment later
        await asyncio.sleep(0.1)
        assert fake.frames == []  # saved by the microphone check, while Alfred answers
        await asyncio.sleep(0.2)
        assert fake.frames == ["EndFrame"]  # still "speaking", but Alfred never answered

    asyncio.run(main())


def test_the_tone_echoing_on_the_microphone_does_not_count(monkeypatch):
    async def main():
        fake, timer = make(monkeypatch)
        fake.activity.mic.on = True  # already "on" when the warning starts: the tone's echo
        await timer.on_idle()
        await asyncio.sleep(0.1)
        assert fake.frames == ["EndFrame"]

    asyncio.run(main())


def test_music_heard_only_by_our_microphone_check_closes_soon(monkeypatch):
    """The mic check hears music after the warning, but Deepgram never reports speech:
    close after ANSWER_SECONDS, not the longer RESCUE_SECONDS."""

    async def main():
        fake, timer = make(monkeypatch)
        monkeypatch.setattr(idle, "RESCUE_SECONDS", 10.0)
        await timer.on_idle()
        await asyncio.sleep(0.01)
        fake.activity.mic.on = True
        await asyncio.sleep(0.15)
        assert fake.frames == ["EndFrame"]

    asyncio.run(main())


def quick_goodbye(monkeypatch):
    monkeypatch.setattr(idle, "GOODBYE_STARTS_WITHIN", 0.1)
    monkeypatch.setattr(idle, "GOODBYE_PAUSE", 0.03)
    monkeypatch.setattr(idle, "GOODBYE_AT_MOST", 0.5)


def test_told_to_sleep_mid_goodnight_closes_when_it_ends(monkeypatch):
    """The usual case: Alfred says goodnight while the router handles the request."""

    async def main():
        fake, timer = make(monkeypatch)
        quick_goodbye(monkeypatch)
        fake.activity.speaker.on = True  # "Goodnight, sir."
        timer.sleep_after_goodbye()
        timer.activity()  # his goodbye doesn't keep the session open
        timer.you_started()  # nor does anything said now
        await asyncio.sleep(0.15)
        assert fake.frames == []  # still saying goodnight
        fake.activity.speaker.on = False
        await asyncio.sleep(0.1)
        assert fake.frames == ["cancel"] and fake.closing  # at once, not politely
        assert fake.played == []  # no warning tone

    asyncio.run(main())


def test_told_to_sleep_while_quiet_waits_for_the_goodnight(monkeypatch):
    async def main():
        fake, timer = make(monkeypatch)
        quick_goodbye(monkeypatch)
        monkeypatch.setattr(idle, "GOODBYE_STARTS_WITHIN", 0.3)
        timer.sleep_after_goodbye()
        await asyncio.sleep(0.05)
        fake.activity.speaker.on = True  # the goodnight comes after the hand-off
        await asyncio.sleep(0.4)
        assert fake.frames == []
        fake.activity.speaker.on = False
        await asyncio.sleep(0.1)
        assert fake.frames == ["cancel"]

    asyncio.run(main())


def test_told_to_sleep_without_a_goodbye_still_closes(monkeypatch):
    async def main():
        fake, timer = make(monkeypatch)
        quick_goodbye(monkeypatch)
        timer.sleep_after_goodbye()
        await asyncio.sleep(0.2)
        assert fake.frames == ["cancel"]

    asyncio.run(main())


def test_told_to_sleep_while_a_job_is_busy_still_closes(monkeypatch):
    """The job keeps running; its result is told next time."""

    async def main():
        fake, timer = make(monkeypatch, busy=True)
        quick_goodbye(monkeypatch)
        timer.sleep_after_goodbye()
        await asyncio.sleep(0.2)
        assert fake.frames == ["cancel"]

    asyncio.run(main())


def test_told_to_sleep_just_after_the_goodnight_closes_at_once(monkeypatch):
    """Alfred already said goodnight (and his speaker is quiet): no wait for another."""

    async def main():
        fake, timer = make(monkeypatch)
        quick_goodbye(monkeypatch)
        monkeypatch.setattr(idle, "GOODBYE_STARTS_WITHIN", 5.0)
        timer.sleep_after_goodbye(said_goodnight=True)
        await asyncio.sleep(0.1)
        assert fake.frames == ["cancel"]

    asyncio.run(main())
