@echo off
rem Legt die Verknuepfung "LD Maschinenlaufzeit" mit Symbol auf dem Desktop und im Startmenue an.
rem Ein Klick oeffnet die Oberflaeche; laeuft die App noch nicht, wird sie gestartet.
rem Keine Adminrechte noetig. Nach dem Verschieben des Ordners erneut ausfuehren.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\verknuepfung.ps1"
pause
