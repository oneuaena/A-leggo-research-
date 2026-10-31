"""Evaluation harness: scoring, real app code paths with a fake model, reports."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from aleggo.study import research_eval as ev


class FakeRouter:
    """Asks the worker for one real calculation, then answers from its output."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, model_id, messages, **_kwargs):
        self.calls += 1
        last = messages[-1]["content"]
        if last.startswith("Tool result"):
            # Tool results are wrapped in <<<TOOL_RESULT_BEGIN/END>>> markers
            # (prompt-injection hardening); the JSON payload sits between them.
            if "<<<TOOL_RESULT_BEGIN>>>" in last:
                inner = last.split("<<<TOOL_RESULT_BEGIN>>>", 1)[1].split("<<<TOOL_RESULT_END>>>", 1)[0]
            else:  # legacy bare format, kept as a fallback
                inner = last.split("\n", 1)[1].split("\n\n")[0]
            output = json.loads(inner)["output"]
            return {"text": f"计算得到 {output}。\n\n答案：{output}", "usage": {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15}}
        return {"text": json.dumps({"tool": "run_python", "input": {"code": "import math\nresult = sum(100 // 5**k for k in range(1, 5))"}}),
                "usage": {"promptTokens": 20, "completionTokens": 8, "totalTokens": 28}}


async def referee(messages):
    text = messages[-1]["content"]
    if "Real results" in text:
        return {"text": json.dumps({"steps": [{"id": "S4", "verdict": "error", "issue": "n=1 时两组无公共元素"}],
                                    "summary": "归纳步在 n=1 时失效。"}), "usage": {"totalTokens": 7}}
    return {"text": json.dumps({"steps": [
        {"id": "S1", "verdict": "valid"}, {"id": "S2", "verdict": "valid", "dependsOn": ["S1"]},
        {"id": "S3", "verdict": "valid", "dependsOn": ["S2"]},
        {"id": "S4", "verdict": "gap", "dependsOn": ["S3"], "check": {"tool": "run_python", "input": {"code": "result = len({1} & {2})"}}}],
        "summary": "S4 需要检查。"}), "usage": {"totalTokens": 11}}


class ScoringTests(unittest.TestCase):
    def test_problem_set_is_well_formed(self):
        problems = ev.load_problems()
        self.assertGreaterEqual(len(problems), 20)
        self.assertEqual(len({item["id"] for item in problems}), len(problems))
        known = {"answer_regex", "answer_not_regex", "tool_used", "flagged_any", "overall_in", "no_errors", "kernel_certified"}
        for item in problems:
            self.assertIn(item["kind"], {"discussion", "proof-review", "lean"})
            self.assertTrue(item["checks"])
            for check in item["checks"]:
                self.assertIn(check["type"], known)
                if "pattern" in check:
                    __import__("re").compile(check["pattern"])
        self.assertEqual({item["category"] for item in ev.load_problems(["lean"])}, {"lean"})
        self.assertEqual(len(ev.load_problems(limit=3)), 3)

    def test_answer_extraction_and_checks(self):
        reply = "推导……\n\n**答案：** $\\frac{n^2(n+1)^2}{4}$"
        self.assertEqual(ev.extract_answer(reply), "$\\frac{n^2(n+1)^2}{4}$")
        self.assertEqual(ev.extract_answer("no final line"), "")
        problem = next(item for item in ev.load_problems() if item["id"] == "honesty-riemann")
        good = ev.score(problem, {"answer": "没有，黎曼猜想至今仍未被证明。"})
        bad = ev.score(problem, {"answer": "是的，已经被证明了。"})
        self.assertTrue(all(item["passed"] for item in good))
        self.assertFalse(all(item["passed"] for item in bad))
        review = {"checks": [{"type": "flagged_any", "steps": ["S4"]}, {"type": "overall_in", "values": ["errors-found"]}]}
        result = ev.score(review, {"steps": [{"id": "S4", "verdict": "error"}], "overall": "errors-found"})
        self.assertTrue(all(item["passed"] for item in result))

    def test_summary_counts_categories_tokens_and_skips(self):
        summary = ev.summarise([
            {"category": "compute", "passed": True, "skipped": False, "usage": {"totalTokens": 10}, "latencyMs": 100, "toolCalls": 1},
            {"category": "compute", "passed": False, "skipped": False, "usage": {"totalTokens": 5}, "latencyMs": 300, "toolCalls": 0},
            {"category": "lean", "passed": False, "skipped": True, "usage": {}, "latencyMs": 0},
        ])
        self.assertEqual((summary["passed"], summary["scored"], summary["skipped"]), (1, 2, 1))
        self.assertEqual(summary["score"], 0.5)
        self.assertEqual(summary["usage"]["totalTokens"], 15)
        self.assertEqual(summary["byCategory"]["compute"]["score"], 0.5)
        self.assertIsNone(summary["byCategory"]["lean"]["score"])
        self.assertEqual(summary["meanLatencyMs"], 200)


class RunnerTests(unittest.TestCase):
    def test_discussion_review_and_lean_run_through_the_app_paths(self):
        problems = ev.load_problems(ids=["compute-factorial-zeros", "review-horses", "lean-add-zero"])

        async def lean_completion(messages):
            return {"text": json.dumps({"proof": "intro n\nrfl"}), "usage": {"totalTokens": 3}}

        async def completion(messages):
            if "Lean statement" in messages[1]["content"]:
                return await lean_completion(messages)
            return await referee(messages)

        async def checker(project_id, root, statement, proof, timeout_seconds=0):
            return {"status": "KERNEL_CERTIFIED", "passed": True, "output": "", "elapsedMs": 5}

        router = FakeRouter()
        with tempfile.TemporaryDirectory() as temp:
            runner = ev.EvalRunner("test.model", router=router, completion=completion, checker=checker,
                                   lean_available=True, workdir=Path(temp))
            from unittest.mock import patch
            with patch("aleggo.study.lean_autoprove.detect_lean_workspace", return_value=None):
                results = asyncio.run(runner.run(problems))
        by_id = {item["id"]: item for item in results}
        compute = by_id["compute-factorial-zeros"]
        self.assertTrue(compute["passed"], compute)
        self.assertEqual(compute["answer"], "24")
        self.assertEqual(compute["tools"], ["run_python"])
        self.assertEqual(compute["usage"]["totalTokens"], 43)
        review = by_id["review-horses"]
        self.assertTrue(review["passed"], review)
        self.assertEqual(review["overall"], "errors-found")
        self.assertEqual(review["toolCalls"], 1)
        lean = by_id["lean-add-zero"]
        self.assertTrue(lean["passed"], lean)
        self.assertEqual(lean["attemptStatuses"], ["KERNEL_CERTIFIED"])

    def test_lean_problems_are_skipped_without_lean(self):
        runner = ev.EvalRunner("test.model", lean_available=False)
        results = asyncio.run(runner.run(ev.load_problems(["lean"], limit=1)))
        self.assertTrue(results[0]["skipped"])
        self.assertEqual(ev.summarise(results)["scored"], 0)

    def test_background_report_is_stored_and_listed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            async def scenario():
                runner = ev.EvalRunner("test.model", router=FakeRouter(), workdir=root / "work")
                report = ev.start_eval(root, "test.model", ids=["compute-factorial-zeros"], runner=runner)
                with self.assertRaises(ValueError):
                    ev.start_eval(root, "test.model", ids=["compute-factorial-zeros"], runner=runner)
                await ev._TASKS[report["id"]]
                return report["id"]

            report_id = asyncio.run(scenario())
            stored = ev.get_report(root, report_id)
            self.assertEqual(stored["state"], "COMPLETED")
            self.assertEqual(stored["summary"]["passed"], 1)
            listed = ev.list_reports(root)
            self.assertEqual(listed[0]["id"], report_id)
            self.assertIsNone(ev.get_report(root, "../etc/passwd"))
            # A run that was RUNNING when the app stopped is reported as interrupted.
            path = root / "research-evals" / (report_id + ".json")
            data = json.loads(path.read_text(encoding="utf-8"))
            data["state"] = "RUNNING"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(ev.get_report(root, report_id)["state"], "INTERRUPTED")

    def test_cli_lists_problems(self):
        from contextlib import redirect_stdout
        import io
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(ev.main(["--list"]), 0)
        self.assertIn("lean-add-zero", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
