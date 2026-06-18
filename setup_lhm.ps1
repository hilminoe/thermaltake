# Elevated bootstrap: register LibreHardwareMonitor to run as admin at logon,
# then launch it now. Run as administrator.
$ErrorActionPreference = "Stop"
$exe  = "C:\Users\hilmi\projeler\thermaltake\LibreHardwareMonitor\LibreHardwareMonitor.exe"
$user = "$env:USERDOMAIN\$env:USERNAME"

$action    = New-ScheduledTaskAction -Execute $exe
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "LibreHardwareMonitor" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

# Launch it now, elevated, so WMI sensors come online immediately.
Start-Process -FilePath $exe
Write-Host "LHM scheduled task registered and launched."
