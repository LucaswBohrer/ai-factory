# Architecture — as implemented (MVP, 2026-11-18)

Single modular backend, Python stdlib only. No microservices, no network,
no LLM calls, SQLite via `sqlite3`.

```
                  ┌─────────────────────┐
                  │  CLI (app/cli.py)   │
                  │  create/list/audit/ │
                  │  credits/kill-switch│
                  │  /execute/list-log  │
                  └──────┬──────────────┘
                         │  actor resolved here
                         │  from a validated key
                         ▼
                  ┌─────────────────────┐
                  │  Auth (app/auth.py) │  Phase 2, step 1
                  │  API keys: HMAC-    │
                  │  hashed, shown once,│
                  │  revocable; admin = │
                  │  lucas, server-side │
                  └──────┬──────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
│ Factory      │ │ Control      │ │ ResourceCore     │
│ factory.py   │ │ control.py   │ │ resource_core.py │
│ - templates  │ │ - registry   │ │ - balances       │
│ - validation │ │ - suspend/   │ │ - charge/deduct  │
│ - human      │ │   kill agent │ │ - agent limits   │
│   approval   │ │ - kill switch│ │ - real_api_cost  │
│ - depth/caps │ │ - audit log  │ │   (separate meter)│
│ - credits    │ │ - execute()  │ │                  │
│   (via Core) │ │   (simulated)│ │                  │
└──────┬───────┘ └──────┬───────┘ └────────┬─────────┘
       │                │                  │
       └────────────────┼──────────────────┘
                        ▼
              ┌──────────────────┐
              │ Store (store.py) │
              │ SQLite: users,   │
              │ agents,          │
              │ executions,      │
              │ audit_log        │
              │ (append-only via │
              │  triggers), meta, │
              │  api_keys (hash   │
              │  only, no raw    │
              │  keys),           │
              │  audit_reports   │
              │  (append-only via │
              │  triggers)        │
              └──────────────────┘

              ┌──────────────────┐
              │ Auditor          │
              │ agent_auditor.py │
              │ READ-ONLY: reads │
              │ agent + log +    │
              │ executions,      │
              │ returns findings │
              │ info/warning/    │
              │ critical. Never  │
              │ writes agent row │
              │ (asserted).      │
              └──────────────────┘
```

## Data flow — global audit (Phase 3, LTP-035..040)

CLI `audit-global` → raw key passed through, never resolved to an
owner string at the CLI → `AuditService.run_global_audit` authenticates
the key itself (`Auth.authenticate` + `require_admin`; non-admin →
PermissionError, no report, no log) → rate limit (60 s, from latest
successful report; refusals log `audit_global_rate_limited`, create no
report, do not move the clock) → snapshot in ONE SQLite read
transaction (explicit BEGIN, raw SELECTs, closed before evaluation) →
38 deterministic rules in `app/auditor/rules/` (PERM 10, CREDIT 9,
LOG 6, KEY 6, SEC 7; ruleset 3.0.0; every rule returns exactly one
Finding; non-applicable → UNVERIFIED) → report row + `audit_global`
log entry in ONE write transaction (both or neither) → report
retrieved with `audit-report --id`. The audit is FREE for Lucas (no
credits, no fictitious charge). It is NOT a security certifier: a
report with all PASS does not mean the system is secure. Full
specification in `docs/auditor-architecture.md`.

## Data flow — authenticated command (Phase 2, step 1)

CLI → Auth.authenticate(key) → owner resolved server-side (a supplied
`--actor` must match or auth fails) → Control/Factory/Auditor with the
resolved owner. No key → generic `authentication failed`, nothing runs.

## Data flow — create agent

CLI → Factory.create_agent → Control.require_kill_switch_off →
human approval check → template check → depth check (no agent parent) →
active-cap check (3/user) → validate tools/permissions vs allowlists →
ResourceCore.charge (10 credits, Lucas bypass logged) →
Store.insert_agent → Control.append_audit.

## Data flow — execute

CLI → Control.execute_agent → kill-switch check → minutes 1..5 →
agent active? → has `execute_limited`? → concurrent cap (2) →
agent credit limit → ResourceCore.charge (1/min) →
Store.start_execution → immediately end_execution (simulated) → audit log.

Note: execution is simulated in the MVP — no real tool calls, no LLM.
The concurrency cap is enforced on the running-count check at call time.

## What is NOT in the diagram (deliberately)

No payment gateway, no sandbox process, no LLM provider,
no queue, no microservices. All of those are out of the approved MVP scope.
The sandbox executor is Phase 2, step 2 — see
`docs/windows-isolation-matrix.md` first.
