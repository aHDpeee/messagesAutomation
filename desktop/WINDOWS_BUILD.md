# Building the Windows executable

Build on a Windows computer with Python 3.10 or later and internet access. Run
`build-windows-exe.bat`; the resulting standalone, windowed application is
`dist\SignalArchive.exe`. The build machine installs PyInstaller and runtime
dependencies into `.venv-windows-build`. The person using the resulting EXE
does not need Python or a command prompt. PyInstaller must build on Windows;
running this build script on Linux does not produce a Windows executable.

## Architecture

Build and test separately for each Windows architecture you intend to ship.
The current Windows build workflow uses an x64 runner; it does not produce a
verified native Windows ARM64 executable. On an ARM64 machine, first check the
architecture of the Python selected by `py -3` with `py -3 -c "import platform; print(platform.machine())"`.
An emulated x64 Python still produces an x64 build. Native ARM64 also requires
compatible native wheels and PyInstaller bootloader support; the current
project has not yet verified that combination. Do not label the result ARM64
without checking its PE machine type and testing it on Windows ARM64.

PyInstaller's `--target-arch` option is macOS-only; it is not a Windows
cross-compilation switch. See the [PyInstaller usage documentation](https://pyinstaller.org/en/stable/usage.html).

The executable is unsigned. Windows may display an unknown-publisher warning.
The executable stores the Telegram session under the user's profile and opens
the local browser interface. It does not embed Telegram API credentials.
