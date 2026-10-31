"""Closed-loop orchestrator for A-leggo Research self-improvement:
Execute -> Record -> Reflect -> Propose -> Review -> Experiment -> Verify -> Update.

Enforces:
- Evaluates the full benchmark problem set (or specified subset) and ONLY reflects on genuinely failed traces.
- If no trace failed, honestly reports '本轮无失败轨迹，无需进化' and finishes.
- Experiment problem set consists of all failed problems + an equal number of passed problems (to test for regression), with k from settings.
- Excludes legacy_demo items by default.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from aleggo.study.model_registry import ModelRouter
from aleggo.study.self_improvement.agents import (
    ExecutionAgent,
    MetaReflectorAgent,
    PractitionerAgent,
    ReflectionParseError,
    ReflectorAgent,
)
from aleggo.study.self_improvement.benchmark import load_benchmark_problems, select_benchmark_subset
from aleggo.study.self_improvement.models import (
    StrategyVersionRef,
    TaskTrace,
    TokenUsage,
    TraceStep,
    VerificationResult,
    new_id,
    utc_now_iso,
)
from aleggo.study.self_improvement.store import SelfImprovementStore
from aleggo.study.self_improvement.strategy_manager import StrategyManager


class SelfImprovementOrchestrator:
    """High-level coordinator wiring Execution Agents A/B, Reflector, Meta-Reflector, Practitioner, and StrategyManager."""

    def __init__(
        self,
        store: SelfImprovementStore,
        workspace_root: Path,
        model_router: ModelRouter | None = None,
        model_id: str = "research.codex_luna",
    ) -> None:
        self.store = store
        self.workspace_root = Path(workspace_root)
        self.router = model_router or ModelRouter()
        self.model_id = model_id
        self.strategy_manager = StrategyManager(store)
        self.agent_a = ExecutionAgent("agent_a", store, self.workspace_root, self.router, model_id)
        self.agent_b = ExecutionAgent("agent_b", store, self.workspace_root, self.router, model_id)
        self.reflector = ReflectorAgent(store, self.strategy_manager, self.router, model_id)
        self.meta_reflector = MetaReflectorAgent(store, self.strategy_manager, self.router, model_id)
        self.practitioner = PractitionerAgent(store, self.strategy_manager, self.workspace_root, self.router, model_id)
        self.active_run_progress: dict[str, Any] = {
            "running": False,
            "run_id": "",
            "stage": "idle",
            "current_stage": "idle",
            "message": "",
            "started_at": None,
            "finished_at": None,
            "events": [],
            "result": None,
            "last_result": None,
            "error": None,
        }

    def get_execution_agent(self, agent_id: str) -> ExecutionAgent:
        if agent_id == "agent_a":
            return self.agent_a
        if agent_id == "agent_b":
            return self.agent_b
        raise ValueError(f"Unknown execution agent_id: {agent_id}")

    async def run_evolution_cycle(
        self,
        *,
        agent_id: str = "agent_a",
        initial_problem_ids: list[str] | None = None,
        benchmark_count: int | None = None,
        runs_per_problem_k: int | None = None,
        progress_callback: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run one complete self-improvement closed loop with real model and real tool execution.

        1. Runs Execution Agent across the full benchmark problem set (or specified initial_problem_ids).
        2. Only triggers reflection on genuinely failed traces (verification_result.passed == False).
        3. If there are no failed traces, reports '本轮无失败轨迹，无需进化' and terminates cleanly.
        4. Constructs experiment problem set as: all failed problems + an equal number of passed problems (for regression check), with k from settings.
        """
        run_id = new_id("evo")
        started_at = utc_now_iso()
        events: list[dict[str, Any]] = []

        def emit(stage: str, message: str, detail: dict[str, Any] | None = None) -> None:
            now_ts = utc_now_iso()
            entry = {
                "timestamp": now_ts,
                "stage": stage,
                "message": message,
                "detail": detail or {},
            }
            events.append(entry)
            is_running = stage not in {"completed", "failed"}
            self.active_run_progress = {
                "running": is_running,
                "run_id": run_id,
                "stage": stage,
                "current_stage": stage,
                "message": message,
                "started_at": started_at,
                "finished_at": None if is_running else now_ts,
                "events": list(events),
                "result": None,
                "last_result": None,
                "error": message if stage == "failed" else None,
            }
            if progress_callback:
                progress_callback(stage, message, detail or {})

        agent = self.get_execution_agent(agent_id)
        if not initial_problem_ids:
            raise ValueError(
                "必须明确指定当前实验划分中的题目 ID；为保护验证题和封存考试题，禁止默认读取全题库。"
            )
        all_benchmarks = load_benchmark_problems()
        by_id = {str(problem["id"]): problem for problem in all_benchmarks}
        requested_ids = list(dict.fromkeys(str(pid) for pid in initial_problem_ids))
        unknown_ids = [pid for pid in requested_ids if pid not in by_id]
        if unknown_ids:
            raise ValueError(f"题目 ID 不属于当前实验划分：{unknown_ids}")
        problems = [by_id[pid] for pid in requested_ids]

        settings = self.store.get_settings()
        k_val = runs_per_problem_k if runs_per_problem_k is not None else settings.default_runs_per_problem_k

        # 1. Execute benchmark problems & Record TaskTrace
        eval_pairs: list[tuple[dict[str, Any], TaskTrace]] = []
        total_probs = len(problems)
        for idx, prob in enumerate(problems, start=1):
            pid = str(prob["id"])
            emit(
                "executing",
                f"阶段 1/8 [执行 ({idx}/{total_probs})]：{agent_id} 正在解答题目 `{pid}` ({prob['title']})",
                {"problem_id": pid, "index": idx, "total": total_probs},
            )
            trace = await agent.solve_task(
                prob["problem_text"],
                task_id=f"{run_id}_eval_{pid}",
                verifier_spec=prob["verifier"],
            )
            eval_pairs.append((prob, trace))
            emit(
                "recording",
                f"阶段 2/8 [记录 ({idx}/{total_probs})]：已写入只追加轨迹 `{trace.id}`（题目={pid}，验证通过={trace.verification_result.passed}，耗时={trace.total_elapsed_ms}ms，Tokens={trace.token_usage.total_tokens}）",
                {"trace_id": trace.id, "problem_id": pid, "passed": trace.verification_result.passed},
            )

        failed_pairs = [(p, t) for p, t in eval_pairs if not t.verification_result.passed]
        passed_pairs = [(p, t) for p, t in eval_pairs if t.verification_result.passed]

        if not failed_pairs:
            no_fail_msg = "本轮无失败轨迹，无需进化"
            summary = {
                "run_id": run_id,
                "agent_id": agent_id,
                "status": "no_failure_no_evolution",
                "message": no_fail_msg,
                "evaluated_traces": [t.model_dump() for _, t in eval_pairs],
                "failed_traces": [],
                "initial_trace": eval_pairs[0][1].model_dump() if eval_pairs else None,
                "proposal": None,
                "meta_decision": None,
                "experiment_task": None,
                "experiment_report": None,
                "final_strategy": None,
                "follow_up_trace": None,
                "compressed_experiences_count": 0,
                "events": events,
            }
            emit(
                "completed",
                no_fail_msg,
                {
                    "run_id": run_id,
                    "evaluated_count": len(eval_pairs),
                    "passed_count": len(passed_pairs),
                    "failed_count": 0,
                },
            )
            self.active_run_progress["result"] = summary
            self.active_run_progress["last_result"] = summary
            return summary

        target_failed_prob, target_failed_trace = failed_pairs[0]
        failed_pids = [str(p["id"]) for p, _ in failed_pairs]
        regression_pids = [str(p["id"]) for p, _ in passed_pairs[: len(failed_pids)]]
        experiment_pids = list(dict.fromkeys(failed_pids + regression_pids))
        min_group_runs = settings.min_sample_runs_per_group
        if experiment_pids:
            required_k = (min_group_runs + len(experiment_pids) - 1) // len(experiment_pids)
            k_val = max(k_val, required_k)
        if k_val > 20:
            fail_msg = (
                "阶段 5/8 [样本量不足]：当前失败/回归题数无法在既有 k 上限内满足每组最少 "
                f"{min_group_runs} 次运行；未降低样本要求，也未启动晋升实验。"
            )
            emit("failed", fail_msg, {"problem_ids": experiment_pids, "runs_per_problem_k": k_val})
            summary = {
                "run_id": run_id,
                "agent_id": agent_id,
                "status": "insufficient_sample_capacity",
                "message": fail_msg,
                "evaluated_traces": [t.model_dump() for _, t in eval_pairs],
                "failed_traces": [t.model_dump() for _, t in failed_pairs],
                "proposal": None,
                "meta_decision": None,
                "experiment_task": None,
                "experiment_report": None,
                "events": events,
            }
            self.active_run_progress["result"] = summary
            self.active_run_progress["last_result"] = summary
            return summary
        emit(
            "sample_sizing",
            f"样本量计划：{len(experiment_pids)} 道对照题 × k={k_val}，每组至少 {len(experiment_pids) * k_val} 次运行（固定下限 {min_group_runs}）。",
            {
                "experiment_problem_ids": experiment_pids,
                "problem_count": len(experiment_pids),
                "runs_per_problem_k": k_val,
                "runs_per_group": len(experiment_pids) * k_val,
                "minimum_runs_per_group": min_group_runs,
            },
        )

        # 2. Reflector Agent analyzes genuinely failed trace and submits StrategyProposal
        emit(
            "reflecting",
            f"阶段 3/8 [批量反思]：共发现 {len(failed_pairs)} 条失败轨迹；反思 Agent 将一次分析整批失败记录。",
            {"trace_ids": [t.id for _, t in failed_pairs], "failed_problem_ids": failed_pids},
        )
        try:
            proposal = await self.reflector.reflect_on_traces(
                [failed_trace.id for _, failed_trace in failed_pairs],
                register=True,
            )
        except ReflectionParseError as exc:
            fail_msg = f"阶段 3/8 [反思解析失败]：{exc}"
            summary = {
                "run_id": run_id,
                "agent_id": agent_id,
                "status": "reflection_parse_failed",
                "message": str(exc),
                "raw_model_output": exc.raw_output,
                "evaluated_traces": [t.model_dump() for _, t in eval_pairs],
                "failed_traces": [t.model_dump() for _, t in failed_pairs],
                "initial_trace": target_failed_trace.model_dump(),
                "proposal": None,
                "meta_decision": None,
                "experiment_task": None,
                "experiment_report": None,
                "final_strategy": None,
                "events": events,
            }
            emit("failed", fail_msg, {"trace_id": target_failed_trace.id})
            self.active_run_progress["result"] = summary
            self.active_run_progress["last_result"] = summary
            return summary

        emit(
            "proposing",
            f"阶段 4/8 [上报]：生成提案 `{proposal.id}`，目标策略 `{proposal.new_strategy_id}:v{proposal.new_strategy_version}`（问题诊断：{proposal.problem_found[:100]}）",
            {
                "proposal_id": proposal.id,
                "strategy_id": proposal.new_strategy_id,
                "new_version": proposal.new_strategy_version,
            },
        )

        # 3. Meta-Reflector reviews proposal & creates ExperimentTask
        emit("reviewing", f"阶段 5/8 [审批]：元反思 Agent 正在审查提案 `{proposal.id}`", {"proposal_id": proposal.id})
        decision, reviewed_proposal, exp_task = await self.meta_reflector.review_proposal(
            proposal.id,
            benchmark_count=len(experiment_pids) or (benchmark_count or 2),
            runs_per_problem_k=k_val,
            experiment_problem_ids=experiment_pids,
            baseline_trace_ids=[trace.id for _, trace in eval_pairs],
        )
        if decision is None:
            emit(
                "failed",
                f"阶段 5/8 [审批失败]：元反思输出解析失败或 decision 非法，提案 `{reviewed_proposal.id}` 状态记为 `review_failed`",
                {"proposal_id": reviewed_proposal.id, "status": reviewed_proposal.status},
            )
            summary = {
                "run_id": run_id,
                "agent_id": agent_id,
                "status": "review_failed",
                "message": "元反思审查解析失败或 decision 非法",
                "evaluated_traces": [t.model_dump() for _, t in eval_pairs],
                "failed_traces": [t.model_dump() for _, t in failed_pairs],
                "initial_trace": target_failed_trace.model_dump(),
                "proposal": reviewed_proposal.model_dump(),
                "meta_decision": None,
                "experiment_task": None,
                "experiment_report": None,
                "final_strategy": None,
                "events": events,
            }
            self.active_run_progress["result"] = summary
            self.active_run_progress["last_result"] = summary
            return summary

        emit(
            "reviewing",
            f"阶段 5/8 [审批完成]：元反思决定 `{decision.decision}`（{decision.review_comment[:120]}）",
            {
                "decision": decision.decision,
                "experiment_task_id": exp_task.id if exp_task else None,
            },
        )

        report = None
        final_strategy = None
        follow_up_trace = None

        if exp_task is not None:
            # 4. Practitioner Agent runs benchmark experiment (old vs. new strategy, k runs per problem)
            emit(
                "practicing",
                f"阶段 6/8 [实践]：实践 Agent 正在执行对照实验 `{exp_task.id}`（失败题={failed_pids}，退化检测题={regression_pids}，k={exp_task.runs_per_problem_k}）",
                {
                    "experiment_task_id": exp_task.id,
                    "problems": exp_task.benchmark_problem_ids,
                    "failed_problems": failed_pids,
                    "regression_problems": regression_pids,
                    "k": exp_task.runs_per_problem_k,
                },
            )
            report = await self.practitioner.run_experiment(exp_task.id)
            emit(
                "verifying",
                f"阶段 7/8 [验证]：报告 `{report.id}` — 旧策略通过率 {report.old_pass_rate:.1%} vs 新策略通过率 {report.new_pass_rate:.1%}，规则判定：{'通过' if report.rule_judgment.passed else '未通过'}（{report.rule_judgment.reason}）",
                {"report_id": report.id, "rule_judgment": report.rule_judgment.model_dump()},
            )

            # 5. Meta-Reflector final review & StrategyManager update
            emit("updating", f"阶段 8/8 [更新]：元反思正在独立终审实验报告 `{report.id}` 并更新策略状态", {"report_id": report.id})
            report, final_strategy = await self.meta_reflector.review_experiment_report(report.id)
            emit(
                "updating",
                f"阶段 8/8 [策略状态更新]：策略 `{final_strategy.id}:v{final_strategy.version}` 当前状态为 `{final_strategy.status}`",
                {"strategy_id": final_strategy.id, "version": final_strategy.version, "status": final_strategy.status},
            )

        # Trigger basic memory compression if duplicate experiences exist
        compressed = self.store.compress_experiences(agent_id, caller_role=agent_id)

        summary = {
            "run_id": run_id,
            "agent_id": agent_id,
            "status": "completed",
            "evaluated_traces": [t.model_dump() for _, t in eval_pairs],
            "failed_traces": [t.model_dump() for _, t in failed_pairs],
            "initial_trace": target_failed_trace.model_dump(),
            "proposal": (self.store.get_proposal(reviewed_proposal.id) or reviewed_proposal).model_dump(),
            "meta_decision": decision.model_dump(),
            "experiment_task": exp_task.model_dump() if exp_task else None,
            "experiment_report": report.model_dump() if report else None,
            "final_strategy": final_strategy.model_dump() if final_strategy else None,
            "follow_up_trace": follow_up_trace.model_dump() if follow_up_trace else None,
            "compressed_experiences_count": len(compressed),
            "events": events,
        }
        emit("completed", f"自我进化闭环 `{run_id}` 运行完成！", {"run_id": run_id})
        self.active_run_progress["result"] = summary
        self.active_run_progress["last_result"] = summary
        return summary

    def dashboard_snapshot(self, *, include_legacy_demo: bool = False) -> dict[str, Any]:
        """Return complete state for the Frontend Self-Improvement Visualization Panel."""
        all_strategies = self.store.list_strategies(
            caller_role="strategy_manager",
            include_legacy_demo=include_legacy_demo,
        )
        by_owner: dict[str, dict[str, list[dict[str, Any]]]] = {"agent_a": {}, "agent_b": {}, "meta": {}}
        for s in all_strategies:
            owner_bucket = by_owner.setdefault(s.owner, {})
            owner_bucket.setdefault(s.id, []).append(s.model_dump())

        strategy_trees: dict[str, list[dict[str, Any]]] = {"agent_a": [], "agent_b": [], "meta": []}
        for owner_key, strat_map in by_owner.items():
            items: list[dict[str, Any]] = []
            for sid, ver_list in strat_map.items():
                sorted_vers = sorted(ver_list, key=lambda v: int(v.get("version") or 1))
                active_ver = next((int(v["version"]) for v in reversed(sorted_vers) if v.get("status") == "active"), None)
                latest = sorted_vers[-1]
                items.append(
                    {
                        "strategy_id": sid,
                        "name": str(latest.get("name") or sid),
                        "owner": owner_key,
                        "active_version": active_ver,
                        "latest_version": int(latest.get("version") or 1),
                        "versions": sorted_vers,
                    }
                )
            strategy_trees[owner_key] = items

        proposals = [p.model_dump() for p in self.store.list_proposals(include_legacy_demo=include_legacy_demo)]
        tasks = [t.model_dump() for t in self.store.list_experiment_tasks(include_legacy_demo=include_legacy_demo)]
        reports = [r.model_dump() for r in self.store.list_experiment_reports(include_legacy_demo=include_legacy_demo)]
        exp_a = [e.model_dump() for e in self.store.list_experiences("agent_a", caller_role="strategy_manager", limit=50)]
        exp_b = [e.model_dump() for e in self.store.list_experiences("agent_b", caller_role="strategy_manager", limit=50)]
        traces = [
            t.model_dump()
            for t in self.store.list_traces(
                limit=80,
                caller_role="strategy_manager",
                include_legacy_demo=include_legacy_demo,
            )
        ]
        audits = [a.model_dump() for a in self.store.list_audit_logs(limit=150)]
        settings = self.store.get_settings().model_dump()
        benchmarks = load_benchmark_problems()

        return {
            "settings": settings,
            "strategy_tree": by_owner,
            "strategy_trees": strategy_trees,
            "strategies": [s.model_dump() for s in all_strategies],
            "proposals": proposals,
            "experiment_tasks": tasks,
            "experiment_reports": reports,
            "experiences": {
                "agent_a": exp_a,
                "agent_b": exp_b,
                "meta": [],
            },
            "traces": traces,
            "audit_logs": audits,
            "benchmark_problems": [
                {
                    "id": p["id"],
                    "category": p["category"],
                    "difficulty": p["difficulty"],
                    "title": p["title"],
                    "problem_text": p["problem_text"],
                    "verifier": p["verifier"],
                    "verifier_type": p["verifier"]["type"],
                    "expected": p["verifier"]["expected"],
                }
                for p in benchmarks
            ],
            "summary": {
                "total_traces": len(traces),
                "agent_a_traces": sum(1 for t in traces if t.get("agent_id") == "agent_a"),
                "agent_b_traces": sum(1 for t in traces if t.get("agent_id") == "agent_b"),
                "total_proposals": len(proposals),
                "total_experiments": len(reports),
                "total_audit_logs": len(audits),
            },
            "progress": self.active_run_progress,
            "active_run_progress": self.active_run_progress,
        }


_DEFAULT_ORCHESTRATOR: SelfImprovementOrchestrator | None = None
_DEFAULT_DB_PATH: Path | None = None


def get_self_improvement_orchestrator(db_path: Path | None = None) -> SelfImprovementOrchestrator:
    global _DEFAULT_ORCHESTRATOR, _DEFAULT_DB_PATH
    if db_path is None:
        home = Path(os.environ.get("ALEGGO_RESEARCH_HOME") or (Path.cwd() / "data"))
        db_path = home / "projects" / "aleggo.sqlite3"
    resolved = db_path.resolve()
    if _DEFAULT_ORCHESTRATOR is None or _DEFAULT_DB_PATH != resolved:
        store = SelfImprovementStore(resolved)
        model_id = os.environ.get("ALEGGO_SELF_IMPROVEMENT_MODEL_ID", "research.codex_luna").strip()
        _DEFAULT_ORCHESTRATOR = SelfImprovementOrchestrator(store, resolved.parent, model_id=model_id)
        _DEFAULT_DB_PATH = resolved
    return _DEFAULT_ORCHESTRATOR


def record_collaborator_trace(
    *,
    db_path: Path,
    queue_id: str,
    problem_text: str,
    tool_results: list[dict[str, Any]],
    final_reply: str,
    usage_summary: dict[str, Any],
    elapsed_ms: int,
    completed: bool,
) -> TaskTrace | None:
    """Hook for existing ResearchCollaborator._discuss to record append-only TaskTrace."""
    try:
        store = SelfImprovementStore(db_path)
        active_strats = store.list_strategies(owner="agent_a", status="active", caller_role="strategy_manager")
        steps = [
            TraceStep(
                step_index=idx + 1,
                reasoning_summary=f"讨论工具调用 {item.get('tool', '')}",
                tool_name=str(item.get("tool") or ""),
                tool_input=item.get("input") if isinstance(item.get("input"), dict) else {},
                tool_output=str(item.get("output") or item.get("error") or "")[:2000],
                tool_ok=bool(item.get("ok")),
                elapsed_ms=int(item.get("elapsed_ms") or 0),
            )
            for idx, item in enumerate(tool_results)
        ]
        trace = TaskTrace(
            id=f"trace_disc_{queue_id[:16]}",
            agent_id="agent_a",
            task_id=f"discussion:{queue_id}",
            problem_text=problem_text,
            available_strategy_versions=[
                StrategyVersionRef(id=s.id, name=s.name, version=s.version, status=s.status)
                for s in active_strats
            ],
            steps=steps,
            final_answer=final_reply[:4000],
            verification_result=VerificationResult(
                passed=completed,
                verifier_type="discussion_completion",
                verifier_output="研究讨论已完成" if completed else "研究讨论未完成",
            ),
            token_usage=TokenUsage(
                prompt_tokens=int(usage_summary.get("promptTokens") or 0),
                completion_tokens=int(usage_summary.get("completionTokens") or 0),
                total_tokens=int(usage_summary.get("totalTokens") or 0),
                model_calls=int(usage_summary.get("modelCalls") or 0),
            ),
            total_elapsed_ms=elapsed_ms,
        )
        return store.append_trace(trace, caller_role="agent_a")
    except Exception:
        return None
