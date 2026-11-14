@echo off
setlocal
title ZonClip Live Clipping
set "ROOT=%~dp0"
set "PY=%USERPROFILE%\clipforge\.venv\Scripts\python.exe"
set "WATCH=%USERPROFILE%\Videos\ZonClipLiveInput"

if not exist "%PY%" (
  echo ZonClip Python environment was not found.
  echo Run setup.ps1 first from the clipper folder.
  pause
  exit /b 1
)

if not exist "%WATCH%" mkdir "%WATCH%"
echo.
echo ZonClip Live Clipping
echo.
echo Put OBS time-split recordings in:
echo   %WATCH%
echo.
set /p "CUSTOM=Enter a different recording folder, or press Enter to use the default: "
if not "%CUSTOM%"=="" set "WATCH=%CUSTOM%"
if not exist "%WATCH%" (
  echo The selected recording folder does not exist.
  pause
  exit /b 1
)

echo.
echo Starting live watcher. Press Ctrl+C to stop and build ranked videos.
echo.
cd /d "%ROOT%"
"%PY%" live.py --watch "%WATCH%" --count 6 --settle-seconds 20
echo.
echo ZonClip live watcher stopped.
pause
