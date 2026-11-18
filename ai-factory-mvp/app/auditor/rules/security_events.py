"""Phase 3 — global Auditor: SEC rules (observable security events).

These rules detect *successful* violations recorded in the log — which
should be impossible through the code paths, so their presence means
fabrication or a bug. Denied *attempts* are invisible by design
(LTP-022: the code raises before any audit call), so SEC-006 is
NOT VERIFIABLE (docs/auditor-architecture.md §7.2). See §6.5.
"""
from __future__ import annotations

from ... import config
from ..models import finding
from .credits import credit_006


def sec_001(snapshot: dict) -> dict:
    """No successful cross-user action: no execute_agent /
    execute_isolated / audit_agent entry has actor != agent owner
    and actor != 'lucas'. audit_global is excluded (not in the action
    set), so the audit's own event can never be mistaken for a
    cross-user audit action."""
    agents_by_id = {a["id"]: a for a in snapshot["agents"]}
    bad = []
    for e in snapshot["audit_log"]:
        if e["action"] not in ("execute_agent", "execute_isolated",
                               "audit_agent"):
            continue
        agent = agents_by_id.get(e["target"])
        if agent is None:
            continue  # owner unknowable; not this rule's claim
        if e["actor"] != agent["owner"] and e["actor"] != config.ADMIN_USER:
            bad.append(f"#{e['id']}")
    if bad:
        return finding("SEC-001", "FAIL", "critical",
                       "Successful cross-user action recorded",
                       sorted(set(bad)),
                       "Investigate immediately — the code paths forbid "
                       "this, so the entry means direct log fabrication, "
                       "a bug, or a bypass of the library.")
    return finding("SEC-001", "PASS", "low",
                   "No successful cross-user action recorded", [],
                   "No action needed.")


def sec_002(snapshot: dict) -> dict:
    """No kill_switch_on / kill_switch_off entry has actor != 'lucas'."""
    bad = [f"#{e['id']}" for e in snapshot["audit_log"]
           if e["action"] in ("kill_switch_on", "kill_switch_off")
           and e["actor"] != config.ADMIN_USER]
    if bad:
        return finding("SEC-002", "FAIL", "critical",
                       "Kill switch toggled by a non-admin",
                       sorted(set(bad)),
                       "Investigate immediately — only Lucas may toggle "
                       "the kill switch.")
    return finding("SEC-002", "PASS", "low",
                   "Kill switch was only toggled by the admin", [],
                   "No action needed.")


def sec_003(snapshot: dict) -> dict:
    """No create_api_key / revoke_api_key entry has actor != 'lucas'
    and actor != 'bootstrap' (bootstrap is the legitimate first-key
    path)."""
    bad = [f"#{e['id']}" for e in snapshot["audit_log"]
           if e["action"] in ("create_api_key", "revoke_api_key")
           and e["actor"] not in (config.ADMIN_USER, "bootstrap")]
    if bad:
        return finding("SEC-003", "FAIL", "critical",
                       "API key created or revoked by a non-admin",
                       sorted(set(bad)),
                       "Investigate immediately — only Lucas (or the "
                       "bootstrap first-key path) may manage API keys.")
    return finding("SEC-003", "PASS", "low",
                   "API keys were only managed by the admin", [],
                   "No action needed.")


def sec_004(snapshot: dict) -> dict:
    """Shared check with CREDIT-006: one check, one finding, filed
    under CREDIT-006 and referenced from the SEC section of the
    summary — not duplicated. This function returns the CREDIT-006
    finding itself, so the report holds one finding for the shared
    check (see rules/__init__.py and models.summarize)."""
    return credit_006(snapshot)


def sec_005(snapshot: dict) -> dict:
    """Unsafe-dev runs are visible: exactly one finding per audit
    summarising the count of execute_isolated entries whose detail
    contains 'unsafe_dev=True'. Zero -> PASS; >= 1 -> WARN (low), so
    it is never silent."""
    runs = [f"#{e['id']}" for e in snapshot["audit_log"]
            if e["action"] == "execute_isolated"
            and "unsafe_dev=True" in (e["detail"] or "")]
    if runs:
        return finding("SEC-005", "WARN", "low",
                       "Unisolated (unsafe_dev) execution runs recorded",
                       sorted(set(runs)),
                       "Review the listed runs — they executed without "
                       "the AppContainer isolation boundary.")
    return finding("SEC-005", "PASS", "low",
                   "No unisolated execution runs recorded", [],
                   "No action needed.")


def sec_006(snapshot: dict) -> dict:
    """Denied cross-user attempts in the log. NOT VERIFIABLE: denied
    attempts are never logged by design (LTP-022) — the code raises
    before any audit call, so there is nothing to count (§7.2)."""
    return finding(
        "SEC-006", "UNVERIFIED", "medium",
        "Cannot verify denied cross-user attempts from the log",
        [],
        "Denied attempts are never recorded by design, so the log "
        "contains only allowed events. Minimal schema change: a "
        "security_events table written on denial paths (see "
        "docs/auditor-architecture.md §7.2).")


def sec_007(snapshot: dict) -> dict:
    """Executions by suspended/killed agents. NOT VERIFIABLE exactly:
    the DB records the agent's *current* status, not its status at
    execution time (§7.5)."""
    return finding(
        "SEC-007", "UNVERIFIED", "medium",
        "Cannot verify no execution ran under a suspended/killed agent",
        [],
        "Agent status at execution time is not recorded — an agent "
        "suspended after executing is indistinguishable from one "
        "suspended before executing. Minimal schema change: "
        "agent_status_at_start on executions (see "
        "docs/auditor-architecture.md §7.5).")
