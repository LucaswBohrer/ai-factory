"""AI Factory MVP — CLI.

Usage (from ~/workspace/ai-factory-mvp/):
  python -m app.cli create-agent --owner alice --name helper --kind summarizer --approve
  python -m app.cli list-agents [--owner alice] [--actor alice]
  python -m app.cli audit-agent --id <agent-id> --actor lucas
  python -m app.cli credits [--owner alice]
  python -m app.cli kill-switch on|off --actor lucas
  python -m app.cli list-audit-log [--limit 20] [--actor alice]
  python -m app.cli execute --id <agent-id> --actor alice --minutes 2

Default DB: ./data.db (override with AI_FACTORY_DB). Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .auditor import Auditor
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
    return store, resource, control, factory, auditor


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ai-factory-mvp")
    parser.add_argument("--db", default=os.environ.get("AI_FACTORY_DB", DEFAULT_DB))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-agent")
    p.add_argument("--owner", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--kind", required=True, choices=sorted(AGENT_TEMPLATES))
    p.add_argument("--description")
    p.add_argument("--tools", help="comma-separated, subset of allowlist")
    p.add_argument("--permissions", help="comma-separated, subset of allowlist")
    p.add_argument("--approve", action="store_true",
                   help="explicit human approval (required)")

    p = sub.add_parser("list-agents")
    p.add_argument("--owner")
    p.add_argument("--actor", default=None,
                   help="requesting user; only own agents are listed "
                        "unless actor is lucas (admin)")

    p = sub.add_parser("audit-agent")
    p.add_argument("--id", required=True)
    p.add_argument("--actor", default=ADMIN_USER)

    p = sub.add_parser("credits")
    p.add_argument("--owner", default=ADMIN_USER)

    p = sub.add_parser("kill-switch")
    p.add_argument("state", choices=["on", "off"])
    p.add_argument("--actor", default=ADMIN_USER)

    p = sub.add_parser("list-audit-log")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--actor", default=None,
                   help="requesting user; only own entries are shown "
                        "unless actor is lucas (admin)")

    p = sub.add_parser("execute")
    p.add_argument("--id", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--minutes", type=int, default=1)

    args = parser.parse_args(argv)
    store, resource, control, factory, auditor = _services(args.db)
    try:
        if args.cmd == "create-agent":
            agent = factory.create_agent(
                owner=args.owner, name=args.name, kind=args.kind,
                description=args.description,
                tools=args.tools.split(",") if args.tools else None,
                permissions=args.permissions.split(",") if args.permissions else None,
                approved_by_human=args.approve)
            print(json.dumps(agent, indent=2, ensure_ascii=False))
        elif args.cmd == "list-agents":
            for a in control.list_agents(args.owner, actor=args.actor):
                print(f"{a['id']}  {a['name']}  ({a['kind']})"
                      f"  owner={a['owner']}  status={a['status']}"
                      f"  depth={a['depth']}  credits={a['credits_used']}/{a['max_credits']}")
        elif args.cmd == "audit-agent":
            print(json.dumps(auditor.audit_agent(args.id, args.actor),
                             indent=2, ensure_ascii=False))
        elif args.cmd == "credits":
            bal = resource.balance(args.owner)
            print("unlimited (admin, bypass logged)" if bal is None
                  else f"{args.owner}: {bal} credits")
        elif args.cmd == "kill-switch":
            control.set_kill_switch(args.state == "on", args.actor)
            print(f"kill switch: {args.state}")
        elif args.cmd == "list-audit-log":
            for e in control.list_audit_log(args.limit, actor=args.actor):
                print(f"#{e['id']} {e['ts']} {e['actor']} {e['action']}"
                      f" {e['target']} — {e['detail']}")
        elif args.cmd == "execute":
            print(json.dumps(control.execute_agent(args.id, args.actor, args.minutes),
                             indent=2, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001 — CLI prints the error plainly
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
