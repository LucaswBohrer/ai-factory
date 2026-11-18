"""Phase 3 — global Auditor: rule registry and shared helpers.

Single rule contract (LTP-038 correction C): every registered rule
produces exactly one finding, including PASS findings; a rule that
does not apply produces one UNVERIFIED finding with an explicit
reason — never None, never silent. rules_run always equals
len(RULES) = 38 in Phase 3.

Note on SEC-004 / CREDIT-006 (docs/auditor-architecture.md §6.5):
one check, one finding, filed under CREDIT-006. The registry maps
both ids to the shared implementation, so the findings list holds 38
entries with the CREDIT-006 finding appearing at both positions;
the summary records the alias in "shared_findings". Distinct rule
ids in a report are therefore 37.
"""
from __future__ import annotations

from ..models import RULESET_VERSION
from ._common import parse_ts
from . import api_keys, audit_log, credits, permissions, security_events

__all__ = ["RULES", "RULE_IDS", "RULESET_VERSION", "run_all", "parse_ts"]


# Registry order is the report order: PERM, CREDIT, LOG, KEY, SEC.
RULES: list[tuple[str, object]] = [
    ("PERM-001", permissions.perm_001),
    ("PERM-002", permissions.perm_002),
    ("PERM-003", permissions.perm_003),
    ("PERM-004", permissions.perm_004),
    ("PERM-005", permissions.perm_005),
    ("PERM-006", permissions.perm_006),
    ("PERM-007", permissions.perm_007),
    ("PERM-008", permissions.perm_008),
    ("PERM-009", permissions.perm_009),
    ("PERM-010", permissions.perm_010),
    ("CREDIT-001", credits.credit_001),
    ("CREDIT-002", credits.credit_002),
    ("CREDIT-003", credits.credit_003),
    ("CREDIT-004", credits.credit_004),
    ("CREDIT-005", credits.credit_005),
    ("CREDIT-006", credits.credit_006),
    ("CREDIT-007", credits.credit_007),
    ("CREDIT-008", credits.credit_008),
    ("CREDIT-009", credits.credit_009),
    ("LOG-001", audit_log.log_001),
    ("LOG-002", audit_log.log_002),
    ("LOG-003", audit_log.log_003),
    ("LOG-004", audit_log.log_004),
    ("LOG-005", audit_log.log_005),
    ("LOG-006", audit_log.log_006),
    ("KEY-001", api_keys.key_001),
    ("KEY-002", api_keys.key_002),
    ("KEY-003", api_keys.key_003),
    ("KEY-004", api_keys.key_004),
    ("KEY-005", api_keys.key_005),
    ("KEY-006", api_keys.key_006),
    ("SEC-001", security_events.sec_001),
    ("SEC-002", security_events.sec_002),
    ("SEC-003", security_events.sec_003),
    ("SEC-004", security_events.sec_004),
    ("SEC-005", security_events.sec_005),
    ("SEC-006", security_events.sec_006),
    ("SEC-007", security_events.sec_007),
]

RULE_IDS = [rule_id for rule_id, _ in RULES]

# Shared-check aliases: rule id -> the rule id whose finding it shares.
SHARED_FINDINGS = {"SEC-004": "CREDIT-006"}


def run_all(snapshot: dict) -> list[dict]:
    """Run every registered rule against the snapshot, in registry
    order. Returns exactly one finding per registered rule (38 in
    Phase 3). Rules are pure functions of the snapshot: no side
    effects, no I/O, no LLM calls."""
    findings = []
    for rule_id, fn in RULES:
        result = fn(snapshot)
        assert result is not None, f"rule {rule_id} returned None"
        assert result["rule_id"] == rule_id or (
            rule_id in SHARED_FINDINGS
            and result["rule_id"] == SHARED_FINDINGS[rule_id]
        ), (f"rule {rule_id} produced finding for "
            f"{result['rule_id']!r}")
        findings.append(result)
    return findings
