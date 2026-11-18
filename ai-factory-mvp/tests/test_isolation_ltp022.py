"""Isolation tests for LTP-022 (second build).

The 10 cases ChatGPT demanded after the first build, using real calls
(no mocks). Covers the two blocking vulnerabilities:
(A) cross-user execution, (B) listing / audit / logs without isolation.
"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auditor import Auditor
from app.control import Control, KillSwitchOn
from app.factory import Factory
from app.resource_core import InsufficientCredits, ResourceCore
from app.store import Store

EXEC_PERMS = ["read", "execute_limited"]


def make_services():
    store = Store(":memory:")
    resource = ResourceCore(store)
    control = Control(store, resource)
    factory = Factory(store, control, resource)
    auditor = Auditor(store, control, resource)
    return store, resource, control, factory, auditor


def make_executable(factory, owner="alice", name="worker"):
    return factory.create_agent(
        owner=owner, name=name, kind="summarizer",
        permissions=EXEC_PERMS, approved_by_human=True)


def count_executions(store):
    row = store.conn.execute(
        "SELECT COUNT(*) AS n FROM executions").fetchone()
    return int(row["n"])


class TestIsolationLTP022(unittest.TestCase):
    def setUp(self):
        self.store, self.resource, self.control, self.factory, self.auditor = make_services()

    def _alice_agent(self):
        return make_executable(self.factory, owner="alice", name="alice-worker")

    # 1. Alice executes Alice's agent (succeeds).
    def test_01_alice_executes_own_agent(self):
        agent = self._alice_agent()
        result = self.control.execute_agent(agent["id"], "alice", minutes=1)
        self.assertEqual(result["status"], "finished")
        self.assertEqual(count_executions(self.store), 1)
        # 100 - 10 (create) - 1 (execute) = 89
        self.assertEqual(self.resource.balance("alice"), 89)

    # 2. Bob executes Alice's agent (fails, nothing recorded, nothing charged).
    def test_02_bob_cannot_execute_alices_agent(self):
        agent = self._alice_agent()
        alice_before = self.resource.balance("alice")
        with self.assertRaises(PermissionError):
            self.control.execute_agent(agent["id"], "bob", minutes=1)
        self.assertEqual(count_executions(self.store), 0)
        self.assertEqual(self.resource.balance("bob"), 100)
        self.assertEqual(self.resource.balance("alice"), alice_before)
        executions = [e for e in self.store.list_audit_log()
                      if e["action"] == "execute_agent"
                      and e["target"] == agent["id"]]
        self.assertEqual(executions, [])

    # 3. Bob lists Alice's agents (returns []).
    def test_03_bob_cannot_list_alices_agents(self):
        self._alice_agent()
        self.assertEqual(
            self.control.list_agents(owner="alice", actor="bob"), [])
        self.assertEqual(self.control.list_agents(actor="bob"), [])
        self.assertEqual(self.control.list_agents(actor=None), [])
        # Alice still sees her own agent; admin sees everything.
        self.assertEqual(
            len(self.control.list_agents(owner="alice", actor="alice")), 1)
        self.assertEqual(len(self.control.list_agents(actor="lucas")), 1)

    # 4. Bob audits Alice's agent (fails with "unknown agent", no charge).
    def test_04_bob_cannot_audit_alices_agent(self):
        agent = self._alice_agent()
        with self.assertRaises(ValueError) as ctx:
            self.auditor.audit_agent(agent["id"], "bob")
        self.assertIn("unknown agent", str(ctx.exception))
        self.assertEqual(self.resource.balance("bob"), 100)
        logged = [e for e in self.store.list_audit_log()
                  if e["target"] == agent["id"] and e["actor"] == "bob"]
        self.assertEqual(logged, [])
        # Same error for a genuinely nonexistent agent: no existence leak.
        with self.assertRaises(ValueError) as ctx2:
            self.auditor.audit_agent("does-not-exist", "bob")
        self.assertIn("unknown agent", str(ctx2.exception))

    # 5. Bob lists audit logs (sees none of Alice's entries).
    def test_05_bob_cannot_see_alices_log_entries(self):
        agent = self._alice_agent()
        entries = self.control.list_audit_log(actor="bob")
        self.assertEqual(
            [e for e in entries if e["actor"] == "alice"], [])
        self.assertEqual(
            [e for e in entries if e["target"] == agent["id"]], [])
        # Bob with no own activity sees nothing at all.
        self.assertEqual(entries, [])
        # Alice sees her own entries; admin sees all.
        self.assertTrue(
            any(e["actor"] == "alice"
                for e in self.control.list_audit_log(actor="alice")))
        self.assertTrue(
            any(e["actor"] == "alice"
                for e in self.control.list_audit_log(actor="lucas")))

    # 6. Lucas executes an admin operation (succeeds, bypass logged).
    def test_06_lucas_admin_operations(self):
        agent = self._alice_agent()
        make_executable(self.factory, owner="bob", name="bob-worker")
        result = self.control.execute_agent(agent["id"], "lucas", minutes=1)
        self.assertEqual(result["status"], "finished")
        bypass = [e for e in self.store.list_audit_log()
                  if e["action"] == "admin_execute"
                  and e["target"] == agent["id"]]
        self.assertTrue(bypass, "admin execution of another user's agent "
                                "must be logged")
        self.assertIn("alice", bypass[0]["detail"])
        # Lucas lists all agents, both owners.
        listed = self.control.list_agents(actor="lucas")
        self.assertEqual({a["owner"] for a in listed}, {"alice", "bob"})

    # 7. Creation with parent_id of another agent (fails, depth rule).
    def test_07_creation_with_agent_parent_rejected(self):
        parent = self._alice_agent()
        with self.assertRaises(ValueError):
            self.factory.create_agent(
                owner="alice", name="child", kind="summarizer",
                parent_id=parent["id"], approved_by_human=True)
        self.assertEqual(len(self.control.list_agents(actor="lucas")), 1)

    # 8. Kill switch blocks creation and execution.
    def test_08_kill_switch_blocks_creation_and_execution(self):
        agent = self._alice_agent()
        self.control.set_kill_switch(True, "lucas")
        with self.assertRaises(KillSwitchOn):
            self.factory.create_agent(
                owner="alice", name="blocked", kind="summarizer",
                approved_by_human=True)
        with self.assertRaises(KillSwitchOn):
            self.control.execute_agent(agent["id"], "alice", minutes=1)
        self.control.set_kill_switch(False, "lucas")
        self.control.execute_agent(agent["id"], "alice", minutes=1)

    # 9. Insufficient balance blocks creation.
    def test_09_insufficient_balance_blocks_creation(self):
        self.store.ensure_user("carol")
        self.store.set_balance("carol", 5)
        with self.assertRaises(InsufficientCredits):
            self.factory.create_agent(
                owner="carol", name="too-expensive", kind="summarizer",
                approved_by_human=True)
        self.assertEqual(
            self.control.list_agents(owner="carol", actor="carol"), [])

    # 10. Full regression: the whole pre-existing suite still passes.
    def test_10_full_regression_existing_suite(self):
        import test_mvp
        suite = unittest.defaultTestLoader.loadTestsFromModule(test_mvp)
        result = unittest.TextTestRunner(stream=io.StringIO(),
                                         verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful(),
                        f"regression failures: {result.failures} "
                        f"{result.errors}")
        self.assertEqual(result.testsRun, 31)


if __name__ == "__main__":
    unittest.main()
