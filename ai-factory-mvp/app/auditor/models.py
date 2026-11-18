"""Phase 3 — global Auditor: report models.

Plain-dict Finding / Report shapes (see docs/auditor-architecture.md
§8). No business logic here, only shape validation and summary
computation. Stdlib only.
"""
from __future__ import annotations

from .. import config

RULESET_VERSION = config.AUDIT_RULESET_VERSION

STATUSES = ("PASS", "WARN", "FAIL", "UNVERIFIED")
SEVERITIES = ("low", "medium", "high", "critical")

DISCLAIMER = (
    "A report with all PASS does not mean the system is secure — it means "
    "the implemented checks found no violations within the examined scope."
)

# Complete audit-action vocabulary (docs/auditor-architecture.md §1.3),
# including the Phase 3 events audit_global and
# audit_global_rate_limited. LOG-004 checks every audit_log.action
# against this list.
AUDIT_ACTION_VOCABULARY = frozenset({
    "create_agent", "execute_agent", "execute_isolated",
    "execute_refused", "execute_failed", "audit_agent",
    "audit_global", "audit_global_rate_limited",
    "credit_charge", "credit_refund", "credit_bypass", "admin_execute",
    "kill_switch_on", "kill_switch_off", "suspend_agent", "kill_agent",
    "create_api_key", "revoke_api_key", "real_api_cost",
})


def finding(rule_id: str, status: str, severity: str, title: str,
            evidence_ids: list, recommendation: str) -> dict:
    """One finding. Every registered rule produces exactly one
    finding, including PASS findings (single rule contract, LTP-038
    correction C). Titles carry no data values; specifics live in
    evidence_ids (identifiers only — never a key, hash, secret, or
    personal data beyond the owner string the admin already sees)."""
    assert status in STATUSES, f"bad finding status: {status!r}"
    assert severity in SEVERITIES, f"bad finding severity: {severity!r}"
    return {
        "rule_id": rule_id,
        "status": status,
        "severity": severity,
        "title": title,
        "evidence_ids": [str(e) for e in evidence_ids],
        "recommendation": recommendation,
    }


def overall_status(findings: list[dict]) -> str:
    """'fail' if any FAIL, else 'warn' if any WARN, else 'pass'.
    UNVERIFIED findings do not change the overall status, but they
    are counted separately and the disclaimer applies with extra
    force when any UNVERIFIED exists."""
    statuses = {f["status"] for f in findings}
    if "FAIL" in statuses:
        return "fail"
    if "WARN" in statuses:
        return "warn"
    return "pass"


def summarize(findings: list[dict], scope: str, evaluated_at: str,
              policy: dict, snapshot_counts: dict) -> dict:
    """Summary block stored on the report. counts_by_status sums over
    all findings (rules_run == len(findings) == 38 in Phase 3);
    counts_by_severity counts every finding by its severity — PASS
    findings carry severity 'low' (there is no issue to rank)."""
    counts_by_status = {s: 0 for s in STATUSES}
    counts_by_severity = {s: 0 for s in SEVERITIES}
    for f in findings:
        counts_by_status[f["status"]] += 1
        counts_by_severity[f["severity"]] += 1
    summary = {
        "rules_run": len(findings),
        "counts_by_status": counts_by_status,
        "counts_by_severity": counts_by_severity,
        "scope": scope,
        "evaluated_at": evaluated_at,
        "policy": dict(policy),
        "snapshot": dict(snapshot_counts),
        "disclaimer": DISCLAIMER,
    }
    return summary
