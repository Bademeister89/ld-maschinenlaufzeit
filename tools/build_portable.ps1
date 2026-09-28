<#
.SYNOPSIS
  Baut die portable Windows-Version: ein Ordner (und ZIP) mit eigenem Python, der ohne
  Installation und ohne Adminrechte auf einem anderen Windows-PC laeuft.

.DESCRIPTION
  - laedt das offizielle "Windows embeddable package" von python.org (einmalig, wird gecacht)
  - installiert die Bibliotheken aus requirements.txt in den Ordner lib\
  - kopiert App, Werkzeuge, config.yaml und die Start-/Autostart-Skripte
  - prueft den fertigen Ordner mit einem Starttest und packt ihn als ZIP

  Ergebnis (Standard): C:\Users\<Name>\ld-mainmachine\dist\LD-Maschinenlaufzeit\ und ...-<Version>.zip

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1
#>
param(
    [string]$PythonVersion = "3.14.4",
    [string]$OutDir = (Join-Path $HOME "ld-mainmachine\dist")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # beschleunigt Invoke-WebRequest/Expand-Archive deutlich

$Root = Split-Path $PSScriptRoot -Parent
$Name = "LD-Maschinenlaufzeit"
$Target = Join-Path $OutDir $Name
$Cache = Join-Path $HOME "ld-mainmachine\cache"
$BuildPython = Join-Path $HOME "ld-mainmachine\venv\Scripts\python.exe"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

Step "Pruefe Build-Umgebung"
if (-not (Test-Path $BuildPython)) { throw "Entwicklungsumgebung fehlt ($BuildPython) - einmal start.ps1 ausfuehren." }
$buildVersion = & $BuildPython -c "import sys; print('%d.%d' % sys.version_info[:2])"
$wantVersion = ($PythonVersion.Split(".")[0..1] -join ".")
if ($buildVersion -ne $wantVersion) {
    throw "Build-Python ist $buildVersion, portable Version soll $wantVersion sein - Bibliotheken muessen zur Python-Version passen."
}

Step "Python $PythonVersion (embeddable) bereitstellen"
$zipName = "python-$PythonVersion-embed-amd64.zip"
$zipPath = Join-Path $Cache $zipName
if (-not (Test-Path $zipPath)) {
    New-Item -ItemType Directory -Force $Cache | Out-Null
    $url = "https://www.python.org/ftp/python/$PythonVersion/$zipName"
    Write-Host "Lade $url"
    Invoke-WebRequest -Uri $url -OutFile "$zipPath.part" -UseBasicParsing
    Move-Item "$zipPath.part" $zipPath
}
Write-Host ("{0} ({1:N1} MB)" -f $zipPath, ((Get-Item $zipPath).Length / 1MB))

Step "Zielordner neu anlegen: $Target"
if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
New-Item -ItemType Directory -Force $Target | Out-Null
$Runtime = Join-Path $Target "runtime"
Expand-Archive -Path $zipPath -DestinationPath $Runtime

# Suchpfade des eingebetteten Pythons: Standardbibliothek, lib\ (Pakete) und der App-Ordner
$pth = Get-ChildItem $Runtime -Filter "python*._pth" | Select-Object -First 1
$stdlib = (Get-ChildItem $Runtime -Filter "python3*.zip" | Select-Object -First 1).Name
Set-Content -Path $pth.FullName -Encoding ascii -Value @($stdlib, ".", "..\lib", "..")

Step "Bibliotheken installieren"
$Lib = Join-Path $Target "lib"
& $BuildPython -m pip install --disable-pip-version-check -q --no-compile --target $Lib -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install fehlgeschlagen" }
Remove-Item -Recurse -Force (Join-Path $Lib "bin") -ErrorAction SilentlyContinue

Step "App und Skripte kopieren"
Copy-Item (Join-Path $Root "app") $Target -Recurse
New-Item -ItemType Directory -Force (Join-Path $Target "tools") | Out-Null
Copy-Item (Join-Path $Root "tools\probe.py"), (Join-Path $Root "tools\seed_demo.py"), (Join-Path $Root "portable\autostart.ps1") (Join-Path $Target "tools")
Copy-Item (Join-Path $Root "portable\*.cmd") $Target
Copy-Item (Join-Path $Root "config.yaml"), (Join-Path $Root "README.md"), (Join-Path $Root "CHANGELOG.md") $Target
# Build-Kennung wie im Docker-Image (Datum + Commit); "+lokal", wenn nicht alles committet ist
try {
    $commit = (& git -C $Root rev-parse --short=7 HEAD).Trim()
    if (& git -C $Root status --porcelain) { $commit += "+lokal" }
} catch { $commit = "ohne-git" }
Set-Content -Path (Join-Path $Target "app\BUILD") -Encoding ascii -Value ("{0}-{1}" -f (Get-Date -Format "yyyy-MM-dd"), $commit)
Get-ChildItem $Target -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Step "Vorkompilieren (schnellerer Start)"
& (Join-Path $Runtime "python.exe") -m compileall -q -j 0 (Join-Path $Target "app") $Lib | Out-Null

Step "Starttest"
$check = & (Join-Path $Runtime "python.exe") -c "import app.main, app.config as c, pyLSV2, fastapi, uvicorn, zoneinfo; zoneinfo.ZoneInfo('Europe/Berlin'); print('portable' if c.IS_PORTABLE else 'NICHT portabel', c.default_data_dir())"
if ($LASTEXITCODE -ne 0 -or $check -notlike "portable*") { throw "Starttest fehlgeschlagen: $check" }
Write-Host $check

Step "ZIP erstellen"
$appVersion = & (Join-Path $Runtime "python.exe") -c "import app; print(app.__version__)"
$zipOut = Join-Path $OutDir ("{0}-{1}.zip" -f $Name, $appVersion)
if (Test-Path $zipOut) { Remove-Item $zipOut }
Compress-Archive -Path $Target -DestinationPath $zipOut -CompressionLevel Optimal

$folderMb = (Get-ChildItem $Target -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ""
Write-Host ("Fertig: {0}  ({1:N0} MB)" -f $Target, $folderMb) -ForegroundColor Green
Write-Host ("        {0}  ({1:N0} MB)" -f $zipOut, ((Get-Item $zipOut).Length / 1MB)) -ForegroundColor Green
