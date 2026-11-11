@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 goto use_py

where python >nul 2>nul
if not errorlevel 1 goto use_python

echo Python was not found.
echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/
echo During installation, select "Add Python to PATH".
pause
exit /b 1

:use_py
set "BOOTSTRAP=py -3"
goto setup

:use_python
set "BOOTSTRAP=python"

:setup
if not exist ".venv\Scripts\python.exe" (
    echo Creating the private Python environment...
    %BOOTSTRAP% -m venv .venv
    if errorlevel 1 goto failed
)

echo Installing or checking the required package...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed

echo.
echo Starting the Polymarket recorder...
echo Keep this window open and keep the computer awake.
echo Press Ctrl+C if you want to stop before the match ends.
echo.
".venv\Scripts\python.exe" polymarket_book_recorder.py
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
    echo The recorder finished normally.
) else (
    echo The recorder stopped with an error. Read the messages above.
)
pause
exit /b %RESULT%

:failed
echo.
echo Setup failed. Read the error shown above.
pause
exit /b 1
