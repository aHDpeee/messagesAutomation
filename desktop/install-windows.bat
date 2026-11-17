@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    where python >nul 2>&1
    if errorlevel 1 (
        echo Python 3.10 or newer was not found. Install Python and enable Add Python to PATH.
        pause
        exit /b 1
    )
    python install_runtime.py
) else (
    py -3 install_runtime.py
)
if errorlevel 1 (echo Installation failed. Check the error above. & pause & exit /b 1)
pause
exit /b 0
