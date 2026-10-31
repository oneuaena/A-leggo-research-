"""LaTeX paper drafts: evidence statuses survive, hostile text cannot break or escape the document."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from aleggo.study.research_delivery import ResearchDelivery
from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_latex import _Converter, build_latex_bibliography, build_latex_paper
from aleggo.study.research_store import ResearchStore
from aleggo.study.store import AleggoStore

HOSTILE = r"""# Heading with $x$
We study $a_n = \sum_{k=1}^n k^2$ and $$\begin{align} a_n &= \frac{n(n+1)(2n+1)}{6} \\ &\le n^3 \end{align}$$
Costs $5 and $10. 100% & #1 ^ ~ _ {braces} \backslash \end{document}.
Unsafe $\input{/etc/passwd}\immediate\write18{rm -rf /}\def\x{1}$ and unbalanced $\frac{1}{2$ end.
Unicode α ≤ β → ∞ in ℝ² ✅ 🎉 and \(y\) \[ z = \alpha \]
- **bold $x$** and *emph* `code_$x$` [link](https://example.org/a_b%20c#frag)
  1. nested
- back
| a | b |
|---|---|
| $x$ | 2 |
> quote $q$
```python
print("\end{verbatim}")
```
\begin{equation}\label{eq:1} E = mc^2 \end{equation}
$x % comment$ and $a & b$ and trailing $x\$ odd
"""


def tables(cjk: bool = False) -> dict:
    node = lambda kind, title, content, status="unverified": {"node_type": kind, "title": title, "content": content, "status": status, "metadata_json": "{}"}  # noqa: E731
    return {
        "research_projects": [{"title": "平方和 #1" if cjk else "Sums & 100% bounds_x", "question": "设 $n$ 为正整数" if cjk else "Study $\\sum k^2$"}],
        "research_graph_nodes": [node("definition", "Sum $S_n$", "Let $S_n=\\sum k^2$."), node("theorem", "Main [closed]", HOSTILE, "verified"),
                                 node("lemma", "Bound ] } {", HOSTILE, "llm_assessed"), node("theorem", "Claimed theorem", "text"),
                                 node("counterexample", "n=0", "$S_0=0$"), node("evidence", "Checked n<=1000", HOSTILE)],
        "research_artifacts": [{"title": "Final", "content": "## Synthesis\n\n" + HOSTILE, "metadata_json": json.dumps({"artifactKind": "final-verification"}), "agent_role": "independent-verifier", "status": "UNVERIFIED_SYNTHESIS"},
                               {"title": "Step output: $x$ & y_1", "content": HOSTILE, "metadata_json": json.dumps({"artifactKind": "step-output"}), "agent_role": "lemma-generator", "status": "CONJECTURAL"}],
        "research_tool_runs": [{"tool_name": "run_python", "status": "COMPLETED", "input_json": json.dumps({"code": "print(285) # \\end{verbatim} α"}), "output_text": json.dumps({"output": "285 ≤ ✅"})},
                               {"tool_name": "llm:planner", "status": "COMPLETED", "input_json": "{}", "output_text": "hidden-model-call"}],
        "research_failed_paths": [{"route_text": "Induction on $n$ & co", "reason": "Fails at 100%", "retry_condition": "if $p$ prime_"}],
        "research_sources": [{"id": "src-1", "title": "On $A_n$ & 50% {things}", "authors": "Ada Lovelace, Alan Turing", "year": "1950a", "doi": "10.1/x_y%", "url": "https://x.org/a_b#c"},
                             {"id": "src-2", "title": "Half } open $\\{x$", "authors": "Smith, John", "year": None, "doi": None, "url": None}],
    }


PLAN = {"objective": "Bound $S_n$", "problemFormalization": HOSTILE, "assumptions": ["1. n odd", "# heading-like", "$$x$$"], "keyQuestions": ["Is $x$ prime?"]}


def outside_verbatim(document: str) -> str:
    return re.sub(r"\\begin\{verbatim\}.*?\\end\{verbatim\}", "", document, flags=re.S)


def structurally_balanced(document: str) -> bool:
    body = outside_verbatim(document)
    body = re.sub(r"(?<!\\)%.*", "", body)
    depth = 0
    for token in re.findall(r"\\.|[{}]", body):
        depth += token == "{"
        depth -= token == "}"
        if depth < 0:
            return False
    stack: list[str] = []
    for kind, name in re.findall(r"\\(begin|end)\{([^}]*)\}", body):
        if kind == "begin":
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return depth == 0 and not stack


class LatexConverterTests(unittest.TestCase):
    def test_text_is_escaped_and_math_passes_through(self):
        converter = _Converter(False)
        rendered = converter.convert("Costs $5 and $10, 100% & #1 _x_ {y}. Value $x^2 + y_1$ here.")
        self.assertIn(r"\$5 and \$10", rendered)
        self.assertIn(r"100\% \& \#1", rendered)
        self.assertIn(r"\{y\}", rendered)
        self.assertIn("$x^2 + y_1$", rendered)

    def test_dangerous_primitives_are_neutralised_inside_math(self):
        rendered = _Converter(False).convert(r"$\input{/etc/passwd} \immediate\write18{id} \def\x{1}$ and \end{document}")
        self.assertNotRegex(rendered, r"\\(?:input|immediate|write18|def)\b")
        self.assertNotIn(r"\end{document}", rendered.replace(r"\textbackslash{}end", ""))
        self.assertIn(r"\backslash\,input", rendered)

    def test_unbalanced_math_falls_back_to_text_and_verbatim_cannot_be_closed(self):
        converter = _Converter(False)
        self.assertIn(r"\$\textbackslash{}frac\{1\}\{2\$", converter.convert(r"broken $\frac{1}{2$ math"))
        block = converter.convert("```\nprint('\\end{verbatim}')\n```")
        self.assertEqual(block.count(r"\end{verbatim}"), 1)

    def test_display_environments_are_normalised(self):
        converter = _Converter(False)
        nested = converter.convert(r"$$\begin{align} a &= b \\ &= c \end{align}$$")
        self.assertIn(r"\begin{aligned}", nested)
        self.assertNotIn(r"\begin{align}", nested)
        labelled = converter.convert(r"\begin{equation}\label{eq:1} E = mc^2 \end{equation}")
        self.assertIn(r"\begin{equation*}", labelled)
        self.assertNotIn(r"\label", labelled)

    def test_unicode_symbols_become_commands_for_pdflatex(self):
        rendered = _Converter(False).convert("α ≤ β in ℝ ✅ 🎉 and $α → ∞$")
        self.assertIn(r"$\alpha$", rendered)
        self.assertIn(r"\mathbb{R}", rendered)
        self.assertIn(r"\alpha \to \infty", rendered)
        self.assertFalse(any(ord(char) > 127 for char in rendered))


class LatexPaperTests(unittest.TestCase):
    def test_claims_keep_their_recorded_evidence_status(self):
        document = build_latex_paper(tables(), PLAN)
        self.assertIn(r"\documentclass[11pt]{article}", document)
        self.assertIn("formally verified (Lean kernel)", document)
        self.assertIn("assessed by a language model only", document)
        # An unverified "theorem" is presented as a conjecture, never as a theorem.
        self.assertRegex(document, r"\\begin\{conjecture\}\[\{Claimed theorem\}\]")
        self.assertIn(r"\begin{counterexample}", document)
        self.assertIn(r"\nocite{*}", document)
        self.assertNotIn("hidden-model-call", document)
        self.assertNotIn("\x00", document)
        self.assertTrue(structurally_balanced(document))

    def test_unknown_math_macros_get_fallbacks(self):
        document = build_latex_paper(tables(), PLAN)
        self.assertIn(r"\providecommand{\x}{\ensuremath{\mathrm{x}}}", document)

    def test_chinese_projects_use_ctex_and_chinese_headings(self):
        document = build_latex_paper(tables(cjk=True), PLAN)
        self.assertTrue(document.startswith("% !TEX program = xelatex"))
        self.assertIn("{ctexart}", document)
        self.assertIn("引理", document)
        self.assertTrue(structurally_balanced(document))

    def test_empty_project_still_produces_a_document(self):
        document = build_latex_paper({"research_projects": [{"title": "", "question": ""}]}, None)
        self.assertIn(r"\begin{document}", document)
        self.assertNotIn(r"\bibliography{", document)
        self.assertTrue(structurally_balanced(document))

    def test_bibliography_fields_are_latex_safe(self):
        bib = build_latex_bibliography(tables()["research_sources"])
        self.assertIn("author = {Ada Lovelace and Alan Turing}", bib)
        self.assertIn("year = {1950}", bib)
        self.assertIn(r"50\%", bib)
        self.assertIn("$A_n$", bib)
        self.assertIn("author = {Smith, John}", bib)
        for entry in bib.split("@misc")[1:]:
            self.assertEqual(entry.count("{"), entry.count("}"), entry)

    @unittest.skipUnless(shutil.which("pdflatex") and shutil.which("bibtex"), "pdflatex not installed")
    def test_hostile_draft_compiles_with_pdflatex(self):
        self._compile(tables(), "pdflatex")

    @unittest.skipUnless(shutil.which("xelatex") and shutil.which("kpsewhich") and subprocess.run(["kpsewhich", "ctexart.cls"], capture_output=True).returncode == 0, "xelatex/ctex not installed")
    def test_chinese_draft_compiles_with_xelatex(self):
        self._compile(tables(cjk=True), "xelatex")

    def _compile(self, data: dict, engine: str) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "main.tex").write_text(build_latex_paper(data, PLAN), encoding="utf-8")
            (root / "references.bib").write_text(build_latex_bibliography(data["research_sources"]), encoding="utf-8")
            command = [engine, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "main.tex"]
            first = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=180)
            self.assertEqual(first.returncode, 0, first.stdout[-3000:])
            subprocess.run(["bibtex", "main"], cwd=root, capture_output=True, timeout=60)
            second = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=180)
            self.assertEqual(second.returncode, 0, second.stdout[-3000:])
            self.assertTrue((root / "main.pdf").read_bytes().startswith(b"%PDF-"))


class LatexPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ResearchStore(AleggoStore(Path(self.temp.name) / "a-leggo-data"))
        self.domain = ResearchDomainStore(self.store)
        self.pid = self.store.create_project("Bound $S_n$ for all n", "research.meridian")["id"]
        self.store.save_plan(self.pid, {"objective": "Bound $S_n$", "keyQuestions": ["Is it sharp?"], "steps": []}, changed_by="researcher", change_reason="Plan", change_summary="Plan")
        self.domain.create_graph_node(self.pid, "lemma", "Monotone", "$S_{n+1} > S_n$", status="llm_assessed")
        self.domain.record_tool_run(self.pid, "run_python", {"code": "print(1)", "apiKey": "sk-PRIVATE-LATEX", "cwd": "D:\\private\\ws"}, '{"output":"1"}', "COMPLETED")

    def tearDown(self):
        self.temp.cleanup()

    def test_package_contains_draft_and_still_restores(self):
        delivery = ResearchDelivery(self.store)
        package = delivery.export_package(self.pid)
        with zipfile.ZipFile(BytesIO(package)) as archive:
            names = set(archive.namelist())
            main = archive.read("paper/main.tex").decode("utf-8")
            readme = archive.read("README.md").decode("utf-8")
            inner = set(zipfile.ZipFile(BytesIO(archive.read("final/reproducibility-package.zip"))).namelist())
        self.assertIn("paper/references.bib", names)
        self.assertIn("paper/main.tex", inner)
        self.assertIn("paper/main.tex", readme)
        self.assertIn("Monotone", main)
        self.assertNotIn("sk-PRIVATE-LATEX", main)
        self.assertNotIn("private", main)
        restored = delivery.restore_package(package)
        self.assertNotEqual(restored["id"], self.pid)

    def test_paper_files_match_package_copy(self):
        delivery = ResearchDelivery(self.store)
        self.assertEqual(delivery.paper_files(self.pid)["main.tex"], delivery.files(self.pid)["paper/main.tex"])


if __name__ == "__main__":
    unittest.main()


class LatexExportEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_endpoint_returns_latex_zip(self):
        from unittest.mock import patch

        from aleggo.api.routers import research as research_router
        with tempfile.TemporaryDirectory() as folder:
            store = ResearchStore(AleggoStore(Path(folder) / "a-leggo-data"))
            project_id = store.create_project("平方和的上界", "research.meridian")["id"]
            with patch("aleggo.api.routers.research._store", return_value=store):
                response = await research_router.export_research_report(project_id, format="latex")
            self.assertEqual(response.media_type, "application/zip")
            self.assertIn("latex.zip", response.headers["content-disposition"])
            with zipfile.ZipFile(BytesIO(response.body)) as archive:
                self.assertEqual(set(archive.namelist()), {"paper/main.tex", "paper/references.bib"})
                self.assertIn("{ctexart}", archive.read("paper/main.tex").decode("utf-8"))
