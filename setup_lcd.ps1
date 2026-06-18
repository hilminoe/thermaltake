# LCD servisini oturum açılışında otomatik başlatacak zamanlanmış görevi kaydeder.
# Pencere açmayan pythonw ile arka planda çalışır. Yönetici gerekmez.
#
# Kullanım:
#   powershell -ExecutionPolicy Bypass -File setup_lcd.ps1            # net modu
#   powershell -ExecutionPolicy Bypass -File setup_lcd.ps1 -Mode monitor
#   powershell -ExecutionPolicy Bypass -File setup_lcd.ps1 -Mode spectrum -Fps 60
param(
    [ValidateSet("monitor", "spectrum", "net")]
    [string]$Mode = "net",
    [int]$Fps = 60
)
$ErrorActionPreference = "Stop"

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $pyw = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
}
if (-not (Test-Path $pyw)) {
    throw "pythonw.exe bulunamadı. Python kurulu mu? Yol: $pyw"
}

$user      = "$env:USERDOMAIN\$env:USERNAME"
$action    = New-ScheduledTaskAction -Execute $pyw `
                -Argument "main.py --mode $Mode --fps $Fps" -WorkingDirectory $dir
# Oturum açıldıktan 20 sn sonra (USB cihaz otursun diye).
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT20S"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
                -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "ThermalrightLCD" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "ThermalrightLCD görevi kaydedildi: main.py --mode $Mode --fps $Fps"
Write-Host "Her oturum açılışında otomatik başlayacak. Şimdi başlatmak için:"
Write-Host "  Start-ScheduledTask -TaskName ThermalrightLCD"
