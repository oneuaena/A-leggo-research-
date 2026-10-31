"""Round 25 standalone tests.

The SageMath bridge (``aleggo.study.external_cas``) runs model-written Sage
code under two layers: a static regex policy (layer 1) and a Python audit
hook installed by the shipped ``_SAGE_RUNNER`` script (layer 2).  Round 22
showed for the math worker that layer 1 is bypassable, so layer 2 must cover
every primitive with an effect.  The Sage runner's layer 2 did not:

1. ``import _posixsubprocess`` / ``import posix`` passed the static policy, and
   ``_posixsubprocess.fork_exec`` raises *no* audit event, so a Sage program
   could spawn arbitrary processes (verified: a shell ran with zero audit
   events).  The runner never neutered it, unlike the math worker.
2. ``os.kill`` / ``os.startfile`` were missing from the runner's block list,
   so sandboxed code could kill server processes.
3. ``os.listdir`` / ``os.scandir`` had no read policy: listings of the
   application's private data roots were allowed.
4. The static policy was blind to Python backslash line continuations
   (``import \\<newline> os as o`` compiles and previously passed).

Fixes: the runner template now neuters ``_posixsubprocess.fork_exec`` /
``_winapi.CreateProcess``, blocks ``os.kill`` et al., applies the read policy
to directory listings, and the static policy collapses backslash continuations
and rejects ``posix`` / ``_posixsubprocess`` / ``_winapi``.

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aleggo.study import external_cas as cas  # noqa: E402


class SagePolicyTests(unittest.TestCase):
    def assert_rejected(self, engine: str, code: str) -> None:
        self.assertTrue(cas.policy_violation(engine, code), f"policy missed: {code!r}")

    def assert_allowed(self, engine: str, code: str) -> None:
        self.assertEqual(cas.policy_violation(engine, code), "", f"false positive: {code!r}")

    def test_backslash_continuation_import_blocked(self) -> None:
        self.assert_rejected("sage", "import \\\n    os as o\no.system('id')\n")
        self.assert_rejected("sage", "from \\\n os import \\\n system\n")
        # Attribute access split across a continuation too.
        self.assert_rejected("sage", "o = 1\nx = os\\\n.system\n")

    def test_c_helper_modules_blocked(self) -> None:
        self.assert_rejected("sage", "import _posixsubprocess\n")
        self.assert_rejected("sage", "import _posixsubprocess as p\np.fork_exec\n")
        self.assert_rejected("sage", "import posix\nposix.system('id')\n")
        self.assert_rejected("sage", "from _winapi import CreateProcess\n")

    def test_existing_rejections_kept(self) -> None:
        self.assert_rejected("sage", "import os\n")
        self.assert_rejected("sage", "print(1); import os as o\n")
        self.assert_rejected("sage", "os.system('id')\n")
        self.assert_rejected("pari", "system('id')\n")
        self.assert_rejected("gap", 'Exec("id");\n')
        self.assert_rejected("macaulay2", 'run "ls"\n')

    def test_legitimate_code_still_allowed(self) -> None:
        self.assert_allowed("sage", 'E = EllipticCurve("37a1")\nprint(E.rank(), E.conductor())\n')
        self.assert_allowed("sage", "import numpy as np\nprint(np.pi)\n")
        self.assert_allowed("sage", 'print("import os")\n')
        self.assert_allowed("sage", "# import os\nprint(1)\n")
        self.assert_allowed("pari", "factor(2^64+1)\n")
        self.assert_allowed("gap", "G := SymmetricGroup(5);;\n")
        self.assert_allowed("macaulay2", "R = QQ[x,y]\nprint betti res ideal(x*y)\n")


_RUNNER_DRIVER = textwrap.dedent("""\
    import os, sys, types

    SRC, WORKSPACE, PROTECTED, PAYLOAD = sys.argv[1:5]
    sys.path.insert(0, SRC)

    # Stub the Sage imports: the runner only needs preparse_file and a
    # (possibly empty) sage.all namespace.
    sage = types.ModuleType("sage"); sage.__path__ = []
    sage_all = types.ModuleType("sage.all")
    repl = types.ModuleType("sage.repl"); repl.__path__ = []
    preparse_mod = types.ModuleType("sage.repl.preparse")
    # Real signature: preparse_file(contents, globals=None, numeric_literals=True)
    # takes the file *contents* and returns preparsed source (identity here).
    preparse_mod.preparse_file = lambda contents: contents
    sys.modules.update({"sage": sage, "sage.all": sage_all,
                        "sage.repl": repl, "sage.repl.preparse": preparse_mod})

    os.environ["ALEGGO_PROTECTED_ROOTS"] = PROTECTED
    os.environ["ALEGGO_WORKER_SCRATCH"] = os.path.join(WORKSPACE, "scratch")

    from aleggo.study.external_cas import _SAGE_RUNNER

    sys.argv = ["aleggo_sage_runner.py", WORKSPACE, PAYLOAD]
    exec(compile(_SAGE_RUNNER, "<shipped-sage-runner>", "exec"),
         {"__name__": "__runner_e2e__"})
    """)

_RUNNER_PAYLOAD = textwrap.dedent("""\
    import json, os

    try:
        import _posixsubprocess as _p
        HAVE_FORK_EXEC = True
    except ImportError:
        HAVE_FORK_EXEC = False

    OUT = os.path.join(workspace, "results.json")
    PROT = protected[0]
    results = {}

    def check(name, fn, expect):
        try:
            fn()
            got = "allowed"
        except PermissionError:
            got = "blocked"
        except Exception as exc:
            got = "error:" + type(exc).__name__
        results[name] = [got, expect]

    def try_fork():
        r, w = os.pipe()
        er, ew = os.pipe()
        os.set_inheritable(w, True)
        try:
            _p.fork_exec([b"/bin/true"], [b"/bin/true"], False, (w,), None, None,
                         -1, -1, -1, -1, -1, -1, er, ew,
                         False, False, -1, -1, None, -1, 0, None, False)
        finally:
            for fd in (r, w, er, ew):
                try:
                    os.close(fd)
                except OSError:
                    pass

    if HAVE_FORK_EXEC:
        check("fork_exec neutered", try_fork, "blocked")
    check("os.kill blocked", lambda: os.kill(os.getpid(), 0), "blocked")
    check("os.system blocked", lambda: os.system("id"), "blocked")
    check("listdir protected blocked", lambda: os.listdir(PROT), "blocked")
    check("scandir protected blocked", lambda: list(os.scandir(PROT)), "blocked")
    check("listdir workspace allowed", lambda: os.listdir(workspace), "allowed")

    def read_secret():
        with open(os.path.join(PROT, "secret.txt")) as fh:
            return fh.read()
    check("read protected blocked", read_secret, "blocked")

    def write_workspace():
        with open(os.path.join(workspace, "ok.txt"), "w") as fh:
            fh.write("ok")
    check("write workspace allowed", write_workspace, "allowed")
    check("write protected blocked",
          lambda: open(os.path.join(PROT, "evil.txt"), "w").write("x"), "blocked")

    with open(OUT, "w") as fh:
        json.dump(results, fh)
    """)


class SageRunnerSandboxTests(unittest.TestCase):
    """Execute the *shipped* _SAGE_RUNNER template in a subprocess with the
    audit hook active, then attack it.  Layer 2 must hold even when layer 1
    (the static policy) is bypassed, so the payload imports os directly."""

    def test_shipped_runner_blocks_sandbox_escapes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="r25-") as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            protected = tmp_path / "protected"
            protected.mkdir()
            (protected / "secret.txt").write_text("top secret", encoding="utf-8")

            driver = tmp_path / "driver.py"
            driver.write_text(_RUNNER_DRIVER, encoding="utf-8")
            payload = workspace / "program.sage"
            payload.write_text(_RUNNER_PAYLOAD, encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(driver), str(ROOT), str(workspace),
                 str(protected), str(payload)],
                capture_output=True, text=True, timeout=90, cwd=str(tmp_path),
            )
            self.assertEqual(completed.returncode, 0,
                             f"runner driver crashed:\n{completed.stderr[-3000:]}")
            results_path = workspace / "results.json"
            self.assertTrue(results_path.is_file(), "payload did not record results")
            results = json.loads(results_path.read_text(encoding="utf-8"))
            failures = {name: got for name, (got, expect) in results.items() if got != expect}
            self.assertEqual(failures, {}, f"sandbox gaps in shipped runner: {failures}")
            # The fork_exec attack must have been attempted on posix.
            if os.name == "posix":
                self.assertIn("fork_exec neutered", results)

    def test_template_contains_winapi_neuter(self) -> None:
        # _winapi only exists on Windows; assert the shipped template at least
        # carries the neutering for it.
        self.assertIn('"_winapi"', cas._SAGE_RUNNER)
        self.assertIn('"CreateProcess"', cas._SAGE_RUNNER)


if __name__ == "__main__":
    unittest.main()
