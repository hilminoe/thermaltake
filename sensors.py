"""Sensör okuma — sıcaklık/yük/RPM kaynakları, kademeli fallback ile.

Öncelik sırası:
  1. LibreHardwareMonitor HTTP JSON sunucusu (http://127.0.0.1:8085/data.json).
     LHM admin olarak çalışıp "Remote Web Server" açıkken CPU/GPU sıcaklık+yük ve
     fan RPM buradan gelir. Bu sürüm WMI sağlayıcısını yayımlamadığı için JSON
     kullanılır (sadece stdlib; wmi/pywin32 gerekmez).
  2. nvidia-smi — NVIDIA GPU temp/yük (LHM yoksa).
  3. psutil — CPU yük, RAM. (Temp Windows'ta psutil ile gelmez.)

Hiçbir kaynak yoksa ilgili alan None döner; render bunu "--" gösterir.

Çevre değişkenleri (opsiyonel):
  LHM_URL       JSON adresi (varsayılan http://127.0.0.1:8085/data.json)
  LHM_CPU_FAN   CPU fan sensör adı; verilmezse ilk sıfır-olmayan anakart fanı
                (örn. "Fan #5" pompa için).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass

import psutil

log = logging.getLogger("trcc.sensors")

# pythonw altında alt süreç (nvidia-smi) konsol penceresi açmasın diye.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

LHM_URL = os.environ.get("LHM_URL", "http://127.0.0.1:8085/data.json")
LHM_CPU_FAN = os.environ.get("LHM_CPU_FAN")  # None ise otomatik seç


@dataclass
class Reading:
    cpu_temp: float | None = None
    cpu_load: float | None = None
    gpu_temp: float | None = None
    gpu_load: float | None = None
    ram_load: float | None = None
    fan_rpm: int | None = None
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None


def _num(text: str) -> float | None:
    """'57,8 °C' / '1512 RPM' / '21,6 %' -> 57.8 / 1512 / 21.6 (virgül/nokta)."""
    if not text:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


# LHM donanım adları için anahtar kelimeler (donanım düğümünü sınıflar).
_CPU_HW = ("ryzen", "intel", "core i", "cpu")
_GPU_HW = ("geforce", "radeon", "nvidia", "rtx", "gtx", "arc ", "gpu")
# CPU paket sıcaklığı için ad öncelik sırası.
_CPU_TEMP_NAMES = ("core (tctl/tdie)", "cpu package", "core average",
                   "tctl", "tdie", "cpu")
_GPU_TEMP_NAMES = ("gpu core", "gpu temperature", "core")
# Donanım düğümünü, çocuklarında bu kategorilerden biri varsa tanırız.
_CATEGORIES = {"temperatures", "load", "fans", "clocks", "voltages",
               "powers", "controls", "currents", "factors", "data", "level"}


class _LHMHttp:
    """LibreHardwareMonitor JSON sunucusundan okur (opsiyonel)."""

    def __init__(self) -> None:
        self.ok_once = False

    def _fetch(self):
        try:
            with urllib.request.urlopen(LHM_URL, timeout=1.0) as resp:
                return json.load(resp)
        except Exception as e:  # bağlı değil / kapalı
            if self.ok_once:
                log.debug("LHM JSON okunamadı: %s", e)
            return None

    def read(self, r: Reading) -> None:
        root = self._fetch()
        if root is None:
            return
        self.ok_once = True

        mb_fans: list[tuple[str, float]] = []  # (ad, rpm) anakart fanları

        def walk(node, hw: str, cat: str) -> None:
            text = (node.get("Text") or "")
            low = text.lower()
            children = node.get("Children") or []
            value = node.get("Value")

            # Bir düğüm, çocuklarından biri kategori adıysa "donanım" düğümüdür.
            child_low = {(c.get("Text") or "").lower() for c in children}
            if _CATEGORIES & child_low:
                hw = low
            if low in _CATEGORIES:
                cat = low

            if value and not children:  # yaprak sensör (çocuğu yok)
                val = _num(value)
                if val is None:
                    return
                parts = value.split()
                unit = parts[-1] if parts else ""
                is_cpu = any(k in hw for k in _CPU_HW)
                is_gpu = any(k in hw for k in _GPU_HW)
                if "°C" in value or "°F" in value:
                    if is_gpu and r.gpu_temp is None and \
                            any(n in low for n in _GPU_TEMP_NAMES):
                        r.gpu_temp = val
                    elif is_cpu and r.cpu_temp is None and \
                            any(n in low for n in _CPU_TEMP_NAMES):
                        r.cpu_temp = val
                elif unit == "%" and cat == "load":
                    if is_cpu and low == "cpu total" and r.cpu_load is None:
                        r.cpu_load = val
                    elif is_gpu and "gpu core" in low and r.gpu_load is None:
                        r.gpu_load = val
                elif "RPM" in value and not is_gpu:
                    mb_fans.append((text, val))  # GPU fanlarını atla
                elif unit == "MB" and is_gpu and cat == "data":
                    if "memory used" in low and r.vram_used_mb is None:
                        r.vram_used_mb = val
                    elif "memory total" in low and r.vram_total_mb is None:
                        r.vram_total_mb = val
                return

            for c in children:
                walk(c, hw, cat)

        walk(root, "", "")

        if r.fan_rpm is None and mb_fans:
            if LHM_CPU_FAN:
                for name, rpm in mb_fans:
                    if name == LHM_CPU_FAN:
                        r.fan_rpm = int(rpm)
                        break
            if r.fan_rpm is None:
                # ilk sıfır-olmayan fan (genelde CPU fan header'ı)
                for _, rpm in mb_fans:
                    if rpm > 0:
                        r.fan_rpm = int(rpm)
                        break


class _Nvidia:
    def __init__(self) -> None:
        self.exe = shutil.which("nvidia-smi")

    def read(self, r: Reading) -> None:
        have_all = (r.gpu_temp is not None and r.gpu_load is not None
                    and r.vram_used_mb is not None and r.vram_total_mb is not None)
        if not self.exe or have_all:
            return
        try:
            out = subprocess.run(
                [self.exe,
                 "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2,
                creationflags=_NO_WINDOW,
            ).stdout.strip().splitlines()
            if out:
                t, u, mu, mt = (x.strip() for x in out[0].split(","))
                if r.gpu_temp is None:
                    r.gpu_temp = float(t)
                if r.gpu_load is None:
                    r.gpu_load = float(u)
                if r.vram_used_mb is None:
                    r.vram_used_mb = float(mu)
                if r.vram_total_mb is None:
                    r.vram_total_mb = float(mt)
        except Exception as e:
            log.debug("nvidia-smi hatası: %s", e)


class Sensors:
    def __init__(self) -> None:
        self._lhm = _LHMHttp()
        self._nv = _Nvidia()
        psutil.cpu_percent(interval=None)  # ilk çağrıyı tetikle

    def read(self) -> Reading:
        r = Reading()
        self._lhm.read(r)
        self._nv.read(r)
        if r.cpu_load is None:
            r.cpu_load = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        if r.ram_load is None:
            r.ram_load = vm.percent
        r.ram_used_gb = (vm.total - vm.available) / 1024 ** 3
        r.ram_total_gb = vm.total / 1024 ** 3
        return r
