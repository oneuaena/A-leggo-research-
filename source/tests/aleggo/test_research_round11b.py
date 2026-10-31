"""Round 11b tests: fixes for the core-audit MEDIUM items (second pass).

Covers:
- research_sharing.update_assignment: only the assignee or a moderator may flip
  an assignment's status (viewers get PermissionError).
- research_sharing.delete_comment: child replies are reparented, not orphaned.
- research_sharing.remove_member(store=...): open assignments of a removed
  member are cleaned up.
- research_sharing.assign_step: serialized DELETE+INSERT (smoke test).
- research_workbench checkpoint lock: concurrent charge_step calls cannot
  lost-update each other.
- research_workbench.reset_stalled_attempt: attemptHistory is compacted and capped.
- research_workbench.complete_milestone: snapshot stores IDs/counts, not the
  whole evidence graph.
- research_memory.add: bad FK -> ValueError; str tags -> single tag.
- research_memory.promote: keeps source_artifact_id.
- research_memory.related_projects: tolerates "steps": null and non-dict steps.
- research_domain.update_branch_limits: an exhausted branch is not silently revived.
- research_domain.confirm_formal_binding: duplicate content raises instead of
  returning another binding's payload.
- research_domain.create_branch / add_graph_edge: duplicate -> Chinese ValueError.
- research_domain.save_source: non-numeric relevance degrades to 0.
- research_domain.create_graph_node: cross-branch artifact mount rejected.

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import json
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


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="round11b-"))
        _install_service_stub(cls.tmp)
        cls.addClassCleanup(_remove_service_stub)

    def setUp(self):
        from aleggo.study.research_store import ResearchStore
        from aleggo.study.research_domain import ResearchDomainStore
        from aleggo.study.research_workbench import ResearchWorkbench

        self.store = ResearchStore()
        self.domain = ResearchDomainStore(self.store)
        self.workbench = ResearchWorkbench(self.store)
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


# --------------------------------------------------------------------------- #
# research_sharing
# --------------------------------------------------------------------------- #

class SharingPermissionTests(_Base):
    def setUp(self):
        super().setUp()
        import aleggo.study.research_sharing as sharing

        self.sharing = sharing
        self._orig_identity = sharing.current_identity
        self.me = {"id": "u-owner", "username": "wang", "isAdmin": False}
        sharing.current_identity = lambda: self.me  # noqa: E731
        self.addCleanup(setattr, sharing, "current_identity", self._orig_identity)
        # isolate the share registry
        self._orig_env = __import__("os").environ.get("ALEGGO_RESEARCH_SHARES_FILE")
        __import__("os").environ["ALEGGO_RESEARCH_SHARES_FILE"] = str(self.tmp / f"shares-{self.pid}.json")

    def tearDown(self):
        import os

        if self._orig_env is None:
            os.environ.pop("ALEGGO_RESEARCH_SHARES_FILE", None)
        else:
            os.environ["ALEGGO_RESEARCH_SHARES_FILE"] = self._orig_env
        super().tearDown()

    def _plan_with_step(self):
        self.store.save_plan(
            self.pid,
            {"objective": "o", "steps": [{"id": "s1", "title": "t", "description": ""}]},
            changed_by="researcher", change_reason="init", change_summary="test")

    def test_update_assignment_viewer_blocked(self):
        self._plan_with_step()
        task = self.sharing.assign_step(self.store, self.pid, "s1", "u-li", "li", "")
        # assignee may flip it
        self.me.update({"id": "u-li", "username": "li"})
        self.assertEqual(
            self.sharing.update_assignment(self.store, self.pid, task["id"], "done")["status"], "done")
        # a stranger (viewer) may not
        self.me.update({"id": "u-zhao", "username": "zhao"})
        with self.assertRaises(PermissionError):
            self.sharing.update_assignment(self.store, self.pid, task["id"], "open")
        # a moderator may
        self.assertEqual(
            self.sharing.update_assignment(self.store, self.pid, task["id"], "open",
                                           can_moderate=True)["status"], "open")

    def test_delete_comment_reparents_children(self):
        c1 = self.sharing.add_comment(self.store, self.pid, "parent", target_type="step", target_id="s1")
        c2 = self.sharing.add_comment(self.store, self.pid, "child", target_type="step", target_id="s1",
                                      parent_id=c1["id"])
        self.sharing.delete_comment(self.store, self.pid, c1["id"])
        with self.store.connection() as conn:
            row = conn.execute("SELECT parent_id FROM research_comments WHERE id=?", (c2["id"],)).fetchone()
        self.assertIsNotNone(row, "child reply must survive")
        self.assertFalse(row["parent_id"], "child becomes top-level instead of orphaned")

    def test_remove_member_cleans_open_assignments(self):
        self._plan_with_step()
        task = self.sharing.assign_step(self.store, self.pid, "s1", "u-li", "li", "")
        # register a share entry for u-li so remove_member finds it
        import os, json as _json
        shares_file = Path(os.environ["ALEGGO_RESEARCH_SHARES_FILE"])
        shares_file.write_text(_json.dumps({"shares": [{
            "projectId": self.pid, "memberId": "u-li", "ownerId": "u-owner",
            "ownerUsername": "wang", "role": "editor"}]}), encoding="utf-8")
        self.assertTrue(self.sharing.remove_member(self.pid, "u-li", self.store))
        with self.store.connection() as conn:
            rows = conn.execute("SELECT id FROM research_assignments WHERE id=?", (task["id"],)).fetchall()
        self.assertEqual(rows, [], "open assignment of removed member must be gone")


# --------------------------------------------------------------------------- #
# research_workbench
# --------------------------------------------------------------------------- #

class CheckpointConcurrencyTests(_Base):
    def test_concurrent_charge_step_no_lost_update(self):
        from aleggo.study.research_workbench import _CHECKPOINT_LOCK

        self.assertIsInstance(_CHECKPOINT_LOCK, type(threading.RLock()))
        step_id = "step-race"
        self.workbench.save_checkpoint(self.pid, 0, step_id, phase="execution")
        threads, per_thread = 8, 25
        errors: list[BaseException] = []

        def worker():
            try:
                for _ in range(per_thread):
                    # elapsed_seconds path exercises the checkpoint
                    # read-modify-write without needing a workbench state row.
                    self.workbench.charge_step(self.pid, 0, step_id, elapsed_seconds=1.5)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        workers = [threading.Thread(target=worker) for _ in range(threads)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(timeout=60)
        self.assertFalse(any(t.is_alive() for t in workers))
        self.assertEqual(errors, [])
        got = (self.workbench.checkpoint(self.pid, 0, step_id) or {})["resources"]
        self.assertEqual(got["workerSeconds"], threads * per_thread * 1.5)

    def test_reset_stalled_attempt_compacts_history(self):
        step_id = "step-stall"
        self.workbench.save_checkpoint(
            self.pid, 0, step_id, phase="execution",
            payload={"bigBlob": "x" * 5000, "expectedBudget": {"llmCalls": 4}})
        for _ in range(3):
            self.workbench.reset_stalled_attempt(self.pid, 0, step_id)
        payload = (self.workbench.checkpoint(self.pid, 0, step_id) or {})["payload"]
        history = payload["attemptHistory"]
        self.assertEqual(len(history), 3)
        for entry in history:
            self.assertNotIn("bigBlob", entry, "history must not copy the full payload")
            self.assertIn("attempt", entry)
        # cap at 10
        for _ in range(12):
            self.workbench.reset_stalled_attempt(self.pid, 0, step_id)
        payload = (self.workbench.checkpoint(self.pid, 0, step_id) or {})["payload"]
        self.assertLessEqual(len(payload["attemptHistory"]), 10)

    def test_complete_milestone_snapshot_is_compact(self):
        self.domain.create_graph_node(self.pid, "theorem", "T", branch_name="main")
        report = self.workbench.complete_milestone(self.pid, 0, "M1", ["s1"])
        snapshot = report["snapshot"]
        self.assertIn("nodeIds", snapshot["evidence"])
        self.assertNotIn("nodes", snapshot["evidence"], "full graph must not be duplicated")
        self.assertIn("count", snapshot["literature"])
        # checkpoint payload must be small too
        key = "milestone:" + __import__("hashlib").sha256("M1".encode()).hexdigest()[:20]
        payload = (self.workbench.checkpoint(self.pid, 0, key) or {})["payload"]
        self.assertLess(len(json.dumps(payload)), 8000)


# --------------------------------------------------------------------------- #
# research_memory
# --------------------------------------------------------------------------- #

class MemoryRobustnessTests(_Base):
    def setUp(self):
        super().setUp()
        from aleggo.study.research_memory import ResearchMemory

        self.memory = ResearchMemory(self.store)

    def test_add_bad_foreign_key_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.memory.add("hello", project_id="no-such-project")

    def test_add_str_tags_become_single_tag(self):
        item = self.memory.add("tag test", project_id=self.pid, tags="重要")
        self.assertEqual(item["tags"], ["重要"])

    def test_update_str_tags_become_single_tag(self):
        item = self.memory.add("tag test 2", project_id=self.pid, tags=["a"])
        updated = self.memory.update(item["id"], tags="重要")
        self.assertEqual(updated["tags"], ["重要"])

    def test_promote_keeps_source_artifact(self):
        artifact = self.store.create_artifact(
            self.pid, 0, "s1", "tester", "A", "content", branch_name="main")
        item = self.memory.add("prov", project_id=self.pid, source_artifact_id=artifact["id"])
        promoted = self.memory.promote(self.pid, item["id"])
        self.assertEqual(promoted["sourceArtifactId"], artifact["id"])

    def test_related_projects_tolerates_dirty_plans(self):
        other = "p-dirty"
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO research_projects(id,title,question,status,model_id,current_version,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (other, "t", "Catalan numbers", "ACTIVE", "m", 0, "2026-01-01", "2026-01-01"),
            )
            # write dirty plans straight to the DB (save_plan would reject them)
            conn.execute(
                "INSERT INTO research_plan_versions(project_id,version,plan_json,changed_by,change_reason,change_summary,approved,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (other, 0, json.dumps({"objective": "o", "steps": None}),
                 "r", "x", "y", 0, "2026-01-01"),
            )
        # must not raise TypeError on "steps": null
        self.memory.related_projects(self.pid, "Catalan numbers")
        with self.store.connection() as conn:
            conn.execute("UPDATE research_plan_versions SET plan_json=? WHERE project_id=? AND version=0",
                         (json.dumps({"objective": "o", "steps": ["raw string", {"title": "t2"}, None]}), other))
        self.memory.related_projects(self.pid, "Catalan numbers")


# --------------------------------------------------------------------------- #
# research_domain
# --------------------------------------------------------------------------- #

class DomainRobustnessTests(_Base):
    def test_update_branch_limits_does_not_revive_exhausted(self):
        self.domain.consume_budget(self.pid, "main", "evaluations", 10_000)
        # consuming exactly to the limit keeps OPEN; one more tips it over
        with self.assertRaises(ValueError):
            self.domain.consume_budget(self.pid, "main", "evaluations", 1)
        branch = self.domain.get_branch(self.pid, "main")
        self.assertEqual(branch["status"], "BUDGET_EXHAUSTED")
        # raising one limit but leaving evaluations exhausted -> stays exhausted
        updated = self.domain.update_branch_limits(self.pid, "main", {"toolSeconds": 50_000})
        self.assertEqual(updated["status"], "BUDGET_EXHAUSTED")
        # raising the exhausted limit above usage -> reopens
        updated = self.domain.update_branch_limits(self.pid, "main", {"evaluations": 20_000})
        self.assertEqual(updated["status"], "OPEN")

    def test_duplicate_branch_name_chinese_error(self):
        self.domain.create_branch(self.pid, "exp")
        with self.assertRaises(ValueError) as ctx:
            self.domain.create_branch(self.pid, "exp")
        self.assertIn("分支名已存在", str(ctx.exception))

    def test_duplicate_edge_chinese_error(self):
        n1 = self.domain.create_graph_node(self.pid, "theorem", "T1", branch_name="main")
        n2 = self.domain.create_graph_node(self.pid, "lemma", "L1", branch_name="main")
        self.domain.add_graph_edge(self.pid, n1["id"], n2["id"], "SUPPORTS")
        with self.assertRaises(ValueError) as ctx:
            self.domain.add_graph_edge(self.pid, n1["id"], n2["id"], "SUPPORTS")
        self.assertIn("已存在", str(ctx.exception))

    def test_save_source_non_numeric_relevance(self):
        # research_literature needs httpx (absent here); stub the two helpers.
        fake_lit = types.ModuleType("aleggo.study.research_literature")
        fake_lit.normalize_doi = lambda v: ""
        fake_lit.normalize_arxiv_id = lambda v: ""
        sys.modules["aleggo.study.research_literature"] = fake_lit
        self.addCleanup(sys.modules.pop, "aleggo.study.research_literature", None)
        src = self.domain.save_source(self.pid, {"title": "t", "relevance": "high"})
        self.assertEqual(src["relevance"], 0)

    def test_create_graph_node_rejects_cross_branch_artifact(self):
        self.domain.create_branch(self.pid, "exp")
        artifact = self.store.create_artifact(
            self.pid, 0, "s1", "tester", "A", "content", branch_name="exp")
        with self.assertRaises(ValueError) as ctx:
            self.domain.create_graph_node(self.pid, "theorem", "T", branch_name="main",
                                          artifact_id=artifact["id"])
        self.assertIn("另一分支", str(ctx.exception))

    def test_confirm_formal_binding_duplicate_raises(self):
        claim = self.domain.create_graph_node(self.pid, "theorem", "若 p 则 q", branch_name="main")
        b1 = self.domain.create_formal_binding(
            self.pid, claim["id"], "若 p 则 q", "p -> q", "theorem t : p -> q",
            confirm_mapping=False)
        # a second binding with different content
        b2 = self.domain.create_formal_binding(
            self.pid, claim["id"], "若 p 则 q", "p -> r", "theorem t : p -> r",
            confirm_mapping=False)
        self.domain.confirm_formal_binding(
            self.pid, b1["id"], "若 p 则 q", "p -> q", "theorem t : p -> q")
        # confirming b2 with b1's content must not silently return b1's payload
        with self.assertRaises(ValueError) as ctx:
            self.domain.confirm_formal_binding(
                self.pid, b2["id"], "若 p 则 q", "p -> q", "theorem t : p -> q")
        self.assertIn(b1["id"], str(ctx.exception))

    def test_safe_relevance(self):
        from aleggo.study.research_domain import _safe_relevance

        self.assertEqual(_safe_relevance("high"), 0.0)
        self.assertEqual(_safe_relevance(None), 0.0)
        self.assertEqual(_safe_relevance("0.75"), 0.75)
        self.assertEqual(_safe_relevance(2), 2.0)


if __name__ == "__main__":
    unittest.main()
