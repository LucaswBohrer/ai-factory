"""AI Factory MVP — Resource Core (credit accounting).

Internal credits only. No money, no payment gateway, no real charges.
Real API consumption is tracked in a separate field (real_api_cost on
the agent), independent of internal credits, per LTP-017.
"""
from __future__ import annotations

from . import config
from .store import Store


class InsufficientCredits(Exception):
    pass


class ResourceCore:
    def __init__(self, store: Store):
        self.store = store

    def is_admin(self, owner: str) -> bool:
        return owner == config.ADMIN_USER

    def balance(self, owner: str) -> int | None:
        """Admin balance is None = unlimited. Others get an int."""
        if self.is_admin(owner):
            return None
        user = self.store.ensure_user(owner)
        return int(user["credits_balance"])

    def can_afford(self, owner: str, amount: int) -> bool:
        if self.is_admin(owner):
            return True
        return self.balance(owner) >= amount

    def charge(self, owner: str, amount: int, reason: str,
               agent_id: str | None = None) -> bool:
        """Charge internal credits. Returns True on success, False if
        the user cannot afford it. Admin is never charged: the bypass
        is logged in the audit log instead (every bypass is logged)."""
        if amount < 0:
            raise ValueError("charge amount must be >= 0")
        if self.is_admin(owner):
            self.store.append_audit(
                owner, "credit_bypass",
                agent_id or "",
                f"admin bypass: {amount} credits not charged ({reason})")
            if agent_id:
                self.store.add_agent_credits_used(agent_id, amount)
            return True
        user = self.store.ensure_user(owner)
        if int(user["credits_balance"]) < amount:
            return False
        self.store.set_balance(owner, int(user["credits_balance"]) - amount)
        if agent_id:
            self.store.add_agent_credits_used(agent_id, amount)
        self.store.append_audit(
            owner, "credit_charge", agent_id or "",
            f"charged {amount} credits ({reason});"
            f" balance now {int(user['credits_balance']) - amount}")
        return True

    def refund(self, owner: str, amount: int, reason: str,
               agent_id: str | None = None) -> None:
        """Credit back a previous charge (e.g. an execution refused
        by the isolation gate after charging). Admin was never
        charged, so a refund for the admin is only an audit entry."""
        if amount < 0:
            raise ValueError("refund amount must be >= 0")
        if self.is_admin(owner):
            self.store.append_audit(
                owner, "credit_bypass",
                agent_id or "",
                f"admin bypass: refund of {amount} credits is a no-op "
                f"({reason})")
            if agent_id:
                self.store.add_agent_credits_used(agent_id, -amount)
            return
        user = self.store.ensure_user(owner)
        self.store.set_balance(owner, int(user["credits_balance"]) + amount)
        if agent_id:
            self.store.add_agent_credits_used(agent_id, -amount)
        self.store.append_audit(
            owner, "credit_refund", agent_id or "",
            f"refunded {amount} credits ({reason});"
            f" balance now {int(user['credits_balance']) + amount}")

    def check_agent_credit_limit(self, agent: dict, additional: int) -> bool:
        return int(agent["credits_used"]) + additional <= int(agent["max_credits"])

    def record_real_api_cost(self, agent_id: str, amount: float) -> None:
        """Separate from internal credits. No money moves here; this is
        only a meter for later reconciliation."""
        if amount < 0:
            raise ValueError("real API cost must be >= 0")
        self.store.add_real_api_cost(agent_id, amount)
        self.store.append_audit(
            "system", "real_api_cost", agent_id,
            f"recorded real API consumption: {amount}")
