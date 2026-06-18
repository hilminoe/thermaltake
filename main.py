"""Thermalright LCD canlı monitör servisi.

Sensörleri okur, 480x480 yuvarlak ekrana akıcı (~30 fps) bir gösterge çizip
USB üzerinden gönderir. Sensörler ~saniyede bir güncellense de değerler her
frame'de hedefe doğru yumuşatıldığı için göstergeler akıcı görünür.

Kullanım:
    python main.py            # canlı, ekrana gönderir
    python main.py --preview  # ekran yokken pencere/PNG önizleme
    python main.py --fps 30   # hedef frame hızı
"""
from __future__ import annotations

import argparse
import colorsys
import io
import logging
import math
import os
import time

from PIL import Image, ImageDraw, ImageFont

from sensors import Reading, Sensors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("trcc.main")

SIZE = 480
CENTER = SIZE // 2

# Renkler (RGB) — koyu zemin, mavi/turkuaz vurgular.
BG = (10, 12, 16)
RING_BG = (38, 42, 50)
BLUE = (55, 138, 221)
TEAL = (29, 158, 117)
CORAL = (216, 90, 48)
AMBER = (239, 159, 39)
TEXT = (235, 238, 242)
MUTED = (140, 146, 156)


def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_BIG = _font(120)
F_MID = _font(46)
F_SMALL = _font(28)
F_TINY = _font(22)
F_MICRO = _font(16)


def _smooth(cur: float, target: float, rate: float) -> float:
    return cur + (target - cur) * rate


def _arc_color(temp: float) -> tuple[int, int, int]:
    if temp >= 80:
        return CORAL
    if temp >= 65:
        return AMBER
    return TEAL


def _temp_color(temp: float | None, sat: float = 0.62, val: float = 1.0):
    """Sıcaklığı renge çevirir: ~38°C serin mavi -> 85°C kızıl
    (mavi→camgöbeği→yeşil→kehribar→kırmızı). 'Isı-tepkili' tema bunu kullanır."""
    t = 40.0 if temp is None else temp
    frac = max(0.0, min(1.0, (t - 38.0) / (85.0 - 38.0)))
    hue = (1.0 - frac) * 0.60  # 0.60 (mavi) -> 0.0 (kırmızı)
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return (int(r * 255), int(g * 255), int(b * 255))


def _blend(a, b, k: float):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def _centered(draw, cy, text, font, fill):
    _centered_at(draw, CENTER, cy, text, font, fill)


def _centered_at(draw, cx, cy, text, font, fill):
    w = draw.textlength(text, font=font)
    asc, desc = font.getmetrics()
    draw.text((cx - w / 2, cy - (asc + desc) / 2), text, font=font, fill=fill)


class Renderer:
    """Yumuşatılmış değerlerle tek bir 480x480 frame üretir."""

    def __init__(self) -> None:
        self.cpu_temp = 40.0
        self.cpu_load = 0.0
        self.gpu_temp = 40.0
        self.gpu_load = 0.0
        self.ram = 0.0
        self.fan = 0.0
        self.history: list[float] = [40.0] * 90  # cpu_temp geçmişi

    def update_targets(self, r: Reading, dt: float) -> None:
        rate = min(1.0, dt * 6.0)
        if r.cpu_temp is not None:
            self.cpu_temp = _smooth(self.cpu_temp, r.cpu_temp, rate)
        if r.gpu_temp is not None:
            self.gpu_temp = _smooth(self.gpu_temp, r.gpu_temp, rate)
        if r.cpu_load is not None:
            self.cpu_load = _smooth(self.cpu_load, r.cpu_load, rate)
        if r.gpu_load is not None:
            self.gpu_load = _smooth(self.gpu_load, r.gpu_load, rate)
        if r.ram_load is not None:
            self.ram = _smooth(self.ram, r.ram_load, rate)
        if r.fan_rpm is not None:
            self.fan = _smooth(self.fan, r.fan_rpm, rate)

    def push_history(self) -> None:
        self.history.append(self.cpu_temp)
        if len(self.history) > 90:
            self.history.pop(0)

    # Halka geometrisi: yarıçap (merkez çizgisi) ve kalınlık.
    _RINGS = (216, 178, 140)   # CPU / GPU / RAM dış->iç
    _RW = 12                   # halka kalınlığı
    _START = 135               # yay başlangıcı (sol-alt), saat yönü 270°
    _SWEEP = 270

    def _ring(self, d, r, frac, color) -> None:
        box = [CENTER - r, CENTER - r, CENTER + r, CENTER + r]
        d.arc(box, self._START, self._START + self._SWEEP, fill=RING_BG, width=self._RW)
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            d.arc(box, self._START, self._START + self._SWEEP * frac,
                  fill=color, width=self._RW)

    def draw(self, t: float) -> Image.Image:
        # Isı-tepkili zemin: en yüksek sıcaklığa göre çok hafif tint.
        hot = max(self.cpu_temp, self.gpu_temp)
        bg = _blend(BG, _temp_color(hot, sat=0.7, val=0.5), 0.10)
        img = Image.new("RGB", (SIZE, SIZE), bg)
        d = ImageDraw.Draw(img)

        rc, rg, rr = self._RINGS
        # Yay uzunluğu = YÜK, renk = SICAKLIK (ısı-tepkili).
        self._ring(d, rc, self.cpu_load / 100, _temp_color(self.cpu_temp))
        self._ring(d, rg, self.gpu_load / 100, _temp_color(self.gpu_temp))
        # RAM termal değil — serin nötr ton.
        self._ring(d, rr, self.ram / 100, (96, 132, 170))

        # Merkez kahraman: CPU yükü.
        accent = _temp_color(self.cpu_temp)
        _centered(d, CENTER - 24, f"{self.cpu_load:.0f}", F_BIG, TEXT)
        # '%' işareti büyük sayının sağ-üstünde, küçük.
        nw = d.textlength(f"{self.cpu_load:.0f}", font=F_BIG)
        d.text((CENTER + nw / 2 + 6, CENTER - 58), "%", font=F_MID, fill=MUTED)
        _centered(d, CENTER + 40, "CPU LOAD", F_TINY, MUTED)
        _centered(d, CENTER + 72, f"{self.cpu_temp:.0f}°", F_SMALL, accent)

        # Alt boşlukta (yayların açık olduğu bölge) sade GPU / RAM özeti.
        self._legend(d)

        # En altta geliştirici imzası.
        _centered_at(d, CENTER, SIZE - 16, "developed by Hilmi Noe", F_MICRO, MUTED)
        return img

    def _legend(self, d) -> None:
        y = CENTER + 150
        rpm = f"{self.fan:.0f} RPM" if self.fan > 0 else "-- RPM"
        # Sol: GPU yük + sıcaklık | Sağ: RAM yük + fan
        left = (140, "GPU", f"{self.gpu_load:.0f}%", f"{self.gpu_temp:.0f}°",
                _temp_color(self.gpu_temp))
        right = (300, "RAM", f"{self.ram:.0f}%", rpm, (150, 170, 200))
        for cx, label, val, sub, col in (left, right):
            _centered_at(d, cx, y, label, F_TINY, MUTED)
            _centered_at(d, cx, y + 26, val, F_SMALL, TEXT)
            _centered_at(d, cx, y + 54, sub, F_TINY, col)


def _open_with_retry(lcd, attempts: int = 30, delay: float = 4.0):
    """Önyüklemede USB cihaz geç hazır/oturmamış olabilir — bağlanana dek dene.
    Hem 'bulunamadı' hem handshake/timeout hatalarını yakalar."""
    from trcc_lcd import LcdNotFound

    for i in range(attempts):
        try:
            lcd.open()
            return
        except LcdNotFound as e:
            log.info("LCD henüz yok (%d/%d): %s — %.0fs sonra tekrar",
                     i + 1, attempts, e, delay)
            time.sleep(delay)
        except Exception as e:
            # handshake timeout / endpoint takılması: kapat, biraz bekle, tekrar
            log.info("LCD açılış hatası (%d/%d): %s — tekrar", i + 1, attempts, e)
            try:
                lcd.close()
            except Exception:
                pass
            time.sleep(delay)
    raise LcdNotFound("LCD belirtilen süre içinde bağlanamadı.")


class SpectrumRenderer:
    """Sistem sesinin dairesel FFT spektrumunu 480x480'e çizer."""

    R0 = 78          # iç yarıçap (bar başlangıcı)
    RMAX = 132       # maksimum bar uzunluğu
    BARW = 7         # bar kalınlığı

    def __init__(self, bands: int) -> None:
        self.n = bands
        self._colors = []
        for i in range(bands):
            # düşük frekans mavi -> orta turkuaz -> tiz mercan
            h = 0.58 - 0.58 * (i / max(1, bands - 1)) * 0.78  # 0.58..0.13
            r, g, b = colorsys.hsv_to_rgb(h, 0.72, 1.0)
            self._colors.append((int(r * 255), int(g * 255), int(b * 255)))

    def draw(self, bands, t: float) -> Image.Image:
        img = Image.new("RGB", (SIZE, SIZE), BG)
        d = ImageDraw.Draw(img)

        level = float(sum(bands) / len(bands)) if len(bands) else 0.0
        bass = float(sum(bands[:max(1, self.n // 8)]) / max(1, self.n // 8))

        # merkez nabız dairesi (basla büyür)
        pr = self.R0 - 12 + bass * 22
        col = (40 + int(bass * 120), 70 + int(bass * 90), 120 + int(bass * 100))
        d.ellipse([CENTER - pr, CENTER - pr, CENTER + pr, CENTER + pr],
                  outline=col, width=3)

        # dairesel barlar — tepeden başlar, saat yönünde
        for i in range(self.n):
            v = float(bands[i])
            ln = self.R0 + v * self.RMAX
            ang = -math.pi / 2 + (i / self.n) * 2 * math.pi
            ca, sa = math.cos(ang), math.sin(ang)
            x0 = CENTER + self.R0 * ca
            y0 = CENTER + self.R0 * sa
            x1 = CENTER + ln * ca
            y1 = CENTER + ln * sa
            d.line([(x0, y0), (x1, y1)], fill=self._colors[i], width=self.BARW)
            # uç nokta parlak
            d.ellipse([x1 - 3, y1 - 3, x1 + 3, y1 + 3], fill=self._colors[i])

        _centered(d, CENTER, f"{int(level * 100)}", F_MID, TEXT)
        _centered(d, 32, "AUDIO  FFT", F_TINY, BLUE)
        return img


_HUD_BG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "assets", "hud_bg.png")


class NetRenderer:
    """Canva'da üretilmiş dairesel HUD arka planı üstüne canlı veri bindirir:
    merkezde kayan ağ throughput ayna-grafiği (indirme aşağı, yükleme yukarı),
    dört köşede CPU / GPU / RAM / VRAM mini göstergeleri."""

    # Merkez grafik kutusu (köşe göstergelerinin arasında kalır).
    GX0, GX1 = 168, 312
    GY0, GY1 = 200, 280
    N = 120                  # geçmiş örnek sayısı (yatay çözünürlük)
    MIN_SCALE = 1_000_000.0  # otoskala tabanı: ~1 MB/s (8 Mb/s)

    def __init__(self) -> None:
        self.down = [0.0] * self.N
        self.up = [0.0] * self.N
        self._peak = self.MIN_SCALE

        # canlı sistem değerleri (yumuşatılmış)
        self.cpu_load = 0.0
        self.cpu_temp = 40.0
        self.gpu_load = 0.0
        self.gpu_temp = 40.0
        self.ram_pct = 0.0
        self.vram_pct = 0.0
        self.ram_used = 0.0
        self.ram_total = 0.0
        self.vram_used = 0.0
        self.vram_total = 0.0

        try:
            self._bg = Image.open(_HUD_BG).convert("RGB")
            if self._bg.size != (SIZE, SIZE):
                self._bg = self._bg.resize((SIZE, SIZE))
        except Exception as e:
            log.warning("HUD arka planı yüklenemedi (%s) — düz zemin.", e)
            self._bg = Image.new("RGB", (SIZE, SIZE), BG)

    def push(self, down_bps: float, up_bps: float) -> None:
        self.down.append(down_bps)
        self.down.pop(0)
        self.up.append(up_bps)
        self.up.pop(0)

    def update_stats(self, r: Reading, dt: float) -> None:
        rate = min(1.0, dt * 6.0)
        if r.cpu_load is not None:
            self.cpu_load = _smooth(self.cpu_load, r.cpu_load, rate)
        if r.cpu_temp is not None:
            self.cpu_temp = _smooth(self.cpu_temp, r.cpu_temp, rate)
        if r.gpu_load is not None:
            self.gpu_load = _smooth(self.gpu_load, r.gpu_load, rate)
        if r.gpu_temp is not None:
            self.gpu_temp = _smooth(self.gpu_temp, r.gpu_temp, rate)
        if r.ram_load is not None:
            self.ram_pct = _smooth(self.ram_pct, r.ram_load, rate)
        if r.ram_used_gb is not None:
            self.ram_used, self.ram_total = r.ram_used_gb, r.ram_total_gb or 0.0
        if r.vram_used_mb is not None and r.vram_total_mb:
            self.vram_used = r.vram_used_mb / 1024.0
            self.vram_total = r.vram_total_mb / 1024.0
            self.vram_pct = _smooth(self.vram_pct,
                                    100.0 * r.vram_used_mb / r.vram_total_mb, rate)

    @staticmethod
    def _fmt(bps: float) -> str:
        bits = bps * 8.0
        if bits >= 1e9:
            return f"{bits / 1e9:.2f} Gb/s"
        if bits >= 1e6:
            return f"{bits / 1e6:.1f} Mb/s"
        if bits >= 1e3:
            return f"{bits / 1e3:.0f} Kb/s"
        return f"{bits:.0f} b/s"

    def _gauge(self, d, cx, cy, frac, color, value, sub, label) -> None:
        r = 54
        box = [cx - r, cy - r, cx + r, cy + r]
        d.arc(box, 135, 135 + 270, fill=RING_BG, width=11)
        frac = max(0.0, min(1.0, frac))
        if frac > 0:
            d.arc(box, 135, 135 + 270 * frac, fill=color, width=11)
        _centered_at(d, cx, cy - r - 16, label, F_TINY, MUTED)
        _centered_at(d, cx, cy - 11, value, F_SMALL, TEXT)
        _centered_at(d, cx, cy + 18, sub, F_MICRO, color)

    def draw(self, t: float) -> Image.Image:
        img = self._bg.copy()
        d = ImageDraw.Draw(img)

        mid = (self.GY0 + self.GY1) // 2
        half = (self.GY1 - self.GY0) / 2.0
        target = max(self.MIN_SCALE, max(self.down), max(self.up))
        self._peak += (target - self._peak) * 0.10
        peak = max(self.MIN_SCALE, self._peak)

        def x_of(i):
            return self.GX0 + (self.GX1 - self.GX0) * i / (self.N - 1)

        down_pts = [(self.GX0, mid)]
        up_pts = [(self.GX0, mid)]
        for i in range(self.N):
            x = x_of(i)
            down_pts.append((x, mid + min(1.0, self.down[i] / peak) * half))
            up_pts.append((x, mid - min(1.0, self.up[i] / peak) * half))
        down_pts.append((self.GX1, mid))
        up_pts.append((self.GX1, mid))

        d.polygon(down_pts, fill=_blend(BG, BLUE, 0.55))
        d.polygon(up_pts, fill=_blend(BG, CORAL, 0.55))
        d.line(down_pts[1:-1], fill=BLUE, width=2)
        d.line(up_pts[1:-1], fill=CORAL, width=2)
        d.line([(self.GX0, mid), (self.GX1, mid)], fill=RING_BG, width=1)

        # köşe göstergeleri: kare panelin gerçek köşelerine yakın.
        # CPU / GPU üst, RAM / VRAM alt.
        self._gauge(d, 100, 110, self.cpu_load / 100, _temp_color(self.cpu_temp),
                    f"{self.cpu_load:.0f}%", f"{self.cpu_temp:.0f}°", "CPU")
        self._gauge(d, 380, 110, self.gpu_load / 100, _temp_color(self.gpu_temp),
                    f"{self.gpu_load:.0f}%", f"{self.gpu_temp:.0f}°", "GPU")
        self._gauge(d, 100, 374, self.ram_pct / 100, (118, 150, 200),
                    f"{self.ram_pct:.0f}%",
                    f"{self.ram_used:.1f}/{self.ram_total:.0f}G", "RAM")
        self._gauge(d, 380, 374, self.vram_pct / 100, TEAL,
                    f"{self.vram_pct:.0f}%",
                    f"{self.vram_used:.1f}/{self.vram_total:.0f}G", "VRAM")

        # ağ okumaları: yükleme üst-orta, indirme alt-orta
        _centered_at(d, CENTER, mid - half - 22, self._fmt(self.up[-1]),
                     F_SMALL, CORAL)
        _centered_at(d, CENTER, mid - half - 44, "▲ UP", F_MICRO, MUTED)
        _centered_at(d, CENTER, mid + half + 22, self._fmt(self.down[-1]),
                     F_SMALL, BLUE)
        _centered_at(d, CENTER, mid + half + 44, "▼ DOWN", F_MICRO, MUTED)
        _centered_at(d, CENTER, SIZE - 14, "developed by Hilmi Noe", F_MICRO, MUTED)
        return img


def run_net(fps: int) -> None:
    from trcc_lcd import TrccLcd, encode_rgb565
    from netflow import NetFlow

    lcd = TrccLcd()
    _open_with_retry(lcd)
    log.info("Ağ modu — ekran %dx%d %s", lcd.width, lcd.height,
             "JPEG" if lcd.jpeg else "RGB565")
    flow = NetFlow()
    sensors = Sensors()
    rend = NetRenderer()

    frame_dt = 1.0 / fps
    last = time.perf_counter()
    start = last
    last_sensor = 0.0
    reading = sensors.read()
    try:
        while True:
            now = time.perf_counter()
            dt = now - last
            last = now

            if now - last_sensor >= 1.0:     # sistem sensörleri ~1 Hz
                reading = sensors.read()
                last_sensor = now

            down, up = flow.poll()           # ağ ~fps Hz (her frame taze)
            rend.push(down, up)
            rend.update_stats(reading, dt)
            img = rend.draw(now - start)

            if lcd.jpeg:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                lcd.send(buf.getvalue())
            else:
                lcd.send(encode_rgb565(img))

            sleep = frame_dt - (time.perf_counter() - now)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        log.info("Durduruluyor.")
    finally:
        lcd.close()


def run_spectrum(fps: int) -> None:
    from audio import AudioSpectrum
    from trcc_lcd import TrccLcd, encode_rgb565

    bands = 48
    spec = AudioSpectrum(bands=bands)
    spec.start()

    lcd = TrccLcd()
    _open_with_retry(lcd)
    log.info("Spektrum modu — ekran %dx%d %s", lcd.width, lcd.height,
             "JPEG" if lcd.jpeg else "RGB565")
    rend = SpectrumRenderer(bands)

    # fps <= 0  -> sınırsız: en taze veriyi mümkün olan en hızlı şekilde gönder
    # (minimum gecikme). fps > 0 -> deadline tabanlı sabit tempo.
    uncapped = fps <= 0
    frame_dt = 0.0 if uncapped else 1.0 / fps
    start = time.perf_counter()
    deadline = start
    try:
        while True:
            bands = spec.poll()                     # FFT burada (güncel veri)
            img = rend.draw(bands, time.perf_counter() - start)
            if lcd.jpeg:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=82)
                lcd.send(buf.getvalue())
            else:
                lcd.send(encode_rgb565(img))
            if uncapped:
                continue                            # bekleme yok
            # düz sleep ile tempo: GIL'i bırakır, ses yakalama thread'i aç kalmaz
            # (busy-wait GIL'i tutup sesi geciktiriyordu). Jitter görsel önemsiz.
            deadline += frame_dt
            slack = deadline - time.perf_counter()
            if slack > 0:
                time.sleep(slack)
            elif slack < -frame_dt:
                deadline = time.perf_counter()
    except KeyboardInterrupt:
        log.info("Durduruluyor.")
    finally:
        spec.stop()
        lcd.close()


def run_live(fps: int) -> None:
    from trcc_lcd import TrccLcd, encode_rgb565

    lcd = TrccLcd()
    _open_with_retry(lcd)
    log.info("Ekran %dx%d, mod=%s", lcd.width, lcd.height,
             "JPEG" if lcd.jpeg else "RGB565")

    sensors = Sensors()
    rend = Renderer()

    frame_dt = 1.0 / fps
    last_sensor = 0.0
    last = time.perf_counter()
    start = last
    last_reading = sensors.read()

    try:
        while True:
            now = time.perf_counter()
            dt = now - last
            last = now

            if now - last_sensor >= 1.0:
                last_reading = sensors.read()
                rend.push_history()
                last_sensor = now

            rend.update_targets(last_reading, dt)
            img = rend.draw(now - start)

            if lcd.jpeg:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                lcd.send(buf.getvalue())
            else:
                lcd.send(encode_rgb565(img))

            sleep = frame_dt - (time.perf_counter() - now)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        log.info("Durduruluyor.")
    finally:
        lcd.close()


def run_preview(fps: int, seconds: float, mode: str = "monitor") -> None:
    """Ekran yokken: birkaç saniyelik render'ı preview.png olarak yazar."""
    if mode == "spectrum":
        from audio import AudioSpectrum

        bands = 48
        spec = AudioSpectrum(bands=bands)
        spec.start()
        rend = SpectrumRenderer(bands)
        start = time.perf_counter()
        img = None
        while time.perf_counter() - start < seconds:
            now = time.perf_counter()
            img = rend.draw(spec.poll(), now - start)
            time.sleep(1.0 / fps)
        spec.stop()
        if img is not None:
            img.save("preview.png")
            log.info("preview.png yazıldı (spectrum, %dx%d).", SIZE, SIZE)
        return

    if mode == "net":
        from netflow import NetFlow

        flow = NetFlow()
        sensors = Sensors()
        rend = NetRenderer()
        start = time.perf_counter()
        last = start
        last_sensor = 0.0
        reading = sensors.read()
        img = None
        while time.perf_counter() - start < seconds:
            now = time.perf_counter()
            dt = now - last
            last = now
            if now - last_sensor >= 1.0:
                reading = sensors.read()
                last_sensor = now
            down, up = flow.poll()
            rend.push(down, up)
            rend.update_stats(reading, dt)
            img = rend.draw(now - start)
            time.sleep(1.0 / fps)
        if img is not None:
            img.save("preview.png")
            log.info("preview.png yazıldı (net, %dx%d).", SIZE, SIZE)
        return

    sensors = Sensors()
    rend = Renderer()
    start = time.perf_counter()
    last = start
    last_sensor = 0.0
    reading = sensors.read()
    img = None
    while time.perf_counter() - start < seconds:
        now = time.perf_counter()
        dt = now - last
        last = now
        if now - last_sensor >= 1.0:
            reading = sensors.read()
            rend.push_history()
            last_sensor = now
        rend.update_targets(reading, dt)
        img = rend.draw(now - start)
        time.sleep(1.0 / fps)
    if img is not None:
        img.save("preview.png")
        log.info("preview.png yazıldı (%dx%d).", SIZE, SIZE)


def main() -> None:
    ap = argparse.ArgumentParser(description="Thermalright LCD canlı monitör")
    ap.add_argument("--fps", type=int, default=60, help="hedef frame hızı")
    ap.add_argument("--mode", choices=("monitor", "spectrum", "net"),
                    default="monitor",
                    help="monitor = sistem göstergesi, spectrum = ses FFT, "
                         "net = ağ throughput grafiği")
    ap.add_argument("--preview", action="store_true",
                    help="ekrana göndermeden preview.png üret")
    ap.add_argument("--seconds", type=float, default=3.0,
                    help="preview süresi")
    args = ap.parse_args()

    if args.preview:
        run_preview(args.fps, args.seconds, args.mode)
    elif args.mode == "spectrum":
        run_spectrum(args.fps)
    elif args.mode == "net":
        run_net(args.fps)
    else:
        run_live(args.fps)


if __name__ == "__main__":
    main()
