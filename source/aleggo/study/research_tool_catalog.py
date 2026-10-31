"""Single source of truth for research tool names, aliases and input schemas.

Plans are written by language models and by researchers, so ``requiredTools``
arrives as free text ("Python (SymPy)", "numerical computation", "Matplotlib",
"SMT solver" …).  Before this catalog every unrecognised spelling blocked the
step with "no registered runtime bridge".  :func:`canonical_requirement` maps
such text to one of a small set of *capabilities* the runtime actually bridges,
and :data:`WORKER_TOOL_SCHEMAS` documents the exact worker input contract for
the tool planner so proposed calls validate on the first attempt.
"""

from __future__ import annotations

import re
from typing import Iterable

# Worker operations that are exact symbolic computations (SymPy).
SYMBOLIC_TOOLS = frozenset({
    "symbolic_simplify", "solve_equation", "differentiate", "integrate", "matrix_compute",
    "symbolic_limit", "symbolic_series", "symbolic_summation", "symbolic_factor",
    "symbolic_expand", "symbolic_dsolve", "symbolic_solve_system", "factor_integer",
})
# Numeric (not symbolic-proof) worker operations.
NUMERIC_TOOLS = frozenset({"evaluate_numeric"})
WORKER_TOOLS = frozenset({"run_python", "capability_check", "z3_check"}) | SYMBOLIC_TOOLS | NUMERIC_TOOLS
# External computer algebra systems (aleggo.study.external_cas) and read-only
# mathematical databases (aleggo.study.math_databases).
CAS_TOOLS = frozenset({"run_pari", "run_gap", "run_sage", "run_macaulay2"})
DATABASE_TOOLS = frozenset({"oeis_search", "zbmath_search", "arxiv_search", "lmfdb_search"})
ALL_TOOLS = WORKER_TOOLS | CAS_TOOLS | DATABASE_TOOLS
CAS_CAPABILITY = {"run_pari": "pari", "run_gap": "gap", "run_sage": "sage", "run_macaulay2": "macaulay2"}

WORKER_TOOL_SCHEMAS: dict[str, str] = {
    "run_python": '{"code": "<Python source; assign the answer to `result`>", "seed": <optional int>} — '
                  "imports allowed: math, cmath, fractions, decimal, statistics, itertools, functools, operator, "
                  "collections, heapq, bisect, random, re, string, copy, dataclasses, typing, time, csv, json, "
                  "sympy, mpmath, numpy, scipy, networkx, z3, matplotlib (Agg; save figures with plt.savefig('name.png')). "
                  "Files may only be written inside the current workspace; no network or shell.",
    "symbolic_simplify": '{"expression": "sin(x)**2+cos(x)**2", "symbols": ["x"]}',
    "solve_equation": '{"expression": "x**2 = 4", "variable": "x"} (an expression without "=" is solved for 0)',
    "symbolic_solve_system": '{"equations": ["x+y=3", "x-y=1"], "symbols": ["x", "y"]}',
    "differentiate": '{"expression": "x**3", "variable": "x", "order": 1}',
    "integrate": '{"expression": "exp(-x**2)", "variable": "x", "lower": "-oo", "upper": "oo"} (omit bounds for an antiderivative)',
    "symbolic_limit": '{"expression": "sin(x)/x", "variable": "x", "point": "0", "direction": "+"}',
    "symbolic_series": '{"expression": "exp(x)", "variable": "x", "point": "0", "order": 6}',
    "symbolic_summation": '{"expression": "1/n**2", "variable": "n", "lower": "1", "upper": "oo"}',
    "symbolic_factor": '{"expression": "x**4-1"}',
    "symbolic_expand": '{"expression": "(x+1)**5"}',
    "symbolic_dsolve": '{"expression": "Derivative(f(x),x,2)+f(x)", "function": "f", "variable": "x"}',
    "factor_integer": '{"n": 4294967297}',
    "evaluate_numeric": '{"expression": "zeta(3)", "precision": 50} (numeric evidence only, not a proof)',
    "matrix_compute": '{"matrix": [[2,1],[1,2]], "operation": "det|rank|eigenvals|eigenvects|inverse|rref|nullspace|columnspace|transpose|trace|charpoly"}',
    "z3_check": '{"smt2": "(declare-const x Int) (assert (> x 2)) (check-sat)", "timeoutMs": 10000}',
    "capability_check": "{}",
}

# Capabilities a plan step may require.  "python"/"sympy"/"numpy"/"scipy"/"z3"
# execute in the mathematical worker; "pari"/"gap"/"sage"/"macaulay2" run in an
# installed external CAS; "database" queries OEIS/zbMATH/arXiv/LMFDB; "lean"
# needs a Lean workspace and a researcher-confirmed binding; "literature" uses
# the academic index bridge.
BRIDGED_CAPABILITIES = ("python", "sympy", "numpy", "scipy", "z3", "pari", "gap", "sage", "macaulay2", "database",
                        "lean", "literature")
WORKER_CAPABILITIES = frozenset({"python", "sympy", "numpy", "scipy", "z3"})
CAS_CAPABILITIES = frozenset({"pari", "gap", "sage", "macaulay2"})

_LITERATURE = {"literature", "literature search", "paper_search", "paper search", "academic search", "arxiv",
               "crossref", "openalex", "semantic scholar", "doi metadata", "zbmath", "mathscinet", "google scholar"}
_LEAN = ("lean", "mathlib", "formal verification", "lean kernel", "proof assistant")
# Specialised systems with no bridge.  Their algorithms are not silently
# substituted; the step blocks with a precise message instead.
_UNBRIDGED = ("magma", "singular", "coq", "isabelle", "agda", "maple", "mathematica", "wolfram", "maxima", "matlab",
              "octave", "julia", "cocalc", "flint", "normaliz", "polymake", "oscar", "kenzo")
# Bridged external systems (available when installed; otherwise the step blocks
# with installation guidance instead of silently substituting another system).
_CAS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sage", ("sagemath", "sage")),
    ("pari", ("pari/gp", "gp/pari", "pari")),
    ("macaulay2", ("macaulay2", "macaulay 2", "m2")),
    ("gap", ("gap",)),
)
_DATABASE = ("oeis", "lmfdb", "integer sequence database", "sequence database", "math database", "mathematical database",
             "数据库", "整数序列")

# Requirements that describe *how* a human/agent reasons rather than a runtime
# tool.  They never block a step and never trigger a worker call.
_NON_TOOLS = ("pen and paper", "paper and pencil", "by hand", "manual", "reasoning", "analysis by hand",
              "latex", "none", "n/a", "no tools", "proof writing", "literature review by hand", "手工", "推理", "无")


def is_non_tool(value: object) -> bool:
    text = _clean(value)
    return bool(text) and any(text == term or text.startswith(term + " ") for term in _NON_TOOLS)


def executable_requirements(values: Iterable[object]) -> list[str]:
    """Original requirement strings that ask for an actual runtime tool."""
    return [str(value).strip() for value in (values or []) if str(value).strip() and not is_non_tool(value)]


# Ordered: first matching rule wins.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("z3", ("z3", "smt", "sat solver", "satisfiability")),
    ("sympy", ("sympy", "symbolic", "computer algebra", "cas", "符号")),
    ("scipy", ("scipy",)),
    ("numpy", ("numpy",)),
    ("python", ("python", "mpmath", "matplotlib", "networkx", "plot", "numerical", "numeric", "computation",
                "compute", "calculation", "simulation", "monte carlo", "brute force", "enumeration", "exhaustive",
                "search", "experiment", "counterexample", "数值", "计算", "实验", "反例", "枚举", "绘图")),
)


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("_", " ").replace("-", " ").casefold().split())


def canonical_requirement(value: object) -> str:
    """Map one free-text tool requirement to a bridged capability, or ``""``.

    ``""`` means *unrecognised or not bridged*; callers keep the original text
    to explain the blocker.
    """
    raw = str(value or "").strip().casefold()
    text = _clean(value)
    if not text:
        return ""
    if raw in _LITERATURE or text in _LITERATURE or any(term in text for term in ("literature", "paper search", "arxiv", "文献")):
        return "literature"
    if any(term in text for term in _LEAN):
        return "lean"
    words = set(re.findall(r"[a-z0-9/+]+", text))
    if any(term in text for term in _DATABASE):
        return "database"
    worker_named = any(n in words for n in ("python", "sympy", "numpy", "scipy", "z3"))
    for capability, needles in _CAS_RULES:
        if any(needle in words or text.startswith(needle) or ("/" in needle and needle in text) or (" " in needle and needle in text)
               for needle in needles):
            if worker_named:
                break  # "Python or Sage": the always-available worker runs it
            return capability
    if any(name in words or text.startswith(name) for name in _UNBRIDGED):
        # "Python or Sage" / "SymPy (instead of Mathematica)" still runs: a
        # bridged capability named in the same requirement takes precedence.
        for capability, needles in _RULES:
            if any(re.search(r"(?<![a-z])" + re.escape(needle) + r"(?![a-z])", text) for needle in needles if needle.isascii()):
                if capability in {"python", "sympy", "numpy", "scipy", "z3"} and any(n in words for n in ("python", "sympy", "numpy", "scipy", "z3")):
                    return capability
        return ""
    for capability, needles in _RULES:
        for needle in needles:
            if needle.isascii():
                if re.search(r"(?<![a-z])" + re.escape(needle) + r"(?![a-z])", text):
                    return capability
            elif needle in text:
                return capability
    return ""


def canonical_requirements(values: Iterable[object]) -> list[str]:
    seen: list[str] = []
    for value in values or []:
        capability = canonical_requirement(value)
        if capability and capability not in seen:
            seen.append(capability)
    return seen


def worker_tool_allowed(requirements: Iterable[object], name: str) -> bool:
    """Whether an approved step's requirements permit one worker operation."""
    raw = executable_requirements(requirements)
    capabilities = set(canonical_requirements(raw))
    if name == "capability_check":
        return bool(raw)
    if name == "run_python":
        return bool(capabilities & {"python", "sympy", "numpy", "scipy"})
    if name == "z3_check":
        return "z3" in capabilities
    if name in SYMBOLIC_TOOLS or name in NUMERIC_TOOLS:
        # SymPy runs inside every Python step, so exact symbolic operations are
        # permitted wherever a Python/SymPy calculation was approved.
        return bool(capabilities & {"sympy", "python"})
    if name in CAS_CAPABILITY:
        return CAS_CAPABILITY[name] in capabilities
    if name in DATABASE_TOOLS:
        return bool(capabilities & {"database", "literature"})
    return False


def _available_cas_schemas() -> dict[str, str]:
    try:
        from aleggo.study.external_cas import tool_schemas

        return tool_schemas(available_only=True)
    except Exception:  # discovery must never break planning
        return {}


def database_tool_schemas() -> dict[str, str]:
    from aleggo.study.math_databases import DATABASE_TOOL_SCHEMAS

    return dict(DATABASE_TOOL_SCHEMAS)


def planner_tool_reference(*, include_external: bool = True, include_databases: bool = True) -> str:
    """Compact, exact schema reference for the tool-planner prompt.

    External CAS tools appear only when the system is installed, so models are
    never invited to call a program that cannot run.
    """
    schemas = dict(WORKER_TOOL_SCHEMAS)
    if include_external:
        schemas.update(_available_cas_schemas())
        if include_databases:
            schemas.update(database_tool_schemas())
    return "\n".join(f"- {name}: {schema}" for name, schema in schemas.items())


def installed_cas_capabilities() -> list[str]:
    return [CAS_CAPABILITY[name] for name in sorted(_available_cas_schemas())]


def plan_tool_vocabulary() -> str:
    """Vocabulary for plan authors (requiredTools); English for models + Chinese for teachers."""
    installed = installed_cas_capabilities()
    labels = {"pari": '"pari" (PARI/GP number theory)', "gap": '"gap" (GAP group theory)',
              "sage": '"sage" (SageMath)', "macaulay2": '"macaulay2" (commutative algebra)'}
    cas = (" Installed computer algebra systems: " + ", ".join(labels[item] for item in installed) + ".") if installed else ""
    missing = [name for key, name in (("sage", "Sage"), ("gap", "GAP"), ("pari", "PARI"), ("macaulay2", "Macaulay2")) if key not in installed]
    english = (
        'Use only these requiredTools values: "python" (numerical experiments, enumeration, counterexample search, '
        'plots; NumPy/SciPy/mpmath/NetworkX/Matplotlib available), "sympy" (exact symbolic algebra, calculus, '
        'series, sums, ODEs, matrices, integer factorisation), "z3" (SMT satisfiability), "database" (OEIS sequence '
        'identification, zbMATH/arXiv lookups, LMFDB objects), "literature" (academic search), "lean" (only with a '
        'researcher-confirmed formal binding).' + cas + ' Leave requiredTools empty for purely argumentative steps. '
        'Do not name ' + ", ".join(missing + ["Mathematica", "Maple", "Magma"]) + ': they are not installed.'
    )
    chinese = (
        "【中文说明·供教师阅读】requiredTools 仅可填：python（数值/枚举/反例/作图）、sympy（符号计算）、"
        "z3（SMT）、database（OEIS/zbMATH/arXiv/LMFDB）、literature（文献检索）、lean（须已确认形式化绑定）。"
        "纯论证步骤可留空。未安装：" + "、".join(missing + ["Mathematica", "Maple", "Magma"]) + "。"
    )
    return english + "\n" + chinese


def teacher_tool_help_zh() -> str:
    """Short Chinese glossary for UI help (tool tokens stay English)."""
    installed = installed_cas_capabilities()
    cas = ("已安装 CAS：" + ", ".join(sorted(installed)) + "。") if installed else "当前未检测到外部 CAS。"
    return (
        "计划步骤的「所需工具」请使用英文标识：python / sympy / z3 / database / literature / lean"
        "（以及已安装时的 pari、gap、sage、macaulay2）。" + cas
    )


_WORKER_NAME_ALIASES = {
    "python": "run_python", "python3": "run_python", "run_code": "run_python", "execute_python": "run_python",
    "numpy": "run_python", "scipy": "run_python", "code": "run_python",
    "simplify": "symbolic_simplify", "sympy": "symbolic_simplify", "solve": "solve_equation",
    "solve_system": "symbolic_solve_system", "diff": "differentiate", "derivative": "differentiate",
    "integral": "integrate", "limit": "symbolic_limit", "series": "symbolic_series",
    "sum": "symbolic_summation", "summation": "symbolic_summation", "factor": "symbolic_factor",
    "expand": "symbolic_expand", "dsolve": "symbolic_dsolve", "factorint": "factor_integer",
    "evaluate": "evaluate_numeric", "numeric": "evaluate_numeric", "matrix": "matrix_compute",
    "z3": "z3_check", "smt": "z3_check",
    "pari": "run_pari", "gp": "run_pari", "pari_gp": "run_pari", "pari/gp": "run_pari", "gap": "run_gap",
    "sage": "run_sage", "sagemath": "run_sage", "macaulay2": "run_macaulay2", "m2": "run_macaulay2",
    "oeis": "oeis_search", "zbmath": "zbmath_search", "arxiv": "arxiv_search", "lmfdb": "lmfdb_search",
}


def canonical_worker_invocation(invocation: object) -> tuple[str, object]:
    """Normalise a model-proposed ``{"name", "input"}`` pair without guessing values."""
    if not isinstance(invocation, dict):
        return "", invocation
    name = str(invocation.get("name") or invocation.get("tool") or "").strip()
    key = name.casefold().replace("-", "_").replace(" ", "_")
    name = name if name in ALL_TOOLS else _WORKER_NAME_ALIASES.get(key, name)
    input_data = invocation.get("input", invocation.get("arguments"))
    if input_data is None and (name == "run_python" or name in CAS_TOOLS) and isinstance(invocation.get("code"), str):
        input_data = {"code": invocation["code"], **({"seed": invocation["seed"]} if "seed" in invocation else {})}
    return name, input_data
