"""TRCC LCD — raw USB-bulk sürücü (Thermalright GrandVision serisi, 87AD:70DB).

Protokol kaynağı: USBLCDNew.exe ThreadSendDeviceData (reverse-engineered,
thermalright-trcc-linux projesi). Cihaz WinUSB sürücüsüne bağlı olduğu için
libusb backend ile doğrudan konuşulabilir — Thermalright yazılımı gerekmez.

Handshake : EP 0x01'e 64-byte istek yaz -> EP 0x81'den 1024-byte oku.
            Geçerlilik resp[24] != 0. PM = resp[24], SUB = resp[36].
Frame     : 64-byte header + payload (JPEG ya da raw RGB565),
            16 KiB parçalar halinde yazılır, 512-byte hizada ZLP eklenir.
"""
from __future__ import annotations

import logging
import struct
import time

import usb.core
import usb.util

log = logging.getLogger("trcc.lcd")

VID = 0x87AD
PID = 0x70DB

_EP_WRITE = 0x01
_EP_READ = 0x81

_HANDSHAKE = bytes([0x12, 0x34, 0x56, 0x78] + [0] * 52 + [1, 0, 0, 0, 0, 0, 0, 0])
_HS_READ = 1024
_HS_TIMEOUT = 1000
_WR_TIMEOUT = 5000
_CHUNK = 16 * 1024

# PM=32 raw RGB565 (cmd=3) kullanır; diğer her şey JPEG (cmd=2).
_RGB565_PMS = {32}
# Bilinen yatay 480x480 paneller dışındaki PM'ler de 480x480'e sabitlenir.
DEFAULT_SIZE = (480, 480)


class LcdNotFound(RuntimeError):
    pass


class TrccLcd:
    """87AD:70DB bulk LCD cihazı."""

    def __init__(self) -> None:
        self._dev: usb.core.Device | None = None
        self.width, self.height = DEFAULT_SIZE
        self.pm = 0
        self.sub = 0
        self.jpeg = True

    # ── bağlantı ──────────────────────────────────────────────────────

    def open(self) -> None:
        # libusb backend: önce libusb-package (DLL paketli), yoksa sistemdeki.
        backend = None
        try:
            import libusb_package

            backend = libusb_package.get_libusb1_backend()
        except Exception:
            backend = None
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
        if dev is None:
            raise LcdNotFound(
                f"LCD bulunamadı (VID={VID:#06x} PID={PID:#06x}). "
                "Kablo bağlı mı ve WinUSB sürücüsü yüklü mü?"
            )
        # Windows/WinUSB: kernel driver detach gerekmez; doğrudan kullan.
        try:
            dev.set_configuration()
        except usb.core.USBError:
            # Zaten yapılandırılmışsa devam et.
            pass
        self._dev = dev
        self._handshake()

    def _handshake(self, attempts: int = 8) -> None:
        assert self._dev is not None
        resp = None
        last_err: Exception | None = None
        for i in range(attempts):
            try:
                self._dev.write(_EP_WRITE, _HANDSHAKE, _HS_TIMEOUT)
                resp = self._dev.read(_EP_READ, _HS_READ, _HS_TIMEOUT)
                if len(resp) >= 41 and resp[24] != 0:
                    break
            except usb.core.USBError as e:
                last_err = e
                log.debug("Handshake denemesi %d başarısız: %s", i + 1, e)
                # endpoint takılıysa temizle
                for ep in (_EP_WRITE, _EP_READ):
                    try:
                        self._dev.clear_halt(ep)
                    except usb.core.USBError:
                        pass
            time.sleep(0.25)
        else:
            raise RuntimeError(
                f"Handshake {attempts} denemede başarısız (son hata: {last_err})"
            )
        if resp is None or len(resp) < 41 or resp[24] == 0:
            raise RuntimeError(
                f"Handshake doğrulanamadı (len={len(resp) if resp else 0}, "
                f"resp[24]={resp[24] if resp and len(resp) > 24 else 'N/A'})"
            )
        self.pm = int(resp[24])
        self.sub = int(resp[36])
        self.jpeg = self.pm not in _RGB565_PMS
        log.info(
            "Handshake OK: PM=%d SUB=%d %dx%d (%s)",
            self.pm, self.sub, self.width, self.height,
            "JPEG" if self.jpeg else "RGB565",
        )

    # ── frame gönderimi ───────────────────────────────────────────────

    def send(self, payload: bytes) -> None:
        """payload: JPEG bytes (jpeg=True) ya da raw RGB565 little-endian."""
        if self._dev is None:
            raise RuntimeError("open() çağrılmadı")

        cmd = 2 if self.jpeg else 3
        header = bytearray(64)
        header[0:4] = _HANDSHAKE[0:4]
        struct.pack_into("<I", header, 4, cmd)
        struct.pack_into("<I", header, 8, self.width)
        struct.pack_into("<I", header, 12, self.height)
        struct.pack_into("<I", header, 56, 2)
        struct.pack_into("<I", header, 60, len(payload))

        frame = bytes(header) + payload
        for off in range(0, len(frame), _CHUNK):
            self._dev.write(_EP_WRITE, frame[off:off + _CHUNK], _WR_TIMEOUT)
        if len(frame) % 512 == 0:
            self._dev.write(_EP_WRITE, b"", _WR_TIMEOUT)

    def close(self) -> None:
        if self._dev is not None:
            usb.util.dispose_resources(self._dev)
            self._dev = None


def encode_rgb565(img) -> bytes:
    """PIL RGB image -> raw RGB565 little-endian bytes (PM=32 yolu için)."""
    rgb = img.convert("RGB")
    out = bytearray(rgb.width * rgb.height * 2)
    px = rgb.load()
    i = 0
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = px[x, y]
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out[i] = v & 0xFF
            out[i + 1] = (v >> 8) & 0xFF
            i += 2
    return bytes(out)
