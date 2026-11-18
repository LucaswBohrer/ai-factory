# Phase 4 — Architecture Revision (Commercial Foundation)

**Date:** 2026-11-18
**Status:** DRAFT rev 2 for ChatGPT review (LTP-050) — implementation NOT authorized
**Requested by:** ChatGPT, LTP-046 §6 (10 items, in order); revised per
LTP-048 (10 mandatory items, in order) — direction approved with
reservations, implementation NOT authorised.
**Direction:** approved by ChatGPT (LTP-046, LTP-048); scope/first
stage (4A) approved by Lucas (16:46 -03), implementation of 4A only
after the architecture review is final.
**Baseline:** Phase 3 closed (LTP-044). 159 tests (148 pass, 11
Windows-only skipped). Auditor ruleset v3.0.0, 38 rules, 31 PASS /
7 UNVERIFIED on a clean DB.

**Rev 2 changes (what LTP-048 required, what changed):** the ledger
is now two tables (`ledger_operations` + `ledger_entries`, §2) instead
of one mutable row; the four buckets are defined mathematically from
the entries, with `refunded` as a cumulative historical measure, not
a holding (§2.5.1); the admin bypass creates no ledger posting — it is
a separate admin event (§2.6, §5.6); recovery flows are defined
explicitly (§3.4); the hash chain is a central `chain_entries`
table with a monotonic sequence, never ordered by timestamps (§6);
the remote Git anchor is accepted only with the LTP-048 conditions
(§6.4); token origin is immutable — `simulated` is never converted
to `measured` (§4.3); PERM-010 stays UNVERIFIED until 4C (§7.1);
SEC-007 requires persisted evidence of the agent's state at
execution time (§7.1); migration/rollback carry the three LTP-048
guarantees (§8). Everything else from rev 1 that LTP-048 accepted
is kept unchanged.

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
| 4A Ledger | Existing `users.credits_balance`, `ResourceCore.charge`/`refund` call sites, `executions.id`, `agents.id` | `ledger_operations` + `ledger_entries` tables [PROPOSED], formal state machine, idempotency keys, opening-balance genesis rows, reconciliation query rebuilt from entries |
| 4B Tokens | `ledger_operations` / `ledger_entries` (a usage record may reference a ledger operation, never the reverse), `executions.id` | `usage_records` table [PROPOSED], adapter interface [PROPOSED], origin/method on every record |
| 4C Antifraud | `api_keys` (new usage/expiry columns), `ledger_operations` / `ledger_entries` (abuse shows up as anomalous operations), `usage_records` (abuse shows up as anomalous consumption) | `security_events` table [PROPOSED], per-key/per-user limits, key usage counters |
| 4D Hash chain + Auditor v4 | All record formats above, stabilised (audit_log, audit_reports, ledger_operations, ledger_entries, security_events, usage_records) | `chain_entries` table, external checkpoint, Auditor ruleset v4.0.0, re-evaluation of the 7 UNVERIFIED items |

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

### 2.2 Proposed tables — `ledger_operations` + `ledger_entries` [PROPOSED]

Rev 1 proposed a single `ledger` table with a mutable `state`
column. LTP-048 §2.A rejects that: one row per operation is useful,
but a state column that is overwritten is not a book of accounts —
every money movement must be traceable, and no movement may
disappear when an operation changes state. Rev 2 therefore splits
the ledger into three layers:

1. **`ledger_operations`** — identity, idempotency, state, and the
   link to the execution. The state column lives here, and it is
   the *only* mutable column in the ledger.
2. **`ledger_entries`** — immutable economic postings. One row per
   money movement. Append-only (UPDATE/DELETE triggers, same
   pattern as `audit_log` in `app/store.py`). A state change on an
   operation that moves money appends an entry in the same
   transaction — it never rewrites history.
3. **Materialised balances** — `users.credits_balance` (spendable)
   and the derived held/spent figures, updated transactionally
   with the entries and always reconcilable from them (§2.5.1).

```sql
CREATE TABLE ledger_operations (              -- [PROPOSED] new table, 4A
    id TEXT PRIMARY KEY,                   -- operation id (uuid hex)
    idempotency_key TEXT NOT NULL UNIQUE,  -- one economic operation, one row, ever (§3)
    ts TEXT NOT NULL,                      -- ISO-8601 UTC, creation time
    actor TEXT NOT NULL,                   -- owner charged/refunded
    agent_id TEXT,                         -- agents.id the movement pays for, NULL if none
    execution_id TEXT,                     -- executions.id, NULL if not an execution charge
    kind TEXT NOT NULL,                    -- 'charge' | 'refund' | 'opening_balance'
    reason TEXT NOT NULL,                  -- 'create' | 'execution' | 'audit' | 'isolated_execution' | 'migration'
    amount INTEGER NOT NULL CHECK (amount >= 0),   -- credits, integer, never float
    state TEXT NOT NULL,                   -- see §2.3
    usage_record_id TEXT,                  -- usage_records.id [PROPOSED, 4B] or NULL
    updated_at TEXT NOT NULL,
    CHECK (state IN ('started','reserved','charged','finished','failed','refunded')),
    CHECK (kind IN ('charge','refund','opening_balance'))
);
CREATE INDEX idx_ledger_ops_actor ON ledger_operations (actor);
CREATE INDEX idx_ledger_ops_execution ON ledger_operations (execution_id);
CREATE INDEX idx_ledger_ops_state ON ledger_operations (state);

CREATE TABLE ledger_entries (                  -- [PROPOSED] new table, 4A
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- monotonic entry sequence (ordering, not a timestamp)
    operation_id TEXT NOT NULL,             -- ledger_operations.id
    ts TEXT NOT NULL,                       -- ISO-8601 UTC, posting time
    entry_type TEXT NOT NULL,               -- see §2.2.1
    amount INTEGER NOT NULL CHECK (amount >= 0),    -- credits, integer, never float
    balance_after INTEGER NOT NULL,         -- users.credits_balance (spendable) after this entry
    held_after INTEGER NOT NULL,            -- held balance (derived, §2.5.1) after this entry
    CHECK (entry_type IN ('reserve','debit','release','refund','opening_balance')),
    FOREIGN KEY (operation_id) REFERENCES ledger_operations (id)
);
CREATE INDEX idx_ledger_entries_op ON ledger_entries (operation_id);
CREATE UNIQUE INDEX idx_ledger_entries_one_debit
    ON ledger_entries (operation_id, entry_type)
    WHERE entry_type IN ('debit','release','refund');
```

Notes on the design:

- **An operation is not a posting.** `ledger_operations` answers
  "what is the state of this economic operation?"; `ledger_entries`
  answers "what money moved, when, and in which direction?".
  Reconciliation (§2.5.1) reads the entries only — it never trusts
  the operation state column. An operation whose state says
  `finished` but whose entries do not add up is a FAIL, not a
  success.
- **The partial unique index** enforces that one operation can
  have at most one `debit`, one `release` and one `refund` entry.
  A second debit or a second refund for the same operation is
  structurally impossible at the database level — not merely
  forbidden by application code. A `reserve` entry is excluded
  from the index because an operation may reserve once (the
  state machine allows exactly one `started → reserved`
  transition, §2.4); the single-reserve property is enforced by
  the transition guard, and the Auditor replays the entry history
  (CREDIT-012) to catch any violation.
- **`balance_after` / `held_after` on every entry** are
  materialised snapshots for auditability: a reviewer can walk
  the entries in `id` order and see the balances evolve without
  recomputing. They are derived values, not the source of truth —
  the source of truth is the entry stream itself (§2.5.1).
- **`kind = 'opening_balance'`** is the migration genesis
  operation (§8): one operation per user, `amount` = the user's
  `credits_balance` at migration time, `state = 'finished'`, with
  exactly one `opening_balance` entry. The ledger never rewrites
  history; it starts from a declared opening balance.
- **There is no `kind = 'bypass'`.** The admin bypass creates no
  ledger posting and no fictitious credit movement (LTP-048
  §2.A). If there is no movement, there is no posting: the bypass
  is recorded as a separate admin event — the existing
  `credit_bypass` audit-log entry (`app/resource_core.py`,
  `ResourceCore.charge` writes it today) plus, from 4C, an
  `admin_bypass` security event (§5.3). See §2.6 and §5.6.
- **`amount` is INTEGER credits.** Money, when it appears (4B
  estimated cost), is stored separately as integer minor units
  (see §2.5) — never in these tables, never as REAL. The existing
  `agents.real_api_cost REAL` field is left untouched and frozen:
  it is a legacy float meter, not a ledger input, and no Phase 4
  rule may read it as evidence of anything.

#### 2.2.1 The postings, defined formally

Each entry type is a movement between buckets (§2.5.1). The
definitions are exhaustive: no money movement in the system may
be represented by any other entry type, and no entry type may be
used for a movement it does not define.

| Entry type | Movement | When it is written |
|---|---|---|
| `opening_balance` | External → spendable: `spendable += amount` | Once per user, at the 4A migration (§8.1). The only entry type with no preceding operation state change — it *is* the genesis. |
| `reserve` | Spendable → held: `spendable -= amount`, `held += amount` | `started → reserved` (§2.4). The amount is held, not yet finally debited. |
| `debit` | Held → spent: `held -= amount`, `spent += amount` | `reserved → charged`. The hold is converted into a final debit. For immediate charges (creation, audit) the `reserve` and `debit` entries are written in the *same* transaction as the `started → charged` transition (§2.4): the reservation is instantaneous and never visible as a held state, but the full movement is still on the record. A debit never moves money directly out of spendable — it always passes through a reserve entry first, even if both entries share a transaction. |
| `release` | Held → spendable: `held -= amount`, `spendable += amount` | `failed → refunded` when no debit happened (the operation was reserved but the work never started, or was refused before the debit became final). A release cancels a reservation; it is not a refund. |
| `refund` | Spent → spendable: `spent -= amount`, `spendable += amount` | `failed → refunded` when a debit *did* happen (the work failed after the debit became final). A refund returns a final debit; it is not a release. |

**Release vs refund — why two entry types for "money back".**
A release undoes a reservation: the money was never finally
debited, so nothing was ever spent. A refund undoes a debit: the
money was spent, and is returned. Confusing them would destroy
the audit trail — a reviewer could no longer tell whether the
work had reached the point of no return. The state machine (§2.4)
decides which one applies: if a `debit` entry exists for the
operation, the terminal entry is a `refund`; if only a `reserve`
exists, it is a `release`. An operation can have exactly one of
the two (enforced by the partial unique index and by the terminal
state).

### 2.3 States

States live on `ledger_operations` only. `ledger_entries` has no
state — an entry is a fact, and facts do not change. The table
below states, for each operation state, which entries exist for
the operation and what the balance effect was when the operation
entered that state.

| State | Meaning | Entries that exist for the operation | Balance effect on entering |
|---|---|---|---|
| `started` | Operation opened: idempotency key registered, amount and reason recorded. No money has moved. | None. | None. |
| `reserved` | Amount held. | One `reserve` entry. | `spendable -= amount`, `held += amount`. |
| `charged` | Debit final: the hold is converted into a final debit. | `reserve` + `debit` entries (for immediate charges both are written in the same transaction as the `started → charged` transition). | `held -= amount`, `spent += amount` (for immediate charges the net effect is `spendable -= amount`, `spent += amount`, via the two entries). |
| `finished` | The paid work completed successfully; the charge is confirmed final. Terminal success. | `reserve` + `debit` (or, for an `opening_balance` operation, the single `opening_balance` entry). | None. |
| `failed` | The paid work did not happen (refused by the isolation gate, supervisor failure, crash before completion, or an invalid operation). Triggers the refund path. | Whatever entries existed before failure (`reserve` only, or `reserve` + `debit`). No new entry is written by the failure itself. | None yet — the money is still held (or already spent, awaiting refund). |
| `refunded` | The held or spent amount was returned to the spendable balance. Terminal. | Plus one terminal entry: `release` if no `debit` exists, `refund` if a `debit` exists (§2.2.1). | `release`: `held -= amount`, `spendable += amount`. `refund`: `spent -= amount`, `spendable += amount`. |

**Two kinds of operation, one machine.** Execution charges follow
the full path `started → reserved → charged → finished`
(`failed → refunded` on the failure path). Immediate charges
(agent creation, audit) follow the short path `started → charged →
finished`: the `reserve` and `debit` entries are written together
in the transition's transaction (§2.2.1) — there is nothing to
hold money for across transactions, and the short path is an
explicit allowed transition (§2.4), not an exception handled in
code comments. A rule that audits the ledger must accept both
paths.

**Admin bypass operations do not exist in the ledger.** There is
no `started → finished` bypass path and no `kind = 'bypass'`
(§2.2). An admin execution is recorded as an audit-log
`credit_bypass` entry (as today) and, from 4C, an `admin_bypass`
security event (§5.3). A bypass that created a ledger row would
be a fictitious movement: the amount "would have been charged"
is a counterfactual, not a movement, and counterfactuals do not
belong in a book of accounts.

### 2.4 Allowed transitions (the whole machine)

Every transition that moves money appends exactly the entry (or
entries) named in the table, in the same transaction as the state
change and the balance update (§3.2). A state change is never
written without its entry, and an entry is never written without
its state change. The entry stream is the history; the state
column is a cache of where the operation stands in it.

| From | To | Condition / effect / entry appended |
|---|---|---|
| `started` | `reserved` | Balance check inside the write transaction (§3): available balance ≥ amount, else the operation fails and stays `started` with a recorded refusal — it never becomes a negative balance. Appends one `reserve` entry. |
| `started` | `charged` | Immediate charges only (creation, audit). Same balance check as above. Appends a `reserve` entry and a `debit` entry in the same transaction. |
| `reserved` | `charged` | The paid work is under way / the debit becomes final. Appends one `debit` entry. |
| `reserved` | `failed` | The paid work will not happen (refused, supervisor failure, crash detected by recovery, §3.4). Money still held. Appends no entry — the failure is a state, not a movement. |
| `charged` | `finished` | The paid work completed. No balance movement, no entry. |
| `charged` | `failed` | The paid work failed after the debit became final (e.g. supervisor failed mid-run after the charge was finalised). Money still spent, awaiting refund. Appends no entry. |
| `failed` | `refunded` | The held or spent amount is returned to the balance. Terminal. Appends exactly one terminal entry: a `release` entry if the operation has a `reserve` and no `debit`; a `refund` entry if it has a `debit` (§2.2.1). The partial unique index (§2.2) makes a second terminal entry impossible. |
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
- `started` → `refunded` (nothing was held or spent, so nothing
  can be returned).
- `failed` → `refunded` with the wrong terminal entry (a `refund`
  entry on an operation with no `debit`, or a `release` entry on
  an operation with a `debit`).
- Any transition that would make `users.credits_balance` negative
  (guarded by the in-transaction check and by a `CHECK
  (credits_balance >= 0)` constraint [PROPOSED] on `users`, §3.3).

**Enforcement.** The state machine is enforced in exactly one
place: a ledger service module [PROPOSED] that owns all transitions
and appends all entries. `ResourceCore.charge` / `refund` become
thin callers of that service (their public signatures do not change
for existing callers). Direct SQL updates to
`ledger_operations.state` from anywhere else are a bug by
construction; the Auditor (§7) checks the transition history by
replaying the entries, not by trusting the service or the state
column (CREDIT-012).

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

#### 2.5.1 The four buckets, defined mathematically from the entries

Let, for one user, `OB`, `R`, `D`, `Rel`, `Ref` be the sums of the
`amount` column of `ledger_entries` of type `opening_balance`,
`reserve`, `debit`, `release`, `refund` respectively. All sums are
over that user's entries only (entries join to operations via
`operation_id`; operations carry the `actor`). The four buckets
are:

```
spendable = OB − R + Rel + Ref
held      = R − D − Rel
spent     = D − Ref
refunded  = Ref
```

Three of the buckets — `spendable`, `held`, `spent` — form a
**partition** of every credit ever granted: each credit is in
exactly one of them at any instant. The fourth, `refunded`, is
**not** a bucket in that partition: it is a cumulative historical
measure (the total credits ever refunded). A refunded credit is
already counted in `spendable` (the refund returned it there);
counting it in `refunded` as well would count the same credit
twice. Rev 1 made exactly this error — its invariant put
`Σ(refunded)` on the right-hand side while refunded credits also
sat in the spendable balance — and rev 2 corrects it here.

**Reconciliation invariant** (the property the Auditor will check
in v4.0.0, §7, rule CREDIT-010):

```
OB = spendable + held + spent
```

Proof by substitution: `(OB − R + Rel + Ref) + (R − D − Rel) +
(D − Ref) = OB`. The invariant holds for every prefix of the
entry stream, not only at the end — after every entry, the three
buckets still sum to `OB`. That is what makes it checkable at any
point in history, and what makes a mid-stream violation locatable
(the first entry after which the invariant breaks is the fault).

**How reconciliation works (and what it must not do).** The
reconciler reads `ledger_entries` only and recomputes
`spendable`, `held` and `spent` from the sums above; then it
compares the recomputed `spendable` with `users.credits_balance`
(the materialised balance). It never uses `users.credits_balance`
as an *input* to the computation — the balance is the thing being
validated, and a check that consults the value it is validating
proves nothing (LTP-048 §2.B). The same rule applies to the
materialised `balance_after` / `held_after` snapshots on the
entries (§2.2): they are cross-checked, not trusted. A mismatch
is a FAIL with the first violating entry id.

**Worked example.** A user starts with `OB = 100`. An execution
reserves 5, is charged, finishes: entries `reserve 5`, `debit 5`.
Buckets: `spendable = 100 − 5 = 95`, `held = 5 − 5 = 0`,
`spent = 5`, `refunded = 0`. Invariant: `95 + 0 + 5 = 100`. A
second execution reserves 3 and is refused: entries `reserve 3`,
`release 3`. Buckets: `spendable = 95 − 3 + 3 = 95`,
`held = 3 − 3 = 0`, `spent = 5`, `refunded = 0`. A third
execution reserves 2, is charged, fails: entries `reserve 2`,
`debit 2`, `refund 2`. Buckets: `spendable = 95 − 2 + 2 = 95`,
`held = 0`, `spent = 2 − 2 = 0`, `refunded = 2`. Invariant still
`95 + 0 + 0 = 100` — and the `refunded = 2` is a historical fact
about the third execution, not 2 credits sitting in a fourth
bucket. If anyone added `refunded` to the partition, the sum
would read 102 against 100 granted — the double count, made
visible.

### 2.6 What the ledger does NOT do

- It does not set prices (the pricing model is a parallel document).
- It does not convert tokens to credits (4B records consumption;
  conversion, if any, is a pricing decision, not a ledger rule —
  LTP-046 §3.6: cost of API, tokens and internal credits are
  different quantities; no equivalence is presumed).
- It does not authorise anything: authorisation stays where it is
  (`Auth.authenticate`, `Control` ownership checks). The ledger
  records the economic consequence of an already-authorised action.
- It does not record admin bypasses: a bypass is an admin event,
  not a money movement (§2.2, §5.6). The ledger's silence about a
  bypass is not a gap — the gap would be inventing a posting for
  money that never moved.
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
  (the existing operation row and its state) and performs no new money
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

**Proposed rule:** every economic step writes its operation state
transition, its ledger entry (or entries), its
`users.credits_balance` update, and its audit-log entry in ONE
`BEGIN IMMEDIATE` transaction. `BEGIN IMMEDIATE`
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
4. If balance < amount: roll back, record the refusal (an operation row
   in `started` with the refusal noted, or a `security_events` row
   [PROPOSED, 4C] — the 4A decision is that a refused reservation
   leaves a trace; the exact table is fixed in the 4A build review).
5. Insert/update the operation row (`started` → `reserved`),
   append the `reserve` entry, and update the balance.
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

LTP-048 §2.B requires the architecture to define explicitly:
what happens if the process crashes after the reserve; what
happens if the execution finishes but the economic recording
fails; how a pending operation is identified; how recovery runs
after a restart; and how recovery cannot create a second debit
or refund. This section answers each in turn. The hard rule
stands: **recovery never silently creates a charge**, and
reconciliation rebuilds its result from the entries (§2.5.1),
never by consulting the balance it is validating.

#### 3.4.1 How a pending operation is identified

A pending operation is any `ledger_operations` row in a
non-terminal state (`started`, `reserved`, `charged`, `failed`)
— terminal states are `finished` and `refunded` (§2.4). The
reconciliation scan [PROPOSED] (`ledger_reconcile`,
admin-triggered, read-only by default) classifies every pending
operation by joining it to `executions` and to its own entries:

| Pending case | Operation state | Execution row | Entries present |
|---|---|---|---|
| A. Reserved, execution never started | `reserved` | No matching `executions` row (or one in `running` older than the age threshold) | `reserve` only |
| B. Reserved/charged, execution finished | `reserved` or `charged` | `executions.status = 'finished'` | `reserve`, or `reserve` + `debit` |
| C. Reserved/charged, execution refused/failed | `reserved` or `charged` | `executions.status = 'refused'` or `'failed'` | `reserve`, or `reserve` + `debit` |
| D. Work done, no ledger operation at all | — (no operation) | `executions.status = 'finished'` with no operation referencing it | None |
| E. Started, never reserved | `started` | No execution row | None |

The scan also flags any operation whose entries do not match its
state (e.g. state `charged` with no `debit` entry, state
`refunded` with no terminal entry) — a state/entry mismatch is a
corruption signal, not a recovery case, and is reported as FAIL,
never auto-repaired.

The scan is read-only: it produces a report and changes nothing.
Recovery is a separate, explicit, admin-triggered step (§3.4.3)
— nothing is auto-repaired by the scan itself, and nothing is
auto-repaired at process start (see §3.4.3).

#### 3.4.2 What happens in each crash scenario

**Crash after the reserve.** The reservation is one transaction
(§3.2): `reserve` entry + balance update + state change to
`reserved` commit together or not at all. If the process dies
*inside* that transaction, SQLite rolls it back — no entry, no
balance movement, the operation is still `started`, and a retry
with the same idempotency key (§3.1) re-runs the reservation
exactly once. If the process dies *after* the transaction
commits, the operation is durably `reserved` with its `reserve`
entry: case A above. The hold is released by recovery
(§3.4.3) once the age threshold passes (proposed default: 1
hour) and no live execution exists for it. A hold must never be
permanent, and a hold is never converted into a charge by
recovery — the work never started, so there is nothing to charge
for.

**Execution finished, economic recording failed.** The failure
LTP-046 §3.4 names. In the new flow the order is: ledger
reservation → `start_execution` → run → finalise. The
finalisation step is one transaction: state change (`charged` →
`finished`, or `reserved` → `charged` → `finished` if the debit
had not yet been finalised) + its entries + the audit-log entry.
If the process dies before that transaction commits, the
operation is durably `reserved` or `charged` and the execution
row is durably `finished`: case B above. Recovery advances it to
`finished`, appending the missing `debit` entry if (and only if)
none exists for the operation. The money was already reserved;
finalisation only confirms it — it does not take new money.

**Crash during recovery itself.** Recovery must be idempotent
too. Every recovery action re-checks, inside its own transaction,
whether the entry it is about to write already exists (by
`(operation_id, entry_type)`, §2.2) and whether the operation is
already in the target state. A recovery run interrupted
mid-way can be re-run safely: the second run finds the entries
already present and writes nothing new. A recovery action that
finds the operation already terminal returns the stored result,
exactly like an idempotency-key retry (§3.1).

#### 3.4.3 How recovery runs after a restart

1. **At process start, nothing is repaired.** The system starts
   with pending operations still pending. A start-up scan runs
   read-only and reports the pending cases (§3.4.1) in the
   start-up log; it writes no entry and changes no state.
   Silent auto-repair at start-up would hide crashes and could
   race with a second process starting at the same time.
2. **Recovery is an explicit admin action** (`ledger_recover`
   [PROPOSED], admin-only, one operation or one case class at a
   time). It takes the scan report as input and applies the
   actions below, one transaction per operation.
3. **Actions, by case:**
   - *Case A (reserved, no live execution, past the age
     threshold):* `reserved` → `failed` → `refunded`, appending
     a `release` entry. The hold is released; no charge.
   - *Case B (execution finished, finalisation missing):*
     advance to `finished`, appending the `debit` entry if
     absent. No new money is taken.
   - *Case C (execution refused/failed):* `failed` →
     `refunded`, appending a `release` entry (no debit) or a
     `refund` entry (debit exists). Automatic: money is never
     kept for work that did not happen.
   - *Case D (work done, no ledger operation):* **never auto-charge.**
     Flag as an anomaly for admin review. Creating a charge
     from recovery would invent a money movement with no
     idempotency key and no authorisation record. The admin may
     resolve it only by opening a new, explicitly authorised
     operation with a new idempotency key — never by editing
     history.
   - *Case E (started, never reserved):* no money moved; the
     operation may be left `started` (a retry with the same key
     resumes it) or closed as `failed` by the admin with no
     entry. Either way, no entry is written for a non-movement.
4. **Every recovery action is itself recorded:** the state
   change, its entries, and an audit-log entry with action
   `ledger_recovery` stating the case, the operation id, and the
   admin who triggered it. Recovery is visible in the same trail
   it repairs.
5. **Recovery cannot create a second debit or refund.** Three
   independent layers: (a) the partial unique index on
   `(operation_id, entry_type)` (§2.2) refuses a duplicate
   `debit`/`release`/`refund` at the database level; (b) the
   state machine refuses the transition once the operation is
   terminal (§2.4); (c) the recovery re-check (§3.4.2) reads the
   entries before writing. Any one layer alone would be enough;
   all three are specified because a recovery bug that
   double-charges is the worst failure this phase can produce.

**Ordering guarantee.** In the new flow the order is: ledger
reservation → `start_execution` → run → finalise. The
reservation is what makes the "no ledger operation" case an anomaly
rather than a routine occurrence. The current flow charges before
`start_execution` too (`app/control.py`, `execute_agent`), but
without a ledger operation; the 4A change is to make that pre-step a
ledger transition with entries, not to invent a new order.

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
    ledger_id TEXT,                          -- ledger_operations.id [PROPOSED, 4A] the consumption is associated with, or NULL
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

**Permanent rule — origin is never rewritten (LTP-048 §2.D).**
`simulated` can never be converted automatically into
`measured`, not even by a reconciliation process. A reconciliation
that "upgrades" a stub record into a real measurement would be
fabricating evidence: the stub never measured anything, and no
later process can retroactively give it a measurement it did not
make. The record preserves its original origin permanently:
`usage_records` gets the same UPDATE/DELETE triggers as
`audit_log` (4B migration, §8.2), so there is no code path that
changes an `origin` after insert — not by the adapter, not by
recovery, not by the admin. If a real measurement of the same
execution later becomes available (a real adapter replacing the
stub), it is written as a *new* record with `origin =
'measured'`, linked to the same `execution_id`; the simulated
record stays, marked as what it was. The two records are
distinguishable forever by `origin` and `method`. The same
permanence applies to `estimated`: an estimate is never
relabelled as a measurement, and an estimated cost is never
relabelled as a provider's actual billed cost — an estimate and a
bill are different information, and the record must keep saying
which one it is.

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

**Append-only, admin-read-only, within local limits.** The table
gets the same UPDATE/DELETE triggers as `audit_log`
(`app/store.py`), and the same ownership rule as
`Control.list_audit_log`: only Lucas reads it; a non-admin gets
nothing (not even a count). The triggers make the table
append-only *within the local security model* — they do not make
it tamper-evident (that is 4D's job, §6), and they do not stop
direct SQL writes or whole-file replacement. The table's
append-only property is a guard against application bugs and
casual deletion, not a cryptographic guarantee; the document does
not claim otherwise.

**What is never in an event (LTP-048 §2.E — exhaustive list):**
a full API key; a key hash; a server secret; a password; an
authentication token of any kind; a full agent id on a denial
path (the id itself would confirm existence); or any prompt /
payload content or other unnecessary sensitive content. The
Auditor v4.0.0 (§7) includes a rule that scans
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
| `admin_bypass` | Ledger bypass path (§5.6): written by the same code path that performs the bypass | The `admin_execute` audit entry it corresponds to (by target/action), the amount that would have been charged. There is no ledger operation id — the bypass creates no ledger posting (§2.2). |
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

**Rate limits are tested under concurrency, not only in
sequential calls (LTP-048 §2.E).** A limit checked as
read-then-increment across separate statements has the same race
as the balance check in §3.3: N parallel callers can all pass
before any of them increments. The 4C tests therefore include
concurrent limit tests (§9.3) — parallel authentication attempts
against one key, parallel execution starts against one user —
and the implementation must enforce the limit inside the same
write transaction as the counter update, so the N+1-th concurrent
caller is refused, not merely the N+1-th sequential one.

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
Phase 4 makes its boundaries explicit, and rev 2 corrects rev 1
on one point: the bypass is **not** a ledger record. A bypass
creates no ledger posting and no fictitious credit movement
(§2.2, LTP-048 §2.A). What exists for a bypass is the admin-event
trail: the `credit_bypass` audit-log entry (as today) and, from
4C, the `admin_bypass` security event (§5.3). The Auditor rule
SEC-009 (§7) checks that trail — every `admin_bypass` event has
a matching `admin_execute` audit entry, and every admin
execution has its bypass event — instead of checking a ledger
row that would record money that never moved.

- **Explicit:** every bypass is a `credit_bypass` audit entry
  and an `admin_bypass` security event, with the amount that
  would have been charged. There is no silent bypass.
- **Restricted:** the bypass covers only the charge for an
  already-authorised admin execution. It does not cover, and must
  never be able to cover: deleting ledger operations or entries, deleting
  `security_events` rows, deleting audit-log rows, altering the
  hash chain, or bypassing hash-chain verification. There is no
  delete API for any of those tables, and the UPDATE/DELETE
  triggers already refuse the operation at the database level
  (`app/store.py`); the bypass has no code path around them.
- **Auditable — and unable to switch off its own audit:**
  the bypass is an explicit exception to *charging*, never an
  exception to *recording*. It cannot disable the auditing of
  security events (LTP-048 §2.E): the `admin_bypass` event is
  written by the same code path that performs the bypass, not by
  a caller that could skip it, and there is no flag, config, or
  admin call that suppresses `security_events` writes. An admin
  action with no corresponding security event is itself a
  detectable anomaly — the Auditor rule SEC-009 FAILs on a
  `credit_bypass` audit entry with no matching `admin_bypass`
  event, and vice versa. The admin's power is to act without
  being charged — not to act without being recorded.

---

## 6. Hash chain and external checkpoint strategy

### 6.1 What the chain covers

The chain covers `audit_log` and `audit_reports` — the two
append-only tables (`app/store.py`, UPDATE/DELETE triggers). It
also covers `ledger_operations` and `ledger_entries` once those
tables exist (4A) and `security_events` (4C): the chain is built
over a single ordered event stream, not one chain per table, so a
deletion in any table breaks the same chain. Both ledger layers
are covered — the operations (state) and the entries (money
movements) — because a state change without its entries, or
entries without their operation, is exactly the inconsistency
the chain exists to catch. The integrity-design decision — which
events the chain must protect — is made in 4A (LTP-046 §2
caveat); the implementation is 4D, after the record formats are
frozen.

### 6.2 Chain construction [PROPOSED]

Rev 1 put `prev_hash` / `entry_hash` columns on each covered
table and ordered verification by `id`. LTP-048 §2.C requires a
central chain-entries table and forbids ordering by timestamps.
Rev 2 adopts the central table: one ordered stream, one chain,
covering all four tables.

```sql
CREATE TABLE chain_entries (                   -- [PROPOSED] new table, 4D
    seq INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotonic chain sequence — THE order
    ts TEXT NOT NULL,                       -- ISO-8601 UTC, recorded instant (metadata only, never ordering)
    entity_type TEXT NOT NULL,              -- 'audit_log' | 'audit_reports' | 'ledger_operation' | 'ledger_entry' | 'security_event'
    entity_id TEXT NOT NULL,                -- id of the covered row (audit_log.id, audit_reports.report_id, ledger_operations.id, ledger_entries.id, security_events.id)
    canonical_hash TEXT NOT NULL,           -- SHA-256 over the canonical serialisation of the covered row (§6.2.1)
    prev_hash TEXT NOT NULL,                -- entry_hash of seq-1, or 'GENESIS' for seq=1
    entry_hash TEXT NOT NULL,               -- SHA-256 over the chain payload (§6.2.1)
    UNIQUE (entity_type, entity_id)         -- one chain entry per covered row
);
```

**Ordering is by `seq`, never by timestamps.** Timestamps do
not guarantee a total order, especially under concurrency: two
rows can share a timestamp, a clock can jump backwards, and two
processes can write in an order their timestamps do not reflect.
The `AUTOINCREMENT` sequence is assigned inside the same
`BEGIN IMMEDIATE` transaction as the covered row's insert, so
the sequence *is* the insertion order — by construction, not by
observation. The `ts` column is metadata for humans; no rule,
no verifier, and no test may use it to order anything.
`chain_entries` itself gets the same UPDATE/DELETE triggers as
`audit_log`: a chain entry is never edited or deleted by the
application — the only way to "correct" the chain is to append
to it, and a correction that rewrites history is itself a new
entry, visible in the stream.

**Original record + chain entry in the same transaction.** The
covered row and its `chain_entries` row are inserted in one
`BEGIN IMMEDIATE` transaction (the `persist_global_audit`
precedent, `app/store.py`): a row is never written without its
chain entry, and a chain entry is never written without its row.
If either insert fails, both roll back. A row that exists in a
covered table with no chain entry is a FAIL (an orphan row), and
a chain entry with no covered row is a FAIL (an orphan entry) —
both are detected by the verification walk (§6.2.2), which joins
the two.

#### 6.2.1 Canonical serialisation

Same logical content must produce the same bytes before hashing,
in the writer and in the verifier, or the chain is unverifiable.
The canonical form is fixed and versioned:

- **Encoding:** JSON object, UTF-8, keys sorted lexicographically
  (byte order), no whitespace, no trailing newline.
- **Values:** integers as JSON numbers (never floats — the
  economic tables carry integers only, §2.5); strings as-is;
  `NULL` as JSON `null`; booleans as JSON booleans. No
  implementation-defined formatting (no Python `repr`, no
  locale-dependent dates — timestamps are ISO-8601 UTC strings,
  already canonical in the schema).
- **Field set:** exactly the covered row's stored fields, in the
  sorted-key order — no computed fields, no `balance_after`
  snapshots excluded or included by accident: the field set is
  listed per entity type in the 4D build review and frozen there.
  A field added later changes the canonical form, which is why
  the form carries a version tag.
- **Version tag:** every hashed payload is prefixed with the
  domain separator `af-chain-v1` (a new version tag is required
  for any change to the field set or the encoding — old entries
  are verified with their own version, recorded per entry).
- **Chain payload:** `entry_hash = SHA256( "af-chain-v1" || "\n"
  || seq || "\n" || entity_type || "\n" || entity_id || "\n" ||
  canonical_hash || "\n" || prev_hash )`, where
  `canonical_hash = SHA256( canonical_json(row) )` and `||` is
  byte concatenation with `\n` separators. The sequence number
  is part of the payload: reordering two entries breaks both
  hashes, not just the linkage.

#### 6.2.2 Verification [PROPOSED]

`verify_chain` (Auditor rule LOG-005 / LOG-006 in v4.0.0, §7)
walks `chain_entries` in `seq` order from the genesis entry:

1. `seq` is contiguous from 1 (a gap is a FAIL — a deleted chain
   entry).
2. For each entry, `prev_hash` equals the previous entry's
   `entry_hash` (`'GENESIS'` for `seq = 1`).
3. `entry_hash` equals the recomputation over the stored
   `canonical_hash`, `seq`, `entity_type`, `entity_id` and
   `prev_hash` (§6.2.1).
4. `canonical_hash` equals the recomputation over the covered
   row's current stored fields (re-serialised canonically).
5. Every covered row has exactly one chain entry, and every
   chain entry has exactly one covered row (the `UNIQUE
   (entity_type, entity_id)` constraint plus the join check).

**What verification detects:** alteration of any covered field
(step 4 fails); deletion of a covered row (step 5 fails — the
chain entry is orphaned) or of a chain entry (steps 1–2 fail);
insertion of a fabricated row in the middle (step 2 fails — the
following entry's `prev_hash` is stale — or step 5 fails);
reordering (steps 2–3 fail, because `seq` is in the payload).
A well-formed row appended at the *end* with correct hashes
still verifies as chain-valid — the chain alone cannot
distinguish it from a legitimate row (§6.5).

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
3. Insert a genesis chain entry (`seq = 1`, `entity_type =
   'chain_start'`, `prev_hash = 'GENESIS'`, whose
   `canonical_hash` commits to the checkpoint hash). Rows before
   this marker are covered by the checkpoint hash, not by the
   chain; rows after it are covered by the chain — each covered
   row gets its own `chain_entries` row from here on.
4. From the marker on, every insert carries its chain entry in
   the same transaction (§6.2).

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
database, protected by a mechanism independent of it.

**Decision (LTP-048 §1.1): option A — checkpoint committed to
the git repo — is accepted as the first implementation, with
conditions.** A Git commit is not automatically an immutable
anchor: if someone can rewrite the remote history, replace the
commit, or compromise the database and the repository together,
the protection loses force. Option A is valid only if all five
conditions hold:

1. **Published in the remote repository.** The checkpoint is
   committed and pushed to the remote `ai-factory` repo (the
   repo is already the shared Theo↔ChatGPT channel). A commit
   that exists only locally is not an anchor — it lives on the
   same machine as the database it is supposed to protect.
2. **The commit contains the checkpoint hash, the final
   sequence, and the reference instant.** The anchor file
   records: the checkpoint hash (§6.3), the final `chain_entries`
   sequence number at checkpoint time, the row counts of the
   covered tables, and the ISO-8601 UTC reference instant.
   Without the sequence, an attacker could truncate the chain
   and present a shorter, valid one.
3. **The remote history is protected against force push and
   against deletion of the used ref.** This is an operational
   configuration, not a code property: a branch protection rule
   (or ruleset) on the remote repository must forbid force
   pushes and ref deletion on the branch/ref carrying the
   checkpoints. The 4D build records the exact rule and verifies
   it is in place; without it, condition 1 is void. Note the
   residual: whoever administers the repo can change the rule —
   the anchor is only as independent as the repo's write access
   is from the DB's.
4. **The Auditor verifies the published checkpoint, never only
   the locally stored hash.** The anchor check consults the
   checkpoint *as published at the protected remote ref* — not
   the hash stored in the local `meta` table and not a local
   file, both of which an attacker with DB/filesystem access
   could rewrite together with the database. How the Auditor
   reaches the remote (a `git ls-remote` / API read of the
   anchor file at the protected ref) is a 4D build decision,
   recorded in the 4D review; the architectural rule is that
   the check's input must come from the remote, with its
   provenance recorded in the finding.
5. **Anchor lookup failure → UNVERIFIED, never PASS.** If the
   remote cannot be consulted (network down, ref missing, anchor
   file unreadable, protection rule not verifiable), the
   LOG-005 finding is UNVERIFIED with the reason stated. A
   check that cannot see its anchor proves nothing, and it must
   never report a PASS it did not earn.

**Whole-DB replacement test — three scenarios, distinguished
(LTP-048 §2.C).** The 4D tests (§9.4) must cover all three, and
must not conflate them:

| Scenario | What the attacker can do | Expected detection |
|---|---|---|
| (a) Replacement without anchor access | Replaces the whole DB file, recomputes the chain from genesis. Cannot read or write the remote anchor. | **Detected.** The recomputed DB's checkpoint hash (and final sequence) does not match the published anchor (§6.4, conditions 2 and 4). |
| (b) Replacement with anchor access (read) | As (a), but can *read* the published anchor (it is, after all, in a public repo). Cannot rewrite the remote ref (condition 3 holds). | **Detected.** Knowing the anchor hash does not let the attacker forge a preimage: they cannot produce a different history whose checkpoint hash equals the published one, and they cannot update the published anchor to match theirs. |
| (c) Simultaneous compromise of DB and anchor | Rewrites the DB, recomputes the chain, *and* rewrites the remote ref (force push, or repo-admin access), publishing a matching anchor. | **NOT detectable — never promised.** No mechanism in this system detects an attacker who controls both the record and the anchor. The Auditor's finding states this on every report (§6.4, residual limitation). |

**Option C (signed checkpoint) is the later hardening step.**
The checkpoint hash is signed, and the signature is stored in
the repo alongside the hash. It detects tampering even if the
attacker can write to the repo — they cannot forge the signature
without the private key. The private key must live outside the
database and outside the audited environment (LTP-048 §1.1); key
generation, storage, backup and the signing step are a separate
decision, not part of 4D. Option C is recorded as the follow-up,
not pretended to exist from day one.

**Option B (checkpoint held by Lucas out-of-band) remains an
operational fallback.** Lucas keeping a checkpoint hash in his
own notes is a valid anchor for a single-user system — but it
must not depend on Lucas executing routine manual checks
(LTP-048 §1.1). Option B is the fallback when the remote anchor
cannot be consulted, not the primary verification path.

**Residual limitation (documented, not solved):** scenario (c)
above. The mitigation is separation of write access: whoever
can write the DB must not be able to write the anchor, and vice
versa. For a single-user local system that separation is thin,
and this document does not pretend otherwise. The Auditor
v4.0.0 rule for LOG-005 reflects this exactly (§7): chain valid
+ anchor present and matching → PASS for "no alteration detected
within the anchored history"; anchor missing, stale or
unreachable → WARN or UNVERIFIED (§6.4, condition 5); chain
broken → FAIL; and the finding always states that whole-DB
replacement with a recomputed anchor is outside what the check
can detect.

Three concrete options, with trade-offs — no silent choice (the
table from rev 1 is kept, with the decision now made):

| Option | How it works | Strengths | Weaknesses |
|---|---|---|---|
| **A. Checkpoint hash committed to the git repo — ACCEPTED, with conditions (§6.4)** | After each checkpoint (e.g. weekly, or after each stage), the checkpoint hash (hash only — no data) is committed to the `ai-factory` repo (the repo is already the shared Theo↔ChatGPT channel, and its history is append-only in practice). Verification compares the DB's checkpoint hash against the repo's. | Free; versioned; public without leaking data (a hash reveals nothing); fits the existing workflow; an attacker who can rewrite the DB but not the repo is detected. | Coarse granularity (only as fresh as the last commit); anyone with repo write access can update the anchor too — the anchor is only as independent as the repo's write access is from the DB's; a public repo means the checkpoint schedule is visible. Conditions 1–5 above are what make it valid; without them it is not an anchor. |
| **B. Checkpoint held by Lucas out-of-band — FALLBACK** | Lucas keeps the checkpoint hash himself (his own notes, a message, a file on his own machine). Verification is manual: compare the DB's hash with his copy. | No infrastructure; the trust anchor is Lucas himself — the one party the system is built to serve; no third party involved. | Manual, coarse, and fragile: human error, lost notes, and no automation; verification happens only when Lucas does it. Must not depend on routine manual checks. |
| **C. Signed checkpoint — LATER HARDENING** | Lucas (or a key he holds) signs the checkpoint hash; the signature is stored in the repo alongside the hash. Verification checks the signature against his public key. | Detects tampering even if the attacker can write to the repo — they cannot forge the signature without the private key; the strongest of the three. | Key management: the private key must be generated, stored and backed up securely, outside the DB and outside the audited environment; more machinery (signing step, public-key distribution); a lost key means the anchor cannot be updated. |

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
| CREDIT-004 (exact charge ↔ execution reconciliation) | 4A | For every `executions` row with `status = 'finished'`, exactly one `ledger_operations` row with `kind = 'charge'`, `execution_id = executions.id`, `amount = minutes × COST_EXECUTION_PER_MIN`, and a terminal state (`finished`); the operation's entries include a `debit` entry for the same amount; and every execution charge matches one execution. | PASS / FAIL (exact). No more approximate matching. |
| CREDIT-009 (creation charge attribution) | 4A | For every agent row, a `ledger_operations` row with `reason = 'create'`, `agent_id = agents.id`, `amount = COST_CREATE`, terminal state, and a matching `debit` entry. The creation charge finally has an agent link (today it is logged with `agent_id=None`, `target = ""`). | PASS / FAIL (exact). |
| SEC-006 (denied cross-user attempts) | 4C | Count `forbidden_actor` / `unknown_agent_denied` events in `security_events`; alert (WARN) above a threshold [PROPOSED, value set in the 4C review]; zero events is PASS with the explicit note "no denials recorded in the window" — not "no denials happened". | PASS / WARN. FAIL only if the table itself is missing or unreadable. |
| KEY-005 (revoked key used after revocation) | 4C | A `public_id` that appears in `auth_failed` events with `ts` after its `revoked_at`. | PASS (none found) / FAIL (found, with the public_id and timestamps as evidence). |
| LOG-005 (tamper-evidence) | 4D | Chain verification (§6.2) + external anchor comparison (§6.4). Chain valid and anchor matching → PASS for "no alteration detected within the anchored history", with the anchor's identity and timestamp in the finding. Anchor missing/stale → WARN. Anchor unreachable or unverifiable → UNVERIFIED, never PASS (§6.4, condition 5). Chain broken → FAIL with the row id. Whole-DB replacement with a recomputed anchor stays outside the check — the finding says so. | PASS / WARN / FAIL / UNVERIFIED (never a blanket "the log is safe"). |
| PERM-010 (was `execute_limited` explicitly granted?) | **4C — stays UNVERIFIED until 4C (LTP-048 §1.2)** | 4A may prepare the identifiers and relationships (the `granted_permissions` column, see below), but the rule only leaves UNVERIFIED when a provenance trail exists that can demonstrate, for each permission grant: who granted the permission; which permission was granted; to which agent or user; when it occurred; what the state was before and after; and whether the operation was authorised. The existence of an economic record does not prove the provenance of a permission grant — a ledger row is evidence of money, not of authorisation. The provenance trail is a 4C deliverable (built on the `security_events` table and the grant records of §5); until it exists, PERM-010 is UNVERIFIED, and no partial ceiling in 4A changes that. Rev 1 proposed a "4A (partial)" ceiling; rev 2 withdraws it — LTP-048 §1.2 decided the matter, and the document records the decision, not the superseded proposal. | **UNVERIFIED until 4C.** In 4C: PASS / WARN / FAIL on the provenance trail, never on the mere existence of a column. |
| SEC-007 (executions by suspended/killed agents) | 4A | New column `agent_status_at_start TEXT` [PROPOSED] on `executions`, written by `Store.start_execution` from the agent row at call time (proposed in `docs/auditor-architecture.md` §7.5). The rule checks: no `executions` row has `agent_status_at_start` IN (`suspended`, `killed`). **Historical criterion (LTP-048 §3):** PASS requires persisted evidence of the agent's state *at the time of the execution*. The current state does not prove the historical state — an agent that is active today may have been suspended when the execution ran, and an agent suspended today may have been active then. A rule that reads the agent row's *current* status and calls it evidence of the past is wrong by construction; only the `agent_status_at_start` value recorded at `start_execution` time counts. If the column is missing or NULL for an execution, that execution is UNVERIFIED (missing data), never PASS. | PASS / FAIL (exact), UNVERIFIED on missing data. A FAIL here means the code check was bypassed — direct insert or a bug. |

### 7.2 New rules in v4.0.0 [PROPOSED]

| Rule [PROPOSED] | Checks |
|---|---|
| CREDIT-010 | Ledger reconciliation: the invariant of §2.5.1 holds for every user (recompute the buckets from `ledger_entries` alone, compare the recomputed `spendable` with `users.credits_balance`). Any mismatch is FAIL. |
| CREDIT-011 | Idempotency: no two `ledger_operations` rows share an `idempotency_key`; no operation was debited twice (one `debit` entry per operation, §2.2). |
| CREDIT-012 | State-machine legality: every operation's state is reachable by an allowed transition (§2.4) — checked by replaying the operation's entry history, not by trusting the state column. |
| CREDIT-013 | No negative balance by race or retry: `users.credits_balance >= 0` for every user, and no entry carries a negative `balance_after`. |
| CREDIT-014 | Simulated data is never a charge: no ledger operation references a `usage_records` row with `origin = 'simulated'` (§4.3). |
| KEY-006 | Key hygiene, extended: keys past `expires_at` are rejected (not merely old); `last_used_at`/`use_count` are populated for keys used after 4C. |
| SEC-008 | Security events contain no secrets: scan `security_events.detail` for key-shaped material (`afk_…`, 64-hex strings, key hashes) — any hit is FAIL. |
| SEC-009 | Admin bypass auditability: every `admin_bypass` security event has a matching `admin_execute` audit entry, and every `credit_bypass` audit entry has a matching `admin_bypass` event (§5.6). A bypass with no matching record on either side is a FAIL. |
| LOG-006 | Hash-chain verification over the covered tables (§6.2) — the rule that implements LOG-005's check in v4.0.0. (LOG-005 itself keeps its id and meaning; LOG-006 is the chain-verification rule that supplies its evidence.) |

The exact rule list and ids are confirmed in the 4D build review;
the table above is the proposal. The ruleset version becomes
`4.0.0` (`AUDIT_RULESET_VERSION`, `app/config.py`).

**Per-rule specification requirements (LTP-048 §3).** Each of
the nine new rules must have, before it is implemented:

- A unique identifier (the ids above are the proposal).
- An explicit objective, stated in one sentence: what violation
  the rule detects, not what table it reads.
- Verifiable evidence: the exact rows, columns and values the
  rule reads, and the exact condition that makes it PASS, WARN
  or FAIL. "The field exists" is never evidence.
- Defined behaviour for missing data: a rule whose evidence is
  absent returns UNVERIFIED with the reason stated — it never
  defaults to PASS (nothing to check is not the same as nothing
  wrong) and never defaults to FAIL (absence of evidence is not
  evidence of a violation).
- Three tests: positive (a fixture that must PASS), negative (a
  fixture that must FAIL), and inconclusive (a fixture with the
  evidence missing that must return UNVERIFIED) — §9.4.

**Rule count is not a security metric.** Going from 38 rules to
47 does not make the system more secure by itself — it makes
more claims, each of which must be true. A rule that cannot be
backed by verifiable evidence must not be added to inflate the
count, and a rule that passes on an empty database proves
nothing about a populated one. The v4.0.0 clean-DB expectation
(§7.3) is set by the 4D build review after the rules exist, not
before — pre-declaring the numbers would be deciding the audit's
outcome before the checks exist.

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

**The three migration/rollback guarantees (LTP-048 §4).**

1. **Validated backup before each migration.** "Backup taken"
   is not enough: the backup is validated before the migration
   proceeds — the file's SHA-256 is recorded, and the backup is
   opened read-only and checked (`PRAGMA integrity_check` and a
   row-count spot check against the live database). A backup
   that cannot be opened or fails the integrity check blocks the
   migration. The validation result is recorded in the migration
   log alongside the hash.
2. **Repeatable migration, no duplication.** Every migration is
   guarded by a migration record in the `meta` table
   (`migration_<stage>` with the stage id and the migration
   hash). Re-running a completed migration is a no-op: it
   detects its own record and stops. Entries and checkpoints are
   created only if absent, keyed by their `UNIQUE` constraints
   (idempotency keys, `(operation_id, entry_type)`, the
   checkpoint hash) — a re-run cannot duplicate a ledger entry,
   an opening-balance genesis row, or a checkpoint.
3. **Rollback must not make the system believe later economic
   operations never existed.** Restoring a backup returns the
   database to the backup's instant — operations that happened
   after the backup are gone from the file. The system must not
   silently treat that as "those operations never happened":
   a restore is a **controlled administrative recovery, not a
   normal system operation** (§8.5). It requires an explicit
   admin action, writes a `migration_rollback` audit entry (and,
   from 4C, a security event) stating the backup's instant, the
   hash, and the discontinuity, and the restored database
   carries a rollback marker in `meta` so the Auditor's
   reconciliation (§2.5.1) can see that history after the marker
   is missing by design, not by tampering. Restoring an old
   backup can also delete legitimate security events recorded
   after it (§8.3) — that loss is part of the discontinuity and
   must be stated in the rollback record, not discovered later.

### 8.1 Stage 4A — ledger

| Step | Action |
|---|---|
| Backup | Copy `data.db` (or the configured DB path) to `data.db.pre-4a-<timestamp>`; validate it (§8, guarantee 1: `PRAGMA integrity_check` + row counts) and record its SHA-256 in an audit entry. |
| Schema | Create `ledger_operations` and `ledger_entries` [PROPOSED] (§2.2) with their indexes and the partial unique index; add `CHECK (credits_balance >= 0)` to `users` — SQLite cannot add a `CHECK` to an existing table with plain `ALTER TABLE`, so this is a table rebuild (create new table, copy rows, drop old, rename) inside the same migration transaction; enable WAL (`PRAGMA journal_mode=WAL`) and set a `busy_timeout` [PROPOSED]. |
| Backfill | One `opening_balance` genesis operation per user, each with exactly one `opening_balance` entry: `amount` = current `credits_balance`, `state = 'finished'`, `reason = 'migration'`. No other backfill: pre-ledger charges stay as free-text audit entries and are NOT converted into ledger operations or entries (converting prose into money movements would fabricate evidence). The reconciliation invariant (§2.5.1) therefore starts at the migration, not at genesis. |
| Code | `ResourceCore.charge` / `refund` delegate to the ledger service [PROPOSED]; existing callers and signatures unchanged. |
| Rollback | Restore the validated backup file (§8, guarantee 3): the ledger tables disappear with it; balances and audit log are exactly as before. The restore is recorded as a controlled administrative recovery (§8.5) with a rollback marker — any ledger operations created after the migration are lost with the restore, and the marker states that loss instead of letting the system pretend they never existed. |

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
| Rollback | Restore the validated backup (§8, guarantee 3). Note: rolling back 4C deletes the `security_events` table — denial history recorded during the rolled-back period is lost. That loss is part of the recorded discontinuity (§8.5): the rollback record states which events were lost (by count and time range, from the pre-rollback scan), so the loss is visible instead of silent. |

### 8.4 Stage 4D — hash chain + Auditor v4

| Step | Action |
|---|---|
| Backup | Same procedure, `data.db.pre-4d-<timestamp>`, validated (§8, guarantee 1). |
| Schema | Create `chain_entries` [PROPOSED] (§6.2) with its indexes and UPDATE/DELETE triggers. No hash columns are added to the covered tables — the chain lives in its own table. |
| Backfill | The checkpoint procedure of §6.3: compute the checkpoint hash over all pre-chain rows, record it in `meta` (`chain_genesis_hash`) and in the external anchor, insert the genesis chain entry (`seq = 1`, `entity_type = 'chain_start'`). Existing rows are NOT rewritten with fresh hashes — the boundary is explicit. |
| Code | Store layer computes hashes at insert and writes the chain entry in the same transaction; `verify_chain` [PROPOSED] implemented; Auditor ruleset v4.0.0 (§7). |
| Rollback | Restore the validated backup. The chain and the checkpoint disappear with it. If a checkpoint hash was already committed to the repo (option A, §6.4), the commit is NOT rewritten — a follow-up commit records that the checkpoint was superseded by the rollback (§8.5). History is appended to, never rewritten, in the repo as in the database. |

### 8.5 What cannot be rolled back — restore is a controlled administrative recovery

- A checkpoint hash already committed to the public repo cannot be
  un-published; it can only be superseded by a later commit (§8.4).
- A restore is not a normal system operation. It is a controlled
  administrative recovery: explicit admin action, a
  `migration_rollback` audit entry stating the backup's instant
  and hash and the discontinuity (which operations and events
  after the backup are gone), and a rollback marker in `meta`
  that survives the restore. The marker tells every later reader
  — including the Auditor — that history between the backup
  instant and the restore instant is missing *by recorded
  decision*, not by tampering: reconciliation (§2.5.1) treats
  the marker as a declared boundary, and a chain verification
  that finds a gap at the marker reports it as a recorded
  discontinuity, not as a broken chain. Without the marker, a
  restore would be indistinguishable from a deletion.
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
  any real use. After real use begins, a restore that rewinds
  economic history would need a compensating procedure of its
  own; that procedure is out of scope for Phase 4 and is recorded
  here as a known gap, not solved silently.

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
| `test_state_machine_all_allowed_transitions` | Every row of the §2.4 table, driven through the ledger service: each allowed transition succeeds, lands in the expected state, and appends exactly the entries §2.4 names — no more, no fewer. |
| `test_state_machine_forbidden_transitions` | Every forbidden transition in §2.4 raises (or is refused) and leaves the operation, the entries and the balance unchanged — including `refunded → charged`, `finished → *`, `failed → charged`, and `failed → refunded` with the wrong terminal entry (a `refund` on an operation with no `debit`, a `release` on one with a `debit`). |
| `test_operation_and_entries_split` | One operation, several entries: a full execution path produces one `ledger_operations` row and three entries (`reserve`, `debit`, and no terminal entry while `finished`); the operation's state is a cache of the entry stream, and the entries alone reconstruct the full history. |
| `test_entries_immutable` | Attempting to UPDATE or DELETE a `ledger_entries` row is refused by the triggers; the entry stream can only grow. |
| `test_short_path_immediate_charges` | Creation and audit charges follow `started → charged → finished` in one transaction, with a `reserve` entry and a `debit` entry written together; balance moves exactly once. |
| `test_bypass_creates_no_posting` | Admin execution creates no `ledger_operations` row and no entry; it creates a `credit_bypass` audit entry (and, from 4C, an `admin_bypass` security event). A ledger that contains a bypass posting is a FAIL. |
| `test_idempotency_same_key_twice` | Same idempotency key submitted twice → one operation row, one debit entry, second call returns the stored result. |
| `test_idempotency_retry_after_refund` | Retry of a refunded operation's key → returns the refund result, no new debit, no new entry. |
| `test_idempotency_key_conflict` | Same key, different amount/actor → rejected as a conflict, no money moved, no entry. |
| `test_concurrent_charges_same_balance` | Two threads (and, in a separate test, two processes) charge the same user concurrently for more than the balance → exactly one succeeds, the other is refused, balance never negative, exactly one debit entry. |
| `test_concurrent_execution_cap_unchanged` | The existing concurrency cap (2 simultaneous executions) still holds under the new transaction path. |
| `test_failure_between_charge_and_record` | Simulated crash between the balance update and the audit entry (fault injection in the ledger service) → transaction rolls back, no partial state; the recovery scan (§3.4.1) reports the mismatch and the retry with the same idempotency key completes the operation exactly once. |
| `test_recovery_finalise_missing` | Operation `reserved`/`charged`, execution `finished` (case B, §3.4.1) → recovery advances to `finished`, appending the missing `debit` entry if absent, no double debit. |
| `test_recovery_refund_refused` | Operation `reserved`/`charged`, execution `refused` (case C) → recovery advances to `failed` → `refunded`, appending a `release` (no debit) or `refund` (debit exists) entry, balance restored. |
| `test_recovery_never_auto_charges` | Execution exists with no ledger operation (case D) → recovery flags an anomaly and creates no charge and no entry. |
| `test_recovery_stuck_reservation_released` | Operation `reserved` with no live execution past the age threshold (case A) → `failed` → `refunded` with a `release` entry, hold released. |
| `test_recovery_idempotent` | Recovery run twice on the same pending operation → the second run writes no new entry (the `(operation_id, entry_type)` unique index and the state re-check, §3.4.2). |
| `test_recovery_crash_midway` | Simulated crash in the middle of a recovery action → re-running recovery completes it with no duplicate entry. |
| `test_independent_balance_reconciliation` | Recompute every user's buckets from `ledger_entries` alone (invariant §2.5.1) and compare the recomputed `spendable` with `users.credits_balance` — including after a mixed history of charges, refunds, releases, bypasses and opening balances. The reconciler never reads the balance as an input. |
| `test_bucket_partition` | The worked example of §2.5.1, asserted entry by entry: after each entry, `spendable + held + spent = OB`, and `refunded` is the cumulative `refund` sum, never a fourth bucket. |
| `test_no_negative_balance_by_race_or_retry` | Property-style test: random interleavings of charges/refunds/retries never produce a negative balance or a duplicated operation or entry. |
| `test_opening_balance_genesis` | Migration creates exactly one `opening_balance` operation per user, each with exactly one `opening_balance` entry, with the pre-migration balance; pre-migration free-text charges are not converted. |
| `test_migration_repeatable` | Running the 4A migration twice creates no duplicate genesis operation or entry (guarded by the `meta` migration record, §8). |
| `test_integer_amounts_only` | Attempting a non-integer amount is rejected; no float ever reaches the ledger (a float amount raises before any write). |

**4A acceptance criteria:** all tests above green; full regression
green; CREDIT-004 and CREDIT-009 demonstrably checkable against
the ledger operations and entries (the Auditor rules exist as v4.0.0
drafts or the v3 rules are shown to pass against ledger-backed data
— the 4A review decides whether the rule updates land in 4A or wait
for 4D, but the *data* that makes them verifiable must exist after
4A); reconciliation invariant (§2.5.1) holds on every test database,
recomputed from entries alone.

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
| `test_origin_never_rewritten` | Attempting to UPDATE a record's `origin` (e.g. `simulated` → `measured`) is refused by the triggers; a reconciliation run leaves every origin untouched; a real measurement of the same execution arrives as a *new* record, and the simulated record stays with its original origin. |
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
| `test_rate_limit_per_key_concurrent` | 20 parallel auth attempts against one key (limit 10/min) → at most 10 succeed, the rest are refused, and exactly the refused attempts (not more) produce `rate_limited` events. The limit is enforced inside the counter's write transaction (§5.4), so parallel callers cannot all pass before any of them increments. |
| `test_rate_limit_per_user_execution` | Execution starts above the per-user hourly limit → refused, event written, no charge, no execution record. |
| `test_rate_limit_per_user_execution_concurrent` | 70 parallel execution starts for one user (limit 60/hour) → at most 60 start, the rest are refused with no charge and no execution record. |
| `test_key_cap_per_user` | Creating a 6th active key for a user (cap 5, §5.4) is refused. |
| `test_revocation_immediate` | Revoke a key, authenticate with it in the next call → fails; the revocation is visible in `security_events` as `key_revoked`. |
| `test_expiry_enforced` | Key past `expires_at` → fails with the generic message and a `key_expired` event; a key before expiry still works. |
| `test_expiry_does_not_lock_out_existing_keys` | Keys created before 4C (`expires_at = NULL`) keep working after the migration. |
| `test_key_usage_counters` | Successful authentications increment `use_count` and update `last_used_at`. |
| `test_admin_bypass_traceable` | An admin execution produces a `credit_bypass` audit entry and an `admin_bypass` security event — both, linked — and no ledger operation or entry. |
| `test_admin_bypass_cannot_disable_event_audit` | An admin execution performed while a bypass is active still writes its `admin_bypass` security event; there is no flag, config, or admin call that suppresses `security_events` writes, and a `credit_bypass` audit entry with no matching `admin_bypass` event is detected by the SEC-009 check (§7.2). |
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
| `test_chain_detects_alteration` | Modify one field of one covered row (via direct SQL, bypassing the triggers) → verification FAILs with that row's id (the recomputed `canonical_hash` differs, §6.2.2 step 4). |
| `test_chain_detects_deletion` | Delete one covered row (via direct SQL) → verification FAILs (the chain entry is orphaned, §6.2.2 step 5). |
| `test_chain_detects_chain_entry_deletion` | Delete one `chain_entries` row (via direct SQL, bypassing the triggers) → verification FAILs (the `seq` gap and the broken `prev_hash` link, §6.2.2 steps 1–2). |
| `test_chain_detects_insertion_middle` | Insert a fabricated covered row in the middle with a recomputed hash for itself → verification FAILs (the following entry's `prev_hash` is stale, or the row has no chain entry). |
| `test_chain_detects_reordering` | Swap two rows' positions / rewrite two `seq` values → verification FAILs (`seq` is part of the payload, §6.2.1). |
| `test_canonical_serialisation_deterministic` | The same logical row serialised twice (and by the writer and by the verifier independently) produces identical bytes and the identical `canonical_hash`; a field-order change in the serialiser is caught by this test. |
| `test_chain_valid_on_clean_db` | A clean database verifies clean, end to end. |
| `test_chain_append_by_attacker_visible` | A well-formed row appended at the end with correct hashes verifies as chain-valid — and the test asserts the *documented* limitation: the chain alone cannot distinguish it from a legitimate row (this is why §6.5 exists). |
| `test_external_anchor_detects_replacement_no_access` | Scenario (a), §6.4: replace the whole DB file with a recomputed one → chain verifies, but the checkpoint hash and final sequence no longer match the published anchor → detection reported. |
| `test_external_anchor_detects_replacement_read_access` | Scenario (b), §6.4: same replacement, attacker can *read* the published anchor but cannot rewrite the remote ref → still detected (knowing the hash does not forge a preimage, and the published anchor cannot be updated to match the fabrication). |
| `test_simultaneous_compromise_not_detectable` | Scenario (c), §6.4: DB replaced *and* the remote ref rewritten with a matching anchor → the test asserts the *documented* limitation: verification passes, and the test exists to prove the limitation is known and stated, not to claim detection. This test must never be written as a PASS for detection. |
| `test_anchor_missing_unverified` | No anchor committed, or the remote cannot be consulted → the LOG-005 finding is UNVERIFIED, never PASS (§6.4, condition 5). |
| `test_anchor_stale_warns` | Anchor present but older than the DB's checkpoint (a newer checkpoint was never published) → WARN with the anchor's identity and timestamp. |
| `test_migration_boundary_explicit` | Pre-chain rows are covered by the checkpoint hash only; the `chain_start` marker exists; verification output states the boundary (hash, row counts, timestamp). |
| `test_migration_does_not_rewrite_history` | Pre-chain rows are byte-identical after migration (no fresh hashes written onto old rows). |
| `test_auditor_v4_seven_items_reevaluated` | One test per UNVERIFIED item (§7.1): each rule's new status with the evidence that justifies it. |
| `test_auditor_v4_no_pass_by_field_existence` | Fixture with the new columns present but empty/inconsistent → the rules do NOT pass (a field's existence is not evidence). |
| `test_auditor_v4_new_rules` | One test per new rule in §7.2 (CREDIT-010…014, KEY-006, SEC-008, SEC-009, LOG-006), each with three fixtures: positive (must PASS), negative (must FAIL), and inconclusive (evidence missing → must return UNVERIFIED, never PASS or FAIL by default). |
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
| 1 | **Idempotency:** a retried request must not double-debit. | §3.1: `UNIQUE` idempotency key, defined retry behaviour, conflict rejection — plus the partial unique index on `(operation_id, entry_type)` (§2.2), which makes a duplicate `debit`/`release`/`refund` entry impossible at the database level. | The key must be generated and stored by the caller correctly; a caller that generates a fresh key per retry defeats the mechanism. Client discipline is outside the DB's control. |
| 2 | **Concurrency:** two executions must not spend the same balance. | §3.2–3.3: single `BEGIN IMMEDIATE` transaction, in-transaction re-check, `CHECK (credits_balance >= 0)`. | Only within one database file. Two files, two service instances, or a copied DB are not protected. |
| 3 | **State machine:** invalid transitions must be impossible. | §2.4: single enforcement point, forbidden-transition table, tests for every forbidden transition — plus the entry stream: a state change without its entries is itself a detectable inconsistency (CREDIT-012 replays the entries). | Enforcement is application-level; direct SQL can still write an illegal state. The Auditor detects it after the fact (CREDIT-012) but cannot prevent it. |
| 4 | **Partial failures:** execution finished, process crashed before the economic record. | §3.4: explicit recovery flows per case (A–E), idempotent recovery, never auto-charge, reconciliation rebuilt from the entries. | The "work done, no ledger operation" case requires human judgment; the scan can flag it but not resolve it. A crash during the recovery itself can leave a row mid-transition — recovery must be idempotent too (same idempotency key, same result; §3.4.2). |
| 5 | **Financial precision:** never float for money. | §2.5: integer credits, integer minor units, float field frozen out. | The legacy `agents.real_api_cost REAL` still exists and could be misread by a future developer as a money value. It is documented as frozen, not removed — removal would destroy history. |
| 6 | **Cost vs credit separation:** API cost, tokens and credits are different quantities. | §4.1: three separate columns, no presumed equivalence, no debit from token data. | A future pricing change could re-introduce an implicit conversion (e.g. "1 credit = N tokens") without updating the rules. The separation is a convention the Auditor checks (CREDIT-014), not a type-system guarantee. |
| 7 | **Abuse of limits:** rate limit does not replace concurrency control, accumulated quotas, or multi-key protection. | §5.4: limits per key and per user, plus the key cap, plus concurrency control (§3) as a separate layer. | A determined user can still spread usage across multiple accounts — there is no identity verification in this system (auth proves key possession, not human identity, `app/auth.py` limitations). Multi-account farming is detectable only by behaviour, not prevented. |
| 8 | **Admin privilege:** the Lucas bypass must be explicit, restricted and auditable, and must not allow deleting economic records or bypassing history integrity. | §5.6: bypass as an admin event (`credit_bypass` audit entry + `admin_bypass` security event, no ledger posting), no delete API, triggers refuse deletes, Auditor rule SEC-009. | Lucas is a single point of trust: whoever holds his key holds the bypass, the kill switch and key management. There is no MFA, no second admin, no separation of duties. Key compromise is total compromise (see §10.2). |

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
- **Rollback rewinding economic history.** A restore returns the
  database to the backup's instant, and the operations after it
  are gone from the file (§8.5). The rollback marker makes the
  discontinuity visible, but it cannot recover the lost
  operations — and after real use begins, a restore that rewinds
  economic history would need a compensating procedure of its
  own, which Phase 4 does not define. In Phase 4 this is
  acceptable (no real users, no real charges); it is recorded
  here as a known gap, not solved silently.
- **The sandbox is still not validated.** Nothing in Phase 4
  changes that. Commercial use remains blocked on the independent
  sandbox-validation track (separate environment, expressly
  authorised — LTP-046 §5), even if the entire economic foundation
  is finished. A reader must not conclude from "Phase 4 done"
  that the system is ready for real users.

---

## Open decisions (for ChatGPT / Lucas)

Decided in LTP-048 and incorporated in rev 2 (kept here as a
record, not as open questions):

1. **External anchor option** (§6.4): **A (repo commit), with
   the five conditions** — published in the remote repo, commit
   carries hash + final sequence + reference instant, remote
   history protected against force push and ref deletion, the
   Auditor verifies the *published* checkpoint, anchor lookup
   failure → UNVERIFIED. C (signed) is the later hardening step;
   B (Lucas out-of-band) is the fallback, not dependent on
   routine manual checks.
2. **PERM-010** (§7.1): stays UNVERIFIED until 4C, when the
   provenance trail exists. The rev 1 "4A (partial)" ceiling is
   withdrawn.
3. **Canonical hash serialisation** (§6.2.1): JSON, sorted keys,
   UTF-8, no whitespace, version tag `af-chain-v1`, `seq` in the
   payload.

Still open:

4. **Key expiry for existing keys** (§8.3): `expires_at = NULL`
   (unchanged) or a default expiry with notice. Not decided here.
5. **Rate-limit values** (§5.4): the proposed defaults (10
   auth/min, 10 creations/hour, 60 executions/hour, 5 active keys)
   are starting points for the 4C build review, not approved
   numbers.
6. **Minor unit for money** (§2.5): the exact integer minor unit
   (e.g. thousandths of a cent) is a 4B decision.
7. **Whether the Auditor rule updates land in 4A or wait for 4D**
   (§9.1): the *data* must exist after 4A; the rule code may wait
   for 4D. The 4A review decides.

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
  8 risks, §4 acceptance additions, §6 ten items), LTP-047
  (architecture rev 1 delivery), LTP-048 (ChatGPT review:
  direction approved with reservations, 10 mandatory rev 2
  items, anchor option A with conditions, PERM-010 UNVERIFIED
  until 4C).

Status: DRAFT rev 2 for ChatGPT review (LTP-050) — implementation NOT authorized
