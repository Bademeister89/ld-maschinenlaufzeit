@echo off
rem LD Maschinenlaufzeit starten. Das Fenster offen lassen - schliessen beendet die Erfassung.
rem Fuer den Dauerbetrieb ohne Fenster: autostart-einrichten.cmd
cd /d "%~dp0"
title LD Maschinenlaufzeit
"%~dp0runtime\python.exe" -m app --open %*
if errorlevel 1 pause
