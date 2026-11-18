"""Phase 3 — global Auditor: KEY rules (API key hygiene).

Key age policy is computed from created_at only — there is no
last_used_at, no expiry, no usage counter (§5). An old key is a
policy alert, NOT proof of compromise (LTP-036 correction C).
See docs/auditor-architecture.md §6.4.
"""
from __future__ import annotations

from ... import config
from ..models import finding
from ._common import parse_ts

_HEX = set("0123456789abcdefABCDEF")


def key_001(snapshot: dict) -> dict:
    """Revocation consistency: revoked = 1 iff revoked_at IS NOT NULL."""
    bad = [k["public_id"] for k in snapshot["api_keys"]
           if bool(int(k["revoked"])) != (k.get("revoked_at") is not None)]
    if bad:
        return finding("KEY-001", "FAIL", "high",
                       "API key revocation state is inconsistent",
                       sorted(bad),
                       "Investigate — revoke_api_key always sets both "
                       "fields, so inconsistency means direct DB "
                       "manipulation.")
    return finding("KEY-001", "PASS", "low",
                   "API key revocation states are consistent", [],
                   "No action needed.")


def key_002(snapshot: dict) -> dict:
    """Key age policy: no active key older than the policy
    (default 180 days, stored in the report as
    policy.key_max_age_days). Age is computed vs evaluated_at, never
    the wall clock at rule time (LTP-038 correction D)."""
    max_age_days = int(snapshot["policy"].get(
        "key_max_age_days", config.AUDIT_KEY_MAX_AGE_DAYS))
    evaluated_at = snapshot["evaluated_at"]
    old = []
    for k in snapshot["api_keys"]:
        if int(k["revoked"]) != 0:
            continue
        created = parse_ts(k["created_at"])
        if created is None:
            continue  # age uncomputable; not this rule's claim
        if (evaluated_at - created).total_seconds() > max_age_days * 86400:
            old.append(k["public_id"])
    if old:
        return finding(
            "KEY-002", "WARN", "low",
            "Active API key older than the age policy",
            sorted(old),
            f"Review the listed key(s): older than {max_age_days} days "
            "is a policy alert, NOT proof of compromise. Rotate or "
            "revoke keys that are no longer needed.")
    return finding("KEY-002", "PASS", "low",
                   "No active API key is older than the age policy", [],
                   "No action needed.")


def key_003(snapshot: dict) -> dict:
    """Admin key count (LTP-038 correction G): reports the number of
    active keys for owner 'lucas' and the expected policy (1 active
    admin key in normal operation). Exactly 1 is a policy
    expectation, NOT an absolute security requirement. 0 active keys
    is a lockout warning; > 1 is a hygiene alert. No FAIL outcome."""
    active_admin = [k["public_id"] for k in snapshot["api_keys"]
                    if k["owner"] == config.ADMIN_USER
                    and int(k["revoked"]) == 0]
    n = len(active_admin)
    if n == 1:
        return finding("KEY-003", "PASS", "low",
                       "Exactly one active admin API key", active_admin,
                       "No action needed.")
    if n == 0:
        return finding(
            "KEY-003", "WARN", "high",
            "No active admin API key",
            [],
            "0 active admin keys found; expected policy is 1 active "
            "admin key in normal operation. The admin is locked out of "
            "key-based access — create a new admin key.")
    return finding(
        "KEY-003", "WARN", "medium",
        "More than one active admin API key",
        sorted(active_admin),
        f"{n} active admin keys found; expected policy is 1 active "
        "admin key in normal operation. This is a hygiene alert — "
        "review the listed keys; it does not by itself prove "
        "compromise.")


def key_004(snapshot: dict) -> dict:
    """No duplicate public_id (impossible through PRIMARY KEY —
    its presence means the table was rebuilt)."""
    ids = [k["public_id"] for k in snapshot["api_keys"]]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        return finding("KEY-004", "FAIL", "critical",
                       "Duplicate API key public_id",
                       dupes,
                       "Investigate immediately — duplicate ids are "
                       "impossible through SQLite, so the table was "
                       "rebuilt.")
    return finding("KEY-004", "PASS", "low",
                   "API key public_ids are unique", [],
                   "No action needed.")


def key_005(snapshot: dict) -> dict:
    """Revoked key used after revocation. NOT VERIFIABLE: failed
    authentications are never logged; a revoked key fails in
    Auth.authenticate before any audit call (§7.2)."""
    return finding(
        "KEY-005", "UNVERIFIED", "medium",
        "Cannot verify a revoked key was never used after revocation",
        [],
        "Failed authentications are never recorded, so use of a "
        "revoked key leaves no trace. Minimal schema change: a "
        "security_events table written on denial paths (see "
        "docs/auditor-architecture.md §7.2).")


def key_006(snapshot: dict) -> dict:
    """Server secret present: meta.server_secret exists and is a
    64-hex-char string."""
    secret = snapshot["meta"].get("server_secret")
    if secret is None:
        return finding("KEY-006", "WARN", "low",
                       "Server secret is missing",
                       [],
                       "It will be generated on next use — but its "
                       "absence in a long-lived DB means keys were never "
                       "created or the meta row was deleted.")
    if len(secret) == 64 and all(c in _HEX for c in secret):
        return finding("KEY-006", "PASS", "low",
                       "Server secret is present and well-formed", [],
                       "No action needed.")
    return finding("KEY-006", "FAIL", "high",
                   "Server secret is malformed",
                   [],
                   "Investigate — the value in meta is not a 64-hex-char "
                   "string, so key hashing is broken.")
