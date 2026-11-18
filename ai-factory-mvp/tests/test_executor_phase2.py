"""Isolated Executor tests — Phase 2, step 2 (LTP-029).

Two groups, kept strictly apart:

A. PLATFORM-INDEPENDENT (run on Linux): fail-closed behaviour, the
   explicitly unsafe dev mode (timeout/kill, output cap, env
   scrubbing), authorization mirroring, charging/refund, audit trail,
   and "no CLI exposes arbitrary execution". The unsafe-dev mode is
   tested AS unsafe: the tests assert it is flagged, logged and
   audited as unsafe — never that it isolates anything.

B. WINDOWS-ONLY (the 10 adversarial attempts from LTP-029 §5):
   each test attempts the real thing inside the AppContainer worker
   and asserts it is blocked. On any non-Windows host they SKIP with
   reason "requires Windows AppContainer — not validated". A skip is
   NOT a pass: it means the isolation is unproven on that host.
   They also skip if AppContainer is not available on that Windows
   machine (isolation not demonstrated, execution refused).

Real calls, no mocks (except os.environ for the env-flag tests and
one monkeypatched supervisor.execute for the refund path).
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.cli import main as cli_main
from app.control import Control, KillSwitchOn
from app.executor import (
    ExecutionLimits, ExecutionSupervisor, IsolationRefused,
    UNSAFE_DEV_ENV,
)
from app.factory import Factory
from app.resource_core import InsufficientCredits, ResourceCore
from app.store import Store

IS_WINDOWS = sys.platform == "win32"
WINDOWS_SKIP = "requires Windows AppContainer — not validated"
EXEC_PERMS = ["read", "execute_limited"]


def make_services():
    store = Store(":memory:")
    resource = ResourceCore(store)
    control = Control(store, resource)
    factory = Factory(store, control, resource)
    return store, resource, control, factory


def make_executable_agent(factory, owner="alice", name="runner"):
    return factory.create_agent(
        owner, name, "summarizer", permissions=EXEC_PERMS,
        approved_by_human=True)


def make_auditor_agent(factory, owner="alice"):
    return factory.create_agent(
        owner, "aud", "auditor", approved_by_human=True)


class TestExecutorFailClosed(unittest.TestCase):
    """A. Fail-closed gate: no isolation, no execution. Runs anywhere."""

    def setUp(self):
        (self.store, self.resource, self.control,
         self.factory) = make_services()
        self.agent = make_executable_agent(self.factory)

    def test_available_is_false_on_linux(self):
        ok, reason = self.control._get_supervisor().available()
        if IS_WINDOWS:
            self.skipTest("Windows host: probe result is machine-specific")
        self.assertFalse(ok)
        self.assertIn("refused", reason)

    def test_execute_isolated_refused_by_default(self):
        with self.assertRaises(IsolationRefused):
            self.control.execute_isolated(
                self.agent["id"], "alice", "print('hi')")
        # Refusal happens BEFORE any charge or record.
        self.assertEqual(self.resource.balance("alice"), 90)  # 100 - 10 create
        self.assertEqual(self.store.count_running_executions(), 0)
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) AS n FROM executions").fetchone()["n"], 0)

    def test_unsafe_dev_requires_env_flag(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(UNSAFE_DEV_ENV, None)
            with self.assertRaises(IsolationRefused):
                self.control.execute_isolated(
                    self.agent["id"], "alice", "print('hi')",
                    unsafe_dev=True)

    def test_unsafe_dev_requires_argument(self):
        with mock.patch.dict(os.environ, {UNSAFE_DEV_ENV: "1"}):
            with self.assertRaises(IsolationRefused):
                self.control.execute_isolated(
                    self.agent["id"], "alice", "print('hi')",
                    unsafe_dev=False)

    def test_unsafe_dev_env_value_must_be_exactly_1(self):
        with mock.patch.dict(os.environ, {UNSAFE_DEV_ENV: "yes"}):
            with self.assertRaises(IsolationRefused):
                self.control.execute_isolated(
                    self.agent["id"], "alice", "print('hi')",
                    unsafe_dev=True)


class TestExecutorUnsafeDev(unittest.TestCase):
    """A. Supervisor logic via the explicitly unsafe dev path.
    These tests prove timeout/kill, output cap and env scrubbing —
    NOT isolation. Every run here is flagged unsafe."""

    def setUp(self):
        (self.store, self.resource, self.control,
         self.factory) = make_services()
        self.agent = make_executable_agent(self.factory)
        self._env = mock.patch.dict(
            os.environ, {UNSAFE_DEV_ENV: "1"})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def _run(self, payload, **kwargs):
        return self.control.execute_isolated(
            self.agent["id"], "alice", payload, unsafe_dev=True, **kwargs)

    def test_unsafe_run_returns_output_and_is_flagged(self):
        out = self._run("print('hello from worker')")
        result = out["result"]
        self.assertEqual(result["status"], "finished")
        self.assertIn("hello from worker", result["stdout"])
        self.assertFalse(result["isolated"])
        self.assertTrue(result["unsafe_dev"])
        self.assertEqual(out["cost"], 1)

    def test_unsafe_run_charges_and_audits(self):
        self._run("print('x')")
        self.assertEqual(self.resource.balance("alice"), 89)  # 90 - 1
        entries = self.store.list_audit_log(limit=50)
        self.assertTrue(any(
            e["action"] == "execute_isolated"
            and "unsafe_dev=True" in e["detail"] for e in entries))

    def test_timeout_kills_worker(self):
        limits = ExecutionLimits(timeout_seconds=1)
        out = self._run("import time; time.sleep(30)", limits=limits)
        result = out["result"]
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["exit_code"])
        self.assertLess(result["wall_seconds"], 10)

    def test_output_cap_truncates(self):
        limits = ExecutionLimits(max_output_bytes=1024)
        out = self._run("print('A' * 100000)", limits=limits)
        result = out["result"]
        self.assertTrue(result["stdout_truncated"])
        self.assertLess(len(result["stdout"].encode("utf-8")), 4096)
        self.assertIn("truncated", result["stdout"])

    def test_env_scrubbing_hides_secrets(self):
        with mock.patch.dict(os.environ, {
                "AI_FACTORY_API_KEY": "afk_MARKER_SECRET_DO_NOT_LEAK",
                "AI_FACTORY_DB": "/srv/secret/data.db",
                "MY_SUPER_SECRET": "hunter2"}):
            out = self._run(
                "import os; print(sorted(os.environ.keys()));"
                " print(repr(os.environ))")
        blob = out["result"]["stdout"]
        self.assertNotIn("MARKER_SECRET_DO_NOT_LEAK", blob)
        self.assertNotIn("AI_FACTORY_API_KEY", blob)
        self.assertNotIn("AI_FACTORY_DB", blob)
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("MY_SUPER_SECRET", blob)

    def test_unsafe_run_logs_warning(self):
        with self.assertLogs("ai_factory.executor", level="WARNING") as cm:
            self._run("print('x')")
        self.assertTrue(any("UNSAFE DEV" in m for m in cm.output))

    def test_worker_exit_code_nonzero_is_failed(self):
        out = self._run("import sys; sys.exit(3)")
        self.assertEqual(out["result"]["status"], "failed")
        self.assertEqual(out["result"]["exit_code"], 3)

    def test_work_dir_is_cleaned_up(self):
        sup = self.control._get_supervisor()
        before = set(os.listdir(sup.work_root))
        self._run("print('x')")
        after = set(os.listdir(sup.work_root))
        self.assertEqual(before, after)


class TestExecutorValidationAndAuthz(unittest.TestCase):
    """A. Payload/limits validation, authorization mirroring, refund."""

    def setUp(self):
        (self.store, self.resource, self.control,
         self.factory) = make_services()
        self.agent = make_executable_agent(self.factory)
        self._env = mock.patch.dict(
            os.environ, {UNSAFE_DEV_ENV: "1"})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_payload_validation(self):
        for bad in ("", "   ", 123, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.control.execute_isolated(
                    self.agent["id"], "alice", bad, unsafe_dev=True)
        with self.assertRaises(ValueError):
            self.control.execute_isolated(
                self.agent["id"], "alice", "x" * (64 * 1024 + 1),
                unsafe_dev=True)

    def test_limits_validation(self):
        with self.assertRaises(ValueError):
            ExecutionLimits(timeout_seconds=0)
        with self.assertRaises(ValueError):
            ExecutionLimits(timeout_seconds=301)  # 5-min containment cap
        with self.assertRaises(ValueError):
            ExecutionLimits(max_memory_bytes=512)
        with self.assertRaises(ValueError):
            ExecutionLimits(max_processes=0)

    def test_non_owner_cannot_execute_isolated(self):
        with self.assertRaises(PermissionError):
            self.control.execute_isolated(
                self.agent["id"], "bob", "print('x')", unsafe_dev=True)
        self.assertEqual(self.resource.balance("bob"), 100)

    def test_agent_without_execute_limited_cannot_run(self):
        aud = make_auditor_agent(self.factory)
        with self.assertRaises(PermissionError):
            self.control.execute_isolated(
                aud["id"], "alice", "print('x')", unsafe_dev=True)

    def test_unknown_agent_for_non_admin(self):
        with self.assertRaises(PermissionError):
            self.control.execute_isolated(
                "no-such-id", "alice", "print('x')", unsafe_dev=True)

    def test_kill_switch_blocks_isolated_execution(self):
        self.control.set_kill_switch(True, "lucas")
        with self.assertRaises(KillSwitchOn):
            self.control.execute_isolated(
                self.agent["id"], "alice", "print('x')", unsafe_dev=True)

    def test_refund_when_supervisor_refuses_after_charging(self):
        """Simulate the Windows launcher failing at launch time (gate
        passed, AppContainer creation failed): the charge is refunded
        and the execution record is 'refused', not 'finished'."""
        sup = self.control._get_supervisor()
        original_execute = sup.execute

        def _refuse(*args, **kwargs):
            raise IsolationRefused("simulated AppContainer failure")

        sup.execute = _refuse
        try:
            with self.assertRaises(IsolationRefused):
                self.control.execute_isolated(
                    self.agent["id"], "alice", "print('x')",
                    unsafe_dev=True)
        finally:
            sup.execute = original_execute
        # Balance restored: 100 - 10 (create) = 90, charge refunded.
        self.assertEqual(self.resource.balance("alice"), 90)
        rows = self.store.conn.execute(
            "SELECT status FROM executions").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "refused")
        entries = self.store.list_audit_log(limit=50)
        self.assertTrue(any(
            e["action"] == "execute_refused" for e in entries))

    def test_no_cli_command_exposes_arbitrary_execution(self):
        """LTP-029 step 2 prepares the executor; the CLI must not
        expose arbitrary execution yet."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "data.db")
            with self.assertRaises(SystemExit) as ctx:
                cli_main(["--db", db, "execute-isolated",
                          "--id", self.agent["id"]])
            self.assertEqual(ctx.exception.code, 2)  # argparse: bad choice


class TestExecutorRegression(unittest.TestCase):
    """A. The 55-test baseline must stay green."""

    def test_full_regression_existing_suite(self):
        """Re-run the 55 pre-existing tests in-process. Explicit
        module imports only — never discovery, which would re-enter
        this file and recurse."""
        import importlib
        import unittest as _ut
        loader = _ut.TestLoader()
        suite = _ut.TestSuite()
        for name in ("test_mvp", "test_isolation_ltp022",
                     "test_auth_phase2"):
            suite.addTests(loader.loadTestsFromModule(
                importlib.import_module(name)))
        result = _ut.TextTestRunner(stream=io.StringIO(),
                                    verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful(),
                        f"regression failures: {result.failures} "
                        f"{result.errors}")
        self.assertEqual(result.testsRun, 55)


# ---------------------------------------------------------------------------
# B. WINDOWS-ONLY adversarial tests (LTP-029 §5).
# On Linux they skip with "requires Windows AppContainer — not
# validated". A skip is NOT a pass.
# ---------------------------------------------------------------------------

def _windows_only(fn):
    """Decorator: run only on Windows with AppContainer available;
    otherwise skip (not validated, not passed)."""
    def wrapper(self, *args, **kwargs):
        if not IS_WINDOWS:
            self.skipTest(WINDOWS_SKIP)
        sup = self.control._get_supervisor()
        ok, reason = sup.available()
        if not ok:
            self.skipTest(
                "AppContainer not available on this machine — "
                f"isolation not demonstrated ({reason})")
        return fn(self, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


class TestExecutorWindowsAdversarial(unittest.TestCase):
    """B. The 10 adversarial attempts from LTP-029 §5, executed for
    real inside the AppContainer worker. Each payload tries to break
    out; the test asserts it is blocked."""

    def setUp(self):
        (self.store, self.resource, self.control,
         self.factory) = make_services()
        self.agent = make_executable_agent(self.factory)
        self.supervisor = self.control._get_supervisor()

    def _run_windows(self, payload, limits=None):
        return self.control.execute_isolated(
            self.agent["id"], "alice", payload,
            limits=limits or ExecutionLimits(timeout_seconds=30))

    # 1. Read files outside the authorized work directory.
    @_windows_only
    def test_01_cannot_read_files_outside_work_dir(self):
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False,
                dir=tempfile.gettempdir()) as fh:
            fh.write("OUTSIDE_MARKER_12345")
            outside = fh.name
        try:
            payload = (
                "try:\n"
                f"    data = open({outside!r}).read()\n"
                "    print('ESCAPED:' + data)\n"
                "except Exception as e:\n"
                "    print('BLOCKED:' + type(e).__name__)\n")
            out = self._run_windows(payload)
            self.assertNotIn("ESCAPED", out["result"]["stdout"])
            self.assertNotIn("OUTSIDE_MARKER_12345",
                             out["result"]["stdout"])
        finally:
            os.unlink(outside)

    # 2. Access the main database.
    @_windows_only
    def test_02_cannot_access_main_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "main.db")
            store = Store(db_path)
            store.ensure_user("alice")
            store.close()
            payload = (
                "import sqlite3\n"
                "try:\n"
                f"    con = sqlite3.connect({db_path!r})\n"
                "    rows = con.execute('SELECT * FROM users').fetchall()\n"
                "    print('ESCAPED:' + str(rows))\n"
                "except Exception as e:\n"
                "    print('BLOCKED:' + type(e).__name__)\n")
            out = self._run_windows(payload)
            self.assertNotIn("ESCAPED", out["result"]["stdout"])
            self.assertTrue(out["result"]["isolated"])

    # 3. Read sensitive environment variables.
    @_windows_only
    def test_03_cannot_read_sensitive_env_vars(self):
        with mock.patch.dict(os.environ, {
                "AI_FACTORY_API_KEY": "afk_WIN_MARKER_SECRET",
                "SUPERVISOR_MARKER": "supervisor-secret-value"}):
            payload = (
                "import os\n"
                "keys = sorted(os.environ.keys())\n"
                "print('KEYS:' + ','.join(keys))\n"
                "print('VALS:' + repr(os.environ))\n")
            out = self._run_windows(payload)
        blob = out["result"]["stdout"]
        self.assertNotIn("AI_FACTORY_API_KEY", blob)
        self.assertNotIn("WIN_MARKER_SECRET", blob)
        self.assertNotIn("SUPERVISOR_MARKER", blob)
        self.assertNotIn("supervisor-secret-value", blob)

    # 4. Open a network connection.
    @_windows_only
    def test_04_network_is_blocked(self):
        payload = (
            "import socket\n"
            "try:\n"
            "    s = socket.create_connection(('8.8.8.8', 53), timeout=5)\n"
            "    print('ESCAPED: connected')\n"
            "    s.close()\n"
            "except Exception as e:\n"
            "    print('BLOCKED:' + type(e).__name__)\n")
        out = self._run_windows(payload)
        self.assertNotIn("ESCAPED", out["result"]["stdout"])
        self.assertIn("BLOCKED", out["result"]["stdout"])

    # 5. Spawn child processes outside the limits.
    @_windows_only
    def test_05_child_process_count_is_capped(self):
        payload = (
            "import subprocess, sys\n"
            "ok = 0\n"
            "procs = []\n"
            "for i in range(20):\n"
            "    try:\n"
            "        p = subprocess.Popen([sys.executable, '-c',"
            " 'import time; time.sleep(3)'])\n"
            "        procs.append(p); ok += 1\n"
            "    except Exception:\n"
            "        pass\n"
            "print('SPAWNED:' + str(ok))\n"
            "for p in procs:\n"
            "    p.wait()\n")
        out = self._run_windows(
            payload, limits=ExecutionLimits(timeout_seconds=60,
                                            max_processes=8))
        # Job Object caps active processes at 8 (worker included), so
        # 20 concurrent children cannot all start.
        import re
        m = re.search(r"SPAWNED:(\d+)", out["result"]["stdout"])
        self.assertIsNotNone(m, out["result"])
        self.assertLess(int(m.group(1)), 20)

    # 6. Consume excessive memory.
    @_windows_only
    def test_06_excessive_memory_is_killed(self):
        payload = (
            "chunks = []\n"
            "while True:\n"
            "    chunks.append(bytearray(16 * 1024 * 1024))\n")
        out = self._run_windows(
            payload,
            limits=ExecutionLimits(timeout_seconds=60,
                                   max_memory_bytes=64 * 1024 * 1024))
        # Job Object memory cap must kill the worker; it must not
        # finish normally with the allocation succeeding.
        self.assertNotEqual(out["result"]["status"], "finished")
        self.assertTrue(out["result"]["isolated"])

    # 7. Survive past the timeout.
    @_windows_only
    def test_07_timeout_kills_worker(self):
        out = self._run_windows(
            "import time; time.sleep(120)",
            limits=ExecutionLimits(timeout_seconds=5))
        self.assertEqual(out["result"]["status"], "timeout")
        self.assertIsNone(out["result"]["exit_code"])
        self.assertLess(out["result"]["wall_seconds"], 30)

    # 8. Modify backend files.
    @_windows_only
    def test_08_cannot_modify_backend_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend_file = os.path.join(tmp, "backend.py")
            with open(backend_file, "w") as fh:
                fh.write("# backend\n")
            payload = (
                "try:\n"
                f"    open({backend_file!r}, 'w').write('pwned')\n"
                "    print('ESCAPED: modified')\n"
                "except Exception as e:\n"
                "    print('BLOCKED:' + type(e).__name__)\n")
            out = self._run_windows(payload)
            self.assertNotIn("ESCAPED", out["result"]["stdout"])
            with open(backend_file) as fh:
                self.assertEqual(fh.read(), "# backend\n")

    # 9. Access the supervisor's identity (parent process / token).
    @_windows_only
    def test_09_cannot_access_supervisor_identity(self):
        payload = (
            "import ctypes, os\n"
            "ppid = os.getppid()\n"
            "k32 = ctypes.WinDLL('kernel32')\n"
            "h = k32.OpenProcess(0x0400, False, ppid)  # QUERY_INFORMATION\n"
            "print('HANDLE:' + str(bool(h)))\n"
            "if h:\n"
            "    print('ESCAPED: opened supervisor process')\n"
            "else:\n"
            "    print('BLOCKED: OpenProcess denied')\n")
        out = self._run_windows(payload)
        self.assertNotIn("ESCAPED", out["result"]["stdout"])
        self.assertIn("BLOCKED", out["result"]["stdout"])

    # 10. Escape the AppContainer (write outside, list the profile).
    @_windows_only
    def test_10_cannot_escape_appcontainer(self):
        payload = (
            "import os\n"
            "targets = [r'C:\\Windows\\Temp\\escape.txt',\n"
            "           os.path.join(os.path.expanduser('~'),"
            "                        'escape.txt')]\n"
            "escaped = []\n"
            "for t in targets:\n"
            "    try:\n"
            "        open(t, 'w').write('x')\n"
            "        escaped.append(t)\n"
            "    except Exception:\n"
            "        pass\n"
            "try:\n"
            "    entries = os.listdir(os.path.expanduser('~'))\n"
            "    print('LISTED_HOME:' + str(len(entries)))\n"
            "except Exception as e:\n"
            "    print('BLOCKED_LIST:' + type(e).__name__)\n"
            "print('ESCAPED_FILES:' + str(escaped))\n")
        out = self._run_windows(payload)
        self.assertIn("ESCAPED_FILES:[]", out["result"]["stdout"])
        self.assertNotIn("LISTED_HOME:", out["result"]["stdout"])

    # Fail-closed on Windows: if the AppContainer probe fails, the
    # supervisor must refuse instead of running unisolated.
    @_windows_only
    def test_11_windows_probe_is_the_gate(self):
        ok, reason = self.supervisor.available()
        self.assertTrue(ok, f"probe failed on Windows: {reason}")
        self.assertIn("presence", reason.lower())


if __name__ == "__main__":
    unittest.main()
