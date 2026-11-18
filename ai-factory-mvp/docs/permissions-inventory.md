# Permissions inventory — as implemented (MVP, 2026-11-18)

Source of truth: `app/config.py`.

## Allowed permissions (agents may hold)

| Permission | Meaning | Who can hold it |
|---|---|---|
| `read` | Read local configs/logs | Any template agent |
| `audit` | Audit other agents | `auditor` template only (in practice) |
| `execute_limited` | Run a bounded simulated execution (max 5 min) | Only if explicitly passed at creation; no default template grants it |

## Allowed tools

`read_config`, `read_logs`, `summarize`, `search_local`, `audit`.

## Forbidden — Factory rejects outright (`factory.py: validate_definition`)

Permissions: `admin`, `self_modify`, `self_modify_permissions`,
`create_agent`, `write`, `delete`, `manage_users`, `manage_billing`.
Tools: `write`, `execute_unlimited`, `create_agent`, plus anything
outside the allowlist.

## Who can do what (enforced in code)

| Action | Lucas (admin) | Owner of the agent | Other user |
|---|---|---|---|
| Create agent | Yes (bypass logged, not charged) | Yes, if credits + approval | Only own agents; cap is per-owner |
| Toggle kill switch | Yes, only Lucas | No — `PermissionError` | No |
| Suspend / kill agent | Yes | Yes (own agents) | No — `PermissionError` |
| Execute agent | Yes (subject to agent perms) | Yes (subject to agent perms) | **GAP — see known-vulnerabilities.md: `execute_agent` does not check actor == owner** |
| List agents `--owner X` | Yes | Yes, filtered by owner | Filter works, but see gap below |
| List agents (no filter) | Yes | **GAP — returns all agents, no ownership check** | **GAP — same** |
| Audit agent | Yes | Yes (costs 1 credit, or bypass logged) | Yes if they know the agent id — no ownership check on audit |
| Read audit log | Via CLI, no actor check | Via CLI, no actor check | Via CLI, no actor check |

## Kill switch

- Stored in `meta` table (`kill_switch` = `on`/`off`), default `off`.
- Only Lucas can toggle (`control.py: set_kill_switch`).
- When ON: Factory creation and Control execution raise `KillSwitchOn`.
- Per-agent kill: `kill_agent` sets status `killed`; killed agents cannot execute.

## Auditor

- Permissions: `read`, `audit` only. Tools: `read_config`, `read_logs`, `audit`.
- Read-only by construction: reads agent row + audit log + executions,
  asserts the agent row is unchanged before returning.
- The Auditor agent itself cannot execute (lacks `execute_limited`).
