@echo off
rem Richtet den Autostart ein: startet LD Maschinenlaufzeit beim Hochfahren im Hintergrund,
rem auch ohne Anmeldung, und gibt den Port der Oberflaeche in der Windows-Firewall frei.
rem Benoetigt Administratorrechte - Windows fragt danach.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File \"%~dp0tools\autostart.ps1\"'"
