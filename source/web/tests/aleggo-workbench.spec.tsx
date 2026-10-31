import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AleggoWorkbench from "@/features/aleggo/AleggoWorkbench";

const mocks = vi.hoisted(() => ({
  fetchOverview: vi.fn(),
  fetchMethodCards: vi.fn(),
  fetchIdentity: vi.fn(),
  fetchSkills: vi.fn(),
  confirmMethodCard: vi.fn(),
  setSkillEnabled: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
}));

vi.mock("@/features/aleggo/api", () => ({
  ...mocks,
  createMethodCard: vi.fn(),
  createStyle: vi.fn(),
  createCompanion: vi.fn(),
  addCompanionMemory: vi.fn(),
  confirmCompanionMemory: vi.fn(),
  deleteCompanionMemory: vi.fn(),
  registerSkill: vi.fn(),
  importLegacy: vi.fn(),
  fetchBackup: vi.fn(),
}));

const overview = {
  product: "A-leggo Next",
  version: "4.0.0",
  due_reviews: [],
  due_review_count: 0,
  weak_points: [],
  mastery: [
    {
      concept_id: "sequence-limit",
      score: 0.2,
      confidence: 0.3,
      level: "insufficient",
      evidence_sufficient: false,
      assessed_count: 1,
      evidence_kinds: ["basic_problem"],
      source_count: 1,
      message: "证据不足/暂无法判断",
    },
  ],
  recent_evidence: [],
  method_card_count: 0,
  note_count: 0,
};

describe("A-leggo workbench", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState(null, "", "/aleggo");
    mocks.push.mockClear();
    localStorage.setItem("aleggo-next.setup-complete", "1");
    mocks.fetchOverview.mockResolvedValue(overview);
    mocks.fetchMethodCards.mockResolvedValue([]);
    mocks.fetchIdentity.mockResolvedValue({ styles: [], companions: [], memories: [] });
    mocks.fetchSkills.mockResolvedValue([]);
  });

  it("sends math research to the plan-first research workspace", async () => {
    render(<AleggoWorkbench />);
    expect(await screen.findByText("今天，想聊哪一道题？")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/把题目贴进来/), {
      target: { value: "Prove a useful mathematical statement" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "数学研究" }));
    fireEvent.click(screen.getByRole("button", { name: /拟定研究计划/ }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/research?question=Prove+a+useful+mathematical+statement",
    );
  });

  it("does not turn insufficient evidence into a mastered percentage claim", async () => {
    window.history.replaceState(null, "", "/aleggo#today");
    render(<AleggoWorkbench />);
    expect(await screen.findByText("sequence-limit")).toBeInTheDocument();
    expect(screen.getByText("证据不足/暂无法判断")).toBeInTheDocument();
    expect(screen.queryByText("掌握 20% · 置信 30%")).not.toBeInTheDocument();
  });

  it("keeps an agent-proposed method card pending until confirmation", async () => {
    mocks.fetchMethodCards.mockResolvedValue([
      {
        id: "card_1",
        problem_type: "一致连续性证明",
        key_insight: "delta 不依赖点",
        common_methods: ["定义法"],
        key_steps: ["固定 epsilon"],
        common_errors: ["依赖 x"],
        next_first_reaction: "检查依赖关系",
        status: "candidate",
        concept_id: "uniform-continuity",
      },
    ]);
    mocks.confirmMethodCard.mockResolvedValue({});
    render(<AleggoWorkbench />);
    await screen.findByText("今天，想聊哪一道题？");
    fireEvent.click(screen.getByRole("link", { name: /方法卡/ }));
    await screen.findByRole("heading", { name: "方法卡" });
    fireEvent.click(await screen.findByRole("button", { name: /确认保存/ }));
    await waitFor(() => expect(mocks.confirmMethodCard).toHaveBeenCalledWith("card_1"));
  });

  it("shows the first-run setup wizard once and persists completion", async () => {
    localStorage.removeItem("aleggo-next.setup-complete");
    render(<AleggoWorkbench />);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "稍后设置" }));
    expect(localStorage.getItem("aleggo-next.setup-complete")).toBe("1");
  });
});

