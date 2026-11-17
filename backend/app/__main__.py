"""python -m app                       start the orchestrator (settings from the environment, see app/config.py)
python -m app set-password [--user NAME]   create a login or change its password (default user: admin)
python -m app users                     list logins
python -m app delete-user NAME          remove a login
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys

import uvicorn

from .auth import check_new_password, check_username, hash_password
from .config import is_loopback, load_settings  # noqa: F401  (is_loopback re-exported for tests)
from .db import Database
from .main import create_app
from .tls import TlsError, ensure_self_signed, fingerprint, host_names


def cmd_set_password(user: str) -> int:
    settings = load_settings()
    try:
        check_username(user)
    except ValueError as exc:
        print(f"Invalid user name: {exc}", file=sys.stderr)
        return 2
    if sys.stdin.isatty():
        pw = getpass.getpass(f"New password for {user}: ")
        if getpass.getpass("Repeat: ") != pw:
            print("Passwords differ; nothing changed.", file=sys.stderr)
            return 2
    else:
        pw = sys.stdin.readline().rstrip("\n")      # scripted use: password on stdin, never argv
    try:
        check_new_password(pw)
    except ValueError as exc:
        print(f"Password not accepted: {exc}", file=sys.stderr)
        return 2
    db = Database(settings.db_path)
    new = db.set_user(user, hash_password(pw))
    db.audit("cli", "user_created" if new else "password_changed", params={"user": user})
    db.close()
    print(f"{'Created' if new else 'Updated'} login '{user}' in {settings.db_path}")
    return 0


def cmd_users() -> int:
    db = Database(load_settings().db_path)
    print("\n".join(db.list_users()) or "(no logins)")
    db.close()
    return 0


def cmd_delete_user(user: str) -> int:
    db = Database(load_settings().db_path)
    ok = db.delete_user(user)
    if ok:
        db.audit("cli", "user_deleted", params={"user": user})
    db.close()
    print("Deleted." if ok else f"No login '{user}'.")
    return 0 if ok else 1


def serve() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    if settings.auth:
        db = Database(settings.db_path)
        users = db.list_users()
        db.close()
        if not users:
            print("Login is required for ORCH_HOST=%s but no login exists yet. Create one first:\n"
                  "  venv/bin/python -m app set-password            (user 'admin')\n"
                  "Use the same ORCH_DB (and user account) as the server." % settings.host, file=sys.stderr)
            return 2
    ssl = {}
    scheme = "http"
    if settings.tls:
        try:
            cert, key = (settings.tls_cert, settings.tls_key) if settings.tls_cert else \
                ensure_self_signed(settings.tls_dir)
        except TlsError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
        scheme = "https"
        logging.getLogger("orchestrator").info("HTTPS certificate %s (SHA-256 %s)", cert, fingerprint(cert))
    if not is_loopback(settings.host):
        shown = host_names()[0] if settings.host in ("0.0.0.0", "::") else settings.host
        logging.getLogger("orchestrator").info("open %s://%s:%d (login required)", scheme, shown, settings.port)
        if not settings.tls:
            logging.getLogger("orchestrator").warning(
                "ORCH_TLS=off on a network address: only safe behind a reverse proxy that terminates HTTPS")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info",
                access_log=False, **ssl)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app", description="PDB Swingbench Orchestrator")
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("set-password", help="create a login or change its password")
    sp.add_argument("--user", default="admin")
    sub.add_parser("users", help="list logins")
    dp = sub.add_parser("delete-user", help="remove a login")
    dp.add_argument("user")
    args = p.parse_args(argv)
    if args.cmd == "set-password":
        return cmd_set_password(args.user)
    if args.cmd == "users":
        return cmd_users()
    if args.cmd == "delete-user":
        return cmd_delete_user(args.user)
    return serve()


if __name__ == "__main__":
    sys.exit(main())
