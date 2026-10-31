import type { ResearchArtifact, ResearchGraphNode, ResearchPlan, ResearchProject } from "@/lib/research-api";

/** Presentation only. Unknown backend values must never leak as UI labels. */
const statuses: Record<string, string> = {
  DRAFT: "草稿", PLANNING: "正在生成计划", PLAN_READY: "计划已生成", AWAITING_APPROVAL: "等待批准",
  APPROVED: "计划已批准", RUNNING: "研究进行中", PAUSED: "已暂停", REPLANNING: "正在调整计划",
  VERIFYING: "正在复核", COMPLETED: "已完成", CANCELLED: "已停止", FAILED: "需要处理",
  PENDING: "待开始", BLOCKED: "等待处理", CHANGED: "已修改", SUPERSEDED: "已替代",
  MILESTONE_REVIEW_REQUIRED: "等待你审阅阶段成果", STEP_REVIEW_REQUIRED: "等待你审阅步骤成果",
  REJECTED_ROUTE: "路线记录待审阅", STALLED: "多次尝试没有新进展", MISSING_INPUT: "等待补充资料",
  INPUT_READY: "资料已补充", SUCCESS: "已完成", QUEUED: "等待中", STOPPED: "已停止", RESEARCHER_PAUSED: "已暂停",
};
export const statusText = (value: string | undefined | null) => statuses[(value ?? "").toUpperCase()] ?? "状态待确认";

/** Compare saved plan contents while ignoring backend-owned version and step statuses. */
export function sameSavedPlan(left: ResearchPlan, right: ResearchPlan | null): boolean {
  if (!right) return false;
  const normalize = (plan: ResearchPlan) => {
    const { version, changedBy, changeReason, changeSummary, approved, createdAt, ...content } = plan;
    void [version, changedBy, changeReason, changeSummary, approved, createdAt];
    return { ...content, steps: content.steps.map(({ status, ...step }) => { void status; return step; }) };
  };
  const ordered = (value: unknown): unknown => Array.isArray(value) ? value.map(ordered)
    : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, ordered(item)])) : value;
  return JSON.stringify(ordered(normalize(left))) === JSON.stringify(ordered(normalize(right)));
}

/**
 * Evidence hierarchy from weakest (1) to strongest (6):
 * 猜想(灰) -> 数值验证(蓝) -> 符号检查 -> 文献支持 -> 已证明(绿) -> 形式化验证(紫)
 */
export const EVIDENCE_HIERARCHY = [
  { key: "CONJECTURAL", label: "猜想", tone: "conjectural", rank: 1 },
  { key: "NUMERICALLY_OBSERVED", label: "数值验证", tone: "numerical", rank: 2 },
  { key: "SYMBOLICALLY_VERIFIED", label: "符号检查", tone: "symbolic", rank: 3 },
  { key: "LITERATURE_SUPPORTED", label: "文献支持", tone: "literature", rank: 4 },
  { key: "PROVEN", label: "已证明", tone: "proven", rank: 5 },
  { key: "FORMALLY_VERIFIED", label: "形式化验证", tone: "formal", rank: 6 },
] as const;

export const evidenceLabels: Record<string, { label: string; tone: string; rank: number }> = {
  CONJECTURAL: { label: "猜想", tone: "conjectural", rank: 1 },
  NUMERICALLY_OBSERVED: { label: "数值验证", tone: "numerical", rank: 2 },
  NUMERICALLY_CHECKED: { label: "数值验证", tone: "numerical", rank: 2 },
  COUNTEREXAMPLE_FOUND: { label: "数值验证", tone: "numerical", rank: 2 },
  CONTRADICTED: { label: "数值验证", tone: "numerical", rank: 2 },
  SYMBOLICALLY_VERIFIED: { label: "符号检查", tone: "symbolic", rank: 3 },
  LITERATURE_SUPPORTED: { label: "文献支持", tone: "literature", rank: 4 },
  PROVEN: { label: "已证明", tone: "proven", rank: 5 },
  FORMALLY_VERIFIED: { label: "形式化验证", tone: "formal", rank: 6 },
};

/** Convert only simple, recognisable un-delimited expressions; keep source untouched. */
export function normalizeMathText(text: string): string {
  const protectedMath = /(\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|```[\s\S]*?```|`[^`]*`|\[[^\]]*\]\([^)]*\)|https?:\/\/\S+)/g;
  const outsideMath = (value: string, convert: (part: string) => string) =>
    value.split(protectedMath).map((part, index) => (index % 2 ? part : convert(part))).join("");

  const opMap = (op: string) => (op === "<=" || op === "≤" ? "\\le " : op === ">=" || op === "≥" ? "\\ge " : op);

  let result = outsideMath(text, part => part.replace(/\b([a-z])\s+squared\b/gi, "$1^2").replace(/\b([a-z])\s+cubed\b/gi, "$1^3"));

  // Congruences: e.g. "P(n) ≡ n(n+1) mod 41", "n ≡ 0 or -1 mod 41"
  result = outsideMath(result, part =>
    part.replace(
      /([A-Za-z]\([A-Za-z0-9+\-^]+\)|[A-Za-z0-9^+\-]+)\s*(?:≡|\\equiv)\s*([A-Za-z0-9^+\-()]+(?:\s+or\s+[A-Za-z0-9^+\-()]+)?)\s*(?:\(\s*mod\s+(\d+|[a-z])\s*\)|\bmod(?:ulo)?\s+(\d+|[a-z])\b)/gi,
      (_match, lhs: string, rhs: string, modParen?: string, modBare?: string) => {
        const modVal = (modParen || modBare || "").trim();
        const cleanRhs = rhs.trim().replace(/\s+or\s+/gi, " \\text{ or } ");
        return `$${lhs.trim()} \\equiv ${cleanRhs} \\pmod{${modVal}}$`;
      },
    ),
  );

  // "<expr> modulo <n>" in prose: e.g. "P(n) modulo 41", "n modulo 41"
  result = outsideMath(result, part =>
    part.replace(/\b([A-Za-z](?:\([A-Za-z0-9+\-^]+\))?)\s+modulo\s+(\d+|[a-z])\b/g, (_m, expr: string, modVal: string) => `$${expr} \\bmod ${modVal}$`),
  );

  // Set membership: "n in Z_{>0}", "n in {1,2,3,...}"
  result = outsideMath(result, part =>
    part
      .replace(/\b([a-z])\s+in\s+Z_\{([^{}]+)\}/g, (_m, v: string, sub: string) => `$${v} \\in \\mathbb{Z}_{${sub}}$`)
      .replace(/\b([a-z])\s+in\s+\{([0-9,\s.]+)\}/g, (_m, v: string, body: string) => `$${v} \\in \\{${body.replace(/\.{3}/g, "\\ldots").replace(/\s+/g, "")}\\}$`),
  );

  // Ranges: "n=1,...,100"
  result = outsideMath(result, part =>
    part.replace(/\b([a-z])\s*=\s*(\d+)\s*,\s*\.{3}\s*,\s*(\d+)/gi, (_match, variable: string, first: string, last: string) => `$${variable}=${first},\\ldots,${last}$`),
  );

  // Chained inequalities: "0 <= n <= 39", "0≤n≤39"
  result = outsideMath(result, part =>
    part.replace(
      /\b(\d+|[a-z])\s*(<=|>=|≤|≥|<|>)\s*([a-z])\s*(<=|>=|≤|≥|<|>)\s*(\d+|[a-z])\b/gi,
      (_m, left: string, op1: string, mid: string, op2: string, right: string) => `$${left} ${opMap(op1).trim()} ${mid} ${opMap(op2).trim()} ${right}$`,
    ),
  );

  // Function definitions / equations with possibly chained "=":
  // e.g. "P(n)=n^2+n+41", "P(n) := n^2+n+41", "P(40)=40^2+40+41=1681=41^2", "P(41m-1)=41(41m^2-m+1)", "P(n)=ab"
  result = outsideMath(result, part =>
    part.replace(
      /\b[A-Za-z]\([A-Za-z0-9+\-^*]+\)\s*(?::=|=)\s*[A-Za-z0-9^()+*\-]+(?:\s*[+\-*=]\s*[A-Za-z0-9^()+*\-]+)*/g,
      value => `$${value.replace(/\s+/g, "").replace(/\*/g, "\\times ")}$`,
    ),
  );

  // Variable equations & products: e.g. "n=41m-1", "m=ab", "1681=41*41", "n=40"
  result = outsideMath(result, part =>
    part.replace(
      /\b(?:[a-z]\s*=\s*-?[0-9a-z^()+*\-]+(?:\s*[+\-*=]\s*[0-9a-z^()+*\-]+)*|\d+\s*=\s*\d+(?:\s*\*\s*\d+)+|\d+\s*\*\s*\d+)\b/gi,
      value => `$${value.replace(/\s+/g, "").replace(/\*/g, "\\times ")}$`,
    ),
  );

  // Single inequalities: "m >= 1", "a>1", "41>1"
  result = outsideMath(result, part =>
    part.replace(/\b([a-z]|\d+)\s*(<=|>=|≤|≥|<|>)\s*(-?\d+|[a-z])\b/gi, (_m, lhs: string, op: string, rhs: string) => {
      const cleanOp = op === "<=" || op === "≤" ? "\\le " : op === ">=" || op === "≥" ? "\\ge " : op;
      return `$${lhs}${cleanOp}${rhs}$`;
    }),
  );

  // Polynomials with powers & factored expressions: "n^2+n+41", "n^2", "41(41m^2-m+1)"
  result = outsideMath(result, part =>
    part
      .replace(/\b\d+\([a-z0-9+\-^]+\)/gi, value => `$${value.replace(/\s+/g, "")}$`)
      .replace(/\b[a-z]\^\d+(?:\s*[+\-]\s*(?:[a-z0-9]+)(?:\^\d+)?)*/gi, value => `$${value.replace(/\s+/g, "")}$`),
  );

  // Standalone function calls: "P(n)", "P(40)", "P(41m-1)"
  result = outsideMath(result, part =>
    part.replace(/\b([A-Za-z])\(([A-Za-z0-9]+(?:\s*[+\-^*]\s*[A-Za-z0-9]+)*)\)/g, (_m, fn: string, arg: string) => `$${fn}(${arg.replace(/\s+/g, "")})$`),
  );

  // Chinese & English variable mentions: "正整数 n", standalone "n"
  result = outsideMath(result, part => part.replace(/(正整数\s+)([a-z])(?=\s|[，。]|$)/gi, "$1$$$2$$"));
  result = outsideMath(result, part =>
    part
      .replace(/(?:(?<=^)|(?<=[\s(，。：；、“"']))n(?=$|[\s).,;:?!，。：；、”"'])/g, "$n$")
      .replace(/\b(integer|integers|small|explicit|variable)\s+([mpkxy])\b/gi, "$1 $$$2$$"),
  );

  return result;
}

export function splitJsonBlocks(text: string): { markdown: string; raw: string[] } {
  const raw: string[] = [];
  const markdown = text.replace(/```([a-zA-Z0-9_-]*)[ \t]*\n([\s\S]*?)(?:```|$)/g, (full, language: string, body: string) => {
    let isJson = language.toLowerCase() === "json";
    if (!language) {
      try { const parsed = JSON.parse(body); isJson = parsed !== null && typeof parsed === "object"; } catch { /* leave other code unchanged */ }
    }
    if (!isJson) return full;
    raw.push(body.trim());
    return "\n";
  });
  return { markdown, raw };
}

export type ResearchEvidenceItem = {
  id: string;
  conclusion: string;
  evidenceLevel: string;
  stepId: string;
  stepNumber: number | null;
  stepTitle: string;
  artifactId?: string | null;
};

export type ResearchArtifactItem = {
  id: string;
  title: string;
  kindLabel: string;
  content: string;
  stepId: string;
  stepLabel: string;
  createdAt: string;
  downloadArtifactId?: string;
  downloadFilename: string;
};

/**
 * Enforce strict evidence classification:
 * - FORMALLY_VERIFIED requires a USER_CONFIRMED + KERNEL_CERTIFIED formal binding or Lean kernel proof record.
 * - PROVEN requires a corresponding verified proof record (proofNodeId, verified proof_attempt node, or kernel proof).
 * - Any conclusion without a corresponding proof record is NEVER allowed to display as "已证明" (PROVEN).
 */
export function resolveNodeEvidenceLevel(node: ResearchGraphNode, project: ResearchProject): string {
  const meta = (node.metadata ?? {}) as Record<string, unknown>;
  const rawClass = String(meta.evidenceClass || meta.evidenceClassification || node.status || "CONJECTURAL").toUpperCase();

  const bindings = project.formalBindings ?? [];
  const hasFormalKernelProof = bindings.some(
    (b) =>
      (b.claimNodeId === node.id || b.id === meta.bindingId) &&
      b.mappingAuthority === "USER_CONFIRMED" &&
      Boolean(b.proofSourceHash) &&
      b.status === "KERNEL_CERTIFIED",
  );
  if (hasFormalKernelProof) {
    return "FORMALLY_VERIFIED";
  }

  const allNodes = project.graph?.nodes ?? [];
  const allEdges = project.graph?.edges ?? [];
  const verifiedProofNodeIds = new Set(
    allNodes
      .filter(
        (n) =>
          (n.type === "proof" || n.type === "proof_attempt") &&
          ["verified", "kernel_certified"].includes((n.status || "").toLowerCase()) &&
          Boolean(n.artifactId) &&
          (n.metadata as Record<string, unknown> | undefined)?.humanFeedback !== "invalid",
      )
      .map((n) => n.id),
  );
  const linkedProofByEdge = allEdges.some(
    (e) =>
      ((e.source === node.id && verifiedProofNodeIds.has(e.target)) ||
        (e.target === node.id && verifiedProofNodeIds.has(e.source))) &&
      /prove|support|verif/i.test(e.type || ""),
  );
  const linkedProofByBinding = bindings.some(
    (b) => b.claimNodeId === node.id && Boolean(b.proofNodeId) && verifiedProofNodeIds.has(b.proofNodeId!),
  );
  const linkedProofByMeta =
    typeof meta.proofNodeId === "string" && verifiedProofNodeIds.has(meta.proofNodeId);
  const isSelfVerifiedProof =
    (node.type === "proof" || node.type === "proof_attempt") && verifiedProofNodeIds.has(node.id);
  const hasProofRecord =
    hasFormalKernelProof || linkedProofByEdge || linkedProofByBinding || linkedProofByMeta || isSelfVerifiedProof;

  if (rawClass === "LITERATURE_SUPPORTED" || node.type === "literature") return "LITERATURE_SUPPORTED";
  if (rawClass === "SYMBOLICALLY_VERIFIED" || rawClass === "SYMBOLIC_CHECK") return "SYMBOLICALLY_VERIFIED";
  if (
    rawClass === "NUMERICALLY_OBSERVED" ||
    rawClass === "NUMERICALLY_CHECKED" ||
    rawClass === "COUNTEREXAMPLE_FOUND" ||
    rawClass === "CONTRADICTED" ||
    rawClass === "BOUNDED_CHECK"
  ) {
    return "NUMERICALLY_OBSERVED";
  }

  let parsedToolName = String(meta.tool || "").toLowerCase();
  if (!parsedToolName && node.content?.trim().startsWith("{")) {
    try {
      const parsed = JSON.parse(node.content) as Record<string, unknown>;
      if (parsed && typeof parsed.tool === "string") {
        parsedToolName = parsed.tool.toLowerCase();
      }
    } catch {
      // ignore non-json
    }
  }
  if (parsedToolName) {
    return /sympy|z3|sage/.test(parsedToolName) ? "SYMBOLICALLY_VERIFIED" : "NUMERICALLY_OBSERVED";
  }

  if (rawClass === "FORMALLY_VERIFIED") {
    return hasFormalKernelProof ? "FORMALLY_VERIFIED" : hasProofRecord ? "PROVEN" : "CONJECTURAL";
  }
  if (rawClass === "PROVEN" || rawClass === "VERIFIED" || node.status?.toLowerCase() === "verified") {
    if (hasProofRecord) return "PROVEN";
    if (node.type === "computation") {
      return /sympy|z3|sage/.test(parsedToolName) ? "SYMBOLICALLY_VERIFIED" : "NUMERICALLY_OBSERVED";
    }
    return "CONJECTURAL";
  }
  return "CONJECTURAL";
}

function cleanConclusionText(raw: string): string {
  return raw
    .replace(/^\s*\*\*+\s*/, "")
    .replace(/^\s*(?:当前结论|结论|Current Conclusion|Conclusion)\s*[:：]\s*/i, "")
    .replace(/^\s*\*\*+\s*/, "")
    .trim();
}

function formatToolNodeConclusion(node: ResearchGraphNode): string | null {
  try {
    const parsed = JSON.parse(node.content) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object" || !parsed.tool) return null;
    const tool = String(parsed.tool);
    const out = typeof parsed.output === "string" ? parsed.output.trim() : JSON.stringify(parsed.output ?? "");
    const cleanedOut = out
      .replace(/^\{|\}$/g, "")
      .replace(/'([A-Za-z0-9_]+)'\s*:\s*/g, "$1=")
      .replace(/'/g, "");
    const shortOut = cleanedOut.length > 220 ? `${cleanedOut.slice(0, 220)}…` : cleanedOut;
    return `执行工具 \`${tool}\` 验证结果：${shortOut || "已完成计算"}`;
  } catch {
    return null;
  }
}

export function buildProjectEvidence(project: ResearchProject): ResearchEvidenceItem[] {
  const steps = [...(project.plan?.steps ?? [])].sort((a, b) => a.order - b.order);
  const stepMap = new Map(steps.map((s, idx) => [s.id, { number: idx + 1, title: s.title }]));
  const artifactStepMap = new Map((project.artifacts ?? []).map((a) => [a.id, a.stepId]));

  const items: ResearchEvidenceItem[] = [];
  const seenConclusions = new Set<string>();

  for (const node of project.graph?.nodes ?? []) {
    const meta = (node.metadata ?? {}) as Record<string, unknown>;
    if (meta.failure === true || /^Failed execution path:/i.test(node.title || "")) continue;
    if (String(node.status || "").toLowerCase() === "rejected" || meta.humanFeedback === "invalid") continue;

    const toolFormatted = formatToolNodeConclusion(node);
    const cleaned = toolFormatted ?? cleanConclusionText(node.content || node.title || "");
    if (!cleaned) continue;
    if (/^(?:none\b|no\b|not found\b|none is advanced|无|未发现|未提出|本步骤未)/i.test(cleaned)) continue;

    const dedupeKey = cleaned.toLowerCase().replace(/\s+/g, " ");
    if (seenConclusions.has(dedupeKey)) continue;
    seenConclusions.add(dedupeKey);

    const stepId =
      (typeof meta.stepId === "string" && meta.stepId) ||
      (node.artifactId ? artifactStepMap.get(node.artifactId) || "" : "");
    const stepInfo = stepMap.get(stepId);
    const evidenceLevel = resolveNodeEvidenceLevel(node, project);

    items.push({
      id: node.id,
      conclusion: cleaned,
      evidenceLevel,
      stepId,
      stepNumber: stepInfo?.number ?? null,
      stepTitle: stepInfo?.title ?? (stepId ? "已完成步骤" : "研究记录"),
      artifactId: node.artifactId,
    });
  }

  // Also include step-report Current Conclusion if not already represented
  for (const artifact of project.artifacts ?? []) {
    if (artifact.metadata?.artifactKind !== "step-report") continue;
    const structured = artifact.metadata?.structuredReport as Record<string, unknown> | undefined;
    const rawConc = typeof structured?.["Current Conclusion"] === "string" ? structured["Current Conclusion"].trim() : "";
    if (!rawConc || /^No separate conclusion was supplied/i.test(rawConc)) continue;
    const cleaned = cleanConclusionText(rawConc);
    if (!cleaned) continue;
    const dedupeKey = cleaned.toLowerCase().replace(/\s+/g, " ");
    if (seenConclusions.has(dedupeKey)) continue;
    seenConclusions.add(dedupeKey);

    const stepId = artifact.stepId || "";
    const stepInfo = stepMap.get(stepId);
    const syntheticNode: ResearchGraphNode = {
      id: `conc_${artifact.id}`,
      type: "conjecture",
      title: artifact.title,
      content: cleaned,
      status: String(artifact.metadata?.evidenceClass || artifact.status || "CONJECTURAL"),
      branch: artifact.branch || "main",
      artifactId: artifact.id,
      metadata: { evidenceClass: artifact.metadata?.evidenceClass || artifact.status, stepId },
    };
    items.push({
      id: syntheticNode.id,
      conclusion: cleaned,
      evidenceLevel: resolveNodeEvidenceLevel(syntheticNode, project),
      stepId,
      stepNumber: stepInfo?.number ?? null,
      stepTitle: stepInfo?.title ?? artifact.title,
      artifactId: artifact.id,
    });
  }

  const rankOf = (level: string) => evidenceLabels[level]?.rank ?? 1;
  return items.sort((a, b) => rankOf(b.evidenceLevel) - rankOf(a.evidenceLevel));
}

export function buildProjectArtifacts(project: ResearchProject): ResearchArtifactItem[] {
  const steps = [...(project.plan?.steps ?? [])].sort((a, b) => a.order - b.order);
  const stepMap = new Map(steps.map((s, idx) => [s.id, `步骤 ${idx + 1} · ${s.title}`]));
  const result: ResearchArtifactItem[] = [];

  for (const artifact of project.artifacts ?? []) {
    const kind = String(artifact.metadata?.artifactKind || "");
    if (
      (artifact.status === "failed" || /^Failed path:/i.test(artifact.title || "")) &&
      /(?:No route failed in this step|无失败路线|未发生路线失败|没有路线失败)/i.test(artifact.content || "")
    ) {
      continue;
    }
    const stepLabel = stepMap.get(artifact.stepId) || (artifact.stepId.startsWith("milestone:") ? "阶段汇总" : "研究记录");

    if (kind === "milestone-report") {
      const cleanTitle = artifact.title.replace(/^Milestone\s*·\s*/i, "阶段报告 · ");
      result.push({
        id: artifact.id,
        title: cleanTitle,
        kindLabel: "阶段报告",
        content: artifact.content,
        stepId: artifact.stepId,
        stepLabel,
        createdAt: artifact.createdAt,
        downloadArtifactId: artifact.id,
        downloadFilename: `${cleanTitle}.md`,
      });
      continue;
    }

    if (kind === "step-report" || kind === "final-verification") {
      const cleanTitle = artifact.title.replace(/^Step\s*(\d+)\s*·\s*/i, "步骤 $1 报告 · ");
      result.push({
        id: artifact.id,
        title: cleanTitle,
        kindLabel: "阶段报告",
        content: artifact.content,
        stepId: artifact.stepId,
        stepLabel,
        createdAt: artifact.createdAt,
        downloadArtifactId: artifact.id,
        downloadFilename: `${cleanTitle}.md`,
      });
      continue;
    }

    if (artifact.agentRole === "math-tool") {
      try {
        const parsed = JSON.parse(artifact.content) as Record<string, unknown>;
        const input = (parsed.input ?? {}) as Record<string, unknown>;
        const code = typeof input.code === "string" ? input.code.trim() : "";
        const output = typeof parsed.output === "string" ? parsed.output : JSON.stringify(parsed.output ?? "", null, 2);
        const body = code
          ? `### 执行代码\n\n\`\`\`python\n${code}\n\`\`\`\n\n### 运行输出\n\n\`\`\`\n${output}\n\`\`\``
          : `\`\`\`json\n${JSON.stringify(parsed, null, 2)}\n\`\`\``;
        result.push({
          id: artifact.id,
          title: code ? `验证代码 · ${stepMap.get(artifact.stepId) || artifact.title}` : `计算记录 · ${artifact.title.replace(/^Tool result:\s*/i, "")}`,
          kindLabel: code ? "代码" : "计算记录",
          content: body,
          stepId: artifact.stepId,
          stepLabel,
          createdAt: artifact.createdAt,
          downloadArtifactId: artifact.id,
          downloadFilename: code ? `step-${artifact.stepId}-code.md` : `tool-${artifact.id}.json`,
        });
      } catch {
        result.push({
          id: artifact.id,
          title: artifact.title,
          kindLabel: "计算记录",
          content: artifact.content,
          stepId: artifact.stepId,
          stepLabel,
          createdAt: artifact.createdAt,
          downloadArtifactId: artifact.id,
          downloadFilename: `${artifact.id}.md`,
        });
      }
      continue;
    }

    if (kind === "agent-output" || artifact.agentRole) {
      const hasTable = /^\|.+\|\s*\r?\n\|[-:\s|]+\|/m.test(artifact.content || "");
      const hasCode = /```(?:python|sympy|lean|bash|sh)/i.test(artifact.content || "");
      const kindLabel = hasTable ? "表格与推导" : hasCode ? "代码与推导" : "步骤推导";
      const cleanTitle = artifact.title
        .replace(/^Failed path:\s*/i, "路线排查记录 · ")
        .replace(/^Experimental Mathematician:\s*/i, "实验数学分析 · ")
        .replace(/^Research Planner:\s*/i, "研究规划分析 · ")
        .replace(/^Explorer:\s*/i, "结构探索 · ")
        .replace(/^Proof Builder:\s*/i, "证明构建 · ")
        .replace(/^Independent Verifier:\s*/i, "独立复核 · ");
      result.push({
        id: artifact.id,
        title: cleanTitle,
        kindLabel,
        content: artifact.content,
        stepId: artifact.stepId,
        stepLabel,
        createdAt: artifact.createdAt,
        downloadArtifactId: artifact.id,
        downloadFilename: `${cleanTitle}.md`,
      });
    }
  }

  return result;
}

export type ProjectListStatusTone = "running" | "review" | "completed" | "stopped";

export function projectListStatus(project: Pick<ResearchProject, "status" | "workbench"> & Partial<Pick<ResearchProject, "lastError">>): {
  label: "进行中" | "等待审阅" | "已暂停" | "已完成" | "已停止";
  tone: ProjectListStatusTone;
} {
  const status = (project.status || "").toUpperCase();
  const reason = (project.workbench?.state?.statusReason || "").toUpperCase();
  if (status === "COMPLETED") {
    return { label: "已完成", tone: "completed" };
  }
  if (status === "CANCELLED") {
    return { label: "已停止", tone: "stopped" };
  }
  const reviewPending = ["MILESTONE_REVIEW_REQUIRED", "STEP_REVIEW_REQUIRED"].includes(reason) || /research artifacts and evidence snapshot are ready for researcher review/i.test(project.lastError || "");
  if (status === "PAUSED" && !reviewPending) {
    return { label: "已暂停", tone: "review" };
  }
  if (
    ["PAUSED", "PLAN_READY", "AWAITING_APPROVAL", "FAILED", "BLOCKED"].includes(status) ||
    ["MILESTONE_REVIEW_REQUIRED", "STEP_REVIEW_REQUIRED", "MISSING_INPUT", "REJECTED_ROUTE", "STALLED"].includes(reason)
  ) {
    return { label: "等待审阅", tone: "review" };
  }
  return { label: "进行中", tone: "running" };
}

export function formatProjectTime(value: string | number | undefined | null): string {
  if (value === undefined || value === null || value === "") return "刚刚";
  let ms: number;
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isNaN(num) && num > 0) {
    ms = num < 1e11 ? num * 1000 : num;
  } else {
    ms = Date.parse(String(value));
  }
  if (Number.isNaN(ms) || ms <= 0) return "刚刚";
  const date = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  return `${month}-${day} ${hours}:${minutes}`;
}
