@echo off
setlocal
cd /d "%~dp0"
set "QUESTION_TASKBAR_REPAIR_EXIT=1"
if not exist "%~dp0.venv\Scripts\python.exe" goto missing_environment
echo Reinstalling Question Taskbar's Python dependencies...
"%~dp0.venv\Scripts\python.exe" -m pip install --force-reinstall -r "%~dp0requirements.txt"
set "QUESTION_TASKBAR_REPAIR_EXIT=%ERRORLEVEL%"
if not "%QUESTION_TASKBAR_REPAIR_EXIT%"=="0" goto finished
echo.
"%~dp0.venv\Scripts\python.exe" "%~dp0diagnose_windows.py"
set "QUESTION_TASKBAR_REPAIR_EXIT=%ERRORLEVEL%"
goto finished
:missing_environment
echo The program's Python environment is missing.
echo Extract the entire ZIP, then double-click setup.cmd first.
:finished
echo.
pause
exit /b %QUESTION_TASKBAR_REPAIR_EXIT%
