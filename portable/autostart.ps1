<#
.SYNOPSIS
  Autostart fuer LD-Machine-Viewer einrichten oder entfernen (portable Version).

.DESCRIPTION
  Einrichten:
   - Windows-Aufgabe "LD-Machine-Viewer": startet beim Hochfahren als SYSTEM, ohne Anmeldung,
     ohne Zeitlimit, bei Absturz Neustart nach 1 Minute.
   - Firewall-Regel fuer den Port der Oberflaeche (eingehend, Netzwerkprofile Domaene und Privat).
  Entfernen (-Remove): Aufgabe beenden und loeschen, Firewall-Regel loeschen. Daten bleiben erhalten.

  Bis Version 1.9.0 hiessen Aufgabe und Regel "LD Maschinenlaufzeit". Beide Wege raeumen diese alten
  Eintraege mit ab - sonst liefe nach dem Update die alte Aufgabe weiter und belegte den Port.

  Aufruf ueber autostart-einrichten.cmd / autostart-entfernen.cmd (fragen nach Adminrechten).
#>
param([switch]$Remove)
$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$TaskName = "LD-Machine-Viewer"
$RuleName = "LD-Machine-Viewer (Weboberflaeche)"
$OldTaskName = "LD Maschinenlaufzeit"
$OldRuleName = "LD Maschinenlaufzeit (Weboberflaeche)"
$Python = Join-Path $Root "runtime\python.exe"
$PythonW = Join-Path $Root "runtime\pythonw.exe"

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Bitte als Administrator ausfuehren (autostart-einrichten.cmd fragt automatisch danach)." -ForegroundColor Red
    exit 1
}

function Remove-AppTask([string]$Name) {
    # Aufgabe beenden (beendet auch die laufende App) und loeschen; $true, wenn es sie gab
    if (-not (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue)) { return $false }
    Stop-ScheduledTask -TaskName $Name
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    Write-Host "Aufgabe '$Name' beendet und entfernt."
    return $true
}

function Remove-AppRule([string]$Name) {
    Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}

if ($Remove) {
    $removed = Remove-AppTask $TaskName
    $removedOld = Remove-AppTask $OldTaskName
    if (-not ($removed -or $removedOld)) { Write-Host "Keine Aufgabe '$TaskName' vorhanden." }
    Remove-AppRule $RuleName
    Remove-AppRule $OldRuleName
    Write-Host "Autostart entfernt. Die Daten im Ordner data bleiben erhalten." -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $PythonW)) { throw "runtime\pythonw.exe nicht gefunden - ist das die portable Version?" }
# Aufgabe und Regel unter dem alten Namen (bis 1.9.0) entfernen, bevor die neue startet
if (Remove-AppTask $OldTaskName) { Start-Sleep -Seconds 2 }
Remove-AppRule $OldRuleName
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
    Write-Host "LD-Machine-Viewer laeuft im Hintergrund." -ForegroundColor Green
    Write-Host "Oberflaeche auf diesem PC:   http://localhost:$Port"
    Write-Host "Von anderen PCs im Netz:     http://$($env:COMPUTERNAME):$Port"
} else {
    Write-Host "Die Oberflaeche antwortet noch nicht. Details stehen in data\logs\laufzeit.log" -ForegroundColor Yellow
}
