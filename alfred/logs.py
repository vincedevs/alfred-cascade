"""Logging: the terminal shows ALFRED_LOG_LEVEL (INFO by default), and every run writes a
full DEBUG log to logs/alfred-<date>-<time>.log. Logs older than KEEP_DAYS are deleted
at startup: they record what was said, and an always-on Alfred would fill the disk."""

import logging
import os
import platform
import sys
import time
from importlib.metadata import version

from loguru import logger

from alfred.config import PROJECT_ROOT, SECRET_SETTINGS, Config

LOG_DIR = PROJECT_ROOT / "logs"
KEEP_DAYS = 14


def setup_logging(config: Config) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    removed = delete_old_logs()
    log_file = LOG_DIR / f"alfred-{config.now():%Y%m%d-%H%M%S}.log"
    logger.remove()
    # diagnose=False: a crash shows where it happened, never the values involved (keys)
    logger.add(sys.stderr, level=config.log_level, diagnose=False)
    logger.add(log_file, level="DEBUG", backtrace=True, diagnose=False)
    logger.info(f"Logging everything to {log_file.relative_to(PROJECT_ROOT)}")
    capture_library_logs("mcp")  # the house connection (house.py): why it drops, if it does
    if removed:
        logger.debug(f"Deleted {removed} log(s) older than {KEEP_DAYS} days")

    packages = ("pipecat-ai", "claude-agent-sdk", "livekit", "sounddevice", "onnxruntime")
    logger.debug(
        f"Python {platform.python_version()} on {platform.platform()}; "
        + ", ".join(f"{name} {version(name)}" for name in packages)
    )
    settings = {k: v for k, v in vars(config).items() if k not in SECRET_SETTINGS}
    settings["google_chat_spaces"] = sorted(config.google_chat_spaces)  # names only
    logger.debug(f"Settings: {settings}")

    # Claude signs in with one of these (never logged): see .env.example.
    if os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("Claude jobs: using ANTHROPIC_API_KEY (billed per use)")
    elif os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        logger.info("Claude jobs: using your Claude plan (CLAUDE_CODE_OAUTH_TOKEN)")
    else:
        logger.warning("No Claude sign-in in .env: background jobs will fail")


def capture_library_logs(name: str) -> None:
    """Send a library's own (standard `logging`) messages to our log as well."""

    class ToLoguru(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            level = record.levelname if record.levelno >= logging.INFO else "DEBUG"
            logger.opt(exception=record.exc_info).log(level, f"[{name}] {record.getMessage()}")

    library = logging.getLogger(name)
    library.handlers = [ToLoguru()]
    library.setLevel(logging.DEBUG)
    library.propagate = False


def delete_old_logs() -> int:
    cutoff = time.time() - KEEP_DAYS * 86400
    old = [log for log in LOG_DIR.glob("alfred-*.log") if log.stat().st_mtime < cutoff]
    for log in old:
        log.unlink()
    return len(old)
