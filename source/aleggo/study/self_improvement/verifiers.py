"""Independent mathematical answer verifiers (Python / SymPy / Constructive code).

Hard Principle 4: Experiment results and task correctness must NEVER be self-proclaimed by an Agent.
All answers are verified programmatically via exact/numeric comparison, SymPy symbolic equivalence,
or constructive Python verification scripts executed in the isolated math worker.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import sympy as sp

from aleggo.study.math_tools import run_math_tool
from aleggo.study.self_improvement.models import VerificationResult


def extract_candidate_answer(text: str) -> str:
    """Extract the concise mathematical answer from model output."""
    if not text:
        return ""
    stripped = text.strip()
    # 1. Try explicit FINAL_ANSWER tag
    tag_match = re.search(r"FINAL_ANSWER\s*[:：=]\s*(.+)", stripped)
    if tag_match:
        ans = tag_match.group(1).strip().splitlines()[0].strip()
        ans = re.sub(r"^\$+|\$+$", "", ans).strip()
        if ans:
            return ans

    # 2. Try JSON object with final_answer / answer key
    for match in re.finditer(r"\{[^{}]*\}", stripped):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                for k in ("final_answer", "answer", "result"):
                    if k in obj and obj[k] is not None:
                        return str(obj[k]).strip()
        except Exception:
            pass

    # 3. Try LaTeX \boxed{...} (handle nested braces)
    boxed_matches: list[str] = []
    for idx in [m.end() for m in re.finditer(r"\\boxed\s*\{", stripped)]:
        depth = 1
        pos = idx
        while pos < len(stripped) and depth > 0:
            if stripped[pos] == "{":
                depth += 1
            elif stripped[pos] == "}":
                depth -= 1
            pos += 1
        if depth == 0:
            boxed_matches.append(stripped[idx : pos - 1].strip())
    if boxed_matches:
        return boxed_matches[-1]

    # 4. Fallback: last non-empty line or last number/expression
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip() and not ln.strip().startswith(">")]
    if lines:
        last = lines[-1]
        num_matches = re.findall(r"[-+]?\d+(?:/\d+|\.\d+)?", last)
        if num_matches:
            return num_matches[-1]
        return last[:200]
    return stripped[:200]


def _latex_to_sympy_str(expr_str: str) -> str:
    """Convert common LaTeX math notations into SymPy-parsable string."""
    s = expr_str.strip()
    s = re.sub(r"^\$+|\$+$", "", s).strip()
    s = s.replace(r"\left", "").replace(r"\right", "").replace(r"\,", "").replace(" ", "")
    s = s.replace(r"\pi", "pi").replace("π", "pi").replace(r"\infty", "oo")
    s = s.replace(r"\ln", "log").replace(r"\log", "log").replace(r"\exp", "exp")
    s = s.replace(r"\cdot", "*").replace(r"\times", "*")
    # \frac{a}{b} -> ((a)/(b))
    for _ in range(6):
        new_s = re.sub(r"\\(?:d|t)?frac\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", s)
        new_s = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", new_s)
        if new_s == s:
            break
        s = new_s
    s = s.replace("^", "**").replace("{", "(").replace("}", ")")
    s = re.sub(r"\\", "", s)
    return s


def verify_numeric(candidate_raw: str, expected_raw: str, tolerance: float = 1e-6) -> VerificationResult:
    """Verify numerical equivalence within tolerance (supports exact fractions/pi expressions too)."""
    cand_extracted = extract_candidate_answer(candidate_raw)
    if not cand_extracted:
        return VerificationResult(
            passed=False,
            verifier_type="numeric",
            verifier_output="未能从模型输出中提取到答案。",
            expected=expected_raw,
            actual="",
        )
    try:
        cand_clean = _latex_to_sympy_str(cand_extracted)
        exp_clean = _latex_to_sympy_str(expected_raw)
        # If there's an assignment like 'det = 4' or 'x = 12', take RHS
        if "=" in cand_clean and not cand_clean.startswith("="):
            cand_clean = cand_clean.split("=")[-1].strip()
        cand_sym = sp.sympify(cand_clean)
        exp_sym = sp.sympify(exp_clean)
        if getattr(exp_sym, "is_Integer", False) and getattr(cand_sym, "is_Integer", False):
            exact_diff = abs(int(cand_sym) - int(exp_sym))
            passed = exact_diff == 0
            return VerificationResult(
                passed=passed,
                verifier_type="numeric",
                verifier_output=(
                    f"精确整数校验{'通过' if passed else '未通过'}："
                    f"期望值={int(exp_sym)}，提取值={int(cand_sym)}，差值={exact_diff}"
                ),
                expected=expected_raw,
                actual=cand_extracted,
                details={"diff": float(exact_diff), "tolerance": 0.0},
            )
        cand_val = complex(sp.N(cand_sym, 30))
        exp_val = complex(sp.N(exp_sym, 30))
        diff = abs(cand_val - exp_val)
        rel_tol = tolerance * max(1.0, abs(exp_val))
        passed = bool(diff <= rel_tol)
        return VerificationResult(
            passed=passed,
            verifier_type="numeric",
            verifier_output=(
                f"数值校验{'通过' if passed else '未通过'}："
                f"期望值={expected_raw} ({exp_val.real:.10g})，提取值={cand_extracted} ({cand_val.real:.10g})，误差={diff:.3e}"
            ),
            expected=expected_raw,
            actual=cand_extracted,
            details={"diff": float(diff), "tolerance": float(rel_tol)},
        )
    except Exception as exc:
        return VerificationResult(
            passed=False,
            verifier_type="numeric",
            verifier_output=f"数值解析或比对失败：{exc}（提取值='{cand_extracted}'，期望值='{expected_raw}'）",
            expected=expected_raw,
            actual=cand_extracted,
        )


def verify_sympy_equiv(candidate_raw: str, expected_raw: str, symbols: list[str] | None = None) -> VerificationResult:
    """Verify symbolic equivalence via SymPy simplify(cand - expected) == 0."""
    cand_extracted = extract_candidate_answer(candidate_raw)
    if not cand_extracted:
        return VerificationResult(
            passed=False,
            verifier_type="sympy_equiv",
            verifier_output="未能从模型输出中提取到符号表达式。",
            expected=expected_raw,
            actual="",
        )
    try:
        local_dict: dict[str, Any] = {
            "pi": sp.pi,
            "e": sp.E,
            "E": sp.E,
            "I": sp.I,
            "oo": sp.oo,
            "ln": sp.log,
            "log": sp.log,
            "sqrt": sp.sqrt,
            "exp": sp.exp,
        }
        for sym_name in symbols or ["x", "y", "z", "n", "k", "t", "a", "b"]:
            local_dict[sym_name] = sp.Symbol(sym_name)

        cand_clean = _latex_to_sympy_str(cand_extracted)
        exp_clean = _latex_to_sympy_str(expected_raw)
        if "=" in cand_clean and not cand_clean.startswith("="):
            cand_clean = cand_clean.split("=")[-1].strip()

        cand_expr = sp.sympify(cand_clean, locals=local_dict)
        exp_expr = sp.sympify(exp_clean, locals=local_dict)
        diff = sp.simplify(sp.expand(cand_expr - exp_expr))
        passed = bool(diff == 0)
        if not passed:
            try:
                trig_diff = sp.trigsimp(cand_expr - exp_expr)
                passed = bool(sp.simplify(trig_diff) == 0)
            except Exception:
                pass
        if not passed and not cand_expr.free_symbols and not exp_expr.free_symbols:
            num_diff = abs(complex(sp.N(cand_expr, 30)) - complex(sp.N(exp_expr, 30)))
            passed = bool(num_diff <= 1e-9)

        return VerificationResult(
            passed=passed,
            verifier_type="sympy_equiv",
            verifier_output=(
                f"SymPy 符号等价校验{'通过' if passed else '未通过'}："
                f"期望={exp_expr}，实际={cand_expr}，化简差={diff}"
            ),
            expected=expected_raw,
            actual=cand_extracted,
            details={"simplified_diff": str(diff)},
        )
    except Exception as exc:
        return VerificationResult(
            passed=False,
            verifier_type="sympy_equiv",
            verifier_output=f"SymPy 符号校验异常：{exc}（提取值='{cand_extracted}'，期望值='{expected_raw}'）",
            expected=expected_raw,
            actual=cand_extracted,
        )


async def verify_constructive_python(
    candidate_raw: str,
    verifier_code: str,
    expected_raw: str,
    workspace_root: Path,
) -> VerificationResult:
    """Run problem-specific constructive verifier Python code in isolated math_worker."""
    cand_extracted = extract_candidate_answer(candidate_raw)
    if not cand_extracted:
        return VerificationResult(
            passed=False,
            verifier_type="constructive_python",
            verifier_output="未能从模型输出中提取到构造性答案。",
            expected=expected_raw,
            actual="",
        )
    script = (
        f"CANDIDATE_RAW = {cand_extracted!r}\n"
        f"EXPECTED_RAW = {expected_raw!r}\n"
        f"{verifier_code}\n"
    )
    res = await run_math_tool(
        workspace_root=workspace_root,
        project_id="research_00000000000000000000000000000003",
        name="run_python",
        input_data={"code": script},
        timeout_seconds=20,
        subdirectory="self_improvement_verifier",
    )
    out = str(res.get("output") or "").strip()
    ok = bool(res.get("ok")) and out.startswith("PASS")
    return VerificationResult(
        passed=ok,
        verifier_type="constructive_python",
        verifier_output=f"构造性校验代码执行结果：{out or res.get('error', '')}",
        expected=expected_raw,
        actual=cand_extracted,
        details={"worker_ok": bool(res.get("ok")), "raw_output": out},
    )


async def verify_problem_answer(
    candidate_raw: str,
    verifier_spec: dict[str, Any],
    workspace_root: Path,
) -> VerificationResult:
    """Dispatch verification according to the benchmark problem's verifier specification."""
    vtype = str(verifier_spec.get("type") or "numeric")
    expected = str(verifier_spec.get("expected") or "")
    tolerance = float(verifier_spec.get("tolerance") or 1e-6)
    symbols = verifier_spec.get("symbols")
    verifier_code = str(verifier_spec.get("code") or "")

    if vtype == "constructive_python" and verifier_code:
        return await verify_constructive_python(candidate_raw, verifier_code, expected, workspace_root)
    if vtype == "sympy_equiv":
        return verify_sympy_equiv(candidate_raw, expected, symbols=symbols)
    return verify_numeric(candidate_raw, expected, tolerance=tolerance)
