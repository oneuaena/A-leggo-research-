"""Structured Pydantic data models for the A-leggo Research self-improvement closed loop.

All inter-agent communication uses these strict schemas — no free-form agent-to-agent chat.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


StrategyStatus = Literal[
    "draft",
    "proposed",
    "approved_for_trial",
    "candidate",
    "active",
    "rejected",
    "deprecated",
    "rolled_back",
]

ExperienceType = Literal["success", "failure", "exploration"]

ProposalStatus = Literal[
    "proposed",
    "approved",
    "rejected",
    "revision_requested",
    "review_failed",
    "testing",
    "completed",
]


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0
    actual_model: str = ""


class StrategyVersionRef(BaseModel):
    id: str
    name: str
    version: int
    status: StrategyStatus = "active"


class StrategySelectionItem(BaseModel):
    strategy_id: str
    version: int = 1
    reason: str


class StrategySelection(BaseModel):
    selected_strategy_ids: list[str] = Field(default_factory=list)
    selections: list[StrategySelectionItem] = Field(default_factory=list)
    overall_plan: str = ""


class TraceStep(BaseModel):
    step_index: int
    reasoning_summary: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_output: str = ""
    tool_ok: bool = True
    elapsed_ms: int = 0


class VerificationResult(BaseModel):
    passed: bool = False
    verifier_type: str = "numeric"
    verifier_output: str = ""
    expected: str = ""
    actual: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class TaskTrace(BaseModel):
    """Append-only raw execution record (TaskTrace)."""

    id: str = Field(default_factory=lambda: new_id("trace"))
    agent_id: str
    task_id: str
    problem_text: str
    available_strategy_versions: list[StrategyVersionRef] = Field(default_factory=list)
    strategy_selection: StrategySelection = Field(default_factory=StrategySelection)
    steps: list[TraceStep] = Field(default_factory=list)
    final_answer: str = ""
    verification_result: VerificationResult = Field(default_factory=VerificationResult)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    total_elapsed_ms: int = 0
    legacy_demo: bool = False
    created_at: str = Field(default_factory=utc_now_iso)


class Experience(BaseModel):
    """Agent private experience entry traceable to raw TaskTrace IDs."""

    id: str = Field(default_factory=lambda: new_id("exp"))
    agent_id: str
    experience_type: ExperienceType
    content: str
    source_trace_ids: list[str]
    created_at: str = Field(default_factory=utc_now_iso)
    is_compressed: bool = False
    compressed_from: list[str] = Field(default_factory=list)
    strategy_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("source_trace_ids")
    @classmethod
    def _require_source_traces(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("Experience.source_trace_ids must contain at least one trace_id.")
        return list(dict.fromkeys(cleaned))


class Strategy(BaseModel):
    """Versioned structured strategy (never a monolithic free-form prompt)."""

    id: str = Field(default_factory=lambda: new_id("strat"))
    name: str
    version: int = 1
    owner: str  # "agent_a", "agent_b", or "meta"
    status: StrategyStatus = "draft"
    applicable_conditions: str
    execution_steps: list[str] = Field(default_factory=list)
    taboos_and_counterexamples: list[str] = Field(default_factory=list)
    parent_version: int | None = None
    origin_proposal_id: str | None = None
    evidence: list[str] = Field(default_factory=list)
    proposed_by: str = "system_seed"
    approved_by: str | None = None
    legacy_demo: bool = False
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    def to_summary_card(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "applicable_conditions": self.applicable_conditions,
            "execution_steps": self.execution_steps,
            "taboos_and_counterexamples": self.taboos_and_counterexamples,
        }


class StructuredStrategyChange(BaseModel):
    name: str
    applicable_conditions: str
    execution_steps: list[str] = Field(default_factory=list)
    taboos_and_counterexamples: list[str] = Field(default_factory=list)
    change_summary: str = ""


class StrategyProposal(BaseModel):
    """Strategy improvement proposal submitted by Reflector Agent (or Meta-Reflector for meta strategies)."""

    id: str = Field(default_factory=lambda: new_id("prop"))
    target_agent: str  # "agent_a", "agent_b", or "meta"
    base_strategy_id: str | None = None
    base_strategy_version: int | None = None
    new_strategy_id: str | None = None
    new_strategy_version: int | None = None
    problem_found: str
    claimed_failure_signature: str = ""
    novel_method_evidence: str = ""
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_trace_ids: list[str]
    experience_ids: list[str] = Field(default_factory=list)
    structured_changes: StructuredStrategyChange
    expected_benefit: str
    applicable_scope: str
    status: ProposalStatus = "proposed"
    review_comment: str = ""
    reviewed_by: str | None = None
    proposed_by: str = "reflector"
    raw_model_output: str = ""
    legacy_demo: bool = False
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("evidence_trace_ids")
    @classmethod
    def _require_evidence_traces(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            raise ValueError("StrategyProposal.evidence_trace_ids must contain at least one trace_id.")
        return list(dict.fromkeys(cleaned))


class MetaReviewDecision(BaseModel):
    """Structured review output from Meta-Reflector Agent."""

    proposal_id: str
    decision: Literal["approve", "reject", "request_revision", "review_failed"]
    review_comment: str
    evolution_memo: str = ""
    benchmark_problem_ids: list[str] = Field(default_factory=list)
    runs_per_problem_k: int = 3
    raw_model_output: str = ""
    token_usage: TokenUsage = Field(default_factory=TokenUsage)


class ExperimentThresholds(BaseModel):
    min_pass_rate_delta: float = 0.0  # new_pass_rate >= old_pass_rate + min_pass_rate_delta
    max_token_increase_ratio: float = 0.20  # (new_tokens - old_tokens) / max(old_tokens, 1) <= 0.20
    min_absolute_pass_rate: float = 0.0
    min_sample_runs_per_group: int = 30  # default minimum sample runs per group
    allow_token_exemption_on_high_pass_gain: bool = False  # default OFF
    high_pass_gain_threshold: float = 0.10


class ExperimentTask(BaseModel):
    """Experiment task order created after Meta-Reflector approves a proposal."""

    id: str = Field(default_factory=lambda: new_id("exptask"))
    proposal_id: str
    target_agent: str
    strategy_id: str
    old_strategy_version: int | None = None
    new_strategy_version: int
    benchmark_problem_ids: list[str]
    runs_per_problem_k: int = 3
    metrics_and_thresholds: ExperimentThresholds = Field(default_factory=ExperimentThresholds)
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    legacy_demo: bool = False
    created_at: str = Field(default_factory=utc_now_iso)


class RuleJudgment(BaseModel):
    """Deterministic judgment computed by StrategyManager (never by an LLM)."""

    passed: bool
    old_pass_rate: float
    new_pass_rate: float
    pass_rate_delta: float
    old_avg_tokens: float
    new_avg_tokens: float
    token_increase_ratio: float
    old_sample_runs: int = 0
    new_sample_runs: int = 0
    min_sample_runs_required: int = 30
    sample_size_ok: bool = True
    used_token_exemption: bool = False
    reason: str
    promoted_to_candidate: bool = False


class ExperimentReport(BaseModel):
    """Complete experiment report comparing old vs. new strategy versions."""

    id: str = Field(default_factory=lambda: new_id("exprep"))
    task_id: str
    proposal_id: str
    target_agent: str
    strategy_id: str
    old_strategy_version: int | None = None
    new_strategy_version: int
    old_run_trace_ids: list[str] = Field(default_factory=list)
    new_run_trace_ids: list[str] = Field(default_factory=list)
    all_trace_ids: list[str] = Field(default_factory=list)
    old_pass_rate: float = 0.0
    new_pass_rate: float = 0.0
    old_avg_tokens: float = 0.0
    new_avg_tokens: float = 0.0
    old_avg_elapsed_ms: float = 0.0
    new_avg_elapsed_ms: float = 0.0
    problem_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    rule_judgment: RuleJudgment
    meta_review_comment: str = ""
    meta_approved: bool | None = None
    human_approved: bool | None = None
    raw_model_output: str = ""
    legacy_demo: bool = False
    created_at: str = Field(default_factory=utc_now_iso)


class AuditLog(BaseModel):
    """Immutable audit log written by StrategyManager on every state transition."""

    id: str = Field(default_factory=lambda: new_id("audit"))
    who: str
    when: str = Field(default_factory=utc_now_iso)
    target_type: str  # "strategy", "proposal", "experiment", "settings", "reflection_error"
    target_id: str
    from_state: str
    to_state: str
    rationale: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelfImprovementSettings(BaseModel):
    require_human_approval: bool = False
    default_runs_per_problem_k: int = 3
    min_pass_rate_delta: float = 0.0
    max_token_increase_ratio: float = 0.20
    min_sample_runs_per_group: int = 30
    allow_token_exemption_on_high_pass_gain: bool = False
    high_pass_gain_threshold: float = 0.10
    evolution_memos: list[dict[str, Any]] = Field(default_factory=list)
