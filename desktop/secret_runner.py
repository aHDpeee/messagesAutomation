#!/usr/bin/env python3
"""Fail-closed bridge between Telegram Archive and the OS Secret Service keyring."""
from __future__ import annotations
import argparse, os, re, subprocess, sys, tempfile
from pathlib import Path

SERVICE = "SignalArchive"
ACCOUNT = "telegram-api-hash"

def keyring_module():
    try:
        import keyring
    except Exception as exc:
        raise RuntimeError(f"OS keyring support is unavailable: {exc}") from exc
    backend = keyring.get_keyring()
    # Reject plaintext/null/fail backends. SecretService is expected on Linux.
    name = type(backend).__module__ + "." + type(backend).__name__
    if "secretservice" not in name.casefold() and "kwallet" not in name.casefold():
        raise RuntimeError(f"Unsafe keyring backend refused: {name}. Configure GNOME Keyring or KDE Wallet.")
    return keyring

def set_secret(value: str):
    if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
        raise ValueError("Telegram API Hash must be exactly 32 hexadecimal characters.")
    kr=keyring_module(); kr.set_password(SERVICE, ACCOUNT, value)
    if kr.get_password(SERVICE, ACCOUNT) != value:
        raise RuntimeError("Keyring verification failed; the secret was not confirmed.")

def remove_env_hash(path: Path):
    lines=path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    hashes=[line.split("=",1)[1] for line in lines if line.startswith("TELEGRAM_API_HASH=")]
    if hashes:
        set_secret(hashes[-1])
        kept=[line for line in lines if not line.startswith("TELEGRAM_API_HASH=")]
        fd,tmp=tempfile.mkstemp(prefix=".env.",dir=path.parent,text=True)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as f: f.write("\n".join(kept)+"\n"); f.flush(); os.fsync(f.fileno())
            os.chmod(tmp,0o600); os.replace(tmp,path)
        finally:
            try: os.unlink(tmp)
            except FileNotFoundError: pass

def main():
    p=argparse.ArgumentParser(); p.add_argument("--set-api-hash-stdin",action="store_true"); p.add_argument("--migrate-env"); p.add_argument("--delete-api-hash",action="store_true"); p.add_argument("--check-api-hash",action="store_true"); p.add_argument("--restore-legacy-env")
    args,cmd=p.parse_known_args()
    try:
        if args.set_api_hash_stdin:
            set_secret(sys.stdin.read().strip()); return 0
        if args.migrate_env:
            remove_env_hash(Path(args.migrate_env)); return 0
        if args.check_api_hash:
            if not keyring_module().get_password(SERVICE, ACCOUNT): raise RuntimeError("API Hash is not present in the OS keyring.")
            return 0
        if getattr(args,"restore_legacy_env",None):
            secret=keyring_module().get_password(SERVICE, ACCOUNT)
            if not secret: raise RuntimeError("API Hash is missing from the OS keyring; cannot restore legacy environment.")
            path=Path(args.restore_legacy_env)
            lines=path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            lines=[line for line in lines if not line.startswith("TELEGRAM_API_HASH=")]
            lines.append("TELEGRAM_API_HASH="+secret)
            fd,tmp=tempfile.mkstemp(prefix=".env.",dir=path.parent,text=True)
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as f: f.write("\n".join(lines)+"\n"); f.flush(); os.fsync(f.fileno())
                os.chmod(tmp,0o600); os.replace(tmp,path)
            finally:
                try: os.unlink(tmp)
                except FileNotFoundError: pass
            return 0
        if args.delete_api_hash:
            kr=keyring_module()
            try: kr.delete_password(SERVICE, ACCOUNT)
            except Exception: pass
            if kr.get_password(SERVICE, ACCOUNT) is not None: raise RuntimeError("Keyring entry still exists after deletion.")
            return 0
        if cmd and cmd[0]=="--": cmd=cmd[1:]
        if not cmd: raise ValueError("No command supplied.")
        kr=keyring_module(); secret=kr.get_password(SERVICE, ACCOUNT)
        if not secret: raise RuntimeError("Telegram API Hash is missing from the OS keyring. Open Settings and configure credentials.")
        env=os.environ.copy(); env["TELEGRAM_API_HASH"]=secret
        os.execvpe(cmd[0],cmd,env)
    except Exception as exc:
        print(f"Signal Archive secure runtime: {exc}",file=sys.stderr); return 1
    return 0

if __name__=="__main__": raise SystemExit(main())
