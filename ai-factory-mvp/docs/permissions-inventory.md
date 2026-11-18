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
| Execute agent | Yes (subject to agent perms; logged as `admin_execute` when it is another user's agent) | Yes (subject to agent perms) | No — `PermissionError`, nothing charged, no execution record (fixed build 2, LTP-022) |
| List agents `--owner X` | Yes, any owner | Yes, only own (`--owner` must match `--actor`) | No — returns `[]`, existence not revealed |
| List agents (no filter) | Yes, all agents | No — returns `[]` | No — returns `[]` |
| Audit agent | Yes (costs 1 credit, or bypass logged) | Yes (costs 1 credit) | No — `ValueError: unknown agent`, same as nonexistent id; no charge, no log entry |
| Read audit log | Yes, all entries | Yes, only own entries (authored, or targeting own agents) | No — only own entries; other users' entries never shown |

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
