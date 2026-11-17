@echo off
setlocal
cd /d "%~dp0"
if not exist .venv-direct\Scripts\python.exe (
    echo First launch: installing Signal Archive. Internet access is required.
    where py >nul 2>&1
    if errorlevel 1 (
        where python >nul 2>&1
        if errorlevel 1 (echo Python 3.10 or newer was not found. Install Python and enable Add Python to PATH. & pause & exit /b 1)
        python install_runtime.py
    ) else (
        py -3 install_runtime.py
    )
    if errorlevel 1 (echo Installation failed. Check the error above. & pause & exit /b 1)
)
if not exist .venv-direct\Scripts\python.exe (echo Installation did not create the Python runtime. & pause & exit /b 1)
.venv-direct\Scripts\python.exe -m signal_archive telegram %*
set "signal_archive_exit_code=%ERRORLEVEL%"
if not "%signal_archive_exit_code%"=="0" echo Signal Archive stopped with an error. See the message above.
pause
exit /b %signal_archive_exit_code%
