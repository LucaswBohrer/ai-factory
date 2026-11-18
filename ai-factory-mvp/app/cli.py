"""AI Factory MVP — CLI (Phase 2, step 1: authenticated).

Identity comes from a validated API key, never from a client-supplied
--actor. Every user-acting command requires --api-key (or env
AI_FACTORY_API_KEY). A --actor flag, if given, must match the key's
owner or authentication fails — a forged actor is rejected, not trusted.

Bootstrap (first admin key, local access only):
  python -m app.cli bootstrap
Key management (authenticated admin only, except list-keys):
  python -m app.cli create-key --owner alice --api-key <lucas-key>
  python -m app.cli list-keys --api-key <key>
  python -m app.cli revoke-key --id <public-id> --api-key <lucas-key>

Usage (from ~/workspace/ai-factory-mvp/):
  python -m app.cli --api-key <key> create-agent --owner alice --name helper --kind summarizer --approve
  python -m app.cli --api-key <key> list-agents [--owner alice]
  python -m app.cli --api-key <key> audit-agent --id <agent-id>
  python -m app.cli --api-key <key> credits [--owner alice]
  python -m app.cli --api-key <key> kill-switch on|off
  python -m app.cli --api-key <key> list-audit-log [--limit 20]
  python -m app.cli --api-key <key> execute --id <agent-id> --minutes 2
Global audit (Phase 3; authenticated admin only, free):
  python -m app.cli --api-key <lucas-key> audit-global [--format json|text]
  python -m app.cli --api-key <lucas-key> audit-report --id <report-id>

Default DB: ./data.db (override with AI_FACTORY_DB). Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .agent_auditor import Auditor
from .auditor import AuditService, RateLimitRefused
from .auditor.models import DISCLAIMER
from .auth import Auth, AuthenticationFailed
from .config import ADMIN_USER, AGENT_TEMPLATES
from .control import Control
from .factory import Factory
from .resource_core import ResourceCore
from .store import Store

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data.db")


def _services(db_path: str):
    store = Store(db_path)
    resource = ResourceCore(store)
    control = Control(store, resource)
    factory = Factory(store, control, resource)
    auditor = Auditor(store, control, resource)
    auth = Auth(store)
    return store, resource, control, factory, auditor, auth


def _resolve_actor(auth: Auth, args) -> str:
    """Resolve the actor from the API key. A supplied --actor must
    match the key's owner; a mismatch is a forgery attempt and fails
    authentication (generic message, no detail leaked)."""
    actor = auth.authenticate(getattr(args, "api_key", None))
    claimed = getattr(args, "actor", None)
    if claimed is not None and claimed != actor:
        raise AuthenticationFailed("authentication failed")
    return actor


def _require_owner_or_admin(actor: str, owner: str) -> None:
    if owner != actor and actor != ADMIN_USER:
        raise PermissionError(
            "cannot act on resources owned by another user")


def _print_audit_report_text(report: dict) -> None:
    """Human-readable rendering of a global audit report. Prints
    every finding (the report is a complete record of what was
    checked) and the mandatory disclaimer verbatim."""
    summary = report["summary"]
    counts = summary["counts_by_status"]
    print(f"global audit report {report['report_id']}")
    print(f"status: {report['status'].upper()}  "
          f"ruleset: {report['ruleset_version']}  "
          f"scope: {report['scope']}  "
          f"evaluated_at: {report['evaluated_at']}")
    print(f"rules run: {summary['rules_run']}  "
          f"PASS: {counts['PASS']}  WARN: {counts['WARN']}  "
          f"FAIL: {counts['FAIL']}  UNVERIFIED: {counts['UNVERIFIED']}")
    print(f"snapshot: {summary['snapshot']['users']} users, "
          f"{summary['snapshot']['agents']} agents, "
          f"{summary['snapshot']['executions']} executions, "
          f"{summary['snapshot']['audit_log_rows']} audit-log rows, "
          f"{summary['snapshot']['api_keys']} api keys")
    for f in report["findings"]:
        evidence = ", ".join(f["evidence_ids"]) or "-"
        print(f"[{f['status']}] {f['rule_id']} ({f['severity']}) — "
              f"{f['title']} — evidence: {evidence} — "
              f"{f['recommendation']}")
    print()
    print(DISCLAIMER)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ai-factory-mvp")
    parser.add_argument("--db", default=os.environ.get("AI_FACTORY_DB", DEFAULT_DB))
    parser.add_argument("--api-key", default=os.environ.get("AI_FACTORY_API_KEY"),
                        help="API key (or env AI_FACTORY_API_KEY); required "
                             "for every command except bootstrap")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("bootstrap",
                       help="create the first admin (lucas) API key; "
                            "only when no keys exist yet")
    p = sub.add_parser("create-key",
                       help="create an API key (authenticated admin only)")
    p.add_argument("--owner", required=True)

    p = sub.add_parser("list-keys",
                       help="list API keys (own keys, or all for admin)")

    p = sub.add_parser("revoke-key",
                       help="revoke an API key (authenticated admin only)")
    p.add_argument("--id", required=True, help="public id of the key")

    p = sub.add_parser("create-agent")
    p.add_argument("--owner", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--kind", required=True, choices=sorted(AGENT_TEMPLATES))
    p.add_argument("--description")
    p.add_argument("--tools", help="comma-separated, subset of allowlist")
    p.add_argument("--permissions", help="comma-separated, subset of allowlist")
    p.add_argument("--approve", action="store_true",
                   help="explicit human approval (required)")
    p.add_argument("--actor", default=None,
                   help="deprecated: must match the key owner if given")

    p = sub.add_parser("list-agents")
    p.add_argument("--owner")
    p.add_argument("--actor", default=None,
                   help="deprecated: must match the key owner if given")

    p = sub.add_parser("audit-agent")
    p.add_argument("--id", required=True)
    p.add_argument("--actor", default=None,
                   help="deprecated: must match the key owner if given")

    p = sub.add_parser("credits")
    p.add_argument("--owner", default=None)

    p = sub.add_parser("kill-switch")
    p.add_argument("state", choices=["on", "off"])
    p.add_argument("--actor", default=None,
                   help="deprecated: must match the key owner if given")

    p = sub.add_parser("list-audit-log")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--actor", default=None,
                   help="deprecated: must match the key owner if given")

    p = sub.add_parser("execute")
    p.add_argument("--id", required=True)
    p.add_argument("--actor", default=None,
                   help="deprecated: must match the key owner if given")
    p.add_argument("--minutes", type=int, default=1)

    p = sub.add_parser("audit-global",
                       help="run a global audit (authenticated admin "
                            "only; free, no credits charged)")
    p.add_argument("--format", choices=["text", "json"], default="text",
                   help="text (human-readable, default) or json")

    p = sub.add_parser("audit-report",
                       help="retrieve a stored global audit report "
                            "(authenticated admin only)")
    p.add_argument("--id", required=True, help="report id")

    args = parser.parse_args(argv)
    store, resource, control, factory, auditor, auth = _services(args.db)
    try:
        if args.cmd == "bootstrap":
            result = auth.bootstrap()
            print("API key created for lucas (admin).")
            print("SAVE THIS KEY NOW — it is shown only once and cannot "
                  "be recovered:")
            print(result["key"])
            print(f"public id: {result['public_id']}")
            return 0

        if args.cmd == "audit-global":
            # Phase 3: the raw key is passed straight through to the
            # service, which authenticates it itself and enforces
            # admin. The CLI never resolves an owner string first.
            try:
                report = AuditService(store, auth).run_global_audit(
                    getattr(args, "api_key", None))
            except RateLimitRefused as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if args.format == "json":
                print(json.dumps(report, indent=2, ensure_ascii=False))
            else:
                _print_audit_report_text(report)
            return 0

        actor = _resolve_actor(auth, args)

        if args.cmd == "create-key":
            auth.require_admin(actor)
            result = auth.create_key(args.owner, actor=actor)
            print("API key created for "
                  f"{result['owner']}. SAVE IT NOW — shown only once:")
            print(result["key"])
            print(f"public id: {result['public_id']}")
        elif args.cmd == "list-keys":
            for k in auth.list_keys(actor):
                print(f"{k['public_id']}  owner={k['owner']}  "
                      f"created={k['created_at']}  "
                      f"revoked={'yes' if k['revoked'] else 'no'}")
        elif args.cmd == "revoke-key":
            auth.revoke_key(args.id, actor)
            print(f"key revoked: {args.id}")
        elif args.cmd == "create-agent":
            _require_owner_or_admin(actor, args.owner)
            agent = factory.create_agent(
                owner=args.owner, name=args.name, kind=args.kind,
                description=args.description,
                tools=args.tools.split(",") if args.tools else None,
                permissions=args.permissions.split(",") if args.permissions else None,
                approved_by_human=args.approve)
            print(json.dumps(agent, indent=2, ensure_ascii=False))
        elif args.cmd == "list-agents":
            for a in control.list_agents(args.owner, actor=actor):
                print(f"{a['id']}  {a['name']}  ({a['kind']})"
                      f"  owner={a['owner']}  status={a['status']}"
                      f"  depth={a['depth']}  credits={a['credits_used']}/{a['max_credits']}")
        elif args.cmd == "audit-agent":
            print(json.dumps(auditor.audit_agent(args.id, actor),
                             indent=2, ensure_ascii=False))
        elif args.cmd == "credits":
            owner = args.owner if args.owner is not None else actor
            _require_owner_or_admin(actor, owner)
            bal = resource.balance(owner)
            print("unlimited (admin, bypass logged)" if bal is None
                  else f"{owner}: {bal} credits")
        elif args.cmd == "kill-switch":
            control.set_kill_switch(args.state == "on", actor)
            print(f"kill switch: {args.state}")
        elif args.cmd == "list-audit-log":
            for e in control.list_audit_log(args.limit, actor=actor):
                print(f"#{e['id']} {e['ts']} {e['actor']} {e['action']}"
                      f" {e['target']} — {e['detail']}")
        elif args.cmd == "execute":
            print(json.dumps(control.execute_agent(args.id, actor, args.minutes),
                             indent=2, ensure_ascii=False, default=str))
        elif args.cmd == "audit-report":
            auth.require_admin(actor)
            report = store.get_audit_report(args.id)
            if report is None:
                print(f"error: unknown report: {args.id}", file=sys.stderr)
                return 1
            print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except AuthenticationFailed as exc:
        # Generic message by design: never reveal whether the key id
        # exists, is revoked, or simply mismatched.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI prints the error plainly
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
