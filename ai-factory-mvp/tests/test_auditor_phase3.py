"""Global Auditor tests — Phase 3 (LTP-035..LTP-040).

Covers: one test per rule (all 38), CREDIT-005 three scenarios,
KEY-002 temporal tests with a fixed clock, service-layer auth,
isolation/authorization, rate limit (including recovery after
failure), report+event atomicity, UPDATE/DELETE triggers on
audit_reports, CLI output, and the full 88-test regression (run by
the suite runner, not modified here).

All fixtures are synthetic, in-memory, using Store(":memory:") and
direct SQL inserts for rows the Factory would never create. No
fixture touches a real database. Real calls, no mocks — except for
the forced-evaluation-failure test, which patches run_all once.
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.agent_auditor import Auditor as AgentAuditor
from app.auditor import AuditService, RateLimitRefused, run_global_audit
from app.auditor import Auditor as AuditorCompat
from app.auditor.models import DISCLAIMER, RULESET_VERSION
from app.auditor.rules import RULE_IDS, RULES, run_all
from app.auth import Auth, AuthenticationFailed
from app.cli import main as cli_main
from app.config import ADMIN_USER
from app.control import Control
from app.factory import Factory
from app.resource_core import ResourceCore
from app.store import Store

NOW = datetime.now(timezone.utc)  # import-time clock; all fixture
# timestamps are relative to it, so hour/day margins hold no matter
# when the suite runs. Service runs use self.now (captured in setUp,
# seconds later) as the evaluated_at reference.
DAY = timedelta(days=1)

EXPECTED_UNVERIFIED_CLEAN = {
    "CREDIT-004", "CREDIT-009", "LOG-005",
    "SEC-006", "KEY-005", "PERM-010", "SEC-007",
}


def make_services():
    store = Store(":memory:")
    resource = ResourceCore(store)
    control = Control(store, resource)
    factory = Factory(store, control, resource)
    auditor = AgentAuditor(store, control, resource)
    auth = Auth(store)
    return store, resource, control, factory, auditor, auth


def run_cli(argv, db_path, api_key=None):
    """Run the CLI with captured output. Returns (rc, stdout, stderr)."""
    full = ["--db", db_path]
    if api_key is not None:
        full += ["--api-key", api_key]
    full += argv
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli_main(full)
    return rc, out.getvalue(), err.getvalue()


def iso(dt: datetime) -> str:
    return dt.isoformat()


class AuditorTestCase(unittest.TestCase):
    """Base: fresh in-memory services + a bootstrapped lucas key."""

    def setUp(self):
        (self.store, self.resource, self.control, self.factory,
         self.auditor, self.auth) = make_services()
        self.lucas_key = self.auth.bootstrap()["key"]
        self.bob_key = self.auth.create_key("bob", actor="lucas")["key"]
        # Clock captured AFTER bootstrap: every timestamp the fixture
        # layer wrote (real _now()) is <= self.now, so a service run
        # with now=self.now never sees timestamps decrease in id
        # order (LOG-003). Module-level NOW is import time and is
        # only used for fixture timestamps relative to it.
        self.now = datetime.now(timezone.utc)

    # --- helpers ---
    def _service(self, now=None, min_interval=0):
        return AuditService(self.store, self.auth,
                            now=now if now is not None else self.now,
                            min_interval_seconds=min_interval)

    def _run(self, key=None, now=None, min_interval=0):
        return self._service(now=now, min_interval=min_interval) \
            .run_global_audit(key if key is not None else self.lucas_key)

    def _by_id(self, report):
        """Findings by rule_id. Every registered rule has its own
        finding (LTP-042): 38 entries, 38 distinct rule ids."""
        return {f["rule_id"]: f for f in report["findings"]}

    def _insert_agent(self, **kw):
        agent = {
            "name": "helper", "owner": "alice", "kind": "summarizer",
            "tools": ["read"], "permissions": ["read"],
            "status": "active", "credits_used": 0, "max_credits": 50,
        }
        agent.update(kw)
        return self.store.insert_agent(agent)

    def _log(self, actor, action, target="", detail="", ts=None):
        self.store.conn.execute(
            "INSERT INTO audit_log (ts, actor, action, target, detail)"
            " VALUES (?, ?, ?, ?, ?)",
            (iso(ts) if ts else iso(NOW), actor, action, target, detail))
        self.store.conn.commit()

    def _log_with_id(self, log_id, actor, action, target="", detail="",
                     ts=None):
        self.store.conn.execute(
            "INSERT INTO audit_log (id, ts, actor, action, target, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (log_id, iso(ts) if ts else iso(NOW), actor, action, target,
             detail))
        self.store.conn.commit()

    def _exec_row(self, agent_id, actor="alice", minutes=1,
                  status="finished", started=None, ended=None):
        self.store.conn.execute(
            "INSERT INTO executions (id, agent_id, actor, minutes, status,"
            " started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("exec" + agent_id[-4:], agent_id, actor, minutes, status,
             iso(started) if started else iso(NOW - timedelta(minutes=5)),
             iso(ended) if ended else iso(NOW)))
        self.store.conn.commit()

    def _key_row(self, public_id, owner, created_at, revoked=0,
                 revoked_at=None):
        self.store.conn.execute(
            "INSERT INTO api_keys (public_id, owner, key_hash, created_at,"
            " revoked, revoked_at) VALUES (?, ?, ?, ?, ?, ?)",
            (public_id, owner, "00" * 32, iso(created_at), revoked,
             iso(revoked_at) if revoked_at else None))
        self.store.conn.commit()

    def _snapshot(self, **overrides):
        snap = {"users": [], "agents": [], "executions": [], "audit_log": [],
                "api_keys": [], "meta": {}, "evaluated_at": NOW,
                "policy": {"key_max_age_days": 180,
                           "audit_global_min_interval_seconds": 60}}
        snap.update(overrides)
        return snap


class TestCleanAudit(AuditorTestCase):
    def test_clean_db_38_findings(self):
        report = self._run()
        self.assertEqual(len(report["findings"]), 38)
        self.assertEqual(report["summary"]["rules_run"], 38)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["ruleset_version"], RULESET_VERSION)
        self.assertEqual(report["ruleset_version"], "3.0.0")
        self.assertEqual(report["evaluated_at"], iso(self.now))
        self.assertEqual(report["policy"]["key_max_age_days"], 180)
        self.assertEqual(report["summary"]["counts_by_status"],
                         {"PASS": 31, "WARN": 0, "FAIL": 0,
                          "UNVERIFIED": 7})
        unverified = {f["rule_id"] for f in report["findings"]
                      if f["status"] == "UNVERIFIED"}
        self.assertEqual(unverified, EXPECTED_UNVERIFIED_CLEAN)
        # Every registered rule produced exactly one finding, each
        # with its own rule_id and its own finding object (LTP-042):
        # 38 findings, 38 distinct rule ids.
        self.assertEqual(len(report["findings"]), len(RULES))
        self.assertEqual(len({f["rule_id"] for f in report["findings"]}),
                         38)
        self.assertEqual(sorted(f["rule_id"]
                                for f in report["findings"]),
                         sorted(RULE_IDS))
        by_id = self._by_id(report)
        # Registry order: CREDIT-006 is index 15, SEC-004 index 34;
        # the two positions hold DISTINCT finding objects, each with
        # its own rule_id.
        self.assertEqual(report["findings"][15]["rule_id"], "CREDIT-006")
        self.assertEqual(report["findings"][34]["rule_id"], "SEC-004")
        self.assertIsNot(report["findings"][34], report["findings"][15])
        self.assertNotIn("shared_findings", report["summary"])
        self.assertEqual(report["summary"]["disclaimer"], DISCLAIMER)
        self.assertEqual(report["summary"]["snapshot"],
                         {"users": 2, "agents": 0, "executions": 0,
                          "audit_log_rows": 2, "api_keys": 2})

    def test_report_persisted_and_log_event(self):
        report = self._run()
        stored = self.store.get_audit_report(report["report_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], "pass")
        self.assertEqual(stored["findings"], report["findings"])
        newest = self.store.list_audit_log(limit=1)[0]
        self.assertEqual(newest["action"], "audit_global")
        self.assertEqual(newest["target"], report["report_id"])
        self.assertEqual(newest["actor"], "lucas")

    def test_second_run_same_findings(self):
        r1 = self._run(now=self.now)
        r2 = self._run(now=self.now + timedelta(seconds=120))
        self.assertEqual(r1["findings"], r2["findings"])
        self.assertEqual(r1["status"], r2["status"])
        self.assertNotEqual(r1["report_id"], r2["report_id"])
        # The second run's snapshot already contains an audit_global
        # entry: LOG-004 must accept it and SEC-001 must ignore it.
        by_id = self._by_id(r2)
        self.assertEqual(by_id["LOG-004"]["status"], "PASS")
        self.assertEqual(by_id["SEC-001"]["status"], "PASS")

    def test_audit_is_free_no_charge(self):
        before = [e for e in self.store.list_audit_log(limit=1000)
                  if e["action"] in ("credit_charge", "credit_bypass")]
        self._run()
        after = [e for e in self.store.list_audit_log(limit=1000)
                 if e["action"] in ("credit_charge", "credit_bypass")]
        self.assertEqual(before, after)
        self.assertEqual(self.store.get_user("lucas")["credits_balance"],
                         100)


class TestFindingIdentity(AuditorTestCase):
    """LTP-042 blocker: each of the 38 registered rules must have
    its OWN finding — own rule_id, own finding object, own evidence
    context. SEC-004 and CREDIT-006 share one internal observation
    function, never a finding object."""

    def test_exactly_38_findings(self):
        report = self._run()
        self.assertEqual(len(report["findings"]), 38)
        self.assertEqual(report["summary"]["rules_run"], 38)

    def test_exactly_38_distinct_rule_ids(self):
        report = self._run()
        ids = [f["rule_id"] for f in report["findings"]]
        self.assertEqual(len(set(ids)), 38)
        self.assertEqual(sorted(ids), sorted(RULE_IDS))

    def test_exactly_one_finding_per_registered_rule(self):
        report = self._run()
        ids = [f["rule_id"] for f in report["findings"]]
        self.assertEqual(sorted(ids), sorted(RULE_IDS))
        # Registry order is the report order.
        self.assertEqual(ids, RULE_IDS)

    def test_no_finding_object_shared_between_rules(self):
        report = self._run()
        findings = report["findings"]
        self.assertEqual(len({id(f) for f in findings}), 38)
        for i, f in enumerate(findings):
            for j, g in enumerate(findings):
                if i != j:
                    self.assertIsNot(f, g)
        # The former shared pair is now two distinct objects.
        by_id = self._by_id(report)
        self.assertIsNot(by_id["SEC-004"], by_id["CREDIT-006"])
        self.assertEqual(by_id["SEC-004"]["rule_id"], "SEC-004")
        self.assertEqual(by_id["CREDIT-006"]["rule_id"], "CREDIT-006")

    def test_sec_004_and_credit_006_stay_coherent(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        # Admin execution of alice's agent, no credit_bypass entry:
        # both rules read the same observation -> both WARN, same
        # evidence, but distinct finding objects.
        self._exec_row(a["id"], actor="lucas")
        self._log("lucas", "admin_execute", a["id"])
        by_id = self._by_id(self._run())
        sec = by_id["SEC-004"]
        cred = by_id["CREDIT-006"]
        self.assertEqual(sec["status"], "WARN")
        self.assertEqual(cred["status"], "WARN")
        self.assertEqual(sec["severity"], cred["severity"])
        self.assertEqual(sec["evidence_ids"], cred["evidence_ids"])
        self.assertIsNot(sec, cred)
        # Add the bypass entries: the observation clears, both PASS.
        self._log("lucas", "credit_bypass", a["id"], "admin bypass")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["SEC-004"]["status"], "PASS")
        self.assertEqual(by_id["CREDIT-006"]["status"], "PASS")
        self.assertIsNot(by_id["SEC-004"], by_id["CREDIT-006"])


class TestPermRules(AuditorTestCase):
    def test_perm_001_forbidden_permission(self):
        a = self._insert_agent(permissions=["read", "create_agent"])
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-001"]["status"], "FAIL")
        self.assertEqual(by_id["PERM-001"]["severity"], "critical")
        self.assertIn(a["id"], by_id["PERM-001"]["evidence_ids"])
        self.assertEqual(by_id["PERM-003"]["status"], "FAIL")

    def test_perm_002_unknown_permission(self):
        a = self._insert_agent(permissions=["read", "teleport"])
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-002"]["status"], "FAIL")
        self.assertIn(a["id"], by_id["PERM-002"]["evidence_ids"])

    def test_perm_004_forbidden_tool(self):
        a = self._insert_agent(tools=["read", "execute_unlimited"])
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-004"]["status"], "FAIL")
        self.assertIn(a["id"], by_id["PERM-004"]["evidence_ids"])

    def test_perm_005_parent_warns_depth_fails(self):
        a = self._insert_agent(parent_id="someparent")
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-005"]["status"], "WARN")
        b = self._insert_agent(name="deep", depth=2)
        self._log("alice", "create_agent", b["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-005"]["status"], "FAIL")

    def test_perm_006_active_cap(self):
        for i in range(4):
            a = self._insert_agent(name=f"a{i}")
            self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-006"]["status"], "FAIL")
        self.assertIn("alice", by_id["PERM-006"]["evidence_ids"])

    def test_perm_007_invalid_status(self):
        a = self._insert_agent(status="flying")
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-007"]["status"], "FAIL")

    def test_perm_008_is_admin_mismatch(self):
        self.store.conn.execute(
            "INSERT INTO users (owner, plan, credits_balance, is_admin)"
            " VALUES ('mallory', 'free', 100, 1)")
        self.store.conn.commit()
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-008"]["status"], "FAIL")
        self.assertIn("mallory", by_id["PERM-008"]["evidence_ids"])

    def test_perm_009_kill_switch_mismatch(self):
        self.store.set_meta("kill_switch", "on")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-009"]["status"], "WARN")
        self._log("lucas", "kill_switch_on")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["PERM-009"]["status"], "PASS")

    def test_perm_010_execute_limited_unverified(self):
        a = self._insert_agent(permissions=["read", "execute_limited"])
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        f = by_id["PERM-010"]
        self.assertEqual(f["status"], "UNVERIFIED")
        self.assertEqual(f["severity"], "medium")
        self.assertIn(a["id"], f["evidence_ids"])


class TestCreditRules(AuditorTestCase):
    def test_credit_001_exceeds_cap(self):
        a = self._insert_agent(credits_used=60, max_credits=50)
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-001"]["status"], "FAIL")
        self.assertEqual(by_id["CREDIT-001"]["severity"], "high")

    def test_credit_002_boundary(self):
        a = self._insert_agent(credits_used=40, max_credits=50)  # 80%
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-002"]["status"], "WARN")
        b = self._insert_agent(name="low", credits_used=39, max_credits=50)
        self._log("alice", "create_agent", b["id"])
        # 39/50 = 78% for b, but a is still at 80% -> still WARN.
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-002"]["status"], "WARN")
        self.assertEqual(by_id["CREDIT-001"]["status"], "PASS")

    def test_credit_002_79_percent_passes(self):
        a = self._insert_agent(credits_used=79, max_credits=100)
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-002"]["status"], "PASS")

    def test_credit_003_negative_balance(self):
        self.store.ensure_user("alice")
        self.store.set_balance("alice", -5)
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-003"]["status"], "FAIL")

    def test_credit_005_no_candidate_warns(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        # Refund with no refused/failed execution for this agent.
        self._log("alice", "credit_refund", a["id"],
                  "refunded 1 credits (refund: test)", ts=NOW)
        by_id = self._by_id(self._run())
        f = by_id["CREDIT-005"]
        self.assertEqual(f["status"], "WARN")
        self.assertIn("no matching", f["title"].lower())

    def test_credit_005_one_candidate_warns_approximate(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        self._exec_row(a["id"], status="refused",
                       started=NOW - timedelta(hours=2),
                       ended=NOW - timedelta(hours=1))
        self._log("alice", "credit_refund", a["id"],
                  "refunded 1 credits (refund: test)", ts=NOW)
        by_id = self._by_id(self._run())
        f = by_id["CREDIT-005"]
        self.assertEqual(f["status"], "WARN")
        self.assertIn("approximate", f["title"].lower())
        self.assertIn("NOT", f["recommendation"])

    def test_credit_005_several_candidates_unverified(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        self._exec_row(a["id"], status="refused",
                       started=NOW - timedelta(hours=3),
                       ended=NOW - timedelta(hours=2))
        # Second refused execution: direct insert with another id.
        self.store.conn.execute(
            "INSERT INTO executions (id, agent_id, actor, minutes, status,"
            " started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("exec-second", a["id"], "alice", 1, "failed",
             iso(NOW - timedelta(hours=2)), iso(NOW - timedelta(hours=1))))
        self.store.conn.commit()
        self._log("alice", "credit_refund", a["id"],
                  "refunded 1 credits (refund: test)", ts=NOW)
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-005"]["status"], "UNVERIFIED")

    def test_credit_005_agent_scoped(self):
        # Refund for agent A, refused execution for agent B: no match.
        a = self._insert_agent(name="a")
        b = self._insert_agent(name="b")
        self._log("alice", "create_agent", a["id"])
        self._log("alice", "create_agent", b["id"])
        self._exec_row(b["id"], status="refused",
                       started=NOW - timedelta(hours=2),
                       ended=NOW - timedelta(hours=1))
        self._log("alice", "credit_refund", a["id"],
                  "refunded 1 credits (refund: test)", ts=NOW)
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-005"]["status"], "WARN")

    def test_credit_006_missing_bypass_warns(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        # Admin execution of alice's agent, no credit_bypass entry.
        self._exec_row(a["id"], actor="lucas")
        self._log("lucas", "admin_execute", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-006"]["status"], "WARN")
        # Add the bypass entries: both checks now match.
        self._log("lucas", "credit_bypass", a["id"], "admin bypass")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-006"]["status"], "PASS")

    def test_credit_007_stale_running_fails(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        self._exec_row(a["id"], status="running",
                       started=NOW - timedelta(hours=2), ended=None)
        # ended=None writes iso(None)?? guard: rewrite ended_at NULL.
        self.store.conn.execute(
            "UPDATE executions SET ended_at = NULL WHERE status='running'")
        self.store.conn.commit()
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-007"]["status"], "FAIL")

    def test_credit_007_fresh_running_passes(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        self._exec_row(a["id"], status="running",
                       started=NOW - timedelta(minutes=10), ended=None)
        self.store.conn.execute(
            "UPDATE executions SET ended_at = NULL WHERE status='running'")
        self.store.conn.commit()
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-007"]["status"], "PASS")

    def test_credit_008_cost_without_entry_warns(self):
        a = self._insert_agent(real_api_cost=2.5)
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-008"]["status"], "WARN")
        self._log("system", "real_api_cost", a["id"], "recorded")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["CREDIT-008"]["status"], "PASS")


class TestLogRules(AuditorTestCase):
    def test_log_001_gap_warns(self):
        self._log_with_id(10, "lucas", "create_api_key", "aaa")
        self._log_with_id(12, "lucas", "create_api_key", "bbb")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["LOG-001"]["status"], "WARN")

    def test_log_002_duplicate_ids_direct_rule(self):
        from app.auditor.rules.audit_log import log_002
        snap = self._snapshot(audit_log=[
            {"id": 1, "ts": iso(NOW), "actor": "lucas",
             "action": "create_agent", "target": "x", "detail": ""},
            {"id": 1, "ts": iso(NOW), "actor": "lucas",
             "action": "create_agent", "target": "y", "detail": ""},
        ])
        f = log_002(snap)
        self.assertEqual(f["rule_id"], "LOG-002")
        self.assertEqual(f["status"], "FAIL")
        self.assertEqual(f["severity"], "critical")

    def test_log_003_ts_decrease_warns(self):
        self._log_with_id(10, "lucas", "create_api_key", "aaa", ts=NOW)
        self._log_with_id(11, "lucas", "create_api_key", "bbb",
                          ts=NOW - timedelta(hours=1))
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["LOG-003"]["status"], "WARN")

    def test_log_004_unknown_action_warns(self):
        self._log("lucas", "teleport_agent", "x")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["LOG-004"]["status"], "WARN")

    def test_log_006_missing_create_agent_warns(self):
        a = self._insert_agent()  # no create_agent log entry
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["LOG-006"]["status"], "WARN")
        self.assertIn(a["id"], by_id["LOG-006"]["evidence_ids"])
        self._log("alice", "create_agent", a["id"])
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["LOG-006"]["status"], "PASS")


class TestKeyRules(AuditorTestCase):
    def test_key_001_revoked_inconsistent(self):
        self._key_row("aabbccddeeff", "alice", NOW - timedelta(days=1),
                      revoked=1, revoked_at=None)
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["KEY-001"]["status"], "FAIL")

    def test_key_002_179_days_passes_181_warns(self):
        # Fixed clock: age is computed vs evaluated_at = NOW.
        self._key_row("111111111111", "alice", NOW - timedelta(days=179))
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["KEY-002"]["status"], "PASS")
        self._key_row("222222222222", "alice", NOW - timedelta(days=181))
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["KEY-002"]["status"], "WARN")
        self.assertIn("222222222222",
                      by_id["KEY-002"]["evidence_ids"])
        self.assertEqual(by_id["KEY-002"]["severity"], "low")
        # The policy value is recorded on the report.
        report = self._run()
        self.assertEqual(report["policy"]["key_max_age_days"], 180)

    def test_key_002_same_fixture_same_result(self):
        # Determinism: the same DB + same evaluated_at => same finding,
        # regardless of the real date the suite runs on.
        self._key_row("333333333333", "alice", NOW - timedelta(days=200))
        r1 = self._run(now=self.now)
        r2 = self._run(now=self.now)
        self.assertEqual(self._by_id(r1)["KEY-002"],
                         self._by_id(r2)["KEY-002"])

    def test_key_003_zero_admin_keys_direct_rule(self):
        from app.auditor.rules.api_keys import key_003
        f = key_003(self._snapshot(api_keys=[]))
        self.assertEqual(f["status"], "WARN")
        self.assertEqual(f["severity"], "high")

    def test_key_003_two_admin_keys_warns(self):
        self.auth.create_key("lucas", actor="lucas")
        by_id = self._by_id(self._run())
        f = by_id["KEY-003"]
        self.assertEqual(f["status"], "WARN")
        self.assertEqual(f["severity"], "medium")
        self.assertEqual(len(f["evidence_ids"]), 2)

    def test_key_004_duplicate_public_id_direct_rule(self):
        from app.auditor.rules.api_keys import key_004
        snap = self._snapshot(api_keys=[
            {"public_id": "aa", "owner": "alice", "key_hash": "x",
             "created_at": iso(NOW), "revoked": 0, "revoked_at": None},
            {"public_id": "aa", "owner": "bob", "key_hash": "y",
             "created_at": iso(NOW), "revoked": 0, "revoked_at": None},
        ])
        f = key_004(snap)
        self.assertEqual(f["rule_id"], "KEY-004")
        self.assertEqual(f["status"], "FAIL")
        self.assertEqual(f["severity"], "critical")

    def test_key_006_missing_and_malformed_direct_rule(self):
        from app.auditor.rules.api_keys import key_006
        self.assertEqual(key_006(self._snapshot(meta={}))["rule_id"],
                         "KEY-006")
        self.assertEqual(key_006(self._snapshot(meta={}))["status"], "WARN")
        self.assertEqual(
            key_006(self._snapshot(
                meta={"server_secret": "not-hex"}))["status"], "FAIL")
        self.assertEqual(
            key_006(self._snapshot(
                meta={"server_secret": "ab" * 32}))["status"], "PASS")


class TestSecRules(AuditorTestCase):
    def test_sec_001_cross_user_action_fails(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        self._log("bob", "execute_agent", a["id"], "executed 1 min")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["SEC-001"]["status"], "FAIL")
        self.assertEqual(by_id["SEC-001"]["severity"], "critical")

    def test_sec_002_kill_switch_by_non_admin_fails(self):
        self._log("alice", "kill_switch_on")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["SEC-002"]["status"], "FAIL")

    def test_sec_003_key_managed_by_non_admin_fails(self):
        self._log("alice", "create_api_key", "deadbeefcafe")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["SEC-003"]["status"], "FAIL")

    def test_sec_005_unsafe_dev_warns(self):
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        self._log("alice", "execute_isolated", a["id"],
                  "isolated run status=finished unsafe_dev=True cost=1")
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["SEC-005"]["status"], "WARN")
        self.assertEqual(by_id["SEC-005"]["severity"], "low")

    def test_sec_005_zero_runs_passes(self):
        by_id = self._by_id(self._run())
        self.assertEqual(by_id["SEC-005"]["status"], "PASS")


class TestServiceAuth(AuditorTestCase):
    def test_bob_cannot_run_service(self):
        with self.assertRaises(PermissionError):
            self._run(key=self.bob_key)
        self.assertEqual(self.store.list_audit_reports(), [])
        # No log entry for the attempt.
        actions = [e["action"] for e in
                   self.store.list_audit_log(limit=1000)]
        self.assertNotIn("audit_global", actions)
        self.assertNotIn("audit_global_rate_limited", actions)

    def test_no_key_fails(self):
        with self.assertRaises(AuthenticationFailed):
            self._service().run_global_audit(None)
        self.assertEqual(self.store.list_audit_reports(), [])

    def test_forged_key_claiming_lucas_fails(self):
        forged = "afk_" + "00" * 6 + "_" + "00" * 32
        with self.assertRaises(AuthenticationFailed):
            self._run(key=forged)
        self.assertEqual(self.store.list_audit_reports(), [])
        # The service takes no actor parameter at all: there is no
        # code path that accepts a bare owner string.
        import inspect
        params = inspect.signature(run_global_audit).parameters
        self.assertNotIn("actor", params)
        self.assertIn("raw_key", params)

    def test_malformed_key_fails(self):
        with self.assertRaises(AuthenticationFailed):
            self._run(key="not-a-key")
        self.assertEqual(self.store.list_audit_reports(), [])

    def test_revoked_lucas_key_fails(self):
        lucas_id = self.auth.list_keys("lucas")[0]["public_id"]
        self.auth.revoke_key(lucas_id, "lucas")
        with self.assertRaises(AuthenticationFailed):
            self._run()
        self.assertEqual(self.store.list_audit_reports(), [])

    def test_read_only_proof(self):
        def _snap_tables():
            return {
                "users": self.store.conn.execute(
                    "SELECT * FROM users").fetchall(),
                "agents": self.store.conn.execute(
                    "SELECT * FROM agents").fetchall(),
                "executions": self.store.conn.execute(
                    "SELECT * FROM executions").fetchall(),
                "api_keys": self.store.conn.execute(
                    "SELECT * FROM api_keys").fetchall(),
                "meta": self.store.conn.execute(
                    "SELECT * FROM meta").fetchall(),
            }
        a = self._insert_agent()
        self._log("alice", "create_agent", a["id"])
        before = {k: [dict(r) for r in v]
                  for k, v in _snap_tables().items()}
        self._run()
        after = {k: [dict(r) for r in v]
                 for k, v in _snap_tables().items()}
        self.assertEqual(before, after)


class TestRateLimit(AuditorTestCase):
    def test_second_run_within_interval_refused(self):
        self._run(now=self.now, min_interval=60)
        with self.assertRaises(RateLimitRefused) as ctx:
            self._run(now=self.now + timedelta(seconds=30), min_interval=60)
        self.assertGreater(ctx.exception.retry_after_seconds, 0)
        # No report for the refusal; exactly one refusal log entry.
        self.assertEqual(len(self.store.list_audit_reports()), 1)
        refusals = [e for e in self.store.list_audit_log(limit=1000)
                    if e["action"] == "audit_global_rate_limited"]
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0]["actor"], "lucas")
        self.assertEqual(refusals[0]["target"], "")

    def test_retry_after_interval_succeeds(self):
        self._run(now=self.now, min_interval=60)
        report = self._run(now=self.now + timedelta(seconds=120),
                           min_interval=60)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(len(self.store.list_audit_reports()), 2)

    def test_refusal_does_not_consume_quota(self):
        # Refusals must not move the clock: the clock is the latest
        # *successful* audit. After a refusal at +30s, a run at +61s
        # (61s after the successful audit) must succeed.
        self._run(now=self.now, min_interval=60)
        with self.assertRaises(RateLimitRefused):
            self._run(now=self.now + timedelta(seconds=30), min_interval=60)
        report = self._run(now=self.now + timedelta(seconds=61),
                           min_interval=60)
        self.assertIsNotNone(report)
        self.assertEqual(len(self.store.list_audit_reports()), 2)
        # And a run at +90s is still refused: the clock is still the
        # first successful audit (+61s is now the latest success).
        with self.assertRaises(RateLimitRefused):
            self._run(now=self.now + timedelta(seconds=90), min_interval=60)
        self.assertEqual(len(self.store.list_audit_reports()), 2)

    def test_failure_does_not_block_reaudit(self):
        import app.auditor.service as svc
        real_run_all = svc.run_all
        calls = {"n": 0}

        def flaky(snapshot):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom (forced evaluation failure)")
            return real_run_all(snapshot)

        with mock.patch.object(svc, "run_all", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                self._run(now=self.now, min_interval=60)
            # No report from the failed run, and the re-audit at the
            # SAME instant succeeds: failures never block re-audit.
            self.assertEqual(self.store.list_audit_reports(), [])
            report = self._run(now=self.now, min_interval=60)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(len(self.store.list_audit_reports()), 1)

    def test_rate_limit_enforced_at_service_not_only_cli(self):
        # Direct service call (no CLI involved) is refused.
        self._run(now=self.now, min_interval=60)
        with self.assertRaises(RateLimitRefused):
            AuditService(self.store, self.auth, now=self.now,
                         min_interval_seconds=60).run_global_audit(
                             self.lucas_key)


class TestAtomicityAndTriggers(AuditorTestCase):
    def test_write_failure_rolls_back_both(self):
        # Force the audit_log INSERT inside persist_global_audit to
        # fail with a temporary trigger (sqlite3.Connection.execute
        # cannot be monkeypatched — it is read-only). The report row
        # is inserted first, so a rollback must remove it too.
        self.store.conn.execute(
            "CREATE TRIGGER _test_block_log BEFORE INSERT ON audit_log "
            "BEGIN SELECT RAISE(ABORT, 'boom (forced)'); END;")
        self.store.conn.commit()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                self._run()
        finally:
            self.store.conn.execute(
                "DROP TRIGGER IF EXISTS _test_block_log")
            self.store.conn.commit()
        # Neither the report row nor the log event survived.
        self.assertEqual(self.store.list_audit_reports(), [])
        actions = [e["action"] for e in
                   self.store.list_audit_log(limit=1000)]
        self.assertNotIn("audit_global", actions)

    def test_audit_reports_update_trigger(self):
        report = self._run()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute(
                "UPDATE audit_reports SET status='fail' WHERE report_id=?",
                (report["report_id"],))

    def test_audit_reports_delete_trigger(self):
        report = self._run()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute(
                "DELETE FROM audit_reports WHERE report_id=?",
                (report["report_id"],))
        self.assertIsNotNone(
            self.store.get_audit_report(report["report_id"]))


class TestCliAudit(AuditorTestCase):
    def _file_db(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        return path

    def _seed_file_db(self, path):
        store = Store(path)
        auth = Auth(store)
        lucas = auth.bootstrap()["key"]
        bob = auth.create_key("bob", actor="lucas")["key"]
        store.close()
        return lucas, bob

    def test_cli_audit_global_text_contains_disclaimer(self):
        path = self._file_db()
        lucas, _ = self._seed_file_db(path)
        rc, out, err = run_cli(["audit-global"], path, api_key=lucas)
        self.assertEqual(rc, 0, err)
        self.assertIn(DISCLAIMER, out)
        self.assertIn("rules run: 38", out)
        self.assertIn("[PASS] PERM-001", out)
        self.assertIn("[UNVERIFIED] CREDIT-004", out)

    def test_cli_audit_global_json(self):
        path = self._file_db()
        lucas, _ = self._seed_file_db(path)
        rc, out, err = run_cli(["audit-global", "--format", "json"], path,
                               api_key=lucas)
        self.assertEqual(rc, 0, err)
        report = json.loads(out)
        self.assertIn("report_id", report)
        self.assertEqual(report["ruleset_version"], "3.0.0")
        self.assertEqual(len(report["findings"]), 38)
        self.assertIn("summary", report)
        # Retrieval of the same stored report.
        rc, out, err = run_cli(["audit-report", "--id",
                                report["report_id"]], path, api_key=lucas)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["report_id"], report["report_id"])

    def test_cli_bob_audit_global_fails_silently(self):
        path = self._file_db()
        lucas, bob = self._seed_file_db(path)
        rc, out, err = run_cli(["audit-global"], path, api_key=bob)
        self.assertEqual(rc, 1)
        store = Store(path)
        try:
            self.assertEqual(store.list_audit_reports(), [])
            actions = [e["action"] for e in
                       store.list_audit_log(limit=1000)]
            self.assertNotIn("audit_global", actions)
        finally:
            store.close()

    def test_cli_no_key_audit_global_fails(self):
        path = self._file_db()
        self._seed_file_db(path)
        rc, out, err = run_cli(["audit-global"], path, api_key=None)
        self.assertEqual(rc, 1)

    def test_cli_audit_report_unknown_and_bob(self):
        path = self._file_db()
        lucas, bob = self._seed_file_db(path)
        rc, out, err = run_cli(["audit-report", "--id", "nope"], path,
                               api_key=lucas)
        self.assertEqual(rc, 1)
        # A fresh DB: run a global audit, then bob must not retrieve it.
        path2 = self._file_db()
        lucas2, bob2 = self._seed_file_db(path2)
        rc, out, err = run_cli(["audit-global", "--format", "json"], path2,
                               api_key=lucas2)
        self.assertEqual(rc, 0, err)
        rid = json.loads(out)["report_id"]
        rc, out, err = run_cli(["audit-report", "--id", rid], path2,
                               api_key=bob2)
        self.assertEqual(rc, 1)


class TestImportCompatibility(unittest.TestCase):
    def test_old_auditor_import_still_works(self):
        self.assertIs(AuditorCompat, AgentAuditor)

    def test_new_package_imports(self):
        from app.auditor import AuditService as S2
        from app.auditor import run_global_audit as r2
        self.assertIs(S2, AuditService)
        self.assertIs(r2, run_global_audit)

    def test_registry_has_38_rules(self):
        self.assertEqual(len(RULES), 38)
        self.assertEqual(len(set(rid for rid, _ in RULES)), 38)
        self.assertEqual(
            [rid for rid, _ in RULES][:3],
            ["PERM-001", "PERM-002", "PERM-003"])
        self.assertEqual(
            [rid for rid, _ in RULES][-3:],
            ["SEC-005", "SEC-006", "SEC-007"])


class TestRulesDirect(unittest.TestCase):
    """Rule functions are pure: test impossible DB states directly
    on hand-built snapshots."""

    def test_all_rules_return_a_finding_never_none(self):
        snap = {"users": [], "agents": [], "executions": [],
                "audit_log": [], "api_keys": [], "meta": {},
                "evaluated_at": NOW,
                "policy": {"key_max_age_days": 180,
                           "audit_global_min_interval_seconds": 60}}
        findings = run_all(snap)
        self.assertEqual(len(findings), 38)
        for f in findings:
            self.assertIsNotNone(f)
            self.assertIn(f["status"],
                          ("PASS", "WARN", "FAIL", "UNVERIFIED"))
            self.assertIn(f["severity"],
                          ("low", "medium", "high", "critical"))
            self.assertTrue(f["rule_id"])
            self.assertTrue(f["title"])
            self.assertTrue(f["recommendation"])

    def test_no_finding_leaks_secrets(self):
        snap = {"users": [{"owner": "lucas", "plan": "free",
                           "credits_balance": 100, "is_admin": 1}],
                "agents": [], "executions": [], "audit_log": [],
                "api_keys": [{"public_id": "aabbccddeeff", "owner": "bob",
                              "key_hash": "deadbeef" * 8,
                              "created_at": iso(NOW), "revoked": 0,
                              "revoked_at": None}],
                "meta": {"server_secret": "ab" * 32},
                "evaluated_at": NOW,
                "policy": {"key_max_age_days": 180,
                           "audit_global_min_interval_seconds": 60}}
        for f in run_all(snap):
            blob = json.dumps(f)
            self.assertNotIn("deadbeef", blob)
            self.assertNotIn("ab" * 32, blob)


if __name__ == "__main__":
    unittest.main()
