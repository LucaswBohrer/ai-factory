"""Phase 3 — global Auditor: shared helpers for rule modules.

Kept separate from rules/__init__.py so the rule modules can import
helpers without a circular import (the registry __init__ imports
the rule modules).
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_ts(value):
    """Parse an ISO-8601 timestamp (as written by Store._now) into an
    aware datetime, or None when it cannot be parsed. Naive values
    are treated as UTC. A trailing 'Z' is accepted."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(
            tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
