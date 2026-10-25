"""Energy-based voice activity segmenter: cuts a PCM16 stream into utterances at pauses."""

from __future__ import annotations

from collections import deque

import numpy as np

SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


class Segmenter:
    """Feeds 16 kHz mono PCM16 bytes in, yields complete utterances (PCM16 bytes) out.

    Speech starts after `start_frames` voiced frames, and ends after `silence_ms` of quiet or at
    `max_utterance_s`. The threshold adapts to the room: noise floor + `margin_db`, never below `min_db`.
    """

    # Configures thresholds and empties the buffers. — pradeep.singh@rodicconsultants.com
    def __init__(
        self,
        silence_ms: int = 600,
        max_utterance_s: float = 15.0,
        min_utterance_ms: int = 400,
        preroll_ms: int = 300,
        start_frames: int = 3,
        margin_db: float = 10.0,
        min_db: float = -50.0,
    ):
        self._silence_frames = max(1, silence_ms // FRAME_MS)
        self._max_frames = int(max_utterance_s * 1000 // FRAME_MS)
        self._min_frames = min_utterance_ms // FRAME_MS
        self._start_frames = start_frames
        self._margin_db = margin_db
        self._min_db = min_db
        self._noise_db = -60.0
        self._pending = bytearray()
        self._preroll: deque[bytes] = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self._speech: list[bytes] = []
        self._in_speech = False
        self._voiced_run = 0
        self._quiet_run = 0
        self._voiced_total = 0

    # Adds audio and returns any utterances completed by it. — pradeep.singh@rodicconsultants.com
    def push(self, pcm16: bytes) -> list[bytes]:
        self._pending.extend(pcm16)
        done: list[bytes] = []
        while len(self._pending) >= FRAME_BYTES:
            frame = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            utterance = self._step(frame)
            if utterance:
                done.append(utterance)
        return done

    # Ends any utterance in progress and returns it (used by "answer now"). — pradeep.singh@rodicconsultants.com
    def flush(self) -> bytes | None:
        return self._finish() if self._in_speech else None

    # Returns True while an utterance is in progress. — pradeep.singh@rodicconsultants.com
    @property
    def in_speech(self) -> bool:
        return self._in_speech

    # Processes one 20 ms frame; returns a finished utterance or None. — pradeep.singh@rodicconsultants.com
    def _step(self, frame: bytes) -> bytes | None:
        db = frame_dbfs(frame)
        voiced = db > max(self._min_db, self._noise_db + self._margin_db)
        if not voiced:
            # Track the noise floor slowly upwards, quickly downwards.
            rate = 0.05 if db > self._noise_db else 0.3
            self._noise_db += (db - self._noise_db) * rate

        if not self._in_speech:
            self._preroll.append(frame)
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            if self._voiced_run >= self._start_frames:
                self._in_speech = True
                self._speech = list(self._preroll)
                self._preroll.clear()
                self._quiet_run = 0
                self._voiced_total = self._voiced_run
            return None

        self._speech.append(frame)
        if voiced:
            self._quiet_run = 0
            self._voiced_total += 1
        else:
            self._quiet_run += 1
        if self._quiet_run >= self._silence_frames or len(self._speech) >= self._max_frames:
            return self._finish()
        return None

    # Closes the current utterance; drops it if too short to be speech. — pradeep.singh@rodicconsultants.com
    def _finish(self) -> bytes | None:
        speech, voiced = self._speech, self._voiced_total
        self._speech, self._in_speech, self._voiced_run, self._quiet_run, self._voiced_total = [], False, 0, 0, 0
        if voiced < self._min_frames:
            return None
        return b"".join(speech)


# Returns the RMS level of a PCM16 frame in dBFS. — pradeep.singh@rodicconsultants.com
def frame_dbfs(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
    return 20.0 * np.log10(max(rms, 1e-6))
