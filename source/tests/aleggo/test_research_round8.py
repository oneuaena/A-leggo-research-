"""Round 8 standalone: LaTeX CJK/math, delivery warnings, usage coerce, eval UX Chinese, reports."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    """Load a module by file path, bypassing stubbed aleggo.study from other tests."""
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_latex_cjk_wrapped_in_text():
    latex = _load("r8_research_latex", "aleggo/study/research_latex.py")
    rendered = latex._Converter(True).convert(r"公式 $面积 = \pi r^2$")
    assert r"\text{面积}" in rendered
    assert r"\pi" in rendered


def test_latex_currency_still_escaped():
    latex = _load("r8_research_latex", "aleggo/study/research_latex.py")
    rendered = latex._Converter(False).convert("Costs $5 and $10. Value $x^2$ here.")
    assert r"\$5 and \$10" in rendered
    assert "$x^2$" in rendered


def test_delivery_bibtex_delegates_and_warns():
    src = (ROOT / "aleggo/study/research_delivery.py").read_text(encoding="utf-8")
    assert "build_latex_bibliography(sources" in src
    assert "final/WARNINGS.md" in src
    assert "跳过损坏的生成文件记录" in src
    assert "原文二进制缺失或损坏" in src


def test_research_reports_module():
    reports = _load("r8_research_reports", "aleggo/study/research_reports.py")
    pdf = reports.build_pdf_report("# Title\n\nBody 中文", "报告")
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 200


def test_usage_as_int_and_cost():
    src = (ROOT / "aleggo/api/routers/research_extensions.py").read_text(encoding="utf-8")
    assert "def _as_int" in src
    start = src.find("def _as_int")
    end = src.find("\ndef project_usage", start)
    from typing import Any
    ns: dict = {"Any": Any}
    exec(compile(src[start:end], "usage_helpers", "exec"), ns)
    assert ns["_as_int"]("3.7") == 3
    assert ns["_as_int"](None) == 0
    assert ns["_cost"]({"models": {"m": {"prompt": 1.0, "completion": 2.0}}}, "m", 2000, 1000) == 4.0
    assert ns["_cost"]({"models": {}}, "m", 1000, 1000) is None


def test_eval_and_lab_chinese_ux():
    eval_src = (ROOT / "aleggo/study/research_eval.py").read_text(encoding="utf-8")
    assert "未安装 Lean" in eval_src or "Lean 不可用" in eval_src
    assert "命中答案行" in eval_src
    lab = (ROOT / "web/features/research/ResearchLabPanel.tsx").read_text(encoding="utf-8")
    assert "EVAL_STATE_LABEL" in lab
    assert "进行中" in lab
    assert "部分未定价" in lab
    assert "已请求取消评测" in lab
    lean = (ROOT / "aleggo/study/lean_autoprove.py").read_text(encoding="utf-8")
    assert "证明任务已取消" in lean
