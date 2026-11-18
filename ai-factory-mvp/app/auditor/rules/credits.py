"""Phase 3 — global Auditor: CREDIT rules (credit ledger reconciliation).

There is NO ledger table: charges exist only as free-text audit-log
details and balance deltas (docs/auditor-architecture.md §2.1, §7.1).
Rules here check the structured invariants that do exist, and report
the rest as UNVERIFIED — they never pretend to reconcile what cannot
be reconciled. See docs/auditor-architecture.md §6.2.
"""
from __future__ import annotations

from ... import config
from ..models import finding
from ._common import parse_ts


def credit_001(snapshot: dict) -> dict:
    """No agent exceeds its cap: credits_used <= max_credits."""
    bad = [a["id"] for a in snapshot["agents"]
           if int(a["credits_used"]) > int(a["max_credits"])]
    if bad:
        return finding("CREDIT-001", "FAIL", "high",
                       "Agent exceeds its credit cap",
                       sorted(bad),
                       "Investigate — the cap is enforced before charging, "
                       "so an excess means direct DB manipulation or a bug.")
    return finding("CREDIT-001", "PASS", "low",
                   "No agent exceeds its credit cap", [],
                   "No action needed.")


def credit_002(snapshot: dict) -> dict:
    """No agent at/above 80% of its cap (skip when max_credits = 0)."""
    near = [a["id"] for a in snapshot["agents"]
            if int(a["max_credits"]) > 0
            and int(a["credits_used"]) / int(a["max_credits"]) >= 0.8]
    if near:
        return finding("CREDIT-002", "WARN", "low",
                       "Agent at or above 80% of its credit cap",
                       sorted(near),
                       "Consider raising the cap or reviewing the agent's "
                       "consumption.")
    return finding("CREDIT-002", "PASS", "low",
                   "No agent is at or above 80% of its credit cap", [],
                   "No action needed.")


def credit_003(snapshot: dict) -> dict:
    """No negative balances: credits_used >= 0 and
    users.credits_balance >= 0."""
    bad = [a["id"] for a in snapshot["agents"]
           if int(a["credits_used"]) < 0]
    bad += [u["owner"] for u in snapshot["users"]
            if int(u["credits_balance"]) < 0]
    if bad:
        return finding("CREDIT-003", "FAIL", "high",
                       "Negative credit balance found",
                       sorted(set(bad)),
                       "Investigate — refund() does not guard against "
                       "over-refund, so a negative value means a refund "
                       "larger than the charged amount (or direct DB "
                       "manipulation).")
    return finding("CREDIT-003", "PASS", "low",
                   "No negative credit balances", [],
                   "No action needed.")


def credit_004(snapshot: dict) -> dict:
    """Exact charge <-> execution reconciliation. NOT VERIFIABLE: no
    ledger table, no charge id, no amount column; charges exist only
    as free-text detail strings (§7.1)."""
    return finding(
        "CREDIT-004", "UNVERIFIED", "medium",
        "Cannot verify exact charge-to-execution reconciliation",
        [],
        "No ledger table exists: charges are free-text audit-log "
        "details with no charge id, amount column, or link to the "
        "execution they paid for. Minimal schema change: a ledger "
        "table written atomically with each charge/refund (see "
        "docs/auditor-architecture.md §7.1).")


def _ts_le(a, b) -> bool:
    """True if timestamp a <= b. Parsed datetimes when both parse,
    otherwise ISO-string comparison (same format, UTC)."""
    da, db = parse_ts(a), parse_ts(b)
    if da is not None and db is not None:
        return da <= db
    return str(a) <= str(b)


def credit_005(snapshot: dict) -> dict:
    """Refund <-> refused/failed execution, APPROXIMATE (LTP-038
    correction F): matching is by agent + time, never by id. No
    candidate -> WARN; one candidate -> WARN (approximate, never
    'reconciled'); several candidates -> UNVERIFIED (ambiguous)."""
    refunds = [e for e in snapshot["audit_log"]
               if e["action"] == "credit_refund"]
    if not refunds:
        return finding("CREDIT-005", "PASS", "low",
                       "No refunds to reconcile", [],
                       "No action needed.")
    executions = snapshot["executions"]
    no_candidate: list[str] = []
    one_candidate: list[str] = []
    ambiguous: list[str] = []
    evidence: list[str] = []
    for r in refunds:
        target = r["target"]
        candidates = [
            x for x in executions
            if x["agent_id"] == target
            and x["status"] in ("refused", "failed")
            and x.get("ended_at")
            and _ts_le(x["ended_at"], r["ts"])
        ] if target else []
        evidence.append(f"#{r['id']}")
        if len(candidates) == 0:
            no_candidate.append(f"#{r['id']}")
        elif len(candidates) == 1:
            one_candidate.append(f"#{r['id']}")
            evidence.append(candidates[0]["id"])
        else:
            ambiguous.append(f"#{r['id']}")
            evidence.extend(c["id"] for c in candidates)
    if ambiguous:
        return finding(
            "CREDIT-005", "UNVERIFIED", "medium",
            "Refund matches several refused/failed executions — "
            "ambiguous",
            sorted(set(evidence)),
            "Investigate manually: matching by agent + time is "
            "ambiguous when several candidate executions exist. A "
            "refund is never declared reconciled from time proximity "
            "alone.")
    if no_candidate:
        return finding(
            "CREDIT-005", "WARN", "medium",
            "Refund with no matching refused/failed execution",
            sorted(set(no_candidate)),
            "Investigate the unexplained refund(s).")
    return finding(
        "CREDIT-005", "WARN", "medium",
        "Refund matched approximately to one refused/failed execution",
        sorted(set(evidence)),
        "Association is by agent + time only — the refund is NOT "
        "declared reconciled. Investigate if the pairing looks wrong.")


def _admin_bypass_missing(snapshot: dict) -> list[str]:
    """Shared internal observation for CREDIT-006 and SEC-004 (see
    docs/auditor-architecture.md §6.2/§6.5): the admin-execution /
    credit_bypass entries that do NOT have a matching bypass entry.
    Returns the sorted evidence ids (execution ids and '#<log id>'
    strings). An empty list means every admin execution matches.
    This is the observation only — each rule builds its OWN finding
    from it (own rule_id, title, recommendation)."""
    log = snapshot["audit_log"]
    bypass_targets = {e["target"] for e in log
                      if e["action"] == "credit_bypass" and e["target"]}
    agents_by_id = {a["id"]: a for a in snapshot["agents"]}
    missing: list[str] = []
    for x in snapshot["executions"]:
        if x["actor"] == config.ADMIN_USER:
            agent = agents_by_id.get(x["agent_id"])
            if agent is None:
                continue  # owner unknowable; not this rule's claim
            if (agent["owner"] != config.ADMIN_USER
                    and x["agent_id"] not in bypass_targets):
                missing.append(x["id"])
    for e in log:
        if (e["action"] == "admin_execute"
                and e["target"] not in bypass_targets):
            missing.append(f"#{e['id']}")
    return sorted(set(missing))


def credit_006(snapshot: dict) -> dict:
    """Admin bypass accounting: for every executions row with
    actor = 'lucas' and agent owner != 'lucas', a credit_bypass entry
    with the same target exists; and for every admin_execute entry a
    credit_bypass entry with the same target exists. Shares the
    internal observation with SEC-004 (the security view of the same
    check) but produces its OWN finding — see
    docs/auditor-architecture.md §6.5."""
    missing = _admin_bypass_missing(snapshot)
    if missing:
        return finding("CREDIT-006", "WARN", "medium",
                       "Admin execution without a matching credit_bypass "
                       "entry",
                       sorted(set(missing)),
                       "Investigate — every admin execution of another "
                       "user's agent must have a bypass entry for the "
                       "same agent.")
    return finding("CREDIT-006", "PASS", "low",
                   "Admin bypass entries match admin executions", [],
                   "No action needed.")


def credit_007(snapshot: dict) -> dict:
    """Stale running executions: no executions row has status
    'running' with started_at older than 1 hour (vs evaluated_at)."""
    evaluated_at = snapshot["evaluated_at"]
    stale = []
    for x in snapshot["executions"]:
        if x["status"] != "running":
            continue
        started = parse_ts(x["started_at"])
        if started is None:
            continue  # age uncomputable; not this rule's claim
        if (evaluated_at - started).total_seconds() > 3600:
            stale.append(x["id"])
    if stale:
        return finding("CREDIT-007", "FAIL", "high",
                       "Stale running execution found",
                       sorted(stale),
                       "Investigate — a stale 'running' row poisons the "
                       "concurrency cap (the Phase 2 step-2 bug class).")
    return finding("CREDIT-007", "PASS", "low",
                   "No stale running executions", [],
                   "No action needed.")


def credit_008(snapshot: dict) -> dict:
    """Real API cost is accounted: for every agent with
    real_api_cost > 0, >= 1 real_api_cost audit entry with target =
    agent id exists."""
    logged = {e["target"] for e in snapshot["audit_log"]
              if e["action"] == "real_api_cost"}
    bad = [a["id"] for a in snapshot["agents"]
           if float(a["real_api_cost"]) > 0 and a["id"] not in logged]
    if bad:
        return finding("CREDIT-008", "WARN", "low",
                       "Agent has real API cost but no accounting entry",
                       sorted(bad),
                       "Investigate — consumption was recorded on the "
                       "agent row without a matching audit entry.")
    return finding("CREDIT-008", "PASS", "low",
                   "Real API cost is accounted for all agents", [],
                   "No action needed.")


def credit_009(snapshot: dict) -> dict:
    """Creation charge attribution. NOT VERIFIABLE exactly: the
    creation charge is logged with agent_id=None (target = ''), so
    its only link is owner + timestamp proximity (§7.1)."""
    return finding(
        "CREDIT-009", "UNVERIFIED", "medium",
        "Cannot verify creation-charge attribution exactly",
        [],
        "The creation charge is logged with agent_id=None, so the "
        "audit entry has an empty target and no link to the agent "
        "row. Minimal schema change: a ledger table with ref_id "
        "(see docs/auditor-architecture.md §7.1).")
