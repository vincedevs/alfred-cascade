from alfred.audio import SAMPLE_RATE, load_sound
from alfred.config import PROJECT_ROOT


def test_wake_sound_is_in_engine_format():
    audio = load_sound(PROJECT_ROOT / "sounds" / "listening_start.wav")
    seconds = len(audio) / 2 / SAMPLE_RATE
    assert 0.2 < seconds < 1.5  # short enough not to delay the conversation
