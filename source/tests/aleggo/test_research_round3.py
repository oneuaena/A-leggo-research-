"""Round 3 standalone tests: PDF blob continue, workbench JSON, thread events, Chinese errors."""

from __future__ import annotations

import ast
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _extract_functions(rel: str, names: set[str], *, include_assigns: bool = True) -> dict:
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aleggo"):
                continue
            keep.append(node)
        elif include_assigns and isinstance(node, (ast.Assign, ast.AnnAssign)):
            keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            keep.append(node)
    mod = ast.Module(body=keep, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {"__name__": "extracted"}
    exec(compile(mod, rel, "exec"), ns)
    return ns


def test_import_pdf_loads_blob_when_data_empty():
    """Source-level: continue_source_id + empty data must call get_document_blob."""
    src = (ROOT / "aleggo/study/research_imports.py").read_text(encoding="utf-8")
    assert "get_document_blob" in src
    assert "continue_source_id" in src
    assert "未找到已保存的 PDF 原文件" in src
    # Empty-data branch before MAX_DOCUMENT_BYTES check
    assert "if (not data) and continue_source_id:" in src or 'if (not data) and continue_source_id' in src


def test_continue_endpoint_and_has_original_file_flag():
    src = (ROOT / "aleggo/api/routers/research_extensions.py").read_text(encoding="utf-8")
    assert "continue_pdf_from_stored" in src
    assert "/imports/pdf/{source_id}/continue" in src
    assert 'item["hasOriginalFile"]' in src
    assert "fromStoredBlob" in src


def test_domain_exposes_blob_loader():
    src = (ROOT / "aleggo/study/research_domain.py").read_text(encoding="utf-8")
    assert "def get_document_blob" in src
    assert "def get_source" in src
    assert "document_blob" in src


def test_workbench_safe_json():
    ns = _extract_functions("aleggo/study/research_workbench.py", {"_safe_json", "_json", "_now", "_id"})
    assert ns["_safe_json"]("not-json", {"a": 1}) == {"a": 1}
    assert ns["_safe_json"]('{"ok": true}', {}) == {"ok": True}
    assert ns["_safe_json"]("", []) == []


def test_thread_includes_pdf_continue_events():
    src = (ROOT / "aleggo/study/research_thread.py").read_text(encoding="utf-8")
    assert '"pdf_vision_continue_pending"' in src
    assert '"pdf_vision_continue_cleared"' in src


def test_teacher_facing_import_errors_are_chinese():
    src = (ROOT / "aleggo/study/research_imports.py").read_text(encoding="utf-8")
    for needle in ("请选择非空且小于 25 MB 的 PDF", "无法读取该 PDF", "支持的图片格式", "压缩包中没有找到"):
        assert needle in src, needle
    mail = (ROOT / "aleggo/study/research_mail_import.py").read_text(encoding="utf-8")
    assert "本部署未配置 Gmail" in mail or "本 overlay 中 Gmail" in mail


def test_gmail_status_stub_chinese():
    for name in ("aleggo", "aleggo.study", "aleggo.study.research_domain", "aleggo.study.research_imports"):
        sys.modules.setdefault(name, types.ModuleType(name))
    imports = sys.modules["aleggo.study.research_imports"]
    imports.MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
    imports.import_kind = lambda name: "document"
    imports.import_email_attachment = lambda *a, **k: {}
    async def _pdf(*a, **k):
        return {}
    imports.import_email_pdf = _pdf
    sys.modules["aleggo.study.research_domain"].ResearchDomainStore = type("RD", (), {})
    if "httpx" not in sys.modules:
        httpx = types.ModuleType("httpx")
        httpx.HTTPError = type("HTTPError", (Exception,), {})
        httpx.get = lambda *a, **k: (_ for _ in ()).throw(httpx.HTTPError("x"))
        sys.modules["httpx"] = httpx
    ns: dict = {}
    code = (ROOT / "aleggo/study/research_mail_import.py").read_text(encoding="utf-8")
    exec(compile(code, "mail", "exec"), ns)
    status = ns["gmail_fetcher_status"]()
    assert "智能体" in status["stubMessage"] or "overlay" in status["stubMessage"].lower()


def test_math_tools_reparse_guard_present():
    src = (ROOT / "aleggo/study/math_tools.py").read_text(encoding="utf-8")
    assert 'getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)' in src
