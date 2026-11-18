# Windows diagnostic — Phase 2, step 2 (LTP-029 §3, LTP-030)

## What it is

`tools/diagnostico-windows-readonly.ps1` — a **read-only** check of
what Lucas's Windows machine actually supports for the isolated
executor (AppContainer/LPAC + restricted token + Job Object).

## What it does NOT do

- No installs, no enabling/disabling, no config changes.
- No registry writes, no files created, no services touched.
- No AppContainer profile is created (that is a persistent object
  and needs separate authorization — LTP-030).
- The only output is the report printed to stdout.
- The only "actions" are read-only queries: `Get-CimInstance`
  (OS, CPU, virtualization), `Get-WindowsOptionalFeature` (state
  query only — Sandbox, Hyper-V, Virtual Machine Platform),
  checking the current token's admin role, and `python --version`.

## How to run it (on Lucas's Windows machine)

Double-click the file, or from PowerShell in the repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\diagnostico-windows-readonly.ps1
```

No admin rights needed; it works from a plain user session. Copy
the WHOLE output and paste it back in the chat. No commands to type
— Lucas stays an end user: one double-click, nothing typed.

## What it checks

One line per item, format `{name} : {value} [status]`, status
**Disponível / Disponível com restrições / Não disponível /
Não verificado**:

| Item | What it means for step 2 |
|---|---|
| Versão do Windows / Edição / Arquitetura | Informational (build number feeds the API checks). Edition (Home vs Pro/Enterprise) only matters for Windows Sandbox (Pro+ required) — AppContainer works on Home too. |
| RAM total / Processador / Núcleos | Decides whether Windows Sandbox would even be viable (≥ 4 GB RAM, ≥ 2 cores). |
| Virtualização (firmware) | Only relevant for Windows Sandbox, not for AppContainer. |
| Sessão com admin | Expected **Não** (plain user session). The executor is designed to run WITHOUT admin. |
| Windows Sandbox / Hyper-V / Virtual Machine Platform | State query only. Sandbox is the strongest option but was never the step-2 default; informational. |
| Job Objects (API) / Token restrito (API) / AppContainer (API) / LPAC (API) | Reported as "API presente nesta build" when build ≥ 9200 (Windows 8). **API presence ≠ proven isolation** — the script says so in its own output. The functional proof is the step-2 probe (the 10 adversarial tests), which needs separate authorization because it creates a temporary AppContainer profile. |
| Python | Informational (`ctypes` is stdlib; any Python 3 works). |

## How to read the result

- **Everything Disponível** → the machine can host the executor;
  the next step is running the 10 adversarial tests
  (`tests/test_executor_phase2.py`, class
  `TestExecutorWindowsAdversarial`) on the real machine. Only
  passing those demonstrates isolation — API presence alone does
  not.
- **AppContainer "Não disponível"** (build < 9200) → stop. No
  executor, no arbitrary execution. The honest fallback from the
  matrix applies.
- **Anything Não verificado** → record it as a limitation; the
  functional probe must answer it, with authorization.
- **Windows Sandbox Desabilitado** → fine. It was never the step-2
  default.

## Note on the executor code

`app/executor.py` was written on a Linux VM and has never run on
Windows. Its `_WindowsIsolatedLauncher.probe()` checks the same API
presence this script reports (via `GetProcAddress`), plus OS
version — the diagnostic is the human-readable version of that
probe, run on the real machine before any executor test is trusted.
The functional probe (creating an AppContainer profile and running
the adversarial tests) is a separate step requiring Lucas's explicit
authorization (LTP-030).
