# Thermalright LCD canlı monitör

Ekranlı sıvı soğutucunun (USB `VID 87AD / PID 70DB`, GrandVision serisi —
**Thermalright**, Thermaltake değil) 480×480 yuvarlak LCD'sine canlı sistem
verisi (CPU/GPU sıcaklık, yük, fan RPM) basan Python servisi.

Cihaz WinUSB sürücüsüne bağlı olduğu için Thermalright'ın kendi yazılımı
gerekmez — libusb üzerinden doğrudan konuşulur.

## Kurulum

1. **Python 3.10+** kur (şu an sistemde gerçek Python yok, sadece Store stub'ı):
   ```powershell
   winget install Python.Python.3.12
   ```
   Yeni bir terminal aç (PATH güncellensin).

2. Bağımlılıklar:
   ```powershell
   py -m pip install -r requirements.txt
   ```

   `libusb-package` bağımlılığı `libusb-1.0.dll`'i kendisi paketler — ayrı
   indirme/Zadig gerekmez. Cihaz zaten WinUSB sürücüsüne bağlı.

## Çalıştırma

```powershell
py main.py --preview                  # ekrana GÖNDERMEDEN preview.png (test)
py main.py                            # monitor modu: CPU/GPU göstergesi, 60 fps
py main.py --mode spectrum            # ses FFT spektrumu, 60 fps (WASAPI loopback)
py main.py --preview --mode spectrum  # ekransız spektrum testi
py main.py --mode net                 # ağ throughput grafiği, her frame örneklenir
py main.py --preview --mode net       # ekransız ağ testi
py main.py --fps 30                   # frame hızını değiştir

# Ölçülen kapasite (gerçek cihaz): USB ~370 fps, render+encode ~462 fps.
# Sensörler donanımda ~1 Hz güncellenir; render her frame yumuşatılarak
# akıcı görünür (sensör okuma ve çizim ayrıdır).
```

İlk olarak `--preview` ile `preview.png`'ye bak — render doğruysa `py main.py`
ile gerçek ekrana geç.

## Otomatik başlatma (her oturum açılışında)

`setup_lcd.ps1`, servisi oturum açılışında pencere açmadan başlatan bir
zamanlanmış görev (`ThermalrightLCD`) kaydeder — yönetici gerekmez:

```powershell
powershell -ExecutionPolicy Bypass -File setup_lcd.ps1                 # net modu
powershell -ExecutionPolicy Bypass -File setup_lcd.ps1 -Mode monitor   # başka mod
Start-ScheduledTask -TaskName ThermalrightLCD                          # hemen başlat
```

## Sıcaklık değerleri "--" görünüyorsa

CPU/GPU sıcaklığı için **LibreHardwareMonitor**'ı yönetici olarak çalıştır
(WMI yayınını açık tut). `sensors.py` otomatik bağlanır. NVIDIA GPU için
`nvidia-smi` varsa o da kullanılır. LHM yoksa CPU/RAM yükü psutil'den gelir,
sıcaklıklar boş kalır.

WMI bağlanması için ek paket: `py -m pip install wmi pywin32`.

## Dosyalar

| Dosya | Görev |
|-------|-------|
| `trcc_lcd.py` | USB protokolü: handshake + 64-byte header + JPEG/RGB565 frame |
| `sensors.py`  | LHM (WMI) → nvidia-smi → psutil kademeli sensör okuma |
| `audio.py`    | WASAPI loopback yakalama + Hann pencereli FFT band analizi |
| `main.py`     | 60 fps render döngüsü; monitor + spectrum + net modları |
| `netflow.py`  | psutil sayaçlarından anlık ağ throughput örnekleyici |
| `setup_lcd.ps1` | oturum açılışında otomatik başlatma görevini kaydeder |
| `bench.py`    | gerçek cihazda frame gönderim hızı ölçümü |

## Protokol notları

- Handshake: EP `0x01`'e 64-byte (`12 34 56 78` + byte[56]=1) → EP `0x81`'den
  1024-byte. `resp[24]` = PM (model), `resp[36]` = SUB.
- Frame header (64B): magic@0, cmd@4 (2=JPEG, 3=RGB565), width@8, height@12,
  sabit 2@56, payload_len@60. Ardından payload.
- 16 KiB parçalar halinde yazılır; frame boyutu 512'nin katıysa boş (ZLP) paket.
- Kaynak: reverse-engineered `thermalright-trcc-linux` projesi.
