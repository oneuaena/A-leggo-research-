"""Round 5 standalone: proof affected, sharing comments, Chinese needles."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _extract(rel: str, names: set[str]) -> dict:
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aleggo"):
                continue
            keep.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            keep.append(node)
    mod = ast.Module(body=keep, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {"__name__": "ex"}
    exec(compile(mod, rel, "exec"), ns)
    return ns


def test_weakest_includes_self_and_researcher_verdict():
    ns = _extract("aleggo/study/proof_review.py", {"_weakest_ancestors", "build_tree", "overall_verdict"})
    steps = [
        {"id": "S1", "text": "A", "verdict": "valid", "dependsOn": []},
        {"id": "S2", "text": "B", "verdict": "gap", "dependsOn": ["S1"]},
        {"id": "S3", "text": "C", "verdict": "valid", "dependsOn": ["S2"]},
    ]
    weak = ns["_weakest_ancestors"](steps)
    assert "S2" in weak  # self gap included
    assert weak["S3"] == ["S2"]
    # researcher override clears gap
    steps[1]["researcherVerdict"] = "valid"
    weak2 = ns["_weakest_ancestors"](steps)
    assert "S3" not in weak2
    assert ns["overall_verdict"](steps) == "no-issues-found"


def test_sharing_parent_comment_validation_present():
    src = (ROOT / "aleggo/study/research_sharing.py").read_text(encoding="utf-8")
    assert "父评论不存在或不属于本项目" in src
    assert "回复必须针对与父评论相同的目标" in src
    assert "isinstance(plan, str)" in src


def test_cas_and_delivery_chinese():
    cas = (ROOT / "aleggo/study/external_cas.py").read_text(encoding="utf-8")
    assert "未安装" in cas
    assert "不允许写入项目工作区以外的位置" in cas
    delivery = (ROOT / "aleggo/study/research_delivery.py").read_text(encoding="utf-8")
    assert "研究包文件数量无效" in delivery or "研究包包含不安全路径" in delivery


def test_toolbench_and_workspace_chinese_markers():
    tb = (ROOT / "web/features/research/ResearchToolbench.tsx").read_text(encoding="utf-8")
    assert 'label: "证据图"' in tb
    assert "请为该证据图记录填写标题" in tb
    ws = (ROOT / "web/features/research/ResearchWorkspace.tsx").read_text(encoding="utf-8")
    assert "指派智能体" in ws
    assert 'PlanField label="研究目标"' in ws


def test_runtime_boundary_chinese():
    src = (ROOT / "aleggo/study/research_runtime.py").read_text(encoding="utf-8")
    assert "形式化验证需要带研究者确认" in src
    assert "仅已暂停的研究运行可以恢复" in src


def test_workbench_chinese_and_safe_decide():
    src = (ROOT / "aleggo/study/research_workbench.py").read_text(encoding="utf-8")
    assert "更改督导模式前请先暂停研究" in src
    assert '_safe_json(row["content_json"], {})' in src or "建议的下一步研究路径" in src
