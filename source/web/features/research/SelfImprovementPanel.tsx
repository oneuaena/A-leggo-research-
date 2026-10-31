"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  approveSelfImprovementReport,
  compressSelfImprovementExperiences,
  getSelfImprovementDashboard,
  getSelfImprovementProgress,
  getSelfImprovementTrace,
  reviewSelfImprovementProposal,
  rollbackSelfImprovementStrategy,
  solveSelfImprovementProblem,
  SIDashboardSnapshot,
  SIExperience,
  SIProgress,
  SIStrategy,
  SITaskTrace,
  triggerSelfImprovementEvolutionRound,
  updateSelfImprovementSettings,
} from "@/lib/research-api";

const PIPELINE_STAGES = [
  { key: "executing", label: "1. 执行", desc: "选策略并调工具解题" },
  { key: "recording", label: "2. 记录", desc: "追加轨迹与私有经验" },
  { key: "reflecting", label: "3. 反思", desc: "诊断缺陷与根因分析" },
  { key: "proposing", label: "4. 上报", desc: "生成结构化改进申请" },
  { key: "reviewing", label: "5. 审批", desc: "元反思审查与派发实验" },
  { key: "practicing", label: "6. 实践", desc: "新旧策略对照基准实验" },
  { key: "verifying", label: "7. 验证", desc: "独立数学校验与规则判定" },
  { key: "updating", label: "8. 更新", desc: "版本晋升与审计归档" },
] as const;

function statusBadgeColor(status: string): { bg: string; fg: string; border: string } {
  switch (status) {
    case "active":
    case "approved":
    case "completed":
      return { bg: "rgba(16, 185, 129, 0.14)", fg: "#10b981", border: "rgba(16, 185, 129, 0.35)" };
    case "candidate":
    case "approved_for_trial":
      return { bg: "rgba(59, 130, 246, 0.14)", fg: "#3b82f6", border: "rgba(59, 130, 246, 0.35)" };
    case "proposed":
    case "pending":
    case "running":
      return { bg: "rgba(245, 158, 11, 0.14)", fg: "#f59e0b", border: "rgba(245, 158, 11, 0.35)" };
    case "revision_requested":
    case "deprecated":
    case "rolled_back":
      return { bg: "rgba(168, 85, 247, 0.14)", fg: "#a855f7", border: "rgba(168, 85, 247, 0.35)" };
    case "rejected":
    case "review_failed":
    case "failed":
      return { bg: "rgba(239, 68, 68, 0.14)", fg: "#ef4444", border: "rgba(239, 68, 68, 0.35)" };
    default:
      return { bg: "rgba(148, 163, 184, 0.14)", fg: "#94a3b8", border: "rgba(148, 163, 184, 0.3)" };
  }
}

export function SelfImprovementPanel({ onClose }: { onClose?: () => void }) {
  const [dashboard, setDashboard] = useState<SIDashboardSnapshot | null>(null);
  const [progress, setProgress] = useState<SIProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [activeTab, setActiveTab] = useState<
    "strategies" | "proposals" | "experiments" | "experiences" | "traces" | "audit"
  >("strategies");
  const [strategyOwnerTab, setStrategyOwnerTab] = useState<"agent_a" | "agent_b" | "meta">("agent_a");
  const [selectedStrategy, setSelectedStrategy] = useState<SIStrategy | null>(null);
  const [inspectedTrace, setInspectedTrace] = useState<SITaskTrace | null>(null);

  // Evolution runner controls
  const [evolveAgent, setEvolveAgent] = useState<"agent_a" | "agent_b">("agent_a");
  const [evolveProblemIds, setEvolveProblemIds] = useState<string[]>([]);
  const [evolveBenchmarkCount, setEvolveBenchmarkCount] = useState<number>(2);
  const [evolveRunsK, setEvolveRunsK] = useState<number>(3);
  const [busyAction, setBusyAction] = useState(false);

  const loadDashboard = useCallback(async () => {
    try {
      const snap = await getSelfImprovementDashboard();
      setDashboard(snap);
      setProgress(snap.progress);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载自我改进面板失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  // Poll progress while an evolution round is running
  useEffect(() => {
    if (!progress?.running) return;
    const timer = window.setInterval(async () => {
      try {
        const p = await getSelfImprovementProgress();
        setProgress(p);
        if (!p.running) {
          await loadDashboard();
        }
      } catch {
        // ignore transient poll error
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [progress?.running, loadDashboard]);

  const handleTriggerEvolution = async () => {
    setBusyAction(true);
    setError("");
    setNotice("");
    try {
      const res = await triggerSelfImprovementEvolutionRound({
        agent_id: evolveAgent,
        initial_problem_ids: evolveProblemIds,
        benchmark_count: evolveBenchmarkCount,
        runs_per_problem_k: evolveRunsK,
      });
      setProgress(res.progress);
      setNotice(`已启动 ${evolveAgent === "agent_a" ? "执行 Agent A" : "执行 Agent B"} 的完整自我进化闭环...`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动进化闭环失败");
    } finally {
      setBusyAction(false);
    }
  };

  const handleRunSelectedOnly = async () => {
    if (!evolveProblemIds.length) return;
    setBusyAction(true);
    setError("");
    setNotice("");
    let passed = 0;
    let failed = 0;
    let errors = 0;
    for (let index = 0; index < evolveProblemIds.length; index += 1) {
      const problemId = evolveProblemIds[index];
      setNotice(`只执行并记录 ${index + 1}/${evolveProblemIds.length}：${problemId}（不触发反思或策略变更）`);
      try {
        const trace = await solveSelfImprovementProblem({ agent_id: evolveAgent, problem_id: problemId });
        if (trace.verification_result.passed) passed += 1;
        else failed += 1;
      } catch {
        errors += 1;
      }
    }
    await loadDashboard();
    setNotice(`纯评测完成：判分通过 ${passed}、未通过 ${failed}、请求错误 ${errors}；未触发反思或策略变更。`);
    setBusyAction(false);
  };

  const handleToggleHumanApproval = async (checked: boolean) => {
    if (!dashboard) return;
    setBusyAction(true);
    try {
      await updateSelfImprovementSettings({ require_human_approval: checked });
      await loadDashboard();
      setNotice(
        checked
          ? "已开启人工审批开关：candidate 策略需人工确认后才激活为 active。"
          : "已关闭人工审批开关：实验验证与元反思通过后自动激活策略。"
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新审批设置失败");
    } finally {
      setBusyAction(false);
    }
  };

  const handleRollback = async (strategyId: string, version: number) => {
    setBusyAction(true);
    setError("");
    try {
      const res = await rollbackSelfImprovementStrategy({
        strategy_id: strategyId,
        version,
        actor: "human",
        rationale: `面板手动一键回滚策略 ${strategyId} v${version} 至上一版本`,
      });
      await loadDashboard();
      if (res.restored_active) {
        setSelectedStrategy(res.restored_active);
      }
      setNotice(
        `策略 ${strategyId} v${version} 已回滚（rolled_back），恢复激活版本 v${res.restored_active?.version ?? "-"}`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "回滚策略失败");
    } finally {
      setBusyAction(false);
    }
  };

  const handleReviewProposal = async (
    proposalId: string,
    decision: "approve" | "reject" | "request_revision"
  ) => {
    setBusyAction(true);
    setError("");
    try {
      await reviewSelfImprovementProposal(proposalId, {
        decision,
        reviewer_role: "human",
        review_comment: `面板人工审批操作：${decision}`,
      });
      await loadDashboard();
      setNotice(`申请 ${proposalId} 已执行操作：${decision}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "审批申请失败");
    } finally {
      setBusyAction(false);
    }
  };

  const handleApproveReport = async (reportId: string, approved: boolean) => {
    setBusyAction(true);
    setError("");
    try {
      const res = await approveSelfImprovementReport(reportId, {
        approved,
        comment: approved ? "人工确认实验报告通过，准予晋升为 active" : "人工驳回候选策略",
      });
      await loadDashboard();
      setNotice(`策略 ${res.strategy.id} v${res.strategy.version} 状态已更新为 ${res.strategy.status}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "实验报告确认失败");
    } finally {
      setBusyAction(false);
    }
  };

  const handleCompressExperiences = async (agentId: "agent_a" | "agent_b") => {
    setBusyAction(true);
    setError("");
    try {
      const res = await compressSelfImprovementExperiences(agentId);
      await loadDashboard();
      setNotice(
        res.compressed_count > 0
          ? `${agentId} 已归档压缩 ${res.compressed_count} 组同类经验（保留全部原始轨迹 ID）`
          : `${agentId} 当前暂无需要合并的同类经验（同策略同类型满 2 条触发压缩）`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "压缩经验失败");
    } finally {
      setBusyAction(false);
    }
  };

  const handleOpenTrace = async (traceId: string) => {
    const local = dashboard?.traces.find((t) => t.id === traceId);
    if (local) {
      setInspectedTrace(local);
      return;
    }
    try {
      const fetched = await getSelfImprovementTrace(traceId);
      setInspectedTrace(fetched);
    } catch (err) {
      setError(err instanceof Error ? err.message : `无法加载轨迹 ${traceId}`);
    }
  };

  const currentStrategyTrees = useMemo(() => {
    if (!dashboard) return [];
    return dashboard.strategy_trees[strategyOwnerTab] || [];
  }, [dashboard, strategyOwnerTab]);

  const activeStageIndex = useMemo(() => {
    if (!progress?.stage) return -1;
    if (progress.stage === "completed") return PIPELINE_STAGES.length;
    return PIPELINE_STAGES.findIndex((s) => s.key === progress.stage);
  }, [progress?.stage]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        width: "100%",
        background: "var(--bg-primary, #0b0f17)",
        color: "var(--text-primary, #e2e8f0)",
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
        overflow: "hidden",
      }}
    >
      {/* Top Header Bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 20px",
          borderBottom: "1px solid rgba(148, 163, 184, 0.16)",
          background: "rgba(15, 23, 42, 0.85)",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: "32px",
              height: "32px",
              borderRadius: "8px",
              background: "rgba(59, 130, 246, 0.18)",
              color: "#60a5fa",
              fontWeight: 700,
              fontSize: "16px",
            }}
          >
            ⟳
          </span>
          <div>
            <div style={{ fontSize: "16px", fontWeight: 700, letterSpacing: "0.01em" }}>
              A-leggo 研究端 · 自我改进闭环中枢
            </div>
            <div style={{ fontSize: "12px", color: "#94a3b8" }}>
              执行 (Agent A/B) → 记录 (TaskTrace) → 反思 (Reflector) → 上报 (Proposal) → 审批 (Meta) → 实践 (Practitioner) → 验证 (Verifier) → 更新 (StrategyManager)
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap" }}>
          {/* Human Approval Switch */}
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "8px",
              padding: "6px 12px",
              borderRadius: "8px",
              background: "rgba(30, 41, 59, 0.8)",
              border: "1px solid rgba(148, 163, 184, 0.2)",
              fontSize: "12px",
              cursor: "pointer",
            }}
            title="开启时 candidate -> active 需人工确认；关闭时实验通过后自动激活"
          >
            <input
              type="checkbox"
              checked={Boolean(dashboard?.settings.require_human_approval)}
              onChange={(e) => void handleToggleHumanApproval(e.target.checked)}
              disabled={busyAction}
            />
            <span>人工审批门禁</span>
            <strong
              style={{
                color: dashboard?.settings.require_human_approval ? "#f59e0b" : "#10b981",
              }}
            >
              {dashboard?.settings.require_human_approval ? "开启（需人工确认）" : "关闭（全自动闭环）"}
            </strong>
          </label>

          <button
            type="button"
            onClick={() => void loadDashboard()}
            style={{
              padding: "6px 12px",
              borderRadius: "8px",
              border: "1px solid rgba(148, 163, 184, 0.25)",
              background: "rgba(30, 41, 59, 0.7)",
              color: "#e2e8f0",
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            刷新数据
          </button>

          {onClose && (
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: "6px 14px",
                borderRadius: "8px",
                border: "1px solid rgba(148, 163, 184, 0.3)",
                background: "rgba(51, 65, 85, 0.6)",
                color: "#f8fafc",
                fontSize: "12px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              返回研究工作台
            </button>
          )}
        </div>
      </div>

      {/* Live Evolution Runner & 8-Stage Pipeline Bar */}
      <div
        style={{
          padding: "14px 20px",
          borderBottom: "1px solid rgba(148, 163, 184, 0.14)",
          background: "rgba(15, 23, 42, 0.55)",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: "10px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "12px", fontWeight: 600, color: "#94a3b8" }}>进化实验配置：</span>
            <select
              value={evolveAgent}
              onChange={(e) => setEvolveAgent(e.target.value as "agent_a" | "agent_b")}
              style={{
                padding: "5px 10px",
                borderRadius: "6px",
                background: "#1e293b",
                color: "#f8fafc",
                border: "1px solid rgba(148, 163, 184, 0.25)",
                fontSize: "12px",
              }}
            >
              <option value="agent_a">执行 Agent A</option>
              <option value="agent_b">执行 Agent B</option>
            </select>

            <select
              aria-label="选择当前实验划分题目，可多选"
              multiple
              size={5}
              value={evolveProblemIds}
              onChange={(e) => setEvolveProblemIds(Array.from(e.target.selectedOptions, (option) => option.value))}
              style={{
                padding: "5px 10px",
                borderRadius: "6px",
                background: "#1e293b",
                color: "#f8fafc",
                border: "1px solid rgba(148, 163, 184, 0.25)",
                fontSize: "12px",
                minWidth: "300px",
              }}
            >
              {(dashboard?.benchmark_problems || []).map((p) => (
                <option key={p.id} value={p.id}>
                  [{p.id}] {p.title} ({p.category})
                </option>
              ))}
            </select>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <button type="button" disabled={busyAction || !dashboard?.benchmark_problems.length}
                onClick={() => setEvolveProblemIds((dashboard?.benchmark_problems || []).map((p) => p.id))}>选择当前划分全部题目</button>
              <button type="button" disabled={busyAction || !evolveProblemIds.length}
                onClick={() => setEvolveProblemIds([])}>清除选择</button>
              <small>{evolveProblemIds.length ? `已选 ${evolveProblemIds.length} 题` : "请选择题目；不会默认读取全题库"}</small>
            </div>

            <label style={{ fontSize: "12px", color: "#cbd5e1", display: "flex", alignItems: "center", gap: "4px" }}>
              对照题数:
              <input
                type="number"
                min={1}
                max={6}
                value={evolveBenchmarkCount}
                onChange={(e) => setEvolveBenchmarkCount(Math.max(1, Number(e.target.value) || 1))}
                style={{
                  width: "48px",
                  padding: "4px 6px",
                  borderRadius: "6px",
                  background: "#1e293b",
                  color: "#f8fafc",
                  border: "1px solid rgba(148, 163, 184, 0.25)",
                  fontSize: "12px",
                }}
              />
            </label>

            <label style={{ fontSize: "12px", color: "#cbd5e1", display: "flex", alignItems: "center", gap: "4px" }}>
              重复 k 次:
              <input
                type="number"
                min={1}
                max={20}
                value={evolveRunsK}
                onChange={(e) => setEvolveRunsK(Math.max(1, Number(e.target.value) || 1))}
                style={{
                  width: "48px",
                  padding: "4px 6px",
                  borderRadius: "6px",
                  background: "#1e293b",
                  color: "#f8fafc",
                  border: "1px solid rgba(148, 163, 184, 0.25)",
                  fontSize: "12px",
                }}
              />
            </label>

            <button
              type="button"
              disabled={busyAction || Boolean(progress?.running) || !evolveProblemIds.length}
              onClick={() => void handleTriggerEvolution()}
              style={{
                padding: "6px 16px",
                borderRadius: "8px",
                border: "1px solid rgba(59, 130, 246, 0.5)",
                background: progress?.running
                  ? "rgba(59, 130, 246, 0.25)"
                  : "linear-gradient(135deg, #2563eb, #1d4ed8)",
                color: "#ffffff",
                fontSize: "12px",
                fontWeight: 600,
                cursor: progress?.running ? "not-allowed" : "pointer",
              }}
            >
              {progress?.running ? `正在运行：${progress.stage}...` : "▶ 运行一轮进化"}
            </button>
            <button
              type="button"
              disabled={busyAction || Boolean(progress?.running) || !evolveProblemIds.length}
              onClick={() => void handleRunSelectedOnly()}
              style={{
                padding: "6px 12px",
                borderRadius: "8px",
                border: "1px solid rgba(148, 163, 184, 0.35)",
                background: "rgba(148, 163, 184, 0.12)",
                color: "#e2e8f0",
                fontSize: "12px",
                cursor: busyAction ? "not-allowed" : "pointer",
              }}
            >
              ▶ 只评测并记录（不反思）
            </button>
          </div>

          {/* Summary Metrics */}
          {dashboard && (
            <div style={{ display: "flex", alignItems: "center", gap: "14px", fontSize: "12px", color: "#cbd5e1" }}>
              <span>
                总轨迹: <strong>{dashboard.summary.total_traces}</strong> (A:{dashboard.summary.agent_a_traces} / B:
                {dashboard.summary.agent_b_traces})
              </span>
              <span>
                改进申请: <strong>{dashboard.summary.total_proposals}</strong>
              </span>
              <span>
                对照实验: <strong>{dashboard.summary.total_experiments}</strong>
              </span>
              <span>
                审计记录: <strong>{dashboard.summary.total_audit_logs}</strong>
              </span>
            </div>
          )}
        </div>

        {/* 8-Stage Pipeline Visual Progress */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(8, minmax(110px, 1fr))",
            gap: "8px",
            overflowX: "auto",
          }}
        >
          {PIPELINE_STAGES.map((stage, idx) => {
            const isCurrent = progress?.running && activeStageIndex === idx;
            const isDone =
              progress?.stage === "completed" || (activeStageIndex > idx && activeStageIndex !== -1);
            return (
              <div
                key={stage.key}
                style={{
                  padding: "8px 10px",
                  borderRadius: "8px",
                  border: isCurrent
                    ? "1px solid #3b82f6"
                    : isDone
                    ? "1px solid rgba(16, 185, 129, 0.4)"
                    : "1px solid rgba(148, 163, 184, 0.16)",
                  background: isCurrent
                    ? "rgba(59, 130, 246, 0.18)"
                    : isDone
                    ? "rgba(16, 185, 129, 0.08)"
                    : "rgba(30, 41, 59, 0.45)",
                }}
              >
                <div
                  style={{
                    fontSize: "12px",
                    fontWeight: 700,
                    color: isCurrent ? "#60a5fa" : isDone ? "#10b981" : "#e2e8f0",
                  }}
                >
                  {stage.label}
                </div>
                <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "2px" }}>{stage.desc}</div>
              </div>
            );
          })}
        </div>

        {progress?.message && (
          <div style={{ fontSize: "12px", color: progress.error ? "#ef4444" : "#93c5fd" }}>
            实时状态：{progress.message}
          </div>
        )}
      </div>

      {/* Alerts */}
      {(error || notice) && (
        <div
          style={{
            padding: "8px 20px",
            background: error ? "rgba(239, 68, 68, 0.15)" : "rgba(16, 185, 129, 0.14)",
            borderBottom: "1px solid rgba(148, 163, 184, 0.15)",
            color: error ? "#fca5a5" : "#6ee7b7",
            fontSize: "12px",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{error || notice}</span>
          <button
            type="button"
            onClick={() => {
              setError("");
              setNotice("");
            }}
            style={{
              background: "transparent",
              border: "none",
              color: "inherit",
              cursor: "pointer",
              fontSize: "12px",
            }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Navigation Tabs */}
      <div
        style={{
          display: "flex",
          gap: "6px",
          padding: "10px 20px",
          borderBottom: "1px solid rgba(148, 163, 184, 0.14)",
          background: "rgba(15, 23, 42, 0.4)",
          flexWrap: "wrap",
        }}
      >
        {[
          { id: "strategies", label: "1. 策略版本树与一键回滚" },
          { id: "proposals", label: `2. 申请与审批队列 (${dashboard?.proposals.length ?? 0})` },
          { id: "experiments", label: `3. 对照实验报告 (${dashboard?.experiment_reports.length ?? 0})` },
          { id: "experiences", label: "4. Agent A / B 经验差异对比" },
          { id: "traces", label: `5. 原始执行轨迹库 (${dashboard?.traces.length ?? 0})` },
          { id: "audit", label: `6. 审计日志 (${dashboard?.audit_logs.length ?? 0})` },
        ].map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id as typeof activeTab)}
            style={{
              padding: "7px 14px",
              borderRadius: "8px",
              border:
                activeTab === tab.id
                  ? "1px solid rgba(59, 130, 246, 0.6)"
                  : "1px solid rgba(148, 163, 184, 0.15)",
              background: activeTab === tab.id ? "rgba(59, 130, 246, 0.2)" : "rgba(30, 41, 59, 0.5)",
              color: activeTab === tab.id ? "#60a5fa" : "#cbd5e1",
              fontSize: "12px",
              fontWeight: activeTab === tab.id ? 700 : 500,
              cursor: "pointer",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Main Content Area */}
      <div style={{ flex: 1, overflowY: "auto", padding: "18px 20px" }}>
        {loading && !dashboard ? (
          <div style={{ padding: "40px", textAlign: "center", color: "#94a3b8" }}>正在加载自我改进数据...</div>
        ) : null}

        {/* TAB 1: Strategy Version Trees & Rollback */}
        {activeTab === "strategies" && dashboard && (
          <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: "18px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                {[
                  { key: "agent_a", label: "执行 Agent A 策略库" },
                  { key: "agent_b", label: "执行 Agent B 策略库" },
                  { key: "meta", label: "元反思 Agent 策略库" },
                ].map((owner) => (
                  <button
                    key={owner.key}
                    type="button"
                    onClick={() => {
                      setStrategyOwnerTab(owner.key as typeof strategyOwnerTab);
                      setSelectedStrategy(null);
                    }}
                    style={{
                      padding: "6px 12px",
                      borderRadius: "6px",
                      border:
                        strategyOwnerTab === owner.key
                          ? "1px solid #3b82f6"
                          : "1px solid rgba(148, 163, 184, 0.2)",
                      background:
                        strategyOwnerTab === owner.key ? "rgba(59, 130, 246, 0.18)" : "rgba(30, 41, 59, 0.6)",
                      color: strategyOwnerTab === owner.key ? "#93c5fd" : "#cbd5e1",
                      fontSize: "12px",
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    {owner.label}
                  </button>
                ))}
              </div>

              {currentStrategyTrees.map((tree) => (
                <div
                  key={tree.strategy_id}
                  style={{
                    padding: "14px",
                    borderRadius: "10px",
                    border: "1px solid rgba(148, 163, 184, 0.18)",
                    background: "rgba(15, 23, 42, 0.65)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <div style={{ fontSize: "14px", fontWeight: 700, color: "#f8fafc" }}>{tree.name}</div>
                      <div style={{ fontSize: "11px", color: "#94a3b8", fontFamily: "monospace" }}>
                        ID: {tree.strategy_id} · 当前激活版本: {tree.active_version ? `v${tree.active_version}` : "无"}
                      </div>
                    </div>
                  </div>

                  {/* Version Chain */}
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      marginTop: "12px",
                      flexWrap: "wrap",
                    }}
                  >
                    {tree.versions.map((ver, idx) => {
                      const badge = statusBadgeColor(ver.status);
                      const isSelected =
                        selectedStrategy?.id === ver.id && selectedStrategy?.version === ver.version;
                      return (
                        <div key={`${ver.id}_v${ver.version}`} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                          {idx > 0 && <span style={{ color: "#64748b", fontSize: "12px" }}>→</span>}
                          <button
                            type="button"
                            onClick={() => setSelectedStrategy(ver)}
                            style={{
                              padding: "6px 10px",
                              borderRadius: "8px",
                              border: isSelected ? "2px solid #60a5fa" : `1px solid ${badge.border}`,
                              background: badge.bg,
                              color: badge.fg,
                              fontSize: "12px",
                              fontWeight: 600,
                              cursor: "pointer",
                              textAlign: "left",
                            }}
                          >
                            v{ver.version} · {ver.status}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>

            {/* Strategy Detail & Rollback Inspector */}
            <div
              style={{
                padding: "16px",
                borderRadius: "10px",
                border: "1px solid rgba(148, 163, 184, 0.2)",
                background: "rgba(15, 23, 42, 0.75)",
                alignSelf: "start",
              }}
            >
              {selectedStrategy ? (
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <div style={{ fontSize: "15px", fontWeight: 700 }}>
                        {selectedStrategy.name} (v{selectedStrategy.version})
                      </div>
                      <div style={{ fontSize: "11px", color: "#94a3b8", fontFamily: "monospace", marginTop: "2px" }}>
                        {selectedStrategy.id} · owner={selectedStrategy.owner} · status={selectedStrategy.status}
                      </div>
                    </div>

                    {selectedStrategy.status === "active" && selectedStrategy.parent_version !== null && (
                      <button
                        type="button"
                        disabled={busyAction}
                        onClick={() => void handleRollback(selectedStrategy.id, selectedStrategy.version)}
                        style={{
                          padding: "6px 12px",
                          borderRadius: "8px",
                          border: "1px solid rgba(239, 68, 68, 0.5)",
                          background: "rgba(239, 68, 68, 0.16)",
                          color: "#fca5a5",
                          fontSize: "12px",
                          fontWeight: 600,
                          cursor: "pointer",
                        }}
                      >
                        ↺ 一键回滚至 v{selectedStrategy.parent_version}
                      </button>
                    )}
                  </div>

                  <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                    <strong>适用条件：</strong>
                    <div style={{ marginTop: "4px", color: "#e2e8f0" }}>{selectedStrategy.applicable_conditions}</div>
                  </div>

                  <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                    <strong>有序执行步骤（结构化字段）：</strong>
                    <ol style={{ margin: "6px 0 0 18px", padding: 0, display: "flex", flexDirection: "column", gap: "4px" }}>
                      {selectedStrategy.execution_steps.map((step, i) => (
                        <li key={i} style={{ color: "#e2e8f0", lineHeight: 1.5 }}>
                          {step}
                        </li>
                      ))}
                    </ol>
                  </div>

                  <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                    <strong>禁忌与反例：</strong>
                    <ul style={{ margin: "6px 0 0 18px", padding: 0, display: "flex", flexDirection: "column", gap: "4px" }}>
                      {selectedStrategy.taboos_and_counterexamples.map((taboo, i) => (
                        <li key={i} style={{ color: "#fca5a5", lineHeight: 1.5 }}>
                          {taboo}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div
                    style={{
                      fontSize: "11px",
                      color: "#94a3b8",
                      borderTop: "1px solid rgba(148, 163, 184, 0.15)",
                      paddingTop: "8px",
                      display: "flex",
                      flexDirection: "column",
                      gap: "4px",
                    }}
                  >
                    <div>上一版本: {selectedStrategy.parent_version ? `v${selectedStrategy.parent_version}` : "初始种子版本"}</div>
                    <div>来源申请 ID: {selectedStrategy.origin_proposal_id || "seed"}</div>
                    <div>验证实验证据: {selectedStrategy.evidence.length ? selectedStrategy.evidence.join(", ") : "seed"}</div>
                    <div>提出方 / 审批方: {selectedStrategy.proposed_by} / {selectedStrategy.approved_by || "-"}</div>
                  </div>
                </div>
              ) : (
                <div style={{ color: "#94a3b8", fontSize: "13px" }}>
                  请点击左侧任意策略版本节点（如 <code>v1 · active</code> 或 <code>v2 · active</code>）查看结构化策略内容、版本演进谱系或执行一键回滚。
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB 2: Proposals Queue */}
        {activeTab === "proposals" && dashboard && (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {dashboard.proposals.length === 0 ? (
              <div style={{ color: "#94a3b8", fontSize: "13px" }}>暂无策略改进申请。</div>
            ) : (
              dashboard.proposals.map((prop) => {
                const badge = statusBadgeColor(prop.status);
                return (
                  <div
                    key={prop.id}
                    style={{
                      padding: "14px 16px",
                      borderRadius: "10px",
                      border: "1px solid rgba(148, 163, 184, 0.18)",
                      background: "rgba(15, 23, 42, 0.65)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "8px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "8px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                        <span
                          style={{
                            padding: "3px 8px",
                            borderRadius: "6px",
                            border: `1px solid ${badge.border}`,
                            background: badge.bg,
                            color: badge.fg,
                            fontSize: "11px",
                            fontWeight: 700,
                          }}
                        >
                          {prop.status}
                        </span>
                        <span style={{ fontWeight: 700, fontSize: "14px" }}>
                          {prop.structured_changes.name}
                        </span>
                        <span style={{ fontSize: "12px", color: "#94a3b8", fontFamily: "monospace" }}>
                          ({prop.id} · 目标: {prop.target_agent} · {prop.base_strategy_id || "new"}:v
                          {prop.base_strategy_version ?? 0} → v{prop.new_strategy_version ?? 1})
                        </span>
                      </div>

                      <div style={{ display: "flex", gap: "8px" }}>
                        {prop.status === "proposed" && (
                          <>
                            <button
                              type="button"
                              disabled={busyAction}
                              onClick={() => void handleReviewProposal(prop.id, "approve")}
                              style={{
                                padding: "4px 10px",
                                borderRadius: "6px",
                                border: "1px solid rgba(16, 185, 129, 0.45)",
                                background: "rgba(16, 185, 129, 0.16)",
                                color: "#6ee7b7",
                                fontSize: "11px",
                                cursor: "pointer",
                              }}
                            >
                              批准实验
                            </button>
                            <button
                              type="button"
                              disabled={busyAction}
                              onClick={() => void handleReviewProposal(prop.id, "request_revision")}
                              style={{
                                padding: "4px 10px",
                                borderRadius: "6px",
                                border: "1px solid rgba(245, 158, 11, 0.45)",
                                background: "rgba(245, 158, 11, 0.16)",
                                color: "#fcd34d",
                                fontSize: "11px",
                                cursor: "pointer",
                              }}
                            >
                              要求修改
                            </button>
                            <button
                              type="button"
                              disabled={busyAction}
                              onClick={() => void handleReviewProposal(prop.id, "reject")}
                              style={{
                                padding: "4px 10px",
                                borderRadius: "6px",
                                border: "1px solid rgba(239, 68, 68, 0.45)",
                                background: "rgba(239, 68, 68, 0.16)",
                                color: "#fca5a5",
                                fontSize: "11px",
                                cursor: "pointer",
                              }}
                            >
                              驳回
                            </button>
                          </>
                        )}
                      </div>
                    </div>

                    <div style={{ fontSize: "12px", color: "#e2e8f0" }}>
                      <strong>发现问题：</strong> {prop.problem_found}
                    </div>
                    {prop.claimed_failure_signature && (
                      <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                        <strong>要验证是否减少的失败：</strong> {prop.claimed_failure_signature}
                      </div>
                    )}
                    {prop.novel_method_evidence && (
                      <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                        <strong>新方法与旧做法的差别：</strong> {prop.novel_method_evidence}
                      </div>
                    )}
                    <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                      <strong>结构化修改摘要：</strong> {prop.structured_changes.change_summary}
                    </div>
                    {prop.recommendations?.length > 0 && (
                      <div style={{ padding: "8px 10px", borderRadius: "6px", border: "1px solid rgba(245, 158, 11, 0.35)", background: "rgba(120, 53, 15, 0.18)", fontSize: "12px", color: "#fde68a" }}>
                        <strong>待人工决定的附加建议（本轮不执行）：</strong>
                        <ul style={{ margin: "6px 0 0", paddingLeft: "20px" }}>
                          {prop.recommendations.map((recommendation, index) => (
                            <li key={`${prop.id}-recommendation-${index}`}>
                              [{recommendation.risk} · {recommendation.type}] {recommendation.summary}
                              {recommendation.risk === "high" ? "（高风险，需人工审批）" : ""}
                            </li>
                          ))}
                        </ul>
                        <div style={{ marginTop: "6px", color: "#cbd5e1" }}>
                          “批准实验”只允许系统做新旧策略对照，不代表批准这些附加建议。
                        </div>
                      </div>
                    )}

                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", fontSize: "11px", color: "#94a3b8" }}>
                      <span>证据轨迹 ID:</span>
                      {prop.evidence_trace_ids.map((tid) => (
                        <button
                          key={tid}
                          type="button"
                          onClick={() => void handleOpenTrace(tid)}
                          style={{
                            padding: "2px 8px",
                            borderRadius: "4px",
                            border: "1px solid rgba(59, 130, 246, 0.4)",
                            background: "rgba(59, 130, 246, 0.12)",
                            color: "#93c5fd",
                            fontSize: "11px",
                            fontFamily: "monospace",
                            cursor: "pointer",
                          }}
                        >
                          {tid} (点击查看轨迹)
                        </button>
                      ))}
                      <span>· 反思 Token: {prop.token_usage.total_tokens}</span>
                    </div>

                    {prop.review_comment && (
                      <div
                        style={{
                          padding: "8px 10px",
                          borderRadius: "6px",
                          background: "rgba(30, 41, 59, 0.7)",
                          fontSize: "12px",
                          color: "#93c5fd",
                        }}
                      >
                        <strong>审批意见 ({prop.reviewed_by || "meta"})：</strong> {prop.review_comment}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* TAB 3: Experiment Reports */}
        {activeTab === "experiments" && dashboard && (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            {dashboard.experiment_reports.length === 0 ? (
              <div style={{ color: "#94a3b8", fontSize: "13px" }}>暂无对照实验报告。</div>
            ) : (
              dashboard.experiment_reports.map((rep) => {
                const badge = statusBadgeColor(rep.rule_judgment.passed ? "active" : "rejected");
                return (
                  <div
                    key={rep.id}
                    style={{
                      padding: "16px",
                      borderRadius: "10px",
                      border: "1px solid rgba(148, 163, 184, 0.2)",
                      background: "rgba(15, 23, 42, 0.7)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "10px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "8px" }}>
                      <div>
                        <span
                          style={{
                            padding: "3px 8px",
                            borderRadius: "6px",
                            border: `1px solid ${badge.border}`,
                            background: badge.bg,
                            color: badge.fg,
                            fontSize: "11px",
                            fontWeight: 700,
                            marginRight: "10px",
                          }}
                        >
                          {rep.rule_judgment.passed ? "规则判定：通过" : "规则判定：未通过"}
                        </span>
                        <strong style={{ fontSize: "14px" }}>
                          策略 {rep.strategy_id} (v{rep.old_strategy_version ?? 0} vs v{rep.new_strategy_version})
                        </strong>
                        <span style={{ fontSize: "12px", color: "#94a3b8", marginLeft: "8px", fontFamily: "monospace" }}>
                          报告 ID: {rep.id}
                        </span>
                      </div>

                      <div style={{ display: "flex", gap: "8px" }}>
                        {rep.rule_judgment.passed && rep.human_approved === null && dashboard.settings.require_human_approval && (
                          <>
                            <button
                              type="button"
                              disabled={busyAction}
                              onClick={() => void handleApproveReport(rep.id, true)}
                              style={{
                                padding: "5px 12px",
                                borderRadius: "6px",
                                border: "1px solid rgba(16, 185, 129, 0.5)",
                                background: "rgba(16, 185, 129, 0.2)",
                                color: "#6ee7b7",
                                fontSize: "12px",
                                fontWeight: 600,
                                cursor: "pointer",
                              }}
                            >
                              ✓ 人工确认激活为 Active
                            </button>
                            <button
                              type="button"
                              disabled={busyAction}
                              onClick={() => void handleApproveReport(rep.id, false)}
                              style={{
                                padding: "5px 12px",
                                borderRadius: "6px",
                                border: "1px solid rgba(239, 68, 68, 0.5)",
                                background: "rgba(239, 68, 68, 0.2)",
                                color: "#fca5a5",
                                fontSize: "12px",
                                cursor: "pointer",
                              }}
                            >
                              人工驳回
                            </button>
                          </>
                        )}
                      </div>
                    </div>

                    {/* Metrics Comparison Cards */}
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "10px" }}>
                      <div style={{ padding: "10px", borderRadius: "8px", background: "rgba(30, 41, 59, 0.6)" }}>
                        <div style={{ fontSize: "11px", color: "#94a3b8" }}>独立数学验证通过率</div>
                        <div style={{ fontSize: "15px", fontWeight: 700, marginTop: "4px", color: "#10b981" }}>
                          旧策略 {(rep.old_pass_rate * 100).toFixed(1)}% → 新策略 {(rep.new_pass_rate * 100).toFixed(1)}%
                        </div>
                      </div>
                      <div style={{ padding: "10px", borderRadius: "8px", background: "rgba(30, 41, 59, 0.6)" }}>
                        <div style={{ fontSize: "11px", color: "#94a3b8" }}>平均 Token 消耗</div>
                        <div style={{ fontSize: "15px", fontWeight: 700, marginTop: "4px", color: "#e2e8f0" }}>
                          旧策略 {rep.old_avg_tokens.toFixed(0)} → 新策略 {rep.new_avg_tokens.toFixed(0)}
                        </div>
                      </div>
                      <div style={{ padding: "10px", borderRadius: "8px", background: "rgba(30, 41, 59, 0.6)" }}>
                        <div style={{ fontSize: "11px", color: "#94a3b8" }}>平均单次耗时</div>
                        <div style={{ fontSize: "15px", fontWeight: 700, marginTop: "4px", color: "#e2e8f0" }}>
                          {(rep.old_avg_elapsed_ms / 1000).toFixed(1)}s → {(rep.new_avg_elapsed_ms / 1000).toFixed(1)}s
                        </div>
                      </div>
                    </div>

                    <div style={{ fontSize: "12px", color: "#cbd5e1" }}>
                      <strong>Strategy Manager 固定规则判定：</strong> {rep.rule_judgment.reason}
                    </div>

                    {rep.meta_review_comment && (
                      <div style={{ fontSize: "12px", color: "#93c5fd" }}>
                        <strong>元反思 Agent 终审意见：</strong> {rep.meta_review_comment}
                      </div>
                    )}

                    {/* Per-problem breakdown */}
                    <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "4px" }}>
                      <div style={{ fontSize: "12px", fontWeight: 600, color: "#94a3b8" }}>分题对照实验明细：</div>
                      {rep.problem_breakdown.map((item) => (
                        <div
                          key={item.problem_id}
                          style={{
                            padding: "8px 10px",
                            borderRadius: "6px",
                            background: "rgba(30, 41, 59, 0.45)",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            flexWrap: "wrap",
                            gap: "8px",
                            fontSize: "12px",
                          }}
                        >
                          <span>
                            <strong>[{item.problem_id}]</strong> {item.title} ({item.category}) · 旧通过 {item.old_passed}/{item.runs_k} vs 新通过{" "}
                            {item.new_passed}/{item.runs_k}
                          </span>
                          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                            {item.old_trace_ids.map((tid) => (
                              <button
                                key={tid}
                                type="button"
                                onClick={() => void handleOpenTrace(tid)}
                                style={{
                                  padding: "2px 6px",
                                  borderRadius: "4px",
                                  border: "1px solid rgba(148, 163, 184, 0.3)",
                                  background: "rgba(30, 41, 59, 0.8)",
                                  color: "#cbd5e1",
                                  fontSize: "11px",
                                  fontFamily: "monospace",
                                  cursor: "pointer",
                                }}
                              >
                                旧轨迹:{tid.slice(0, 12)}
                              </button>
                            ))}
                            {item.new_trace_ids.map((tid) => (
                              <button
                                key={tid}
                                type="button"
                                onClick={() => void handleOpenTrace(tid)}
                                style={{
                                  padding: "2px 6px",
                                  borderRadius: "4px",
                                  border: "1px solid rgba(59, 130, 246, 0.4)",
                                  background: "rgba(59, 130, 246, 0.14)",
                                  color: "#93c5fd",
                                  fontSize: "11px",
                                  fontFamily: "monospace",
                                  cursor: "pointer",
                                }}
                              >
                                新轨迹:{tid.slice(0, 12)}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* TAB 4: Agent A vs Agent B Experience Comparison */}
        {activeTab === "experiences" && dashboard && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "18px" }}>
            {(["agent_a", "agent_b"] as const).map((aid) => {
              const exps: SIExperience[] = dashboard.experiences[aid] || [];
              return (
                <div
                  key={aid}
                  style={{
                    padding: "14px",
                    borderRadius: "10px",
                    border: "1px solid rgba(148, 163, 184, 0.2)",
                    background: "rgba(15, 23, 42, 0.65)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "10px",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <div style={{ fontSize: "14px", fontWeight: 700 }}>
                        {aid === "agent_a" ? "执行 Agent A 私有经验库" : "执行 Agent B 私有经验库"} ({exps.length} 条)
                      </div>
                      <div style={{ fontSize: "11px", color: "#94a3b8" }}>
                        运行期严格隔离，仅自身、反思与元反思可读，每条均绑定真实 TaskTrace
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={busyAction}
                      onClick={() => void handleCompressExperiences(aid)}
                      style={{
                        padding: "5px 10px",
                        borderRadius: "6px",
                        border: "1px solid rgba(168, 85, 247, 0.45)",
                        background: "rgba(168, 85, 247, 0.15)",
                        color: "#d8b4fe",
                        fontSize: "11px",
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      压缩重复经验
                    </button>
                  </div>

                  {exps.map((exp) => (
                    <div
                      key={exp.id}
                      style={{
                        padding: "10px 12px",
                        borderRadius: "8px",
                        border: "1px solid rgba(148, 163, 184, 0.16)",
                        background: "rgba(30, 41, 59, 0.55)",
                        display: "flex",
                        flexDirection: "column",
                        gap: "6px",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px" }}>
                        <span
                          style={{
                            color: exp.experience_type === "success" ? "#10b981" : "#f59e0b",
                            fontWeight: 700,
                          }}
                        >
                          [{exp.experience_type.toUpperCase()}] {exp.is_compressed ? "· 已压缩合并条目" : ""}
                        </span>
                        <span style={{ color: "#94a3b8", fontFamily: "monospace" }}>{exp.id}</span>
                      </div>
                      <div style={{ fontSize: "12px", color: "#e2e8f0", lineHeight: 1.5 }}>{exp.content}</div>
                      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center", fontSize: "11px", color: "#94a3b8" }}>
                        <span>溯源轨迹:</span>
                        {exp.source_trace_ids.map((tid) => (
                          <button
                            key={tid}
                            type="button"
                            onClick={() => void handleOpenTrace(tid)}
                            style={{
                              padding: "1px 6px",
                              borderRadius: "4px",
                              border: "1px solid rgba(59, 130, 246, 0.35)",
                              background: "rgba(59, 130, 246, 0.12)",
                              color: "#93c5fd",
                              fontSize: "11px",
                              fontFamily: "monospace",
                              cursor: "pointer",
                            }}
                          >
                            {tid}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}

        {/* TAB 5: TaskTrace Append-Only Repository */}
        {activeTab === "traces" && dashboard && (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {dashboard.traces.map((trace) => (
              <div
                key={trace.id}
                onClick={() => setInspectedTrace(trace)}
                style={{
                  padding: "12px 14px",
                  borderRadius: "8px",
                  border: "1px solid rgba(148, 163, 184, 0.18)",
                  background: "rgba(15, 23, 42, 0.65)",
                  cursor: "pointer",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "12px",
                }}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <span
                      style={{
                        color: trace.verification_result.passed ? "#10b981" : "#ef4444",
                        fontWeight: 700,
                        fontSize: "12px",
                      }}
                    >
                      {trace.verification_result.passed ? "✓ 验证通过" : "✗ 验证失败"}
                    </span>
                    <span style={{ fontFamily: "monospace", fontSize: "12px", color: "#93c5fd" }}>{trace.id}</span>
                    <span style={{ fontSize: "12px", fontWeight: 600 }}>Agent: {trace.agent_id}</span>
                    <span style={{ fontSize: "11px", color: "#94a3b8" }}>
                      选用策略: {trace.strategy_selection.selected_strategy_ids.join(", ")}
                    </span>
                  </div>
                  <div style={{ fontSize: "12px", color: "#cbd5e1", marginTop: "4px" }}>
                    {trace.problem_text.slice(0, 140)}
                  </div>
                </div>
                <div style={{ fontSize: "11px", color: "#94a3b8", textAlign: "right", whiteSpace: "nowrap" }}>
                  <div>步骤数: {trace.steps.length}</div>
                  <div>Tokens: {trace.token_usage.total_tokens}</div>
                  <div>耗时: {(trace.total_elapsed_ms / 1000).toFixed(1)}s</div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* TAB 6: Audit Logs */}
        {activeTab === "audit" && dashboard && (
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "12px",
                background: "rgba(15, 23, 42, 0.65)",
                borderRadius: "8px",
              }}
            >
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(148, 163, 184, 0.2)", textAlign: "left", color: "#94a3b8" }}>
                  <th style={{ padding: "10px 12px" }}>时间 (when)</th>
                  <th style={{ padding: "10px 12px" }}>执行主体 (who)</th>
                  <th style={{ padding: "10px 12px" }}>目标对象 (target)</th>
                  <th style={{ padding: "10px 12px" }}>状态流转 (from → to)</th>
                  <th style={{ padding: "10px 12px" }}>审计依据 (rationale)</th>
                </tr>
              </thead>
              <tbody>
                {dashboard.audit_logs.map((log) => (
                  <tr key={log.id} style={{ borderBottom: "1px solid rgba(148, 163, 184, 0.1)" }}>
                    <td style={{ padding: "8px 12px", fontFamily: "monospace", color: "#94a3b8", whiteSpace: "nowrap" }}>
                      {log.when.replace("T", " ").slice(0, 19)}
                    </td>
                    <td style={{ padding: "8px 12px", fontWeight: 600, color: "#93c5fd" }}>{log.who}</td>
                    <td style={{ padding: "8px 12px", fontFamily: "monospace" }}>
                      [{log.target_type}] {log.target_id}
                    </td>
                    <td style={{ padding: "8px 12px", fontFamily: "monospace", whiteSpace: "nowrap" }}>
                      {log.from_state} → <strong style={{ color: "#10b981" }}>{log.to_state}</strong>
                    </td>
                    <td style={{ padding: "8px 12px", color: "#e2e8f0" }}>{log.rationale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* TaskTrace Detail Modal */}
      {inspectedTrace && (
        <div
          onClick={() => setInspectedTrace(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(2, 6, 23, 0.78)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 9999,
            padding: "24px",
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "min(960px, 95vw)",
              maxHeight: "88vh",
              overflowY: "auto",
              background: "#0f172a",
              border: "1px solid rgba(148, 163, 184, 0.3)",
              borderRadius: "12px",
              padding: "20px",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontSize: "15px", fontWeight: 700 }}>
                  原始执行轨迹详情 · <span style={{ fontFamily: "monospace", color: "#60a5fa" }}>{inspectedTrace.id}</span>
                </div>
                <div style={{ fontSize: "12px", color: "#94a3b8" }}>
                  Agent: {inspectedTrace.agent_id} · Task: {inspectedTrace.task_id} · Token 总计: {inspectedTrace.token_usage.total_tokens} · 耗时:{" "}
                  {(inspectedTrace.total_elapsed_ms / 1000).toFixed(2)}s (只追加不可篡改记录)
                </div>
              </div>
              <button
                type="button"
                onClick={() => setInspectedTrace(null)}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "1px solid rgba(148, 163, 184, 0.3)",
                  background: "#1e293b",
                  color: "#f8fafc",
                  cursor: "pointer",
                }}
              >
                关闭
              </button>
            </div>

            <div style={{ padding: "10px", borderRadius: "8px", background: "rgba(30, 41, 59, 0.6)", fontSize: "12px" }}>
              <strong>题目：</strong> {inspectedTrace.problem_text}
            </div>

            <div style={{ padding: "10px", borderRadius: "8px", background: "rgba(30, 41, 59, 0.6)", fontSize: "12px" }}>
              <strong>策略选择 (strategy_selection)：</strong>
              <div style={{ marginTop: "4px", color: "#93c5fd" }}>
                已选策略 ID: {inspectedTrace.strategy_selection.selected_strategy_ids.join(", ")}
              </div>
              <div style={{ marginTop: "2px", color: "#cbd5e1" }}>
                规划: {inspectedTrace.strategy_selection.overall_plan}
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <strong style={{ fontSize: "12px" }}>执行步骤与真实工具调用链 ({inspectedTrace.steps.length} 步)：</strong>
              {inspectedTrace.steps.map((step) => (
                <div
                  key={step.step_index}
                  style={{
                    padding: "10px",
                    borderRadius: "8px",
                    border: "1px solid rgba(148, 163, 184, 0.16)",
                    background: "rgba(15, 23, 42, 0.8)",
                    fontSize: "12px",
                  }}
                >
                  <div style={{ fontWeight: 700, color: "#60a5fa" }}>
                    Step #{step.step_index} · 工具: {step.tool_name || "最终推理输出"} ({step.elapsed_ms} ms)
                  </div>
                  {step.tool_name && (
                    <pre
                      style={{
                        marginTop: "6px",
                        padding: "8px",
                        borderRadius: "6px",
                        background: "#020617",
                        color: "#cbd5e1",
                        overflowX: "auto",
                        fontSize: "11px",
                      }}
                    >
                      输入: {JSON.stringify(step.tool_input, null, 2)}
                      {"\n"}输出: {step.tool_output}
                    </pre>
                  )}
                  {!step.tool_name && (
                    <div style={{ marginTop: "4px", color: "#e2e8f0", whiteSpace: "pre-wrap" }}>
                      {step.reasoning_summary}
                    </div>
                  )}
                </div>
              ))}
            </div>

            <div
              style={{
                padding: "10px",
                borderRadius: "8px",
                border: inspectedTrace.verification_result.passed
                  ? "1px solid rgba(16, 185, 129, 0.4)"
                  : "1px solid rgba(239, 68, 68, 0.4)",
                background: inspectedTrace.verification_result.passed
                  ? "rgba(16, 185, 129, 0.1)"
                  : "rgba(239, 68, 68, 0.1)",
                fontSize: "12px",
              }}
            >
              <strong>独立验证器结果 ({inspectedTrace.verification_result.verifier_type})：</strong>{" "}
              {inspectedTrace.verification_result.verifier_output}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
