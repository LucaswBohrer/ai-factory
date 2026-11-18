"""AI Factory MVP — Agent Auditor (READ-ONLY).

Analyses an agent's config, permissions, risks, execution cycles and
consumption. It never modifies the audited agent: it only reads the
agent row, the audit log and the executions table. The audit action
itself is logged (and charged 1 credit) — that is a log entry about
the audit, not a modification of the agent.

Findings carry severity: info / warning / critical.
"""
from __future__ import annotations

from . import config
from .control import Control
from .resource_core import InsufficientCredits, ResourceCore
from .store import Store


def _finding(severity: str, code: str, message: str) -> dict:
    assert severity in ("info", "warning", "critical")
    return {"severity": severity, "code": code, "message": message}


class Auditor:
    def __init__(self, store: Store, control: Control, resource: ResourceCore):
        self.store = store
        self.control = control
        self.resource = resource

    def audit_agent(self, agent_id: str, actor: str) -> dict:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"unknown agent: {agent_id}")
        # Snapshot BEFORE, to prove read-only behaviour in tests.
        before = dict(agent)
        # Audit costs 1 credit (admin bypass is logged, not charged).
        if not self.resource.charge(actor, config.COST_AUDIT,
                                    f"audit agent {agent_id}"):
            raise InsufficientCredits(
                f"{actor} cannot afford {config.COST_AUDIT} credit to audit")
        findings: list[dict] = []

        # --- permissions ---
        perms = set(agent["permissions"])
        forbidden = perms & set(config.FORBIDDEN_PERMISSIONS)
        if forbidden:
            findings.append(_finding(
                "critical", "forbidden_permission",
                f"agent has forbidden permission(s): {sorted(forbidden)}"))
        unknown_perms = perms - set(config.ALLOWED_PERMISSIONS) - forbidden
        if unknown_perms:
            findings.append(_finding(
                "critical", "unknown_permission",
                f"agent has permission(s) outside the allowlist: {sorted(unknown_perms)}"))
        if "admin" in perms or "self_modify" in perms or \
                "self_modify_permissions" in perms:
            findings.append(_finding(
                "critical", "self_modify_risk",
                "agent can modify its own permissions — must never happen"))
        if "create_agent" in perms:
            findings.append(_finding(
                "critical", "recursion_risk",
                "agent can create other agents — recursion is disabled in MVP"))

        # --- tools ---
        tools = set(agent["tools"])
        bad_tools = tools - set(config.ALLOWED_TOOLS)
        if bad_tools:
            findings.append(_finding(
                "critical", "forbidden_tool",
                f"agent has tool(s) outside the allowlist: {sorted(bad_tools)}"))

        # --- depth / parent ---
        if int(agent["depth"]) > config.MAX_DEPTH:
            findings.append(_finding(
                "critical", "depth_exceeded",
                f"depth {agent['depth']} exceeds MAX_DEPTH={config.MAX_DEPTH}"))
        if agent.get("parent_id"):
            findings.append(_finding(
                "warning", "has_parent",
                f"agent has parent_id {agent['parent_id']!r};"
                " in MVP agents are created by humans via the Factory only"))

        # --- credits / consumption ---
        used, limit = int(agent["credits_used"]), int(agent["max_credits"])
        if used > limit:
            findings.append(_finding(
                "critical", "credit_limit_exceeded",
                f"credits_used {used} exceeds max_credits {limit}"))
        elif limit and used / limit >= 0.8:
            findings.append(_finding(
                "warning", "credit_limit_near",
                f"credits_used {used}/{limit} (>=80% of limit)"))
        if float(agent["real_api_cost"]) > 0:
            findings.append(_finding(
                "info", "real_api_cost",
                f"real API consumption recorded: {agent['real_api_cost']}"
                " (tracked separately from internal credits)"))

        # --- status ---
        if agent["status"] == "killed":
            findings.append(_finding("info", "agent_killed", "agent is killed"))
        elif agent["status"] == "suspended":
            findings.append(_finding("warning", "agent_suspended",
                                     "agent is suspended"))

        # --- execution cycles (from audit log + executions table) ---
        log = self.store.list_audit_log(limit=1000)
        agent_events = [e for e in log if e["target"] == agent_id]
        executions = [e for e in agent_events if e["action"] == "execute_agent"]
        if len(executions) > 10:
            findings.append(_finding(
                "warning", "many_executions",
                f"{len(executions)} executions recorded for this agent"))
        if not findings:
            findings.append(_finding("info", "clean",
                                     "no risks found: config within allowlists,"
                                     " depth and credits within limits"))

        # Read-only proof: the agent row must be unchanged.
        after = self.store.get_agent(agent_id)
        assert after == before, "auditor modified the agent (bug)"

        self.control.append_audit(
            actor, "audit_agent", agent_id,
            f"audit complete: {len(findings)} finding(s),"
            f" {sum(1 for f in findings if f['severity'] == 'critical')} critical")
        counts = {s: sum(1 for f in findings if f["severity"] == s)
                  for s in ("info", "warning", "critical")}
        return {
            "agent_id": agent_id,
            "agent_name": agent["name"],
            "owner": agent["owner"],
            "kind": agent["kind"],
            "depth": agent["depth"],
            "status": agent["status"],
            "credits_used": agent["credits_used"],
            "max_credits": agent["max_credits"],
            "real_api_cost": agent["real_api_cost"],
            "executions_recorded": len(executions),
            "findings": findings,
            "counts": counts,
            "read_only": True,
        }
