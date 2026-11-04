"""Settings for Alfred, read once at startup from environment variables (and .env)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# never logged (Google Chat webhook addresses carry their own key and token)
SECRET_SETTINGS = (
    "openai_api_key",
    "deepgram_api_key",
    "cartesia_api_key",
    "email_app_password",
    "google_chat_spaces",
)
ARCHIE = "ef191366-f52f-447a-a398-ed8c0f2943a1"  # Cartesia's Archie: a calm British man
GOOGLE_CHAT_PREFIX = "ALFRED_GOOGLE_CHAT_"  # ALFRED_GOOGLE_CHAT_FAMILY=<webhook> -> "family"


class ConfigError(Exception):
    """A setting is missing or invalid. The message says how to fix it."""


@dataclass(frozen=True)
class Config:
    openai_api_key: str = field(repr=False)  # secrets never appear when Config is printed
    # The voice pipeline: Deepgram hears, GPT-6 Luna thinks, Cartesia speaks (voice.py).
    deepgram_api_key: str | None = field(default=None, repr=False)
    cartesia_api_key: str | None = field(default=None, repr=False)
    stt_model: str = "flux-general-en"  # Deepgram Flux: also decides when your turn ends
    llm_model: str = "gpt-6-luna"
    tts_model: str = "sonic-3.6"
    tts_voice: str = ARCHIE
    wakeword_dir: Path = PROJECT_ROOT / "models"
    wake_threshold: float = 0.5
    idle_seconds: float = 25.0
    input_device: int | str | None = None  # None means the system default
    output_device: int | str | None = None
    echo_cancellation: bool = True
    log_level: str = "INFO"  # what the terminal shows; the log file always gets everything
    data_dir: Path = Path.home() / ".alfred"  # database and Claude's workspace
    agent_model: str = "claude-haiku-5-5"  # Claude model for background jobs
    task_budget_usd: float = 0.25  # a job stops if it would cost more than this
    email_address: str | None = None  # the Gmail account jobs send from
    email_app_password: str | None = field(default=None, repr=False)  # a Gmail app password
    # Alfred doesn't start talking on his own between these hours (start, end), except for
    # reminders you set. None: no quiet hours.
    quiet_hours: tuple[int, int] | None = (22, 7)
    # Where you are: places, units, money and spelling default to these.
    home: str = "Manila, Philippines"
    units: str = "metric"  # metric | imperial
    currency: str = "Philippine pesos (PHP, ₱)"
    # Every time Alfred works with (reminders, quiet hours, "now") is in this time zone,
    # whatever the computer is set to: a new Raspberry Pi starts on UTC.
    timezone: str = "Asia/Manila"
    # Wayne Manor, the virtual smart home (house.py), e.g. http://127.0.0.1:8765/mcp.
    # None: no house.
    wayne_manor_url: str | None = None
    web_port: int | None = None  # share jobs' web pages on the home network (web.py); None: off
    # Google Chat spaces Alfred can message: name -> incoming webhook address (chat.py).
    google_chat_spaces: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "alfred.db"

    @property
    def workspace_dir(self) -> Path:
        """The only folder Claude may read and write files in."""
        return self.data_dir / "workspace"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def profile_path(self) -> Path:
        """What Alfred knows about you: one fact per line, yours to read and edit."""
        return self.data_dir / "profile.md"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> datetime:
        """The current time where you are."""
        return datetime.now(self.tz)

    def local(self, timestamp: float) -> datetime:
        """A stored Unix time, as your local time."""
        return datetime.fromtimestamp(timestamp, self.tz)

    def home_text(self) -> str:
        """For every prompt (Alfred's, the jobs'): where the user is."""
        offset = self.now().strftime("%z")
        examples = "°C, km, kg" if self.units == "metric" else "°F, miles, pounds"
        return (
            f"\n\n## Where the user is\nThe user lives in {self.home} (local time is "
            f"UTC{offset[:3]}:{offset[3:]}). Unless they say otherwise: assume places, "
            f"news, weather, shops and events near home; give {self.units} units "
            f"({examples}) and prices in {self.currency}; use local dates and times."
        )

    def check_voice_keys(self) -> None:
        """Alfred can't hear or speak without these (checked at startup, not by every
        test that builds a Config)."""
        for key, name in ((self.deepgram_api_key, "DEEPGRAM"), (self.cartesia_api_key, "CARTESIA")):
            if not key:
                raise ConfigError(f"{name}_API_KEY is not set. Add it to .env (see .env.example).")

    def is_quiet(self, now: datetime | None = None) -> bool:
        if not self.quiet_hours:
            return False
        start, end = self.quiet_hours
        hour = (now or self.now()).hour
        return start <= hour or hour < end if start > end else start <= hour < end


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a Config from `env`, or from the real environment plus .env if `env` is None."""
    if env is None:
        # override: .env wins over variables left in your shell (e.g. by `source .env`).
        load_dotenv(PROJECT_ROOT / ".env", override=True)
        env = os.environ

    api_key = env.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in.")

    defaults = Config(openai_api_key=api_key)
    return Config(
        openai_api_key=api_key,
        deepgram_api_key=env.get("DEEPGRAM_API_KEY", "").strip() or None,
        cartesia_api_key=env.get("CARTESIA_API_KEY", "").strip() or None,
        stt_model=env.get("ALFRED_STT_MODEL") or defaults.stt_model,
        llm_model=env.get("ALFRED_LLM_MODEL") or defaults.llm_model,
        tts_model=env.get("ALFRED_TTS_MODEL") or defaults.tts_model,
        tts_voice=env.get("ALFRED_TTS_VOICE") or defaults.tts_voice,
        wakeword_dir=Path(env.get("ALFRED_WAKEWORD_DIR") or defaults.wakeword_dir),
        wake_threshold=_number(env, "ALFRED_WAKE_THRESHOLD", defaults.wake_threshold, 0.0, 1.0),
        idle_seconds=_number(env, "ALFRED_IDLE_SECONDS", defaults.idle_seconds, 5.0, 600.0),
        input_device=_device(env.get("ALFRED_INPUT_DEVICE")),
        output_device=_device(env.get("ALFRED_OUTPUT_DEVICE")),
        echo_cancellation=_on_off(env, "ALFRED_ECHO_CANCELLATION", defaults.echo_cancellation),
        log_level=(env.get("ALFRED_LOG_LEVEL") or defaults.log_level).upper(),
        data_dir=Path(env.get("ALFRED_DATA") or defaults.data_dir).expanduser(),
        agent_model=env.get("ALFRED_AGENT_MODEL") or defaults.agent_model,
        task_budget_usd=_number(
            env, "ALFRED_TASK_BUDGET_USD", defaults.task_budget_usd, 0.01, 10.0
        ),
        email_address=(env.get("ALFRED_EMAIL") or "").strip() or None,
        email_app_password=(env.get("ALFRED_EMAIL_APP_PASSWORD") or "").replace(" ", "") or None,
        quiet_hours=_hours(env.get("ALFRED_QUIET_HOURS"), defaults.quiet_hours),
        home=env.get("ALFRED_HOME") or defaults.home,
        units=_choice(env, "ALFRED_UNITS", defaults.units, ("metric", "imperial")),
        currency=env.get("ALFRED_CURRENCY") or defaults.currency,
        timezone=_timezone(env.get("ALFRED_TIMEZONE") or defaults.timezone),
        wayne_manor_url=(env.get("ALFRED_WAYNE_MANOR_URL") or "").strip() or None,
        web_port=_port(env.get("ALFRED_WEB_PORT")),
        google_chat_spaces=_chat_spaces(env),
    )


def load_prompt(name: str) -> str:
    """Prompts live as plain text in prompts/<name>.md."""
    return (PROJECT_ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8").strip()


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = env.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}.")
    return value


def _on_off(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("on", "true", "yes", "1"):
        return True
    if raw in ("off", "false", "no", "0"):
        return False
    raise ConfigError(f"{name} must be on or off, got {raw!r}.")


def _chat_spaces(env: Mapping[str, str]) -> dict[str, str]:
    """Every ALFRED_GOOGLE_CHAT_<NAME>=<webhook>: the space "<name>" (underscores as spaces)."""
    spaces = {}
    for key, value in env.items():
        if key.startswith(GOOGLE_CHAT_PREFIX) and value.strip():
            name = key.removeprefix(GOOGLE_CHAT_PREFIX).lower().replace("_", " ")
            if not value.strip().startswith("https://chat.googleapis.com/"):
                raise ConfigError(
                    f"{key} must be a Google Chat webhook (https://chat.googleapis.com/...)."
                )
            spaces[name] = value.strip()
    return spaces


def _timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        message = f"ALFRED_TIMEZONE must be a time zone like Asia/Manila, got {name!r}."
        raise ConfigError(message) from None
    return name


def _choice(env: Mapping[str, str], name: str, default: str, allowed: tuple[str, ...]) -> str:
    raw = (env.get(name) or "").strip().lower() or default
    if raw not in allowed:
        raise ConfigError(f"{name} must be one of {', '.join(allowed)}, got {raw!r}.")
    return raw


def _hours(raw: str | None, default: tuple[int, int] | None) -> tuple[int, int] | None:
    """ "22-7" means 10 pm to 7 am; "off" means no quiet hours."""
    raw = (raw or "").strip().lower()
    if not raw:
        return default
    if raw in ("off", "none", "no"):
        return None
    try:
        start, end = (int(part) for part in raw.split("-"))
    except ValueError:
        raise ConfigError(f"ALFRED_QUIET_HOURS must look like 22-7 or off, got {raw!r}.") from None
    if not (0 <= start <= 23 and 0 <= end <= 23):
        raise ConfigError(f"ALFRED_QUIET_HOURS hours must be 0 to 23, got {raw!r}.")
    return start, end


def _port(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not 1024 <= int(raw) <= 65535:
        raise ConfigError(f"ALFRED_WEB_PORT must be a port number from 1024 to 65535, got {raw!r}.")
    return int(raw)


def _device(raw: str | None) -> int | str | None:
    """sounddevice accepts a device index or part of a device name."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    return int(raw) if raw.isdigit() else raw
