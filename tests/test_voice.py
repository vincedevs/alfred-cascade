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
    conversation = SimpleNamespace(_user_speaking=True, _waiting_since=None, worker=FakeWorker())

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
    conversation = SimpleNamespace(_user_speaking=True, _waiting_since=None, worker=FakeWorker())
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


def test_mm_hmm_does_not_stop_alfred_but_a_sentence_does():
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
    from pipecat.turns.user_stop import EagerUserTurnStopStrategy

    user_turns, _alfred_turns = voice.create_turns(LLMContext())
    turns = user_turns._params.user_turn_strategies
    (start,) = turns.start
    assert isinstance(start, MinWordsUserTurnStartStrategy)
    assert isinstance(start, voice.NotHisEcho)  # and his own voice heard back doesn't
    assert start._min_words == voice.INTERRUPT_WORDS > 2  # "mm-hmm yeah" is two
    assert [type(stop) for stop in turns.stop] == [EagerUserTurnStopStrategy]


def test_news_waits_until_alfred_has_answered_you(monkeypatch):
    """Said while he's still writing his answer, news would get into it and be said twice."""
    monkeypatch.setattr(voice, "SAY_WHEN_FREE_EVERY", 0.005)
    conversation = SimpleNamespace(_user_speaking=False, _waiting_since=1.0, worker=FakeWorker())

    async def main():
        saying = asyncio.create_task(voice.Conversation._say_when_free(conversation, "News."))
        await asyncio.sleep(0.03)
        assert conversation.worker.frames == []  # his answer isn't out yet
        conversation._waiting_since = None
        await saying

    asyncio.run(main())
    assert len(conversation.worker.frames) == 1


def test_the_same_news_twice_is_said_once():
    started = []
    conversation = SimpleNamespace(
        _said_notes={}, llm=SimpleNamespace(create_task=lambda job, name: started.append(job))
    )
    conversation._say_when_free = lambda note, still=None: SimpleNamespace(
        close=lambda: None, note=note
    )
    voice.Conversation.say(conversation, "The game is ready.")
    voice.Conversation.say(conversation, "The game is ready.")
    voice.Conversation.say(conversation, "Your timer is done.")
    assert [job.note for job in started] == ["The game is ready.", "Your timer is done."]


def test_starting_a_job_is_said_at_once():
    said, started = [], []

    async def say_aloud(text):
        said.append(text)

    conversation = SimpleNamespace(
        tool_calls=0,
        timer=SimpleNamespace(activity=lambda: None),
        llm=SimpleNamespace(create_task=lambda job, name: started.append(job)),
        _say_aloud=say_aloud,
        services=SimpleNamespace(questions=SimpleNamespace(waiting=lambda: [])),
    )
    voice.Conversation._used_tools(conversation, [SimpleNamespace(function_name="recall")])
    assert started == []  # a quick tool: he answers himself
    voice.Conversation._used_tools(conversation, [SimpleNamespace(function_name="do_task")])
    asyncio.run(started[0])
    assert said == [voice.JOB_STARTED] and conversation.tool_calls == 2


def test_running_out_of_credits_names_the_service():
    class CartesiaTTSService:
        pass

    error = SimpleNamespace(error="402 Payment Required", processor=CartesiaTTSService())
    assert voice.NO_CREDITS.search(error.error)
    assert voice.whose_error(error) == "Cartesia"
    no_quota = SimpleNamespace(error="insufficient_quota", processor=None)
    assert voice.whose_error(no_quota) == "OpenAI"
    assert not voice.NO_CREDITS.search("timeout after 4020 ms")
    cartesia = "Error: {'error_code': 'quota_exceeded', 'message': 'Generation stopped'}"
    assert voice.NO_CREDITS.search(cartesia)  # what Cartesia actually sent on the Pi


def test_his_name_or_stop_interrupts_him_at_once():
    assert voice.STOP_HIM.search("Hey, Alfred.")
    assert voice.STOP_HIM.search("Alfred")
    assert voice.STOP_HIM.search("Stop.") and voice.STOP_HIM.search("wait")
    assert not voice.STOP_HIM.search("mm-hmm")
    assert not voice.STOP_HIM.search("Music stopped.")  # his own words, heard back


def test_no_right_away_while_a_job_waits_for_an_answer():
    started = []
    conversation = SimpleNamespace(
        tool_calls=0,
        timer=SimpleNamespace(activity=lambda: None),
        llm=SimpleNamespace(create_task=lambda job, name: started.append(job)),
        services=SimpleNamespace(questions=SimpleNamespace(waiting=lambda: ["a question"])),
    )
    voice.Conversation._used_tools(conversation, [SimpleNamespace(function_name="do_task")])
    assert started == []
