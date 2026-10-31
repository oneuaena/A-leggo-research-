"""Non-LLM Strategy Manager for A-leggo Research self-improvement system.

Enforces:
- Strict state machine transitions for Strategy:
    draft -> proposed -> approved_for_trial -> candidate -> active
    any -> rejected / deprecated
    active -> rolled_back (automatically restores parent_version to active)
- Strict state machine transitions for StrategyProposal:
    proposed -> approved | rejected | revision_requested | review_failed
    revision_requested -> proposed | rejected | review_failed
    review_failed -> proposed | rejected
    approved -> testing | completed | rejected | review_failed
    testing -> completed | rejected | review_failed
- Role permission checks (no Agent can directly modify formal strategies; Meta-Reflector cannot self-approve).
- Fixed-rule experiment verification judgment (minimum sample size check, pass rate >= old pass rate, avg token increase check, optional high pass-gain token exemption defaulting to False).
- Immutable AuditLog recording on every state change.
"""
from __future__ import annotations

from typing import Any

from aleggo.study.self_improvement.models import (
    AuditLog,
    ExperimentReport,
    ExperimentTask,
    ExperimentThresholds,
    MetaReviewDecision,
    ProposalStatus,
    RuleJudgment,
    Strategy,
    StrategyProposal,
    StrategyStatus,
    TokenUsage,
    utc_now_iso,
    new_id,
)
from aleggo.study.self_improvement.store import (
    PermissionViolationError,
    SelfImprovementStore,
    VALID_EXECUTION_AGENTS,
)


class IllegalStateTransitionError(ValueError):
    """Raised when a requested strategy or proposal state transition violates the state machine."""


ALLOWED_FORWARD_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"proposed", "rejected", "deprecated"},
    "proposed": {"approved_for_trial", "rejected", "deprecated"},
    "approved_for_trial": {"candidate", "rejected", "deprecated"},
    "candidate": {"active", "rejected", "deprecated"},
    "active": {"rolled_back", "deprecated", "rejected"},
    "rejected": {"deprecated"},
    "deprecated": set(),
    "rolled_back": {"deprecated"},
}

ALLOWED_PROPOSAL_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"proposed", "rejected", "review_failed"},
    "proposed": {"approved", "rejected", "revision_requested", "review_failed"},
    # A proposal may be re-reviewed after the review context is corrected. Keep
    # the repeated request in the append-only audit trail instead of turning a
    # valid second review into an HTTP 422 at the status write.
    "revision_requested": {"proposed", "approved", "rejected", "review_failed", "revision_requested"},
    "review_failed": {"proposed", "approved", "rejected", "revision_requested"},
    "approved": {"testing", "completed", "rejected", "review_failed"},
    "testing": {"completed", "rejected", "review_failed"},
    "completed": set(),
    "rejected": set(),
}


class StrategyManager:
    """Deterministic backend controller for strategy versions, proposal states, and audit logging."""

    def __init__(self, store: SelfImprovementStore) -> None:
        self.store = store
        self._token = store._bind_strategy_manager(self)

    @staticmethod
    def validate_transition(from_state: str, to_state: str) -> None:
        allowed = ALLOWED_FORWARD_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            raise IllegalStateTransitionError(
                f"Illegal strategy state transition: '{from_state}' -> '{to_state}'. "
                f"Allowed targets from '{from_state}': {sorted(allowed)}"
            )

    @staticmethod
    def validate_proposal_transition(from_state: str, to_state: str) -> None:
        allowed = ALLOWED_PROPOSAL_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            raise IllegalStateTransitionError(
                f"Illegal proposal state transition: '{from_state}' -> '{to_state}'. "
                f"Allowed targets from '{from_state}': {sorted(allowed)}"
            )

    def transition_strategy_status(
        self,
        strategy_id: str,
        version: int,
        to_state: StrategyStatus,
        *,
        actor: str,
        rationale: str,
        evidence: list[str] | None = None,
    ) -> Strategy:
        """Validate permissions and state machine rules, update strategy status, and write AuditLog."""
        if actor in VALID_EXECUTION_AGENTS or actor in {"reflector", "practitioner"}:
            raise PermissionViolationError(
                f"Permission denied: role '{actor}' is forbidden from changing strategy state."
            )
        strat = self.store.get_strategy(strategy_id, version, caller_role="strategy_manager")
        if not strat:
            raise KeyError(f"Strategy {strategy_id} v{version} does not exist.")

        from_state = strat.status
        self.validate_transition(from_state, to_state)

        # Hard Principle 7: Meta-Reflector cannot self-approve or activate its own strategy without human approval
        if strat.owner == "meta" and to_state in {"approved_for_trial", "active"} and actor == "meta":
            raise PermissionViolationError(
                "Permission denied (Hard Principle 7): Meta-Reflector cannot self-approve its own strategy changes."
            )

        if to_state == "rolled_back":
            return self.rollback_strategy(strategy_id, version, actor=actor, rationale=rationale)

        updated = self.store._update_strategy_status(
            strategy_id,
            version,
            to_state,
            approved_by=actor if to_state in {"approved_for_trial", "candidate", "active"} else strat.approved_by,
            evidence=evidence,
            _manager_token=self._token,
        )
        self.store.append_audit_log(
            AuditLog(
                who=actor,
                target_type="strategy",
                target_id=f"{strategy_id}:v{version}",
                from_state=from_state,
                to_state=to_state,
                rationale=rationale,
                metadata={"strategy_id": strategy_id, "version": version, "owner": strat.owner},
            ),
            caller_role="strategy_manager",
        )
        return updated

    def transition_proposal_status(
        self,
        proposal_id: str,
        to_state: ProposalStatus,
        *,
        actor: str,
        rationale: str,
        review_comment: str | None = None,
        raw_model_output: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StrategyProposal:
        """Validate proposal state machine rules, update proposal status, and write AuditLog."""
        if actor in VALID_EXECUTION_AGENTS or actor in {"reflector", "practitioner"}:
            raise PermissionViolationError(
                f"Permission denied: role '{actor}' is forbidden from changing proposal state."
            )
        proposal = self.store.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(f"Proposal '{proposal_id}' not found.")

        from_state = proposal.status
        self.validate_proposal_transition(from_state, to_state)

        proposal.status = to_state
        if review_comment is not None:
            proposal.review_comment = review_comment
        if raw_model_output is not None:
            proposal.raw_model_output = raw_model_output
        proposal.reviewed_by = actor
        proposal.updated_at = utc_now_iso()

        self.store._save_proposal(
            proposal,
            caller_role="strategy_manager",
            _manager_token=self._token,
        )
        self.store.append_audit_log(
            AuditLog(
                who=actor,
                target_type="proposal",
                target_id=proposal.id,
                from_state=from_state,
                to_state=to_state,
                rationale=rationale,
                metadata=metadata or {},
            ),
            caller_role="strategy_manager",
        )
        return proposal

    def record_reflection_error(
        self,
        *,
        agent_role: str,
        target_id: str,
        error_reason: str,
        raw_model_output: str,
        token_usage: TokenUsage | None = None,
    ) -> AuditLog:
        """Record a failed model output and its usage in the append-only audit log."""
        metadata: dict[str, Any] = {"raw_model_output": raw_model_output}
        if token_usage is not None:
            metadata["token_usage"] = token_usage.model_dump()
        return self.store.append_audit_log(
            AuditLog(
                who=agent_role,
                target_type="parse_error",
                target_id=target_id,
                from_state="model_output",
                to_state="parse_failed",
                rationale=error_reason,
                metadata=metadata,
            ),
            caller_role="strategy_manager",
        )

    def mark_legacy_demo_records(
        self,
        *,
        strategy_ids: list[str] | None = None,
        trace_ids: list[str] | None = None,
        proposal_ids: list[str] | None = None,
        task_ids: list[str] | None = None,
        report_ids: list[str] | None = None,
        rationale: str = "归档旧版造失败演示数据（legacy_demo）",
    ) -> None:
        self.store.mark_legacy_demo(
            strategy_ids=strategy_ids,
            trace_ids=trace_ids,
            proposal_ids=proposal_ids,
            task_ids=task_ids,
            report_ids=report_ids,
            reason=rationale,
            _manager_token=self._token,
        )
        self.store.append_audit_log(
            AuditLog(
                who="strategy_manager",
                target_type="legacy_demo",
                target_id=",".join(strategy_ids or ["batch"]),
                from_state="normal",
                to_state="legacy_demo",
                rationale=rationale,
                metadata={
                    "strategy_ids": strategy_ids or [],
                    "trace_ids": trace_ids or [],
                    "proposal_ids": proposal_ids or [],
                    "task_ids": task_ids or [],
                    "report_ids": report_ids or [],
                },
            ),
            caller_role="strategy_manager",
        )

    def register_proposal(self, proposal: StrategyProposal, *, caller_role: str = "reflector") -> tuple[StrategyProposal, Strategy]:
        """Register a new StrategyProposal from Reflector (or Meta-Reflector), creating a draft->proposed Strategy version."""
        if caller_role not in {"reflector", "meta"}:
            raise PermissionViolationError(
                f"Permission denied: '{caller_role}' cannot submit StrategyProposals."
            )
        if caller_role == "reflector" and proposal.target_agent == "meta":
            raise PermissionViolationError(
                "Permission denied: reflector cannot propose changes to 'meta' strategies."
            )

        base_version: int | None = None
        if proposal.base_strategy_id:
            existing = self.store.list_strategies(
                owner=proposal.target_agent,
                caller_role="strategy_manager",
                include_legacy_demo=True,
            )
            matching = [s for s in existing if s.id == proposal.base_strategy_id]
            if not matching:
                raise ValueError(
                    f"Base strategy '{proposal.base_strategy_id}' not found for agent '{proposal.target_agent}'."
                )
            latest = max(matching, key=lambda s: s.version)
            active_matching = [s for s in matching if s.status == "active"]
            parent = active_matching[-1] if active_matching else latest
            base_version = parent.version
            strat_id = parent.id
            new_version = latest.version + 1
        else:
            strat_id = new_id(f"strat_{proposal.target_agent}")
            new_version = 1

        now = utc_now_iso()
        # Step 1: Create strategy in 'draft' state
        draft_strategy = Strategy(
            id=strat_id,
            name=proposal.structured_changes.name,
            version=new_version,
            owner=proposal.target_agent,
            status="draft",
            applicable_conditions=proposal.structured_changes.applicable_conditions,
            execution_steps=list(proposal.structured_changes.execution_steps),
            taboos_and_counterexamples=list(proposal.structured_changes.taboos_and_counterexamples),
            parent_version=base_version,
            origin_proposal_id=proposal.id,
            evidence=[],
            proposed_by=caller_role,
            approved_by=None,
            created_at=now,
            updated_at=now,
        )
        self.store._save_new_strategy_version(draft_strategy, _manager_token=self._token)
        self.store.append_audit_log(
            AuditLog(
                who="strategy_manager",
                target_type="strategy",
                target_id=f"{strat_id}:v{new_version}",
                from_state="none",
                to_state="draft",
                rationale=f"根据提案 {proposal.id} 创建草稿策略版本（由 {caller_role} 提交）",
                metadata={"proposal_id": proposal.id, "evidence_trace_ids": proposal.evidence_trace_ids},
            ),
            caller_role="strategy_manager",
        )

        # Step 2: Transition draft -> proposed
        proposed_strategy = self.transition_strategy_status(
            strat_id,
            new_version,
            "proposed",
            actor="strategy_manager",
            rationale=f"提案 {proposal.id} 已关联证据轨迹 {proposal.evidence_trace_ids}，流转为 proposed 待审状态",
        )

        proposal.base_strategy_version = base_version
        proposal.new_strategy_id = strat_id
        proposal.new_strategy_version = new_version
        proposal.proposed_by = caller_role
        proposal.status = "proposed"
        proposal.updated_at = now
        self.store._save_proposal(proposal, caller_role=caller_role, _manager_token=self._token)
        self.store.append_audit_log(
            AuditLog(
                who=caller_role,
                target_type="proposal",
                target_id=proposal.id,
                from_state="none",
                to_state="proposed",
                rationale=proposal.problem_found[:300],
                metadata={
                    "target_agent": proposal.target_agent,
                    "strategy_id": strat_id,
                    "new_version": new_version,
                    "evidence_trace_ids": proposal.evidence_trace_ids,
                },
            ),
            caller_role="strategy_manager",
        )
        return proposal, proposed_strategy

    def apply_meta_review(
        self,
        decision: MetaReviewDecision,
        *,
        reviewer_role: str = "meta",
        raw_model_output: str = "",
    ) -> tuple[StrategyProposal, ExperimentTask | None]:
        """Apply Meta-Reflector's (or Human's) review decision on a StrategyProposal."""
        if reviewer_role not in {"meta", "human", "strategy_manager"}:
            raise PermissionViolationError(f"Permission denied: '{reviewer_role}' cannot review proposals.")

        proposal = self.store.get_proposal(decision.proposal_id)
        if not proposal:
            raise KeyError(f"Proposal '{decision.proposal_id}' not found.")

        # Hard Principle 7: Meta-Reflector cannot self-approve its own strategy proposal
        if (proposal.target_agent == "meta" or proposal.proposed_by == "meta") and reviewer_role == "meta":
            if decision.decision == "approve":
                raise PermissionViolationError(
                    "Permission denied (Hard Principle 7): Meta-Reflector cannot approve its own strategy proposal; human approval is required."
                )

        assert proposal.new_strategy_id is not None and proposal.new_strategy_version is not None

        if decision.decision == "approve":
            proposal = self.transition_proposal_status(
                proposal.id,
                "approved",
                actor=reviewer_role,
                rationale=decision.review_comment,
                review_comment=decision.review_comment,
                raw_model_output=raw_model_output or proposal.raw_model_output,
            )
            # Transition strategy: proposed -> approved_for_trial
            self.transition_strategy_status(
                proposal.new_strategy_id,
                proposal.new_strategy_version,
                "approved_for_trial",
                actor=reviewer_role,
                rationale=f"审批通过提案 {proposal.id}：{decision.review_comment[:200]}",
            )
            settings = self.store.get_settings()
            if decision.evolution_memo:
                settings.evolution_memos.insert(
                    0,
                    {
                        "proposal_id": proposal.id,
                        "memo": decision.evolution_memo,
                        "created_at": utc_now_iso(),
                    },
                )
                settings.evolution_memos = settings.evolution_memos[:50]
                self.store.save_settings(settings, who="strategy_manager")

            task = ExperimentTask(
                proposal_id=proposal.id,
                target_agent=proposal.target_agent,
                strategy_id=proposal.new_strategy_id,
                old_strategy_version=proposal.base_strategy_version,
                new_strategy_version=proposal.new_strategy_version,
                benchmark_problem_ids=decision.benchmark_problem_ids,
                runs_per_problem_k=decision.runs_per_problem_k or settings.default_runs_per_problem_k,
                metrics_and_thresholds=ExperimentThresholds(
                    min_pass_rate_delta=settings.min_pass_rate_delta,
                    max_token_increase_ratio=settings.max_token_increase_ratio,
                    min_sample_runs_per_group=settings.min_sample_runs_per_group,
                    allow_token_exemption_on_high_pass_gain=settings.allow_token_exemption_on_high_pass_gain,
                    high_pass_gain_threshold=settings.high_pass_gain_threshold,
                ),
                status="pending",
            )
            self.store.save_experiment_task(task, caller_role="strategy_manager")
            return proposal, task

        elif decision.decision == "reject":
            proposal = self.transition_proposal_status(
                proposal.id,
                "rejected",
                actor=reviewer_role,
                rationale=decision.review_comment,
                review_comment=decision.review_comment,
                raw_model_output=raw_model_output or proposal.raw_model_output,
            )
            self.transition_strategy_status(
                proposal.new_strategy_id,
                proposal.new_strategy_version,
                "rejected",
                actor=reviewer_role,
                rationale=f"驳回提案 {proposal.id}：{decision.review_comment[:200]}",
            )
            return proposal, None

        elif decision.decision == "request_revision":
            proposal = self.transition_proposal_status(
                proposal.id,
                "revision_requested",
                actor=reviewer_role,
                rationale=decision.review_comment,
                review_comment=decision.review_comment,
                raw_model_output=raw_model_output or proposal.raw_model_output,
            )
            return proposal, None

        raise ValueError(f"Unsupported review decision: {decision.decision}")

    def judge_experiment(
        self,
        task: ExperimentTask,
        *,
        old_run_trace_ids: list[str],
        new_run_trace_ids: list[str],
        old_pass_rate: float,
        new_pass_rate: float,
        old_avg_tokens: float,
        new_avg_tokens: float,
        old_avg_elapsed_ms: float,
        new_avg_elapsed_ms: float,
        problem_breakdown: list[dict[str, Any]],
    ) -> ExperimentReport:
        """Deterministically judge an experiment using fixed configurable thresholds (Hard Principle 4).

        Enforces:
        1. Minimum sample size check (old_sample_runs >= min_sample_runs_per_group and new_sample_runs >= min_sample_runs_per_group).
        2. Pass rate non-regression / improvement check.
        3. Token increase check (with optional high pass-gain exemption defaulting to False).
        """
        thresholds = task.metrics_and_thresholds
        old_sample_runs = len(old_run_trace_ids)
        new_sample_runs = len(new_run_trace_ids)
        min_req = int(thresholds.min_sample_runs_per_group)
        sample_size_ok = (old_sample_runs >= min_req) and (new_sample_runs >= min_req)

        pass_rate_delta = round(new_pass_rate - old_pass_rate, 6)
        if old_avg_tokens > 0:
            token_increase_ratio = round((new_avg_tokens - old_avg_tokens) / old_avg_tokens, 6)
        else:
            token_increase_ratio = 0.0

        pass_rate_ok = (pass_rate_delta >= thresholds.min_pass_rate_delta - 1e-9) and (
            new_pass_rate >= thresholds.min_absolute_pass_rate - 1e-9
        )
        token_ok = token_increase_ratio <= thresholds.max_token_increase_ratio + 1e-9
        high_pass_gain = pass_rate_delta >= thresholds.high_pass_gain_threshold - 1e-9
        used_token_exemption = bool(
            not token_ok
            and thresholds.allow_token_exemption_on_high_pass_gain
            and high_pass_gain
        )

        passed = bool(sample_size_ok and pass_rate_ok and (token_ok or used_token_exemption))

        reasons: list[str] = []
        if not sample_size_ok:
            reasons.append(
                f"样本不足，不予晋升（旧策略运行 {old_sample_runs} 次、新策略运行 {new_sample_runs} 次，低于最小样本量要求 {min_req} 次/组）"
            )
        else:
            reasons.append(
                f"样本量达标（旧策略 {old_sample_runs} 次、新策略 {new_sample_runs} 次 >= {min_req} 次/组）"
            )

        if pass_rate_ok:
            reasons.append(
                f"验证通过率达标（新策略 {new_pass_rate:.1%} vs 旧策略 {old_pass_rate:.1%}，Δ={pass_rate_delta:+.1%} >= {thresholds.min_pass_rate_delta:.1%}）"
            )
        else:
            reasons.append(
                f"验证通过率未达标（新策略 {new_pass_rate:.1%} vs 旧策略 {old_pass_rate:.1%}，Δ={pass_rate_delta:+.1%} < {thresholds.min_pass_rate_delta:.1%}）"
            )

        if token_ok:
            reasons.append(
                f"Token 增幅受控（新策略均值 {new_avg_tokens:.0f} vs 旧策略均值 {old_avg_tokens:.0f}，增幅 {token_increase_ratio:+.1%} <= {thresholds.max_token_increase_ratio:.0%}）"
            )
        elif used_token_exemption:
            reasons.append(
                f"【已启用高通过率增幅豁免 Token 上限规则】通过率提升 Δ={pass_rate_delta:+.1%} >= {thresholds.high_pass_gain_threshold:.1%}，豁免 Token 增幅 {token_increase_ratio:+.1%}（上限 {thresholds.max_token_increase_ratio:.0%}）"
            )
        else:
            reasons.append(
                f"Token 增幅超限（新策略均值 {new_avg_tokens:.0f} vs 旧策略均值 {old_avg_tokens:.0f}，增幅 {token_increase_ratio:+.1%} > {thresholds.max_token_increase_ratio:.0%}，高通过率豁免未启用）"
            )

        judgment = RuleJudgment(
            passed=passed,
            old_sample_runs=old_sample_runs,
            new_sample_runs=new_sample_runs,
            min_sample_runs_required=min_req,
            sample_size_ok=sample_size_ok,
            old_pass_rate=old_pass_rate,
            new_pass_rate=new_pass_rate,
            pass_rate_delta=pass_rate_delta,
            old_avg_tokens=old_avg_tokens,
            new_avg_tokens=new_avg_tokens,
            token_increase_ratio=token_increase_ratio,
            used_token_exemption=used_token_exemption,
            reason="；".join(reasons),
            promoted_to_candidate=passed,
        )

        report = ExperimentReport(
            task_id=task.id,
            proposal_id=task.proposal_id,
            target_agent=task.target_agent,
            strategy_id=task.strategy_id,
            old_strategy_version=task.old_strategy_version,
            new_strategy_version=task.new_strategy_version,
            old_run_trace_ids=old_run_trace_ids,
            new_run_trace_ids=new_run_trace_ids,
            all_trace_ids=old_run_trace_ids + new_run_trace_ids,
            old_pass_rate=old_pass_rate,
            new_pass_rate=new_pass_rate,
            old_avg_tokens=old_avg_tokens,
            new_avg_tokens=new_avg_tokens,
            old_avg_elapsed_ms=old_avg_elapsed_ms,
            new_avg_elapsed_ms=new_avg_elapsed_ms,
            problem_breakdown=problem_breakdown,
            rule_judgment=judgment,
        )
        self.store.save_experiment_report(report, caller_role="strategy_manager")
        task.status = "completed"
        self.store.save_experiment_task(task, caller_role="strategy_manager")

        if passed:
            # Transition strategy: approved_for_trial -> candidate
            self.transition_strategy_status(
                task.strategy_id,
                task.new_strategy_version,
                "candidate",
                actor="strategy_manager",
                rationale=f"实验报告 {report.id} 通过固定规则判定：{judgment.reason}",
                evidence=[report.id],
            )
        else:
            self.transition_strategy_status(
                task.strategy_id,
                task.new_strategy_version,
                "rejected",
                actor="strategy_manager",
                rationale=f"实验报告 {report.id} 未通过固定规则判定：{judgment.reason}",
                evidence=[report.id],
            )
            proposal = self.store.get_proposal(task.proposal_id)
            if proposal and proposal.status not in {"rejected", "completed"}:
                self.transition_proposal_status(
                    proposal.id,
                    "rejected",
                    actor="strategy_manager",
                    rationale=f"实验未达标：{judgment.reason}",
                    review_comment=f"实验未达标：{judgment.reason}",
                )
        return report

    def finalize_candidate_strategy(
        self,
        report_id: str,
        *,
        meta_approved: bool,
        meta_review_comment: str,
        human_approved: bool | None = None,
        actor: str = "strategy_manager",
        raw_model_output: str = "",
    ) -> Strategy:
        """Promote a 'candidate' strategy to 'active' (or 'rejected') after Meta-Reflector review and optional Human gate."""
        report = self.store.get_experiment_report(report_id)
        if not report:
            raise KeyError(f"ExperimentReport '{report_id}' not found.")

        report.meta_approved = meta_approved
        report.meta_review_comment = meta_review_comment
        if raw_model_output:
            report.raw_model_output = raw_model_output
        if human_approved is not None:
            report.human_approved = human_approved
        self.store.save_experiment_report(report, caller_role="strategy_manager")

        strat = self.store.get_strategy(report.strategy_id, report.new_strategy_version, caller_role="strategy_manager")
        if not strat:
            raise KeyError(f"Strategy {report.strategy_id} v{report.new_strategy_version} not found.")

        if not report.rule_judgment.passed or not meta_approved or human_approved is False:
            if strat.status != "rejected":
                strat = self.transition_strategy_status(
                    strat.id,
                    strat.version,
                    "rejected",
                    actor=actor,
                    rationale=f"终审未通过：{meta_review_comment}",
                    evidence=[report.id],
                )
            proposal = self.store.get_proposal(report.proposal_id)
            if proposal and proposal.status not in {"rejected", "completed"}:
                self.transition_proposal_status(
                    proposal.id,
                    "rejected",
                    actor=actor,
                    rationale=meta_review_comment,
                    review_comment=meta_review_comment,
                )
            return strat

        settings = self.store.get_settings()
        needs_human = settings.require_human_approval or (strat.owner == "meta")
        if needs_human and not report.human_approved and actor != "human":
            # Remains in 'candidate' awaiting human approval
            self.store.append_audit_log(
                AuditLog(
                    who="meta",
                    target_type="strategy",
                    target_id=f"{strat.id}:v{strat.version}",
                    from_state="candidate",
                    to_state="candidate",
                    rationale=f"元反思终审通过（{meta_review_comment[:160]}），等待人工审批确认后激活 active",
                    metadata={"report_id": report.id, "awaiting_human_approval": True},
                ),
                caller_role="strategy_manager",
            )
            return strat

        # Deprecate previous active version(s) of the same strategy ID
        all_versions = [
            s
            for s in self.store.list_strategies(
                owner=strat.owner,
                caller_role="strategy_manager",
                include_legacy_demo=True,
            )
            if s.id == strat.id and s.version != strat.version and s.status == "active"
        ]
        for prev in all_versions:
            self.transition_strategy_status(
                prev.id,
                prev.version,
                "deprecated",
                actor="strategy_manager",
                rationale=f"被新版本 v{strat.version}（实验报告 {report.id}）替代而归档",
            )

        activated = self.transition_strategy_status(
            strat.id,
            strat.version,
            "active",
            actor="human" if (report.human_approved or actor == "human") else "strategy_manager",
            rationale=f"通过独立实验验证（{report.id}）与元反思审查：{meta_review_comment[:200]}",
            evidence=[report.id],
        )

        proposal = self.store.get_proposal(report.proposal_id)
        if proposal and proposal.status not in {"completed", "rejected"}:
            self.transition_proposal_status(
                proposal.id,
                "completed",
                actor="strategy_manager",
                rationale=f"新策略 {strat.id}:v{strat.version} 已正式激活为 active",
                review_comment=meta_review_comment,
            )
        return activated

    def rollback_strategy(
        self,
        strategy_id: str,
        version: int,
        *,
        actor: str = "strategy_manager",
        rationale: str = "一键回滚策略版本",
    ) -> Strategy:
        """Roll back an 'active' strategy version to 'rolled_back' and restore parent_version to 'active'."""
        if actor in VALID_EXECUTION_AGENTS or actor in {"reflector", "practitioner"}:
            raise PermissionViolationError(f"Permission denied: '{actor}' cannot roll back strategies.")

        strat = self.store.get_strategy(strategy_id, version, caller_role="strategy_manager")
        if not strat:
            raise KeyError(f"Strategy {strategy_id} v{version} not found.")
        self.validate_transition(strat.status, "rolled_back")

        rolled_back = self.store._update_strategy_status(
            strategy_id,
            version,
            "rolled_back",
            _manager_token=self._token,
        )
        self.store.append_audit_log(
            AuditLog(
                who=actor,
                target_type="strategy",
                target_id=f"{strategy_id}:v{version}",
                from_state="active",
                to_state="rolled_back",
                rationale=rationale,
                metadata={"parent_version": strat.parent_version},
            ),
            caller_role="strategy_manager",
        )

        if strat.parent_version is not None:
            parent = self.store.get_strategy(strategy_id, strat.parent_version, caller_role="strategy_manager")
            if parent:
                old_parent_state = parent.status
                self.store._update_strategy_status(
                    strategy_id,
                    parent.version,
                    "active",
                    _manager_token=self._token,
                )
                self.store.append_audit_log(
                    AuditLog(
                        who="strategy_manager",
                        target_type="strategy",
                        target_id=f"{strategy_id}:v{parent.version}",
                        from_state=old_parent_state,
                        to_state="active",
                        rationale=f"因新版本 v{version} 回滚，自动恢复父版本 v{parent.version} 为 active",
                    ),
                    caller_role="strategy_manager",
                )
        return rolled_back
