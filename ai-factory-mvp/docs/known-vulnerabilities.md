# Known vulnerabilities / limitations — MVP, 2026-11-18

Honest list, updated for Phase 2 step 1 (auth layer, LTP-026/027).
Nothing here is hidden.

## FIXED in build 2 (LTP-022)

1. **Cross-user execution — FIXED.** `Control.execute_agent` now checks
   actor == agent owner or Lucas, before any charge or execution record.
   Admin execution is logged as `admin_execute`. Verified by
   `test_02_bob_cannot_execute_alices_agent` and
   `test_06_lucas_admin_operations` in `tests/test_isolation_ltp022.py`.

2. **Agent enumeration — FIXED.** `Control.list_agents` is now
   ownership-filtered: a non-admin only sees their own agents; any
   other request returns `[]`. No actor given returns `[]`.
   Verified by `test_03_bob_cannot_list_alices_agents`.

3. **Audit / audit-log visibility — FIXED.** `Auditor.audit_agent`
   checks ownership before charging and returns the same
   `unknown agent` error as for a nonexistent id (no existence leak,
   no charge, no log entry for the denied attempt).
   `Control.list_audit_log` is ownership-filtered (own entries, or
   entries targeting own agents). Verified by
   `test_04_bob_cannot_audit_alices_agent` and
   `test_05_bob_cannot_see_alices_log_entries`.

## FIXED (at the CLI boundary) in Phase 2 step 1 (LTP-026/027)

4. **No authentication — FIXED at the CLI boundary.** The CLI now
   resolves the actor from a validated API key (`app/auth.py`):
   keys are random (32 bytes entropy), stored only as HMAC-SHA256
   hashes, shown once, revocable immediately; a forged `--actor`
   fails authentication; admin is server-assigned from the key's
   owner. Verified by `tests/test_auth_phase2.py` (14 tests).
   **Residual:** the library API (`Control`/`Factory`/`Auditor`)
   still accepts a plain actor string — the guarantee holds only
   where callers authenticate first (the CLI does). Any future API
   server must authenticate before calling the library.

## New risks from LTP-026 §5 — status

5. **Roubo de API keys (key theft).** A leaked key gives the owner's
   access until revoked. Mitigations in place: hash-only storage
   (a DB leak does not expose keys), immediate revocation, keys shown
   once, generic failure messages. Residual: keys have no expiry;
   the server secret lives in the DB (see 10); there is no
   breach-detection or usage anomaly alerting.

6. **Elevação de privilégios (privilege escalation).** The binding
   identity → permissions is server-side (key owner → admin only for
   lucas), tested by the admin-only CLI tests. Residual: the binding
   is only as strong as key storage (see 5) and the library API's
   raw actor parameter (see 4).

7. **Escape da sandbox (sandbox escape).** STILL NOT APPLICABLE —
   there is no enabled executor and arbitrary code execution stays
   DISABLED. Step 2 prepared the executor code
   (`app/executor.py`) and the 10 adversarial tests, but the
   Windows launcher has NEVER run on Windows and the adversarial
   tests SKIP on Linux. The risk becomes real the moment the
   executor is enabled on a Windows machine; it must only be
   enabled after the adversarial tests PASS there.

8. **Esgotamento de recursos (resource exhaustion).** Partially
   mitigated today: execution is simulated, capped at 5 min / 2
   concurrent, and the 3-active-agents cap bounds creation. The
   step-2 executor defines Job Object limits (memory/CPU/process
   caps, `KILL_ON_JOB_CLOSE`) and the supervisor enforces timeout
   + output cap — but those limits are only demonstrated by the
   unsafe-dev tests on Linux (timeout kill, output cap) and the
   Windows adversarial tests (memory/process/timeout), which are
   still skipped. Until the Windows tests pass, a flood of CLI
   calls is bounded only by credits and caps, not by OS resource
   limits.

9. **Falsa sensação de isolamento (false sense of isolation).**
   Documented explicitly: the matrix (`docs/windows-isolation-matrix.md`)
   states a subprocess + Job Object is a resource-limited launcher,
   not a sandbox, and that arbitrary code execution stays disabled
   until AppContainer + restricted token + Job Object isolation is
   *demonstrated* on the real Windows machine. Step 2 keeps this
   honest: the Windows tests SKIP on Linux (never pass by
   simulation) and the executor fails closed everywhere else.

## Remaining — confirmed / known by design

10. **Server secret lives in the DB (auth limitation).** The HMAC
    server secret is generated per database and stored in the `meta`
    table. Anyone who can read the DB file can read the secret and
    forge key hashes — but they already own the data in that file.
    A production secret must live outside the DB (env var / secret
    manager). No key expiry in step 1; revoke instead.

11. **No real sandbox (executor prepared, not enabled).**
    "Isolation" is logical only (minimum permissions + limits).
    Step 2 prepared `app/executor.py` (AppContainer/LPAC +
    restricted token + Job Object, via ctypes) and the 10
    adversarial tests, but the Windows launcher has never run on
    Windows and the tests SKIP on Linux. No Docker / subprocess
    isolation is claimed. Execution of agent payloads is only
    possible through `Control.execute_isolated` (library, no CLI)
    and is REFUSED on any host without AppContainer — except the
    explicitly unsafe dev mode (see 18). Arbitrary code execution
    stays DISABLED until the adversarial tests pass on Lucas's
    real Windows machine.

18. **Unsafe dev mode (`AI_FACTORY_UNSAFE_DEV=1`).** The executor
    has an explicitly-named plain-subprocess path, disabled by
    default, requiring BOTH `unsafe_dev=True` AND the env flag
    `AI_FACTORY_UNSAFE_DEV=1`. It runs the payload with the
    supervisor's own rights — it CAN read the supervisor's files
    and is NOT a sandbox. It exists only so supervisor logic
    (timeout/kill, output cap, env scrubbing) can be tested on
    Linux. Every unsafe run logs a WARNING and writes an audit
    entry with `unsafe_dev=True`. Risk: anyone who can set the
    env flag AND call the library can run unisolated code — that
    is local-access trust, same as the DB file itself. Never
    enable it on a machine with other users' data, never present
    it as isolation.

12. **No real token counting.** `real_api_cost` is only a meter; no
    provider is connected, no token counting per model exists.

13. **No antifraud / rate limiting.** No defence against credit
    farming via multiple free accounts — now bounded by the fact that
    keys are created only by an authenticated admin, but a rogue admin
    or a stolen admin key removes that bound. No per-key rate limits.

14. **Audit log integrity is DB-local.** Append-only triggers stop
    UPDATE/DELETE through this SQLite file, but anyone with file
    access can delete or replace the whole `data.db`. No signing,
    no remote / tamper-evident log.

15. **Kill switch is local.** It lives in the same DB (`meta` table).
    If the DB is replaced, the switch state is lost. No independent
    kill path.

16. **Execution is simulated.** The concurrency cap is enforced at
    call time; because simulated executions finish immediately, true
    overlapping execution was not demonstrated end-to-end, only via
    the running-count check in tests.

17. **No input sanitisation beyond allowlists.** Agent names /
    descriptions are stored as given; no XSS / injection review
    was done because there is no web UI in the MVP.
