"""Phase 3 — global Auditor: PERM rules (permissions, grants, containment).

Every rule is a deterministic function rule(snapshot) -> Finding.
Single rule contract (LTP-038 correction C): never returns None —
every registered rule produces exactly one finding. See
docs/auditor-architecture.md §6.1.
"""
from __future__ import annotations

from ... import config
from ..models import finding


def perm_001(snapshot: dict) -> dict:
    """No agent holds a forbidden permission."""
    bad = {}
    for a in snapshot["agents"]:
        hit = sorted(set(a["permissions"]) & set(config.FORBIDDEN_PERMISSIONS))
        if hit:
            bad[a["id"]] = hit
    if bad:
        return finding("PERM-001", "FAIL", "critical",
                       "Agent holds a forbidden permission",
                       sorted(bad),
                       "Remove the forbidden permission(s) — agents are "
                       "created by the Factory only, so their presence "
                       "means direct DB manipulation or a bug.")
    return finding("PERM-001", "PASS", "low",
                   "No agent holds a forbidden permission", [],
                   "No action needed.")


def perm_002(snapshot: dict) -> dict:
    """No agent holds a permission outside the allowlists."""
    bad = {}
    for a in snapshot["agents"]:
        hit = sorted(set(a["permissions"]) - set(config.ALLOWED_PERMISSIONS)
                     - set(config.FORBIDDEN_PERMISSIONS))
        if hit:
            bad[a["id"]] = hit
    if bad:
        return finding("PERM-002", "FAIL", "critical",
                       "Agent holds a permission outside the allowlists",
                       sorted(bad),
                       "Remove the unknown permission(s) — their presence "
                       "means direct DB manipulation or a bug.")
    return finding("PERM-002", "PASS", "low",
                   "No agent holds a permission outside the allowlists", [],
                   "No action needed.")


def perm_003(snapshot: dict) -> dict:
    """No agent holds self_modify / self_modify_permissions / admin /
    create_agent — the recursion / self-modification risk class."""
    risky = {"self_modify", "self_modify_permissions", "admin", "create_agent"}
    bad = [a["id"] for a in snapshot["agents"]
           if set(a["permissions"]) & risky]
    if bad:
        return finding("PERM-003", "FAIL", "critical",
                       "Agent holds a self-modification or agent-creation "
                       "permission",
                       sorted(bad),
                       "Remove the permission(s) immediately — an agent "
                       "must never modify its own permissions or create "
                       "other agents.")
    return finding("PERM-003", "PASS", "low",
                   "No agent holds a self-modification or agent-creation "
                   "permission", [],
                   "No action needed.")


def perm_004(snapshot: dict) -> dict:
    """No agent holds a tool outside the allowlist (this includes the
    forbidden tools: they are all outside the allowlist)."""
    bad = {}
    for a in snapshot["agents"]:
        hit = sorted(set(a["tools"]) - set(config.ALLOWED_TOOLS))
        if hit:
            bad[a["id"]] = hit
    if bad:
        return finding("PERM-004", "FAIL", "critical",
                       "Agent holds a tool outside the allowlist",
                       sorted(bad),
                       "Remove the tool(s) — their presence means direct "
                       "DB manipulation or a bug.")
    return finding("PERM-004", "PASS", "low",
                   "No agent holds a tool outside the allowlist", [],
                   "No action needed.")


def perm_005(snapshot: dict) -> dict:
    """Depth / parent: depth <= MAX_DEPTH (1) and parent_id is NULL."""
    deep = [a["id"] for a in snapshot["agents"]
            if int(a["depth"]) > config.MAX_DEPTH]
    if deep:
        return finding("PERM-005", "FAIL", "critical",
                       "Agent depth exceeds the containment limit",
                       sorted(deep),
                       "Investigate — creation code forbids agent parents, "
                       "so depth above the limit means direct DB "
                       "manipulation or a bug.")
    parents = [a["id"] for a in snapshot["agents"] if a.get("parent_id")]
    if parents:
        return finding("PERM-005", "WARN", "medium",
                       "Agent has a parent_id",
                       sorted(parents),
                       "Investigate — agents are created by humans via the "
                       "Factory only, so a parent means direct DB "
                       "manipulation or a bug.")
    return finding("PERM-005", "PASS", "low",
                   "All agents satisfy the depth and parent rules", [],
                   "No action needed.")


def perm_006(snapshot: dict) -> dict:
    """Active-agent cap: count_active(owner) <= MAX_ACTIVE_PER_USER (3)."""
    counts: dict[str, int] = {}
    for a in snapshot["agents"]:
        if a["status"] == "active":
            counts[a["owner"]] = counts.get(a["owner"], 0) + 1
    over = sorted(o for o, n in counts.items()
                  if n > config.MAX_ACTIVE_PER_USER)
    if over:
        return finding("PERM-006", "FAIL", "high",
                       "Owner exceeds the active-agent cap",
                       over,
                       "Investigate — the Factory enforces the cap, so an "
                       "excess means it was bypassed (direct insert or a "
                       "bug).")
    return finding("PERM-006", "PASS", "low",
                   "All owners are within the active-agent cap", [],
                   "No action needed.")


def perm_007(snapshot: dict) -> dict:
    """Agent status is one of active / suspended / killed."""
    bad = [a["id"] for a in snapshot["agents"]
           if a["status"] not in config.AGENT_STATUSES]
    if bad:
        return finding("PERM-007", "FAIL", "high",
                       "Agent has an invalid status",
                       sorted(bad),
                       "Investigate — only direct DB manipulation can set "
                       "a status outside the allowlist.")
    return finding("PERM-007", "PASS", "low",
                   "All agent statuses are valid", [],
                   "No action needed.")


def perm_008(snapshot: dict) -> dict:
    """users.is_admin agrees with the name rule: is_admin = 1 iff
    owner = 'lucas'. The flag is unused for authorization, so this is
    a data-integrity check, not a permission check."""
    bad = [u["owner"] for u in snapshot["users"]
           if int(u["is_admin"]) != (1 if u["owner"] == config.ADMIN_USER
                                     else 0)]
    if bad:
        return finding("PERM-008", "FAIL", "high",
                       "users.is_admin disagrees with the admin name rule",
                       sorted(bad),
                       "Fix the flag — it grants nothing by itself, but "
                       "the inconsistency means the row was edited "
                       "directly.")
    return finding("PERM-008", "PASS", "low",
                   "users.is_admin agrees with the admin name rule", [],
                   "No action needed.")


def perm_009(snapshot: dict) -> dict:
    """Kill-switch state is consistent with the toggle log: if
    meta.kill_switch = 'on', the latest toggle entry is kill_switch_on;
    if 'off' (or absent), the latest toggle is kill_switch_off or
    there is no toggle entry."""
    stored = snapshot["meta"].get("kill_switch", "off")
    toggles = [e for e in snapshot["audit_log"]
               if e["action"] in ("kill_switch_on", "kill_switch_off")]
    latest = toggles[-1]["action"] if toggles else None
    consistent = ((stored == "on" and latest == "kill_switch_on")
                  or (stored != "on"
                      and latest in (None, "kill_switch_off")))
    if consistent:
        return finding("PERM-009", "PASS", "low",
                       "Kill-switch state matches the toggle log", [],
                       "No action needed.")
    return finding("PERM-009", "WARN", "medium",
                   "Kill-switch state disagrees with the toggle log",
                   [f"#{toggles[-1]['id']}"] if toggles else [],
                   "Investigate — the stored value was edited directly "
                   "or a toggle log entry is missing.")


def perm_010(snapshot: dict) -> dict:
    """No agent holds execute_limited unless explicitly granted at
    creation. NOT VERIFIABLE: the DB records only the current
    permission list, not how it got there (§7.4). The rule produces
    exactly one UNVERIFIED finding listing every agent holding
    execute_limited, for review — the list is the finding."""
    holders = sorted(a["id"] for a in snapshot["agents"]
                     if "execute_limited" in a["permissions"])
    return finding(
        "PERM-010", "UNVERIFIED", "medium",
        "Cannot verify execute_limited was explicitly granted at "
        "creation",
        holders,
        "Review the listed agents: grant provenance is not recorded, "
        "so explicit grant cannot be proven from the database. "
        "Minimal schema change: granted_permissions JSON on agents "
        "(see docs/auditor-architecture.md §7.4).")
