"""One spoken conversation, from wake word to idle timeout (or "go to sleep").

    microphone -> Deepgram Flux -> your words -> GPT-6 Luna -> Alfred's words -> Cartesia
               -> speaker

Deepgram Flux turns speech into text and also decides when you've finished speaking; GPT-6
Luna starts on its first guess of that. Talking over Alfred stops him, once you've said a
few words ("mm-hmm" doesn't); if those words weren't meant for him, he carries on
(carry_on.py). GPT-6 Luna answers, with OpenAI's
hosted web search, Alfred's own tools for jobs, memory and reminders (tools.py) and Wayne
Manor's for the house (house.py), all called directly: there's no hand-off to another
model. Cartesia speaks the answer in Alfred's voice, minus links and markdown
(speakable.py).

Alfred also speaks up without being asked, about jobs and reminders (announcer.py), and
greets you if you only said his name (greeting.py).

The session closes after about `idle_seconds` with nobody speaking, with a warning tone
first; it stays open longer while a job is busy (idle.py). "Go to sleep" closes it as soon
as Alfred has said goodnight (greeting.py).
"""

import asyncio
import re
import time

from loguru import logger
from pipecat.adapters.llm.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMMessagesAppendFrame,
    LLMRunFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.loggers.debug_log_observer import DebugLogObserver
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_stop import EagerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from alfred.announcer import Announcer, opening_messages, recent_jobs_context
from alfred.audio import SAMPLE_RATE, AudioEngine, load_sound
from alfred.carry_on import CarryOnAfterAside
from alfred.chat import chat_tools
from alfred.config import PROJECT_ROOT, Config, load_prompt
from alfred.echo import TAIL_SECONDS, EchoGuard
from alfred.greeting import SleepCommand, greet_unless_asked
from alfred.house import wayne_manor_tools
from alfred.idle import QuietTimer, check_every
from alfred.services import Services
from alfred.speakable import SpeakableText
from alfred.tools import job_tools, memory_tools, reminder_tools
from alfred.transport import EngineTransport

# Pipecat frames worth a line in the debug log: who is talking, transcripts, tools, errors.
LOGGED_FRAMES = (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    InterruptionFrame,
    TranscriptionFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    ErrorFrame,
)

WEB_SEARCH = {"type": "web_search"}  # hosted by OpenAI, runs on their side
NO_HOUSE = (
    "\n\n## The house right now\nThe house (Wayne Manor: lights, temperature, telephone, "
    "music) isn't connected in this conversation. If asked for any of it, say briefly that "
    "you can't reach the house right now. Don't start a background job for it: jobs can't "
    "reach the house either."
)
LOOKUP_SECONDS = 30  # an answer gets this long (a slow web search) before quiet counts
SAY_WHEN_FREE_EVERY = 0.1  # news waits while you're speaking; checked this often
SAY_WITHIN = 10.0  # ...but no longer than this, in case the end of your turn went unnoticed
REPEAT_SECONDS = 30.0  # the same news twice within this is said only once
# The voice starts once it has this much of the reply (or a moment of it), word by word,
# instead of waiting for a whole sentence: a haiku without full stops waited 1.3 s.
TTS_BUFFER_MS = 150
# While Alfred is speaking, it takes this many of your words to stop him, so "mm-hmm" or
# "alright" don't. When he isn't, your first word starts your turn.
INTERRUPT_WORDS = 3
# ...except his name, or "stop", "wait", "hold on": those stop him straight away.
STOP_HIM = re.compile(r"\balfred\b|^\W*(stop|wait|hold on)\b", re.IGNORECASE)
# Words Flux should expect: it heard "set the mood" as "set them wood", "play it" as
# "payout" and "demo's" as "them was".
KEYTERMS = ["Alfred", "mood", "demo", "Spotify", "Wayne Manor", "living room", "play it"]
# GPT-6 Luna answers straight away, without reasoning first: the fastest setting.
REASONING = "none"
# How a service says the account has no credits left (then nothing works until you add some)
NO_CREDITS = re.compile(
    r"no credits remaining|insufficient_quota|exceeded your current quota|quota.exceeded"
    r"|insufficient credit"
    r"|out of credits|payment required|\b402\b",
    re.IGNORECASE,
)
SERVICES = ("Deepgram", "Cartesia", "OpenAI")  # whose credits: named in the error's processor
JOB_STARTED = "Right away, sir."  # said the moment a job starts, so there's no silence


async def run_conversation(
    engine: AudioEngine, services: Services, woken: bool = False
) -> list[str] | None:
    """Talk until nobody has spoken for a while. Returns what was said, line by line, or
    None if the conversation never started (no internet, a service unreachable, no
    credits). woken: by "Hey Alfred" (then he may greet you), not speaking up himself."""
    house_tools = await wayne_manor_tools(services.config)
    return await Conversation(engine, services, house_tools, woken).run()


class Conversation:
    """One conversation: the pipeline, and what listens to it: announcer.py (news and
    quick answers), idle.py (when to close) and greeting.py (greeting and sleep)."""

    def __init__(self, engine: AudioEngine, services: Services, house_tools: list, woken=False):
        s = self.services = services
        self.engine, self._woken = engine, woken
        self._user_spoke = asyncio.Event()  # Flux heard the user start speaking
        self._user_speaking = False
        self._waiting_since: float | None = None  # you spoke; Alfred hasn't answered yet
        self._said_notes: dict[str, float] = {}  # news Alfred was given, and when
        self.echo = EchoGuard()  # his own voice heard back isn't you interrupting
        self.transcript: list[str] = []
        self.tool_calls = 0
        extra = background(s) + ("" if house_tools else NO_HOUSE)
        opening, self._told_jobs, self._said_reminders = opening_messages(
            s.db, s.questions, s.reminders
        )
        self._has_news = bool(opening)  # Alfred opens with it, so no greeting
        context = LLMContext(messages=opening, tools=alfred_tools(s, house_tools))
        self.stt, self.llm, self.tts = (
            create_stt(s.config),
            create_llm(s.config, extra),
            create_tts(s.config),
        )
        self.transport = EngineTransport(engine)
        user_turns, alfred_turns = create_turns(context, self.echo)
        self.worker = create_worker(
            Pipeline(
                [
                    self.transport.input(),
                    self.stt,
                    user_turns,
                    self.llm,
                    CarryOnAfterAside(),  # cut off by words not for him: he carries on
                    SpeakableText(self.echo),  # no links or markdown read aloud
                    self.tts,
                    self.transport.output(),
                    alfred_turns,
                ]
            ),
            s.config,
        )
        self._listen(engine, user_turns, alfred_turns)

    def _listen(self, engine: AudioEngine, user_turns, alfred_turns) -> None:
        """Who listens to what."""
        s = self.services
        self.announcer = Announcer(
            self.say, s.db, s.tasks, s.questions, s.reminders, s.ringer, s.chat
        )
        self._sleep_sound = load_sound(PROJECT_ROOT / "sounds" / "session_end.wav")
        self._sleep_sound_played = False
        self.timer = quiet_timer(self.worker, engine, s, self._closing, self._looking_up)
        self.out_of_credits = end_if_out_of_credits(self.worker)
        self.sleep = SleepCommand(self.timer.sleep_after_goodbye)  # "go to sleep"
        self.timer.listen(self.worker, user_turns)
        user_turns.add_event_handler("on_user_turn_started", self._user_started)
        user_turns.add_event_handler("on_user_turn_stopped", self._user_stopped)
        self.stt.add_event_handler("on_connected", self._listening)
        log_conversation(self.llm, user_turns, alfred_turns, self._line, self._used_tools)

    async def run(self) -> list[str] | None:
        s = self.services
        s.questions.new_question.clear()  # what's waiting is in the opening message
        s.reminders.wake.clear()
        self.announcer.start()
        if self._has_news:
            await self.worker.queue_frame(LLMRunFrame())  # Alfred opens with the news
        runner = WorkerRunner(handle_sigint=False)  # Ctrl+C is handled in __main__.py
        await runner.add_workers(self.worker)
        started = time.monotonic()
        try:
            await runner.run()  # returns when the conversation has closed
        finally:
            self.announcer.stop()
            self.timer.stop()
            minutes = (time.monotonic() - started) / 60
            logger.info(
                f"Conversation closed after {minutes:.1f} min, {self.tool_calls} tool call(s)"
            )
        if self.out_of_credits.is_set():
            return None  # __main__.py plays the "couldn't connect" beeps
        if not self.announcer.session_open:
            logger.warning("Couldn't reach Deepgram: check the internet connection and the key")
            return None
        if not asyncio.current_task().cancelling():  # not Ctrl+C
            self._play_sleep_sound()  # closed some other way (connection dropped)
        return self.transcript

    def say(self, note: str, still=None) -> None:
        """Alfred says this now, without being asked (announcer.py), once you've finished
        speaking and he has answered you: a plain model would otherwise talk over you, or
        say it twice (once in the answer it's writing, once for this). The same news twice
        within REPEAT_SECONDS is said once. still(), if given, is checked when he's free:
        a job's question that was answered meanwhile isn't asked."""
        now = time.monotonic()
        if now - self._said_notes.get(note, -REPEAT_SECONDS) < REPEAT_SECONDS:
            logger.info("Not repeating news Alfred was just given")
            return
        self._said_notes[note] = now
        self.llm.create_task(self._say_when_free(note, still), "say")

    async def _say_when_free(self, note: str, still=None) -> None:
        waited = 0.0
        while (self._user_speaking or self._waiting_since is not None) and waited < SAY_WITHIN:
            await asyncio.sleep(SAY_WHEN_FREE_EVERY)
            waited += SAY_WHEN_FREE_EVERY
        if still is not None and not still():
            logger.info("Not saying news that's no longer current (a question already settled)")
            return
        message = {"role": "developer", "content": note}
        await self.worker.queue_frame(LLMMessagesAppendFrame(messages=[message], run_llm=True))

    def _closing(self) -> None:
        """Alfred decided to close (quiet, or told to sleep): the sleep sound plays now,
        not after the second or two Pipecat takes to shut the conversation down."""
        self.announcer.closing_now()
        self._play_sleep_sound()

    def _play_sleep_sound(self) -> None:
        """Tells you to say "Hey Alfred" next time. Played once."""
        if not self._sleep_sound_played:
            self._sleep_sound_played = True
            self.engine.play(self._sleep_sound)

    async def _listening(self, _stt) -> None:
        """Deepgram is connected: Alfred hears you from now on. (It may reconnect later in
        the conversation; only the first time counts.)"""
        if self.announcer.session_open:
            return
        self.announcer.session_open = True
        # only now: if nothing connected, you'd not have heard the opening news
        self.services.db.mark_told(self._told_jobs)
        self.services.reminders.mark_said(self._said_reminders)
        logger.info("Listening (Deepgram connected)")
        self.transport.mic_input.start_forwarding()
        if self._woken and not self._has_news:
            greeting = greet_unless_asked(self._user_spoke, self._say_aloud)
            self.llm.create_task(greeting, "greet")

    async def _say_aloud(self, text: str) -> None:
        """Speak these exact words, without the model (the greeting, JOB_STARTED)."""
        self.echo.alfred_said(f" {text} ")
        await self.worker.queue_frame(TTSSpeakFrame(text))

    async def _user_started(self, _aggregator, _strategy) -> None:
        self._user_spoke.set()
        self._user_speaking = True

    async def _user_stopped(self, _aggregator, _strategy, _message) -> None:
        self._user_speaking = False

    def _line(self, who: str, text: str) -> None:
        """Each thing said. Only Alfred answering keeps him awake (idle.py): an empty
        reply (he stayed silent for side talk or a TV) doesn't."""
        if who == "Alfred" and not text.strip():
            self._waiting_since = None  # he decided not to answer
            return
        self.transcript.append(f"{who}: {text}")
        if who == "You":
            self._waiting_since = time.monotonic()
            self.timer.you_said()
            self.announcer.user_said(text)
            self.sleep.user_said(text)
        else:
            if text.strip() != JOB_STARTED:  # said for him as a job starts: not his answer
                self._waiting_since = None
            self.timer.activity()
            self.sleep.alfred_said(text)

    def _used_tools(self, calls) -> None:
        """Alfred called tools. Starting a job, he says so at once: the job takes a few
        seconds to answer, and GPT-6 Luna says nothing until it does."""
        self.tool_calls += 1
        self.timer.activity()
        starts_job = any(call.function_name == "do_task" for call in calls)
        # With a job waiting for your answer, do_task asks the model to check first
        # (tools.py): "Right away, sir" would be premature.
        if starts_job and not self.services.questions.waiting():
            self.llm.create_task(self._say_aloud(JOB_STARTED), "job started")

    def _looking_up(self) -> bool:
        """Is Alfred probably still working on an answer? (A slow web search sends
        nothing meanwhile.)"""
        waiting = self._waiting_since
        return waiting is not None and time.monotonic() - waiting < LOOKUP_SECONDS


def end_if_out_of_credits(worker) -> asyncio.Event:
    """OpenAI, Deepgram or Cartesia refuses everything once its credits run out: say which
    in the log and end the conversation at once (with the "couldn't connect" beeps),
    instead of sitting there silent. The event is set if so."""
    out = asyncio.Event()

    @worker.event_handler("on_pipeline_error")
    async def on_error(_worker, frame):
        if out.is_set() or not NO_CREDITS.search(str(frame.error)):
            return
        out.set()
        service = whose_error(frame)
        logger.error(f"{service} credits used up: add credits in your {service} account")
        # not awaited here: this handler runs inside the pipeline that's being cancelled
        out.cancelling = asyncio.create_task(worker.cancel(reason=f"no {service} credits"))

    return out


def whose_error(frame) -> str:
    """The service an error came from, by its processor's name (OpenAI if none)."""
    name = type(frame.processor).__name__ if getattr(frame, "processor", None) else ""
    return next((service for service in SERVICES if service in name), "OpenAI")


def quiet_timer(
    worker, engine: AudioEngine, services: Services, on_closing, looking_up
) -> QuietTimer:
    """Closes the conversation when it goes quiet; stays open while a job is busy or
    something rings, so you can still say "stop" (idle.py)."""
    s = services
    return QuietTimer(
        worker,
        engine,
        load_sound(PROJECT_ROOT / "sounds" / "session_warning.wav"),
        s.config.idle_seconds,
        # a job working or waiting for an answer, an answer on its way, or something ringing
        is_busy=lambda: (
            s.tasks.busy
            or bool(s.questions.waiting())
            or looking_up()
            or bool(s.ringer and s.ringer.ringing)
        ),
        on_closing=on_closing,
    )


def background(services: Services) -> str:
    """Added to the instructions: the time, where you are, what Alfred knows about you,
    and recent jobs, so a conversation doesn't start blank."""
    now = f"\n\n## Now\n{services.config.now():%A %d %B %Y, %I:%M %p} (the user's local time)."
    about = services.config.home_text() + services.memory.profile_text()
    if services.web:
        about += services.web.address_text()
    return now + about + recent_jobs_context(services.db)


def alfred_tools(services: Services, house_tools: list) -> ToolsSchema:
    """Alfred's tools: OpenAI's web search, plus his own and the house's (run here)."""
    s = services
    own = job_tools(s.tasks, s.db, s.questions) + memory_tools(s.memory)
    return ToolsSchema(
        standard_tools=own
        + reminder_tools(s.reminders, s.ringer)
        + chat_tools(s.chat)
        + house_tools,
        custom_tools={AdapterType.OPENAI: [WEB_SEARCH]},
    )


def create_stt(config: Config) -> DeepgramFluxSTTService:
    """Deepgram Flux: your words, and when your turn has ended. It also guesses the end a
    moment early, and GPT-6 Luna starts on that guess; the answer is thrown away if you
    carry on speaking."""
    return DeepgramFluxSTTService(
        api_key=config.deepgram_api_key or "",
        settings=DeepgramFluxSTTService.Settings(model=config.stt_model, keyterm=KEYTERMS),
        enable_eager_end_of_turn=True,
    )


def create_turns(context: LLMContext, echo: EchoGuard | None = None) -> LLMContextAggregatorPair:
    """Your side of the conversation and Alfred's. Your turn starts on your first word, or
    on your INTERRUPT_WORDS-th while Alfred is speaking (unless they're mostly his own,
    heard back: echo.py), and ends when Flux says so (see create_stt)."""
    turns = UserTurnStrategies(
        start=[NotHisEcho(echo, min_words=INTERRUPT_WORDS)],
        stop=[EagerUserTurnStopStrategy()],
    )
    return LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(user_turn_strategies=turns)
    )


class NotHisEcho(MinWordsUserTurnStartStrategy):
    """Pipecat's minimum-words start, skipping words heard while Alfred speaks that are
    mostly his own, and a word or two of his own heard just after he stops (echo.py).
    His name or "stop" interrupts him at once, however short: "Hey, Alfred" was ignored
    while he talked on. Without a guard (the benchmark), it's the plain one."""

    def __init__(self, echo: EchoGuard | None, **kwargs):
        super().__init__(**kwargs)
        self._echo = echo
        self._stopped_at = -1e9  # when Alfred last stopped speaking

    async def _handle_bot_stopped_speaking(self, frame) -> None:
        await super()._handle_bot_stopped_speaking(frame)
        self._stopped_at = time.monotonic()

    async def _handle_transcription(self, frame) -> None:
        if self._echo:
            if self._bot_speaking and STOP_HIM.search(frame.text):
                await self.report_user_turn_started()
                return
            if self._bot_speaking and self._echo.is_echo(frame.text):
                await self.trigger_reset_aggregation()  # not a turn: dropped, not kept
                return
            just_stopped = time.monotonic() - self._stopped_at < TAIL_SECONDS
            if not self._bot_speaking and just_stopped and self._echo.is_tail_echo(frame.text):
                logger.debug(f"Heard Alfred's own last words, not yours: {frame.text!r}")
                await self.trigger_reset_aggregation()
                return
        await super()._handle_transcription(frame)


def create_llm(config: Config, extra: str = "") -> OpenAIResponsesLLMService:
    """GPT-6 Luna, Alfred's mind. extra is added to its instructions (see background())."""
    logger.debug(
        f"Opening conversation: speech {config.stt_model}, model {config.llm_model}, "
        f"voice {config.tts_model}"
    )
    return OpenAIResponsesLLMService(
        api_key=config.openai_api_key,
        settings=OpenAIResponsesLLMService.Settings(
            model=config.llm_model,
            system_instruction=load_prompt("voice") + extra,
            reasoning=OpenAIResponsesLLMService.ReasoningConfig(effort=REASONING),
        ),
    )


def create_tts(config: Config) -> CartesiaTTSService:
    """Cartesia: Alfred's voice. It gets the reply word by word as GPT-6 Luna writes it,
    and starts speaking within TTS_BUFFER_MS."""
    return CartesiaTTSService(
        api_key=config.cartesia_api_key or "",
        settings=CartesiaTTSService.Settings(model=config.tts_model, voice=config.tts_voice),
        text_aggregation_mode=TextAggregationMode.TOKEN,  # stream words as they come
        max_buffer_delay_ms=TTS_BUFFER_MS,
    )


def create_worker(pipeline: Pipeline, config: Config) -> PipelineWorker:
    return PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=check_every(config.idle_seconds),  # see idle.py
        # Quiet means "Alfred hasn't spoken or used a tool": background voices don't count.
        idle_timeout_frames=(BotSpeakingFrame, FunctionCallInProgressFrame),
        cancel_on_idle_timeout=False,  # idle.py warns, then closes politely
        # If a service's connection fails, end at once and go back to the wake word
        # instead of sitting silent until the idle timeout.
        processor_unusable_policy=ProcessorUnusablePolicy.END,
        observers=[DebugLogObserver(frame_types=LOGGED_FRAMES), MetricsLogObserver()],
    )


def log_conversation(llm, user_turns, alfred_turns, on_line, on_tool=None) -> None:
    """Show what was said, and each tool Alfred calls. on_line(who, text) is called with
    each thing said ("You" or "Alfred"); on_tool(calls) with each set of tool calls."""

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(_llm, calls):
        logger.info(f"Alfred is using: {', '.join(call.function_name for call in calls)}")
        if on_tool:
            on_tool(calls)

    @user_turns.event_handler("on_user_turn_message_added")
    async def on_user_turn(_aggregator, message):
        logger.info(f"You: {message.content}")
        on_line("You", str(message.content))

    @alfred_turns.event_handler("on_assistant_turn_stopped")
    async def on_alfred_turn(_aggregator, message):
        if message.content:
            logger.info(f"Alfred: {message.content}")
        on_line("Alfred", str(message.content or ""))
