@echo off
rem Startet die Einrichtung per Doppelklick.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0einrichtung\einrichten.ps1"
pause
