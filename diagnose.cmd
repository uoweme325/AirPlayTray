@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run install.cmd first.
    pause
    exit /b 1
)
echo This records system audio to diagnostic.wav for up to 40 seconds.
echo Keep private conversations and audio closed. Stop the tray app before using this.
".venv\Scripts\python.exe" -u "airplay_system_audio.py" --dump "diagnostic.wav" --report 5 --seconds 40
pause
