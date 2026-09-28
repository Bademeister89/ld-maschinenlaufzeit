<#
.SYNOPSIS
  Verknuepfung "LD Maschinenlaufzeit" mit Symbol auf dem Desktop und im Startmenue anlegen
  (portable Version, keine Adminrechte noetig).

.DESCRIPTION
  Die Verknuepfung startet start.cmd minimiert. Laeuft die App schon (z. B. ueber den Autostart),
  oeffnet sie nur die Oberflaeche im Browser; sonst startet sie die App und oeffnet dann die
  Oberflaeche. Aufruf ueber verknuepfung-erstellen.cmd.
#>
$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$Target = Join-Path $Root "start.cmd"
$Icon = Join-Path $Root "app\static\icons\ld-maschinenlaufzeit.ico"
if (-not (Test-Path $Target)) { throw "start.cmd nicht gefunden - ist das die portable Version?" }
if (-not (Test-Path $Icon)) { throw "Symbol nicht gefunden: $Icon" }

$shell = New-Object -ComObject WScript.Shell
$folders = @(
    [Environment]::GetFolderPath("Desktop"),
    [Environment]::GetFolderPath("Programs")
)
foreach ($folder in $folders) {
    $path = Join-Path $folder "LD Maschinenlaufzeit.lnk"
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $Target
    $link.Arguments = ""
    $link.WorkingDirectory = $Root
    $link.IconLocation = "$Icon,0"
    $link.WindowStyle = 7  # minimiert
    $link.Description = "LD Maschinenlaufzeit oeffnen"
    $link.Save()
    Write-Host "Verknuepfung angelegt: $path"
}
Write-Host ""
Write-Host "Fertig. Die Verknuepfung oeffnet die Oberflaeche und startet die App, falls sie nicht laeuft." -ForegroundColor Green
