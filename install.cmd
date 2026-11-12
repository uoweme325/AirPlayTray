@echo off
cd /d "%~dp0"
python setup_env.py
if errorlevel 1 (
    echo Installation failed. See README.md for Python setup instructions.
    pause
    exit /b 1
)
pause
