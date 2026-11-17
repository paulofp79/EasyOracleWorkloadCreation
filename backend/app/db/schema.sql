-- Schema version 1. Secrets are never stored in any table.
CREATE TABLE IF NOT EXISTS profiles (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  is_active   INTEGER NOT NULL DEFAULT 0,
  params_json TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id           TEXT PRIMARY KEY,
  type         TEXT NOT NULL,
  status       TEXT NOT NULL,
  phase        TEXT,
  profile_id   INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
  params_json  TEXT NOT NULL,
  run_dir      TEXT,
  retry_of     TEXT REFERENCES runs(id),
  created_at   TEXT NOT NULL,
  started_at   TEXT,
  ended_at     TEXT,
  summary_json TEXT,
  message      TEXT
);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created_at);

CREATE TABLE IF NOT EXISTS run_items (
  run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  pdb            TEXT NOT NULL,
  ord            INTEGER NOT NULL,
  state          TEXT NOT NULL,
  con_uid        TEXT,
  check_status   TEXT,
  check_detail   TEXT,
  instance       TEXT,
  pid            INTEGER,
  pgid           INTEGER,
  proc_starttime INTEGER,
  exit_code      INTEGER,
  started_at     TEXT,
  ended_at       TEXT,
  detail         TEXT,
  error_count    INTEGER,
  first_error    TEXT,
  PRIMARY KEY (run_id, pdb)
);

CREATE TABLE IF NOT EXISTS soe_markers (
  pdb        TEXT PRIMARY KEY,
  con_uid    TEXT NOT NULL,
  loaded_at  TEXT NOT NULL,
  run_id     TEXT,
  scale      TEXT,
  tablespace TEXT
);

CREATE TABLE IF NOT EXISTS last_checks (
  pdb        TEXT PRIMARY KEY,
  status     TEXT NOT NULL,
  detail     TEXT,
  instance   TEXT,
  checked_at TEXT NOT NULL,
  run_id     TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY,
  ts          TEXT NOT NULL,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  run_id      TEXT,
  params_json TEXT
);

CREATE TABLE IF NOT EXISTS users (
  username      TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
