"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { AdminLink } from "@/components/auth/AdminLink";
import { LogoutButton } from "@/components/auth/LogoutButton";
import { ProfileLink } from "@/components/auth/ProfileLink";
import { VersionBadge } from "@/components/sidebar/VersionBadge";
import { Icon, type IconName } from "@/components/ui/Icon";
import { listResearchProjects, type ResearchProject } from "@/lib/research-api";
import { listSessions, type SessionSummary } from "@/lib/session-api";
import { sessionRoute } from "@/lib/mastery-session";
import { browserStorage } from "@/shared/storage";

/* Legacy sidebar children use this as an optional close callback. The v4
   shell ships its own left sidebar; keeping the narrow context makes the
   compatibility components safe for any remaining embedded caller. */
const SidebarDrawerContext = createContext<{ close: () => void } | null>(null);

export function useSidebarDrawer() {
  return useContext(SidebarDrawerContext);
}

interface AppShellProps {
  /** Kept for route-group compatibility; the v4 shell renders its own sidebar. */
  sidebar?: ReactNode;
  children: ReactNode;
}

type Context = "study" | "research";

interface NavItem {
  href: string;
  label: string;
  icon: IconName;
}

const STUDY_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "学习",
    items: [
      { href: "/aleggo", label: "讲题首页", icon: "home" },
      { href: "/mastery", label: "复习与掌握", icon: "graduation" },
      { href: "/courses", label: "课程体系", icon: "route" },
    ],
  },
  {
    label: "资料库",
    items: [
      { href: "/knowledge-bases", label: "教材与资料", icon: "library" },
      { href: "/notebooks", label: "笔记", icon: "notebook" },
      { href: "/books", label: "活书", icon: "book-open" },
      { href: "/reading", label: "教材阅读", icon: "file-search" },
      { href: "/co-writer", label: "笔记工坊", icon: "edit" },
    ],
  },
  {
    label: "工具",
    items: [
      { href: "/agents", label: "智能工具", icon: "bot" },
      { href: "/partners", label: "长期伙伴", icon: "heart" },
      { href: "/memory", label: "长期记忆", icon: "memory" },
    ],
  },
  {
    label: "记录",
    items: [
      { href: "/space", label: "学习记录", icon: "users" },
      { href: "/space/chat-history", label: "对话记录", icon: "history" },
      { href: "/space/questions", label: "题库", icon: "help" },
      { href: "/space/skills", label: "技能注册", icon: "grid" },
      { href: "/space/mcp", label: "MCP 工具", icon: "network" },
    ],
  },
];

const RESEARCH_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "研究",
    items: [
      { href: "/research", label: "研究工作台", icon: "flask" },
      { href: "/settings", label: "研究设置", icon: "settings" },
    ],
  },
];

const COLLAPSE_KEY = "aleggo.sidebar.collapsed";

/* Collapsed state lives in localStorage behind the shared storage boundary.
   useSyncExternalStore keeps the SSR render expanded while the client reads
   and persists the real preference without a cascading effect update. */
const collapseListeners = new Set<() => void>();

function subscribeToCollapse(callback: () => void) {
  collapseListeners.add(callback);
  return () => {
    collapseListeners.delete(callback);
  };
}

function readCollapseSnapshot(): boolean {
  return browserStorage.readRaw("local", COLLAPSE_KEY) === "1";
}

function isActivePath(pathname: string, href: string) {
  if (href === "/research") return pathname.startsWith("/research");
  if (href === "/space") return pathname === "/space" || pathname.startsWith("/space/");
  if (href === "/aleggo") return pathname === "/aleggo" || pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * A-leggo v4 shell: a collapsible left sidebar with two directly switchable
 * contexts — 学习 and 数学研究 — plus recent history, an on-demand library
 * and settings. Replaces the legacy top-bar + "选择工作台" navigator.
 */
export default function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const context: Context = pathname.startsWith("/research") ? "research" : "study";
  const collapsed = useSyncExternalStore(subscribeToCollapse, readCollapseSnapshot, () => false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [projects, setProjects] = useState<ResearchProject[]>([]);

  const toggleCollapsed = useCallback(() => {
    const next = readCollapseSnapshot() ? "0" : "1";
    browserStorage.writeRaw("local", COLLAPSE_KEY, next);
    for (const listener of collapseListeners) listener();
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (context === "study") {
      listSessions(6, 0)
        .then((items) => {
          if (!cancelled) setSessions(items.slice(0, 6));
        })
        .catch(() => {
          /* recent history is optional chrome */
        });
    } else {
      listResearchProjects()
        .then((response) => {
          if (!cancelled) setProjects((response.items ?? []).slice(0, 6));
        })
        .catch(() => {
          /* recent projects are optional chrome */
        });
    }
    return () => {
      cancelled = true;
    };
  }, [context, pathname]);

  const groups = context === "study" ? STUDY_GROUPS : RESEARCH_GROUPS;
  const primary = context === "study"
    ? { href: "/chat", label: "新对话", icon: "plus" as IconName }
    : { href: "/research", label: "新研究", icon: "plus" as IconName };
  const primaryActive = context === "study"
    ? pathname.startsWith("/chat")
    : pathname === "/research" && !new URLSearchParams(typeof window === "undefined" ? "" : window.location.search).get("project");

  const recent = useMemo(() => {
    if (context === "study") {
      return sessions.map((session) => ({
        key: session.session_id || session.id,
        href: sessionRoute(session),
        label: session.title || "未命名对话",
      }));
    }
    return projects.map((project) => ({
      key: project.id,
      href: `/research?project=${encodeURIComponent(project.id)}`,
      label: project.question || "未命名研究",
    }));
  }, [context, sessions, projects]);

  const close = useCallback(() => setMobileOpen(false), []);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close, mobileOpen]);

  const studyChrome = context === "study" ? (
      <SidebarDrawerContext.Provider value={{ close }}>
        <div className="aleggo-app-shell al-app-body al-study-desk" data-mode="study">
          <header className="al-study-masthead">
            <Link href="/aleggo" className="al-study-wordmark" aria-label="A-leggo 首页">
              <span>A-leggo</span>
            </Link>
            <nav className="al-study-contexts" aria-label="切换工作上下文">
              <Link href="/aleggo" aria-current="page">学习</Link>
              <Link href="/research">数学研究</Link>
            </nav>
          <div className={`al-study-routebar${collapsed ? " is-compact" : ""}`}>
            <nav aria-label="学习导航">
              {STUDY_GROUPS[0].items.map((item, index) => (
                <Link key={item.href} href={item.href} aria-current={isActivePath(pathname, item.href) ? "page" : undefined}>
                  <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>{item.label}
                </Link>
              ))}
            </nav>
            <div className="al-study-route-actions">
              <ActionButton type="button" onClick={() => setMobileOpen((open) => !open)} aria-expanded={mobileOpen} aria-controls="al-study-directory" className="al-study-directory-toggle">
                <Icon name="grid" size={15} />{mobileOpen ? "收起功能目录" : "功能目录"}
              </ActionButton>
              <ActionButton type="button" onClick={toggleCollapsed} className="al-study-density-toggle" aria-label={collapsed ? "展开导航" : "精简导航"} aria-pressed={collapsed}>
                <Icon name={collapsed ? "chevrons-right" : "chevrons-left"} size={15} />
              </ActionButton>
              <Link href={primary.href} className="al-study-new"><Icon name="plus" size={15} />{primary.label}</Link>
            </div>
          </div>
            <div className="al-study-account">
              <ProfileLink /><AdminLink /><LogoutButton />
              <Link href="/settings" className="al-study-setting" aria-label="设置"><Icon name="settings" size={16} /><span>设置</span></Link>
            </div>
          </header>
          {mobileOpen ? (
            <section id="al-study-directory" className="al-study-directory" aria-label="全部功能目录">
              {STUDY_GROUPS.map((group, groupIndex) => (
                <section key={group.label} aria-label={group.label}>
                  <h2><span>{String(groupIndex + 1).padStart(2, "0")}</span>{group.label}</h2>
                  {group.items.map((item) => (
                    <Link key={item.href} href={item.href} onClick={close} aria-current={isActivePath(pathname, item.href) ? "page" : undefined}>
                      <Icon name={item.icon} size={15} /><span>{item.label}</span><span aria-hidden="true">↗</span>
                    </Link>
                  ))}
                </section>
              ))}
              <section aria-label="最近对话">
                <h2><span>05</span>最近对话</h2>
                {recent.length ? recent.map((item) => <Link key={item.key} href={item.href} onClick={close}><Icon name="chat" size={15} /><span>{item.label}</span><span aria-hidden="true">↗</span></Link>) : <p>暂无对话记录</p>}
                <Link href="/space/chat-history" onClick={close}><Icon name="history" size={15} /><span>全部记录</span><span aria-hidden="true">↗</span></Link>
                <VersionBadge />
              </section>
            </section>
          ) : null}
          <main className="al-study-content">{children}</main>
        </div>
      </SidebarDrawerContext.Provider>
    ) : null;
  if (studyChrome) return studyChrome;

  const renderLink = (item: NavItem) => {
    const active = isActivePath(pathname, item.href);
    return (
      <Link
        key={item.href}
        href={item.href}
        onClick={close}
        aria-current={active ? "page" : undefined}
        className={active ? "al-sidebar-link is-active" : "al-sidebar-link"}
      >
        <span className="al-sidebar-link-icon">
          <Icon name={item.icon} size={16} />
        </span>
        <span className="al-sidebar-link-text">{item.label}</span>
        <span className="al-rail-tip" aria-hidden="true">{item.label}</span>
      </Link>
    );
  };

  return (
    <SidebarDrawerContext.Provider value={{ close }}>
      <div
        className={`aleggo-app-shell al-app-body${context === "research" ? " aleggo-research-shell" : ""}`}
        data-mode={context}
      >
        {mobileOpen ? (
          <ActionButton
            className="al-sidebar-scrim"
            type="button"
            aria-label="关闭导航"
            onClick={close}
          />
        ) : null}

        <aside
          className={[
            "al-sidebar",
            collapsed ? "is-collapsed" : "",
            mobileOpen ? "is-mobile-open" : "",
          ].join(" ")}
          data-context={context}
          aria-label="主导航"
        >
          <div className="al-sidebar-brand">
            <Link href="/aleggo" className="flex items-center gap-2 overflow-hidden" aria-label="A-leggo 首页">
              <Image src="/aleggo-mark.svg" alt="" width={26} height={26} />
              <span className="al-sidebar-brand-name">
                A-leggo
                <small>学术工作台</small>
              </span>
            </Link>
            <ActionButton
              type="button"
              className="al-sidebar-link mt-0 hidden min-h-[30px] w-[30px] shrink-0 justify-center px-0 md:inline-flex"
              onClick={toggleCollapsed}
              aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
              aria-expanded={!collapsed}
            >
              <Icon name={collapsed ? "chevrons-right" : "chevrons-left"} size={15} />
            </ActionButton>
          </div>

          <nav className="al-context-switch" aria-label="切换工作上下文">
            <Link
              href="/aleggo"
              className={`al-context-tab${context === "study" ? " is-active" : ""}`}
              aria-current={context === "study" ? "page" : undefined}
              onClick={close}
            >
              <Icon name="graduation" size={14} />
              <span>学习</span>
            </Link>
            <Link
              href="/research"
              className={`al-context-tab${context === "research" ? " is-active" : ""}`}
              aria-current={context === "research" ? "page" : undefined}
              onClick={close}
            >
              <Icon name="flask" size={14} />
              <span>数学研究</span>
            </Link>
          </nav>

          <Link href={primary.href} onClick={close} className={`al-sidebar-primary${primaryActive ? "" : ""}`} aria-label={primary.label}>
            <Icon name={primary.icon} size={15} />
            <span>{primary.label}</span>
          </Link>

          <div className="al-sidebar-scroll">
            {groups.map((group) => (
              <section key={group.label} aria-label={group.label}>
                <span className="al-sidebar-group-label">{group.label}</span>
                {group.items.map(renderLink)}
              </section>
            ))}

            {recent.length > 0 ? (
              <section aria-label={context === "study" ? "最近对话" : "最近研究"}>
                <span className="al-sidebar-group-label">{context === "study" ? "最近对话" : "最近研究"}</span>
                {recent.map((item) => {
                  const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                  return (
                    <Link
                      key={item.key}
                      href={item.href}
                      onClick={close}
                      className={active ? "al-sidebar-recent is-active" : "al-sidebar-recent"}
                    >
                      <span className="al-sidebar-link-icon">
                        <Icon name={context === "study" ? "chat" : "flask"} size={13} />
                      </span>
                      <span className="al-sidebar-recent-label">{item.label}</span>
                      <span className="al-rail-tip" aria-hidden="true">{item.label}</span>
                    </Link>
                  );
                })}
              </section>
            ) : null}
          </div>

          <footer className="al-sidebar-footer">
            {renderLink({ href: "/settings", label: "设置", icon: "settings" })}
            <div className="flex items-center justify-between gap-1 px-1 pt-1 text-[11px] text-[var(--al-text-muted)]">
              <div className="flex min-w-0 items-center gap-1 overflow-hidden">
                <ProfileLink />
                <AdminLink />
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <LogoutButton />
              </div>
            </div>
            <div className="px-1 pt-0.5">
              <VersionBadge />
            </div>
          </footer>
        </aside>

        <main className="al-app-main">
          <div className="flex h-[46px] shrink-0 items-center gap-2 border-b border-[var(--al-border)] px-3 md:hidden">
            <ActionButton
              type="button"
              className="al-sidebar-link min-h-[32px] w-8 justify-center px-0"
              onClick={() => setMobileOpen(true)}
              aria-label="打开导航"
              aria-expanded={mobileOpen}
            >
              <Icon name="menu" size={17} />
            </ActionButton>
            <Link href="/aleggo" className="flex items-center gap-1.5" aria-label="A-leggo 首页">
              <Image src="/aleggo-mark.svg" alt="" width={20} height={20} />
              <span className="text-[13px] font-bold">A-leggo</span>
            </Link>
            <span className="ml-auto inline-flex items-center gap-1 text-[12px] font-semibold text-[var(--al-text-muted)]">
              <Icon name={context === "study" ? "graduation" : "flask"} size={13} />
              {context === "study" ? "学习" : "数学研究"}
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
        </main>
      </div>
    </SidebarDrawerContext.Provider>
  );
}
