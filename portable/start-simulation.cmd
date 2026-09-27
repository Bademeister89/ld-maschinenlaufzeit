@echo off
rem Zum Ausprobieren: simulierte Maschinen, eigene Demo-Datenbank (data\demo.db).
rem Echte Daten (data\data.db) werden dabei nicht angefasst.
cd /d "%~dp0"
title LD Maschinenlaufzeit (SIMULATION)
"%~dp0runtime\python.exe" -m app --simulate --open %*
if errorlevel 1 pause
