@echo off
setlocal
cd /d "%~dp0"
echo Installing Question Taskbar for Windows...
py -3.14 "%~dp0install_windows.py" %*
set "QUESTION_VOLUME_SETUP_EXIT=%ERRORLEVEL%"
if not "%QUESTION_VOLUME_SETUP_EXIT%"=="0" (
    echo.
    echo Setup did not finish. Read the error above.
    echo This setup needs Python 3.14, Tesseract OCR, and a running Ollama model.
)
echo.
pause
exit /b %QUESTION_VOLUME_SETUP_EXIT%
