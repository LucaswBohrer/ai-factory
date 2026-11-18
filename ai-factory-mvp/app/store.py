"""AI Factory MVP — persistence (sqlite3, stdlib only).

Tables: users, agents, executions, audit_log (append-only), meta.
The audit log is append-only at the DB level: triggers abort any
UPDATE or DELETE on audit_log. There is deliberately no update/delete
API for it in this module.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    owner TEXT PRIMARY KEY,
    plan TEXT NOT NULL DEFAULT 'free',
    credits_balance INTEGER NOT NULL DEFAULT 100,
    is_admin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tools TEXT NOT NULL DEFAULT '[]',
    permissions TEXT NOT NULL DEFAULT '[]',
    parent_id TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    credits_used INTEGER NOT NULL DEFAULT 0,
    max_credits INTEGER NOT NULL DEFAULT 50,
    real_api_cost REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    minutes INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS api_keys (
    public_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_to_row(agent: dict) -> tuple:
    return (
        agent["id"], agent["name"], agent["owner"], agent["kind"],
        agent.get("description", ""),
        json.dumps(list(agent.get("tools", []))),
        json.dumps(list(agent.get("permissions", []))),
        agent.get("parent_id"),
        int(agent.get("depth", 0)),
        agent.get("status", "active"),
        int(agent.get("credits_used", 0)),
        int(agent.get("max_credits", 50)),
        float(agent.get("real_api_cost", 0.0)),
        agent.get("created_at", _now()),
    )


def _row_to_agent(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["tools"] = json.loads(d["tools"])
    d["permissions"] = json.loads(d["permissions"])
    d["credits_used"] = int(d["credits_used"])
    d["max_credits"] = int(d["max_credits"])
    d["depth"] = int(d["depth"])
    d["real_api_cost"] = float(d["real_api_cost"])
    return d


class Store:
    """Thin sqlite3 wrapper. No business rules here — those live in
    Factory / Control / ResourceCore. This layer only persists."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- users ---
    def get_user(self, owner: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE owner = ?", (owner,)).fetchone()
        return dict(row) if row else None

    def create_user(self, owner: str, plan: str = "free") -> dict:
        is_admin = 1 if owner == config.ADMIN_USER else 0
        if plan == "free":
            balance = config.FREE_QUOTA
        elif plan in config.PAID_PLANS:
            balance = config.PAID_PLANS[plan]
        else:
            balance = config.FREE_QUOTA
        self.conn.execute(
            "INSERT INTO users (owner, plan, credits_balance, is_admin)"
            " VALUES (?, ?, ?, ?)",
            (owner, plan, balance, is_admin))
        self.conn.commit()
        return self.get_user(owner)

    def ensure_user(self, owner: str, plan: str = "free") -> dict:
        user = self.get_user(owner)
        return user if user else self.create_user(owner, plan)

    def set_balance(self, owner: str, balance: int) -> None:
        self.conn.execute(
            "UPDATE users SET credits_balance = ? WHERE owner = ?",
            (balance, owner))
        self.conn.commit()

    # --- agents (low-level insert: no permission validation here;
    # Factory validates. This lets the Auditor be tested against
    # risky rows that the Factory would never create.) ---
    def insert_agent(self, agent: dict) -> dict:
        agent = dict(agent)
        agent.setdefault("id", uuid.uuid4().hex[:12])
        agent.setdefault("created_at", _now())
        self.conn.execute(
            "INSERT INTO agents (id, name, owner, kind, description, tools,"
            " permissions, parent_id, depth, status, credits_used,"
            " max_credits, real_api_cost, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _agent_to_row(agent))
        self.conn.commit()
        return self.get_agent(agent["id"])

    def get_agent(self, agent_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return _row_to_agent(row) if row else None

    def list_agents(self, owner: str | None = None) -> list[dict]:
        if owner is None:
            rows = self.conn.execute(
                "SELECT * FROM agents ORDER BY created_at").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM agents WHERE owner = ? ORDER BY created_at",
                (owner,)).fetchall()
        return [_row_to_agent(r) for r in rows]

    def count_active(self, owner: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM agents WHERE owner = ?"
            " AND status = 'active'", (owner,)).fetchone()
        return int(row["n"])

    def update_agent_status(self, agent_id: str, status: str) -> None:
        if status not in config.AGENT_STATUSES:
            raise ValueError(f"unknown status: {status}")
        self.conn.execute(
            "UPDATE agents SET status = ? WHERE id = ?", (status, agent_id))
        self.conn.commit()

    def add_agent_credits_used(self, agent_id: str, amount: int) -> None:
        self.conn.execute(
            "UPDATE agents SET credits_used = credits_used + ? WHERE id = ?",
            (amount, agent_id))
        self.conn.commit()

    def add_real_api_cost(self, agent_id: str, amount: float) -> None:
        """Real API consumption, tracked independently of internal credits."""
        self.conn.execute(
            "UPDATE agents SET real_api_cost = real_api_cost + ?"
            " WHERE id = ?", (amount, agent_id))
        self.conn.commit()

    # --- executions ---
    def start_execution(self, agent_id: str, actor: str, minutes: int) -> dict:
        exec_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            "INSERT INTO executions (id, agent_id, actor, minutes, status,"
            " started_at) VALUES (?, ?, ?, ?, 'running', ?)",
            (exec_id, agent_id, actor, minutes, _now()))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM executions WHERE id = ?", (exec_id,)).fetchone()
        return dict(row)

    def end_execution(self, exec_id: str) -> None:
        self.conn.execute(
            "UPDATE executions SET status = 'finished', ended_at = ?"
            " WHERE id = ?", (_now(), exec_id))
        self.conn.commit()

    def count_running_executions(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM executions WHERE status = 'running'"
        ).fetchone()
        return int(row["n"])

    # --- audit log: append-only. No update/delete method exists. ---
    def append_audit(self, actor: str, action: str, target: str = "",
                     detail: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO audit_log (ts, actor, action, target, detail)"
            " VALUES (?, ?, ?, ?, ?)",
            (_now(), actor, action, target, detail))
        self.conn.commit()
        return cur.lastrowid

    def list_audit_log(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- meta / kill switch ---
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))
        self.conn.commit()

    # --- API keys (auth layer, Phase 2 step 1) ---
    # Only public_id + HMAC hash are ever stored. The raw key is never
    # persisted anywhere. See app/auth.py.
    def insert_api_key(self, public_id: str, owner: str,
                       key_hash: str) -> dict:
        self.conn.execute(
            "INSERT INTO api_keys (public_id, owner, key_hash, created_at,"
            " revoked) VALUES (?, ?, ?, ?, 0)",
            (public_id, owner, key_hash, _now()))
        self.conn.commit()
        return self.get_api_key(public_id)

    def get_api_key(self, public_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM api_keys WHERE public_id = ?",
            (public_id,)).fetchone()
        return dict(row) if row else None

    def list_api_keys(self, owner: str | None = None) -> list[dict]:
        if owner is None:
            rows = self.conn.execute(
                "SELECT * FROM api_keys ORDER BY created_at").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM api_keys WHERE owner = ? ORDER BY created_at",
                (owner,)).fetchall()
        return [dict(r) for r in rows]

    def count_api_keys(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM api_keys").fetchone()
        return int(row["n"])

    def revoke_api_key(self, public_id: str) -> None:
        self.conn.execute(
            "UPDATE api_keys SET revoked = 1, revoked_at = ?"
            " WHERE public_id = ?",
            (_now(), public_id))
        self.conn.commit()

    def get_or_create_server_secret(self) -> str:
        """Per-database server secret for HMAC key hashing.

        Generated once (32 bytes, hex) and stored in the meta table.
        LOCAL-MVP LIMITATION: a production server secret must live
        outside the database (env var / secret manager). Anyone with
        read access to this DB file can read the secret and forge key
        hashes — but they would already have the DB itself. Documented
        in docs/known-vulnerabilities.md.
        """
        existing = self.get_meta("server_secret")
        if existing:
            return existing
        import secrets as _secrets
        secret = _secrets.token_hex(32)
        self.set_meta("server_secret", secret)
        return secret
