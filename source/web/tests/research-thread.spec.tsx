import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ResearchThread from "@/features/research/ResearchThread";
import type { ResearchProject, ResearchThreadItem, ResearchThreadPage } from "@/lib/research-api";

const api = vi.hoisted(() => ({ getResearchThread: vi.fn(), addResearchIntervention: vi.fn() }));
vi.mock("@/lib/research-api", async (original) => ({ ...await original<typeof import("@/lib/research-api")>(), ...api }));
vi.mock("@/features/research/ProviderModelSelector", () => ({ default: () => null }));
vi.mock("@/features/research/ResearchModeSelector", () => ({ default: () => null }));

const project: ResearchProject = {
  id: "p1", title: "T", question: "证明素数有无穷多个", modelId: "research.meridian", status: "RUNNING", currentVersion: 1, approvedVersion: 1,
  lastError: "", createdAt: "", updatedAt: "", plan: null, revisions: [], artifacts: [], sources: [],
};
const stamp = (n: number) => `2026-10-12T00:00:${String(n).padStart(2, "0")}+00:00`;
const item = (extra: Record<string, unknown>) => ({ cursor: `${extra.createdAt}|${extra.id}`, ...extra }) as ResearchThreadItem;
const page = (items: ResearchThreadItem[], extra: Partial<ResearchThreadPage> = {}): ResearchThreadPage => ({
  items, hasMore: false, nextCursor: items.length ? items[items.length - 1].cursor : "", newestCursor: items.length ? items[0].cursor : "", queue: {}, live: null, ...extra,
});

const tool = item({ id: "t:1", createdAt: stamp(3), kind: "tool", tool: "sympy", status: "VERIFIED", ok: true, input: '{"expr":"x+x"}', inputTruncated: false, output: "2*x", outputTruncated: false, elapsedMs: 340 });
const answer = item({ id: "a:1", createdAt: stamp(4), kind: "answer", title: "步骤报告", role: "explorer", status: "CONJECTURAL", text: "结论：**成立**", truncated: false, artifactId: "a1" });
const mine = item({ id: "u:1", createdAt: stamp(2), kind: "user", intervention: "human_note", text: "只考虑奇素数", truncated: false, branch: "", queueId: "" });

beforeEach(() => {
  vi.clearAllMocks();
  api.getResearchThread.mockResolvedValue(page([answer, tool, mine], { live: { action: "正在验证引理", tool: "sympy", agent: "", phase: "", step: "", stepIndex: 1, stepTotal: 3 } }));
  api.addResearchIntervention.mockResolvedValue({ recorded: "human_note", projectId: "p1" });
});

const props = () => ({ project, onChanged: vi.fn(), onAttach: vi.fn(), onProposedPlan: vi.fn() });

describe("research conversation", () => {
  it("saves a researcher note from the same composer without sending it as a discussion", async () => {
    render(<ResearchThread {...props()} />);
    await screen.findByText("只考虑奇素数");
    fireEvent.change(screen.getByLabelText("向研究插话"), { target: { value: "/备注 请记录边界条件" } });
    fireEvent.click(screen.getByRole("button", { name: "发送插话" }));
    await waitFor(() => expect(api.addResearchIntervention).toHaveBeenCalledWith("p1", "human_note", { message: "请记录边界条件", note: "请记录边界条件", branch: "main" }));
  });

  it("shows answers normally and opens each tool call independently", async () => {
    const secondTool = item({ ...tool, id: "t:2", createdAt: stamp(4), tool: "python", output: "second output", ok: false });
    api.getResearchThread.mockResolvedValue(page([answer, secondTool, tool, mine], { live: { action: "正在验证引理", tool: "sympy", agent: "", phase: "", step: "", stepIndex: 1, stepTotal: 3 } }));
    render(<ResearchThread {...props()} />);
    expect(await screen.findByText("只考虑奇素数")).toBeInTheDocument();
    expect(screen.getByText("证明素数有无穷多个")).toBeInTheDocument();
    expect(screen.getByText(/成立/)).toBeInTheDocument();
    const call = screen.getByText("sympy").closest("details")!;
    const secondCall = screen.getByText("python").closest("details")!;
    expect(call).not.toHaveAttribute("open");
    expect(secondCall).not.toHaveAttribute("open");
    expect(screen.queryByText("2*x")).not.toBeInTheDocument();
    call.open = true; fireEvent(call, new Event("toggle"));
    expect(await screen.findByText("2*x")).toBeInTheDocument();
    expect(secondCall).not.toHaveAttribute("open");
    expect(screen.queryByText("second output")).not.toBeInTheDocument();
    secondCall.open = true; fireEvent(secondCall, new Event("toggle"));
    expect(await screen.findByText("second output")).toBeInTheDocument();
    expect(secondCall).toHaveTextContent("未成功");
    expect(screen.getByText("正在验证引理")).toBeInTheDocument();
  });

  it("folds intermediate reports individually while preserving full content and direct replies", async () => {
    const agent = item({ id: "a:agent", createdAt: stamp(1), kind: "answer", title: "探索一个路线", role: "explorer", status: "CONJECTURAL", text: "中间过程摘要", truncated: true, artifactId: "agent" });
    const step = item({ id: "a:step", createdAt: stamp(2), kind: "answer", title: "Step 01 · Formalize", role: "research-step-report", status: "CONJECTURAL", text: "步骤正文", truncated: false, artifactId: "step" });
    const milestone = item({ id: "a:milestone", createdAt: stamp(3), kind: "answer", title: "Milestone · 完成阶段", role: "milestone-controller", status: "MILESTONE_REPORT", text: "阶段正文", truncated: false, artifactId: "milestone" });
    const final = item({ id: "a:final", createdAt: stamp(4), kind: "answer", title: "最终验证", role: "independent-verifier", status: "UNVERIFIED_SYNTHESIS", text: "重要的最终结论", truncated: false, artifactId: "final" });
    const reply = item({ id: "a:reply", createdAt: stamp(5), kind: "answer", title: "Discussion", role: "research-collaborator", status: "CONJECTURAL", text: "直接讨论的回答", truncated: false, artifactId: "reply" });
    api.getResearchThread.mockResolvedValue(page([reply, final, milestone, step, agent]));
    const artifact = { id: "agent", projectId: "p1", planVersion: 1, stepId: "s1", agentRole: "explorer", title: "探索一个路线", content: "这是完整的中间过程", status: "CONJECTURAL", createdAt: "", metadata: { artifactKind: "agent-output" } };
    render(<ResearchThread {...props()} project={{ ...project, artifacts: [artifact] }} />);
    const agentRow = (await screen.findByText("研究过程")).closest("details")!;
    const stepRow = screen.getByText("步骤记录").closest("details")!;
    const milestoneRow = screen.getByText("阶段记录").closest("details")!;
    expect(agentRow).not.toHaveAttribute("open");
    expect(stepRow).not.toHaveAttribute("open");
    expect(milestoneRow).not.toHaveAttribute("open");
    expect(screen.queryByText("中间过程摘要")).not.toBeInTheDocument();
    expect(screen.getByText("重要的最终结论")).toBeInTheDocument();
    expect(screen.getByText("直接讨论的回答")).toBeInTheDocument();
    agentRow.open = true; fireEvent(agentRow, new Event("toggle"));
    expect(await screen.findByText("这是完整的中间过程")).toBeInTheDocument();
    expect(screen.queryByText("步骤正文")).not.toBeInTheDocument();
    stepRow.open = true; fireEvent(stepRow, new Event("toggle"));
    expect(await screen.findByText("步骤正文")).toBeInTheDocument();
    expect(milestoneRow).not.toHaveAttribute("open");
  });

  it("lets the researcher interject while research is running, then refreshes with a cursor", async () => {
    const onChanged = vi.fn();
    render(<ResearchThread {...props()} onChanged={onChanged} />);
    await screen.findByText("只考虑奇素数");
    const input = screen.getByLabelText("向研究插话");
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: "再加一个条件：n 为奇数" } });
    fireEvent.click(screen.getByRole("button", { name: "发送插话" }));
    await waitFor(() => expect(api.addResearchIntervention).toHaveBeenCalledWith("p1", "discuss", { message: "再加一个条件：n 为奇数" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(api.getResearchThread).toHaveBeenCalledWith("p1", { after: answer.cursor });
    expect(input).toHaveValue("");
  });

  it("talks to the AI collaborator by default and shows its reply state", async () => {
    api.addResearchIntervention.mockResolvedValue({ recorded: "discuss", projectId: "p1", status: "RUNNING", queueId: "q1" });
    const asked = item({ id: "u:2", createdAt: stamp(5), kind: "user", intervention: "discuss", text: "n=2 时成立吗？", truncated: false, branch: "", queueId: "q1" });
    const reply = item({ id: "a:2", createdAt: stamp(6), kind: "answer", title: "Discussion", role: "research-collaborator", status: "CONJECTURAL", text: "算过了：成立", truncated: false, artifactId: "a2" });
    const onChanged = vi.fn();
    render(<ResearchThread {...props()} onChanged={onChanged} />);
    await screen.findByText("只考虑奇素数");
    expect(screen.queryByRole("combobox", { name: "输入类型" })).not.toBeInTheDocument();
    api.getResearchThread.mockResolvedValue(page([asked], { queue: { q1: "RUNNING" } }));
    fireEvent.change(screen.getByLabelText("向研究插话"), { target: { value: "n=2 时成立吗？" } });
    fireEvent.click(screen.getByRole("button", { name: "发送插话" }));
    await waitFor(() => expect(api.addResearchIntervention).toHaveBeenCalledWith("p1", "discuss", { message: "n=2 时成立吗？" }));
    expect(await screen.findByText("AI 思考中…")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "输入类型" })).not.toBeInTheDocument();
    api.getResearchThread.mockResolvedValue(page([reply], { queue: { q1: "COMPLETED" } }));
    fireEvent.change(screen.getByLabelText("向研究插话"), { target: { value: "再算 n=3" } });
    fireEvent.click(screen.getByRole("button", { name: "发送插话" }));
    expect(await screen.findByText("算过了：成立")).toBeInTheDocument();
    expect(screen.getByText(/研究伙伴/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("AI 思考中…")).not.toBeInTheDocument());
  });

  it("keeps plan proposals reviewable when the unified conversation returns one", async () => {
    const proposed = { objective: "新路线" } as never;
    api.addResearchIntervention.mockResolvedValue({ recorded: "add_human_idea", projectId: "p1", proposedPlan: proposed });
    const handlers = props();
    render(<ResearchThread {...handlers} />);
    await screen.findByText("只考虑奇素数");
    fireEvent.change(screen.getByLabelText("向研究插话"), { target: { value: "改用反证法" } });
    fireEvent.keyDown(screen.getByLabelText("向研究插话"), { key: "Enter" });
    await waitFor(() => expect(handlers.onProposedPlan).toHaveBeenCalledWith(proposed));
    expect(api.addResearchIntervention).toHaveBeenCalledWith("p1", "discuss", { message: "改用反证法" });
  });

  it.each(["帮我找反例", "帮我证明一下这个，必要时查文献", "检查这个推导有没有错误", "只考虑正整数，重新分析"])("forwards the natural request without a manual mode: %s", async (message) => {
    render(<ResearchThread {...props()} />);
    await screen.findByText("只考虑奇素数");
    expect(screen.queryByRole("combobox", { name: "输入类型" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("向研究插话"), { target: { value: message } });
    fireEvent.click(screen.getByRole("button", { name: "发送插话" }));
    await waitFor(() => expect(api.addResearchIntervention).toHaveBeenCalledWith("p1", "discuss", { message }));
  });

  it("does not send on Shift+Enter, empty text or while the plan is still being drafted", async () => {
    const { rerender } = render(<ResearchThread {...props()} />);
    await screen.findByText("只考虑奇素数");
    fireEvent.change(screen.getByLabelText("向研究插话"), { target: { value: "第一行" } });
    fireEvent.keyDown(screen.getByLabelText("向研究插话"), { key: "Enter", shiftKey: true });
    expect(api.addResearchIntervention).not.toHaveBeenCalled();
    rerender(<ResearchThread {...props()} project={{ ...project, status: "PLANNING" }} />);
    expect(screen.getByLabelText("向研究插话")).toBeDisabled();
  });

  it("loads older history only on demand and keeps the earlier rows", async () => {
    const older = item({ id: "u:0", createdAt: stamp(1), kind: "user", intervention: "human_note", text: "最早的补充", truncated: false, branch: "", queueId: "" });
    api.getResearchThread.mockResolvedValueOnce(page([answer, tool, mine], { hasMore: true, nextCursor: mine.cursor }));
    api.getResearchThread.mockResolvedValueOnce(page([older], { hasMore: false, nextCursor: older.cursor, newestCursor: older.cursor }));
    render(<ResearchThread {...props()} project={{ ...project, status: "COMPLETED" }} />);
    await screen.findByText("只考虑奇素数");
    expect(screen.queryByText("最早的补充")).not.toBeInTheDocument();
    expect(screen.queryByText("证明素数有无穷多个")).not.toBeInTheDocument(); // the question appears only once the start is reached
    fireEvent.click(screen.getByRole("button", { name: /加载更早的记录/ }));
    expect(await screen.findByText("最早的补充")).toBeInTheDocument();
    expect(api.getResearchThread).toHaveBeenLastCalledWith("p1", { limit: 40, before: mine.cursor });
    expect(screen.getByText("只考虑奇素数")).toBeInTheDocument();
    expect(await screen.findByText("证明素数有无穷多个")).toBeInTheDocument();
  });

  it("renders the missing-material request and approvals in the flow via the footer", async () => {
    render(<ResearchThread {...props()} footer={<section aria-label="等待补充资料">需要资料</section>} />);
    expect(await screen.findByRole("region", { name: "等待补充资料" })).toBeInTheDocument();
  });

  it("renders LaTeX in AI replies with KaTeX and never as raw HTML", async () => {
    const math = item({ id: "a:2", createdAt: stamp(5), kind: "answer", title: "推导", role: "collaborator", status: "CONJECTURAL",
      text: "设 $f(x)=x^2+1$，则\n\n$$\\int_0^1 f(x)\\,dx = \\frac{4}{3}$$\n\n<img src=x onerror=alert(1)>", truncated: false, artifactId: "a2" });
    api.getResearchThread.mockResolvedValue(page([math, mine]));
    const { container } = render(<ResearchThread {...props()} />);
    await waitFor(() => expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2));
    expect(container).not.toHaveTextContent("$f(x)=x^2+1$");
    expect(container.querySelector("img[onerror]")).toBeNull();
  });
});
