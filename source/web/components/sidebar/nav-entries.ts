import {
  BookOpen,
  BookText,
  Bot,
  Brain,
  GraduationCap,
  HeartHandshake,
  House,
  Library,
  NotebookPen,
  PenLine,
  Route,
  Settings,
  Sparkles,
  Users,
  type LucideIcon,
} from "@/shared/ui/glyphs";

import type { Capability } from "@/lib/capability-routes";

/** V3-style sidebar sections. Groups give the feature list a reading order —
 *  what you do daily first, consoles last — instead of one flat pile. */
export type NavGroupId = "learning" | "tracking" | "library" | "more" | "context";

export interface NavEntry {
  href: string;
  label: string;
  icon: LucideIcon;
  tooltipKey?: string;
  /** Model capability this feature needs; locked when the user lacks it. */
  requires?: Capability;
  /** Section this feature lives in (defaults to "more"). */
  group?: NavGroupId;
}

export const NAV_GROUP_LABELS: Record<NavGroupId, string> = {
  learning: "学习",
  tracking: "追踪",
  library: "资料库",
  more: "更多学习功能",
  context: "学情与上下文",
};

export const NAV_GROUP_ORDER: NavGroupId[] = [
  "learning",
  "tracking",
  "library",
  "more",
  "context",
];

/**
 * The workspace features, in the order they ship in.
 *
 * This is the *default* arrangement, not the rendered one — a learner can
 * reorder these and fold the ones they don't use into "More"
 * (``lib/sidebar-layout.ts``). Adding an entry here places it for everyone,
 * including people who have already arranged their sidebar: it arrives next to
 * the neighbour it follows below rather than at the bottom of their list.
 */
export const PRIMARY_NAV: NavEntry[] = [
  // 学习 — the daily loop: start here, work in chat, review what is due.
  {
    href: "/aleggo",
    label: "首页 · 讲题",
    icon: GraduationCap,
    tooltipKey: "今日学习、掌握变化与下一步",
    group: "learning",
  },
  {
    href: "/chat",
    label: "学习功能",
    icon: House,
    tooltipKey: "讲题、第一处错误批改与引导学习",
    requires: "llm",
    group: "learning",
  },
  {
    href: "/mastery",
    label: "复习与掌握",
    icon: Sparkles,
    tooltipKey: "薄弱点、间隔复习与掌握地图",
    requires: "llm",
    group: "learning",
  },
  // 追踪 — where the learning evidence turns into a map.
  {
    href: "/courses",
    label: "课程体系",
    icon: Route,
    tooltipKey: "按课程组织教材、知识点与学习证据",
    requires: "llm",
    group: "tracking",
  },
  // 资料库 — everything that feeds the tutor: materials, notes, history.
  {
    href: "/knowledge-bases",
    label: "教材与资料",
    icon: BookOpen,
    tooltipKey: "管理教材、讲义、笔记与可追溯引用",
    group: "library",
  },
  {
    href: "/notebooks",
    label: "笔记",
    icon: NotebookPen,
    tooltipKey: "整理笔记、引用和学习证据",
    group: "library",
  },
  // 更多学习功能 — deeper consoles, folded by intent rather than by hand.
  {
    href: "/books",
    label: "活书",
    icon: Library,
    tooltipKey: "把课程材料编排成可持续更新的学习书",
    requires: "llm",
    group: "more",
  },
  {
    href: "/reading",
    label: "教材阅读",
    icon: BookText,
    tooltipKey: "在教材上下文中阅读、提问与做标记",
    requires: "llm",
    group: "more",
  },
  {
    href: "/co-writer",
    label: "笔记工坊",
    icon: PenLine,
    tooltipKey: "整理笔记、引用和学习证据",
    requires: "llm",
    group: "more",
  },
  {
    href: "/agents",
    label: "智能工具",
    icon: Bot,
    tooltipKey: "配置可调用的 Agent、技能与工具",
    group: "more",
  },
  // 学情与上下文 — identity, memory and companions the tutor learns from.
  {
    href: "/partners",
    label: "长期伙伴",
    icon: HeartHandshake,
    tooltipKey: "管理伙伴身份与可控长期记忆",
    requires: "llm",
    group: "context",
  },
  {
    href: "/memory",
    label: "长期记忆",
    icon: Brain,
    tooltipKey: "Memory tooltip",
    group: "context",
  },
];

/** Consoles that sit under the chat history. Not arrangeable: Settings has to
 *  stay findable, and a console nobody folds away is one less thing to explain. */
export const SECONDARY_NAV: NavEntry[] = [
  {
    // The study-record console keeps the richer legacy workspace reachable
    // without duplicating the primary Materials Library entry.
    href: "/space",
    label: "学习记录",
    icon: Users,
    tooltipKey: "错题、笔记、证据与学习活动记录",
  },
  { href: "/settings", label: "设置", icon: Settings },
];

export const PRIMARY_NAV_HREFS = PRIMARY_NAV.map((entry) => entry.href);

export const NAV_BY_HREF = new Map(
  [...PRIMARY_NAV, ...SECONDARY_NAV].map((entry) => [entry.href, entry]),
);

export function navGroupOf(href: string): NavGroupId {
  return NAV_BY_HREF.get(href)?.group ?? "more";
}

export function isNavActive(pathname: string, href: string) {
  if (href === "/space") {
    return (
      (pathname === "/space" || pathname.startsWith("/space/")) &&
      !pathname.startsWith("/mastery")
    );
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}
