"""Bounded bridges to specialised computer algebra systems.

Research mathematicians rely on systems that SymPy does not replace: PARI/GP
(number theory), GAP (group theory), SageMath (everything glued together) and
Macaulay2 (commutative algebra / algebraic geometry).  This module runs one
program in one of those systems with the same contract as the mathematical
worker: project-local working directory, clean environment without
credentials, CPU/memory/file-size limits, timeout, output cap, captured
generated files, and a reproducible run record.

Isolation is layered because these systems are full programming languages:

1. a static policy rejects the commands that reach the operating system
   (shell escapes, process spawning, sockets, arbitrary file reads/writes);
2. each engine runs in its own secure mode where it has one (PARI ``secure``,
   GAP with ``Exec``/``Process`` disabled, SageMath under a Python audit hook);
3. when bubblewrap is usable the process additionally runs without network
   and with the application's private data and the user's home hidden.

Set ``ALEGGO_CAS_REQUIRE_SANDBOX=1`` to refuse execution unless layer 3 is
available.  Executables are discovered on PATH, from ``ALEGGO_GP_PATH`` /
``ALEGGO_GAP_PATH`` / ``ALEGGO_SAGE_PATH`` / ``ALEGGO_M2_PATH``, or from the
administrator-configured paths in ``<tools home>/cas-config.json``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import functools
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from aleggo.study.lean_formal import tools_home

MAX_CODE_CHARACTERS = 100_000
MAX_OUTPUT_BYTES = 2_000_000
DEFAULT_MEMORY_MB = 4096
PROJECT_ID_RE = re.compile(r"^(?:research_[0-9a-f]{32}|mra_[0-9a-f]{20})$")


@dataclass(frozen=True)
class Engine:
    key: str
    tool: str
    label: str
    executables: tuple[str, ...]
    env_var: str
    homepage: str
    install_hint: str
    language: str
    example: str


ENGINES: dict[str, Engine] = {
    "pari": Engine(
        "pari", "run_pari", "PARI/GP", ("gp", "gp.exe", "gp-2.17", "gp-2.15"), "ALEGGO_GP_PATH",
        "https://pari.math.u-bordeaux.fr/download.html",
        "Linux: sudo apt install pari-gp · macOS: brew install pari · Windows: the PARI/GP installer from the PARI website.",
        "GP", "factor(2^64+1)\nbnfinit(x^2+5).cyc\nellinit([0,0,0,-1,0]).disc",
    ),
    "gap": Engine(
        "gap", "run_gap", "GAP", ("gap", "gap.sh", "gap.bat"), "ALEGGO_GAP_PATH",
        "https://www.gap-system.org/install/",
        "Linux: sudo apt install gap · macOS: brew install gap · Windows: the GAP installer, then configure the path to gap.",
        "GAP", "G := SymmetricGroup(5);; Print(Size(G), \"\\n\");\nPrint(StructureDescription(Centre(SmallGroup(16,3))), \"\\n\");",
    ),
    "sage": Engine(
        "sage", "run_sage", "SageMath", ("sage",), "ALEGGO_SAGE_PATH",
        "https://doc.sagemath.org/html/en/installation/index.html",
        "Linux/macOS: conda create -n sage -c conda-forge sage (or your distribution package) · Windows: use WSL.",
        "Sage", "E = EllipticCurve('37a1')\nprint(E.rank(), E.conductor())\nprint(factor(x^4 - 1))",
    ),
    "macaulay2": Engine(
        "macaulay2", "run_macaulay2", "Macaulay2", ("M2", "M2.exe", "m2"), "ALEGGO_M2_PATH",
        "https://macaulay2.com/Downloading/",
        "Linux: sudo apt install macaulay2 · macOS: brew install macaulay2 · Windows: use WSL.",
        "Macaulay2", "R = QQ[x,y,z]\nI = ideal(x*y, y*z, x*z)\nprint betti res I\nprint dim I",
    ),
}
CAS_TOOLS = frozenset(engine.tool for engine in ENGINES.values())
TOOL_TO_ENGINE = {engine.tool: engine for engine in ENGINES.values()}

# Static policy: identifiers/forms that reach the operating system.  Matching is
# deliberately conservative; a rejected program receives the exact reason and
# can be rewritten without the escape.
_POLICY: dict[str, tuple[tuple[str, str], ...]] = {
    "pari": (
        (r"\b(?:system|extern|externstr|install|gpextern|fileextern|fileopen|filewrite|filewrite1|fileread|filereadstr|"
         r"read|readstr|readvec|readbin|write|write1|writebin|writetex|getenv|addhelp|alias)\s*\(",
         "file, shell and library functions are disabled; print results instead"),
        (r"""\bdefault\s*\(\s*["']?(?:secure|help|datadir|path|sopath|logfile|psfile|histfile)\b""", "this default cannot be changed"),
        (r"(?m)^\s*\\(?![\\pgcx])", "GP meta-commands other than \\p, \\g, \\c and \\x are disabled"),
    ),
    "gap": (
        (r"\b(?:Exec|Process|ExecuteProcess|IO_\w+|InputTextFile|OutputTextFile|InputOutputLocalProcess|PrintTo|AppendTo|"
         r"PrintToFile|AppendToFile|Read|ReadAsFunction|ReadPackage|RereadPackage|ReadLib|InputTextUser|OutputTextUser|"
         r"FileString|StringFile|RemoveFile|CreateDir|RemoveDir|ChangeDirectoryCurrent|Runtime\(\)\s*;?\s*QUIT|"
         r"GAPInfo\.SystemEnvironment|DirectoryHome|DirectoryDesktop|SaveWorkspace|LogTo|InputLogTo|OutputLogTo|"
         r"DownloadURL|SingleHTTPRequest|ListenOnSocket|ConnectToSocket|EvalString|ValueGlobal|VALUE_GLOBAL|"
         r"READ|READ_GAP_ROOT|LoadDynamicModule|LoadKernelExtension|LoadAllPackages)\b",
         "process, file and network functions are disabled; use Print for results"),
        (r"(?i)\bLoadPackage\s*\(\s*\"(?:io|curlinterface|jupyterkernel|zeromqinterface|httpd|semigroups-io)\"",
         "packages that reach the operating system cannot be loaded"),
    ),
    "sage": (
        # Module attribute / "import os" spelling (also catches mid-statement uses).
        # ``posix`` / ``_posixsubprocess`` / ``_winapi`` are the same C helpers
        # spelled without the ``os.`` prefix; ``_posixsubprocess.fork_exec``
        # raises no audit event, so it must be rejected here too.
        (r"\b(?:os|subprocess|socket|shutil|ctypes|requests|urllib|http|pexpect|multiprocessing|pty|signal|importlib|"
         r"builtins|sys|threading|asyncio|webbrowser|ftplib|smtplib|pathlib|glob|tempfile|posix|_posixsubprocess|_winapi)"
         r"\b\s*(?:\.|import\b)",
         "operating-system, network and process modules are not available"),
        # import/from anywhere (start of line, after ';', or after a colon), including aliases:
        # "print(1); import os as o" and "from os import system".
        (r"(?:^|[;:\n])\s*(?:import|from)\s+(?:os|subprocess|socket|shutil|ctypes|requests|urllib|http|pexpect|"
         r"multiprocessing|pty|signal|importlib|builtins|sys|threading|asyncio|webbrowser|ftplib|smtplib|pathlib|"
         r"glob|tempfile|posix|_posixsubprocess|_winapi)\b",
         "operating-system, network and process modules are not available"),
        (r"\b(?:__import__|eval|exec|compile|globals|locals|vars|getattr|setattr|delattr|open|breakpoint|input)\s*\(",
         "dynamic code execution and direct file access are disabled; save figures with .save('name.png')"),
        (r"__\w+__", "dunder attribute access is disabled"),
        (r"(?m)^\s*!|\b(?:os\.system|sage\.misc\.(?:temporary_file|remote_file)|load|attach|preparse_file)\s*\(",
         "shell escapes and loading external files are disabled"),
        # Line magics without parentheses: %load / %run / %attach read files.
        (r"(?m)^\s*%(?:load|run|attach)\b",
         "file-loading magics are disabled"),
        (r"\b(?:maxima|gp|gap|singular|magma|mathematica|maple|matlab|octave|macaulay2|giac|kash|lie|lisp|mupad|qepcad|"
         r"r|scilab|polymake)\s*\.\s*(?:eval|console|interact|_eval_line)\s*\(",
         "pexpect interfaces to other programs are disabled"),
    ),
    "macaulay2": (
        (r"""\b(?:run|fork|exec|openListener|openInOut|openIn|openOut|openOutAppend|removeFile|removeDirectory|moveFile|"""
         r"""copyFile|copyDirectory|symlinkFile|makeDirectory|changeDirectory|installPackage|uninstallPackage|"""
         r"""installMethod|getenv|setGroupID|kill|wait|processID|load|input|needs|needsPackage|loadPackage|get|"""
         r"""readDirectory|fileExists|fileTime|fileMode|instance|debug|restart|exit)\b\s*(?:\(|"|\s)""",
         "process, file and package-installation functions are disabled; use print for results"),
        (r"\"\s*[!$]", "pipe and socket file names are disabled"),
        (r"<<\s*\"(?!\s*\")", "writing to files is disabled; print results instead"),
    ),
}
_GAP_DISABLED = ("Exec", "Process", "ExecuteProcess", "InputOutputLocalProcess", "LoadDynamicModule",
                 "LoadKernelExtension", "SaveWorkspace", "DownloadURL", "SingleHTTPRequest", "LoadAllPackages",
                 "EvalString", "ChangeDirectoryCurrent", "RemoveFile", "CreateDir", "RemoveDir")
_GAP_BLOCKED_PACKAGES = ("io", "curlinterface", "jupyterkernel", "zeromqinterface", "httpd", "crypting", "francy")
# ``needsPackage``/``loadPackage`` of standard packages is common in Macaulay2;
# allow them only for packages that ship with Macaulay2 itself.  Anything else
# is left in place and rejected by the policy pattern below.
_M2_SHIPPED_PACKAGES = frozenset({
    "BoijSoederberg", "Complexes", "ConvexInterface", "Depth", "Divisor",
    "EdgeIdeals", "Elimination", "FourTiTwo", "Graphs", "HomologicalAlgebra",
    "K3Surfaces", "LatticePolytopes", "LocalRings", "Macaulay2Doc",
    "MatroidTheory", "MinimalPrimes", "MultiplierIdeals", "NormalToricVarieties",
    "Parametrization", "Polyhedra", "Posets", "PrimaryDecomposition",
    "QuillenSuslin", "ReesAlgebra", "Resolutions", "SchurRings",
    "SimplicialComplexes", "SimplicialDecompositions", "SpaceCurves",
    "SumsOfSquares", "TensorComplexes", "ToricTopology", "Truncations",
    "VersalDeformations", "WeylAlgebras",
})
_M2_PACKAGE = re.compile(r"""\b(?:needsPackage|loadPackage)\s*(?:\(\s*)?"([A-Za-z0-9_]+)"\s*\)?""")


def _strip_m2_package(match: "re.Match[str]") -> str:
    return "" if match.group(1) in _M2_SHIPPED_PACKAGES else match.group(0)


def policy_violation(engine: str, code: str) -> str:
    """Return the reason a program is rejected, or ``""``."""
    if engine == "macaulay2":
        code = _M2_PACKAGE.sub(_strip_m2_package, code)
    if engine == "sage":
        # Explicit line joining (``import \<newline> os``) is invisible to the
        # line-oriented patterns below; collapse it before matching.  Inside a
        # string literal the join only changes the literal's own content, which
        # is stripped right after, so this cannot hide a payload.
        code = re.sub(r"\\\r?\n", " ", code)
        # Strings and comments cannot reach the OS; strip them so that text
        # such as print("os.path") is not rejected.
        code = re.sub(r"(?m)#.*$", "", code)
        code = re.sub(r"\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", "''", code)
    for pattern, reason in _POLICY.get(engine, ()):
        match = re.search(pattern, code)
        if match:
            return f"{ENGINES[engine].label} program rejected near {match.group(0).strip()[:40]!r}: {reason}."
    return ""


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def _config_path() -> Path:
    return tools_home() / "cas-config.json"


def load_config() -> dict[str, str]:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if k in ENGINES and isinstance(v, str)} if isinstance(data, dict) else {}


def save_config(paths: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in paths.items():
        if key not in ENGINES:
            raise ValueError(f"未知的计算机代数系统 {key!r}。")
        value = str(value or "").strip()
        if value:
            candidate = Path(value).expanduser()
            if not candidate.is_file():
                raise ValueError(f"{ENGINES[key].label}: {value} is not an executable file.")
            cleaned[key] = str(candidate.resolve())
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    find_executable.cache_clear()
    engine_version.cache_clear()
    return cleaned


@functools.lru_cache(maxsize=16)
def find_executable(engine: str) -> str:
    spec = ENGINES[engine]
    candidates: list[str] = []
    if os.environ.get(spec.env_var):
        candidates.append(os.environ[spec.env_var])
    configured = load_config().get(engine)
    if configured:
        candidates.append(configured)
    managed = tools_home() / "cas" / engine / "bin"
    candidates.extend(str(managed / name) for name in spec.executables)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    for name in spec.executables:
        found = shutil.which(name)
        if found:
            return found
    return ""


def _version_command(engine: str, executable: str) -> list[str]:
    if engine == "pari":
        return [executable, "--version-short"]
    if engine == "gap":
        return [executable, "-q", "-A", "-b"]
    return [executable, "--version"]


@functools.lru_cache(maxsize=16)
def engine_version(engine: str, executable: str) -> str:
    if not executable:
        return ""
    try:
        stdin = "Print(GAPInfo.Version, \"\\n\");\nQUIT;\n" if engine == "gap" else None
        completed = subprocess.run(_version_command(engine, executable), input=stdin, capture_output=True, text=True,
                                   timeout=60, env=_base_env(Path(tempfile.gettempdir())), stdin=None if stdin else subprocess.DEVNULL)
        text = (completed.stdout or completed.stderr).strip().splitlines()
        return text[0][:120] if text else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


@functools.lru_cache(maxsize=1)
def _bwrap_usable() -> str:
    """Path to a working bubblewrap, or ``""`` (containers often forbid it)."""
    if os.name != "posix" or sys.platform == "darwin":
        return ""
    found = shutil.which("bwrap")
    if not found:
        return ""
    try:
        completed = subprocess.run([found, "--ro-bind", "/", "/", "--unshare-net", "--die-with-parent", "true"],
                                   capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return found if completed.returncode == 0 else ""


def isolation_level() -> str:
    if _bwrap_usable():
        return "os-sandbox"  # no network, private data hidden
    return "job-object" if os.name == "nt" else "resource-limits"


def engine_status() -> list[dict[str, Any]]:
    items = []
    for key, spec in ENGINES.items():
        executable = find_executable(key)
        items.append({
            "key": key, "tool": spec.tool, "label": spec.label, "available": bool(executable),
            "path": executable, "version": engine_version(key, executable) if executable else "",
            "isolation": isolation_level(), "homepage": spec.homepage, "installHint": spec.install_hint,
            "envVar": spec.env_var, "example": spec.example, "language": spec.language,
        })
    return items


def engine_available(engine: str) -> bool:
    return engine in ENGINES and bool(find_executable(engine))


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

_SAGE_RUNNER = r'''
import os, sys
workspace, source_path = sys.argv[1], sys.argv[2]
with open(source_path, encoding="utf-8") as handle:
    source = handle.read()
from sage.all import *  # noqa: F401,F403 - Sage's global namespace
from sage.repl.preparse import preparse_file
code = compile(preparse_file(source), "<research-sage>", "exec")
os.chdir(workspace)
protected = tuple(os.path.realpath(p) for p in os.environ.get("ALEGGO_PROTECTED_ROOTS", "").split(os.pathsep) if p)
write_roots = tuple(os.path.realpath(p) for p in (workspace, os.environ.get("ALEGGO_WORKER_SCRATCH", "")) if p)
def _under(path, roots):
    try:
        target = os.path.realpath(os.fspath(path) if not isinstance(path, bytes) else path.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        return False
    for root in roots:
        try:
            if os.path.commonpath([root, target]) == root:
                return True
        except ValueError:
            continue
    return False
BLOCK = {"os.system", "subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "pty.spawn",
         "os.kill", "os.startfile",
         "socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto", "socket.sethostname",
         "ftplib.connect", "smtplib.connect", "urllib.Request", "ctypes.dlopen",
         "winreg.OpenKey", "winreg.CreateKey"}
def _read_blocked(path):
    return _under(path, protected) and not _under(path, write_roots)
def hook(event, args):
    if event in BLOCK:
        raise PermissionError("Operation %r is not allowed in the research CAS bridge." % event)
    if event in ("os.listdir", "os.scandir") and args:
        # A listing of the application's private data would reveal other
        # projects' names; apply the same read policy as open().
        if _read_blocked(args[0]):
            raise PermissionError("不允许读取应用的私有数据。")
        return
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        writing = any(c in mode for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
        if writing and not _under(args[0], write_roots):
            raise PermissionError("不允许写入项目工作区以外的位置。")
        if not writing and _read_blocked(args[0]):
            raise PermissionError("不允许读取应用的私有数据。")
def _neuter_direct_spawn():
    # _posixsubprocess.fork_exec / _winapi.CreateProcess raise no audit event,
    # so the hook above is blind to them; patch the shared module objects
    # before untrusted code runs (same fix as math_worker). Sage itself is
    # fully imported by this point, so nothing legitimate breaks.
    def _blocked(*args, **kwargs):
        raise PermissionError("Spawning processes is not allowed in the research CAS bridge.")
    for module_name, attr in (("_posixsubprocess", "fork_exec"), ("_winapi", "CreateProcess")):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        try:
            setattr(module, attr, _blocked)
        except (AttributeError, TypeError):
            continue
sys.addaudithook(hook)
_neuter_direct_spawn()
exec(code, {"__name__": "__main__", **{k: v for k, v in globals().items() if not k.startswith("_") and k not in {"os", "sys", "hook", "BLOCK", "handle"}}})
'''


def _base_env(scratch: Path) -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    env.update({"HOME": str(scratch), "USERPROFILE": str(scratch), "TMPDIR": str(scratch), "TEMP": str(scratch),
                "TMP": str(scratch), "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1", "MPLBACKEND": "Agg",
                "MPLCONFIGDIR": str(scratch / ".mpl"), "DOT_SAGE": str(scratch / ".sage"), "LANG": env.get("LANG", "C.UTF-8")})
    return env


def _command(engine: str, executable: str, source: Path, workspace: Path, runner: Path | None) -> tuple[list[str], bytes | None]:
    text = source.read_text(encoding="utf-8")
    if engine == "pari":
        # -q quiet, -f skip gprc; secure mode forbids system()/extern(); \q ends a stdin session.
        return [executable, "-q", "-f", "-D", "secure=1", "-D", "readline=0", "-D", "colors=no"], (text + "\n\\q\n").encode()
    if engine == "gap":
        # Errors return to the top level instead of opening a break loop that
        # would consume the following program lines; process functions are
        # rebound as defence in depth behind the static policy.
        prelude = (
            "BreakOnError := false;;\nSetPrintFormattingStatus(\"*stdout*\", false);;\n"
            "__aleggo_disable := function(n) if IsBoundGlobal(n) then MakeReadWriteGlobal(n); UnbindGlobal(n); "
            "BindGlobal(n, function(arg) Error(n, \" is disabled in the A-leggo research bridge\"); end); fi; end;;\n"
            "Perform(Concatenation([" + ",".join(f'\"{name}\"' for name in _GAP_DISABLED) + "], "
            "Filtered(NamesGVars(), n -> Length(n) > 3 and n{[1..3]} = \"IO_\")), __aleggo_disable);;\n"
            "__aleggo_guard := function(blocked) local orig; orig := LoadPackage; MakeReadWriteGlobal(\"LoadPackage\"); "
            "UnbindGlobal(\"LoadPackage\"); BindGlobal(\"LoadPackage\", function(arg) "
            "if Length(arg) > 0 and IsString(arg[1]) and LowercaseString(arg[1]) in blocked then "
            "Error(\"package \", arg[1], \" is disabled in the A-leggo research bridge\"); fi; "
            "return CallFuncList(orig, arg); end); end;;\n"
            "__aleggo_guard([" + ",".join(f'\"{name}\"' for name in _GAP_BLOCKED_PACKAGES) + "]);;\n"
            "Unbind(__aleggo_disable);; Unbind(__aleggo_guard);;\n"
        )
        return [executable, "-q", "-b"], (prelude + text + "\nQUIT;\n").encode()
    if engine == "sage":
        assert runner is not None
        return [executable, "-python", str(runner), str(workspace), str(source)], None
    return [executable, "--script", str(source)], None


def _sandbox_prefix(workspace: Path, scratch: Path, data_root: Path, executable: str) -> list[str]:
    bwrap = _bwrap_usable()
    if not bwrap:
        return []
    prefix = [bwrap, "--die-with-parent", "--unshare-all", "--new-session", "--ro-bind", "/", "/", "--dev", "/dev",
              "--proc", "/proc", "--tmpfs", "/tmp"]
    hidden = {str(data_root.resolve())}
    try:
        hidden.add(str(Path.home().resolve()))
    except (RuntimeError, OSError):
        pass
    for path in sorted(hidden, key=len):
        if Path(path).is_dir():
            prefix += ["--tmpfs", path]
    exe_root = Path(executable).resolve().parent.parent
    if any(str(exe_root).startswith(h + os.sep) or str(exe_root) == h for h in hidden):
        prefix += ["--ro-bind", str(exe_root), str(exe_root)]
    prefix += ["--bind", str(workspace), str(workspace), "--bind", str(scratch), str(scratch), "--chdir", str(workspace)]
    return prefix


def _limits(timeout: int, memory_mb: int):
    if os.name != "posix":
        return None

    def apply() -> None:  # runs in the child before exec
        import resource
        for name, value in (("RLIMIT_CPU", timeout + 5), ("RLIMIT_FSIZE", 256 * 1024 * 1024), ("RLIMIT_CORE", 0)):
            constant = getattr(resource, name, None)
            if constant is not None:
                try:
                    resource.setrlimit(constant, (value, value))
                except (ValueError, OSError):
                    pass
        if memory_mb > 0 and hasattr(resource, "RLIMIT_AS"):
            limit = memory_mb * 1024 * 1024
            try:
                _, hard = resource.getrlimit(resource.RLIMIT_AS)
                resource.setrlimit(resource.RLIMIT_AS, (limit if hard == resource.RLIM_INFINITY else min(limit, hard), hard))
            except (ValueError, OSError):
                pass
    return apply


def _memory_mb(engine: str) -> int:
    try:
        value = int(os.environ.get("ALEGGO_CAS_MEMORY_MB", str(DEFAULT_MEMORY_MB)))
    except ValueError:
        value = DEFAULT_MEMORY_MB
    # GAP and Sage reserve large virtual address ranges at start-up; an address
    # space limit that is too tight makes them fail before user code runs.
    return 0 if engine in {"gap", "sage"} and value < 16384 else value


def _windows_job(process: asyncio.subprocess.Process, memory_mb: int, engine: str = "") -> Any:
    """Kill-on-close job object with a per-process memory ceiling (Windows only)."""
    if os.name != "nt":
        return None
    try:
        from aleggo.study.math_worker import _windows_job_object

        return _windows_job_object(
            memory_mb * 1024 * 1024, process_handle_pid=process.pid,
            max_processes=0 if engine in {"sage", "macaulay2"} else 1,
        )
    except Exception:
        return None


def _error_from_output(engine: str, stdout: str, stderr: str, exit_code: int | None) -> str:
    combined = stdout + "\n" + stderr
    if engine == "pari":
        lines = [line for line in combined.splitlines() if "***" in line]
        text = "\n".join(lines[:20])
    elif engine == "gap":
        match = re.search(r"(?m)^Error, .*(?:\n(?!gap>).*){0,6}", combined)
        text = match.group(0) if match else ""
    elif exit_code not in (0, None):
        return (stderr or stdout).strip()[-4000:] or f"exit code {exit_code}"
    else:
        return ""
    if not text and exit_code not in (0, None):
        # Crashed (segfault / killed) with no parseable error banner: an empty
        # pattern match must never be mistaken for success.
        text = (stderr or stdout).strip()[-4000:] or f"{engine} 异常退出（exit code {exit_code}）"
    return text


def _failure(error: str, error_type: str, status: str = "TOOL_FAILURE") -> dict[str, Any]:
    return {"ok": False, "output": "", "error": error, "error_type": error_type,
            "verification_status": status, "verification_level": "UNKNOWN"}


async def run_cas_tool(*, workspace_root: Path, project_id: str, name: str, input_data: dict[str, Any],
                       timeout_seconds: int = 60, workspace: Path | None = None,
                       generated: Any = None) -> dict[str, Any]:
    """Run one program in an external CAS. ``generated`` captures new files."""
    engine_spec = TOOL_TO_ENGINE.get(name)
    if engine_spec is None:
        raise ValueError("未知的计算机代数工具。")
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError("研究项目标识无效。")
    engine = engine_spec.key
    code = input_data.get("code") if isinstance(input_data, dict) else None
    if not isinstance(code, str) or not code.strip():
        return _failure(f'{engine_spec.label} 输入须为 {{"code": "<{engine_spec.language} 程序>"}}。', "VALIDATION_ERROR", "REJECTED")
    if len(code) > MAX_CODE_CHARACTERS or "\x00" in code:
        return _failure(f"{engine_spec.label} 程序过大或包含 NUL 字节。", "VALIDATION_ERROR", "REJECTED")
    violation = policy_violation(engine, code)
    if violation:
        return _failure(violation, "VALIDATION_ERROR", "REJECTED")
    executable = find_executable(engine)
    if not executable:
        return _failure(f"{engine_spec.label} 未安装。{engine_spec.install_hint} "
                        f"也可设置 {engine_spec.env_var}，或在研究工具中配置路径。", "UNAVAILABLE")
    if os.environ.get("ALEGGO_CAS_REQUIRE_SANDBOX") == "1" and not _bwrap_usable():
        return _failure("本安装要求操作系统沙箱（bubblewrap），但当前不可用。", "UNAVAILABLE")
    try:
        timeout = max(1, min(300, int(input_data.get("timeoutSeconds") or timeout_seconds)))
    except (TypeError, ValueError):
        timeout = max(1, min(300, int(timeout_seconds) if timeout_seconds else 60))
    root = workspace_root.resolve()
    run_dir = (workspace or (root / "research-projects" / project_id)).resolve()
    if not run_dir.is_relative_to(root / "research-projects" / project_id):
        raise ValueError("CAS 工作目录超出项目工作区。")
    run_dir.mkdir(parents=True, exist_ok=True)
    scratch_parent = root / "research-projects" / ".cas-scratch"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    before = generated.snapshot(run_dir) if generated is not None else None
    with tempfile.TemporaryDirectory(prefix=f"{engine}-", dir=scratch_parent) as temp:
        scratch = Path(temp)
        suffix = {"pari": ".gp", "gap": ".g", "sage": ".sage", "macaulay2": ".m2"}[engine]
        source = scratch / ("program" + suffix)
        source.write_text(code, encoding="utf-8")
        runner = None
        if engine == "sage":
            runner = scratch / "aleggo_sage_runner.py"
            runner.write_text(_SAGE_RUNNER, encoding="utf-8")
        command, stdin = _command(engine, executable, source, run_dir, runner)
        env = _base_env(scratch)
        env["PATH"] = str(Path(executable).parent) + os.pathsep + env.get("PATH", "")
        env["ALEGGO_WORKER_SCRATCH"] = str(scratch)
        protected = [str(root)]
        try:
            protected.append(str(Path.home()))
        except (RuntimeError, OSError):
            pass
        env["ALEGGO_PROTECTED_ROOTS"] = os.pathsep.join(protected)
        sandbox = _sandbox_prefix(run_dir, scratch, root, executable)
        memory = _memory_mb(engine)
        started = datetime.now(UTC)
        kwargs: dict[str, Any] = {"cwd": str(run_dir), "env": env, "stdin": asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                                  "stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE}
        if os.name == "posix":
            kwargs["preexec_fn"] = _limits(timeout, memory)
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = await asyncio.create_subprocess_exec(*(sandbox + command), **kwargs)
        except OSError as exc:
            return _failure(f"{engine_spec.label} 启动失败：{exc}", "UNAVAILABLE")
        job = _windows_job(process, memory or DEFAULT_MEMORY_MB, engine)

        def kill() -> None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, 9)
                else:
                    process.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass

        try:
            stdout_b, stderr_b = await asyncio.wait_for(process.communicate(stdin), timeout=timeout)
        except asyncio.CancelledError:
            kill()
            await process.wait()
            raise
        except (TimeoutError, asyncio.TimeoutError):
            kill()
            await process.wait()
            result = _failure(f"{engine_spec.label} 程序运行超时（超过 {timeout} 秒）。", "TIMEOUT")
            return _finish(result, generated, run_dir, before, engine_spec, executable, code, sandbox, started)
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, 9)  # stray children of the CAS
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            del job
    if len(stdout_b) > MAX_OUTPUT_BYTES or len(stderr_b) > MAX_OUTPUT_BYTES:
        result = _failure(f"{engine_spec.label} 程序输出超过上限。", "OUTPUT_LIMIT")
        return _finish(result, generated, run_dir, before, engine_spec, executable, code, sandbox, started)
    stdout, stderr = stdout_b.decode("utf-8", "replace"), stderr_b.decode("utf-8", "replace")
    error = _error_from_output(engine, stdout, stderr, process.returncode)
    if error:
        result = {"ok": False, "output": stdout[-200_000:], "error": error[:4000], "error_type": "PROGRAM_ERROR",
                  "stderr": stderr[-4000:], "verification_status": "PROGRAM_FAILURE", "verification_level": "UNKNOWN"}
    else:
        result = {"ok": True, "output": stdout.strip("\n").rstrip()[:2_000_000], "error": "", "stderr": stderr[-4000:],
                  "verification_status": "SUCCESS", "verification_level": "BOUNDED_CHECK"}
    result["worker_exit_code"] = 0 if not error else (process.returncode or 1)
    return _finish(result, generated, run_dir, before, engine_spec, executable, code, sandbox, started)


def _finish(result: dict[str, Any], generated: Any, run_dir: Path, before: Any, spec: Engine, executable: str,
            code: str, sandbox: list[str], started: datetime) -> dict[str, Any]:
    if generated is not None:
        files, warnings = generated.collect(run_dir, before)
    else:
        files, warnings = [], []
    result["generated_files"] = files
    result["generated_file_warnings"] = warnings
    version = engine_version(spec.key, executable)
    result["tool_version"] = f"{spec.label} {version}".strip()
    result["environment"] = {"engine": spec.label, "engineVersion": version, "executable": Path(executable).name,
                             "isolation": "os-sandbox" if sandbox else isolation_level(), "platform": platform.platform()}
    result["input_sha256"] = hashlib.sha256(code.encode("utf-8")).hexdigest()
    result["recorded_at"] = started.isoformat()
    return result


def tool_schemas(available_only: bool = True) -> dict[str, str]:
    schemas = {}
    for key, spec in ENGINES.items():
        if available_only and not engine_available(key):
            continue
        schemas[spec.tool] = (f'{{"code": "<{spec.language} program>"}} — runs in {spec.label}; print what you need '
                              f"(e.g. {spec.example.splitlines()[0]!r}). No files outside the workspace, no shell, no network.")
    return schemas
