"""HTTPS for network access: the user's certificate, or a self-signed one generated once with openssl."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path


class TlsError(Exception):
    pass


def host_names() -> list[str]:
    """The machine's own names, most specific first (FQDN, short name), then localhost."""
    out: list[str] = []
    for n in (socket.getfqdn(), socket.gethostname()):
        if n and n not in out and n != "localhost" and not n.startswith("localhost."):
            out.append(n)
    return out + ["localhost"]


def ensure_self_signed(directory: Path, names: list[str] | None = None, days: int = 825) -> tuple[Path, Path]:
    """Return (cert, key); create them on first use. The key is mode 0600, the directory 0700."""
    cert, key = directory / "cert.pem", directory / "key.pem"
    if cert.is_file() and key.is_file():
        return cert, key
    openssl = shutil.which("openssl")
    if openssl is None:
        raise TlsError("openssl not found: install it (dnf install -y openssl) or set ORCH_TLS_CERT/ORCH_TLS_KEY")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    names = names or host_names()
    san = ",".join([f"DNS:{n}" for n in names] + ["IP:127.0.0.1"])
    old = os.umask(0o077)
    try:
        r = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", str(days),
                            "-subj", f"/CN={names[0]}", "-addext", f"subjectAltName={san}",
                            "-keyout", str(key), "-out", str(cert)], capture_output=True, text=True)
    finally:
        os.umask(old)
    if r.returncode != 0:
        raise TlsError(f"openssl failed: {r.stderr.strip()[:300]}")
    os.chmod(key, 0o600)
    return cert, key


def fingerprint(cert: Path) -> str:
    r = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"],
                       capture_output=True, text=True)
    return r.stdout.strip().split("=", 1)[-1] if r.returncode == 0 else "?"
