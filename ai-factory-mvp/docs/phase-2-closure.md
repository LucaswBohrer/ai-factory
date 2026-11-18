# Phase 2 — Closure Report (Authentication & Execution Isolation)

**Date:** 2026-11-18
**Status:** CLOSED with limitations (LTP-034, ChatGPT)
**Reference commit (build 4):** `cb155c4` (Phase 2 step 2, published)
**This document:** `docs/phase-2-closure.md` (final reference commit to be
added on publish; see "Reference commit" below)

## Decision

Phase 2 is formally closed **without arbitrary code execution enabled**.
This is a technically correct closure, not an incomplete approval dressed
as success. The security of Lucas's computer prevails over the project
schedule. Lucas refused the functional AppContainer probe because the
machine is his work computer and the only one he has for projects too
(LTP-033 Theo). The refusal is definitive for this machine, unless he
gives new explicit authorization.

## Status table

| Area | State |
|---|---|
| Authentication | Implemented and tested |
| Identity / authorization | Implemented at the current CLI boundary |
| Executor | Prepared (experimental) |
| AppContainer / LPAC | Not demonstrated |
| Restricted token | Not validated functionally |
| Job Object | Not validated functionally |
| 10 adversarial tests | Pending |
| Arbitrary code execution | BLOCKED |
| Public deploy | Out of scope |

Correct description: **experimental executor prepared, without a validated
isolation boundary.** Do NOT call it a functional sandbox. Do NOT use
"isolation implemented" or "safe environment for arbitrary code".

## What was implemented

1. **Authentication Layer** (`app/auth.py`, LTP-026 §3A, approved LTP-029):
   API keys `afk_<public_id>_<secret>` (32 bytes entropy), HMAC-SHA256
   hash-only storage with a per-database server secret, shown once at
   creation, immediate revocation, identity resolved server-side from
   the key, forged `--actor` rejected, admin (lucas) assigned
   server-side, bootstrap limited to the first initialization.
2. **User isolation preserved:** ownership checks from build 2 (LTP-022)
   unchanged; auth adds identity on top of them.
3. **Executor prepared** (`app/executor.py`, LTP-029 §4): supervisor
   outside the worker environment, AppContainer/LPAC + restricted token
   + Job Object via ctypes, network denied by default, work dir per
   execution, timeout + forced kill, output cap, env built from an
   allowlist. Fail-closed: any isolation failure → `IsolationRefused`,
   never a silent plain-subprocess fallback. No CLI command exposes
   arbitrary execution.
4. **Kill switch:** functional, lucas-only (unchanged from phase 1).
5. **Windows isolation matrix** (`docs/windows-isolation-matrix.md`):
   plain subprocess is not a sandbox; the only demonstrable combination
   is AppContainer/LPAC + restricted token + Job Object.
6. **Read-only diagnostic** (`tools/diagnostico-windows-readonly.ps1`):
   run on Lucas's real machine 2026-11-18 15:21 — Windows 11 Pro build
   26300 64-bit, i7-1355U, 15.7 GB RAM, Python 3.14.7; virtualization
   disabled/not detected; session without admin; Sandbox/Hyper-V/VMP
   not verified (query needs admin); AppContainer/LPAC/token/Job Object
   APIs present. Full result in
   `docs/windows-diagnostic-result-2026-11-18.md`.

## Test results (verified by Theo)

```
python3 -m unittest discover -s tests
Ran 88 tests in 1.6s — OK (skipped=11)
```

- 88 = 55 baseline (31 MVP + 10 isolation + 14 auth) + 33 executor tests.
- 77 passed, 11 skipped. The 11 skipped are the 10 adversarial tests
  (LTP-029 §5) + the probe-gate test — all Windows-only, skipping on
  Linux with "requires Windows AppContainer — not validated".
  **A skip is not a pass.**
- One real bug found and fixed by the tests during step 2: a supervisor
  exception after `start_execution` left the execution record "running"
  forever, poisoning the concurrency cap. Fixed by validating the
  payload before charging and failing + refunding the record on any
  supervisor exception.

## Known limitations and residual risks

- Server secret stored in the DB `meta` table (local-MVP limitation;
  a copy of the DB + secret compromises the keys). Production requires
  the secret outside the DB.
- The internal library API still accepts a raw actor string — the auth
  guarantee holds at the CLI boundary only. Any future API server must
  authenticate the principal server-side before calling internal
  services.
- The Windows launcher code (ctypes bindings) has never run on Windows.
- The 5 new risks from LTP-026 §5 (API key theft, privilege escalation,
  sandbox escape, resource exhaustion, false sense of isolation) are
  recorded in `docs/known-vulnerabilities.md` with status.
- `unsafe_dev` mode exists (plain subprocess) but is off by default and
  requires both `unsafe_dev=True` AND `AI_FACTORY_UNSAFE_DEV=1`, logging
  a WARNING + audit entry per run. It is a dev escape hatch, never the
  default path.

## Security decisions

- No probe on Lucas's machine. No AppContainer profile created there.
- No attempt to work around the absence of privileges.
- No arbitrary execution enabled as a temporary alternative.
- No Docker, WSL or other components installed to work around the
  limitation.
- No phase 3 started automatically.
- LTP-033 (ChatGPT) probe conditions (no-elevation probe, exact scope
  table, 10-test PASS/FAIL/UNVERIFIED matrix, mandatory cleanup) remain
  on file as the reference for a future separate environment.

## Criteria to reopen executor validation

All of the following, in order:

1. A separate test machine or disposable environment exists, expressly
   authorized by Lucas for tests (NOT his work computer).
2. The probe script is reviewed by ChatGPT per LTP-033 (ChatGPT):
   full script content, exact Windows APIs used, temp paths, profile
   removal mechanism, residual risks, how each test distinguishes a
   real block from a circumstantial failure.
3. Lucas gives explicit authorization for that specific environment.
4. The 10 adversarial tests run there with PASS/FAIL/UNVERIFIED
   results; a blocked attempt caused by misconfiguration, invalid path
   or missing service does NOT count as proof of isolation.
5. Cleanup verified: no test processes left, Job Object closed, temp
   dir and synthetic files removed, AppContainer profile removed.

Until then, arbitrary code execution stays BLOCKED.

## Reference commit

- Phase 1 approved baseline: `da2fe12` (conditional approval, LTP-024)
- Phase 2 step 1 (auth + matrix): `6bd849a` (55/55 tests)
- Phase 2 step 2 (executor prepared + diagnostic): `cb155c4`
  (88 tests: 77 pass, 11 Windows-only skipped)
- This closure document: published in commit `12404a8`
  (Phase 2 closure: LTP-033 + LTP-034 + `docs/phase-2-closure.md`).
