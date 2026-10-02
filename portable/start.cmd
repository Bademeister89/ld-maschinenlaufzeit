@echo off
rem LD-Machine-Viewer starten. Das Fenster offen lassen - schliessen beendet die Erfassung.
rem Fuer den Dauerbetrieb ohne Fenster: autostart-einrichten.cmd
cd /d "%~dp0"
title LD-Machine-Viewer
"%~dp0runtime\python.exe" -m app --open %*
if errorlevel 1 pause
