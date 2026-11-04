"""Detects "Hey Alfred" in 16 kHz audio using the three openWakeWord ONNX models.

openWakeWord splits detection into three small models, run in a chain:

  1. melspectrogram.onnx  raw audio      -> mel frames (one per 10 ms)
  2. embedding.onnx       76 mel frames  -> one speech embedding (96 numbers)
  3. hey_alfred.onnx      16 embeddings  -> score from 0 to 1 (about 1.3 s of audio)

Every 80 ms of new audio adds one embedding and gives one new score. This is the
same streaming scheme the openwakeword package uses, so scores match training.
We call onnxruntime directly instead of using that package: it is about 60 lines
and avoids the package's extra dependencies.
"""

from collections import deque
from pathlib import Path

import numpy as np
import onnxruntime as ort

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 1280  # 80 ms: one new score per chunk
MEL_CONTEXT_SAMPLES = 480  # earlier audio prepended so each chunk gives exactly 8 mel frames
MEL_BANDS = 32
MEL_WINDOW = 76  # mel frames per embedding
EMBEDDING_SIZE = 96
EMBEDDING_WINDOW = 16  # embeddings per score

MODEL_FILES = ("melspectrogram.onnx", "embedding.onnx", "hey_alfred.onnx")


class WakeWordDetector:
    def __init__(self, model_dir: Path, threshold: float = 0.5):
        missing = [name for name in MODEL_FILES if not (model_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(f"Wake-word model files missing from {model_dir}: {missing}")
        self.threshold = threshold
        self._mel_model, self._embedding_model, self._wake_model = (
            _load(model_dir / name) for name in MODEL_FILES
        )
        self.reset()

    def reset(self) -> None:
        """Forget all past audio. Scores stay 0 for the first 1.3 s after a reset."""
        self._pending = np.zeros(0, dtype=np.int16)
        self._context = np.zeros(MEL_CONTEXT_SAMPLES, dtype=np.int16)
        self._mel_frames = np.ones((MEL_WINDOW, MEL_BANDS), dtype=np.float32)  # as openWakeWord
        self._embeddings: deque[np.ndarray] = deque(maxlen=EMBEDDING_WINDOW)
        self.last_score = 0.0

    def process(self, pcm: bytes) -> bool:
        """Feed 16-bit mono 16 kHz audio of any length. Returns True when the wake word is heard."""
        self._pending = np.concatenate([self._pending, np.frombuffer(pcm, dtype=np.int16)])
        heard = False
        while len(self._pending) >= CHUNK_SAMPLES:
            chunk = self._pending[:CHUNK_SAMPLES]
            self._pending = self._pending[CHUNK_SAMPLES:]
            self.last_score = self._score(chunk)
            heard = heard or self.last_score >= self.threshold
        if heard:
            score = self.last_score
            self.reset()  # so one "Hey Alfred" cannot trigger twice
            self.last_score = score
        return heard

    def _score(self, chunk: np.ndarray) -> float:
        audio = np.concatenate([self._context, chunk])
        self._context = audio[-MEL_CONTEXT_SAMPLES:]

        # The models expect int16-scale floats; "/ 10 + 2" is openWakeWord's mel scaling.
        mel = _run(self._mel_model, audio[None, :].astype(np.float32)).reshape(-1, MEL_BANDS)
        self._mel_frames = np.vstack([self._mel_frames, mel / 10 + 2])[-MEL_WINDOW:]

        window = self._mel_frames[None, :, :, None]
        self._embeddings.append(_run(self._embedding_model, window).reshape(EMBEDDING_SIZE))
        if len(self._embeddings) < EMBEDDING_WINDOW:
            return 0.0  # not enough history yet

        features = np.stack(self._embeddings)[None, :, :].astype(np.float32)
        return float(_run(self._wake_model, features).squeeze())


def _load(path: Path) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1  # these models are tiny; one thread is enough
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def _run(session: ort.InferenceSession, x: np.ndarray) -> np.ndarray:
    return session.run(None, {session.get_inputs()[0].name: x})[0]
