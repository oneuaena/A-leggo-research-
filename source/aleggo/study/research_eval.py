"""Reproducible evaluation of the research side.

The problem set (``evals/research_eval_problems.json``) covers what an
ordinary university mathematician relies on: honest computation, sequence
identification through OEIS, counterexample search, honesty about open
problems, step-by-step proof review with planted errors, and Lean proofs that
the kernel must certify.  Every problem runs through the *same* code paths the
app uses (discussion with real tool calls, :class:`ProofReviewer`,
:mod:`lean_autoprove`) inside a throw-away store, so a score measures the
product rather than a bare model.

Reports are JSON files under ``<data root>/research-evals/`` and record, per
problem, every check, the extracted answer, latency, tokens and tool calls.

CLI::

    python -m aleggo.study.research_eval --model research.meridian --categories compute,lean --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

PROBLEMS_PATH = Path(__file__).with_name("evals") / "research_eval_problems.json"
ANSWER_INSTRUCTION = "\n\n（评测要求：正常作答，最后单独一行用 `答案：...` 给出最终答案。）"
TIMEOUTS = {"discussion": 900.0, "proof-review": 900.0, "lean": 1500.0}
LEAN_ATTEMPTS = 3
MAX_REPORTS = 100
Completion = Callable[[list[dict[str, str]]], Awaitable[dict[str, Any]]]

_TASKS: dict[str, asyncio.Task] = {}
_START_LOCK = __import__('threading').Lock()
_CANCEL_REQUESTED: set[str] = set()
_ANSWER = re.compile(r"^[\s>*_#-]*(?:\*\*)?\s*(?:最终)?答案\s*(?:\*\*)?\s*[:：]\s*(?:\*\*)?\s*(.+?)\s*$", re.M)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# Problems and scoring
# --------------------------------------------------------------------------- #
def load_problems(categories: list[str] | None = None, ids: list[str] | None = None, limit: int | None = None,
                  path: Path | None = None) -> list[dict[str, Any]]:
    data = json.loads((path or PROBLEMS_PATH).read_text(encoding="utf-8"))
    problems = [item for item in data["problems"] if isinstance(item, dict) and item.get("id")]
    if categories:
        wanted = {item.strip() for item in categories if item.strip()}
        problems = [item for item in problems if item.get("category") in wanted]
    if ids:
        wanted_ids = {item.strip() for item in ids if item.strip()}
        problems = [item for item in problems if item["id"] in wanted_ids]
    if limit:
        problems = problems[:max(1, int(limit))]
    return problems


def problem_catalogue() -> dict[str, Any]:
    data = json.loads(PROBLEMS_PATH.read_text(encoding="utf-8"))
    problems = data["problems"]
    categories: dict[str, int] = {}
    for item in problems:
        categories[item["category"]] = categories.get(item["category"], 0) + 1
    return {"version": data.get("version"), "description": data.get("description", ""), "categories": categories,
            "problems": [{"id": item["id"], "category": item["category"], "kind": item["kind"],
                          "prompt": item.get("prompt") or item.get("statement") or ""} for item in problems]}


def extract_answer(reply: str) -> str:
    """The last ``答案：...`` line; empty when the reply has none.

    A missing answer line is a format failure (scored as failed), not a cue
    to score the reply tail -- otherwise a model could pass ``answer_regex``
    by stuffing keywords at the end without ever giving an answer.
    """
    matches = _ANSWER.findall(str(reply or ""))
    if matches:
        return matches[-1].strip().strip("*").strip()
    return ""


def score(problem: dict[str, Any], outcome: dict[str, Any]) -> list[dict[str, Any]]:
    answer = outcome.get("answer") or ""
    text = answer  # no fallback to the reply tail: see extract_answer
    steps = outcome.get("steps") or []
    flagged = {step.get("id") for step in steps if step.get("verdict") in {"gap", "error"}}
    results = []
    for check in problem.get("checks", []):
        kind = check.get("type")
        passed, detail = False, ""
        if kind == "answer_regex":
            if answer:
                passed = bool(re.search(check["pattern"], text, re.I))
                detail = "命中答案行"
            else:
                detail = "缺少「答案：」行（格式不合规）"
        elif kind == "answer_not_regex":
            passed = not re.search(check["pattern"], text, re.I)
        elif kind == "tool_used":
            used = set(outcome.get("tools") or [])
            passed = bool(used & set(check.get("tools", [])))
            detail = ", ".join(sorted(used)) or "no tools"
        elif kind == "flagged_any":
            passed = bool(flagged & set(check.get("steps", [])))
            detail = "已标记：" + (", ".join(sorted(item for item in flagged if item)) or "none")
        elif kind == "overall_in":
            passed = outcome.get("overall") in set(check.get("values", []))
            detail = str(outcome.get("overall") or "")
        elif kind == "no_errors":
            passed = bool(steps) and not any(step.get("verdict") == "error" for step in steps) and outcome.get("overall") != "errors-found"
            detail = str(outcome.get("overall") or "")
        elif kind == "kernel_certified":
            passed = bool(outcome.get("kernelCertified"))
            detail = " → ".join(outcome.get("attemptStatuses") or [])
        else:
            detail = "未知检查类型"
        results.append({"type": kind, "passed": passed, "detail": detail[:300]})
    return results


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [item for item in results if not item.get("skipped")]
    by_category: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = by_category.setdefault(item["category"], {"total": 0, "passed": 0, "skipped": 0})
        bucket["total"] += 1
        bucket["passed"] += int(bool(item.get("passed")))
        bucket["skipped"] += int(bool(item.get("skipped")))
    totals = {key: sum(int((item.get("usage") or {}).get(key) or 0) for item in results)
              for key in ("promptTokens", "completionTokens", "totalTokens", "modelCalls")}
    latencies = [int(item.get("latencyMs") or 0) for item in scored]
    passed = sum(1 for item in scored if item.get("passed"))
    return {"total": len(results), "scored": len(scored), "passed": passed,
            "skipped": len(results) - len(scored), "failed": len(scored) - passed,
            "score": round(passed / len(scored), 4) if scored else None,
            "byCategory": {key: {**value, "score": round(value["passed"] / (value["total"] - value["skipped"]), 4)
                                 if value["total"] - value["skipped"] else None} for key, value in by_category.items()},
            "usage": totals, "toolCalls": sum(int(item.get("toolCalls") or 0) for item in results),
            "meanLatencyMs": int(sum(latencies) / len(latencies)) if latencies else 0}


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
class EvalRunner:
    """Runs problems through the app's own discussion / review / Lean paths.

    ``router`` (an object with ``async complete(model_id, messages, **kw)``) and
    ``completion`` (for review and Lean) replace the model in tests; ``checker``
    replaces the Lean kernel.  By default the configured model and kernel run.
    """

    def __init__(self, model_id: str, *, router: Any = None, completion: Completion | None = None,
                 checker: Any = None, lean_available: bool | None = None, workdir: Path | None = None) -> None:
        self.model_id = model_id
        self.router = router
        self.completion = completion
        self.checker = checker
        self.lean_available = lean_available
        self.workdir = workdir

    def _lean_ready(self) -> tuple[bool, str]:
        if self.lean_available is not None:
            return self.lean_available, "" if self.lean_available else "未安装 Lean。"
        try:
            from aleggo.study.lean_formal import detect_lean_workspace

            detect_lean_workspace()
            return True, ""
        except Exception as exc:
            return False, f"Lean 不可用：{str(exc)[:300]}"

    async def run(self, problems: list[dict[str, Any]], *, concurrency: int = 1,
                  progress: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
        from aleggo.study.research_store import ResearchStore
        from aleggo.study.store import AleggoStore

        own_dir = self.workdir is None
        root = Path(tempfile.mkdtemp(prefix="aleggo-eval-")) if own_dir else Path(self.workdir)
        try:
            store = ResearchStore(AleggoStore(root / "data"))
            gate = asyncio.Semaphore(max(1, min(4, int(concurrency))))
            results: list[dict[str, Any] | None] = [None] * len(problems)

            async def one(index: int, problem: dict[str, Any]) -> None:
                async with gate:
                    result = await self.run_problem(store, problem)
                results[index] = result
                if progress:
                    progress(result)

            await asyncio.gather(*(one(index, problem) for index, problem in enumerate(problems)))
            return [item for item in results if item is not None]
        finally:
            if own_dir:
                shutil.rmtree(root, ignore_errors=True)

    async def run_problem(self, store: Any, problem: dict[str, Any]) -> dict[str, Any]:
        kind = problem.get("kind")
        base = {"id": problem["id"], "category": problem.get("category", ""), "kind": kind, "passed": False,
                "skipped": False, "checks": [], "answer": "", "error": "", "latencyMs": 0, "toolCalls": 0,
                "tools": [], "usage": {}}
        started = time.monotonic()
        try:
            if kind == "discussion":
                runner = self._discussion
            elif kind == "proof-review":
                runner = self._review
            elif kind == "lean":
                ready, reason = self._lean_ready()
                if not ready:
                    return {**base, "skipped": True, "error": reason}
                runner = self._lean
            else:
                return {**base, "skipped": True, "error": "未知的问题类型。"}
            outcome = await asyncio.wait_for(runner(store, problem), timeout=TIMEOUTS.get(str(kind), 900.0))
        except (TimeoutError, asyncio.TimeoutError):
            outcome = {"error": "超时。"}
        except Exception as exc:
            outcome = {"error": f"{type(exc).__name__}: {str(exc)[:800]}"}
        checks = score(problem, outcome) if not outcome.get("error") or outcome.get("partial") else []
        return {**base, "checks": checks, "passed": bool(checks) and all(item["passed"] for item in checks) and not outcome.get("error"),
                "answer": str(outcome.get("answer") or "")[:500], "reply": str(outcome.get("reply") or "")[:4000],
                "error": str(outcome.get("error") or ""), "latencyMs": int((time.monotonic() - started) * 1000),
                "toolCalls": int(outcome.get("toolCalls") or 0), "tools": outcome.get("tools") or [],
                "usage": outcome.get("usage") or {}, "overall": outcome.get("overall", ""),
                "attemptStatuses": outcome.get("attemptStatuses", [])}

    async def _discussion(self, store: Any, problem: dict[str, Any]) -> dict[str, Any]:
        from aleggo.study import research_collaborator as collaborator
        from aleggo.study.research_domain import ResearchDomainStore

        project = store.create_project(problem["prompt"], self.model_id)
        message = problem["prompt"] + ANSWER_INSTRUCTION
        queue_id = collaborator.create_discussion(store, project["id"], message, "main")
        token = collaborator.MODEL_ROUTER_OVERRIDE.set(self.router) if self.router is not None else None
        try:
            await collaborator._run_discussion(project["id"], store, queue_id, message, "main")
        finally:
            if token is not None:
                collaborator.MODEL_ROUTER_OVERRIDE.reset(token)
        reply = next((item for item in store.list_artifacts(project["id"])
                      if (item.get("metadata") or {}).get("requestId") == queue_id), None)
        runs = [item for item in ResearchDomainStore(store).list_tool_runs(project["id"], 200)
                if item["stepId"] == "discussion:" + queue_id and not item["tool"].startswith("llm:")]
        content = str((reply or {}).get("content") or "")
        failed = not reply or reply.get("status") == "FAILED"
        return {"reply": content, "answer": extract_answer(content), "tools": sorted({item["tool"] for item in runs}),
                "toolCalls": len(runs), "usage": (reply or {}).get("metadata", {}).get("usage", {}),
                "error": (content[:500] or "无回复。") if failed else ""}

    async def _review(self, store: Any, problem: dict[str, Any]) -> dict[str, Any]:
        from aleggo.study.proof_review import ProofReviewer

        project = store.create_project("Proof review: " + str(problem.get("statement", ""))[:200], self.model_id)
        reviewer = ProofReviewer(store)
        created = reviewer.create(project["id"], problem["proof"], statement=problem.get("statement", ""), title=problem["id"])
        view = await reviewer.run(project["id"], created["id"], completion=self.completion)
        effective = [{**step, "verdict": step.get("researcherVerdict") or step.get("verdict")} for step in view["steps"]]
        return {"steps": effective, "overall": view["overall"], "usage": view.get("usage", {}),
                "toolCalls": len(view.get("checks") or []), "tools": sorted({item.get("tool", "") for item in view.get("checks") or []} - {""}),
                "reply": view.get("summary", ""), "error": view.get("error", "") if view["state"] != "COMPLETED" else ""}

    async def _lean(self, store: Any, problem: dict[str, Any]) -> dict[str, Any]:
        from aleggo.study import lean_autoprove
        from aleggo.study.research_domain import ResearchDomainStore

        project = store.create_project("Lean: " + str(problem.get("statement", ""))[:200], self.model_id)
        domain = ResearchDomainStore(store)
        statement = problem["lean"]
        claim = domain.create_graph_node(project["id"], "theorem", problem.get("statement") or statement, problem.get("statement") or statement)
        binding = domain.create_formal_binding(project["id"], claim["id"], problem.get("statement") or statement,
                                               problem["id"], statement, confirm_mapping=True)
        kwargs: dict[str, Any] = {"max_attempts": LEAN_ATTEMPTS, "completion": self.completion}
        if self.checker is not None:
            kwargs["checker"] = self.checker
        job = await lean_autoprove.run(project["id"], binding["id"], store, **kwargs)
        statuses = [item.get("status", "") for item in job.get("attempts") or []]
        return {"kernelCertified": bool(job.get("passed")), "attemptStatuses": statuses, "reply": job.get("proof", ""),
                "usage": job.get("usage") or {}, "toolCalls": sum(1 for item in statuses if item not in {"REJECTED_BY_FILTER", ""}),
                "tools": ["lean_kernel_check"] if statuses else [], "error": job.get("error", ""), "partial": True}


# --------------------------------------------------------------------------- #
# Stored reports and background runs
# --------------------------------------------------------------------------- #
def reports_dir(data_root: Path) -> Path:
    path = Path(data_root) / "research-evals"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, report: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def new_report(model_id: str, problems: list[dict[str, Any]], *, categories: list[str] | None = None) -> dict[str, Any]:
    return {"id": "eval_" + uuid4().hex[:16], "model": model_id, "state": "RUNNING", "startedAt": _now(), "finishedAt": "",
            "categories": categories or [], "problemIds": [item["id"] for item in problems], "results": [],
            "summary": summarise([]), "error": ""}


def _order_results(report: dict[str, Any]) -> None:
    """Restore problem order in-place. Results for unknown ids sort last."""
    order = {pid: index for index, pid in enumerate(report.get("problemIds") or [])}
    report["results"].sort(key=lambda item: order.get(item["id"], len(order)))


async def run_report(report: dict[str, Any], problems: list[dict[str, Any]], runner: EvalRunner, *,
                     path: Path | None = None, concurrency: int = 1,
                     on_result: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    def progress(result: dict[str, Any]) -> None:
        report["results"].append(result)
        if on_result:
            on_result(result)
        report["summary"] = summarise(report["results"])
        if path:
            _write(path, report)

    try:
        await runner.run(problems, concurrency=concurrency, progress=progress)
        report["state"] = "CANCELLED" if report.get("id") in _CANCEL_REQUESTED else "COMPLETED"
    except asyncio.CancelledError:
        report["state"] = "CANCELLED"
        report["error"] = report.get("error") or "评测已被取消。"
        raise
    except Exception as exc:
        report.update(state="FAILED", error=f"{type(exc).__name__}: {str(exc)[:1000]}")
    finally:
        _order_results(report)
        report["summary"] = summarise(report["results"])
        report["finishedAt"] = _now()
        if path:
            _write(path, report)
    return report


def start_eval(data_root: Path, model_id: str, *, categories: list[str] | None = None, ids: list[str] | None = None,
               limit: int | None = None, concurrency: int = 1, runner: EvalRunner | None = None) -> dict[str, Any]:
    problems = load_problems(categories, ids, limit)
    if not problems:
        raise ValueError("没有与所选条件匹配的评测题目。")
    with _START_LOCK:
        if any(task for task in _TASKS.values() if not task.done()):
            raise ValueError("已有评测在运行，请等待完成后再开始。")
        report = new_report(model_id, problems, categories=categories)
        folder = reports_dir(data_root)
        path = folder / (report["id"] + ".json")
        _write(path, report)
        for old in sorted(folder.glob("eval_*.json"), key=lambda item: item.stat().st_mtime)[:-MAX_REPORTS]:
            old.unlink(missing_ok=True)
        task = asyncio.create_task(run_report(report, problems, runner or EvalRunner(model_id), path=path, concurrency=concurrency))
        _TASKS[report["id"]] = task
        task.add_done_callback(lambda _done, key=report["id"]: (_TASKS.pop(key, None), _CANCEL_REQUESTED.discard(key)))
        return report


def _load(path: Path) -> dict[str, Any] | None:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            report = {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if report.get("state") == "RUNNING" and report.get("id") not in _TASKS:
        report["state"] = "INTERRUPTED"
        report["error"] = report.get("error") or "应用在评测过程中重启，评测已中断。"
    return report


def list_reports(data_root: Path) -> list[dict[str, Any]]:
    items = []
    for path in sorted(reports_dir(data_root).glob("eval_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        report = _load(path)
        if report:
            items.append({key: report.get(key) for key in ("id", "model", "state", "startedAt", "finishedAt", "categories", "summary", "error")}
                         | {"problemCount": len(report.get("problemIds") or [])})
    return items


def get_report(data_root: Path, report_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"eval_[0-9a-f]{16}", report_id or ""):
        return None
    path = reports_dir(data_root) / (report_id + ".json")
    return _load(path) if path.exists() else None


def cancel_eval(report_id: str, data_root: Path | None = None) -> bool:
    """Cancel a live evaluation and persist CANCELLED promptly (avoids RUNNING stuck)."""
    if not re.fullmatch(r"eval_[0-9a-f]{16}", report_id or ""):
        return False
    _CANCEL_REQUESTED.add(report_id)
    task = _TASKS.get(report_id)
    cancelled = False
    if task and not task.done():
        task.cancel()
        cancelled = True
    # Persist state even if the task already finished racing the cancel call.
    if data_root is not None:
        path = reports_dir(data_root) / (report_id + ".json")
        report = _load(path) if path.exists() else None
        if report and report.get("state") == "RUNNING":
            report["state"] = "CANCELLED"
            report["error"] = report.get("error") or "评测已被取消。"
            report["finishedAt"] = report.get("finishedAt") or _now()
            _write(path, report)
            cancelled = True
    return cancelled


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _split(value: str | None) -> list[str] | None:
    return [item.strip() for item in value.split(",") if item.strip()] if value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the A-leggo research side on the built-in problem set.")
    parser.add_argument("--model", default="research.meridian", help="model id configured in A-leggo")
    parser.add_argument("--categories", help="comma-separated: compute,sequence,counterexample,honesty,proof-review,lean")
    parser.add_argument("--ids", help="comma-separated problem ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--out", help="write the JSON report here")
    parser.add_argument("--list", action="store_true", help="list problems and exit")
    args = parser.parse_args(argv)
    if args.list:
        for item in problem_catalogue()["problems"]:
            print(f"{item['id']:<28} {item['category']:<15} {item['prompt'][:60]}")
        return 0
    problems = load_problems(_split(args.categories), _split(args.ids), args.limit)
    if not problems:
        print("No problems match the selection.", file=sys.stderr)
        return 2
    report = new_report(args.model, problems, categories=_split(args.categories))

    def show(result: dict[str, Any]) -> None:
        mark = "SKIP" if result["skipped"] else "PASS" if result["passed"] else "FAIL"
        print(f"[{mark}] {result['id']:<28} {result['latencyMs'] / 1000:7.1f}s  tokens={result['usage'].get('totalTokens', 0)}"
              f"  tools={result['toolCalls']}  {result['error'][:80]}", flush=True)

    async def go() -> dict[str, Any]:
        return await run_report(report, problems, EvalRunner(args.model), path=Path(args.out) if args.out else None,
                                concurrency=args.concurrency, on_result=show)

    final = asyncio.run(go())
    summary = final["summary"]
    print(f"\nScore {summary['passed']}/{summary['scored']} (skipped {summary['skipped']}), "
          f"tokens {summary['usage']['totalTokens']}, mean latency {summary['meanLatencyMs'] / 1000:.1f}s")
    for name, bucket in summary["byCategory"].items():
        print(f"  {name:<15} {bucket['passed']}/{bucket['total'] - bucket['skipped']}")
    if args.out:
        print("Report:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
