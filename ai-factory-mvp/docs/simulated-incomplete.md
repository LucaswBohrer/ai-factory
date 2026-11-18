# Simulated / incomplete — MVP, 2026-11-18

What the first build does NOT really do, for the LTP-020 review.

- **Agent generation:** template-based only. No LLM generates agents.
  The "Factory" assembles a validated config from a template.
- **Execution:** simulated. `execute_agent` records a bounded execution
  and finishes immediately. No real tools run, no LLM is called.
- **Sandbox:** simulated by logical isolation only (allowlist +
  limits). No Docker, subprocess, or OS sandbox.
- **Real API cost:** meter only. `record_real_api_cost` stores a number;
  no provider, no token counting, no billing reconciliation.
- **Payments:** absent by design. Paid plans are config placeholders
  (`PAID_PLANS = {"basic": 500, "pro": 2000}`), no gateway, no charges.
- **Recursion:** absent by design. Depth is always 0 in the MVP;
  the ceiling of 1 is never reached (see `evidence-containment.md`).
- **Multi-tenancy:** partial. Per-user caps, balances, and suspend
  ownership are enforced; cross-user execute/list/audit gaps exist
  (see `known-vulnerabilities.md`).
- **Authentication:** absent. Owner is a string passed on the CLI.
- **Orchestrator:** absent as a component. The hierarchy exists as a
  rule (Lucas > human-approved creation > agents), not as a running
  orchestrator service.
- **Web UI / API server:** absent. CLI + library only.
- **Windows validation:** not done. Tested only on this VM
  (31 unit tests + CLI smoke tests).
