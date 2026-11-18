"""AI Factory MVP — Factory (template-based agent creation only).

No LLM generation in MVP. An agent is built from a template in
config.AGENT_TEMPLATES, optionally narrowed by the caller. Creation
requires explicit human approval (approved_by_human=True) and enforces
every containment limit.
"""
from __future__ import annotations

from . import config
from .control import Control, KillSwitchOn
from .resource_core import InsufficientCredits, ResourceCore
from .store import Store


class Factory:
    def __init__(self, store: Store, control: Control, resource: ResourceCore):
        self.store = store
        self.control = control
        self.resource = resource

    @staticmethod
    def validate_definition(tools, permissions) -> None:
        """Reject any definition outside the allowlists, in particular
        any permission that would let an agent modify itself or create
        other agents."""
        tools = list(tools or [])
        permissions = list(permissions or [])
        bad_tools = set(tools) - set(config.ALLOWED_TOOLS) | \
            set(tools) & set(config.FORBIDDEN_TOOLS)
        if bad_tools:
            raise ValueError(f"forbidden tools: {sorted(bad_tools)}")
        bad_perms = set(permissions) - set(config.ALLOWED_PERMISSIONS) | \
            set(permissions) & set(config.FORBIDDEN_PERMISSIONS)
        if bad_perms:
            raise ValueError(f"forbidden permissions: {sorted(bad_perms)}")

    def create_agent(self, owner: str, name: str, kind: str,
                     description: str | None = None,
                     tools=None, permissions=None,
                     parent_id: str | None = None,
                     approved_by_human: bool = False,
                     max_credits: int | None = None) -> dict:
        # 1. Kill switch blocks all creation.
        self.control.require_kill_switch_off()
        # 2. Explicit human approval is mandatory for every new agent.
        if not approved_by_human:
            raise PermissionError(
                "new agents require explicit human approval (approved_by_human=True)")
        # 3. Template-based only: kind must be a known template.
        if kind not in config.AGENT_TEMPLATES:
            raise ValueError(
                f"unknown template kind: {kind!r}."
                f" Known: {sorted(config.AGENT_TEMPLATES)}")
        template = config.AGENT_TEMPLATES[kind]
        if not name or not str(name).strip():
            raise ValueError("agent name is required")
        # 4. Depth: MVP allows NO agent-created agents. A parent must be
        # human / the Factory itself (parent_id None, 'human' or 'factory').
        # Anything else is an agent parent and is rejected. The hard
        # ceiling is MAX_DEPTH=1; it is never reached in MVP because
        # agent-parent creation is disabled entirely (stricter than the cap).
        if parent_id not in (None, "human", "factory", config.ADMIN_USER):
            parent = self.store.get_agent(parent_id)
            if parent is not None:
                raise ValueError(
                    "depth limit: an agent cannot create another agent"
                    f" (parent {parent_id} is an agent; MAX_DEPTH={config.MAX_DEPTH})")
            raise ValueError(f"unknown parent_id: {parent_id}")
        depth = 0
        # 5. Per-user active cap.
        if self.store.count_active(owner) >= config.MAX_ACTIVE_PER_USER:
            raise RuntimeError(
                f"active agent limit reached for {owner}"
                f" ({config.MAX_ACTIVE_PER_USER})")
        # 6. Tools/permissions: template defaults, optionally narrowed.
        final_tools = list(tools) if tools is not None else list(template["tools"])
        final_perms = list(permissions) if permissions is not None \
            else list(template["permissions"])
        self.validate_definition(final_tools, final_perms)
        # 7. Credits: creation costs COST_CREATE. Admin bypass is logged
        # by ResourceCore; insufficient balance aborts with no agent created.
        if not self.resource.charge(owner, config.COST_CREATE,
                                    f"create agent {name!r}"):
            raise InsufficientCredits(
                f"{owner} cannot afford {config.COST_CREATE} credits"
                " to create an agent")
        agent = self.store.insert_agent({
            "name": str(name).strip(),
            "owner": owner,
            "kind": kind,
            "description": description if description is not None
                           else template["description"],
            "tools": final_tools,
            "permissions": final_perms,
            "parent_id": None,
            "depth": depth,
            "status": "active",
            "credits_used": config.COST_CREATE if owner != config.ADMIN_USER else 0,
            "max_credits": max_credits if max_credits is not None
                           else template["max_credits"],
            "real_api_cost": 0.0,
        })
        # Note: ResourceCore.charge above ran with agent_id=None (the agent
        # did not exist yet), so it only deducted the user balance. The
        # creation cost is recorded on the agent row here, once.
        # (Admin: 0 charged, bypass logged by ResourceCore.)
        self.control.append_audit(
            owner, "create_agent", agent["id"],
            f"created {kind} agent {name!r} (depth {depth}, approved by human)")
        return agent

    def create_many(self, specs: list[dict]) -> list[dict]:
        """MAX_CREATIONS_PER_REQUEST = 1: bulk creation is rejected."""
        if len(specs) != 1:
            raise ValueError(
                f"only {config.MAX_CREATIONS_PER_REQUEST} creation per"
                f" request allowed, got {len(specs)}")
        return [self.create_agent(**specs[0])]
