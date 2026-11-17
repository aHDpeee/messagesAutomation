# Building the Windows executable

Build on a Windows computer with Python 3.10 or later and internet access. Run
`build-windows-exe.bat`; the resulting standalone, windowed application is
`dist\SignalArchive.exe`. The build machine installs PyInstaller and runtime
dependencies into `.venv-windows-build`. The person using the resulting EXE
does not need Python or a command prompt. PyInstaller must build on Windows;
running this build script on Linux does not produce a Windows executable.

The executable is unsigned. Windows may display an unknown-publisher warning.
The executable stores the Telegram session under the user's profile and opens
the local browser interface. It does not embed Telegram API credentials.
