@echo off
rem Beendet LD-Machine-Viewer im Hintergrund und entfernt Autostart und Firewall-Freigabe.
rem Die erfassten Daten im Ordner data bleiben erhalten. Benoetigt Administratorrechte.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File \"%~dp0tools\autostart.ps1\" -Remove'"
