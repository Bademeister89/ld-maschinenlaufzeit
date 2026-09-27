<#
.SYNOPSIS
  Autostart fuer LD Maschinenlaufzeit einrichten oder entfernen (portable Version).

.DESCRIPTION
  Einrichten:
   - Windows-Aufgabe "LD Maschinenlaufzeit": startet beim Hochfahren als SYSTEM, ohne Anmeldung,
     ohne Zeitlimit, bei Absturz Neustart nach 1 Minute.
   - Firewall-Regel fuer den Port der Oberflaeche (eingehend, Netzwerkprofile Domaene und Privat).
  Entfernen (-Remove): Aufgabe beenden und loeschen, Firewall-Regel loeschen. Daten bleiben erhalten.

  Aufruf ueber autostart-einrichten.cmd / autostart-entfernen.cmd (fragen nach Adminrechten).
#>
param([switch]$Remove)
$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$TaskName = "LD Maschinenlaufzeit"
$RuleName = "LD Maschinenlaufzeit (Weboberflaeche)"
$Python = Join-Path $Root "runtime\python.exe"
$PythonW = Join-Path $Root "runtime\pythonw.exe"

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Bitte als Administrator ausfuehren (autostart-einrichten.cmd fragt automatisch danach)." -ForegroundColor Red
    exit 1
}

if ($Remove) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $TaskName
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Aufgabe '$TaskName' beendet und entfernt."
    } else {
        Write-Host "Keine Aufgabe '$TaskName' vorhanden."
    }
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-Host "Autostart entfernt. Die Daten im Ordner data bleiben erhalten." -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $PythonW)) { throw "runtime\pythonw.exe nicht gefunden - ist das die portable Version?" }
$Port = [int](& $Python -c "from app.config import load_settings; print(load_settings().port)")

$action = New-ScheduledTaskAction -Execute $PythonW -Argument "-m app" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Erfasst die Laufzeit der Heidenhain-Maschinen ($Root)" -Force | Out-Null
Write-Host "Aufgabe '$TaskName' eingerichtet (Start beim Hochfahren, auch ohne Anmeldung)."

if (-not (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $Port -Profile Domain, Private | Out-Null
    Write-Host "Firewall: Port $Port fuer Domaenen- und private Netzwerke freigegeben."
}

Start-ScheduledTask -TaskName $TaskName
Write-Host "Starte ..."
$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-WebRequest -Uri "http://localhost:$Port/api/meta" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $ok = $true
    } catch { }
}
if ($ok) {
    Write-Host ""
    Write-Host "LD Maschinenlaufzeit laeuft im Hintergrund." -ForegroundColor Green
    Write-Host "Oberflaeche auf diesem PC:   http://localhost:$Port"
    Write-Host "Von anderen PCs im Netz:     http://$($env:COMPUTERNAME):$Port"
} else {
    Write-Host "Die Oberflaeche antwortet noch nicht. Details stehen in data\logs\laufzeit.log" -ForegroundColor Yellow
}
