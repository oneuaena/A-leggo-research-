"""Round 7 standalone: sibling-project guards, share revoke, lean lock, paths, Toolbench taxonomy."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_research_require_project_uses_shared_guards():
    src = (ROOT / "aleggo/api/routers/research.py").read_text(encoding="utf-8")
    assert "research_sharing.revalidate_share(project_id)" in src
    assert "research_sharing.assert_bound_project(project_id)" in src
    assert "未找到该研究项目。" in src


def test_extensions_project_uses_shared_helpers():
    src = (ROOT / "aleggo/api/routers/research_extensions.py").read_text(encoding="utf-8")
    assert "research_sharing.revalidate_share(project_id)" in src
    assert "research_sharing.assert_bound_project(project_id)" in src
    assert "async for chunk in request.stream()" in src


def test_sharing_assert_and_revalidate_helpers():
    src = (ROOT / "aleggo/study/research_sharing.py").read_text(encoding="utf-8")
    assert "def assert_bound_project" in src
    assert "def revalidate_share" in src
    assert "对该项目的共享访问已撤销。" in src
    assert "仅所有者可撤销其他成员的共享" in src
    # resolve snapshots under lock
    chunk = src[src.find("def resolve"): src.find("def bind")]
    assert "with _LOCK:" in chunk


def test_lean_start_lock():
    src = (ROOT / "aleggo/study/lean_autoprove.py").read_text(encoding="utf-8")
    assert "_START_LOCK" in src
    start = src[src.find("def start("): src.find("\ndef ", src.find("def start(") + 10)]
    assert "with _START_LOCK:" in start


def test_workspace_path_is_relative_to():
    research = (ROOT / "aleggo/api/routers/research.py").read_text(encoding="utf-8")
    assert "path.is_relative_to(root)" in research
    assert "工作区相对路径无效。" in research
    wb = (ROOT / "aleggo/study/research_workbench.py").read_text(encoding="utf-8")
    assert "def _safe_workspace_relative" in wb
    assert "target.is_relative_to(root)" in wb
    assert "root not in target.parents" not in wb


def test_toolbench_taxonomy_chinese():
    src = (ROOT / "web/features/research/ResearchToolbench.tsx").read_text(encoding="utf-8")
    assert "NODE_TYPE_LABELS" in src
    assert "EDGE_TYPE_LABELS" in src
    assert "CLASSIFICATION_LABELS" in src
    for needle in (
        'question: "问题"',
        'SUPPORTS: "支持"',
        'survey: "综述"',
        "类型",
        "变量",
        "我已审阅原始陈述、形式化 IR 与 Lean 命题",
        "检索覆盖 · 非新颖性主张",
        "本项目",
        "撤销确认",
    ):
        assert needle in src, needle
    # residual English field labels that Round 7 targeted
    assert 'field-label">Type<' not in src
    assert 'field-label">Variable<' not in src
    assert 'field-label">Query<' not in src
