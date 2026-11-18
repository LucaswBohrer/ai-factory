"""Tests for the AI Factory MVP (stdlib unittest, no external deps).

Covers the approved containment limits and the required behaviours:
depth, max 3 active/user, credits + Lucas bypass, kill switch,
auditor flagging risky permissions, append-only audit log, and
Factory rejecting self-modify permissions.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auditor import Auditor
from app.config import COST_AUDIT, COST_CREATE, MAX_ACTIVE_PER_USER
from app.control import Control, KillSwitchOn
from app.factory import Factory
from app.resource_core import InsufficientCredits, ResourceCore
from app.store import Store


def make_services():
    store = Store(":memory:")
    resource = ResourceCore(store)
    control = Control(store, resource)
    factory = Factory(store, control, resource)
    auditor = Auditor(store, control, resource)
    return store, resource, control, factory, auditor


def create(store_factory, owner="alice", name="helper", kind="summarizer", **kw):
    kw.setdefault("approved_by_human", True)
    return store_factory.create_agent(owner=owner, name=name, kind=kind, **kw)


class TestDepth(unittest.TestCase):
    def setUp(self):
        self.store, self.resource, self.control, self.factory, self.auditor = make_services()

    def test_agent_cannot_create_another_agent(self):
        parent = create(self.factory, name="parent")
        with self.assertRaises(ValueError):
            create(self.factory, name="child", parent_id=parent["id"])
        self.assertEqual(len(self.control.list_agents(actor="lucas")), 1)

    def test_unknown_parent_rejected(self):
        with self.assertRaises(ValueError):
            create(self.factory, name="orphan", parent_id="does-not-exist")

    def test_human_created_agent_has_depth_zero(self):
        agent = create(self.factory)
        self.assertEqual(agent["depth"], 0)
        self.assertIsNone(agent["parent_id"])


class TestActiveCap(unittest.TestCase):
    def setUp(self):
        _, _, self.control, self.factory, _ = make_services()

    def test_max_3_active_per_user(self):
        for i in range(MAX_ACTIVE_PER_USER):
            create(self.factory, name=f"a{i}")
        with self.assertRaises(RuntimeError):
            create(self.factory, name="a3")
        self.assertEqual(self.control.count_active("alice"), 3)

    def test_cap_is_per_user(self):
        for i in range(3):
            create(self.factory, name=f"a{i}")
        # Another user still has their own quota.
        create(self.factory, owner="bob", name="b0")

    def test_suspended_agent_frees_a_slot(self):
        agents = [create(self.factory, name=f"a{i}") for i in range(3)]
        self.control.suspend_agent(agents[0]["id"], "alice")
        create(self.factory, name="a3")


class TestCredits(unittest.TestCase):
    def setUp(self):
        self.store, self.resource, self.control, self.factory, _ = make_services()

    def test_creation_deducts_10_credits(self):
        self.assertEqual(self.resource.balance("alice"), 100)
        agent = create(self.factory)
        self.assertEqual(self.resource.balance("alice"), 100 - COST_CREATE)
        self.assertEqual(agent["credits_used"], COST_CREATE)

    def test_insufficient_credits_creates_nothing(self):
        self.store.ensure_user("carol")
        self.store.set_balance("carol", 5)
        with self.assertRaises(InsufficientCredits):
            create(self.factory, owner="carol")
        self.assertEqual(self.control.list_agents("carol", actor="carol"), [])

    def test_lucas_bypass_is_logged_not_charged(self):
        self.assertIsNone(self.resource.balance("lucas"))
        create(self.factory, owner="lucas", name="admin-agent")
        entries = [e for e in self.store.list_audit_log()
                   if e["action"] == "credit_bypass"]
        self.assertTrue(entries, "Lucas bypass must be logged")
        self.assertIn("10", entries[0]["detail"])

    def test_real_api_cost_is_tracked_separately(self):
        agent = create(self.factory)
        before = self.resource.balance("alice")
        self.resource.record_real_api_cost(agent["id"], 2.5)
        after = self.store.get_agent(agent["id"])
        self.assertEqual(after["real_api_cost"], 2.5)
        self.assertEqual(self.resource.balance("alice"), before)


class TestKillSwitch(unittest.TestCase):
    def setUp(self):
        self.store, self.resource, self.control, self.factory, _ = make_services()

    def test_only_lucas_can_toggle(self):
        with self.assertRaises(PermissionError):
            self.control.set_kill_switch(True, "alice")

    def test_kill_switch_blocks_creation(self):
        self.control.set_kill_switch(True, "lucas")
        self.assertTrue(self.control.is_kill_switch_on())
        with self.assertRaises(KillSwitchOn):
            create(self.factory)
        self.control.set_kill_switch(False, "lucas")
        create(self.factory)  # works again

    def test_kill_switch_blocks_execution(self):
        agent = create(
            self.factory, permissions=["read", "execute_limited"])
        self.control.set_kill_switch(True, "lucas")
        with self.assertRaises(KillSwitchOn):
            self.control.execute_agent(agent["id"], "alice", minutes=1)


class TestFactoryValidation(unittest.TestCase):
    def setUp(self):
        self.store, _, self.control, self.factory, _ = make_services()

    def test_rejects_self_modify_permission(self):
        with self.assertRaises(ValueError):
            create(self.factory, permissions=["read", "self_modify_permissions"])
        self.assertEqual(self.control.list_agents(actor="lucas"), [])

    def test_rejects_admin_and_create_agent_permissions(self):
        for perms in (["admin"], ["create_agent"], ["self_modify"]):
            with self.assertRaises(ValueError, msg=str(perms)):
                create(self.factory, permissions=perms)

    def test_rejects_forbidden_tools(self):
        with self.assertRaises(ValueError):
            create(self.factory, tools=["read_config", "write"])

    def test_requires_human_approval(self):
        with self.assertRaises(PermissionError):
            self.factory.create_agent(owner="alice", name="x",
                                      kind="summarizer",
                                      approved_by_human=False)

    def test_unknown_template_rejected(self):
        with self.assertRaises(ValueError):
            create(self.factory, kind="superintelligence")

    def test_bulk_creation_rejected(self):
        with self.assertRaises(ValueError):
            self.factory.create_many([
                {"owner": "alice", "name": "a", "kind": "summarizer",
                 "approved_by_human": True},
                {"owner": "alice", "name": "b", "kind": "summarizer",
                 "approved_by_human": True},
            ])


class TestAuditor(unittest.TestCase):
    def setUp(self):
        self.store, self.resource, self.control, self.factory, self.auditor = make_services()

    def _risky_agent(self):
        # Bypass the Factory (which would never create this) via the
        # low-level store insert, to test the Auditor against bad data.
        return self.store.insert_agent({
            "name": "risky", "owner": "alice", "kind": "summarizer",
            "description": "injected", "tools": ["write", "read_config"],
            "permissions": ["admin", "self_modify_permissions", "create_agent"],
            "parent_id": None, "depth": 0, "status": "active",
            "credits_used": 0, "max_credits": 50, "real_api_cost": 0.0,
        })

    def test_auditor_flags_risky_permissions(self):
        agent = self._risky_agent()
        report = self.auditor.audit_agent(agent["id"], "lucas")
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("forbidden_permission", codes)
        self.assertIn("self_modify_risk", codes)
        self.assertIn("recursion_risk", codes)
        self.assertIn("forbidden_tool", codes)
        self.assertGreater(report["counts"]["critical"], 0)

    def test_auditor_is_read_only(self):
        agent = create(self.factory)
        before = self.store.get_agent(agent["id"])
        report = self.auditor.audit_agent(agent["id"], "lucas")
        after = self.store.get_agent(agent["id"])
        self.assertEqual(before, after)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["counts"]["critical"], 0)

    def test_auditor_flags_credit_limit_exceeded(self):
        agent = self.store.insert_agent({
            "name": "spender", "owner": "alice", "kind": "summarizer",
            "description": "", "tools": ["read_config"],
            "permissions": ["read"], "parent_id": None, "depth": 0,
            "status": "active", "credits_used": 99, "max_credits": 50,
            "real_api_cost": 0.0,
        })
        report = self.auditor.audit_agent(agent["id"], "lucas")
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("credit_limit_exceeded", codes)

    def test_audit_costs_one_credit(self):
        agent = create(self.factory)  # alice: 100 -> 90
        self.auditor.audit_agent(agent["id"], "alice")
        self.assertEqual(self.resource.balance("alice"), 100 - COST_CREATE - COST_AUDIT)


class TestAuditLogAppendOnly(unittest.TestCase):
    def setUp(self):
        self.store, _, self.control, self.factory, _ = make_services()

    def test_no_update_or_delete_api(self):
        self.assertFalse(hasattr(self.store, "update_audit"))
        self.assertFalse(hasattr(self.store, "delete_audit"))

    def test_update_is_blocked_by_trigger(self):
        self.store.append_audit("lucas", "test_action", "x", "hello")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute(
                "UPDATE audit_log SET detail='tampered' WHERE id=1")
        row = self.store.conn.execute(
            "SELECT detail FROM audit_log WHERE id=1").fetchone()
        self.assertEqual(row["detail"], "hello")

    def test_delete_is_blocked_by_trigger(self):
        self.store.append_audit("lucas", "test_action", "x", "hello")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute("DELETE FROM audit_log WHERE id=1")
        self.assertEqual(len(self.store.list_audit_log()), 1)

    def test_log_records_creations(self):
        create(self.factory)
        actions = [e["action"] for e in self.store.list_audit_log()]
        self.assertIn("create_agent", actions)


class TestExecution(unittest.TestCase):
    def setUp(self):
        _, self.resource, self.control, self.factory, _ = make_services()

    def test_execution_charges_per_minute(self):
        agent = create(self.factory, permissions=["read", "execute_limited"])
        self.control.execute_agent(agent["id"], "alice", minutes=2)
        # 100 - 10 (create) - 2 (execute) = 88
        self.assertEqual(self.resource.balance("alice"), 88)

    def test_execution_minutes_capped_at_5(self):
        agent = create(self.factory, permissions=["read", "execute_limited"])
        with self.assertRaises(ValueError):
            self.control.execute_agent(agent["id"], "alice", minutes=6)

    def test_auditor_agent_cannot_execute(self):
        agent = create(self.factory, kind="auditor")
        with self.assertRaises(PermissionError):
            self.control.execute_agent(agent["id"], "lucas", minutes=1)

    def test_concurrent_execution_cap(self):
        agent = create(self.factory, permissions=["read", "execute_limited"])
        # Simulate 2 already-running executions.
        self.control.store.start_execution(agent["id"], "alice", 1)
        self.control.store.start_execution(agent["id"], "alice", 1)
        with self.assertRaises(RuntimeError):
            self.control.execute_agent(agent["id"], "alice", minutes=1)


if __name__ == "__main__":
    unittest.main()
