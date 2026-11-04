import pytest

from alfred.config import PROJECT_ROOT, SECRET_SETTINGS, ConfigError, load_config


def test_defaults_need_only_the_api_key():
    config = load_config({"OPENAI_API_KEY": "sk-test"})
    assert config.stt_model == "flux-general-en"
    assert config.llm_model == "gpt-6-luna"
    assert config.tts_model == "sonic-3.6"
    assert config.tts_voice == "ef191366-f52f-447a-a398-ed8c0f2943a1"  # Archie
    assert config.wakeword_dir == PROJECT_ROOT / "models"
    assert config.idle_seconds == 25.0
    assert config.input_device is None
    assert config.echo_cancellation is True
    assert config.agent_model == "claude-haiku-5-5"
    assert config.workspace_dir.name == "workspace"


def test_missing_api_key_explains_the_fix():
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load_config({})


def test_values_are_parsed():
    config = load_config(
        {
            "OPENAI_API_KEY": "sk-test",
            "ALFRED_WAKE_THRESHOLD": "0.7",
            "ALFRED_IDLE_SECONDS": "40",
            "ALFRED_INPUT_DEVICE": "2",
            "ALFRED_OUTPUT_DEVICE": "MacBook Pro Speakers",
            "ALFRED_LOG_LEVEL": "debug",
        }
    )
    assert config.wake_threshold == 0.7
    assert config.idle_seconds == 40.0
    assert config.input_device == 2  # an index
    assert config.output_device == "MacBook Pro Speakers"  # part of a name
    assert config.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["loud", "1.5"])
def test_bad_threshold_is_rejected(value):
    with pytest.raises(ConfigError, match="ALFRED_WAKE_THRESHOLD"):
        load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_WAKE_THRESHOLD": value})


def test_prompt_files_exist():
    for name in ("voice", "agent"):
        assert (PROJECT_ROOT / "prompts" / f"{name}.md").read_text().strip()


@pytest.mark.parametrize(("value", "expected"), [("off", False), ("ON", True), ("0", False)])
def test_echo_cancellation_switch(value, expected):
    config = load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_ECHO_CANCELLATION": value})
    assert config.echo_cancellation is expected


def test_bad_switch_value_is_rejected():
    with pytest.raises(ConfigError, match="ALFRED_ECHO_CANCELLATION"):
        load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_ECHO_CANCELLATION": "maybe"})


def test_data_folder_and_budget(tmp_path):
    config = load_config(
        {"OPENAI_API_KEY": "sk-test", "ALFRED_DATA": str(tmp_path), "ALFRED_TASK_BUDGET_USD": "0.5"}
    )
    assert config.database_path == tmp_path / "alfred.db"
    assert config.workspace_dir == tmp_path / "workspace"
    assert config.task_budget_usd == 0.5


def test_email_settings():
    config = load_config(
        {
            "OPENAI_API_KEY": "sk-test",
            "ALFRED_EMAIL": " me@gmail.com ",
            "ALFRED_EMAIL_APP_PASSWORD": "abcd efgh ijkl mnop",  # as Google shows it
        }
    )
    assert config.email_address == "me@gmail.com"
    assert config.email_app_password == "abcdefghijklmnop"
    assert "email_app_password" in SECRET_SETTINGS  # never written to the log
    assert load_config({"OPENAI_API_KEY": "sk-test"}).email_address is None


def test_quiet_hours():
    from datetime import datetime

    config = load_config({"OPENAI_API_KEY": "sk-test"})
    assert config.quiet_hours == (22, 7)
    night = [config.is_quiet(datetime(2026, 1, 1, hour)) for hour in (21, 22, 3, 6, 7, 12)]
    assert night == [False, True, True, True, False, False]
    assert (
        load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_QUIET_HOURS": "off"}).quiet_hours is None
    )
    with pytest.raises(ConfigError):
        load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_QUIET_HOURS": "late"})


def test_home_settings_reach_the_prompts():
    config = load_config({"OPENAI_API_KEY": "sk-test"})
    text = config.home_text()
    assert "Manila, Philippines" in text and "°C" in text and "PHP" in text
    us = load_config(
        {
            "OPENAI_API_KEY": "sk-test",
            "ALFRED_HOME": "Austin, Texas",
            "ALFRED_UNITS": "Imperial",
            "ALFRED_CURRENCY": "US dollars",
        }
    )
    assert "Austin, Texas" in us.home_text() and "°F" in us.home_text()
    with pytest.raises(ConfigError):
        load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_UNITS": "furlongs"})


def test_time_zone_is_ours_not_the_computers():
    config = load_config({"OPENAI_API_KEY": "sk-test"})
    assert config.timezone == "Asia/Manila"
    assert config.now().utcoffset().total_seconds() == 8 * 3600
    assert config.local(0).hour == 8  # midnight UTC on 1 Jan 1970 was 8 am in Manila
    assert "UTC+08:00" in config.home_text()
    with pytest.raises(ConfigError):
        load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_TIMEZONE": "Mars/Olympus"})


def test_wayne_manor_is_off_unless_set():
    assert load_config({"OPENAI_API_KEY": "sk-test"}).wayne_manor_url is None
    url = "http://127.0.0.1:8765/mcp"
    config = load_config({"OPENAI_API_KEY": "sk-test", "ALFRED_WAYNE_MANOR_URL": f" {url} "})
    assert config.wayne_manor_url == url


def test_the_voice_keys_are_checked_at_startup():
    with pytest.raises(ConfigError, match="DEEPGRAM_API_KEY"):
        load_config({"OPENAI_API_KEY": "sk-test"}).check_voice_keys()
    with pytest.raises(ConfigError, match="CARTESIA_API_KEY"):
        load_config({"OPENAI_API_KEY": "sk-test", "DEEPGRAM_API_KEY": "dg"}).check_voice_keys()
    keys = {"OPENAI_API_KEY": "sk-test", "DEEPGRAM_API_KEY": "dg", "CARTESIA_API_KEY": "ca"}
    load_config(keys).check_voice_keys()


def test_secrets_never_show_when_config_is_printed():
    config = load_config(
        {
            "OPENAI_API_KEY": "sk-secret-123",
            "DEEPGRAM_API_KEY": "dg-secret-456",
            "CARTESIA_API_KEY": "ca-secret-789",
            "ALFRED_EMAIL_APP_PASSWORD": "abcdefghijklmnop",
            "ALFRED_GOOGLE_CHAT_FAMILY": "https://chat.googleapis.com/v1/spaces/X?key=K&token=T",
        }
    )
    shown = repr(config)
    assert "sk-secret-123" not in shown and "abcdefghijklmnop" not in shown and "key=K" not in shown
    assert "dg-secret-456" not in shown and "ca-secret-789" not in shown
