"""Runtime self-improvement agents: ExecutionAgent (A/B), ReflectorAgent, MetaReflectorAgent, PractitionerAgent.

Hard Principles Enforced:
- No free chat between agents; all inputs and outputs use strict Pydantic schemas.
- On-demand invocation for Reflector and Meta-Reflector; every LLM call records token usage.
- Execution Agent A and B have identical initial config and strictly isolated private memory.
- Practitioner Agent only executes benchmark runs and records raw traces; StrategyManager judges validity.
- No fake failures, no forced approvals, and no default fallback strings masking LLM parse failures.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import time
from typing import Any

from aleggo.study.math_tools import ALLOWED_MATH_TOOLS, run_math_tool
from aleggo.study.model_registry import ModelRouter
from aleggo.study.research_collaborator import _tool_call
from aleggo.study.research_tool_catalog import canonical_worker_invocation, planner_tool_reference
from aleggo.study.self_improvement.benchmark import load_benchmark_problems, select_benchmark_subset
from aleggo.study.self_improvement.models import (
    Experience,
    ExperimentReport,
    ExperimentTask,
    MetaReviewDecision,
    Strategy,
    StrategyProposal,
    StrategySelection,
    StrategySelectionItem,
    StrategyVersionRef,
    StructuredStrategyChange,
    TaskTrace,
    TokenUsage,
    TraceStep,
    VerificationResult,
    new_id,
    utc_now_iso,
)
from aleggo.study.self_improvement.store import SelfImprovementStore
from aleggo.study.self_improvement.strategy_manager import StrategyManager
from aleggo.study.self_improvement.verifiers import verify_problem_answer

MAX_EXECUTION_TOOL_STEPS = 5
MAX_AGENT_OUTPUT_TOKENS = 8192
AGENT_PROJECT_IDS = {
    "agent_a": "research_00000000000000000000000000000001",
    "agent_b": "research_00000000000000000000000000000002",
}


class ReflectionParseError(ValueError):
    """Raised when ReflectorAgent output is not valid JSON or lacks required fields."""

    def __init__(self, message: str, raw_output: str = "") -> None:
        super().__init__(message)
        self.raw_output = raw_output


class MetaReviewParseError(ValueError):
    """Raised when MetaReflectorAgent output is not valid JSON or has an invalid decision."""

    def __init__(self, message: str, raw_output: str = "") -> None:
        super().__init__(message)
        self.raw_output = raw_output


async def _complete_with_usage(
    router: ModelRouter,
    model_id: str,
    messages: list[dict[str, str]],
    usage_acc: TokenUsage,
    *,
    temperature: float = 0.2,
    json_object: bool = False,
    max_output_tokens: int = MAX_AGENT_OUTPUT_TOKENS,
    retry_on_output_limit: bool = True,
) -> str:
    try:
        resp = await router.complete(
            model_id,
            messages,
            temperature=temperature,
            json_object=json_object,
            max_output_tokens=max_output_tokens,
        )
        _accumulate_usage(usage_acc, resp.get("usage"))
        return str(resp.get("text") or "")
    except Exception as exc:
        reported = getattr(exc, "reported_usage", None)
        if isinstance(reported, dict):
            _accumulate_usage(usage_acc, reported)
        if retry_on_output_limit and getattr(exc, "code", "") == "output_limit":
            resp2 = await router.complete(
                model_id,
                messages + [{"role": "user", "content": "请务必精简思考过程，直接给出紧凑的 JSON 或最终结论。"}],
                temperature=temperature,
                json_object=json_object,
                max_output_tokens=16384,
            )
            _accumulate_usage(usage_acc, resp2.get("usage"))
            return str(resp2.get("text") or "")
        raise


def _accumulate_usage(acc: TokenUsage, raw_usage: dict[str, Any] | None) -> None:
    if not isinstance(raw_usage, dict):
        return
    acc.model_calls += 1
    acc.prompt_tokens += int(raw_usage.get("promptTokens") or raw_usage.get("prompt_tokens") or 0)
    acc.completion_tokens += int(raw_usage.get("completionTokens") or raw_usage.get("completion_tokens") or 0)
    total = int(raw_usage.get("totalTokens") or raw_usage.get("total_tokens") or 0)
    acc.total_tokens += total or (
        int(raw_usage.get("promptTokens") or 0) + int(raw_usage.get("completionTokens") or 0)
    )
    if raw_usage.get("actualModel"):
        acc.actual_model = str(raw_usage["actualModel"])


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", stripped)
    if fenced:
        try:
            obj = json.loads(fenced.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", stripped):
        try:
            obj, _ = decoder.raw_decode(stripped[match.start() :])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return {}


class ExecutionAgent:
    """Execution Agent (instantiated as 'agent_a' or 'agent_b') solving mathematical tasks."""

    def __init__(
        self,
        agent_id: str,
        store: SelfImprovementStore,
        workspace_root: Path,
        model_router: ModelRouter | None = None,
        model_id: str = "research.meridian",
    ) -> None:
        if agent_id not in {"agent_a", "agent_b"}:
            raise ValueError(f"ExecutionAgent agent_id must be 'agent_a' or 'agent_b', got '{agent_id}'.")
        self.agent_id = agent_id
        self.store = store
        self.workspace_root = Path(workspace_root)
        self.router = model_router or ModelRouter()
        self.model_id = model_id

    def get_active_strategies(self) -> list[Strategy]:
        return self.store.list_strategies(
            owner=self.agent_id,
            status="active",
            caller_role=self.agent_id,
            include_legacy_demo=False,
        )

    def get_private_experiences(self, limit: int = 10) -> list[Experience]:
        return self.store.list_experiences(
            self.agent_id,
            caller_role=self.agent_id,
            limit=limit,
        )

    async def select_strategies(
        self,
        problem_text: str,
        available_strategies: list[Strategy],
        experiences: list[Experience],
        usage_acc: TokenUsage,
        *,
        temperature: float = 0.2,
    ) -> StrategySelection:
        """Step 1: Inject active strategy summary cards and output structured strategy_selection."""
        cards = [s.to_summary_card() for s in available_strategies]
        exp_summaries = [
            {"type": e.experience_type, "content": e.content[:240], "strategy_ids": e.strategy_ids}
            for e in experiences[:6]
        ]
        system_prompt = (
            f"你是 A-leggo 研究端执行智能体 {self.agent_id}。"
            "在开始解答数学题之前，你必须先根据你自己的 active 策略摘要卡片和历史经验，自主选择 1～2 条最适合本题的策略，"
            "并只输出一个 JSON 对象，格式严格为：\n"
            '{"selected_strategy_ids": ["策略id"], '
            '"selections": [{"strategy_id": "策略id", "version": 1, "reason": "选择该策略的具体数学理由"}], '
            '"overall_plan": "简明解题步骤规划"}'
        )
        user_prompt = json.dumps(
            {
                "problem": problem_text,
                "active_strategy_cards": cards,
                "recent_private_experiences": exp_summaries,
            },
            ensure_ascii=False,
        )
        text = await _complete_with_usage(
            self.router,
            self.model_id,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            usage_acc,
            temperature=temperature,
            json_object=True,
        )
        parsed = _parse_json_object(text)
        valid_by_id = {s.id: s for s in available_strategies}

        selections: list[StrategySelectionItem] = []
        raw_selections = parsed.get("selections")
        if isinstance(raw_selections, list):
            for item in raw_selections:
                if isinstance(item, dict):
                    sid = str(item.get("strategy_id") or "").strip()
                    reason_str = str(item.get("reason") or "").strip()
                    if sid in valid_by_id:
                        selections.append(
                            StrategySelectionItem(
                                strategy_id=sid,
                                version=valid_by_id[sid].version,
                                reason=reason_str,
                            )
                        )
        selected_ids = list(dict.fromkeys(item.strategy_id for item in selections))
        return StrategySelection(
            selected_strategy_ids=selected_ids,
            selections=selections,
            overall_plan=str(parsed.get("overall_plan") or "").strip(),
        )

    async def solve_task(
        self,
        problem_text: str,
        *,
        task_id: str | None = None,
        verifier_spec: dict[str, Any] | None = None,
        strategy_override: list[Strategy] | None = None,
        record_experience: bool = True,
        temperature: float = 0.2,
        allow_tools: bool = True,
    ) -> TaskTrace:
        """Execute full problem-solving loop: strategy_selection -> tool calls -> verification -> append TaskTrace -> Experience."""
        started = time.monotonic()
        tid = task_id or new_id("task")
        active_strategies = strategy_override if strategy_override is not None else self.get_active_strategies()
        experiences = self.get_private_experiences(limit=8)
        usage_acc = TokenUsage()

        selection = await self.select_strategies(
            problem_text,
            active_strategies,
            experiences,
            usage_acc,
            temperature=temperature,
        )
        selected_cards = [
            s.to_summary_card()
            for s in active_strategies
            if s.id in selection.selected_strategy_ids
        ]

        tool_ref = planner_tool_reference(include_databases=False) if allow_tools else "当前模式禁用外部工具。"
        system_prompt = (
            f"你是 A-leggo 研究端执行智能体 `{self.agent_id}`。\n"
            "你必须严格按照你所选定的策略执行步骤（execution_steps）和禁忌（taboos_and_counterexamples）来解数学题。\n\n"
            f"【你本次选定的策略卡片】\n{json.dumps(selected_cards, ensure_ascii=False, indent=2)}\n\n"
            + (
                "【工具调用规则】\n"
                "当需要执行数值实验、符号推导或方程求解时，请只输出一行 JSON 工具调用：\n"
                '{"tool": "run_python", "input": {"code": "import sympy as sp\\n...\\nresult = ..."}, "purpose": "简述本步计算目的"}\n'
                f"可用工具目录：\n{tool_ref}\n\n"
                if allow_tools
                else ""
            )
            + "【最终作答格式】\n"
            "请保持推导精炼紧凑，完成必要的工具验证后立即给出结论，并在**最后一行**严格按以下格式给出最终精简答案（不要带多余文字）：\n"
            "FINAL_ANSWER: <你的最终表达式或数值>"
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"题目：{problem_text}\n\n"
                    f"已选策略：{json.dumps(selection.model_dump(), ensure_ascii=False)}\n"
                    "请按所选策略开始解题。"
                ),
            },
        ]

        steps: list[TraceStep] = []
        final_answer_text = ""

        max_turns = (MAX_EXECUTION_TOOL_STEPS + 2) if allow_tools else 1
        for turn in range(max_turns):
            step_started = time.monotonic()
            try:
                reply_text = (
                    await _complete_with_usage(
                        self.router,
                        self.model_id,
                        messages,
                        usage_acc,
                        temperature=temperature,
                        json_object=False,
                        max_output_tokens=MAX_AGENT_OUTPUT_TOKENS,
                        retry_on_output_limit=True,
                    )
                ).strip()
            except Exception as exc:
                step_elapsed = int((time.monotonic() - step_started) * 1000)
                steps.append(
                    TraceStep(
                        step_index=len(steps) + 1,
                        reasoning_summary=f"{type(exc).__name__}: {exc}",
                        tool_name="",
                        tool_input={},
                        tool_output=str(exc),
                        tool_ok=False,
                        elapsed_ms=step_elapsed,
                    )
                )
                final_answer_text = ""
                break

            call = _tool_call(reply_text) if allow_tools else None
            if call is None:
                unquoted = re.sub(r"```[\s\S]*?```", "", reply_text)
                if allow_tools and re.search(r"</?[|｜]{1,2}\s*DSML\s*[|｜]{1,2}", unquoted):
                    messages += [
                        {"role": "assistant", "content": reply_text},
                        {
                            "role": "user",
                            "content": '工具调用格式无效，未被执行。请仅输出一个标准 JSON 对象：{"tool": "run_python", "input": {"code": "..."}, "purpose": "..."}，或直接给出包含 FINAL_ANSWER: 的最终解答。',
                        },
                    ]
                    continue
                step_elapsed = int((time.monotonic() - step_started) * 1000)
                steps.append(
                    TraceStep(
                        step_index=len(steps) + 1,
                        reasoning_summary=reply_text[:600],
                        tool_name="",
                        tool_input={},
                        tool_output="",
                        tool_ok=True,
                        elapsed_ms=step_elapsed,
                    )
                )
                final_answer_text = reply_text
                break

            if len(steps) >= MAX_EXECUTION_TOOL_STEPS:
                messages += [
                    {"role": "assistant", "content": reply_text},
                    {
                        "role": "user",
                        "content": "工具调用次数已达上限，请立即根据已有计算结果给出最终解答，并在最后一行写出 FINAL_ANSWER: <答案>。",
                    },
                ]
                continue

            tool_name, tool_input = canonical_worker_invocation(call)
            tool_started = time.monotonic()
            if tool_name not in ALLOWED_MATH_TOOLS or not isinstance(tool_input, dict):
                tool_res: dict[str, Any] = {
                    "ok": False,
                    "output": "",
                    "error": f"未知或参数非法的工具调用: {tool_name}",
                }
            else:
                try:
                    tool_res = await asyncio.wait_for(
                        run_math_tool(
                            workspace_root=self.workspace_root,
                            project_id=AGENT_PROJECT_IDS.get(
                                self.agent_id, "research_00000000000000000000000000000001"
                            ),
                            name=tool_name,
                            input_data=tool_input,
                            timeout_seconds=30,
                            subdirectory=f"si_runs/{tid}",
                        ),
                        timeout=35,
                    )
                except Exception as exc:
                    tool_res = {"ok": False, "output": "", "error": str(exc)}
            tool_elapsed = int((time.monotonic() - tool_started) * 1000)
            tool_ok = bool(tool_res.get("ok"))
            tool_output = str(tool_res.get("output") or tool_res.get("error") or "")[:3000]
            purpose = str(call.get("purpose") or "").strip()

            steps.append(
                TraceStep(
                    step_index=len(steps) + 1,
                    reasoning_summary=purpose[:400],
                    tool_name=tool_name,
                    tool_input=tool_input if isinstance(tool_input, dict) else {},
                    tool_output=tool_output,
                    tool_ok=tool_ok,
                    elapsed_ms=tool_elapsed,
                )
            )
            observed_payload = {
                "tool": tool_name,
                "ok": tool_ok,
                "output": tool_output,
                "error": str(tool_res.get("error") or "")[:1000],
            }
            messages += [
                {"role": "assistant", "content": reply_text},
                {
                    "role": "user",
                    "content": (
                        "<<<TOOL_RESULT_BEGIN>>>\n"
                        + json.dumps(observed_payload, ensure_ascii=False)
                        + "\n<<<TOOL_RESULT_END>>>\n"
                        "请基于以上真实工具返回结果继续推理。如果已能确定答案，请给出解答并在最后一行写明 FINAL_ANSWER: <答案>。"
                    ),
                },
            ]

        if not final_answer_text and steps and steps[-1].tool_ok:
            final_answer_text = steps[-1].tool_output

        if verifier_spec:
            verification = await verify_problem_answer(final_answer_text, verifier_spec, self.workspace_root)
        else:
            any_tool_ok = any(s.tool_name and s.tool_ok for s in steps)
            verification = VerificationResult(
                passed=bool(final_answer_text.strip()) and (any_tool_ok or not allow_tools),
                verifier_type="execution_check",
                verifier_output="已通过工具执行与输出完整性校验" if any_tool_ok else "已完成推演输出",
                expected="",
                actual=final_answer_text[-200:],
            )

        total_elapsed_ms = int((time.monotonic() - started) * 1000)
        trace = TaskTrace(
            agent_id=self.agent_id,
            task_id=tid,
            problem_text=problem_text,
            available_strategy_versions=[
                StrategyVersionRef(id=s.id, name=s.name, version=s.version, status=s.status)
                for s in active_strategies
            ],
            strategy_selection=selection,
            steps=steps,
            final_answer=final_answer_text,
            verification_result=verification,
            token_usage=usage_acc,
            total_elapsed_ms=total_elapsed_ms,
            created_at=utc_now_iso(),
        )
        self.store.append_trace(trace, caller_role=self.agent_id)

        if record_experience:
            exp_type = "success" if verification.passed else "failure"
            tools_used = [s.tool_name for s in steps if s.tool_name]
            exp_content = (
                f"在任务 {tid}（{problem_text[:80]}...）中选用策略 {selection.selected_strategy_ids}，"
                f"调用工具 {tools_used or ['无']}，验证结果：{'通过' if verification.passed else '失败'}（{verification.verifier_output[:160]}）。"
            )
            exp = Experience(
                agent_id=self.agent_id,
                experience_type=exp_type,
                content=exp_content,
                source_trace_ids=[trace.id],
                strategy_ids=selection.selected_strategy_ids,
                tags=["auto_recorded", exp_type],
            )
            self.store.add_experience(exp, caller_role=self.agent_id)

        return trace


class ReflectorAgent:
    """Shared Reflector Agent ('reflector'): analyzes a TaskTrace and outputs a StrategyProposal."""

    role = "reflector"

    def __init__(
        self,
        store: SelfImprovementStore,
        strategy_manager: StrategyManager,
        model_router: ModelRouter | None = None,
        model_id: str = "research.meridian",
    ) -> None:
        self.store = store
        self.strategy_manager = strategy_manager
        self.router = model_router or ModelRouter()
        self.model_id = model_id

    async def reflect_on_trace(
        self,
        trace_id: str,
        *,
        register: bool = True,
    ) -> StrategyProposal:
        """Compatibility wrapper that sends even a single trace through the batch reflector."""
        return await self.reflect_on_traces([trace_id], register=register)

    async def reflect_on_traces(
        self,
        trace_ids: list[str],
        *,
        register: bool = True,
    ) -> StrategyProposal:
        """Analyze a batch of failed TaskTrace records in one model call and propose one reusable strategy.

        If JSON parsing fails or required fields are missing, records a parse_error audit log with raw model output
        and raises ReflectionParseError without generating a proposal.
        """
        unique_trace_ids = list(dict.fromkeys(str(value).strip() for value in trace_ids if str(value).strip()))
        if not unique_trace_ids:
            raise ValueError("反思批次至少需要一条失败轨迹。")
        traces = []
        for current_id in unique_trace_ids:
            current = self.store.get_trace(current_id, caller_role=self.role)
            if not current:
                raise KeyError(f"TaskTrace '{current_id}' not found.")
            traces.append(current)
        if any(current.verification_result.passed for current in traces):
            raise ValueError("ReflectorAgent 只接受验证失败的轨迹。")
        if len({current.agent_id for current in traces}) != 1:
            raise ValueError("同一反思批次中的轨迹必须来自同一执行 Agent。")
        trace = traces[0]

        target_agent = trace.agent_id
        strategies = self.store.list_strategies(
            owner=target_agent,
            status="active",
            caller_role=self.role,
            include_legacy_demo=False,
        )
        experiences = self.store.list_experiences(target_agent, caller_role=self.role, limit=10)

        selected_ids = trace.strategy_selection.selected_strategy_ids
        base_strategy = next((s for s in strategies if s.id in selected_ids), strategies[0] if strategies else None)

        system_prompt = (
            "你是 A-leggo 研究端的共享反思智能体（Reflector Agent）。\n"
            "你的职责是一次分析一批执行智能体的真实失败轨迹（TaskTrace），结合私有经验与当前策略，找出跨题重复出现的失败机制，"
            "并提出一条可验证、可复用的策略改进申请（StrategyProposal）。优先区分共性原因和单题偶发现象；不要只围绕第一条轨迹下结论。\n"
            "注意：\n"
            "1. 你只能输出结构化 JSON，不能直接修改策略，也不能批准申请。\n"
            "2. 新策略必须是可复用的结构化数学方法（包含适用条件、有序执行步骤、禁忌和反例），绝不能把某道题的具体答案写进策略。\n"
            "3. 明确写出新方法相对于轨迹中原有做法新增了什么；如果轨迹已表明 Agent 会用该方法，不能宣称这是新能力。\n"
            "4. 仅依据当前批次的真实轨迹，不得引用未提供的题目或实验结果。\n"
            "5. 可提出诊断实验、合并/废弃策略、程序规则、新工具或答案有误举报；这些只是待审建议，绝不能在本轮执行或替人批准。高风险建议必须标记 risk=high 并等待人工审批。\n"
            "6. available_math_tools 是当前已接通工具目录，不是题目证据或解题提示。只有当工具能支持可复现的核验时，才可建议在策略中使用；不得声称产生了未出现在轨迹中的工具结果。\n"
            "7. 请严格返回如下 JSON 格式：\n"
            "{\n"
            '  "base_strategy_id": "被改进的旧策略ID（或 null 表示新增策略）",\n'
            '  "problem_found": "从轨迹中诊断出的具体问题与原因",\n'
            '  "structured_changes": {\n'
            '    "name": "改进后的策略名称",\n'
            '    "applicable_conditions": "适用条件",\n'
            '    "execution_steps": ["步骤 1：...", "步骤 2：...", "步骤 3：..."],\n'
            '    "taboos_and_counterexamples": ["禁忌 1：...", "禁忌 2：..."],\n'
            '    "change_summary": "核心改动摘要"\n'
            "  },\n"
            '  "expected_benefit": "预期在同类数学问题上的收益",\n'
            '  "claimed_failure_signature": "希望减少的、可由后续轨迹判定的具体失败特征",\n'
            '  "novel_method_evidence": "轨迹中原有解法与新方法的差别及其证据",\n'
            '  "recommendations": [{"type":"diagnostic_experiment | merge_strategy | deprecate_strategy | program_rule | new_tool | answer_error_report", "summary":"建议内容", "risk":"low | medium | high", "evidence_trace_ids":["真实轨迹 ID"]}],\n'
            '  "applicable_scope": "适用的数学分支与题型范围"\n'
            "}"
        )
        user_payload = {
            "failed_trace_count": len(traces),
            "available_math_tools": planner_tool_reference(include_external=False, include_databases=False),
            "failed_traces": [
                {
                    "id": current.id,
                    "agent_id": current.agent_id,
                    "problem_text": current.problem_text,
                    "strategy_selection": current.strategy_selection.model_dump(),
                    "steps": [step.model_dump() for step in current.steps],
                    "final_answer": current.final_answer[:600],
                    "verification_result": current.verification_result.model_dump(),
                    "token_usage": current.token_usage.model_dump(),
                }
                for current in traces
            ],
            "base_strategy": base_strategy.model_dump() if base_strategy else None,
            "target_agent_experiences": [
                {"id": e.id, "type": e.experience_type, "content": e.content, "source_trace_ids": e.source_trace_ids}
                for e in experiences[:5]
            ],
        }

        usage_acc = TokenUsage()
        text = await _complete_with_usage(
            self.router,
            self.model_id,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            usage_acc,
            temperature=0.2,
            json_object=True,
        )
        parsed = _parse_json_object(text)
        if not parsed:
            err_msg = f"反思 Agent 对轨迹 {trace.id} 的输出无法解析为有效 JSON 对象"
            self.strategy_manager.record_reflection_error(
                agent_role=self.role,
                target_id=trace.id,
                error_reason=err_msg,
                raw_model_output=text,
                token_usage=usage_acc,
            )
            raise ReflectionParseError(err_msg, raw_output=text)

        raw_changes = parsed.get("structured_changes")
        if not isinstance(raw_changes, dict):
            err_msg = f"反思 Agent 对轨迹 {trace.id} 的输出缺少 structured_changes 对象"
            self.strategy_manager.record_reflection_error(
                agent_role=self.role,
                target_id=trace.id,
                error_reason=err_msg,
                raw_model_output=text,
                token_usage=usage_acc,
            )
            raise ReflectionParseError(err_msg, raw_output=text)

        name = str(raw_changes.get("name") or "").strip()
        applicable_conditions = str(raw_changes.get("applicable_conditions") or "").strip()
        raw_steps = raw_changes.get("execution_steps")
        exec_steps = [str(x).strip() for x in raw_steps if str(x).strip()] if isinstance(raw_steps, list) else []
        raw_taboos = raw_changes.get("taboos_and_counterexamples")
        taboos = [str(x).strip() for x in raw_taboos if str(x).strip()] if isinstance(raw_taboos, list) else []
        change_summary = str(raw_changes.get("change_summary") or "").strip()

        problem_found = str(parsed.get("problem_found") or "").strip()
        claimed_failure_signature = str(parsed.get("claimed_failure_signature") or "").strip()
        novel_method_evidence = str(parsed.get("novel_method_evidence") or "").strip()
        raw_recommendations = parsed.get("recommendations", [])
        allowed_recommendation_types = {
            "diagnostic_experiment",
            "merge_strategy",
            "deprecate_strategy",
            "program_rule",
            "new_tool",
            "answer_error_report",
        }
        recommendations: list[dict[str, Any]] = []
        invalid_recommendation = False
        if not isinstance(raw_recommendations, list):
            invalid_recommendation = True
        else:
            for item in raw_recommendations:
                if not isinstance(item, dict):
                    invalid_recommendation = True
                    continue
                item_type = str(item.get("type") or "").strip()
                summary = str(item.get("summary") or "").strip()
                risk = str(item.get("risk") or "").strip().lower()
                evidence_ids = [
                    str(trace_id).strip()
                    for trace_id in (item.get("evidence_trace_ids") or [])
                    if str(trace_id).strip()
                ] if isinstance(item.get("evidence_trace_ids") or [], list) else []
                if (
                    item_type not in allowed_recommendation_types
                    or not summary
                    or risk not in {"low", "medium", "high"}
                    or not evidence_ids
                    or any(trace_id not in unique_trace_ids for trace_id in evidence_ids)
                ):
                    invalid_recommendation = True
                    continue
                recommendations.append(
                    {"type": item_type, "summary": summary, "risk": risk, "evidence_trace_ids": evidence_ids}
                )
        expected_benefit = str(parsed.get("expected_benefit") or "").strip()
        applicable_scope = str(parsed.get("applicable_scope") or "").strip()

        missing_fields: list[str] = []
        if not problem_found:
            missing_fields.append("problem_found")
        if not claimed_failure_signature:
            missing_fields.append("claimed_failure_signature")
        if not novel_method_evidence:
            missing_fields.append("novel_method_evidence")
        if invalid_recommendation:
            missing_fields.append("recommendations (invalid structure or evidence provenance)")
        if not name:
            missing_fields.append("structured_changes.name")
        if not applicable_conditions:
            missing_fields.append("structured_changes.applicable_conditions")
        if not exec_steps:
            missing_fields.append("structured_changes.execution_steps")
        if not taboos:
            missing_fields.append("structured_changes.taboos_and_counterexamples")
        if not expected_benefit:
            missing_fields.append("expected_benefit")
        if not applicable_scope:
            missing_fields.append("applicable_scope")

        if missing_fields:
            err_msg = f"反思 Agent 对轨迹 {trace.id} 的输出缺少必填字段：{', '.join(missing_fields)}"
            self.strategy_manager.record_reflection_error(
                agent_role=self.role,
                target_id=trace.id,
                error_reason=err_msg,
                raw_model_output=text,
                token_usage=usage_acc,
            )
            raise ReflectionParseError(err_msg, raw_output=text)

        structured_changes = StructuredStrategyChange(
            name=name,
            applicable_conditions=applicable_conditions,
            execution_steps=exec_steps,
            taboos_and_counterexamples=taboos,
            change_summary=change_summary,
        )

        trace_id_set = set(unique_trace_ids)
        matching_exp_ids = [
            e.id for e in experiences if trace_id_set.intersection(e.source_trace_ids) and e.source_trace_ids
        ]
        proposal = StrategyProposal(
            target_agent=target_agent,
            base_strategy_id=base_strategy.id if base_strategy else None,
            base_strategy_version=base_strategy.version if base_strategy else None,
            problem_found=problem_found,
            claimed_failure_signature=claimed_failure_signature,
            novel_method_evidence=novel_method_evidence,
            recommendations=recommendations,
            evidence_trace_ids=unique_trace_ids,
            experience_ids=matching_exp_ids,
            structured_changes=structured_changes,
            expected_benefit=expected_benefit,
            applicable_scope=applicable_scope,
            status="proposed",
            proposed_by=self.role,
            token_usage=usage_acc,
            raw_model_output=text,
        )
        if register:
            proposal, _ = self.strategy_manager.register_proposal(proposal, caller_role=self.role)
        return proposal


class MetaReflectorAgent:
    """Meta-Reflector Agent ('meta'): reviews proposals, generates ExperimentTasks, and reviews ExperimentReports."""

    role = "meta"

    def __init__(
        self,
        store: SelfImprovementStore,
        strategy_manager: StrategyManager,
        model_router: ModelRouter | None = None,
        model_id: str = "research.meridian",
    ) -> None:
        self.store = store
        self.strategy_manager = strategy_manager
        self.router = model_router or ModelRouter()
        self.model_id = model_id

    async def review_proposal(
        self,
        proposal_id: str,
        *,
        benchmark_count: int = 3,
        runs_per_problem_k: int | None = None,
        experiment_problem_ids: list[str] | None = None,
        baseline_trace_ids: list[str] | None = None,
    ) -> tuple[MetaReviewDecision | None, StrategyProposal, ExperimentTask | None]:
        """Review a StrategyProposal ('approve' / 'reject' / 'request_revision') and generate an ExperimentTask if approved.

        If JSON parsing fails or decision is invalid, transitions the proposal to 'review_failed' without approving.
        """
        proposal = self.store.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(f"StrategyProposal '{proposal_id}' not found.")

        meta_strategies = self.store.list_strategies(
            owner="meta",
            status="active",
            caller_role=self.role,
            include_legacy_demo=False,
        )
        if not experiment_problem_ids:
            raise ValueError("元反思必须收到显式的当前训练/验证划分题目 ID；禁止向策略 Agent 提供全题库。")
        all_benchmarks = load_benchmark_problems()
        benchmark_by_id = {str(problem["id"]): problem for problem in all_benchmarks}
        requested_ids = list(dict.fromkeys(str(pid) for pid in experiment_problem_ids))
        invalid_ids = [pid for pid in requested_ids if pid not in benchmark_by_id]
        if invalid_ids:
            raise ValueError(f"元反思题目 ID 不属于当前划分：{invalid_ids}")
        selected_benchmarks = [benchmark_by_id[pid] for pid in requested_ids]
        benchmark_catalog = [
            {
                "id": p["id"],
                "split": str(p.get("split") or "").strip().lower(),
                "category": p["category"],
                "title": p["title"],
            }
            for p in selected_benchmarks
        ]
        selected_problem_texts = {str(problem.get("problem_text") or "") for problem in selected_benchmarks}
        configured_benchmark_path = os.environ.get("ALEGGO_BENCHMARK_PROBLEMS_FILE", "").strip()
        full_benchmark_path = (
            Path(configured_benchmark_path)
            if configured_benchmark_path
            else Path(__file__).with_name("benchmark_problems.json")
        )
        full_benchmarks = json.loads(full_benchmark_path.read_text(encoding="utf-8"))
        if not isinstance(full_benchmarks, list):
            raise ValueError("benchmark_problems.json must contain a JSON array of problem objects.")
        training_problem_texts = {
            str(problem.get("problem_text") or "")
            for problem in full_benchmarks
            if str(problem.get("split") or "").strip().lower() == "train"
        }
        requested_baseline_ids = list(dict.fromkeys(baseline_trace_ids or proposal.evidence_trace_ids))
        baseline_traces = []
        for trace_id in requested_baseline_ids:
            baseline_trace = self.store.get_trace(trace_id, caller_role=self.role)
            if not baseline_trace:
                if baseline_trace_ids is not None:
                    raise KeyError(f"Baseline TaskTrace '{trace_id}' not found.")
                continue
            if baseline_trace.agent_id != proposal.target_agent:
                raise ValueError(f"Baseline TaskTrace '{trace_id}' belongs to a different execution agent.")
            if baseline_trace.problem_text not in selected_problem_texts:
                raise ValueError(f"Baseline TaskTrace '{trace_id}' is outside the explicitly selected benchmark split.")
            expected_base_version = proposal.base_strategy_version or 1
            if proposal.base_strategy_id and not any(
                item.id == proposal.base_strategy_id and item.version == expected_base_version
                for item in baseline_trace.available_strategy_versions
            ):
                raise ValueError(
                    f"Baseline TaskTrace '{trace_id}' was not generated with the proposal's base strategy version available."
                )
            selected_base_versions = {
                item.version
                for item in baseline_trace.strategy_selection.selections
                if item.strategy_id == proposal.base_strategy_id
            }
            if selected_base_versions and selected_base_versions != {expected_base_version}:
                raise ValueError(
                    f"Baseline TaskTrace '{trace_id}' selected a different version of the proposal's base strategy."
                )
            baseline_traces.append(
                {
                    "trace_id": baseline_trace.id,
                    "task_id": baseline_trace.task_id,
                    "problem_text": baseline_trace.problem_text[:2500],
                    "strategy_selection": baseline_trace.strategy_selection.model_dump(),
                    "available_strategy_versions": [
                        item.model_dump() for item in baseline_trace.available_strategy_versions
                    ],
                    "steps": [
                        {
                            "step_index": step.step_index,
                            "reasoning_summary": step.reasoning_summary[:500],
                            "tool_name": step.tool_name,
                            "tool_input": json.dumps(step.tool_input, ensure_ascii=False)[:2000],
                            "tool_output": step.tool_output[:1000],
                            "tool_ok": step.tool_ok,
                        }
                        for step in baseline_trace.steps[:8]
                    ],
                    "final_answer": baseline_trace.final_answer[:5000],
                    "verification_passed": baseline_trace.verification_result.passed,
                    "verification_output": baseline_trace.verification_result.verifier_output[:1000],
                }
            )
        if not baseline_traces:
            raise ValueError(
                "元反思缺少可核验的改进前基线轨迹；反思用的失败轨迹不能自动充当旧策略基线。"
            )

        # Proposal evidence and the held-out baseline have different roles. Keep
        # both in the review payload: evidence traces show what the proposal
        # actually learned from, while baseline traces show pre-change behavior
        # on the explicitly selected experiment split. Previously only the
        # evidence IDs embedded in proposal text were sent, so Meta-Reflector
        # could not inspect the cited traces and incorrectly requested revision.
        cited_evidence_ids = set(
            re.findall(r"trace_[0-9a-f]+", proposal.novel_method_evidence or "")
        )
        proposal_evidence_ids = list(dict.fromkeys(proposal.evidence_trace_ids))
        unknown_citations = cited_evidence_ids.difference(proposal_evidence_ids)
        if unknown_citations:
            raise ValueError(
                "novel_method_evidence cites traces that are not in evidence_trace_ids: "
                f"{sorted(unknown_citations)}"
            )
        proposal_evidence_traces = []
        evidence_trace_inventory = []
        for trace_id in proposal_evidence_ids:
            evidence_trace = self.store.get_trace(trace_id, caller_role=self.role)
            if not evidence_trace:
                raise KeyError(f"Proposal evidence TaskTrace '{trace_id}' not found.")
            if evidence_trace.agent_id != proposal.target_agent:
                raise ValueError(
                    f"Proposal evidence TaskTrace '{trace_id}' belongs to a different execution agent."
                )
            if evidence_trace.problem_text not in training_problem_texts:
                raise ValueError(
                    f"Proposal evidence TaskTrace '{trace_id}' is not from the frozen training split."
                )
            expected_base_version = proposal.base_strategy_version or 1
            if proposal.base_strategy_id and not any(
                item.id == proposal.base_strategy_id and item.version == expected_base_version
                for item in evidence_trace.available_strategy_versions
            ):
                raise ValueError(
                    f"Proposal evidence TaskTrace '{trace_id}' lacks the proposal's base strategy version."
                )
            evidence_trace_inventory.append(
                {
                    "trace_id": evidence_trace.id,
                    "problem_split": "train",
                    "verification_passed": evidence_trace.verification_result.passed,
                    "is_cited_by_novel_method_evidence": trace_id in cited_evidence_ids,
                }
            )
            if trace_id not in cited_evidence_ids:
                continue
            proposal_evidence_traces.append(
                {
                    "trace_id": evidence_trace.id,
                    "problem_text": evidence_trace.problem_text[:2500],
                    "strategy_selection": evidence_trace.strategy_selection.model_dump(),
                    "available_strategy_versions": [
                        item.model_dump() for item in evidence_trace.available_strategy_versions
                    ],
                    "steps": [
                        {
                            "step_index": step.step_index,
                            "reasoning_summary": step.reasoning_summary[:500],
                            "tool_name": step.tool_name,
                            "tool_input": json.dumps(step.tool_input, ensure_ascii=False)[:1000],
                            "tool_output": step.tool_output[:500],
                            "tool_ok": step.tool_ok,
                        }
                        for step in evidence_trace.steps[:6]
                    ],
                    "final_answer": evidence_trace.final_answer[:3500],
                    "verification_passed": evidence_trace.verification_result.passed,
                    "verification_output": evidence_trace.verification_result.verifier_output[:800],
                }
            )
        if cited_evidence_ids.difference({item["trace_id"] for item in proposal_evidence_traces}):
            raise ValueError("One or more cited proposal evidence traces could not be loaded for review.")
        settings = self.store.get_settings()
        k_val = runs_per_problem_k if runs_per_problem_k is not None else settings.default_runs_per_problem_k

        system_prompt = (
            "你是 A-leggo 研究端的元反思智能体（Meta-Reflector Agent）。\n"
            "请依据你的元反思策略独立审查反思智能体提交的策略改进申请（StrategyProposal），并输出结构化审批意见。\n"
            "审批原则：\n"
            "1. 检查 proposal_evidence_traces 是否真实对应 evidence_trace_ids，并支撑提案所述失败与改进；执行步骤还必须具体可执行、未硬编码单题答案。\n"
            "2. proposal_evidence_traces 是训练集中的提案依据，baseline_traces 是当前显式实验划分中的改进前对照；两者都属于改进前证据。将 novel_method_evidence 与两组轨迹逐项比较，只有方法未在两组轨迹中出现、且新策略确实增加可执行步骤，才能判为 demonstrated_new_method；否则标为 already_present 或 unclear。\n"
            "3. 现在还没有新旧策略对照结果，claimed_failure_reduction_status 必须为 pending_test，不能预言失败已经减少。\n"
            "4. 输出 decision 只能是 'approve'、'reject' 或 'request_revision' 之一；novelty 不是 demonstrated_new_method 时不得 approve。\n"
            f"5. 若 decision 为 'approve'，只能从 benchmark_catalog（当前实验划分）选择至少 {benchmark_count} 道适用题目 ID 组成 benchmark_problem_ids。\n"
            "请严格返回如下 JSON：\n"
            "{\n"
            '  "decision": "approve | reject | request_revision",\n'
            '  "review_comment": "详细审批意见",\n'
            '  "evolution_memo": "全局进化方向备忘",\n'
            '  "novelty_check": "demonstrated_new_method | already_present | unclear",\n'
            '  "claimed_failure_reduction_status": "pending_test",\n'
            '  "benchmark_problem_ids": ["calc_01", "seq_01", "nt_01"]\n'
            "}"
        )
        user_payload = {
            "proposal": proposal.model_dump(),
            "proposal_evidence_trace_inventory": evidence_trace_inventory,
            "proposal_evidence_traces": proposal_evidence_traces,
            "baseline_traces": baseline_traces,
            "meta_active_strategies": [s.to_summary_card() for s in meta_strategies],
            "benchmark_catalog": benchmark_catalog,
        }

        usage_acc = TokenUsage()
        text = await _complete_with_usage(
            self.router,
            self.model_id,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            usage_acc,
            temperature=0.1,
            json_object=True,
        )
        parsed = _parse_json_object(text)
        raw_decision = str(parsed.get("decision") or "").strip().lower()
        review_comment = str(parsed.get("review_comment") or "").strip()
        evolution_memo = str(parsed.get("evolution_memo") or "").strip()
        novelty_check = str(parsed.get("novelty_check") or "").strip()
        failure_reduction_status = str(parsed.get("claimed_failure_reduction_status") or "").strip()

        if (
            not parsed
            or raw_decision not in {"approve", "reject", "request_revision"}
            or not review_comment
            or novelty_check not in {"demonstrated_new_method", "already_present", "unclear"}
            or failure_reduction_status != "pending_test"
        ):
            fail_reason = (
                "元反思审查输出解析失败或字段非法 "
                f"（decision={raw_decision!r}, novelty={novelty_check!r}, "
                f"failure_test={failure_reduction_status!r}, has_comment={bool(review_comment)}）"
            )
            self.strategy_manager.record_reflection_error(
                agent_role=self.role,
                target_id=proposal.id,
                error_reason=fail_reason,
                raw_model_output=text,
                token_usage=usage_acc,
            )
            failed_proposal = self.strategy_manager.transition_proposal_status(
                proposal.id,
                "review_failed",
                actor=self.role,
                rationale=fail_reason,
                review_comment="",
                raw_model_output=text,
            )
            return None, failed_proposal, None

        review_comment = (
            f"新方法核验={novelty_check}；声称失败的对照状态={failure_reduction_status}；{review_comment}"
        )
        high_risk_recommendations = [
            item for item in proposal.recommendations if str(item.get("risk") or "").lower() == "high"
        ]
        if high_risk_recommendations:
            review_comment += (
                f"；另有 {len(high_risk_recommendations)} 项高风险建议仅登记待人工审批，本轮不执行、不视为已批准。"
            )
        if raw_decision == "approve" and novelty_check != "demonstrated_new_method":
            raw_decision = "request_revision"
            review_comment += "；系统保护：缺少新方法证据，已转为 request_revision。"

        valid_pids = {str(p["id"]) for p in all_benchmarks}
        if experiment_problem_ids:
            chosen_pids = [str(pid) for pid in experiment_problem_ids if str(pid) in valid_pids]
        else:
            raw_pids = parsed.get("benchmark_problem_ids")
            chosen_pids = (
                [str(pid) for pid in raw_pids if str(pid) in valid_pids]
                if isinstance(raw_pids, list)
                else []
            )

        if raw_decision == "approve" and not chosen_pids:
            fail_reason = "元反思 decision 为 approve 但未提供有效的 benchmark_problem_ids"
            self.strategy_manager.record_reflection_error(
                agent_role=self.role,
                target_id=proposal.id,
                error_reason=fail_reason,
                raw_model_output=text,
                token_usage=usage_acc,
            )
            failed_proposal = self.strategy_manager.transition_proposal_status(
                proposal.id,
                "review_failed",
                actor=self.role,
                rationale=fail_reason,
                review_comment=review_comment,
                raw_model_output=text,
            )
            return None, failed_proposal, None

        decision = MetaReviewDecision(
            proposal_id=proposal.id,
            decision=raw_decision,  # type: ignore[arg-type]
            review_comment=review_comment,
            evolution_memo=evolution_memo,
            benchmark_problem_ids=chosen_pids,
            runs_per_problem_k=k_val,
            token_usage=usage_acc,
        )
        try:
            updated_proposal, exp_task = self.strategy_manager.apply_meta_review(
                decision,
                reviewer_role=self.role,
                raw_model_output=text,
            )
        except Exception as exc:
            # Preserve the completed model call even if a later state write
            # fails; otherwise its raw decision and token usage disappear with
            # the HTTP error response.
            try:
                self.strategy_manager.record_reflection_error(
                    agent_role=self.role,
                    target_id=proposal.id,
                    error_reason=f"元反思决定未能写入状态机：{exc}",
                    raw_model_output=json.dumps(
                        {"raw_model_output": text, "token_usage": usage_acc.model_dump()},
                        ensure_ascii=False,
                    ),
                    token_usage=usage_acc,
                )
            except Exception:
                pass
            raise
        return decision, updated_proposal, exp_task

    async def review_experiment_report(
        self,
        report_id: str,
        *,
        human_approved: bool | None = None,
    ) -> tuple[ExperimentReport, Strategy]:
        """Independently review an ExperimentReport after StrategyManager's rule judgment and finalize candidate strategy."""
        report = self.store.get_experiment_report(report_id)
        if not report:
            raise KeyError(f"ExperimentReport '{report_id}' not found.")
        proposal = self.store.get_proposal(report.proposal_id)
        evidence_traces = [
            self.store.get_trace(trace_id, caller_role=self.role)
            for trace_id in (proposal.evidence_trace_ids if proposal else [])
        ]
        baseline_trace_summaries = [
            {
                "trace_id": trace.id,
                "problem_text": trace.problem_text[:2500],
                "strategy_selection": trace.strategy_selection.model_dump(),
                "steps": [
                    {
                        "step_index": step.step_index,
                        "reasoning_summary": step.reasoning_summary[:500],
                        "tool_name": step.tool_name,
                        "tool_input": json.dumps(step.tool_input, ensure_ascii=False)[:2000],
                        "tool_output": step.tool_output[:1000],
                        "tool_ok": step.tool_ok,
                    }
                    for step in trace.steps[:8]
                ],
                "final_answer": trace.final_answer[:5000],
                "verification_result": {
                    "passed": trace.verification_result.passed,
                    "verifier_type": trace.verification_result.verifier_type,
                    "verifier_output": trace.verification_result.verifier_output[:1000],
                    "expected": trace.verification_result.expected[:500],
                    "actual": trace.verification_result.actual[:1000],
                },
            }
            for trace in evidence_traces
            if trace
        ]

        def _trial_trace_summary(trace_id: str) -> dict[str, Any] | None:
            trace = self.store.get_trace(trace_id, caller_role=self.role)
            if not trace:
                return None
            return {
                "trace_id": trace.id,
                "task_id": trace.task_id,
                "problem_text": trace.problem_text[:2500],
                "strategy_selection": trace.strategy_selection.model_dump(),
                "steps": [
                    {
                        "step_index": step.step_index,
                        "reasoning_summary": step.reasoning_summary[:500],
                        "tool_name": step.tool_name,
                        "tool_input": json.dumps(step.tool_input, ensure_ascii=False)[:2000],
                        "tool_output": step.tool_output[:1000],
                        "tool_ok": step.tool_ok,
                    }
                    for step in trace.steps[:8]
                ],
                "final_answer": trace.final_answer[:5000],
                "verification_result": {
                    "passed": trace.verification_result.passed,
                    "verifier_type": trace.verification_result.verifier_type,
                    "verifier_output": trace.verification_result.verifier_output[:1000],
                    "expected": trace.verification_result.expected[:500],
                    "actual": trace.verification_result.actual[:1000],
                },
                "token_usage": trace.token_usage.model_dump(),
                "total_elapsed_ms": trace.total_elapsed_ms,
            }

        # The deterministic report already carries all k-run aggregate counts. Give Meta
        # one actual old/new execution trace per problem so it can inspect what the
        # candidate did, while keeping the review prompt bounded and auditable.
        old_trial_trace_summaries = [
            summary
            for row in report.problem_breakdown
            for trace_id in (row.get("old_trace_ids") or [])[:1]
            if (summary := _trial_trace_summary(str(trace_id))) is not None
        ]
        new_trial_trace_summaries = [
            summary
            for row in report.problem_breakdown
            for trace_id in (row.get("new_trace_ids") or [])[:1]
            if (summary := _trial_trace_summary(str(trace_id))) is not None
        ]

        system_prompt = (
            "你是 A-leggo 研究端的元反思智能体（Meta-Reflector Agent）。\n"
            "请独立审查实践智能体完成的新旧策略对照实验报告（ExperimentReport）及 Strategy Manager 的规则判定结果。\n"
            "请结合实验样本量、新旧策略在各题上的通过率表现、Token 消耗增幅、耗时变化以及证据轨迹完整性，独立做出是否批准晋升的判断。\n"
            "必须对照策略声明的 novel_method_evidence 与旧策略轨迹，判断新策略是否教会模型原本不会的方法；还必须查看 claimed_failure_signature 对应题目的 old_passed/new_passed，判断失败是否实际减少。\n"
            "没有可核对的逐题记录、没有新方法证据，或目标失败数没有下降时，不得批准。不得用总通过率掩盖目标失败没有改善。\n"
            "请输出 JSON：\n"
            '{"meta_approved": true 或 false, "novel_method_validated": true 或 false, '
            '"claimed_failure_reduced": true 或 false, "meta_review_comment": "说明新方法是否原本不会、目标失败是否减少，以及样本量和成本变化"}'
        )
        usage_acc = TokenUsage()
        text = await _complete_with_usage(
            self.router,
            self.model_id,
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "experiment_report": {
                                "id": report.id,
                                "task_id": report.task_id,
                                "proposal_id": report.proposal_id,
                                "target_agent": report.target_agent,
                                "strategy_id": report.strategy_id,
                                "old_strategy_version": report.old_strategy_version,
                                "new_strategy_version": report.new_strategy_version,
                                "old_sample_runs": report.rule_judgment.old_sample_runs,
                                "new_sample_runs": report.rule_judgment.new_sample_runs,
                                "old_pass_rate": report.old_pass_rate,
                                "new_pass_rate": report.new_pass_rate,
                                "old_avg_tokens": report.old_avg_tokens,
                                "new_avg_tokens": report.new_avg_tokens,
                                "old_avg_elapsed_ms": report.old_avg_elapsed_ms,
                                "new_avg_elapsed_ms": report.new_avg_elapsed_ms,
                                "rule_judgment": report.rule_judgment.model_dump(),
                                "problem_breakdown": [
                                    {
                                        key: row.get(key)
                                        for key in (
                                            "problem_id",
                                            "title",
                                            "category",
                                            "runs_k",
                                            "old_passed",
                                            "new_passed",
                                            "old_pass_rate",
                                            "new_pass_rate",
                                        )
                                    }
                                    for row in report.problem_breakdown
                                ],
                            },
                            "strategy_proposal": proposal.model_dump() if proposal else None,
                            "baseline_failure_traces": baseline_trace_summaries,
                            "trial_trace_sampling": "one earliest recorded old/new execution per problem; aggregate counts include every run",
                            "old_strategy_trial_trace_samples": old_trial_trace_summaries,
                            "candidate_strategy_trial_trace_samples": new_trial_trace_summaries,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            usage_acc,
            temperature=0.1,
            json_object=True,
        )
        parsed = _parse_json_object(text)
        raw_approved = parsed.get("meta_approved")
        novelty_validated = parsed.get("novel_method_validated")
        model_failure_reduced = parsed.get("claimed_failure_reduced")
        comment = str(parsed.get("meta_review_comment") or "").strip()

        if (
            not parsed
            or not isinstance(raw_approved, bool)
            or not isinstance(novelty_validated, bool)
            or not isinstance(model_failure_reduced, bool)
            or not comment
        ):
            fail_reason = (
                "元反思终审输出解析失败或缺字段 "
                f"（meta_approved={raw_approved!r}, novelty={novelty_validated!r}, "
                f"failure_reduced={model_failure_reduced!r}, has_comment={bool(comment)}），按未批准处理"
            )
            self.strategy_manager.record_reflection_error(
                agent_role=self.role,
                target_id=report.id,
                error_reason=fail_reason,
                raw_model_output=text,
                token_usage=usage_acc,
            )
            proposal = self.store.get_proposal(report.proposal_id)
            if proposal and proposal.status not in {"rejected", "completed", "review_failed"}:
                self.strategy_manager.transition_proposal_status(
                    proposal.id,
                    "review_failed",
                    actor=self.role,
                    rationale=fail_reason,
                    review_comment="",
                    raw_model_output=text,
                )
            final_strategy = self.strategy_manager.finalize_candidate_strategy(
                report.id,
                meta_approved=False,
                meta_review_comment=comment,
                human_approved=human_approved,
                actor="human" if human_approved else "strategy_manager",
                raw_model_output=text,
            )
            updated_report = self.store.get_experiment_report(report.id)
            assert updated_report is not None
            return updated_report, final_strategy

        # The report rows are the exact, explicitly assigned old/new trial set.
        # Proposal evidence can come from train while this report is held-out validation;
        # filtering report rows by the proposal evidence text would silently erase that set.
        target_rows = list(report.problem_breakdown)
        old_target_failures = sum(
            max(0, int(row.get("runs_k") or 0) - int(row.get("old_passed") or 0)) for row in target_rows
        )
        new_target_failures = sum(
            max(0, int(row.get("runs_k") or 0) - int(row.get("new_passed") or 0)) for row in target_rows
        )
        measured_failure_reduced = bool(target_rows) and new_target_failures < old_target_failures
        comment += (
            f"；系统逐题复核：证据题数={len(target_rows)}，旧策略失败={old_target_failures}，"
            f"新策略失败={new_target_failures}，目标失败减少={measured_failure_reduced}。"
        )
        raw_approved = bool(
            raw_approved and novelty_validated and model_failure_reduced and measured_failure_reduced
        )

        meta_approved = bool(report.rule_judgment.passed and raw_approved)
        final_strategy = self.strategy_manager.finalize_candidate_strategy(
            report.id,
            meta_approved=meta_approved,
            meta_review_comment=comment,
            human_approved=human_approved,
            actor="human" if human_approved else "strategy_manager",
            raw_model_output=text,
        )
        updated_report = self.store.get_experiment_report(report.id)
        assert updated_report is not None
        return updated_report, final_strategy


class PractitionerAgent:
    """Practitioner Agent ('practitioner'): executes controlled old-vs-new strategy benchmark experiments."""

    role = "practitioner"

    def __init__(
        self,
        store: SelfImprovementStore,
        strategy_manager: StrategyManager,
        workspace_root: Path,
        model_router: ModelRouter | None = None,
        model_id: str = "research.meridian",
    ) -> None:
        self.store = store
        self.strategy_manager = strategy_manager
        self.workspace_root = Path(workspace_root)
        self.router = model_router or ModelRouter()
        self.model_id = model_id

    async def run_experiment(
        self,
        task_id: str,
        *,
        temperature: float = 0.2,
    ) -> ExperimentReport:
        """Run old vs. new strategy on benchmark problems k times each under identical parameters, then hand off to StrategyManager."""
        task = self.store.get_experiment_task(task_id)
        if not task:
            raise KeyError(f"ExperimentTask '{task_id}' not found.")

        task.status = "running"
        self.store.save_experiment_task(task, caller_role=self.role)

        problems = select_benchmark_subset(task.benchmark_problem_ids)
        if not problems:
            raise ValueError(f"ExperimentTask '{task_id}' has no valid benchmark_problem_ids: {task.benchmark_problem_ids}")

        new_strat = self.store.get_strategy(task.strategy_id, task.new_strategy_version, caller_role="strategy_manager")
        if not new_strat:
            raise KeyError(f"New strategy {task.strategy_id} v{task.new_strategy_version} not found.")

        old_strat: Strategy | None = None
        if task.old_strategy_version is not None:
            old_strat = self.store.get_strategy(task.strategy_id, task.old_strategy_version, caller_role="strategy_manager")

        exec_agent_id = task.target_agent if task.target_agent in {"agent_a", "agent_b"} else "agent_a"
        runner = ExecutionAgent(
            agent_id=exec_agent_id,
            store=self.store,
            workspace_root=self.workspace_root,
            model_router=self.router,
            model_id=self.model_id,
        )

        old_traces: list[TaskTrace] = []
        new_traces: list[TaskTrace] = []
        breakdown: list[dict[str, Any]] = []

        existing_traces = {
            t.task_id: t
            for t in self.store.list_traces(agent_id=exec_agent_id, limit=500, caller_role="strategy_manager")
            if t.task_id.startswith(f"{task.id}_")
        }

        k = max(1, int(task.runs_per_problem_k))
        sem = asyncio.Semaphore(6)
        old_override = [old_strat] if old_strat else []

        async def _get_or_run(run_tid: str, ptext: str, vspec: dict[str, Any], override: list[Strategy]) -> TaskTrace:
            if run_tid in existing_traces:
                return existing_traces[run_tid]
            async with sem:
                return await runner.solve_task(
                    ptext,
                    task_id=run_tid,
                    verifier_spec=vspec,
                    strategy_override=override,
                    record_experience=False,
                    temperature=temperature,
                )

        pair_jobs: list[tuple[dict[str, Any], int, Any, Any]] = []
        for prob in problems:
            pid = str(prob["id"])
            ptext = str(prob["problem_text"])
            vspec = prob["verifier"]
            for run_idx in range(1, k + 1):
                old_tid = f"{task.id}_{pid}_old_r{run_idx}"
                new_tid = f"{task.id}_{pid}_new_r{run_idx}"
                pair_jobs.append(
                    (
                        prob,
                        run_idx,
                        _get_or_run(old_tid, ptext, vspec, old_override),
                        _get_or_run(new_tid, ptext, vspec, [new_strat]),
                    )
                )

        old_results = await asyncio.gather(*[job[2] for job in pair_jobs])
        new_results = await asyncio.gather(*[job[3] for job in pair_jobs])

        idx_cursor = 0
        for prob in problems:
            pid = str(prob["id"])
            prob_old_passed = 0
            prob_new_passed = 0
            prob_old_tids: list[str] = []
            prob_new_tids: list[str] = []
            for _ in range(1, k + 1):
                otrace = old_results[idx_cursor]
                ntrace = new_results[idx_cursor]
                idx_cursor += 1
                old_traces.append(otrace)
                prob_old_tids.append(otrace.id)
                if otrace.verification_result.passed:
                    prob_old_passed += 1
                new_traces.append(ntrace)
                prob_new_tids.append(ntrace.id)
                if ntrace.verification_result.passed:
                    prob_new_passed += 1

            breakdown.append(
                {
                    "problem_id": pid,
                    "title": prob.get("title", pid),
                    "category": prob.get("category", ""),
                    "runs_k": k,
                    "old_passed": prob_old_passed,
                    "new_passed": prob_new_passed,
                    "old_pass_rate": round(prob_old_passed / k, 4),
                    "new_pass_rate": round(prob_new_passed / k, 4),
                    "old_trace_ids": prob_old_tids,
                    "new_trace_ids": prob_new_tids,
                }
            )

        total_runs = max(1, len(old_traces))
        old_pass_rate = round(sum(1 for t in old_traces if t.verification_result.passed) / total_runs, 4)
        new_pass_rate = round(sum(1 for t in new_traces if t.verification_result.passed) / total_runs, 4)
        old_avg_tokens = round(sum(t.token_usage.total_tokens for t in old_traces) / total_runs, 2)
        new_avg_tokens = round(sum(t.token_usage.total_tokens for t in new_traces) / total_runs, 2)
        old_avg_elapsed = round(sum(t.total_elapsed_ms for t in old_traces) / total_runs, 2)
        new_avg_elapsed = round(sum(t.total_elapsed_ms for t in new_traces) / total_runs, 2)

        # Hand off to StrategyManager for deterministic rule judgment
        report = self.strategy_manager.judge_experiment(
            task,
            old_run_trace_ids=[t.id for t in old_traces],
            new_run_trace_ids=[t.id for t in new_traces],
            old_pass_rate=old_pass_rate,
            new_pass_rate=new_pass_rate,
            old_avg_tokens=old_avg_tokens,
            new_avg_tokens=new_avg_tokens,
            old_avg_elapsed_ms=old_avg_elapsed,
            new_avg_elapsed_ms=new_avg_elapsed,
            problem_breakdown=breakdown,
        )
        return report
