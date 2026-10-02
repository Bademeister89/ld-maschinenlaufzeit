<#
.SYNOPSIS
  Startet LD-Machine-Viewer. Legt beim ersten Start die Python-Umgebung an.

.EXAMPLE
  .\start.ps1                 # echte Maschinen aus config.yaml
.EXAMPLE
  .\start.ps1 -Simulate       # simulierte Maschinen, eigene Demo-Datenbank
.EXAMPLE
  .\start.ps1 -ListenHost 0.0.0.0 -Port 8080   # auch von anderen PCs im Netz erreichbar
#>
param(
    [switch]$Simulate,
    [int]$Port = 8000,
    [string]$ListenHost = "127.0.0.1"
)
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
# Umgebung und Daten liegen bewusst ausserhalb des Nextcloud-Ordners.
$Venv = Join-Path $HOME "ld-mainmachine\venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Requirements = Join-Path $Root "requirements.txt"

if (-not (Test-Path $Python)) {
    Write-Host "Lege Python-Umgebung an: $Venv"
    py -3 -m venv $Venv
    if (-not $?) { throw "Python-Umgebung konnte nicht angelegt werden (ist Python 3.10+ installiert?)" }
}

# Abhaengigkeiten nur installieren, wenn sich requirements.txt geaendert hat
$Stamp = Join-Path $Venv ".requirements.sha256"
$Hash = (Get-FileHash $Requirements -Algorithm SHA256).Hash
if (-not (Test-Path $Stamp) -or (Get-Content $Stamp) -ne $Hash) {
    Write-Host "Installiere Abhaengigkeiten ..."
    & $Python -m pip install --disable-pip-version-check -q -r $Requirements
    if ($LASTEXITCODE -ne 0) { throw "pip install fehlgeschlagen" }
    Set-Content -Path $Stamp -Value $Hash -Encoding ascii
}

$env:PYTHONDONTWRITEBYTECODE = "1"   # keine __pycache__-Ordner im Nextcloud-Verzeichnis
$AppArgs = @("-m", "app", "--host", $ListenHost, "--port", $Port)
if ($Simulate) { $AppArgs += "--simulate" } else { Remove-Item Env:SIMULATE -ErrorAction SilentlyContinue }

Set-Location $Root
& $Python @AppArgs
