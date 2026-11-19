@echo off
setlocal
cd /d "%~dp0"
py -3.14 main.py %*
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start Daily CLI Updater.
    pause
)
endlocal
