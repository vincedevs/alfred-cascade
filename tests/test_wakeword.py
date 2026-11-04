"""Tests run the real wake-word models in models/."""

import wave

import numpy as np
import pytest

from alfred.config import PROJECT_ROOT
from alfred.wakeword import CHUNK_SAMPLES, SAMPLE_RATE, WakeWordDetector

MODELS = PROJECT_ROOT / "models"
# Optional: record yourself saying "Hey Alfred" to enable the detection test (see README).
SAMPLE_CLIP = PROJECT_ROOT / "tests" / "hey_alfred.wav"


def noise(seconds: float, level: int = 300, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    samples = rng.normal(0, level, int(seconds * SAMPLE_RATE))
    return samples.clip(-32768, 32767).astype(np.int16).tobytes()


@pytest.fixture
def detector():
    return WakeWordDetector(MODELS, threshold=0.5)


def test_scores_stay_zero_until_enough_history(detector):
    detector.process(noise(1.2))  # 15 chunks: one short of the 16 needed
    assert detector.last_score == 0.0


def test_noise_and_silence_do_not_wake(detector):
    assert not detector.process(noise(10))
    assert not detector.process(bytes(SAMPLE_RATE * 2 * 5))  # 5 s of silence
    assert 0.0 <= detector.last_score < 0.5


def test_chunk_size_does_not_change_scores():
    """The audio engine delivers 10 ms blocks; scores must match feeding whole 80 ms chunks."""
    audio = noise(3, level=2000, seed=1)
    big, small = WakeWordDetector(MODELS), WakeWordDetector(MODELS)
    big_scores, small_scores = [], []
    for start in range(0, len(audio), CHUNK_SAMPLES * 2):
        big.process(audio[start : start + CHUNK_SAMPLES * 2])
        big_scores.append(big.last_score)
    for start in range(0, len(audio), 320):  # 10 ms = 160 samples = 320 bytes
        small.process(audio[start : start + 320])
        if (start + 320) % (CHUNK_SAMPLES * 2) == 0:
            small_scores.append(small.last_score)
    assert small_scores == pytest.approx(big_scores[: len(small_scores)])


def test_missing_model_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="hey_alfred.onnx"):
        WakeWordDetector(tmp_path)


@pytest.mark.skipif(not SAMPLE_CLIP.exists(), reason="no tests/hey_alfred.wav recorded")
def test_sample_clip_wakes(detector):
    with wave.open(str(SAMPLE_CLIP)) as clip:
        assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (
            SAMPLE_RATE,
            1,
            2,
        )
        audio = clip.readframes(clip.getnframes())
    padding = bytes(SAMPLE_RATE * 2 * 2)  # 2 s of silence before and after
    assert detector.process(padding + audio + padding)
