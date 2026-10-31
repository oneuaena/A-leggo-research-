"use client";

import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { ActionButton } from "@/shared/ui/ActionButton";
import { ArrowUp, ChevronRight, CircleAlert, FileText, LoaderCircle, Paperclip, Terminal } from "@/shared/ui/glyphs";
import MathText from "./MathText";
import ProviderModelSelector from "./ProviderModelSelector";
import ResearchModeSelector from "./ResearchModeSelector";
import { statusText } from "./research-presentation";
import { hasSubstantialEnglish, translateMathFields, translateMathText } from "./research-translation";
import {
  addResearchIntervention, cancelResearchDiscussion, decideResearchRecommendation, getResearchThread,
  type ResearchPlan, type ResearchProject, type ResearchThreadItem, type ResearchThreadLive, type ResearchThreadPage,
} from "@/lib/research-api";
import {
  INTERVENTION_LABELS, STICK_TO_BOTTOM_PX, queueLabelFor,
  formatElapsed, groupThreadItems, mergeThreadItems, stabilizeRows,
  type ThreadRow,
} from "@/lib/research-thread";

/*
 * Codex-style research conversation.
 *
 * - Researcher messages, answers and real tool calls form one thread; the
 *   composer stays available while research is running so the researcher can
 *   interject, add conditions or change direction.
 * - Real tool calls render light and collapsed by default; answers use heavier
 *   sans-serif body text.
 * - Long sessions load on demand (newest page first, older pages when scrolled
 *   to the top) and polling only fetches rows newer than the last cursor.
 *   Rows are memoized and reused across merges so old messages never re-render.
 */

type ToolItem = Extract<ResearchThreadItem, { kind: "tool" }>;

const PAGE_SIZE = 40;
const POLL_MS = 3000;
const LIVE_STATUSES = ["RUNNING", "VERIFYING", "PLANNING", "REPLANNING"];
const COMPOSER_LOCKED = ["DRAFT", "PLANNING"];

const ROLE_LABELS: Record<string, string> = {
  "research-planner": "规划", explorer: "探索", "proof-builder": "证明构建",
  "experimental-mathematician": "实验与反例", "research-skeptic": "质疑", "independent-verifier": "复核",
  "research-synthesizer": "综合", "researcher-intervention": "你的请求", "literature-intelligence": "文献", "math-tool": "工具",
  "research-collaborator": "研究伙伴",
};

function sameRecord(a: Record<string, string>, b: Record<string, string>) {
  const keys = Object.keys(b);
  return keys.length === Object.keys(a).length && keys.every((key) => a[key] === b[key]);
}

function sameLive(a: ResearchThreadLive | null, b: ResearchThreadLive | null) {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.action === b.action && a.tool === b.tool && a.agent === b.agent && a.phase === b.phase && a.stepIndex === b.stepIndex && a.stepTotal === b.stepTotal;
}


const ToolCall = memo(function ToolCall({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState(false);
  const meta = [formatElapsed(item.elapsedMs), item.ok ? "成功" : "未成功"].filter(Boolean).join(" · ");
  return (
    <details className="research-tool-call" data-ok={item.ok ? "true" : "false"} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary><Terminal size={13} aria-hidden="true" /><span>运行了</span><span className="research-tool-name">{item.tool}</span><span className="research-tool-meta">{meta}</span><ChevronRight size={11} className="research-thread-chevron" aria-hidden="true" /></summary>
      {open ? (
        <div className="research-tool-body">
          {item.input ? <><div className="research-tool-label">输入</div><pre>{item.input}</pre></> : null}
          <div className="research-tool-label">输出</div>
          <pre>{item.output || "（无输出）"}</pre>
          {item.outputTruncated || item.inputTruncated ? <small>内容过长已截断，完整记录在导出的研究包中。</small> : null}
        </div>
      ) : null}
    </details>
  );
});

const ToolGroup = memo(function ToolGroup({ items }: { items: ToolItem[] }) {
  return (
    <div className="research-tool-sequence">{items.map((item) => <ToolCall key={item.id} item={item} />)}</div>
  );
});

const UserMessage = memo(function UserMessage({ item, queueStatus, onCancel }: { item: Extract<ResearchThreadItem, { kind: "user" }>; queueStatus: string; onCancel: (id: string) => void }) {
  const label = item.intervention === "human_note" ? "" : INTERVENTION_LABELS[item.intervention] ?? "研究请求";
  return (
    <div className="research-msg-user-row">
      <div className="research-msg-user">
        {label ? <span className="research-msg-chip">{label}</span> : null}
        <MathText text={item.text} />
        {queueLabelFor(item.intervention, queueStatus) ? <small data-state={queueStatus}>{queueLabelFor(item.intervention, queueStatus)}</small> : null}
        {item.intervention === "discuss" && item.queueId && queueStatus === "RUNNING" ? <button type="button" onClick={() => onCancel(item.queueId)}>停止本次讨论</button> : null}
      </div>
    </div>
  );
});

const AnswerMessage = memo(function AnswerMessage({ item, getFull }: { item: Extract<ResearchThreadItem, { kind: "answer" }>; getFull: (artifactId: string) => string | undefined }) {
  const [expanded, setExpanded] = useState(false);
  const [full, setFull] = useState<string | null>(null);
  const [translated, setTranslated] = useState<string | null>(null);
  const [showZh, setShowZh] = useState(false);
  const [translating, setTranslating] = useState(false);
  const long = item.truncated || item.text.length > 1400;
  const sourceText = expanded && full !== null ? full : item.text;
  const hasEnglish = hasSubstantialEnglish(sourceText);
  const toggle = () => {
    if (!expanded && item.truncated && full === null) setFull(getFull(item.artifactId) ?? item.text);
    setExpanded((value) => !value);
  };
  const handleTranslate = async () => {
    if (translated) {
      setShowZh((prev) => !prev);
      return;
    }
    setTranslating(true);
    try {
      const res = await translateMathText(sourceText);
      setTranslated(res);
      setShowZh(true);
    } finally {
      setTranslating(false);
    }
  };
  return (
    <article className="research-msg-answer" data-clamped={long && !expanded ? "true" : "false"}>
      <div className="research-msg-meta">
        <span>{[ROLE_LABELS[item.role] ?? "研究伙伴", item.title].filter(Boolean).join(" · ")}</span>
        {hasEnglish ? (
          <button
            type="button"
            className="research-translate-btn"
            disabled={translating}
            onClick={() => void handleTranslate()}
          >
            {translating ? "翻译中…" : translated ? (showZh ? "查看原文" : "查看中文") : "翻译成中文"}
          </button>
        ) : null}
      </div>
      <div className="research-msg-body"><MathText text={showZh && translated ? translated : sourceText} showRaw /></div>
      {long ? <button type="button" className="research-thread-link" onClick={toggle}>{expanded ? "收起" : "展开全文"}</button> : null}
    </article>
  );
});

const PROCESS_KINDS: Record<string, string> = {
  "agent-output": "研究过程", "step-output": "步骤输出", "step-report": "步骤记录", "milestone-report": "阶段记录",
};

function processLabel(item: Extract<ResearchThreadItem, { kind: "answer" }>, artifactKind: string): string {
  if (PROCESS_KINDS[artifactKind]) return PROCESS_KINDS[artifactKind];
  if (item.role === "research-step-report") return "步骤记录";
  if (item.role === "milestone-controller") return "阶段记录";
  if (item.title.startsWith("Step output:")) return "步骤输出";
  return "";
}

const ProcessMessage = memo(function ProcessMessage({ item, label, getFull }: {
  item: Extract<ResearchThreadItem, { kind: "answer" }>;
  label: string;
  getFull: (artifactId: string) => string | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [translated, setTranslated] = useState<{ title: string; body: string } | null>(null);
  const [showZh, setShowZh] = useState(false);
  const [translating, setTranslating] = useState(false);
  const full = open && item.truncated ? getFull(item.artifactId) : undefined;
  const sourceBody = full ?? item.text;
  const hasEnglish = hasSubstantialEnglish(sourceBody) || hasSubstantialEnglish(item.title);
  const handleTranslate = async () => {
    if (translated) {
      setShowZh((prev) => !prev);
      return;
    }
    setTranslating(true);
    try {
      const res = await translateMathFields(
        { title: item.title || "", body: sourceBody },
        `proc:${item.id}`,
      );
      setTranslated(res);
      setShowZh(true);
    } finally {
      setTranslating(false);
    }
  };
  const displayTitle = showZh && translated?.title ? translated.title : item.title;
  const displayBody = showZh && translated?.body ? translated.body : sourceBody;
  return (
    <details className="research-process-entry" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary title={displayTitle}>
        <FileText size={13} aria-hidden="true" />
        <span className="research-process-label">{label}</span>
        {displayTitle ? <span className="research-process-title">{displayTitle}</span> : null}
        <ChevronRight size={11} className="research-thread-chevron" aria-hidden="true" />
      </summary>
      {open ? <div className="research-process-body">
        {hasEnglish ? (
          <div className="research-step-translate-bar">
            <button
              type="button"
              className="research-translate-btn"
              disabled={translating}
              onClick={() => void handleTranslate()}
            >
              {translating ? "翻译中…" : translated ? (showZh ? "查看原文" : "查看中文") : "翻译成中文"}
            </button>
          </div>
        ) : null}
        <MathText text={displayBody} showRaw />
        {item.truncated && full === undefined ? <small>当前显示部分内容，完整记录可在研究成果中查看或导出。</small> : null}
      </div> : null}
    </details>
  );
});

const EventLine = memo(function EventLine({ item, failedAttempt }: { item: Extract<ResearchThreadItem, { kind: "event" }>; failedAttempt?: string }) {
  const labels: Record<string, string> = { step_started: "开始步骤", step_completed: "完成步骤", research_review_required: "等待你审阅阶段成果", research_started: "研究已开始", plan_approved: "计划已批准" };
  const rejectedRoute = item.eventType === "step_failed" && item.summary.includes("REJECTED_ROUTE");
  const message = rejectedRoute ? "记录了路线审查信息" : (labels[item.eventType] ?? (item.summary.replace(/\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b/g, statusText) || "研究记录已更新"));
  return <div className="research-thread-event" data-tone={rejectedRoute ? "info" : item.tone}><span aria-hidden="true" /><div><MathText text={message} />
    {rejectedRoute && failedAttempt ? <details className="research-attempt-detail"><summary>查看路线记录</summary><MathText text={failedAttempt.replace(/\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b/g, statusText)} />
      <details className="research-raw-data"><summary>查看原始数据</summary><pre>{failedAttempt}</pre></details></details> : null}</div></div>;
});

const RowView = memo(function RowView({ row, queueStatus, artifactKind, getFull, getFailed, onCancel }: { row: ThreadRow; queueStatus: string; artifactKind: string; getFull: (artifactId: string) => string | undefined; getFailed: (stepId: string) => string | undefined; onCancel: (id: string) => void }) {
  let content: ReactNode;
  if (row.type === "tools") content = <ToolGroup items={row.items} />;
  else if (row.item.kind === "user") content = <UserMessage item={row.item} queueStatus={queueStatus} onCancel={onCancel} />;
  else if (row.item.kind === "answer") {
    const label = processLabel(row.item, artifactKind);
    content = label ? <ProcessMessage item={row.item} label={label} getFull={getFull} /> : <AnswerMessage item={row.item} getFull={getFull} />;
  }
  else if (row.item.kind === "event") content = <EventLine item={row.item} failedAttempt={getFailed(row.item.stepId || "")} />;
  else content = null;
  return <div className="research-thread-row">{content}</div>;
});

function ProcessStepBlock({
  id,
  label,
  stepTitle,
  stepDescription,
  children,
}: {
  id: string;
  label: string;
  stepTitle?: string;
  stepDescription?: string;
  children: ReactNode;
}) {
  const [translated, setTranslated] = useState<{ title: string; description: string } | null>(null);
  const [showZh, setShowZh] = useState(false);
  const [translating, setTranslating] = useState(false);
  const hasEnglish = hasSubstantialEnglish(stepTitle) || hasSubstantialEnglish(stepDescription);

  const handleTranslate = async () => {
    if (translated) {
      setShowZh((prev) => !prev);
      return;
    }
    setTranslating(true);
    try {
      const res = await translateMathFields(
        { title: stepTitle || "", description: stepDescription || "" },
        `step-block:${id}`,
      );
      setTranslated(res);
      setShowZh(true);
    } finally {
      setTranslating(false);
    }
  };

  const displayTitle = showZh && translated?.title ? translated.title : stepTitle;
  const displayDesc = showZh && translated?.description ? translated.description : stepDescription;

  return (
    <details className="research-process-step" data-process-step={id}>
      <summary>
        <span>{label}</span>
        {displayTitle ? (
          <span className="research-process-step-title">
            {" · "}
            <MathText text={displayTitle} />
          </span>
        ) : null}
      </summary>
      {hasEnglish ? (
        <div className="research-step-translate-bar">
          <button
            type="button"
            className="research-translate-btn"
            disabled={translating}
            onClick={() => void handleTranslate()}
          >
            {translating ? "翻译中…" : translated ? (showZh ? "查看原文" : "查看中文") : "翻译成中文"}
          </button>
          {showZh && displayDesc ? (
            <div className="research-step-translated-desc">
              <MathText text={displayDesc} />
            </div>
          ) : null}
        </div>
      ) : null}
      {children}
    </details>
  );
}

export default function ResearchThread({
  project, footer, dock, compactProcess = false, hideOrigin = false, externalScrollRef, focusRequest, busy = false, onProposedPlan, onChanged, onAttach,
}: {
  project: ResearchProject;
  /** Pending approvals and missing-material requests render in the flow, below the latest message. */
  footer?: ReactNode;
  dock?: ReactNode;
  compactProcess?: boolean;
  hideOrigin?: boolean;
  externalScrollRef?: RefObject<HTMLElement | null>;
  focusRequest?: { stepId: string; sequence: number } | null;
  busy?: boolean;
  onProposedPlan: (plan: ResearchPlan) => void;
  onChanged: () => void;
  onAttach: (files: File[]) => Promise<void> | void;
}) {
  const projectId = project.id;
  const [items, setItems] = useState<ResearchThreadItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [queue, setQueue] = useState<Record<string, string>>({});
  const [live, setLive] = useState<ResearchThreadLive | null>(null);
  const [loadError, setLoadError] = useState("");
  const [draft, setDraft] = useState("");
  const [inputMode, setInputMode] = useState("discuss");
  const [requestKind] = useState("request_verification");
  const [slashOpen, setSlashOpen] = useState(false);
  const [targetBranch, setTargetBranch] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [showFullResult, setShowFullResult] = useState(false);

  const innerScrollRef = useRef<HTMLDivElement>(null);
  const scrollRef = externalScrollRef ?? innerScrollRef;
  const topRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const olderCursor = useRef("");
  const newestCursor = useRef("");
  const generation = useRef(0);
  const initialized = useRef(false);
  const inflight = useRef(false);
  const loadingOlderRef = useRef(false);
  const hasMoreRef = useRef(false);
  const stick = useRef(!externalScrollRef);
  const anchor = useRef<number | null>(null);
  const artifactsRef = useRef(project.artifacts);
  const previousRows = useRef(new Map<string, ThreadRow>());
  const threadRef = useRef<HTMLElement>(null);
  const handledFocus = useRef(0);

  useEffect(() => { artifactsRef.current = project.artifacts; }, [project.artifacts]);
  useEffect(() => { hasMoreRef.current = hasMore; }, [hasMore]);

  const getFull = useCallback((artifactId: string) => artifactsRef.current?.find((artifact) => artifact.id === artifactId)?.content, []);
  const getFailed = useCallback((stepId: string) => stepId ? artifactsRef.current?.find(artifact => artifact.stepId === stepId && artifact.status.toLowerCase() === "failed")?.content : undefined, []);

  const ingestFirst = useCallback((page: ResearchThreadPage) => {
    olderCursor.current = page.nextCursor;
    newestCursor.current = page.newestCursor;
    initialized.current = true;
    setItems((current) => mergeThreadItems(current, page.items));
    setHasMore(page.hasMore);
    setQueue((previous) => (sameRecord(previous, page.queue) ? previous : page.queue));
    setLive((previous) => (sameLive(previous, page.live) ? previous : page.live));
    setLoadError("");
    setLoaded(true);
  }, []);

  const loadInitial = useCallback(async () => {
    const run = ++generation.current;
    try {
      const page = await getResearchThread(projectId, { limit: PAGE_SIZE });
      if (run !== generation.current) return;
      stick.current = !externalScrollRef;
      ingestFirst(page);
    } catch (cause) {
      if (run !== generation.current) return;
      setLoadError(cause instanceof Error ? cause.message : "对话记录暂时无法加载。");
      setLoaded(true);
    }
  }, [externalScrollRef, ingestFirst, projectId]);

  // Fetch only rows newer than the last cursor; an unchanged answer never touches state.
  const pollNew = useCallback(async () => {
    if (!initialized.current || inflight.current) return;
    inflight.current = true;
    const run = generation.current;
    try {
      const page = newestCursor.current
        ? await getResearchThread(projectId, { after: newestCursor.current })
        : await getResearchThread(projectId, { limit: PAGE_SIZE });
      if (run !== generation.current) return;
      if (!newestCursor.current) {
        if (page.items.length) ingestFirst(page);
      } else if (page.items.length) {
        newestCursor.current = page.newestCursor || newestCursor.current;
        setItems((current) => mergeThreadItems(current, page.items));
      }
      setQueue((previous) => (sameRecord(previous, page.queue) ? previous : page.queue));
      setLive((previous) => (sameLive(previous, page.live) ? previous : page.live));
    } catch {
      /* transient; the next tick retries */
    } finally {
      inflight.current = false;
    }
  }, [ingestFirst, projectId]);

  const cancelDiscussion = useCallback(async (queueId: string) => {
    try {
      await cancelResearchDiscussion(projectId, queueId);
      await pollNew();
      onChanged();
    } catch (cause) {
      setSendError(cause instanceof Error ? cause.message : "停止讨论失败");
    }
  }, [projectId, pollNew, onChanged]);

  const loadOlder = useCallback(async () => {
    if (loadingOlderRef.current || !olderCursor.current || !hasMoreRef.current) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    const run = generation.current;
    const el = scrollRef.current;
    anchor.current = el ? el.scrollHeight - el.scrollTop : null; // distance from the bottom stays fixed
    try {
      const page = await getResearchThread(projectId, { limit: PAGE_SIZE, before: olderCursor.current });
      if (run !== generation.current) return;
      olderCursor.current = page.nextCursor;
      setHasMore(page.hasMore);
      setItems((current) => mergeThreadItems(current, page.items));
    } catch (cause) {
      anchor.current = null;
      setLoadError(cause instanceof Error ? cause.message : "更早的记录暂时无法加载。");
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [projectId, scrollRef]);

  useEffect(() => {
    generation.current += 1;
    initialized.current = false;
    inflight.current = false;
    olderCursor.current = "";
    newestCursor.current = "";
    previousRows.current = new Map();
    stick.current = !externalScrollRef;
    setItems([]); setHasMore(false); setLoaded(false); setQueue({}); setLive(null); setLoadError("");
    setDraft(""); setSendError("");
    if (project.status === "DRAFT" && !project.plan) { setLoaded(true); return; }
    void loadInitial();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the project itself changes
  }, [projectId, loadInitial]);

  const queueBusy = Object.values(queue).some((state) => state === "QUEUED" || state === "RUNNING");
  const polling = LIVE_STATUSES.includes(project.status) || queueBusy;
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => { if (document.visibilityState !== "hidden") void pollNew(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [polling, pollNew]);

  // Any state change in the project (approval, pause, completion, new artifacts) refreshes once.
  useEffect(() => { void pollNew(); }, [pollNew, project.status, project.updatedAt, project.artifacts?.length]);

  useEffect(() => {
    const top = topRef.current;
    if (!hasMore || !top || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) void loadOlder(); }, { root: scrollRef.current, rootMargin: "160px 0px 0px 0px" });
    observer.observe(top);
    return () => observer.disconnect();
  }, [hasMore, loadOlder, items.length, scrollRef]);

  const rows = useMemo(() => stabilizeRows(previousRows.current, groupThreadItems(items)), [items]);
  const artifactKinds = useMemo(() => new Map((project.artifacts ?? []).map((artifact) => [artifact.id, String(artifact.metadata?.artifactKind ?? "")])), [project.artifacts]);
  const featuredResult = useMemo(() => {
    const items = (project.artifacts || []).filter((artifact) => ["step-output", "agent-output"].includes(String(artifact.metadata?.artifactKind || "")) && artifact.content.trim() && !["failed", "invalid", "rejected"].includes(artifact.status.toLowerCase()));
    return [...items].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))[0];
  }, [project.artifacts]);
  const resultExcerpt = featuredResult?.content.split(/\n\s*\n/).filter((part) => !/^\s*#{1,4}\s/.test(part)).slice(0, 1).join("\n\n") || "";
  const processSegments = useMemo(() => {
    const result: { kind: "process" | "row"; key: string; rows: ThreadRow[] }[] = [];
    for (const row of rows) {
      const process = row.type === "tools" || row.type === "item" && (row.item.kind === "event" && (row.item.eventType === "research_review_required" || row.item.eventType === "step_failed" && row.item.summary.includes("REJECTED_ROUTE") || !["warning", "danger"].includes(row.item.tone)) || row.item.kind === "answer" && Boolean(processLabel(row.item, artifactKinds.get(row.item.artifactId) ?? "")));
      const last = result[result.length - 1];
      if (process && last?.kind === "process") last.rows.push(row);
      else result.push({ kind: process ? "process" : "row", key: row.key, rows: [row] });
    }
    return result;
  }, [artifactKinds, rows]);
  useEffect(() => { previousRows.current = new Map(rows.map((row) => [row.key, row])); }, [rows]);

  useEffect(() => {
    if (!focusRequest || handledFocus.current === focusRequest.sequence || !loaded) return;
    const step = [...(threadRef.current?.querySelectorAll<HTMLDetailsElement>("details[data-process-step]") ?? [])]
      .find(element => element.dataset.processStep === focusRequest.stepId);
    if (step) {
      const group = step.closest<HTMLDetailsElement>("details.research-process-group");
      if (group) group.open = true;
      step.open = true;
      handledFocus.current = focusRequest.sequence;
      requestAnimationFrame(() => (step.querySelector(":scope > summary") ?? step).scrollIntoView({ block: "center", behavior: "smooth" }));
    } else if (hasMore && !loadingOlder) {
      void loadOlder();
    }
  }, [focusRequest, hasMore, loaded, loadingOlder, loadOlder, rows]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (anchor.current !== null) { el.scrollTop = el.scrollHeight - anchor.current; anchor.current = null; return; }
    if (stick.current) el.scrollTop = el.scrollHeight;
  }, [rows, live, loaded, scrollRef]);

  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_TO_BOTTOM_PX;
  }, [scrollRef]);
  useEffect(() => {
    const el = externalScrollRef?.current;
    if (!el) return;
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [externalScrollRef, onScroll]);

  const locked = COMPOSER_LOCKED.includes(project.status);
  const latestRecommendation = [...(project.workbench?.recommendations || [])].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))[0];

  const send = useCallback(async () => {
    let text = draft.trim();
    if (!text || sending || locked) return;
    const command = text.match(/^\/(备注|计划提案|条件|引理|替代路线|替代建议|文献|验证|证明|找反例|数值计算|符号计算|检查论证|lean|分支)(?:\s+|$)/i);
    let selectedMode = inputMode;
    let explicitRequest = "";
    if (command) {
      const name = command[1].toLowerCase();
      text = text.slice(command[0].length).trim();
      if (name === "lean" || name === "分支") {
        window.dispatchEvent(new CustomEvent("research-open-materials", { detail: name === "lean" ? "formal" : "branches" }));
        setDraft(text); setSlashOpen(false); return;
      }
      const modes: Record<string, string> = { "备注": "human_note", "计划提案": "plan", "条件": "human_result", "引理": "human_lemma", "替代路线": "add_human_idea", "替代建议": "replace_recommendation", "文献": "discuss" };
      selectedMode = modes[name] || "discuss";
      const requests: Record<string, string> = { "验证": "request_verification", "证明": "request_proof", "找反例": "request_counterexample", "数值计算": "request_numerical", "符号计算": "request_symbolic", "检查论证": "request_critique" };
      explicitRequest = requests[name] || "";
      if (!text) { setSendError("请在命令后输入内容。"); return; }
      if (name === "文献") text = `请检索相关文献：${text}`;
    }
    setSending(true);
    setSendError("");
    try {
      const branch = targetBranch || project.workbench?.state?.currentBranch || "main";
      const kind = explicitRequest || (selectedMode === "plan" || selectedMode === "replace_recommendation" ? "add_human_idea" : selectedMode === "request" ? requestKind : selectedMode);
      if (selectedMode === "replace_recommendation" && (!latestRecommendation || latestRecommendation.status !== "PENDING")) throw new Error("当前没有可替代的待审建议。");
      const details = kind === "discuss" && !targetBranch ? { message: text } : { message: text, note: text, branch };
      const result = await addResearchIntervention(projectId, kind, details);
      let proposal = result.proposedPlan;
      if (selectedMode === "replace_recommendation") {
        if (!latestRecommendation) throw new Error("当前没有可替代的待审建议。");
        const decision = await decideResearchRecommendation(projectId, latestRecommendation.id, "alternative", "研究者提出替代路线。", { summary: "研究者提供了替代路线。", primary: [{ path: text, reason: "研究者撰写的替代方案。" }], secondary: [], deprioritize: [], source: "RESEARCHER" });
        proposal = decision.proposedPlan || proposal;
      }
      setDraft(""); setInputMode("discuss"); setSlashOpen(false);
      if (proposal) onProposedPlan(proposal);
      stick.current = true;
      await pollNew();
      onChanged();
    } catch (cause) {
      setSendError(cause instanceof Error ? cause.message : "发送失败，请重试。");
    } finally {
      setSending(false);
    }
  }, [draft, inputMode, requestKind, targetBranch, project.workbench, latestRecommendation, locked, onChanged, onProposedPlan, pollNew, projectId, sending]);

  const showOrigin = loaded && !hasMore;
  const running = LIVE_STATUSES.includes(project.status);

  return (
    <section ref={threadRef} className="research-thread" aria-label="研究对话">
      <div className="research-thread-scroll" ref={innerScrollRef} onScroll={externalScrollRef ? undefined : onScroll}>
        <div ref={topRef} className="research-thread-top">
          {hasMore ? (
            <ActionButton type="button" className="research-thread-more" disabled={loadingOlder} onClick={() => void loadOlder()}>
              {loadingOlder ? <LoaderCircle size={13} className="research-spin" /> : null} 加载更早的记录
            </ActionButton>
          ) : null}
        </div>
        {showOrigin && !hideOrigin ? (
          <div className="research-msg-user-row"><div className="research-msg-user research-msg-origin"><MathText text={project.question} /></div></div>
        ) : null}
        {compactProcess && featuredResult && resultExcerpt ? <article className="reference-featured-result"><div className="reference-result-caption">研究记录 · {featuredResult.status === "CONJECTURAL" ? "待核验" : "已记录"}</div><MathText text={showFullResult ? featuredResult.content : resultExcerpt} showRaw={showFullResult} /><button type="button" onClick={() => setShowFullResult((value) => !value)}>{showFullResult ? "收起成果" : "查看完整成果"}</button></article> : null}
        {!loaded ? <div className="research-thread-status" role="status"><LoaderCircle size={14} className="research-spin" /> 正在载入对话…</div> : null}
        {loadError ? <div className="research-thread-status" role="status" data-tone="warning"><CircleAlert size={14} /> {loadError}</div> : null}
        {(compactProcess ? processSegments : rows.map(row => ({ kind: "row" as const, key: row.key, rows: [row] }))).map(segment => {
          const renderRow = (row: ThreadRow) => <RowView key={row.key} row={row} queueStatus={row.type === "item" && row.item.kind === "user" && row.item.queueId ? queue[row.item.queueId] ?? "" : ""} artifactKind={row.type === "item" && row.item.kind === "answer" ? artifactKinds.get(row.item.artifactId) ?? "" : ""} getFull={getFull} getFailed={getFailed} onCancel={cancelDiscussion} />;
          if (segment.kind === "row") return renderRow(segment.rows[0]);
          const byStep = new Map<string, ThreadRow[]>();
          for (const row of segment.rows) {
            let stepId = row.type === "tools" ? row.items[0]?.stepId : row.item.kind !== "user" ? row.item.stepId : "";
            if (stepId?.startsWith("milestone:")) {
              const artifactId = row.type === "item" && row.item.kind === "answer" ? row.item.artifactId : "";
              const artifact = project.artifacts?.find(a => a.id === artifactId);
              const ids = artifact?.metadata?.stepIds;
              if (Array.isArray(ids) && typeof ids[0] === "string") stepId = ids[0];
            }
            const key = stepId || "other";
            byStep.set(key, [...(byStep.get(key) ?? []), row]);
          }
          const stepCount = [...byStep.keys()].filter(key => key !== "other").length;
          return <details className="research-process-group" key={segment.key}><summary>研究过程{stepCount ? ` · ${stepCount} 个步骤` : ""}</summary>
            {[...byStep].map(([id, values]) => {
              const step = project.plan?.steps.find(item => item.id === id);
              const label = step ? `步骤 ${step.order + 1} · ${statusText(step.status)}` : "其他研究记录";
              return (
                <ProcessStepBlock
                  key={id}
                  id={id}
                  label={label}
                  stepTitle={step?.title}
                  stepDescription={step?.description}
                >
                  {values.map(renderRow)}
                </ProcessStepBlock>
              );
            })}
          </details>;
        })}
        {running ? (
          <div className="research-thread-live" role="status">
            <LoaderCircle size={13} className="research-spin" />
            <span>{live?.action?.trim() || "研究正在推进…"}</span>
            {live?.tool ? <span className="research-thread-live-tool">· {live.tool}</span> : null}
          </div>
        ) : null}
        {footer ? <div className="research-thread-footer">{footer}</div> : null}
      </div>

      <div className="research-thread-dock">
      {dock ? <div className="research-thread-review-slot">{dock}</div> : null}
      {slashOpen ? <div className="reference-slash-menu" role="menu" aria-label="输入快捷命令"><small>研究者输入</small>
        {[ ["备注", "记录一条研究备注"], ["计划提案", "提出计划修改"], ["条件", "补充条件或结果"], ["引理", "补充引理"], ["替代路线", "提出替代路线"], ...(latestRecommendation?.status === "PENDING" ? [["替代建议", "替代当前建议"]] : []), ["文献", "请 AI 检索相关文献"], ["验证", "提交验证请求"], ["证明", "提交证明请求"], ["找反例", "提交反例搜索请求"], ["数值计算", "提交数值计算请求"], ["符号计算", "提交符号计算请求"], ["检查论证", "提交论证检查请求"], ["lean", "打开形式化资料"], ["分支", "打开分支与预算"] ].map(([command, description]) => <button type="button" role="menuitem" key={command} onClick={() => { setDraft(`/${command} `); setSlashOpen(false); inputRef.current?.focus(); }}><strong>/{command}</strong><span>{description}</span></button>)}
      </div> : null}
      <form className="research-thread-composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <textarea
          ref={inputRef}
          aria-label="向研究插话"
          value={draft}
          rows={1}
          maxLength={8000}
          disabled={locked}
          placeholder={inputMode === "discuss" ? "回复或与 AI 讨论研究结果…" : "写下备注、条件或计划提案，提交后会保存到研究记录…"}
          onChange={(event) => { setDraft(event.target.value); setInputMode("discuss"); setSlashOpen(/^\/[^\s]*$/.test(event.target.value)); }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
          }}
        />
        <div className="research-thread-composer-bar">
          <label className="research-attach-control" title="补充研究资料（PDF/LaTeX/图片可在「研究实验室 → 导入资料」中深度识别；聊天附件走 chat-attach）">
            <Paperclip size={16} /><span>附资料</span>
            <input aria-label="上传资料到对话" type="file" multiple accept=".pdf,.txt,.md,.docx,.html,.htm,.tex,.csv" disabled={locked || busy}
              onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; if (files.length) void onAttach(files); }} />
          </label>
          <button type="button" className="reference-slash-trigger" aria-label="输入快捷命令" onClick={() => { setDraft("/"); setSlashOpen(true); inputRef.current?.focus(); }}>/</button>
          <ResearchModeSelector projectId={project.id} disabled={running} />
          <details className="reference-thread-branch"><summary>分支 · {targetBranch || project.workbench?.state?.currentBranch || "main"}</summary><label><span className="sr-only">目标分支</span><select aria-label="目标分支" value={targetBranch} onChange={(event) => setTargetBranch(event.target.value)}><option value="">当前分支 · {project.workbench?.state?.currentBranch || "main"}</option>{(project.branches || []).map((branch) => <option key={branch.id} value={branch.name}>{branch.name}</option>)}</select></label></details>
          <ProviderModelSelector disabled={running} />
          <ActionButton type="submit" data-al-tone="primary" className="research-send-button" aria-label="发送插话" disabled={locked || sending || !draft.trim()}>
            {sending ? <LoaderCircle size={18} className="research-spin" /> : <ArrowUp size={18} />}
          </ActionButton>
        </div>
        {sendError ? <p className="research-thread-error" role="status"><CircleAlert size={13} /> {sendError}</p> : null}
      </form></div>
    </section>
  );
}
