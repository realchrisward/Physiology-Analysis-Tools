@echo off
rem Physiology Analysis Tools - starts the app. Double-click this file.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto notinstalled
if not exist "node_modules" goto notinstalled
echo Starting Physiology Analysis Tools...
echo Keep this window open while you use the app. The app window opens in a moment.
echo.
call npm run dev
goto end
:notinstalled
echo The app is not installed yet. Double-click install.bat in the installation folder first.
:end
echo.
pause
