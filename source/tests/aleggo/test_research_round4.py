"""Round 4 standalone tests: PDF byte-hash continue, i18n needles, safe JSON helpers."""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _extract(rel: str, names: set[str], *, include_assigns: bool = True) -> dict:
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep: list = []
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


def test_pdf_continue_stores_and_checks_original_bytes_hash():
    src = (ROOT / "aleggo/study/research_imports.py").read_text(encoding="utf-8")
    assert "originalBytesHash" in src
    assert "内容哈希不匹配" in src
    assert "loaded_from_blob" in src
    assert "bytes_hash = sha256(data).hexdigest()" in src


def test_hash_mismatch_message_is_chinese():
    src = (ROOT / "aleggo/study/research_imports.py").read_text(encoding="utf-8")
    assert "上传的 PDF 与原先导入的文件不一致" in src


def test_archive_errors_are_chinese():
    src = (ROOT / "aleggo/study/research_imports.py").read_text(encoding="utf-8")
    for needle in ("压缩炸弹", "ZIP 压缩包无效", "文件数量过多", "压缩包中某个文件超过"):
        assert needle in src


def test_domain_safe_json():
    ns = _extract("aleggo/study/research_domain.py", {"_safe_json"})
    assert ns["_safe_json"]("nope", {}) == {}
    assert ns["_safe_json"]('{"a":1}', {}) == {"a": 1}
    assert ns["_safe_json"]("[1]", []) == [1]
    assert ns["_safe_json"]("[1]", {}) == {}  # type mismatch → fallback


def test_literature_safe_json_helper():
    ns = _extract("aleggo/study/research_literature_intelligence.py", {"_safe_json"})
    assert ns["_safe_json"]("{", {"x": 1}) == {"x": 1}


def test_lean_autoprove_stale_running_and_coerce():
    src = (ROOT / "aleggo/study/lean_autoprove.py").read_text(encoding="utf-8")
    assert "job[\"id\"] not in _TASKS" in src
    assert "except (TypeError, ValueError)" in src
    assert "请先核对并确认命题映射" in src


def test_extensions_chinese_http_details():
    src = (ROOT / "aleggo/api/routers/research_extensions.py").read_text(encoding="utf-8")
    assert "未找到该研究项目" in src
    assert "仅管理员可修改此设置" in src


def test_frontend_imports_loading_markers():
    src = (ROOT / "web/features/research/ResearchLabPanel.tsx").read_text(encoding="utf-8")
    assert "continuesLoading" in src
    assert "正在加载待续传讲义状态" in src
    assert "当前没有待续传的长 PDF" in src
    assert "正在加载证明审查列表" in src
    assert "reviewLoading" in src


def test_bytes_hash_roundtrip_helper():
    data = b"%PDF-1.4 demo"
    digest = hashlib.sha256(data).hexdigest()
    assert len(digest) == 64
    assert digest == hashlib.sha256(data).hexdigest()
