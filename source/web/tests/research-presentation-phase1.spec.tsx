import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { buildProjectArtifacts, buildProjectEvidence, EVIDENCE_HIERARCHY, evidenceLabels, formatProjectTime, normalizeMathText, projectListStatus, resolveNodeEvidenceLevel, sameSavedPlan, splitJsonBlocks, statusText } from "@/features/research/research-presentation";
import { hasSubstantialEnglish } from "@/features/research/research-translation";
import ResearchTopBar from "@/features/research/ResearchTopBar";
import type { ResearchProject, ResearchWorkbenchSnapshot } from "@/lib/research-api";
import { reviewSummary } from "@/features/research/review-summary";

const api = vi.hoisted(() => ({ getResearchWorkbench: vi.fn(), researchAction: vi.fn(), resumeResearchAnyway: vi.fn() }));
vi.mock("@/lib/research-api", async original => ({ ...await original<typeof import("@/lib/research-api")>(), ...api }));

describe("research presentation", () => {
  it("maps codes without leaking unknown internal values or promoting a symbolic check to a proof", () => {
    expect(statusText("MILESTONE_REVIEW_REQUIRED")).toBe("等待你审阅阶段成果");
    expect(statusText("REJECTED_ROUTE")).toBe("路线记录待审阅");
    expect(statusText("SOME_NEW_BACKEND_CODE")).toBe("状态待确认");
    expect(evidenceLabels.SYMBOLICALLY_VERIFIED.label).toBe("符号检查");
    expect(evidenceLabels.CONJECTURAL.label).toBe("猜想");
  });
  it("normalizes simple bare mathematics without changing existing LaTeX or code", () => {
    expect(normalizeMathText('证明 n squared +n+41 对所有正整数 n 都是素数')).toBe('证明 $n^2+n+41$ 对所有正整数 $n$ 都是素数');
    expect(normalizeMathText('已有 $x^2+1$ 和 `n^2+1`')).toBe('已有 $x^2+1$ 和 `n^2+1`');
    expect(normalizeMathText('考虑 n^2 的取值')).toBe('考虑 $n^2$ 的取值');
    expect(normalizeMathText('check n=1,...,100')).toBe('check $n=1,\\ldots,100$');
    expect(normalizeMathText('Verify n=40 and P(40)=40^2+40+41.')).toBe('Verify $n=40$ and $P(40)=40^2+40+41$.');
    expect(normalizeMathText('Use exact integer arithmetic to evaluate P(n) for n=1,...,100.')).toBe('Use exact integer arithmetic to evaluate $P(n)$ for $n=1,\\ldots,100$.');
    expect(normalizeMathText('Check the congruence P(n) ≡ n(n+1) mod 41.')).toBe('Check the congruence $P(n) \\equiv n(n+1) \\pmod{41}$.');
    expect(normalizeMathText('test P(41m-1)=41(41m^2-m+1) for m>=1')).toBe('test $P(41m-1)=41(41m^2-m+1)$ for $m\\ge 1$');
  });
  it("keeps fenced JSON separate from the default research text", () => {
    const separated = splitJsonBlocks('结论待验证。\n```json\n{"raw":true}\n```\n下一步。');
    expect(separated.markdown).toContain('结论待验证');
    expect(separated.markdown).not.toContain('"raw"');
    expect(separated.raw).toEqual(['{"raw":true}']);
    expect(splitJsonBlocks('```\n{"x":1}\n```').raw).toEqual(['{"x":1}']);
    expect(splitJsonBlocks('```json\n{"unfinished":').markdown).toBe('\n');
  });
  it("recognizes a saved plan despite version and execution-status changes but rejects changed content", () => {
    const draft = { objective: '目标', steps: [{ id: 's', status: 'completed', title: '原步骤' }] } as never;
    const saved = { objective: '目标', version: 2, steps: [{ id: 's', status: 'superseded', title: '原步骤' }] } as never;
    const changed = { objective: '另一个目标', version: 2, steps: [{ id: 's', status: 'completed', title: '原步骤' }] } as never;
    expect(sameSavedPlan(draft, saved)).toBe(true);
    expect(sameSavedPlan(draft, changed)).toBe(false);
  });
  it("uses the linked step evidence, keeps unverified results qualified, and calls the existing resume action", async () => {
    const snapshot = { state: { currentStepId: "s1", statusReason: "MILESTONE_REVIEW_REQUIRED" } } as ResearchWorkbenchSnapshot;
    const project = { id: "p1", status: "PAUSED", currentVersion: 1, approvedVersion: 1, plan: { steps: [] }, inputRequests: [], workbench: snapshot,
      artifacts: [{ id: "a1", planVersion: 1, stepId: "s1", metadata: { artifactKind: "step-report", evidenceClass: "CONJECTURAL", structuredReport: { "Current Conclusion": "No separate conclusion was supplied.", Outputs: "已将命题形式化。" } } }] } as unknown as ResearchProject;
    api.getResearchWorkbench.mockResolvedValue(snapshot);
    api.researchAction.mockResolvedValue({});
    const onEditPlan = vi.fn();
    render(<ResearchTopBar project={project} refreshKey="1" onChanged={vi.fn()} onEditPlan={onEditPlan} />);
    expect(await screen.findByText("等待你审阅阶段成果")).toBeInTheDocument();
    expect(screen.getByText("猜想")).toBeInTheDocument();
    expect(screen.getByText(/已将命题形式化/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修改计划" }));
    expect(onEditPlan).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await waitFor(() => expect(api.researchAction).toHaveBeenCalledWith("p1", "resume"));
    expect(screen.queryByRole("button", { name: "暂停" })).not.toBeInTheDocument();
  });
  it("extracts recorded stage output instead of inventing a conclusion, and uses the next pending step", () => {
    const project = { currentVersion: 1, plan: { steps: [
      { id: 's1', order: 0, enabled: true, status: 'completed' },
      { id: 's2', order: 1, enabled: true, status: 'pending', title: '检查数值', description: '对 n=1,...,100 检查。再汇总。' },
    ]}, artifacts: [{ id: 'a1', planVersion: 1, stepId: 's1', metadata: { artifactKind: 'step-report', evidenceClass: 'CONJECTURAL', structuredReport: { 'Current Conclusion': 'No separate conclusion was supplied.', Outputs: '已确定命题。\n\n$x^2+1$\n\n**Lemma:** 待验证' } } }] } as unknown as ResearchProject;
    const result = reviewSummary(project, 's1');
    expect(result.text).toContain('已确定命题');
    expect(result.text).toContain('$x^2+1$');
    expect(result.text).not.toContain('待验证');
    expect(result.nextStep).toBe('对 n=1,...,100 检查。');
    expect(result.evidence).toBe('CONJECTURAL');
    expect(reviewSummary({ ...project, artifacts: [] }, 's1').hasArtifacts).toBe(false);
  });
  it("orders evidence hierarchy from weakest to strongest and blocks unverified claims from showing as proven", () => {
    expect(EVIDENCE_HIERARCHY.map(item => item.label)).toEqual([
      "猜想",
      "数值验证",
      "符号检查",
      "文献支持",
      "已证明",
      "形式化验证",
    ]);
    const unverifiedProject = {
      currentVersion: 1,
      plan: { steps: [{ id: "step_1", order: 0, title: "形式化命题", enabled: true }] },
      graph: {
        nodes: [
          {
            id: "node_claim",
            type: "claim",
            title: "命题 $P(n)$ 对所有正整数成立",
            content: "假设 $P(n)=n^2+n+41$ 为素数",
            status: "verified",
            branch: "main",
            metadata: { evidenceClass: "PROVEN", stepId: "step_1" },
          },
          {
            id: "node_num",
            type: "computation",
            title: "当 $n=40$ 时 $P(40)=41^2$ 为合数",
            content: "数值计算验证 $P(40)=1681$",
            status: "verified",
            branch: "main",
            metadata: { evidenceClass: "NUMERICALLY_OBSERVED", stepId: "step_1" },
          },
        ],
        edges: [],
      },
      artifacts: [
        {
          id: "art_1",
          stepId: "step_1",
          title: "阶段报告 1",
          artifactType: "report",
          content: "# 阶段报告\n验证了 $P(40)=41^2$。",
          createdAt: 1700000000,
          metadata: { artifactKind: "step-report", evidenceClass: "NUMERICALLY_OBSERVED" },
        },
      ],
    } as unknown as ResearchProject;

    // Without a proof_attempt/proof node or formal certified binding, PROVEN must be downgraded to CONJECTURAL
    expect(resolveNodeEvidenceLevel(unverifiedProject.graph!.nodes[0], unverifiedProject)).toBe("CONJECTURAL");
    expect(resolveNodeEvidenceLevel(unverifiedProject.graph!.nodes[1], unverifiedProject)).toBe("NUMERICALLY_OBSERVED");

    const evidenceList = buildProjectEvidence(unverifiedProject);
    expect(evidenceList.find(item => item.id === "node_claim")?.evidenceLevel).toBe("CONJECTURAL");
    expect(evidenceList.find(item => item.id === "node_num")?.evidenceLevel).toBe("NUMERICALLY_OBSERVED");

    const artifactsList = buildProjectArtifacts(unverifiedProject);
    expect(artifactsList).toHaveLength(1);
    expect(artifactsList[0].kindLabel).toBe("阶段报告");
    expect(artifactsList[0].stepLabel).toContain("步骤 1");
  });
  it("maps historical research statuses into the four left-rail categories and formats timestamps", () => {
    expect(projectListStatus({ status: "RUNNING" } as never).label).toBe("进行中");
    expect(projectListStatus({ status: "PAUSED" } as never).label).toBe("已暂停");
    expect(projectListStatus({ status: "PAUSED", workbench: { state: { statusReason: "MILESTONE_REVIEW_REQUIRED" } } } as never).label).toBe("等待审阅");
    expect(projectListStatus({ status: "PAUSED", lastError: "Research artifacts and evidence snapshot are ready for researcher review." } as never).label).toBe("等待审阅");
    expect(projectListStatus({ status: "PLAN_READY" } as never).label).toBe("等待审阅");
    expect(projectListStatus({ status: "COMPLETED" } as never).label).toBe("已完成");
    expect(projectListStatus({ status: "CANCELLED" } as never).label).toBe("已停止");
    expect(formatProjectTime(1761630000)).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/);
  });
  it("detects legacy English records while ignoring Chinese records with LaTeX formulas", () => {
    expect(hasSubstantialEnglish("Determine the truth value of the universal claim that $P(n) = n^2 + n + 41$ is prime.")).toBe(true);
    expect(hasSubstantialEnglish("判定全称命题“对每个正整数 $n$，$P(n)=n^2+n+41$ 均为素数”的真值。")).toBe(false);
  });
});
