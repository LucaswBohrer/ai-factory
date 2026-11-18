"""Phase 3 — global Auditor: service layer.

Entry point: run_global_audit(store, auth, raw_key, now=None).

Ordering and transactions (docs/auditor-architecture.md §8.4,
LTP-038 corrections B and E):
  1. Authenticate + authorize IN THE SERVICE (Auth.authenticate +
     Auth.require_admin on the raw key — the CLI passes the raw key
     through and never resolves an owner string first; the service
     never accepts a bare actor string). Then the rate-limit check.
  2. ONE SQLite read transaction: raw SELECTs into plain-dict
     snapshot structures, evaluated_at captured with the snapshot,
     transaction closed before evaluation. No Store method that
     writes/commits may be called while it is open.
  3. All 38 rules run against the snapshot — outside any
     transaction, deterministic, side-effect-free.
  4. Report row + audit_global log entry in ONE write transaction
     (both or neither). The read transaction is already closed.

Cost: FREE for Lucas — no credit charge, no credit_bypass entry.
The run is recorded by the audit_global log entry itself.

Rate limit (LTP-038 §3B): configurable minimum interval between
*successful* global audits, enforced here (not only in the CLI).
The clock is the latest persisted audit_reports.created_at. A failed
audit never updates the clock and never blocks a re-audit. A refusal
is logged as one audit_global_rate_limited entry.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .. import config
from ..auth import Auth
from ..store import Store, _row_to_agent
from .models import RULESET_VERSION, overall_status, summarize
from .rules import SHARED_FINDINGS, parse_ts, run_all


class RateLimitRefused(Exception):
    """A global audit was refused by the service-layer rate limit.
    Carries retry_after_seconds. No report row is created for the
    refusal; exactly one audit_global_rate_limited log entry is."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(
            "global audit rate limit: retry after "
            f"{retry_after_seconds}s")
        self.retry_after_seconds = int(retry_after_seconds)


class AuditService:
    def __init__(self, store: Store, auth: Auth, now=None,
                 min_interval_seconds: int | None = None):
        """``now`` is an injectable clock: a datetime, or a callable
        returning one (tests pass a fixed datetime; default is the
        real UTC clock). ``min_interval_seconds`` overrides
        config.AUDIT_GLOBAL_MIN_INTERVAL_SECONDS (tests use 0 to
        allow back-to-back runs)."""
        self.store = store
        self.auth = auth
        self._now = now
        self.min_interval_seconds = (
            config.AUDIT_GLOBAL_MIN_INTERVAL_SECONDS
            if min_interval_seconds is None else int(min_interval_seconds))

    # --- clock ---
    def _resolve_now(self, now=None) -> datetime:
        candidate = now if now is not None else self._now
        if candidate is None:
            return datetime.now(timezone.utc)
        dt = candidate() if callable(candidate) else candidate
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    # --- entry point ---
    def run_global_audit(self, raw_key, now=None) -> dict:
        now_dt = self._resolve_now(now)
        # 1. Service-layer authentication + authorization. A declared
        # identity claiming to be Lucas, without a valid raw key,
        # fails here — before any read, report, or log entry.
        owner = self.auth.authenticate(raw_key)
        self.auth.require_admin(owner)
        # Rate limit, clocked from the latest successful audit.
        latest = self.store.latest_audit_report()
        if latest is not None:
            last = parse_ts(latest["created_at"])
            if last is not None:
                elapsed = (now_dt - last).total_seconds()
                if elapsed < self.min_interval_seconds:
                    retry = int(self.min_interval_seconds - elapsed) + 1
                    self.store.append_audit(
                        owner, "audit_global_rate_limited", "",
                        "global audit refused by rate limit;"
                        f" retry after {retry}s")
                    raise RateLimitRefused(retry)
        # 2. Consistent snapshot in ONE read transaction.
        snapshot = self._take_snapshot(now_dt)
        # 3. Rules, outside any transaction.
        findings = run_all(snapshot)
        # 4. Report + audit_global event in ONE write transaction.
        report = self._build_report(owner, now_dt, snapshot, findings)
        counts = report["summary"]["counts_by_status"]
        detail = (
            "global audit complete: "
            f"{counts['PASS']} PASS, {counts['WARN']} WARN, "
            f"{counts['FAIL']} FAIL, {counts['UNVERIFIED']} UNVERIFIED; "
            f"ruleset {RULESET_VERSION}")
        self.store.persist_global_audit(report, owner, detail)
        return report

    # --- snapshot ---
    def _take_snapshot(self, now_dt: datetime) -> dict:
        """Read every table the rules need inside ONE SQLite read
        transaction, then close it. Raw SELECTs only — no Store
        method that writes or commits may run while it is open."""
        conn = self.store.conn
        conn.execute("BEGIN")
        try:
            users = [dict(r) for r in conn.execute("SELECT * FROM users")]
            agents = [_row_to_agent(r)
                      for r in conn.execute("SELECT * FROM agents")]
            executions = [dict(r)
                          for r in conn.execute("SELECT * FROM executions")]
            audit_log = [dict(r) for r in conn.execute(
                "SELECT * FROM audit_log ORDER BY id")]
            api_keys = [dict(r)
                        for r in conn.execute("SELECT * FROM api_keys")]
            meta = {r["key"]: r["value"]
                    for r in conn.execute("SELECT * FROM meta")}
        finally:
            conn.commit()  # read-only: ends the transaction
        policy = {
            "key_max_age_days": config.AUDIT_KEY_MAX_AGE_DAYS,
            "audit_global_min_interval_seconds": self.min_interval_seconds,
        }
        return {
            "users": users,
            "agents": agents,
            "executions": executions,
            "audit_log": audit_log,
            "api_keys": api_keys,
            "meta": meta,
            "evaluated_at": now_dt,
            "policy": policy,
        }

    # --- report ---
    def _build_report(self, owner: str, now_dt: datetime,
                      snapshot: dict, findings: list[dict]) -> dict:
        evaluated_at = now_dt.isoformat()
        policy = dict(snapshot["policy"])
        snapshot_counts = {
            "users": len(snapshot["users"]),
            "agents": len(snapshot["agents"]),
            "executions": len(snapshot["executions"]),
            "audit_log_rows": len(snapshot["audit_log"]),
            "api_keys": len(snapshot["api_keys"]),
        }
        summary = summarize(findings, "global", evaluated_at, policy,
                            snapshot_counts,
                            shared_findings=dict(SHARED_FINDINGS))
        return {
            "report_id": uuid.uuid4().hex[:12],
            "requested_by": owner,
            "scope": "global",
            "created_at": evaluated_at,
            "evaluated_at": evaluated_at,
            "policy": policy,
            "ruleset_version": RULESET_VERSION,
            "status": overall_status(findings),
            "findings": findings,
            "summary": summary,
        }


def run_global_audit(store: Store, auth: Auth, raw_key,
                     now=None) -> dict:
    """Module-level entry point (signature per
    docs/auditor-architecture.md §8.4)."""
    return AuditService(store, auth, now=now).run_global_audit(raw_key)
