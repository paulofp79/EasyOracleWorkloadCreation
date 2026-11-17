"""SQLite access (stdlib sqlite3, WAL). One connection, used from the event loop thread."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
            os.chmod(self.path, 0o600)
        self.migrate()

    def migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Database {self.path} has schema {version}, newer than this program "
                               f"({SCHEMA_VERSION}). Upgrade the program.")
        if version < 1:
            # executescript commits on its own; the script is idempotent (IF NOT EXISTS).
            self.conn.executescript(SCHEMA_FILE.read_text() + f"\nPRAGMA user_version = {SCHEMA_VERSION};\n")

    def tx(self):
        return _Tx(self.conn)

    def close(self) -> None:
        self.conn.close()

    # ---- profiles -------------------------------------------------------------------------
    @staticmethod
    def _profile(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "is_active": bool(row["is_active"]),
                "params": json.loads(row["params_json"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM profiles ORDER BY name").fetchall()
        return [self._profile(r) for r in rows]

    def get_profile(self, pid: int) -> dict[str, Any] | None:
        return self._profile(self.conn.execute("SELECT * FROM profiles WHERE id = ?", (pid,)).fetchone())

    def get_profile_by_name(self, name: str) -> dict[str, Any] | None:
        return self._profile(self.conn.execute("SELECT * FROM profiles WHERE name = ?", (name,)).fetchone())

    def active_profile(self) -> dict[str, Any] | None:
        return self._profile(self.conn.execute(
            "SELECT * FROM profiles WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone())

    def create_profile(self, name: str, params: dict[str, Any], active: bool = False) -> dict[str, Any]:
        now = utcnow()
        with self.tx():
            if active:
                self.conn.execute("UPDATE profiles SET is_active = 0")
            cur = self.conn.execute(
                "INSERT INTO profiles(name, is_active, params_json, created_at, updated_at) VALUES (?,?,?,?,?)",
                (name, int(active), json.dumps(params, sort_keys=True), now, now))
        return self.get_profile(cur.lastrowid)

    def update_profile(self, pid: int, name: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self.conn.execute("UPDATE profiles SET name = ?, params_json = ?, updated_at = ? WHERE id = ?",
                          (name, json.dumps(params, sort_keys=True), utcnow(), pid))
        return self.get_profile(pid)

    def activate_profile(self, pid: int) -> dict[str, Any] | None:
        with self.tx():
            if self.conn.execute("SELECT 1 FROM profiles WHERE id = ?", (pid,)).fetchone() is None:
                return None
            self.conn.execute("UPDATE profiles SET is_active = (id = ?)", (pid,))
        return self.get_profile(pid)

    def delete_profile(self, pid: int) -> bool:
        with self.tx():
            row = self.conn.execute("SELECT is_active FROM profiles WHERE id = ?", (pid,)).fetchone()
            if row is None:
                return False
            self.conn.execute("DELETE FROM profiles WHERE id = ?", (pid,))
            if row["is_active"]:
                self.conn.execute(
                    "UPDATE profiles SET is_active = 1 WHERE id = (SELECT MIN(id) FROM profiles)")
        return True

    # ---- audit ----------------------------------------------------------------------------
    def audit(self, actor: str, action: str, run_id: str | None = None,
              params: dict[str, Any] | None = None) -> None:
        self.conn.execute("INSERT INTO audit_log(ts, actor, action, run_id, params_json) VALUES (?,?,?,?,?)",
                          (utcnow(), actor, action, run_id, json.dumps(params or {}, sort_keys=True)))


class _Tx:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
