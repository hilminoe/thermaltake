"""Gerçek cihazda maksimum frame gönderim hızını ölçer."""
import io
import time

from PIL import Image, ImageDraw

from trcc_lcd import TrccLcd

lcd = TrccLcd()
lcd.open()
print(f"Ekran {lcd.width}x{lcd.height} mod={'JPEG' if lcd.jpeg else 'RGB565'}")

N = 120
t0 = time.perf_counter()
enc_total = 0.0
send_total = 0.0
for i in range(N):
    img = Image.new("RGB", (lcd.width, lcd.height), (10, 12, 16))
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, 460, 460], outline=(55, 138, 221), width=8)
    d.text((180, 220), f"{i}", fill=(235, 238, 242))

    te = time.perf_counter()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    payload = buf.getvalue()
    enc_total += time.perf_counter() - te

    ts = time.perf_counter()
    lcd.send(payload)
    send_total += time.perf_counter() - ts

dt = time.perf_counter() - t0
lcd.close()
print(f"{N} frame / {dt:.2f}s = {N/dt:.1f} fps")
print(f"  ortalama JPEG encode: {enc_total/N*1000:.2f} ms/frame")
print(f"  ortalama USB send   : {send_total/N*1000:.2f} ms/frame")
