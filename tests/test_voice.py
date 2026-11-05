"""The voice pipeline's own logic: news waits until you've finished speaking, and the
speaker goes quiet at once when you interrupt. (Needs Pipecat with its Deepgram and
Cartesia extras, as installed by `uv sync`.)"""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("pipecat.services.deepgram.flux.stt")
pytest.importorskip("pipecat.services.cartesia.tts")

from pipecat.frames.frames import InterruptionFrame, LLMMessagesAppendFrame  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402
from pipecat.transports.base_output import BaseOutputTransport  # noqa: E402

from alfred import transport, voice  # noqa: E402


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


def test_news_waits_until_you_have_finished_speaking(monkeypatch):
    monkeypatch.setattr(voice, "SAY_WHEN_FREE_EVERY", 0.005)
    conversation = SimpleNamespace(_user_speaking=True, worker=FakeWorker())

    async def main():
        saying = asyncio.create_task(voice.Conversation._say_when_free(conversation, "News."))
        await asyncio.sleep(0.03)
        assert conversation.worker.frames == []  # you're still talking
        conversation._user_speaking = False
        await saying

    asyncio.run(main())
    (frame,) = conversation.worker.frames
    assert isinstance(frame, LLMMessagesAppendFrame) and frame.run_llm
    assert frame.messages == [{"role": "developer", "content": "News."}]


def test_news_is_said_anyway_if_your_turn_never_seems_to_end(monkeypatch):
    monkeypatch.setattr(voice, "SAY_WHEN_FREE_EVERY", 0.005)
    monkeypatch.setattr(voice, "SAY_WITHIN", 0.03)
    conversation = SimpleNamespace(_user_speaking=True, worker=FakeWorker())
    asyncio.run(voice.Conversation._say_when_free(conversation, "News."))
    assert len(conversation.worker.frames) == 1


def test_an_empty_reply_does_not_keep_alfred_awake():
    """Alfred stays silent for side talk or a TV: that isn't him answering."""
    calls = []
    conversation = SimpleNamespace(
        transcript=[],
        _waiting_since=1.0,
        timer=SimpleNamespace(activity=lambda: calls.append("activity")),
        sleep=SimpleNamespace(alfred_said=lambda text: calls.append(text)),
    )
    voice.Conversation._line(conversation, "Alfred", "")
    assert calls == [] and conversation.transcript == []
    assert conversation._waiting_since is None
    voice.Conversation._line(conversation, "Alfred", "Very good, sir.")
    assert calls == ["activity", "Very good, sir."]


def test_interrupting_alfred_empties_the_speaker(monkeypatch):
    stopped = []

    async def passed_on(self, frame, direction):
        pass

    monkeypatch.setattr(BaseOutputTransport, "process_frame", passed_on)
    output = transport._SpeakerOutput.__new__(transport._SpeakerOutput)  # no pipeline needed
    output._engine = SimpleNamespace(stop_playback=lambda: stopped.append(True))
    asyncio.run(output.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM))
    assert stopped == [True]
