"""Background always-listen loop: mic open, wake word -> callback, then pause for the turn."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from voice.wakeword import SAMPLE_RATE, WakeWordDetector

logger = logging.getLogger("steve.voice.always_listen")

OnWake = Callable[[], None]


class AlwaysListenService:
    def __init__(
        self,
        detector: WakeWordDetector,
        on_wake: OnWake,
        input_device: str | int | None = None,
        chunk_seconds: float = 0.08,
    ):
        self.detector = detector
        self.on_wake = on_wake
        self.input_device = input_device
        self.chunk_seconds = chunk_seconds
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        if not self.detector.is_available():
            logger.warning("Wake detector indisponivel; always-listen nao iniciado.")
            return
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._loop, name="steve-always-listen", daemon=True)
        self._thread.start()
        logger.info("Always-listen iniciado (wake word).")

    def stop(self) -> None:
        self._stop.set()
        self._paused.clear()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.detector.reset()

    def pause(self) -> None:
        self._paused.set()
        self.detector.reset()

    def resume(self) -> None:
        self._paused.clear()

    def _loop(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice ausente; always-listen abortado.")
            return

        frames = int(SAMPLE_RATE * self.chunk_seconds)
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=self.input_device,
                blocksize=frames,
            ) as stream:
                while not self._stop.is_set():
                    if self._paused.is_set():
                        time.sleep(0.05)
                        continue
                    chunk, overflowed = stream.read(frames)
                    if overflowed:
                        logger.debug("always-listen overflow")
                    audio = np.asarray(chunk, dtype=np.float32).reshape(-1)
                    try:
                        if self.detector.process_audio(audio, SAMPLE_RATE):
                            logger.info("Wake word detectado.")
                            self.pause()
                            try:
                                self.on_wake()
                            except Exception:
                                logger.exception("on_wake falhou")
                                self.resume()
                    except Exception:
                        logger.exception("Erro no detector de wake")
                        time.sleep(0.1)
        except Exception:
            logger.exception("Always-listen stream falhou")
