"use client";

import AnimatedGreeting from "@/components/chat/home/AnimatedGreeting";

import { ActionButton } from "@/shared/ui/ActionButton";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  Download,
  FlaskConical,
  GraduationCap,
  HeartHandshake,
  ImagePlus,
  LoaderCircle,
  NotebookPen,
  Paperclip,
  RefreshCw,
  Search,
  Smartphone,
  Sparkles,
  Upload,
  X,
} from "@/shared/ui/glyphs";

import {
  addCompanionMemory,
  confirmCompanionMemory,
  confirmMethodCard,
  createCompanion,
  createMethodCard,
  createStyle,
  deleteCompanionMemory,
  fetchBackup,
  fetchIdentity,
  fetchMethodCards,
  fetchOverview,
  fetchSkills,
  importLegacy,
  registerSkill,
  setSkillEnabled,
} from "./api";
import type {
  AleggoOverview,
  IdentityState,
  MethodCard,
  RegisteredSkill,
} from "./types";
import { browserStorage } from "@/shared/storage";
import { PhonePairDialog } from "./PhonePairDialog";
import { listSessions, type SessionSummary } from "@/lib/session-api";
import { apiFetch, apiUrl } from "@/lib/api";

/**
 * The A-leggo home. One question at the top, one composer under it, the day's
 * numbers on a single quiet row, and the recent conversations below — the V3
 * layout, but each function keeps its own panel behind a hash anchor instead
 * of stacking tabs on the landing view.
 */

type Panel = "" | "today" | "cards" | "identity" | "skills" | "data";

const PANEL_HASHES: Panel[] = ["today", "cards", "identity", "skills", "data"];

const panelLinks: { id: Panel; label: string; icon: typeof Sparkles }[] = [
  { id: "today", label: "今日面板", icon: GraduationCap },
  { id: "cards", label: "方法卡", icon: NotebookPen },
  { id: "identity", label: "回答风格与伙伴", icon: HeartHandshake },
  { id: "skills", label: "技能注册", icon: Sparkles },
  { id: "data", label: "迁移与备份", icon: Download },
];

const modeChips: { capability: string; label: string; icon: typeof Sparkles }[] = [
  { capability: "aleggo_tutor", label: "老师讲题", icon: GraduationCap },
  { capability: "first_error_diagnosis", label: "批改诊断", icon: Search },
  { capability: "ask_questions", label: "引导我做", icon: Sparkles },
  { capability: "deep_question", label: "迁移训练", icon: FlaskConical },
  { capability: "math_research", label: "数学研究", icon: BookOpen },
];

const levelLabel = {
  insufficient: "证据不足",
  weak: "薄弱",
  developing: "形成中",
  mastered: "已掌握",
};

function readPanelFromHash(): Panel {
  if (typeof window === "undefined") return "";
  const hash = window.location.hash.replace(/^#/, "");
  return (PANEL_HASHES as string[]).includes(hash) ? (hash as Panel) : "";
}

function SetupWizard({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const pages = [
    {
      title: "本地学习模式",
      body: "学习记录、方法卡与伙伴记忆默认保存在本机用户工作区。密钥不会进入前端包。",
      link: null,
    },
    {
      title: "选择模型 Provider",
      body: "在模型目录中配置 OpenAI、Anthropic、DeepSeek、Gemini 等兼容端点；凭据由后端安全保存。",
      link: { href: "/settings#models", label: "打开模型设置" },
    },
    {
      title: "可选：加入教材",
      body: "PDF、Office、Markdown、LaTeX、代码和图片会进入正式解析与 RAG 流程，回答可跳回来源。",
      link: { href: "/knowledge-bases", label: "打开知识库" },
    },
    {
      title: "可选：外部 Agent 与 MCP",
      body: "复杂研究才升级到外部 Agent；MCP 能力会先发现、再调用，并保留可见运行记录。",
      link: { href: "/agents", label: "外部 Agent" },
      secondLink: { href: "/space/mcp", label: "MCP 设置" },
    },
  ];
  const current = pages[step];
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[var(--overlay)] p-4" role="dialog" aria-modal="true" aria-labelledby="setup-title">
      <div className="w-full max-w-lg rounded-[28px] border border-[var(--border)] bg-[var(--card)] p-7 shadow-[var(--shadow-raised)]">
        <div className="mb-6 flex items-center justify-between">
          <span className="text-xs tracking-[0.2em] text-[var(--muted-foreground)]">SETUP {step + 1} / {pages.length}</span>
          <ActionButton onClick={onDone} className="rounded-full p-2 hover:bg-[var(--muted)]" aria-label="稍后设置"><X size={17} /></ActionButton>
        </div>
        <h2 id="setup-title" className="font-serif text-3xl">{current.title}</h2>
        <p className="mt-4 font-serif text-[17px] leading-8 text-[var(--muted-foreground)]">{current.body}</p>
        <div className="mt-6 flex flex-wrap gap-2">
          {current.link ? <Link href={current.link.href} className="rounded-full border border-[var(--border)] px-4 py-2 text-sm hover:bg-[var(--muted)]">{current.link.label}</Link> : null}
          {"secondLink" in current && current.secondLink ? <Link href={current.secondLink.href} className="rounded-full border border-[var(--border)] px-4 py-2 text-sm hover:bg-[var(--muted)]">{current.secondLink.label}</Link> : null}
        </div>
        <div className="mt-8 flex justify-between">
          <ActionButton disabled={step === 0} onClick={() => setStep((value) => value - 1)} className="px-3 py-2 text-sm disabled:opacity-30">上一步</ActionButton>
          {step < pages.length - 1 ? (
            <ActionButton onClick={() => setStep((value) => value + 1)} className="rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-5 py-2 text-sm text-[var(--al-accent-contrast)]">继续</ActionButton>
          ) : (
            <ActionButton onClick={onDone} className="rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-5 py-2 text-sm text-[var(--al-accent-contrast)]">完成设置</ActionButton>
          )}
        </div>
      </div>
    </div>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return <p className="rounded-2xl border border-dashed border-[var(--border)] p-5 text-sm text-[var(--muted-foreground)]">{children}</p>;
}

function TodayPanel({ overview }: { overview: AleggoOverview }) {
  return (
    <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
      <section className="rounded-[26px] border border-[var(--border)] bg-[var(--card)] p-6">
        <div className="flex items-center justify-between"><h2 className="font-serif text-2xl">今天该学什么</h2><span className="text-sm text-[var(--muted-foreground)]">{overview.due_review_count} 项到期</span></div>
        <div className="mt-5 space-y-3">
          {overview.due_reviews.length ? overview.due_reviews.map((item) => (
            <Link key={item.concept_id} href={`/chat?capability=aleggo_tutor&prompt=${encodeURIComponent(`请复习知识点：${item.concept_id}`)}`} className="flex items-center justify-between rounded-2xl bg-[var(--muted)] px-4 py-3 transition hover:translate-x-0.5">
              <div><p className="font-medium">{item.concept_id}</p><p className="mt-1 text-xs text-[var(--muted-foreground)]">上次：{item.last_result} · 间隔 {item.interval_days.toFixed(1)} 天</p></div><ChevronRight size={17} />
            </Link>
          )) : <EmptyState>今天没有到期复习。可以从一道真正想弄懂的题开始。</EmptyState>}
        </div>
      </section>
      <section className="rounded-[26px] border border-[var(--border)] bg-[var(--card)] p-6">
        <h2 className="font-serif text-2xl">最近卡点</h2>
        <div className="mt-5 space-y-4">
          {overview.weak_points.length ? overview.weak_points.slice(0, 5).map((item) => (
            <div key={`${item.concept_id}-${item.error_tag}`}>
              <div className="flex justify-between text-sm"><span>{item.concept_id}</span><span className="text-[var(--muted-foreground)]">{item.error_count} 次</span></div>
              <p className="mt-1 text-xs text-[var(--al-accent-text)]">{item.error_tag || "未分类错误"}</p>
            </div>
          )) : <EmptyState>还没有错误证据。系统不会凭聊天印象制造薄弱点。</EmptyState>}
        </div>
      </section>
      <section className="rounded-[26px] border border-[var(--border)] bg-[var(--card)] p-6 lg:col-span-2">
        <h2 className="font-serif text-2xl">掌握地图</h2>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {overview.mastery.length ? overview.mastery.map((item) => (
            <div key={item.concept_id} className="rounded-2xl bg-[var(--muted)] p-4">
              <div className="flex items-center justify-between"><span className="font-medium">{item.concept_id}</span><span className="text-xs">{levelLabel[item.level]}</span></div>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--border)]"><div className="h-full rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] text-[#fdf6e9]" style={{ width: `${Math.round(item.score * 100)}%` }} /></div>
              <p className="mt-2 text-xs text-[var(--muted-foreground)]">{item.evidence_sufficient ? `掌握 ${Math.round(item.score * 100)}% · 置信 ${Math.round(item.confidence * 100)}%` : "证据不足/暂无法判断"}</p>
            </div>
          )) : <EmptyState>完成一次作答、迁移题或主动回忆后，这里才会开始形成地图。</EmptyState>}
        </div>
      </section>
    </div>
  );
}

function MethodCardsPanel({ cards, reload }: { cards: MethodCard[]; reload: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true);
    const data = new FormData(event.currentTarget);
    await createMethodCard({
      problem_type: data.get("problem_type"), key_insight: data.get("key_insight"),
      common_methods: String(data.get("common_methods") || "").split("\n").filter(Boolean),
      key_steps: String(data.get("key_steps") || "").split("\n").filter(Boolean),
      common_errors: String(data.get("common_errors") || "").split("\n").filter(Boolean),
      next_first_reaction: data.get("next_first_reaction"), confirmed: true,
    });
    event.currentTarget.reset(); setOpen(false); await reload(); setBusy(false);
  };
  return (
    <section className="rounded-[26px] border border-[var(--border)] bg-[var(--card)] p-6">
      <div className="flex items-center justify-between"><div><h2 className="font-serif text-2xl">方法卡</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">Agent 只能提出候选；你确认后才进入长期方法库。</p></div><ActionButton onClick={() => setOpen((value) => !value)} className="rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-4 py-2 text-sm text-[var(--al-accent-contrast)]">新建</ActionButton></div>
      {open ? <form onSubmit={submit} className="mt-6 grid gap-3 rounded-2xl bg-[var(--muted)] p-5 sm:grid-cols-2">
        <input required name="problem_type" placeholder="题型名称" className="rounded-xl border border-[var(--border)] bg-[var(--card)] px-3 py-2" />
        <input required name="key_insight" placeholder="本题题眼" className="rounded-xl border border-[var(--border)] bg-[var(--card)] px-3 py-2" />
        <textarea name="common_methods" placeholder="常用方法（每行一项）" className="min-h-24 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3" />
        <textarea name="key_steps" placeholder="关键步骤（每行一项）" className="min-h-24 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3" />
        <textarea name="common_errors" placeholder="常见错误（每行一项）" className="min-h-24 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3" />
        <textarea required name="next_first_reaction" placeholder="下次第一反应" className="min-h-24 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3" />
        <ActionButton disabled={busy} className="rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-4 py-2 text-sm text-[var(--al-accent-contrast)] sm:col-span-2">{busy ? "保存中…" : "确认并保存"}</ActionButton>
      </form> : null}
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        {cards.length ? cards.map((card) => <article key={card.id} className="rounded-2xl border border-[var(--border)] p-5">
          <div className="flex items-start justify-between gap-3"><h3 className="font-serif text-xl">{card.problem_type}</h3><span className={`rounded-full px-2 py-1 text-[11px] ${card.status === "confirmed" ? "bg-[var(--success-surface)] text-[var(--success)]" : "bg-[var(--warning-surface)] text-[var(--warning)]"}`}>{card.status === "confirmed" ? "已确认" : "待确认"}</span></div>
          <dl className="mt-4 space-y-3 text-sm"><div><dt className="text-[var(--muted-foreground)]">本题题眼</dt><dd className="mt-1">{card.key_insight}</dd></div><div><dt className="text-[var(--muted-foreground)]">下次第一反应</dt><dd className="mt-1">{card.next_first_reaction}</dd></div></dl>
          {card.status === "candidate" ? <ActionButton onClick={async () => { await confirmMethodCard(card.id); await reload(); }} className="mt-4 rounded-full border border-[var(--border)] px-3 py-1.5 text-sm"><Check size={14} className="mr-1 inline" />确认保存</ActionButton> : null}
        </article>) : <EmptyState>还没有方法卡。完成题目后可让 Agent 生成候选，再由你确认。</EmptyState>}
      </div>
    </section>
  );
}

function IdentityPanel({ state, reload }: { state: IdentityState; reload: () => Promise<void> }) {
  const [selected, setSelected] = useState("");
  const companionId = selected || state.companions[0]?.id || "";
  return <div className="grid gap-5 lg:grid-cols-2">
    <section className="rounded-[26px] border border-[var(--border)] bg-[var(--card)] p-6"><h2 className="font-serif text-2xl">回答风格</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">风格只决定表达方式，不拥有你的长期记忆。</p>
      <form className="mt-5 space-y-3" onSubmit={async (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); await createStyle({ name: data.get("name"), instructions: data.get("instructions"), active: true }); event.currentTarget.reset(); await reload(); }}><input required name="name" placeholder="风格名称" className="w-full rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2" /><textarea required name="instructions" placeholder="例如：先直觉，再定义，最后给一道最小练习" className="min-h-28 w-full rounded-xl border border-[var(--border)] bg-[var(--background)] p-3" /><ActionButton className="rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-4 py-2 text-sm text-[var(--al-accent-contrast)]">保存并启用</ActionButton></form>
      <div className="mt-5 space-y-2">{state.styles.map((item) => <div key={item.id} className="rounded-xl bg-[var(--muted)] p-3 text-sm"><span className="font-medium">{item.name}</span>{item.active ? <span className="ml-2 text-xs text-[var(--success)]">使用中</span> : null}<p className="mt-1 text-[var(--muted-foreground)]">{item.instructions}</p></div>)}</div>
    </section>
    <section className="rounded-[26px] border border-[var(--border)] bg-[var(--card)] p-6"><h2 className="font-serif text-2xl">长期学习伙伴</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">伙伴决定关系与可控记忆；模型推断必须经你确认。</p>
      <form className="mt-5 grid gap-3" onSubmit={async (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); const made = await createCompanion({ name: data.get("name"), description: data.get("description"), active: true }); setSelected(made.id); event.currentTarget.reset(); await reload(); }}><input required name="name" placeholder="伙伴名字" className="rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2" /><textarea name="description" placeholder="你们如何长期相处" className="min-h-20 rounded-xl border border-[var(--border)] bg-[var(--background)] p-3" /><ActionButton className="rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-4 py-2 text-sm text-[var(--al-accent-contrast)]">创建伙伴</ActionButton></form>
      {state.companions.length ? <select value={companionId} onChange={(event) => setSelected(event.target.value)} className="mt-5 w-full rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2">{state.companions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select> : null}
      {companionId ? <form className="mt-3 flex gap-2" onSubmit={async (event) => { event.preventDefault(); const input = event.currentTarget.elements.namedItem("memory") as HTMLInputElement; await addCompanionMemory({ companion_id: companionId, kind: "explicit_fact", content: input.value, confirmed: true }); input.value = ""; await reload(); }}><input required name="memory" placeholder="明确保存给伙伴的事实" className="min-w-0 flex-1 rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2" /><ActionButton className="rounded-full border border-[var(--border)] px-3">保存</ActionButton></form> : null}
      <div className="mt-4 space-y-2">{state.memories.filter((item) => item.companion_id === companionId).map((item) => <div key={item.id} className="rounded-xl bg-[var(--muted)] p-3 text-sm"><div className="flex items-center justify-between gap-2"><span>{item.content}</span><ActionButton onClick={async () => { await deleteCompanionMemory(item.id); await reload(); }} aria-label="删除记忆"><X size={14} /></ActionButton></div><div className="mt-2 flex items-center gap-2 text-xs text-[var(--muted-foreground)]"><span>{item.kind}</span><span>·</span><span>{item.status === "confirmed" ? "已确认" : "待确认"}</span>{item.status === "pending" ? <ActionButton onClick={async () => { await confirmCompanionMemory(item.id); await reload(); }} className="text-[var(--al-accent-text)]">确认</ActionButton> : null}</div></div>)}</div>
    </section>
  </div>;
}

function SkillsPanel({ skills, reload }: { skills: RegisteredSkill[]; reload: () => Promise<void> }) {
  return <section className="rounded-[26px] border border-[var(--border)] bg-[var(--card)] p-6"><h2 className="font-serif text-2xl">Skill Registry</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">真实 SKILL.md、依赖检查、作用域、版本与运行门禁；关闭后不会进入 Agent manifest。</p>
    <form className="mt-6 grid gap-3 lg:grid-cols-2" onSubmit={async (event) => { event.preventDefault(); const data = new FormData(event.currentTarget); await registerSkill({ name: data.get("name"), description: data.get("description"), instructions: data.get("instructions"), kind: data.get("kind"), scope: data.get("scope") || "global", version: data.get("version") || "1.0.0", enabled: true, requires: {}, entrypoint: "" }); event.currentTarget.reset(); await reload(); }}>
      <input required name="name" pattern="[a-z0-9][a-z0-9-]{0,63}" placeholder="skill-name" className="rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2" /><input required name="description" placeholder="何时使用这个技能" className="rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2" />
      <select name="kind" className="rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2"><option value="prompt">Prompt skill</option><option value="workflow">Workflow skill</option><option value="tool">Tool skill</option><option value="agent">Agent skill</option></select><div className="grid grid-cols-2 gap-2"><input name="scope" defaultValue="global" aria-label="作用域" className="rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2" /><input name="version" defaultValue="1.0.0" aria-label="版本" className="rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2" /></div>
      <textarea required name="instructions" placeholder="完整、可复用的技能定义" className="min-h-32 rounded-xl border border-[var(--border)] bg-[var(--background)] p-3 lg:col-span-2" /><ActionButton className="rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-4 py-2 text-sm text-[var(--al-accent-contrast)] lg:col-span-2">创建并启用</ActionButton>
    </form>
    <div className="mt-6 space-y-3">{skills.length ? skills.map((item) => <div key={item.skill_name} className="flex flex-col gap-3 rounded-2xl border border-[var(--border)] p-4 sm:flex-row sm:items-center sm:justify-between"><div><p className="font-medium">{item.skill_name} <span className="ml-1 rounded-full bg-[var(--muted)] px-2 py-0.5 text-xs">{item.kind}</span></p><p className="mt-1 text-xs text-[var(--muted-foreground)]">v{item.version} · {item.scope} · {item.available ? "依赖就绪" : item.missing_dependencies.join(", ")}</p></div><ActionButton role="switch" aria-checked={item.enabled} onClick={async () => { await setSkillEnabled(item.skill_name, !item.enabled); await reload(); }} className={`rounded-full px-4 py-2 text-sm ${item.enabled ? "bg-[var(--success-surface)] text-[var(--success)]" : "bg-[var(--muted)]"}`}>{item.enabled ? "已启用" : "已关闭"}</ActionButton></div>) : <EmptyState>还没有自定义技能。内置技能会在需要时按权限加载。</EmptyState>}</div>
  </section>;
}

function DataPanel({ refresh }: { refresh: () => Promise<void> }) {
  const fileRef = useRef<HTMLInputElement>(null); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  const handleFile = async (file: File) => { setBusy(true); setMessage(""); try { const raw = JSON.parse(await file.text()) as Record<string, unknown>; const result = await importLegacy(raw, file.name); setMessage(`已迁移：${Object.values(result.counts).reduce((a, b) => a + b, 0)} 项。导入前备份：${result.backup_path}${result.warnings.length ? `；${result.warnings.join("；")}` : ""}`); await refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : "导入失败"); } finally { setBusy(false); } };
  const download = async () => { const data = await fetchBackup(); const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `a-leggo-next-backup-${new Date().toISOString().slice(0, 10)}.json`; anchor.click(); URL.revokeObjectURL(url); };
  return <section className="rounded-[26px] border border-[var(--border)] bg-[var(--card)] p-6"><h2 className="font-serif text-2xl">迁移与备份</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--muted-foreground)]">导入旧 V3 JSON 前，后端会先原样备份。旧模型密钥不会迁移；课程学习对象进入正式持久层，旧提示词技能升级为 SKILL.md。</p><input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void handleFile(file); event.target.value = ""; }} /><div className="mt-6 flex flex-wrap gap-3"><ActionButton disabled={busy} onClick={() => fileRef.current?.click()} className="rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-5 py-2.5 text-sm text-[var(--al-accent-contrast)]"><Upload size={15} className="mr-2 inline" />{busy ? "迁移中…" : "导入 A-leggo V3"}</ActionButton><ActionButton onClick={() => void download()} className="rounded-full border border-[var(--border)] px-5 py-2.5 text-sm"><Download size={15} className="mr-2 inline" />导出 V4 备份</ActionButton></div>{message ? <p role="status" className="mt-5 rounded-2xl bg-[var(--muted)] p-4 text-sm leading-6">{message}</p> : null}</section>;
}

function HeroHome({
  overview,
  sessions,
  identity,
  onConnectPhone,
}: {
  overview: AleggoOverview | null;
  sessions: SessionSummary[];
  identity: IdentityState;
  onConnectPhone: () => void;
}) {
  const router = useRouter();
  const [studyModel, setStudyModel] = useState("study.compass");
  const [atlasMessages, setAtlasMessages] = useState<Array<{ role: "user" | "assistant"; content: string }>>([]);
  const [atlasSessionId, setAtlasSessionId] = useState<string | null>(null);
  const [atlasBusy, setAtlasBusy] = useState(false);
  const [atlasError, setAtlasError] = useState("");
  const [mode, setMode] = useState(modeChips[0].capability);
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const imageRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const activeCompanion = identity.companions.find((item) => item.active) ?? identity.companions[0];
  const activeStyle = identity.styles.find((item) => item.active);
  const todayCount = useMemo(() => {
    if (!overview) return 0;
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    return overview.recent_evidence.filter(
      (item) => new Date(item.occurred_at).getTime() >= start.getTime(),
    ).length;
  }, [overview]);

  const send = async () => {
    const text = prompt.trim();
    if (mode === "math_research") {
      const query = new URLSearchParams();
      if (text) query.set("question", text);
      router.push(`/research${query.size ? `?${query.toString()}` : ""}`);
      return;
    }
    if (studyModel === "study.atlas") {
      if (!text || atlasBusy) return;
      setAtlasBusy(true);
      setAtlasError("");
      try {
        const nextMessages = [...atlasMessages, { role: "user" as const, content: text }];
        const response = await apiFetch(apiUrl("/api/research/model-completion"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ modelId: "study.atlas", sessionId: atlasSessionId, messages: nextMessages }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "Atlas could not answer this request.");
        setAtlasMessages([...nextMessages, { role: "assistant", content: String(payload.text ?? "") }]);
        setAtlasSessionId(typeof payload.session_id === "string" ? payload.session_id : atlasSessionId);
        setPrompt("");
      } catch (reason) {
        setAtlasError(reason instanceof Error ? reason.message : "Atlas could not answer this request.");
      } finally {
        setAtlasBusy(false);
      }
      return;
    }
    const query = new URLSearchParams({ capability: mode });
    if (text) query.set("prompt", text);
    query.set("study_model", studyModel);
    router.push(`/chat?${query.toString()}`);
  };

  const stats = [
    { label: "知识点", value: overview ? overview.mastery.length : "—" },
    { label: "方法卡", value: overview ? overview.method_card_count : "—" },
    { label: "待复习", value: overview ? overview.due_review_count : "—" },
    { label: "今日已学", value: overview ? todayCount : "—" },
  ];

  return (
    <div className="al-study-home">
      {/* Hero — one question, nothing competing with it. */}
      <header className="al-study-home-intro">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[var(--al-accent-text)]">
          学习册 / WORKSHEET
        </p>
        <AnimatedGreeting className="mt-5 leading-tight" />
        <p className="mx-auto mt-4 max-w-xl text-[14.5px] leading-7 text-[var(--muted-foreground)]">
          把问题写下来，我们一步一步来。
        </p>
      </header>

      {/* Intent chips — plain-Chinese study intents; the internal capability
          IDs stay in the URL, never on screen. */}
      <div className="al-study-intents" role="radiogroup" aria-label="学习意图">
        {modeChips.map((chip, index) => {
          const activeChip = mode === chip.capability;
          return (
            <ActionButton
              key={chip.capability}
              type="button"
              role="radio"
              aria-checked={activeChip}
              onClick={() => setMode(chip.capability)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-4 py-2 text-[13px] font-semibold transition-colors duration-150 active:scale-[0.98] ${
                activeChip
                  ? "border-[var(--al-accent-border)] bg-[var(--al-accent-soft)] text-[var(--al-accent-text)]"
                  : "border-[var(--al-border-strong)] bg-[var(--al-surface)] text-[var(--muted-foreground)] hover:bg-[var(--al-hover)] hover:text-[var(--foreground)]"
              }`}
            >
              <span className="al-study-intent-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><chip.icon size={14} strokeWidth={1.8} />
              {chip.label}
            </ActionButton>
          );
        })}
      </div>

      {/* Composer card. */}
      <div className="al-study-question"><label className="al-study-section-label" htmlFor="al-study-prompt"><span>题目与思路</span><span>Enter 发送 · Shift+Enter 换行</span></label>
        <textarea
          id="al-study-prompt"
          aria-label="题目与思路"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
          rows={6}
          placeholder="把题目贴进来，或描述你卡住的地方…（Enter 发送，Shift+Enter 换行）"
          className="w-full resize-none bg-transparent px-3 pt-2 text-[14.5px] leading-6 outline-none placeholder:text-[var(--muted-foreground)]/50"
        />
        {attachments.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 px-3 pb-1">
            {attachments.map((name) => (
              <span key={name} className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)] px-2.5 py-1 text-[11.5px] text-[var(--muted-foreground)]">
                <Paperclip size={11} /> {name}
                <ActionButton type="button" aria-label={`移除 ${name}`} onClick={() => setAttachments((list) => list.filter((item) => item !== name))}>
                  <X size={11} />
                </ActionButton>
              </span>
            ))}
            <span className="self-center text-[11px] text-[var(--muted-foreground)]/70">进入对话后可直接上传这些文件</span>
          </div>
        ) : null}
        <div className="al-study-question-tools">
          <input ref={imageRef} type="file" accept="image/*" multiple className="hidden" onChange={(event) => { const names = Array.from(event.target.files ?? []).map((file) => file.name); setAttachments((list) => [...list, ...names]); event.target.value = ""; }} />
          <input ref={fileRef} type="file" multiple className="hidden" onChange={(event) => { const names = Array.from(event.target.files ?? []).map((file) => file.name); setAttachments((list) => [...list, ...names]); event.target.value = ""; }} />
          <ActionButton type="button" onClick={() => imageRef.current?.click()} className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]">
            <ImagePlus size={15} /> 图片
          </ActionButton>
          <ActionButton type="button" onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]">
            <Paperclip size={15} /> 文件
          </ActionButton>
          <label className="ml-1 inline-flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
            学习模型
            <select value={studyModel} onChange={(event) => setStudyModel(event.target.value)} className="max-w-[190px] rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-[11px] text-[var(--foreground)]">
              <option value="study.quill">Quill · 快速学习</option>
              <option value="study.compass">Compass · 均衡讲解</option>
              <option value="study.atlas">Atlas · 学习智能体</option>
            </select>
          </label>
          <span className="ml-1 hidden items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]/80 sm:inline-flex">
            {activeStyle ? `风格：${activeStyle.name}` : "无风格"} · {activeCompanion ? `伙伴：${activeCompanion.name}` : "无伙伴"}
          </span>
          <ActionButton
            type="button"
            onClick={send}
            disabled={atlasBusy}
            className="ml-auto inline-flex items-center gap-1.5 rounded-[11px] border bg-[var(--al-accent)] shadow-[var(--al-shadow-btn)] hover:bg-[var(--al-accent-strong)] active:scale-[0.985] px-5 py-2 text-[13px] font-medium text-[var(--al-accent-contrast)] transition-opacity hover:opacity-90"
          >
            {atlasBusy ? "Atlas 思考中…" : mode === "math_research" ? "拟定研究计划" : studyModel === "study.atlas" ? "Ask Atlas" : "开始学习"} <ArrowRight size={14} />
          </ActionButton>
        </div>
      </div>
      {studyModel === "study.atlas" ? (
        <section className="al-study-atlas rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4" aria-label="Atlas conversation">
          <div className="mb-3 flex items-center justify-between"><h2 className="font-serif text-lg">A-leggo Atlas · Study Agent</h2><ActionButton type="button" className="text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]" onClick={() => { setAtlasMessages([]); setAtlasSessionId(null); setAtlasError(""); }}>Clear conversation</ActionButton></div>
          {atlasError ? <p className="mb-3 rounded-xl bg-[var(--destructive)]/10 p-3 text-sm text-[var(--destructive)]" role="alert">{atlasError}</p> : null}
          <div className="max-h-72 space-y-3 overflow-y-auto">
            {atlasMessages.map((item, index) => <article key={`${item.role}-${index}`} className={item.role === "assistant" ? "rounded-xl bg-[var(--muted)] p-3 text-sm leading-6" : "ml-8 rounded-xl bg-[var(--al-accent-soft)] p-3 text-sm leading-6"}><strong className="mb-1 block text-[11px] uppercase tracking-wide text-[var(--muted-foreground)]">{item.role === "assistant" ? "Atlas" : "You"}</strong><p className="whitespace-pre-wrap">{item.content}</p></article>)}
            {!atlasMessages.length ? <p className="text-sm text-[var(--muted-foreground)]">Ask Atlas for a guided explanation. This Study Agent uses the configured Bailian application.</p> : null}
          </div>
        </section>
      ) : null}

      {/* Stats — one quiet row, hairline separated. */}
      <section className="al-study-progress" aria-label="学习概览"><h2 className="al-study-section-label"><span>学习概览</span><span>按证据记录</span></h2><div className="al-study-stat-grid">
        {stats.map((item) => (
          <div key={item.label} className="flex-1 px-4 py-3.5 text-center">
            <p className="font-serif text-[22px] leading-none tabular-nums">{item.value}</p>
            <p className="mt-1.5 text-[11.5px] tracking-wide text-[var(--muted-foreground)]">{item.label}</p>
          </div>
        ))}
      </div></section>

      {/* Panels live behind anchors — the landing view stays a landing view. */}
      <details className="al-study-ledger-links"><summary>学习管理</summary><nav aria-label="学习管理"><h2 className="al-study-section-label"><span>资料与记录</span></h2>
        {panelLinks.map((item) => (
          <a
            key={item.id}
            href={`/aleggo#${item.id}`}
            className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <item.icon size={13.5} /> {item.label}
          </a>
        ))}
        <ActionButton
          onClick={onConnectPhone}
          className="inline-flex items-center gap-1.5 rounded-full border border-[color-mix(in_srgb,var(--primary)_45%,transparent)] bg-[var(--al-accent-soft)] px-3 py-1.5 text-[12.5px] font-semibold text-[var(--al-accent-text)] transition hover:brightness-[1.04]"
        >
          <Smartphone size={13.5} /> 连接手机
        </ActionButton>
      </nav></details>

      {/* Continue where you left off. */}
      <section className="al-study-recent">
        <div className="flex items-baseline justify-between">
          <h2 className="font-serif text-xl">继续之前的对话</h2>
          <Link href="/space/chat-history" className="text-[12.5px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--al-accent-text)]">
            全部记录 →
          </Link>
        </div>
        <div className="mt-4 space-y-2">
          {sessions.length ? (
            sessions.slice(0, 6).map((session) => (
              <Link
                key={session.session_id}
                href={`/chat/${session.session_id}`}
                className="flex items-center justify-between gap-3 rounded-2xl border border-[var(--border)] bg-[var(--card)] px-4 py-3 transition hover:-translate-y-px hover:shadow-sm"
              >
                <div className="min-w-0">
                  <p className="truncate text-[13.5px] font-medium">{session.title || "未命名对话"}</p>
                  <p className="mt-0.5 text-[11.5px] text-[var(--muted-foreground)]">
                    {session.updated_at ? new Date(session.updated_at * 1000).toLocaleString() : ""}
                  </p>
                </div>
                <ChevronRight size={16} className="shrink-0 text-[var(--muted-foreground)]" />
              </Link>
            ))
          ) : (
            <EmptyState>还没有对话。从上面选一个模式，把今天的题带进来。</EmptyState>
          )}
        </div>
      </section>
    </div>
  );
}

export default function AleggoWorkbench() {
  const [panel, setPanel] = useState<Panel>("");
  const [overview, setOverview] = useState<AleggoOverview | null>(null);
  const [cards, setCards] = useState<MethodCard[]>([]);
  const [identity, setIdentity] = useState<IdentityState>({ styles: [], companions: [], memories: [] });
  const [skills, setSkills] = useState<RegisteredSkill[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [showSetup, setShowSetup] = useState(false);

  const reload = useCallback(async () => {
    setError("");
    try {
      const [nextOverview, nextCards, nextIdentity, nextSkills, nextSessions] = await Promise.all([
        fetchOverview(),
        fetchMethodCards(),
        fetchIdentity(),
        fetchSkills(),
        listSessions(8).catch(() => [] as SessionSummary[]),
      ]);
      setOverview(nextOverview);
      setCards(nextCards);
      setIdentity(nextIdentity);
      setSkills(nextSkills);
      setSessions(nextSessions);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取学习数据");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setShowSetup(browserStorage.readRaw("local", "aleggo-next.setup-complete") !== "1");
    void reload();
  }, [reload]);

  // The sidebar links to `/aleggo#cards` and friends; follow the hash so the
  // panels stay reachable without a tab bar on the landing view.
  useEffect(() => {
    const sync = () => setPanel(readPanelFromHash());
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const [showPair, setShowPair] = useState(false);

  const closeSetup = () => {
    browserStorage.writeRaw("local", "aleggo-next.setup-complete", "1");
    setShowSetup(false);
  };

  return (
    <main className="h-full overflow-y-auto bg-[var(--background)]">
      <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-8 lg:py-12">
        {showSetup ? <SetupWizard onDone={closeSetup} /> : null}
        <PhonePairDialog open={showPair} onClose={() => setShowPair(false)} />
        {panel ? (
          <>
            <div className="mb-5 flex items-center justify-between">
              <a href="/aleggo" className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] px-4 py-2 text-[13px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]">
                ← 回到首页
              </a>
              <div className="flex items-center gap-1">
                {panelLinks.map((item) => (
                  <a
                    key={item.id}
                    href={`/aleggo#${item.id}`}
                    className={`whitespace-nowrap rounded-full px-3.5 py-2 text-[13px] transition ${
                      panel === item.id
                        ? "bg-[var(--accent)] text-[var(--foreground)]"
                        : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                    }`}
                  >
                    {item.label}
                  </a>
                ))}
                <ActionButton onClick={() => void reload()} className="ml-1 rounded-full p-2 text-[var(--muted-foreground)]" aria-label="刷新">
                  <RefreshCw size={16} />
                </ActionButton>
              </div>
            </div>
            {error ? <div role="alert" className="mb-5 rounded-2xl bg-[var(--destructive)]/10 p-4 text-sm text-[var(--destructive)]">{error}</div> : null}
            {loading || !overview ? (
              <div className="grid min-h-48 place-items-center"><LoaderCircle className="animate-spin text-[var(--al-accent-text)]" /></div>
            ) : panel === "today" ? (
              <TodayPanel overview={overview} />
            ) : panel === "cards" ? (
              <MethodCardsPanel cards={cards} reload={reload} />
            ) : panel === "identity" ? (
              <IdentityPanel state={identity} reload={reload} />
            ) : panel === "skills" ? (
              <SkillsPanel skills={skills} reload={reload} />
            ) : (
              <DataPanel refresh={reload} />
            )}
          </>
        ) : (
          <>
            {error ? <div role="alert" className="mx-auto mb-5 max-w-3xl rounded-2xl bg-[var(--destructive)]/10 p-4 text-sm text-[var(--destructive)]">{error}</div> : null}
            {loading ? (
              <div className="grid min-h-64 place-items-center"><LoaderCircle className="animate-spin text-[var(--al-accent-text)]" /></div>
            ) : (
              <HeroHome overview={overview} sessions={sessions} identity={identity} onConnectPhone={() => setShowPair(true)} />
            )}
          </>
        )}
      </div>
    </main>
  );
}
