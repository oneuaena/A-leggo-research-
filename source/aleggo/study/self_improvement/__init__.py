"""A-leggo Research Self-Improvement System (Execute -> Record -> Reflect -> Propose -> Review -> Experiment -> Verify -> Update)."""
from __future__ import annotations

from aleggo.study.self_improvement.agents import (
    ExecutionAgent,
    MetaReflectorAgent,
    MetaReviewParseError,
    PractitionerAgent,
    ReflectionParseError,
    ReflectorAgent,
)
from aleggo.study.self_improvement.benchmark import (
    get_benchmark_problem,
    load_benchmark_problems,
    select_benchmark_subset,
)
from aleggo.study.self_improvement.loop import (
    SelfImprovementOrchestrator,
    get_self_improvement_orchestrator,
    record_collaborator_trace,
)
from aleggo.study.self_improvement.models import (
    AuditLog,
    Experience,
    ExperimentReport,
    ExperimentTask,
    ExperimentThresholds,
    MetaReviewDecision,
    RuleJudgment,
    SelfImprovementSettings,
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
)
from aleggo.study.self_improvement.store import PermissionViolationError, SelfImprovementStore
from aleggo.study.self_improvement.strategy_manager import IllegalStateTransitionError, StrategyManager
from aleggo.study.self_improvement.verifiers import verify_problem_answer

__all__ = [
    "AuditLog",
    "ExecutionAgent",
    "Experience",
    "ExperimentReport",
    "ExperimentTask",
    "ExperimentThresholds",
    "IllegalStateTransitionError",
    "MetaReflectorAgent",
    "MetaReviewDecision",
    "MetaReviewParseError",
    "PermissionViolationError",
    "PractitionerAgent",
    "ReflectionParseError",
    "ReflectorAgent",
    "RuleJudgment",
    "SelfImprovementOrchestrator",
    "SelfImprovementSettings",
    "SelfImprovementStore",
    "Strategy",
    "StrategyManager",
    "StrategyProposal",
    "StrategySelection",
    "StrategySelectionItem",
    "StrategyVersionRef",
    "StructuredStrategyChange",
    "TaskTrace",
    "TokenUsage",
    "TraceStep",
    "VerificationResult",
    "get_benchmark_problem",
    "get_self_improvement_orchestrator",
    "load_benchmark_problems",
    "record_collaborator_trace",
    "select_benchmark_subset",
    "verify_problem_answer",
]
