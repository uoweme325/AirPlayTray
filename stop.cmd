@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_background.ps1"
if errorlevel 1 (
    echo Failed to stop AirPlay. See the error above.
) else (
    echo AirPlay streaming and tray icons stopped.
)
pause
