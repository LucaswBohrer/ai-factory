"""Phase 3 — global Auditor: LOG rules (audit-log integrity).

Internal consistency only. Anything about "the log was not tampered
with" is UNVERIFIED by design in Phase 3 (docs/auditor-architecture.md
§3.2, §7.3): triggers block UPDATE/DELETE through SQLite but not
direct file edits, trigger drops, INSERTs of fabricated rows, or
whole-DB replacement. See §6.3.
"""
from __future__ import annotations

from ..models import AUDIT_ACTION_VOCABULARY, finding
from ._common import parse_ts


def _ordered_log(snapshot: dict) -> list[dict]:
    return sorted(snapshot["audit_log"], key=lambda e: int(e["id"]))


def log_001(snapshot: dict) -> dict:
    """ID sequence: audit_log.id strictly increasing with no gaps
    when read in insertion order. Gaps are suspicious, never proof
    of deletion (AUTOINCREMENT can skip on rolled-back transactions)."""
    entries = _ordered_log(snapshot)
    gaps = [f"#{entries[i]['id']}->#{entries[i + 1]['id']}"
            for i in range(len(entries) - 1)
            if int(entries[i + 1]["id"]) != int(entries[i]["id"]) + 1]
    if gaps:
        return finding("LOG-001", "WARN", "low",
                       "Gap in the audit-log id sequence",
                       gaps,
                       "Investigate — a gap can come from a rolled-back "
                       "transaction, but it is never proof of deletion "
                       "either way.")
    return finding("LOG-001", "PASS", "low",
                   "Audit-log id sequence has no gaps", [],
                   "No action needed.")


def log_002(snapshot: dict) -> dict:
    """ID uniqueness: no duplicate id values (impossible through
    SQLite PRIMARY KEY — its presence means the table was rebuilt)."""
    ids = [int(e["id"]) for e in snapshot["audit_log"]]
    if len(ids) != len(set(ids)):
        dupes = sorted({f"#{i}" for i in ids if ids.count(i) > 1})
        return finding("LOG-002", "FAIL", "critical",
                       "Duplicate id in the audit log",
                       dupes,
                       "Investigate immediately — duplicate ids are "
                       "impossible through SQLite, so the table was "
                       "rebuilt.")
    return finding("LOG-002", "PASS", "low",
                   "Audit-log ids are unique", [],
                   "No action needed.")


def log_003(snapshot: dict) -> dict:
    """Timestamp order: ts is non-decreasing when rows are ordered by
    id. Wall-clock ts can go backwards under clock adjustment, so a
    decrease is a data-quality WARN, not tamper proof."""
    entries = _ordered_log(snapshot)
    bad = []
    for prev, cur in zip(entries, entries[1:]):
        p, c = parse_ts(prev["ts"]), parse_ts(cur["ts"])
        if p is not None and c is not None:
            if c < p:
                bad.append(f"#{cur['id']}")
        elif str(cur["ts"]) < str(prev["ts"]):
            bad.append(f"#{cur['id']}")
    if bad:
        return finding("LOG-003", "WARN", "low",
                       "Audit-log timestamps decrease in id order",
                       sorted(set(bad)),
                       "Investigate — wall-clock timestamps can go "
                       "backwards under clock adjustment, so this is a "
                       "data-quality warning, not proof of tampering.")
    return finding("LOG-003", "PASS", "low",
                   "Audit-log timestamps are non-decreasing", [],
                   "No action needed.")


def log_004(snapshot: dict) -> dict:
    """Action vocabulary: every action is in the §1.3 list (including
    audit_global and audit_global_rate_limited)."""
    bad = sorted({e["action"] for e in snapshot["audit_log"]
                  if e["action"] not in AUDIT_ACTION_VOCABULARY})
    if bad:
        return finding("LOG-004", "WARN", "medium",
                       "Unknown action in the audit log",
                       bad,
                       "Investigate — either code added an action without "
                       "updating the inventory, or a row was fabricated.")
    return finding("LOG-004", "PASS", "low",
                   "All audit-log actions are in the vocabulary", [],
                   "No action needed.")


def log_005(snapshot: dict) -> dict:
    """Tamper-evidence: the log has not been altered since the events
    were written. Always UNVERIFIED in Phase 3 — no hash chain, no
    signature, no external anchor exists (§7.3). A PASS here would
    be a lie."""
    return finding(
        "LOG-005", "UNVERIFIED", "medium",
        "Cannot verify the audit log was not tampered with",
        [],
        "No hash chain, signature, or external anchor exists; "
        "triggers block UPDATE/DELETE through SQLite but not direct "
        "file edits, trigger drops, fabricated INSERTs, or whole-DB "
        "replacement. Minimal schema change: hash-chain columns "
        "(see docs/auditor-architecture.md §7.3).")


def log_006(snapshot: dict) -> dict:
    """Completeness vs. expected events: for every agents row, >= 1
    create_agent entry with target = agent id exists."""
    created = {e["target"] for e in snapshot["audit_log"]
               if e["action"] == "create_agent"}
    missing = [a["id"] for a in snapshot["agents"]
               if a["id"] not in created]
    if missing:
        return finding("LOG-006", "WARN", "medium",
                       "Agent without a create_agent log entry",
                       sorted(missing),
                       "Investigate — the agent row was inserted without "
                       "the Factory path (direct insert or log loss).")
    return finding("LOG-006", "PASS", "low",
                   "Every agent has a create_agent log entry", [],
                   "No action needed.")
