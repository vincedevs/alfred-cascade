"""The benchmark version of alfred/voice.py: one conversation, built for fd-benchmark.

Alfred's own building blocks are imported, not copied, so the benchmark runs the same
pipeline, models, voice and settings as Alfred:

    transport -> Deepgram Flux -> your turns -> GPT-6 Luna (web search) -> Cartesia
              -> output -> Alfred's turns

What differs, and why:
- The transport is fd-benchmark's (clips in, Alfred's voice out), not the microphone.
- No wake word and no idle close: one continuous conversation for the whole round, which
  the benchmark ends.
- No background jobs, memory or reminders: no tools besides web search, no database, and
  nothing written to ~/.alfred. Their prompt sections are left out (benchmark/prompts.py).
"""

from dataclasses import replace

from loguru import logger
from pipecat.adapters.llm.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.workers.runner import WorkerRunner

import alfred.voice as alfred_voice
from alfred.carry_on import CarryOnAfterAside
from alfred.config import Config
from alfred.speakable import SpeakableText
from benchmark.prompts import benchmark_prompt

ONE_HOUR = 3600.0  # "idle" time for Pipecat: longer than any round, so it never fires


async def build_benchmark_conversation(transport, config: Config):
    """Returns (a coroutine that runs the conversation, an async function that stops it)."""
    stt, llm, tts = (
        alfred_voice.create_stt(config),
        create_llm(config),
        alfred_voice.create_tts(config),
    )
    tools = ToolsSchema(
        standard_tools=[], custom_tools={AdapterType.OPENAI: [alfred_voice.WEB_SEARCH]}
    )
    context = LLMContext(messages=[], tools=tools)
    user_turns, alfred_turns = alfred_voice.create_turns(context)
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_turns,
            llm,
            CarryOnAfterAside(),
            SpeakableText(),
            tts,
            transport.output(),
            alfred_turns,
        ]
    )
    worker = alfred_voice.create_worker(pipeline, replace(config, idle_seconds=ONE_HOUR))

    @stt.event_handler("on_connected")
    async def on_connected(_stt):
        logger.info("Listening (Deepgram connected, benchmark mode)")

    alfred_voice.log_conversation(llm, user_turns, alfred_turns, on_line=lambda _who, _text: None)
    runner = WorkerRunner(handle_sigint=False)  # the launcher handles Ctrl+C

    async def run() -> None:
        await runner.add_workers(worker)
        await runner.run()

    async def cancel() -> None:
        await worker.cancel()

    return run(), cancel


def create_llm(config: Config):
    """Alfred's own create_llm(), unchanged, reading the benchmark's prompt instead of the
    full one. (It reads it while it runs, so swapping the reader for that one call is
    enough; it is put back straight after.)"""
    extra = f"\n\n## Now\n{config.now():%A %d %B %Y, %I:%M %p} (the user's local time)."
    extra += config.home_text()
    real_reader = alfred_voice.load_prompt
    alfred_voice.load_prompt = benchmark_prompt
    try:
        return alfred_voice.create_llm(config, extra)
    finally:
        alfred_voice.load_prompt = real_reader
