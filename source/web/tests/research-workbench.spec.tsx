import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ResearchWorkbenchPanel from "@/features/research/ResearchWorkbenchPanel";
import ResearchToolbench from "@/features/research/ResearchToolbench";
import ResearchWorkspace from "@/features/research/ResearchWorkspace";
import { hasResearchFolderPicker, pickResearchFolder } from "@/lib/research-native";
import type { ResearchProject, ResearchWorkbenchSnapshot } from "@/lib/research-api";

const api = vi.hoisted(() => ({ getResearchWorkbench: vi.fn(), getResearchProject: vi.fn(), getLiteratureState: vi.fn(), getLiteratureGraph: vi.fn(), decideResearchRecommendation: vi.fn(), openResearchArtifact: vi.fn(), addResearchIntervention: vi.fn(), researchAction: vi.fn(), restoreResearchWorkspace: vi.fn(), listResearchProjects: vi.fn(), getResearchModels: vi.fn(), getResearchSettings: vi.fn() }));
vi.mock("@/lib/research-api", async (importOriginal) => ({ ...await importOriginal<typeof import("@/lib/research-api")>(), ...api }));
vi.mock("@/components/common/SimpleMarkdownRenderer", () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

const project: ResearchProject = {
  id: "project-1", title: "Boundary analysis", question: "Is the boundary condition sufficient?", modelId: "research.meridian", status: "PAUSED", currentVersion: 1, approvedVersion: 1, lastError: "", createdAt: "", updatedAt: "", revisions: [], plan: null,
  artifacts: Array.from({ length: 13 }, (_, index) => ({ id: `artifact-${index}`, projectId: "project-1", planVersion: 1, stepId: "step-1", agentRole: "proof-builder", title: `Report ${index}`, content: index === 0 ? "a".repeat(8100) + " FULL CONTENT END" : `Evidence ${index}`, status: "CONJECTURAL", createdAt: "2026-09-26T00:00:00Z", filePath: `C:/Research/report-${index}.md` })), graph: { nodes: [], edges: [] }, sources: [], branches: [], memories: [], formalBindings: [],
};
const snapshot: ResearchWorkbenchSnapshot = {
  state: { supervisionMode: "MILESTONE_REVIEW", currentStepId: "step-1", currentAgent: "proof-builder", currentAction: "Testing boundary n = 1", currentBranch: "main", currentMilestone: "Boundary", runStartedAt: "", statusReason: "MILESTONE_REVIEW_REQUIRED", blockerType: "", blockerDetail: "", stalledAttempts: 0, llmCalls: 1, toolCalls: 1, failures: 0, lastProgressAt: "", workspaceRoot: "C:/Research", workspaceStatus: "READY", currentTool: "SymPy", activeAgents: ["proof-builder"], stepIndex: 1, stepTotal: 3, milestoneIndex: 1, milestoneTotal: 2 },
  currentStep: null, timeline: [], decisions: [], recommendations: [{ id: "rec-1", stepId: "step-1", status: "PENDING", createdAt: "", content: { summary: "Check a weakened assumption", primary: [{ path: "Test n = 2", reason: "Boundary case" }], secondary: [], deprioritize: [] } }], counts: { artifacts: 13, literature: 0, evidence: 0, counterexamples: 0, failedPaths: 0 }, workspacePath: "C:/Research", budget: { llmCalls: 1, toolCalls: 1, failures: 0, elapsedSeconds: 50, informationGain: "observed" }, summary: { question: project.question, failedPaths: [] },
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/research");
  api.getResearchWorkbench.mockResolvedValue(snapshot);
  api.getResearchProject.mockResolvedValue(project);
  api.getLiteratureState.mockResolvedValue({ asOf: "2026-09-26", counts: {} });
  api.getLiteratureGraph.mockResolvedValue({ nodes: [], edges: [] });
  api.openResearchArtifact.mockResolvedValue({ opened: true });
  api.addResearchIntervention.mockResolvedValue({ status: "QUEUED" });
  api.listResearchProjects.mockResolvedValue({ items: [] });
  api.getResearchModels.mockResolvedValue({ items: [{ id: "research.meridian", label: "Meridian" }], credentialConfigured: false, atlasAgentConfigured: false });
  api.getResearchSettings.mockResolvedValue({ isAdmin: false, atlasAgentId: "" });
});

describe("research workbench connected controls", () => {
  it("keeps only the latest recommendation in the active card and moves older advice to history", async () => {
    const older = { ...snapshot.recommendations[0], id: "old", createdAt: "2026-01-01T00:00:00Z", content: { primary: [{ path: "旧路线", reason: "旧理由" }], secondary: [], deprioritize: [] } };
    const newer = { ...snapshot.recommendations[0], id: "new", createdAt: "2026-01-02T00:00:00Z", content: { primary: [{ path: "最新路线", reason: "最新理由" }], secondary: [], deprioritize: [] } };
    api.getResearchWorkbench.mockResolvedValue({ ...snapshot, recommendations: [older, newer] });
    render(<ResearchWorkbenchPanel project={project} onEditPlan={vi.fn()} onRefresh={vi.fn().mockResolvedValue(undefined)} onProposedPlan={vi.fn()} onRestored={vi.fn()} />);
    expect((await screen.findAllByText("最新路线")).filter((item) => !item.closest("details:not([open])"))).toHaveLength(1);
    expect(screen.getByText("旧路线")).not.toBeVisible();
    expect(screen.getByText(/历史建议（1，已被取代）/)).toBeVisible();
    expect(screen.getAllByRole("button", { name: "采纳" })).toHaveLength(1);
  });

  it("shows an initial service error and retries loading the real workbench", async () => {
    api.getResearchWorkbench.mockRejectedValueOnce(new Error("Research service temporarily unavailable"));
    render(<ResearchWorkbenchPanel project={project} onEditPlan={vi.fn()} onRefresh={vi.fn().mockResolvedValue(undefined)} onProposedPlan={vi.fn()} onRestored={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Research service temporarily unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry research state" }));
    expect(await screen.findByText("Testing boundary n = 1")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("previews the complete artifact and reaches older artifacts through pagination", async () => {
    render(<ResearchWorkbenchPanel project={project} onEditPlan={vi.fn()} onRefresh={vi.fn().mockResolvedValue(undefined)} onProposedPlan={vi.fn()} onRestored={vi.fn()} />);
    await screen.findByText("研究成果 · 13");
    const details = screen.getByText("Report 0").closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(await screen.findByText(/FULL CONTENT END/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("在文件管理器中显示")[0]);
    await waitFor(() => expect(api.openResearchArtifact).toHaveBeenCalledWith("project-1", "artifact-0", true));
    fireEvent.click(screen.getByText("下一页"));
    expect(screen.getByText("Report 12")).toBeInTheDocument();

  });

  it("loads a recommendation proposal for normal plan review without executing it", async () => {
    const proposal = { objective: "Review route" };
    api.decideResearchRecommendation.mockResolvedValue({ proposedPlan: proposal, pendingReview: true });
    const onProposedPlan = vi.fn();
    render(<ResearchWorkbenchPanel project={project} onEditPlan={vi.fn()} onRefresh={vi.fn().mockResolvedValue(undefined)} onProposedPlan={onProposedPlan} onRestored={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "采纳" }));
    await waitFor(() => expect(onProposedPlan).toHaveBeenCalledWith(proposal));
    expect(api.researchAction).not.toHaveBeenCalled();
  });

  it("queues an explicit researcher action for the selected branch", async () => {
    const branch = { id: "branch-2", name: "boundary", status: "OPEN", parent: "main", parentId: "branch-main", basePlanVersion: 1, limits: { evaluations: 10, proofAttempts: 2, toolSeconds: 5 }, used: { evaluations: 0, proofAttempts: 0, toolSeconds: 0 }, createdAt: "", updatedAt: "" };
    render(<ResearchWorkbenchPanel project={{ ...project, branches: [branch] }} onEditPlan={vi.fn()} onRefresh={vi.fn().mockResolvedValue(undefined)} onProposedPlan={vi.fn()} onRestored={vi.fn()} />);
    await screen.findByText("Testing boundary n = 1");
    fireEvent.change(screen.getByLabelText("Action"), { target: { value: "request_counterexample" } });
    fireEvent.change(screen.getByLabelText("Target branch"), { target: { value: "boundary" } });
    fireEvent.change(screen.getByLabelText("Request, result, assumptions, or idea"), { target: { value: "Check n <= 20 with exact integers" } });
    fireEvent.click(screen.getByRole("button", { name: "Save research intervention" }));
    await waitFor(() => expect(api.addResearchIntervention).toHaveBeenCalledWith("project-1", "request_counterexample", expect.objectContaining({ branch: "boundary", message: "Check n <= 20 with exact integers" })));
    expect(await screen.findByText("Research action queued for the next safe step boundary.")).toBeInTheDocument();
    expect(api.researchAction).not.toHaveBeenCalled();
  });

  it("restores a package without refreshing or switching back to the old project", async () => {
    const restored = { ...project, id: "restored-project" };
    api.restoreResearchWorkspace.mockResolvedValue(restored);
    const onRestored = vi.fn();
    const onRefresh = vi.fn();
    render(<ResearchWorkbenchPanel project={project} onEditPlan={vi.fn()} onRefresh={onRefresh} onProposedPlan={vi.fn()} onRestored={onRestored} />);
    const input = await screen.findByLabelText("选择研究工作区包");
    const file = new File(["recorded package"], "workspace.zip", { type: "application/zip" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(onRestored).toHaveBeenCalledWith(restored));
    expect(api.restoreResearchWorkspace).toHaveBeenCalledWith(file);
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("exposes workspace restore before any project exists", async () => {
    const restored = { ...project, id: "restored-project", title: "Restored workspace" };
    api.restoreResearchWorkspace.mockResolvedValue(restored);
    api.getResearchProject.mockResolvedValue(restored);
    render(<ResearchWorkspace />);
    const input = await screen.findByLabelText("恢复研究压缩包");
    fireEvent.change(input, { target: { files: [new File(["recorded package"], "workspace.zip")] } });
    await waitFor(() => expect(window.location.search).toBe("?project=restored-project"));
    expect(await screen.findByRole("button", { name: /Restored workspace/ })).toBeInTheDocument();
    expect(api.researchAction).not.toHaveBeenCalled();
  });

  it("retains the user's toolbench tab when polling replaces project arrays", async () => {
    const view = render(<ResearchToolbench project={project} isAdmin={false} onImportFinished={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Literature & files" }));
    await screen.findByText("Search literature");
    await act(async () => { view.rerender(<ResearchToolbench project={{ ...project, graph: { nodes: [], edges: [] }, sources: [], branches: [], memories: [], formalBindings: [] }} isAdmin={false} onImportFinished={vi.fn()} />); });
    expect(screen.getByRole("button", { name: "Literature & files" })).toHaveClass("is-active");
    expect(screen.getByText("Search literature")).toBeInTheDocument();
  });
});

describe("Tauri directory picker adapter", () => {
  it("uses the documented plugin global and preserves cancellation", async () => {
    const open = vi.fn().mockResolvedValue(null);
    expect(hasResearchFolderPicker({ dialog: { open } })).toBe(true);
    expect(await pickResearchFolder({ dialog: { open } })).toBeNull();
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ directory: true, multiple: false }));
  });
  it("invokes the real Tauri v2 dialog command through core when required", async () => {
    const invoke = vi.fn().mockResolvedValue("D:/Research");
    expect(await pickResearchFolder({ core: { invoke } })).toBe("D:/Research");
    expect(invoke).toHaveBeenCalledWith("plugin:dialog|open", { options: expect.objectContaining({ directory: true, multiple: false }) });
  });
});
