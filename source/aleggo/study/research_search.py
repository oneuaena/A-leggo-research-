"""Bounded, reproducible finite discovery and counterexample searches."""

from __future__ import annotations

import ast
from itertools import combinations
import math
import random
import re
from typing import Any


MAX_EVALUATIONS = 10_000
MAX_CONSTRAINT_OPERATIONS = 25_000_000
MAX_COUNTEREXAMPLE_CASES = 100_000
MAX_EXACT_MAGNITUDE = 10**100
_VARIABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


def finite_discovery_search(problem: dict[str, Any], *, seed: int, evaluation_budget: int) -> dict[str, Any]:
    """Search a finite subset system with exact constraints and a fixed seed."""
    if not isinstance(problem, dict):
        raise ValueError("A finite discovery problem is required.")
    universe_size = _integer(problem.get("universeSize"), "universeSize", 2, 4096)
    candidate_size = _integer(problem.get("candidateSize"), "candidateSize", 1, min(512, universe_size))
    seed = _integer(seed, "seed", 0, 2_147_483_646)
    budget = _integer(evaluation_budget, "evaluationBudget", 1, MAX_EVALUATIONS)

    raw_pairs = problem.get("incompatibilities", [])
    raw_groups = problem.get("coverageGroups", [])
    if not isinstance(raw_pairs, list) or len(raw_pairs) > 20_000:
        raise ValueError("At most 20,000 incompatibility pairs are supported.")
    if not isinstance(raw_groups, list) or len(raw_groups) > 1_024:
        raise ValueError("At most 1,024 coverage groups are supported.")
    pairs: set[tuple[int, int]] = set()
    for pair in raw_pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Each incompatibility must be a pair of universe indices.")
        left = _integer(pair[0], "incompatibility index", 0, universe_size - 1)
        right = _integer(pair[1], "incompatibility index", 0, universe_size - 1)
        if left == right:
            raise ValueError("An incompatibility pair must name two different indices.")
        pairs.add(tuple(sorted((left, right))))
    groups: list[frozenset[int]] = []
    for group in raw_groups:
        if not isinstance(group, list) or not group or len(group) > 512:
            raise ValueError("Each coverage group must contain 1 to 512 indices.")
        groups.append(frozenset(_integer(index, "coverage index", 0, universe_size - 1) for index in group))

    effective_budget = min(budget, max(1, MAX_CONSTRAINT_OPERATIONS // max(1, len(pairs) + len(groups))))
    possible = math.comb(universe_size, candidate_size)
    exhaustive = possible <= effective_budget
    if exhaustive:
        candidates = combinations(range(universe_size), candidate_size)
        limit = possible
    else:
        rng = random.Random(seed)
        candidates = (tuple(sorted(rng.sample(range(universe_size), candidate_size))) for _ in range(effective_budget))
        limit = effective_budget

    ranked: list[dict[str, Any]] = []
    evaluations = 0
    for candidate in candidates:
        selected = frozenset(candidate)
        violations = sum(1 for left, right in pairs if left in selected and right in selected)
        covered = sum(1 for group in groups if selected & group)
        spread = sum(value * (2 * index - candidate_size + 1) for index, value in enumerate(candidate))
        ranked.append({
            "candidate": list(candidate), "violations": violations, "coverage": covered,
            "coverageTotal": len(groups), "spread": spread,
        })
        evaluations += 1
        if evaluations >= limit:
            break
    ranked.sort(key=lambda item: (item["violations"], -item["coverage"], -item["spread"], item["candidate"]))
    ranked = ranked[:20]
    best = ranked[0] if ranked else None
    solved = bool(best and best["violations"] == 0 and best["coverage"] == len(groups))
    status = "SOLUTION_FOUND" if solved else "EXHAUSTIVE_NO_SOLUTION" if exhaustive else "BUDGET_REACHED"
    return {
        "status": status, "seed": seed, "evaluationBudget": effective_budget,
        "requestedEvaluationBudget": budget,
        "evaluations": evaluations, "searchSpaceSize": str(possible),
        "exhaustive": exhaustive, "bestCandidates": ranked,
        "scope": "Exact finite-set evaluator; this search does not prove an unrestricted mathematical statement.",
    }


def integer_counterexample_search(
    left_expression: str, right_expression: str, *, variable: str, start: int, end: int,
) -> dict[str, Any]:
    """Find an exact integer witness to inequality of two safe expressions."""
    if not _VARIABLE.fullmatch(variable):
        raise ValueError("Use a simple variable name containing letters, digits, and underscores.")
    low = _integer(start, "start", -10**12, 10**12)
    high = _integer(end, "end", -10**12, 10**12)
    if high < low:
        raise ValueError("The search end must be at least the start.")
    count = high - low + 1
    if count > MAX_COUNTEREXAMPLE_CASES:
        raise ValueError(f"A counterexample search is limited to {MAX_COUNTEREXAMPLE_CASES:,} integer cases.")
    left = _parse_integer_expression(left_expression, variable)
    right = _parse_integer_expression(right_expression, variable)
    for value in range(low, high + 1):
        lhs = _eval_integer_expression(left, variable, value)
        rhs = _eval_integer_expression(right, variable, value)
        if lhs != rhs:
            return {
                "status": "COUNTEREXAMPLE_FOUND", "variable": variable, "value": value,
                "leftValue": str(lhs), "rightValue": str(rhs), "casesChecked": value - low + 1,
                "searchRange": [low, high], "verification": "Exact Python integer arithmetic; witness directly reevaluated.",
                "scope": "A witness refutes the equality on the declared integer domain; no witness in this interval is not a proof.",
            }
    return {
        "status": "NO_COUNTEREXAMPLE_IN_RANGE", "variable": variable, "casesChecked": count,
        "searchRange": [low, high], "verification": "Every integer in the inclusive range was evaluated exactly.",
        "scope": "A bounded search result is not a proof outside the declared integer interval.",
    }


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    return value


def _parse_integer_expression(source: str, variable: str) -> ast.Expression:
    if not isinstance(source, str) or not source.strip() or len(source) > 2000:
        raise ValueError("Each integer expression must contain 1 to 2,000 characters.")
    try:
        expression = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Enter a polynomial-style integer expression using +, -, *, and non-negative integer powers.") from exc
    for node in ast.walk(expression):
        if isinstance(node, ast.Expression | ast.BinOp | ast.UnaryOp | ast.Load | ast.Add | ast.Sub | ast.Mult | ast.Pow | ast.USub | ast.UAdd):
            continue
        if isinstance(node, ast.Constant) and type(node.value) is int:
            continue
        if isinstance(node, ast.Name) and node.id == variable:
            continue
        raise ValueError("Only integer constants, the selected variable, unary signs, +, -, *, and powers are allowed.")
    if sum(1 for _ in ast.walk(expression)) > 128:
        raise ValueError("Integer expressions are limited to 128 syntax nodes.")
    return expression


def _eval_integer_expression(expression: ast.Expression, variable: str, value: int) -> int:
    def evaluate(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id == variable:
            return value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            result = -evaluate(node.operand)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            result = evaluate(node.operand)
        elif isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Pow):
                if right < 0 or right > 12:
                    raise ValueError("Integer powers must have exponents from 0 to 12.")
                result = left**right
            else:  # The AST whitelist makes this unreachable.
                raise ValueError("Unsupported integer operation.")
        else:
            raise ValueError("Unsupported integer expression.")
        if abs(result) > MAX_EXACT_MAGNITUDE:
            raise ValueError("An intermediate integer exceeds the exact-search magnitude limit.")
        return result

    return evaluate(expression)
