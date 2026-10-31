import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AppShell from "@/components/layout/AppShell";

const route = vi.hoisted(() => ({ pathname: "/aleggo" }));

vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
vi.mock("next/link", async () => {
  const React = await import("react");
  return {
    default: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) =>
      React.createElement("a", { href, ...props }, children),
  };
});
vi.mock("next/image", async () => {
  const React = await import("react");
  return {
    default: (props: React.ImgHTMLAttributes<HTMLImageElement>) => React.createElement("img", props),
  };
});
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/components/auth/AdminLink", () => ({ AdminLink: () => null }));
vi.mock("@/components/auth/LogoutButton", () => ({ LogoutButton: () => null }));
vi.mock("@/components/auth/ProfileLink", () => ({ ProfileLink: () => null }));
vi.mock("@/components/sidebar/VersionBadge", () => ({ VersionBadge: () => null }));
vi.mock("@/lib/session-api", () => ({ listSessions: vi.fn(async () => []) }));
vi.mock("@/lib/research-api", () => ({ listResearchProjects: vi.fn(async () => ({ items: [] })) }));
vi.mock("@/shared/storage", () => ({
  browserStorage: { readRaw: () => null, writeRaw: () => true },
}));

describe("A-leggo study notebook navigation", () => {
  beforeEach(() => {
    route.pathname = "/aleggo";
  });

  it("keeps Study and Math Research as directly switchable contexts without mounting the legacy sidebar", () => {
    render(
      <AppShell sidebar={<aside data-testid="legacy-sidebar">Old navigation</aside>}>
        <div>Current workspace</div>
      </AppShell>,
    );

    expect(screen.queryByTestId("legacy-sidebar")).not.toBeInTheDocument();
    const contextSwitch = screen.getByRole("navigation", { name: "切换工作上下文" });
    expect(within(contextSwitch).getByRole("link", { name: /学习/ })).toHaveAttribute("href", "/aleggo");
    expect(within(contextSwitch).getByRole("link", { name: /数学研究/ })).toHaveAttribute("href", "/research");
    expect(screen.getByRole("link", { name: "新对话" })).toHaveAttribute("href", "/chat");
    expect(screen.getByRole("link", { name: "讲题首页" })).toHaveAttribute("href", "/aleggo");
  });

  it("keeps every preserved Study tool reachable through the functional directory", () => {
    render(
      <AppShell>
        <div>Current workspace</div>
      </AppShell>,
    );

    expect(screen.queryByLabelText("全部功能目录")).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "功能目录" });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const sidebar = screen.getByLabelText("全部功能目录");
    for (const [label, href] of [
      ["讲题首页", "/aleggo"],
      ["复习与掌握", "/mastery"],
      ["课程体系", "/courses"],
      ["教材与资料", "/knowledge-bases"],
      ["笔记", "/notebooks"],
      ["活书", "/books"],
      ["教材阅读", "/reading"],
      ["笔记工坊", "/co-writer"],
      ["智能工具", "/agents"],
      ["长期伙伴", "/partners"],
      ["长期记忆", "/memory"],
      ["学习记录", "/space"],
      ["对话记录", "/space/chat-history"],
      ["题库", "/space/questions"],
      ["技能注册", "/space/skills"],
      ["MCP 工具", "/space/mcp"],
    ] as const) {
      const links = Array.from(sidebar.querySelectorAll(`a[href="${href}"]`));
      expect(links.length > 0, `Preserved entry ${href}`).toBe(true);
      expect(links.some((link) => link.textContent?.includes(label)), `Preserved label ${label}`).toBe(true);
    }
    expect(screen.getByRole("link", { name: "设置" })).toHaveAttribute("href", "/settings");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByLabelText("全部功能目录")).not.toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("shows research entry points when the math research context is active", () => {
    route.pathname = "/research";
    render(
      <AppShell>
        <div>Current workspace</div>
      </AppShell>,
    );

    const sidebar = screen.getByLabelText("主导航");
    expect(screen.getByRole("link", { name: "新研究" })).toHaveAttribute("href", "/research");
    expect(sidebar.querySelector('a[href="/research"]')).not.toBeNull();
    expect(sidebar.querySelector('a[href="/settings"]')).not.toBeNull();
  });
});
