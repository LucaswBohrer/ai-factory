# Phase 3 — Closure Report (Auditor: first real use)

**Date:** 2026-11-18
**Status:** CLOSED — build 1 definitively approved (LTP-044, ChatGPT)
**Build commit:** `80aa30e` (blocker fix: SEC-004/CREDIT-006 separate
findings, 38 distinct rule ids)
**This document:** `docs/phase-3-closure.md` (closure commit recorded
below)

## Decision

Phase 3 is closed. The Auditor is a deterministic integrity,
consistency and observable-security-events checker — NOT a general
security auditor and NOT a certifier. A report with all PASS does not
mean the system is secure; it means the implemented checks found no
violations within the examined scope.

## Status table

| Area | State |
|---|---|
| Auditor (38 deterministic rules, v3.0.0) | Implemented and tested |
| Global audit (Lucas admin only) | Implemented, service-layer auth |
| Individual audit | Out of the phase 3 MVP |
| Agent audit | None in this phase |
| Report persistence (`audit_reports`) | Implemented, UPDATE/DELETE triggers |
| `audit_global` event | Implemented, distinct from `audit_agent` |
| Rate limit (60 s, service layer) | Implemented |
| Arbitrary code execution | BLOCKED |
| LLM calls | None |
| Public deploy / payments / recursion | Out of scope |

## What was implemented

1. **Package `app/auditor/`** — service (`run_global_audit(store,
   auth, raw_key, now=None)`), models, rules in five modules
   (permissions, credits, audit_log, api_keys, security_events).
   Rules are deterministic functions, side-effect-free, no LLM.
2. **38 rules** (PERM 10, CREDIT 9, LOG 6, KEY 6, SEC 7), each
   producing exactly one finding with its own rule_id, title,
   recommendation and evidence. `rules_run` = 38.
3. **Service-layer auth:** the service authenticates the raw key
   itself via `Auth.authenticate` + `require_admin`; the CLI passes
   the raw key through and never resolves an owner string.
4. **Consistent snapshot:** one SQLite read transaction; rules run
   outside any transaction; report + `audit_global` event in one
   write transaction (both or neither).
5. **Rate limit:** 60 s configurable, service layer, clocked from the
   latest successful audit; refusal logs `audit_global_rate_limited`
   without a report, without consuming quota, without blocking retry
   after the interval; a failed evaluation never blocks an immediate
   re-audit.
6. **Global audit is free for Lucas** (no credits, no fictitious
   charge); the admin execution is logged.
7. **`app/auditor.py` → `app/agent_auditor.py`** (pure move);
   compatibility preserved via re-export in `app/auditor/__init__.py`.
8. **Reports carry** `evaluated_at` + `policy` + `ruleset_version`
   (`3.0.0`); tests use an injectable clock / fixed date.

## Test results (verified by Theo)

```
python3 -m unittest discover -s tests
Ran 159 tests — OK (skipped=11)
```

- 159 = 88 baseline (77 pass + 11 Windows-only skipped) + 66 new
  phase 3 tests + 5 finding-identity tests. **148 pass, 11 skipped.**
  A skip is not a pass.
- Clean-DB global audit: **38 findings, 38 distinct rule ids,
  31 PASS / 0 WARN / 0 FAIL / 7 UNVERIFIED.**
- The 7 UNVERIFIED (data the DB does not record): CREDIT-004,
  CREDIT-009, LOG-005, SEC-006, KEY-005, PERM-010, SEC-007.
- Blocker fix (LTP-042 → LTP-043): SEC-004 and CREDIT-006 now emit
  separate findings from the shared internal observation
  `_admin_bypass_missing(snapshot)`; verified by 5 identity tests
  and a CLI smoke run (38 findings, 38 distinct ids).

## Known limitations and residual risks

- No credit ledger exists — charges are free-text audit details +
  balance deltas; exact charge↔execution reconciliation is impossible
  without a `ledger` table (proposed, not built).
- Denied attempts are invisible by design (LTP-022): the code raises
  before any audit call, so the log holds only allowed events.
- The audit log is append-only, NOT tamper-evident: triggers do not
  stop fabricated INSERTs or whole-DB replacement (LOG-005 stays
  UNVERIFIED; hash chain deferred).
- `audit_reports` triggers mirror `audit_log` but do not make reports
  tamper-evident either.
- Key hygiene has only `created_at` — no `last_used_at`, no expiry,
  no usage counter.
- `users.is_admin` is not an authorization source; every check
  compares the owner string to `"lucas"`.
- The internal library API still accepts a raw actor string; the auth
  guarantee holds at the CLI boundary and in the Auditor service
  (which authenticates the raw key itself).
- No report retention policy yet.
- The 11 Windows-only adversarial tests (phase 2 executor) remain
  skipped — isolation is still not demonstrated on a real machine.

## Security decisions

- Global audit is an explicit admin privilege (Lucas only).
- No individual audit in this phase; agents audit nothing.
- Reports never contain API keys, key hashes, secrets, or
  unnecessary personal data — evidence by IDs only.
- Arbitrary execution stays BLOCKED (phase 2 closure stands).
- No phase 4 started automatically. Any next-phase proposal must be
  presented separately, with objective, scope, risks and acceptance
  criteria (LTP-044).

## Reference commits

- Phase 1 approved baseline: `da2fe12`
- Phase 2 step 1 (auth): `6bd849a` (55/55 tests)
- Phase 2 step 2 (executor prepared): `cb155c4` (88 tests)
- Phase 2 closure: `12404a8` (ref. recorded in `9984abf`)
- Phase 3 architecture: `873fc53` (rev 1)
- Phase 3 architecture rev 2: `399a34d`
- **Phase 3 build 1: `42a2bec` (154 tests)**
- **Phase 3 blocker fix: `80aa30e` (159 tests)**
- This closure document: published in the commit after `80aa30e`
  (commit SHA to be recorded here on publish).
