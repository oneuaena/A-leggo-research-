"""Pinned, bounded Lean kernel checks for explicit research formal bindings.

This adapter accepts only theorem statements and tactic proofs. It constructs the
Lean file itself, pins the existing Lake/Mathlib workspace, applies a timeout and
output cap, and never accepts a caller-supplied path or command.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
import time
from typing import Any


# Version pinning lives in lean_bundle (single source of truth); re-exported
# here so every existing import keeps working.
from aleggo.study.lean_bundle import LEAN_TOOLCHAIN as PINNED_LEAN
from aleggo.study.lean_bundle import MATHLIB_REV as PINNED_MATHLIB
MAX_PROOF_BYTES = 40_000
MAX_CHECK_OUTPUT = 4 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 180

_FORBIDDEN = re.compile(
    r"(?im)(?:\b(?:sorry|sorryAx|admit|axiom|constant|unsafe|partial|native_decide|"
    r"run_tac|elab|macro|syntax|extern|implemented_by|include_str|evalConst|"
    r"addDecl|addAndCompile|IO|process|spawn|nativeCompile)\b|"
    r"#\s*(?:eval|run|guard_msgs|check|print|synth|compile|reduce|kernel|exit)\b|"
    r"\b(?:import|open|namespace|end|theorem|lemma|def|opaque|instance|structure|class)\b)"
)
_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


class LeanConfigurationError(RuntimeError):
    """The pinned Lean toolchain or prebuilt Mathlib workspace is unavailable."""


class UnsafeLeanInput(ValueError):
    """The supplied text contains a forbidden command or unsafe construct."""


@dataclass(frozen=True)
class LeanWorkspace:
    root: Path
    lake: Path
    lean_version: str


def tools_home() -> Path:
    """Shared, per-installation directory for large optional research tools.

    Lean/Mathlib needs several gigabytes, so one managed copy serves every
    account of a deployment instead of living in each user's workspace.
    """
    configured = os.environ.get("ALEGGO_TOOLS_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        from aleggo.runtime.home import get_runtime_home

        return get_runtime_home() / "data" / "tools"
    except Exception:  # pragma: no cover - runtime home is always importable in the app
        return Path.home() / ".aleggo" / "tools"


def managed_elan_home() -> Path:
    return tools_home() / "elan"


def managed_workspace() -> Path:
    return tools_home() / "lean4-project"


def _candidate_workspaces() -> list[Path]:
    candidates = [
        os.environ.get("ALEGGO_FORMAL_WORKSPACE", ""),
        os.environ.get("MRA_FORMAL_WORKSPACE", ""),
        str(managed_workspace()),
        r"D:\Math Research Agent\formal-workspace\lean4-project",
        str(Path.home() / "Math Research Agent" / "formal-workspace" / "lean4-project"),
    ]
    return [Path(item).expanduser() for item in candidates if item]


def _pinned_workspace(root: Path) -> bool:
    toolchain_file = root / "lean-toolchain"
    lakefile = root / "lakefile.toml"
    manifest = root / "lake-manifest.json"
    olean = root / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean"
    if not all(path.is_file() for path in (toolchain_file, lakefile, manifest, olean)):
        return False
    if toolchain_file.read_text(encoding="utf-8").strip() != PINNED_LEAN:
        return False
    try:
        import tomllib

        config = tomllib.loads(lakefile.read_text(encoding="utf-8"))
        requirements = config.get("require", [])
        mathlib = [item for item in requirements if item.get("name") == "mathlib"]
        if len(mathlib) != 1 or mathlib[0].get("rev") != PINNED_MATHLIB:
            return False
        if mathlib[0].get("git") != "https://github.com/leanprover-community/mathlib4.git":
            return False
        lock = json.loads(manifest.read_text(encoding="utf-8"))
        packages = [item for item in lock.get("packages", []) if item.get("name") == "mathlib"]
        if len(packages) != 1 or packages[0].get("inputRev") != PINNED_MATHLIB:
            return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return True


def _elan_bin_candidates() -> list[Path]:
    homes = [os.environ.get("ELAN_HOME", ""), str(managed_elan_home()), str(Path.home() / ".elan")]
    return [Path(item).expanduser() / "bin" for item in homes if item]


def _lake_executable() -> Path | None:
    configured = os.environ.get("ALEGGO_LAKE_PATH")
    candidates = [Path(configured)] if configured else []
    # elan installs ``lake.exe`` on Windows and ``lake`` on Linux/macOS.
    names = ("lake.exe", "lake") if os.name == "nt" else ("lake", "lake.exe")
    for directory in _elan_bin_candidates():
        candidates.extend(directory / name for name in names)
    found = shutil.which("lake")
    if found:
        candidates.append(Path(found))
    return next((item.resolve() for item in candidates if item.is_file()), None)


def detect_lean_workspace() -> LeanWorkspace:
    lake = _lake_executable()
    if lake is None:
        raise LeanConfigurationError(
            "Lean 不可用：请在研究工具中「安装 Lean + Mathlib」，或安装 elan 并配置 ALEGGO_LAKE_PATH。"
        )
    for candidate in _candidate_workspaces():
        try:
            root = candidate.resolve(strict=True)
        except OSError:
            continue
        if root.is_dir() and _pinned_workspace(root):
            return LeanWorkspace(root=root, lake=lake, lean_version=PINNED_LEAN)
    raise LeanConfigurationError(
        "已检测到 Lean，但未找到锁定的 Lean 4.32.0 / Mathlib 4.32.0 构建。"
        "请在研究工具中「安装 Lean + Mathlib」，或将 ALEGGO_FORMAL_WORKSPACE 指向带预构建 Mathlib 缓存的可信工作区。"
    )


def _normalize_statement(value: str) -> str:
    # Normalize only line endings. Whitespace inside a Lean string literal can
    # affect theorem meaning, so the formalization binding must remain exact.
    return str(value).strip().replace("\r\n", "\n").replace("\r", "\n")


def _validate(statement: str, proof: str) -> tuple[str, str]:
    statement = str(statement).strip()
    proof = str(proof).strip()
    if not statement or len(statement.encode("utf-8")) > MAX_PROOF_BYTES:
        raise ValueError("Lean 命题须为 1 到 40,000 字节（UTF-8）。")
    if not proof or len(proof.encode("utf-8")) > MAX_PROOF_BYTES:
        raise ValueError("Lean 证明须为 1 到 40,000 字节（UTF-8）。")
    if "\x00" in statement or "\x00" in proof:
        raise UnsafeLeanInput("Lean 输入不允许包含 NUL 字节。")
    # Statements are a single Lean term; proof tactics are indented into a
    # generated theorem body. This prevents a caller from injecting imports or
    # additional top-level declarations into the generated file.
    if "\n" in statement or "\r" in statement or ";" in statement:
        raise UnsafeLeanInput("请将 Lean 命题写成单一表达式，不要使用命令分隔符。")
    if _FORBIDDEN.search(statement) or _FORBIDDEN.search(proof):
        raise UnsafeLeanInput("Lean 输入包含不允许的命令或不安全构造。")
    return statement, proof


def binding_hash(original_statement: str, formal_ir: str, lean_statement: str) -> tuple[str, str, str, str]:
    original = _normalize_statement(original_statement)
    ir = _normalize_statement(formal_ir)
    lean = _normalize_statement(lean_statement)
    if not original or not ir or not lean:
        raise ValueError("需要同时提供原始命题、形式化 IR 与 Lean 命题。")
    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    binding = hashlib.sha256("\n".join((original, ir, lean)).encode("utf-8")).hexdigest()
    return digest(original), digest(ir), digest(lean), binding


def proof_digest(proof: str) -> str:
    return hashlib.sha256(proof.encode("utf-8")).hexdigest()


def _clean_environment(lake: Path) -> dict[str, str]:
    allowed = {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "USERPROFILE", "HOME", "APPDATA",
        "LOCALAPPDATA", "TEMP", "TMP", "ELAN_HOME", "LANG", "LC_ALL",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    elan_bin = str(lake.parent)
    env["PATH"] = elan_bin + os.pathsep + os.environ.get("PATH", "")
    env.setdefault("ELAN_HOME", str(lake.parent.parent))
    return env


async def _kill_process_tree(process: "asyncio.subprocess.Process") -> None:
    """Kill a spawned Lean check and every process it started.

    ``lake env lean <file>`` spawns the real ``lean`` elaboration as a child
    of the ``lake`` wrapper, so killing only the direct child orphans the
    elaboration: a hung check would keep burning CPU forever after the timeout.
    Best effort -- never raises.
    """
    pid = getattr(process, "pid", None)
    if pid:
        if os.name == "posix":
            try:
                # Spawned with start_new_session=True, so the child's PID is
                # its process-group ID: one kill takes wrapper + elaboration.
                os.killpg(pid, signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
        else:
            # Windows: TerminateProcess only kills the direct child; ask
            # taskkill to take the tree too.
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/T", "/F", "/PID", str(pid),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(killer.wait(), timeout=15)
                if killer.returncode == 0:
                    return
            except Exception:
                pass
    try:
        process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


async def _drain_stdout(process: "asyncio.subprocess.Process", output: bytearray) -> bool:
    """Read process stdout until EOF. Returns True when the output cap was hit."""
    assert process.stdout is not None
    while True:
        chunk = await process.stdout.read(16 * 1024)
        if not chunk:
            return False
        remaining = MAX_CHECK_OUTPUT - len(output)
        if len(chunk) > remaining:
            output.extend(chunk[: max(0, remaining)])
            return True
        output.extend(chunk)


async def _guarded_wait(
    process: "asyncio.subprocess.Process", output: bytearray, timeout: float
) -> tuple[bool, bool]:
    """Wait for *process* while draining its stdout.

    Returns ``(timed_out, output_limited)``.  On timeout, output-cap breach,
    drain failure or cancellation the whole process *tree* is killed, so a
    hung Lean elaboration can never leak CPU-burning strays.  Cancellation is
    re-raised after cleanup.
    """
    reader = asyncio.create_task(_drain_stdout(process, output))
    timed_out = False
    output_limited = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
        output_limited = await reader
    except (TimeoutError, asyncio.TimeoutError):
        timed_out = True
    except (asyncio.CancelledError, Exception):
        await _kill_process_tree(process)
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        raise
    if timed_out or output_limited:
        await _kill_process_tree(process)
        try:
            await process.wait()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        await reader
    return timed_out, output_limited


async def run_kernel_check(
    project_id: str,
    workspace_root: Path,
    lean_statement: str,
    proof: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not _PROJECT_ID.fullmatch(project_id):
        raise ValueError("研究项目标识无效。")
    statement, proof = _validate(lean_statement, proof)
    workspace = detect_lean_workspace()
    timeout = max(1.0, min(float(timeout_seconds), DEFAULT_TIMEOUT_SECONDS))
    target_dir = (workspace_root / "research-projects" / project_id / "formal-checks").resolve()
    managed_root = (workspace_root / "research-projects" / project_id).resolve()
    if not target_dir.is_relative_to(managed_root):
        raise ValueError("形式化检查路径超出项目工作区。")
    target_dir.mkdir(parents=True, exist_ok=True)
    source = "import Mathlib\n\nexample : " + statement + " := by\n"
    source += "\n".join("  " + line if line.strip() else "" for line in proof.splitlines())
    source += "\n"
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".lean", prefix="check-", dir=target_dir, delete=False
    )
    source_path = Path(handle.name)
    try:
        with handle:
            handle.write(source)
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            str(workspace.lake), "env", "lean", str(source_path),
            cwd=workspace.root,
            env=_clean_environment(workspace.lake),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Own process group: _kill_process_tree can then take the whole
            # tree (lake wrapper + lean elaboration) with one killpg.
            start_new_session=True,
        )
        output = bytearray()
        timed_out, output_limited = await _guarded_wait(process, output, timeout)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        text = output.decode("utf-8", errors="replace")
        passed = process.returncode == 0 and not timed_out and not output_limited
        return {
            "status": "KERNEL_CERTIFIED" if passed else "TIMEOUT" if timed_out else "OUTPUT_LIMIT" if output_limited else "CHECK_FAILED",
            "passed": passed,
            "exitCode": process.returncode,
            "elapsedMs": elapsed_ms,
            "leanVersion": workspace.lean_version,
            "output": text[-MAX_CHECK_OUTPUT:],
            "proofHash": proof_digest(proof),
        }
    finally:
        source_path.unlink(missing_ok=True)
