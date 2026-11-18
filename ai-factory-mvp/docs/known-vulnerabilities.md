# Known vulnerabilities / limitations — MVP, 2026-11-18

Honest list for the LTP-020 review, updated for build 2 (LTP-022).
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

## Remaining — confirmed / known by design

4. **No authentication (residual from the fix above).** Ownership checks
   compare the caller-supplied `actor` string against the stored owner.
   There is no login, no token, no signature proving the actor is who
   they claim to be. In the MVP the CLI is run by the local user, so
   this is acceptable for personal use; it is NOT sufficient for a
   multi-user service. Any future API must authenticate the actor
   before these checks mean anything.

5. **No real sandbox.** "Isolation" in the MVP is logical only
   (minimum permissions + limits). No Docker / subprocess isolation,
   no OS-level sandbox. A malicious agent definition cannot do much
   because execution is simulated, but that is not a sandbox proof.

6. **No real token counting.** `real_api_cost` is only a meter; no
   provider is connected, no token counting per model exists.

7. **No antifraud / rate limiting.** No defence against credit farming
   via multiple free accounts. Free quota is 100 credits per owner
   string — an attacker can invent owners. (Authentication itself is
   item 4 above.)

8. **Audit log integrity is DB-local.** Append-only triggers stop
   UPDATE/DELETE through this SQLite file, but anyone with file
   access can delete or replace the whole `data.db`. No signing,
   no remote / tamper-evident log.

9. **Kill switch is local.** It lives in the same DB (`meta` table).
   If the DB is replaced, the switch state is lost. No independent
   kill path.

10. **Execution is simulated.** The concurrency cap is enforced at
    call time; because simulated executions finish immediately, true
    overlapping execution was not demonstrated end-to-end, only via
    the running-count check in tests.

11. **No input sanitisation beyond allowlists.** Agent names /
    descriptions are stored as given; no XSS / injection review
    was done because there is no web UI in the MVP.
