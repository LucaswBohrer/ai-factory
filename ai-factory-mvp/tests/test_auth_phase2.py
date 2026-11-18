"""Authentication Layer tests — Phase 2, step 1 (LTP-026).

Covers: key shown once / never stored in plaintext, hash-only storage,
revocation, generic failures (no existence leak), forged --actor
rejection, admin-only operations, bootstrap rules, and full regression
of the 41 pre-existing tests. Real calls, no mocks (except os.environ
for the env-var key test).
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

from app.auditor import Auditor
from app.auth import Auth, AuthenticationFailed
from app.cli import main as cli_main
from app.config import ADMIN_USER
from app.control import Control
from app.factory import Factory
from app.resource_core import ResourceCore
from app.store import Store


def make_services():
    store = Store(":memory:")
    resource = ResourceCore(store)
    control = Control(store, resource)
    factory = Factory(store, control, resource)
    auditor = Auditor(store, control, resource)
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


class TestAuthPhase2(unittest.TestCase):
    def setUp(self):
        (self.store, self.resource, self.control, self.factory,
         self.auditor, self.auth) = make_services()

    def _bootstrap(self):
        return self.auth.bootstrap()["key"]

    def _key_for(self, owner, admin_key=None):
        admin = admin_key or self._bootstrap()
        actor = self.auth.authenticate(admin)
        return self.auth.create_key(owner, actor=actor)["key"]

    # --- key creation: shown once, never stored in plaintext ---
    def test_key_shown_once_and_never_stored(self):
        result = self.auth.bootstrap()
        raw = result["key"]
        self.assertTrue(raw.startswith("afk_"))
        parts = raw.split("_")
        self.assertEqual(len(parts), 3)
        secret = parts[2]
        # Authenticate with the shown key.
        self.assertEqual(self.auth.authenticate(raw), "lucas")
        # No table anywhere contains the raw key or its secret part.
        for table in ("api_keys", "meta", "audit_log", "users", "agents"):
            rows = self.store.conn.execute(
                f"SELECT * FROM {table}").fetchall()
            for row in rows:
                for value in dict(row).values():
                    text = "" if value is None else str(value)
                    self.assertNotIn(raw, text)
                    self.assertNotIn(secret, text)
        # Listings never expose the hash or the secret either.
        for entry in self.auth.list_keys("lucas"):
            self.assertNotIn("key", entry)
            self.assertNotIn("key_hash", entry)
            self.assertNotIn("secret", entry)
            self.assertNotIn(secret, str(entry))

    def test_hash_only_storage(self):
        result = self.auth.bootstrap()
        raw = result["key"]
        public_id, secret = raw.split("_")[1], raw.split("_")[2]
        row = self.store.get_api_key(public_id)
        self.assertNotEqual(row["key_hash"], secret)
        self.assertNotEqual(row["key_hash"], raw)
        # The stored hash is exactly HMAC(server_secret, "id:secret").
        import hashlib
        import hmac as hmac_mod
        server_secret = self.store.get_meta("server_secret")
        expected = hmac_mod.new(
            server_secret.encode(), f"{public_id}:{secret}".encode(),
            hashlib.sha256).hexdigest()
        self.assertEqual(row["key_hash"], expected)
        # Same public id, different secret: fails.
        import secrets as _secrets
        forged = f"afk_{public_id}_{_secrets.token_hex(32)}"
        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(forged)
        self.assertEqual(str(ctx.exception), "authentication failed")

    # --- revocation ---
    def test_revocation_blocks_immediately(self):
        lucas_key = self._bootstrap()
        alice_key = self._key_for("alice", admin_key=lucas_key)
        self.assertEqual(self.auth.authenticate(alice_key), "alice")
        public_id = alice_key.split("_")[1]
        self.auth.revoke_key(public_id, "lucas")
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(alice_key)
        # Revoking again is idempotent, not an error.
        self.auth.revoke_key(public_id, "lucas")

    def test_revoke_requires_admin_and_known_id(self):
        lucas_key = self._bootstrap()
        alice_key = self._key_for("alice", admin_key=lucas_key)
        public_id = alice_key.split("_")[1]
        with self.assertRaises(PermissionError):
            self.auth.revoke_key(public_id, "alice")
        with self.assertRaises(ValueError):
            self.auth.revoke_key("deadbeefdead", "lucas")
        # Still valid after the failed attempts.
        self.assertEqual(self.auth.authenticate(alice_key), "alice")

    # --- generic failures: no existence leak ---
    def test_wrong_key_fails_with_generic_message(self):
        lucas_key = self._bootstrap()
        public_id = lucas_key.split("_")[1]
        import secrets as _secrets
        bad_keys = [
            None, "", "afk_", "afk_x_y", "not-a-key",
            f"afk_{'00'*6}_{_secrets.token_hex(32)}",  # unknown id
            f"afk_{public_id}_{_secrets.token_hex(32)}",  # wrong secret
            lucas_key[:-1] + ("0" if lucas_key[-1] != "0" else "1"),
        ]
        for bad in bad_keys:
            with self.assertRaises(AuthenticationFailed) as ctx:
                self.auth.authenticate(bad)
            self.assertEqual(str(ctx.exception), "authentication failed")

    # --- bootstrap rules ---
    def test_bootstrap_rules(self):
        result = self.auth.bootstrap()
        self.assertEqual(self.auth.authenticate(result["key"]), "lucas")
        with self.assertRaises(PermissionError):
            self.auth.bootstrap()
        with self.assertRaises(PermissionError):
            self.auth.create_key("alice", actor=None)
        with self.assertRaises(PermissionError):
            self.auth.create_key("mallory", actor="alice")
        # Only an authenticated admin creates later keys.
        with self.assertRaises(PermissionError):
            self.auth.create_key("bob", actor="bob")

    def test_bootstrap_only_for_admin(self):
        with self.assertRaises(PermissionError):
            self.auth.create_key("alice", actor=None)

    # --- forged actor via CLI ---
    def test_forged_actor_rejected_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            lucas_key = run_cli(["bootstrap"], db)[1].splitlines()[2]
            alice_key = run_cli(
                ["create-key", "--owner", "alice"], db, lucas_key
            )[1].splitlines()[1]
            bob_key = run_cli(
                ["create-key", "--owner", "bob"], db, lucas_key
            )[1].splitlines()[1]
            # Bob's key + --actor alice: forgery, generic failure.
            rc, _, err = run_cli(
                ["list-agents", "--actor", "alice"], db, bob_key)
            self.assertEqual(rc, 1)
            self.assertIn("authentication failed", err)
            rc, _, err = run_cli(
                ["execute", "--id", "whatever", "--actor", "alice"],
                db, bob_key)
            self.assertEqual(rc, 1)
            self.assertIn("authentication failed", err)
            # Bob's key alone works as bob (empty list, no leak).
            rc, out, _ = run_cli(["list-agents"], db, bob_key)
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "")

    def test_missing_key_fails_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            rc, _, err = run_cli(
                ["create-agent", "--owner", "alice", "--name", "x",
                 "--kind", "summarizer", "--approve"], db)
            self.assertEqual(rc, 1)
            self.assertIn("authentication failed", err)

    def test_api_key_from_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            lucas_key = run_cli(["bootstrap"], db)[1].splitlines()[2]
            with mock.patch.dict(os.environ,
                                {"AI_FACTORY_API_KEY": lucas_key}):
                rc, out, _ = run_cli(["credits"], db)
            self.assertEqual(rc, 0)
            self.assertIn("unlimited", out)

    # --- admin-only operations ---
    def test_admin_only_operations_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            lucas_key = run_cli(["bootstrap"], db)[1].splitlines()[2]
            bob_key = run_cli(
                ["create-key", "--owner", "bob"], db, lucas_key
            )[1].splitlines()[1]
            # Kill switch with a non-admin key: rejected.
            rc, _, err = run_cli(["kill-switch", "on"], db, bob_key)
            self.assertEqual(rc, 1)
            self.assertNotIn("authentication failed", err)
            # Kill switch with the lucas key: works.
            rc, out, _ = run_cli(["kill-switch", "on"], db, lucas_key)
            self.assertEqual(rc, 0)
            self.assertIn("kill switch: on", out)
            rc, out, _ = run_cli(["kill-switch", "off"], db, lucas_key)
            self.assertEqual(rc, 0)
            # create-key with a non-admin key: rejected.
            rc, _, err = run_cli(
                ["create-key", "--owner", "mallory"], db, bob_key)
            self.assertEqual(rc, 1)

    def test_admin_execute_logged_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            lucas_key = run_cli(["bootstrap"], db)[1].splitlines()[2]
            alice_key = run_cli(
                ["create-key", "--owner", "alice"], db, lucas_key
            )[1].splitlines()[1]
            rc, out, err = run_cli(
                ["create-agent", "--owner", "alice", "--name", "w",
                 "--kind", "summarizer", "--permissions",
                 "read,execute_limited", "--approve"], db, alice_key)
            self.assertEqual(rc, 0, err)
            agent_id = __import__("json").loads(out)["id"]
            rc, out, err = run_cli(
                ["execute", "--id", agent_id, "--minutes", "1"],
                db, lucas_key)
            self.assertEqual(rc, 0, err)
            rc, out, _ = run_cli(["list-audit-log", "--limit", "50"],
                                 db, lucas_key)
            self.assertEqual(rc, 0)
            self.assertIn("admin_execute", out)

    def test_create_agent_owner_scoping_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.db")
            lucas_key = run_cli(["bootstrap"], db)[1].splitlines()[2]
            bob_key = run_cli(
                ["create-key", "--owner", "bob"], db, lucas_key
            )[1].splitlines()[1]
            # Bob cannot create an agent owned by alice.
            rc, _, err = run_cli(
                ["create-agent", "--owner", "alice", "--name", "x",
                 "--kind", "summarizer", "--approve"], db, bob_key)
            self.assertEqual(rc, 1)
            # Lucas (admin) can create for alice.
            rc, _, err = run_cli(
                ["create-agent", "--owner", "alice", "--name", "x",
                 "--kind", "summarizer", "--approve"], db, lucas_key)
            self.assertEqual(rc, 0, err)

    # --- full regression of the 41 pre-existing tests ---
    def test_full_regression_existing_suite(self):
        import test_isolation_ltp022
        import test_mvp
        suite = unittest.TestSuite()
        suite.addTests(
            unittest.defaultTestLoader.loadTestsFromModule(test_mvp))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(
            test_isolation_ltp022))
        result = unittest.TextTestRunner(stream=io.StringIO(),
                                         verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful(),
                        f"regression failures: {result.failures} "
                        f"{result.errors}")
        self.assertEqual(result.testsRun, 41)


if __name__ == "__main__":
    unittest.main()
