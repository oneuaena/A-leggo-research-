"""Round 24 standalone tests.

1. ``aleggo.study.research_documents`` did not exist in the delivered tree,
   but ``research_imports`` and the research API router import it at module
   level (``MAX_DOCUMENT_BYTES``, ``_split_text``, ``import_research_document``)
   -- the same class of breakage Round 23 fixed for ``research_inputs``.
   The module now exists with the interface pinned by every in-tree call
   site; these tests pin its contract.
2. ``research_eval.score`` had a duplicated ``elif kind == "answer_not_regex"``
   branch (dead copy-paste).  Removed; the check type still scores correctly.
3. ``research_mail_import.decode_base64_payload`` raised bare ``IndexError``
   for a ``data:`` URL without a comma (``"data:xxx".split(",", 1)[1]``),
   escaping the router's ``ValueError -> 422`` mapping as a 500.  It now
   raises ``ValueError``.

unittest style (no pytest on this machine); also collected by pytest in CI.
"""

from __future__ import annotations

import base64
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_STUB_STATE: dict[str, object] = {}


def _install_stubs() -> None:
    """Stubs needed to import research_mail_import (httpx, host service)."""
    if "httpx" not in sys.modules:
        httpx = types.ModuleType("httpx")
        httpx.HTTPError = type("HTTPError", (Exception,), {})
        sys.modules["httpx"] = httpx
        _STUB_STATE["httpx"] = True
    name = "aleggo.study.service"
    if name not in sys.modules:
        service = types.ModuleType(name)
        service.get_aleggo_service = lambda: None
        sys.modules[name] = service
        _STUB_STATE[name] = True


def _remove_stubs() -> None:
    for name in ("httpx", "aleggo.study.service"):
        if _STUB_STATE.pop(name, False):
            sys.modules.pop(name, None)


class ResearchDocumentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from aleggo.study import research_documents as docs

        cls.docs = docs

    def test_size_cap_matches_callers(self):
        self.assertEqual(self.docs.MAX_DOCUMENT_BYTES, 25 * 1024 * 1024)

    def test_split_text_chunks_within_cap(self):
        short = "hello world"
        self.assertEqual(self.docs._split_text(short), [short])
        self.assertEqual(self.docs._split_text("   \n\n  "), [])
        long = "段落一。\n\n" + "x" * 9000 + "\n\n段落三。"
        parts = self.docs._split_text(long)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), 4000)
        # Chunking only moves boundaries; no content is lost or duplicated.
        self.assertEqual("".join(parts).replace("\n\n", ""), "段落一。" + "x" * 9000 + "段落三。")
        # Document order is preserved: the first paragraph stays in chunk 0.
        self.assertTrue(parts[0].startswith("段落一。"))
        self.assertTrue(parts[-1].endswith("段落三。"))

    def test_import_rejects_empty_and_oversize(self):
        domain = _FakeDomain()
        with self.assertRaises(ValueError):
            self.docs.import_research_document(domain, "p1", "a.txt", b"")
        with self.assertRaises(ValueError):
            self.docs.import_research_document(domain, "p1", "a.txt", b"x" * (self.docs.MAX_DOCUMENT_BYTES + 1))
        with self.assertRaises(ValueError):
            self.docs.import_research_document(domain, "p1", "   ", b"data")
        with self.assertRaises(ValueError):
            self.docs.import_research_document(domain, "p1", "a.txt", b"   \n  ")

    def test_import_saves_source_with_chunks(self):
        domain = _FakeDomain()
        source = self.docs.import_research_document(domain, "p1", "notes.txt", "定理内容。\n\n证明过程。".encode("utf-8"))
        self.assertEqual(source["title"], "notes.txt")
        self.assertEqual(source["id"], "source_1")
        self.assertEqual(source["chunkCount"], 1)
        saved = domain.saved
        self.assertEqual(saved["project_id"], "p1")
        self.assertEqual(saved["data"]["provider"], "local-document")
        self.assertEqual(saved["original_file"], "定理内容。\n\n证明过程。".encode("utf-8"))
        self.assertEqual(len(saved["chunks"]), 1)

    def test_import_sanitises_filename(self):
        domain = _FakeDomain()
        source = self.docs.import_research_document(domain, "p1", "../../etc/passwd", b"data")
        self.assertEqual(source["title"], "passwd")


class _FakeDomain:
    """Minimal ResearchDomainStore double capturing save_source calls."""

    def __init__(self):
        self.saved: dict = {}
        self._n = 0

    def save_source(self, project_id, data, chunks=None, *, original_file=None):
        self._n += 1
        self.saved = {"project_id": project_id, "data": data, "chunks": chunks, "original_file": original_file}
        return {"id": f"source_{self._n}", "title": data["title"], "chunkCount": len(chunks or [])}


class EvalScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from aleggo.study import research_eval as ev

        cls.ev = ev

    def test_answer_not_regex_branch_occurs_once(self):
        # The duplicated ``elif kind == "answer_not_regex"`` dead branch is gone.
        source = Path(self.ev.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('kind == "answer_not_regex"'), 1)

    def test_answer_not_regex_check_behaviour(self):
        problem = {"checks": [{"type": "answer_not_regex", "pattern": "已被证明|has been proved"}]}
        ok = self.ev.score(problem, {"answer": "尚未被证明。"})
        bad = self.ev.score(problem, {"answer": "黎曼猜想已被证明。"})
        self.assertTrue(ok[0]["passed"])
        self.assertFalse(bad[0]["passed"])


class MailImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        from aleggo.study import research_mail_import as mail

        cls.mail = mail

    @classmethod
    def tearDownClass(cls):
        _remove_stubs()

    def test_data_url_without_comma_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.mail.decode_base64_payload("data:没有逗号的非法载荷")

    def test_valid_payloads_still_decode(self):
        self.assertEqual(self.mail.decode_base64_payload(base64.b64encode(b"hello").decode()), b"hello")
        self.assertEqual(
            self.mail.decode_base64_payload("data:text/plain;base64," + base64.b64encode(b"hi").decode()),
            b"hi",
        )
        with self.assertRaises(ValueError):
            self.mail.decode_base64_payload("!!!不是base64!!!")


class ImportChainTests(unittest.TestCase):
    """The Round 24 headline: research_imports must import without stubs."""

    @classmethod
    def setUpClass(cls):
        _install_stubs()

    @classmethod
    def tearDownClass(cls):
        _remove_stubs()

    def test_research_imports_imports_cleanly(self):
        for name in ("aleggo.study.research_imports", "aleggo.study.research_documents"):
            sys.modules.pop(name, None)
        import importlib

        imports = importlib.import_module("aleggo.study.research_imports")
        self.assertEqual(imports.MAX_DOCUMENT_BYTES, 25 * 1024 * 1024)
        self.assertTrue(callable(imports._split_text))


if __name__ == "__main__":
    unittest.main()
