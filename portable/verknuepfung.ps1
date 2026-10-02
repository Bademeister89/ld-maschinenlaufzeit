<#
.SYNOPSIS
  Verknuepfung "LD-Machine-Viewer" mit Symbol auf dem Desktop und im Startmenue anlegen
  (portable Version, keine Adminrechte noetig).

.DESCRIPTION
  Die Verknuepfung startet start.cmd minimiert. Laeuft die App schon (z. B. ueber den Autostart),
  oeffnet sie nur die Oberflaeche im Browser; sonst startet sie die App und oeffnet dann die
  Oberflaeche. Aufruf ueber verknuepfung-erstellen.cmd.

  Bis Version 1.9.0 hiess die Verknuepfung "LD Maschinenlaufzeit". Zeigt eine solche alte
  Verknuepfung auf diese Installation, wird sie entfernt.
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
    $old = Join-Path $folder "LD Maschinenlaufzeit.lnk"
    if ((Test-Path $old) -and $shell.CreateShortcut($old).TargetPath -eq $Target) {
        Remove-Item $old
        Write-Host "Alte Verknuepfung entfernt: $old"
    }
    $path = Join-Path $folder "LD-Machine-Viewer.lnk"
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $Target
    $link.Arguments = ""
    $link.WorkingDirectory = $Root
    $link.IconLocation = "$Icon,0"
    $link.WindowStyle = 7  # minimiert
    $link.Description = "LD-Machine-Viewer oeffnen"
    $link.Save()
    Write-Host "Verknuepfung angelegt: $path"
}
Write-Host ""
Write-Host "Fertig. Die Verknuepfung oeffnet die Oberflaeche und startet die App, falls sie nicht laeuft." -ForegroundColor Green
