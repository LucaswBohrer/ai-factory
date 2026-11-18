# Phase 3 — Auditor Architecture (detailed design document)

**Status:** DOCUMENT ONLY. Direction approved by ChatGPT (LTP-036);
implementation **NOT authorized** yet. This document must be approved
by Lucas before any code is written. No code, no tests, no schema
changes were made to produce it.

**Ruleset version (proposed):** `3.0.0`
**Baseline at time of writing:** 88 tests (77 pass, 11 Windows-only
skipped) — see §11.1 for the correction to LTP-036 §6 item 11, which
says 55.
**Date:** 2026-11-18
**Sources:** `app/store.py`, `app/control.py`, `app/resource_core.py`,
`app/factory.py`, `app/auditor.py`, `app/auth.py`, `app/config.py`,
`app/cli.py`, `app/executor.py` (result statuses only),
`docs/architecture.md`, `docs/permissions-inventory.md`,
`docs/evidence-containment.md`, `docs/known-vulnerabilities.md`.
Every claim about current behaviour cites the real code. Where this
document proposes something that does not exist yet, it says so
explicitly.

## 0. What Phase 3 is (and is not) — scope decisions (LTP-036)

The Phase 3 Auditor is a **deterministic, reproducible, evidence-based
checker of integrity, consistency and observable security events**.
It is NOT a general security auditor, NOT a certifier of safety, and
NOT an autonomous agent with administrative powers.

| Decision | Rule |
|---|---|
| Global audit | Only Lucas (admin), authenticated via API key. `Auth.require_admin` + service-level check. |
| Individual-user audit | OUT of the Phase 3 MVP. A common user audits nothing in this phase (not even their own data, via this service). |
| Agents auditing | No agent audits anything in this phase. The Auditor *agent template* (`auditor` kind) is unchanged; the new service is server-side code, not an agent action. |
| Rules | Pure deterministic functions. No LLM calls. No side effects. No direct access to the CLI. |
| Writes | Read-only against everything audited. The only writes are: one row in the new `audit_reports` table, and one `audit_log` entry recording that the audit ran (see §8.4 — written *after* the rules read their snapshot, so it cannot influence its own result). |
| Execution | No arbitrary code execution. The isolated executor (Phase 2) is not used by the Auditor and stays BLOCKED. |
| Deploy / payments | Out of scope, unchanged from Phase 1/2. |

**Mandatory disclaimer (must appear in every report and in the CLI
output):**

> A report with all PASS does not mean the system is secure — it means
> the implemented checks found no violations within the examined scope.

## 1. What exists today

### 1.1 Module map (real)

| Module | Role |
|---|---|
| `app/config.py` | Constants: `ADMIN_USER = "lucas"`, containment limits, credit costs, allowlists, templates, statuses. |
| `app/store.py` | SQLite persistence. Tables: `users`, `agents`, `executions`, `audit_log` (append-only via triggers), `meta`, `api_keys`. No business rules. |
| `app/factory.py` | Template-based agent creation only. Human approval, depth rule, caps, allowlist validation, creation charge. |
| `app/control.py` | Registry (ownership-filtered), suspend/kill, kill switch, `execute_agent` (simulated), `execute_isolated` (library only, fail-closed gate). |
| `app/resource_core.py` | Internal credit accounting: `charge`, `refund`, `balance`, `check_agent_credit_limit`, `record_real_api_cost`. |
| `app/auth.py` | API keys: `afk_<public_id>_<secret>`, HMAC-SHA256 hash-only storage, shown once, revocation, bootstrap. |
| `app/auditor.py` | **Existing per-agent Auditor** (`Auditor.audit_agent`). Read-only, per-agent findings with severity `info` / `warning` / `critical`, costs 1 credit (admin bypass logged). See §1.2. |
| `app/cli.py` | CLI. Actor resolved from a validated API key; `--actor` must match the key owner. |
| `app/executor.py` | Isolated executor (prepared, NOT enabled). `ExecutionResult.status` ∈ {`finished`, `timeout`, `killed`, `limit_exceeded`, `failed`, `refused`}. |

### 1.2 The existing per-agent Auditor (`app/auditor.py`)

`Auditor.audit_agent(agent_id, actor)` — the Phase 3 service is a
*different* thing; do not conflate them.

- Ownership check FIRST: non-owner (and non-admin) gets
  `ValueError("unknown agent: <id>")`, identical to a nonexistent id;
  no charge, no log entry for the denied attempt.
- Costs `COST_AUDIT` = 1 credit (admin bypass logged, not charged).
- Checks, all read-only against one agent row + audit log +
  executions table: forbidden permission, permission outside the
  allowlist, self-modify / `create_agent` permission, tool outside the
  allowlist, depth > `MAX_DEPTH`, `parent_id` set, `credits_used` >
  `max_credits` (critical) or ≥ 80% (warning), `real_api_cost` > 0
  (info), status killed (info) / suspended (warning), > 10 executions
  (warning). Finding codes today: `forbidden_permission`,
  `unknown_permission`, `self_modify_risk`, `recursion_risk`,
  `forbidden_tool`, `depth_exceeded`, `has_parent`,
  `credit_limit_exceeded`, `credit_limit_near`, `real_api_cost`,
  `agent_killed`, `agent_suspended`, `many_executions`, `clean`.
- Proves read-only by asserting the agent row is identical before and
  after (`assert after == before`).
- Appends one `audit_agent` entry to the log when it completes.

The Phase 3 service REUSES these checks where they apply to the
whole system (e.g. PERM-001..003 below mirror the per-agent codes),
but it operates on the whole database, not one agent, and it persists
a report. The per-agent Auditor is unchanged by Phase 3.

### 1.3 The audit-action vocabulary (complete, from the code)

These are the only `action` values the system ever writes to
`audit_log` (grep over `app/*.py`):

`create_agent`, `execute_agent`, `execute_isolated`,
`execute_refused`, `execute_failed`, `audit_agent`,
`credit_charge`, `credit_refund`, `credit_bypass`, `admin_execute`,
`kill_switch_on`, `kill_switch_off`, `suspend_agent`, `kill_agent`,
`create_api_key`, `revoke_api_key`, `real_api_cost`.

**Never logged:** read/list actions (`list-agents`, `list-audit-log`,
`credits`), failed authentications (a bad key fails in
`Auth.authenticate` before any audit call), and denied authorization
attempts (`execute_agent` / `audit_agent` raise `PermissionError` /
`ValueError` *before* any charge or log entry — by design, LTP-022).
This has a direct consequence for the SEC rules: see §7.2.

## 2. The credit ledger — as it actually exists today

### 2.1 There is no ledger table

This is the single most important fact for Phase 3. **No table
records charges.** Credits exist only as three mutable numbers plus
free-text log lines:

| Location | Field | Meaning |
|---|---|---|
| `users.credits_balance` | INTEGER | Current balance of a non-admin user. Admin (`lucas`) has no balance: `ResourceCore.balance` returns `None` = unlimited, and `charge` never deducts from him. |
| `agents.credits_used` | INTEGER | Cumulative credits attributed to one agent. Incremented by `ResourceCore.charge` when `agent_id` is given; decremented by `refund`. |
| `agents.max_credits` | INTEGER | Per-agent cap (template default: auditor 100, summarizer/researcher 50; overridable at creation). Enforced by `check_agent_credit_limit`: `credits_used + additional <= max_credits`. |
| `agents.real_api_cost` | REAL | Separate meter for real API consumption. Only written by `record_real_api_cost`; no provider is connected, no token counting exists (vuln 12). |

Charge/refund events exist ONLY as `audit_log` rows whose `detail`
is a **free-text, human-readable string**, e.g.
`"charged 1 credits (execution 1 min); balance now 99"` or
`"refunded 1 credits (refund: isolated execution refused); balance
now 90"`. There is no `amount` column, no charge id, no link from a
charge to the execution it paid for, and no transaction id. Any
reconciliation must parse English free text with a regex — fragile,
and it cannot be exact. See §7.1.

### 2.2 Charge flows (exact order, from the code)

**Create agent** (`Factory.create_agent`): kill-switch check →
human approval → template → depth → active-cap (3/user) →
allowlist validation → `ResourceCore.charge(owner, 10,
"create agent '<name>'")` with `agent_id=None` (the agent does not
exist yet, so the charge is NOT attributed to any agent row) →
`insert_agent` with `credits_used = 10` (non-admin) or `0` (admin).
Note the asymmetry: the user balance is deducted by `charge`, and
the agent row independently records `credits_used = 10`. These two
numbers are written by different statements; nothing ties them
together in one transaction.

**Simulated execution** (`Control.execute_agent`): kill-switch →
minutes 1..5 → agent exists & actor is owner-or-admin → agent
`active` → has `execute_limited` → running-count < 2 →
`check_agent_credit_limit` → `charge(actor, minutes * 1,
"execution N min", agent_id=agent_id)` → `start_execution` →
immediately `end_execution` (`finished`) → `audit_agent` entry.
Admin execution of another user's agent additionally appends
`admin_execute`. Admin is never charged: `charge` appends a
`credit_bypass` entry instead.

**Isolated execution** (`Control.execute_isolated`): isolation gate
FIRST (refuses before any charge/record) → authorization (same rules
as `execute_agent`) → payload validation BEFORE charging → charge up
front for the timeout budget (`limits.charge_minutes() * 1`) →
`start_execution` → supervisor run → on success `end_execution`
(`finished`); on `IsolationRefused` or any supervisor exception,
`refund` the charge and `fail_execution` (`refused` / `failed`).
No CLI command exposes this (verified by test).

**Audit** (`Auditor.audit_agent`): ownership check → `charge(actor,
1, "audit agent <id>")` with `agent_id=None` — the 1 credit is
deducted from the user balance but is NOT attributed to any agent
row (`agents.credits_used` is untouched by audits).

**Refund** (`ResourceCore.refund`): `amount >= 0` else `ValueError`.
Admin: no-op on balance, appends a `credit_bypass` entry, and still
decrements `agents.credits_used` by the amount when `agent_id` is
given. Non-admin: balance += amount, `agents.credits_used` -= amount
when `agent_id` is given, appends a `credit_refund` entry. Refunds
are only issued by `execute_isolated` failure paths.

**Real API cost** (`ResourceCore.record_real_api_cost`): `amount >= 0`
else `ValueError`; adds to `agents.real_api_cost` and appends a
`real_api_cost` entry with actor `"system"`.

### 2.3 Execution states (actual)

`executions.status` is one of:

| State | How it is reached |
|---|---|
| `running` | `Store.start_execution` inserts it. Simulated executions end immediately; a record can only stay `running` if the process dies between start and end, or if a supervisor exception path fails to fail it (the Phase 2 step-2 bug that was fixed: any supervisor exception now fails the record + refunds). |
| `finished` | `Store.end_execution` sets it, with `ended_at`. |
| `refused` | `Store.fail_execution(id, "refused")` — isolation gate refused after charging. |
| `failed` | `Store.fail_execution(id, "failed")` — supervisor failed before/during the run. |

`ExecutionResult.status` (supervisor return value, NOT a DB column)
is one of `finished`, `timeout`, `killed`, `limit_exceeded`,
`failed`, `refused`. The DB row only ever shows `running` /
`finished` / `refused` / `failed`; a supervisor `timeout` is stored
as a `finished` row (the supervisor completed its run with a timeout
outcome) — the timeout fact lives only in the `execute_isolated`
audit-log detail string.

### 2.4 Credit invariants the rules can check (structured data only)

- (I1) No user balance is negative: `users.credits_balance >= 0`.
- (I2) No agent exceeds its cap: `agents.credits_used <= max_credits`.
- (I3) `agents.credits_used >= 0` (a refund larger than the charged
  amount would drive it negative — `refund` does not guard this).
- (I4) Every `finished` execution was preceded by a charge — NOT
  checkable from structured data (see §7.1).
- (I5) Every `credit_refund` corresponds to a `refused`/`failed`
  execution — checkable only approximately, by agent + time ordering
  (see CREDIT-004).

## 3. Audit log — real structure and real guarantees

### 3.1 Schema (`store.py`)

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,          -- ISO-8601 UTC, datetime.now(timezone.utc)
    actor TEXT NOT NULL,       -- owner string, or "system" / "bootstrap"
    action TEXT NOT NULL,      -- see §1.3 vocabulary
    target TEXT NOT NULL DEFAULT '',   -- usually an agent id or key public_id
    detail TEXT NOT NULL DEFAULT ''    -- free-text, human-readable
);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
```

Accessors: `Store.append_audit(actor, action, target, detail)`,
`Store.list_audit_log(limit=100)` (newest first, `ORDER BY id DESC`).
`Control.list_audit_log(limit, actor)` fetches 1000 rows and filters
by ownership (admin sees all; others see entries they authored or
entries targeting their own agents; no actor → `[]`).

### 3.2 What the triggers guarantee — and what they do not

**Guaranteed:** through this SQLite connection, no `UPDATE` and no
`DELETE` on `audit_log` can succeed; the triggers abort them. There
is deliberately no update/delete API in `Store`.

**NOT guaranteed (LTP-036 correction A, agreed):**

- **No tamper-evidence.** There is no hash chain, no signature, no
  external anchor. Nothing in the schema can prove that a row was
  not altered — because alteration is only blocked *through the
  triggers*, and anyone with file access can drop/recreate the
  triggers, edit the file directly, or replace the whole `data.db`
  (known-vulnerabilities item 14). A hash chain without external
  protection could itself be recomputed by anyone with DB access.
- **No insert protection.** The triggers block UPDATE/DELETE only.
  Anyone with DB write access can INSERT fabricated entries
  (including back-dated `ts` and fake `actor` values) — nothing
  distinguishes a genuine row from an inserted one.
- **ID gaps are not proof of deletion.** `AUTOINCREMENT` ids can
  skip values on failed/rolled-back transactions, so a gap is
  suspicious but not conclusive. Conversely, a deleted-and-rebuilt
  table can have a perfectly dense id sequence.
- **Timestamps are wall-clock strings**, not monotonic. `ts` order
  can disagree with `id` order under clock adjustments; `ts` is
  written by the application (`_now()`), not by the DB.
- **No retention guarantee.** The log is unbounded; `list_audit_log`
  caps reads at 100 (Store) / 1000 (Control filter), so a global
  audit must read the table directly (via `Store`, bypassing the
  Control cap) and must handle arbitrarily large logs.

Consequence for the rules: LOG rules may only assert *internal
consistency* (sequence, duplicates, vocabulary, cross-references).
Anything about "the log was not tampered with" is UNVERIFIED by
design in Phase 3 (§7.3).

## 4. Permission and grant model — as it actually exists

### 4.1 Agent permissions and tools (`config.py`)

- Allowed permissions: `read`, `audit`, `execute_limited`.
- Forbidden permissions (Factory rejects outright):
  `admin`, `self_modify`, `self_modify_permissions`, `create_agent`,
  `write`, `delete`, `manage_users`, `manage_billing`.
- Allowed tools: `read_config`, `read_logs`, `summarize`,
  `search_local`, `audit`.
- Forbidden tools: `write`, `execute_unlimited`, `create_agent`,
  plus anything outside the allowlist.
- Agent statuses: `active`, `suspended`, `killed`
  (`Store.update_agent_status` rejects anything else).
- Templates (only way to create an agent; `kind` must be a known
  template): `auditor` (tools `read_config, read_logs, audit`;
  permissions `read, audit`; `max_credits` 100), `summarizer`
  (`read`; 50), `researcher` (`read`; 50). **No template grants
  `execute_limited`** — it must be explicitly passed at creation.
  A caller may narrow a template's tools/permissions, never widen
  them (`validate_definition`).

### 4.2 Containment limits (hard, enforced in code)

`MAX_DEPTH = 1` (in practice 0: an agent parent is always rejected,
so every created agent has `depth = 0` and `parent_id = None`;
LTP-022 §3 decided this is correct — "depth 1" means 1 generation),
`MAX_ACTIVE_PER_USER = 3`, `MAX_CREATIONS_PER_REQUEST = 1`,
`MAX_CONCURRENT_EXECUTIONS = 2`, `MAX_EXECUTION_MINUTES = 5`,
human approval mandatory for every creation, kill switch blocks
creation and execution.

### 4.3 Who can do what (enforced in code, not in a table)

There is **no grants table**. Authorization is procedural, in
`Control` / `Factory` / `Auth`, and always reduces to two checks:
`actor == agent["owner"]` or `actor == config.ADMIN_USER`
(`"lucas"`). The `users.is_admin` column exists but is **not used
for authorization anywhere** — every check compares the owner string
to `ADMIN_USER`. The column is set at user creation
(`is_admin = 1 iff owner == ADMIN_USER`) and is redundant.

| Action | Lucas | Owner | Other user |
|---|---|---|---|
| Create agent | yes (bypass logged) | yes, own only, if credits + approval | no (`_require_owner_or_admin` in CLI) |
| Create API key | yes, only Lucas | no | no |
| Revoke API key | yes, only Lucas | no | no |
| Toggle kill switch | yes, only Lucas | no | no |
| Suspend / kill agent | yes | yes, own | no (`PermissionError`) |
| Execute agent | yes (`admin_execute` logged when another user's agent) | yes | no (`PermissionError`, nothing charged/recorded) |
| Audit agent (per-agent) | yes | yes | no (`ValueError: unknown agent`, no charge/log) |
| List agents | any owner / all | own only; anything else → `[]` | `[]` |
| Read audit log | all | own entries only | own entries only |

### 4.4 Kill switch

Stored in `meta` (`kill_switch` = `on`/`off`, default `off`). Only
Lucas can toggle (`Control.set_kill_switch`, `PermissionError`
otherwise). When ON, `Factory.create_agent`,
`Control.execute_agent` and `Control.execute_isolated` raise
`KillSwitchOn`. Toggling appends `kill_switch_on` / `kill_switch_off`
to the log. Per-agent kill is `kill_agent` → status `killed`;
killed/suspended agents cannot execute.

## 5. API keys — states and metadata available today

Table `api_keys` (`store.py` + `auth.py`):

| Column | Type / values | Notes |
|---|---|---|
| `public_id` | TEXT PRIMARY KEY, 12 hex chars (6 bytes) | Public identifier, shown in listings. |
| `owner` | TEXT NOT NULL | Key owner. Admin = `lucas` only, server-assigned. |
| `key_hash` | TEXT NOT NULL | HMAC-SHA256 of `"<public_id>:<secret>"` with the per-DB server secret. Raw key is never stored, logged, or returned after creation. |
| `created_at` | TEXT NOT NULL | ISO-8601 UTC at creation. The ONLY time metadata a key has. |
| `revoked` | INTEGER 0/1 | Revocation is immediate; revoked keys fail authentication. |
| `revoked_at` | TEXT, nullable | Set on revocation; NULL while active. |

**What does NOT exist:** no expiry date, no `last_used_at`, no usage
counter, no label/purpose, no per-key rate limit, no scope field.
Key age policy can only be computed from `created_at`. Whether a key
was ever used, or used after revocation was attempted, is not
recorded anywhere (failed authentications are not logged — §1.3).
The server secret itself lives in `meta` (`server_secret`, 32 bytes
hex, generated once per DB) — a local-MVP limitation (vuln 10):
anyone who can read the DB file can read it.

## 6. Proposed rules

Status values: `PASS`, `WARN`, `FAIL`, `UNVERIFIED`.
Severity values: `low`, `medium`, `high`, `critical`.
Every rule is a deterministic function of the database snapshot at
audit time. A rule returns exactly one finding (or none, when it
passes cleanly — see §8.2: passing rules produce a PASS finding so
the report is a complete record, except pure informational checks
which produce an `info` PASS finding).

`evidence_ids` contain only identifiers (agent ids, execution ids,
audit-log ids `#n`, key `public_id`s). Never a key, a hash, a secret,
a balance history, or personal data beyond the owner string already
visible to the admin.

### 6.1 PERM — permissions, grants, containment

| Rule | Check (exact criteria) | PASS | WARN | FAIL |
|---|---|---|---|---|
| PERM-001 | No agent holds a forbidden permission (`config.FORBIDDEN_PERMISSIONS`). One finding per offending agent, listing the permission(s). | No agent has any | — | ≥ 1 agent has ≥ 1 |
| PERM-002 | No agent holds a permission outside `ALLOWED_PERMISSIONS` ∪ `FORBIDDEN_PERMISSIONS` (unknown permission). | No agent has any | — | ≥ 1 agent has ≥ 1 |
| PERM-003 | No agent holds `self_modify`, `self_modify_permissions`, `admin` or `create_agent`. (Subset of PERM-001, kept separate because it is the recursion/self-modification risk, the highest-severity class.) | No agent has any | — | ≥ 1 agent has ≥ 1; severity `critical` |
| PERM-004 | No agent holds a tool outside `ALLOWED_TOOLS` ∪ `FORBIDDEN_TOOLS` (unknown tool), and no forbidden tool (`write`, `execute_unlimited`, `create_agent`). | No agent has any | — | ≥ 1 agent has ≥ 1 |
| PERM-005 | Agent depth / parent: `depth <= MAX_DEPTH` (1) and `parent_id IS NULL` for every agent. | All satisfy | `parent_id` set on any agent (severity `medium`; creation code forbids it, so its presence means direct DB manipulation or a bug) | `depth > 1` on any agent |
| PERM-006 | Active-agent cap: `count_active(owner) <= MAX_ACTIVE_PER_USER` (3) for every owner. | All owners ≤ 3 | — | Any owner > 3 (means cap bypassed — direct insert or a bug — since `Factory` enforces it) |
| PERM-007 | Agent status is one of `active`, `suspended`, `killed`. | All valid | — | Any other value (only reachable by direct DB manipulation; `update_agent_status` rejects others) |
| PERM-008 | `users.is_admin` agrees with the name rule: `is_admin = 1` iff `owner = "lucas"`. | All rows agree | — | Any row disagrees (flag is unused for authorization, so this is a data-integrity FAIL, severity `high`) |
| PERM-009 | Kill-switch state is consistent: if `meta.kill_switch = "on"`, a `kill_switch_on` entry exists and no later `kill_switch_off` entry exists; if `"off"` (or absent), no unmatched `kill_switch_on` is the latest toggle entry. | Consistent | Latest toggle entry and stored value disagree (severity `medium`; indicates direct `meta` manipulation or a missing log entry) | — |
| PERM-010 | No agent holds `execute_limited` unless it was explicitly granted at creation. **Not directly checkable:** the DB records only the current permission list, not how it got there. | — | — | See §7.4: classified NOT VERIFIABLE; the rule as stated cannot be implemented from recorded data. A weaker check — "agents with `execute_limited` are listed for review" — is an `info` finding, not a PASS/FAIL. |

### 6.2 CREDIT — credit ledger reconciliation

| Rule | Check (exact criteria) | PASS | WARN | FAIL |
|---|---|---|---|---|
| CREDIT-001 | No agent exceeds its cap: `credits_used <= max_credits` for every agent. | All satisfy | — | Any agent exceeds (severity `high`) |
| CREDIT-002 | No agent at/above 80% of its cap: `credits_used / max_credits < 0.8` (skip when `max_credits = 0`). | All below 80% | Any agent ≥ 80% (severity `low`) | — |
| CREDIT-003 | No agent balance is negative: `credits_used >= 0` and `users.credits_balance >= 0` for every row. | All ≥ 0 | — | Any negative (severity `high`; `refund` does not guard against over-refund) |
| CREDIT-004 | Exact charge ↔ execution reconciliation: for every `finished` execution, exactly one matching `credit_charge` entry exists (same `agent_id`, amount = `minutes × COST_EXECUTION_PER_MIN`), and every `credit_charge` for executions matches one execution. | — | — | **NOT VERIFIABLE — see §7.1.** No ledger table, no charge id, no amount column; charges exist only as free-text `detail` strings. In Phase 3 this rule returns UNVERIFIED (one finding, severity `medium`, explaining the missing data). |
| CREDIT-005 | Refund ↔ refused/failed execution (approximate): for every `credit_refund` audit entry with `target` = agent id, there exists an `executions` row for that agent with `status` IN (`refused`, `failed`) and `ended_at <=` the refund entry's `ts`. | Every refund matches ≥ 1 such row | A refund has no matching row (severity `medium`) — with the caveat that matching is by agent + time, not by id, so a WARN here means "unexplained refund", not "proven fraud" | — |
| CREDIT-006 | Admin bypass accounting: for every `executions` row with `actor = "lucas"` and agent owner ≠ `"lucas"`, a `credit_bypass` entry with the same `target` (agent id) exists; and for every `admin_execute` entry a `credit_bypass` entry with the same `target` exists. | All match | A bypass entry is missing its `admin_execute` counterpart, or vice versa (severity `medium`) | — |
| CREDIT-007 | Stale `running` executions: no `executions` row has `status = "running"` with `started_at` older than 1 hour. (A simulated execution finishes immediately; an isolated run is bounded by its timeout, max 5 min + supervisor overhead.) | None stale | — | ≥ 1 stale row (severity `high`; a stale `running` row poisons the concurrency cap — this is the exact Phase 2 step-2 bug class) |
| CREDIT-008 | Real API cost is accounted: for every agent with `real_api_cost > 0`, ≥ 1 `real_api_cost` audit entry with `target` = agent id exists. | All match | An agent has cost but no entry (severity `low`) | — |
| CREDIT-009 | Creation charge attribution: for every agent row with `owner ≠ "lucas"` and `credits_used >= 10`, a `credit_charge` entry with `detail` containing `create agent` and `target` = agent id OR actor = owner exists near `created_at`… **No.** This cannot be stated exactly: the creation charge is logged with `agent_id=None`, so its audit entry has `target = ""`. The only link is owner + timestamp proximity. | — | — | **NOT VERIFIABLE exactly — see §7.1.** Phase 3 reports it as UNVERIFIED together with CREDIT-004. |

### 6.3 LOG — audit-log integrity (internal consistency only)

| Rule | Check (exact criteria) | PASS | WARN | FAIL | UNVERIFIED |
|---|---|---|---|---|---|
| LOG-001 | ID sequence: `audit_log.id` is strictly increasing with no gaps when read in insertion order. | No gaps | ≥ 1 gap (severity `low`; gaps can occur on failed/rolled-back transactions, so a gap is suspicious, never proof of deletion) | — | — |
| LOG-002 | ID uniqueness: no duplicate `id` values. | Unique | — | Any duplicate (severity `critical`; impossible through SQLite `PRIMARY KEY` — its presence means the table was rebuilt) | — |
| LOG-003 | Timestamp order: `ts` is non-decreasing when rows are ordered by `id`. | Non-decreasing | Any decrease (severity `low`; wall-clock `ts` can go backwards under clock adjustment, so this is a data-quality WARN, not tamper proof) | — | — |
| LOG-004 | Action vocabulary: every `action` is in the §1.3 list. | All known | Any unknown action (severity `medium`; indicates code added an action without updating the inventory, or a fabricated row) | — | — |
| LOG-005 | Tamper-evidence: the log has not been altered since the events were written. | — | — | — | **Always UNVERIFIED in Phase 3** — no hash chain, no signature, no external anchor exists (§3.2). A PASS here would be a lie. |
| LOG-006 | Completeness vs. expected events: the log contains the events the code paths must have produced (e.g. one `create_agent` entry per agent row). Checkable case: for every `agents` row, ≥ 1 `create_agent` entry with `target` = agent id exists. | All agents have one | An agent has none (severity `medium`; the agent row was inserted without the Factory path — direct insert or log loss) | — | — |

### 6.4 KEY — API key hygiene

| Rule | Check (exact criteria) | PASS | WARN | FAIL |
|---|---|---|---|---|
| KEY-001 | Revocation consistency: `revoked = 1` iff `revoked_at IS NOT NULL`. | All rows consistent | — | Any row inconsistent (severity `high`; `revoke_api_key` always sets both, so inconsistency means direct DB manipulation) |
| KEY-002 | Key age policy (parameter, default 180 days, stored in the report as `policy.key_max_age_days`): no active key has `created_at` older than the policy. | No active key older than the policy | ≥ 1 active key older (severity `low`; a policy alert, NOT proof of compromise — LTP-036 correction C) | — |
| KEY-003 | Admin key count: exactly 1 active key for owner `"lucas"` is expected in normal operation; more than one active admin key, or zero active admin keys, is flagged. | Exactly 1 active admin key | 0 active admin keys (severity `high`; admin is locked out of key-based access) or > 1 (severity `medium`; hygiene) | — |
| KEY-004 | No duplicate `public_id`. | Unique | — | Any duplicate (severity `critical`; impossible through `PRIMARY KEY` — table rebuilt) |
| KEY-005 | Revoked key used after revocation. | — | — | **NOT VERIFIABLE — see §7.2.** Failed authentications are never logged; a revoked key fails in `Auth.authenticate` before any audit call. |
| KEY-006 | Server secret present: `meta.server_secret` exists and is a 64-hex-char string. | Present and well-formed | Missing (severity `low`; it will be generated on next use — but its absence in a long-lived DB means keys were never created or the meta row was deleted) | Malformed value (severity `high`) |

### 6.5 SEC — observable security events

| Rule | Check (exact criteria) | PASS | WARN | FAIL |
|---|---|---|---|---|
| SEC-001 | No successful cross-user action: no `execute_agent` / `execute_isolated` / `audit_agent` audit entry has `actor` ≠ agent owner and `actor` ≠ `"lucas"`. (Resolved by joining the entry's `target` agent id to `agents.owner`.) | None found | — | ≥ 1 found (severity `critical`; the code paths forbid this, so its presence means direct log fabrication, a bug, or a bypass of the library) |
| SEC-002 | No `kill_switch_on` / `kill_switch_off` entry has `actor` ≠ `"lucas"`. | None found | — | ≥ 1 found (severity `critical`) |
| SEC-003 | No `create_api_key` / `revoke_api_key` entry has `actor` ≠ `"lucas"` and `actor` ≠ `"bootstrap"`. (Bootstrap is the legitimate first-key path.) | None found | — | ≥ 1 found (severity `critical`) |
| SEC-004 | No `admin_execute` entry without a matching `credit_bypass` entry for the same `target` (same as CREDIT-006, reported here as the security view of the same check; the two rules share one implementation and one finding id — see note below). | Matches | Mismatch (severity `medium`) | — |
| SEC-005 | Unsafe-dev runs are visible: count `execute_isolated` entries whose `detail` contains `unsafe_dev=True`. Each one is reported as an `info` finding (not a violation — the mode is legitimate for dev), so the report always shows how many unisolated runs happened. | Zero runs | ≥ 1 run (severity `low`, status WARN so it is never silent) | — |
| SEC-006 | Denied cross-user attempts in the log. | — | — | **NOT VERIFIABLE — see §7.2.** Denied attempts are never logged by design (LTP-022): the code raises before any audit call. There is nothing to count. |
| SEC-007 | Executions by suspended/killed agents. | — | — | **NOT VERIFIABLE exactly — see §7.5.** The DB records the agent's *current* status, not its status at execution time; an agent suspended after executing is indistinguishable from one suspended before executing. |

Note on SEC-004 / CREDIT-006: the same underlying check must not
produce two findings with different ids. Implementation rule: one
check, one finding; it is filed under CREDIT-006 and *referenced*
from the SEC section of the summary, not duplicated.

## 7. NOT VERIFIABLE — checks the data does not support

Every item below is reported by the Phase 3 service as a single
UNVERIFIED finding (status `UNVERIFIED`, severity `medium` unless
noted), with the reason and the minimal schema change that would make
it verifiable. None of these changes is implemented in Phase 3.

### 7.1 CREDIT-004 / CREDIT-009 — exact charge ↔ execution reconciliation

**Why not verifiable:** there is no ledger table, no charge id, no
`amount` column, and no link from a charge to the execution it paid
for. Charges exist only as free-text `audit_log.detail` strings
("charged 1 credits (execution 1 min); balance now 99"), and the
creation charge is logged with `agent_id=None` (`target = ""`).
Reconciliation would require parsing English prose with a regex and
guessing the link by owner + timestamp proximity. That is not an
audit — it is a guess.

**Minimal schema change (proposed, NOT implemented):** add a
`ledger` table:

```sql
CREATE TABLE ledger (
    id TEXT PRIMARY KEY,              -- uuid, one row per money movement
    ts TEXT NOT NULL,                 -- ISO-8601 UTC
    actor TEXT NOT NULL,              -- owner charged/refunded, or "lucas"
    agent_id TEXT,                    -- NULL for audit charges (no agent row)
    kind TEXT NOT NULL,               -- 'charge' | 'refund' | 'bypass'
    amount INTEGER NOT NULL,          -- credits, >= 0
    reason TEXT NOT NULL,             -- e.g. 'create', 'execution', 'audit', 'isolated_execution'
    ref_id TEXT,                      -- executions.id / agents.id the movement pays for, NULL if none
    balance_after INTEGER             -- balance after this movement (NULL for admin bypass)
);
```

and make `ResourceCore.charge` / `refund` write one `ledger` row
atomically with the balance update (same transaction), instead of
relying on the free-text audit entry. The audit-log entries stay as
they are (human-readable trail); the ledger becomes the
machine-readable source of truth. With it, CREDIT-004 becomes:
"for every `executions` row with `status = 'finished'`, exactly one
`ledger` row with `kind = 'charge'`, `ref_id = executions.id` and
`amount = minutes × COST_EXECUTION_PER_MIN` exists" — exact, no
parsing. Estimated size: one table + two methods, no changes to
callers (they already pass `reason` and `agent_id`).

### 7.2 SEC-006 / KEY-005 — denied attempts and revoked-key use

**Why not verifiable:** failed authentications and denied
authorizations are never written anywhere. `Auth.authenticate`
raises `AuthenticationFailed` before any audit call; `execute_agent`
and `audit_agent` raise before any charge or log entry — by design
(LTP-022: a denied attempt must not even reveal that the target
exists). The log therefore contains only events that were allowed
(or performed by the admin). Counting "attempts" from it is counting
from an empty set.

**Minimal schema change (proposed, NOT implemented):** a separate
`security_events` table, written on the denial paths only, containing
no secrets and no existence-revealing data:

```sql
CREATE TABLE security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,        -- 'auth_failed' | 'forbidden_actor' | 'unknown_agent_denied'
    public_id TEXT,             -- key public_id when a key was presented, else NULL
    target_kind TEXT,           -- 'agent' | 'key' | 'log' | NULL
    detail TEXT NOT NULL DEFAULT ''   -- free text, MUST NOT contain a secret, a hash, or a target id that would confirm existence to a non-admin reader
);
```

Rules: write one row in `Auth.authenticate` on every failure (with
`public_id` only when the key parsed — a malformed key has no id to
record); write one row in `Control.execute_agent` / `Auditor.audit_agent`
on the non-owner denial path (with `target_kind = 'agent'` and NO
agent id — the id itself would confirm existence). The table is
admin-read-only (same ownership rule as the audit log: only Lucas).
With it, SEC-006 becomes "count `forbidden_actor` events per day;
alert above a threshold" and KEY-005 becomes "a `public_id` that
appears in `auth_failed` after its `revoked_at`". Note the tension:
logging denials re-introduces a small existence signal (an attacker
who can read the table learns which ids were tried) — that is why
the table must be admin-only and must never contain full key
material. This is a design decision for Lucas/ChatGPT, not a
foregone conclusion.

### 7.3 LOG-005 — tamper-evidence of the audit log

**Why not verifiable:** no hash chain, no signature, no external
anchor; triggers block UPDATE/DELETE through SQLite but not direct
file edits, trigger drops, INSERTs of fabricated rows, or whole-DB
replacement (§3.2).

**Minimal schema change (proposed, NOT implemented):** add a
hash-chain column to `audit_log` — `prev_hash TEXT` and
`entry_hash TEXT` where
`entry_hash = SHA256(prev_hash || ts || actor || action || target ||
detail)`, computed in `Store.append_audit` and verified by a LOG rule
that recomputes the chain from row 1. This detects *accidental*
corruption and *naive* tampering (editing one row breaks the chain),
but it does NOT stop an attacker with DB write access: they can
recompute the whole chain. Full tamper-evidence needs an external
anchor (e.g. periodic hash publication to a separate store, or
append-only remote logging) — that is out of scope for this MVP and
is recorded here only so nobody mistakes the chain for a guarantee.
With the chain, LOG-005 becomes FAIL on a broken link, WARN on a
missing anchor, and stays UNVERIFIED for "the log was not replaced
wholesale". Estimated size: two columns + ~15 lines in `append_audit`
+ one rule. A migration must backfill existing rows (chain from
genesis with `prev_hash = "GENESIS"`).

### 7.4 PERM-010 — was `execute_limited` explicitly granted?

**Why not verifiable:** the DB stores only the *current*
permission list of each agent. Nothing records *how* a permission
got there (template default vs. explicitly passed at creation).
`execute_limited` is in no template, so its presence implies it was
passed explicitly — but "implies" is not evidence: a direct DB edit
could add it, and the check cannot distinguish the two cases.

**Minimal schema change (proposed, NOT implemented):** add
`granted_permissions TEXT` (JSON: `{"permission": "explicit" |
"template", ...}`) to `agents`, written by `Factory.create_agent`
at creation time. With it, PERM-010 becomes: "every permission
marked `explicit` was passed in the creation call (cross-checkable
against the `create_agent` audit entry's detail only if the call
arguments are logged — they are not today, so the check stays at
'granted-by-template vs granted-explicitly is recorded and
consistent')". Full provability would also require logging creation
arguments — a further decision, not assumed here.

### 7.5 SEC-007 — executions by suspended/killed agents

**Why not verifiable:** `agents.status` is current state only.
`executions` rows record `started_at` / `ended_at` but not the
agent's status at that time, so an agent suspended *after* executing
is indistinguishable from one suspended *before* executing.

**Minimal schema change (proposed, NOT implemented):** add
`agent_status_at_start TEXT` to `executions`, written by
`Store.start_execution` from the agent row at call time. With it,
SEC-007 becomes exact: "no `executions` row has
`agent_status_at_start` IN (`suspended`, `killed`)". (The code
already forbids executing a non-active agent, so a FAIL here would
mean the check was bypassed — direct insert or a bug.)

### 7.6 Summary of NOT VERIFIABLE items

| Rule | Missing data | Minimal change |
|---|---|---|
| CREDIT-004, CREDIT-009 | No ledger; charges are free text, creation charge has no agent link | `ledger` table (§7.1) |
| SEC-006, KEY-005 | Denials and failed auth are never recorded | `security_events` table (§7.2) |
| LOG-005 | No hash chain / signature / external anchor | Hash-chain columns (§7.3) |
| PERM-010 | Grant provenance not recorded | `granted_permissions` on `agents` (§7.4) |
| SEC-007 | Agent status at execution time not recorded | `agent_status_at_start` on `executions` (§7.5) |

## 8. Report model

### 8.1 `audit_reports` table (new, Phase 3)

```sql
CREATE TABLE audit_reports (
    report_id TEXT PRIMARY KEY,     -- uuid hex (12 chars, same style as agent/execution ids)
    requested_by TEXT NOT NULL,     -- owner who ran the audit; always "lucas" in Phase 3
    scope TEXT NOT NULL,            -- 'global' (only value in Phase 3)
    created_at TEXT NOT NULL,       -- ISO-8601 UTC
    ruleset_version TEXT NOT NULL,  -- e.g. '3.0.0'
    status TEXT NOT NULL,           -- 'pass' | 'warn' | 'fail' (worst finding status; see §8.3)
    findings TEXT NOT NULL,         -- JSON array of finding objects (§8.2)
    summary TEXT NOT NULL           -- JSON object (§8.3)
);
```

Access: written only by the audit service. Read only by Lucas
(admin). There is no CLI command for a common user to list or read
reports in Phase 3 (a common user asking for one gets the generic
authentication/authorization failure, with no hint that reports
exist). Reports are never deleted by the service (no retention
policy in Phase 3 — noted as a future decision; the table is
append-only in practice, enforced by having no delete API).

### 8.2 Finding shape

```json
{
  "rule_id": "CREDIT-003",
  "status": "WARN",
  "severity": "medium",
  "title": "Cobrança sem execução correspondente",
  "evidence_ids": ["event-123", "charge-456"],
  "recommendation": "Investigar a transição de estado"
}
```

- `rule_id`: unique per rule (PERM-*, CREDIT-*, LOG-*, KEY-*, SEC-*).
- `status`: `PASS` | `WARN` | `FAIL` | `UNVERIFIED`.
- `severity`: `low` | `medium` | `high` | `critical`. (The old
  per-agent Auditor used `info` / `warning` / `critical`; the new
  model separates *status* from *severity* so a WARN can be `low`
  and an UNVERIFIED can be `medium`.)
- `title`: one line, human-readable, no data values.
- `evidence_ids`: identifiers only — agent ids, execution ids,
  audit-log ids (`#n`), key `public_id`s. NEVER an API key, a key
  hash, a secret, a balance history, or personal data beyond the
  owner string (which the admin already sees).
- `recommendation`: one line, what to do next.

Every rule that runs produces exactly one finding, including PASS
findings — the report is a complete record of what was checked, not
only of what failed. The one exception: SEC-005 produces one finding
per run summarising the count of unsafe-dev runs (not one finding per
run), so the report stays bounded.

### 8.3 Overall status and summary

- `status` = `fail` if any finding is FAIL; else `warn` if any finding
  is WARN; else `pass`. UNVERIFIED findings do not change the overall
  status, but they are counted separately and the disclaimer (§0)
  applies with extra force when any UNVERIFIED exists.
- `summary` JSON:

```json
{
  "rules_run": 38,
  "counts_by_status": {"PASS": 28, "WARN": 3, "FAIL": 0, "UNVERIFIED": 7},
  "counts_by_severity": {"low": 3, "medium": 6, "high": 1, "critical": 0},
  "scope": "global",
  "snapshot": {"users": 4, "agents": 7, "executions": 12,
               "audit_log_rows": 58, "api_keys": 3},
  "disclaimer": "A report with all PASS does not mean the system is secure — it means the implemented checks found no violations within the examined scope."
}
```

`snapshot` counts let a later reader tell whether the database the
report describes is the one in front of them.

### 8.4 Cost, ordering, and the self-reference problem

- A global audit costs `COST_AUDIT` = 1 credit, same as the
  per-agent audit; for Lucas (the only caller in Phase 3) the admin
  bypass applies and the bypass is logged, exactly as
  `ResourceCore.charge` already does. (Decision, consistent with
  existing behaviour — not a new rule.)
- Ordering: the service (1) takes a snapshot (reads all tables it
  needs), (2) runs every rule against the snapshot, (3) writes the
  `audit_reports` row, (4) appends ONE `audit_agent` audit-log entry
  recording that the audit ran (`target` = the `report_id`,
  `detail` = counts by status). Step 4 happens after the rules have
  read, so the audit's own log entry cannot influence its own
  result. The entry is the audit trail of the audit — it is not
  part of the audited data.
- Reproducibility: same database + same `ruleset_version` ⇒ same
  findings. The report records `ruleset_version` and `scope`, so two
  reports are comparable only when both match.

## 9. Target module layout

LTP-036 §2 proposes `ai_factory/auditor/` — **that path does not
exist in this repo**: the package is `app/`, not `ai_factory/`.
The target layout below maps the proposal onto the real package,
with one correction (§9.2).

```
app/
  auditor/                  # NEW package (Phase 3)
    __init__.py             # exposes run_global_audit(store, actor) -> report dict
    service.py              # AuditService: authz, snapshot, orchestration, report persistence
    models.py               # Finding, Report, RulesetVersion; JSON (de)serialisation
    rules/
      __init__.py           # RULES registry: rule_id -> function; ruleset version
      permissions.py        # PERM-001..010
      credits.py            # CREDIT-001..009
      audit_log.py          # LOG-001..006
      api_keys.py           # KEY-001..006
      security_events.py    # SEC-001..007
  agent_auditor.py          # MOVED, unchanged: today's app/auditor.py (per-agent Auditor)
  cli.py                    # + one command: audit-global (admin only, see §9.3)
  store.py                  # + audit_reports table + insert/get/list methods
```

### 9.1 Rules contract

Each rule is a function `rule(snapshot) -> Finding | None` where
`snapshot` is a plain-dict read of the tables the rule needs
(`users`, `agents`, `executions`, `audit_log`, `api_keys`, `meta`).
Rules are deterministic, side-effect-free, and never touch the CLI,
the network, or the filesystem. A rule returns `None` only when it
does not apply at all (e.g. a rule about isolated executions when
no isolated execution exists — in which case no finding is emitted,
rather than a vacuous PASS). The registry maps `rule_id` → function
and carries the `ruleset_version`; adding a rule bumps the version.

### 9.2 Naming collision — must be decided before implementation

Python cannot have both `app/auditor.py` (the existing per-agent
Auditor module) and `app/auditor/` (the new package) in the same
package. The move proposed above — `app/auditor.py` →
`app/agent_auditor.py`, pure move, no behaviour change, imports in
`app/cli.py` updated — is the minimal resolution. Alternative (no
move): put the service in flat modules `app/audit_service.py` +
`app/audit_rules.py`. The move is preferred because it keeps the
LTP-036 layout; the flat alternative is the fallback if the move is
rejected. Either way, this is a decision for Lucas/ChatGPT, not
something to improvise during implementation.

### 9.3 CLI

One new command, admin only:

```
python -m app.cli --api-key <lucas-key> audit-global [--format json|text]
```

Flow: `Auth.authenticate` → `require_admin` (non-admin gets the
generic failure; the command's existence is not advertised to
non-admins — it appears in `--help` like any other command, which is
accepted: the help text reveals the command name, not any data) →
`AuditService.run_global_audit` → print the report (JSON, or a
human-readable text rendering) including the disclaimer. The command
charges 1 credit via the normal path (admin bypass logged).

## 10. Synthetic test plan (tests are NOT written in this document)

Test file (proposed): `tests/test_auditor_phase3.py`. All fixtures
are synthetic, built in-memory (`Store(":memory:")`), using the
low-level `Store.insert_agent` / direct SQL inserts that the
existing test-suite already uses to create rows the Factory would
never create (see the `insert_agent` comment in `store.py`).
No fixture touches Lucas's real database.

### 10.1 One fixture per finding type

For every rule in §6, at least two fixtures:

- **Positive fixture** (rule fires): a DB state that triggers the
  finding with the exact expected `rule_id`, `status` and
  `severity`. Examples: an agent inserted with permission
  `create_agent` (PERM-001/003 FAIL); an agent with
  `credits_used = 60, max_credits = 50` (CREDIT-001 FAIL);
  `credits_used = 45, max_credits = 50` (CREDIT-002 WARN);
  an `audit_log` with a manually deleted id (gap → LOG-001 WARN);
  a key row with `revoked = 1, revoked_at = NULL` (KEY-001 FAIL);
  an `execute_agent` log entry whose actor is neither the agent
  owner nor lucas (SEC-001 FAIL); a `kill_switch_on` entry with
  actor `alice` (SEC-002 FAIL).
- **Negative fixture** (rule passes): the same shape in a clean
  state, expecting a PASS finding — including the boundary values
  (79% of cap passes CREDIT-002; exactly 80% warns; `credits_used
  == max_credits` passes CREDIT-001 and warns CREDIT-002).

### 10.2 False positives and inconclusive cases

- LOG-001: a gap created by a rolled-back transaction (simulate by
  inserting then deleting a row through a *second* connection that
  bypasses the trigger — or by inserting with an explicit high id)
  must yield WARN, never FAIL.
- LOG-003: rows inserted with out-of-order `ts` (clock skew) must
  yield WARN, never FAIL.
- CREDIT-005: a refund whose matching `refused` execution belongs
  to a *different* agent with a close timestamp must NOT match
  (agent-scoped matching), and an ambiguous match must yield WARN
  with the caveat in the finding, never a confident FAIL.
- KEY-002: a key created 181 days ago must warn; a key created 179
  days ago must pass; the policy value (`key_max_age_days`) must
  appear in the report so the threshold is auditable.
- A completely clean synthetic DB must produce all PASS except the
  structural UNVERIFIED findings (CREDIT-004, CREDIT-009, LOG-005,
  SEC-006, KEY-005, PERM-010, SEC-007) — i.e. a clean system still
  reports what it *cannot* verify. A test must assert exactly that
  set of UNVERIFIED ids on a clean DB, so a future rule that
  silently stops reporting an unverifiable check fails the suite.

### 10.3 Isolation tests

- A non-admin API key (e.g. bob) running `audit-global` via the CLI
  fails (exit 1, generic failure) and creates NO report row and NO
  audit-log entry for the attempt.
- A non-admin calling `AuditService.run_global_audit` directly
  raises `PermissionError`.
- A global report contains no rows, ids, counts or details about
  any user other than through the aggregated `snapshot` counts —
  and the snapshot counts are admin-only output (the CLI prints the
  report only to the authenticated admin). A test asserts that no
  finding's `evidence_ids` and no summary field contains another
  user's data beyond what the admin is already entitled to see.
  (In Phase 3 the only reader is Lucas, so this is defence in
  depth; it becomes load-bearing if individual audit is ever added.)
- The service never writes to `users`, `agents`, `executions`,
  `api_keys` or `meta`: snapshot those tables before and after a
  run and assert equality (same technique as the existing
  read-only proof in `auditor.py`).

### 10.4 Regression

The full existing suite must stay green: **88 tests** (77 pass, 11
Windows-only skipped) — see §11.1. The Phase 3 suite adds its own
file; it must not modify any existing test and must not weaken any
existing assertion. The `audit_reports` table is additive; no
existing table changes in Phase 3 (all §7 schema changes are
proposals for later phases, explicitly NOT implemented here).

### 10.5 Report and CLI tests

- Report round-trip: run the service twice on the same DB → two
  reports with the same findings (except `report_id` /
  `created_at`), same `ruleset_version`, same `status`.
- `audit-global` CLI output (text format) contains the disclaimer
  verbatim; JSON output parses and contains `report_id`,
  `ruleset_version`, `findings`, `summary`.
- The audit's own log entry appears AFTER the audited snapshot:
  run the service, then assert the newest `audit_log` row is the
  `audit_agent` entry with `target` = the new `report_id`, and
  that a second run's findings do not include that entry as an
  anomaly (LOG-004 vocabulary check must accept it).

## 11. Corrections to assumptions in LTP-035 / LTP-036

### 11.1 Baseline is 88 tests, not 55 (LTP-036 §6 item 11)

LTP-036 §6 item 11 says "Os 55 testes anteriores permanecem
verdes". 55 was the baseline after Phase 2 step 1 (auth). Phase 2
step 2 (executor, `cb155c4`) added 33 tests, bringing the suite to
**88 tests: 77 pass, 11 skipped** (the 11 are Windows-only
adversarial tests that skip on Linux — a skip is not a pass).
Verified by running `python3 -m unittest discover -s tests` on
2026-11-18 while writing this document: `Ran 88 tests — OK
(skipped=11)`. The Phase 3 acceptance criterion is therefore:
**all 88 existing tests stay green (77 pass + 11 skip), and no
existing assertion is weakened.** The number 55 in LTP-036 is
stale, not wrong at the time it was written.

### 11.2 Module path: `ai_factory/` does not exist (LTP-036 §2)

LTP-036 §2 proposes `ai_factory/auditor/service.py` etc. The repo
package is `app/`, not `ai_factory/`. The layout in §9 maps the
proposal onto `app/auditor/`. If a rename of the package to
`ai_factory` is wanted, that is a separate decision (it would touch
every import and the CLI invocation `python -m app.cli`).

### 11.3 Naming collision: `app/auditor.py` vs `app/auditor/` (§9.2)

Both cannot exist. Resolution proposed in §9.2 (move the per-agent
Auditor to `app/agent_auditor.py`, pure move). This was not
mentioned in LTP-036 and must be decided before implementation.

### 11.4 There is no credit ledger to reconcile (LTP-035, LTP-036 §1)

LTP-035 proposes "verificar a consistência do livro de créditos
(cobranças vs. registros de execução, reembolsos)" and LTP-036 §1
calls the ledger "essential". In the real code there is no ledger:
charges exist only as free-text audit details and as balance deltas
(§2.1). Exact reconciliation is NOT VERIFIABLE in Phase 3
(CREDIT-004, CREDIT-009, §7.1). The ledger table is proposed as
the minimal change for a later phase. The Phase 3 Auditor checks
the structured invariants that *do* exist (CREDIT-001/002/003/005/
006/007/008) and reports the rest as UNVERIFIED — it does not
pretend to reconcile what cannot be reconciled.

### 11.5 Denied attempts are invisible by design (LTP-035)

LTP-035 proposes "varrer o log por tentativas de violação de
isolamento". Denied attempts are never logged — the code raises
before any audit call, deliberately, so a denied attempt does not
even confirm the target exists (§1.3). The SEC rules can therefore
detect *successful* violations (which should be impossible through
the code paths — their presence means fabrication or a bug), not
*attempts*. Detecting attempts needs the proposed
`security_events` table (§7.2), which is itself a design decision
(it re-introduces a small existence signal) and is NOT part of
Phase 3.

### 11.6 Log append-only ≠ tamper-evident (LTP-036 correction A — agreed, extended)

LTP-036 already corrects this; the code confirms it and adds two
details: the triggers do not stop INSERTs of fabricated rows, and
they do not stop whole-DB replacement (§3.2). LOG-005 is therefore
permanently UNVERIFIED in Phase 3, and the hash-chain proposal in
§7.3 is explicitly documented as detecting naive tampering only,
not a guarantee.

### 11.7 Key hygiene has only one time field (LTP-036 §1)

Key-age policy can only use `created_at` — there is no
`last_used_at`, no expiry, no usage counter (§5). KEY-002 is a
policy alert based on age alone. Whether a key was ever used, or
used after revocation, is unknowable from the current data (KEY-005,
§7.2).

### 11.8 `users.is_admin` is not an authorization source (§4.3)

The column exists and is set at creation, but every authorization
check in the code compares the owner string to `ADMIN_USER`
(`"lucas"`). PERM-008 checks the column's consistency as a
data-integrity rule, not as a permission rule — because even a
correct flag grants nothing by itself.

## 12. Acceptance criteria (mapped to LTP-036 §6)

| # | LTP-036 criterion | How Phase 3 meets it (design) |
|---|---|---|
| 1 | Global audit only with authenticated admin identity | `Auth.authenticate` + `require_admin` in service and CLI (§9.3); isolation tests (§10.3) |
| 2 | Rules without side effects | §9.1 contract; snapshot-in / finding-out; read-only proof test (§10.3) |
| 3 | No LLM calls | Stdlib only; rules are pure functions over the snapshot |
| 4 | No arbitrary code execution | Executor not used; no CLI path to it (unchanged) |
| 5 | No modification of audited entities | Only writes: `audit_reports` row + one audit-log entry after the snapshot (§8.4) |
| 6 | Persistence only of the report | `audit_reports` is the only new table; no schema changes to existing tables |
| 7 | Synthetic-data tests for every finding type | §10.1 |
| 8 | Isolation tests: common user cannot run global audit | §10.3 |
| 9 | Tests for false positives and inconclusive cases | §10.2 |
| 10 | Reproducible report with ruleset version | §8.3–8.4; report round-trip test (§10.5) |
| 11 | Previous tests stay green | **88 tests** (77 pass + 11 Windows-only skipped) — corrected per §11.1 |
| 12 | No public deploy | Unchanged; nothing in Phase 3 deploys anything |

## 13. Open decisions for Lucas / ChatGPT (before implementation)

1. **Module move vs flat layout** (§9.2): move `app/auditor.py` →
   `app/agent_auditor.py` and create `app/auditor/`, or use flat
   `app/audit_service.py` + `app/audit_rules.py`?
2. **Package rename**: keep `app/` or rename to `ai_factory/`
   as LTP-036 §2 assumed?
3. **Key-age policy value**: default 180 days for KEY-002, or
   another value? (Recorded in the report as
   `policy.key_max_age_days` either way.)
4. **Global audit cost**: 1 credit with admin bypass logged (§8.4),
   as proposed — or free for the admin? (Consistent with the
   per-agent audit either way; the bypass is logged in both cases.)
5. **§7 schema changes**: approve none for Phase 3 (recommended),
   or pull the `ledger` table (§7.1) into Phase 3 because
   CREDIT-004 is the most valuable reconciliation? Note: pulling
   it in changes `ResourceCore.charge`/`refund` (the hottest path
   in the system) and needs its own review.
6. **Report retention**: no retention policy in Phase 3 (table
   grows by one row per audit). Accept, or set a cap now?
