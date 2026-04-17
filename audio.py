"""Audio output: ring buffer + sounddevice stream to VB-Cable."""
from __future__ import annotations

import logging
import threading
from collections import deque

import numpy as np
import sounddevice as sd

from config import (
    CHANNELS,
    DTYPE,
    RING_BUF_SIZE,
    SAMPLE_RATE,
    VBCABLE_NAME_HINTS,
)

log = logging.getLogger(__name__)


class VBCableNotFound(RuntimeError):
    pass


class AudioOutput:
    def __init__(self, blocksize: int = 1024) -> None:
        self._buffer: deque[float] = deque(maxlen=RING_BUF_SIZE)
        self._lock = threading.Lock()
        self._underruns = 0

        device_index, device_name = self._find_vbcable()
        log.info("Using output device [%d]: %s", device_index, device_name)

        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=blocksize,
            device=device_index,
            callback=self._callback,
        )

    @staticmethod
    def _find_vbcable() -> tuple[int, str]:
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev['max_output_channels'] < 1:
                continue
            name = dev['name'].lower()
            if any(hint in name for hint in VBCABLE_NAME_HINTS):
                return i, dev['name']

        available = '\n'.join(
            f"  [{i}] {d['name']}"
            for i, d in enumerate(devices)
            if d['max_output_channels'] >= 1
        )
        raise VBCableNotFound(
            "VB-Cable output device not found.\n"
            "Install VB-Cable from https://vb-audio.com/Cable/ and reboot.\n"
            f"Available output devices:\n{available}"
        )

    def _callback(self, outdata, frames, time_info, status) -> None:
        if status:
            log.debug("stream status: %s", status)

        needed = frames * CHANNELS
        with self._lock:
            have = len(self._buffer)
            take = min(have, needed)
            if take:
                samples = np.fromiter(
                    (self._buffer.popleft() for _ in range(take)),
                    dtype=np.float32,
                    count=take,
                )
            else:
                samples = np.empty(0, dtype=np.float32)

        if take < needed:
            self._underruns += 1
            pad = np.zeros(needed - take, dtype=np.float32)
            samples = np.concatenate([samples, pad])

        outdata[:] = samples.reshape(frames, CHANNELS)

    def push(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        samples = np.frombuffer(pcm_bytes, dtype=np.float32)
        with self._lock:
            self._buffer.extend(samples.tolist())

    def start(self) -> None:
        self.stream.start()

    def stop(self) -> None:
        self.stream.stop()
        self.stream.close()

    @property
    def underruns(self) -> int:
        return self._underruns
