"""Round 23 standalone tests.

Covers the two research-side findings of this round:

1. ``aleggo.study.research_inputs`` was imported by ``research_runtime`` and the
   ``research`` API router but the module was never delivered -- the whole
   research API failed to import.  The module now exists; these tests pin its
   contract: ``normalize_input_requests`` coercion, idempotent ``register``,
   ``pending``/``guard``/``pause``/``respond``/``context`` semantics and the
   ``KeyError``/``ValueError`` paths the router maps to 404/422.
2. Project lifecycle races in ``research_runtime``:
   - ``start_project`` used to return silently on ``status == "RUNNING"``
     without checking whether a task was actually alive, so a stale RUNNING
     status (e.g. server restart mid-run) left the project permanently
     unstartable (start no-ops, resume raises).  It now recovers with a fresh
     start while still de-duplicating a genuinely live run.
   - ``stop_project`` read ``_RUNS`` without ``_RUNS_LOCK``; it now holds the
     lock for the get/cancel/await sequence.

unittest style (no pytest on this machine); also collected by pytest in CI.
``aleggo.study.service`` and ``httpx`` are stubbed because neither exists in
this environment.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_SERVICE_NAME = "aleggo.study.service"

# NOTE: stubs are installed lazily in LifecycleTests.setUpClass (not at import
# time).  Installing a process-global stub during discovery lets unrelated
# modules (e.g. research_delivery -> research_store) import successfully and
# permanently binds the stub inside research_store, breaking the other round
# tests that install their own stubs per class.
_STUB_STATE: dict[str, object] = {}


def _install_stubs(tmp: Path) -> None:
    """Install import-time stubs needed to import research_runtime here."""
    if "httpx" not in sys.modules:
        sys.modules["httpx"] = types.ModuleType("httpx")
        _STUB_STATE["httpx"] = True
    if _SERVICE_NAME not in sys.modules:
        service = types.ModuleType(_SERVICE_NAME)
        fake_store = SimpleNamespace(path=tmp / "stub.db",
                                     snapshot_database=lambda *a, **k: None)
        service.get_aleggo_service = lambda: SimpleNamespace(store=fake_store)
        sys.modules[_SERVICE_NAME] = service
        _STUB_STATE["service"] = True


def _remove_stubs() -> None:
    if _STUB_STATE.pop("service", False):
        sys.modules.pop(_SERVICE_NAME, None)
    if _STUB_STATE.pop("httpx", False):
        sys.modules.pop("httpx", None)


def tearDownModule() -> None:  # noqa: N807 - unittest hook
    _remove_stubs()


from aleggo.study.research_inputs import ResearchInputs, normalize_input_requests  # noqa: E402


class _FakeStore:
    """Minimal store surface used by ResearchInputs / start/stop_project."""

    def __init__(self, path: Path) -> None:
        self._path = str(path)
        self._projects: dict[str, dict] = {}

    @contextlib.contextmanager
    def connection(self):
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add_project(self, project: dict) -> dict:
        self._projects[project["id"]] = project
        return project

    def get_project(self, project_id: str):
        return self._projects.get(project_id)

    def set_status(self, project_id: str, status: str, *, error: str = "", actor: str = "system"):
        project = self._projects.get(project_id)
        if project is not None:
            project["status"] = status
            project["last_error"] = error
        return project


def _make_project(pid: str, status: str = "APPROVED") -> dict:
    return {"id": pid, "approvedVersion": 3, "currentVersion": 3,
            "status": status, "plan": {}, "last_error": ""}


class NormalizeInputRequestsTests(unittest.TestCase):
    def test_none_and_non_list_become_empty(self):
        self.assertEqual(normalize_input_requests(None), [])
        self.assertEqual(normalize_input_requests("label"), [])
        self.assertEqual(normalize_input_requests({"label": "x"}), [])

    def test_dict_items_normalised(self):
        out = normalize_input_requests([
            {"label": " 定义 ", "reason": "需要形式化定义", "kind": "document"},
            {"label": "数据集", "kind": "weird-kind"},
        ])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {"label": "定义", "reason": "需要形式化定义", "kind": "document"})
        self.assertEqual(out[1]["kind"], "text")  # unknown kind falls back

    def test_bare_strings_and_empty_labels(self):
        out = normalize_input_requests(["  论文原文  ", "   ", 42, None])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["label"], "论文原文")

    def test_caps_applied(self):
        big = [{"label": "x" * 500, "reason": "y" * 5000, "kind": "text"}]
        out = normalize_input_requests(big)
        self.assertEqual(len(out[0]["label"]), 200)
        self.assertEqual(len(out[0]["reason"]), 2000)
        many = [{"label": f"q{i}"} for i in range(80)]
        self.assertEqual(len(normalize_input_requests(many)), 50)


class ResearchInputsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = _FakeStore(Path(self._tmp.name) / "research.db")
        self.inputs = ResearchInputs(self.store)
        self.pid = "proj_round23"

    def tearDown(self):
        self._tmp.cleanup()

    def test_register_idempotent(self):
        reqs = [{"label": "定义", "reason": "r", "kind": "text"}]
        self.assertEqual(self.inputs.register(self.pid, reqs), 1)
        self.assertEqual(self.inputs.register(self.pid, reqs), 0)  # duplicate skipped
        self.assertEqual(len(self.inputs.pending(self.pid)), 1)

    def test_reopen_creates_new_row_preserving_history(self):
        reqs = [{"label": "论文", "kind": "document"}]
        self.inputs.register(self.pid, reqs)
        rid = self.inputs.pending(self.pid)[0]["id"]
        self.inputs.respond(self.pid, rid, "已上传", ["doc1"])
        self.assertEqual(self.inputs.register(self.pid, reqs), 0)  # answered, no reopen
        self.assertEqual(self.inputs.register(self.pid, reqs, reopen=True), 1)
        pending = self.inputs.pending(self.pid)
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0]["id"], rid)
        # history: the answered row still exists
        self.assertEqual(len(self.inputs.list(self.pid)), 2)

    def test_step_scoped_dedupe(self):
        reqs = [{"label": "定义"}]
        self.assertEqual(self.inputs.register(self.pid, reqs, step_id="s1"), 1)
        self.assertEqual(self.inputs.register(self.pid, reqs, step_id="s2"), 1)  # different step
        self.assertEqual(self.inputs.register(self.pid, reqs, step_id="s1"), 0)

    def test_guard_blocks_until_answered(self):
        self.inputs.register(self.pid, [{"label": "定义"}, {"label": "数据集"}])
        with self.assertRaises(PermissionError):
            self.inputs.guard(self.pid)
        for row in self.inputs.pending(self.pid):
            self.inputs.respond(self.pid, row["id"], "好的")
        self.inputs.guard(self.pid)  # no raise now

    def test_pause_keeps_requests_unresolved(self):
        self.inputs.register(self.pid, [{"label": "定义"}])
        self.assertEqual(self.inputs.pause(self.pid), 1)
        pending = self.inputs.pending(self.pid)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "paused")
        with self.assertRaises(PermissionError):
            self.inputs.guard(self.pid)  # paused still blocks start

    def test_respond_errors(self):
        self.inputs.register(self.pid, [{"label": "定义"}])
        rid = self.inputs.pending(self.pid)[0]["id"]
        with self.assertRaises(KeyError):
            self.inputs.respond(self.pid, "input_nope", "x")
        with self.assertRaises(ValueError):
            self.inputs.respond(self.pid, rid, "   ")
        self.inputs.respond(self.pid, rid, "收到")
        with self.assertRaises(ValueError):
            self.inputs.respond(self.pid, rid, "重复")

    def test_context_renders_answered(self):
        self.assertEqual(self.inputs.context(self.pid), "")
        self.inputs.register(self.pid, [{"label": "关键定义"}])
        rid = self.inputs.pending(self.pid)[0]["id"]
        self.inputs.respond(self.pid, rid, "群的定义如下…", ["doc9"])
        ctx = self.inputs.context(self.pid)
        self.assertIn("关键定义", ctx)
        self.assertIn("群的定义如下…", ctx)
        self.assertIn("doc9", ctx)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    """start_project / stop_project against a fake store."""

    @classmethod
    def setUpClass(cls):
        cls._stub_tmp = tempfile.TemporaryDirectory()
        _install_stubs(Path(cls._stub_tmp.name))
        import aleggo.study.research_runtime as rr
        cls.rr = rr
        cls._orig_execute = rr.MultiAgentRuntime.execute_project

    @classmethod
    def tearDownClass(cls):
        cls.rr.MultiAgentRuntime.execute_project = cls._orig_execute
        cls._stub_tmp.cleanup()

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = _FakeStore(Path(self._tmp.name) / "research.db")
        self.calls: list[tuple[str, bool]] = []
        self._block_execute = False
        self._release = asyncio.Event()
        started = asyncio.Event()

        async def _fake_execute(runtime_self, project_id: str, *, resume: bool = False):
            self.calls.append((project_id, resume))
            started.set()
            if self._block_execute:
                await self._release.wait()

        self.rr.MultiAgentRuntime.execute_project = _fake_execute
        self._started = started

    async def asyncTearDown(self):
        self._release.set()
        for task in list(self.rr._RUNS.values()):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.rr._RUNS.clear()
        self.rr.MultiAgentRuntime.execute_project = self._orig_execute
        self._tmp.cleanup()

    async def _drain(self, seconds: float = 5.0):
        await asyncio.wait_for(self._started.wait(), timeout=seconds)
        await asyncio.sleep(0)

    async def test_start_recovers_stale_running_status(self):
        """RUNNING status but no live task (e.g. restart) -> fresh start, not a silent no-op."""
        pid = "stale_running"
        self.store.add_project(_make_project(pid, status="RUNNING"))
        self.assertEqual(self.rr._RUNS.get(pid), None)

        await self.rr.start_project(pid, self.store)

        await self._drain()
        self.assertEqual(self.calls, [(pid, False)])
        self.assertEqual(self.store.get_project(pid)["status"], "RUNNING")

    async def test_start_dedups_genuinely_live_run(self):
        """A live task + RUNNING status -> second start returns without replacing it."""
        pid = "live_run"
        self.store.add_project(_make_project(pid, status="RUNNING"))

        async def _sleeper():
            await asyncio.sleep(30)

        live = asyncio.create_task(_sleeper())
        self.rr._RUNS[pid] = live
        try:
            await self.rr.start_project(pid, self.store)
            self.assertIs(self.rr._RUNS.get(pid), live)
            self.assertEqual(self.calls, [])
        finally:
            live.cancel()
            await asyncio.gather(live, return_exceptions=True)

    async def test_start_then_stop_cancels_task(self):
        pid = "stop_me"
        self.store.add_project(_make_project(pid, status="APPROVED"))
        self._block_execute = True  # keep the run task alive for stop_project
        await self.rr.start_project(pid, self.store)
        await self._drain()
        task = self.rr._RUNS.get(pid)
        self.assertIsNotNone(task)

        await self.rr.stop_project(pid, self.store)

        self.assertTrue(task.done())
        await asyncio.sleep(0)  # let the done-callback pop the entry
        self.assertIsNot(self.rr._RUNS.get(pid), task)

    async def test_stop_without_task_is_noop(self):
        await self.rr.stop_project("no_such_run", self.store)  # must not raise

    async def test_blocked_start_when_inputs_pending(self):
        """guard() still blocks start_project while inputs are unresolved."""
        pid = "needs_input"
        self.store.add_project(_make_project(pid, status="APPROVED"))
        ResearchInputs(self.store).register(pid, [{"label": "关键定义"}])
        with self.assertRaises(PermissionError):
            await self.rr.start_project(pid, self.store)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
