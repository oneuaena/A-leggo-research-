"""Co-research features for ordinary university mathematicians.

Covers the capable-but-isolated mathematical worker, tool-name normalisation,
the compute → observe → repair loop, direct AI discussion in the research
thread, immediate execution of requests on finished projects, and restart
recovery of those new queue rows.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from aleggo.api.routers import research as research_router
from aleggo.study import research_collaborator as collaborator
from aleggo.study import research_store as store_module
from aleggo.study.math_tools import ALLOWED_MATH_TOOLS, run_math_tool
from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_runtime import (
    _ASSIGNMENT_ALIASES,
    _TOOL_PLANNER_SYSTEM,
    _execute_project,
    _process_interventions,
    _requested_tools,
    _required_tool_blocker,
)
from aleggo.study.research_store import ResearchStore
from aleggo.study.research_thread import build_thread_page
from aleggo.study.research_tool_catalog import (
    ALL_TOOLS,
    SYMBOLIC_TOOLS,
    WORKER_TOOLS,
    canonical_requirement,
    canonical_worker_invocation,
    worker_tool_allowed,
)
from aleggo.study.research_workbench import ResearchWorkbench, evidence_classification
from aleggo.study.store import AleggoStore

PID = "research_0123456789abcdef0123456789abcdef"


def worker(root: Path, name: str, data: dict) -> dict:
    return asyncio.run(run_math_tool(workspace_root=root, project_id=PID, name=name, input_data=data))


class MathWorkerCapabilityTests(unittest.TestCase):
    """Typical research code runs; dangerous effects do not."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name) / "data"
        cls.root.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_code(self, code: str) -> dict:
        return worker(self.root, "run_python", {"code": code})

    def test_ordinary_research_python_runs(self):
        programs = {
            "result = sorted(map(lambda n: n * n, filter(lambda n: n % 2, range(7))))": "[1, 9, 25]",
            "from fractions import Fraction\nresult = sum(Fraction(1, k * (k + 1)) for k in range(1, 10))": "9/10",
            "from functools import lru_cache\n@lru_cache(None)\ndef f(n):\n    return n if n < 2 else f(n-1) + f(n-2)\nresult = f(80)": "23416728348467685",
            "import mpmath\nmpmath.mp.dps = 30\nresult = mpmath.nstr(mpmath.zeta(3), 25)": "1.202056903159594285399738",
            "class V:\n    def __init__(self, a):\n        self.a = a\n    def __add__(self, other):\n        return V(self.a + other.a)\nresult = (V(2) + V(3)).a": "5",
            "from dataclasses import dataclass\n@dataclass\nclass P:\n    a: int\n    b: int = 2\nresult = P(1)": "P(a=1, b=2)",
            "try:\n    1 / 0\nexcept ZeroDivisionError:\n    result = isinstance(divmod(7, 2), tuple)": "True",
            "import statistics, heapq\nresult = (statistics.median([5, 1, 3]), heapq.nsmallest(2, [4, 1, 3]))": "(3, [1, 3])",
            "import z3\nx = z3.Int('x')\ns = z3.Solver(); s.add(x * x == 49, x > 0); s.check()\nresult = s.model()[x]": "7",
        }
        for code, expected in programs.items():
            with self.subTest(code=code.splitlines()[0]):
                response = self.run_code(code)
                self.assertTrue(response["ok"], response)
                self.assertEqual(response["output"], expected)

    def test_matplotlib_figure_is_captured_as_a_generated_file(self):
        response = self.run_code("import matplotlib.pyplot as plt\nplt.plot([1, 2, 3], [1, 4, 9])\nplt.savefig('growth.png')\nresult = 'saved'")
        self.assertTrue(response["ok"], response)
        names = [item["relativeName"] for item in response["generated_files"]]
        self.assertIn("growth.png", names)
        self.assertFalse(any(name.startswith(".") for name in names))

    def test_escape_attempts_are_blocked_by_effect_not_only_by_syntax(self):
        settings = self.root / "settings.json"
        settings.write_text("PRIVATE-SETTINGS")
        other = self.root / "research-projects" / "research_ffffffffffffffffffffffffffffffff"
        other.mkdir(parents=True, exist_ok=True)
        (other / "notes.txt").write_text("OTHER-PROJECT")
        outside = self.root / "outside.txt"
        u = "'_'*2"
        attacks = {
            "shell via sympify": f"import sympy\nu={u}\nresult = sympy.sympify(u+\"import\"+u+\"('os').system('echo hi')\")",
            "subprocess via sympify": f"import sympy\nu={u}\nresult = sympy.sympify(u+\"import\"+u+\"('subprocess').check_output(['id'])\")",
            "write outside via numpy": f"import numpy as np\nnp.savetxt({str(outside)!r}, [1, 2])\nresult = 'wrote'",
            "write outside via sympify": f"import sympy\nu={u}\nresult = sympy.sympify(u+\"import\"+u+\"('pathlib').Path({str(outside)!r}).write_text('x')\")",
            "read app settings": f"import numpy as np\nresult = np.loadtxt({str(settings)!r}, dtype=str)",
            "read another project": f"import numpy as np\nresult = np.loadtxt({str(other / 'notes.txt')!r}, dtype=str)",
            "dunder via getattr": "result = getattr((), '__cl' + 'ass__')",
            "dunder via syntax": "result = ().__class__.__bases__",
            "blocked import": "import os\nresult = os.getcwd()",
        }
        for label, code in attacks.items():
            with self.subTest(label):
                response = self.run_code(code)
                self.assertFalse(response["ok"], (label, response))
                self.assertNotIn("PRIVATE-SETTINGS", json.dumps(response))
                self.assertNotIn("OTHER-PROJECT", json.dumps(response))
        self.assertFalse(outside.exists())

    def test_workspace_files_remain_usable(self):
        response = self.run_code("import numpy as np\nnp.save('vector.npy', np.arange(4))\nresult = np.load('vector.npy').tolist()")
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["output"], "[0, 1, 2, 3]")


class SymbolicToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, name, data):
        return worker(self.root, name, data)

    def test_missing_expression_is_an_error_not_a_silent_zero(self):
        response = self.call("integrate", {"wrong_key": "x**2"})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error_type"], "VALIDATION_ERROR")
        record = {"tool": "integrate", "ok": response["ok"], "workerExitCode": 0, "verificationLevel": response.get("verification_level", ""),
                  "input": {"wrong_key": "x**2"}, "output": response["output"]}
        self.assertNotEqual(evidence_classification(json.dumps(record), "", []), "SYMBOLICALLY_VERIFIED")

    def test_common_spellings_and_operations_are_exact(self):
        cases = [
            ("integrate", {"expr": "x**2"}, "x**3/3"),
            ("integrate", {"expression": "exp(-x**2)", "lower": "-oo", "upper": "oo"}, "sqrt(pi)"),
            ("solve_equation", {"expression": "x**2 = 4"}, "[-2, 2]"),
            ("symbolic_simplify", {"expression": "sin(t)**2 + cos(t)**2", "symbols": "t"}, "1"),
            ("symbolic_limit", {"expression": "sin(x)/x", "point": "0"}, "1"),
            ("symbolic_summation", {"expression": "1/n**2", "variable": "n", "lower": 1, "upper": "oo"}, "pi**2/6"),
            ("symbolic_factor", {"expression": "x**4 - 1"}, "(x - 1)*(x + 1)*(x**2 + 1)"),
            ("symbolic_solve_system", {"equations": ["x + y = 3", "x - y = 1"], "symbols": ["x", "y"]}, "[{x: 2, y: 1}]"),
            ("symbolic_dsolve", {"expression": "Derivative(f(x), x, 2) + f(x)"}, "Eq(f(x), C1*sin(x) + C2*cos(x))"),
            ("factor_integer", {"n": 4294967297}, "{641: 1, 6700417: 1}"),
            ("matrix_compute", {"matrix": [[2, 1], [1, 2]], "operation": "charpoly"}, "lambda**2 - 4*lambda + 3"),
        ]
        for name, data, expected in cases:
            with self.subTest(name=name, data=data):
                response = self.call(name, data)
                self.assertTrue(response["ok"], response)
                self.assertEqual(response["output"], expected)
                self.assertEqual(response["verification_level"], "SYMBOLIC_CHECK")

    def test_numeric_evaluation_is_not_labelled_symbolic(self):
        response = self.call("evaluate_numeric", {"expression": "pi", "precision": 30})
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["output"].startswith("3.14159265358979323846"))
        self.assertEqual(response["verification_level"], "BOUNDED_CHECK")

    def test_injection_through_expression_text_is_rejected(self):
        response = self.call("symbolic_simplify", {"expression": "__import__('os').getcwd()"})
        self.assertFalse(response["ok"])
        self.assertIn("__", response["error"])

    def test_catalog_and_bridge_agree(self):
        self.assertEqual(set(ALLOWED_MATH_TOOLS), set(ALL_TOOLS))
        self.assertTrue(set(WORKER_TOOLS) <= set(ALLOWED_MATH_TOOLS))
        for name in SYMBOLIC_TOOLS | {"run_python", "z3_check", "evaluate_numeric"}:
            self.assertIn(name, _TOOL_PLANNER_SYSTEM)
        self.assertIn("bounded mathematical tool call", _TOOL_PLANNER_SYSTEM)


class ToolRequirementTests(unittest.TestCase):
    def test_free_text_requirements_map_to_bridged_capabilities(self):
        expected = {
            "Python (SymPy)": "sympy", "matplotlib": "python", "numerical computation": "python",
            "numerical_experiment": "python", "SMT solver": "z3", "Lean 4 + Mathlib": "lean",
            "arXiv": "literature", "computer algebra": "sympy", "scipy.optimize": "scipy",
            "数值实验": "python", "SageMath": "sage", "PARI/GP": "pari", "Python or Sage": "python", "casework analysis": "",
            "OEIS lookup": "database", "Macaulay2": "macaulay2", "GAP": "gap", "Magma": "",
        }
        for text, capability in expected.items():
            with self.subTest(text=text):
                self.assertEqual(canonical_requirement(text), capability)

    def test_blocker_accepts_spellings_and_explains_unbridged_systems(self):
        for tools in (["Python (SymPy)"], ["matplotlib", "numerical computation"], ["pen and paper"], ["SMT solver"]):
            with self.subTest(tools=tools):
                self.assertEqual(_required_tool_blocker({"requiredTools": tools}), "")
        with patch("aleggo.study.external_cas.engine_available", return_value=False):
            blocker = _required_tool_blocker({"requiredTools": ["SageMath"]})
        self.assertIn("not installed", blocker)
        self.assertIn("ALEGGO_SAGE_PATH", blocker)
        blocker = _required_tool_blocker({"requiredTools": ["Magma"]})
        self.assertIn("no registered runtime bridge", blocker)
        self.assertIn("python, sympy", blocker)
        self.assertEqual(_required_tool_blocker({"requiredTools": ["OEIS"]}), "")
        self.assertEqual(_requested_tools({"requiredTools": ["Python (SymPy)"]}), ["AVAILABLE_FOR_EXECUTION:Python (SymPy) (via sympy)"])

    def test_project_can_disable_automatic_lean_without_blocking_manual_actions(self):
        blocker = _required_tool_blocker({"requiredTools": ["Lean"]}, auto_lean_enabled=False)
        self.assertIn("关闭研究过程中的自动 Lean 调用", blocker)
        self.assertIn("手动启动 Lean 检查", blocker)

    def test_allow_list_follows_capabilities(self):
        self.assertTrue(worker_tool_allowed(["matplotlib"], "run_python"))
        self.assertTrue(worker_tool_allowed(["python"], "symbolic_limit"))
        self.assertFalse(worker_tool_allowed(["python"], "z3_check"))
        self.assertFalse(worker_tool_allowed(["pen and paper"], "run_python"))
        self.assertFalse(worker_tool_allowed(["sympy"], "run_command"))

    def test_model_spellings_of_worker_tools_are_normalised(self):
        self.assertEqual(canonical_worker_invocation({"name": "python", "input": {"code": "result=1"}}), ("run_python", {"code": "result=1"}))
        self.assertEqual(canonical_worker_invocation({"tool": "limit", "input": {"expression": "1/x"}})[0], "symbolic_limit")
        self.assertEqual(canonical_worker_invocation({"name": "run_python", "code": "result=2"}), ("run_python", {"code": "result=2"}))

    def test_symbolic_analyst_alias_is_not_overridden(self):
        self.assertEqual(_ASSIGNMENT_ALIASES["symbolic analyst"], "lemma-generator")
        self.assertEqual(_ASSIGNMENT_ALIASES["symbolic-analyst"], "lemma-generator")


def plan(tools=None, count=1):
    return {"objective": "Study a scoped statement", "assumptions": ["n is a positive integer"], "steps": [
        {"id": f"s{i}", "title": f"Step {i}", "description": f"Compute obligation {i}.", "status": "pending", "order": i,
         "priority": 3, "enabled": True, "branch": "main", "dependencies": [], "assignedAgent": "explorer",
         "requiredTools": list(tools or []), "milestone": "M1", "expectedOutput": "Evidence", "verificationMethod": "Review"}
        for i in range(count)]}


class RuntimeHarness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ResearchStore(AleggoStore(Path(self.temp.name) / "data"))
        self.workbench = ResearchWorkbench(self.store)
        self.planner_messages, self.planner_replies, self.chat_messages, self.chat_replies = [], [], [], []
        self.worker_calls, self.worker_replies = [], []

        async def role(project, plan_value, step, agent, prior):
            return "Finding: recorded observation for " + step["id"]

        async def complete(model, messages, **kwargs):
            if "bounded mathematical tool call" in messages[0]["content"]:
                self.planner_messages.append(messages)
                return {"text": self.planner_replies.pop(0) if self.planner_replies else json.dumps({"name": "run_python", "input": {"code": "result = 1"}})}
            if messages[0]["content"].startswith("You are A-leggo's research collaborator"):
                self.chat_messages.append([dict(m) for m in messages])
                reply = self.chat_replies.pop(0) if self.chat_replies else "Final reply."
                if isinstance(reply, Exception):
                    raise reply
                return {"text": reply}
            return {"text": json.dumps({"summary": "Continue.", "primary": [], "secondary": [], "deprioritize": [], "uncertainty": "none"})}

        async def tool(**kwargs):
            self.worker_calls.append(kwargs)
            reply = self.worker_replies.pop(0) if self.worker_replies else {"ok": True, "verification_status": "SUCCESS", "verification_level": "BOUNDED_CHECK", "output": "ok", "worker_exit_code": 0}
            return reply

        async def literature(*args, **kwargs):
            return [], {"status": "not-triggered", "items": [], "newRecords": 0}

        self.patches = [patch("aleggo.study.research_runtime._run_role", side_effect=role),
                        patch("aleggo.study.research_runtime._MODEL_ROUTER.complete", side_effect=complete),
                        patch("aleggo.study.research_runtime._targeted_literature", side_effect=literature),
                        patch("aleggo.study.research_runtime.run_math_tool", side_effect=tool),
                        patch("aleggo.study.research_collaborator.run_math_tool", side_effect=tool)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def create(self, tools=None, count=1, status="RUNNING"):
        project = self.store.create_project("Is every even n > 2 a sum of two primes up to 100?", "research.meridian")
        self.store.save_plan(project["id"], plan(tools, count), changed_by="researcher", change_reason="test", change_summary="plan")
        self.store.approve(project["id"])
        self.workbench.configure_supervision(project["id"], "AUTONOMOUS")
        self.store.set_status(project["id"], status)
        return project["id"]

    def queue_rows(self, project_id):
        with self.store.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM research_intervention_queue WHERE project_id=? ORDER BY created_at", (project_id,))]


class RepairLoopTests(RuntimeHarness):
    async def test_failed_model_program_is_repaired_from_the_real_error(self):
        project_id = self.create(tools=["numerical computation"])
        self.planner_replies = [json.dumps({"name": "python", "input": {"code": "result = undefined_name"}}),
                                json.dumps({"name": "run_python", "input": {"code": "result = {'casesChecked': 49}"}})]
        self.worker_replies = [
            {"ok": False, "verification_status": "PROGRAM_FAILURE", "error_type": "PROGRAM_ERROR", "error": "name 'undefined_name' is not defined", "worker_exit_code": 0, "output": ""},
            {"ok": True, "verification_status": "SUCCESS", "verification_level": "BOUNDED_CHECK", "output": json.dumps({"casesChecked": 49}), "worker_exit_code": 0},
        ]
        await _execute_project(project_id, self.store)
        self.assertEqual(self.store.get_project(project_id)["status"], "COMPLETED")
        self.assertEqual([call["name"] for call in self.worker_calls], ["run_python", "run_python"])
        self.assertEqual(len(self.planner_messages), 2)
        self.assertIn("undefined_name", self.planner_messages[1][1]["content"])
        runs = [row for row in ResearchDomainStore(self.store).list_tool_runs(project_id) if row["tool"] == "run_python"]
        self.assertEqual(len(runs), 2)
        artifact = next(item for item in self.store.list_artifacts(project_id) if item["agentRole"] == "math-tool")
        record = json.loads(artifact["content"])
        self.assertTrue(record["ok"])
        self.assertEqual(record["repairAttempts"][0]["errorType"], "PROGRAM_ERROR")
        self.assertEqual(evidence_classification(artifact["content"], "", []), "NUMERICALLY_OBSERVED")

    async def test_identical_repair_is_not_retried(self):
        project_id = self.create(tools=["python"])
        failing = json.dumps({"name": "run_python", "input": {"code": "result = 1/0"}})
        self.planner_replies = [failing, failing, failing]
        self.worker_replies = [{"ok": False, "verification_status": "PROGRAM_FAILURE", "error_type": "PROGRAM_ERROR", "error": "division by zero", "worker_exit_code": 0, "output": ""}] * 3
        project = self.store.get_project(project_id)
        from aleggo.study.research_runtime import _run_planned_tool
        await _run_planned_tool(project, project["plan"]["steps"][0], self.store)
        self.assertEqual(len(self.worker_calls), 1)
        self.assertEqual(len(self.planner_messages), 2)

    async def test_repairs_are_bounded(self):
        project_id = self.create(tools=["python"])
        self.planner_replies = [json.dumps({"name": "run_python", "input": {"code": f"result = bad{i}"}}) for i in range(5)]
        self.worker_replies = [{"ok": False, "verification_status": "PROGRAM_FAILURE", "error_type": "PROGRAM_ERROR", "error": f"bad{i}", "worker_exit_code": 0, "output": ""} for i in range(5)]
        project = self.store.get_project(project_id)
        from aleggo.study.research_runtime import _run_planned_tool
        await _run_planned_tool(project, project["plan"]["steps"][0], self.store)
        self.assertEqual(len(self.worker_calls), 3)  # first call + two repairs


class DiscussionTests(RuntimeHarness):
    async def discuss(self, project_id, message):
        queue_id = collaborator.create_discussion(self.store, project_id, message, "main")
        with self.store.connection() as conn:
            self.store._intervention(conn, project_id, 1, "discuss", "researcher", {"message": message, "queueId": queue_id}, "2099-01-01T00:00:00+00:00")
        await collaborator.start_discussion(project_id, self.store, queue_id, message, "main")
        return queue_id

    async def test_timeout_preserves_completed_tool_and_unknown_usage(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = [json.dumps({"tool": "run_python", "input": {"code": "result=42"}}), TimeoutError()]
        self.worker_replies = [{"ok": True, "verification_status": "SUCCESS", "output": "42"}]
        queue_id = await self.discuss(project_id, "计算")
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "FAILED")
        reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertIn("42", reply["content"])
        self.assertIn("用量未知", reply["content"])
        self.assertTrue(reply["metadata"]["usageIncomplete"])

    async def test_cancel_running_discussion_preserves_tool_result(self):
        project_id = self.create(status="COMPLETED")
        waiting = asyncio.Event()
        async def model(model_id, messages, **kwargs):
            if len(messages) == 2:
                return {"text": json.dumps({"tool": "run_python", "input": {"code": "result=42"}})}
            waiting.set()
            await asyncio.Event().wait()
        queue_id = collaborator.create_discussion(self.store, project_id, "计算", "main")
        self.worker_replies = [{"ok": True, "verification_status": "SUCCESS", "output": "42"}]
        with patch("aleggo.study.research_runtime._MODEL_ROUTER.complete", side_effect=model):
            collaborator.start_discussion(project_id, self.store, queue_id, "计算", "main")
            await asyncio.wait_for(waiting.wait(), 5)
            self.assertFalse(await collaborator.cancel_discussion("wrong-project", self.store, queue_id))
            self.assertTrue(await collaborator.cancel_discussion(project_id, self.store, queue_id))
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "CANCELLED")
        reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertIn("42", reply["content"])
        self.assertTrue(reply["metadata"]["usageIncomplete"])

    async def test_dsml_tool_request_is_executed_and_result_returned_to_model(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = ['<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="run_python">\n<｜｜DSML｜｜ parameter name="code" string="true">\nresult = 6 * 7\n</｜｜DSML｜｜ parameter>\n<｜｜DSML｜｜ parameter name="purpose" string="true">arithmetic</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>', '结果是 42。']
        self.worker_replies = [{"ok": True, "verification_status": "SUCCESS", "output": "42"}]
        queue_id = await self.discuss(project_id, "计算 6 乘 7")
        self.assertEqual(self.worker_calls[0]["input_data"]["code"], "result = 6 * 7")
        self.assertIn("42", self.chat_messages[1][-1]["content"])
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "COMPLETED")

    async def test_malformed_dsml_is_retried_instead_of_shown_as_answer(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = ['<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="run_python">broken</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>', json.dumps({"tool": "run_python", "input": {"code": "result=42"}}), '结果是 42。']
        self.worker_replies = [{"ok": True, "verification_status": "SUCCESS", "output": "42"}]
        queue_id = await self.discuss(project_id, "计算")
        self.assertIn("not valid and was not executed", self.chat_messages[1][-1]["content"])
        self.assertEqual(len(self.worker_calls), 1)
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertEqual(reply["content"], '结果是 42。')

    def test_dsml_requires_one_complete_unquoted_invocation(self):
        tag = '｜｜DSML｜｜'
        call = f'<{tag} calls><{tag} invoke name="matrix_compute"><{tag} parameter name="input">{{"matrix":[[1]],"operation":"det"}}</{tag} parameter></{tag} invoke></{tag} calls>'
        self.assertEqual(collaborator._tool_call(call)["input"]["matrix"], [[1]])
        self.assertEqual(collaborator._tool_call('I will calculate this.\n' + call)["input"]["matrix"], [[1]])
        bare = call.replace('<'+tag+' calls>', '', 1)
        self.assertEqual(collaborator._tool_call(bare)["input"]["matrix"], [[1]])
        self.assertIsNone(collaborator._tool_call(bare.replace('</'+tag+' invoke>', '')))
        self.assertIsNone(collaborator._tool_call('示例：' + call))
        self.assertIsNone(collaborator._tool_call(call.replace('</'+tag+' invoke>', '</'+tag+' invoke><'+tag+' invoke name="run_python"></'+tag+' invoke>')))
        json_call = json.dumps({"tool": "run_python", "input": {"code": "result = '} { quoted'"}})
        self.assertEqual(collaborator._tool_call(json_call)["input"]["code"], "result = '} { quoted'")
        self.assertEqual(collaborator._tool_call('For $v_{k-1}$, compute:\n' + json_call)["input"]["code"], "result = '} { quoted'")

    async def test_incomplete_dsml_is_not_reported_as_completed_answer(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = ['<｜｜DSML｜｜ invoke name="run_python">incomplete'] * (collaborator.MAX_TOOL_CALLS + 2)
        queue_id = await self.discuss(project_id, "计算")
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "FAILED")
        reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertIn("没有执行数学计算", reply["content"])
        self.assertEqual(len(self.worker_calls), 0)

    async def test_prefaced_incomplete_dsml_is_retried(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = ['I will use Python.\n<｜｜DSML｜｜ invoke name="run_python">broken',
                             json.dumps({"tool": "run_python", "input": {"code": "result=42"}}), '结果是 42。']
        self.worker_replies = [{"ok": True, "verification_status": "SUCCESS", "output": "42"}]
        queue_id = await self.discuss(project_id, "计算")
        self.assertIn("not valid and was not executed", self.chat_messages[1][-1]["content"])
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(len(self.worker_calls), 1)

    async def test_stray_closing_dsml_is_not_an_answer(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = ['unfinished request </｜｜DSML｜｜>', '正常答复。']
        queue_id = await self.discuss(project_id, "计算")
        self.assertIn("not valid and was not executed", self.chat_messages[1][-1]["content"])
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "COMPLETED")

    async def test_offline_session_rejects_database_without_disabling_math(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = [json.dumps({"tool": "oeis_search", "input": {"query": "1,2,3"}}),
                             json.dumps({"tool": "run_python", "input": {"code": "result=42"}}), '结果是 42。']
        self.worker_replies = [{"ok": True, "verification_status": "SUCCESS", "output": "42"}]
        with patch.dict("os.environ", {"ALEGGO_RESEARCH_DISABLE_DATABASES": "1"}):
            queue_id = await self.discuss(project_id, "计算")
        self.assertNotIn("oeis_search", self.chat_messages[0][0]["content"])
        self.assertIn("disabled for this session", self.chat_messages[1][-1]["content"])
        self.assertEqual([x["name"] for x in self.worker_calls], ["run_python"])
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "COMPLETED")

    async def test_ai_answers_with_a_recorded_calculation(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = [json.dumps({"tool": "run_python", "input": {"code": "result = {'casesChecked': 49, 'counterexample': None}"}, "purpose": "check small cases"}),
                             "在 $4 \\le n \\le 100$ 的偶数中没有反例（计算验证，非证明）。"]
        self.worker_replies = [{"ok": True, "verification_status": "SUCCESS", "verification_level": "BOUNDED_CHECK", "output": "{'casesChecked': 49, 'counterexample': None}", "worker_exit_code": 0}]
        queue_id = await self.discuss(project_id, "能先帮我验证一下小范围的情况吗？")
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "COMPLETED")
        reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertEqual(reply["agentRole"], "research-collaborator")
        self.assertIn("计算验证", reply["content"])
        self.assertEqual(reply["metadata"]["toolCalls"], 1)
        # The model saw the real worker output before answering.
        self.assertIn("casesChecked", self.chat_messages[1][-1]["content"])
        self.assertIn("Is every even n > 2", self.chat_messages[0][0]["content"])
        page = build_thread_page(self.store, self.store.get_project(project_id))
        kinds = [(item["kind"], item.get("intervention") or item.get("role") or item.get("tool")) for item in page["items"]]
        self.assertIn(("user", "discuss"), kinds)
        self.assertIn(("tool", "run_python"), kinds)
        self.assertIn(("answer", "research-collaborator"), kinds)
        self.assertEqual(page["queue"][queue_id], "COMPLETED")

    async def test_natural_proof_request_can_choose_literature_and_computation_in_one_discussion(self):
        project_id = self.create(status="PAUSED")
        self.chat_replies = [
            json.dumps({"tool": "arxiv_search", "input": {"query": "prime generating polynomials", "limit": 2}, "purpose": "look for relevant literature"}),
            json.dumps({"tool": "factor_integer", "input": {"n": 1681}, "purpose": "check a potential counterexample"}),
            "查阅检索结果后做了精确计算：1681 = 41^2，原命题有反例。",
        ]
        self.worker_replies = [
            {"ok": True, "verification_status": "DATABASE_RECORDS", "output": "A synthetic literature result", "records": [{"title": "Fixture reference"}]},
            {"ok": True, "verification_status": "SUCCESS", "verification_level": "SYMBOLIC_CHECK", "output": "1681 = 41**2"},
        ]
        message = "帮我证明一下这个，需要什么工具你自己决定。"
        queue_id = await self.discuss(project_id, message)
        self.assertEqual([call["name"] for call in self.worker_calls], ["arxiv_search", "factor_integer"])
        self.assertEqual(self.chat_messages[0][-1]["content"], message)
        self.assertIn("literature result", self.chat_messages[1][-1]["content"])
        self.assertIn("1681 = 41**2", self.chat_messages[2][-1]["content"])
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(self.store.get_project(project_id)["status"], "PAUSED")

    async def test_natural_counterexample_request_uses_the_same_agent_entry(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = [
            json.dumps({"tool": "run_python", "input": {"code": "result = [(n, n*n+n+41) for n in range(1, 42) if (n*n+n+41) % 41 == 0]"}}),
            "找到了 n=40 的反例，P(40)=1681。",
        ]
        self.worker_replies = [{"ok": True, "verification_status": "SUCCESS", "output": "[(40, 1681), (41, 1763)]"}]
        queue_id = await self.discuss(project_id, "帮我找反例")
        self.assertEqual(self.worker_calls[0]["name"], "run_python")
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["intervention_type"], "discuss")
        self.assertEqual(row["status"], "COMPLETED")

    async def test_follow_up_sees_previous_turn_and_never_enters_the_step_queue(self):
        project_id = self.create(status="RUNNING")
        self.chat_replies = ["First answer about parity.", "Second answer."]
        await self.discuss(project_id, "First question?")
        await self.discuss(project_id, "Follow-up?")
        second = self.chat_messages[1]
        self.assertEqual([m["content"] for m in second[1:]], ["First question?", "First answer about parity.", "Follow-up?"])
        project = self.store.get_project(project_id)
        await _process_interventions(project, project["plan"], self.store)
        self.assertEqual(len(self.chat_messages), 2)
        self.assertEqual(len(self.worker_calls), 0)

    async def test_tool_budget_is_bounded_and_unknown_tools_are_reported(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = [json.dumps({"tool": "shell", "input": {"cmd": "ls"}})] + [
            json.dumps({"tool": "run_python", "input": {"code": f"result = {i}"}}) for i in range(10)]
        queue_id = await self.discuss(project_id, "Compute a lot")
        self.assertLessEqual(len(self.worker_calls), collaborator.MAX_TOOL_CALLS - 1)
        self.assertIn("Unknown tool", self.chat_messages[1][-1]["content"])
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "FAILED")  # no final reply after the budget was exhausted
        reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertTrue(reply["content"])

    async def test_model_failure_is_shown_with_secrets_redacted(self):
        project_id = self.create(status="COMPLETED")
        self.chat_replies = [RuntimeError("provider rejected api_key=sk-SECRET-123")]
        queue_id = await self.discuss(project_id, "Hello?")
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        self.assertEqual(row["status"], "FAILED")
        reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertIn("[redacted]", reply["content"])
        self.assertNotIn("sk-SECRET-123", reply["content"])


class FinishedProjectRequestTests(RuntimeHarness):
    def setUp(self):
        super().setUp()
        self.router_patch = patch("aleggo.api.routers.research._store", return_value=self.store)
        self.router_patch.start()

    def tearDown(self):
        self.router_patch.stop()
        super().tearDown()

    async def test_request_on_completed_project_runs_now_instead_of_waiting_forever(self):
        project_id = self.create(status="COMPLETED")
        payload = research_router.InterventionRequest(kind="request_critique", message="Attack the parity argument")
        result = await research_router.record_intervention(project_id, payload)
        self.assertEqual(result["status"], "RUNNING_NOW")
        await collaborator._DRAINS[project_id] if project_id in collaborator._DRAINS else None
        row = next(item for item in self.queue_rows(project_id) if item["id"] == result["queueId"])
        self.assertEqual(row["status"], "COMPLETED")
        artifact = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertIn("Finding", artifact["content"])

    async def test_request_on_paused_project_still_waits_for_the_next_safe_step(self):
        project_id = self.create(status="PAUSED")
        payload = research_router.InterventionRequest(kind="request_critique", message="Later please")
        result = await research_router.record_intervention(project_id, payload)
        self.assertEqual(result["status"], "QUEUED")
        self.assertNotIn(project_id, collaborator._DRAINS)

    async def test_discuss_endpoint_validates_and_replies(self):
        project_id = self.create(status="AWAITING_APPROVAL")
        with self.assertRaises(HTTPException) as raised:
            await research_router.record_intervention(project_id, research_router.InterventionRequest(kind="discuss", message="   "))
        self.assertEqual(raised.exception.status_code, 422)
        self.chat_replies = ["Here is an idea."]
        result = await research_router.record_intervention(project_id, research_router.InterventionRequest(kind="discuss", message="Any idea?"))
        self.assertEqual(result["status"], "RUNNING")
        await collaborator._TASKS[result["queueId"]] if result["queueId"] in collaborator._TASKS else None
        row = next(item for item in self.queue_rows(project_id) if item["id"] == result["queueId"])
        self.assertEqual(row["status"], "COMPLETED")

    async def test_thread_poll_drains_requests_left_by_an_older_release(self):
        project_id = self.create(status="COMPLETED")
        with self.store.connection() as conn:
            conn.execute("INSERT INTO research_intervention_queue(id,project_id,intervention_type,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         ("legacy", project_id, "request_critique", json.dumps({"message": "old"}), "QUEUED", "2026-01-01", "2026-01-01"))
        await research_router.research_thread(project_id)
        await collaborator._DRAINS[project_id] if project_id in collaborator._DRAINS else None
        self.assertEqual(next(item for item in self.queue_rows(project_id) if item["id"] == "legacy")["status"], "COMPLETED")


class RecoveryTests(RuntimeHarness):
    async def test_restart_requeues_discussions_and_finished_project_requests(self):
        done = self.create(status="COMPLETED")
        with self.store.connection() as conn:
            for queue_id, kind in (("talk", "discuss"), ("req", "request_critique")):
                conn.execute("INSERT INTO research_intervention_queue(id,project_id,intervention_type,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                             (queue_id, done, kind, "{}", "RUNNING", "2026-01-01", "2026-01-01"))
        store_module._RECOVERED_PATHS.discard(str(self.store.path.resolve()))
        recovered = ResearchStore(self.store.aleggo_store)
        with recovered.connection() as conn:
            states = dict(conn.execute("SELECT id,status FROM research_intervention_queue WHERE project_id=?", (done,)).fetchall())
        self.assertEqual(states, {"talk": "QUEUED", "req": "QUEUED"})

    async def test_interrupted_discussion_is_answered_after_restart(self):
        from datetime import UTC, datetime, timedelta

        project_id = self.create(status="COMPLETED")
        recent = datetime.now(UTC).isoformat()
        stale = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        with self.store.connection() as conn:
            for queue_id, created in (("fresh", recent), ("old", stale)):
                conn.execute("INSERT INTO research_intervention_queue(id,project_id,intervention_type,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                             (queue_id, project_id, "discuss", json.dumps({"message": "Still there? " + queue_id, "branch": "main"}), "RUNNING", created, created))
        store_module._RECOVERED_PATHS.discard(str(self.store.path.resolve()))
        recovered = ResearchStore(self.store.aleggo_store)
        self.chat_replies = ["Resumed answer."]
        resumed = collaborator.resume_pending_discussions(project_id, recovered)
        self.assertEqual(resumed, ["fresh"])
        self.assertEqual(collaborator.resume_pending_discussions(project_id, recovered), [])  # claimed once
        await collaborator._TASKS["fresh"]
        rows = {row["id"]: row for row in self.queue_rows(project_id)}
        self.assertEqual(rows["fresh"]["status"], "COMPLETED")
        self.assertEqual(rows["old"]["status"], "FAILED")
        self.assertEqual(self.chat_messages[0][-1]["content"], "Still there? fresh")
        old_reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == rows["old"]["result_artifact_id"])
        self.assertIn("重新发送", old_reply["content"])

    async def test_discussion_tools_run_in_their_own_directory_and_record_usage(self):
        project_id = self.create(status="COMPLETED")

        async def complete(model, messages, **kwargs):
            self.chat_messages.append(messages)
            if len(self.chat_messages) == 1:
                return {"text": json.dumps({"tool": "oeis_search", "input": {"query": "1,1,2,5,14,42"}}),
                        "usage": {"promptTokens": 100, "completionTokens": 20, "totalTokens": 120}}
            return {"text": "Catalan numbers (A000108).", "usage": {"promptTokens": 150, "completionTokens": 30, "totalTokens": 180}}

        with patch("aleggo.study.research_runtime._MODEL_ROUTER.complete", side_effect=complete):
            queue_id = collaborator.create_discussion(self.store, project_id, "What is 1,1,2,5,14,42?", "main")
            await collaborator.start_discussion(project_id, self.store, queue_id, "What is 1,1,2,5,14,42?", "main")
        self.assertEqual(self.worker_calls[0]["name"], "oeis_search")
        self.assertEqual(self.worker_calls[0]["subdirectory"], "discussions/" + queue_id)
        self.assertIn("oeis_search", self.chat_messages[0][0]["content"])
        row = next(item for item in self.queue_rows(project_id) if item["id"] == queue_id)
        reply = next(item for item in self.store.list_artifacts(project_id) if item["id"] == row["result_artifact_id"])
        self.assertEqual(reply["metadata"]["usage"]["totalTokens"], 300)
        llm_runs = [run for run in ResearchDomainStore(self.store).list_tool_runs(project_id) if run["tool"].startswith("llm:")]
        self.assertTrue(all(run["metadata"].get("usage") for run in llm_runs))


if __name__ == "__main__":
    unittest.main()
