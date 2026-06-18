"""Sistem sesi (WASAPI loopback) yakalama + FFT band analizi.

Mimari (düşük gecikme, kendini iyileştiren):
  - Yakalama thread'i SADECE örnekleri kayan tampona yazar (çok hafif). Render
    onu bir süre aç bırakırsa WASAPI'de birikmiş blokları tight loop'ta hızla
    çekip ATLAYARAK her zaman EN GÜNCEL örneklerde kalır — backlog kalıcı olmaz.
  - FFT/band hesabı poll() ile render thread'inde, güncel tampondan yapılır.
    Böylece eski sesi sırayla işleyip lag biriktirmek imkânsız.
İniş (decay) duvar-saati dt'sine göre, çağrı hızından bağımsız.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np
import soundcard as sc

log = logging.getLogger("trcc.audio")

SR = 48000
FFT = 1024           # kayan pencere — kısa = düşük gecikme (bin ~47 Hz)
BLOCK = 256          # yakalama bloğu (~5.3 ms)


class AudioSpectrum:
    def __init__(self, bands: int = 48, fmin: float = 50.0, fmax: float = 18000.0) -> None:
        self.n = bands
        self.bands = np.zeros(bands, dtype=np.float32)
        self._buf = np.zeros(FFT, dtype=np.float32)
        self._lock = threading.Lock()
        self._win = np.hanning(FFT).astype(np.float32)
        self._winsum = float(self._win.sum())
        self._db_floor = -72.0
        self._db_ceil = -18.0
        self._gate_rms = 8e-4
        self._half_life = 0.10            # saniye — vuruş ~100ms'de yarıya iner
        self._last_poll = time.perf_counter()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        freqs = np.fft.rfftfreq(FFT, 1.0 / SR)
        edges = np.logspace(np.log10(fmin), np.log10(fmax), bands + 1)
        self._bin_idx = np.clip(np.searchsorted(freqs, edges), 1, len(freqs) - 1)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    # ── yakalama thread'i: sadece tamponu güncel tut ──────────────────

    def _run(self) -> None:
        spk = sc.default_speaker()
        mic = sc.get_microphone(spk.id, include_loopback=True)
        log.info("Loopback yakalama: %s", mic.name)
        with mic.recorder(samplerate=SR, blocksize=BLOCK) as rec:
            while not self._stop.is_set():
                data = rec.record(numframes=BLOCK)
                if data.ndim > 1:
                    data = data.mean(axis=1)
                n = len(data)
                with self._lock:
                    if n >= FFT:
                        self._buf[:] = data[-FFT:]
                    else:
                        self._buf = np.roll(self._buf, -n)
                        self._buf[-n:] = data

    # ── render thread'i: güncel tampondan FFT/band hesapla ────────────

    def poll(self) -> np.ndarray:
        now = time.perf_counter()
        dt = now - self._last_poll
        self._last_poll = now
        decay = float(0.5 ** (dt / self._half_life)) if dt > 0 else 1.0

        with self._lock:
            buf = self._buf.copy()

        rms = float(np.sqrt(np.mean(buf * buf)))
        if rms < self._gate_rms:
            self.bands *= decay
            np.clip(self.bands, 0.0, 1.0, out=self.bands)
            return self.bands

        spec = np.abs(np.fft.rfft(buf * self._win)) * (2.0 / self._winsum)
        out = np.empty(self.n, dtype=np.float32)
        for i in range(self.n):
            a, b = self._bin_idx[i], self._bin_idx[i + 1]
            if b <= a:
                b = a + 1
            out[i] = spec[a:b].mean()
        db = 20.0 * np.log10(out + 1e-9)
        out = (db - self._db_floor) / (self._db_ceil - self._db_floor)
        np.clip(out, 0.0, 1.0, out=out)

        rise = out > self.bands
        decayed = self.bands * decay
        self.bands = np.where(rise, out, np.maximum(decayed, out)).astype(np.float32)
        np.clip(self.bands, 0.0, 1.0, out=self.bands)
        return self.bands
