# Elevated: stop LHM, write config enabling the HTTP JSON web server + start
# minimized to tray, then relaunch via the registered scheduled task.
$ErrorActionPreference = "Stop"
$dir = "C:\Users\hilmi\projeler\thermaltake\LibreHardwareMonitor"
$cfg = Join-Path $dir "LibreHardwareMonitor.config"

taskkill /F /IM LibreHardwareMonitor.exe 2>$null
Start-Sleep -Seconds 2

$xml = @'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <appSettings>
    <add key="runWebServerMenuItem" value="true" />
    <add key="listenerPort" value="8085" />
    <add key="minTrayMenuItem" value="true" />
    <add key="minCloseMenuItem" value="true" />
    <add key="startMinMenuItem" value="true" />
    <add key="minimizeToTray" value="true" />
  </appSettings>
</configuration>
'@
Set-Content -Path $cfg -Value $xml -Encoding UTF8

Start-ScheduledTask -TaskName "LibreHardwareMonitor"
Start-Sleep -Seconds 6
"running: " + ((Get-Process LibreHardwareMonitor -ErrorAction SilentlyContinue).Count)
