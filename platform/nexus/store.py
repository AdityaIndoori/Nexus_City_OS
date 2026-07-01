"""
Nexus City OS — Durable persistence layer (production pillar 1).

SQLite-backed (stdlib, zero external dependencies; the SQL surface is kept
ANSI-portable so a production deployment can point the same DAO at
PostgreSQL/TimescaleDB).

What is persisted and why:
  * audit            — the legally critical artifact (PRD §11.3). Append-only
                       hash chain stored durably; survives restarts; export
                       for legal discovery reads from disk, not memory.
  * kv               — operating mode, confidence threshold: restored on
                       restart so a crash never silently resets governance
                       state to defaults.
  * incidents/plans  — operational state snapshots for restart recovery and
                       the historical outcomes database (feeds confidence
                       calibration).
  * users            — PBKDF2-hashed credentials for the auth layer.

WAL journal mode keeps readers non-blocking under the background tick loop.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_entries (
    seq         INTEGER PRIMARY KEY,
    entry_json  TEXT NOT NULL,
    entry_hash  TEXT NOT NULL,
    prev_hash   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    id          TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    json        TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    plan_id     TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    incident_id TEXT,
    json        TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    role        TEXT NOT NULL,
    salt        BLOB NOT NULL,
    pw_hash     BLOB NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS congestion_history (
    intersection_id TEXT NOT NULL,
    congestion      REAL NOT NULL,
    at              REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ch_at ON congestion_history(at);
-- Analytics queries filter incidents/plans by updated_at; without these
-- indexes each /api/analytics call was a full table scan.
CREATE INDEX IF NOT EXISTS idx_inc_updated ON incidents(updated_at);
CREATE INDEX IF NOT EXISTS idx_plans_updated ON plans(updated_at);
"""


class Store:
    """Thread-safe SQLite persistence. One connection, serialized writes."""

    def __init__(self, path: str = "platform/data/nexus.db") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- audit (append-only; no UPDATE/DELETE statements exist) ---------

    def append_audit(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_entries (seq, entry_json, entry_hash, "
                "prev_hash) VALUES (?, ?, ?, ?)",
                (entry["seq"], json.dumps(entry, default=str),
                 entry["entry_hash"], entry["prev_hash"]))
            self._conn.commit()

    def load_audit(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT entry_json FROM audit_entries ORDER BY seq").fetchall()
        return [json.loads(r[0]) for r in rows]

    def audit_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM audit_entries").fetchone()
        return int(row[0])

    # ---- kv (governance state) ------------------------------------------

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, default=str)))
            self._conn.commit()

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # ---- incidents / plans (operational snapshots + outcomes DB) --------

    def upsert_incident(self, incident_id: str, state: str,
                        payload: Dict[str, Any], updated_at: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO incidents (id, state, json, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "state=excluded.state, json=excluded.json, "
                "updated_at=excluded.updated_at",
                (incident_id, state, json.dumps(payload, default=str),
                 updated_at))
            self._conn.commit()

    def upsert_plan(self, plan_id: str, status: str,
                    incident_id: Optional[str],
                    payload: Dict[str, Any], updated_at: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO plans (plan_id, status, incident_id, json, "
                "updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(plan_id) DO UPDATE SET status=excluded.status, "
                "json=excluded.json, updated_at=excluded.updated_at",
                (plan_id, status, incident_id,
                 json.dumps(payload, default=str), updated_at))
            self._conn.commit()

    # ---- congestion history (analytics — Phase 3) ------------------------

    def add_congestion_samples(self, rows) -> None:
        """Bulk insert (intersection_id, congestion, at) tuples."""
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO congestion_history (intersection_id, "
                "congestion, at) VALUES (?, ?, ?)", rows)
            self._conn.commit()

    def congestion_history(self, since: float) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT intersection_id, congestion, at FROM "
                "congestion_history WHERE at >= ? ORDER BY at",
                (since,)).fetchall()
        return [{"intersection_id": r[0], "congestion": r[1], "at": r[2]}
                for r in rows]

    def prune_history(self, before: float) -> int:
        """Delete congestion samples older than ``before``. Returns count."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM congestion_history WHERE at < ?", (before,))
            self._conn.commit()
        return cur.rowcount

    def incident_history(self, since: float) -> List[Dict[str, Any]]:
        """Incident snapshots updated since ``since`` (analytics)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT json, updated_at FROM incidents WHERE updated_at >= ?"
                " ORDER BY updated_at", (since,)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r[0])
            d["updated_at"] = r[1]
            out.append(d)
        return out

    def plan_history(self, since: float) -> List[Dict[str, Any]]:
        """Plan status snapshots updated since ``since`` (analytics)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT plan_id, status, updated_at FROM plans "
                "WHERE updated_at >= ? ORDER BY updated_at",
                (since,)).fetchall()
        return [{"plan_id": r[0], "status": r[1], "updated_at": r[2]}
                for r in rows]

    def plan_outcomes(self) -> Dict[str, int]:
        """Historical outcomes for confidence calibration."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM plans GROUP BY status"
            ).fetchall()
        return {status: count for status, count in rows}

    # ---- users -----------------------------------------------------------

    def upsert_user(self, user_id: str, role: str, salt: bytes,
                    pw_hash: bytes, created_at: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (user_id, role, salt, pw_hash, created_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                "role=excluded.role, salt=excluded.salt, "
                "pw_hash=excluded.pw_hash",
                (user_id, role, salt, pw_hash, created_at))
            self._conn.commit()

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, role, salt, pw_hash FROM users "
                "WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        return {"user_id": row[0], "role": row[1],
                "salt": row[2], "pw_hash": row[3]}

    def list_users(self) -> List[Dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, role FROM users ORDER BY user_id").fetchall()
        return [{"user_id": r[0], "role": r[1]} for r in rows]