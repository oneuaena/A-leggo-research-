"""Unit and state-machine tests for the A-leggo Research Self-Improvement System."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import tempfile
from typing import Any
import unittest

from aleggo.study.self_improvement import (
    Experience,
    IllegalStateTransitionError,
    MetaReflectorAgent,
    MetaReviewDecision,
    PermissionViolationError,
    ReflectionParseError,
    ReflectorAgent,
    SelfImprovementStore,
    StrategyManager,
    StrategyProposal,
    StrategySelection,
    StrategySelectionItem,
    StructuredStrategyChange,
    TaskTrace,
    TokenUsage,
    TraceStep,
    VerificationResult,
    load_benchmark_problems,
)
from aleggo.study.self_improvement.verifiers import verify_numeric, verify_sympy_equiv


class _DummyFakeRouter:
    """Fake router for testing LLM parse failures without hitting real API."""

    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text

    async def complete(self, model_id: str, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        return {
            "text": self.reply_text,
            "usage": {"promptTokens": 50, "completionTokens": 20, "totalTokens": 70, "actualModel": "fake-model"},
        }


class SelfImprovementArchitectureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test_si.sqlite3"
        self.store = SelfImprovementStore(self.db_path)
        self.manager = StrategyManager(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _create_dummy_trace(self, agent_id: str = "agent_a", passed: bool = False) -> TaskTrace:
        trace = TaskTrace(
            agent_id=agent_id,
            task_id="task_unit_01",
            problem_text="求 \\sum_{k=1}^{50} 1/(k(k+1)(k+2))",
            strategy_selection=StrategySelection(
                selected_strategy_ids=[f"strat_{agent_id}_small_scale_numeric_probe"],
                selections=[
                    StrategySelectionItem(
                        strategy_id=f"strat_{agent_id}_small_scale_numeric_probe",
                        version=1,
                        reason="先计算小规模和寻找通项公式",
                    )
                ],
                overall_plan="先用 Python 算 n=1,2,3，再化简分式",
            ),
            steps=[
                TraceStep(
                    step_index=1,
                    reasoning_summary="调用 Python 计算前 50 项和",
                    tool_name="run_python",
                    tool_input={"code": "from fractions import Fraction\nresult = sum(Fraction(1, k*(k+1)*(k+2)) for k in range(1, 51))"},
                    tool_output="1325/5304",
                    tool_ok=True,
                    elapsed_ms=120,
                )
            ],
            final_answer="FINAL_ANSWER: 1325/5304" if passed else "FINAL_ANSWER: 1/4",
            verification_result=VerificationResult(
                passed=passed,
                verifier_type="sympy_equiv",
                verifier_output="测试校验",
                expected="1325/5304",
                actual="1325/5304" if passed else "1/4",
            ),
            token_usage=TokenUsage(prompt_tokens=300, completion_tokens=120, total_tokens=420, model_calls=2),
            total_elapsed_ms=850,
        )
        return self.store.append_trace(trace, caller_role=agent_id)

    def test_append_only_task_trace_rejects_update_and_delete(self) -> None:
        """Hard Principle 5: TaskTrace is strictly append-only; SQL UPDATE and DELETE must be aborted."""
        trace = self._create_dummy_trace("agent_a", passed=True)
        fetched = self.store.get_trace(trace.id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.steps[0].tool_output, "1325/5304")

        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connection() as conn:
                conn.execute("UPDATE si_task_traces SET final_answer = 'hacked' WHERE id = ?", (trace.id,))

        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connection() as conn:
                conn.execute("DELETE FROM si_task_traces WHERE id = ?", (trace.id,))

    def test_agent_a_and_b_memory_and_strategy_isolation(self) -> None:
        """Stage 3: Agent A and B start with identical 5 seed strategies and have strictly isolated memory."""
        strats_a = self.store.list_strategies(owner="agent_a", caller_role="agent_a")
        strats_b = self.store.list_strategies(owner="agent_b", caller_role="agent_b")
        self.assertEqual(len(strats_a), 5)
        self.assertEqual(len(strats_b), 5)
        self.assertEqual([s.name for s in strats_a], [s.name for s in strats_b])

        trace_a = self._create_dummy_trace("agent_a", passed=True)
        trace_b = self._create_dummy_trace("agent_b", passed=False)

        exp_a = Experience(
            agent_id="agent_a",
            experience_type="success",
            content="Agent A 的私有成功经验：裂项求和用 Fraction 精确核验",
            source_trace_ids=[trace_a.id],
        )
        exp_b = Experience(
            agent_id="agent_b",
            experience_type="failure",
            content="Agent B 的私有失败经验：忽略尾项余项 1/(51*52) 导致误差",
            source_trace_ids=[trace_b.id],
        )
        self.store.add_experience(exp_a, caller_role="agent_a")
        self.store.add_experience(exp_b, caller_role="agent_b")

        # Agent A cannot read Agent B's experiences, traces, or strategies
        with self.assertRaises(PermissionViolationError):
            self.store.list_experiences("agent_b", caller_role="agent_a")
        with self.assertRaises(PermissionViolationError):
            self.store.list_strategies(owner="agent_b", caller_role="agent_a")
        with self.assertRaises(PermissionViolationError):
            self.store.get_trace(trace_b.id, caller_role="agent_a")

        # Agent B cannot read Agent A's experiences, traces, or strategies
        with self.assertRaises(PermissionViolationError):
            self.store.list_experiences("agent_a", caller_role="agent_b")
        with self.assertRaises(PermissionViolationError):
            self.store.list_strategies(owner="agent_a", caller_role="agent_b")
        with self.assertRaises(PermissionViolationError):
            self.store.get_trace(trace_a.id, caller_role="agent_b")

    def test_memory_compression_preserves_provenance(self) -> None:
        """Memory compression merges duplicate experiences under the same agent and retains compressed_from + source_trace_ids."""
        t1 = self._create_dummy_trace("agent_a", passed=True)
        t2 = self._create_dummy_trace("agent_a", passed=True)
        e1 = self.store.add_experience(
            Experience(
                agent_id="agent_a",
                experience_type="success",
                content="分式裂项求和应使用 Python Fractions 检验前 n 项闭合公式",
                source_trace_ids=[t1.id],
            ),
            caller_role="agent_a",
        )
        e2 = self.store.add_experience(
            Experience(
                agent_id="agent_a",
                experience_type="success",
                content="分式裂项求和应使用 Python Fractions 检验前 n 项闭合公式",
                source_trace_ids=[t2.id],
            ),
            caller_role="agent_a",
        )
        compressed = self.store.compress_experiences("agent_a", caller_role="agent_a")
        self.assertEqual(len(compressed), 1)
        self.assertTrue(compressed[0].is_compressed)
        self.assertEqual(set(compressed[0].compressed_from), {e1.id, e2.id})
        self.assertEqual(set(compressed[0].source_trace_ids), {t1.id, t2.id})

    def test_bypassing_strategy_manager_on_store_is_rejected(self) -> None:
        """Requirement 四.1 & 四.2: Bypassing StrategyManager to mutate strategy or proposal state on store must fail."""
        trace_a = self._create_dummy_trace("agent_a", passed=False)
        base_strat = self.store.list_strategies(owner="agent_a", status="active", caller_role="agent_a")[0]

        # Calling public methods on store directly must raise PermissionViolationError even with caller_role='strategy_manager'
        with self.assertRaises(PermissionViolationError):
            self.store.update_strategy_status(base_strat.id, 1, "deprecated", caller_role="strategy_manager")
        with self.assertRaises(PermissionViolationError):
            self.store.save_new_strategy_version(base_strat, caller_role="strategy_manager")

        # Calling private _update_strategy_status / _save_proposal without StrategyManager's private token must also fail
        with self.assertRaises(PermissionViolationError):
            self.store._update_strategy_status(base_strat.id, 1, "deprecated", _manager_token=object())
        with self.assertRaises(PermissionViolationError):
            self.store._save_new_strategy_version(base_strat, _manager_token=None)

        proposal = StrategyProposal(
            target_agent="agent_a",
            base_strategy_id=base_strat.id,
            problem_found="测试绕过 StrategyManager",
            evidence_trace_ids=[trace_a.id],
            structured_changes=StructuredStrategyChange(
                name=base_strat.name + " v2",
                applicable_conditions=base_strat.applicable_conditions,
                execution_steps=base_strat.execution_steps,
                taboos_and_counterexamples=base_strat.taboos_and_counterexamples,
            ),
            expected_benefit="测试",
            applicable_scope="测试",
        )
        with self.assertRaises(PermissionViolationError):
            self.store.save_proposal(proposal, caller_role="strategy_manager")
        with self.assertRaises(PermissionViolationError):
            self.store._save_proposal(proposal, caller_role="strategy_manager", _manager_token=object())

    def test_rejected_to_candidate_and_illegal_transitions_are_rejected(self) -> None:
        """Requirement 四.2: rejected -> candidate (and rejected -> approved on proposal) MUST be rejected."""
        trace_a = self._create_dummy_trace("agent_a", passed=False)
        base_strat = self.store.list_strategies(owner="agent_a", status="active", caller_role="agent_a")[0]

        proposal = StrategyProposal(
            target_agent="agent_a",
            base_strategy_id=base_strat.id,
            problem_found="测试非法状态流转",
            evidence_trace_ids=[trace_a.id],
            structured_changes=StructuredStrategyChange(
                name=base_strat.name + " v2",
                applicable_conditions=base_strat.applicable_conditions,
                execution_steps=base_strat.execution_steps,
                taboos_and_counterexamples=base_strat.taboos_and_counterexamples,
            ),
            expected_benefit="测试",
            applicable_scope="测试",
        )
        prop, strat_v2 = self.manager.register_proposal(proposal, caller_role="reflector")

        # Reject the proposal via Meta-Reflector
        rej_prop, _ = self.manager.apply_meta_review(
            MetaReviewDecision(
                proposal_id=prop.id,
                decision="reject",
                review_comment="样本不足予以驳回",
            ),
            reviewer_role="meta",
        )
        self.assertEqual(rej_prop.status, "rejected")
        v2_rejected = self.store.get_strategy(strat_v2.id, 2)
        assert v2_rejected is not None
        self.assertEqual(v2_rejected.status, "rejected")

        # Attempting rejected -> candidate MUST raise IllegalStateTransitionError
        with self.assertRaises(IllegalStateTransitionError):
            self.manager.transition_strategy_status(
                strat_v2.id,
                2,
                "candidate",
                actor="strategy_manager",
                rationale="试图把已驳回策略强行改回 candidate",
            )

        # Attempting rejected -> active MUST raise IllegalStateTransitionError
        with self.assertRaises(IllegalStateTransitionError):
            self.manager.transition_strategy_status(
                strat_v2.id,
                2,
                "active",
                actor="strategy_manager",
                rationale="试图把已驳回策略强行激活",
            )

        # Attempting rejected -> approved on proposal MUST raise IllegalStateTransitionError
        with self.assertRaises(IllegalStateTransitionError):
            self.manager.transition_proposal_status(
                prop.id,
                "approved",
                actor="strategy_manager",
                rationale="试图把已驳回提案强行改回 approved",
            )

    def test_sample_size_and_token_exemption_judgment_rules(self) -> None:
        """Requirement 五.1 & 五.2: Sample size < 30 must fail judgment; token exemption defaults to False."""
        trace_a = self._create_dummy_trace("agent_a", passed=False)
        base_strat = self.store.list_strategies(owner="agent_a", status="active", caller_role="agent_a")[0]

        # Case 1: Default min_sample_runs_per_group = 30. Running only 2 traces per group MUST fail with '样本不足，不予晋升'
        prop1 = StrategyProposal(
            target_agent="agent_a",
            base_strategy_id=base_strat.id,
            problem_found="测试小样本拒绝晋升",
            evidence_trace_ids=[trace_a.id],
            structured_changes=StructuredStrategyChange(
                name=base_strat.name + " v2",
                applicable_conditions=base_strat.applicable_conditions,
                execution_steps=base_strat.execution_steps,
                taboos_and_counterexamples=base_strat.taboos_and_counterexamples,
            ),
            expected_benefit="测试",
            applicable_scope="测试",
        )
        prop1, strat_v2 = self.manager.register_proposal(prop1, caller_role="reflector")
        _, exp_task1 = self.manager.apply_meta_review(
            MetaReviewDecision(
                proposal_id=prop1.id,
                decision="approve",
                review_comment="批准实验",
                benchmark_problem_ids=["seq_03", "calc_01"],
                runs_per_problem_k=1,
            ),
            reviewer_role="meta",
        )
        assert exp_task1 is not None
        self.assertEqual(exp_task1.metrics_and_thresholds.min_sample_runs_per_group, 30)
        self.assertFalse(exp_task1.metrics_and_thresholds.allow_token_exemption_on_high_pass_gain)

        rep1 = self.manager.judge_experiment(
            exp_task1,
            old_run_trace_ids=[trace_a.id, trace_a.id],
            new_run_trace_ids=[trace_a.id, trace_a.id],
            old_pass_rate=0.0,
            new_pass_rate=1.0,
            old_avg_tokens=400.0,
            new_avg_tokens=410.0,
            old_avg_elapsed_ms=500.0,
            new_avg_elapsed_ms=510.0,
            problem_breakdown=[],
        )
        self.assertFalse(rep1.rule_judgment.passed)
        self.assertFalse(rep1.rule_judgment.sample_size_ok)
        self.assertIn("样本不足，不予晋升", rep1.rule_judgment.reason)
        v2_after_small_sample = self.store.get_strategy(strat_v2.id, 2)
        assert v2_after_small_sample is not None
        self.assertEqual(v2_after_small_sample.status, "rejected")

        # Case 2: Sample size >= 30 (30 runs per group), pass rate gain +50%, but token increase +35% (> 20%)
        # With allow_token_exemption_on_high_pass_gain = False (default), MUST fail!
        prop2 = StrategyProposal(
            target_agent="agent_a",
            base_strategy_id=base_strat.id,
            problem_found="测试默认禁用 Token 豁免",
            evidence_trace_ids=[trace_a.id],
            structured_changes=StructuredStrategyChange(
                name=base_strat.name + " v3",
                applicable_conditions=base_strat.applicable_conditions,
                execution_steps=base_strat.execution_steps,
                taboos_and_counterexamples=base_strat.taboos_and_counterexamples,
            ),
            expected_benefit="测试",
            applicable_scope="测试",
        )
        prop2, strat_v3 = self.manager.register_proposal(prop2, caller_role="reflector")
        _, exp_task2 = self.manager.apply_meta_review(
            MetaReviewDecision(
                proposal_id=prop2.id,
                decision="approve",
                review_comment="批准 30 次样本实验",
                benchmark_problem_ids=["seq_03", "calc_01"],
                runs_per_problem_k=15,
            ),
            reviewer_role="meta",
        )
        assert exp_task2 is not None
        thirty_tids = [trace_a.id] * 30
        rep2 = self.manager.judge_experiment(
            exp_task2,
            old_run_trace_ids=thirty_tids,
            new_run_trace_ids=thirty_tids,
            old_pass_rate=0.40,
            new_pass_rate=0.90,
            old_avg_tokens=400.0,
            new_avg_tokens=540.0,  # +35% > 20%
            old_avg_elapsed_ms=500.0,
            new_avg_elapsed_ms=600.0,
            problem_breakdown=[],
        )
        self.assertTrue(rep2.rule_judgment.sample_size_ok)
        self.assertFalse(rep2.rule_judgment.passed)
        self.assertFalse(rep2.rule_judgment.used_token_exemption)
        self.assertIn("高通过率豁免未启用", rep2.rule_judgment.reason)

    def test_strategy_manager_state_machine_permissions_and_rollback(self) -> None:
        """Full valid lifecycle when sample size >= 30 and token increase <= 20%, followed by one-click rollback."""
        trace_a = self._create_dummy_trace("agent_a", passed=False)
        base_strat = self.store.list_strategies(owner="agent_a", status="active", caller_role="agent_a")[0]

        with self.assertRaises(PermissionViolationError):
            self.manager.transition_strategy_status(
                base_strat.id, 1, "deprecated", actor="reflector", rationale="越权修改"
            )

        proposal = StrategyProposal(
            target_agent="agent_a",
            base_strategy_id=base_strat.id,
            problem_found="小规模数值实验后未对高阶余项做符号等价核对",
            evidence_trace_ids=[trace_a.id],
            structured_changes=StructuredStrategyChange(
                name=base_strat.name + " v2",
                applicable_conditions=base_strat.applicable_conditions,
                execution_steps=base_strat.execution_steps + ["步骤 4：对通项公式与前 20 项部分和做精确有理数恒等核验。"],
                taboos_and_counterexamples=base_strat.taboos_and_counterexamples,
                change_summary="新增第 4 步精确有理数恒等核验",
            ),
            expected_benefit="消除裂项余项漏项错误",
            applicable_scope="数列求和与微积分",
        )
        prop, strat_v2 = self.manager.register_proposal(proposal, caller_role="reflector")
        self.assertEqual(strat_v2.version, 2)
        self.assertEqual(strat_v2.status, "proposed")

        with self.assertRaises(IllegalStateTransitionError):
            self.manager.transition_strategy_status(
                strat_v2.id, 2, "active", actor="strategy_manager", rationale="非法跳级激活"
            )

        rev_prop, rev_task = self.manager.apply_meta_review(
            MetaReviewDecision(
                proposal_id=prop.id,
                decision="request_revision",
                review_comment="请补充适用边界说明",
            ),
            reviewer_role="meta",
        )
        self.assertEqual(rev_prop.status, "revision_requested")
        self.assertIsNone(rev_task)

        app_prop, exp_task = self.manager.apply_meta_review(
            MetaReviewDecision(
                proposal_id=prop.id,
                decision="approve",
                review_comment="证据充分，批准实验",
                benchmark_problem_ids=["seq_03", "calc_01"],
                runs_per_problem_k=15,
            ),
            reviewer_role="meta",
        )
        self.assertEqual(app_prop.status, "approved")
        assert exp_task is not None

        t_old = self._create_dummy_trace("agent_a", passed=False)
        t_new = self._create_dummy_trace("agent_a", passed=True)
        report = self.manager.judge_experiment(
            exp_task,
            old_run_trace_ids=[t_old.id] * 30,
            new_run_trace_ids=[t_new.id] * 30,
            old_pass_rate=0.5,
            new_pass_rate=0.8,
            old_avg_tokens=400.0,
            new_avg_tokens=440.0,
            old_avg_elapsed_ms=800.0,
            new_avg_elapsed_ms=850.0,
            problem_breakdown=[],
        )
        self.assertTrue(report.rule_judgment.passed)
        strat_candidate = self.store.get_strategy(strat_v2.id, 2)
        assert strat_candidate is not None
        self.assertEqual(strat_candidate.status, "candidate")

        activated = self.manager.finalize_candidate_strategy(
            report.id,
            meta_approved=True,
            meta_review_comment="30 次样本实验通过率提升且 Token 增幅仅 10%，准予激活。",
        )
        self.assertEqual(activated.status, "active")
        v1_after_activate = self.store.get_strategy(strat_v2.id, 1)
        assert v1_after_activate is not None
        self.assertEqual(v1_after_activate.status, "deprecated")

        rolled_back = self.manager.rollback_strategy(
            strat_v2.id, 2, actor="human", rationale="测试一键回滚至 v1"
        )
        self.assertEqual(rolled_back.status, "rolled_back")
        v1_restored = self.store.get_strategy(strat_v2.id, 1)
        assert v1_restored is not None
        self.assertEqual(v1_restored.status, "active")

    def test_parse_failures_do_not_use_default_fallbacks(self) -> None:
        """Requirement 三.1 & 三.2: Reflector and Meta-Reflector parse failures must fail honestly without defaults."""
        trace_a = self._create_dummy_trace("agent_a", passed=False)

        # 1. Reflector receives broken JSON -> raises ReflectionParseError, creates 0 proposals, records raw output
        bad_reflector = ReflectorAgent(
            self.store,
            self.manager,
            model_router=_DummyFakeRouter("NOT_VALID_JSON_AT_ALL"),  # type: ignore[arg-type]
        )
        with self.assertRaises(ReflectionParseError):
            asyncio.run(bad_reflector.reflect_on_trace(trace_a.id))
        self.assertEqual(len(self.store.list_proposals()), 0)
        audits = self.store.list_audit_logs(limit=10)
        self.assertEqual(audits[0].target_type, "parse_error")
        self.assertEqual(audits[0].metadata.get("raw_model_output"), "NOT_VALID_JSON_AT_ALL")

        # 2. Meta-Reflector receives invalid decision -> sets proposal to review_failed, never defaults to approve
        base_strat = self.store.list_strategies(owner="agent_a", status="active", caller_role="agent_a")[0]
        prop = StrategyProposal(
            target_agent="agent_a",
            base_strategy_id=base_strat.id,
            problem_found="真实问题诊断",
            evidence_trace_ids=[trace_a.id],
            structured_changes=StructuredStrategyChange(
                name=base_strat.name + " v2",
                applicable_conditions=base_strat.applicable_conditions,
                execution_steps=base_strat.execution_steps,
                taboos_and_counterexamples=base_strat.taboos_and_counterexamples,
            ),
            expected_benefit="预期收益",
            applicable_scope="适用范围",
        )
        prop, _ = self.manager.register_proposal(prop, caller_role="reflector")

        bad_meta = MetaReflectorAgent(
            self.store,
            self.manager,
            model_router=_DummyFakeRouter('{"decision": "invalid_choice", "review_comment": ""}'),  # type: ignore[arg-type]
        )
        dec, failed_prop, exp_task = asyncio.run(bad_meta.review_proposal(prop.id))
        self.assertIsNone(dec)
        self.assertIsNone(exp_task)
        self.assertEqual(failed_prop.status, "review_failed")
        self.assertEqual(failed_prop.raw_model_output, '{"decision": "invalid_choice", "review_comment": ""}')

    def test_meta_reflector_cannot_self_approve_own_strategy(self) -> None:
        """Hard Principle 7: Meta-Reflector cannot self-approve its own strategy proposal."""
        trace_a = self._create_dummy_trace("agent_a", passed=False)
        meta_strat = self.store.list_strategies(owner="meta", status="active")[0]
        meta_prop = StrategyProposal(
            target_agent="meta",
            base_strategy_id=meta_strat.id,
            problem_found="元反思需要强化对 Token 增幅的审查",
            evidence_trace_ids=[trace_a.id],
            structured_changes=StructuredStrategyChange(
                name=meta_strat.name + " v2",
                applicable_conditions=meta_strat.applicable_conditions,
                execution_steps=meta_strat.execution_steps,
                taboos_and_counterexamples=meta_strat.taboos_and_counterexamples,
            ),
            expected_benefit="更严格控制 Token",
            applicable_scope="元反思审批",
            proposed_by="meta",
        )
        meta_prop, _ = self.manager.register_proposal(meta_prop, caller_role="meta")
        with self.assertRaises(PermissionViolationError):
            self.manager.apply_meta_review(
                MetaReviewDecision(
                    proposal_id=meta_prop.id,
                    decision="approve",
                    review_comment="元反思试图自己批准自己",
                ),
                reviewer_role="meta",
            )

    def test_benchmark_and_verifiers(self) -> None:
        problems = load_benchmark_problems()
        self.assertEqual(len(problems), 46)
        r1 = verify_sympy_equiv("推导完毕。\nFINAL_ANSWER: 3*pi/512", "3*pi/512")
        self.assertTrue(r1.passed)
        r2 = verify_numeric("计算得行列式为 4。\nFINAL_ANSWER: 4", "4")
        self.assertTrue(r2.passed)
        r3 = verify_numeric("FINAL_ANSWER: 999", "4")
        self.assertFalse(r3.passed)
        # Exact large integer check (off by 1 must fail!)
        r4 = verify_numeric("FINAL_ANSWER: 166666666661", "166666666660")
        self.assertFalse(r4.passed)
        r5 = verify_numeric("FINAL_ANSWER: 166666666660", "166666666660")
        self.assertTrue(r5.passed)


if __name__ == "__main__":
    unittest.main()
