# LCD ekranı takılı/boş kaldıysa USB cihazını yazılımsal yeniden takar.
# Çift tıkla çalıştır; UAC onayı isteyecek (yönetici gerekir).
$ErrorActionPreference = "Stop"
$id = "USB\VID_87AD&PID_70DB\28330C1819914F04"

# Yönetici değilsek kendini yükselt
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File","`"$PSCommandPath`""
    exit
}

Write-Host "LCD cihazı sıfırlanıyor..."
try {
    Disable-PnpDevice -InstanceId $id -Confirm:$false
    Start-Sleep -Seconds 2
    Enable-PnpDevice -InstanceId $id -Confirm:$false
    Start-Sleep -Seconds 3
    $st = (Get-PnpDevice -InstanceId $id).Status
    Write-Host "Durum: $st"
    # servisi yeniden başlat
    Start-ScheduledTask -TaskName "ThermalrightLCD"
    Write-Host "Servis yeniden başlatıldı."
} catch {
    Write-Host "Hata: $($_.Exception.Message)"
}
Start-Sleep -Seconds 3
