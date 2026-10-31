import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ResearchWorkspace from "@/features/research/ResearchWorkspace";
import ResearchInputPanel from "@/features/research/ResearchInputPanel";
import ResearchTopBar from "@/features/research/ResearchTopBar";
import type { ResearchProject } from "@/lib/research-api";

const api = vi.hoisted(() => ({ listResearchProjects: vi.fn(), getResearchModels: vi.fn(), getResearchSettings: vi.fn(), getResearchProject: vi.fn(), createResearchProject: vi.fn(), importResearchDocument: vi.fn(), generateResearchPlan: vi.fn(), respondResearchInput: vi.fn(), researchAction: vi.fn(), getResearchWorkbench: vi.fn(), setResearchSupervision: vi.fn() }));
vi.mock("@/lib/research-api", async (original) => ({ ...await original<typeof import("@/lib/research-api")>(), ...api }));
vi.mock("@/features/research/ResearchWorkbenchPanel", () => ({ default: () => <div>Expert console</div> }));
vi.mock("@/features/research/ProviderModelSelector", () => ({ default: () => null }));
const project: ResearchProject = { id: "p1", title: "黎曼假设", question: "黎曼假设", modelId: "research.meridian", status: "DRAFT", currentVersion: 0, approvedVersion: null, lastError: "", createdAt: "", updatedAt: "", plan: null, revisions: [], sources: [] };
const required = { id: "input1", label: "原始定理", reason: "需要核对原文里的必要条件", kind: "document" as const, status: "OPEN" as const, stepId: "s2", planVersion: 1, response: "", sourceIds: [] };

beforeEach(() => {
  vi.clearAllMocks(); window.history.replaceState(null, "", "/research");
  api.listResearchProjects.mockResolvedValue({ items: [] });
  api.getResearchModels.mockResolvedValue({ items: [{ id: "research.meridian", label: "Meridian", description: "Advanced research" }], credentialConfigured: true });
  api.getResearchSettings.mockResolvedValue({ isAdmin: false });
  api.createResearchProject.mockResolvedValue(project);
  api.setResearchSupervision.mockResolvedValue({ supervisionMode: "MILESTONE_REVIEW" });
  api.getResearchProject.mockResolvedValue(project);
  api.getResearchWorkbench.mockResolvedValue({ state: { statusReason: "MISSING_INPUT" } });
});

describe("conversation research entry and mandatory inputs", () => {
  it("applies the selected supervision mode to a new research project", async () => {
    api.generateResearchPlan.mockResolvedValue({ ...project, status: "PLAN_READY" });
    render(<ResearchWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "小步骤暂停" }));
    fireEvent.change(screen.getByLabelText("研究问题"), { target: { value: "检验一个命题" } });
    fireEvent.click(screen.getByRole("button", { name: "起草计划" }));
    await waitFor(() => expect(api.setResearchSupervision).toHaveBeenCalledWith("p1", "STEP_BY_STEP"));
    await waitFor(() => expect(api.generateResearchPlan).toHaveBeenCalled());
  });

  it("sends a short Chinese question with attachments directly to planning, without executing research", async () => {
    api.importResearchDocument.mockResolvedValue({ id: "source1", chunkCount: 1 });
    api.generateResearchPlan.mockResolvedValue({ ...project, status: "PAUSED", inputRequests: [required] });
    render(<ResearchWorkspace />);
    await screen.findByRole("button", { name: "起草计划" });
    expect(screen.queryByText("Expert console")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建研究项目" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("研究问题"), { target: { value: "黎曼假设" } });
    const file = new File(["actual theorem bytes"], "theorem.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("添加研究资料"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "起草计划" }));
    expect(await screen.findByRole("region", { name: "等待补充资料" })).toBeInTheDocument();
    expect(api.createResearchProject).toHaveBeenCalledWith("黎曼假设", "research.meridian");
    expect(api.importResearchDocument).toHaveBeenCalledWith("p1", file);
    expect(api.generateResearchPlan).toHaveBeenCalledWith("p1", "research.meridian", "");
    expect(api.setResearchSupervision).toHaveBeenCalledWith("p1", "MILESTONE_REVIEW");
    expect(api.importResearchDocument.mock.invocationCallOrder[0]).toBeLessThan(api.generateResearchPlan.mock.invocationCallOrder[0]);
    expect(api.researchAction).not.toHaveBeenCalled();
  });

  it("stops before planning on an unreadable attachment and preserves the persisted draft", async () => {
    const savedDraft = { ...project, question: "Check the attached theorem" };
    api.createResearchProject.mockResolvedValue(savedDraft);
    api.getResearchProject.mockResolvedValue(savedDraft);
    api.importResearchDocument.mockResolvedValue({ id: "empty-source", chunkCount: 0 });
    render(<ResearchWorkspace />);
    fireEvent.change(await screen.findByLabelText("研究问题"), { target: { value: "Check the attached theorem" } });
    fireEvent.change(screen.getByLabelText("添加研究资料"), { target: { files: [new File([""], "empty.txt")] } });
    fireEvent.click(screen.getByRole("button", { name: "起草计划" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("未解析出可读内容");
    expect(api.generateResearchPlan).not.toHaveBeenCalled();
    expect(api.researchAction).not.toHaveBeenCalled();
    await waitFor(() => expect(window.location.search).toBe("?project=p1"));
    expect(screen.getByLabelText("研究问题")).toHaveValue("Check the attached theorem");
    fireEvent.click(screen.getByRole("button", { name: "移除 empty.txt" }));
    api.generateResearchPlan.mockResolvedValue(project);
    fireEvent.click(screen.getByRole("button", { name: "起草计划" }));
    await waitFor(() => expect(api.generateResearchPlan).toHaveBeenCalled());
    expect(api.createResearchProject).toHaveBeenCalledTimes(1);
  });

  it("requires document bytes, submits parsed source IDs and stays paused after a supplement", async () => {
    api.importResearchDocument.mockResolvedValue({ id: "readable-paper", chunkCount: 3 });
    const answered = { ...project, status: "PAUSED", inputRequests: [{ ...required, status: "RESOLVED" }] };
    api.respondResearchInput.mockResolvedValue(answered);
    const changed = vi.fn();
    render(<ResearchInputPanel project={{ ...project, status: "PAUSED", inputRequests: [required] }} onChanged={changed} onPlan={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("补充：原始定理"), { target: { value: "I already read it" } });
    expect(screen.getByRole("button", { name: "提交补充" })).toBeDisabled();
    const file = new File(["Necessary conditions"], "paper.txt");
    fireEvent.change(screen.getByLabelText("上传：原始定理"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "提交补充" }));
    await waitFor(() => expect(changed).toHaveBeenCalledWith(answered));
    expect(api.respondResearchInput).toHaveBeenCalledWith("p1", "input1", "I already read it", ["readable-paper"]);
    expect(api.researchAction).not.toHaveBeenCalled();
  });

  it("hides resume and override controls while necessary inputs are missing", async () => {
    api.getResearchWorkbench.mockResolvedValue({ state: { statusReason: "BLOCKED" } });
    render(<ResearchTopBar project={{ ...project, status: "PAUSED", currentVersion: 1, approvedVersion: 1, inputRequests: [required] }} refreshKey="blocked" onChanged={vi.fn()} />);
    await waitFor(() => expect(api.getResearchWorkbench).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "继续" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /仍要继续/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "停止" })).toBeInTheDocument();
  });
});
