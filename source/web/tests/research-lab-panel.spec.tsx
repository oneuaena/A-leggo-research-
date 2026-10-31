import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ResearchLabPanel from "@/features/research/ResearchLabPanel";
import type { LeanStatus, ProofReview, ResearchFormalBinding, ResearchProject } from "@/lib/research-api";

const api = vi.hoisted(() => ({
  getLeanStatus: vi.fn(), startLeanInstall: vi.fn(), cancelLeanInstall: vi.fn(), getAutoProve: vi.fn(), startAutoProve: vi.fn(),
  getLeanResearchSettings: vi.fn(), saveLeanResearchSettings: vi.fn(),
  getCasStatus: vi.fn(), getVisionConfig: vi.fn(), saveCasConfig: vi.fn(),
  queryMathDatabase: vi.fn(),
  listProofReviews: vi.fn(), startProofReview: vi.fn(), getProofReview: vi.fn(), annotateProofStep: vi.fn(),
  listLongTermMemory: vi.fn(), getMemorySuggestions: vi.fn(), getRelatedProjects: vi.fn(), createLongTermMemory: vi.fn(),
  getCollaborationCandidates: vi.fn(), getProjectMembers: vi.fn(), listResearchComments: vi.fn(), listResearchAssignments: vi.fn(), addProjectMember: vi.fn(),
  getProjectUsage: vi.fn(), getUsagePrices: vi.fn(),
  getEvalProblems: vi.fn(), listEvalRuns: vi.fn(), getEvalRun: vi.fn(), startEvalRun: vi.fn(),
}));
vi.mock("@/lib/research-api", async (original) => ({ ...await original<typeof import("@/lib/research-api")>(), ...api }));

const binding = {
  id: "fb1", projectId: "p1", claimNodeId: "n1", proofNodeId: null, originalStatement: "n + 0 = n", formalIr: "", leanStatement: "theorem t (n : Nat) : n + 0 = n",
  originalHash: "", formalIrHash: "", leanStatementHash: "", bindingHash: "", mappingAuthority: "USER_CONFIRMED", status: "UNVERIFIED", proofSourceHash: "",
  leanVersion: "", checkerOutput: "", createdAt: "", updatedAt: "",
} as ResearchFormalBinding;

const project: ResearchProject = {
  id: "p1", title: "T", question: "证明素数有无穷多个", modelId: "research.meridian", status: "RUNNING", currentVersion: 1, approvedVersion: 1,
  lastError: "", createdAt: "", updatedAt: "", revisions: [], artifacts: [], sources: [], formalBindings: [binding],
  plan: { steps: [{ id: "s1", title: "构造反证", description: "", status: "pending", order: 1, priority: 1, enabled: true, branch: "main", dependencies: [], assignedAgent: "", requiredTools: [], expectedOutput: "", verificationMethod: "", milestone: "", humanNotes: "", agentNotes: "" }] } as unknown as ResearchProject["plan"],
};

const install = { status: "idle", phase: "", message: "", error: "", startedAt: null, finishedAt: null, progress: 0, phases: [], log: [] };
const leanStatus = (ready: boolean, extra: Partial<LeanStatus> = {}): LeanStatus => ({
  ready, reason: ready ? "" : "未安装 Lean 工具链", activeWorkspace: "", pinnedLean: "4.32.0", pinnedMathlib: "v4.32.0", toolsHome: "/tools", lake: "", elan: "",
  gitAvailable: true, freeDiskBytes: 20 * 1024 ** 3, requiredDiskBytes: 9 * 1024 ** 3, platform: "linux",
  checks: [{ id: "git", label: "git 可用", ok: true, detail: "git 2.40" }, { id: "elan", label: "elan 工具链", ok: ready, detail: ready ? "ok" : "缺失" }],
  install: install as LeanStatus["install"], ...extra,
});

const review: ProofReview = {
  id: "r1", title: "无穷素数", state: "COMPLETED", statement: "素数有无穷多个",
  steps: [
    { id: "S1", text: "假设只有有限个素数 $p_1,\\dots,p_n$。", verdict: "valid", dependsOn: [], issue: "", suggestion: "", researcherVerdict: "", researcherNote: "" },
    { id: "S2", text: "令 $N = p_1\\cdots p_n + 1$，则 $N$ 是素数。", verdict: "gap", dependsOn: ["S1"], issue: "N 不一定是素数，只能说明它有不在列表中的素因子。", suggestion: "改为取 N 的一个素因子。", researcherVerdict: "", researcherNote: "" },
    { id: "S3", text: "矛盾，故素数有无穷多个。", verdict: "valid", dependsOn: ["S2"], issue: "", suggestion: "", researcherVerdict: "", researcherNote: "" },
  ],
  tree: { edges: [{ source: "S1", target: "S2" }, { source: "S2", target: "S3" }], roots: ["S3"], conclusion: "S3", affectedByIssues: { S3: ["S2"] },
    tree: { id: "S3", verdict: "valid", children: [{ id: "S2", verdict: "gap", children: [{ id: "S1", verdict: "valid", children: [] }] }] } },
  overall: "gaps-found", summary: "主体思路正确，第 2 步表述有缺口。", missingHypotheses: [], error: "",
};

beforeEach(() => {
  api.getLeanResearchSettings.mockResolvedValue({ confirmBeforeCheck: true, autoCallEnabled: false });
  api.saveLeanResearchSettings.mockImplementation(async (_id, value) => value);
  vi.clearAllMocks();
  api.getLeanStatus.mockResolvedValue(leanStatus(false));
  api.startLeanInstall.mockResolvedValue({ ...install, status: "running" });
  api.getAutoProve.mockResolvedValue({ id: "", bindingId: "fb1", state: "NONE", attempts: [], passed: false, proof: "", error: "", startedAt: null, finishedAt: null, maxAttempts: 4 });
  api.getCasStatus.mockResolvedValue({ isolation: "resource-limits", config: {}, engines: [
    { key: "pari", tool: "pari_gp", label: "PARI/GP", available: true, path: "/usr/bin/gp", version: "2.17.4", isolation: "resource-limits", homepage: "", installHint: "", envVar: "ALEGGO_GP_PATH", example: "", language: "GP" },
    { key: "gap", tool: "gap_system", label: "GAP", available: false, path: "", version: "", isolation: "", homepage: "", installHint: "apt install gap", envVar: "ALEGGO_GAP_PATH", example: "", language: "GAP" },
  ] });
  api.getVisionConfig.mockResolvedValue({ model: "", source: "unset" });
  api.queryMathDatabase.mockResolvedValue({ ok: true, tool: "oeis_search", error: "", errorType: "", savedSourceIds: [],
    data: { provider: "OEIS", query: "1,1,2,5,14", count: 1, items: [{ id: "A000108", name: "Catalan numbers", terms: "1,1,2,5,14,42", url: "https://oeis.org/A000108" }] } });
  api.listProofReviews.mockResolvedValue({ items: [{ id: "r1", title: "无穷素数", createdAt: "", state: "COMPLETED", overall: "gaps-found", stepCount: 3 }] });
  api.getProofReview.mockResolvedValue(review);
  api.annotateProofStep.mockResolvedValue({ ...review, steps: review.steps.map((step) => step.id === "S2" ? { ...step, researcherVerdict: "error" as const } : step) });
  api.getProjectUsage.mockResolvedValue({
    total: { promptTokens: 1200, completionTokens: 300, totalTokens: 1500, calls: 3, cost: 0.0123, priced: true },
    byRole: [{ role: "explorer", promptTokens: 1200, completionTokens: 300, totalTokens: 1500, calls: 3, cost: 0.0123, priced: true }],
    byModel: [{ model: "qwen-max", promptTokens: 1200, completionTokens: 300, totalTokens: 1500, calls: 3, cost: 0.0123, priced: true }],
    byDay: [], untrackedCalls: 1, currency: "CNY", pricesConfigured: true,
  });
  api.getUsagePrices.mockResolvedValue({ currency: "CNY", models: { "qwen-max": { prompt: 0.02, completion: 0.06 } } });
  api.getCollaborationCandidates.mockResolvedValue({ me: { id: "u1", username: "owner", isAdmin: true }, multiUser: true, items: [{ id: "u1", username: "owner", role: "admin" }, { id: "u2", username: "li", role: "user" }], sharedWithMe: [] });
  api.getProjectMembers.mockResolvedValue({ owner: { id: "u1", username: "owner" }, myRole: "owner", members: [] });
  api.listResearchComments.mockResolvedValue({ items: [] });
  api.listResearchAssignments.mockResolvedValue({ items: [] });
  api.addProjectMember.mockResolvedValue({ owner: { id: "u1", username: "owner" }, myRole: "owner", members: [{ id: "u2", username: "li", role: "viewer", since: "" }] });
  api.getEvalProblems.mockResolvedValue({ version: "1", description: "", categories: { computation: 8, counterexample: 4 }, problems: [] });
  api.listEvalRuns.mockResolvedValue({ items: [] });
});

const openTab = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

describe("research lab panel", () => {
  it("keeps safe Lean settings by default and saves each preference", async () => {
    render(<ResearchLabPanel project={project} isAdmin />);
    const confirm = await screen.findByRole("checkbox", { name: /AI 证明前先由我确认 Lean 陈述/ });
    const automatic = screen.getByRole("checkbox", { name: /研究中允许 AI 自动调用 Lean/ });
    expect(confirm).toBeChecked();
    expect(automatic).not.toBeChecked();
    fireEvent.click(automatic);
    await waitFor(() => expect(api.saveLeanResearchSettings).toHaveBeenCalledWith("p1", { confirmBeforeCheck: true, autoCallEnabled: true }));
    await waitFor(() => expect(automatic).toBeChecked());
    fireEvent.click(confirm);
    await waitFor(() => expect(api.saveLeanResearchSettings).toHaveBeenCalledWith("p1", { confirmBeforeCheck: false, autoCallEnabled: true }));
  });

  it("guides Lean installation and gates AI proving on a ready kernel", async () => {
    render(<ResearchLabPanel project={project} isAdmin />);
    expect(await screen.findByText(/Lean 尚未就绪/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "让 AI 尝试证明" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "一键安装 Lean + Mathlib" }));
    await waitFor(() => expect(api.startLeanInstall).toHaveBeenCalledTimes(1));
  });

  it("shows kernel feedback for each AI proof attempt", async () => {
    api.getLeanStatus.mockResolvedValue(leanStatus(true));
    api.startAutoProve.mockResolvedValue({ id: "j1", bindingId: "fb1", state: "COMPLETED", passed: true, proof: "by simp", error: "", startedAt: null, finishedAt: null, maxAttempts: 4,
      attempts: [{ attempt: 1, proof: "by rfl", explanation: "", status: "FAILED", feedback: "error: type mismatch", elapsedMs: 900 }, { attempt: 2, proof: "by simp", explanation: "根据报错改用 simp", status: "PASSED", feedback: "", elapsedMs: 800 }] });
    render(<ResearchLabPanel project={project} isAdmin />);
    expect(await screen.findByText(/Lean 已就绪/)).toBeInTheDocument();
    const prove = screen.getByRole("button", { name: "让 AI 尝试证明" });
    await waitFor(() => expect(prove).not.toBeDisabled());
    fireEvent.click(prove);
    expect(await screen.findByText("Lean 内核已通过该证明。")).toBeInTheDocument();
    expect(screen.getByText("error: type mismatch")).toBeInTheDocument();
    expect(api.startAutoProve).toHaveBeenCalledWith("p1", "fb1", { maxAttempts: 4, hint: "" });
  });

  it("lists detected computer algebra systems with install hints", async () => {
    render(<ResearchLabPanel project={project} isAdmin={false} />);
    openTab("计算系统");
    expect(await screen.findByText("PARI/GP")).toBeInTheDocument();
    expect(screen.getByText(/2\.17\.4/)).toBeInTheDocument();
    expect(screen.getByText("apt install gap")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存路径" })).not.toBeInTheDocument();
  });

  it("queries OEIS and shows linked results", async () => {
    render(<ResearchLabPanel project={project} isAdmin />);
    openTab("数学数据库");
    fireEvent.change(screen.getByLabelText("查询内容"), { target: { value: "1,1,2,5,14" } });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));
    const link = await screen.findByRole("link", { name: "Catalan numbers" });
    expect(link).toHaveAttribute("href", "https://oeis.org/A000108");
    expect(api.queryMathDatabase).toHaveBeenCalledWith("p1", "oeis_search", { query: "1,1,2,5,14", limit: 10 }, false);
  });

  it("renders the proof tree, marks dependent steps, and records the researcher's verdict", async () => {
    render(<ResearchLabPanel project={project} isAdmin />);
    openTab("证明审查");
    fireEvent.click(await screen.findByRole("button", { name: /无穷素数/ }));
    const tree = await screen.findByLabelText("证明树");
    expect(tree.querySelectorAll(".research-proof-tree-node").length).toBe(3);
    expect(within(tree).getByText(/依赖有问题的步骤[：:]\s*S2/)).toBeInTheDocument();
    expect(screen.getByText(/N 不一定是素数/)).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".katex")).not.toBeNull());
    fireEvent.change(screen.getByLabelText("S2 你的判断"), { target: { value: "error" } });
    await waitFor(() => expect(api.annotateProofStep).toHaveBeenCalledWith("p1", "r1", "S2", "error", ""));
    expect(await screen.findByText("你：错误")).toBeInTheDocument();
  });

  it("shows token usage and estimated cost", async () => {
    render(<ResearchLabPanel project={project} isAdmin />);
    openTab("用量与费用");
    expect(await screen.findByText("1,500")).toBeInTheDocument();
    expect(screen.getAllByText("0.0123 CNY").length).toBeGreaterThan(0);
    expect(screen.getByText(/1 次调用的服务商未返回 token 数/)).toBeInTheDocument();
  });

  it("lets the owner share a project with another account", async () => {
    render(<ResearchLabPanel project={project} isAdmin />);
    openTab("协作");
    const select = await screen.findByLabelText("添加成员");
    await waitFor(() => expect(within(select).getByRole("option", { name: "li" })).toBeInTheDocument());
    fireEvent.change(select, { target: { value: "u2" } });
    fireEvent.change(screen.getByLabelText("权限"), { target: { value: "viewer" } });
    fireEvent.click(screen.getByRole("button", { name: "共享" }));
    await waitFor(() => expect(api.addProjectMember).toHaveBeenCalledWith("p1", "u2", "viewer"));
  });

  it("starts an evaluation run on the selected categories", async () => {
    api.startEvalRun.mockResolvedValue({ id: "e1", model: "research.lumen", state: "COMPLETED", startedAt: "2026-10-19T00:00:00Z", finishedAt: null, categories: ["computation"], error: "", problemCount: 1,
      summary: { total: 1, scored: 1, passed: 1, skipped: 0, failed: 0, score: 1, byCategory: { computation: { total: 1, passed: 1, score: 1 } } },
      results: [{ id: "comp-1", category: "computation", kind: "numeric", passed: true, skipped: false, checks: [], answer: "42", error: "", latencyMs: 1200, toolCalls: 1, tools: ["sympy"] }] });
    render(<ResearchLabPanel project={project} isAdmin />);
    openTab("评测");
    fireEvent.click(await screen.findByRole("checkbox", { name: /computation/ }));
    fireEvent.click(screen.getByRole("button", { name: "开始评测" }));
    await waitFor(() => expect(api.startEvalRun).toHaveBeenCalledWith({ modelId: "research.lumen", categories: ["computation"] }));
    expect(await screen.findByText("comp-1")).toBeInTheDocument();
  });
});
