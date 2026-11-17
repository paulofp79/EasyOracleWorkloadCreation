"""python -m app  — start the orchestrator (settings from environment, see app/config.py)."""
from __future__ import annotations

import ipaddress
import logging
import sys

import uvicorn

from .config import load_settings
from .main import create_app


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    if not is_loopback(settings.host):
        print(f"Refusing to bind to {settings.host}: binding to anything other than localhost needs "
              "authentication, which is not available yet. Use an SSH tunnel "
              "(ssh -L 8080:127.0.0.1:8080 oracle@<host>).", file=sys.stderr)
        return 2
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info",
                access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
