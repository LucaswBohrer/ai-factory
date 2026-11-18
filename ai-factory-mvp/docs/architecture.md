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
              │  triggers), meta │
              └──────────────────┘

              ┌──────────────────┐
              │ Auditor          │
              │ auditor.py       │
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

No payment gateway, no auth service, no sandbox process, no LLM provider,
no queue, no microservices. All of those are out of the approved MVP scope.
