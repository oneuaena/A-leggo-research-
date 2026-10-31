"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import Link from "next/link";
import { Button } from "@/shared/ui/Button";
import { IconButton } from "@/shared/ui/IconButton";
import { Icon, type IconName } from "@/components/ui/Icon";

/*
 * A-leggo v4 button & icon reference — rendered by the real production
 * primitives (web/shared/ui/Button.tsx, IconButton.tsx, ui/Icon.tsx) with
 * the live theme tokens. This page is the acceptance artifact for the
 * redesigned control language; it is also the canonical style guide.
 */

const ALL_ICONS: IconName[] = [
  "spark", "chat", "flask", "home", "library", "notebook", "book-open",
  "graduation", "route", "memory", "bot", "heart", "history", "help",
  "grid", "network", "settings", "user", "users", "shield", "shield-check",
  "logout", "plus", "paperclip", "send", "stop", "mic", "cpu", "image",
  "wrench", "search", "more-h", "more-v", "x", "menu", "check", "alert",
  "play", "pause", "download", "upload", "folder", "file", "file-search",
  "branch", "layers", "clock", "refresh", "eye", "external", "edit",
  "save", "list", "trash", "copy", "target", "lightbulb", "chart", "atom",
  "key", "globe", "arrow-right", "arrow-up-right", "panel-left", "panel-right",
  "chevron-down", "chevron-left", "chevron-right", "chevrons-left",
  "chevrons-right", "link", "pin",
];

function Row({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-[14px] border border-[var(--al-border)] bg-[var(--al-surface)] p-5">
      <h2 className="text-[15px] font-bold text-[var(--al-text)]">{title}</h2>
      {note ? <p className="mt-1 text-[12px] text-[var(--al-text-muted)]">{note}</p> : null}
      <div className="mt-4 flex flex-wrap items-center gap-3">{children}</div>
    </section>
  );
}

export default function DesignButtonsPage() {
  return (
    <main className="h-full overflow-y-auto px-6 py-8">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <header>
          <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-[var(--al-accent-text)]">
            A-leggo v4 · 设计体系
          </p>
          <h1 className="mt-2 font-serif text-3xl">按钮与图标对照图</h1>
          <p className="mt-2 max-w-2xl text-[13.5px] leading-6 text-[var(--al-text-muted)]">
            本页全部控件由生产组件直接渲染：<code>shared/ui/Button</code>、
            <code>shared/ui/IconButton</code>、<code>components/ui/Icon</code>
            （70 个原创 SVG 图标）。用于验收新的按钮语言：形状、状态、图标图案与组合。
            <Link href="/aleggo" className="ml-1 text-[var(--al-accent-text)] underline">返回首页</Link>
          </p>
        </header>

        <Row title="主要按钮 Primary（实色靛蓝）" note="页面上的唯一强调动作；悬停加深、按下 0.985、键盘焦点为 accent 圆环。">
          <Button>开始学习</Button>
          <Button variant="primary" loading>生成中…</Button>
          <Button variant="primary" disabled>已禁用</Button>
          <Button size="sm">小号</Button>
          <Button size="lg">批准并开始研究</Button>
        </Row>

        <Row title="次要按钮 Secondary（描边面板）" note="并列操作使用；悬停带 accent 描边。">
          <Button variant="secondary">保存草稿</Button>
          <Button variant="secondary" icon={<Icon name="save" size={14} />}>带图标</Button>
          <Button variant="secondary" selected>已选中状态</Button>
          <Button variant="secondary" disabled>已禁用</Button>
        </Row>

        <Row title="柔和按钮 Soft 与幽灵按钮 Ghost" note="Soft 用于强调但不抢占主操作的项；Ghost 用于低层级操作。">
          <Button variant="soft">里程碑审阅</Button>
          <Button variant="ghost">展开历史</Button>
          <Button variant="ghost" loading>加载中</Button>
        </Row>

        <Row title="危险按钮 Danger" note="仅用于不可逆操作：停止、删除、断开连接。">
          <Button variant="danger">停止研究</Button>
          <Button variant="secondary">取消</Button>
        </Row>

        <Row title="发送 / 停止 四态" note="聊天输入框的同一控件横跨整轮对话。">
          <span className="inline-grid h-8 w-8 place-items-center rounded-full ring-1 ring-inset ring-[var(--al-border-strong)] text-[var(--al-text-muted)]"><Icon name="send" size={15} /></span>
          <span className="inline-grid h-8 w-8 place-items-center rounded-full bg-[var(--al-accent)] text-[var(--al-accent-contrast)] ring-[3px] ring-[color-mix(in_srgb,var(--al-accent)_18%,transparent)]"><Icon name="send" size={15} strokeWidth={2.4} /></span>
          <span className="relative inline-grid h-8 w-8 place-items-center rounded-full bg-[var(--al-accent)] text-[var(--al-accent-contrast)]"><span className="absolute -inset-[3px] animate-spin rounded-full border-2 border-[color-mix(in_srgb,var(--al-accent)_15%,transparent)] border-t-[var(--al-accent)]" /><Icon name="stop" size={11} strokeWidth={2.6} /></span>
          <span className="inline-grid h-8 w-8 place-items-center rounded-full bg-[color-mix(in_srgb,var(--al-text-muted)_30%,transparent)] text-[var(--al-text)]"><Icon name="send" size={15} /></span>
        </Row>

        <Row title="图标按钮 IconButton" note="悬停出提示（tooltip），必须带可访问名称。">
          <IconButton label="新建对话" icon={<Icon name="plus" size={15} />} />
          <IconButton label="收起侧栏" icon={<Icon name="chevrons-left" size={15} />} variant="secondary" />
          <IconButton label="搜索" icon={<Icon name="search" size={15} />} selected />
          <IconButton label="删除" icon={<Icon name="trash" size={15} />} variant="primary" />
        </Row>

        <Row title="状态徽标与事件卡" note="研究运行时的实时状态与对话中的简洁事件卡片。">
          <div className="w-full max-w-md">
            <div className="al-research-topbar">
              <span className="al-research-topbar-status"><span className="al-pulse-dot" />研究进行中</span>
              <span className="al-research-topbar-step"><strong>正在检索与紧性假设有关的文献 · 步骤 2/6</strong></span>
              <span className="al-research-topbar-actions">
                <ActionButton className="al-btn al-btn-sm">暂停</ActionButton>
                <ActionButton className="al-btn al-btn-sm al-btn-danger">停止</ActionButton>
              </span>
            </div>
            <div className="al-event-card mt-3">
              <div className="al-event-card-head"><Icon name="check" size={14} /> 研究计划已生成 — 请审阅后批准</div>
              <div className="al-event-card-body">研究不会在批准前执行。检查目标、假设与步骤后批准。</div>
              <div className="al-event-card-actions">
                <ActionButton className="al-btn al-btn-primary">批准并开始研究</ActionButton>
                <ActionButton className="al-btn">编辑计划</ActionButton>
              </div>
            </div>
          </div>
        </Row>

        <Row title="侧栏导航" note="新左侧栏的链接、选中态与分组标签。">
          <div className="flex w-full max-w-xs flex-col gap-1 rounded-[12px] border border-[var(--al-border)] p-3">
            <span className="al-sidebar-group-label">学习</span>
            <span className="al-sidebar-link"><span className="al-sidebar-link-icon"><Icon name="home" size={15} /></span><span className="al-sidebar-link-text">讲题首页</span></span>
            <span className="al-sidebar-link is-active"><span className="al-sidebar-link-icon"><Icon name="graduation" size={15} /></span><span className="al-sidebar-link-text">复习与掌握</span></span>
            <span className="al-sidebar-recent"><span className="al-sidebar-link-icon"><Icon name="chat" size={13} /></span><span className="al-sidebar-recent-label">最近对话：二次型判别</span></span>
            <span className="al-sidebar-primary"><Icon name="plus" size={14} /> 新对话</span>
          </div>
        </Row>

        <Row title={`原创图标集（${ALL_ICONS.length} 个）`} note="24×24 网格、1.7px 圆头描边，全部为 A-leggo 手绘路径，未复制第三方图标库。悬停查看名称。">
          <div className="grid w-full grid-cols-[repeat(auto-fill,minmax(88px,1fr))] gap-2">
            {ALL_ICONS.map((name) => (
              <span
                key={name}
                className="group flex flex-col items-center gap-1.5 rounded-[10px] border border-transparent px-2 py-3 text-[11px] text-[var(--al-text-muted)] transition-colors hover:border-[var(--al-border-strong)] hover:bg-[var(--al-hover)] hover:text-[var(--al-text)]"
              >
                <Icon name={name} size={19} />
                <span className="truncate font-mono text-[10px]">{name}</span>
              </span>
            ))}
          </div>
        </Row>
      </div>
    </main>
  );
}
