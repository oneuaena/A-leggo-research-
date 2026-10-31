"""Standalone ingestion tests (no full Aleggo install required for the pure helpers)."""

from __future__ import annotations

import gzip
import io
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_imports():
    for name in (
        "aleggo",
        "aleggo.study",
        "aleggo.study.research_documents",
        "aleggo.study.research_domain",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))
    docs = sys.modules["aleggo.study.research_documents"]
    docs.MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
    docs._split_text = lambda text: [text[i : i + 4000] for i in range(0, max(len(text), 1), 4000)]
    sys.modules["aleggo.study.research_domain"].ResearchDomainStore = type("RD", (), {})
    ns: dict = {}
    code = (ROOT / "aleggo/study/research_imports.py").read_text(encoding="utf-8")
    exec(compile(code, "research_imports.py", "exec"), ns)
    return ns


def _load_cas():
    for name in ("aleggo", "aleggo.study", "aleggo.study.lean_formal"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["aleggo.study.lean_formal"].tools_home = lambda: Path("/tmp")
    ns: dict = {}
    code = (ROOT / "aleggo/study/external_cas.py").read_text(encoding="utf-8")
    exec(compile(code, "external_cas.py", "exec"), ns)
    return ns


@pytest.fixture(scope="module")
def imports():
    return _load_imports()


@pytest.fixture(scope="module")
def cas():
    return _load_cas()


def test_vision_page_limit_is_configurable_and_clamped(imports, monkeypatch):
    monkeypatch.setenv("ALEGGO_MAX_VISION_PAGES", "50")
    assert imports["vision_page_limit"](None) == 50
    assert imports["vision_page_limit"](999) == imports["HARD_MAX_VISION_PAGES"]
    assert imports["vision_page_limit"](3) == 3


def test_nested_relative_and_bare_input(imports):
    members = {
        "paper/main.tex": r"\documentclass{article}\begin{document}\input{sections/a}\end{document}",
        "paper/sections/a.tex": r"\input b",
        "paper/sections/b.tex": r"NESTED",
    }
    full, used = imports["inline_inputs"](members, "paper/main.tex")
    assert "NESTED" in full and "paper/sections/b.tex" in used


def test_chinese_theorem_and_pf_proof_extraction(imports):
    envs = imports["theorem_environments"](r"\newtheorem{thm}{定理}")
    body = r"\begin{thm}陈述\end{thm}\begin{证明}证明体\end{证明}"
    stmts = imports["extract_statements"](body, {**envs, "thm": "theorem"})
    assert stmts[0]["proof"] == "证明体"
    body2 = r"\begin{定理}中文命题\end{定理}\begin{pf}P\end{pf}"
    stmts2 = imports["extract_statements"](body2, imports["THEOREM_KINDS"])
    assert "中文命题" in stmts2[0]["statement"] and stmts2[0]["proof"] == "P"


def test_gzip_bomb_rejected(imports):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as handle:
        handle.write(b"x" * 200_000)
    with pytest.raises(ValueError):
        imports["_gzip_decompress_bounded"](buf.getvalue(), 1024)


def test_lone_bib_parse(imports):
    data = b"@article{a, title={Hello}, author={A}, year={2020}, doi={10.1/x}}"
    parsed = imports["parse_latex"]("refs.bib", data)
    assert parsed["statements"] == [] and parsed["references"][0]["title"] == "Hello"


def test_sage_policy_blocks_aliased_imports(cas):
    assert cas["policy_violation"]("sage", "print(1); import os as o\no.system('id')")
    assert cas["policy_violation"]("sage", "from os import system\nsystem('id')")
    assert cas["policy_violation"]("sage", 'print("os.path is just text")') == ""


def test_import_kind_recognises_archives_and_bib(imports):
    assert imports["import_kind"]("paper.tar.gz") == "latex"
    assert imports["import_kind"]("refs.bib") == "latex"
    assert imports["import_kind"]("scan.pdf") == "pdf"
    assert imports["import_kind"]("board.HEIC") == "image"
