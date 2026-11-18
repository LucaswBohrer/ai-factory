# Known vulnerabilities / limitations — MVP, 2026-11-18

Honest list for the LTP-020 review. Nothing here is hidden; fixing them
is out of the approved MVP scope unless Lucas + ChatGPT decide otherwise.

## Confirmed by Theo (reproduced, not theoretical)

1. **Cross-user execution (isolation gap).** `Control.execute_agent`
   does not check that the actor owns the agent (or is Lucas).
   Reproduced: bob executed alice's executable agent; cost was charged
   to bob, execution recorded as `actor=bob`. Fix: require
   `actor == agent.owner or actor == ADMIN` before executing.
   Severity: high for any multi-user use; low for personal-only MVP use.

2. **Agent enumeration (isolation gap).** `Control.list_agents(None)` /
   CLI `list-agents` with no `--owner` returns every agent to any caller.
   No authentication layer exists in the MVP, so this is enumeration
   by design of the CLI, not an auth bypass — but it must not ship as
   multi-user behaviour. Fix: require an owner filter, or an admin-only
   unfiltered list.

3. **Audit / audit-log visibility.** `audit-agent` and `list-audit-log`
   have no ownership check — anyone who knows an agent id can audit it,
   anyone can read the log. Same fix class as (2).

## Known by design (documented in README, not bugs)

4. **No real sandbox.** "Isolation" in the MVP is logical only
   (minimum permissions + limits). No Docker / subprocess isolation,
   no OS-level sandbox. A malicious agent definition cannot do much
   because execution is simulated, but that is not a sandbox proof.

5. **No real token counting.** `real_api_cost` is only a meter; no
   provider is connected, no token counting per model exists.

6. **No authentication, no antifraud.** No login, no rate limiting
   per identity, no defence against credit farming via multiple
   free accounts. Free quota is 100 credits per owner string — an
   attacker can invent owners.

7. **Audit log integrity is DB-local.** Append-only triggers stop
   UPDATE/DELETE through this SQLite file, but anyone with file
   access can delete or replace the whole `data.db`. No signing,
   no remote / tamper-evident log.

8. **Kill switch is local.** It lives in the same DB (`meta` table).
   If the DB is replaced, the switch state is lost. No independent
   kill path.

9. **Execution is simulated.** The concurrency cap is enforced at
   call time; because simulated executions finish immediately, true
   overlapping execution was not demonstrated end-to-end, only via
   the running-count check in tests.

10. **No input sanitisation beyond allowlists.** Agent names /
    descriptions are stored as given; no XSS / injection review
    was done because there is no web UI in the MVP.
