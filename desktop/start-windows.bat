@echo off
setlocal
cd /d "%~dp0"
if not exist .venv-direct\Scripts\python.exe (echo Run install-windows.bat first. & pause & exit /b 1)
.venv-direct\Scripts\python.exe -m signal_archive telegram %*
set "signal_archive_exit_code=%ERRORLEVEL%"
if not "%signal_archive_exit_code%"=="0" echo Signal Archive stopped with an error. See the message above.
pause
exit /b %signal_archive_exit_code%
