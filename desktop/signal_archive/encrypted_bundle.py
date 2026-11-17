"""Atomic AES ZIP updates for password-protected exports.

ZIP entry names are not encrypted. Passwords are never stored here.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def add_encrypted_file(bundle: Path, source: Path, password: str, *, arcname: str | None = None) -> None:
    """Add one file and atomically publish the archive before checkpointing.

    The caller owns password collection and must keep the same password for
    every entry in a bundle. Failure leaves any existing bundle unchanged.
    """
    if not password:
        raise ValueError("An archive password is required.")
    try:
        import pyzipper
    except ImportError as exc:
        raise RuntimeError("Encrypted ZIP support is unavailable; reinstall the application.") from exc
    bundle, source = Path(bundle), Path(source)
    bundle.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=".encrypted-", suffix=".part", dir=bundle.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        if bundle.exists():
            shutil.copyfile(bundle, temporary)
            # Validate the existing archive and password before appending.
            with pyzipper.AESZipFile(temporary, "r") as previous:
                previous.setpassword(password.encode("utf-8"))
                for info in previous.infolist():
                    if not info.flag_bits & 1:
                        raise ValueError("Cannot add encrypted files to an unencrypted archive.")
                    with previous.open(info) as stream:
                        while stream.read(1024 * 1024):
                            pass
        with pyzipper.AESZipFile(temporary, "a", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES, allowZip64=True) as archive:
            archive.setpassword(password.encode("utf-8"))
            archive.setencryption(pyzipper.WZ_AES, nbits=256)
            archive.write(source, arcname=arcname or source.name)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, bundle)
    finally:
        temporary.unlink(missing_ok=True)
