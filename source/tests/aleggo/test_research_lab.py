"""Research-lab features for ordinary university mathematicians.

Covers the external computer-algebra bridges (PARI/GP, GAP, Sage, Macaulay2),
the mathematical databases (OEIS, zbMATH Open, arXiv, LMFDB), Lean onboarding
and the AI prover's kernel-feedback loop, LaTeX/PDF/handwriting imports,
step-by-step proof review, cross-project memory, collaboration, usage/cost
accounting, the Windows Job Object and path normalisation of the math worker,
and the HTTP endpoints that expose all of it.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import stat
from unittest.mock import patch
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from aleggo.study import (
    external_cas,
    lean_autoprove,
    lean_setup,
    math_databases,
    math_worker,
    research_sharing,
)
from aleggo.study.lean_formal import LeanConfigurationError, detect_lean_workspace
from aleggo.study.model_registry import capture_model_usage, normalise_usage, summarise_usage
from aleggo.study.proof_review import ProofReviewer, build_tree, overall_verdict, segment_proof
from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_imports import (
    import_latex,
    import_pdf,
    parse_bibtex,
    parse_latex,
    promote_import,
    read_archive,
    save_transcription,
    transcribe_images,
)
from aleggo.study.research_memory import ResearchMemory
from aleggo.study.research_store import ResearchStore

# The ``research`` fixture points ALEGGO_TOOLS_HOME at a temporary directory;
# the real-kernel test restores the developer's configured tools home.
_REAL_TOOLS_HOME = os.environ.get("ALEGGO_TOOLS_HOME", "")
from aleggo.study.store import AleggoStore


@pytest.fixture
def research(tmp_path, monkeypatch):
    monkeypatch.setenv("ALEGGO_TOOLS_HOME", str(tmp_path / "tools"))
    external_cas.find_executable.cache_clear()
    external_cas.engine_version.cache_clear()
    store = ResearchStore(AleggoStore(tmp_path / "data"))
    project = store.create_project("Study the Catalan numbers and prove a bound on primes", "research.meridian")
    yield store, ResearchDomainStore(store), project["id"]
    external_cas.find_executable.cache_clear()
    external_cas.engine_version.cache_clear()


def _shim(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _cas(store: ResearchStore, project_id: str, name: str, code: str, **extra) -> dict:
    return asyncio.run(external_cas.run_cas_tool(workspace_root=store.aleggo_store.root, project_id=project_id,
                                                 name=name, input_data={"code": code, **extra}))


def test_lean_project_settings_default_safely_and_persist(research):
    _store, domain, project_id = research
    assert domain.lean_settings(project_id) == {"confirmBeforeCheck": True, "autoCallEnabled": False}
    assert domain.save_lean_settings(project_id, confirm_before_check=False, auto_call_enabled=True) == {
        "confirmBeforeCheck": False, "autoCallEnabled": True,
    }
    assert domain.lean_settings(project_id) == {"confirmBeforeCheck": False, "autoCallEnabled": True}


# --------------------------------------------------------------------------- #
# Computer algebra systems
# --------------------------------------------------------------------------- #

def test_cas_catalogue_lists_four_engines_and_honours_configured_paths(research, tmp_path, monkeypatch):
    status = {item["key"]: item for item in external_cas.engine_status()}
    assert set(status) == {"pari", "gap", "sage", "macaulay2"}
    assert all(item["installHint"] and item["envVar"] for item in status.values())
    fake = _shim(tmp_path / "bin", "M2", 'echo "1.24.11"\n')
    monkeypatch.setenv("ALEGGO_M2_PATH", str(fake))
    external_cas.find_executable.cache_clear()
    assert external_cas.find_executable("macaulay2") == str(fake.resolve())
    monkeypatch.delenv("ALEGGO_M2_PATH")
    external_cas.save_config({"macaulay2": str(fake)})
    assert external_cas.load_config() == {"macaulay2": str(fake.resolve())}
    assert external_cas.find_executable("macaulay2") == str(fake.resolve())
    with pytest.raises(ValueError):
        external_cas.save_config({"macaulay2": str(tmp_path / "missing")})
    with pytest.raises(ValueError):
        external_cas.save_config({"maple": str(fake)})


@pytest.mark.parametrize(("engine", "code"), [
    ("pari", 'system("id")'), ("pari", 'extern("ls")'), ("pari", "\\l /tmp/log"), ("pari", 'default(secure, 0)'),
    ("gap", 'Exec("id");'), ("gap", 'PrintTo("/tmp/x", 1);'), ("gap", 'LoadPackage("io");'),
    ("sage", "import os\nos.system('id')"), ("sage", "__import__('os')"), ("sage", "open('/etc/passwd').read()"),
    ("macaulay2", 'run "id"'), ("macaulay2", 'get "/etc/passwd"'), ("macaulay2", '"/tmp/x" << "data"'),
])
def test_cas_policy_rejects_operating_system_access(engine, code):
    assert external_cas.policy_violation(engine, code)


@pytest.mark.parametrize(("engine", "code"), [
    ("pari", "factor(2^64+1)\nbnfinit(x^2+5).cyc"), ("gap", "Print(Size(SymmetricGroup(5)), \"\\n\");"),
    ("sage", 'print("os.path is just text")\nprint(factor(2**64+1))'), ("macaulay2", 'needsPackage "Elimination"\nR = QQ[x,y]\nprint ideal(x^2-y)'),
])
def test_cas_policy_allows_ordinary_mathematics(engine, code):
    assert external_cas.policy_violation(engine, code) == ""


def test_cas_bridge_runs_an_engine_with_limits_and_provenance(research, tmp_path, monkeypatch):
    store, _domain, pid = research
    fake = _shim(tmp_path / "bin", "M2", 'if [ "$1" = "--version" ]; then echo 1.24.11; exit 0; fi\necho "script:$1"\ncat "$2"\n')
    monkeypatch.setenv("ALEGGO_M2_PATH", str(fake))
    external_cas.find_executable.cache_clear()
    result = _cas(store, pid, "run_macaulay2", "R = QQ[x]\nprint(x^2)")
    assert result["ok"], result
    assert "script:--script" in result["output"] and "print(x^2)" in result["output"]
    assert result["tool_version"] == "Macaulay2 1.24.11"
    assert result["environment"]["engine"] == "Macaulay2" and result["input_sha256"]
    assert _cas(store, pid, "run_macaulay2", 'run "id"')["verification_status"] == "REJECTED"
    assert _cas(store, pid, "run_macaulay2", "")["error_type"] == "VALIDATION_ERROR"


def test_cas_bridge_enforces_the_time_limit_and_reports_missing_engines(research, tmp_path, monkeypatch):
    store, _domain, pid = research
    slow = _shim(tmp_path / "bin", "sage", 'if [ "$1" = "--version" ]; then echo "SageMath version 10.4"; exit 0; fi\nsleep 20\n')
    monkeypatch.setenv("ALEGGO_SAGE_PATH", str(slow))
    external_cas.find_executable.cache_clear()
    result = _cas(store, pid, "run_sage", "print(1)", timeoutSeconds=1)
    assert result["error_type"] == "TIMEOUT"
    monkeypatch.delenv("ALEGGO_SAGE_PATH")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    external_cas.find_executable.cache_clear()
    missing = _cas(store, pid, "run_sage", "print(1)")
    assert missing["error_type"] == "UNAVAILABLE" and "ALEGGO_SAGE_PATH" in missing["error"]


def _real(engine: str, candidates: list[str]) -> str:
    found = external_cas.find_executable(engine)
    return found or next((item for item in candidates if os.path.isfile(item) and os.access(item, os.X_OK)), "")


def test_real_pari_gp_computes_and_cannot_escape(research, monkeypatch):
    store, _domain, pid = research
    gp = _real("pari", ["/data/tools/pari/bin/gp", "/usr/bin/gp", "/usr/local/bin/gp"])
    if not gp:
        pytest.skip("PARI/GP is not installed")
    monkeypatch.setenv("ALEGGO_GP_PATH", gp)
    external_cas.find_executable.cache_clear()
    result = _cas(store, pid, "run_pari", "factor(2^64+1)\nbnfinit(x^2+5).cyc")
    assert result["ok"], result
    assert "274177" in result["output"] and "67280421310721" in result["output"] and "[2]" in result["output"]
    assert result["tool_version"].startswith("PARI/GP 2.")
    failed = _cas(store, pid, "run_pari", "1/0")
    assert not failed["ok"] and failed["error_type"] == "PROGRAM_ERROR" and "***" in failed["error"]
    assert _cas(store, pid, "run_pari", 'system("id")')["verification_status"] == "REJECTED"


def test_real_gap_computes_and_process_functions_are_disabled(research, monkeypatch):
    store, _domain, pid = research
    gap = _real("gap", ["/data/tools/src/gap-4.14.0/gap", "/usr/bin/gap", "/usr/local/bin/gap"])
    if not gap:
        pytest.skip("GAP is not installed")
    monkeypatch.setenv("ALEGGO_GAP_PATH", gap)
    external_cas.find_executable.cache_clear()
    result = _cas(store, pid, "run_gap", 'Print(Size(SymmetricGroup(5)), "\\n");\nPrint(IsSolvable(SymmetricGroup(4)), "\\n");')
    assert result["ok"], result
    assert "120" in result["output"] and "true" in result["output"]
    failed = _cas(store, pid, "run_gap", "Print(1/0);")
    assert not failed["ok"] and "Error" in failed["error"]
    # The static policy rejects Exec; the runtime prelude also rebinds it.
    assert _cas(store, pid, "run_gap", 'Exec("id");')["verification_status"] == "REJECTED"
    sneaky = _cas(store, pid, "run_gap", 'f := ValueGlobal;;')
    assert sneaky["verification_status"] == "REJECTED"


# --------------------------------------------------------------------------- #
# Mathematical databases
# --------------------------------------------------------------------------- #

_OEIS = json.dumps([{"number": 108, "name": "Catalan numbers: C(n) = binomial(2n,n)/(n+1).", "data": "1,1,2,5,14,42,132",
                     "formula": ["a(n) = binomial(2*n,n)/(n+1)"], "comment": ["Counts Dyck paths."], "keyword": "core,nonn",
                     "author": "N. J. A. Sloane"}])
_ARXIV = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
<opensearch:totalResults>1</opensearch:totalResults>
<entry><id>http://arxiv.org/abs/1909.03562v3</id><title>Almost all orbits of the Collatz map attain almost bounded values</title>
<published>2019-09-08T00:00:00Z</published><summary>We show that almost all Collatz orbits attain almost bounded values.</summary>
<author><name>Terence Tao</name></author><category term="math.PR"/><category term="math.DS"/></entry></feed>"""
_ZBMATH = json.dumps({"status": {"nr_total_results": 1}, "result": [{
    "id": 7_000_001, "identifier": "1234.11001", "title": {"title": "On the Erdős–Straus conjecture"}, "year": 2020,
    "contributors": {"authors": [{"name": "Elsholtz, Christian"}]}, "source": {"source": "J. Number Theory 1 (2020)"},
    "msc": [{"code": "11D68"}], "links": [{"type": "doi", "identifier": "10.1000/xyz"}],
    "editorial_contributions": [{"text": "The author studies 4/n = 1/x + 1/y + 1/z."}]}]})
_LMFDB = json.dumps({"data": [{"id": 1, "lmfdb_label": "11.a2", "conductor": 11, "rank": 0, "torsion": 5, "ainvs": [0, -1, 1, -10, -20]}]})


def _fake_get(calls: list[str]):
    async def fake(url: str, *, accept: str = "application/json", timeout: float = 25.0):
        calls.append(url)
        if "oeis.org" in url:
            return 200, _OEIS
        if "arxiv.org" in url:
            return 200, _ARXIV
        if "zbmath.org" in url:
            return 200, _ZBMATH
        if "lmfdb.org" in url:
            return 200, _LMFDB
        return 404, ""
    return fake


def test_math_databases_parse_each_provider_and_produce_citable_sources():
    math_databases._CACHE.clear()
    calls: list[str] = []
    with patch.object(math_databases, "_get", _fake_get(calls)):
        oeis = asyncio.run(math_databases.run_database_tool("oeis_search", {"query": "1 1 2 5 14 42"}))
        arxiv = asyncio.run(math_databases.run_database_tool("arxiv_search", {"query": "Collatz orbits", "category": "math.PR"}))
        zbmath = asyncio.run(math_databases.run_database_tool("zbmath_search", {"query": "Erdős–Straus"}))
        lmfdb = asyncio.run(math_databases.run_database_tool("lmfdb_search", {"label": "11.a2"}))
        again = asyncio.run(math_databases.run_database_tool("oeis_search", {"query": "1 1 2 5 14 42"}))
    assert "q=1%2C1%2C2%2C5%2C14%2C42" in calls[0]
    assert "cat:math.PR" in calls[1]
    assert "/api/ec_curvedata/" in calls[3] and "lmfdb_label=11.a2" in calls[3]
    assert len(calls) == 4 and again["environment"]["cached"] is True
    items = {name: json.loads(result["output"])["items"] for name, result in
             {"oeis": oeis, "arxiv": arxiv, "zbmath": zbmath, "lmfdb": lmfdb}.items()}
    assert items["oeis"][0]["id"] == "A000108" and items["oeis"][0]["url"] == "https://oeis.org/A000108"
    assert items["arxiv"][0]["id"] == "1909.03562v3" and items["arxiv"][0]["authors"] == "Terence Tao"
    assert items["zbmath"][0]["doi"] == "10.1000/xyz" and items["zbmath"][0]["msc"] == ["11D68"]
    assert items["lmfdb"][0]["lmfdb_label"] == "11.a2" and "id" not in items["lmfdb"][0]
    assert all(result["verification_level"] == "DATABASE_LOOKUP" for result in (oeis, arxiv, zbmath, lmfdb))
    sources = math_databases.database_sources("oeis_search", oeis)
    assert sources[0]["title"].startswith("A000108: Catalan") and sources[0]["provider"] == "OEIS"
    assert math_databases.database_sources("lmfdb_search", lmfdb)[0]["url"].endswith("label=11.a2")
    assert math_databases.database_sources("arxiv_search", arxiv)[0]["year"] == "2019"


def test_math_databases_report_validation_challenges_and_network_errors():
    math_databases._CACHE.clear()

    async def challenge(url, **_kwargs):
        return 403, "<html><title>Just a moment...</title></html>"

    async def offline(url, **_kwargs):
        import httpx

        raise httpx.ConnectError("offline")

    assert asyncio.run(math_databases.run_database_tool("oeis_search", {"query": ""}))["verification_status"] == "REJECTED"
    assert asyncio.run(math_databases.run_database_tool("arxiv_search", {"query": "x", "category": "cs.AI"}))["error_type"] == "VALIDATION_ERROR"
    assert asyncio.run(math_databases.run_database_tool("lmfdb_search", {"label": "???"}))["error_type"] == "VALIDATION_ERROR"
    with patch.object(math_databases, "_get", challenge):
        blocked = asyncio.run(math_databases.run_database_tool("oeis_search", {"query": "1,2,3,5,8"}))
    assert not blocked["ok"] and "browser" in blocked["error"]
    with patch.object(math_databases, "_get", offline):
        down = asyncio.run(math_databases.run_database_tool("zbmath_search", {"query": "Riemann"}))
    assert not down["ok"] and down["error_type"] == "UNAVAILABLE"
    with pytest.raises(ValueError):
        asyncio.run(math_databases.run_database_tool("mathscinet_search", {"query": "x"}))


# --------------------------------------------------------------------------- #
# Lean onboarding and the AI prover
# --------------------------------------------------------------------------- #

def test_lean_status_explains_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ALEGGO_TOOLS_HOME", str(tmp_path / "tools"))
    monkeypatch.setenv("ALEGGO_LEAN_WORKSPACE", str(tmp_path / "nowhere"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    status = lean_setup.lean_status()
    assert status["ready"] is False and status["reason"]
    assert {check["id"] for check in status["checks"]} == {"elan", "lake", "git", "workspace", "mathlib", "disk"}
    assert status["install"]["status"] in {"idle", "succeeded", "failed", "cancelled"}
    assert status["pinnedLean"] and status["pinnedMathlib"] and status["requiredDiskBytes"] > 0


def test_lean_workspace_files_pin_lean_and_mathlib(tmp_path):
    lean_setup.write_workspace_files(tmp_path)
    toolchain = (tmp_path / "lean-toolchain").read_text(encoding="utf-8").strip()
    lakefile = (tmp_path / "lakefile.toml").read_text(encoding="utf-8")
    assert toolchain == lean_setup.PINNED_LEAN
    assert f'rev = "{lean_setup.PINNED_MATHLIB}"' in lakefile and "mathlib4.git" in lakefile


def _confirmed_binding(store: ResearchStore, domain: ResearchDomainStore, pid: str, statement: str, lean: str) -> dict:
    claim = domain.create_graph_node(pid, "theorem", statement, statement, branch_name="main")
    return domain.create_formal_binding(pid, claim["id"], statement, statement, lean, confirm_mapping=True)


def test_ai_prover_feeds_kernel_errors_back_until_the_kernel_accepts(research):
    store, domain, pid = research
    binding = _confirmed_binding(store, domain, pid, "n + 0 = n", "∀ n : Nat, n + 0 = n")
    replies = iter([
        "not json at all",
        json.dumps({"proof": "intro n\nsorry", "explanation": "placeholder"}),
        json.dumps({"proof": "intro n\nexact rfl_wrong", "explanation": "guess"}),
        "```json\n" + json.dumps({"proof": "intro n\nsimp", "explanation": "simp closes it"}) + "\n```",
    ])
    seen: list[list[dict]] = []

    async def completion(messages):
        seen.append(list(messages))
        return {"text": next(replies), "usage": {"promptTokens": 100, "completionTokens": 20, "totalTokens": 120}}

    async def checker(project_id, root, statement, proof, timeout_seconds=180.0):
        if "rfl_wrong" in proof:
            return {"status": "CHECK_FAILED", "passed": False, "elapsedMs": 30,
                    "output": "/srv/x/check-abc123.lean:3:8: error: unknown identifier 'rfl_wrong'"}
        return {"status": "KERNEL_CERTIFIED", "passed": True, "elapsedMs": 40, "output": "",
                "leanVersion": "leanprover/lean4:v4.32.0", "mathlibRevision": "v4.32.0", "sourceHash": "h", "kernelCertified": True}

    with patch.object(lean_autoprove, "detect_lean_workspace", lambda: None):
        job = asyncio.run(lean_autoprove.run(pid, binding["id"], store, max_attempts=4, completion=completion, checker=checker))
    statuses = [item["status"] for item in job["attempts"]]
    assert statuses[:2] == ["REJECTED_BY_FILTER", "REJECTED_BY_FILTER"]
    assert statuses[2:] == ["CHECK_FAILED", "KERNEL_CERTIFIED"]
    assert job["passed"] and job["proof"] == "intro n\nsimp" and job["state"] == "COMPLETED"
    # The kernel's error reached the model with the server path hidden.
    fed_back = seen[3][-1]["content"]
    assert "unknown identifier 'rfl_wrong'" in fed_back and "/srv/x" not in fed_back and "Proof.lean:3:8" in fed_back
    assert job["usage"]["modelCalls"] == 4 and job["usage"]["totalTokens"] == 480
    assert domain.get_formal_binding(pid, binding["id"])["status"] == "KERNEL_CERTIFIED"
    later = lean_autoprove.status(pid, binding["id"], store)
    assert later["passed"] and later["artifactId"]


def test_ai_prover_requires_a_confirmed_mapping(research):
    store, domain, pid = research
    claim = domain.create_graph_node(pid, "theorem", "1 + 1 = 2", "1 + 1 = 2", branch_name="main")
    binding = domain.create_formal_binding(pid, claim["id"], "1 + 1 = 2", "1 + 1 = 2", "1 + 1 = 2", confirm_mapping=False)
    with patch.object(lean_autoprove, "detect_lean_workspace", lambda: None), pytest.raises(ValueError):
        lean_autoprove.prepare(pid, binding["id"], store)
    with pytest.raises(KeyError):
        lean_autoprove.prepare(pid, "formal_missing", store)


def test_ai_prover_with_the_real_lean_kernel(research, monkeypatch):
    if _REAL_TOOLS_HOME:
        monkeypatch.setenv("ALEGGO_TOOLS_HOME", _REAL_TOOLS_HOME)
    try:
        detect_lean_workspace()
    except LeanConfigurationError as exc:
        pytest.skip(str(exc))
    store, domain, pid = research
    binding = _confirmed_binding(store, domain, pid, "addition on Nat commutes", "∀ n m : Nat, n + m = m + n")
    replies = iter([json.dumps({"proof": "intro n m\nexact Nat.add_comm m n", "explanation": "wrong order"}),
                    json.dumps({"proof": "intro n m\nexact Nat.add_comm n m", "explanation": "fixed"})])

    async def completion(messages):
        return {"text": next(replies), "usage": {}}

    job = asyncio.run(lean_autoprove.run(pid, binding["id"], store, max_attempts=2, completion=completion))
    assert [item["status"] for item in job["attempts"]][-1] == "KERNEL_CERTIFIED", job
    assert job["attempts"][0]["status"] != "KERNEL_CERTIFIED" and "error" in job["attempts"][0]["feedback"]
    assert job["passed"]


# --------------------------------------------------------------------------- #
# Imports
# --------------------------------------------------------------------------- #

_MAIN = r"""\documentclass{amsart}
\newtheorem{theorem}{Theorem}
\newtheorem{lemma}[theorem]{Lemma}
\theoremstyle{definition}\newtheorem{defn}{Definition}
\title{Bounds for Catalan numbers}\author{A. Researcher}
\begin{document}
\begin{abstract}We bound $C_n$.\end{abstract}
\section{Main}
\input{sections/lemmas}
\begin{theorem}[Upper bound]\label{thm:main} For all $n\ge 1$, $C_n \le 4^n$. \end{theorem}
\begin{proof} By Lemma~\ref{lem:binom}, $C_n \le \binom{2n}{n} \le 4^n$. \end{proof}
\bibliography{refs}
\end{document}
"""
_LEMMAS = r"""\begin{defn}\label{def:cat} $C_n = \frac{1}{n+1}\binom{2n}{n}$. \end{defn}
\begin{lemma}\label{lem:binom} $\binom{2n}{n} \le 4^n$. \end{lemma}
% \begin{lemma} commented out \end{lemma}
"""
_BIB = """@article{stanley, author = {Stanley, Richard P.}, title = {Catalan Numbers}, journal = {Cambridge}, year = {2015}, doi = {10.1017/CBO9781139871495}}
@misc{tao, author={Tao, Terence}, title={{Almost all orbits}}, eprint={1909.03562}, archivePrefix={arXiv}, year={2019}}
"""


def _zip(members: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_latex_project_import_finds_main_file_inputs_statements_and_bibliography(research):
    store, domain, pid = research
    data = _zip({"paper/main.tex": _MAIN, "paper/sections/lemmas.tex": _LEMMAS, "paper/refs.bib": _BIB,
                 "paper/notes.tex": "just notes", "paper/fig.png": b"\x89PNG"})
    parsed = parse_latex("paper.zip", data)
    assert parsed["main"] == "paper/main.tex" and "paper/sections/lemmas.tex" in parsed["files"]
    kinds = [(item["environment"], item["label"]) for item in parsed["statements"]]
    assert kinds == [("defn", "def:cat"), ("lemma", "lem:binom"), ("theorem", "thm:main")]
    theorem = parsed["statements"][-1]
    assert theorem["note"] == "Upper bound" and "4^n" in theorem["proof"]
    assert {item["kind"] for item in parsed["statements"]} >= {"definition", "lemma", "theorem"}
    assert parsed["title"] == "Bounds for Catalan numbers" and parsed["abstract"] == "We bound $C_n$."
    references = parsed["references"]
    assert references[0]["doi"] == "10.1017/CBO9781139871495" and references[1]["arxivId"] == "1909.03562"
    imported = import_latex(domain, pid, "paper.zip", data)
    assert imported["statements"][-1]["hasProof"] and imported["source"]["type"] == "tex"
    promoted = promote_import(domain, pid, imported["source"]["id"], statements=[item["index"] for item in imported["statements"]],
                              references=[0, 1])
    assert len(promoted["nodes"]) == 3 and len(promoted["sources"]) == 2
    types = sorted(node["type"] for node in domain.list_graph(pid)["nodes"])
    assert types == ["definition", "lemma", "theorem"]
    assert all(node["status"] != "verified" for node in domain.list_graph(pid)["nodes"])


def test_archive_reader_rejects_traversal_and_bombs():
    with pytest.raises(ValueError):
        read_archive("evil.zip", _zip({"../../etc/passwd.tex": "x"}))
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.tex", "a" * (60 * 1024 * 1024))
    with pytest.raises(ValueError):
        read_archive("bomb.zip", bomb.getvalue())
    with pytest.raises(ValueError):
        read_archive("nothing.zip", _zip({"image.png": b"\x89PNG"}))
    assert parse_bibtex("@book{k, title={A {B} C}, author={X and Y}, year=1999}")[0]["title"] == "A B C"


def _pdf(pages: list[str]) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def test_formula_pdf_import_uses_the_vision_model_only_where_text_fails(research):
    store, domain, pid = research
    data = _pdf(["The Catalan numbers satisfy a simple recurrence relation that we use throughout this paper. " * 3, ""])
    calls: list[int] = []

    async def transcribe(images, prompt=""):
        calls.append(len(images))
        return {"text": r"$$C_{n+1} = \sum_{i=0}^{n} C_i C_{n-i}$$", "model": "qwen-vl-max",
                "usage": {"promptTokens": 900, "completionTokens": 40, "totalTokens": 940}}

    result = asyncio.run(import_pdf(domain, pid, "paper.pdf", data, mode="auto", transcribe=transcribe))
    assert result["pages"] == 2 and result["pagesNeedingVision"] == [2] and result["transcribedPages"] == [2]
    assert calls == [1] and result["visionModel"] == "qwen-vl-max" and result["usage"]["totalTokens"] == 940
    text_only = asyncio.run(import_pdf(domain, pid, "paper2.pdf", _pdf(["Plain text page about primes. " * 5]), mode="text",
                                       transcribe=transcribe))
    assert text_only["transcribedPages"] == [] and calls == [1]

    async def broken(images, prompt=""):
        raise RuntimeError("no vision model configured")

    degraded = asyncio.run(import_pdf(domain, pid, "paper3.pdf", data, mode="vision", transcribe=broken))
    assert degraded["transcribedPages"] == [] and any("no vision model" in item for item in degraded["warnings"])


def test_handwriting_photos_are_transcribed_then_saved_after_review(research):
    store, domain, pid = research
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buffer, format="PNG")

    async def transcribe(images, prompt):
        assert images[0][0] == "image/jpeg" and "ζ" in prompt
        return {"text": r"Let $\zeta(s) = \sum n^{-s}$.", "model": "qwen-vl-max", "usage": {"totalTokens": 10}}

    draft = asyncio.run(transcribe_images([("page1.png", buffer.getvalue())], transcribe=transcribe, hint="ζ is the zeta function"))
    assert draft["transcription"].startswith("Let $\\zeta") and draft["model"] == "qwen-vl-max"
    with pytest.raises(ValueError):
        asyncio.run(transcribe_images([("notes.txt", b"x")], transcribe=transcribe))
    saved = save_transcription(domain, pid, "Zeta notes", draft["transcription"] + " (checked)", model=draft["model"], originals=["page1.png"])
    assert saved["type"] == "handwritten" and saved["metadata"]["reviewedByResearcher"]


# --------------------------------------------------------------------------- #
# Proof review
# --------------------------------------------------------------------------- #

_PROOF = """Suppose there are only finitely many primes p_1, ..., p_n.
Let N = p_1 p_2 ... p_n + 1. Then N is prime.
Since N is larger than every p_i, this contradicts the assumption.
Therefore there are infinitely many primes."""


def test_proof_segmentation_tree_and_verdicts():
    numbered = segment_proof("Proof.\n1. Assume x > 0.\n2. Then x^2 > 0.\n3. Hence the claim.")
    assert [step["id"] for step in numbered] == ["S1", "S2", "S3", "S4"]
    assert len(segment_proof(_PROOF.replace("\n", "\n\n"))) == 4
    steps = [{"id": "S1", "verdict": "valid", "dependsOn": []}, {"id": "S2", "verdict": "gap", "dependsOn": ["S1"]},
             {"id": "S3", "verdict": "valid", "dependsOn": ["S2", "S3", "S9"]}, {"id": "S4", "verdict": "valid", "dependsOn": ["S3", "S1"]}]
    tree = build_tree(steps)
    assert tree["conclusion"] == "S4" and tree["tree"]["id"] == "S4"
    assert {"source": "S1", "target": "S2"} in tree["edges"]
    assert {"source": "S9", "target": "S3"} not in tree["edges"]  # unknown and self references are dropped
    assert tree["affectedByIssues"] == {"S3": ["S2"], "S4": ["S2"]}
    assert overall_verdict(steps) == "gaps-found"
    assert overall_verdict([{"verdict": "valid"}]) == "no-issues-found"
    assert overall_verdict([{"verdict": "gap"}, {"verdict": "error"}]) == "errors-found"


def test_proof_review_flags_the_gap_runs_a_check_and_accepts_researcher_overrides(research):
    store, domain, pid = research
    reviewer = ProofReviewer(store)
    created = reviewer.create(pid, _PROOF.replace("\n", "\n\n"), statement="There are infinitely many primes.", title="Euclid")
    referee = {"steps": [
        {"id": "S1", "verdict": "valid", "dependsOn": []},
        {"id": "S2", "verdict": "gap", "dependsOn": ["S1"], "issue": "N need not be prime.",
         "suggestion": "Take a prime factor of N.", "check": {"tool": "sympy", "input": {"operation": "factor", "expression": "2*3*5*7*11*13+1"}, "purpose": "counterexample"}},
        {"id": "S3", "verdict": "valid", "dependsOn": ["S2"]}, {"id": "S4", "verdict": "valid", "dependsOn": ["S3"]}],
        "summary": "Correct idea; step 2 overstates.", "missingHypotheses": []}
    calls: list[str] = []

    async def completion(messages):
        calls.append(messages[-1]["content"])
        return {"text": json.dumps(referee), "usage": {"promptTokens": 50, "completionTokens": 50, "totalTokens": 100}}

    async def tool_runner(**kwargs):
        assert kwargs["subdirectory"].startswith("proof-reviews/")
        return {"ok": True, "output": "59*509", "verification_status": "SUCCESS"}

    view = asyncio.run(reviewer.run(pid, created["id"], completion=completion, tool_runner=tool_runner))
    assert view["state"] == "COMPLETED" and view["overall"] == "gaps-found"
    step2 = next(step for step in view["steps"] if step["id"] == "S2")
    assert step2["issue"] == "N need not be prime." and step2["check"]["ok"] and "59*509" in step2["check"]["output"]
    assert len(calls) == 2 and "59*509" in calls[1]
    assert view["tree"]["conclusion"] == "S4" and "S4" in view["tree"]["affectedByIssues"]
    assert view["usage"]["modelCalls"] == 2
    accepted = reviewer.annotate(pid, created["id"], "S2", verdict="valid", note="Fixed in the revised draft.")
    assert accepted["overall"] == "no-issues-found" and accepted["tree"]["affectedByIssues"] == {}
    assert next(step for step in accepted["steps"] if step["id"] == "S2")["researcherNote"] == "Fixed in the revised draft."
    with pytest.raises(ValueError):
        reviewer.annotate(pid, created["id"], "S2", verdict="maybe")
    assert reviewer.list(pid)[0]["id"] == created["id"]


def test_proof_review_failure_is_recorded_not_raised(research):
    store, _domain, pid = research
    reviewer = ProofReviewer(store)
    created = reviewer.create(pid, "Step one.\n\nStep two.\n\nStep three.")

    async def completion(messages):
        raise RuntimeError("model unavailable")

    view = asyncio.run(reviewer.run(pid, created["id"], completion=completion))
    assert view["state"] == "FAILED" and "model unavailable" in view["error"]


# --------------------------------------------------------------------------- #
# Long-term memory
# --------------------------------------------------------------------------- #

def test_long_term_memory_is_retrieved_across_projects_only_when_confirmed(research):
    store, domain, pid = research
    memory = ResearchMemory(store)
    technique = memory.add("For Catalan number bounds use the central binomial estimate binom(2n,n) <= 4^n.",
                           kind="technique", tags=["catalan", "binomial"])
    memory.add("Unconfirmed guess about Catalan numbers and primes.", kind="note", confirmed=False)
    other = store.create_project("Asymptotics of Catalan numbers and central binomial coefficients", "research.meridian")
    found = memory.relevant(other["id"], "Catalan numbers binomial bound")
    assert [item["id"] for item in found] == [technique["id"]]
    assert memory.get(technique["id"])["useCount"] == 1
    assert memory.list_long_term("binomial", kind="technique")[0]["id"] == technique["id"]
    updated = memory.update(technique["id"], tags=["catalan"], confirmed=False)
    assert updated["tags"] == ["catalan"] and not memory.relevant(other["id"], "Catalan binomial")
    related = memory.related_projects(other["id"])
    assert related and related[0]["projectId"] == pid
    assert "Catalan" in memory.context_section(other["id"], "Catalan numbers") or memory.context_section(other["id"], "Catalan numbers") == ""
    assert memory.delete(technique["id"]) and memory.get(technique["id"]) is None


def test_project_lessons_are_distilled_as_unconfirmed_memories(research):
    store, domain, pid = research
    memory = ResearchMemory(store)
    domain.create_graph_node(pid, "lemma", "binom(2n,n) <= 4^n", "Sum of the binomial row is 4^n.", status="unverified")
    store.create_artifact(pid, 0, "", "researcher", "Researcher note", "Prefer elementary arguments.", status="human-note")
    suggestions = memory.suggestions(pid)
    assert any(item["kind"] == "preference" and "elementary" in item["content"] for item in suggestions)
    created = memory.distill(pid)
    assert created and all(not item["confirmed"] and item["originProject"] == pid for item in created)
    assert memory.suggestions(pid) == []  # the same lessons are not suggested twice
    project_note = memory.add("Project-only observation about C_5 = 42.", project_id=pid)
    promoted = memory.promote(pid, project_note["id"])
    assert promoted["scope"] == "long-term" and promoted["originProject"] == pid


# --------------------------------------------------------------------------- #
# Collaboration
# --------------------------------------------------------------------------- #

def test_sharing_roles_comments_and_assignments(research, tmp_path, monkeypatch):
    store, _domain, pid = research
    monkeypatch.setenv("ALEGGO_RESEARCH_SHARES_FILE", str(tmp_path / "shares.json"))
    users = {"owner": {"id": "u-owner", "username": "wang", "isAdmin": False},
             "li": {"id": "u-li", "username": "li", "isAdmin": False},
             "zhao": {"id": "u-zhao", "username": "zhao", "isAdmin": False}}
    store.save_plan(pid, {"objective": "Bound Catalan numbers", "steps": [{"id": "s1", "title": "Check the citation", "description": ""}]},
                    changed_by="researcher", change_reason="initial", change_summary="test")
    me = {"user": users["owner"]}
    monkeypatch.setattr(research_sharing, "current_identity", lambda: me["user"])
    monkeypatch.setattr(research_sharing, "list_candidates",
                        lambda: [{"id": item["id"], "username": item["username"], "role": "user"} for item in users.values() if item["id"] != me["user"]["id"]])
    monkeypatch.setattr(research_sharing, "owner_store", lambda owner_id, is_admin: store)
    monkeypatch.setattr(research_sharing, "_project_in_own_store", lambda project_id: me["user"]["id"] == "u-owner")

    with pytest.raises(ValueError):
        research_sharing.add_member(pid, "u-owner", "editor")
    with pytest.raises(KeyError):
        research_sharing.add_member(pid, "u-ghost", "editor")
    research_sharing.add_member(pid, "u-li", "editor")
    research_sharing.add_member(pid, "u-zhao", "viewer")
    assert research_sharing.binding_for_request(pid, "POST", f"/research/projects/{pid}/steps") is None  # the owner's own store

    me["user"] = users["zhao"]
    viewer = research_sharing.binding_for_request(pid, "GET", f"/research/projects/{pid}")
    assert viewer is not None and viewer.role == "viewer" and viewer.store is store
    with pytest.raises(PermissionError):
        research_sharing.binding_for_request(pid, "POST", f"/research/projects/{pid}/steps")
    assert research_sharing.binding_for_request(pid, "POST", f"/research/projects/{pid}/comments") is not None
    research_sharing.bind(viewer)
    try:
        comment = research_sharing.add_comment(store, pid, "Step 2 needs a citation: $\\binom{2n}{n}$.", target_type="step", target_id="s1")
        assert comment["author"] == "zhao" and comment["targetType"] == "step"
        members = research_sharing.members(pid)
        assert members["owner"]["username"] == "wang" and members["myRole"] == "viewer"
        assert {item["username"] for item in members["members"]} == {"li", "zhao"}
    finally:
        research_sharing.bind(None)

    me["user"] = users["li"]
    editor = research_sharing.binding_for_request(pid, "POST", f"/research/projects/{pid}/steps")
    assert editor is not None and editor.role == "editor"
    with pytest.raises(PermissionError):
        research_sharing.binding_for_request(pid, "DELETE", f"/research/projects/{pid}")
    with pytest.raises(PermissionError):
        research_sharing.update_comment(store, pid, comment["id"], body="rewritten by someone else")
    resolved = research_sharing.update_comment(store, pid, comment["id"], resolved=True, can_moderate=True)
    assert resolved["resolved"]
    task = research_sharing.assign_step(store, pid, "s1", "u-li", "li", "Check the citation")
    assert research_sharing.update_assignment(store, pid, task["id"], "done")["status"] == "done"
    assert research_sharing.list_assignments(store, pid)[0]["assignee"] == "li"
    assert [item["projectId"] for item in research_sharing.shared_with_me()] == [pid]

    me["user"] = users["owner"]
    assert research_sharing.remove_member(pid, "u-zhao")
    me["user"] = users["zhao"]
    assert research_sharing.binding_for_request(pid, "GET", f"/research/projects/{pid}") is None


# --------------------------------------------------------------------------- #
# Usage accounting
# --------------------------------------------------------------------------- #

def test_usage_is_normalised_and_captured_per_logical_call():
    assert normalise_usage({"prompt_tokens": 10, "completion_tokens": 5}) == {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15}
    assert normalise_usage({"input_tokens": 3, "output_tokens": 4, "total_tokens": 9})["totalTokens"] == 9
    assert normalise_usage({}) == {} and normalise_usage(None) == {}
    from aleggo.study import model_registry

    with capture_model_usage() as outer:
        model_registry._report_usage("research.meridian", "qwen-max", {"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        with capture_model_usage() as inner:
            model_registry._report_usage("research.meridian", "qwen-max", {"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    model_registry._report_usage("research.meridian", "qwen-max", {"usage": {"prompt_tokens": 100}})  # nobody listening
    assert len(outer) == 2 and len(inner) == 1
    summary = summarise_usage(outer)
    assert summary["totalTokens"] == 12 and summary["requests"] == 2 and summary["actualModel"] == "qwen-max"


# --------------------------------------------------------------------------- #
# Math worker reliability on Windows-like platforms
# --------------------------------------------------------------------------- #

class _FakeKernel32:
    def __init__(self):
        self.calls: list[tuple] = []
        self.limits = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args):
            self.calls.append((name, args))
            if name == "SetInformationJobObject":
                import ctypes

                self.limits = ctypes.cast(args[2], ctypes.c_void_p)
            return 1 if name != "CreateJobObjectW" else 1234

        call.restype = None
        setattr(self, name, call)
        return call


def test_windows_job_object_sets_kill_on_close_memory_and_process_limits(monkeypatch):
    import ctypes

    assert math_worker._windows_job_object(1024) is None  # not Windows
    fake = _FakeKernel32()
    monkeypatch.setattr(math_worker.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: fake, raising=False)
    before = len(math_worker._WINDOWS_JOB_HANDLES)
    job = math_worker._windows_job_object(512 * 1024 * 1024, process_handle_pid=4321, max_processes=1)
    names = [name for name, _args in fake.calls]
    assert job == 1234 and names[:2] == ["CreateJobObjectW", "SetInformationJobObject"]
    assert ("OpenProcess" in names) and ("AssignProcessToJobObject" in names)
    assert len(math_worker._WINDOWS_JOB_HANDLES) == before + 1  # kept alive: closing would kill the job
    math_worker._WINDOWS_JOB_HANDLES.pop()
    assert math_worker._windows_job_object(0) is None


def test_worker_path_checks_fold_case_and_resolve_symlinks(tmp_path, monkeypatch):
    root = tmp_path / "Workspace"
    (root / "project").mkdir(parents=True)
    link = tmp_path / "alias"
    link.symlink_to(root, target_is_directory=True)
    roots = (math_worker._normalise(str(root)),)
    assert math_worker._under(str(link / "project" / "file.txt"), roots)
    assert not math_worker._under(str(tmp_path / "Workspace2" / "x"), roots)
    assert not math_worker._under("", roots)
    monkeypatch.setattr(math_worker.os.path, "normcase", lambda value: value.lower())
    folded = (math_worker._normalise(str(root)),)
    assert math_worker._under(str(root).upper() + "/project/a.txt", folded) or math_worker._under(str(root) + "/PROJECT/a.txt", folded)


# --------------------------------------------------------------------------- #
# HTTP endpoints
# --------------------------------------------------------------------------- #

@pytest.fixture
def lab_api(research, tmp_path, monkeypatch):
    from aleggo.api.routers import research as research_router
    from aleggo.api.routers import research_extensions

    store, domain, pid = research
    monkeypatch.setenv("ALEGGO_RESEARCH_SHARES_FILE", str(tmp_path / "shares.json"))
    app = FastAPI()
    app.include_router(research_router.router, prefix="/research")
    with patch("aleggo.api.routers.research._store", return_value=store), \
            patch.object(research_extensions, "ResearchStore", return_value=store), TestClient(app) as client:
        yield store, domain, pid, client


def test_lab_endpoints_status_databases_and_imports(lab_api):
    store, domain, pid, client = lab_api
    assert {item["key"] for item in client.get("/research/cas/status").json()["engines"]} == {"pari", "gap", "sage", "macaulay2"}
    status = client.get("/research/lean/status").json()
    assert "checks" in status and "install" in status
    math_databases._CACHE.clear()
    with patch.object(math_databases, "_get", _fake_get([])):
        found = client.post(f"/research/projects/{pid}/database-query",
                            json={"tool": "oeis_search", "input": {"query": "1,1,2,5,14"}, "saveSources": True}).json()
    assert found["ok"] and found["data"]["items"][0]["id"] == "A000108" and len(found["savedSourceIds"]) == 1
    assert any(source["title"].startswith("A000108") for source in domain.list_sources(pid))
    assert client.post(f"/research/projects/{pid}/database-query", json={"tool": "maple", "input": {}}).status_code == 422


    tex = client.post(f"/research/projects/{pid}/imports/latex", params={"filename": "paper.zip"},
                      content=_zip({"main.tex": _MAIN, "sections/lemmas.tex": _LEMMAS, "refs.bib": _BIB}),
                      headers={"Content-Type": "application/octet-stream"})
    assert tex.status_code in (200, 201), tex.text
    body = tex.json()
    assert body["mainFile"] == "main.tex" and len(body["statements"]) == 3 and len(body["references"]) == 2
    promoted = client.post(f"/research/projects/{pid}/imports/{body['source']['id']}/promote",
                           json={"statements": [body["statements"][-1]["index"]], "references": [0]}).json()
    assert len(promoted["nodes"]) == 1 and promoted["nodes"][0]["type"] == "theorem"
    assert client.post(f"/research/projects/{pid}/imports/latex", params={"filename": "paper.zip"}, content=b"").status_code == 422

    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(image, format="PNG")

    async def fake_transcribe(images, prompt):
        return {"text": "$a^2+b^2=c^2$", "model": "qwen-vl-max", "usage": {"promptTokens": 5, "completionTokens": 5, "totalTokens": 10}}

    with patch("aleggo.study.research_imports._transcribe", fake_transcribe):
        hand = client.post(f"/research/projects/{pid}/imports/handwriting", files=[("files", ("p1.png", image.getvalue(), "image/png"))],
                           data={"hint": "Pythagoras"})
    assert hand.status_code == 200, hand.text
    assert hand.json()["transcription"] == "$a^2+b^2=c^2$"
    saved = client.post(f"/research/projects/{pid}/imports/handwriting/save",
                        json={"title": "Pythagoras", "content": "$a^2+b^2=c^2$", "model": "qwen-vl-max", "originals": ["p1.png"]})
    assert saved.status_code in (200, 201) and saved.json()["type"] == "handwritten"


def test_lean_project_settings_api_is_additive_and_persists(lab_api):
    _store, _domain, pid, client = lab_api
    url = f"/research/projects/{pid}/settings/lean"
    assert client.get(url).json() == {"confirmBeforeCheck": True, "autoCallEnabled": False}
    saved = client.put(url, json={"confirmBeforeCheck": False, "autoCallEnabled": True})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"confirmBeforeCheck": False, "autoCallEnabled": True}
    assert client.get(url).json() == saved.json()


def test_lab_endpoints_review_memory_collaboration_usage_and_eval(lab_api):
    store, domain, pid, client = lab_api
    with patch.object(ProofReviewer, "start", lambda self, project_id, review_id, **kwargs: None):
        created = client.post(f"/research/projects/{pid}/proof-reviews", json={"proof": _PROOF.replace("\n", "\n\n"), "title": "Euclid"})
    assert created.status_code == 202, created.text
    review_id = created.json()["id"]
    assert client.get(f"/research/projects/{pid}/proof-reviews").json()["items"][0]["id"] == review_id
    annotated = client.put(f"/research/projects/{pid}/proof-reviews/{review_id}/steps/S2", json={"verdict": "error", "note": "N need not be prime"})
    assert annotated.status_code == 200 and annotated.json()["overall"] == "errors-found"
    assert client.put(f"/research/projects/{pid}/proof-reviews/{review_id}/steps/S99", json={"verdict": "error"}).status_code == 404

    memory = client.post("/research/memory", json={"content": "Catalan: use binom(2n,n) <= 4^n", "kind": "technique", "tags": ["catalan"]})
    assert memory.status_code == 201
    memory_id = memory.json()["id"]
    assert client.get("/research/memory", params={"query": "catalan"}).json()["items"][0]["id"] == memory_id
    assert client.put(f"/research/memory/{memory_id}", json={"confirmed": False}).json()["confirmed"] is False
    assert client.get(f"/research/projects/{pid}/memory/suggestions").status_code == 200
    assert client.delete(f"/research/memory/{memory_id}").status_code == 204

    comment = client.post(f"/research/projects/{pid}/comments", json={"body": "Looks good", "targetType": "project"})
    assert comment.status_code in (200, 201), comment.text
    comment_id = comment.json()["id"]
    assert client.put(f"/research/projects/{pid}/comments/{comment_id}", json={"resolved": True}).json()["resolved"]
    assert len(client.get(f"/research/projects/{pid}/comments").json()["items"]) == 1
    assert client.delete(f"/research/projects/{pid}/comments/{comment_id}").status_code == 204
    team = client.get(f"/research/projects/{pid}/members").json()
    assert team["myRole"] == "owner" and team["members"] == []
    assert "multiUser" in client.get("/research/collaboration/candidates").json()

    domain.record_tool_run(pid, "llm:explorer", {"modelId": "research.meridian"}, "reply", "COMPLETED",
                           metadata={"usage": {"promptTokens": 2000, "completionTokens": 1000, "totalTokens": 3000, "actualModel": "qwen-max"}})
    domain.record_tool_run(pid, "llm:planner", {}, "reply", "COMPLETED", metadata={})
    usage = client.get(f"/research/projects/{pid}/usage").json()
    assert usage["total"]["totalTokens"] == 3000 and usage["untrackedCalls"] == 1 and not usage["pricesConfigured"]
    prices = client.put("/research/usage/prices", json={"currency": "CNY", "models": {"qwen-max": {"prompt": 0.02, "completion": 0.06}}})
    assert prices.status_code == 200, prices.text
    priced = client.get(f"/research/projects/{pid}/usage").json()
    assert priced["pricesConfigured"] and abs(priced["total"]["cost"] - 0.1) < 1e-9
    assert priced["byModel"][0]["model"] == "qwen-max" and priced["byRole"][0]["role"] == "explorer"

    problems = client.get("/research/eval/problems").json()
    assert sum(problems["categories"].values()) == len(problems["problems"]) >= 20
