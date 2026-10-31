"""Round 9 standalone: databases/literature ZH+dedupe, memory guards, LabPanel UX."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_math_databases_chinese_and_empty_hint():
    src = (ROOT / "aleggo/study/math_databases.py").read_text(encoding="utf-8")
    assert "数据库未在时限内响应" in src
    assert "Cloudflare 人机验证" in src
    assert "emptyHint" in src
    assert "Refusing to contact" not in src
    assert "The database did not answer" not in src


def test_research_literature_module_exists():
    lit = _load("r9_research_literature", "aleggo/study/research_literature.py")
    assert lit.normalize_doi("https://doi.org/10.1000/xyz") == "10.1000/xyz"
    assert lit.normalize_arxiv_id("https://arxiv.org/abs/1234.56789v2") == "1234.56789"
    assert "openalex" in lit.PROVIDERS
    assert len(lit._dedupe([
        {"title": "A", "doi": "10.1/x"},
        {"title": "A copy", "doi": "10.1/x"},
        {"title": "B", "doi": ""},
    ])) == 2


def test_literature_intelligence_empty_hint():
    src = (ROOT / "aleggo/study/research_literature_intelligence.py").read_text(encoding="utf-8")
    assert "emptyHintZh" in src
    assert "Cloudflare" in src
    assert "缺少标题" in src


def test_memory_guards_and_chinese():
    src = (ROOT / "aleggo/study/research_memory.py").read_text(encoding="utf-8")
    assert "include_long_term" in src
    assert "require_long_term" in src
    assert "研究记忆须为" in src
    assert "未知的记忆类型" in src
    assert "相关历史项目" in src
    domain = (ROOT / "aleggo/study/research_domain.py").read_text(encoding="utf-8")
    assert "include_long_term=include_lt" in domain
    ext = (ROOT / "aleggo/api/routers/research_extensions.py").read_text(encoding="utf-8")
    assert "require_long_term=True" in ext


def test_lab_panel_chinese_and_loading():
    lab = (ROOT / "web/features/research/ResearchLabPanel.tsx").read_text(encoding="utf-8")
    assert 'eyebrow="数学数据库"' in lab
    assert "正在查询数据库" in lab
    assert "memoryLoading" in lab
    assert "正在加载长期记忆" in lab
    assert "collabLoading" in lab
    assert 'eyebrow="MATHEMATICAL DATABASES"' not in lab
    assert 'eyebrow="LONG-TERM MEMORY"' not in lab
