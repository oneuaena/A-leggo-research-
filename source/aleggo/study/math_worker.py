"""Restricted JSON-in/JSON-out mathematical tool worker.

This worker is the only place model-proposed or researcher-proposed
calculations run.  It must therefore be *both* capable enough for the ordinary
mathematics an university researcher writes (classes, ``fractions``, ``mpmath``,
``matplotlib`` figures, exceptions, comprehensions over ``sorted``/``map`` …)
*and* strongly isolated: no network, no sub-processes, no writing or reading
outside the per-project workspace's allowed roots, and a hard memory ceiling.

Two layers provide that isolation:

* a conservative AST check plus a curated ``__builtins__`` and import allow-list
  (keeps obviously dangerous names out and gives friendly errors), and
* a process-wide :func:`sys.addaudithook` that blocks the *effects* that matter
  (process spawning — including the C helpers ``_posixsubprocess.fork_exec``
  and ``_winapi.CreateProcess``, which raise no audit event of their own —
  signals to other processes, sockets, writes/removes outside the workspace,
  loading any named shared library through ctypes — raw native calls such as
  ``CDLL("libc.so.6").system(...)`` raise no audit event, so only
  ``dlopen(NULL)`` is allowed — and reads *or listings* of the application's
  private data root and the real home directory) even if model code reaches an
  interpreter primitive some other way (for example via ``sympy.sympify`` or
  a ``str.format`` field, which the AST check cannot see into).

The audit hook and the RLIMIT memory ceiling are POSIX features; on platforms
without them the AST/builtins layer still applies and the limitation is
documented.  Both can be disabled for debugging with
``ALEGGO_MATH_WORKER_AUDIT=0`` / ``ALEGGO_MATH_WORKER_MEMORY_MB=0``.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import collections
import io
import itertools
import json
import math
import os
import random
import shutil
import sys
import traceback
import types
from typing import Any


if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# Modules a research calculation may import.  Everything here is either pure
# computation or has its dangerous surface covered by the audit hook below.
ALLOWED_IMPORTS = {
    "json", "math", "cmath", "decimal", "fractions", "statistics", "numbers",
    "random", "itertools", "functools", "operator", "collections", "heapq",
    "bisect", "array", "string", "re", "textwrap", "copy", "dataclasses",
    "typing", "enum", "abc", "contextlib", "time", "datetime", "csv", "io",
    "sympy", "mpmath", "numpy", "scipy", "networkx", "z3", "matplotlib",
    "pickle",
}
# Import roots that are only *offered* when actually installed; a missing
# optional package yields a normal ImportError rather than a policy error.
_OPTIONAL_IMPORTS = {"mpmath", "numpy", "scipy", "networkx", "z3", "matplotlib"}

BLOCKED_NAMES = {"exec", "eval", "compile", "input", "help", "breakpoint", "__import__", "globals", "vars"}
# Dunder *attribute access* that enables interpreter escapes stays blocked; the
# ordinary dunders used by classes and operator overloading are allowed so that
# real research code (dataclasses, custom numeric types, context managers …)
# works.
DANGEROUS_DUNDERS = {
    "__subclasses__", "__bases__", "__base__", "__mro__", "__class__",
    "__globals__", "__code__", "__closure__", "__func__", "__self__",
    "__dict__", "__builtins__", "__import__", "__loader__", "__spec__",
    "__getattribute__", "__subclasshook__", "__init_subclass__",
}
MAX_CAPTURE_CHARACTERS = 2_000_000


class JsonCheckpointCompatibility(types.ModuleType):
    """A deliberately non-pickle compatibility facade for JSON data only.

    Mathematical jobs sometimes use pickle merely for a dict/list checkpoint.  Real
    pickle is code-execution capable and must stay unavailable in this worker.  The
    facade keeps the familiar dump/load API but serializes JSON only; unsupported
    Python objects fail explicitly instead of weakening the isolation boundary.
    """

    def __init__(self) -> None:
        super().__init__("pickle")
        self.HIGHEST_PROTOCOL = 0

    @staticmethod
    def dumps(value, protocol=None, *, fix_imports=True, buffer_callback=None):
        del protocol, fix_imports, buffer_callback
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def loads(data, *, fix_imports=True, encoding="ASCII", errors="strict", buffers=()):
        del fix_imports, encoding, errors, buffers
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)

    @classmethod
    def dump(cls, value, file, protocol=None, *, fix_imports=True, buffer_callback=None):
        encoded = cls.dumps(value, protocol, fix_imports=fix_imports, buffer_callback=buffer_callback)
        try:
            file.write(encoded)
        except TypeError:
            file.write(encoded.decode("utf-8"))

    @classmethod
    def load(cls, file, *, fix_imports=True, encoding="ASCII", errors="strict", buffers=()):
        return cls.loads(file.read(), fix_imports=fix_imports, encoding=encoding, errors=errors, buffers=buffers)


SAFE_PICKLE = JsonCheckpointCompatibility()
SAFE_IMPORTS = {
    "itertools": itertools,
    "collections": collections,
    "random": random,
    "pickle": SAFE_PICKLE,
}


def _operator_facade() -> types.ModuleType:
    """The ``operator`` module minus its C-level attribute/item accessors.

    ``operator.attrgetter``/``itemgetter``/``methodcaller`` resolve attributes
    from plain strings inside C code, so they bypass both the AST dunder
    block in :func:`validate_code` and the :func:`_safe_builtins` getattr
    wrapper (``operator.attrgetter("__class__")(())`` reaches the real class
    without any ``ast.Attribute`` node existing).  Researchers keep the
    arithmetic, comparison and sequence helpers; attribute access stays
    available through ``getattr`` and lambdas.
    """
    import operator as _real_operator

    facade = types.ModuleType("operator")
    for name in dir(_real_operator):
        if name in {"attrgetter", "itemgetter", "methodcaller"}:
            continue
        setattr(facade, name, getattr(_real_operator, name))
    return facade


SAFE_IMPORTS["operator"] = _operator_facade()


class OutputLimitExceeded(RuntimeError):
    """Raised before model-generated output can exhaust worker memory."""


class LimitedTextBuffer(io.StringIO):
    def write(self, value: str) -> int:
        if self.tell() + len(value) > MAX_CAPTURE_CHARACTERS:
            raise OutputLimitExceeded(f"Program output exceeded {MAX_CAPTURE_CHARACTERS:,} characters.")
        return super().write(value)


# --------------------------------------------------------------------------- #
# Process-wide isolation: memory ceiling + audit hook.
# --------------------------------------------------------------------------- #

def _normalise(path: str) -> str:
    """Resolve symlinks and fold case on case-insensitive platforms (Windows)."""
    try:
        resolved = os.path.realpath(path)
    except (OSError, ValueError):
        resolved = os.path.abspath(path)
    return os.path.normcase(resolved)


def _under(path: str, roots: tuple[str, ...]) -> bool:
    if not path:
        return False
    target = _normalise(path)
    for root in roots:
        if not root:
            continue
        try:
            if os.path.commonpath([root, target]) == root:
                return True
        except ValueError:  # different drive / mixed abs-rel on Windows
            continue
    return False


def _code_roots() -> tuple[str, ...]:
    roots = {sys.prefix, sys.base_prefix, getattr(sys, "_MEIPASS", "")}
    for entry in sys.path:
        if entry and os.path.isdir(entry):
            roots.add(_normalise(entry))
    # Standard system library locations needed by the loader and timezone data.
    for system in ("/usr", "/lib", "/lib64", "/etc", "/proc", "/sys", "/dev/urandom",
                   "/private/var", "/System", "/Library", "/opt"):
        if os.path.exists(system):
            roots.add(system)
    return tuple(sorted(r for r in (_normalise(x) if x else "" for x in roots) if r))


_WINDOWS_JOB_HANDLES: list[Any] = []


def _windows_job_object(
    limit_bytes: int, process_handle_pid: int | None = None, max_processes: int = 1,
) -> Any:
    """Put a process in a kill-on-close Windows Job Object with a memory ceiling.

    ``process_handle_pid`` is ``None`` for the current process (the worker limits
    itself at start-up) or the pid of a child the caller just spawned (external
    CAS engines).  The job also caps the number of active processes so code that
    escapes the Python layer still cannot spawn helpers (``max_processes=0``
    disables that cap for engines that legitimately fork), and every process in it
    dies when the last handle closes (i.e. when the worker/parent exits).
    Returns the job handle or ``None`` when the API is unavailable.
    """
    if os.name != "nt" or limit_bytes <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # pragma: no cover - ctypes missing
        return None

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_limit_process_memory = 0x00000100
    job_object_limit_active_process = 0x00000008
    job_object_limit_kill_on_job_close = 0x00002000
    job_object_limit_die_on_unhandled_exception = 0x00000400
    job_object_extended_limit_information = 9
    process_set_quota = 0x0100
    process_terminate = 0x0001
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimits()
        flags = (
            job_object_limit_process_memory
            | job_object_limit_kill_on_job_close
            | job_object_limit_die_on_unhandled_exception
        )
        if max_processes > 0:
            flags |= job_object_limit_active_process
            info.BasicLimitInformation.ActiveProcessLimit = int(max_processes)
        info.BasicLimitInformation.LimitFlags = flags
        info.ProcessMemoryLimit = int(limit_bytes)
        if not kernel32.SetInformationJobObject(
            job, job_object_extended_limit_information, ctypes.byref(info), ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return None
        if process_handle_pid is None:
            process = kernel32.GetCurrentProcess()
            close_process = False
        else:
            process = kernel32.OpenProcess(process_set_quota | process_terminate, False, int(process_handle_pid))
            close_process = True
        if not process:
            kernel32.CloseHandle(job)
            return None
        assigned = kernel32.AssignProcessToJobObject(job, process)
        if close_process:
            kernel32.CloseHandle(process)
        if not assigned:
            kernel32.CloseHandle(job)
            return None
        # Keep the handle alive for the lifetime of this process: closing it
        # would terminate every process in the job (kill-on-close).
        _WINDOWS_JOB_HANDLES.append(job)
        return job
    except Exception:  # pragma: no cover - Windows-only failure modes
        return None


def _apply_memory_limit() -> None:
    megabytes = 0
    try:
        megabytes = int(os.environ.get("ALEGGO_MATH_WORKER_MEMORY_MB", "4096"))
    except ValueError:
        megabytes = 4096
    if megabytes <= 0:
        return
    limit = megabytes * 1024 * 1024
    if os.name == "nt":
        _windows_job_object(limit)
        return
    try:
        import resource
    except ImportError:  # unusual platforms
        return
    for name in ("RLIMIT_AS", "RLIMIT_DATA"):
        constant = getattr(resource, name, None)
        if constant is None:
            continue
        try:
            soft, hard = resource.getrlimit(constant)
            ceiling = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
            resource.setrlimit(constant, (ceiling, hard))
            if name == "RLIMIT_AS":  # AS covers DATA; one effective ceiling is enough
                return
        except (ValueError, OSError):
            continue


def _install_audit_hook(workspace_root: str) -> None:
    if os.environ.get("ALEGGO_MATH_WORKER_AUDIT", "1") == "0":
        return
    workspace = _normalise(workspace_root)
    # Only the project workspace and the bridge-provided scratch/cache
    # directories (MPLCONFIGDIR, TMPDIR) are writable.  The system temp dir is
    # deliberately *not* a write root: it is shared with other processes.
    scratch = [os.environ.get("MPLCONFIGDIR", ""), os.environ.get("ALEGGO_WORKER_SCRATCH", "")]
    write_roots = tuple(r for r in (workspace, *(_normalise(p) for p in scratch if p)) if r)
    code_roots = _code_roots()
    protected = tuple(_normalise(p) for p in os.environ.get("ALEGGO_PROTECTED_ROOTS", "").split(os.pathsep) if p)
    try:
        home = _normalise(os.path.expanduser("~"))
    except (OSError, ValueError):
        home = ""
    protected = tuple(p for p in (*protected, home) if p)

    block_events = {
        "os.system", "subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn",
        "os.fork", "os.forkpty", "pty.spawn",
        "os.kill", "os.startfile",
        "socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto",
        "socket.sethostname", "ftplib.connect", "smtplib.connect", "urllib.Request",
        "winreg.OpenKey", "winreg.CreateKey",
    }
    path_write_events = {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.link",
                         "os.symlink", "os.chmod", "os.chown", "os.truncate", "shutil.copyfile"}
    watched = block_events | path_write_events | {
        "open", "os.open", "os.listdir", "os.scandir", "ctypes.dlopen", "ctypes.LoadLibrary",
    }

    def _is_write_mode(mode: object) -> bool:
        return isinstance(mode, str) and any(flag in mode for flag in ("w", "a", "x", "+"))

    def _is_write_flags(flags: object) -> bool:
        if not isinstance(flags, int):
            return False
        access = flags & (os.O_WRONLY | os.O_RDWR)
        return bool(access or flags & getattr(os, "O_CREAT", 0) or flags & getattr(os, "O_TRUNC", 0) or flags & getattr(os, "O_APPEND", 0))

    def _check_write(path: object) -> None:
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        text = os.fspath(path)
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        if not _under(text, write_roots):
            raise PermissionError("Writing outside the project workspace is not allowed in the mathematical worker.")

    def _check_read(path: object) -> None:
        if not isinstance(path, (str, bytes, os.PathLike)):
            return
        text = os.fspath(path)
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        if _under(text, protected) and not _under(text, code_roots) and not _under(text, write_roots):
            raise PermissionError("Reading the application's private data is not allowed in the mathematical worker.")

    def hook(event: str, args: tuple) -> None:
        if event not in watched:
            return
        if event in block_events:
            raise PermissionError(f"Operation '{event}' is not allowed in the mathematical worker.")
        if event == "open":
            path = args[0] if args else None
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else None
            if _is_write_mode(mode) or _is_write_flags(flags):
                _check_write(path)
            else:
                _check_read(path)
            return
        if event == "os.open":
            path = args[0] if args else None
            flags = args[1] if len(args) > 1 else 0
            if _is_write_flags(flags):
                _check_write(path)
            else:
                _check_read(path)
            return
        if event in {"os.listdir", "os.scandir"}:
            # A directory listing of the protected roots would reveal the
            # names of other projects and private files; apply the same read
            # policy as open().
            _check_read(args[0] if args else None)
            return
        if event in path_write_events:
            # Audit-arg layouts differ per call (trailing dir_fd / mode /
            # length ints, e.g. os.remove -> (path, dir_fd)); the old
            # args[-1] therefore checked an int and the guard never fired.
            # Checking every path-like argument covers all layouts, and for
            # rename/link/symlink it guards BOTH endpoints.
            for arg in args:
                _check_write(arg)
            return
        if event in {"ctypes.dlopen", "ctypes.LoadLibrary"}:
            name = args[0] if args else None
            # Raw native calls made through ctypes (for example
            # CDLL("libc.so.6").system(...)) raise no audit event of their
            # own, so the hook cannot police what happens after the load.
            # Only dlopen(NULL) — the interpreter itself, which
            # ctypes.pythonapi touches during legitimate library imports —
            # is allowed; every named shared library is refused.
            if name is not None:
                raise PermissionError("Loading shared libraries is not allowed in the mathematical worker.")

    sys.addaudithook(hook)
    _neuter_direct_spawn()


def _neuter_direct_spawn() -> None:
    """Replace process-spawning C helpers that raise no audit event.

    ``subprocess.Popen`` is already blocked through its audit event, but code
    that reaches the real interpreter can call the C helpers directly:
    ``_posixsubprocess.fork_exec`` and ``_winapi.CreateProcess`` raise no
    audit event of their own.  Patching the shared module object covers every
    importer, including a later ``__import__`` by untrusted code.  The worker
    itself never spawns processes and legitimate research code cannot import
    ``subprocess`` (not allow-listed), so nothing legitimate breaks.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise PermissionError("Spawning processes is not allowed in the mathematical worker.")

    for module_name, attr in (("_posixsubprocess", "fork_exec"), ("_winapi", "CreateProcess")):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        try:
            setattr(module, attr, _blocked)
        except (AttributeError, TypeError):
            continue


# --------------------------------------------------------------------------- #
# Program execution.
# --------------------------------------------------------------------------- #

def validate_code(source: str) -> None:
    tree = ast.parse(source, mode="exec")
    version_owners: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in {"sympy", "numpy", "scipy", "mpmath", "matplotlib", "networkx"}:
                    version_owners.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {"sympy", "numpy", "scipy", "mpmath", "matplotlib", "networkx"}:
            version_owners.add((node.module or "").split(".")[0])
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name.split(".")[0] for alias in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
            if any(name not in ALLOWED_IMPORTS for name in names):
                bad = next(name for name in names if name not in ALLOWED_IMPORTS)
                raise ValueError(
                    f"Import '{bad}' is not available in the mathematical worker. Allowed: "
                    + ", ".join(sorted(ALLOWED_IMPORTS)) + "."
                )
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise ValueError(f"Blocked operation: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in DANGEROUS_DUNDERS:
            raise ValueError("Access to interpreter-internal attributes is not allowed.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__") and node.attr in {
            "__class__", "__bases__", "__mro__", "__subclasses__", "__globals__", "__dict__", "__code__",
        }:
            raise ValueError("Access to interpreter-internal attributes is not allowed.")


def _safe_builtins() -> dict:
    exported = (
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
        "callable", "chr", "complex", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "getattr", "hasattr", "hash", "hex",
        "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max",
        "min", "next", "object", "oct", "ord", "pow", "print", "range", "repr",
        "reversed", "round", "set", "setattr", "slice", "sorted", "str", "sum",
        "tuple", "type", "zip",
    )
    safe = {name: getattr(builtins, name) for name in exported}

    def safe_getattr(obj, name, *default):
        if isinstance(name, str) and name in DANGEROUS_DUNDERS:
            raise AttributeError("Access to interpreter-internal attributes is not allowed.")
        return getattr(obj, name, *default)

    safe["getattr"] = safe_getattr
    exceptions = (
        "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
        "Exception", "FloatingPointError", "IndexError", "KeyError", "LookupError",
        "MemoryError", "NameError", "NotImplementedError", "OverflowError",
        "RecursionError", "RuntimeError", "StopIteration", "StopAsyncIteration",
        "TypeError", "ValueError", "ZeroDivisionError", "UnboundLocalError",
        "GeneratorExit", "KeyboardInterrupt",
    )
    for name in exceptions:
        safe[name] = getattr(builtins, name)
    constants = {"True": True, "False": False, "None": None, "NotImplemented": NotImplemented,
                 "Ellipsis": Ellipsis, "__name__": "__research_experiment__", "__doc__": None,
                 "__build_class__": builtins.__build_class__}
    safe.update(constants)
    return safe


def run_python(data: dict) -> dict:
    source = str(data.get("code", ""))
    seed = data.get("seed")
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int) or abs(seed) > 2**32 - 1:
            return {
                "ok": False, "output": "", "error": "seed must be an integer from -2^32+1 to 2^32-1.",
                "error_type": "INVALID_SEED", "exit_code": 1, "verification_status": "PROGRAM_FAILURE",
            }
        random.seed(seed)
        try:
            import numpy as np
            np.random.seed(seed % 2**32)
        except ImportError:
            pass
    if len(source) > 20000:
        raise ValueError("Code exceeds the 20,000 character limit.")
    try:
        validate_code(source)
    except Exception as exc:
        return {
            "ok": False,
            "output": "",
            "stdout": "",
            "stderr": traceback.format_exc(),
            "error": str(exc),
            "error_type": "VALIDATION_ERROR",
            "exit_code": 1,
            "verification_status": "PROGRAM_FAILURE",
        }
    compatibility_fallbacks = []
    for module in ("itertools", "collections", "random", "pickle"):
        if module in source:
            compatibility_fallbacks.append(
                "pickle=>json-checkpoint facade" if module == "pickle" else f"{module}=>safe standard-library facade"
            )

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if root not in ALLOWED_IMPORTS:
            raise ImportError(f"Import not allowed: {name}")
        if root in SAFE_IMPORTS:
            return SAFE_IMPORTS[root]
        if root == "matplotlib" and "matplotlib" not in sys.modules:
            # Keep figure generation head-less and deterministic for the server.
            import matplotlib
            matplotlib.use("Agg")
        return __import__(name, globals, locals, fromlist, level)

    workspace_root = os.path.realpath(os.getcwd())

    def workspace_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        if not isinstance(file, (str, bytes, os.PathLike)):
            raise PermissionError("Only project-workspace file paths are allowed.")
        candidate = os.path.realpath(os.path.join(workspace_root, os.fspath(file)))
        try:
            inside_workspace = os.path.commonpath([workspace_root, candidate]) == workspace_root
        except ValueError:
            inside_workspace = False
        if not inside_workspace:
            raise PermissionError("File access must remain inside the project workspace.")
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            os.makedirs(os.path.dirname(candidate), exist_ok=True)
        return builtins.open(candidate, mode, buffering, encoding, errors, newline, closefd, opener)

    safe_builtins = _safe_builtins()
    safe_builtins["open"] = workspace_open
    safe_builtins["__import__"] = safe_import
    # Run in a registered module so dataclasses/typing/pickle-style lookups of
    # ``cls.__module__`` resolve like an ordinary script.
    experiment = types.ModuleType("__research_experiment__")
    sys.modules["__research_experiment__"] = experiment
    namespace = experiment.__dict__
    namespace.update({"__builtins__": safe_builtins, "math": math})
    captured_stdout = LimitedTextBuffer()
    captured_stderr = LimitedTextBuffer()
    try:
        with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
            exec(compile(source, "<research-experiment>", "exec", dont_inherit=True), namespace, namespace)
        result = namespace.get("result")
        stdout = captured_stdout.getvalue()
        output = str(result) if result is not None else stdout.rstrip() or "Completed. Set `result` to return a value."
        return {
            "ok": True,
            "output": output,
            "stdout": stdout,
            "stderr": captured_stderr.getvalue(),
            "error_type": "NONE",
            "exit_code": 0,
            "verification_status": "SUCCESS",
            "verification_level": "BOUNDED_CHECK",
            "compatibility_fallbacks": compatibility_fallbacks,
        }
    except OutputLimitExceeded as exc:
        return {
            "ok": False,
            "output": "",
            "stdout": captured_stdout.getvalue(),
            "stderr": captured_stderr.getvalue(),
            "error": str(exc),
            "error_type": "OUTPUT_LIMIT",
            "exit_code": 1,
            "verification_status": "PROGRAM_FAILURE",
        }
    except Exception as exc:
        technical = traceback.format_exc()
        stderr = captured_stderr.getvalue()
        message = str(exc) or type(exc).__name__
        if isinstance(exc, MemoryError):
            message = ("The calculation exceeded the worker memory limit "
                       f"({os.environ.get('ALEGGO_MATH_WORKER_MEMORY_MB', '4096')} MB). Reduce the problem size or stream results.")
        return {
            "ok": False,
            "output": "",
            "stdout": captured_stdout.getvalue(),
            "stderr": f"{stderr}{technical}",
            "error": message,
            "error_type": "PROGRAM_ERROR",
            "exit_code": 1,
            "verification_status": "PROGRAM_FAILURE",
        }


# --------------------------------------------------------------------------- #
# Symbolic tools.
# --------------------------------------------------------------------------- #

def _safe_symbolic_text(value: str, field: str) -> str:
    text = str(value)
    if "__" in text:
        raise ValueError(f"'{field}' 字段不能包含 '__'。")
    if len(text) > 20000:
        raise ValueError(f"'{field}' 字段过长。")
    return text


def _symbol_names(data: dict) -> list[str]:
    raw = data.get("symbols", data.get("variables"))
    if raw is None:
        raw = ["x"]
    if isinstance(raw, str):
        names = [part for part in raw.replace(",", " ").split() if part]
    elif isinstance(raw, (list, tuple)):
        names = [str(part) for part in raw]
    else:
        raise ValueError("'symbols' must be a list of names or a space/comma separated string.")
    names = names or ["x"]
    for name in names:
        _safe_symbolic_text(name, "symbols")
    return names


def _parse_expression(sp, text: str, symbols: dict, field: str = "expression"):
    clean = _safe_symbolic_text(text, field)
    if "=" in clean and "==" not in clean and ">=" not in clean and "<=" not in clean and "!=" not in clean:
        lhs, rhs = clean.split("=", 1)
        return sp.Eq(sp.sympify(lhs, locals=symbols), sp.sympify(rhs, locals=symbols))
    return sp.sympify(clean, locals=symbols)


def _require_expression(data: dict) -> str:
    for key in ("expression", "expr", "f", "equation", "function"):
        if key in data and str(data.get(key)).strip():
            return str(data[key])
    raise ValueError("An 'expression' is required for this symbolic tool.")


def symbolic(tool: str, data: dict) -> str:
    import sympy as sp

    symbols = {name: sp.Symbol(name) for name in _symbol_names(data)}
    variable_name = str(data.get("variable", data.get("var", data.get("wrt", "x"))))
    _safe_symbolic_text(variable_name, "variable")
    variable = symbols.get(variable_name, sp.Symbol(variable_name))
    symbols.setdefault(variable_name, variable)

    if tool == "matrix_compute":
        matrix = sp.Matrix(data.get("matrix", []))
        operation = str(data.get("operation", "det"))
        operations = {
            "det": matrix.det, "rank": matrix.rank, "eigenvals": matrix.eigenvals,
            "eigenvects": matrix.eigenvects, "inverse": matrix.inv, "inv": matrix.inv,
            "rref": lambda: matrix.rref(), "nullspace": matrix.nullspace,
            "transpose": lambda: matrix.T, "trace": matrix.trace,
            "charpoly": lambda: matrix.charpoly().as_expr(), "columnspace": matrix.columnspace,
        }
        if operation not in operations:
            raise ValueError("Unsupported matrix operation: " + operation)
        return str(operations[operation]())

    if tool == "factor_integer":
        value = int(data.get("n", data.get("value", 0)))
        return str(sp.factorint(value))

    if tool == "symbolic_solve_system":
        raw = data.get("equations") or data.get("system") or []
        if isinstance(raw, str):
            raw = [part for part in raw.split(";") if part.strip()]
        equations = [_parse_expression(sp, str(item), symbols, "equations") for item in raw]
        unknowns = [symbols[name] for name in _symbol_names(data)] if data.get("symbols") else sorted(
            {s for eq in equations for s in eq.free_symbols}, key=str)
        return str(sp.solve(equations, unknowns, dict=True))

    expression = _parse_expression(sp, _require_expression(data), symbols)

    if tool == "symbolic_simplify":
        return str(sp.simplify(expression))
    if tool == "solve_equation":
        target = expression.lhs - expression.rhs if isinstance(expression, sp.Eq) else expression
        return str(sp.solve(target, variable))
    if tool == "differentiate":
        return str(sp.diff(expression, variable, int(data.get("order", 1))))
    if tool == "integrate":
        lower = data.get("lower", data.get("from"))
        upper = data.get("upper", data.get("to"))
        if lower is not None and upper is not None:
            lo = sp.sympify(_safe_symbolic_text(lower, "lower"), locals=symbols)
            hi = sp.sympify(_safe_symbolic_text(upper, "upper"), locals=symbols)
            return str(sp.integrate(expression, (variable, lo, hi)))
        return str(sp.integrate(expression, variable))
    if tool == "symbolic_limit":
        point = sp.sympify(_safe_symbolic_text(data.get("point", 0), "point"), locals=symbols)
        direction = str(data.get("direction", "+"))
        direction = direction if direction in {"+", "-", "+-"} else "+"
        return str(sp.limit(expression, variable, point, direction))
    if tool == "symbolic_series":
        point = sp.sympify(_safe_symbolic_text(data.get("point", 0), "point"), locals=symbols)
        order = int(data.get("order", 6))
        return str(sp.series(expression, variable, point, order))
    if tool == "symbolic_summation":
        lower = sp.sympify(_safe_symbolic_text(data.get("lower", data.get("from", 1)), "lower"), locals=symbols)
        upper = sp.sympify(_safe_symbolic_text(data.get("upper", data.get("to", "oo")), "upper"), locals=symbols)
        return str(sp.summation(expression, (variable, lower, upper)))
    if tool == "symbolic_factor":
        return str(sp.factor(expression))
    if tool == "symbolic_expand":
        return str(sp.expand(expression))
    if tool == "symbolic_dsolve":
        func_name = str(data.get("function", "f"))
        _safe_symbolic_text(func_name, "function")
        return str(sp.dsolve(expression, sp.Function(func_name)(variable)))
    if tool == "evaluate_numeric":
        precision = max(1, min(200, int(data.get("precision", data.get("digits", 50)))))
        return str(sp.N(expression, precision))
    raise ValueError("Unsupported tool.")


def capabilities() -> str:
    def package(name: str) -> dict:
        try:
            module = __import__(name)
            version = module.get_version_string() if name == "z3" else getattr(module, "__version__", "available")
            return {"available": True, "version": str(version)}
        except Exception:
            return {"available": False, "version": ""}

    def executable(name: str) -> dict:
        path = shutil.which(name)
        return {"available": bool(path), "version": path or ""}

    report = {
        "python": {"available": True, "version": sys.version.split()[0]},
        "sympy": package("sympy"),
        "numpy": package("numpy"),
        "scipy": package("scipy"),
        "mpmath": package("mpmath"),
        "matplotlib": package("matplotlib"),
        "networkx": package("networkx"),
        "z3": package("z3"),
        "lean": executable("lean"),
        "sage": executable("sage"),
    }
    return json.dumps(report, ensure_ascii=False)


def z3_check(data: dict) -> dict:
    try:
        import z3
    except Exception as exc:
        return {
            "ok": False,
            "output": "",
            "stdout": "",
            "stderr": traceback.format_exc(),
            "error": f"Z3 is unavailable: {exc}",
            "error_type": "UNAVAILABLE",
            "exit_code": 1,
            "verification_status": "TOOL_FAILURE",
            "verification_level": "UNKNOWN",
        }
    smt2 = str(data.get("smt2", ""))
    if not smt2 or len(smt2) > 200000:
        raise ValueError("Provide an SMT-LIB2 script no longer than 200,000 characters.")
    timeout_ms = max(1, min(120000, int(data.get("timeoutMs", 10000))))
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    solver.add(z3.parse_smt2_string(smt2))
    status = solver.check()
    model = str(solver.model()) if status == z3.sat else ""
    reason_unknown = solver.reason_unknown() if status == z3.unknown else ""
    normalized = "SAT" if status == z3.sat else "UNSAT" if status == z3.unsat else "UNKNOWN"
    output = json.dumps({
        "status": normalized,
        "model": model,
        "reason_unknown": reason_unknown,
        "timeout_ms": timeout_ms,
        "bounded": True,
    }, ensure_ascii=False)
    return {
        "ok": True,
        "output": output,
        "stdout": "",
        "stderr": "",
        "error_type": "NONE",
        "exit_code": 0,
        "verification_status": normalized,
        "verification_level": normalized,
        "reason_unknown": reason_unknown,
    }


def successful(output: str, verification_status: str = "SUCCESS", verification_level: str | None = None) -> dict:
    response = {
        "ok": True,
        "output": output,
        "stdout": "",
        "stderr": "",
        "error_type": "NONE",
        "exit_code": 0,
        "verification_status": verification_status,
    }
    if verification_level:
        response["verification_level"] = verification_level
    return response


def main() -> None:
    request = json.loads(sys.stdin.read())
    tool = request.get("name")
    data = request.get("input", {})
    # Enforce the process-wide isolation before any model/researcher-authored
    # expression or program runs.  capability_check reads nothing untrusted.
    _apply_memory_limit()
    if tool == "z3_check" or (tool == "run_python" and "z3" in str(data.get("code", ""))):
        try:
            import z3  # noqa: F401 - preload trusted libz3.dll before audit hook locks dlopen
        except Exception:
            pass
    if tool != "capability_check":
        _install_audit_hook(os.getcwd())
    if tool == "run_python":
        response = run_python(data)
    elif tool == "capability_check":
        response = successful(capabilities())
    elif tool == "z3_check":
        response = z3_check(data)
    else:
        try:
            # A high-precision numeric evaluation is evidence, not a symbolic proof.
            level = "BOUNDED_CHECK" if tool == "evaluate_numeric" else "SYMBOLIC_CHECK"
            response = successful(symbolic(tool, data), verification_level=level)
        except Exception as exc:
            response = {
                "ok": False, "output": "", "stdout": "", "stderr": traceback.format_exc(),
                "error": str(exc), "error_type": "VALIDATION_ERROR" if isinstance(exc, (ValueError, TypeError)) else "PROGRAM_ERROR",
                "exit_code": 1, "verification_status": "PROGRAM_FAILURE",
            }
    try:
        import sympy as sp
        environment = f"Python {sys.version.split()[0]}; SymPy {sp.__version__}; isolated worker"
    except Exception:
        environment = f"Python {sys.version.split()[0]}; isolated worker"
    response.update({"protocol_version": 2, "environment": environment})
    print(json.dumps(response, ensure_ascii=False))


def entrypoint() -> None:
    """Identical JSON protocol for source scripts and frozen sidecar dispatch."""
    try:
        main()
    except Exception as exc:
        print(json.dumps({
            "protocol_version": 2,
            "ok": False,
            "output": "",
            "stdout": "",
            "stderr": traceback.format_exc(),
            "error": str(exc),
            "error_type": "PROGRAM_ERROR",
            "exit_code": 1,
            "verification_status": "PROGRAM_FAILURE",
        }, ensure_ascii=False))


if __name__ == "__main__":
    entrypoint()
