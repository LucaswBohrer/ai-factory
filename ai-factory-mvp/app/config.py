"""AI Factory MVP — config.

Approved scope: LTP-017 (ChatGPT) + LTP-018 (Theo) + Lucas approval
2026-11-18 14:30. Single modular backend, Python stdlib only.
No recursion, no real payments, no microservices, no LLM calls in MVP.
"""
from __future__ import annotations

ADMIN_USER = "lucas"

# --- Containment limits (hard, enforced in code) ---
MAX_DEPTH = 1
MAX_ACTIVE_PER_USER = 3
MAX_CREATIONS_PER_REQUEST = 1
MAX_CONCURRENT_EXECUTIONS = 2
MAX_EXECUTION_MINUTES = 5

# --- Credits (internal units only; no money, no payment gateway) ---
FREE_QUOTA = 100
COST_CREATE = 10          # credits per agent creation
COST_EXECUTION_PER_MIN = 1  # credits per execution minute
COST_AUDIT = 1            # credits per audit

# Plan quotas are config placeholders only. No real charges, no gateway.
PAID_PLANS = {
    "basic": 500,
    "pro": 2000,
}

# --- Agent schema allowlists ---
ALLOWED_TOOLS = frozenset(
    {"read_config", "read_logs", "summarize", "search_local", "audit"}
)
ALLOWED_PERMISSIONS = frozenset({"read", "audit", "execute_limited"})
# Anything here in a definition is rejected by the Factory outright.
FORBIDDEN_PERMISSIONS = frozenset(
    {"admin", "self_modify", "self_modify_permissions", "create_agent",
     "write", "delete", "manage_users", "manage_billing"}
)
FORBIDDEN_TOOLS = frozenset({"write", "execute_unlimited", "create_agent"})

AGENT_STATUSES = frozenset({"active", "suspended", "killed"})

# Template-based creation only (no LLM generation in MVP).
# kind -> defaults. An agent's tools/permissions must stay within the
# allowlists above; a template may only narrow them, never widen them.
AGENT_TEMPLATES = {
    "auditor": {
        "description": "Read-only auditor: analyses agent config, "
                       "permissions, risks, execution cycles and consumption.",
        "tools": ["read_config", "read_logs", "audit"],
        "permissions": ["read", "audit"],
        "max_credits": 100,
    },
    "summarizer": {
        "description": "Summarizes local configs and logs. Read-only.",
        "tools": ["read_config", "read_logs", "summarize"],
        "permissions": ["read"],
        "max_credits": 50,
    },
    "researcher": {
        "description": "Searches local data and summarizes findings. Read-only.",
        "tools": ["search_local", "summarize", "read_config"],
        "permissions": ["read"],
        "max_credits": 50,
    },
}
