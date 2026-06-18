"""Ağ throughput örnekleyici: psutil sayaçlarından anlık bayt/sn hesaplar.

psutil.net_io_counters() kümülatif bayt verir; iki örnek arası farkı geçen
süreye bölerek anlık hız (bayt/sn) elde edilir. Render döngüsünde ~60 Hz
çağrılabilir — sayaç okuma ucuzdur. Windows sayaçları her 16 ms'de
güncellenmeyebileceğinden ham fark basamaklı gelebilir; değerler hafif EMA
ile yumuşatılır.
"""
from __future__ import annotations

import time

import psutil


class NetFlow:
    def __init__(self, smooth: float = 0.35) -> None:
        c = psutil.net_io_counters()
        self._last_recv = c.bytes_recv
        self._last_sent = c.bytes_sent
        self._last_t = time.perf_counter()
        self._down = 0.0   # bayt/sn (yumuşatılmış)
        self._up = 0.0
        self._k = smooth

    def poll(self) -> tuple[float, float]:
        now = time.perf_counter()
        dt = now - self._last_t
        if dt <= 0:
            return self._down, self._up
        c = psutil.net_io_counters()
        d = max(0.0, (c.bytes_recv - self._last_recv) / dt)
        u = max(0.0, (c.bytes_sent - self._last_sent) / dt)
        self._down += (d - self._down) * self._k
        self._up += (u - self._up) * self._k
        self._last_t = now
        self._last_recv = c.bytes_recv
        self._last_sent = c.bytes_sent
        return self._down, self._up
