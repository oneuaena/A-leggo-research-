"""Benchmark dataset loader and selector for A-leggo Research self-improvement."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BENCHMARK_FILE = Path(__file__).with_name("benchmark_problems.json")


def load_benchmark_problems(path: Path | None = None) -> list[dict[str, Any]]:
    configured_path = os.environ.get("ALEGGO_BENCHMARK_PROBLEMS_FILE", "").strip()
    target = path or (Path(configured_path) if configured_path else BENCHMARK_FILE)
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("benchmark_problems.json must contain a JSON array of problem objects.")
    split = os.environ.get("ALEGGO_ACTIVE_BENCHMARK_SPLIT", "").strip().lower()
    if split and split != "all":
        data = [item for item in data if str(item.get("split") or "").strip().lower() == split]
    return data


def get_benchmark_problem(problem_id: str, path: Path | None = None) -> dict[str, Any]:
    for item in load_benchmark_problems(path):
        if item.get("id") == problem_id:
            return item
    raise KeyError(f"Benchmark problem '{problem_id}' not found.")


def select_benchmark_subset(
    problem_ids: list[str] | None = None,
    *,
    category: str | None = None,
    limit: int = 5,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    all_problems = load_benchmark_problems(path)
    by_id = {str(p["id"]): p for p in all_problems}
    if problem_ids:
        selected = [by_id[pid] for pid in problem_ids if pid in by_id]
        if selected:
            return selected
    if category:
        cat_filtered = [p for p in all_problems if p.get("category") == category]
        if cat_filtered:
            return cat_filtered[:limit]
    return all_problems[:limit]
