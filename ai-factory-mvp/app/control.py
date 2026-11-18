"""AI Factory MVP — Control (registry, permissions, audit, kill switch).

Kill switch: only Lucas (admin) can toggle it. When ON it blocks all
new creations and executions. It is checked by Factory and by execute().
"""
from __future__ import annotations

from . import config
from .resource_core import ResourceCore
from .store import Store

KILL_SWITCH_KEY = "kill_switch"  # meta key; value "on" / "off"


class KillSwitchOn(Exception):
    pass


class Control:
    def __init__(self, store: Store, resource: ResourceCore):
        self.store = store
        self.resource = resource

    # --- audit (append-only; see Store) ---
    def append_audit(self, actor: str, action: str, target: str = "",
                     detail: str = "") -> int:
        return self.store.append_audit(actor, action, target, detail)

    def list_audit_log(self, limit: int = 100) -> list[dict]:
        return self.store.list_audit_log(limit)

    # --- kill switch ---
    def is_kill_switch_on(self) -> bool:
        return self.store.get_meta(KILL_SWITCH_KEY, "off") == "on"

    def set_kill_switch(self, on: bool, actor: str) -> None:
        if actor != config.ADMIN_USER:
            raise PermissionError("only Lucas (admin) can toggle the kill switch")
        self.store.set_meta(KILL_SWITCH_KEY, "on" if on else "off")
        self.append_audit(actor, "kill_switch_on" if on else "kill_switch_off",
                          "", f"kill switch set to {'on' if on else 'off'} by {actor}")

    def require_kill_switch_off(self) -> None:
        if self.is_kill_switch_on():
            raise KillSwitchOn("kill switch is ON: creations and executions are blocked")

    # --- registry ---
    def get_agent(self, agent_id: str) -> dict | None:
        return self.store.get_agent(agent_id)

    def list_agents(self, owner: str | None = None) -> list[dict]:
        return self.store.list_agents(owner)

    def count_active(self, owner: str) -> int:
        return self.store.count_active(owner)

    def suspend_agent(self, agent_id: str, actor: str) -> None:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"unknown agent: {agent_id}")
        if actor != config.ADMIN_USER and actor != agent["owner"]:
            raise PermissionError("only the owner or Lucas can suspend an agent")
        self.store.update_agent_status(agent_id, "suspended")
        self.append_audit(actor, "suspend_agent", agent_id, "agent suspended")

    def kill_agent(self, agent_id: str, actor: str) -> None:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"unknown agent: {agent_id}")
        if actor != config.ADMIN_USER and actor != agent["owner"]:
            raise PermissionError("only the owner or Lucas can kill an agent")
        self.store.update_agent_status(agent_id, "killed")
        self.append_audit(actor, "kill_agent", agent_id, "agent killed")

    # --- execution (simulated in MVP: no real tool calls, no LLM) ---
    def execute_agent(self, agent_id: str, actor: str, minutes: int = 1) -> dict:
        """Simulate a bounded execution. Enforces kill switch, active
        status, concurrent-execution cap, per-minute credit cost and the
        5-minute cap. Returns the execution record."""
        self.require_kill_switch_off()
        if minutes < 1 or minutes > config.MAX_EXECUTION_MINUTES:
            raise ValueError(
                f"execution minutes must be 1..{config.MAX_EXECUTION_MINUTES}")
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"unknown agent: {agent_id}")
        if agent["status"] != "active":
            raise ValueError(f"agent is {agent['status']}, not active")
        if "execute_limited" not in agent["permissions"]:
            # Every kind needs execute_limited to run — including the
            # Auditor, which is read-only and must never execute.
            raise PermissionError("agent lacks execute_limited permission")
        if self.store.count_running_executions() >= config.MAX_CONCURRENT_EXECUTIONS:
            raise RuntimeError(
                f"concurrent execution limit reached ({config.MAX_CONCURRENT_EXECUTIONS})")
        cost = minutes * config.COST_EXECUTION_PER_MIN
        if not self.resource.check_agent_credit_limit(agent, cost):
            raise RuntimeError("agent credit limit would be exceeded")
        if not self.resource.charge(actor, cost, f"execution {minutes} min",
                                    agent_id=agent_id):
            from .resource_core import InsufficientCredits
            raise InsufficientCredits(
                f"{actor} cannot afford {cost} credits for execution")
        execution = self.store.start_execution(agent_id, actor, minutes)
        # MVP: execution is simulated and finishes immediately; the
        # running-count check above is what enforces the concurrency cap
        # for overlapping calls. Mark finished at once.
        self.store.end_execution(execution["id"])
        self.append_audit(actor, "execute_agent", agent_id,
                          f"executed {minutes} min, cost {cost} credits (simulated)")
        execution["status"] = "finished"
        return execution
