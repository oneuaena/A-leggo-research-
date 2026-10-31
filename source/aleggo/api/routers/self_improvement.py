"""FastAPI router for the A-leggo Research Self-Improvement System."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from aleggo.study.self_improvement import (
    IllegalStateTransitionError,
    MetaReviewDecision,
    PermissionViolationError,
    ReflectionParseError,
    SelfImprovementSettings,
    get_benchmark_problem,
    get_self_improvement_orchestrator,
)

router = APIRouter(prefix="/self-improvement", tags=["self-improvement"])

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


class SettingsUpdateRequest(BaseModel):
    require_human_approval: bool | None = None
    default_runs_per_problem_k: int | None = Field(default=None, ge=1, le=20)
    min_pass_rate_delta: float | None = None
    max_token_increase_ratio: float | None = None
    min_sample_runs_per_group: int | None = Field(default=None, ge=1, le=500)
    allow_token_exemption_on_high_pass_gain: bool | None = None


class RunCycleRequest(BaseModel):
    agent_id: Literal["agent_a", "agent_b"] = "agent_a"
    problem_id: str | None = None
    problem_text: str | None = None
    initial_problem_ids: list[str] | None = None
    benchmark_count: int | None = Field(default=None, ge=1, le=50)
    runs_per_problem_k: int | None = Field(default=None, ge=1, le=20)
    wait: bool = False


class SolveProblemRequest(BaseModel):
    agent_id: Literal["agent_a", "agent_b"] = "agent_a"
    problem_id: str | None = None
    problem_text: str | None = None
    expected_answer: str | None = None
    verifier_type: Literal["numeric", "sympy_equiv"] = "numeric"


class ReflectBatchRequest(BaseModel):
    trace_ids: list[str] = Field(min_length=1, max_length=100)


class ProposalReviewRequest(BaseModel):
    decision: Literal["approve", "reject", "request_revision"]
    review_comment: str = ""
    benchmark_problem_ids: list[str] = Field(default_factory=list)
    baseline_trace_ids: list[str] = Field(default_factory=list)
    runs_per_problem_k: int | None = Field(default=None, ge=1, le=20)
    reviewer_role: Literal["meta", "human"] = "human"


class FinalizeCandidateRequest(BaseModel):
    approved: bool | None = None
    human_approved: bool = True
    comment: str = "人工审批确认通过，准予激活为正式策略。"


class RollbackRequest(BaseModel):
    strategy_id: str | None = None
    version: int | None = None
    actor: str = "human"
    rationale: str = "人工触发一键回滚至上一版本"


@router.get("/dashboard")
async def get_dashboard(include_legacy_demo: bool = False) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    return orch.dashboard_snapshot(include_legacy_demo=include_legacy_demo)


@router.get("/progress")
async def get_progress() -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    return orch.active_run_progress


@router.get("/traces/{trace_id}")
async def get_trace_detail(trace_id: str) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    trace = orch.store.get_trace(trace_id, caller_role="strategy_manager")
    if not trace:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found.")
    return trace.model_dump()


@router.put("/settings")
async def update_settings(payload: SettingsUpdateRequest) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    current = orch.store.get_settings()
    if payload.require_human_approval is not None:
        current.require_human_approval = payload.require_human_approval
    if payload.default_runs_per_problem_k is not None:
        current.default_runs_per_problem_k = payload.default_runs_per_problem_k
    if payload.min_pass_rate_delta is not None:
        current.min_pass_rate_delta = payload.min_pass_rate_delta
    if payload.max_token_increase_ratio is not None:
        current.max_token_increase_ratio = payload.max_token_increase_ratio
    if payload.min_sample_runs_per_group is not None:
        current.min_sample_runs_per_group = payload.min_sample_runs_per_group
    if payload.allow_token_exemption_on_high_pass_gain is not None:
        current.allow_token_exemption_on_high_pass_gain = payload.allow_token_exemption_on_high_pass_gain
    saved = orch.store.save_settings(current, who="human")
    return saved.model_dump()


@router.post("/run-cycle")
@router.post("/evolve")
async def run_evolution_cycle(payload: RunCycleRequest) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    if orch.active_run_progress.get("running"):
        return {
            "started": False,
            "message": "当前已有一轮进化正在运行中",
            "progress": orch.active_run_progress,
        }

    init_pids = payload.initial_problem_ids
    if not init_pids and payload.problem_id:
        init_pids = [payload.problem_id]

    async def _runner() -> dict[str, Any]:
        try:
            return await orch.run_evolution_cycle(
                agent_id=payload.agent_id,
                initial_problem_ids=init_pids,
                benchmark_count=payload.benchmark_count,
                runs_per_problem_k=payload.runs_per_problem_k,
            )
        except Exception as exc:
            orch.active_run_progress["running"] = False
            orch.active_run_progress["current_stage"] = "failed"
            orch.active_run_progress["events"].append(
                {"stage": "failed", "message": f"进化循环异常：{exc}", "detail": {}}
            )
            raise

    if payload.wait:
        result = await _runner()
        return {"started": True, "completed": True, "result": result}

    task = asyncio.create_task(_runner())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    await asyncio.sleep(0.05)
    return {
        "started": True,
        "completed": False,
        "progress": orch.active_run_progress,
    }


@router.post("/solve")
async def solve_problem(payload: SolveProblemRequest) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    agent = orch.get_execution_agent(payload.agent_id)
    if payload.problem_id:
        try:
            prob = get_benchmark_problem(payload.problem_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        trace = await agent.solve_task(
            prob["problem_text"],
            task_id=f"manual_{prob['id']}",
            verifier_spec=prob["verifier"],
        )
        return trace.model_dump()
    if not payload.problem_text or not payload.problem_text.strip():
        raise HTTPException(status_code=422, detail="problem_id or problem_text is required.")
    vspec = (
        {"type": payload.verifier_type, "expected": payload.expected_answer.strip()}
        if payload.expected_answer and payload.expected_answer.strip()
        else None
    )
    trace = await agent.solve_task(payload.problem_text.strip(), verifier_spec=vspec)
    return trace.model_dump()


@router.post("/reflect/{trace_id}")
async def reflect_trace(trace_id: str) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    try:
        proposal = await orch.reflector.reflect_on_trace(trace_id, register=True)
        return proposal.model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionViolationError, ReflectionParseError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reflect-batch")
async def reflect_batch(payload: ReflectBatchRequest) -> dict[str, Any]:
    """Analyze a batch of failed traces in one reflection call."""
    orch = get_self_improvement_orchestrator()
    try:
        proposal = await orch.reflector.reflect_on_traces(payload.trace_ids, register=True)
        return proposal.model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionViolationError, ReflectionParseError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/proposals/{proposal_id}/review")
async def review_proposal(proposal_id: str, payload: ProposalReviewRequest) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    try:
        if payload.reviewer_role == "meta":
            decision, updated_prop, exp_task = await orch.meta_reflector.review_proposal(
                proposal_id,
                runs_per_problem_k=payload.runs_per_problem_k,
                experiment_problem_ids=payload.benchmark_problem_ids or None,
                baseline_trace_ids=payload.baseline_trace_ids or None,
            )
            return {
                "decision": decision.model_dump() if decision else None,
                "proposal": updated_prop.model_dump(),
                "experiment_task": exp_task.model_dump() if exp_task else None,
            }
        settings = orch.store.get_settings()
        dec = MetaReviewDecision(
            proposal_id=proposal_id,
            decision=payload.decision,
            review_comment=payload.review_comment or f"人工审批操作：{payload.decision}",
            benchmark_problem_ids=payload.benchmark_problem_ids or ["calc_01", "seq_03", "nt_01"],
            runs_per_problem_k=payload.runs_per_problem_k or settings.default_runs_per_problem_k,
        )
        updated_prop, exp_task = orch.strategy_manager.apply_meta_review(dec, reviewer_role="human")
        return {
            "decision": dec.model_dump(),
            "proposal": updated_prop.model_dump(),
            "experiment_task": exp_task.model_dump() if exp_task else None,
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionViolationError, IllegalStateTransitionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/experiments/{task_id}/run")
async def run_experiment_task(task_id: str) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    try:
        report = await orch.practitioner.run_experiment(task_id)
        final_report, final_strat = await orch.meta_reflector.review_experiment_report(report.id)
        return {
            "report": final_report.model_dump(),
            "strategy": final_strat.model_dump(),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionViolationError, IllegalStateTransitionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reports/{report_id}/finalize")
@router.post("/reports/{report_id}/approve")
async def finalize_candidate_strategy(report_id: str, payload: FinalizeCandidateRequest) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    try:
        approved_val = payload.approved if payload.approved is not None else payload.human_approved
        rep = orch.store.get_experiment_report(report_id)
        if not rep:
            raise HTTPException(status_code=404, detail=f"ExperimentReport '{report_id}' not found.")
        strat = orch.strategy_manager.finalize_candidate_strategy(
            report_id,
            meta_approved=bool(rep.meta_approved) if rep.meta_approved is not None else bool(approved_val),
            meta_review_comment=rep.meta_review_comment or payload.comment,
            human_approved=approved_val,
            actor="human",
        )
        rep = orch.store.get_experiment_report(report_id)
        data = strat.model_dump()
        data["strategy"] = strat.model_dump()
        data["report"] = rep.model_dump() if rep else None
        return data
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionViolationError, IllegalStateTransitionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/strategies/{strategy_id}/versions/{version}/rollback")
async def rollback_strategy(strategy_id: str, version: int, payload: RollbackRequest) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    try:
        rolled = orch.strategy_manager.rollback_strategy(
            strategy_id,
            version,
            actor=payload.actor or "human",
            rationale=payload.rationale,
        )
        restored = (
            orch.store.get_strategy(strategy_id, rolled.parent_version, caller_role="strategy_manager")
            if rolled.parent_version is not None
            else None
        )
        data = rolled.model_dump()
        data["rolled_back"] = rolled.model_dump()
        data["restored_active"] = restored.model_dump() if restored else None
        return data
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionViolationError, IllegalStateTransitionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/strategies/rollback")
async def rollback_strategy_body(payload: RollbackRequest) -> dict[str, Any]:
    if not payload.strategy_id:
        raise HTTPException(status_code=422, detail="strategy_id is required.")
    orch = get_self_improvement_orchestrator()
    ver = payload.version
    if ver is None:
        current = orch.store.get_strategy(payload.strategy_id, caller_role="strategy_manager")
        if not current:
            raise HTTPException(status_code=404, detail=f"Strategy '{payload.strategy_id}' not found.")
        ver = current.version
    return await rollback_strategy(payload.strategy_id, ver, payload)


@router.post("/experiences/{agent_id}/compress")
async def compress_agent_experiences(agent_id: Literal["agent_a", "agent_b"]) -> dict[str, Any]:
    orch = get_self_improvement_orchestrator()
    compressed = orch.store.compress_experiences(agent_id, caller_role=agent_id)
    return {"compressed_count": len(compressed), "items": [c.model_dump() for c in compressed]}
