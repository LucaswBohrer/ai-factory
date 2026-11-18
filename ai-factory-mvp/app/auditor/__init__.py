"""Phase 3 — global Auditor package.

Service entry point: run_global_audit(store, auth, raw_key, now=None)
(see service.py). The service authenticates the raw key itself and
enforces admin — the CLI passes the raw key through and never
resolves an owner string.

Import compatibility (docs/auditor-architecture.md §9.2): the
per-agent Auditor moved to app.agent_auditor, and `from app.auditor
import Auditor` keeps working through this re-export, so existing
callers (CLI, tests) are unaffected.
"""
from __future__ import annotations

from ..agent_auditor import Auditor
from .models import DISCLAIMER, RULESET_VERSION
from .service import AuditService, RateLimitRefused, run_global_audit

__all__ = [
    "AuditService",
    "Auditor",
    "DISCLAIMER",
    "RULESET_VERSION",
    "RateLimitRefused",
    "run_global_audit",
]
