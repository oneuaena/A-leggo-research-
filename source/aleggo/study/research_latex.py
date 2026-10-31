"""LaTeX paper draft assembled from durable research records.

The draft is a writing aid for the researcher: it arranges the recorded
question, typed claims, computational evidence, failed routes and literature
into a compilable article skeleton. Every claim keeps its recorded evidence
status; model-written arguments are never presented as checked proofs.
"""

from __future__ import annotations

import json
import re
from typing import Any
import unicodedata

_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")

# Unicode symbols models commonly emit; mapped to math commands so that both
# pdfLaTeX and XeLaTeX compile them.
_MATH_SYMBOLS = {
    "≤": r"\le", "≥": r"\ge", "≠": r"\neq", "≈": r"\approx", "≡": r"\equiv", "∼": r"\sim", "≅": r"\cong",
    "→": r"\to", "←": r"\leftarrow", "↔": r"\leftrightarrow", "⇒": r"\Rightarrow", "⇐": r"\Leftarrow",
    "⇔": r"\Leftrightarrow", "↦": r"\mapsto", "∈": r"\in", "∉": r"\notin", "∋": r"\ni", "⊂": r"\subset",
    "⊆": r"\subseteq", "⊃": r"\supset", "⊇": r"\supseteq", "∪": r"\cup", "∩": r"\cap", "∅": r"\emptyset",
    "∀": r"\forall", "∃": r"\exists", "¬": r"\neg", "∧": r"\wedge", "∨": r"\vee", "⊕": r"\oplus", "⊗": r"\otimes",
    "∘": r"\circ", "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∮": r"\oint", "∂": r"\partial", "∇": r"\nabla",
    "∞": r"\infty", "√": r"\surd", "±": r"\pm", "∓": r"\mp", "×": r"\times", "÷": r"\div", "·": r"\cdot",
    "⋅": r"\cdot", "…": r"\ldots", "⋯": r"\cdots", "′": r"\prime", "∣": r"\mid", "∥": r"\parallel",
    "⊥": r"\perp", "∠": r"\angle", "°": r"^{\circ}", "ℵ": r"\aleph", "ℓ": r"\ell", "ℏ": r"\hbar",
    "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}", "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}",
    "𝔽": r"\mathbb{F}", "ℙ": r"\mathbb{P}", "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil", "⌉": r"\rceil",
    "⟨": r"\langle", "⟩": r"\rangle", "≪": r"\ll", "≫": r"\gg", "≺": r"\prec", "≻": r"\succ", "∝": r"\propto",
    "²": r"^{2}", "³": r"^{3}", "¹": r"^{1}", "⁰": r"^{0}", "⁴": r"^{4}", "⁵": r"^{5}", "⁶": r"^{6}",
    "⁷": r"^{7}", "⁸": r"^{8}", "⁹": r"^{9}", "ⁿ": r"^{n}", "₀": r"_{0}", "₁": r"_{1}", "₂": r"_{2}",
    "₃": r"_{3}", "₄": r"_{4}", "₅": r"_{5}", "₆": r"_{6}", "₇": r"_{7}", "₈": r"_{8}", "₉": r"_{9}",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "ϵ": r"\epsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho", "σ": r"\sigma",
    "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi", "ϕ": r"\phi", "χ": r"\chi",
    "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Υ": r"\Upsilon", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}
_TEXT_SYMBOLS = {
    "\u00a0": "~", "–": "--", "—": "---", "‘": "`", "’": "'", "“": "``", "”": "''", "„": ",,",
    "•": r"\textbullet{}", "§": r"\S{}", "¶": r"\P{}", "©": r"\copyright{}", "†": r"\dag{}", "‡": r"\ddag{}",
    "✓": r"$\checkmark$", "✔": r"$\checkmark$", "✗": "x", "✘": "x", "\u200b": "", "\ufeff": "",
}
_TEXT_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#", "^": r"\textasciicircum{}",
    "_": r"\_", "%": r"\%", "~": r"\textasciitilde{}", "<": r"\textless{}", ">": r"\textgreater{}", "|": r"\textbar{}",
}
# Primitives that could read/write files or redefine the document when a draft
# containing model text is compiled. Neutralised inside passthrough math.
_UNSAFE_MATH = re.compile(
    r"\\(?:input|include|includeonly|InputIfFileExists|write|immediate|openout|openin|read|readline|"
    r"catcode|uccode|lccode|def|edef|gdef|xdef|let|futurelet|csname|expandafter|directlua|luaexec|"
    r"latelua|special|pdfliteral|usepackage|RequirePackage|documentclass|newwrite|newread|ShellEscape|"
    r"endinput|endcsname|newcommand|renewcommand|providecommand|DeclareRobustCommand|makeatletter|"
    r"output|everypar|everymath|everydisplay|jobname|outer|batchmode|nonstopmode|scrollmode|errorstopmode)(?![A-Za-z@])"
    r"|\\(?:begin|end)\s*\{document\}"
)
_ALIGNING_ENVS = ("align", "aligned", "alignat", "eqnarray", "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix",
                  "Bmatrix", "smallmatrix", "cases", "array", "split", "gathered", "tabular", "dcases", "rcases")
_DISPLAY_ENVS = r"equation|align|alignat|gather|multline|eqnarray|flalign"
_MATH_PATTERN = re.compile(
    r"(?P<dd>\$\$(?P<ddc>.+?)\$\$)"
    r"|(?P<br>\\\[(?P<brc>.+?)\\\])"
    r"|(?P<env>\\begin\{(?P<envname>" + _DISPLAY_ENVS + r")(?P<star>\*?)\}.+?\\end\{(?P=envname)(?P=star)\})"
    r"|(?P<pa>\\\((?P<pac>.+?)\\\))"
    r"|(?P<sd>(?<![\\$A-Za-z0-9])\$(?![\s$])(?P<sdc>[^$\n]+?)(?<![\s\\])\$(?![A-Za-z0-9$]))",
    re.S,
)
_FENCE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)", re.S | re.M)
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,4}[.)])\s+(.*)$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FALLBACK_MARK = "%%A-LEGGO-MATH-FALLBACKS%%"
_PLACEHOLDER = re.compile(r"\x00([MCB])(\d+)\x00")

_STRINGS = {
    "en": {
        "abstract": "Abstract", "intro": "Problem statement", "question": "Research question",
        "objective": "Objective", "formalization": "Formalization", "assumptions": "Standing assumptions",
        "definitions": "Definitions", "results": "Results", "verified": "Formally verified results",
        "claims": "Lemmas, propositions and conjectures", "counterexamples": "Counterexamples",
        "evidence": "Computational and heuristic evidence", "experiments": "Recorded computations",
        "synthesis": "Summary of findings", "failed": "Approaches that did not work",
        "open": "Open questions and next steps", "appendix": "Research notes by step",
        "status": "Evidence status", "noclaims": "No typed claim has been recorded yet.",
        "noexperiments": "No computation has been recorded yet.", "retry": "Retry only if",
        "draft": "Draft assembled by A-leggo from recorded research. Claim statuses are recorded evidence levels: only results marked as Lean-verified were checked by a proof kernel; all other arguments are model- or researcher-written and must be checked before submission.",
        "code": "Code", "output": "Output", "tool": "Tool", "input": "Input",
        "statuses": {"verified": "formally verified (Lean kernel)", "llm_assessed": "assessed by a language model only — not a proof",
                     "unverified": "unverified", "refuted": "refuted", "rejected": "rejected"},
    },
    "zh": {
        "abstract": "摘要", "intro": "问题陈述", "question": "研究问题", "objective": "研究目标",
        "formalization": "形式化表述", "assumptions": "基本假设", "definitions": "定义", "results": "结果",
        "verified": "已形式化验证的结果", "claims": "引理、命题与猜想", "counterexamples": "反例",
        "evidence": "计算与启发式证据", "experiments": "计算记录", "synthesis": "研究发现小结",
        "failed": "未成功的思路", "open": "开放问题与下一步", "appendix": "分步研究笔记",
        "status": "证据状态", "noclaims": "尚未记录任何结构化命题。", "noexperiments": "尚未记录任何计算。",
        "retry": "仅在以下条件下重试",
        "draft": "本稿由 A-leggo 根据研究记录自动整理。命题状态为记录的证据等级：只有标注为 Lean 已验证的结果经过证明内核检查，其余论证均由模型或研究者撰写，投稿前必须逐一核对。",
        "code": "代码", "output": "输出", "tool": "工具", "input": "输入",
        "statuses": {"verified": "已形式化验证（Lean 内核）", "llm_assessed": "仅经语言模型评估——不是证明",
                     "unverified": "未验证", "refuted": "已被否定", "rejected": "已拒绝"},
    },
}
_ENV_NAMES = {
    "en": {"theorem": "Theorem", "lemma": "Lemma", "proposition": "Proposition", "conjecture": "Conjecture",
           "definition": "Definition", "remark": "Remark", "counterexample": "Counterexample", "observation": "Observation"},
    "zh": {"theorem": "定理", "lemma": "引理", "proposition": "命题", "conjecture": "猜想",
           "definition": "定义", "remark": "注记", "counterexample": "反例", "observation": "观察"},
}


class _Converter:
    """Markdown-with-math to LaTeX, conservative enough to always compile."""

    def __init__(self, cjk: bool) -> None:
        self.cjk = cjk
        # Every control word passed through in math; undefined ones get a visible fallback.
        self.math_commands: set[str] = set()

    # -- characters -----------------------------------------------------
    def _foreign(self, char: str) -> str:
        if self.cjk and (_CJK.match(char) or unicodedata.category(char)[0] in "LNP"):
            return char
        if ord(char) <= 0x17F and unicodedata.category(char)[0] == "L":
            return char
        if unicodedata.category(char) in {"So", "Sk", "Cs", "Co", "Cn", "Mn"} or ord(char) >= 0x1F000:
            return ""
        return "?"

    def text(self, value: str) -> str:
        out = []
        for char in value:
            if char in _TEXT_ESCAPES:
                out.append(_TEXT_ESCAPES[char])
            elif char in _TEXT_SYMBOLS:
                out.append(_TEXT_SYMBOLS[char])
            elif char in _MATH_SYMBOLS:
                out.append("$" + _MATH_SYMBOLS[char] + "$")
            elif char == "\t":
                out.append(" ")
            elif ord(char) < 32 and char != "\n":
                continue
            elif ord(char) < 128:
                out.append(char)
            else:
                out.append(self._foreign(char))
        return "".join(out)

    def raw(self, value: str) -> str:
        """Characters for verbatim blocks: nothing executes, only encoding matters."""
        out = []
        for char in value.replace("\t", "    "):
            if char == "\n" or 32 <= ord(char) < 127:
                out.append(char)
            elif ord(char) < 160:
                continue
            elif self.cjk:
                category = unicodedata.category(char)
                out.append("" if category in {"So", "Cs", "Co", "Cn", "Cc", "Cf"} or ord(char) >= 0x1F000 else char)
            else:
                out.append(self._foreign(char))
        return re.sub(r"\\end\s*\{verbatim\}", r"\\end {verbatim}", "".join(out))

    # -- math -----------------------------------------------------------
    def _math_body(self, body: str, *, aligned_env: bool) -> str | None:
        body = _UNSAFE_MATH.sub(lambda match: r"\backslash\," + match.group(0)[1:], body)
        # Labels repeat across model text; unnumbered displays keep the draft stable.
        body = re.sub(r"\\label\s*\{[^{}]*\}", "", body)
        body = re.sub(r"(?<!\\)%", r"\\%", body)
        body = re.sub(r"(?<!\\)#", r"\\#", body)
        if not aligned_env and not any("\\begin{" + name in body for name in _ALIGNING_ENVS):
            body = re.sub(r"(?<!\\)&", r"\\&", body)
        chars = []
        cjk_run: list[str] = []

        def flush_cjk() -> None:
            if not cjk_run:
                return
            # Math mode needs \text{...} for CJK; raw Han breaks pdfLaTeX and confuses XeLaTeX.
            piece = "".join(cjk_run)
            cjk_run.clear()
            safe = piece.replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}")
            chars.append("\\text{" + safe + "}")

        for char in body:
            if char in _MATH_SYMBOLS:
                flush_cjk()
                chars.append(_MATH_SYMBOLS[char] + " ")
            elif ord(char) < 128:
                flush_cjk()
                chars.append(char if (char == "\n" or ord(char) >= 32) else " ")
            elif self.cjk and _CJK.match(char):
                cjk_run.append(char)
            elif self.cjk and unicodedata.category(char)[0] == "L" and ord(char) > 0x17F:
                cjk_run.append(char)
            elif ord(char) <= 0x17F and unicodedata.category(char)[0] == "L":
                flush_cjk()
                chars.append(char)
            else:
                flush_cjk()
                chars.append("?" if unicodedata.category(char)[0] not in "MC" else "")
        flush_cjk()
        body = "".join(chars)
        self.math_commands.update(re.findall(r"\\([A-Za-z]+)", body))
        # Unbalanced groups or environments would derail the whole document.
        depth = 0
        for match in re.finditer(r"\\.|[{}]", body, re.S):
            token = match.group(0)
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
                if depth < 0:
                    return None
        if depth:
            return None
        begins = re.findall(r"\\begin\s*\{([^}]*)\}", body)
        ends = re.findall(r"\\end\s*\{([^}]*)\}", body)
        if sorted(begins) != sorted(ends) or re.search(r"\n[ \t]*\n", body):
            return None
        if re.search(r"(?<!\\)(?:\\\\)*\\$", body.rstrip()):
            return None
        return body

    @staticmethod
    def _inside_display(body: str) -> str:
        """`$$\\begin{align}..$$` is common model output; use the nested form."""
        body = re.sub(r"\\(begin|end)\s*\{(?:align|flalign|eqnarray|alignat)\*?\}(\{\d+\})?", lambda m: "\\" + m.group(1) + "{aligned}", body)
        body = re.sub(r"\\(begin|end)\s*\{(?:gather|multline)\*?\}", lambda m: "\\" + m.group(1) + "{gathered}", body)
        return re.sub(r"\\(?:begin|end)\s*\{equation\*?\}", "", body)

    def math(self, match: re.Match[str], *, display: bool = True) -> str | None:
        source = match.group(0)
        if match.group("dd") is not None or match.group("br") is not None:
            inner = self._inside_display(match.group("ddc") if match.group("dd") is not None else match.group("brc"))
            body = self._math_body(inner, aligned_env=False)
            if body is None or not body.strip():
                return None
            return "\n\\[\n" + body.strip() + "\n\\]\n" if display else "$\\displaystyle " + " ".join(body.split()) + "$"
        if match.group("env") is not None:
            if not display:
                return None
            name = match.group("envname") + match.group("star")
            inner = source[source.index("}") + 1:source.rindex("\\end")]
            body = self._math_body(inner, aligned_env=match.group("envname") in {"align", "alignat", "eqnarray", "flalign"})
            if body is None or not body.strip():
                return None
            starred = name if name.endswith("*") else name + "*"
            if starred in {"eqnarray*", "alignat*"}:
                starred = "align*"
                body = re.sub(r"^\s*\{\d+\}", "", body).replace("&=&", "&=").replace("& = &", "&=")
            return "\n\\begin{" + starred + "}\n" + body.strip() + "\n\\end{" + starred + "}\n"
        body = self._math_body(match.group("pac") if match.group("pa") is not None else match.group("sdc"), aligned_env=False)
        if body is None or not body.strip():
            return None
        return "$" + " ".join(body.split()) + "$"

    # -- inline and block markdown -------------------------------------
    def protect(self, value: str, slots: list[str], raw: dict[int, str], *, display: bool = True) -> str:
        """Move code spans and math into slots so text escaping never touches them."""
        def code(match: re.Match[str]) -> str:
            slots.append(r"\texttt{" + self.text(match.group(1)) + "}")
            raw[len(slots) - 1] = match.group(0)
            return f"\x00C{len(slots) - 1}\x00"

        def math(match: re.Match[str]) -> str:
            rendered = self.math(match, display=display)
            if rendered is None:
                return match.group(0)  # falls back to escaped text
            slots.append(rendered)
            raw[len(slots) - 1] = match.group(0)
            return f"\x00M{len(slots) - 1}\x00"

        value = re.sub(r"`([^`\n]+)`", code, value)
        return _MATH_PATTERN.sub(math, value)

    def inline(self, value: str, slots: list[str]) -> str:
        def link(match: re.Match[str]) -> str:
            slots.append(self.text(match.group(1)) + r" (\texttt{" + self.text(match.group(2)) + "})")
            return f"\x00C{len(slots) - 1}\x00"

        value = re.sub(r"\[([^\[\]\n\x00]+)\]\((https?://[^\s()\x00]+)\)", link, value)
        pieces = re.split(r"(\x00[MCB]\d+\x00)", value)
        rendered = "".join(piece if _PLACEHOLDER.fullmatch(piece) else self.text(piece) for piece in pieces)
        rendered = re.sub(r"\*\*(?=\S)([^\n]+?)(?<=\S)\*\*", r"\\textbf{\1}", rendered)
        rendered = re.sub(r"(?<![*\w\\])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![*\w])", r"\\emph{\1}", rendered)
        return rendered

    def restore(self, value: str, slots: list[str]) -> str:
        for _ in range(3):
            value = _PLACEHOLDER.sub(lambda m: slots[int(m.group(2))], value)
        return value.replace("\x00", "")

    def line(self, value: Any) -> str:
        """One-line contexts (titles, items, optional arguments): no blocks, no display math."""
        slots: list[str] = []
        text = " ".join(str(value or "").replace("\x00", "").split())
        return self.restore(self.inline(self.protect(text, slots, {}, display=False), slots), slots).strip()

    def convert(self, markdown: str, *, heading: str = "subsubsection*") -> str:
        slots: list[str] = []
        text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")

        def fence(match: re.Match[str]) -> str:
            slots.append("\\begin{verbatim}\n" + self.raw(match.group(2).rstrip("\n")) + "\n\\end{verbatim}")
            return f"\n\x00B{len(slots) - 1}\x00\n"

        text = _FENCE.sub(fence, text)
        raw_math: dict[int, str] = {}
        text = self.protect(text, slots, raw_math)
        lines = text.split("\n")
        out: list[str] = []
        paragraph: list[str] = []
        stack: list[tuple[int, str]] = []

        def flush_paragraph() -> None:
            if paragraph:
                out.append(self.inline(" ".join(part.strip() for part in paragraph), slots))
                out.append("")
                paragraph.clear()

        def close_lists(level: int = -1) -> None:
            while stack and stack[-1][0] > level:
                out.append("\\end{" + stack.pop()[1] + "}")
            if level < 0:
                out.append("")

        def restore_raw(value: str) -> str:
            return _PLACEHOLDER.sub(lambda m: raw_math.get(int(m.group(2)), ""), value)

        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            block = re.fullmatch(r"\x00B(\d+)\x00", stripped)
            if block:
                flush_paragraph()
                if stack:
                    close_lists()
                out.extend([slots[int(block.group(1))], ""])
                index += 1
                continue
            if not stripped:
                flush_paragraph()
                if stack:
                    # A blank line inside a list only ends it when the next line is not an item.
                    upcoming = next((item for item in lines[index + 1:] if item.strip()), "")
                    if not _LIST_ITEM.match(upcoming):
                        close_lists()
                index += 1
                continue
            heading_match = _HEADING.match(line)
            if heading_match:
                flush_paragraph()
                if stack:
                    close_lists()
                title = self.line(restore_raw(heading_match.group(2)))
                if heading == "bold":
                    # Sectioning inside theorem-like environments breaks list layout; use a bold line.
                    out.extend(["\\par\\medskip\\noindent\\textbf{" + title + "}\\par", ""])
                else:
                    command = heading if len(heading_match.group(1)) <= 2 else "paragraph*"
                    out.extend(["\\" + command + "{" + title + "}", ""])
                index += 1
                continue
            if re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", stripped.replace(" ", "")):
                flush_paragraph()
                if stack:
                    close_lists()
                out.extend(["\\par\\noindent\\rule{\\linewidth}{0.4pt}\\par", ""])
                index += 1
                continue
            if stripped.startswith("|"):
                flush_paragraph()
                if stack:
                    close_lists()
                rows = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    rows.append(restore_raw(lines[index].strip()))
                    index += 1
                out.extend(["{\\small", "\\begin{verbatim}", self.raw("\n".join(rows)), "\\end{verbatim}", "}", ""])
                continue
            if stripped.startswith(">"):
                flush_paragraph()
                if stack:
                    close_lists()
                quoted = []
                while index < len(lines) and lines[index].strip().startswith(">"):
                    quoted.append(lines[index].strip()[1:].strip())
                    index += 1
                out.extend(["\\begin{quote}", self.inline(" ".join(quoted), slots), "\\end{quote}", ""])
                continue
            item = _LIST_ITEM.match(line)
            if item:
                flush_paragraph()
                indent = len(item.group(1).replace("\t", "    "))
                kind = "enumerate" if item.group(2)[0].isdigit() else "itemize"
                if not stack or indent > stack[-1][0]:
                    if len(stack) < 3:
                        stack.append((indent, kind))
                        out.append("\\begin{" + kind + "}")
                else:
                    while len(stack) > 1 and indent < stack[-1][0]:
                        out.append("\\end{" + stack.pop()[1] + "}")
                    if stack[-1][1] != kind:
                        out.append("\\end{" + stack.pop()[1] + "}")
                        stack.append((indent, kind))
                        out.append("\\begin{" + kind + "}")
                body = [item.group(3)]
                index += 1
                while index < len(lines) and lines[index].strip() and not _LIST_ITEM.match(lines[index]) \
                        and not _HEADING.match(lines[index]) and lines[index].startswith((" ", "\t")) \
                        and not re.fullmatch(r"\x00B\d+\x00", lines[index].strip()):
                    body.append(lines[index].strip())
                    index += 1
                out.append("\\item " + self.inline(" ".join(body), slots))
                continue
            if stack:
                close_lists()
            paragraph.append(line)
            index += 1
        flush_paragraph()
        if stack:
            close_lists()
        result = self.restore("\n".join(out), slots)
        return re.sub(r"\n{3,}", "\n\n", result).strip() + "\n"


def _decode(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        decoded = json.loads(value or "null")
    except (TypeError, ValueError):
        return fallback
    return fallback if decoded is None else decoded


def _clip(value: str, limit: int) -> str:
    value = str(value or "")
    return value if len(value) <= limit else value[:limit].rstrip() + "\n\n[…]"


def _balanced(value: str) -> bool:
    depth = 0
    for char in value:
        depth += char == "{"
        depth -= char == "}"
        if depth < 0:
            return False
    return depth == 0


def _bib_key(source_id: str) -> str:
    return "aleggo_" + re.sub(r"[^A-Za-z0-9]", "", str(source_id))


def build_latex_paper(tables: dict[str, list[dict[str, Any]]], plan: dict[str, Any] | None = None) -> str:
    """Return `main.tex` for a portable package; pairs with `references.bib`."""
    project = tables["research_projects"][0]
    plan = plan if isinstance(plan, dict) else {}
    nodes = list(tables.get("research_graph_nodes") or [])
    artifacts = list(tables.get("research_artifacts") or [])
    sample = " ".join([str(project.get("title", "")), str(project.get("question", "")), json.dumps(plan, ensure_ascii=False)[:4000]]
                      + [str(node.get("title", "")) for node in nodes[:50]])
    cjk = bool(_CJK.search(sample))
    lang = "zh" if cjk else "en"
    words = _STRINGS[lang]
    names = _ENV_NAMES[lang]
    convert = _Converter(cjk)
    line = convert.line
    inline_line = convert.line

    def _optarg(text: Any) -> str:
        """Title inside a \\begin{env}[...] optional argument: a literal ]
        would close the argument early and break compilation."""
        return inline_line(text).replace("]", "{]}")

    out: list[str] = (["% !TEX program = xelatex"] if cjk else []) + [
        "% A-leggo research paper draft. Generated from recorded research; edit freely.",
        "% Compile: " + ("xelatex main.tex && bibtex main && xelatex main.tex && xelatex main.tex  (Chinese text needs XeLaTeX or LuaLaTeX)"
                         if cjk else "pdflatex main.tex && bibtex main && pdflatex main.tex && pdflatex main.tex  (XeLaTeX/LuaLaTeX also work)"),
        "\\documentclass[11pt" + (",UTF8" if cjk else "") + "]{" + ("ctexart" if cjk else "article") + "}",
    ]
    if not cjk:
        out += ["\\usepackage{iftex}", "\\ifPDFTeX", "  \\usepackage[utf8]{inputenc}", "  \\usepackage[T1]{fontenc}",
                "  \\usepackage{lmodern}", "\\else", "  \\usepackage{fontspec}", "\\fi"]
    out += [
        "\\usepackage{amsmath,amssymb,amsthm}", "\\usepackage[margin=1in]{geometry}",
        "\\usepackage[hidelinks]{hyperref}", "",
        "% Common shorthands; \\providecommand keeps any definition you add yourself.",
        "\\providecommand{\\R}{\\mathbb{R}}", "\\providecommand{\\N}{\\mathbb{N}}", "\\providecommand{\\Z}{\\mathbb{Z}}",
        "\\providecommand{\\Q}{\\mathbb{Q}}", "\\providecommand{\\C}{\\mathbb{C}}", "\\providecommand{\\F}{\\mathbb{F}}",
        "\\providecommand{\\abs}[1]{\\left|#1\\right|}", "\\providecommand{\\norm}[1]{\\left\\|#1\\right\\|}",
        "\\providecommand{\\floor}[1]{\\left\\lfloor #1\\right\\rfloor}", "\\providecommand{\\ceil}[1]{\\left\\lceil #1\\right\\rceil}",
        "\\providecommand{\\lcm}{\\operatorname{lcm}}", "\\providecommand{\\rank}{\\operatorname{rank}}",
        "\\providecommand{\\tr}{\\operatorname{tr}}", "\\providecommand{\\checkmark}{\\surd}", "",
        "\\theoremstyle{plain}",
        f"\\newtheorem{{theorem}}{{{names['theorem']}}}[section]",
        f"\\newtheorem{{lemma}}[theorem]{{{names['lemma']}}}",
        f"\\newtheorem{{proposition}}[theorem]{{{names['proposition']}}}",
        f"\\newtheorem{{conjecture}}[theorem]{{{names['conjecture']}}}",
        "\\theoremstyle{definition}",
        f"\\newtheorem{{definition}}[theorem]{{{names['definition']}}}",
        f"\\newtheorem{{counterexample}}[theorem]{{{names['counterexample']}}}",
        f"\\newtheorem{{observation}}[theorem]{{{names['observation']}}}",
        "\\theoremstyle{remark}",
        f"\\newtheorem*{{remark}}{{{names['remark']}}}", "", _FALLBACK_MARK, "",
        "\\title{" + line(project.get("title") or "Research draft") + "}",
        "\\author{}", "\\date{\\today}", "", "\\begin{document}", "\\maketitle", "",
        "\\begin{abstract}",
        convert.convert(_clip(str(plan.get("objective") or project.get("question") or ""), 4000), heading="bold").strip(),
        "\\end{abstract}", "",
        "\\noindent\\fbox{\\parbox{\\dimexpr\\linewidth-2\\fboxsep-2\\fboxrule\\relax}{\\small " + line(words["draft"]) + "}}", "",
        "\\section{" + words["intro"] + "}", "",
        "\\subsection*{" + words["question"] + "}", convert.convert(_clip(str(project.get("question") or ""), 20000)),
    ]
    if plan.get("objective") and str(plan.get("objective")) != str(project.get("question")):
        out += ["\\subsection*{" + words["objective"] + "}", convert.convert(_clip(str(plan["objective"]), 8000))]
    if plan.get("problemFormalization"):
        out += ["\\subsection*{" + words["formalization"] + "}", convert.convert(_clip(str(plan["problemFormalization"]), 12000))]
    assumptions = [str(item) for item in plan.get("assumptions") or [] if str(item).strip()]
    if assumptions:
        out += ["\\subsection*{" + words["assumptions"] + "}", "\\begin{itemize}"]
        out += ["\\item " + inline_line(item) for item in assumptions[:40]] + ["\\end{itemize}", ""]
    definition_nodes = [node for node in nodes if node.get("node_type") == "definition"]
    plan_definitions = [str(item) for item in plan.get("definitions") or [] if str(item).strip()]
    if definition_nodes or plan_definitions:
        out.append("\\subsection*{" + words["definitions"] + "}")
        for node in definition_nodes[:40]:
            out += ["\\begin{definition}[{" + _optarg(_clip(node.get("title"), 300)) + "}]",
                    convert.convert(_clip(node.get("content") or node.get("title"), 6000), heading="bold").strip(), "\\end{definition}", ""]
        for item in plan_definitions[:40]:
            out += ["\\begin{definition}", convert.convert(_clip(item, 4000), heading="bold").strip(), "\\end{definition}", ""]

    def claim(node: dict[str, Any], env: str) -> list[str]:
        status = str(node.get("status") or "unverified")
        label = words["statuses"].get(status, status)
        metadata = _decode(node.get("metadata_json"), {})
        body = convert.convert(_clip(node.get("content") or "", 12000), heading="bold").strip()
        lines = ["\\begin{" + env + "}[{" + _optarg(_clip(node.get("title"), 400)) + "}]", body or inline_line(node.get("title")),
                 "\\end{" + env + "}", "\\noindent{\\small\\textit{" + words["status"] + ":} " + line(label)]
        if isinstance(metadata, dict) and metadata.get("bindingHash"):
            lines[-1] += " \\texttt{" + line(str(metadata["bindingHash"])[:16]) + "}"
        lines[-1] += "}"
        return lines + [""]

    out += ["\\section{" + words["results"] + "}", ""]
    claims = [node for node in nodes if node.get("node_type") in {"theorem", "lemma", "proposition", "conjecture"}]
    verified = [node for node in claims if node.get("status") == "verified"]
    pending = [node for node in claims if node.get("status") != "verified"]
    if verified:
        out.append("\\subsection{" + words["verified"] + "}")
        for node in verified[:60]:
            out += claim(node, node["node_type"] if node["node_type"] != "conjecture" else "theorem")
    if pending:
        out.append("\\subsection{" + words["claims"] + "}")
        for node in pending[:120]:
            # An unverified "theorem" is still only a claim; keep it visibly conjectural.
            env = node["node_type"] if node["node_type"] in {"lemma", "proposition", "conjecture"} else "conjecture"
            out += claim(node, env)
    if not claims:
        out += [line(words["noclaims"]), ""]
    counterexamples = [node for node in nodes if node.get("node_type") == "counterexample"]
    if counterexamples:
        out.append("\\subsection{" + words["counterexamples"] + "}")
        for node in counterexamples[:60]:
            out += claim(node, "counterexample")
    final = next((item for item in reversed(artifacts) if _decode(item.get("metadata_json"), {}).get("artifactKind") == "final-verification"), None)
    if final:
        synthesis = str(final.get("content") or "")
        synthesis = synthesis.split("## Synthesis", 1)[1] if "## Synthesis" in synthesis else synthesis
        out += ["\\subsection{" + words["synthesis"] + "}", convert.convert(_clip(synthesis, 30000))]

    out += ["\\section{" + words["evidence"] + "}", ""]
    for node in [node for node in nodes if node.get("node_type") in {"evidence", "experiment"}][:60]:
        out += claim(node, "observation")
    runs = [run for run in tables.get("research_tool_runs") or [] if not str(run.get("tool_name", "")).startswith("llm:")]
    out.append("\\subsection{" + words["experiments"] + "}")
    if not runs:
        out += [line(words["noexperiments"]), ""]
    for number, run in enumerate(runs[:40], start=1):
        input_data = _decode(run.get("input_json"), {})
        code = ""
        if isinstance(input_data, dict):
            code = str(input_data.get("code") or input_data.get("pythonCode") or input_data.get("script") or "")
            shown = code or json.dumps(input_data, ensure_ascii=False, indent=1)
        else:
            shown = json.dumps(input_data, ensure_ascii=False)
        output = str(run.get("output_text") or "")
        parsed = _decode(output, None)
        if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
            output = parsed["output"]
        shown_lines = _clip(shown, 4000).splitlines()[:60]
        output_lines = _clip(output, 3000).splitlines()[:40]
        out += [
            "\\paragraph{" + words["tool"] + f" {number}: \\texttt{{" + line(run.get("tool_name")) + "} (" + line(run.get("status")) + ")}",
            "\\textit{" + (words["code"] if code else words["input"]) + ":}", "{\\small", "\\begin{verbatim}",
            convert.raw("\n".join(shown_lines)), "\\end{verbatim}", "}",
            "\\textit{" + words["output"] + ":}", "{\\small", "\\begin{verbatim}", convert.raw("\n".join(output_lines)),
            "\\end{verbatim}", "}", "",
        ]

    failed = list(tables.get("research_failed_paths") or [])
    if failed:
        out += ["\\section{" + words["failed"] + "}", "\\begin{itemize}"]
        for path in failed[:40]:
            item = "\\item \\textbf{" + inline_line(_clip(path.get("route_text"), 600)) + "} " + inline_line(_clip(path.get("reason"), 2000))
            if path.get("retry_condition"):
                item += " \\emph{" + line(words["retry"]) + ":} " + inline_line(_clip(path["retry_condition"], 600))
            out.append(item)
        out += ["\\end{itemize}", ""]
    questions = [str(item) for item in plan.get("keyQuestions") or [] if str(item).strip()]
    if questions:
        out += ["\\section{" + words["open"] + "}", "\\begin{enumerate}"]
        out += ["\\item " + inline_line(_clip(item, 1500)) for item in questions[:30]] + ["\\end{enumerate}", ""]
    if tables.get("research_sources"):
        out += ["\\nocite{*}", "\\bibliographystyle{plain}", "\\bibliography{references}", ""]

    notes = [item for item in artifacts if _decode(item.get("metadata_json"), {}).get("artifactKind") in {"step-output", "discussion-reply", "intervention-result"}]
    if notes:
        out += ["\\appendix", "\\section{" + words["appendix"] + "}", ""]
        for item in notes[:80]:
            out += ["\\subsection{" + line(_clip(item.get("title"), 300)) + "}",
                    "{\\small\\textit{" + line(f"{item.get('agent_role')} · {item.get('status')}") + "}}", "",
                    convert.convert(_clip(item.get("content"), 20000))]
    out += ["\\end{document}", ""]
    fallbacks = [name for name in sorted(convert.math_commands) if len(name) <= 40 and not name.startswith("end")]
    preamble = ["% Fallbacks for control words in recorded math. \\providecommand only takes effect when no",
                "% loaded package defines the word; replace any fallback with a proper definition."]
    preamble += ["\\providecommand{\\" + name + "}{\\ensuremath{\\mathrm{" + name + "}}}" for name in fallbacks]
    document = "\n".join(out).replace(_FALLBACK_MARK, "\n".join(preamble) if fallbacks else "")
    return re.sub(r"\n{3,}", "\n\n", document)


def build_latex_bibliography(sources: list[dict[str, Any]], question: str = "") -> str:
    """BibTeX whose field text is LaTeX-safe (titles often contain _, %, & or math)."""
    convert = _Converter(bool(_CJK.search(question + " ".join(str(item.get("title", "")) for item in sources))))
    entries = []

    def bib_safe(value: Any) -> str:
        # BibTeX balances every brace character, escaped or not.
        rendered = convert.line(value)
        if rendered.count("{") == rendered.count("}") and _balanced(rendered):
            return rendered
        return convert.text(" ".join(str(value or "").split())).replace("\\{", "\\textbraceleft{}").replace("\\}", "\\textbraceright{}")

    for source in sources:
        authors = " ".join(str(source.get("authors") or "").split())
        if ";" in authors or authors.count(",") > 1 or (authors.count(",") == 1 and all(" " in part.strip() for part in authors.split(","))):
            authors = " and ".join(part.strip() for part in re.split(r"[;,]", authors) if part.strip())
        fields = [("title", "{" + bib_safe(source.get("title")) + "}"), ("author", bib_safe(authors)),
                  ("year", re.sub(r"[^0-9]", "", str(source.get("year") or ""))[:4]),
                  ("note", ("doi: " + convert.text(str(source["doi"]))) if source.get("doi") else ""),
                  ("howpublished", ("\\texttt{" + convert.text(str(source["url"])) + "}") if source.get("url") else "")]
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
        entries.append("@misc{" + _bib_key(source["id"]) + ",\n" + body + "\n}")
    return "\n\n".join(entries) + "\n"
