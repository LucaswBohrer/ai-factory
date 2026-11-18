"""AI Factory — Isolated Executor (Phase 2, step 2).

Architecture (LTP-029 §4):

    AI Control  ->  Execution Supervisor  ->  Isolated Worker
    (auth,           (prepares process,        (AppContainer/LPAC +
     authorization,   work dir, policies;       restricted token +
     limits)         stays OUTSIDE the          Job Object)
                      worker environment)
                      -> limited, audited result

What the worker receives: ONLY the task payload (a Python source
string, written to ``task.py`` inside its own fresh work directory)
and that work directory. It receives NO API keys, NO database
secrets, NO access to the project directory, NO permission to spawn
other agents, NO network, and NO admin rights. The supervisor builds
the worker's environment from scratch (allowlist only).

HONEST CONSTRAINT — READ THIS FIRST
====================================
This module was written on a Linux VM. Windows AppContainer/LPAC,
Job Objects and restricted tokens CANNOT be executed or validated
here. The Windows launcher below (``_WindowsIsolatedLauncher``) is
implemented from the documented Win32 APIs, but it is **NOT
VALIDATED** until the adversarial tests in
``tests/test_executor_phase2.py`` run on Lucas's real Windows machine
and pass. Until then:

- ``ExecutionSupervisor.execute`` FAILS CLOSED: on any host where
  AppContainer isolation cannot be established it raises
  ``IsolationRefused`` — it NEVER silently falls back to a plain
  subprocess.
- The only plain-subprocess path is the explicitly-named
  ``unsafe_dev`` mode, disabled by default, requiring BOTH the
  ``unsafe_dev=True`` argument AND the env flag
  ``AI_FACTORY_UNSAFE_DEV=1``, and it logs a warning + an audit
  entry on every run. It exists so the supervisor's platform-
  independent logic (timeout/kill, output cap, env scrubbing) can be
  tested on Linux. It is NOT a sandbox and must never be presented
  as one.
- No CLI command exposes arbitrary execution yet (LTP-029 step 2 is
  "prepare the executor", not "enable it"). The only callers are
  ``Control.execute_isolated`` (library) and the tests.

Network: denied by default. On Windows this comes from the
AppContainer profile having ZERO network capabilities (the WFP
default block filters drop the traffic); the worker is never merely
"instructed not to use" the network (LTP-029 §2).

Limits enforced by the supervisor (and, on Windows, by the Job
Object): timeout (hard kill), memory cap, CPU-time cap, active-
process cap (fork-bomb), output-size cap. ``KILL_ON_JOB_CLOSE``
reaps the whole tree when the supervisor dies.

Python stdlib only.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from . import config

logger = logging.getLogger("ai_factory.executor")

UNSAFE_DEV_ENV = "AI_FACTORY_UNSAFE_DEV"
MAX_PAYLOAD_BYTES = 64 * 1024          # 64 KiB of Python source
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_MEMORY_BYTES = 256 * 1024 * 1024   # 256 MiB
DEFAULT_MAX_CPU_SECONDS = 60
DEFAULT_MAX_PROCESSES = 8
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024          # 64 KiB captured stdout+stderr

# The worker's environment is built from scratch: only these variables
# survive, and none of them can carry a secret. Everything else —
# AI_FACTORY_API_KEY, database paths, user credentials — is dropped.
# On Windows the values are the system defaults; on the unsafe-dev
# path the values come from the supervisor, never from the caller.
_WORKER_ENV_WINDOWS = {
    "SystemRoot": r"C:\Windows",
    "SystemDrive": "C:",
    "TEMP": None,   # filled in: the run's work dir
    "TMP": None,    # filled in: the run's work dir
    "PATH": r"C:\Windows\system32;C:\Windows",
}
_WORKER_ENV_UNSAFE_DEV = {
    "PATH": "/usr/bin:/bin",
    "TEMP": None,
    "TMP": None,
    "HOME": None,   # filled in: the run's work dir (no real home)
}

# Names that must never appear in a worker environment, even by
# accident. Checked by _scrubbed_env and by the tests.
FORBIDDEN_ENV_PREFIXES = ("AI_FACTORY_", "AWS_", "GITHUB_")
FORBIDDEN_ENV_NAMES = frozenset({
    "API_KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL",
    "AI_FACTORY_DB", "AI_FACTORY_API_KEY",
})


class IsolationRefused(Exception):
    """Fail-closed refusal: isolation could not be established on this
    host, so the execution is refused. Never a fallback, never silent."""


@dataclasses.dataclass
class ExecutionLimits:
    """Per-execution limits. The timeout is additionally capped by the
    approved containment limit (MAX_EXECUTION_MINUTES = 5)."""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES
    max_cpu_seconds: int = DEFAULT_MAX_CPU_SECONDS
    max_processes: int = DEFAULT_MAX_PROCESSES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES

    def __post_init__(self):
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if self.timeout_seconds > config.MAX_EXECUTION_MINUTES * 60:
            raise ValueError(
                "timeout_seconds exceeds the containment cap of "
                f"{config.MAX_EXECUTION_MINUTES} minutes")
        if self.max_memory_bytes < 1024 * 1024:
            raise ValueError("max_memory_bytes must be >= 1 MiB")
        if self.max_cpu_seconds < 1:
            raise ValueError("max_cpu_seconds must be >= 1")
        if self.max_processes < 1:
            raise ValueError("max_processes must be >= 1")
        if self.max_output_bytes < 1024:
            raise ValueError("max_output_bytes must be >= 1 KiB")

    def charge_minutes(self) -> int:
        """Credits charged up front for the run: 1 per started minute
        of the timeout budget (same unit as execute_agent)."""
        return max(1, -(-self.timeout_seconds // 60))


@dataclasses.dataclass
class ExecutionResult:
    """What the supervisor returns. ``status`` is one of:
    finished / timeout / killed / limit_exceeded / failed / refused.
    ``stdout``/``stderr`` are truncated at the output cap."""
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    wall_seconds: float
    reason: str
    isolated: bool        # True only for a real AppContainer run
    unsafe_dev: bool      # True only for the explicitly unsafe dev path

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _validate_payload(payload: str) -> str:
    if not isinstance(payload, str) or not payload.strip():
        raise ValueError("payload must be a non-empty Python source string")
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    return payload


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    """Cap captured output. Returns (text, was_truncated)."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= cap:
        return text, False
    cut = encoded[:cap].decode("utf-8", errors="ignore")
    return cut + "\n[output truncated at cap]", True


def _scrubbed_env(base: dict, work_dir: str) -> dict:
    """Build the worker env from the allowlisted base. TEMP/TMP/HOME
    point at the run's work dir. Raises if a forbidden name sneaks in
    — defence in depth, the base is already built by hand."""
    env = {}
    for key, value in base.items():
        if value is None:
            value = work_dir
        if key in FORBIDDEN_ENV_NAMES or any(
                key.startswith(p) for p in FORBIDDEN_ENV_PREFIXES):
            raise ValueError(f"forbidden env var in worker env: {key}")
        env[key] = value
    return env


class ExecutionSupervisor:
    """Prepares and supervises one isolated run. Stays OUTSIDE the
    worker environment: it never hands the worker its own token,
    credentials, or the project directory.

    ``control`` is the AI Control object (duck-typed: only
    ``append_audit`` is used here; authorization + charging live in
    ``Control.execute_isolated``).
    """

    def __init__(self, control, work_root: str | None = None):
        self.control = control
        self.work_root = work_root or os.path.join(
            tempfile.gettempdir(), "ai-factory-executions")
        os.makedirs(self.work_root, exist_ok=True)
        self._launcher = None  # lazy: _WindowsIsolatedLauncher on win32

    # --- availability / fail-closed gate ---
    def available(self) -> tuple[bool, str]:
        """(True, reason) if a real isolated run is possible on this
        host, else (False, reason). On Linux this is always False —
        isolation is a Windows capability here."""
        if sys.platform == "win32":
            return self._windows_launcher().probe()
        return (False,
                "isolated execution requires Windows AppContainer/LPAC; "
                f"this host is {sys.platform} — execution refused")

    def check_available(self, unsafe_dev: bool = False) -> None:
        """Fail-closed gate. Raises IsolationRefused unless a real
        isolated run is possible, or the explicitly unsafe dev mode is
        enabled (argument AND env flag)."""
        ok, reason = self.available()
        if ok:
            return
        if unsafe_dev and os.environ.get(UNSAFE_DEV_ENV) == "1":
            return
        raise IsolationRefused(
            "isolated execution refused: " + reason +
            ("" if unsafe_dev else
             " (unsafe_dev mode is off; set AI_FACTORY_UNSAFE_DEV=1 AND "
             "pass unsafe_dev=True only for local dev testing — "
             "it is NOT a sandbox)"))

    def _windows_launcher(self):
        if self._launcher is None:
            self._launcher = _WindowsIsolatedLauncher()
        return self._launcher

    # --- one run ---
    def execute(self, agent_id: str, actor: str, payload: str,
                limits: ExecutionLimits | None = None,
                unsafe_dev: bool = False) -> ExecutionResult:
        """Run ``payload`` (Python source) in isolation. Authorization
        and charging are the caller's job (Control.execute_isolated);
        this method enforces the isolation gate, the limits, and the
        audit trail. Raises IsolationRefused before doing anything if
        isolation cannot be established."""
        limits = limits or ExecutionLimits()
        payload = _validate_payload(payload)
        self.check_available(unsafe_dev=unsafe_dev)
        # Mode is decided by availability, not by platform alone:
        # a real isolated run when the Windows APIs are present,
        # otherwise the explicitly unsafe dev path (check_available
        # already gated it: argument AND env flag).
        windows_ok, _ = self.available()
        use_unsafe = not windows_ok

        run_id = uuid.uuid4().hex[:12]
        work_dir = os.path.join(self.work_root, run_id)
        os.makedirs(work_dir)
        if os.name == "posix":
            os.chmod(work_dir, 0o700)
        task_path = os.path.join(work_dir, "task.py")
        with open(task_path, "w", encoding="utf-8") as fh:
            fh.write(payload)

        started = time.monotonic()
        try:
            if not use_unsafe:
                result = self._windows_launcher().launch(
                    run_id=run_id, work_dir=work_dir, task_path=task_path,
                    limits=limits)
            else:
                # unsafe_dev only: check_available already gated this.
                logger.warning(
                    "UNSAFE DEV execution (no isolation): agent=%s "
                    "actor=%s run=%s — NOT a sandbox, dev testing only",
                    agent_id, actor, run_id)
                result = self._run_unsafe_dev(
                    work_dir=work_dir, task_path=task_path, limits=limits)
            result.wall_seconds = time.monotonic() - started
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        self.control.append_audit(
            actor, "execute_isolated", agent_id,
            f"run {run_id}: status={result.status} "
            f"exit={result.exit_code} wall={result.wall_seconds:.2f}s "
            f"isolated={result.isolated} unsafe_dev={result.unsafe_dev} "
            f"reason={result.reason}")
        return result

    def _run_unsafe_dev(self, work_dir: str, task_path: str,
                        limits: ExecutionLimits) -> ExecutionResult:
        """Plain-subprocess runner. UNSAFE BY DESIGN: no AppContainer,
        no restricted token, no Job Object — the child runs with the
        supervisor's own rights and CAN read the supervisor's files.
        Exists only so supervisor logic (timeout/kill, output cap, env
        scrubbing) can be tested on Linux. Never the default, never a
        sandbox, never presented as one."""
        env = _scrubbed_env(_WORKER_ENV_UNSAFE_DEV, work_dir)
        try:
            proc = subprocess.Popen(
                [sys.executable, task_path],
                cwd=work_dir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace")
        except OSError as exc:
            return ExecutionResult(
                status="failed", exit_code=None, stdout="", stderr="",
                stdout_truncated=False, wall_seconds=0.0,
                reason=f"could not start worker: {exc}",
                isolated=False, unsafe_dev=True)
        try:
            stdout, stderr = proc.communicate(
                timeout=limits.timeout_seconds)
            status = "finished" if proc.returncode == 0 else "failed"
            reason = (f"worker exited with code {proc.returncode}"
                      if proc.returncode != 0 else "worker finished")
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = "", ""
            status, reason = "timeout", (
                f"worker killed after exceeding "
                f"{limits.timeout_seconds}s timeout")
            exit_code = None
        stdout, trunc_out = _truncate(stdout or "", limits.max_output_bytes)
        stderr, trunc_err = _truncate(stderr or "", limits.max_output_bytes)
        return ExecutionResult(
            status=status, exit_code=exit_code, stdout=stdout,
            stderr=stderr, stdout_truncated=trunc_out or trunc_err,
            wall_seconds=0.0, reason=reason,
            isolated=False, unsafe_dev=True)


class _WindowsIsolatedLauncher:
    """Windows-only launcher: AppContainer/LPAC + restricted token +
    Job Object, via ctypes (stdlib only).

    STATUS: NOT VALIDATED. Written from the documented Win32 APIs
    (see docs/windows-isolation-matrix.md); it has never run on
    Windows. The adversarial tests in tests/test_executor_phase2.py
    are the validation — they SKIP on Linux with "requires Windows
    AppContainer — not validated" and must pass on Lucas's real
    machine before any claim of isolation is made. Any failure inside
    this launcher surfaces as IsolationRefused (setup) or a "failed"
    result (after start) — never as a silent fallback.

    Steps (see matrix §4 + §"Recommended combination"):
    1. Create an AppContainer profile for this run (Low integrity,
       ZERO network capabilities — network denied by default).
    2. Grant exactly the run's work dir to the package SID (icacls).
    3. Build a restricted token (DISABLE_MAX_PRIVILEGE), pin Low IL,
       VERIFY the kernel honoured Low IL before launching.
    4. Create a Job Object: KILL_ON_JOB_CLOSE, memory/CPU/process caps.
    5. Launch suspended via CreateProcessAsUserW with the
       SECURITY_CAPABILITIES attribute, assign to the job, resume.
    6. Wait with timeout; TerminateJobObject on timeout; capture
       capped output; delete the profile afterwards.
    """

    def __init__(self):
        self._ctypes = None  # imported lazily, Windows only

    # --- probe: API presence only, no state change ---
    def probe(self) -> tuple[bool, str]:
        """Check the needed Win32 APIs exist. This probes presence
        only — it creates nothing and changes nothing. A True here
        means "the APIs exist", NOT "isolation is demonstrated". The
        adversarial tests are the demonstration."""
        if sys.platform != "win32":
            return (False, "not a Windows host")
        try:
            import ctypes
            from ctypes import wintypes
        except ImportError as exc:  # pragma: no cover - Windows-only
            return (False, f"ctypes unavailable: {exc}")
        needed = {
            "userenv.dll": ["CreateAppContainerProfile",
                            "DeriveAppContainerSidFromAppContainerName",
                            "DeleteAppContainerProfile"],
            "advapi32.dll": ["CreateRestrictedToken",
                             "SetTokenInformation", "GetTokenInformation",
                             "OpenProcessToken"],
            "kernel32.dll": ["CreateJobObjectW", "SetInformationJobObject",
                             "AssignProcessToJobObject", "TerminateJobObject",
                             "CreateProcessAsUserW",
                             "UpdateProcThreadAttribute",
                             "InitializeProcThreadAttributeList",
                             "GetCurrentProcess", "WaitForSingleObject",
                             "CloseHandle"],
        }
        missing = []
        for dll_name, funcs in needed.items():
            try:
                dll = ctypes.WinDLL(dll_name)
            except OSError:
                missing.append(f"{dll_name} (missing)")
                continue
            for fn in funcs:
                if not hasattr(dll, fn):
                    missing.append(f"{dll_name}!{fn}")
        if missing:
            return (False,
                    "missing Win32 APIs: " + ", ".join(missing))
        return (True, "Win32 isolation APIs present (presence only — "
                      "isolation NOT yet demonstrated on this machine)")

    # --- one launch (Windows only) ---
    def launch(self, run_id: str, work_dir: str, task_path: str,
               limits: ExecutionLimits) -> ExecutionResult:
        """Launch the worker inside the AppContainer. Raises
        IsolationRefused if any isolation step fails BEFORE the
        process starts; returns a 'failed' result if the worker dies
        abnormally after starting. NEVER falls back to a plain
        subprocess."""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        userenv = ctypes.WinDLL("userenv", use_last_error=True)
        self._bind_win32(kernel32, advapi32, userenv)

        profile_name = f"ai-factory-exec-{run_id}"
        started = time.monotonic()
        profile_created = False
        job = None
        process_handle = None
        try:
            sid = self._create_profile(
                userenv, profile_name)
            profile_created = True
            self._grant_work_dir(work_dir, sid)
            token = self._restricted_low_token(
                kernel32, advapi32)
            self._verify_low_integrity(advapi32, token)
            job = self._create_job(kernel32, limits)
            interpreter = self._resolve_interpreter()
            process_handle, stdout, stderr, exit_code, timed_out = (
                self._launch_in_container(
                    kernel32, advapi32, token, job, sid,
                    interpreter, task_path, work_dir, limits))
            wall = time.monotonic() - started
            if timed_out:
                status, reason = "timeout", (
                    f"worker killed after exceeding "
                    f"{limits.timeout_seconds}s timeout")
                exit_code = None
            elif exit_code == 0:
                status, reason = "finished", "worker finished"
            else:
                status, reason = "failed", (
                    f"worker exited with code {exit_code}")
            stdout, trunc_out = _truncate(
                stdout, limits.max_output_bytes)
            stderr, trunc_err = _truncate(
                stderr, limits.max_output_bytes)
            return ExecutionResult(
                status=status, exit_code=exit_code, stdout=stdout,
                stderr=stderr, stdout_truncated=trunc_out or trunc_err,
                wall_seconds=wall, reason=reason,
                isolated=True, unsafe_dev=False)
        except IsolationRefused:
            raise
        except Exception as exc:  # any setup failure = fail closed
            raise IsolationRefused(
                "Windows isolation setup failed — execution refused: "
                f"{exc}") from exc
        finally:
            if process_handle is not None:
                kernel32.CloseHandle(process_handle)
            if job is not None:
                kernel32.CloseHandle(job)
            if profile_created:
                try:
                    userenv.DeleteAppContainerProfile(profile_name)
                except Exception:
                    pass  # best effort; a stale profile is inert

    @staticmethod
    def _bind_win32(kernel32, advapi32, userenv) -> None:
        """Set argtypes/restype for every Win32 call used below.
        Without these, ctypes cannot pass strings/structs correctly
        and the calls would fail outright. This binding is part of the
        implementation — but it is still NOT VALIDATED on Windows
        (see the class docstring)."""
        import ctypes
        from ctypes import wintypes

        LPVOID = wintypes.LPVOID
        HANDLE = wintypes.HANDLE
        DWORD = wintypes.DWORD
        BOOL = wintypes.BOOL
        LPCWSTR = wintypes.LPCWSTR
        LPWSTR = wintypes.LPWSTR

        # userenv.dll
        userenv.CreateAppContainerProfile.argtypes = [
            LPCWSTR, LPCWSTR, LPCWSTR, LPVOID, DWORD,
            ctypes.POINTER(LPVOID)]
        userenv.CreateAppContainerProfile.restype = ctypes.HRESULT
        userenv.DeleteAppContainerProfile.argtypes = [LPCWSTR]
        userenv.DeleteAppContainerProfile.restype = ctypes.HRESULT
        # advapi32.dll
        advapi32.ConvertSidToStringSidW.argtypes = [
            LPVOID, ctypes.POINTER(LPWSTR)]
        advapi32.ConvertSidToStringSidW.restype = BOOL
        advapi32.ConvertStringSidToSidW.argtypes = [
            LPCWSTR, ctypes.POINTER(LPVOID)]
        advapi32.ConvertStringSidToSidW.restype = BOOL
        advapi32.OpenProcessToken.argtypes = [
            HANDLE, DWORD, ctypes.POINTER(HANDLE)]
        advapi32.OpenProcessToken.restype = BOOL
        advapi32.CreateRestrictedToken.argtypes = [
            HANDLE, DWORD, DWORD, LPVOID, DWORD, LPVOID, DWORD, LPVOID,
            ctypes.POINTER(HANDLE)]
        advapi32.CreateRestrictedToken.restype = BOOL
        advapi32.SetTokenInformation.argtypes = [
            HANDLE, DWORD, LPVOID, DWORD]
        advapi32.SetTokenInformation.restype = BOOL
        advapi32.GetTokenInformation.argtypes = [
            HANDLE, DWORD, LPVOID, DWORD, ctypes.POINTER(DWORD)]
        advapi32.GetTokenInformation.restype = BOOL
        advapi32.AllocateAndInitializeSid.argtypes = [
            LPVOID, ctypes.c_ubyte] + [DWORD] * 8 + [
            ctypes.POINTER(LPVOID)]
        advapi32.AllocateAndInitializeSid.restype = BOOL
        advapi32.FreeSid.argtypes = [LPVOID]
        advapi32.FreeSid.restype = LPVOID
        advapi32.CreateProcessAsUserW.argtypes = [
            HANDLE, LPCWSTR, LPWSTR, LPVOID, LPVOID, BOOL, DWORD,
            LPVOID, LPCWSTR, LPVOID, LPVOID]
        advapi32.CreateProcessAsUserW.restype = BOOL
        # kernel32.dll
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = HANDLE
        kernel32.CreateJobObjectW.argtypes = [LPVOID, LPCWSTR]
        kernel32.CreateJobObjectW.restype = HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            HANDLE, DWORD, LPVOID, DWORD]
        kernel32.SetInformationJobObject.restype = BOOL
        kernel32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
        kernel32.AssignProcessToJobObject.restype = BOOL
        kernel32.TerminateJobObject.argtypes = [HANDLE, DWORD]
        kernel32.TerminateJobObject.restype = BOOL
        kernel32.TerminateProcess.argtypes = [HANDLE, DWORD]
        kernel32.TerminateProcess.restype = BOOL
        kernel32.CreatePipe.argtypes = [
            ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE), LPVOID, DWORD]
        kernel32.CreatePipe.restype = BOOL
        kernel32.SetHandleInformation.argtypes = [HANDLE, DWORD, DWORD]
        kernel32.SetHandleInformation.restype = BOOL
        kernel32.InitializeProcThreadAttributeList.argtypes = [
            LPVOID, DWORD, DWORD, ctypes.POINTER(ctypes.c_size_t)]
        kernel32.InitializeProcThreadAttributeList.restype = BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [
            LPVOID, DWORD, ctypes.c_size_t, LPVOID, ctypes.c_size_t,
            LPVOID, ctypes.POINTER(ctypes.c_size_t)]
        kernel32.UpdateProcThreadAttribute.restype = BOOL
        kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
        kernel32.WaitForSingleObject.restype = DWORD
        kernel32.GetExitCodeProcess.argtypes = [
            HANDLE, ctypes.POINTER(DWORD)]
        kernel32.GetExitCodeProcess.restype = BOOL
        kernel32.ReadFile.argtypes = [
            HANDLE, LPVOID, DWORD, ctypes.POINTER(DWORD), LPVOID]
        kernel32.ReadFile.restype = BOOL
        kernel32.ResumeThread.argtypes = [HANDLE]
        kernel32.ResumeThread.restype = DWORD
        kernel32.CloseHandle.argtypes = [HANDLE]
        kernel32.CloseHandle.restype = BOOL
        kernel32.CreateFileW.argtypes = [
            LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE]
        kernel32.CreateFileW.restype = HANDLE

    # --- profile ---
    def _create_profile(self, userenv, profile_name: str) -> str:
        """Create the AppContainer profile, return its SID string.
        Zero capabilities: no network, no extra access."""
        import ctypes
        from ctypes import wintypes

        sid = wintypes.LPVOID()
        hr = userenv.CreateAppContainerProfile(
            profile_name, profile_name, None, None, 0,
            ctypes.byref(sid))
        if hr != 0:
            raise IsolationRefused(
                f"CreateAppContainerProfile failed (HRESULT {hr:#x}) — "
                "AppContainer not available on this machine")
        sid_str = wintypes.LPWSTR()
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        if not advapi32.ConvertSidToStringSidW(
                sid, ctypes.byref(sid_str)):
            raise IsolationRefused("ConvertSidToStringSidW failed")
        return sid_str.value

    def _grant_work_dir(self, work_dir: str, sid: str) -> None:
        """Grant exactly the run's work dir to the package SID.
        icacls ships with Windows; the grant is Modify on the dir and
        its children, nothing else."""
        result = subprocess.run(
            ["icacls", work_dir, "/grant", f"*{sid}:(OI)(CI)M",
             "/T", "/Q"],
            capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise IsolationRefused(
                "icacls grant of the work dir to the AppContainer SID "
                f"failed: {result.stderr.strip() or result.stdout.strip()}")

    # --- restricted token, Low integrity, verified ---
    def _restricted_low_token(self, kernel32, advapi32):
        """Restricted token from the current process token:
        DISABLE_MAX_PRIVILEGE, then pin integrity to Low
        (S-1-16-4096), built with AllocateAndInitializeSid using the
        mandatory-label authority {0,0,0,0,0,16} and sub-authority
        SECURITY_MANDATORY_LOW_RID (0x1000)."""
        import ctypes
        from ctypes import wintypes

        DISABLE_MAX_PRIVILEGE = 0x1
        TOKEN_ALL_ACCESS = 0xF01FF
        TokenIntegrityLevel = 25

        current = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(), TOKEN_ALL_ACCESS,
                ctypes.byref(current)):
            raise IsolationRefused("OpenProcessToken failed")
        new_token = wintypes.HANDLE()
        ok = advapi32.CreateRestrictedToken(
            current, DISABLE_MAX_PRIVILEGE, 0, None, 0, None, 0, None,
            ctypes.byref(new_token))
        kernel32.CloseHandle(current)
        if not ok:
            raise IsolationRefused("CreateRestrictedToken failed")
        # Build the Low integrity SID: authority {0,0,0,0,0,16},
        # one sub-authority 0x1000.
        class SID_IDENTIFIER_AUTHORITY(ctypes.Structure):
            _fields_ = [("Value", ctypes.c_ubyte * 6)]

        authority = SID_IDENTIFIER_AUTHORITY()
        authority.Value[5] = 16
        low_sid = wintypes.LPVOID()
        if not advapi32.AllocateAndInitializeSid(
                ctypes.byref(authority), 1, 0x1000, 0, 0, 0, 0, 0, 0, 0,
                ctypes.byref(low_sid)):
            kernel32.CloseHandle(new_token)
            raise IsolationRefused(
                "AllocateAndInitializeSid(Low integrity) failed")
        try:
            # TOKEN_MANDATORY_LABEL: Label.Sid + Label.Attributes
            # (SE_GROUP_INTEGRITY = 0x20).
            class SID_AND_ATTRIBUTES(ctypes.Structure):
                _fields_ = [("Sid", wintypes.LPVOID),
                            ("Attributes", wintypes.DWORD)]

            class TOKEN_MANDATORY_LABEL(ctypes.Structure):
                _fields_ = [("Label", SID_AND_ATTRIBUTES)]

            label = TOKEN_MANDATORY_LABEL()
            label.Label.Sid = low_sid
            label.Label.Attributes = 0x20
            if not advapi32.SetTokenInformation(
                    new_token, TokenIntegrityLevel,
                    ctypes.byref(label), ctypes.sizeof(label)):
                kernel32.CloseHandle(new_token)
                raise IsolationRefused(
                    "SetTokenInformation(Low integrity) failed")
        finally:
            advapi32.FreeSid(low_sid)
        return new_token

    def _verify_low_integrity(self, advapi32, token) -> None:
        """Read the token's integrity level back from the kernel and
        assert it is Low (S-1-16-4096). The supervisor does not trust
        that the request was honoured — it verifies.

        TOKEN_MANDATORY_LABEL layout: first 8 bytes (64-bit) are the
        Sid POINTER, then Attributes. The SID itself lives at that
        pointer: revision (1 byte), sub-authority count (1 byte),
        authority (6 bytes), then sub-authorities (4 bytes each,
        little-endian). An integrity SID has exactly 1 sub-authority.
        """
        import ctypes
        from ctypes import wintypes

        TokenIntegrityLevel = 25
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(
            token, TokenIntegrityLevel, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            raise IsolationRefused(
                "GetTokenInformation(integrity) returned no size")
        buf = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
                token, TokenIntegrityLevel, buf, needed,
                ctypes.byref(needed)):
            raise IsolationRefused(
                "GetTokenInformation(integrity) failed")
        # Sid pointer = first pointer-sized field of the label struct.
        sid_addr = ctypes.cast(
            buf, ctypes.POINTER(wintypes.c_void_p)).contents.value
        if not sid_addr:
            raise IsolationRefused("token integrity SID is null")
        count = ctypes.cast(
            sid_addr, ctypes.POINTER(ctypes.c_ubyte))[1]
        if count != 1:
            raise IsolationRefused(
                "token integrity SID has unexpected shape")
        rid = ctypes.cast(
            sid_addr + 8, ctypes.POINTER(wintypes.DWORD)).contents.value
        if rid != 4096:  # SECURITY_MANDATORY_LOW_RID
            raise IsolationRefused(
                f"token integrity is RID {rid}, not Low (4096) — "
                "the kernel did not honour the Low integrity request")

    # --- Job Object ---
    def _create_job(self, kernel32, limits: ExecutionLimits):
        """Job Object with KILL_ON_JOB_CLOSE + memory/CPU/process caps."""
        import ctypes
        from ctypes import wintypes

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
        JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
        JobObjectExtendedLimitInformation = 9

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation",
                 JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise IsolationRefused("CreateJobObjectW failed")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        # 100ns units: 1s = 10_000_000.
        info.BasicLimitInformation.PerJobUserTimeLimit = (
            limits.max_cpu_seconds * 10_000_000)
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_JOB_TIME)
        info.BasicLimitInformation.ActiveProcessLimit = limits.max_processes
        info.ProcessMemoryLimit = limits.max_memory_bytes
        info.JobMemoryLimit = limits.max_memory_bytes
        if not kernel32.SetInformationJobObject(
                job, JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            raise IsolationRefused("SetInformationJobObject failed")
        return job

    # --- interpreter ---
    @staticmethod
    def _resolve_interpreter() -> str:
        """Which Python runs inside the container. UNVERIFIED: the
        interpreter path must be readable by the AppContainer — a
        user-profile python.exe is NOT readable by default, so the
        system launcher (C:\\Windows\\py.exe) is preferred. The
        diagnostic + adversarial tests must confirm this on the real
        machine; override with AI_FACTORY_WORKER_PYTHON if needed."""
        override = os.environ.get("AI_FACTORY_WORKER_PYTHON")
        if override:
            return override
        for candidate in (r"C:\Windows\py.exe", "py.exe"):
            if os.path.exists(candidate) or candidate == "py.exe":
                return candidate
        return sys.executable

    # --- launch inside the container ---
    def _launch_in_container(self, kernel32, advapi32, token, job, sid,
                             interpreter: str, task_path: str,
                             work_dir: str,
                             limits: ExecutionLimits):
        """Create the process as the restricted token inside the
        AppContainer, suspended; assign to the job; resume; wait with
        timeout; capture output (capped by the caller).

        Returns (process_handle, stdout, stderr, exit_code, timed_out).
        Raises IsolationRefused on setup failure.
        """
        import ctypes
        from ctypes import wintypes

        # Anonymous pipes for stdout/stderr, inheritable by the child.
        class SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD),
                        ("lpSecurityDescriptor", wintypes.LPVOID),
                        ("bInheritHandle", wintypes.BOOL)]

        sa = SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(sa)
        sa.bInheritHandle = True
        out_r, out_w = wintypes.HANDLE(), wintypes.HANDLE()
        err_r, err_w = wintypes.HANDLE(), wintypes.HANDLE()
        if not kernel32.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w),
                                   ctypes.byref(sa), 0):
            raise IsolationRefused("CreatePipe(stdout) failed")
        if not kernel32.CreatePipe(ctypes.byref(err_r), ctypes.byref(err_w),
                                   ctypes.byref(sa), 0):
            raise IsolationRefused("CreatePipe(stderr) failed")
        # The child must inherit the WRITE ends (its stdout/stderr).
        # The READ ends stay with the parent: make them
        # non-inheritable, otherwise the child holds a read end open
        # and the parent can never see EOF on the pipes.
        kernel32.SetHandleInformation(out_r, 0x00000001, 0)
        kernel32.SetHandleInformation(err_r, 0x00000001, 0)

        # SECURITY_CAPABILITIES: AppContainerSid + zero capabilities.
        class SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", wintypes.LPVOID),
                        ("Attributes", wintypes.DWORD)]

        class SECURITY_CAPABILITIES(ctypes.Structure):
            _fields_ = [("AppContainerSid", wintypes.LPVOID),
                        ("Capabilities", wintypes.LPVOID),
                        ("CapabilityCount", wintypes.DWORD),
                        ("Reserved", wintypes.DWORD)]

        app_sid = wintypes.LPVOID()
        if not advapi32.ConvertStringSidToSidW(
                sid, ctypes.byref(app_sid)):
            raise IsolationRefused("ConvertStringSidToSidW failed")
        caps = SECURITY_CAPABILITIES()
        caps.AppContainerSid = app_sid
        caps.Capabilities = None
        caps.CapabilityCount = 0  # ZERO capabilities = no network

        PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
        EXTENDED_STARTUPINFO_PRESENT = 0x00080000
        CREATE_SUSPENDED = 0x00000004
        CREATE_NO_WINDOW = 0x08000000
        CREATE_UNICODE_ENVIRONMENT = 0x00000400
        STARTF_USESTDHANDLES = 0x00000100

        class STARTUPINFO(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", wintypes.LPVOID),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            ]

        class STARTUPINFOEX(ctypes.Structure):
            _fields_ = [("StartupInfo", STARTUPINFO),
                        ("lpAttributeList", wintypes.LPVOID)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", wintypes.HANDLE),
                        ("hThread", wintypes.HANDLE),
                        ("dwProcessId", wintypes.DWORD),
                        ("dwThreadId", wintypes.DWORD)]

        # Attribute list holding the security capabilities.
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0,
                                                   ctypes.byref(size))
        attr_list = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(
                attr_list, 1, 0, ctypes.byref(size)):
            raise IsolationRefused(
                "InitializeProcThreadAttributeList failed")
        if not kernel32.UpdateProcThreadAttribute(
                attr_list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(caps), ctypes.sizeof(caps), None, None):
            raise IsolationRefused(
                "UpdateProcThreadAttribute(SECURITY_CAPABILITIES) failed")

        env = _scrubbed_env(_WORKER_ENV_WINDOWS, work_dir)
        env_block = "\x00".join(
            f"{k}={v}" for k, v in env.items()) + "\x00\x00"
        env_block = ctypes.create_unicode_buffer(env_block)

        si = STARTUPINFOEX()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        # stdin: NUL, so the worker cannot read the supervisor's stdin.
        nul = kernel32.CreateFileW(
            "NUL", 0x80000000, 0x00000003, None, 3, 0, None)
        if not nul or nul == wintypes.HANDLE(-1).value:
            raise IsolationRefused("CreateFileW(NUL) failed")
        si.StartupInfo.hStdInput = nul
        si.StartupInfo.hStdOutput = out_w
        si.StartupInfo.hStdError = err_w
        si.lpAttributeList = attr_list

        pi = PROCESS_INFORMATION()
        # CreateProcess may modify the command line: pass a writable
        # buffer, never a string literal.
        cmdline = ctypes.create_unicode_buffer(
            f'"{interpreter}" "{task_path}"')
        ok = advapi32.CreateProcessAsUserW(
            token, None, cmdline, None, None, True,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED
            | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
            env_block, work_dir,
            ctypes.byref(si), ctypes.byref(pi))
        kernel32.CloseHandle(nul)
        kernel32.CloseHandle(out_w)
        kernel32.CloseHandle(err_w)
        if not ok:
            raise IsolationRefused(
                "CreateProcessAsUserW (AppContainer) failed — the "
                "worker could not be started inside the container")
        try:
            if not kernel32.AssignProcessToJobObject(job, pi.hProcess):
                kernel32.TerminateProcess(pi.hProcess, 1)
                raise IsolationRefused(
                    "AssignProcessToJobObject failed — worker not in job")
            kernel32.ResumeThread(pi.hThread)
            kernel32.CloseHandle(pi.hThread)
            waited = kernel32.WaitForSingleObject(
                pi.hProcess, limits.timeout_seconds * 1000)
            WAIT_OBJECT_0 = 0x00000000
            WAIT_TIMEOUT = 0x00000102
            timed_out = (waited == WAIT_TIMEOUT)
            if timed_out:
                kernel32.TerminateJobObject(job, 1)
                kernel32.WaitForSingleObject(pi.hProcess, 5000)
            exit_code = wintypes.DWORD(0)
            kernel32.GetExitCodeProcess(pi.hProcess,
                                        ctypes.byref(exit_code))
            stdout = self._read_pipe(kernel32, out_r)
            stderr = self._read_pipe(kernel32, err_r)
            return (pi.hProcess, stdout, stderr,
                    int(exit_code.value), timed_out)
        except IsolationRefused:
            kernel32.TerminateProcess(pi.hProcess, 1)
            raise
        finally:
            kernel32.CloseHandle(out_r)
            kernel32.CloseHandle(err_r)

    @staticmethod
    def _read_pipe(kernel32, handle) -> str:
        """Drain a pipe until EOF. Bounded: the caller truncates at
        the output cap; the pipe itself is read in chunks."""
        import ctypes
        from ctypes import wintypes

        chunks = []
        total = 0
        buf = ctypes.create_string_buffer(65536)
        while True:
            read = wintypes.DWORD(0)
            ok = kernel32.ReadFile(handle, buf, 65536,
                                   ctypes.byref(read), None)
            if not ok or read.value == 0:
                break
            chunks.append(buf.raw[:read.value])
            total += read.value
            if total > 16 * 1024 * 1024:  # hard safety stop: 16 MiB
                break
        return b"".join(chunks).decode("utf-8", errors="replace")
