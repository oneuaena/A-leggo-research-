"""Round 26 standalone tests.

The evaluation harness (``research_eval``) and the research test-suite could
not run outside the host app, for three stacked reasons:

1. ``research_store`` imported the host-only ``aleggo.study.service`` at module
   level, although it is only needed when no store is injected -- every
   ``ResearchStore(AleggoStore(...))`` construction paid for a host dependency
   it never used.  The import is now lazy inside ``__init__``.
2. ``aleggo.study.store.AleggoStore`` -- referenced by ``research_eval.run()``,
   ``research_sharing.owner_store()`` and three test modules, always built
   from a plain directory -- did not exist anywhere in the delivered tree
   (same family as the Round 23 ``research_inputs`` and Round 24
   ``research_documents`` findings).  It is rebuilt here per the call sites:
   ``root`` / ``path`` / ``snapshot_database(name)``.
3. ``model_registry`` and ``math_databases`` imported ``httpx`` at module
   level, although the evaluation harness injects a fake router and the tool
   catalog only reads database *schemas* at import time.  ``httpx`` is now
   imported lazily inside the two functions that perform real HTTP.

Additionally the eval's ``FakeRouter`` still parsed the pre-Round-11 bare
tool-result format; since Round 11 tool results are wrapped in
``<<<TOOL_RESULT_BEGIN/END>>>`` markers, the fake is updated to the envelope.

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class ImportBoundaryTests(unittest.TestCase):
    def _fresh_import(self, module: str, forbid: tuple[str, ...]) -> None:
        code = (
            "import sys; sys.path.insert(0, %r); "
            "import %s; "
            "missing = [m for m in %r if m in sys.modules]; "
            "assert not missing, missing; print('ok')"
        ) % (str(ROOT), module, forbid)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn("ok", proc.stdout)

    def test_research_store_importable_without_host_service(self):
        self._fresh_import("aleggo.study.research_store", ("aleggo.study.service",))

    def test_model_registry_importable_without_httpx(self):
        self._fresh_import("aleggo.study.model_registry", ("httpx",))

    def test_math_databases_importable_without_httpx(self):
        self._fresh_import("aleggo.study.math_databases", ("httpx",))

    def test_lean_autoprove_importable_without_host_or_httpx(self):
        self._fresh_import("aleggo.study.lean_autoprove", ("httpx", "aleggo.study.service"))

    def test_research_store_without_injected_store_needs_host(self):
        from aleggo.study.research_store import ResearchStore

        with self.assertRaises(ModuleNotFoundError):
            ResearchStore()

    def test_database_lookup_without_httpx_raises_module_not_found(self):
        # The lazy import must surface as a clear ModuleNotFoundError at call
        # time, not some unrelated failure.
        from aleggo.study import math_databases

        async def go():
            await math_databases._get("https://www.oeis.org/search?q=1%2C1%2C2%2C3&fmt=json")

        with self.assertRaises(ModuleNotFoundError) as ctx:
            asyncio.run(go())
        self.assertIn("httpx", str(ctx.exception))


class AleggoStoreTests(unittest.TestCase):
    def test_interface(self):
        from aleggo.study.store import AleggoStore

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "nested" / "data"
            store = AleggoStore(root)
            self.assertTrue(root.is_dir())
            self.assertEqual(store.root, root)
            self.assertEqual(store.path, root / "aleggo.sqlite3")
            # No database yet: snapshot is a no-op returning None.
            self.assertIsNone(store.snapshot_database("pre-upgrade"))

    def test_snapshot_copies_database(self):
        from aleggo.study.store import AleggoStore

        with tempfile.TemporaryDirectory() as temp:
            store = AleggoStore(Path(temp))
            store.path.write_bytes(b"sqlite-format-3\0payload")
            backup = store.snapshot_database("research-schema-v5-to-v7")
            self.assertIsNotNone(backup)
            assert backup is not None
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.read_bytes(), b"sqlite-format-3\0payload")
            self.assertIn("research-schema-v5-to-v7", backup.name)
            # Hostile names cannot escape the root directory.
            evil = store.snapshot_database("../../etc/passwd")
            self.assertIsNotNone(evil)
            assert evil is not None
            self.assertEqual(evil.parent, store.root)

    def test_research_store_throwaway_roundtrip(self):
        from aleggo.study.research_store import ResearchStore
        from aleggo.study.store import AleggoStore

        with tempfile.TemporaryDirectory() as temp:
            store = ResearchStore(AleggoStore(Path(temp) / "data"))
            project = store.create_project("素数有无穷多个", "test.model")
            self.assertTrue(project["id"])
            self.assertEqual(store.get_project(project["id"])["id"], project["id"])
            self.assertEqual(len(store.list_projects()), 1)
            with store.connection() as conn:
                row = conn.execute("SELECT value FROM meta WHERE key='research_schema_version'").fetchone()
            self.assertEqual(int(row[0]), 7)
            # Reopening the same root must not create upgrade backups.
            before = {item.name for item in store.aleggo_store.root.iterdir()}
            ResearchStore(AleggoStore(Path(temp) / "data"))
            after = {item.name for item in store.aleggo_store.root.iterdir()}
            self.assertEqual(before, after)

    def test_schema_upgrade_takes_snapshot(self):
        from aleggo.study.research_store import ResearchStore
        from aleggo.study.store import AleggoStore

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "data"
            backing = AleggoStore(root)
            conn = sqlite3.connect(backing.path)
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO meta VALUES ('research_schema_version', '5')")
            conn.execute("CREATE TABLE filler (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()
            ResearchStore(backing)
            backups = [item for item in root.iterdir() if item.suffix == ".bak"]
            self.assertEqual(len(backups), 1)
            self.assertIn("research-schema-v5-to-v7", backups[0].name)


if __name__ == "__main__":
    unittest.main()
