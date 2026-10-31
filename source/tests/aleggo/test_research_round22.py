"""Round 22 standalone tests (math-worker sandbox: C-level attribute bypasses + no-audit-event spawn primitives).

Covers:
- operator facade: attrgetter/itemgetter/methodcaller are gone (they resolve
  dunders from plain strings in C, bypassing validate_code's AST block and
  the safe_getattr wrapper); arithmetic helpers still work, including
  end-to-end through run_python.
- audit hook: named ctypes loads (CDLL("libc.so.6"), absolute libc path)
  are refused while dlopen(NULL)/ctypes.pythonapi still works;
  _posixsubprocess.fork_exec is neutered (it raises no audit event);
  os.kill is blocked; os.listdir/os.scandir of protected roots are blocked
  while workspace listings still work.

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WORKER = ROOT / "aleggo" / "study" / "math_worker.py"


def _run_worker(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(WORKER)],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    return json.loads(proc.stdout.decode("utf-8"))


_HOOK_SCRIPT = r"""
import os, sys, tempfile
sys.path.insert(0, {root!r})
from aleggo.study import math_worker
ws = tempfile.mkdtemp(prefix='ws-')
protected = tempfile.mkdtemp(prefix='protected-')
os.environ['ALEGGO_PROTECTED_ROOTS'] = protected
math_worker._install_audit_hook(ws)
{CHECKS}
print('hook-ok')
"""


def _run_hook_checks(checks: str) -> str:
    script = _HOOK_SCRIPT.format(root=str(ROOT), CHECKS=checks)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        cwd=str(ROOT),  # keep /tmp off sys.path so it is not a code root
    )
    if proc.returncode != 0:
        raise AssertionError(
            "hook subprocess failed:\n" + proc.stderr.decode("utf-8", errors="replace")
        )
    return proc.stdout.decode("utf-8")


def _blocked(code: str) -> str:
    return (
        "try:\n"
        f"    {code}\n"
        "    raise SystemExit('NOT BLOCKED: " + code.replace("'", "\\'") + "')\n"
        "except PermissionError:\n"
        "    pass\n"
    )


class OperatorFacadeTests(unittest.TestCase):
    def test_bypass_helpers_removed(self):
        from aleggo.study import math_worker

        facade = math_worker.SAFE_IMPORTS["operator"]
        for name in ("attrgetter", "itemgetter", "methodcaller"):
            self.assertFalse(hasattr(facade, name), name)
        # Legitimate helpers survive.
        self.assertEqual(facade.add(40, 2), 42)
        self.assertTrue(facade.lt(1, 2))

    def test_validate_code_still_allows_operator(self):
        from aleggo.study import math_worker

        math_worker.validate_code("import operator\nx = operator.add(1, 2)\n")

    def test_attrgetter_escape_fails_end_to_end(self):
        response = _run_worker({
            "name": "run_python",
            "input": {"code": 'import operator\nresult = operator.attrgetter("__class__")(())'},
        })
        self.assertFalse(response["ok"])
        self.assertIn("attrgetter", response.get("error", ""))

    def test_legit_operator_use_end_to_end(self):
        response = _run_worker({
            "name": "run_python",
            "input": {"code": "import operator\nresult = operator.add(40, 2)"},
        })
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["output"], "42")


class AuditHookHardeningTests(unittest.TestCase):
    def test_named_dlopen_blocked_null_allowed(self):
        out = _run_hook_checks(
            _blocked("import ctypes; ctypes.CDLL('libc.so.6')")
            + _blocked("import ctypes; ctypes.CDLL('/lib/x86_64-linux-gnu/libc.so.6')")
            + "import ctypes\n_ = ctypes.pythonapi  # dlopen(NULL): must keep working\n"
        )
        self.assertIn("hook-ok", out)

    def test_fork_exec_neutered(self):
        out = _run_hook_checks(
            _blocked("import _posixsubprocess; _posixsubprocess.fork_exec()")
        )
        self.assertIn("hook-ok", out)

    def test_os_kill_blocked(self):
        out = _run_hook_checks(
            _blocked("import os; os.kill(os.getpid(), 0)")
        )
        self.assertIn("hook-ok", out)

    def test_listdir_scandir_protected_blocked_workspace_allowed(self):
        out = _run_hook_checks(
            _blocked("import os; os.listdir(protected)")
            + _blocked("import os; os.scandir(protected)")
            + "import os\nos.listdir(ws)\nlist(os.scandir(ws))\n"
        )
        self.assertIn("hook-ok", out)

    def test_numpy_still_works_with_hook(self):
        out = _run_hook_checks(
            "import numpy as np\nassert float(np.linalg.det(np.eye(3))) == 1.0\n"
        )
        self.assertIn("hook-ok", out)


if __name__ == "__main__":
    unittest.main()
