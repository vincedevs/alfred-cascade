"""Connects Pipecat to the AudioEngine: mic audio in, Alfred's voice out.

A new transport is made for each conversation; the engine keeps running in
between so the wake word can listen.
"""

import asyncio

from pipecat.frames.frames import InputAudioRawFrame, InterruptionFrame, OutputAudioRawFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

from alfred.audio import SAMPLE_RATE, AudioEngine

# Keep little speaker audio queued, so Alfred goes quiet fast when you interrupt him.
MAX_QUEUED_SPEECH_SECONDS = 0.1


class EngineTransport(BaseTransport):
    """The pipeline's input (mic) and output (speaker) for one conversation."""

    def __init__(self, engine: AudioEngine):
        super().__init__()
        params = TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_enabled=True,
            audio_out_sample_rate=SAMPLE_RATE,
            # Pipecat pads the end with 2 s of silence, played in real time, which delayed
            # every close. The engine plays out whatever is queued anyway.
            audio_out_end_silence_secs=0,
        )
        self.mic_input = _MicInput(engine, params)
        self._speaker_output = _SpeakerOutput(engine, params)

    def input(self) -> "_MicInput":
        return self.mic_input

    def output(self) -> "_SpeakerOutput":
        return self._speaker_output


class _MicInput(BaseInputTransport):
    """Moves clean microphone audio from the engine into the pipeline.

    Audio is held back until Deepgram is connected, then sent in order,
    so "Hey Alfred, what's the weather?" works in one breath."""

    def __init__(self, engine: AudioEngine, params: TransportParams):
        super().__init__(params)
        self._engine = engine
        self._session_open = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start_forwarding(self) -> None:
        self._session_open.set()

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        self._task = self.create_task(self._forward_mic(), "forward_mic")

    async def stop(self, frame):
        await self._stop_task()
        await super().stop(frame)

    async def cancel(self, frame):
        await self._stop_task()
        await super().cancel(frame)

    async def cleanup(self):
        await self._stop_task()  # also covers Ctrl+C before the session opened
        await super().cleanup()

    async def _forward_mic(self):
        await self._session_open.wait()
        while True:
            pcm = await self._engine.mic.get()
            await self.push_audio_frame(
                InputAudioRawFrame(audio=pcm, sample_rate=SAMPLE_RATE, num_channels=1)
            )

    async def _stop_task(self):
        if self._task:
            await self.cancel_task(self._task)
            self._task = None


class _SpeakerOutput(BaseOutputTransport):
    """Sends Alfred's voice (already resampled to 16 kHz by Pipecat) to the engine."""

    def __init__(self, engine: AudioEngine, params: TransportParams):
        super().__init__(params)
        self._engine = engine

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def process_frame(self, frame, direction):
        if isinstance(frame, InterruptionFrame):  # you talked over Alfred
            self._engine.stop_playback()  # drop what's queued, so he stops at once
        await super().process_frame(frame, direction)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        self._engine.play(frame.audio)
        while self._engine.queued_playback_seconds() > MAX_QUEUED_SPEECH_SECONDS:
            await asyncio.sleep(0.005)  # wait for the speaker to catch up
        return True
