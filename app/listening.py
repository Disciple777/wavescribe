"""Hands-free listening for WaveScribe sessions.

While a listening session runs, the microphone stays open and a small
voice-activity detector (VAD) splits the stream into *utterances*: a
segment starts when speech is detected and ends after a pause. Silence
between utterances is never kept, so only speech is transcribed — and
because each utterance is transcribed on its own as soon as it ends,
sessions can run for hours without hitting the API's file-size limit.

The segmenter is pure NumPy (no audio device) so it is unit-testable;
``ListeningStream`` wires it to ``sounddevice``.
"""

import queue
import re
import threading
from collections import deque
from typing import Callable, List, Optional

import numpy as np
import sounddevice as sd

from app.audio_capture import AudioCaptureError, compute_rms_level

# dB above the tracked noise floor a frame must reach to count as speech.
SENSITIVITY_MARGINS_DB = {"high": 6.0, "medium": 9.0, "low": 13.0}

# Frames quieter than this are never speech, whatever the noise floor says.
ABSOLUTE_MIN_DB = -55.0


def _frame_db(frame: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(frame)))) if frame.size else 0.0
    return 20.0 * np.log10(rms) if rms > 1e-10 else -100.0


class SpeechSegmenter:
    """Turn a continuous mono float32 stream into speech-only segments.

    Feed arbitrary-sized chunks with :meth:`feed`; it returns the list of
    utterances (NumPy arrays) that finished during that chunk.
    """

    def __init__(
        self,
        samplerate: int = 16000,
        frame_ms: int = 30,
        pause_s: float = 1.2,
        sensitivity: str = "medium",
        min_speech_s: float = 0.35,
        max_segment_s: float = 90.0,
        preroll_s: float = 0.3,
        tail_s: float = 0.25,
    ) -> None:
        self.samplerate = samplerate
        self.frame_len = int(samplerate * frame_ms / 1000)
        self._frame_s = self.frame_len / samplerate
        self.pause_s = pause_s
        self.margin_db = SENSITIVITY_MARGINS_DB.get(sensitivity, SENSITIVITY_MARGINS_DB["medium"])
        self.min_speech_frames = max(1, int(min_speech_s / self._frame_s))
        self.max_segment_frames = int(max_segment_s / self._frame_s)
        self.tail_frames = int(tail_s / self._frame_s)
        self._preroll: deque = deque(maxlen=max(1, int(preroll_s / self._frame_s)))
        self._leftover = np.zeros(0, dtype=np.float32)
        # Rolling frame levels (~6 s). Their 10th percentile is the noise
        # floor: natural speech always has quiet gaps, steady noise doesn't,
        # so the floor follows the room even while someone is talking.
        self._levels: deque = deque(maxlen=int(6.0 / self._frame_s))
        self._warmup_frames = int(0.45 / self._frame_s)
        self.reset()

    # ── Configuration ──

    def configure(self, pause_s: Optional[float] = None, sensitivity: Optional[str] = None) -> None:
        if pause_s is not None:
            self.pause_s = float(pause_s)
        if sensitivity is not None:
            self.margin_db = SENSITIVITY_MARGINS_DB.get(sensitivity, self.margin_db)

    # ── State ──

    def reset(self) -> None:
        """Drop any partial utterance (keeps the learned noise floor)."""
        self._segment: List[np.ndarray] = []
        self._in_speech = False
        self._voiced_run = 0
        self._silence_run = 0
        self._speech_frames = 0
        self._preroll.clear()
        self._leftover = np.zeros(0, dtype=np.float32)
        if not hasattr(self, "_noise_db"):
            self._noise_db: Optional[float] = None
        self._frames_seen = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def noise_floor_db(self) -> Optional[float]:
        return self._noise_db

    # ── Processing ──

    def feed(self, chunk: np.ndarray) -> List[np.ndarray]:
        data = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if self._leftover.size:
            data = np.concatenate([self._leftover, data])
        n_frames = data.size // self.frame_len
        self._leftover = data[n_frames * self.frame_len:].copy()

        finished: List[np.ndarray] = []
        for i in range(n_frames):
            frame = data[i * self.frame_len:(i + 1) * self.frame_len]
            segment = self._process_frame(frame)
            if segment is not None:
                finished.append(segment)
        return finished

    def flush(self) -> Optional[np.ndarray]:
        """End the stream: return the in-progress utterance if it has speech."""
        segment = None
        if self._in_speech and self._speech_frames >= self.min_speech_frames:
            segment = self._close_segment()
        self.reset()
        return segment

    def _threshold_db(self) -> float:
        floor = self._noise_db if self._noise_db is not None else -60.0
        return max(floor + self.margin_db, ABSOLUTE_MIN_DB)

    def _track_noise(self, db: float) -> None:
        self._levels.append(db)
        floor = float(np.percentile(self._levels, 10))
        self._noise_db = min(max(floor, -90.0), -20.0)

    def _process_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        db = _frame_db(frame)
        self._track_noise(db)
        self._frames_seen += 1
        # Learn the room for a moment before anything counts as speech.
        voiced = db > self._threshold_db() and self._frames_seen > self._warmup_frames

        if not self._in_speech:
            self._preroll.append(frame)
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            if self._voiced_run >= 3:  # ~90 ms of speech starts an utterance
                self._in_speech = True
                self._segment = list(self._preroll)
                self._preroll.clear()
                self._speech_frames = self._voiced_run
                self._silence_run = 0
            return None

        self._segment.append(frame)
        if voiced:
            self._speech_frames += 1
            self._silence_run = 0
        else:
            self._silence_run += 1

        pause_frames = max(1, int(self.pause_s / self._frame_s))
        too_long = len(self._segment) >= self.max_segment_frames
        # Long monologue: cut at the first short breath instead of waiting
        # for a full pause, and hard-cut if there's no breath at all.
        breath = self._silence_run >= max(1, int(0.3 / self._frame_s))
        hard_cap = len(self._segment) >= int(self.max_segment_frames * 1.3)

        if self._silence_run >= pause_frames or (too_long and breath) or hard_cap:
            enough = self._speech_frames >= self.min_speech_frames
            segment = self._close_segment() if enough else None
            self._in_speech = False
            self._segment = []
            self._voiced_run = 0
            self._silence_run = 0
            self._speech_frames = 0
            return segment
        return None

    def _close_segment(self) -> np.ndarray:
        frames = self._segment
        # Trim the trailing pause down to a short natural tail.
        trailing = self._silence_run
        if trailing > self.tail_frames:
            frames = frames[: len(frames) - (trailing - self.tail_frames)]
        return np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)


# ── Whisper hallucination guard ──
#
# On near-silent or very short clips Whisper famously "hears" stock phrases
# from its training data. Short segments consisting only of one of these are
# dropped instead of being added to a session.

_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thank you very much", "thanks for watching",
    "thank you for watching", "thanks for watching!", "please subscribe",
    "bye", "bye bye", "okay", "ok", "so", "uh", "um", "hmm", "oh",
    "subtitles by the amara org community", "transcribed by", "music",
    "gracias", "gracias por ver el video", "subtitulos realizados por la comunidad de amara org",
}


def is_probable_hallucination(text: str, duration_s: float) -> bool:
    """True when ``text`` looks like a Whisper artefact rather than speech."""
    norm = re.sub(r"[^\w\s]", "", (text or "").lower(), flags=re.UNICODE)
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return True
    return duration_s < 4.0 and norm in _HALLUCINATIONS


# ── Live microphone stream ──


class ListeningStream:
    """Keeps the mic open and reports finished utterances via a callback.

    ``on_segment(audio: np.ndarray)`` runs on a worker thread, never in
    the realtime audio callback.
    """

    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        samplerate: int = 16000,
        pause_s: float = 1.2,
        sensitivity: str = "medium",
    ) -> None:
        self.samplerate = samplerate
        self._on_segment = on_segment
        self._segmenter = SpeechSegmenter(samplerate, pause_s=pause_s, sensitivity=sensitivity)
        self._chunks: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()
        self._stream: Optional[sd.InputStream] = None
        self._worker: Optional[threading.Thread] = None
        self._device = None
        self._suspended = False
        self._level = 0.0
        self._lock = threading.Lock()

    def set_input_device(self, device_id) -> None:
        self._device = None if device_id in ("default", None) else int(device_id)

    def configure(self, pause_s: Optional[float] = None, sensitivity: Optional[str] = None) -> None:
        with self._lock:
            self._segmenter.configure(pause_s, sensitivity)

    @property
    def running(self) -> bool:
        return self._stream is not None

    @property
    def speaking(self) -> bool:
        return self.running and not self._suspended and self._segmenter.in_speech

    def get_input_level(self) -> float:
        with self._lock:
            return self._level

    def start(self) -> None:
        if self._stream is not None:
            return
        kwargs = dict(
            samplerate=self.samplerate, channels=1, dtype=np.float32,
            blocksize=int(self.samplerate * 0.03), callback=self._callback,
        )
        if self._device is not None:
            kwargs["device"] = self._device
        try:
            self._stream = sd.InputStream(**kwargs)
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioCaptureError(f"Could not open the microphone: {exc}")
        self._worker = threading.Thread(target=self._process_loop, daemon=True, name="listening-vad")
        self._worker.start()

    def stop(self, flush: bool = True) -> None:
        """Close the mic. With ``flush`` the utterance in progress is kept."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if self._worker is not None:
            self._chunks.put(None if flush else "drop")  # type: ignore[arg-type]
            self._worker.join(timeout=2.0)
            self._worker = None
        with self._lock:
            self._level = 0.0

    def set_suspended(self, suspended: bool) -> None:
        """Ignore the mic (e.g. while push-to-talk is recording the same speech)."""
        self._suspended = suspended
        if suspended:
            self._chunks.put("reset")  # type: ignore[arg-type]

    def _callback(self, indata, frames, time_info, status) -> None:
        level = compute_rms_level(indata)
        with self._lock:
            self._level = self._level * 0.6 + level * 0.4
        if not self._suspended:
            self._chunks.put(indata[:, 0].copy())

    def _process_loop(self) -> None:
        while True:
            item = self._chunks.get()
            if item is None or isinstance(item, str):
                with self._lock:
                    if item is None:
                        tail = self._segmenter.flush()
                    else:
                        tail = None
                        self._segmenter.reset()
                if tail is not None and tail.size:
                    self._emit(tail)
                if item == "reset":
                    continue
                return
            with self._lock:
                segments = self._segmenter.feed(item)
            for seg in segments:
                self._emit(seg)

    def _emit(self, segment: np.ndarray) -> None:
        try:
            self._on_segment(segment)
        except Exception as exc:  # never kill the VAD thread
            print(f"Listening: segment handler failed ({exc})")
