# Containment evidence — MVP, 2026-11-18

## Build 2 (LTP-022) — isolation fixes

Verified on this VM. Test command:

```
$ cd ~/workspace/ai-factory-mvp && python3 -m unittest discover -s tests -v
Ran 41 tests in 0.056s — OK
```

41 tests: the 31 from build 1 plus 10 new isolation tests in
`tests/test_isolation_ltp022.py`, exactly the 10 cases from LTP-022:

| # | LTP-022 case | Test | Result |
|---|---|---|---|
| 1 | Alice executes Alice's agent | `test_01_alice_executes_own_agent` | Pass — execution finished, alice 100 → 89 (10 create + 1 execute) |
| 2 | Bob executes Alice's agent | `test_02_bob_cannot_execute_alices_agent` | Pass — `PermissionError`, 0 execution records, bob still 100, alice unchanged, no `execute_agent` log entry |
| 3 | Bob lists Alice's agents | `test_03_bob_cannot_list_alices_agents` | Pass — `[]` for owner=alice and for no filter; alice sees 1, lucas sees 1 |
| 4 | Bob audits Alice's agent | `test_04_bob_cannot_audit_alices_agent` | Pass — `ValueError: unknown agent: <id>` (same as nonexistent id), bob still 100, no log entry for the attempt |
| 5 | Bob lists audit logs | `test_05_bob_cannot_see_alices_log_entries` | Pass — bob sees `[]`; no entry with actor=alice or target=alice's agent; alice sees her own, lucas sees all |
| 6 | Lucas admin operations | `test_06_lucas_admin_operations` | Pass — lucas executes alice's agent, `admin_execute` entry logged naming alice as owner; lucas lists agents of both alice and bob |
| 7 | Creation with parent_id of another agent | `test_07_creation_with_agent_parent_rejected` | Pass — `ValueError`, depth rule unchanged per LTP-022 §3 |
| 8 | Kill switch blocks creation/execution | `test_08_kill_switch_blocks_creation_and_execution` | Pass — `KillSwitchOn` for both, works again after OFF |
| 9 | Insufficient balance blocks creation | `test_09_insufficient_balance_blocks_creation` | Pass — `InsufficientCredits`, 0 agents for carol |
| 10 | Full regression | `test_10_full_regression_existing_suite` | Pass — the whole pre-existing suite re-run in-process: 31 tests, all green |

CLI smoke test (2026-11-18, temp DB, removed afterwards): bob
`list-agents --owner alice --actor bob` → empty, exit 0; bob
`execute` → `error: not authorized to execute this agent`, exit 1;
bob `audit-agent` → `error: unknown agent: <id>`, exit 1; bob
`list-audit-log --actor bob` → empty; lucas `execute` on alice's
agent → OK, log shows `admin_execute` + `credit_bypass` entries.

## Build 1 (LTP-020)

Verified by Theo on this VM, after the builder run. Test command:

```
$ cd ~/workspace/ai-factory-mvp && python3 -m unittest discover -s tests -v
Ran 31 tests in 0.026s — OK
```

All 31 tests pass. Mapping to the LTP-017 limits:

| Limit (LTP-017) | Test(s) | Result |
|---|---|---|
| Depth 1 / no agent creates agent | `test_agent_cannot_create_another_agent`, `test_unknown_parent_rejected`, `test_human_created_agent_has_depth_zero` | Pass — parent that is an agent is rejected; created agents have depth 0 |
| Max 3 active / user | `test_max_3_active_per_user`, `test_cap_is_per_user`, `test_suspended_agent_frees_a_slot` | Pass — 4th active creation fails; cap is per-user; suspend frees a slot |
| 1 creation / request | `test_bulk_creation_rejected` | Pass — `create_many` with != 1 spec raises |
| Max 2 concurrent executions | `test_concurrent_execution_cap` | Pass |
| Max 5 min / execution | `test_execution_minutes_capped_at_5`, `test_execution_charges_per_minute` | Pass — minutes outside 1..5 rejected |
| Human approval required | `test_requires_human_approval` | Pass |
| Forbidden perms/tools rejected | `test_rejects_self_modify_permission`, `test_rejects_admin_and_create_agent_permissions`, `test_rejects_forbidden_tools`, `test_unknown_template_rejected` | Pass |
| Kill switch | `test_kill_switch_blocks_creation`, `test_kill_switch_blocks_execution`, `test_only_lucas_can_toggle` | Pass |
| Credits | `test_creation_deducts_10_credits`, `test_insufficient_credits_creates_nothing`, `test_lucas_bypass_is_logged_not_charged`, `test_real_api_cost_is_tracked_separately`, `test_audit_costs_one_credit` | Pass |
| Auditor read-only + flags risk | `test_auditor_is_read_only`, `test_auditor_flags_risky_permissions`, `test_auditor_flags_credit_limit_exceeded` | Pass — agent row identical before/after audit |
| Audit log append-only | `test_update_is_blocked_by_trigger`, `test_delete_is_blocked_by_trigger`, `test_no_update_or_delete_api`, `test_log_records_creations` | Pass — DB triggers abort UPDATE/DELETE |

## CLI demos (run by Theo, 2026-11-18)

### Kill switch

- `kill-switch on --actor alice` → `error: only Lucas (admin) can toggle the kill switch`, exit 1.
- `kill-switch on --actor lucas` → `kill switch: on`.
- `create-agent --owner alice ... --approve` while ON → `error: kill switch is ON: creations and executions are blocked`, exit 1.
- `kill-switch off --actor lucas` → service restored.

### Credit-limit blocking

- User `carol`, balance drained 100 → 0 via 10× 10-credit charges.
- `create-agent --owner carol --kind summarizer --approve` → `InsufficientCredits: carol cannot afford 10 credits to create an agent`. Agents created for carol: 0.
- Alice after 1 creation: `alice: 90 credits` (100 − 10). Lucas: `unlimited (admin, bypass logged)`.

### Isolation between users — FIXED in build 2 (LTP-022)

- `list-agents` is ownership-filtered: a non-admin only sees their own
  agents; any other request returns `[]`, and no actor given returns `[]`.
- `execute_agent` checks actor == owner or Lucas, before any charge or
  execution record; admin execution is logged as `admin_execute`.
- `audit_agent` checks ownership before charging: a non-owner gets the
  same `unknown agent` error as for a nonexistent id, with no charge
  and no log entry for the attempt.
- `list_audit_log` is ownership-filtered: a non-admin sees only entries
  they authored or entries targeting their own agents.
- `suspend_agent` / `kill_agent` by non-owner non-admin → `PermissionError`.
  Enforced (unchanged from build 1).
- Residual: actor identity is caller-supplied (there is no auth layer in
  the MVP). The ownership checks assume the actor string was validated
  upstream — see `known-vulnerabilities.md`.

## Depth semantics — DECIDED in LTP-022 §3 (was open in build 1)

ChatGPT confirmed the current implementation is correct: Factory
(human) may create agents, agents may execute authorized functions,
agents may NOT create other agents, and `parent_id` of an agent stays
rejected. "Profundidade 1" means 1 generation, creation by agents is
prohibited, recursion is disabled. **No change to `factory.py`.**
