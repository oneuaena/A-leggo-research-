import type { ResearchThreadItem } from "@/lib/research-api";

/** What the researcher is doing with a message sent into a running (or paused) session. */
export type ThreadIntent = "discuss" | "note" | "direction" | "literature" | "proof" | "counterexample" | "numerical" | "symbolic" | "critique" | "verification";

export const THREAD_INTENTS: { id: ThreadIntent; label: string; kind: string; hint: string }[] = [
  { id: "discuss", label: "和 AI 讨论", kind: "discuss", hint: "AI 立即回复，可现场调用 Python/SymPy 验证；不改变已批准的计划" },
  { id: "note", label: "补充条件", kind: "human_note", hint: "加入已知条件、约束或想法，之后每一步都会参考" },
  { id: "direction", label: "调整方向", kind: "add_human_idea", hint: "生成一版新路线供你审阅，批准前不会改变当前计划" },
  { id: "literature", label: "查文献", kind: "request_literature", hint: "在下一个安全步骤边界检索文献" },
  { id: "proof", label: "尝试证明", kind: "request_proof", hint: "让证明构建者在下一个安全步骤边界尝试" },
  { id: "counterexample", label: "找反例", kind: "request_counterexample", hint: "有界反例搜索" },
  { id: "numerical", label: "数值实验", kind: "request_numerical", hint: "用 Python 做数值实验" },
  { id: "symbolic", label: "符号计算", kind: "request_symbolic", hint: "用 SymPy 做符号检查" },
  { id: "critique", label: "挑错复核", kind: "request_critique", hint: "让质疑者攻击当前结论" },
  { id: "verification", label: "形式验证", kind: "request_verification", hint: "交给 Lean 检查" },
];

export const INTERVENTION_LABELS: Record<string, string> = Object.fromEntries([
  ...THREAD_INTENTS.map((intent) => [intent.kind, intent.label]),
  ["human_result", "补充结果"],
  ["human_lemma", "补充引理"],
  ["request_counterexample_search", "找反例"],
  ["request_symbolic_check", "符号计算"],
  ["request_critique_attack", "挑错复核"],
]);

export const QUEUE_LABELS: Record<string, string> = {
  QUEUED: "已排队，等待下一个安全步骤",
  RUNNING: "执行中",
  COMPLETED: "已完成",
  BLOCKED: "被阻塞",
  FAILED: "失败",
};

/** A discussion is answered right away, so its status reads as a reply, not as a queue position. */
export function queueLabelFor(intervention: string, status: string): string {
  if (!status) return "";
  if (intervention === "discuss") {
    if (status === "QUEUED" || status === "RUNNING") return "AI 思考中…";
    if (status === "FAILED" || status === "BLOCKED") return "回复失败，可重新发送";
    return "";
  }
  return QUEUE_LABELS[status] ?? status;
}

export type ThreadRow =
  | { type: "item"; key: string; item: ResearchThreadItem }
  | { type: "tools"; key: string; items: Extract<ResearchThreadItem, { kind: "tool" }>[] };

function order(a: ResearchThreadItem, b: ResearchThreadItem) {
  return a.createdAt === b.createdAt ? (a.id < b.id ? -1 : a.id > b.id ? 1 : 0) : a.createdAt < b.createdAt ? -1 : 1;
}

/**
 * Merge freshly fetched items (any order) into the chronological list.
 * Returns the *same array* when nothing changed so React skips re-rendering a long thread.
 */
export function mergeThreadItems(current: ResearchThreadItem[], incoming: ResearchThreadItem[]): ResearchThreadItem[] {
  if (!incoming.length) return current;
  const known = new Set(current.map((item) => item.id));
  const fresh = incoming.filter((item) => !known.has(item.id));
  if (!fresh.length) return current;
  const merged = [...current, ...fresh].sort(order);
  return merged;
}

/** Consecutive real tool calls collapse into one light "已运行 N 个工具调用" row. */
export function groupThreadItems(items: ResearchThreadItem[]): ThreadRow[] {
  const rows: ThreadRow[] = [];
  for (const item of items) {
    const last = rows[rows.length - 1];
    if (item.kind === "tool") {
      if (last?.type === "tools") last.items.push(item);
      else rows.push({ type: "tools", key: `tools:${item.id}`, items: [item] });
    } else rows.push({ type: "item", key: item.id, item });
  }
  return rows;
}

/** Reuse the previous row object whenever its content is unchanged so memoized rows do not re-render. */
export function stabilizeRows(previous: Map<string, ThreadRow>, next: ThreadRow[]): ThreadRow[] {
  return next.map((row) => {
    const old = previous.get(row.key);
    if (!old || old.type !== row.type) return row;
    if (old.type === "item" && row.type === "item") return old.item === row.item ? old : row;
    if (old.type === "tools" && row.type === "tools") {
      return old.items.length === row.items.length && old.items.every((item, index) => item === row.items[index]) ? old : row;
    }
    return row;
  });
}

export function formatElapsed(ms: number): string {
  if (!ms || ms < 0) return "";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  return seconds < 60 ? `${seconds.toFixed(seconds < 10 ? 1 : 0)} s` : `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

export function toolGroupSummary(items: { ok: boolean }[]): string {
  const failed = items.filter((item) => !item.ok).length;
  return `已运行 ${items.length} 个工具调用${failed ? ` · ${failed} 个未成功` : ""}`;
}

/** The threshold above which the thread stops auto-following new output (user scrolled up). */
export const STICK_TO_BOTTOM_PX = 96;
