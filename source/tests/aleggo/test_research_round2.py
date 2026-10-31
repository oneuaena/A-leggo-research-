"""Round 2 standalone tests (mail import glue, proof tree, sharing, memory, collaborator parse)."""

from __future__ import annotations

import ast
import base64
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _stub_pkg(*names: str) -> None:
    for name in names:
        sys.modules.setdefault(name, types.ModuleType(name))


def _exec_module(rel: str) -> dict:
    ns: dict = {"__name__": "standalone", "__file__": str(ROOT / rel)}
    code = (ROOT / rel).read_text(encoding="utf-8")
    exec(compile(code, rel, "exec"), ns)
    return ns


def _extract_functions(rel: str, names: set[str], *, drop_aleggo_imports: bool = True,
                       include_assigns: bool = True) -> dict:
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if drop_aleggo_imports and isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aleggo"):
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


@pytest.fixture(scope="module")
def mail():
    _stub_pkg("aleggo", "aleggo.study", "aleggo.study.research_domain", "aleggo.study.research_imports")
    imports = sys.modules["aleggo.study.research_imports"]
    imports.MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
    imports.import_kind = lambda name: (
        "pdf" if str(name).lower().endswith(".pdf") else
        "image" if str(name).lower().endswith((".png", ".jpg", ".jpeg")) else
        "latex" if str(name).lower().endswith((".tex", ".zip")) else "document"
    )
    imports.import_email_attachment = lambda *a, **k: {"source": {"id": "s1", "title": k.get("filename")}}

    async def _pdf(*a, **k):
        return {"source": {"id": "s1", "title": k.get("filename")}, "pages": 1}

    imports.import_email_pdf = _pdf
    sys.modules["aleggo.study.research_domain"].ResearchDomainStore = type("RD", (), {})
    if "httpx" not in sys.modules:
        httpx = types.ModuleType("httpx")
        httpx.HTTPError = type("HTTPError", (Exception,), {})
        httpx.get = lambda *a, **k: (_ for _ in ()).throw(httpx.HTTPError("no network"))
        sys.modules["httpx"] = httpx
    return _exec_module("aleggo/study/research_mail_import.py")


@pytest.fixture(scope="module")
def proof():
    return _extract_functions(
        "aleggo/study/proof_review.py",
        {"build_tree", "_weakest_ancestors", "overall_verdict", "segment_proof"},
    )


@pytest.fixture(scope="module")
def sharing_helpers():
    return _extract_functions("aleggo/study/research_sharing.py", {"_is_comment_write_path"})


@pytest.fixture(scope="module")
def memory_helpers():
    return _extract_functions("aleggo/study/research_memory.py", {"tokens", "relevance", "_now"})


@pytest.fixture(scope="module")
def collaborator_tool():
    return _extract_functions("aleggo/study/research_collaborator.py", {"_tool_call"}, drop_aleggo_imports=True, include_assigns=False)


def test_gmail_status_stub_when_unconfigured(mail, monkeypatch):
    monkeypatch.delenv("ALEGGO_GMAIL_ATTACHMENT_FETCH_URL", raising=False)
    mail["set_attachment_fetcher"](None)
    status = mail["gmail_fetcher_status"]()
    assert status["available"] is False
    assert status["stubMessage"]


def test_gmail_list_attachment_hints(mail):
    hints = mail["list_attachment_hints"]({
        "id": "msg1",
        "attachments": [
            {"id": "attA", "filename": "notes.pdf", "mimeType": "application/pdf"},
            {"attachmentId": "attB", "name": "paper.zip"},
        ],
    })
    assert len(hints) == 2
    assert hints[0]["kind"] == "pdf"
    assert hints[0]["downloadFileId"] == "msg1/attA"
    assert hints[1]["kind"] == "latex"


def test_gmail_decode_and_injected_fetcher(mail):
    payload = base64.b64encode(b"%PDF-1.4 fake").decode()
    data = mail["decode_base64_payload"](payload)
    assert data.startswith(b"%PDF")
    mail["set_attachment_fetcher"](lambda mid, aid: b"hello-" + mid.encode() + b"-" + aid.encode())
    assert mail["fetch_attachment_bytes"]("m1", "a1") == b"hello-m1-a1"
    mail["set_attachment_fetcher"](None)
    with pytest.raises(mail["GmailFetcherUnavailable"]):
        mail["fetch_attachment_bytes"]("m1", "a1")


def test_comment_write_path_rejects_lookalikes(sharing_helpers):
    fn = sharing_helpers["_is_comment_write_path"]
    pid = "proj_abc"
    assert fn(f"/projects/{pid}/comments", pid)
    assert fn(f"/projects/{pid}/comments/comment_1", pid)
    assert not fn(f"/projects/{pid}/comments-extra", pid)
    assert not fn(f"/projects/{pid}/comments/foo/bar", pid)
    assert not fn(f"/projects/{pid}/assignments", pid)
    assert not fn(f"/projects/other/comments", pid)
    assert not fn(f"/projects/{pid}/comments/../assignments", pid)


def test_memory_relevance_cjk_and_empty(memory_helpers):
    tokens = memory_helpers["tokens"]
    relevance = memory_helpers["relevance"]
    cjk = tokens("这是一个定理证明")
    assert any("定理" in t or t == "定理" for t in cjk)
    q = tokens("Cauchy Schwarz inequality")
    score = relevance(q, "The Cauchy–Schwarz inequality is classical.")
    assert score > 0
    assert relevance(set(), "anything") == 0.0
    assert relevance(q, "") == 0.0


def test_collaborator_tool_trailing_and_prefix(collaborator_tool):
    fn = collaborator_tool["_tool_call"]
    assert fn('{"tool":"sympy_eval","input":{"expr":"1+1"},"purpose":"check"}')["tool"] == "sympy_eval"
    trailing = fn('{"tool":"sympy_eval","input":{"expr":"2"},"purpose":"x"}\nSure, I will compute that.')
    assert trailing and trailing["tool"] == "sympy_eval"
    prefixed = fn('Calling tool:\n{"tool":"oeis_search","input":{"query":"1,1,2,3,5"},"purpose":"seq"}')
    assert prefixed and prefixed["tool"] == "oeis_search"
    fenced = fn('```json\n{"tool":"sympy_eval","input":{"expr":"3"},"purpose":"y"}\n```\nok')
    assert fenced and fenced["input"]["expr"] == "3"
    assert fn("no json here") is None


def test_proof_build_tree_drops_unknown_deps(proof):
    build_tree = proof["build_tree"]
    steps = [
        {"id": "S1", "text": "A", "verdict": "valid", "dependsOn": []},
        {"id": "S2", "text": "B", "verdict": "gap", "dependsOn": ["S1"]},
        {"id": "S3", "text": "C", "verdict": "valid", "dependsOn": ["S2", "missing"]},
    ]
    tree = build_tree(steps)
    assert tree["conclusion"] == "S3"
    assert "missing" not in steps[2]["dependsOn"]
    copied = [{**item, "dependsOn": list(item.get("dependsOn") or [])} for item in steps]
    build_tree(copied)
    assert steps[1]["dependsOn"] == ["S1"]
    assert proof["overall_verdict"](steps) == "gaps-found"
