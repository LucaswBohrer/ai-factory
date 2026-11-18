"""AI Factory — Authentication Layer (Phase 2, step 1).

API keys, HMAC-hashed, shown once. This layer sits ABOVE the library
API (Control / Factory / Auditor), which still takes a plain actor
string: the CLI resolves the actor from a validated key and passes the
resolved owner down. Nothing below this layer ever trusts a
client-supplied --actor.

Design (LTP-026):
- Key format: ``afk_<public_id>_<secret>``. public_id is 12 hex chars
  (6 bytes, an identifier shown in listings). secret is 64 hex chars
  (32 bytes of entropy from ``secrets``). Total secret entropy: 32 bytes.
- Stored: public_id + HMAC-SHA256(secret) only. HMAC key = per-database
  server secret (see Store.get_or_create_server_secret), message =
  ``"<public_id>:<secret>"`` so a hash is bound to its public id.
  The raw key is NEVER stored, logged, or returned again after creation.
- Keys are shown ONLY ONCE, at creation. Listings expose public_id,
  owner, created_at, revoked — never the hash or the secret.
- Revocation is immediate: a revoked key fails authentication.
- Admin: only ``lucas`` (config.ADMIN_USER), assigned server-side from
  the key's owner. No client flag can grant admin.
- Bootstrap: the very first key (lucas, admin) is created by an
  explicit local bootstrap command that needs no key — local access to
  the machine/DB is the trust anchor. Once any key exists, bootstrap
  refuses, and all later keys are created by an authenticated admin.

LIMITATIONS (local MVP, documented in docs/known-vulnerabilities.md):
- The server secret lives in the DB meta table. A production secret
  must live outside the DB. Anyone who can read the DB file can read
  the secret — but they already own the data.
- Authentication here proves possession of a key, not a human identity.
  There is no password, MFA, or expiry (keys do not expire in step 1;
  revoke them instead).
- The library API still accepts a raw actor string; the guarantee
  "actor comes from a validated key" holds only at the CLI boundary
  (and wherever callers use Auth.authenticate first).
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import secrets

from . import config
from .store import Store

KEY_PREFIX = "afk"
PUBLIC_ID_HEX_CHARS = 12          # 6 bytes
SECRET_HEX_CHARS = 64             # 32 bytes of entropy


class AuthenticationFailed(Exception):
    """Generic auth failure. The message is deliberately identical for
    missing, malformed, unknown, revoked, and mismatched keys, so a
    caller cannot learn whether a key id exists."""


class Auth:
    def __init__(self, store: Store):
        self.store = store

    # --- hashing ---
    def _hash(self, public_id: str, secret: str) -> str:
        server_secret = self.store.get_or_create_server_secret()
        return hmac_mod.new(
            server_secret.encode("utf-8"),
            f"{public_id}:{secret}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _parse(raw_key: str) -> tuple[str, str]:
        """Return (public_id, secret) or raise AuthenticationFailed."""
        if not isinstance(raw_key, str):
            raise AuthenticationFailed("authentication failed")
        parts = raw_key.split("_")
        if len(parts) != 3 or parts[0] != KEY_PREFIX:
            raise AuthenticationFailed("authentication failed")
        public_id, secret = parts[1], parts[2]
        if (len(public_id) != PUBLIC_ID_HEX_CHARS
                or len(secret) != SECRET_HEX_CHARS):
            raise AuthenticationFailed("authentication failed")
        try:
            int(public_id, 16)
            int(secret, 16)
        except ValueError:
            raise AuthenticationFailed("authentication failed")
        return public_id, secret

    # --- creation ---
    def create_key(self, owner: str, actor: str | None = None) -> dict:
        """Create a key for ``owner``. Returns a dict with the raw key
        under ``"key"`` — shown ONCE to the caller, never stored.

        ``actor`` is the authenticated owner creating the key, or None
        for the bootstrap path. Rules:
        - Bootstrap (actor=None): allowed ONLY when no keys exist at
          all, and ONLY for the admin (lucas). Local access is the
          trust anchor.
        - Otherwise: only an authenticated admin (lucas) may create keys.
        """
        if not owner or not str(owner).strip():
            raise ValueError("owner is required")
        owner = str(owner).strip()
        if actor is None:
            if self.store.count_api_keys() != 0:
                raise PermissionError(
                    "bootstrap already completed: keys exist;"
                    " new keys require an authenticated admin")
            if owner != config.ADMIN_USER:
                raise PermissionError(
                    "bootstrap may only create the first admin key (lucas)")
        elif actor != config.ADMIN_USER:
            raise PermissionError(
                "only an authenticated admin (lucas) may create API keys")
        public_id = secrets.token_hex(6)          # 12 hex chars
        secret = secrets.token_hex(32)            # 64 hex chars
        raw_key = f"{KEY_PREFIX}_{public_id}_{secret}"
        self.store.insert_api_key(
            public_id, owner, self._hash(public_id, secret))
        self.store.ensure_user(owner)
        self.store.append_audit(
            actor if actor is not None else "bootstrap", "create_api_key",
            public_id, f"API key created for {owner} (public id {public_id})")
        return {
            "public_id": public_id,
            "owner": owner,
            "key": raw_key,
        }

    def bootstrap(self) -> dict:
        """Create the first admin key. See create_key for the rules."""
        return self.create_key(config.ADMIN_USER, actor=None)

    # --- authentication ---
    def authenticate(self, raw_key: str | None) -> str:
        """Validate a raw key, return the owner. Raises the generic
        AuthenticationFailed for every failure mode (missing, malformed,
        unknown id, revoked, hash mismatch) — no existence leak."""
        if not raw_key:
            raise AuthenticationFailed("authentication failed")
        public_id, secret = self._parse(raw_key)
        row = self.store.get_api_key(public_id)
        if row is None or int(row["revoked"]) != 0:
            raise AuthenticationFailed("authentication failed")
        expected = row["key_hash"]
        if not hmac_mod.compare_digest(
                expected, self._hash(public_id, secret)):
            raise AuthenticationFailed("authentication failed")
        return row["owner"]

    def is_admin(self, owner: str) -> bool:
        return owner == config.ADMIN_USER

    def require_admin(self, owner: str) -> None:
        if not self.is_admin(owner):
            raise PermissionError(
                "only an authenticated admin (lucas) may do this")

    # --- listing / revocation ---
    def list_keys(self, actor: str) -> list[dict]:
        """Admin sees all keys; a user sees only their own. Only the
        public fields are returned — never hashes or secrets."""
        rows = self.store.list_api_keys(
            owner=None if self.is_admin(actor) else actor)
        return [{
            "public_id": r["public_id"],
            "owner": r["owner"],
            "created_at": r["created_at"],
            "revoked": bool(int(r["revoked"])),
        } for r in rows]

    def revoke_key(self, public_id: str, actor: str) -> None:
        """Admin-only, immediate. Revoking an unknown id fails with the
        same generic 'unknown key' error an admin would get from listing
        (admins already see the full key list)."""
        self.require_admin(actor)
        row = self.store.get_api_key(public_id)
        if row is None:
            raise ValueError(f"unknown key: {public_id}")
        if int(row["revoked"]) != 0:
            return  # already revoked: idempotent
        self.store.revoke_api_key(public_id)
        self.store.append_audit(
            actor, "revoke_api_key", public_id,
            f"API key revoked (owner {row['owner']})")
