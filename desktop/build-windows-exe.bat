@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    where python >nul 2>&1
    if errorlevel 1 (
        echo Python 3.10 or newer is required to build the executable.
        exit /b 1
    )
    python -m venv .venv-windows-build
) else (
    py -3 -m venv .venv-windows-build
)
if errorlevel 1 exit /b 1
.venv-windows-build\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv-windows-build\Scripts\python.exe -m pip install ".[lan]" "pyinstaller>=6,<7"
if errorlevel 1 exit /b 1
.venv-windows-build\Scripts\python.exe -m PyInstaller --noconfirm --clean SignalArchive.spec
if errorlevel 1 exit /b 1
echo Built: %CD%\dist\SignalArchive.exe
exit /b 0
