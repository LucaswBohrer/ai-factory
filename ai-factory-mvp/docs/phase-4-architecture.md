# Phase 4 — Architecture Revision (Commercial Foundation)

**Date:** 2026-11-18
**Status:** DRAFT for ChatGPT review (LTP-047) — implementation NOT authorized
**Requested by:** ChatGPT, LTP-046 §6 (10 items, in order)
**Direction:** approved by ChatGPT (LTP-046); scope/first stage still
needs Lucas's explicit approval after this review.
**Baseline:** Phase 3 closed (LTP-044). 159 tests (148 pass, 11
Windows-only skipped). Auditor ruleset v3.0.0, 38 rules, 31 PASS /
7 UNVERIFIED on a clean DB.

## Purpose and scope

Phase 4 prepares the economic and operational foundation for a future
commercial use of the AI Factory — without charging anyone, without a
payment gateway, without public deploy, without a real provider with
paid consumption, without arbitrary execution, without recursion, and
without any increase in agent autonomy (LTP-046 §5). It is a program
of four sequential stages:

| Stage | Name | Content |
|---|---|---|
| 4A | Economic ledger | Credit ledger, formal states, idempotency, reconciliation, Resource Core integration |
| 4B | Consumption accounting | Measurement interface, stub adapters, token and estimated-cost records linked to executions |
| 4C | Antifraud and operational security | Key usage, expiry, rate limits, security events, admin controls |
| 4D | Cryptographic integrity and Auditor v4 | Hash chain, checkpoints, integrity verification, Auditor ruleset v4.0.0 |

Complementary delivery, in parallel, document only: the commercial
pricing model (plans, free quota, credit price) — design, no gateway,
no charging.

**Reading conventions.** Every schema, table, column, function and
config name introduced here is a **proposal** and is marked
`[PROPOSED]`. Names without the marker already exist in the code.
Every claim about current behaviour cites the real file and function
it was read from. Nothing in this document is implemented; no file in
`app/` or `tests/` is changed by it.

## 0. What exists today (grounding)

- **Schema** (`app/store.py`, `SCHEMA`): `users` (`owner`,
  `plan`, `credits_balance INTEGER`, `is_admin`),
  `agents` (`credits_used INTEGER`, `max_credits INTEGER`,
  `real_api_cost REAL`, `permissions TEXT` JSON),
  `executions` (`id`, `agent_id`, `actor`, `minutes`, `status`,
  `started_at`, `ended_at` — no agent-status-at-execution-time),
  `audit_log` (`id INTEGER PRIMARY KEY AUTOINCREMENT`, `ts`,
  `actor`, `action`, `target`, `detail`; UPDATE/DELETE triggers),
  `api_keys` (`public_id`, `owner`, `key_hash`, `created_at`,
  `revoked`, `revoked_at` — no `last_used_at`, no expiry, no usage
  counter), `audit_reports`, `meta` (incl. the server secret).
- **Charges** (`app/resource_core.py`, `ResourceCore.charge` /
  `refund`): a charge reads the balance, calls
  `Store.set_balance` (commits immediately), then calls
  `Store.append_audit` with a free-text `detail` string
  ("charged 1 credits (execution 1 min); balance now 99") — a
  second, separate commit. There is no ledger table, no charge id,
  no amount column, no link from a charge to the execution it paid
  for, and the creation charge is logged with `agent_id=None`
  (`target = ""`). A crash between the two commits leaves the
  balance moved with no audit entry, or vice versa. Admin is never
  charged; the bypass is logged as `credit_bypass`.
- **Costs** (`app/config.py`): `FREE_QUOTA = 100`,
  `COST_CREATE = 10`, `COST_EXECUTION_PER_MIN = 1`,
  `COST_AUDIT = 1`, `PAID_PLANS = {"basic": 500, "pro": 2000}`
  (config placeholders only), `AUDIT_RULESET_VERSION = "3.0.0"`,
  `AUDIT_GLOBAL_MIN_INTERVAL_SECONDS = 60`,
  `AUDIT_KEY_MAX_AGE_DAYS = 180`.
- **Execution flow** (`app/control.py`, `Control.execute_agent` and
  `execute_isolated`): charge up front for the timeout budget
  (`ExecutionLimits.charge_minutes()` in `app/executor.py`: 1 credit
  per started minute, capped at 5), then run; on
  `IsolationRefused` or supervisor failure, `refund` and mark the
  execution `refused`/`failed`. The concurrency cap is a
  check-then-act: `Store.count_running_executions() >=
  MAX_CONCURRENT_EXECUTIONS` (2) is tested, then the charge and the
  `start_execution` insert happen in separate commits — two
  concurrent callers can both pass the check before either inserts.
  The same read-then-write race exists in `ResourceCore.charge`:
  balance is read, compared, then written in a later statement.
- **Auth** (`app/auth.py`): keys `afk_<public_id 12 hex>_<secret 64
  hex>`, HMAC-SHA256 with a per-database server secret stored in the
  `meta` table (`Store.get_or_create_server_secret` — documented
  local-MVP limitation), generic `AuthenticationFailed` for every
  failure mode, immediate revocation, admin = `lucas` assigned
  server-side. Keys never expire; there is no usage tracking.
  `Auth.authenticate` raises before any audit call, so failed
  authentications are never recorded anywhere.
- **Atomicity precedent** (`app/store.py`,
  `Store.persist_global_audit`): the audit report row and the
  `audit_global` log entry are inserted in ONE `BEGIN IMMEDIATE`
  transaction (both or neither). Phase 4 reuses this pattern for
  economic writes.
- **Auditor v3.0.0** (`app/auditor/`, `docs/auditor-architecture.md`
  §6–§7): 38 deterministic rules. The 7 UNVERIFIED items, with the
  minimal schema changes already proposed in
  `docs/auditor-architecture.md` §7 (this document formalises them,
  it does not re-invent them): CREDIT-004 / CREDIT-009 (no ledger),
  SEC-006 / KEY-005 (denials never recorded), LOG-005 (no hash
  chain), PERM-010 (grant provenance not recorded), SEC-007 (agent
  status at execution time not recorded).

---

## 1. Dependency diagram between 4A, 4B, 4C and 4D

Order is intentional (LTP-046 §2): the ledger establishes the
economic truth of the system; every later subsystem must produce
evidence that can be associated with that truth. The hash chain comes
last, after the event and record formats are stabilised — but its
design starts in 4A (which events it must protect is decided when
the ledger is designed, not when the chain is built).

```
                    ┌──────────────────────────────────┐
                    │  pricing-model.md  [PROPOSED]    │
                    │  document only — no code,        │
                    │  no charging, runs in parallel   │
                    └──────────────────────────────────┘

   ┌────────┐    ┌────────┐    ┌────────┐    ┌────────┐
   │  4A    │───▶│  4B    │───▶│  4C    │───▶│  4D    │
   │ Ledger │    │ Tokens │    │ Anti-  │    │ Hash   │
   │        │    │        │    │ fraud  │    │ chain  │
   │        │    │        │    │        │    │ + Aud. │
   │        │    │        │    │        │    │  v4    │
   └────────┘    └────────┘    └────────┘    └────────┘
      ▲                                        │
      │  integrity design starts here          │
      │  (which events the chain must protect) │
      └────────────────────────────────────────┘
              (design thread, not a code dependency)
```

| Stage | Consumes from previous stages | Produces for later stages |
|---|---|---|
| 4A Ledger | Existing `users.credits_balance`, `ResourceCore.charge`/`refund` call sites, `executions.id`, `agents.id` | `ledger` table [PROPOSED], formal state machine, idempotency keys, opening-balance genesis rows, reconciliation query |
| 4B Tokens | `ledger` (a usage record may reference a ledger operation, never the reverse), `executions.id` | `usage_records` table [PROPOSED], adapter interface [PROPOSED], origin/method on every record |
| 4C Antifraud | `api_keys` (new usage/expiry columns), `ledger` (abuse shows up as anomalous operations), `usage_records` (abuse shows up as anomalous consumption) | `security_events` table [PROPOSED], per-key/per-user limits, key usage counters |
| 4D Hash chain + Auditor v4 | All record formats above, stabilised (audit_log, audit_reports, ledger, security_events, usage_records) | Hash-chain columns, external checkpoint, Auditor ruleset v4.0.0, re-evaluation of the 7 UNVERIFIED items |

**Stage gates.** Each stage is delivered, tested and reviewed
separately (same pattern as the phases: build → LTP message →
ChatGPT review → Lucas approval) before the next one starts.
LTP-046 §1: the division does not mean all stages must be approved
in advance. A stage that fails its acceptance criteria blocks the
next stage; it does not silently roll into it.

**What does NOT depend on what.** The pricing-model document depends
on nothing and blocks nothing. 4B does not change the ledger: the
adapter contract must allow a real provider later without modifying
the ledger (see §4). 4C does not change the ledger state machine: it
adds observation (events, counters, limits), not new economic
states. 4D does not change any record format: it adds integrity
columns and verification on top of formats frozen by 4A–4C.

---

## 2. Formal ledger model and state transitions

### 2.1 Why a ledger, and what it replaces

Today a charge is a balance delta plus a free-text audit entry
(`app/resource_core.py`, `ResourceCore.charge`). That is not
reconcilable: there is no machine-readable link between a charge and
what it paid for, and no way to prove a charge happened exactly
once. The ledger replaces the free-text entry as the source of
economic truth. The audit-log entries stay as they are (human-readable
trail); the ledger becomes the machine-readable record. The existing
`users.credits_balance` stays the spendable balance; the ledger is
what proves it.

### 2.2 Proposed table — `ledger` [PROPOSED]

```sql
CREATE TABLE ledger (                      -- [PROPOSED] new table
    id TEXT PRIMARY KEY,                   -- operation id (uuid hex)
    idempotency_key TEXT NOT NULL UNIQUE,  -- one economic operation, one row, ever (§3)
    ts TEXT NOT NULL,                      -- ISO-8601 UTC, creation time
    actor TEXT NOT NULL,                   -- owner charged/refunded, or "lucas" for admin bypass
    agent_id TEXT,                         -- agents.id the movement pays for, NULL if none
    execution_id TEXT,                     -- executions.id, NULL if not an execution charge
    kind TEXT NOT NULL,                    -- 'charge' | 'refund' | 'bypass' | 'opening_balance'
    reason TEXT NOT NULL,                  -- 'create' | 'execution' | 'audit' | 'isolated_execution' | 'migration'
    amount INTEGER NOT NULL CHECK (amount >= 0),   -- credits, integer, never float
    state TEXT NOT NULL,                   -- see §2.3
    balance_after INTEGER,                 -- users.credits_balance after this transition (NULL for bypass)
    reserved_amount INTEGER NOT NULL DEFAULT 0,    -- amount currently held (state='reserved' only)
    usage_record_id TEXT,                  -- usage_records.id [PROPOSED, 4B] or NULL
    updated_at TEXT NOT NULL,
    CHECK (state IN ('started','reserved','charged','finished','failed','refunded'))
);
CREATE INDEX idx_ledger_actor ON ledger (actor);
CREATE INDEX idx_ledger_execution ON ledger (execution_id);
CREATE INDEX idx_ledger_state ON ledger (state);
```

Notes on the design:

- **One row per economic operation, state transitions on the row.**
  A refund is a state transition of the same operation
  (`failed` → `refunded`), not a second row — this makes "exactly
  one money movement per operation" checkable by counting rows, and
  makes a double refund structurally impossible (a row in
  `refunded` has no outgoing transition, §2.4).
- **`kind = 'bypass'`** records admin executions: Lucas is never
  charged (`ResourceCore.charge` returns early for the admin today),
  but the bypass must exist in the ledger as a first-class record,
  with `amount` = the amount that *would* have been charged and
  `balance_after = NULL`. This preserves the current audit behaviour
  (`credit_bypass` entries) in machine-readable form and is what
  makes SEC-004 / CREDIT-006 verifiable against a ledger instead of
  against free text.
- **`kind = 'opening_balance'`** is the migration genesis row
  (§8): one row per user, `amount` = the user's
  `credits_balance` at migration time, `state = 'finished'`. The
  ledger never rewrites history; it starts from a declared opening
  balance.
- **`amount` is INTEGER credits.** Money, when it appears (4B
  estimated cost), is stored separately as integer minor units
  (see §2.5) — never in this table, never as REAL. The existing
  `agents.real_api_cost REAL` field is left untouched and frozen:
  it is a legacy float meter, not a ledger input, and no Phase 4
  rule may read it as evidence of anything.

### 2.3 States

| State | Meaning | Balance effect |
|---|---|---|
| `started` | Operation opened: idempotency key registered, amount and reason recorded. No money has moved. | None. |
| `reserved` | Amount held: the spendable balance is reduced by `amount` (the money is in hold, not yet finally debited). | `users.credits_balance` −= amount. `reserved_amount` = amount. |
| `charged` | Debit final: the hold is converted into a final debit. The balance does not move again — it already moved at reservation. | None (already moved). `reserved_amount` = 0. |
| `finished` | The paid work completed successfully; the charge is confirmed final. Terminal success. | None. |
| `failed` | The paid work did not happen (refused by the isolation gate, supervisor failure, crash before completion, or an invalid operation). Triggers the refund path. | None yet — the money is still held. |
| `refunded` | The held amount was returned to the spendable balance. Terminal. | `users.credits_balance` += amount. `reserved_amount` = 0. |

**Two kinds of operation, one machine.** Execution charges follow
the full path `started → reserved → charged → finished`
(`failed → refunded` on the failure path). Immediate charges
(agent creation, audit) follow the short path `started → charged →
finished`: they are single-step, there is nothing to hold money
for, and the reservation step would add failure modes without
adding safety. The short path is an explicit allowed transition
(§2.4), not an exception handled in code comments — a rule that
audits the ledger must accept both paths.

**Admin bypass operations** follow `started → finished` with
`kind = 'bypass'` and no balance movement at any step. The bypass
is a ledger record, not a gap in the ledger.

### 2.4 Allowed transitions (the whole machine)

| From | To | Condition / effect |
|---|---|---|
| `started` | `reserved` | Balance check inside the write transaction (§3): available balance ≥ amount, else the operation fails and stays `started` with a recorded refusal — it never becomes a negative balance. |
| `started` | `charged` | Immediate charges only (creation, audit). Same balance check as above; the balance moves at this step instead of at reservation. |
| `started` | `finished` | Admin bypass only (`kind = 'bypass'`). No balance movement. |
| `reserved` | `charged` | The paid work is under way / the debit becomes final. No balance movement (already moved). |
| `reserved` | `failed` | The paid work will not happen (refused, supervisor failure, crash detected by recovery, §3.4). Money still held. |
| `charged` | `finished` | The paid work completed. No balance movement. |
| `charged` | `failed` | The paid work failed after the debit became final (e.g. supervisor failed mid-run after the charge was finalised). Money still held for refund. |
| `failed` | `refunded` | The held amount is returned to the balance. Terminal. |
| — | — | `finished` and `refunded` are terminal: no outgoing transitions. |

**Forbidden transitions (each must be impossible, enforced by a
state-transition guard in the ledger service [PROPOSED] and by
tests, §9):**

- `finished` → anything; `refunded` → anything.
- `refunded` → `charged` (a retry after a refund must open a NEW
  operation with a NEW idempotency key; retrying the same key
  returns the original result, never re-debits — §3.1).
- `charged` → `reserved` (a final debit never goes back to hold).
- `failed` → `charged` or `failed` → `finished` (a failed operation
  can only be refunded).
- `started` → `finished` for `kind = 'charge'` (a charge must pass
  through `reserved` or `charged`).
- `started` → `refunded` (nothing was held, so nothing can be
  refunded).
- Any transition that would make `users.credits_balance` negative
  (guarded by the in-transaction check and by a `CHECK
  (credits_balance >= 0)` constraint [PROPOSED] on `users`, §3.3).

**Enforcement.** The state machine is enforced in exactly one
place: a ledger service module [PROPOSED] that owns all transitions.
`ResourceCore.charge` / `refund` become thin callers of that
service (their public signatures do not change for existing
callers). Direct SQL updates to `ledger.state` from anywhere else
are a bug by construction; the Auditor (§7) checks the transition
history by re-reading the table, not by trusting the service.

### 2.5 Precision: integers only, never float

- Credits are integers today (`credits_balance INTEGER`,
  `COST_*` integers in `app/config.py`) and stay integers.
- Money (4B estimated cost) is stored as **integer minor units**
  (e.g. integer thousandths of a US cent — the exact minor unit is
  a 4B decision, recorded in the 4B design note, not here; the rule
  is only that it is an integer). No `REAL`, no float arithmetic,
  no rounding at write time anywhere in the economic path.
- Tokens are integers (`tokens_in`, `tokens_out`).
- The existing `agents.real_api_cost REAL` is the counterexample
  that proves the rule is needed: a float meter cannot be
  reconciled exactly, which is why it is frozen out of the ledger
  (§2.2). `ResourceCore.record_real_api_cost` keeps working as
  today (it writes the float field and an audit entry) but its
  output is not ledger evidence.
- **Reconciliation invariant** (the property the Auditor will check
  in v4.0.0, §7): for every user,
  `credits_balance + Σ(amount of rows in 'reserved') +
  Σ(amount of rows in 'charged' or 'finished' with kind='charge')
  = Σ(opening_balance amounts) + Σ(amount of rows in 'refunded')`.
  Equivalently: every credit granted is exactly once in exactly one
  bucket — spendable, held, spent, or refunded. A row that is in
  none of the buckets, or in two, is a FAIL.

### 2.6 What the ledger does NOT do

- It does not set prices (the pricing model is a parallel document).
- It does not convert tokens to credits (4B records consumption;
  conversion, if any, is a pricing decision, not a ledger rule —
  LTP-046 §3.6: cost of API, tokens and internal credits are
  different quantities; no equivalence is presumed).
- It does not authorise anything: authorisation stays where it is
  (`Auth.authenticate`, `Control` ownership checks). The ledger
  records the economic consequence of an already-authorised action.
- It does not replace the audit log: the audit log stays the
  human-readable trail; the ledger is the machine-readable truth.
  Both are written in the same transaction (§3.2), so they cannot
  diverge by a crash.

---

## 3. Idempotency and concurrency strategy in SQLite

### 3.1 Idempotency keys

- Every economic operation carries an `idempotency_key`
  [PROPOSED]: a client-supplied or server-generated unique string
  (uuid hex by default, generated by the caller — the CLI generates
  one per invocation). The column is `UNIQUE`; the database, not
  application memory, is the authority on whether a key was seen.
- **Retry behaviour (defined, not improvised):** re-submitting an
  operation with an already-seen key returns the stored result
  (the existing ledger row and its state) and performs no new money
  movement. A retry of a `finished` operation returns success
  without charging again. A retry of a `refunded` operation returns
  the refund result without debiting again. A retry of an operation
  still in `started` or `reserved` returns its current state and
  does not create a second row. A retry with the same key but
  different parameters (different amount, actor, reason) is
  rejected as a conflict — it is never silently treated as the
  same operation.
- **Scope of the key:** one key = one economic operation. The key
  is generated per operation (per charge, per refund trigger), not
  per execution and not per user. Two different charges for the same
  execution (e.g. a creation charge and an execution charge) have
  different keys; the same execution retried has the same key for
  the same charge.
- **Where the key is checked:** inside the same write transaction
  as the balance update (§3.2). Checking the key in a separate
  read before opening the write transaction would re-introduce the
  race the key exists to prevent.

### 3.2 Atomicity: one transaction per economic step

The current failure mode is that `ResourceCore.charge` performs
`set_balance` (commit #1) and `append_audit` (commit #2) as separate
commits — a crash between them leaves the balance moved with no
audit entry, or an audit entry with no balance movement. The fix
reuses the precedent that already exists in the codebase:
`Store.persist_global_audit` (`app/store.py`) wraps the report row
and the `audit_global` log entry in a single `BEGIN IMMEDIATE`
transaction (both or neither).

**Proposed rule:** every economic step writes its ledger row
transition, its `users.credits_balance` update, and its audit-log
entry in ONE `BEGIN IMMEDIATE` transaction. `BEGIN IMMEDIATE`
acquires the SQLite write lock up front, so the balance read, the
idempotency check, the state transition and the balance write are
serialised against every other writer. The Store's methods each
call `self.conn.commit()` after their statement
(`app/store.py`: `set_balance` commits, `append_audit` commits),
so today the two writes land in two separate transactions; the
ledger service must instead open the transaction explicitly and
commit or roll back as a unit — exactly as `persist_global_audit`
does today.

Concretely, the reservation step is one transaction:

1. `BEGIN IMMEDIATE`.
2. Check the idempotency key (insert-or-return-existing).
3. Read `users.credits_balance` (now under the write lock — the
   value cannot change under us).
4. If balance < amount: roll back, record the refusal (a ledger row
   in `started` with the refusal noted, or a `security_events` row
   [PROPOSED, 4C] — the 4A decision is that a refused reservation
   leaves a trace; the exact table is fixed in the 4A build review).
5. Insert/update the ledger row (`started` → `reserved`) and update
   the balance.
6. Append the audit-log entry.
7. `COMMIT`. Any exception → `ROLLBACK`, no partial state.

### 3.3 Concurrency: two executions cannot spend the same balance

Today the concurrency cap (`count_running_executions() >= 2`) and
the balance check are check-then-act across separate commits, so
two concurrent callers can both pass the check and both spend the
same balance (`app/control.py`, `Control.execute_agent`;
`app/resource_core.py`, `ResourceCore.charge`). The fix has three
layers, in order of strength:

1. **Serialised writes (primary).** All economic writes go through
   the single `BEGIN IMMEDIATE` transaction of §3.2. SQLite allows
   one writer at a time; the second writer blocks on the lock
   (with a configured `busy_timeout` [PROPOSED], e.g. 5000 ms —
   the current code sets no timeout, so the default applies) and
   then re-reads the balance inside its own transaction. The
   second charge sees the reduced balance and is refused if it no
   longer fits. This is the mechanism; the other two layers are
   defence in depth, not substitutes.
2. **In-transaction re-check (secondary).** The balance comparison
   happens after the write lock is held, never before. A balance
   read taken before `BEGIN IMMEDIATE` is never trusted for a
   spending decision.
3. **Database constraint (tertiary).** A `CHECK (credits_balance >=
   0)` constraint [PROPOSED] on `users.credits_balance`, added in
   the 4A migration. If application logic ever fails, the database
   itself refuses the negative balance — the transaction aborts
   instead of persisting an impossible state. (SQLite `CHECK`
   constraints are enforced per-row on write; adding one to an
   existing table requires a table rebuild — see §8.)

**What is NOT claimed.** SQLite serialised writes protect one
database file against concurrent writers in one process and across
processes on the same filesystem. They do not protect against two
different database files, a copied database, or a second service
instance with its own file. Multi-instance deployment is out of
scope for Phase 4 (no deploy in this phase); the constraint is
recorded here so nobody mistakes the single-file guarantee for a
distributed one.

**WAL mode.** The 4A migration enables WAL journal mode
(`PRAGMA journal_mode=WAL`) [PROPOSED] with a `busy_timeout`, so
readers do not block the single writer and concurrent CLI processes
fail cleanly instead of with `database is locked`. WAL is a
robustness setting, not a correctness mechanism — correctness comes
from §3.2.

### 3.4 Recovery policy for partial failures

The failure LTP-046 §3.4 names: the execution finishes, but the
process crashes before the economic result is recorded. The
recovery policy has three parts — detection, classification, and
action — and a hard rule: **recovery never silently creates a
charge.**

- **Detection (proposed, 4A).** A reconciliation scan [PROPOSED]
  (`ledger_reconcile`, admin-triggered, read-only by default)
  lists: (a) `executions` rows in `finished`/`refused`/`failed`
  with no ledger row in a terminal state referencing them;
  (b) ledger rows stuck in `started` or `reserved` longer than a
  configurable age (proposed default: 1 hour) with no matching
  live execution; (c) any row violating the invariant of §2.5.
  The scan produces a report; it changes nothing by itself.
- **Classification.** Each mismatch is one of: *charge taken, work
  done, finalisation missing* (ledger `reserved`/`charged`,
  execution `finished`); *charge taken, work refused* (ledger
  `reserved`/`charged`, execution `refused`/`failed`); *work done,
  no ledger row at all* (execution exists, ledger has nothing —
  in the new flow the reservation happens before
  `start_execution`, so this means the economic path was bypassed
  entirely); *ledger row with no execution* (stuck reservation).
- **Action.**
  - *Work done, finalisation missing:* advance the ledger row to
    `charged` → `finished` (the money was already reserved; this
    only finalises it). This is the only automatic transition
    recovery may perform, and it is logged as a recovery action.
  - *Work refused:* advance to `failed` → `refunded`. Automatic:
    money is never kept for work that did not happen.
  - *Work done, no ledger row:* **never auto-charge.** Flag as an
    anomaly for admin review. Creating a charge from recovery
    would invent a money movement with no idempotency key and no
    authorisation record.
  - *Stuck reservation, no live execution:* after the age
    threshold, advance to `failed` → `refunded` (release the hold)
    and log it. A hold must never be permanent.
- **Ordering guarantee.** In the new flow the order is:
  ledger reservation → `start_execution` → run → finalise. The
  reservation is what makes the "no ledger row" case an anomaly
  rather than a routine occurrence. The current flow charges before
  `start_execution` too (`app/control.py`, `execute_agent`), but
  without a ledger row; the 4A change is to make that pre-step a
  ledger transition, not to invent a new order.

---

## 4. Token adapter contract

### 4.1 Three quantities, never confused

LTP-046 §3.6: cost of API, tokens and internal credits are
different quantities. The contract keeps them in three separate
columns on every usage record, and no rule in the system may assume
an equivalence between them:

| Quantity | Unit | Source |
|---|---|---|
| Tokens in / out | Integer token counts | Adapter measurement or estimate |
| Estimated cost | Integer minor units (§2.5) | Pricing table [PROPOSED] applied to a measurement |
| Internal credits | Integer credits | Ledger (4A) — charged by execution minutes today (`COST_EXECUTION_PER_MIN = 1`, `app/config.py`), not by tokens |

A token count is not a charge. A cost estimate is not a charge. A
charge is a ledger transition, and only the ledger debits a balance.
In particular: **no automatic debit may be based on simulated data**
(§4.4).

### 4.2 Proposed table — `usage_records` [PROPOSED]

```sql
CREATE TABLE usage_records (                 -- [PROPOSED] new table, 4B
    id TEXT PRIMARY KEY,                     -- uuid hex
    execution_id TEXT NOT NULL,              -- executions.id
    agent_id TEXT NOT NULL,                  -- agents.id
    actor TEXT NOT NULL,                     -- owner of the execution
    provider TEXT NOT NULL,                  -- declared provider, e.g. 'stub'
    model TEXT NOT NULL,                     -- declared model, e.g. 'stub-1'
    tokens_in INTEGER NOT NULL CHECK (tokens_in >= 0),
    tokens_out INTEGER NOT NULL CHECK (tokens_out >= 0),
    origin TEXT NOT NULL,                    -- 'measured' | 'estimated' | 'simulated'
    method TEXT NOT NULL,                    -- how the numbers were produced, e.g. 'stub-fixed', 'stub-random-seed-7'
    cost_minor_units INTEGER,                -- estimated cost, integer minor units; NULL if not computable
    ledger_id TEXT,                          -- ledger.id [PROPOSED, 4A] the consumption is associated with, or NULL
    error TEXT,                              -- NULL on success; else 'measurement_failed' | 'invalid_response' | 'missing_data'
    measured_at TEXT NOT NULL,               -- ISO-8601 UTC
    adapter_version TEXT NOT NULL,           -- adapter implementation version, for traceability
    CHECK (origin IN ('measured','estimated','simulated'))
);
CREATE INDEX idx_usage_execution ON usage_records (execution_id);
```

### 4.3 Origin and method — every record declares itself

LTP-046 §3: a stub does not measure real tokens. Every record
therefore carries `origin` and `method`, and every consumer of the
table must read them:

| Origin | Meaning | May be used as evidence of… |
|---|---|---|
| `measured` | Real measurement supplied by a trusted adapter (a real provider client, when one exists). | Consumption, and — only through an explicit pricing rule — cost. |
| `estimated` | Produced locally (e.g. a tokeniser run over the prompt, or a price-table lookup over a measured count). | Planning and display. Never as a charge by itself. |
| `simulated` | Fictitious values generated by the stub adapter for testing the pipeline. | Nothing economic. Pipeline behaviour only. |

Rules: a record with `origin = 'simulated'` must never be presented
as real consumption (in a report, a CLI listing, or an Auditor
finding) and must never be used as evidence of a charge. The
Auditor v4.0.0 (§7) checks exactly this: any ledger charge
referencing a `usage_record_id` whose origin is `simulated` is a
FAIL. `method` records *how* the numbers were produced so a later
reviewer can tell a stub run from a real one without guessing.

### 4.4 Adapter interface [PROPOSED]

The interface is a documented protocol, not a class hierarchy.
One interface, two implementations in Phase 4: the stub (built in
4B) and, later, a real provider adapter (not built in this phase).
The ledger never imports the adapter; the adapter never imports the
ledger. The only link is `usage_records.ledger_id`, written by the
execution flow after both exist.

| Operation [PROPOSED] | Input | Output | Notes |
|---|---|---|---|
| `measure(execution_id, provider, model, payload_meta)` | Execution identity, declared provider/model, non-sensitive metadata (sizes, counts — never prompt content or secrets) | A `usage_records` row | The single entry point. The adapter decides `origin` and `method`; the caller never overrides them. |
| `estimate_cost(usage_record)` | A usage record | Integer minor units, or NULL | Pure function over the pricing table [PROPOSED]. No network, no side effects. |
| `capabilities()` | — | The origins this adapter can produce, and whether it makes network calls | The stub reports `simulated` only and no network calls. A real adapter would report `measured`. |

**Stub behaviours the stub must support** (LTP-046 §3, all
testable, §9): in/out tokens; declared model and provider;
estimated cost; measurement failure (adapter raises a typed
measurement error — the execution flow must record the failure,
not invent numbers); missing data (adapter returns a record with
tokens NULL-able? No — see below); invalid responses (adapter
receives malformed input and rejects it instead of recording
garbage); duplicate records (same execution measured twice —
second call returns the existing record, no duplicate row).

On missing data: the table columns `tokens_in` / `tokens_out`
are `NOT NULL`, so "missing data" is represented by a row with
`origin = 'simulated'`, `method = 'stub-missing-data'`, tokens
`0`, and `error = 'missing_data'`. A failed measurement is a row
with an error, not an absent row and not a fabricated count.

**No network in the stub.** The stub adapter makes no network
calls, reads no provider credentials, and holds no secrets. The
interface is designed so that adding a real provider later means
adding one adapter implementation — the ledger, the execution
flow and the Auditor do not change. That is the contract's
acceptance test (§9): a second adapter can be registered without
touching `ledger` or `ResourceCore`.

### 4.5 What 4B explicitly does not do

- No real provider, no provider credentials, no paid consumption.
- No conversion from tokens to credits (that is a pricing-model
  decision for the parallel document, and a later phase's
  implementation — not 4B).
- No change to `COST_EXECUTION_PER_MIN`: executions are still
  charged by the minute in 4B, exactly as today. Token accounting
  is observation; the ledger is the only thing that debits.
- No change to `agents.real_api_cost REAL`: it stays frozen (§2.2).

---

## 5. Security events model

### 5.1 The gap today

Failed authentications and denied authorizations are never written
anywhere. `Auth.authenticate` (`app/auth.py`) raises
`AuthenticationFailed` before any audit call; `Control.execute_agent`
and the Auditor raise before any charge or log entry — by design
(LTP-022: a denied attempt must not even reveal that the target
exists). The log therefore contains only events that were allowed.
That is why SEC-006 and KEY-005 are NOT VERIFIABLE in v3.0.0
(`docs/auditor-architecture.md` §7.2). Phase 4C records denials
without re-introducing the existence leak that LTP-022 closed: the
events table is admin-only, and it never contains a secret, a hash,
or a target id that would confirm existence to a non-admin reader.

### 5.2 Proposed table — `security_events` [PROPOSED]

```sql
CREATE TABLE security_events (               -- [PROPOSED] new table, 4C
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                        -- ISO-8601 UTC
    event TEXT NOT NULL,                     -- see §5.3
    actor TEXT,                              -- owner when known, else NULL (never a secret)
    public_id TEXT,                          -- api_keys.public_id when a key was presented and parsed, else NULL
    target_kind TEXT,                        -- 'agent' | 'key' | 'log' | 'ledger' | NULL (kind only, never an id)
    detail TEXT NOT NULL DEFAULT ''          -- free text; MUST NOT contain a secret, a key hash, or a target id
);
CREATE INDEX idx_sec_event ON security_events (event, ts);
CREATE INDEX idx_sec_public_id ON security_events (public_id, ts);
```

**Append-only, admin-read-only.** The table gets the same
UPDATE/DELETE triggers as `audit_log` (`app/store.py`), and the
same ownership rule as `Control.list_audit_log`: only Lucas reads
it; a non-admin gets nothing (not even a count). The triggers do
not make it tamper-evident — that is 4D's job (§6).

**What is never in an event:** a raw key, a key hash, a server
secret, a password, a full agent id on a denial path (the id
itself would confirm existence), or any prompt/payload content.
The Auditor v4.0.0 (§7) includes a rule that scans
`security_events.detail` for key-shaped material
(`afk_…` patterns, 64-hex strings) and FAILs if any is found.

### 5.3 Event types [PROPOSED]

| Event | Written where | Contains |
|---|---|---|
| `auth_failed` | `Auth.authenticate`, on every failure | `public_id` only when the key parsed (a malformed key has no id to record); never the reason that would distinguish "unknown id" from "wrong secret" in a user-visible message (the generic message stays generic — the event is admin-only, the CLI message is not) |
| `forbidden_actor` | `Control.execute_agent` / `execute_isolated` / Auditor service, on the non-owner denial path | `target_kind = 'agent'`, no agent id |
| `unknown_agent_denied` | Same paths, when a non-admin asks for a nonexistent agent | `target_kind = 'agent'`, no id (the denial itself is the event; the id is not recorded) |
| `rate_limited` | Rate-limit enforcement points (§5.4) | Which limit fired (key or user), the actor/public_id |
| `key_revoked` | `Auth.revoke_key` (in addition to the existing audit-log entry) | `public_id`, actor |
| `key_expired` | `Auth.authenticate`, when a presented key is past `expires_at` (§5.5) | `public_id` |
| `admin_bypass` | Ledger bypass operations (§2.3) | The ledger operation id, the amount that would have been charged |
| `key_created` | `Auth.create_key` (in addition to the existing audit-log entry) | `public_id`, owner |

### 5.4 Limits: per-key and per-user [PROPOSED]

Rate limiting is not a substitute for concurrency control, quotas,
or protection against multiple-key creation (LTP-046 §3.7) — it is
one layer. Proposed config (all [PROPOSED], values to be confirmed
in the 4C build review):

| Limit | Scope | Proposed default | Enforced where |
|---|---|---|---|
| Authentication attempts | Per key (`public_id`), per minute | 10/min, then `rate_limited` + temporary refusal | `Auth.authenticate` |
| Agent creations | Per user, per hour | 10/hour | Factory creation path |
| Executions started | Per user, per hour | 60/hour | `Control.execute_agent` / `execute_isolated` |
| Global audits | Per admin, per interval | 60 s (already exists: `AUDIT_GLOBAL_MIN_INTERVAL_SECONDS`, `app/config.py`) | Auditor service (unchanged) |
| API keys per user | Per user, total active | 5 active keys | `Auth.create_key` |

Limits are enforced in the service layer, not only in the CLI
(the Auditor rate limit already follows this pattern, LTP-038
§3B). Hitting a limit writes a `rate_limited` security event and
returns a refusal — it never writes a partial economic record.
Limits count attempts, not successes, so a flood of refused
requests still trips the limit.

**False positives and admin lockout.** A limit that blocks Lucas
is a system outage, not a security win. Proposed safeguards: the
admin's own limits are higher (or logged-only, never blocking, for
the kill-switch and key-management paths — the exact carve-out is
a 4C build decision, recorded in the 4C review); every limit hit
is visible to the admin in `security_events`; and no limit may
block the revocation path (revoking a key must always work, even
under a flood).

### 5.5 Key usage and expiry [PROPOSED]

New columns on the existing `api_keys` table (additive migration,
§8):

| Column [PROPOSED] | Meaning |
|---|---|
| `last_used_at TEXT` | Last successful authentication with this key (NULL = never used) |
| `use_count INTEGER NOT NULL DEFAULT 0` | Count of successful authentications |
| `expires_at TEXT` | Expiry timestamp, NULL = no expiry (existing keys get NULL; new keys get a configurable default, proposed: 180 days, matching `AUDIT_KEY_MAX_AGE_DAYS` in `app/config.py`) |

`Auth.authenticate` updates `last_used_at` and `use_count` on
success (same transaction as the authentication check — a
successful auth that is not counted is a gap the Auditor would
flag) and rejects an expired key with the same generic failure as
every other failure mode. Expiry is enforced, not advisory: an
expired key fails authentication exactly like a revoked one, and
writes a `key_expired` event. Revocation stays immediate
(`revoke_key` sets `revoked = 1`; `authenticate` checks it) and
is unchanged by this phase.

### 5.6 Admin bypass: explicit, restricted, auditable

The admin bypass exists today (Lucas is never charged;
`ResourceCore.charge` logs `credit_bypass`) and stays — but
Phase 4 makes its boundaries explicit:

- **Explicit:** every bypass is a ledger row (`kind = 'bypass'`,
  §2.3) and an `admin_bypass` security event (§5.3), with the
  amount that would have been charged. There is no silent bypass.
- **Restricted:** the bypass covers only the charge for an
  already-authorised admin execution. It does not cover, and must
  never be able to cover: deleting ledger rows, deleting
  `security_events` rows, deleting audit-log rows, altering the
  hash chain, or bypassing hash-chain verification. There is no
  delete API for any of those tables, and the UPDATE/DELETE
  triggers already refuse the operation at the database level
  (`app/store.py`); the bypass has no code path around them.
- **Auditable:** the Auditor v4.0.0 (§7) has a rule that lists
  bypass operations and checks each one against an `admin_execute`
  audit entry; a bypass with no matching admin execution is a
  FAIL. The admin's power is to act without being charged — not
  to act without being recorded.

---

## 6. Hash chain and external checkpoint strategy

### 6.1 What the chain covers

The chain covers `audit_log` and `audit_reports` — the two
append-only tables (`app/store.py`, UPDATE/DELETE triggers). It
also covers `ledger` and `security_events` once those tables exist
(4A and 4C respectively): the chain is built over a single
ordered event stream, not one chain per table, so a deletion in
any table breaks the same chain. The integrity-design decision
— which events the chain must protect — is made in 4A (LTP-046
§2 caveat); the implementation is 4D, after the record formats
are frozen.

### 6.2 Chain construction [PROPOSED]

New columns on `audit_log`, `audit_reports`, `ledger` and
`security_events` (additive, §8):

```sql
prev_hash TEXT NOT NULL,   -- entry_hash of the previous row in the chain, or 'GENESIS'
entry_hash TEXT NOT NULL,  -- SHA-256 over the canonical serialisation of this row
```

`entry_hash = SHA256(prev_hash || canonical(row))`, where
`canonical(row)` is a fixed, documented serialisation: the row's
fields joined in a fixed order with an unambiguous separator, or
JSON with sorted keys — the exact format is a 4D build decision,
recorded in the 4D review; what matters is that it is fixed,
documented, and identical in the writer and the verifier. The hash
is computed in the store layer at insert time (`Store.append_audit`
and equivalents), inside the same transaction as the insert — a
row is never written without its hash, and a hash is never written
without its row.

**Verification** [PROPOSED] (`verify_chain`, Auditor rule
LOG-005 in v4.0.0, §7): recompute the chain from the genesis row;
for each row, check `prev_hash` equals the previous row's
`entry_hash`, and `entry_hash` equals the recomputation over the
row's stored fields. A mismatch is a FAIL with the row id. The
verifier reads the table in `id` order (the AUTOINCREMENT primary
key is the chain order; wall-clock `ts` is not trusted for
ordering).

**What verification detects:** alteration of any covered field in
any row (the recomputed hash differs); deletion of a row (the
next row's `prev_hash` no longer matches its predecessor);
insertion of a fabricated row (it either breaks the link or, if
inserted at the end with a correct hash, is indistinguishable from
a legitimate row — see §6.4); reordering (the `id` sequence no
longer matches the `prev_hash` links).

### 6.3 Migration: an explicit historical boundary

The chain cannot be retroactively applied to the past as if it
had always been there. The migration is:

1. Take a full backup of the database file (§8).
2. Compute a single hash over the entire pre-chain content of
   `audit_log` and `audit_reports` (canonical serialisation of
   all existing rows, in `id` order). Record it as the
   **checkpoint hash** in the `meta` table (`chain_genesis_hash`)
   and in the external anchor (§6.4), together with the row counts
   and the timestamp.
3. Insert a genesis marker row (an audit-log entry with action
   `chain_start`, `prev_hash = 'GENESIS'`, whose `entry_hash`
   commits to the checkpoint hash). Rows before this marker are
   covered by the checkpoint hash, not by the chain; rows after
   it are covered by the chain.
4. From the marker on, every insert carries `prev_hash` /
   `entry_hash`.

The boundary is explicit and visible: any report or verification
output states the checkpoint (hash, row counts, timestamp) and the
fact that pre-boundary rows are checkpoint-covered, not
chain-covered. A chain that silently "backfills" old rows with
freshly computed hashes would be a lie — it would claim
protection for a past nobody observed.

### 6.4 The hard problem: whole-DB replacement, and the external anchor

A hash chain stored only in SQLite does **not** detect whole-DB
replacement: an attacker with write access to the database file
can replace the entire file, recompute the chain from genesis,
and present a perfectly valid chain over fabricated history
(LTP-046 §3, final paragraph; `docs/auditor-architecture.md`
§7.3). Detection requires a checkpoint anchored **outside** the
database, protected by a mechanism independent of it. Three
concrete options, with trade-offs — no silent choice:

| Option | How it works | Strengths | Weaknesses |
|---|---|---|---|
| **A. Checkpoint hash committed to the git repo** | After each checkpoint (e.g. weekly, or after each stage), the checkpoint hash (hash only — no data) is committed to the `ai-factory` repo (the repo is already the shared Theo↔ChatGPT channel, and its history is append-only in practice). Verification compares the DB's checkpoint hash against the repo's. | Free; versioned; public without leaking data (a hash reveals nothing); fits the existing workflow; an attacker who can rewrite the DB but not the repo is detected. | Coarse granularity (only as fresh as the last commit); anyone with repo write access can update the anchor too — the anchor is only as independent as the repo's write access is from the DB's; a public repo means the checkpoint schedule is visible. |
| **B. Checkpoint held by Lucas out-of-band** | Lucas keeps the checkpoint hash himself (his own notes, a message, a file on his own machine). Verification is manual: compare the DB's hash with his copy. | No infrastructure; the trust anchor is Lucas himself — the one party the system is built to serve; no third party involved. | Manual, coarse, and fragile: human error, lost notes, and no automation; verification happens only when Lucas does it. |
| **C. Signed checkpoint** | Lucas (or a key he holds) signs the checkpoint hash; the signature is stored in the repo alongside the hash. Verification checks the signature against his public key. | Detects tampering even if the attacker can write to the repo — they cannot forge the signature without the private key; the strongest of the three. | Key management: the private key must be generated, stored and backed up securely; more machinery (signing step, public-key distribution); a lost key means the anchor cannot be updated. |

**Recommendation: A now, C as the hardening step, B as the
fallback.** Option A is implementable in 4D with no new
infrastructure and matches the repo's existing role; it is the
recommended default for the 4D build. Option C is the honest
upgrade path and should be recorded as the follow-up, not
pretended to exist from day one. Option B is always available as
a human anchor and costs nothing — Lucas keeping a checkpoint
hash in his own notes is a valid anchor for a single-user system.

**Residual limitation (documented, not solved):** if the database
*and* the checkpoint mechanism are compromised together — the
attacker rewrites the DB, recomputes the chain, and updates the
anchor (or forges a signature with a stolen key) — the tampering
is undetectable by any mechanism in this system. The mitigation
is separation of write access: whoever can write the DB must not
be able to write the anchor, and vice versa. For a single-user
local system that separation is thin, and this document does not
pretend otherwise. The Auditor v4.0.0 rule for LOG-005 reflects
this exactly (§7): chain valid + anchor present and matching →
PASS for "no alteration detected within the anchored history";
anchor missing or stale → WARN; chain broken → FAIL; and the
finding always states that whole-DB replacement with a
recomputed anchor is outside what the check can detect.

### 6.5 What the chain does not do

- It does not encrypt the database. DB theft is still data theft.
- It does not stop an attacker with DB write access from
  *appending* a correctly-hashed fabricated row at the end of the
  chain. It detects alteration, deletion, insertion in the middle,
  and reordering — not a well-formed append by someone who holds
  the write path. (That append is visible in the audit trail as a
  row nobody authorised, which is why the ledger and the
  security-events table matter: the chain protects the record; the
  rules in §7 judge its content.)
- It does not replace the external anchor. A chain without an
  anchor is a tamper-*evidence* mechanism for accidental
  corruption and naive tampering only — the architecture doc
  (§7.3) already says this, and 4D does not change the claim.

---

## 7. Impact on Auditor v3.0.0 — ruleset v4.0.0

### 7.1 Re-evaluation of the 7 UNVERIFIED items (one by one)

A rule becomes PASS only with verifiable evidence. Adding a field
is necessary, never sufficient: the rule must check the *content*
of the new data, not its existence. The table below is the
commitment for v4.0.0.

| Rule | Stage that makes it verifiable | Evidence the rule will check | What it becomes |
|---|---|---|---|
| CREDIT-004 (exact charge ↔ execution reconciliation) | 4A | For every `executions` row with `status = 'finished'`, exactly one `ledger` row with `kind = 'charge'`, `execution_id = executions.id`, `amount = minutes × COST_EXECUTION_PER_MIN`, and a terminal state (`finished`); and every execution charge matches one execution. | PASS / FAIL (exact). No more approximate matching. |
| CREDIT-009 (creation charge attribution) | 4A | For every agent row, a `ledger` row with `reason = 'create'`, `agent_id = agents.id`, `amount = COST_CREATE`, terminal state. The creation charge finally has an agent link (today it is logged with `agent_id=None`, `target = ""`). | PASS / FAIL (exact). |
| SEC-006 (denied cross-user attempts) | 4C | Count `forbidden_actor` / `unknown_agent_denied` events in `security_events`; alert (WARN) above a threshold [PROPOSED, value set in the 4C review]; zero events is PASS with the explicit note "no denials recorded in the window" — not "no denials happened". | PASS / WARN. FAIL only if the table itself is missing or unreadable. |
| KEY-005 (revoked key used after revocation) | 4C | A `public_id` that appears in `auth_failed` events with `ts` after its `revoked_at`. | PASS (none found) / FAIL (found, with the public_id and timestamps as evidence). |
| LOG-005 (tamper-evidence) | 4D | Chain verification (§6.2) + external anchor comparison (§6.4). Chain valid and anchor matching → PASS for "no alteration detected within the anchored history", with the anchor's identity and timestamp in the finding. Anchor missing/stale → WARN. Chain broken → FAIL with the row id. Whole-DB replacement with a recomputed anchor stays outside the check — the finding says so. | PASS / WARN / FAIL (never a blanket "the log is safe"). |
| PERM-010 (was `execute_limited` explicitly granted?) | 4A (partial) | New column `granted_permissions TEXT` [PROPOSED] on `agents` (JSON: `{"permission": "explicit" \| "template"}`), written by `Factory.create_agent` at creation time (proposed in `docs/auditor-architecture.md` §7.4). The rule checks: every agent holding `execute_limited` has provenance `explicit` AND a matching `create_agent` audit entry. **Ceiling:** full provability would also require logging the creation call's arguments, which is not proposed here — so PERM-010 can reach PASS on "provenance recorded and consistent", and the finding states the ceiling. It must NOT become PASS merely because the column exists. | PASS / WARN / FAIL on consistency, with the ceiling documented. |
| SEC-007 (executions by suspended/killed agents) | 4A | New column `agent_status_at_start TEXT` [PROPOSED] on `executions`, written by `Store.start_execution` from the agent row at call time (proposed in `docs/auditor-architecture.md` §7.5). The rule checks: no `executions` row has `agent_status_at_start` IN (`suspended`, `killed`). | PASS / FAIL (exact). A FAIL here means the code check was bypassed — direct insert or a bug. |

### 7.2 New rules in v4.0.0 [PROPOSED]

| Rule [PROPOSED] | Checks |
|---|---|
| CREDIT-010 | Ledger reconciliation: the invariant of §2.5 holds for every user (recompute the balance from the ledger, compare with `users.credits_balance`). Any mismatch is FAIL. |
| CREDIT-011 | Idempotency: no two ledger rows share an `idempotency_key`; no operation was debited twice (one row per key, §3.1). |
| CREDIT-012 | State-machine legality: every ledger row's state is reachable by an allowed transition (§2.4) — checked by replaying the row's history, not by trusting a flag. |
| CREDIT-013 | No negative balance by race or retry: `users.credits_balance >= 0` for every user, and no ledger transition produced a negative `balance_after`. |
| CREDIT-014 | Simulated data is never a charge: no ledger row references a `usage_records` row with `origin = 'simulated'` (§4.3). |
| KEY-006 | Key hygiene, extended: keys past `expires_at` are rejected (not merely old); `last_used_at`/`use_count` are populated for keys used after 4C. |
| SEC-008 | Security events contain no secrets: scan `security_events.detail` for key-shaped material (`afk_…`, 64-hex strings, key hashes) — any hit is FAIL. |
| SEC-009 | Admin bypass auditability: every ledger `kind = 'bypass'` row has a matching `admin_execute` audit entry and an `admin_bypass` security event (§5.6). |
| LOG-006 | Hash-chain verification over the covered tables (§6.2) — the rule that implements LOG-005's check in v4.0.0. (LOG-005 itself keeps its id and meaning; LOG-006 is the chain-verification rule that supplies its evidence.) |

The exact rule list and ids are confirmed in the 4D build review;
the table above is the proposal. The ruleset version becomes
`4.0.0` (`AUDIT_RULESET_VERSION`, `app/config.py`).

### 7.3 Preserving v3.0.0 history

- `audit_reports` rows keep their `ruleset_version`. v3.0.0
  reports are never rewritten, re-evaluated, or deleted.
- A v4.0.0 report carries its own findings and summary; a reader
  can compare a v3 report (31 PASS / 7 UNVERIFIED on a clean DB)
  with a v4 report and see exactly which rules changed status and
  why. The comparison is the evidence that the phase worked — it
  is not recomputed after the fact.
- The v3.0.0 ruleset definition stays in the repository
  (`docs/auditor-architecture.md` rev 2) alongside the v4.0.0
  definition (a new revision of the same document, or a companion
  section — the 4D review decides the file layout; the content
  decision is that both versions remain readable).
- The clean-DB expectation for v4.0.0 is defined by the 4D build
  review, not by this document: it depends on which of the 7 items
  become PASS and which stay UNVERIFIED/WARN. This document does
  not pre-declare the numbers — pre-declaring them would be
  deciding the audit's outcome before the checks exist.

---

## 8. Migration and rollback plan

**Global rules for every stage.** (1) All schema changes are
additive: new tables, new columns, new triggers. No `DROP TABLE`,
no destructive `ALTER`, no rewriting of existing rows except the
backfills listed below. (2) A full backup of the database file is
taken before each stage's migration, and the backup's hash is
recorded in the migration log (an audit-log entry with action
`migration_backup`). (3) Each stage's migration is one
transaction: if it fails, nothing is half-applied. (4) Rollback is
always possible by restoring the backup — the migration never
depends on a step that cannot be undone by a restore. (5) A ledger
migration cannot silently drop existing balance history: balances
are preserved, the audit log is untouched, and the opening-balance
genesis rows (§2.2) are the only new economic records.

### 8.1 Stage 4A — ledger

| Step | Action |
|---|---|
| Backup | Copy `data.db` (or the configured DB path) to `data.db.pre-4a-<timestamp>`; record its SHA-256 in an audit entry. |
| Schema | Create `ledger` [PROPOSED] (§2.2); add `CHECK (credits_balance >= 0)` to `users` — SQLite cannot add a `CHECK` to an existing table with plain `ALTER TABLE`, so this is a table rebuild (create new table, copy rows, drop old, rename) inside the same migration transaction; enable WAL (`PRAGMA journal_mode=WAL`) and set a `busy_timeout` [PROPOSED]. |
| Backfill | One `opening_balance` genesis row per user: `amount` = current `credits_balance`, `state = 'finished'`, `reason = 'migration'`. No other backfill: pre-ledger charges stay as free-text audit entries and are NOT converted into ledger rows (converting prose into money movements would fabricate evidence). The reconciliation invariant (§2.5) therefore starts at the migration, not at genesis. |
| Code | `ResourceCore.charge` / `refund` delegate to the ledger service [PROPOSED]; existing callers and signatures unchanged. |
| Rollback | Restore the backup file. The ledger table disappears with it; balances and audit log are exactly as before. Any ledger rows created after the migration are lost with the restore — that is correct behaviour for a rollback, and it is why the backup is taken first. |

### 8.2 Stage 4B — tokens

| Step | Action |
|---|---|
| Backup | Same procedure, `data.db.pre-4b-<timestamp>`. |
| Schema | Create `usage_records` [PROPOSED] (§4.2, with the `error TEXT` correction from §4.4). No changes to existing tables. |
| Backfill | None. There is no historical token data; the table starts empty. The existing `agents.real_api_cost` values are left as they are (frozen, §2.2) and are not imported. |
| Code | Adapter interface [PROPOSED] + stub adapter; the execution flow calls `measure()` after a run and stores the record. No change to charging. |
| Rollback | Restore the backup, or drop `usage_records` (it contains no economic truth — dropping it loses only observation, never money). |

### 8.3 Stage 4C — antifraud

| Step | Action |
|---|---|
| Backup | Same procedure, `data.db.pre-4c-<timestamp>`. |
| Schema | Create `security_events` [PROPOSED] (§5.2) with UPDATE/DELETE triggers; add `last_used_at`, `use_count`, `expires_at` to `api_keys` (plain `ALTER TABLE ADD COLUMN` — additive, safe). |
| Backfill | Existing keys get `expires_at = NULL` (no expiry — changing existing keys' validity without notice would lock users out) and `use_count = 0`, `last_used_at = NULL`. The 4C review decides whether existing keys get a default expiry with notice, or stay unexpired; this document does not decide it silently. |
| Code | `Auth.authenticate` writes events and enforces expiry; rate limits enforced in the service layer; `Auth.create_key` enforces the per-user key cap. |
| Rollback | Restore the backup. Note: rolling back 4C deletes the `security_events` table — denial history recorded during the rolled-back period is lost. That loss is acceptable (the events are observations, not money) but must be recorded in an audit entry if a rollback happens. |

### 8.4 Stage 4D — hash chain + Auditor v4

| Step | Action |
|---|---|
| Backup | Same procedure, `data.db.pre-4d-<timestamp>`. |
| Schema | Add `prev_hash` / `entry_hash` columns to `audit_log`, `audit_reports`, `ledger`, `security_events` (additive). |
| Backfill | The checkpoint procedure of §6.3: compute the checkpoint hash over all pre-chain rows, record it in `meta` (`chain_genesis_hash`) and in the external anchor, insert the `chain_start` marker row. Existing rows are NOT rewritten with fresh hashes — the boundary is explicit. |
| Code | Store layer computes hashes at insert; `verify_chain` [PROPOSED] implemented; Auditor ruleset v4.0.0 (§7). |
| Rollback | Restore the backup. The chain and the checkpoint disappear with it. If a checkpoint hash was already committed to the repo (option A, §6.4), the commit is NOT rewritten — a follow-up commit records that the checkpoint was superseded by the rollback. History is appended to, never rewritten, in the repo as in the database. |

### 8.5 What cannot be rolled back

- A checkpoint hash already committed to the public repo cannot be
  un-published; it can only be superseded by a later commit (§8.4).
- Audit-log entries written during a stage cannot be deleted by a
  rollback that restores an older backup — the restore itself is a
  loss of those entries, which is why the backup is taken first
  and the rollback is an explicit admin decision, recorded in the
  migration log.
- Money movements that happened while a stage was live cannot be
  "un-happened" by a schema rollback: the backup restore returns
  balances to their pre-stage values, which is only correct if no
  real user depended on the intermediate state. This is acceptable
  in Phase 4 because there are no real users and no real charges
  yet — and it is a further reason the phase must complete before
  any real use.

---

## 9. Tests and acceptance criteria per stage

**Baseline preserved through the whole phase:** 159 tests (148
pass, 11 Windows-only skipped). No stage may weaken an existing
assertion. New tests are added per stage; the baseline count grows,
it never shrinks. Test file names below are [PROPOSED] (new files
per stage, following the existing pattern:
`tests/test_isolation_ltp022.py`,
`tests/test_auth_phase2.py`,
`tests/test_executor_phase2.py`,
`tests/test_auditor_phase3.py`).

### 9.1 Stage 4A — ledger (LTP-046 §4, 4A additions, verbatim basis)

Mandatory additions from LTP-046 §4, 4A:

- Máquina de estados formal.
- Idempotência comprovada.
- Testes concorrentes.
- Testes de falha entre operações.
- Reconciliação independente do saldo.
- Nenhum saldo negativo por corrida ou retry.
- Nenhuma operação econômica duplicada.

Concrete scenarios [PROPOSED] (`tests/test_ledger_phase4a.py`):

| Test | Scenario |
|---|---|
| `test_state_machine_all_allowed_transitions` | Every row of the §2.4 table, driven through the ledger service: each allowed transition succeeds and lands in the expected state with the expected balance effect. |
| `test_state_machine_forbidden_transitions` | Every forbidden transition in §2.4 raises (or is refused) and leaves the row and the balance unchanged — including `refunded → charged`, `finished → *`, `failed → charged`. |
| `test_short_path_immediate_charges` | Creation and audit charges follow `started → charged → finished` in one transaction; balance moves exactly once. |
| `test_bypass_path` | Admin execution creates a `kind='bypass'` row, `started → finished`, balance untouched, amount recorded. |
| `test_idempotency_same_key_twice` | Same idempotency key submitted twice → one ledger row, one debit, second call returns the stored result. |
| `test_idempotency_retry_after_refund` | Retry of a refunded operation's key → returns the refund result, no new debit. |
| `test_idempotency_key_conflict` | Same key, different amount/actor → rejected as a conflict, no money moved. |
| `test_concurrent_charges_same_balance` | Two threads (and, in a separate test, two processes) charge the same user concurrently for more than the balance → exactly one succeeds, the other is refused, balance never negative, exactly one ledger row debited. |
| `test_concurrent_execution_cap_unchanged` | The existing concurrency cap (2 simultaneous executions) still holds under the new transaction path. |
| `test_failure_between_charge_and_record` | Simulated crash between the balance update and the audit entry (fault injection in the ledger service) → transaction rolls back, no partial state; the recovery scan (§3.4) reports the mismatch and the retry with the same idempotency key completes the operation exactly once. |
| `test_recovery_finalise_missing` | Ledger `reserved`, execution `finished` → recovery advances to `charged → finished`, no double debit. |
| `test_recovery_refund_refused` | Ledger `reserved`, execution `refused` → recovery advances to `failed → refunded`, balance restored. |
| `test_recovery_never_auto_charges` | Execution exists with no ledger row → recovery flags an anomaly and creates no charge. |
| `test_recovery_stuck_reservation_released` | Ledger `reserved` with no live execution past the age threshold → `failed → refunded`, hold released. |
| `test_independent_balance_reconciliation` | Recompute every user's balance from the ledger alone (invariant §2.5) and compare with `users.credits_balance` — including after a mixed history of charges, refunds, bypasses and opening balances. |
| `test_no_negative_balance_by_race_or_retry` | Property-style test: random interleavings of charges/refunds/retries never produce a negative balance or a duplicated operation. |
| `test_opening_balance_genesis` | Migration creates exactly one `opening_balance` row per user with the pre-migration balance; pre-migration free-text charges are not converted. |
| `test_integer_amounts_only` | Attempting a non-integer amount is rejected; no float ever reaches the ledger (a float amount raises before any write). |

**4A acceptance criteria:** all tests above green; full regression
green; CREDIT-004 and CREDIT-009 demonstrably checkable against
the ledger (the Auditor rules exist as v4.0.0 drafts or the v3
rules are shown to pass against ledger-backed data — the 4A review
decides whether the rule updates land in 4A or wait for 4D, but
the *data* that makes them verifiable must exist after 4A);
reconciliation invariant holds on every test database.

### 9.2 Stage 4B — tokens (LTP-046 §4, 4B additions)

Mandatory additions from LTP-046 §4, 4B:

- Origem da medição explícita.
- Separação entre estimativa e medição real.
- Stub sem chamadas externas.
- Nenhum débito automático baseado em dados simulados.

Concrete scenarios [PROPOSED] (`tests/test_tokens_phase4b.py`):

| Test | Scenario |
|---|---|
| `test_stub_records_in_out_tokens` | Stub returns a record with in/out tokens, declared provider/model, `origin='simulated'`, `method` set. |
| `test_stub_estimated_cost` | `estimate_cost` returns integer minor units from the pricing table; no float. |
| `test_stub_measurement_failure` | Stub forced to fail → a `usage_records` row with `error='measurement_failed'`, tokens 0, no fabricated count. |
| `test_stub_missing_data` | Missing input → row with `error='missing_data'`, not an absent row. |
| `test_stub_invalid_response` | Malformed adapter input → rejected, no row with garbage values. |
| `test_stub_duplicate_measurement` | Same execution measured twice → one row, second call returns the existing record. |
| `test_stub_no_network` | The stub performs no network calls (asserted by running with networking disabled / by inspecting the adapter's capabilities declaration). |
| `test_origin_explicit_on_every_record` | Every row in `usage_records` has `origin` and `method` set; a row without them cannot be inserted. |
| `test_no_debit_from_simulated_data` | A ledger charge referencing a `simulated` usage record is refused (and the v4 rule CREDIT-014 FAILs on a fixture that contains one). |
| `test_measured_estimated_simulated_separation` | The three origins are distinguishable in every listing/report; a `simulated` record is never labelled as real consumption. |
| `test_second_adapter_without_ledger_change` | A second adapter implementation (a test double with `origin='measured'`) registers and records through the same interface with zero changes to the ledger or `ResourceCore`. |
| `test_charging_still_by_minute` | Executions are still charged `COST_EXECUTION_PER_MIN` per minute; token records do not change the charged amount. |

**4B acceptance criteria:** all tests above green; full regression
green; the adapter contract allows a real provider later without
modifying the ledger (demonstrated by the second-adapter test);
no simulated value is usable as charge evidence.

### 9.3 Stage 4C — antifraud (LTP-046 §4, 4C additions)

Mandatory additions from LTP-046 §4, 4C:

- Eventos de segurança sem exposição de segredos.
- Limites por chave e usuário.
- Revogação e expiração imediatas.
- Bypass administrativo rastreável.
- Proteção contra falsos positivos e bloqueio administrativo acidental.

Concrete scenarios [PROPOSED] (`tests/test_antifraud_phase4c.py`):

| Test | Scenario |
|---|---|
| `test_denied_attempt_recorded_without_id` | Non-owner execution attempt → `forbidden_actor` event with `target_kind='agent'` and no agent id; the event is visible to the admin only. |
| `test_revoked_key_use_recorded` | Authentication with a revoked key → `auth_failed` event with the `public_id`; the key still fails with the generic message. |
| `test_failed_auth_recorded` | Unknown/malformed key → `auth_failed` event; a malformed key records no `public_id` (there is none to record). |
| `test_events_contain_no_secrets` | Scan all `security_events.detail` values for `afk_` patterns, 64-hex strings and key hashes — none found, including after a flood of failures. |
| `test_events_admin_only` | Non-admin listing of `security_events` returns nothing (not even a count). |
| `test_rate_limit_per_key` | 11 auth attempts in a minute with one key → the 11th is refused and a `rate_limited` event is written. |
| `test_rate_limit_per_user_execution` | Execution starts above the per-user hourly limit → refused, event written, no charge, no execution record. |
| `test_key_cap_per_user` | Creating a 6th active key for a user (cap 5, §5.4) is refused. |
| `test_revocation_immediate` | Revoke a key, authenticate with it in the next call → fails; the revocation is visible in `security_events` as `key_revoked`. |
| `test_expiry_enforced` | Key past `expires_at` → fails with the generic message and a `key_expired` event; a key before expiry still works. |
| `test_expiry_does_not_lock_out_existing_keys` | Keys created before 4C (`expires_at = NULL`) keep working after the migration. |
| `test_key_usage_counters` | Successful authentications increment `use_count` and update `last_used_at`. |
| `test_admin_bypass_traceable` | An admin execution produces a ledger `bypass` row, an `admin_bypass` security event, and an `admin_execute` audit entry — all three, linked. |
| `test_admin_cannot_delete_economic_records` | Attempting to delete ledger / security-events / audit-log rows as the admin is refused by the triggers (and there is no API for it). |
| `test_no_false_positive_admin_lockout` | Under a flood of failed authentications, the admin can still revoke a key and toggle the kill switch. |
| `test_revocation_path_never_rate_limited` | Revocation succeeds even when the auth rate limit is tripped. |

**4C acceptance criteria:** all tests above green; full regression
green; SEC-006 and KEY-005 demonstrably checkable against
`security_events`; no secret material in any event; the admin
cannot be locked out of revocation or the kill switch by the
limits.

### 9.4 Stage 4D — hash chain + Auditor v4 (LTP-046 §4, 4D and Auditor v4 additions)

Mandatory additions from LTP-046 §4, 4D:

- Verificação de alteração, exclusão, inserção e reordenação.
- Checkpoint externo ao banco para detectar substituição do arquivo.
- Limitações documentadas para comprometimento simultâneo do banco e do mecanismo de checkpoint.
- Migração com fronteira histórica explícita.

Mandatory additions from LTP-046 §4, Auditor v4:

- Reavaliação individual dos sete itens UNVERIFIED.
- Não transformar uma regra em PASS simplesmente porque um novo campo foi adicionado.
- Cada regra precisa de evidência verificável.
- Preservar o histórico de resultados do ruleset v3.0.0.

Concrete scenarios [PROPOSED] (`tests/test_hashchain_phase4d.py`,
`tests/test_auditor_phase4.py`):

| Test | Scenario |
|---|---|
| `test_chain_detects_alteration` | Modify one field of one row (via direct SQL, bypassing the triggers) → verification FAILs with that row's id. |
| `test_chain_detects_deletion` | Delete one row (via direct SQL) → verification FAILs (the next row's `prev_hash` no longer matches). |
| `test_chain_detects_insertion_middle` | Insert a fabricated row in the middle with a recomputed hash for itself → verification FAILs (the following row's `prev_hash` is stale). |
| `test_chain_detects_reordering` | Swap two rows' positions → verification FAILs. |
| `test_chain_valid_on_clean_db` | A clean database verifies clean, end to end. |
| `test_chain_append_by_attacker_visible` | A well-formed row appended at the end with correct hashes verifies as chain-valid — and the test asserts the *documented* limitation: the chain alone cannot distinguish it from a legitimate row (this is why §6.5 exists). |
| `test_external_anchor_detects_replacement` | Replace the whole DB file with a recomputed one → chain verifies, but the checkpoint hash no longer matches the repo anchor (option A fixture) → detection reported. |
| `test_anchor_missing_warns` | No anchor committed → verification reports WARN, not PASS. |
| `test_migration_boundary_explicit` | Pre-chain rows are covered by the checkpoint hash only; the `chain_start` marker exists; verification output states the boundary (hash, row counts, timestamp). |
| `test_migration_does_not_rewrite_history` | Pre-chain rows are byte-identical after migration (no fresh hashes written onto old rows). |
| `test_auditor_v4_seven_items_reevaluated` | One test per UNVERIFIED item (§7.1): each rule's new status with the evidence that justifies it. |
| `test_auditor_v4_no_pass_by_field_existence` | Fixture with the new columns present but empty/inconsistent → the rules do NOT pass (a field's existence is not evidence). |
| `test_auditor_v4_new_rules` | One test per new rule in §7.2 (CREDIT-010…014, KEY-006, SEC-008, SEC-009, LOG-006), each with a PASS and a FAIL fixture. |
| `test_auditor_v4_history_preserved` | v3.0.0 reports remain readable and unchanged after the v4 migration; a v4 report carries `ruleset_version = "4.0.0"`. |
| `test_auditor_v4_clean_db_expectation` | The clean-DB result for v4.0.0 is recorded (the numbers are set by the 4D build review, §7.3 — this test asserts the recorded numbers, it does not invent them). |

**4D acceptance criteria:** all tests above green; full regression
green; LOG-005 re-evaluated with the chain + anchor; the 7
UNVERIFIED items each have an individual verdict with evidence;
v3.0.0 history preserved; the residual limitation (DB + anchor
compromised together) is documented in the report disclaimer.

### 9.5 Whole-phase acceptance criteria

- Each stage's criteria above, met in order, with its own review
  (LTP message → ChatGPT review → Lucas approval) before the next
  stage starts.
- Full regression green at every stage (baseline 159 tests, growing).
- Docs updated per stage (this architecture doc revised, status,
  known-vulnerabilities, pricing model).
- No real payments, no gateway, no public deploy, no real provider
  with paid consumption, no arbitrary execution, no recursion, no
  autonomy increase — at any point in the phase.

---

## 10. Risks that cannot yet be eliminated

### 10.1 The eight risks from LTP-046 §3, with the Phase 4 answer

| # | Risk (LTP-046 §3) | How Phase 4 addresses it | What remains |
|---|---|---|---|
| 1 | **Idempotency:** a retried request must not double-debit. | §3.1: `UNIQUE` idempotency key, defined retry behaviour, conflict rejection. | The key must be generated and stored by the caller correctly; a caller that generates a fresh key per retry defeats the mechanism. Client discipline is outside the DB's control. |
| 2 | **Concurrency:** two executions must not spend the same balance. | §3.2–3.3: single `BEGIN IMMEDIATE` transaction, in-transaction re-check, `CHECK (credits_balance >= 0)`. | Only within one database file. Two files, two service instances, or a copied DB are not protected. |
| 3 | **State machine:** invalid transitions must be impossible. | §2.4: single enforcement point, forbidden-transition table, tests for every forbidden transition. | Enforcement is application-level; direct SQL can still write an illegal state. The Auditor detects it after the fact (CREDIT-012) but cannot prevent it. |
| 4 | **Partial failures:** execution finished, process crashed before the economic record. | §3.4: detection scan, classification, automatic finalisation/refund, never auto-charge. | The "work done, no ledger row" case requires human judgment; the scan can flag it but not resolve it. A crash during the recovery itself can leave a row mid-transition — recovery must be idempotent too (same idempotency key, same result). |
| 5 | **Financial precision:** never float for money. | §2.5: integer credits, integer minor units, float field frozen out. | The legacy `agents.real_api_cost REAL` still exists and could be misread by a future developer as a money value. It is documented as frozen, not removed — removal would destroy history. |
| 6 | **Cost vs credit separation:** API cost, tokens and credits are different quantities. | §4.1: three separate columns, no presumed equivalence, no debit from token data. | A future pricing change could re-introduce an implicit conversion (e.g. "1 credit = N tokens") without updating the rules. The separation is a convention the Auditor checks (CREDIT-014), not a type-system guarantee. |
| 7 | **Abuse of limits:** rate limit does not replace concurrency control, accumulated quotas, or multi-key protection. | §5.4: limits per key and per user, plus the key cap, plus concurrency control (§3) as a separate layer. | A determined user can still spread usage across multiple accounts — there is no identity verification in this system (auth proves key possession, not human identity, `app/auth.py` limitations). Multi-account farming is detectable only by behaviour, not prevented. |
| 8 | **Admin privilege:** the Lucas bypass must be explicit, restricted and auditable, and must not allow deleting economic records or bypassing history integrity. | §5.6: bypass as a ledger row + security event, no delete API, triggers refuse deletes, Auditor rule SEC-009. | Lucas is a single point of trust: whoever holds his key holds the bypass, the kill switch and key management. There is no MFA, no second admin, no separation of duties. Key compromise is total compromise (see §10.2). |

### 10.2 Whole-DB replacement with checkpoint compromise

If the database **and** the checkpoint mechanism are compromised
together — the attacker rewrites the DB, recomputes the chain, and
updates the anchor (or forges a signature with a stolen key) — the
tampering is undetectable by any mechanism in this system (§6.4).
The mitigation is separation of write access, which is thin in a
single-user local system. This is not a gap to be closed in Phase
4; it is a property of the deployment model, and the Auditor's
LOG-005 finding states it on every report.

### 10.3 Further honest risks

- **Server secret in the database.** The HMAC server secret lives
  in the `meta` table (`app/store.py`,
  `Store.get_or_create_server_secret` — documented local-MVP
  limitation). Anyone who can read the DB file can read the secret
  and forge key hashes. The hash chain does not fix this: it
  protects the *record* of events, not the *authentication* of
  actors. Moving the secret out of the DB is a separate decision
  (env var / secret manager), not part of Phase 4 — but it must
  be named here, because a reader who sees "hash chain" might
  assume authentication is now safe. It is not.
- **Single-file SQLite.** DB theft is data theft. There is no
  encryption at rest in this phase. The chain detects tampering;
  it does not detect copying.
- **Stub data mistaken for real data.** The origin field (§4.3)
  mitigates this, but a future report, export or integration that
  ignores `origin` could present simulated consumption as real.
  The Auditor rule CREDIT-014 checks the ledger link, not every
  downstream consumer.
- **Clock trust.** Timestamps are wall-clock UTC
  (`_now()` in `app/store.py`). Ordering relies on the
  AUTOINCREMENT `id`, not on `ts` — but any rule that reasons
  about "before/after" from timestamps (e.g. KEY-005's
  `revoked_at` comparison) trusts the clock. Clock manipulation
  on the host is out of scope.
- **Scope creep into payments.** The pricing-model document is the
  most likely leak: once prices exist on paper, the next request
  is a gateway. The boundary (design, not charge) must be
  re-stated at every stage review.
- **The sandbox is still not validated.** Nothing in Phase 4
  changes that. Commercial use remains blocked on the independent
  sandbox-validation track (separate environment, expressly
  authorised — LTP-046 §5), even if the entire economic foundation
  is finished. A reader must not conclude from "Phase 4 done"
  that the system is ready for real users.

---

## Open decisions (for ChatGPT / Lucas)

1. **External anchor option** (§6.4): A (repo commit) is
   recommended for 4D; C (signed) is the hardening step; B
   (Lucas out-of-band) is the fallback. The choice is not made
   silently — it needs an explicit decision before the 4D build.
2. **Key expiry for existing keys** (§8.3): `expires_at = NULL`
   (unchanged) or a default expiry with notice. Not decided here.
3. **Rate-limit values** (§5.4): the proposed defaults (10
   auth/min, 10 creations/hour, 60 executions/hour, 5 active keys)
   are starting points for the 4C build review, not approved
   numbers.
4. **Minor unit for money** (§2.5): the exact integer minor unit
   (e.g. thousandths of a cent) is a 4B decision.
5. **Whether the Auditor rule updates land in 4A or wait for 4D**
   (§9.1): the *data* must exist after 4A; the rule code may wait
   for 4D. The 4A review decides.
6. **Canonical hash serialisation** (§6.2): fixed format to be
   recorded in the 4D review.

## Restrictions (standing, LTP-046 §5)

During the whole of Phase 4: no real payments, no gateway, no
public deploy, no real provider with paid consumption, no
arbitrary execution, no recursion, no increase in agent autonomy,
no change to Lucas's work computer to test isolation. Sandbox
validation remains an independent track, in a separate, expressly
authorised environment.

## Sources

- `app/store.py` — schema, triggers, `persist_global_audit`
  (`BEGIN IMMEDIATE` precedent), `get_or_create_server_secret`.
- `app/resource_core.py` — `charge` / `refund` / `record_real_api_cost`
  (free-text charges, separate commits, float cost meter).
- `app/auth.py` — key format, HMAC, generic failure, revocation,
  no expiry, no usage tracking, limitations.
- `app/config.py` — costs, quotas, `AUDIT_RULESET_VERSION =
  "3.0.0"`, audit interval, key max age.
- `app/control.py` — `execute_agent` / `execute_isolated`
  (charge up front, refund on refusal, check-then-act concurrency cap).
- `app/executor.py` — `ExecutionLimits.charge_minutes()`,
  supervisor (prepared, not enabled).
- `app/auditor/` — ruleset v3.0.0, 38 rules, 7 UNVERIFIED.
- `docs/auditor-architecture.md` rev 2 — §6 rule tables, §7 NOT
  VERIFIABLE items and minimal schema changes (ledger,
  security_events, hash chain, `granted_permissions`,
  `agent_status_at_start`).
- `docs/phase-3-closure.md` — limitations carried into Phase 4.
- LTP-045 (proposal), LTP-046 (ChatGPT review: split into 4A–4D,
  8 risks, §4 acceptance additions, §6 ten items).

Status: DRAFT for ChatGPT review (LTP-047) — implementation NOT authorized
