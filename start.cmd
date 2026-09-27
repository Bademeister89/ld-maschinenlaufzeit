@echo off
rem Doppelklick-Start (umgeht die PowerShell-Ausführungsrichtlinie nur für dieses Skript).
rem Parameter werden durchgereicht, z. B.:  start.cmd -Simulate
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
