"""Start Alfred: `uv run alfred`.

The whole program is one loop:
    listen for "Hey Alfred" -> wake sound -> talk (voice.py) -> (idle, or "go to sleep")
    -> sleep sound -> listen again

Background jobs (tasks.py) run alongside that loop; a soft chime plays when one finishes.
Alfred also chimes and speaks up without the wake word when a reminder goes off, or a job
needs your answer (not during quiet hours, except reminders). After each conversation,
memory.py notes anything worth remembering.

Logging is set up in logs.py; everything long-lived is built in services.py.
"""

import asyncio
import gc
import os
import signal
import sys

from loguru import logger

from alfred.audio import SAMPLE_RATE, AudioEngine, load_sound
from alfred.config import PROJECT_ROOT, Config, ConfigError, load_config
from alfred.lock import only_one_alfred
from alfred.logs import setup_logging
from alfred.ringer import Ringer
from alfred.services import Services
from alfred.tasks import TaskRunner
from alfred.voice import run_conversation
from alfred.wakeword import WakeWordDetector

NEAR_MISS_SCORE = 0.2  # wake-word scores above this are logged, to help tune the threshold


async def run(config: Config) -> None:
    detector = WakeWordDetector(config.wakeword_dir, config.wake_threshold)
    wake_sound = load_sound(PROJECT_ROOT / "sounds" / "listening_start.wav")
    done_sound = load_sound(PROJECT_ROOT / "sounds" / "task_done.wav")
    offline_sound = load_sound(PROJECT_ROOT / "sounds" / "connection_error.wav")
    engine = AudioEngine(config.input_device, config.output_device, config.echo_cancellation)
    engine.start()

    def chime(_task) -> None:  # a job finished (silent during quiet hours)
        if not config.is_quiet():
            engine.play(done_sound)

    services = Services.start(config, on_job_finished=chime)
    services.ringer = start_ringer(engine, services)
    handle_shutdown_signals(services.tasks)
    background = {asyncio.create_task(services.reminders.run(), name="reminders")}
    try:
        while True:
            logger.info('Waiting for "Hey Alfred"...')
            reason = await wait_for_wake_or_news(engine, detector, services)
            if reason == "wake word":
                logger.info(f"Wake word heard (score {detector.last_score:.2f})")
                engine.play(wake_sound)  # tells you Alfred is listening
            else:
                logger.info(f"Speaking up: {reason}")
                engine.play(done_sound)
            woken = reason == "wake word"  # "Hey Alfred" alone gets a greeting
            transcript = await run_conversation(engine, services, woken)

            # Pipecat's runner swallows the cancellation Ctrl+C sends, so pass it on.
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            if transcript is None:
                engine.play(offline_sound)  # two low beeps: couldn't reach OpenAI
            else:  # the sleep sound has played (voice.py)
                learning = asyncio.create_task(services.memory.learn(transcript), name="learn")
                background.add(learning)
                learning.add_done_callback(background.discard)
            detector.reset()
            engine.clear_mic()  # don't let the end of the conversation wake us again
    finally:
        for job in background:
            job.cancel()
        await services.tasks.close()  # unfinished jobs stay recorded and resume next start
        if services.web:
            services.web.stop()
        services.db.close()
        engine.close()
        await let_go_of_subprocesses()


async def let_go_of_subprocesses() -> None:
    """Claude's processes (jobs, memory notes) leave asyncio subprocess objects behind.
    Collected after the event loop has closed, each printed "Exception ignored ...
    RuntimeError: Event loop is closed" on Ctrl+C. Collect them now, while it's open."""
    gc.collect()
    await asyncio.sleep(0.1)  # their clean-up runs on the loop


def quiet_closed_loop_noise(unraisable) -> None:
    """The same harmless message from any subprocess object collected later still: hidden.
    Anything else that can't be raised is reported as usual."""
    error = unraisable.exc_value
    if isinstance(error, RuntimeError) and str(error) == "Event loop is closed":
        return
    sys.__unraisablehook__(unraisable)


def start_ringer(engine: AudioEngine, services: Services) -> Ringer:
    """Timers beep and alarms ring when they go off, until you say stop (ringer.py)."""
    sounds = PROJECT_ROOT / "sounds"
    ringer = Ringer(
        engine, load_sound(sounds / "timer_done.wav"), load_sound(sounds / "alarm.wav"), SAMPLE_RATE
    )

    def ring(reminder) -> None:
        if reminder.kind in ("timer", "alarm"):
            ringer.ring(reminder.kind, alarm_id=reminder.id)

    services.reminders.on_due.append(ring)
    return ringer


def handle_shutdown_signals(tasks: TaskRunner) -> None:
    """Ctrl+C (or a stop from systemd later) ends Alfred cleanly. Tell the job runner first:
    the same Ctrl+C also interrupts Claude's process, and that must not count as a failure."""
    main_task = asyncio.current_task()
    stopping = False

    def shut_down() -> None:
        nonlocal stopping
        if stopping:
            os._exit(1)  # a second Ctrl+C quits at once
        stopping = True
        logger.info("Shutting down (press Ctrl+C again to force)...")
        tasks.begin_shutdown()
        main_task.cancel()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shut_down)


async def wait_for_wake_or_news(
    engine: AudioEngine, detector: WakeWordDetector, services: Services
) -> str:
    """Wait until you say the wake word, or there's something to speak up about: a reminder
    going off, or a job with a question or a result for you. During quiet hours only
    reminders speak up; the rest waits for the next conversation."""
    s = services
    while True:
        waits = {
            asyncio.create_task(wait_for_wake_word(engine, detector)): "wake word",
            asyncio.create_task(s.reminders.wake.wait()): "a reminder",
            asyncio.create_task(s.questions.new_question.wait()): "a job",
        }
        try:
            done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiting in waits:
                waiting.cancel()
        if any(waits[task] == "wake word" for task in done):
            return "wake word"
        s.reminders.wake.clear()
        s.questions.new_question.clear()
        if s.reminders.unsaid():
            return "a reminder"
        if s.has_news() and not s.config.is_quiet():
            return "a job"


async def wait_for_wake_word(engine: AudioEngine, detector: WakeWordDetector) -> None:
    last_logged = 0.0
    while not detector.process(await engine.mic.get()):
        score = detector.last_score
        if score >= NEAR_MISS_SCORE and score != last_logged:
            logger.debug(f"Wake word near miss: score {score:.2f}")
            last_logged = score


def main() -> None:
    # `uv run alfred` sets VIRTUAL_ENV to Alfred's own environment. Jobs' commands inherit
    # it, which confuses uv in the workspace; Alfred itself doesn't need it once running.
    os.environ.pop("VIRTUAL_ENV", None)
    try:
        config = load_config()
        config.check_voice_keys()
    except ConfigError as error:
        sys.exit(f"Config error: {error}")

    _lock = only_one_alfred(config.data_dir)  # kept for as long as Alfred runs (lock.py)
    if _lock is None:
        sys.exit("Alfred is already running (stop the other one first).")
    setup_logging(config)
    sys.unraisablehook = quiet_closed_loop_noise
    try:
        asyncio.run(run(config))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Bye.")
    except Exception:
        logger.exception("Alfred crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()
