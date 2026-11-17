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

    # ---- runs -----------------------------------------------------------------------------
    @staticmethod
    def _run(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json"))
        d["summary"] = json.loads(d.pop("summary_json") or "null")
        return d

    def insert_run(self, run: dict[str, Any], pdbs: list[str]) -> None:
        with self.tx():
            self.conn.execute(
                "INSERT INTO runs(id, type, status, phase, profile_id, params_json, run_dir, retry_of, created_at, "
                "started_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run["id"], run["type"], run["status"], run.get("phase"), run.get("profile_id"),
                 json.dumps(run["params"], sort_keys=True), run.get("run_dir"), run.get("retry_of"),
                 run["created_at"], run.get("started_at")))
            self.conn.executemany("INSERT INTO run_items(run_id, pdb, ord, state) VALUES (?,?,?, 'pending')",
                                  [(run["id"], p, i) for i, p in enumerate(pdbs)])

    RUN_FIELDS = {"status", "phase", "started_at", "ended_at", "summary", "message", "run_dir"}

    def update_run(self, run_id: str, **fields: Any) -> None:
        bad = set(fields) - self.RUN_FIELDS
        if bad:
            raise ValueError(f"unknown run fields {bad}")
        if "summary" in fields:
            fields["summary_json"] = json.dumps(fields.pop("summary"), sort_keys=True)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._run(self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())

    def list_runs(self, limit: int = 50, type_: str | None = None, status: str | None = None,
                  q: str | None = None, since: str | None = None, until: str | None = None,
                  offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """Newest first. q matches the run id or any parameter text (e.g. a prefix or users=16)."""
        where, args = [], []
        if type_:
            where.append("type = ?")
            args.append(type_)
        if status:
            where.append("status = ?")
            args.append(status)
        if q:
            where.append("(id LIKE ? OR params_json LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        if since:
            where.append("created_at >= ?")
            args.append(since)
        if until:
            where.append("created_at < ?")
            args.append(until)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.conn.execute(f"SELECT COUNT(*) FROM runs {clause}", args).fetchone()[0]
        rows = self.conn.execute(f"SELECT * FROM runs {clause} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                                 (*args, limit, offset)).fetchall()
        return [self._run(r) for r in rows], total

    def item_counts(self, run_id: str) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, COUNT(*) n FROM run_items WHERE run_id = ? GROUP BY state",
                                 (run_id,)).fetchall()
        return {r["state"]: r["n"] for r in rows}

    ITEM_FIELDS = ("state", "con_uid", "check_status", "check_detail", "instance", "pid", "pgid", "proc_starttime",
                   "exit_code", "started_at", "ended_at", "detail", "error_count", "first_error")

    def get_items(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM run_items WHERE run_id = ? ORDER BY ord", (run_id,)).fetchall()
        return [{k: r[k] for k in r.keys() if k != "run_id"} for r in rows]

    def get_item(self, run_id: str, pdb: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM run_items WHERE run_id = ? AND pdb = ?", (run_id, pdb)).fetchone()
        return None if r is None else {k: r[k] for k in r.keys() if k != "run_id"}

    def update_items(self, run_id: str, items: list[dict[str, Any]]) -> None:
        """Write full item rows (all ITEM_FIELDS) in one transaction."""
        cols = ", ".join(f"{k} = ?" for k in self.ITEM_FIELDS)
        with self.tx():
            self.conn.executemany(
                f"UPDATE run_items SET {cols} WHERE run_id = ? AND pdb = ?",
                [(*(it.get(k) for k in self.ITEM_FIELDS), run_id, it["pdb"]) for it in items])

    def running_run_ids(self) -> list[str]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM runs WHERE status = 'running'").fetchall()]

    def mark_interrupted(self, run_id: str, alive: dict[str, int]) -> None:
        """Startup recovery for a run left 'running' by a previous backend process.

        alive: PDB -> pid of processes that are verifiably still running (they keep state 'running');
        others that were in progress ended while the backend was down, with an unknown result."""
        now = utcnow()
        with self.tx():
            self.conn.execute(
                "UPDATE runs SET status = 'interrupted', ended_at = COALESCE(ended_at, ?), message = ? WHERE id = ?",
                (now, "The backend stopped while this run was in progress"
                      + (f"; {len(alive)} process group(s) are still running." if alive else "."), run_id))
            self.conn.execute("UPDATE run_items SET state = 'not_started' WHERE run_id = ? AND state = 'pending'",
                              (run_id,))
            for pdb, pid in alive.items():
                self.conn.execute(
                    "UPDATE run_items SET state = 'running', detail = ? WHERE run_id = ? AND pdb = ?",
                    (f"still running after the backend restart (pid {pid}); no longer monitored", run_id, pdb))
            rows = self.conn.execute(
                "SELECT pdb, pid FROM run_items WHERE run_id = ? AND state IN ('checking', 'running')",
                (run_id,)).fetchall()
            for r in rows:
                if r["pdb"] in alive:
                    continue
                self.conn.execute(
                    "UPDATE run_items SET state = 'cancelled', ended_at = COALESCE(ended_at, ?), detail = ? "
                    "WHERE run_id = ? AND pdb = ?",
                    (now, "ended while the backend was down; result unknown"
                          + (f" (pid {r['pid']})" if r["pid"] else ""), run_id, r["pdb"]))

    # ---- per-PDB state kept across runs ----------------------------------------------------
    def upsert_last_check(self, pdb: str, status: str, detail: str, instance: str | None, run_id: str) -> None:
        self.conn.execute(
            "INSERT INTO last_checks(pdb, status, detail, instance, checked_at, run_id) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(pdb) DO UPDATE SET status = excluded.status, detail = excluded.detail, "
            "instance = excluded.instance, checked_at = excluded.checked_at, run_id = excluded.run_id",
            (pdb, status, detail, instance, utcnow(), run_id))

    def last_checks(self, prefix: str) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM last_checks WHERE pdb LIKE ? || '\\_%' ESCAPE '\\'",
                                 (prefix,)).fetchall()
        return {r["pdb"]: dict(r) for r in rows}

    def set_soe_marker(self, pdb: str, con_uid: str, run_id: str | None, scale: str | None,
                       tablespace: str | None, loaded_at: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO soe_markers(pdb, con_uid, loaded_at, run_id, scale, tablespace) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(pdb) DO UPDATE SET con_uid = excluded.con_uid, loaded_at = excluded.loaded_at, "
            "run_id = excluded.run_id, scale = excluded.scale, tablespace = excluded.tablespace",
            (pdb, con_uid, loaded_at or utcnow(), run_id, scale, tablespace))

    def delete_soe_marker(self, pdb: str) -> None:
        self.conn.execute("DELETE FROM soe_markers WHERE pdb = ?", (pdb,))

    def soe_markers(self, prefix: str) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM soe_markers WHERE pdb LIKE ? || '\\_%' ESCAPE '\\'",
                                 (prefix,)).fetchall()
        return {r["pdb"]: dict(r) for r in rows}

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
