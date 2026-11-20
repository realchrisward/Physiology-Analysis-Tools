@echo off
rem Physiology Analysis Tools - first-time setup. Double-click this file.
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
