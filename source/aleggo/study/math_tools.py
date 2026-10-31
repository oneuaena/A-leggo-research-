"""Bounded bridge to the hardened mathematical worker from MRA 2.1.0."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import re
import stat
import sys
from typing import Any


from aleggo.study.research_tool_catalog import ALL_TOOLS, CAS_TOOLS, DATABASE_TOOLS

ALLOWED_MATH_TOOLS = set(ALL_TOOLS)
MAX_INPUT_CHARACTERS = 250_000
MAX_TIMEOUT_SECONDS = 120
PROJECT_ID_RE = re.compile(r"^(?:research_[0-9a-f]{32}|mra_[0-9a-f]{20})$")
MAX_GENERATED_FILE_BYTES = 128 * 1024 * 1024
MAX_GENERATED_TOTAL_BYTES = 512 * 1024 * 1024
MAX_GENERATED_FILES = 2000
# Sub-workspaces with their own run records (discussion tool calls, formal
# checks, imported material) are not attributed to a project-level step run.
_SEPARATE_RUN_DIRS = frozenset({"discussions", "formal-checks", "imports", ".cas-scratch"})
_SUBDIRECTORY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}(?:/[A-Za-z0-9_-]{1,80}){0,2}$")


def _workspace_files(workspace: Path) -> dict[str, tuple[Path, int, int]]:
    """Inventory regular files only, without following links outside the run."""
    files = {}
    for directory, folders, names in os.walk(workspace, followlinks=False):
        if Path(directory) == workspace:
            folders[:] = [name for name in folders if name not in _SEPARATE_RUN_DIRS]
        _reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()
                      and not bool(getattr((Path(directory) / name).lstat(), "st_file_attributes", 0) & _reparse)
                      and (Path(directory) / name).resolve().is_relative_to(workspace)]
        for name in names:
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(workspace):
                continue
            file_stat = path.stat()
            files[path.relative_to(workspace).as_posix()] = (path, file_stat.st_size, file_stat.st_mtime_ns)
    return files


def _generated_files(workspace: Path, before: dict) -> tuple[list[dict], list[str]]:
    """Keep exact bytes of tool-created outputs in the durable run record."""
    records, warnings = [], []
    total = 0
    for name, (path, size, modified) in sorted(_workspace_files(workspace).items()):
        prior = before.get(name)
        if prior and (size, modified) == prior[1:]:
            continue
        if len(records) >= MAX_GENERATED_FILES or size > MAX_GENERATED_FILE_BYTES or total + size > MAX_GENERATED_TOTAL_BYTES:
            warnings.append(f"Generated file {name!r} exceeds the recorded-output limits.")
            continue
        try:
            content = path.read_bytes()
        except OSError:
            warnings.append(f"Generated file {name!r} could not be read after execution.")
            continue
        if len(content) > MAX_GENERATED_FILE_BYTES or total + len(content) > MAX_GENERATED_TOTAL_BYTES:
            warnings.append(f"Generated file {name!r} exceeds the recorded-output limits.")
            continue
        total += len(content)
        records.append({"relativeName": name, "encoding": "base64", "data": base64.b64encode(content).decode("ascii"),
                        "sha256": hashlib.sha256(content).hexdigest(), "sizeBytes": len(content), "evidenceClass": "ACTUAL_TOOL_GENERATED"})
    return records, warnings


class _GeneratedFiles:
    """Generated-file capture shared with the external CAS bridge."""

    @staticmethod
    def snapshot(workspace: Path) -> dict:
        return _workspace_files(workspace)

    @staticmethod
    def collect(workspace: Path, before: dict) -> tuple[list[dict], list[str]]:
        return _generated_files(workspace, before)


def _worker_env(workspace: Path | None = None, data_root: Path | None = None) -> dict[str, str]:
    # The worker needs a minimal Windows process environment for CPython and
    # native math wheels. It deliberately receives no credentials or app key.
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    if getattr(sys, "frozen", False):
        # PyInstaller runtime hooks (including NLTK's downloader initialization)
        # need an existing home even before the mathematical entry point runs.
        # Use this run's directory, without forwarding the user's home or keys.
        if workspace is not None:
            env["HOME"] = env["USERPROFILE"] = str(workspace)
        # Preserve only bootloader state, so this bounded worker reuses the
        # parent's extracted bundle instead of unpacking another full sidecar.
        # Provider/runtime settings remain excluded from the child environment.
        for key in ("_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL", "_PYI_APPLICATION_HOME_DIR",
                    "_PYI_LINUX_PROCESS_NAME", "_PYI_SPLASH_IPC", "_MEIPASS2", "LD_LIBRARY_PATH", "LIBPATH"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
    )
    if workspace is not None:
        # Matplotlib/font caches and Python temp files go to a scratch area next
        # to (not inside) the run workspace, so they are writable by the
        # isolated worker without polluting the recorded generated files.
        projects = data_root / "research-projects" if data_root is not None else workspace.parent
        scratch = projects / ".worker-scratch" / workspace.name
        cache = projects / ".mplconfig"
        for directory in (scratch, cache):
            directory.mkdir(parents=True, exist_ok=True)
        env.update({"MPLCONFIGDIR": str(cache), "MPLBACKEND": "Agg", "ALEGGO_WORKER_SCRATCH": str(scratch),
                    "TMPDIR": str(scratch), "TEMP": str(scratch), "TMP": str(scratch)})
        # The application's private data root (other projects, settings, the
        # research database) and the user's real home are read-protected inside
        # the worker; only this project's workspace remains accessible.
        protected = [str(data_root if data_root is not None else workspace.parent.parent)]
        try:
            protected.append(str(Path.home()))
        except (RuntimeError, OSError):
            pass
        env["ALEGGO_PROTECTED_ROOTS"] = os.pathsep.join(protected)
    return env


def _worker_command(worker: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        # sys.executable is the application bootloader in a frozen install,
        # rather than a Python interpreter accepting -I/-B/script arguments.
        return [sys.executable, "--aleggo-math-worker"]
    return [sys.executable, "-I", "-B", "-X", "utf8", str(worker)]


async def run_math_tool(
    *,
    workspace_root: Path,
    project_id: str,
    name: str,
    input_data: dict[str, Any],
    timeout_seconds: int = 30,
    subdirectory: str = "",
) -> dict[str, Any]:
    """Run one allow-listed research tool in a project-local workspace.

    Worker tools run in the isolated Python worker; ``run_pari``/``run_gap``/
    ``run_sage``/``run_macaulay2`` run in an installed external CAS; database
    tools are read-only lookups.  ``subdirectory`` gives a run family (for
    example one discussion) its own working directory and file attribution.
    """
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError("Invalid research project identifier.")
    if name not in ALLOWED_MATH_TOOLS:
        raise ValueError("This mathematical tool is not enabled in the A-leggo bridge.")
    request_text = json.dumps({"name": name, "input": input_data}, ensure_ascii=False)
    if len(request_text) > MAX_INPUT_CHARACTERS:
        raise ValueError("Mathematical tool input is too large.")
    if subdirectory and not _SUBDIRECTORY_RE.fullmatch(subdirectory):
        raise ValueError("Invalid tool working directory.")
    timeout = max(1, min(MAX_TIMEOUT_SECONDS, int(timeout_seconds)))
    project_workspace = (workspace_root / "research-projects" / project_id).resolve()
    workspace = (project_workspace / subdirectory).resolve() if subdirectory else project_workspace
    if not workspace.is_relative_to(project_workspace):
        raise ValueError("Invalid tool working directory.")
    workspace.mkdir(parents=True, exist_ok=True)
    if name in DATABASE_TOOLS:
        from aleggo.study.math_databases import run_database_tool

        return await run_database_tool(name, input_data, timeout_seconds=max(timeout, 20))
    if name in CAS_TOOLS:
        from aleggo.study.external_cas import run_cas_tool

        return await run_cas_tool(workspace_root=workspace_root, project_id=project_id, name=name, input_data=input_data,
                                  timeout_seconds=max(timeout, 60), workspace=workspace, generated=_GeneratedFiles)
    worker = Path(__file__).with_name("math_worker.py")
    if not worker.is_file():
        return {
            "ok": False,
            "output": "",
            "error": "The mathematical worker is missing from this installation.",
            "error_type": "UNAVAILABLE",
            "verification_status": "TOOL_FAILURE",
            "verification_level": "UNKNOWN",
        }
    worker_digest = hashlib.sha256(worker.read_bytes()).hexdigest()
    package_versions: dict[str, str] = {}
    for package in ("sympy", "numpy", "scipy", "z3-solver"):
        try:
            package_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            package_versions[package] = "unavailable"
    run_environment = {
        "pythonVersion": sys.version.split()[0], "pythonImplementation": platform.python_implementation(),
        "platform": platform.platform(), "executable": str(Path(sys.executable).name),
        "packages": package_versions, "pythonHashSeed": "0",
    }
    files_before = _workspace_files(workspace)

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        files, warnings = _generated_files(workspace, files_before)
        result["generated_files"] = files
        result["generated_file_warnings"] = warnings
        return result

    process = await asyncio.create_subprocess_exec(
        *_worker_command(worker),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace),
        env=_worker_env(workspace, workspace_root.resolve()),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(request_text.encode("utf-8")), timeout=timeout
        )
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    except TimeoutError:
        process.kill()
        await process.wait()
        return finish({
            "ok": False,
            "output": "",
            "error": f"The mathematical tool exceeded its {timeout}-second time limit.",
            "error_type": "TIMEOUT",
            "verification_status": "TOOL_FAILURE",
            "verification_level": "UNKNOWN",
        })
    if len(stdout) > 4_000_000 or len(stderr) > 4_000_000:
        return finish({
            "ok": False,
            "output": "",
            "error": "The mathematical worker exceeded the output limit.",
            "error_type": "OUTPUT_LIMIT",
            "verification_status": "TOOL_FAILURE",
            "verification_level": "UNKNOWN",
        })
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return finish({
            "ok": False,
            "output": "",
            "error": "The mathematical worker returned invalid protocol data.",
            "error_type": "PROTOCOL_ERROR",
            "stderr": stderr.decode("utf-8", errors="replace")[:4000],
            "verification_status": "TOOL_FAILURE",
            "verification_level": "UNKNOWN",
        })
    if not isinstance(response, dict) or response.get("protocol_version") != 2:
        return finish({
            "ok": False,
            "output": "",
            "error": "The mathematical worker returned an unsupported protocol version.",
            "error_type": "PROTOCOL_ERROR",
            "verification_status": "TOOL_FAILURE",
            "verification_level": "UNKNOWN",
        })
    response["worker_exit_code"] = process.returncode
    response["stderr"] = str(response.get("stderr") or stderr.decode("utf-8", errors="replace"))[:4000]
    response["output"] = str(response.get("output") or "")[:2_000_000]
    response["tool_version"] = "math-worker-v2/sha256:" + worker_digest
    response["environment"] = run_environment
    response["input_sha256"] = hashlib.sha256(request_text.encode("utf-8")).hexdigest()
    response["recorded_at"] = datetime.now(UTC).isoformat()
    return finish(response)
