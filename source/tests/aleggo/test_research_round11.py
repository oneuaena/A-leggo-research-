"""Round 11 standalone tests (audit findings: sandbox write guard, dirty-JSON tolerance, budget race).

Covers:
- math_worker audit hook: os.remove/rename/mkdir/symlink/truncate/chmod outside the
  workspace are blocked (the old args[-1] check never fired), workspace writes work.
- research_store._safe_json: dirty DB JSON degrades to the fallback.
- research_domain ResearchDomainStore.consume_budget: concurrent consumers cannot
  lost-update each other (module-level _BUDGET_LOCK).

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_SERVICE_NAME = "aleggo.study.service"


def _install_service_stub(tmp: Path) -> None:
    service = types.ModuleType(_SERVICE_NAME)
    fake_store = types.SimpleNamespace(path=tmp / "test.db", snapshot_database=lambda *a, **k: None)
    service.get_aleggo_service = lambda: types.SimpleNamespace(store=fake_store)
    sys.modules[_SERVICE_NAME] = service


def _remove_service_stub() -> None:
    sys.modules.pop(_SERVICE_NAME, None)


class AuditHookTests(unittest.TestCase):
    def test_blocks_writes_outside_workspace(self):
        # The audit hook is process-global and cannot be uninstalled, so the
        # checks run in a subprocess (mirroring the production worker).
        script = (
            "import os, sys, tempfile\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from aleggo.study import math_worker\n"
            "ws = Path(tempfile.mkdtemp(prefix='ws-'))\n"
            "outside = Path(tempfile.mkdtemp(prefix='outside-'))\n"
            "victims = []\n"
            "for i in range(6):\n"
            "    v = outside / f'v{i}.txt'\n"
            "    v.write_text('precious')\n"
            "    victims.append(v)\n"
            "math_worker._install_audit_hook(str(ws))\n"
            "def blocked(fn):\n"
            "    try:\n"
            "        fn()\n"
            "    except PermissionError:\n"
            "        return True\n"
            "    return False\n"
            "checks = [\n"
            "    blocked(lambda: os.remove(str(victims[0]))),\n"
            "    blocked(lambda: victims[1].unlink()),\n"
            "    blocked(lambda: os.mkdir(str(outside / 'newdir'))),\n"
            "    blocked(lambda: os.rename(str(victims[2]), str(ws / 'stolen.txt'))),\n"
            "    blocked(lambda: os.symlink(str(victims[3]), str(ws / 'link'))),\n"
            "    blocked(lambda: os.truncate(str(victims[4]), 0)),\n"
            "    blocked(lambda: os.chmod(str(victims[5]), 0o600)),\n"
            "]\n"
            "(ws / 'ok.txt').write_text('fine')\n"
            "os.mkdir(str(ws / 'sub'))\n"
            "assert all(checks), f'hook missed some writes: {checks}'\n"
            "assert all(v.exists() for v in victims), 'victim file was modified'\n"
            "print('hook-ok')\n"
        )
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr[-2000:]}")
        self.assertIn("hook-ok", proc.stdout)


class SafeJsonTests(unittest.TestCase):
    def test_fallback(self):
        # research_store binds get_aleggo_service at import; ensure the stub
        # exists for a standalone run (no-op if already imported).
        if "aleggo.study.research_store" not in sys.modules:
            _install_service_stub(Path(tempfile.mkdtemp()))
            self.addCleanup(_remove_service_stub)
        from aleggo.study.research_store import _safe_json

        self.assertEqual(_safe_json('{"a": 1}', {}), {"a": 1})
        self.assertEqual(_safe_json("broken{", {}), {})
        self.assertEqual(_safe_json("", []), [])
        self.assertEqual(_safe_json(None, {}), {})
        self.assertEqual(_safe_json("[1, 2]", {}), [1, 2])


class BudgetRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Install the service stub ONCE: research_store binds get_aleggo_service
        # at import time, so per-test re-stubbing has no effect.
        cls.tmp = Path(tempfile.mkdtemp(prefix="round11-"))
        _install_service_stub(cls.tmp)
        cls.addClassCleanup(_remove_service_stub)

    def setUp(self):
        from aleggo.study.research_store import ResearchStore
        from aleggo.study.research_domain import ResearchDomainStore

        self.store = ResearchStore()
        self.domain = ResearchDomainStore(self.store)
        self.pid = f"p-{self.id().split('.')[-1]}"
        self.branch = "main"
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO research_projects(id,title,question,status,model_id,current_version,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (self.pid, "t", "q", "ACTIVE", "m", 0, "2026-01-01", "2026-01-01"),
            )
            conn.execute(
                "INSERT INTO research_branches(id,project_id,name,status,limits_json,used_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (f"b-{self.pid}", self.pid, self.branch, "OPEN",
                 json.dumps({"evaluations": 10_000, "proofAttempts": 10_000, "toolSeconds": 10_000}),
                 json.dumps({"evaluations": 0, "proofAttempts": 0, "toolSeconds": 0}),
                 "2026-01-01", "2026-01-01"),
            )

    def _used(self) -> dict:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT used_json FROM research_branches WHERE project_id=? AND name=?",
                (self.pid, self.branch)).fetchone()
        return json.loads(row["used_json"])

    def test_concurrent_consume_no_lost_update(self):
        from aleggo.study.research_domain import _BUDGET_LOCK

        self.assertIsInstance(_BUDGET_LOCK, type(threading.Lock()))
        threads, per_thread = 8, 25
        errors: list[BaseException] = []

        def worker():
            try:
                for _ in range(per_thread):
                    self.domain.consume_budget(self.pid, self.branch, "toolSeconds", 1)
            except BaseException as exc:  # noqa: BLE001 - collected for assertion
                errors.append(exc)

        workers = [threading.Thread(target=worker) for _ in range(threads)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(timeout=60)
        self.assertFalse(any(t.is_alive() for t in workers), "worker threads hung")
        self.assertEqual(errors, [])
        self.assertEqual(self._used()["toolSeconds"], threads * per_thread)

    def test_exhaustion_still_raises(self):
        self.domain.consume_budget(self.pid, self.branch, "evaluations", 10_000)
        with self.assertRaises(ValueError):
            self.domain.consume_budget(self.pid, self.branch, "evaluations", 1)
        with self.assertRaises(ValueError):
            self.domain.consume_budget(self.pid, self.branch, "evaluations", 1)


if __name__ == "__main__":
    unittest.main()
