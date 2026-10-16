@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo This folder has not been set up yet.
    echo Extract the whole ZIP, then double-click setup.cmd in this folder.
    echo.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0diagnose_windows.py"
set "QUESTION_VOLUME_DIAGNOSE_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %QUESTION_VOLUME_DIAGNOSE_EXIT%
