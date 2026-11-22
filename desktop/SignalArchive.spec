# PyInstaller spec: build on Windows to produce a single windowed .exe.
from PyInstaller.utils.hooks import collect_all

keyring_data, keyring_binaries, keyring_hidden = collect_all("keyring")
telethon_data, telethon_binaries, telethon_hidden = collect_all("telethon")
webauthn_data, webauthn_binaries, webauthn_hidden = collect_all("webauthn")

a = Analysis(
    ["windows_entry.py"],
    pathex=["."],
    binaries=keyring_binaries + telethon_binaries + webauthn_binaries,
    datas=keyring_data + telethon_data + webauthn_data + [("CODEX_ARCHIVE_GUIDE.md", "."), ("LAN_SECURITY.md", ".")],
    hiddenimports=keyring_hidden + telethon_hidden + webauthn_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SignalArchive",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icons/tegash.ico",
)
