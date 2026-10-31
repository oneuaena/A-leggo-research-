"""Round 6 standalone: Toolbench Chinese markers, promote rules, eval cancel, IDOR guard, vocab."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_toolbench_primary_actions_chinese():
    src = (ROOT / "web/features/research/ResearchToolbench.tsx").read_text(encoding="utf-8")
    for needle in ("添加类型化记录", "运行有限发现", "下载 Markdown 报告", "保存待确认", "创建分支"):
        assert needle in src, needle
    assert 'label: "证据图"' in src


def test_extensions_project_binding_guard():
    src = (ROOT / "aleggo/api/routers/research_extensions.py").read_text(encoding="utf-8")
    # Round 7 consolidated the inline check into shared helpers.
    assert "research_sharing.assert_bound_project(project_id)" in src
    assert "research_sharing.revalidate_share(project_id)" in src
    sharing = (ROOT / "aleggo/study/research_sharing.py").read_text(encoding="utf-8")
    assert "共享绑定仅覆盖已分享的项目" in sharing


def test_memory_promote_requires_confirmed():
    src = (ROOT / "aleggo/study/research_memory.py").read_text(encoding="utf-8")
    assert "请先确认该记忆，再提升为长期记忆" in src
    assert "该记忆已是长期记忆，无需再提升" in src
    assert "AND confirmed=1" in src


def test_eval_cancel_persists_and_locks():
    src = (ROOT / "aleggo/study/research_eval.py").read_text(encoding="utf-8")
    assert "_START_LOCK" in src
    assert "_CANCEL_REQUESTED" in src
    assert "评测已被取消" in src
    ext = (ROOT / "aleggo/api/routers/research_extensions.py").read_text(encoding="utf-8")
    assert "cancel_eval(report_id, _eval_root())" in ext


def test_plan_vocabulary_bilingual():
    src = (ROOT / "aleggo/study/research_tool_catalog.py").read_text(encoding="utf-8")
    start = src.find("def plan_tool_vocabulary")
    end = src.find("\n\n_WORKER_NAME_ALIASES", start)
    chunk = src[start:end]
    ns = {"installed_cas_capabilities": lambda: set()}
    exec(compile(chunk, "cat", "exec"), ns)
    text = ns["plan_tool_vocabulary"]()
    assert "【中文说明" in text
    assert "requiredTools" in text
    assert "python" in ns["teacher_tool_help_zh"]()


def test_workspace_tool_help_chinese():
    src = (ROOT / "web/features/research/ResearchWorkspace.tsx").read_text(encoding="utf-8")
    assert "所需工具请用英文标识" in src


def test_autoprove_artifact_failure_recorded():
    src = (ROOT / "aleggo/study/lean_autoprove.py").read_text(encoding="utf-8")
    assert "未能写入产物" in src
