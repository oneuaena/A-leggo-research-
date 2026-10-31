"""Python commands that preserve source and packaged desktop execution."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
from typing import Sequence

_WORKER_FLAG = "--aleggo-module-worker"
_INTERNAL_WORKERS = frozenset({
    "aleggo.runtime.worker_process",
    "aleggo.runtime.code_worker",
    "aleggo.services.parsing.engines.docling.local_worker",
})


def module_command(module: str, *arguments: str) -> list[str]:
    """Run an internal worker with the bundled interpreter when frozen."""
    if getattr(sys, "frozen", False):
        if module not in _INTERNAL_WORKERS:
            raise ValueError(f"Unsupported bundled Python worker: {module}")
        return [sys.executable, _WORKER_FLAG, module, *arguments]
    return [sys.executable, "-m", module, *arguments]


def dispatch_module_worker(arguments: Sequence[str]) -> bool:
    """Dispatch only trusted built-in entry points before desktop startup."""
    if not arguments or arguments[0] != _WORKER_FLAG:
        return False
    if len(arguments) < 2 or arguments[1] not in _INTERNAL_WORKERS:
        raise SystemExit("Unsupported A-leggo Python worker")
    configure_optional_packages()
    worker = importlib.import_module(arguments[1])
    raise SystemExit(worker.main(list(arguments[2:])))


def configure_optional_packages() -> Path:
    """Expose the desktop's separately installed optional parser packages."""
    from aleggo.runtime.home import get_runtime_home

    target = get_runtime_home() / "python-packages"
    if target.is_dir() and str(target) not in sys.path:
        sys.path.insert(0, str(target))
        importlib.invalidate_caches()
    return target


def external_python() -> str:
    """Find Python for creating venvs; a frozen executable is not python.exe.

    CLI app venvs require a normal Python installation. Match the application's
    Python ABI so the existing installation stamp remains accurate.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    expected = f"{sys.version_info.major}.{sys.version_info.minor}:{struct.calcsize('P') * 8}"
    candidates = [os.environ.get("ALEGGO_PYTHON_EXECUTABLE", "")]
    candidates.extend(shutil.which(name) or "" for name in ("python3", "python"))
    checked: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in checked:
            continue
        checked.add(candidate)
        if Path(candidate).resolve() == Path(sys.executable).resolve():
            continue
        try:
            result = subprocess.run(
                [candidate, "-I", "-B", "-c",
                 "import sys,struct; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+':'+str(struct.calcsize('P')*8))"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip() == expected:
            return candidate
    raise RuntimeError(
        f"Optional integration execution needs Python {expected} on PATH, or set "
        "ALEGGO_PYTHON_EXECUTABLE to that interpreter. The packaged desktop "
        "interpreter cannot create third-party virtual environments."
    )
