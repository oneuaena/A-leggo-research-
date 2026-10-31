"""Round 12 tests: leftover audit items (workspace resolve, dirty JSON, truncation, latex, i18n).

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import json
import sys
import tempfile
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


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="round12-"))
        _install_service_stub(cls.tmp)
        cls.addClassCleanup(_remove_service_stub)

    def setUp(self):
        from aleggo.study.research_store import ResearchStore
        from aleggo.study.research_domain import ResearchDomainStore

        self.store = ResearchStore()
        self.domain = ResearchDomainStore(self.store)
        self.pid = f"p-{self.id().split('.')[-1]}"
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO research_projects(id,title,question,status,model_id,current_version,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (self.pid, "t", "q", "ACTIVE", "m", 0, "2026-01-01", "2026-01-01"),
            )
            conn.execute(
                "INSERT INTO research_branches(id,project_id,name,status,limits_json,used_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (f"b-{self.pid}", self.pid, "main", "OPEN",
                 json.dumps({"evaluations": 10_000, "proofAttempts": 10_000, "toolSeconds": 10_000}),
                 json.dumps({"evaluations": 0, "proofAttempts": 0, "toolSeconds": 0}),
                 "2026-01-01", "2026-01-01"),
            )


class WorkspaceResolveTests(unittest.TestCase):
    def test_missing_folder_chinese_error(self):
        from aleggo.study.research_workbench import _resolve_existing_dir

        with self.assertRaises(ValueError) as ctx:
            _resolve_existing_dir("/no/such/folder/xyz-123")
        self.assertIn("不存在", str(ctx.exception))

    def test_existing_folder_ok(self):
        from aleggo.study.research_workbench import _resolve_existing_dir

        self.assertTrue(_resolve_existing_dir(str(Path(tempfile.gettempdir()))).is_dir())


class SafeIntTests(unittest.TestCase):
    def test_safe_int(self):
        from aleggo.study.research_thread import _safe_int

        self.assertEqual(_safe_int(None), 0)
        self.assertEqual(_safe_int("abc"), 0)
        self.assertEqual(_safe_int("42"), 42)
        self.assertEqual(_safe_int(12.7), 12)
        self.assertEqual(_safe_int(7), 7)


class RecordToolRunTests(_Base):
    def test_output_truncated(self):
        run_id = self.domain.record_tool_run(
            self.pid, "t", {}, "x" * 300_000, "COMPLETED", elapsed_ms="bad")
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT output_text, elapsed_ms FROM research_tool_runs WHERE id=?", (run_id,)).fetchone()
        self.assertLess(len(row["output_text"]), 300_000)
        self.assertIn("截断", row["output_text"])
        self.assertEqual(row["elapsed_ms"], 0)


class MemoryUpdateTests(_Base):
    def test_update_non_str_content(self):
        from aleggo.study.research_memory import ResearchMemory

        mem = ResearchMemory(self.store)
        item = mem.add("hello", project_id=self.pid)
        updated = mem.update(item["id"], content=12345)
        self.assertEqual(updated["content"], "12345")


class LatexOptargTests(unittest.TestCase):
    def test_bracket_does_not_break_optional_argument(self):
        from aleggo.study.research_latex import build_latex_paper

        tables = {
            "research_projects": [{"title": "t", "question": "q"}],
            "research_graph_nodes": [{
                "id": "n1", "node_type": "theorem", "title": "若 a] 则 b",
                "content": "proof", "status": "unverified", "branch_name": "main",
                "artifact_id": None, "metadata_json": "{}", "created_at": "2026-01-01",
            }],
            "research_artifacts": [],
        }
        tex = build_latex_paper(tables)
        # the title inside \begin{env}[{...}] must not contain a bare ]
        line = next(l for l in tex.splitlines() if "[{" in l and l.startswith("\\begin{"))
        inner = line.split("[{", 1)[1].rsplit("}]", 1)[0]
        self.assertIn("{]}", inner)
        self.assertNotIn("]", inner.replace("{]}", ""))


if __name__ == "__main__":
    unittest.main()
