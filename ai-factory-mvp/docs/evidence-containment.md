# Containment evidence — MVP, 2026-11-18

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

### Isolation between users — PARTIAL, see vulnerabilities

- `list-agents --owner alice` returns only alice's agent; `--owner bob` only bob's. Per-owner filter works.
- `suspend_agent` by non-owner non-admin → `PermissionError: only the owner or Lucas can suspend an agent`. Enforced.
- GAPS found by Theo (not covered by the 31 tests): `list-agents` with no `--owner` returns all agents, and `execute_agent` does not verify actor == owner — see `known-vulnerabilities.md`. These are documented, not hidden.

## Depth semantics — open question (flagged by builder, confirmed by Theo)

Implemented literally from the brief: any agent `parent_id` is rejected,
so created agents always have depth 0 and the ceiling of 1 is never
reached in the MVP. This is stricter than the cap. If the intended
semantics were "an agent may create one level down (depth 1)", that is
a one-line change in `factory.py` but it contradicts "parent must be
human/Factory" — needs Lucas + ChatGPT confirmation before changing.
No change made in this build.
