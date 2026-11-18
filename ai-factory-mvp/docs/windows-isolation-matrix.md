# Windows isolation matrix — Phase 2, step 1 (2026-11-18)

Purpose: the LTP-026 survey ChatGPT asked for before any executor is
built. Researched from documentation only. **Nothing on Lucas's
machine was changed, nothing was installed, nothing was assumed.**
Docker and WSL are NOT assumed — Docker was uninstalled from Lucas's
Windows.

Scope note: this matrix is about isolating an executor process that
would run agent code on Lucas's Windows PC. It is not a review of the
auth layer (see `app/auth.py`).

## The question for each mechanism

What does it actually restrict, what does it NOT restrict, can it be
driven from Python stdlib (`ctypes`, no extra tooling), and is it a
sandbox by itself?

## Matrix

### 1. Plain subprocess (`subprocess.Popen`)

| | |
|---|---|
| Restricts | Nothing by itself. Timeout, output size, and the working directory can be set by the parent — all advisory/application-level. |
| Does NOT restrict | Identity (inherits the parent token = Lucas's full user rights), file access (whole user profile readable/writable), network, registry, other processes, devices, privilege escalation. A child can read the backend's own `data.db`, API key hashes, and the server secret. |
| From Python stdlib | Yes — `subprocess`, trivially. |
| Verdict | **Insufficient alone.** A subprocess is a launch mechanism, not a security boundary. This is Discordance 01 (LTP-026 §7). |

### 2. Windows Job Objects

| | |
|---|---|
| Restricts | Kernel-enforced, per job: per-process and job-wide CPU time (process killed on exceedance), per-process and job-wide committed memory, active process count (fork-bomb cap), CPU rate control, working-set limits, UI restrictions. `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` reaps the whole tree when the controller dies — no orphaned children. Network *rate* control (outgoing bandwidth cap) exists. |
| Does NOT restrict | Identity, file access, registry, network *access* (rate ≠ block), other processes outside the job, privileges. A job member still runs as Lucas and can read his files. |
| From Python stdlib | Yes — `ctypes` on `kernel32`: `CreateJobObject`, `SetInformationJobObject`, `AssignProcessToJobObject`. Available since Windows XP; no extra tooling. |
| Verdict | **Partial — necessary, not sufficient.** The resource-limit layer. Every executor should be in a job (with `KILL_ON_JOB_CLOSE`, memory/CPU/time/active-process caps), but a job alone is not a sandbox. |

### 3. Restricted Tokens (`CreateRestrictedToken`)

| | |
|---|---|
| Restricts | Privileges (dangerous privilege LUIDs deleted), group SIDs turned deny-only (`SE_GROUP_USE_FOR_DENY_ONLY`, one-way ratchet), optional restricting-SID list (access granted only if BOTH the token and the restricting list allow it), integrity level (can be pinned to Low, S-1-16-4096). A restricted version of the caller's own primary token waives the `SeAssignPrimaryTokenPrivilege` requirement. This is the mechanism Chromium's Windows sandbox builds on. |
| Does NOT restrict | File access by itself — a restricted token still passes DACL checks as the same user for objects whose ACL grants that user; the restriction only bites where the DACL or the integrity label denies it. Does not block network, does not cap resources, does not isolate the registry. Needs to be combined with Low IL + ACLs/AppContainer to mean anything for files. |
| From Python stdlib | Yes — `ctypes` on `advapi32`/`kernel32`: `OpenProcessToken`, `CreateRestrictedToken`, `SetTokenInformation`, `CreateProcessAsUserW`. Complex struct marshalling; no extra tooling, but easy to get subtly wrong (e.g. keeping `SeChangeNotifyPrivilege` is required for allowlist path traversal to resolve). |
| Verdict | **Partial — the privilege-stripping layer.** Required in any serious combination, useless alone for file/network isolation. |

### 4. AppContainer (incl. Less-Privileged AppContainer, LPAC)

| | |
|---|---|
| Restricts | OS-enforced security boundary (since Windows 8): the process runs at Low integrity with its own package SID (`S-1-15-2-…`). Deny-by-default for files, registry, devices, processes, windows, credentials, and network — access only via explicitly granted package/capability SIDs in an object's DACL. Network: zero capabilities = no network; loopback blocked by default; `internetClient` / `privateNetworkClientServer` are explicit opt-in capabilities. LPAC is stricter still (needs explicit capabilities even for registry/COM that regular AppContainers get). Per-run profile folder gives temp/state isolation. |
| Does NOT restrict | Anything the profile explicitly grants (capabilities and ACL grants are holes — each must be justified). Network enforcement has known rough edges: NLA interface classification quirks (Chromium brokers connections it cannot classify), and at least one reported firewall-configuration bypass Microsoft marked WontFix. AppContainer does not cap CPU/memory — that is the Job Object's job. |
| From Python stdlib | Possible but the hardest of the native options: `ctypes` on `CreateAppContainerProfile` / `DeriveAppContainerSidFromAppContainerName`, building the token via the process-attribute list (`UpdateProcThreadAttribute` + `CreateProcessAsUserW`), plus ACL grants (`SetNamedSecurityInfo`) for the single work directory. No extra tooling, but significant code and a real risk of misconfiguration. Microsoft's documented `CreateProcessInSandbox` API is an alternative entry point with strict parameter rules. |
| Verdict | **Sufficient as the identity/access layer — IF correctly configured and verified on the real machine.** This is the only native mechanism here that is a genuine sandbox boundary for files and network. It must be combined with a Job Object (resources) and a restricted token (privileges). |

### 5. ACLs (DACLs on files/directories)

| | |
|---|---|
| Restricts | Per-object access: who (which SID) can read/write/execute a file, directory, or registry key. Can grant exactly one work directory to the AppContainer package SID and nothing else. |
| Does NOT restrict | Anything, on their own, for a process running as Lucas: the user's own files grant the user SID, so a same-user process passes the DACL. ACLs only bite when the process runs as a *different, less-privileged principal* (AppContainer SID / restricted token). They also do not restrict network, CPU, memory, or processes. Misapplied ACLs can lock the owner out of their own files. |
| From Python stdlib | Yes — `ctypes` (`SetNamedSecurityInfo`) or shelling out to `icacls` (external tool, but ships with Windows). |
| Verdict | **Partial — the grant mechanism, not a boundary.** Necessary to give the AppContainer exactly one writable directory; meaningless without a less-privileged principal. |

### 6. Windows Firewall / Windows Filtering Platform (WFP)

| | |
|---|---|
| Restricts | Network, for AppContainer processes, by default: zero capabilities means the WFP default block filters drop the traffic (outbound and inbound, loopback included). For ordinary processes, per-application firewall rules can block/allow by program path. |
| Does NOT restrict | Anything for a non-AppContainer process running as the user in a reliable way: firewall rules are admin-configurable policy, not a per-process sandbox — a process with the user's rights cannot be assumed unable to influence its own network path, and rule changes need admin rights to create. Does not restrict files, CPU, memory, or processes. NLA misclassification can leave some interfaces unclassified and traffic dropped (or, in the reported bypass case, allowed) in surprising ways. |
| From Python stdlib | Only indirectly: `netsh advfirewall` / PowerShell `New-NetFirewallRule` (external tools, need admin for rule creation); AppContainer network denial needs no firewall work at all — it comes from the zero-capability profile. |
| Verdict | **Partial — network denial comes free with AppContainer; standalone firewall rules are not a sandbox.** Do not rely on firewall rules to contain a same-user process. |

### 7. Windows Sandbox (Hyper-V disposable VM)

| | |
|---|---|
| Restricts | Almost everything: separate kernel on the Microsoft hypervisor, disposable desktop, state wiped on close. Strongest isolation available on Windows. |
| Does NOT restrict | Nothing relevant — but it has hard prerequisites and costs: requires Windows **Pro, Enterprise, or Education** (NOT Home), 64-bit CPU with virtualization enabled in BIOS/UEFI, ≥ 4 GB RAM (8 GB recommended), ≥ 2 cores, ≥ 1 GB free disk. Only one sandbox at a time. Networking is **enabled by default** (Hyper-V default switch) and mapped folders are exposed, so network/files must be configured off. Lucas's Windows edition is **unknown / unverified** — it must be checked (Settings > System > About) before assuming this exists. |
| From Python stdlib | No — it is a Windows optional feature (`Windows Sandbox` / `Containers-DisposableClientVM`), enabled via Windows Features + restart, driven by `.wsb` files, not by Python. |
| Verdict | **Sufficient, but NOT assumable.** Strongest option, yet unusable until the edition + virtualization prerequisites are verified on Lucas's machine, and heavyweight (a whole VM per execution). Not the step-2 default. |

### 8. Docker / WSL — explicitly excluded

Docker was uninstalled from Lucas's Windows; WSL availability is
unverified. Neither is assumed anywhere in this matrix or in the
Phase 2 plan. Reinstalling Docker/WSL is a separate decision for
Lucas, not a prerequisite smuggled into the sandbox design.

## Recommended combination (step 2, to be demonstrated)

If the executor is built, it must be all of these together — no single
layer is a sandbox:

1. **AppContainer (or LPAC) profile per run** — Low integrity, zero
   network capabilities, exactly one writable work directory granted
   by ACL to the package SID. This is the identity/access boundary.
2. **Restricted token** — dangerous privileges deleted, groups
   deny-only, Low IL pinned. This is the privilege boundary.
3. **Job Object** — `KILL_ON_JOB_CLOSE`, memory cap, CPU time cap,
   active-process cap (fork-bomb), CPU rate. This is the resource
   boundary.
4. **Backend supervision** — the backend (authenticated, see
   `app/auth.py`) starts the process suspended, verifies the token
   integrity level the kernel actually honoured, and kills it on
   timeout or violation; every start/kill/result is audit-logged.

## Honest fallback

If the combination above cannot be **demonstrated** on Lucas's real
Windows machine — with adversarial tests proving network blocked,
files outside the work directory unreadable, limits enforced, and
forced termination working — then **arbitrary code execution stays
DISABLED**. The executor will not ship as a "sandbox" on the strength
of a subprocess + a Job Object alone. A subprocess with limits is a
resource-limited launcher, not a sandbox, and presenting it as one
would be the false sense of isolation warned about in LTP-026 §5.

## What still needs verifying on Lucas's machine (read-only checks)

No configuration was changed for this matrix. Before step 2 is built,
these must be checked on the real machine (read-only, no installs):

- Windows edition and build (Settings > System > About) — Home vs
  Pro/Enterprise; 64-bit or ARM64.
- Virtualization enabled in firmware (Task Manager > Performance >
  CPU) — only relevant if Windows Sandbox is ever considered.
- Whether `CreateAppContainerProfile` / AppContainer launch works
  from a plain user session (no admin) — a small probe program, to be
  written and run in step 2, must pass on the real machine.
- Python version and `ctypes` availability (already known: Python
  3.14.7 on his machine; `ctypes` is stdlib).

## Sources

- Microsoft Learn, "Launch an AppContainer" (AppContainer model,
  capabilities, Low IL, DACL enforcement).
- Microsoft Learn / Windows Sandbox installation requirements
  (Pro/Enterprise/Education, virtualization, RAM/CPU/disk).
- Chromium `sandbox/win` (`CreateRestrictedToken`,
  `StartRestrictedProcessInJob`) and `services/network/broker_helper_win.cc`
  (AppContainer firewall/NLA behaviour).
- Job Object API documentation (`JOBOBJECT_BASIC_LIMIT_INFORMATION`,
  `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`, `KILL_ON_JOB_CLOSE`).
- Independent AppContainer sandbox audit write-ups (Job Object +
  restricted token + Low IL + package-SID ACL grants as the combined
  pattern; runtime verification on a real Windows host required
  before any release claim).
