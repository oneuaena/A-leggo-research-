"use client";

import MathText from "./MathText";
import { ActionButton } from "@/shared/ui/ActionButton";
import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";
import {
  Archive, ArrowDownToLine, ArrowRight, BookOpen, Check, CircleAlert, FileText,
  GitBranch, Link2, LoaderCircle, Plus, Search, ShieldCheck, Workflow,
} from "@/shared/ui/glyphs";
import {
  addResearchMemory, checkFormalBinding, compareResearchBranches, confirmFormalBinding, confirmResearchMemory,
  createFormalBinding,
  createResearchBranch, createResearchGraphEdge, createResearchGraphNode,
  exportResearchReport, importExistingMraWorkspace, importMraDatabaseFile,
  importResearchDocument, listImportedResearchRecords, listResearchBranches,
  getResearchProject, promoteFormalBinding, proposeFormalBinding,
  runFiniteDiscoverySearch, runIntegerCounterexampleSearch, searchFormalProof,
  searchResearchLiterature, searchResearchSources, type ResearchBranch, type ResearchFormalBinding, type ResearchGraphNode,
  curateResearchSource, linkResearchLiterature,
  setResearchBranchState, getLiteratureState, getLiteratureGraph, analyzeResearchLiterature,
  getLiteratureNote, generateLiteratureNote, saveLiteratureNote, importLiteratureDoi, importLiteratureLibrary,
  type FiniteDiscoveryResult, type IntegerCounterexampleResult, type ResearchMemory, type ResearchProject, type ResearchSource,
} from "@/lib/research-api";

type Tab = "graph" | "search" | "formal" | "literature" | "branches" | "memory" | "delivery";
const TABS: { id: Tab; label: string }[] = [
  { id: "graph", label: "证据图" },
  { id: "search", label: "反例与失败路径" },
  { id: "formal", label: "形式化" },
  { id: "literature", label: "文献" },
  { id: "branches", label: "分支与预算" },
  { id: "memory", label: "研究记忆" },
  { id: "delivery", label: "迁移与报告" },
];

const NODE_TYPE_LABELS: Record<string, string> = {
  question: "问题", conjecture: "猜想", definition: "定义", lemma: "引理",
  proposition: "命题", theorem: "定理", proof_attempt: "证明尝试",
  counterexample: "反例", evidence: "证据", experiment: "实验", source: "来源",
};
const EDGE_TYPE_LABELS: Record<string, string> = {
  SUPPORTS: "支持", REFUTES: "反驳", DEPENDS_ON: "依赖", DERIVED_FROM: "推导自",
  VERIFIES: "验证", CITES: "引用", USES: "使用", CONTRADICTS: "矛盾",
};
const LIT_RELATION_LABELS: Record<string, string> = {
  RELATED_TO: "相关", SUPPORTS: "支持", CONTRADICTS: "矛盾", USES: "使用", CITES: "引用",
};
const CLASSIFICATION_LABELS: Record<string, string> = {
  foundational: "基础", classic: "经典", survey: "综述", direct: "直接相关",
  methodological: "方法", recent: "近期", preprint: "预印本", contradictory: "相悖",
  supporting: "佐证", background: "背景",
};
const LEAN_CHECK_STATUS_ZH: Record<string, string> = {
  KERNEL_CERTIFIED: "内核已认证", TIMEOUT: "超时",
  OUTPUT_LIMIT: "输出超限", CHECK_FAILED: "检查未通过",
};
const leanCheckStatusZh = (status: string) => LEAN_CHECK_STATUS_ZH[status] ?? status;


function errorMessage(value: unknown) {
  return value instanceof Error ? value.message : "研究服务暂时无法完成该操作。";
}

function researchDetail(value: unknown): string {
  if (Array.isArray(value)) return value.map(researchDetail).join("\n");
  if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${researchDetail(item)}`).join(" · ");
  return value == null ? "无记录" : String(value);
}

export default function ResearchToolbench({
  project,
  isAdmin,
  onImportFinished,
  initialTab = "graph",
}: {
  project: ResearchProject;
  isAdmin: boolean;
  onImportFinished: () => Promise<void>;
  initialTab?: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [branches, setBranches] = useState<ResearchBranch[]>(project.branches ?? []);
  const [graph, setGraph] = useState(project.graph ?? { nodes: [], edges: [] });
  const [formalBindings, setFormalBindings] = useState<ResearchFormalBinding[]>(project.formalBindings ?? []);
  const [sources, setSources] = useState<ResearchSource[]>(project.sources ?? []);
  const [memories, setMemories] = useState<ResearchMemory[]>(project.memories ?? []);

  const [nodeType, setNodeType] = useState("conjecture");
  const [nodeTitle, setNodeTitle] = useState("");
  const [nodeContent, setNodeContent] = useState("");
  const [nodeBranch, setNodeBranch] = useState("main");
  const [edgeSource, setEdgeSource] = useState("");
  const [edgeTarget, setEdgeTarget] = useState("");
  const [edgeType, setEdgeType] = useState("SUPPORTS");
  const [edgeEvidence, setEdgeEvidence] = useState("");

  const [claimNodeId, setClaimNodeId] = useState("");
  const [originalStatement, setOriginalStatement] = useState("");
  const [formalIr, setFormalIr] = useState("");
  const [leanStatement, setLeanStatement] = useState("");
  const [proofSource, setProofSource] = useState("");
  const [confirmMapping, setConfirmMapping] = useState(false);
  const [checkResult, setCheckResult] = useState<{ status: string; output: string; elapsedMs: number } | null>(null);
  const [reviewBindingId, setReviewBindingId] = useState("");

  const [literatureQuery, setLiteratureQuery] = useState("");
  const [literatureItems, setLiteratureItems] = useState<ResearchSource[]>([]);
  const [literatureWarnings, setLiteratureWarnings] = useState<Record<string, string>>({});
  const [literatureEmptyHint, setLiteratureEmptyHint] = useState("");
  const [literatureLinkTargets, setLiteratureLinkTargets] = useState<Record<string, string>>({});
  const [literatureLinkTypes, setLiteratureLinkTypes] = useState<Record<string, "SUPPORTS" | "CONTRADICTS" | "USES" | "RELATED_TO" | "CITES">>({});
  const [literatureState, setLiteratureState] = useState<Record<string, unknown> | null>(null);
  const [literatureGraph, setLiteratureGraph] = useState<{ nodes: Record<string, unknown>[]; edges: Record<string, unknown>[] } | null>(null);
  const [noteDrafts, setNoteDrafts] = useState<Record<string, Record<string, string>>>({});
  const [noteProvenance, setNoteProvenance] = useState<Record<string, Record<string, unknown>>>({});
  const [sourcePage, setSourcePage] = useState(1);
  const [sourceFilter, setSourceFilter] = useState("");
  const [doiDraft, setDoiDraft] = useState("");
  const [branchReason, setBranchReason] = useState("");
  const [sourceQuery, setSourceQuery] = useState("");
  const [sourceHits, setSourceHits] = useState<{ id: string; title: string; page_number: number | null; section: string; content: string }[]>([]);

  const [branchName, setBranchName] = useState("");
  const [parentBranch, setParentBranch] = useState("main");
  const [compareLeft, setCompareLeft] = useState("main");
  const [compareRight, setCompareRight] = useState("");
  const [comparison, setComparison] = useState<Record<string, unknown> | null>(null);
  const [budgetBranch, setBudgetBranch] = useState("main");
  const [budgetDraft, setBudgetDraft] = useState<ResearchBranch["limits"]>({ evaluations: 1000000, proofAttempts: 128, toolSeconds: 900 });

  const [memoryDraft, setMemoryDraft] = useState("");
  const [memoryScope, setMemoryScope] = useState<"project" | "workspace">("project");
  const [legacyRecords, setLegacyRecords] = useState<{ sourceTable: string; id: string; collection: string; payload: unknown; createdAt: string }[]>([]);
  const [importSummary, setImportSummary] = useState<Record<string, unknown> | null>(null);
  const [discoveryProblem, setDiscoveryProblem] = useState('{"universeSize":12,"candidateSize":4,"incompatibilities":[[0,1],[2,3]],"coverageGroups":[[0,4,8],[1,5,9],[2,6,10],[3,7,11]]}');
  const [discoverySeed, setDiscoverySeed] = useState(42);
  const [discoveryBudget, setDiscoveryBudget] = useState(1000);
  const [discoveryBranch, setDiscoveryBranch] = useState("main");
  const [discoveryResult, setDiscoveryResult] = useState<FiniteDiscoveryResult | null>(null);
  const [counterexampleLeft, setCounterexampleLeft] = useState("n**2+n+1");
  const [counterexampleRight, setCounterexampleRight] = useState("0");
  const [counterexampleVariable, setCounterexampleVariable] = useState("n");
  const [counterexampleStart, setCounterexampleStart] = useState(0);
  const [counterexampleEnd, setCounterexampleEnd] = useState(100);
  const [counterexampleBranch, setCounterexampleBranch] = useState("main");
  const [counterexampleResult, setCounterexampleResult] = useState<IntegerCounterexampleResult | null>(null);

  const selectedBudgetBranch = branches.find((item) => item.name === budgetBranch);
  const nodesById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  const graphRows = useMemo(() => {
    const seen = new Set<string>();
    const rows: { node: ResearchGraphNode; depth: number; relation: string }[] = [];
    const visit = (node: ResearchGraphNode, depth: number, relation = "") => {
      if (seen.has(node.id)) return;
      seen.add(node.id); rows.push({ node, depth, relation });
      graph.edges.filter(edge => edge.source === node.id).forEach(edge => {
        const target = nodesById.get(edge.target);
        if (target) visit(target, Math.min(depth + 1, 5), EDGE_TYPE_LABELS[edge.type] || "关联");
      });
    };
    const targets = new Set(graph.edges.map(edge => edge.target));
    graph.nodes.filter(node => !targets.has(node.id)).forEach(node => visit(node, 0));
    graph.nodes.forEach(node => visit(node, 0));
    return rows;
  }, [graph, nodesById]);

  const refresh = useCallback(async () => {
    const current = await getResearchProject(project.id);
    setBranches(current.branches ?? []);
    setGraph(current.graph ?? { nodes: [], edges: [] });
    setFormalBindings(current.formalBindings ?? []);
    setSources(current.sources ?? []);
    setMemories(current.memories ?? []);
  }, [project.id]);

  useEffect(() => {
    setBranches(project.branches ?? []);
    setGraph(project.graph ?? { nodes: [], edges: [] });
    setFormalBindings(project.formalBindings ?? []);
    setSources(project.sources ?? []);
    setMemories(project.memories ?? []);
  }, [project.branches, project.graph, project.formalBindings, project.sources, project.memories]);

  useEffect(() => {
    setTab(initialTab); setError(""); setNotice(""); setCheckResult(null); setConfirmMapping(false);
    setLiteratureState(null); setLiteratureGraph(null); setNoteDrafts({}); setSourcePage(1);
    void refresh().catch((loadError) => setError(errorMessage(loadError)));
  }, [project.id, refresh, initialTab]);

  useEffect(() => {
    if (tab !== "literature") return;
    let active = true;
    void Promise.all([getLiteratureState(project.id), getLiteratureGraph(project.id)]).then(([state, map]) => { if (active) { setLiteratureState(state); setLiteratureGraph(map); } }).catch((cause) => { if (active) setError(errorMessage(cause)); });
    return () => { active = false; };
  }, [project.id, tab, sources]);

  useEffect(() => {
    const onTabRequest = (event: Event) => {
      const requested = (event as CustomEvent<string>).detail;
      if (["graph", "search", "formal", "literature", "branches", "memory", "delivery"].includes(requested)) setTab(requested as Tab);
      document.getElementById("research-toolbench")?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    window.addEventListener("research-toolbench-tab", onTabRequest);
    return () => window.removeEventListener("research-toolbench-tab", onTabRequest);
  }, []);

  useEffect(() => {
    const eligible = graph.nodes.find((node) => ["conjecture", "lemma", "proposition", "theorem"].includes(node.type));
    if (!claimNodeId && eligible) {
      setClaimNodeId(eligible.id);
      setOriginalStatement(eligible.content || eligible.title);
    }
  }, [graph.nodes, claimNodeId]);

  const run = useCallback(async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError("");
    setNotice("");
    try { await action(); }
    catch (actionError) { setError(errorMessage(actionError)); }
    finally { setBusy(""); }
  }, []);

  const addNode = () => run("node", async () => {
    if (!nodeTitle.trim()) throw new Error("请为该证据图记录填写标题。");
    const node = await createResearchGraphNode(project.id, { type: nodeType, title: nodeTitle.trim(), content: nodeContent, branch: nodeBranch });
    setGraph((current) => ({ ...current, nodes: [...current.nodes, node] }));
    setNodeTitle(""); setNodeContent(""); setNotice("已将类型化记录加入证据图。");
  });

  const addEdge = () => run("edge", async () => {
    if (!edgeSource || !edgeTarget) throw new Error("请先选择起点与终点记录。");
    const edge = await createResearchGraphEdge(project.id, { sourceId: edgeSource, targetId: edgeTarget, type: edgeType, evidence: edgeEvidence });
    setGraph((current) => ({ ...current, edges: [...current.edges, edge as typeof current.edges[number]] }));
    setEdgeEvidence(""); setNotice("已保存类型化关系。");
  });

  const saveFormalBinding = () => run("formal-binding", async () => {
    if (!claimNodeId || !originalStatement.trim() || !formalIr.trim() || !leanStatement.trim()) {
      throw new Error("请选择命题，并填写原始命题、形式化 IR 与 Lean 命题。");
    }
    const values = { originalStatement: originalStatement.trim(), formalIr: formalIr.trim(), leanStatement: leanStatement.trim() };
    if (reviewBindingId) {
      if (!confirmMapping) throw new Error("请勾选确认框以确认已审阅的映射。");
      const binding = await confirmFormalBinding(project.id, reviewBindingId, values);
      setFormalBindings((current) => current.map((item) => item.id === binding.id ? binding : item));
      setReviewBindingId(""); setNotice("形式化映射已审阅、确认并绑定哈希。");
      return;
    }
    const binding = await createFormalBinding(project.id, { claimNodeId, ...values, confirmMapping });
    setFormalBindings((current) => [binding, ...current]);
    setNotice(binding.mappingAuthority === "USER_CONFIRMED" ? "命题映射已确认并绑定哈希。" : "映射已保存为未确认；审阅确认前无法做内核检查。");
  });

  const proposeFormal = () => run("formal-propose", async () => {
    if (!claimNodeId) throw new Error("请先选择命题节点。");
    const binding = await proposeFormalBinding(project.id, claimNodeId);
    setFormalBindings((current) => [binding, ...current.filter((item) => item.id !== binding.id)]);
    setOriginalStatement(binding.originalStatement); setFormalIr(binding.formalIr); setLeanStatement(binding.leanStatement);
    setReviewBindingId(binding.id); setConfirmMapping(false);
    setNotice("AI 已生成命题映射提案，请先确认映射关系再做内核检查。");
  });

  const runFormalCheck = (binding: ResearchFormalBinding) => run(`formal-check-${binding.id}`, async () => {
    if (!proofSource.trim()) throw new Error("运行内核检查前请先输入 Lean tactic 证明。");
    const result = await checkFormalBinding(project.id, binding.id, proofSource);
    setCheckResult({ status: result.check.status, output: result.check.output, elapsedMs: result.check.elapsedMs });
    await refresh();
    setNotice(result.check.passed ? "Lean 内核已接受这次证明。提升为正式结论仍需单独操作。" : `内核检查结束：${leanCheckStatusZh(result.check.status)}。`);
  });

  const runProofSearch = (binding: ResearchFormalBinding) => run(`formal-search-${binding.id}`, async () => {
    const result = await searchFormalProof(project.id, binding.id);
    setCheckResult({ status: result.check.status, output: result.check.output, elapsedMs: result.check.elapsedMs });
    await refresh();
    setNotice(result.check.passed
      ? `有界 tactic 搜索（${result.searchPortfolio}）找到了内核认可的证明。`
      : `有界 tactic 搜索未能封闭该目标：${leanCheckStatusZh(result.check.status)}。`);
  });

  const runDiscovery = () => run("discovery", async () => {
    const parsed = JSON.parse(discoveryProblem) as { universeSize: number; candidateSize: number; incompatibilities: number[][]; coverageGroups: number[][] };
    const result = await runFiniteDiscoverySearch(project.id, {
      problem: parsed, branch: discoveryBranch, seed: discoverySeed, evaluationBudget: discoveryBudget,
    });
    setDiscoveryResult(result); await refresh();
    setNotice(`${result.status.replaceAll("_", " ")}（精确有限域求值 ${result.evaluations} 次）。这不是无限制的证明。`);
  });

  const runCounterexampleSearch = () => run("counterexample", async () => {
    const result = await runIntegerCounterexampleSearch(project.id, {
      leftExpression: counterexampleLeft, rightExpression: counterexampleRight,
      variable: counterexampleVariable, start: counterexampleStart, end: counterexampleEnd,
      branch: counterexampleBranch,
    });
    setCounterexampleResult(result); await refresh();
    setNotice(result.status === "COUNTEREXAMPLE_FOUND"
      ? `Exact counterexample found at ${result.variable} = ${result.value}.`
      : `在 ${result.casesChecked} 个精确整数情形中未找到反例；这不是证明。`);
  });

  const promoteBinding = (binding: ResearchFormalBinding) => run(`formal-promote-${binding.id}`, async () => {
    await promoteFormalBinding(project.id, binding.id);
    await refresh();
    setNotice("内核认证的证明已提升为类型化图节点（VERIFIES 边：验证）。");
  });

  const searchLiterature = () => run("literature", async () => {
    const result = await searchResearchLiterature(project.id, literatureQuery);
    setLiteratureItems(result.items); setLiteratureWarnings(result.warnings);
    setLiteratureEmptyHint(result.items.length ? "" : (result.emptyHint || "没有找到匹配的文献元数据。可尝试更短关键词、英文术语或作者姓名。"));
    await refresh();
    const availability = result.availabilityStatus === "PARTIAL" ? "（部分文献源不可用，详见下方各源状态）" : "";
    setNotice(`已保存 ${result.items.length} 条文献元数据。${availability}元数据匹配不代表新颖性结论。`);
  });

  const curateSource = (source: ResearchSource, changes: { reading_status?: ResearchSource["readingStatus"]; classification?: string; notes?: string; important?: boolean }) => run(`curate-${source.id}`, async () => {
    const updated = await curateResearchSource(project.id, source.id, changes);
    setSources((current) => current.map((item) => item.id === updated.id ? updated : item));
    await refresh();
    setNotice("文献审阅状态已保存。");
  });

  const linkSource = (source: ResearchSource) => run(`link-${source.id}`, async () => {
    const entityId = literatureLinkTargets[source.id];
    if (!entityId) throw new Error("请选择要关联的引理、猜想或证据记录。");
    const isBranch = entityId.startsWith("branch:");
    const entity = graph.nodes.find((item) => item.id === entityId);
    const entityType = isBranch ? "branch" : entity?.type === "lemma" ? "lemma" : entity?.type === "conjecture" ? "conjecture" : "graph_node";
    await linkResearchLiterature(project.id, { sourceId: source.id, entityType, entityId: isBranch ? entityId.slice(7) : entityId, relationship: literatureLinkTypes[source.id] || "RELATED_TO", notes: "研究者整理的文献关联。" });
    await refresh();
    setNotice("文献关联已写入证据图。");
  });

  const onDocument = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    void run("document", async () => {
      if (file.size > 25 * 1024 * 1024) throw new Error("研究文档上限为 25 MB。");
      const source = await importResearchDocument(project.id, file);
      await refresh();
      setNotice(`${source.title}: ${source.chunkCount} searchable chunk${source.chunkCount === 1 ? "" : "s"} indexed.`);
    });
  };

  const searchSources = () => run("source-search", async () => {
    const result = await searchResearchSources(project.id, sourceQuery);
    setSourceHits(result.items); setNotice(`${result.items.length} matching indexed passage${result.items.length === 1 ? "" : "s"}.`);
  });

  const addBranch = () => run("branch", async () => {
    const branch = await createResearchBranch(project.id, branchName, parentBranch);
    setBranches((current) => [...current, branch]); setBranchName(""); setCompareRight(branch.name); setBudgetBranch(branch.name);
    setNotice(`Branch “${branch.name}” created from ${parentBranch}.`);
  });

  const changeBranchState = (branch: ResearchBranch, status: string, priority?: number) => run(`branch-${branch.id}`, async () => {
    const updated = await setResearchBranchState(project.id, branch.name, status, branchReason || "研究者更改了分支状态或优先级。", priority);
    setBranches((current) => current.map((item) => item.id === updated.id ? updated : item));
    setNotice("分支决策已保存，将在下一个安全步骤边界生效。");
  });

  const loadNote = (source: ResearchSource, generate = false) => run(`note-${source.id}`, async () => {
    const note = await (generate ? generateLiteratureNote(project.id, source.id) : getLiteratureNote(project.id, source.id));
    const fields = note.content || note.fields || Object.fromEntries(Object.entries(note).filter(([, value]) => typeof value === "string")) as Record<string, string>;
    setNoteDrafts((current) => ({ ...current, [source.id]: fields }));
    setNoteProvenance((current) => ({ ...current, [source.id]: note }));
    setNotice(generate ? "已根据现有材料生成结构化笔记；请审阅后再依赖其内容。" : "已加载已保存的文献笔记。");
  });

  const saveBudget = () => run("budget", async () => {
    if (!selectedBudgetBranch) throw new Error("请先选择分支。");
    const { requestJson } = await import("@/shared/api/client");
    const updated = await requestJson<ResearchBranch>(`/api/research/projects/${encodeURIComponent(project.id)}/branches/${encodeURIComponent(budgetBranch)}/budget`, {
      method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limits: budgetDraft }),
    });
    setBranches((current) => current.map((item) => item.name === updated.name ? updated : item));
    setNotice("分支资源限额已保存。");
  });

  const compare = () => run("compare", async () => {
    const result = await compareResearchBranches(project.id, compareLeft, compareRight);
    setComparison(result); setNotice("分支对比已更新。");
  });

  const addMemory = () => run("memory", async () => {
    if (!memoryDraft.trim()) throw new Error("请先撰写研究笔记。");
    await addResearchMemory(project.id, memoryDraft, memoryScope, false);
    setMemoryDraft(""); await refresh(); setNotice("记忆已保存为未确认。确认后才会用于后续推理。");
  });

  const setMemoryConfirmed = (memory: ResearchMemory) => run(`memory-${memory.id}`, async () => {
    await confirmResearchMemory(project.id, memory.id, !memory.confirmed);
    setMemories((current) => current.map((item) => item.id === memory.id ? { ...item, confirmed: !memory.confirmed } : item));
  });

  const importExisting = () => run("import", async () => {
    const summary = await importExistingMraWorkspace(); setImportSummary(summary);
    await onImportFinished();
    setNotice(summary.alreadyImported ? "This MRA database was already migrated; the existing import is unchanged." : "Math Research Agent workspace imported. Its source database remains unchanged.");
  });

  const importFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    void run("import-file", async () => {
      if (file.size > 512 * 1024 * 1024) throw new Error("Math Research Agent 数据库上限为 512 MB。");
      const summary = await importMraDatabaseFile(file); setImportSummary(summary);
      await onImportFinished();
      setNotice(summary.alreadyImported ? "该数据库此前已迁移。" : "数据库已导入；源文件未被修改。");
    });
  };

  const loadLegacyRecords = () => run("legacy", async () => {
    const response = await listImportedResearchRecords(project.id);
    setLegacyRecords(response.items);
    setNotice(`${response.items.length} imported records loaded (up to 100 shown).`);
  });

  const exportReport = (format: "markdown" | "pdf" | "latex") => run(`export-${format}`, async () => {
    const blob = await exportResearchReport(project.id, format);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const extension = format === "markdown" ? "md" : format === "pdf" ? "pdf" : "latex.zip";
    link.href = url; link.download = `${project.title.replace(/[^\p{L}\p{N}.-]+/gu, "-") || "research-report"}.${extension}`;
    document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    setNotice(format === "latex" ? "LaTeX paper draft downloaded (main.tex + references.bib)." : `${format.toUpperCase()} report downloaded.`);
  });

  const filteredSources = sources.filter((source) => `${source.title} ${source.authors} ${source.classification || ""} ${source.readingStatus || ""}`.toLocaleLowerCase().includes(sourceFilter.toLocaleLowerCase()));
  const sourcePages = Math.max(1, Math.ceil(filteredSources.length / 20));
  const visibleSources = filteredSources.slice((Math.min(sourcePage, sourcePages) - 1) * 20, Math.min(sourcePage, sourcePages) * 20);

  return (
    <section id="research-toolbench" className="research-toolbench" aria-label="Research tools">
      <header className="research-toolbench-header">
        <span>依据本研究的实际记录展示</span><details className="reference-export-menu"><summary>导出</summary><div>{(["markdown", "pdf", "latex"] as const).map(format => <button type="button" key={format} disabled={Boolean(busy)} onClick={() => exportReport(format)}>{format === "markdown" ? "Markdown" : format === "pdf" ? "PDF" : "LaTeX"}</button>)}</div></details>
      </header>
      <nav className="research-tool-tabs" aria-label="研究项目工具">
        {TABS.map((item) => <ActionButton key={item.id} type="button" className={tab === item.id ? "is-active" : ""} onClick={() => setTab(item.id)}>{item.label}</ActionButton>)}
      </nav>
      {error ? <div className="research-error" role="alert"><CircleAlert size={15} /> {error}</div> : null}
      {notice ? <p className="research-tool-notice" role="status">{notice}</p> : null}

      {tab === "graph" ? (
        <div className="reference-graph-area">
          <div className="reference-graph-tree">{graph.nodes.length ? graphRows.map(({node, depth, relation}) => <button type="button" key={node.id} style={{ paddingLeft: `${9 + depth * 20}px` }} className={(selectedNodeId || graph.nodes[0]?.id) === node.id ? "is-selected" : ""} onClick={() => setSelectedNodeId(node.id)}><span title={relation || "独立记录"}>●</span><MathText text={node.title} /><small>{NODE_TYPE_LABELS[node.type] || "研究记录"}</small></button>) : <p>当前研究还没有证据图记录。</p>}</div>
          <div className="reference-graph-detail">{(() => { const node = nodesById.get(selectedNodeId) || graph.nodes[0]; if (!node) return <p>选择一条记录查看详情。</p>; const artifact = project.artifacts?.find(item => item.id === node.artifactId); return <><small>{NODE_TYPE_LABELS[node.type] || "研究记录"}</small><h4><MathText text={node.title} /></h4>{node.content ? <MathText text={node.content} /> : <p>暂无详细内容。</p>}<hr /><small>来源</small><p>{artifact?.title || (node.artifactId ? "关联研究成果" : "暂无来源记录")}</p><small>所在步骤</small><p>{artifact?.stepId ? project.plan?.steps.find(step => step.id === artifact.stepId)?.title || "关联成果中的研究步骤" : "暂无步骤记录"}</p><small>分支</small><p>{node.branch || "当前分支"}</p><small>关联记录</small>{graph.edges.filter(edge => edge.source === node.id || edge.target === node.id).length ? graph.edges.filter(edge => edge.source === node.id || edge.target === node.id).map(edge => <p key={edge.id}>{EDGE_TYPE_LABELS[edge.type] || "关联"}：{nodesById.get(edge.source === node.id ? edge.target : edge.source)?.title || "关联记录"}</p>) : <p>暂无关联记录。</p>}</>; })()}</div>
          <details className="reference-graph-edit"><summary>编辑证据图</summary><div className="research-tool-grid">
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">类型化命题图</span><h4>添加研究记录</h4></div><Workflow size={17} /></div>
            <div className="research-control-grid">
              <label className="research-field"><span className="research-field-label">类型</span><select value={nodeType} onChange={(event) => setNodeType(event.target.value)}>{Object.entries(NODE_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label className="research-field"><span className="research-field-label">分支</span><select value={nodeBranch} onChange={(event) => setNodeBranch(event.target.value)}>{branches.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label>
            </div>
            <label className="research-field"><span className="research-field-label">命题或记录</span><input value={nodeTitle} onChange={(event) => setNodeTitle(event.target.value)} maxLength={500} /></label>
            <label className="research-field"><span className="research-field-label">陈述、推导或证据</span><textarea rows={4} value={nodeContent} onChange={(event) => setNodeContent(event.target.value)} maxLength={100000} /></label>
            <ActionButton className="research-primary-button" type="button" disabled={busy === "node"} onClick={() => void addNode()}>{busy === "node" ? <LoaderCircle size={15} className="research-spin" /> : <Plus size={15} />} 添加类型化记录</ActionButton>
          </div>
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">关系</span><h4>连接证据</h4></div><Link2 size={17} /></div>
            <div className="research-control-grid">
              <label className="research-field"><span className="research-field-label">起点</span><select value={edgeSource} onChange={(event) => setEdgeSource(event.target.value)}><option value="">选择节点</option>{graph.nodes.map((item) => <option key={item.id} value={item.id}>{item.type}: {item.title}</option>)}</select></label>
              <label className="research-field"><span className="research-field-label">关系类型</span><select value={edgeType} onChange={(event) => setEdgeType(event.target.value)}>{Object.entries(EDGE_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            </div>
            <label className="research-field"><span className="research-field-label">终点</span><select value={edgeTarget} onChange={(event) => setEdgeTarget(event.target.value)}><option value="">选择节点</option>{graph.nodes.map((item) => <option key={item.id} value={item.id}>{item.type}: {item.title}</option>)}</select></label>
            <label className="research-field"><span className="research-field-label">证据或理由</span><textarea rows={3} value={edgeEvidence} onChange={(event) => setEdgeEvidence(event.target.value)} /></label>
            <ActionButton className="research-secondary-button" type="button" disabled={busy === "edge" || graph.nodes.length < 2} onClick={() => void addEdge()}>{busy === "edge" ? <LoaderCircle size={15} className="research-spin" /> : <Link2 size={15} />} 连接记录</ActionButton>
          </div>
          <div className="research-graph-ledger">
            <h4>图台账 <small>{graph.nodes.length} 节点 · {graph.edges.length} 条边</small></h4>
            <div className="research-graph-node-list">{graph.nodes.length ? graph.nodes.map((node) => <article key={node.id}><span className="research-graph-type">{node.type}</span><strong>{node.title}</strong><small>{node.branch} · {node.status}</small>{node.content ? <p>{node.content}</p> : null}</article>) : <p className="research-muted-copy">命题、定义、实验、证据、失败路径与证明尝试将作为类型化记录显示于此。</p>}</div>
            <div className="research-graph-edge-list">{graph.edges.map((edge) => <div key={edge.id}><span>{nodesById.get(edge.source)?.title ?? edge.source}</span><b>{edge.type}</b><ArrowRight size={14} /><span>{nodesById.get(edge.target)?.title ?? edge.target}</span><small>{edge.evidence}</small></div>)}</div>
            <p className="research-proof-gate"><ShieldCheck size={14} /> 模型输出未经核验。仅服务端 Lean 内核适配器可将证明标为已验证以便提升。</p>
          </div>
        </div></details></div>
      ) : null}

      {tab === "formal" ? (
        <div className="research-formal-workbench">
          <div className="research-formal-intro">
            <div className="research-card-heading"><div><span className="research-eyebrow">LEAN 内核 · MATHLIB 4.32.0</span><h4>将命题绑定到形式化陈述</h4></div><ShieldCheck size={18} /></div>
            <p>请记录原始数学命题、可审阅的形式化 IR，以及对应的 Lean 命题。研究者必须先确认映射，再送检代码。通过检查与证明提升是分开的步骤。</p>
          </div>
          <div className="research-formal-grid">
            <div className="research-tool-form">
              <label className="research-field"><span className="research-field-label">命题节点</span><select value={claimNodeId} onChange={(event) => { const id = event.target.value; setClaimNodeId(id); const node = graph.nodes.find((item) => item.id === id); if (node) setOriginalStatement(node.content || node.title); }}><option value="">选择命题</option>{graph.nodes.filter((node) => ["conjecture", "lemma", "proposition", "theorem"].includes(node.type)).map((node) => <option value={node.id} key={node.id}>{node.type}: {node.title}</option>)}</select></label>
              <label className="research-field"><span className="research-field-label">原始定理或命题</span><textarea rows={3} value={originalStatement} onChange={(event) => setOriginalStatement(event.target.value)} maxLength={12000} placeholder="须与所选图节点的标题或内容一致" /></label>
              <label className="research-field"><span className="research-field-label">形式化 IR · 定义、量词、假设</span><textarea rows={4} value={formalIr} onChange={(event) => setFormalIr(event.target.value)} maxLength={12000} placeholder="写出连接命题与形式化陈述的明确数学解释" /></label>
              <label className="research-field"><span className="research-field-label">Lean 命题 · 单一表达式</span><textarea rows={3} value={leanStatement} onChange={(event) => setLeanStatement(event.target.value)} maxLength={12000} placeholder="例如：∀ n m : Nat, n + m = m + n" /></label>
              <label className="research-confirm-mapping"><input type="checkbox" checked={confirmMapping} onChange={(event) => setConfirmMapping(event.target.checked)} /><span>我已审阅原始陈述、形式化 IR 与 Lean 命题，确认它们表达同一主张。</span></label>
              <div className="research-formal-actions"><ActionButton className="research-secondary-button" type="button" disabled={busy === "formal-propose" || !claimNodeId} onClick={() => void proposeFormal()}>{busy === "formal-propose" ? <LoaderCircle size={15} className="research-spin" /> : <Search size={15} />} 请 {project.modelId.split(".")[1]} 提议映射</ActionButton><ActionButton className="research-primary-button" type="button" disabled={busy === "formal-binding" || !claimNodeId} onClick={() => void saveFormalBinding()}>{busy === "formal-binding" ? <LoaderCircle size={15} className="research-spin" /> : <Check size={15} />} {reviewBindingId ? "确认已审阅的映射" : "保存形式化映射"}</ActionButton></div>
              <p className="research-muted-copy">未确认的手工映射与 AI 提议分开标注。在您审阅并确认之前，都不会送交 Lean。</p>
            </div>
            <div className="research-tool-form">
              <div className="research-card-heading"><div><span className="research-eyebrow">TACTIC 证明</span><h4>运行有界内核检查</h4></div><Workflow size={17} /></div>
              <label className="research-field"><span className="research-field-label">Lean tactic 正文 · 定理头与 import 由服务器控制</span><textarea className="research-monospace-input" rows={9} value={proofSource} onChange={(event) => setProofSource(event.target.value)} maxLength={40000} placeholder={'intro n m\nexact Nat.add_comm n m'} /></label>
              <p className="research-muted-copy">在锁定的本地 Lean 与预构建 Mathlib 工作区中运行，有输出上限、超时与分支证明/时间预算。不接受任意 shell 或项目文件。</p>
              {checkResult ? <div className="research-formal-check-result" role="status"><strong>{checkResult.status}</strong><small>{checkResult.elapsedMs} ms</small><pre>{checkResult.output || "无检查器输出。"}</pre></div> : null}
            </div>
          </div>
          <div className="research-formal-ledger">
            <h4>形式化绑定 <small>{formalBindings.length}</small></h4>
            {formalBindings.length ? formalBindings.map((binding) => {
              const claim = graph.nodes.find((node) => node.id === binding.claimNodeId);
              return <article className="research-formal-binding" key={binding.id}>
                <header><strong>{claim?.title ?? binding.originalStatement}</strong><span data-status={binding.status}>{binding.status.replaceAll("_", " ")}</span></header>
                <p>{binding.originalStatement}</p>
                <small>{binding.mappingAuthority.replaceAll("_", " ")} · binding SHA-256 {binding.bindingHash.slice(0, 20)}…{binding.leanVersion ? ` · ${binding.leanVersion}` : ""}</small>
                <details><summary>形式化 IR 与 Lean 命题</summary><p>{binding.formalIr}</p><pre>{binding.leanStatement}</pre></details>
                {binding.checkerOutput ? <details><summary>最近一次检查器输出</summary><pre>{binding.checkerOutput}</pre></details> : null}
                <div className="research-formal-actions">
                  {binding.mappingAuthority !== "USER_CONFIRMED" ? <ActionButton className="research-secondary-button" type="button" onClick={() => { setClaimNodeId(binding.claimNodeId); setOriginalStatement(binding.originalStatement); setFormalIr(binding.formalIr); setLeanStatement(binding.leanStatement); setReviewBindingId(binding.id); setConfirmMapping(false); setTab("formal"); }}>审阅映射</ActionButton> : null}
                  {binding.mappingAuthority === "USER_CONFIRMED" && binding.status !== "PROMOTED" ? <>
                    <ActionButton className="research-secondary-button" type="button" disabled={busy === `formal-check-${binding.id}`} onClick={() => void runFormalCheck(binding)}>{busy === `formal-check-${binding.id}` ? <LoaderCircle size={15} className="research-spin" /> : <Workflow size={15} />} 检查已输入证明</ActionButton>
                    <ActionButton className="research-secondary-button" type="button" disabled={busy === `formal-search-${binding.id}`} onClick={() => void runProofSearch(binding)}>{busy === `formal-search-${binding.id}` ? <LoaderCircle size={15} className="research-spin" /> : <Search size={15} />} 搜索安全 tactic 集</ActionButton>
                  </> : null}
                  {binding.status === "KERNEL_CERTIFIED" ? <ActionButton className="research-primary-button" type="button" disabled={busy === `formal-promote-${binding.id}`} onClick={() => void promoteBinding(binding)}>{busy === `formal-promote-${binding.id}` ? <LoaderCircle size={15} className="research-spin" /> : <ShieldCheck size={15} />} 提升证明</ActionButton> : null}
                  {binding.mappingAuthority !== "USER_CONFIRMED" ? <span className="research-dependency-warning"><CircleAlert size={14} /> {binding.mappingAuthority.replaceAll("_", " ")} · 须研究者审阅确认。</span> : null}
                </div>
              </article>;
            }) : <p className="research-muted-copy">请先在证据图中创建定理、引理、命题或猜想节点，再在此绑定其陈述。</p>}
          </div>
        </div>
      ) : null}

      {tab === "search" ? (
        <div><h4>反例与失败路径</h4><div className="reference-result-list">{(() => { const records = graph.nodes.filter(node => node.type === "counterexample" || /failed|failure|失败/i.test(`${node.type} ${node.title}`)); return records.length ? records.map(node => <article key={node.id}><small>{node.type === "counterexample" ? "反例记录" : "失败路径"}</small><MathText text={node.title} />{node.content && node.content !== node.title ? <MathText text={node.content} /> : null}</article>) : <p>目前没有记录反例或失败路径。</p>; })()}</div><details><summary>手动运行搜索</summary><div className="research-tool-grid">
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">可复现有限搜索</span><h4>发现候选结构</h4></div><Search size={17} /></div>
            <p className="research-muted-copy">在有限集合构造空间中，按显式不相容与覆盖约束搜索。结果仅为候选，不能证明无限制猜想。</p>
            <label className="research-field"><span className="research-field-label">有限问题 · JSON</span><textarea className="research-monospace-input" rows={7} value={discoveryProblem} onChange={(event) => setDiscoveryProblem(event.target.value)} /></label>
            <div className="research-control-grid"><label className="research-field"><span className="research-field-label">研究分支</span><select value={discoveryBranch} onChange={(event) => setDiscoveryBranch(event.target.value)}>{branches.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label><label className="research-field"><span className="research-field-label">随机种子</span><input type="number" min={0} max={2147483646} value={discoverySeed} onChange={(event) => setDiscoverySeed(Number(event.target.value))} /></label></div>
            <label className="research-field"><span className="research-field-label">求值预算 · 最多 10,000</span><input type="number" min={1} max={10000} value={discoveryBudget} onChange={(event) => setDiscoveryBudget(Number(event.target.value))} /></label>
            <ActionButton className="research-primary-button" type="button" disabled={busy === "discovery" || project.approvedVersion !== project.currentVersion} onClick={() => void runDiscovery()}>{busy === "discovery" ? <LoaderCircle size={15} className="research-spin" /> : <Workflow size={15} />} 运行有限发现</ActionButton>
            {project.approvedVersion !== project.currentVersion ? <p className="research-dependency-warning"><CircleAlert size={14} /> 请先批准当前计划，再运行研究搜索。</p> : null}
            {discoveryResult ? <article className="research-search-result"><strong>{discoveryResult.status.replaceAll("_", " ")}</strong><small>{discoveryResult.evaluations} / {discoveryResult.evaluationBudget} evaluations · seed {discoveryResult.seed} · {discoveryResult.exhaustive ? "exhaustive" : "bounded"}</small><pre>{JSON.stringify(discoveryResult.bestCandidates.slice(0, 5), null, 2)}</pre><p>{discoveryResult.scope}</p></article> : null}
          </div>
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">精确整数求值</span><h4>搜索反例</h4></div><CircleAlert size={17} /></div>
            <p className="research-muted-copy">在声明的闭区间上比较安全的整数表达式。找到见证即可在该处反驳等式；区间内无反例并不构成证明。</p>
            <label className="research-field"><span className="research-field-label">左表达式</span><input value={counterexampleLeft} onChange={(event) => setCounterexampleLeft(event.target.value)} placeholder="n**2+n+1" /></label>
            <label className="research-field"><span className="research-field-label">右表达式</span><input value={counterexampleRight} onChange={(event) => setCounterexampleRight(event.target.value)} placeholder="0" /></label>
            <div className="research-control-grid"><label className="research-field"><span className="research-field-label">变量</span><input value={counterexampleVariable} onChange={(event) => setCounterexampleVariable(event.target.value)} /></label><label className="research-field"><span className="research-field-label">分支</span><select value={counterexampleBranch} onChange={(event) => setCounterexampleBranch(event.target.value)}>{branches.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label></div>
            <div className="research-control-grid"><label className="research-field"><span className="research-field-label">起始整数</span><input type="number" value={counterexampleStart} onChange={(event) => setCounterexampleStart(Number(event.target.value))} /></label><label className="research-field"><span className="research-field-label">结束整数 · 最多 100,000 次</span><input type="number" value={counterexampleEnd} onChange={(event) => setCounterexampleEnd(Number(event.target.value))} /></label></div>
            <ActionButton className="research-primary-button" type="button" disabled={busy === "counterexample" || project.approvedVersion !== project.currentVersion} onClick={() => void runCounterexampleSearch()}>{busy === "counterexample" ? <LoaderCircle size={15} className="research-spin" /> : <Search size={15} />} 运行反例搜索</ActionButton>
            {counterexampleResult ? <article className="research-search-result"><strong>{counterexampleResult.status.replaceAll("_", " ")}</strong><small>{counterexampleResult.casesChecked} cases · requested {counterexampleResult.requestedRange[0]}…{counterexampleResult.requestedRange[1]} · searched {counterexampleResult.searchRange[0]}…{counterexampleResult.searchRange[1]}</small>{counterexampleResult.value !== undefined ? <pre>{`${counterexampleResult.variable} = ${counterexampleResult.value}\nleft = ${counterexampleResult.leftValue}\nright = ${counterexampleResult.rightValue}`}</pre> : null}<p>{counterexampleResult.scope}</p></article> : null}
          </div>
        </div></details></div>
      ) : null}

      {tab === "literature" ? (
        <div className="research-tool-grid">
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">公开元数据索引</span><h4>搜索文献</h4></div><BookOpen size={17} /></div>
            <label className="research-field"><span className="research-field-label">检索词</span><input value={literatureQuery} onChange={(event) => setLiteratureQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void searchLiterature(); } }} /></label>
            <ActionButton className="research-primary-button" type="button" disabled={busy === "literature" || literatureQuery.trim().length < 3} onClick={() => void searchLiterature()}>{busy === "literature" ? <LoaderCircle size={15} className="research-spin" /> : <Search size={15} />} 搜索公开元数据索引</ActionButton>
            {Object.entries(literatureWarnings).map(([provider, status]) => <p className="research-dependency-warning" key={provider}><CircleAlert size={14} /> {provider}: {status}</p>)}
            {literatureEmptyHint ? <p className="research-muted-copy" role="status"><CircleAlert size={14} /> {literatureEmptyHint}</p> : null}
            <label className="research-field"><span className="research-field-label">索引本地文档</span><input type="file" accept=".pdf,.docx,.txt,.md,.markdown,.tex" onChange={onDocument} /></label>
            <p className="research-muted-copy">支持 PDF、DOCX、TXT、Markdown 或 TeX · 上限 25 MB · 扫描版 PDF 须先 OCR 再索引。</p>
            <div className="research-divider" />
            <label className="research-field"><span className="research-field-label">导入 DOI 元数据</span><input value={doiDraft} onChange={(event) => setDoiDraft(event.target.value)} placeholder="10.1234/example" /></label><ActionButton className="research-secondary-button" type="button" disabled={!doiDraft.trim() || busy === "doi"} onClick={() => void run("doi", async () => { await importLiteratureDoi(project.id, doiDraft.trim()); setDoiDraft(""); await refresh(); setNotice("已导入 DOI 并去重。"); })}>导入 DOI</ActionButton>
            <label className="research-field"><span className="research-field-label">导入个人文献库</span><input type="file" accept=".bib,.json" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void run("library", async () => { await importLiteratureLibrary(project.id, file); await refresh(); setNotice("已导入文献库并去重。"); }); }} /></label>
            <ActionButton type="button" className="research-secondary-button" disabled={busy === "literature-analysis"} onClick={() => void run("literature-analysis", async () => { const result = await analyzeResearchLiterature(project.id); setLiteratureState(result); await refresh(); setNotice("已保存文献与研究空白分析（含检索范围与引用）。"); })}>分析文献与研究空白</ActionButton>
            {literatureState ? <details className="research-literature-record" open><summary>文献现状与研究空白</summary>{Object.entries(literatureState).map(([key, value]) => <div key={key}><strong>{key.replace(/([A-Z])/g, " $1")}</strong><p style={{ whiteSpace: "pre-wrap" }}>{researchDetail(value)}</p></div>)}</details> : null}
          </div>
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">本地索引</span><h4>搜索已索引段落</h4></div><Search size={17} /></div>
            <label className="research-field"><span className="research-field-label">搜索词</span><input value={sourceQuery} onChange={(event) => setSourceQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void searchSources(); } }} /></label>
            <ActionButton type="button" className="research-secondary-button" disabled={busy === "source-search" || sourceQuery.trim().length < 2} onClick={() => void searchSources()}>{busy === "source-search" ? <LoaderCircle size={15} className="research-spin" /> : <Search size={15} />} 搜索项目来源</ActionButton>
            {sourceHits.map((hit) => <article className="research-source-hit" key={hit.id}><strong>{hit.title}</strong><small>{hit.section}{hit.page_number ? ` · page ${hit.page_number}` : ""}</small><p>{hit.content}</p></article>)}
            <div className="research-literature-coverage"><strong>检索覆盖 · 非新颖性主张</strong><small>{sources.filter((item) => item.type === "literature").length} 条元数据 · {sources.filter((item) => item.classification === "direct").length} 条直接匹配 · {sources.filter((item) => item.classification === "survey").length} 篇综述 · {sources.filter((item) => item.important || item.readingStatus === "important").length} 条标为重要</small></div>
            <label className="research-field"><span className="research-field-label">筛选已保存文献</span><input value={sourceFilter} onChange={(event) => { setSourceFilter(event.target.value); setSourcePage(1); }} placeholder="标题、作者、分类、阅读状态" /></label>
            <div className="research-source-list"><h5>已保存来源 <span>{sources.length}</span></h5>{visibleSources.map((source) => <details key={source.id} className="research-literature-record"><summary><span>{source.type} · {source.classification || "background"} · {source.readingStatus || "unread"}</span><a href={source.url || undefined} target={source.url ? "_blank" : undefined} rel="noreferrer">{source.title}</a><small>{source.authors}{source.year ? ` · ${source.year}` : ""}{source.doi ? ` · DOI ${source.doi}` : ""}{source.provider ? ` · ${source.provider}` : ""}{source.chunkCount ? ` · ${source.chunkCount} chunks` : ""} · retrieved {source.lastSeenAt || source.firstSeenAt || "日期不可用"}</small></summary>
              <div className="research-literature-curation">
                <label className="research-field"><span className="research-field-label">审阅状态</span><select value={source.readingStatus === "important" ? "unread" : source.readingStatus || "unread"} disabled={busy === `curate-${source.id}`} onChange={(event) => void curateSource(source, { reading_status: event.target.value as ResearchSource["readingStatus"] })}><option value="unread">未读</option><option value="read">已读</option><option value="irrelevant">无关</option></select></label>
                <label className="research-confirm-mapping"><input type="checkbox" checked={Boolean(source.important || source.readingStatus === "important")} disabled={busy === `curate-${source.id}`} onChange={(event) => void curateSource(source, { important: event.target.checked })} /><span>重要文献</span></label>
                <label className="research-field"><span className="research-field-label">分类</span><select value={source.classification || "background"} disabled={busy === `curate-${source.id}`} onChange={(event) => void curateSource(source, { classification: event.target.value })}>{Object.entries(CLASSIFICATION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                <label className="research-field"><span className="research-field-label">研究者备注</span><textarea rows={2} defaultValue={source.notes || ""} onBlur={(event) => { if (event.currentTarget.value !== (source.notes || "")) void curateSource(source, { notes: event.currentTarget.value }); }} placeholder="为何重要、支持或反驳某命题" /></label>
                <div className="research-control-grid"><label className="research-field"><span className="research-field-label">关联到命题、证据或分支</span><select value={literatureLinkTargets[source.id] || ""} onChange={(event) => setLiteratureLinkTargets((current) => ({ ...current, [source.id]: event.target.value }))}><option value="">选择研究记录</option>{graph.nodes.filter((node) => ["lemma", "conjecture", "proposition", "theorem", "evidence", "experiment"].includes(node.type)).map((node) => <option key={node.id} value={node.id}>{node.type}: {node.title}</option>)}{branches.map((branch) => <option key={branch.id} value={`branch:${branch.name}`}>分支：{branch.name}</option>)}</select></label><label className="research-field"><span className="research-field-label">文献关系</span><select value={literatureLinkTypes[source.id] || "RELATED_TO"} onChange={(event) => setLiteratureLinkTypes((current) => ({ ...current, [source.id]: event.target.value as "SUPPORTS" | "CONTRADICTS" | "USES" | "RELATED_TO" | "CITES" }))}>{Object.entries(LIT_RELATION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><ActionButton type="button" className="research-secondary-button" disabled={!literatureLinkTargets[source.id] || busy === `link-${source.id}`} onClick={() => void linkSource(source)}>关联记录</ActionButton></div>
                <div className="research-inline-actions"><ActionButton className="research-text-button" type="button" disabled={busy === `note-${source.id}`} onClick={() => void loadNote(source)}>加载研究笔记</ActionButton><ActionButton className="research-text-button" type="button" disabled={busy === `note-${source.id}`} onClick={() => void loadNote(source, true)}>生成结构化笔记</ActionButton></div>
                {noteDrafts[source.id] ? <div><p>来源覆盖：{noteProvenance[source.id]?.fullTextAvailable ? "已索引全文" : "仅元数据与摘要"} · {String(noteProvenance[source.id]?.status || "研究者笔记")}</p>{noteProvenance[source.id]?.validationWarnings ? <p>{researchDetail(noteProvenance[source.id].validationWarnings)}</p> : null}{Object.entries(noteDrafts[source.id]).map(([field, value]) => <label className="research-field" key={field}><span className="research-field-label">{field.replace(/([A-Z])/g, " $1").replaceAll("_", " ")}</span><textarea rows={3} value={value} onChange={(event) => setNoteDrafts((current) => ({ ...current, [source.id]: { ...current[source.id], [field]: event.target.value } }))} /></label>)}<details><summary>笔记引用与证据</summary><p style={{ whiteSpace: "pre-wrap" }}>{researchDetail(noteProvenance[source.id]?.citations)}</p><p style={{ whiteSpace: "pre-wrap" }}>{researchDetail(noteProvenance[source.id]?.fieldEvidence)}</p></details><ActionButton className="research-secondary-button" type="button" disabled={busy === `note-${source.id}`} onClick={() => void run(`note-${source.id}`, async () => { const saved = await saveLiteratureNote(project.id, source.id, noteDrafts[source.id]); setNoteProvenance((current) => ({ ...current, [source.id]: saved })); setNotice("已保存编辑后的研究笔记。"); })}>保存研究笔记</ActionButton></div> : null}
                {source.abstract ? <p>{source.abstract}</p> : null}
              </div>
            </details>)}</div>
            <div className="research-inline-actions"><ActionButton className="research-text-button" type="button" disabled={sourcePage <= 1} onClick={() => setSourcePage((page) => page - 1)}>上一页</ActionButton><small>{filteredSources.length} 条 · 第 {Math.min(sourcePage, sourcePages)} / {sourcePages}</small><ActionButton className="research-text-button" type="button" disabled={sourcePage >= sourcePages} onClick={() => setSourcePage((page) => page + 1)}>下一页</ActionButton></div>
            {literatureGraph ? <details className="research-literature-record"><summary>文献图谱 · {literatureGraph.nodes.length} 节点 · {literatureGraph.edges.length} 条关系</summary>{literatureGraph.nodes.map((node, index) => <p key={String(node.id || index)}>{researchDetail(node)}</p>)}{literatureGraph.edges.map((edge, index) => <p key={String(edge.id || index)}>{researchDetail(edge)}</p>)}</details> : null}
            {literatureItems.length ? <div className="research-literature-results">{literatureItems.map((item) => <article key={item.id}><strong>{item.title}</strong><small>{item.authors}{item.year ? ` · ${item.year}` : ""}</small>{item.url ? <a href={item.url} target="_blank" rel="noreferrer">打开索引记录</a> : null}<p>{item.abstract}</p></article>)}</div> : null}
          </div>
        </div>
      ) : null}

      {tab === "branches" ? (
        <div className="research-tool-grid">
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">并行研究</span><h4>分支</h4></div><GitBranch size={17} /></div>
            <label className="research-field"><span className="research-field-label">分支决策理由</span><input value={branchReason} onChange={(event) => setBranchReason(event.target.value)} /></label>
            <div className="research-branch-cards">{branches.map((branch) => <article key={branch.id}><strong>{branch.name}</strong><small>{branch.parentName ? `来自 ${branch.parentName}` : "根路径"} · {branch.status}</small><p>求值 {branch.used.evaluations}/{branch.limits.evaluations} · 证明尝试 {branch.used.proofAttempts}/{branch.limits.proofAttempts} · 工具秒数 {branch.used.toolSeconds}/{branch.limits.toolSeconds}</p><div className="research-inline-actions"><ActionButton className="research-text-button" type="button" disabled={busy === `branch-${branch.id}`} onClick={() => void changeBranchState(branch, branch.status.toLowerCase() === "closed" ? "active" : "closed")}>{branch.status.toLowerCase() === "closed" ? "重新打开分支" : "关闭分支"}</ActionButton><ActionButton className="research-text-button" type="button" disabled={busy === `branch-${branch.id}`} onClick={() => void changeBranchState(branch, "paused")}>暂停分支</ActionButton><label className="research-field"><span className="research-field-label">优先级</span><select defaultValue="3" onChange={(event) => void changeBranchState(branch, branch.status, Number(event.target.value))}>{[1, 2, 3, 4, 5].map((priority) => <option key={priority} value={priority}>{priority}</option>)}</select></label></div></article>)}</div>
            <div className="research-control-grid"><label className="research-field"><span className="research-field-label">新分支名称</span><input value={branchName} onChange={(event) => setBranchName(event.target.value)} /></label><label className="research-field"><span className="research-field-label">父分支</span><select value={parentBranch} onChange={(event) => setParentBranch(event.target.value)}>{branches.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label></div>
            <ActionButton className="research-primary-button" type="button" disabled={busy === "branch" || !branchName.trim()} onClick={() => void addBranch()}>{busy === "branch" ? <LoaderCircle size={15} className="research-spin" /> : <Plus size={15} />} 创建分支</ActionButton>
          </div>
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">资源配额</span><h4>分支预算</h4></div><Archive size={17} /></div>
            <label className="research-field"><span className="research-field-label">分支</span><select value={budgetBranch} onChange={(event) => { const name = event.target.value; setBudgetBranch(name); const found = branches.find((item) => item.name === name); if (found) setBudgetDraft(found.limits); }}>{branches.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label>
            {(["evaluations", "proofAttempts", "toolSeconds"] as const).map((key) => <label className="research-field" key={key}><span className="research-field-label">{key === "evaluations" ? "求值次数" : key === "proofAttempts" ? "证明尝试" : "工具秒数"} · used {selectedBudgetBranch?.used[key] ?? 0}</span><input type="number" min={selectedBudgetBranch?.used[key] ?? 0} value={budgetDraft[key]} onChange={(event) => setBudgetDraft((current) => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}
            <ActionButton className="research-secondary-button" type="button" disabled={busy === "budget"} onClick={() => void saveBudget()}>{busy === "budget" ? <LoaderCircle size={15} className="research-spin" /> : <Check size={15} />} 保存资源限额</ActionButton>
            <div className="research-divider" />
            <div className="research-control-grid"><label className="research-field"><span className="research-field-label">对比</span><select value={compareLeft} onChange={(event) => setCompareLeft(event.target.value)}>{branches.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label><label className="research-field"><span className="research-field-label">与</span><select value={compareRight} onChange={(event) => setCompareRight(event.target.value)}>{branches.filter((item) => item.name !== compareLeft).map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}</select></label></div>
            <ActionButton className="research-secondary-button" type="button" disabled={busy === "compare" || branches.length < 2} onClick={() => void compare()}><GitBranch size={15} /> 对比分支</ActionButton>
            {comparison ? <div className="research-comparison"><p><strong>{compareLeft}</strong>: {(comparison[compareLeft] as { artifacts?: unknown[] } | undefined)?.artifacts?.length ?? 0} 个产物 · <strong>{compareRight}</strong>: {(comparison[compareRight] as { artifacts?: unknown[] } | undefined)?.artifacts?.length ?? 0} 个产物</p><small>差异： {JSON.stringify(comparison.differences)}</small></div> : null}
          </div>
        </div>
      ) : null}

      {tab === "memory" ? (
        <div className="research-tool-grid">
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">持久研究语境</span><h4>保存研究记忆</h4></div><BookOpen size={17} /></div>
            <label className="research-field"><span className="research-field-label">笔记</span><textarea rows={5} value={memoryDraft} onChange={(event) => setMemoryDraft(event.target.value)} /></label>
            <label className="research-field"><span className="research-field-label">范围</span><select value={memoryScope} onChange={(event) => setMemoryScope(event.target.value as "project" | "workspace")}><option value="project">本项目</option><option value="workspace">全部数学研究项目</option></select></label>
            <ActionButton className="research-primary-button" type="button" disabled={busy === "memory" || !memoryDraft.trim()} onClick={() => void addMemory()}><Plus size={15} /> 保存待确认</ActionButton>
          </div>
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">人工整理</span><h4>已保存记忆</h4></div><ShieldCheck size={17} /></div>
            <div className="research-memory-list">{memories.length ? memories.map((memory) => <article key={memory.id}><p>{memory.content}</p><small>{memory.projectId ? "项目" : "工作区"}范围 · {memory.confirmed ? "已确认" : "未确认"}</small><ActionButton type="button" className="research-text-button" disabled={busy === `memory-${memory.id}`} onClick={() => void setMemoryConfirmed(memory)}>{memory.confirmed ? "撤销确认" : "确认记忆"}</ActionButton></article>) : <p className="research-muted-copy">暂无已保存记忆。模型生成的摘要须经您确认后才会再次使用。</p>}</div>
          </div>
        </div>
      ) : null}

      {tab === "delivery" ? (
        <div className="research-tool-grid">
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">数据保全</span><h4>导入 Math Research Agent</h4></div><Archive size={17} /></div>
            <p className="research-muted-copy">将项目、记录、活动与已索引段落导入 A-leggo 存储。导入经哈希校验且幂等。源数据库不变；提供商凭证字段会被排除。</p>
            {isAdmin ? <ActionButton className="research-primary-button" type="button" disabled={busy === "import"} onClick={() => void importExisting()}>{busy === "import" ? <LoaderCircle size={15} className="research-spin" /> : <Archive size={15} />} 导入本地 MRA 工作区</ActionButton> : <p className="research-dependency-warning"><CircleAlert size={14} /> 导入本地 MRA 工作区需要管理员权限。</p>}
            <label className="research-field research-file-import"><span className="research-field-label">导入数据库文件</span><input type="file" accept=".sqlite,.sqlite3,.db" onChange={importFile} /></label>
            {importSummary ? <div className="research-migration-summary"><strong>{String(importSummary.alreadyImported ? "已有导入" : "导入完成")}</strong><p>{String(importSummary.projects)} 个项目 · {String(importSummary.records)} 条记录 · {String(importSummary.activities)} 条活动 · {String(importSummary.documentChunks)} 段索引</p><small>{String(importSummary.credentialsExcluded)} 个凭证字段已排除 · SHA-256 {String(importSummary.sourceHash).slice(0, 16)}…</small></div> : null}
            {project.id.startsWith("mra_") ? <ActionButton className="research-secondary-button" type="button" disabled={busy === "legacy"} onClick={() => void loadLegacyRecords()}><Archive size={15} /> 加载已导入记录</ActionButton> : null}
            {legacyRecords.length ? <div className="research-legacy-records">{legacyRecords.map((item) => <details key={`${item.sourceTable}:${item.id}`}><summary>{item.collection || item.sourceTable} · {item.id}</summary><pre>{typeof item.payload === "string" ? item.payload.slice(0, 1400) : JSON.stringify(item.payload, null, 2).slice(0, 1400)}</pre></details>)}</div> : null}
          </div>
          <div className="research-tool-form">
            <div className="research-card-heading"><div><span className="research-eyebrow">可移植研究记录</span><h4>导出项目报告</h4></div><ArrowDownToLine size={17} /></div>
            <p className="research-muted-copy">包含已批准计划与修订、带证据状态的智能体输出、工具台账、证据图、已索引来源、研究者干预与不确定性边界。</p>
            <ActionButton className="research-secondary-button" type="button" disabled={busy === "export-markdown"} onClick={() => void exportReport("markdown")}>{busy === "export-markdown" ? <LoaderCircle size={15} className="research-spin" /> : <FileText size={15} />} 下载 Markdown 报告</ActionButton>
            <ActionButton className="research-secondary-button" type="button" disabled={busy === "export-pdf"} onClick={() => void exportReport("pdf")}>{busy === "export-pdf" ? <LoaderCircle size={15} className="research-spin" /> : <ArrowDownToLine size={15} />} 下载 PDF 报告</ActionButton>
            <ActionButton className="research-secondary-button" type="button" disabled={busy === "export-latex"} onClick={() => void exportReport("latex")} title="可编辑论文骨架：命题保留其记录的证据状态">{busy === "export-latex" ? <LoaderCircle size={15} className="research-spin" /> : <FileText size={15} />} 下载 LaTeX 论文草稿</ActionButton>
            <div className="research-divider" />
            <h5>本机导入记录</h5>
            <p className="research-muted-copy">执行导入后，此处会显示幂等键与来源计数。</p>
          </div>
        </div>
      ) : null}
    </section>
  );
}
