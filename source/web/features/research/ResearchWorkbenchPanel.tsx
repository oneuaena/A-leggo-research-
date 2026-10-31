"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { useCallback, useEffect, useState } from "react";
import { ArrowDownToLine, BookOpen, CircleAlert, FileCheck2, FolderOpen, GitBranch, LoaderCircle, Pause, Play, RefreshCw, Square, WandSparkles } from "@/shared/ui/glyphs";
import {
  decideResearchRecommendation, downloadResearchArtifact, exportReproducibilityPackage,
  getResearchWorkbench, openResearchWorkspace, resumeResearchAnyway, researchAction,
  setResearchSupervision, setResearchWorkspace, syncResearchWorkspace,
  openResearchArtifact, giveArtifactFeedback, addResearchIntervention, restoreResearchWorkspace,
  type ResearchArtifact, type ResearchProject, type ResearchWorkbenchSnapshot, type ResearchPlan, type ResearchArtifactFormat,
} from "@/lib/research-api";
import { hasResearchFolderPicker, pickResearchFolder } from "@/lib/research-native";
import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import MathText from "./MathText";
import dynamic from "next/dynamic";

const ResearchFilesPanel = dynamic(() => import("./ResearchFilesPanel"), { ssr: false });

function summaryValue(value: unknown): string {
  if (value == null || value === "") return "暂无记录";
  if (Array.isArray(value)) return value.map(summaryValue).join("\n") || "暂无记录";
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${summaryValue(item)}`).join(" · ");
  return String(value);
}

const RECOMMENDATION_ACTION_ZH: Record<string, string> = {
  accept: "接受", modify: "修改", reject: "拒绝", alternative: "替代路线", add_human_idea: "补充人工想法",
};

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function readableElapsed(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}

function downloadName(value: string, fallback: string) {
  return value.replace(/[\\/:*?"<>|]+/g, "-").slice(0, 100) || fallback;
}

export default function ResearchWorkbenchPanel({
  project,
  onEditPlan,
  onRefresh,
  onProposedPlan,
  onRestored,
  showIntervention = true,
}: {
  project: ResearchProject;
  onEditPlan: () => void;
  onRefresh: () => Promise<void>;
  onProposedPlan: (plan: ResearchPlan) => void;
  onRestored: (project: ResearchProject) => void;
  showIntervention?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<ResearchWorkbenchSnapshot | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [folderDraft, setFolderDraft] = useState("");
  const [restoreName, setRestoreName] = useState("");
  const [nativeFolderPicker, setNativeFolderPicker] = useState(false);
  const [interventionMode, setInterventionMode] = useState("note");
  const [alternativeAction, setAlternativeAction] = useState("idea");
  const [reviewingArtifact, setReviewingArtifact] = useState("");
  const [artifactQuery, setArtifactQuery] = useState("");
  const [artifactPage, setArtifactPage] = useState(1);
  const [interventionKind, setInterventionKind] = useState("human_note");
  const [interventionText, setInterventionText] = useState("");
  const [interventionBranch, setInterventionBranch] = useState("");
  const [decisionReason, setDecisionReason] = useState("");
  const [decisionAction, setDecisionAction] = useState<"modify" | "reject" | "">("");
  const [timelineLimit, setTimelineLimit] = useState(8);
  const [showSystem, setShowSystem] = useState(false);
  const [detailTab, setDetailTab] = useState("artifacts");

  const refresh = useCallback(async () => {
    const next = await getResearchWorkbench(project.id);
    setSnapshot(next);
    setLoadError("");
  }, [project.id]);

  useEffect(() => {
    let active = true;
    const load = () => getResearchWorkbench(project.id).then((next) => { if (active) { setSnapshot(next); setLoadError(""); } }).catch((cause) => { if (active) setLoadError(cause instanceof Error ? cause.message : "无法加载研究工作台状态。"); });
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [project.id]);

  useEffect(() => {
    setNativeFolderPicker(hasResearchFolderPicker());
  }, []);

  useEffect(() => { setSnapshot(null); setError(""); setLoadError(""); setNotice(""); setReviewingArtifact(""); setArtifactPage(1); setArtifactQuery(""); setInterventionBranch(""); }, [project.id]);

  const run = async (key: string, action: () => Promise<unknown>, message?: string) => {
    setBusy(key); setError(""); setNotice("");
    try {
      await action();
      await Promise.all([refresh(), onRefresh()]);
      if (message) setNotice(message);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "研究服务未能完成该操作。");
    } finally { setBusy(""); }
  };

  const chooseFolder = async () => {
    setError("");
    if (!hasResearchFolderPicker()) {
      setError("请使用已安装的桌面应用在本机选择文件夹。浏览器路径指向的是运行 A-leggo 后端的那台机器。");
      return;
    }
    try {
      const path = await pickResearchFolder();
      if (!path) return;
      setFolderDraft(path);
      await run("workspace", () => setResearchWorkspace(project.id, path), "项目工作区已选定并同步。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法打开文件夹选择器。");
    }
  };

  const downloadArtifact = (artifact: ResearchArtifact, format: ResearchArtifactFormat = "markdown") => run(`artifact-${artifact.id}`, async () => {
    saveBlob(await downloadResearchArtifact(project.id, artifact.id, format), `${downloadName(artifact.title, "research-artifact")}.${format === "markdown" ? "md" : format === "text" ? "txt" : "json"}`);
  }, "构件已下载。");

  const acceptRecommendation = (id: string, action: "accept" | "modify" | "reject" | "alternative" | "add_human_idea", alternative = "") => run(`recommendation-${id}`, async () => {
    const modified = ["alternative", "add_human_idea"].includes(action) && alternative.trim()
      ? { summary: "研究者提供了替代路线。", primary: [{ path: alternative.trim(), reason: "研究者撰写的替代方案。" }], secondary: [], deprioritize: [], source: "RESEARCHER" }
      : undefined;
    const result = await decideResearchRecommendation(project.id, id, action, decisionReason || (action === "alternative" ? "研究者提出了另一条路线。" : `研究者选择了${RECOMMENDATION_ACTION_ZH[action] ?? action}。`), modified);
    if (result.proposedPlan) onProposedPlan(result.proposedPlan);
    else if (action === "modify") onEditPlan();
  }, action === "reject" ? "已记录拒绝。" : "候选路线已载入待审阅。保存并批准计划修订后即可执行。");

  const submitIntervention = () => run("intervention", async () => {
    const kind = interventionMode === "note" ? "human_note" : interventionMode === "condition" ? interventionKind : interventionMode === "alternative" || interventionMode === "plan" ? "add_human_idea" : interventionKind;
    if (interventionMode === "alternative" && alternativeAction === "recommendation" && latest) {
      await addResearchIntervention(project.id, "add_human_idea", { message: interventionText.trim(), note: interventionText.trim(), branch: interventionBranch || snapshot?.state.currentBranch || "main" });
      await decideResearchRecommendation(project.id, latest.id, "alternative", "研究者提出替代路线。", { summary: "研究者提供了替代路线。", primary: [{ path: interventionText.trim(), reason: "研究者撰写的替代方案。" }], secondary: [], deprioritize: [], source: "RESEARCHER" }).then((result) => { if (result.proposedPlan) onProposedPlan(result.proposedPlan); });
      setInterventionText("");
      return;
    }
    const result = await addResearchIntervention(project.id, kind, { message: interventionText.trim(), note: interventionText.trim(), branch: interventionBranch || snapshot?.state.currentBranch || "main" });
    if (result.proposedPlan) onProposedPlan(result.proposedPlan);
    setInterventionText("");
  }, interventionKind.startsWith("request_") ? "研究动作已排队，将在下一个安全步骤边界执行。" : "研究者留言已保存。");

  const restorePackage = async (file: File) => {
    setBusy("restore"); setError(""); setNotice("");
    try { onRestored(await restoreResearchWorkspace(file)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "无法恢复研究工作区。"); }
    finally { setBusy(""); }
  };

  if (!snapshot) return <section className="research-workbench-panel" aria-label="研究工作台">{loadError || error ? <><p className="research-error" role="alert"><CircleAlert size={14} /> {error || loadError}</p><ActionButton type="button" className="research-secondary-button" disabled={busy === "refresh"} onClick={() => void run("refresh", refresh)}>重新加载研究状态</ActionButton></> : <div className="research-panel-loading"><LoaderCircle size={16} className="research-spin" /> 正在加载研究状态…</div>}</section>;
  const state = snapshot.state;
  const missingInput = Boolean(project.inputRequests?.some((request) => request.status === "OPEN"));
  const artifacts = project.artifacts ?? snapshot.project?.artifacts ?? [];
  const filteredArtifacts = artifacts.filter((item) => `${item.title} ${item.agentRole} ${item.stepId} ${item.status}`.toLocaleLowerCase().includes(artifactQuery.toLocaleLowerCase()));
  const artifactPages = Math.max(1, Math.ceil(filteredArtifacts.length / 12));
  const visibleArtifacts = filteredArtifacts.slice((Math.min(artifactPage, artifactPages) - 1) * 12, Math.min(artifactPage, artifactPages) * 12);
  // A pending recommendation may remain in storage after a newer recommendation arrives.
  const recommendations = [...snapshot.recommendations].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
  const latest = recommendations[0];
  const pendingRecommendations = latest?.status === "PENDING" ? [latest] : [];
  const olderRecommendations = recommendations.slice(1);
  const summaryText = latest?.content.summary || "";
  const candidates = (project.graph?.nodes ?? []).filter((node) => node.type === "counterexample" && !["invalid", "rejected"].includes(node.status.toLowerCase()));
  const keyFormula = candidates.length ? summaryText.match(/\$\$([\s\S]*?)\$\$/)?.[0] : undefined;
  const candidateText = candidates.map((node) => node.content.match(/\b[a-z]\s*=\s*-?\d+/i)?.[0]).find(Boolean);
  const outstanding = summaryText.match(/[^。；：]*第二套[^。；]*/)?.[0];
  const confirmedCounterexample = candidates.some((node) => node.status.toLowerCase() === "verified" && node.metadata?.evidenceClass === "COUNTEREXAMPLE_FOUND");
  const confirmedProof = (project.formalBindings || []).some((binding) => binding.mappingAuthority === "USER_CONFIRMED" && binding.status === "KERNEL_CERTIFIED" && project.graph?.nodes.some((node) => node.id === binding.claimNodeId && node.metadata?.isMainClaim === true));
  const bounded = (project.graph?.nodes || []).some((node) => node.metadata?.evidenceClass === "BOUNDED_CHECK" && node.metadata?.isMainClaim === true && node.status.toLowerCase() === "verified");
  const conclusionStatus = confirmedCounterexample ? "已否定" : candidates.length ? "有候选反例" : confirmedProof ? "已证明" : bounded ? "有界范围已验证" : "未定";
  const sources = (project.sources || []).filter((item) => item.type === "literature").sort((a, b) => (b.relevance || 0) - (a.relevance || 0));
  const displayText = (value: string) => value.replace(/step_\d+_[a-z0-9]+/gi, (id) => {
    const index = project.plan?.steps.findIndex((step) => step.id === id) ?? -1;
    return index >= 0 ? `步骤 ${index + 1}` : "研究步骤";
  }).replace(/\bstep_(\d+)\b/gi, "步骤 $1").replace(/\bverificationStrategy\b/g, "验证策略").replace(/\bretryCondition\b/g, "重试条件").replace(/\bformalBindingId\b/g, "形式化绑定").replace(/\bunverified\b/gi, "待验证").replace(/\bnot applicable\b/gi, "不适用").replace(/\bcounterexampleStrategy\b/g, "反例检验策略").replace(/\bBOUNDED_CHECK\b/g, "有限范围验证").replace(/\bCONJECTURAL\b/g, "待验证").replace(/\bREJECTED_ROUTE\b/g, "已拒绝路线").replace(/\b(?:budget-guard|milestone-controller|research-synthesizer)\b/gi, "研究系统");
  const shortPath = (value: string) => value.split(/[：:]/, 1)[0].replace(/\bstep_(\d+)\b/gi, "步骤 $1");
  const firstReason = (value: string) => value.match(/^.*?[。！？]/s)?.[0] || value;
  const removeSourceList = (value: string) => {
    let result = value.replace(/引用索引文献[（(][^）)]*[）)]/g, "引用低相关检索结果");
    for (const source of sources) if (source.title.length > 12) result = result.split(source.title).join("低相关检索结果");
    return result;
  };
  const relevant = sources.filter((item) => (item.relevance || 0) >= 0.5);
  const lowRelevance = sources.filter((item) => (item.relevance || 0) < 0.5);
  const nodes = project.graph?.nodes || [];
  const readableTitle = (artifact: ResearchArtifact) => {
    const step = project.plan?.steps.find((item) => item.id === artifact.stepId);
    if (!step) return /^Milestone/i.test(artifact.title) ? "里程碑成果" : /^Researcher note/i.test(artifact.title) ? "研究者备注" : artifact.title.replace(/step_\d+_[a-z0-9]+/gi, "研究步骤");
    const label = /factorization audit/i.test(step.title) ? "小范围精确因式分解审计" : /candidate counterexample/i.test(step.title) ? "候选反例验证" : /formaliz/i.test(step.title) ? "命题与定义域的形式化" : /modular/i.test(step.title) ? "模运算与因子规律" : step.title;
    return `步骤 ${step.order + 1} · ${label}`;
  };
  const relativeTime = (value: string) => {
    const delta = Math.floor((Date.now() - Date.parse(value)) / 1000);
    if (!Number.isFinite(delta)) return "时间未记录";
    if (delta < 60) return "刚刚";
    if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
    if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
    return `${Math.floor(delta / 86400)} 天前`;
  };
  const stoppedReason = state.statusReason === "STOPPED" ? "由研究者手动停止" : state.statusReason === "BLOCKED" || state.statusReason === "STALLED" ? `遇到阻塞：${state.blockerDetail || "请查看研究记录"}` : state.statusReason === "RESEARCHER_PAUSED" ? "由研究者手动暂停" : ["MILESTONE_REVIEW_REQUIRED", "STEP_REVIEW_REQUIRED"].includes(state.statusReason) ? "研究已暂停，等待研究者审阅" : project.status === "PAUSED" ? "研究已暂停" : project.status === "COMPLETED" ? "研究已完成" : "研究尚未结束";

  return (
    <section className="research-workbench-panel" aria-label="研究工作台">
      <details className="research-settings-drawer"><summary aria-label="项目设置" title="项目设置">⚙</summary><div className="research-settings-content"><h4>项目设置</h4>
        <label className="research-field"><span className="research-field-label">监督方式</span>
          <select value={state.supervisionMode} disabled={busy === "supervision" || project.status === "RUNNING" || project.status === "VERIFYING"}
            onChange={(event) => void run("supervision", () => setResearchSupervision(project.id, event.target.value as ResearchWorkbenchSnapshot["state"]["supervisionMode"]), "监督方式已保存。")}>
            <option value="AUTONOMOUS">自主运行，遇到阻塞时暂停</option>
            <option value="MILESTONE_REVIEW">里程碑审阅</option>
            <option value="STEP_BY_STEP">逐步审阅</option>
          </select>
        </label>
      <div className="research-workspace-folder">
        <div><span className="research-eyebrow">本地工作区</span><p title={snapshot.workspacePath}>{snapshot.workspacePath || "尚未选择本地工作区"}</p></div>
        <div className="research-inline-actions">
          <ActionButton className="research-secondary-button" type="button" disabled={busy === "workspace"} onClick={() => void chooseFolder()}><FolderOpen size={14} /> 选择文件夹</ActionButton>
          <ActionButton className="research-text-button" type="button" disabled={!snapshot.workspacePath} onClick={() => void run("open-folder", () => openResearchWorkspace(project.id))}>打开文件夹</ActionButton>
          <ActionButton className="research-text-button" type="button" disabled={!snapshot.workspacePath} onClick={() => void run("sync-folder", () => syncResearchWorkspace(project.id), "工作区已同步。")}>同步</ActionButton>
        </div>
        {!nativeFolderPicker ? <div className="research-folder-fallback"><input aria-label="本地工作区路径" value={folderDraft} onChange={(event) => setFolderDraft(event.target.value)} placeholder="输入后端所在设备上的文件夹路径" /><ActionButton type="button" className="research-text-button" disabled={!folderDraft.trim()} onClick={() => void run("workspace", () => setResearchWorkspace(project.id, folderDraft.trim()), "工作区已选择并同步。")}>使用此路径</ActionButton></div> : null}
        <small>工作区用于保存研究文件。</small>
        <label className="research-restore-button">选择研究工作区包<input aria-label="选择研究工作区包" type="file" accept=".zip" disabled={busy === "restore"} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) { setRestoreName(file.name); void restorePackage(file); } }} /></label><small>{restoreName || "请选择要恢复的压缩包。"}</small>
      </div>

      </div></details>
      <section className="research-conclusion-card" aria-label="研究结论">
        <div className="research-card-heading"><div><span className="research-status-pill">{conclusionStatus}</span> <small>证据等级：{confirmedCounterexample ? "反例已验证" : confirmedProof ? "形式化验证" : bounded ? "有限范围验证" : candidates.length ? "待独立复核" : "暂无可确认的结论"}</small></div><ActionButton type="button" className="research-icon-button" onClick={() => void run("refresh", refresh)} aria-label="刷新研究状态"><RefreshCw size={15} /></ActionButton></div>
        {candidateText ? <MathText text={`候选反例：${candidateText}`} /> : <p>目前没有可确认的命题结论。</p>}
        {keyFormula ? <MathText text={keyFormula} /> : null}
        {candidates.length ? <MathText text={outstanding || "尚需核对候选反例及其验证条件。"} /> : null}
        <p>{stoppedReason}</p>
        <div className="research-inline-actions">
          {project.status === "PAUSED" && !missingInput && project.approvedVersion === project.currentVersion ? <ActionButton className="research-primary-button" type="button" onClick={() => void run("resume", () => ["STALLED", "BLOCKED"].includes(state.statusReason) ? resumeResearchAnyway(project.id) : researchAction(project.id, "resume"))}>继续研究</ActionButton> : null}
          {project.status === "CANCELLED" && !missingInput && project.approvedVersion === project.currentVersion ? <ActionButton className="research-primary-button" type="button" onClick={() => void run("start", () => researchAction(project.id, "start"))}>继续研究</ActionButton> : null}
          <ActionButton className="research-secondary-button" type="button" disabled={busy === "package"} onClick={() => void run("package", async () => saveBlob(await exportReproducibilityPackage(project.id), `${downloadName(project.title, "research")}-reproducibility.zip`), "最终包已下载。")}><ArrowDownToLine size={14} />下载最终包</ActionButton>
        </div>
      </section>
      <section className="research-path-recommendations" id="research-recommendations">
        <div className="research-card-heading"><h4>下一步建议</h4></div>
        {pendingRecommendations.map((item) => <article key={item.id} className="research-recommendation-card">
          {item.content.primary?.[0] ? <><MathText text={displayText(shortPath(removeSourceList(item.content.primary[0].path)))} /><MathText text={displayText(firstReason(removeSourceList(item.content.primary[0].reason)))} /></> : <p>当前没有首选建议。</p>}
          <div className="research-inline-actions">
            <ActionButton type="button" className="research-primary-button" disabled={busy === `recommendation-${item.id}`} onClick={() => void acceptRecommendation(item.id, "accept")}>采纳</ActionButton>
            <ActionButton type="button" className="research-secondary-button" onClick={() => setDecisionAction("modify")}>修改</ActionButton>
            <ActionButton type="button" className="research-text-button" onClick={() => setDecisionAction("reject")}>拒绝</ActionButton>
          </div>
          {decisionAction ? <div className="research-field"><label>决定理由（可选）<input value={decisionReason} onChange={(event) => setDecisionReason(event.target.value)} placeholder="说明修改或拒绝的理由" /></label><div className="research-inline-actions"><ActionButton type="button" disabled={busy === `recommendation-${item.id}`} onClick={() => { void acceptRecommendation(item.id, decisionAction); setDecisionAction(""); }}>{decisionAction === "modify" ? "确认修改" : "确认拒绝"}</ActionButton><ActionButton type="button" onClick={() => setDecisionAction("")}>取消</ActionButton></div></div> : null}
          <details><summary>查看全部建议</summary><MathText text={displayText(removeSourceList(item.content.summary || "暂无补充说明。"))} />{(["primary", "secondary", "deprioritize"] as const).map((key) => <div key={key}><strong>{key === "primary" ? "首选" : key === "secondary" ? "备选" : "暂缓"}</strong>{item.content[key]?.map((path, index) => <div key={`${key}-${index}`}><MathText text={displayText(removeSourceList(path.path))} /><MathText text={displayText(removeSourceList(path.reason))} /></div>)}</div>)}{item.content.uncertainty ? <MathText text={displayText(removeSourceList(item.content.uncertainty))} /> : null}</details>
        </article>)}
        {!pendingRecommendations.length ? <p>当前没有待审阅的最新建议。</p> : null}
        {olderRecommendations.length ? <details><summary>历史建议（{olderRecommendations.length}，已被取代）</summary>{olderRecommendations.map((item) => <article key={item.id}><small>已被取代 · {new Date(item.createdAt).toLocaleString("zh-CN")}</small><MathText text={displayText(removeSourceList(item.content.summary || "历史研究建议"))} />{(["primary", "secondary", "deprioritize"] as const).map((key) => item.content[key]?.map((path, index) => <div key={`${key}-${index}`}><MathText text={displayText(removeSourceList(path.path))} /><MathText text={displayText(removeSourceList(path.reason))} /></div>))}</article>)}</details> : null}
      </section>

      {error || loadError ? <p className="research-error" role="alert"><CircleAlert size={14} /> {error || loadError}</p> : null}
      {notice ? <p className="research-tool-notice" role="status">{notice}</p> : null}
      <div className="research-status-line">
        <span>{project.status === "CANCELLED" ? "已停止" : project.status === "PAUSED" ? "已暂停" : project.status === "RUNNING" || project.status === "VERIFYING" ? "研究中" : project.status === "COMPLETED" ? "已完成" : "等待研究"}</span>
        {state.milestoneTotal ? <span>里程碑 {state.milestoneIndex || 0}/{state.milestoneTotal}</span> : null}
        {state.stepTotal ? <span>步骤 {state.stepIndex || 0}/{state.stepTotal}</span> : null}
        {state.currentBranch ? <span>{state.currentBranch} 分支</span> : null}
        <span>{snapshot.budget.llmCalls} 次模型调用</span><span>{snapshot.budget.toolCalls} 次工具调用</span>
        <span>用时 {snapshot.budget.researchElapsedSeconds == null ? "不可用" : `${readableElapsed(snapshot.budget.researchElapsedSeconds)}${snapshot.budget.researchElapsedKind === "estimated" ? "（估算）" : ""}`}</span>
        {["RUNNING", "VERIFYING"].includes(project.status) && state.currentAction ? <span><MathText text={state.currentAction.replace(/step_\d+_[a-z0-9]+/gi, "研究步骤")} /></span> : null}
        {["RUNNING", "VERIFYING"].includes(project.status) && state.currentTool ? <span>正在使用工具</span> : null}
        {["RUNNING", "VERIFYING"].includes(project.status) && state.activeAgents?.length ? <span>研究任务进行中</span> : null}
      </div>
      <div className="research-counts">{[["artifacts", "成果", snapshot.counts.artifacts], ["literature", "文献", snapshot.counts.literature], ["evidence", "证据", snapshot.counts.evidence], ["counterexamples", "失败路径", snapshot.counts.failedPaths], ["counterexamples", "反例", snapshot.counts.counterexamples]].map(([id, label, count], index) => <button key={`${id}-${index}`} type="button" onClick={() => { setDetailTab(String(id)); document.getElementById("research-details")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{label} {count}</button>)}</div>

      {["RUNNING", "VERIFYING", "PAUSED", "APPROVED", "FAILED"].includes(project.status) ? <div className="research-supervision-row">
        <div className="research-inline-actions">
          {project.status === "RUNNING" || project.status === "VERIFYING" ? <ActionButton className="research-secondary-button" type="button" onClick={() => void run("pause", () => researchAction(project.id, "pause"))}><Pause size={14} /> 暂停</ActionButton> : null}
          {project.status === "PAUSED" && !missingInput && project.approvedVersion === project.currentVersion && state.statusReason !== "STALLED" && state.statusReason !== "BLOCKED" ? <ActionButton className="research-primary-button" type="button" onClick={() => void run("resume", () => researchAction(project.id, "resume"), "研究已从保存的步骤继续。")}><Play size={14} /> 继续</ActionButton> : null}
          {project.status === "PAUSED" && !missingInput && project.approvedVersion === project.currentVersion && ["STALLED", "BLOCKED"].includes(state.statusReason) ? <ActionButton className="research-primary-button" type="button" onClick={() => void run("resume-anyway", () => resumeResearchAnyway(project.id), "已记录研究者决定，研究继续。")}><Play size={14} /> 仍然继续</ActionButton> : null}
          {["RUNNING", "VERIFYING", "PAUSED", "APPROVED", "FAILED"].includes(project.status) ? <ActionButton className="research-stop-button" type="button" onClick={() => void run("stop", () => researchAction(project.id, "stop"))}><Square size={13} /> 停止</ActionButton> : null}
        </div>
      </div> : null}

      {state.statusReason === "STALLED" || state.statusReason === "BLOCKED" ? (
        <div className="research-blocker-card">
          <strong>{state.statusReason === "STALLED" ? "研究停滞" : "研究遇到阻塞"}</strong>
          <p>{state.blockerDetail || project.lastError || "自动运行已停止。已有产物与证据仍会保留。"}</p>
          <div className="research-inline-actions">
            <ActionButton type="button" className="research-secondary-button" onClick={onEditPlan}><WandSparkles size={14} /> 修改计划</ActionButton>
            <ActionButton type="button" className="research-text-button" onClick={() => { setDetailTab("evidence"); document.getElementById("research-details")?.scrollIntoView({ behavior: "smooth" }); }}>查看证据</ActionButton>
            <ActionButton type="button" className="research-text-button" onClick={() => { setDetailTab("literature"); document.getElementById("research-details")?.scrollIntoView({ behavior: "smooth" }); }}>查看文献</ActionButton>
          </div>
        </div>
      ) : null}

      <section className="research-details" id="research-details">
        <nav className="research-details-tabs" aria-label="研究详情">{[["artifacts", "成果"], ["evidence", "证据"], ["counterexamples", "失败路径与反例"], ["literature", "文献"], ["timeline", "时间线"], ["files", "文件"]].map(([id, label]) => <button type="button" key={id} aria-current={detailTab === id ? "page" : undefined} onClick={() => setDetailTab(id)}>{label}</button>)}</nav>
        {detailTab === "evidence" ? <div>{nodes.filter((n) => ["evidence", "experiment"].includes(n.type)).length ? nodes.filter((n) => ["evidence", "experiment"].includes(n.type)).map((n) => <details className="research-timeline-row" key={n.id} title={n.title}><summary>{n.type === "experiment" ? "实验记录" : "证据记录"} · {n.status === "verified" ? "已验证" : "待验证"}</summary><MathText text={displayText(n.content)} showRaw /></details>) : <p>目前没有证据记录。</p>}</div> : null}
        {detailTab === "counterexamples" ? <div>{candidates.length || artifacts.some((a) => a.status === "failed") ? <>{candidates.map((n) => <article className="research-timeline-row" key={n.id}><MathText text={n.title.replace(/step_\d+_[a-z0-9]+/gi, "研究步骤")} /><small>候选反例 · 待验证</small></article>)}{artifacts.filter((a) => a.status === "failed").map((a) => <article className="research-timeline-row" key={a.id}><MathText text={readableTitle(a)} /><small>失败路径</small></article>)}</> : <p>目前没有失败路径或候选反例。</p>}</div> : null}
        {detailTab === "literature" ? <div>{relevant.length ? relevant.map((source) => <article className="research-timeline-row" key={source.id}><MathText text={source.title} />{source.url ? <a href={source.url} target="_blank" rel="noreferrer">查看来源</a> : null}</article>) : <p>未检索到相关文献。</p>}{lowRelevance.length ? <details><summary>另有 {lowRelevance.length} 条低相关结果</summary>{lowRelevance.map((source) => <article key={source.id}><MathText text={source.title} />{source.url ? <a href={source.url} target="_blank" rel="noreferrer">查看来源</a> : null}</article>)}</details> : null}</div> : null}
      <div className="research-stage-artifacts" hidden={detailTab !== "artifacts"}>
        <div className="research-card-heading"><h4>研究成果 · {artifacts.length}</h4></div>
        <input className="research-monospace-input" aria-label="搜索研究成果" placeholder="搜索成果或步骤" value={artifactQuery} onChange={(event) => { setArtifactQuery(event.target.value); setArtifactPage(1); }} />
        {visibleArtifacts.length ? visibleArtifacts.map((artifact) => (
          <details key={artifact.id} className="research-workbench-artifact" open={reviewingArtifact === artifact.id} onToggle={(event) => { const isOpen = (event.currentTarget as HTMLDetailsElement).open; setReviewingArtifact((current) => isOpen ? artifact.id : current === artifact.id ? "" : current); }}>
            <summary title={`${new Date(artifact.createdAt).toLocaleString("zh-CN")} · ${artifact.title} · ${artifact.id}`}><strong><MathText text={readableTitle(artifact)} /></strong><small>研究成果 · {artifact.status === "CONJECTURAL" ? "待验证" : artifact.status === "failed" ? "失败" : "已记录"} · {relativeTime(artifact.createdAt)}</small></summary>
            {reviewingArtifact === artifact.id ? <MathText text={artifact.content} showRaw /> : null}
            <small>{artifact.filePath || "选择工作区后可生成本地文件。"}</small>
            <div className="research-inline-actions">
              <ActionButton type="button" className="research-secondary-button" disabled={busy === `artifact-${artifact.id}`} onClick={() => void downloadArtifact(artifact)}><ArrowDownToLine size={13} /> 下载</ActionButton>
              <ActionButton type="button" className="research-text-button" onClick={() => void downloadArtifact(artifact, "json")}>导出 JSON</ActionButton>
              <ActionButton type="button" className="research-text-button" onClick={() => void downloadArtifact(artifact, "text")}>导出文本</ActionButton>
              {snapshot.workspacePath ? <><ActionButton type="button" className="research-text-button" onClick={() => void run("open-artifact", () => openResearchArtifact(project.id, artifact.id, true))}>在文件管理器中显示</ActionButton><ActionButton type="button" className="research-text-button" onClick={() => void run("open-artifact-folder", () => openResearchArtifact(project.id, artifact.id, false))}>打开本地文件夹</ActionButton></> : null}
              <ActionButton type="button" className="research-text-button" onClick={() => void run("feedback", () => giveArtifactFeedback(project.id, artifact.id, "useful"), "Result marked useful.")}>标记有用</ActionButton>
              <ActionButton type="button" className="research-text-button" onClick={() => void run("feedback", () => giveArtifactFeedback(project.id, artifact.id, "invalid"), "Result marked invalid.")}>标记无效</ActionButton>
              {artifact.stepId ? <ActionButton type="button" className="research-text-button" onClick={() => { setReviewingArtifact(artifact.id); document.getElementById(`stage-${artifact.stepId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); }}>查看步骤</ActionButton> : null}
            </div>
          </details>
        )) : <p className="research-muted-copy">完成步骤后将在这里显示成果。</p>}
        <div className="research-inline-actions"><ActionButton type="button" className="research-text-button" disabled={artifactPage <= 1} onClick={() => setArtifactPage((page) => page - 1)}>上一页</ActionButton><small>{filteredArtifacts.length} 条成果 · 第 {Math.min(artifactPage, artifactPages)} / {artifactPages}</small><ActionButton type="button" className="research-text-button" disabled={artifactPage >= artifactPages} onClick={() => setArtifactPage((page) => page + 1)}>下一页</ActionButton></div>
      </div>

      {detailTab === "files" ? <ResearchFilesPanel key={project.id} projectId={project.id} workspacePath={snapshot.workspacePath} revision={`${project.currentVersion}:${snapshot.counts.artifacts}:${snapshot.counts.literature}:${state.toolCalls}`} /> : null}

        {detailTab === "timeline" ? <div className="research-timeline-card">
          <label><input type="checkbox" checked={showSystem} onChange={(event) => setShowSystem(event.target.checked)} /> 显示系统事件</label>
          {showSystem && snapshot.summary ? <details><summary>研究数据摘要</summary><MathText text={displayText(summaryValue(snapshot.summary))} /></details> : null}
          {(() => {
            const allowed = new Set(["step_started", "step_completed", "milestone_report_created", "next_path_recommended", "human_intervention", "research_review_required", "research_started", "research_blocked", "research_stalled", "final_package_created"]);
            const entries = [
              ...snapshot.timeline.filter((event) => event.eventType !== "decision_recorded" && (showSystem || allowed.has(event.eventType))).map((event) => ({ id: event.id, time: event.createdAt, human: event.actor === "researcher", text: event.eventType === "next_path_recommended" ? "已生成下一步建议" : event.eventType === "human_intervention" ? String(event.details.message || event.details.note || "研究者提交了输入") : event.eventType === "milestone_report_created" ? "里程碑成果已保存" : event.eventType === "research_review_required" ? "研究暂停，等待审阅" : event.summary, recommendation: event.eventType === "next_path_recommended" })),
              ...snapshot.decisions.map((decision) => ({ id: decision.id, time: decision.createdAt, human: true, text: decision.decision === "research_stopped" ? "研究者停止了研究" : decision.decision === "research_resumed" ? "研究者继续了研究" : decision.decision === "research_paused" ? "研究者暂停了研究" : decision.decision === "supervision_mode" ? "研究者调整了监督方式" : decision.decision === "plan_approved" ? "研究者批准了计划" : decision.rationale || "研究者记录了一项决定", recommendation: false })),
            ].sort((a, b) => Date.parse(b.time) - Date.parse(a.time)).slice(0, timelineLimit);
            const groups = new Map<string, typeof entries>();
            entries.forEach((entry) => { const second = entry.time.slice(0, 19); groups.set(second, [...(groups.get(second) || []), entry]); });
            return groups.size ? [...groups].map(([second, group]) => <div className="research-timeline-row" key={second}><small>{new Date(group[0].time).toLocaleString("zh-CN")}</small>{group.map((entry) => <div key={entry.id} className={entry.human ? "research-human-event" : ""}><MathText text={displayText(entry.text)} />{entry.recommendation ? <a href="#research-recommendations">查看建议</a> : null}</div>)}</div>) : <p>还没有研究活动记录。</p>;
          })()}
          {snapshot.timeline.length + snapshot.decisions.length > timelineLimit ? <ActionButton type="button" className="research-text-button" onClick={() => setTimelineLimit((limit) => limit + 20)}>显示更早记录</ActionButton> : null}
        </div> : null}
      </section>

      {showIntervention ? <section className="research-human-input">
        <h4>研究者输入</h4><p>保存研究记录、补充条件或提交计划提案；如需直接向 AI 提问，请使用讨论框。</p>
        <div className="research-control-grid">
          <label className="research-field">输入类型<select value={interventionMode} onChange={(event) => { setInterventionMode(event.target.value); if (event.target.value === "condition") setInterventionKind("human_result"); if (event.target.value === "request") setInterventionKind("request_verification"); }}><option value="note">备注</option><option value="condition">补充条件或假设</option><option value="alternative">提出替代路线</option><option value="plan">要求修改计划</option><option value="request">其他研究动作</option></select></label>
          {interventionMode === "condition" ? <label className="research-field">记录方式<select value={interventionKind} onChange={(e) => setInterventionKind(e.target.value)}><option value="human_result">补充条件或结果</option><option value="human_lemma">补充引理</option></select></label> : null}
          {interventionMode === "alternative" ? <label className="research-field">提交方式<select value={alternativeAction} onChange={(e) => setAlternativeAction(e.target.value)}><option value="idea">作为计划提案</option>{latest?.status === "PENDING" ? <option value="recommendation">替代当前建议</option> : null}</select></label> : null}
          {interventionMode === "request" ? <label className="research-field">研究动作<select value={interventionKind} onChange={(e) => setInterventionKind(e.target.value)}><option value="request_verification">要求验证</option><option value="request_literature">检索文献</option><option value="request_proof">尝试证明</option><option value="request_counterexample">寻找反例</option><option value="request_numerical">数值计算</option><option value="request_symbolic">符号计算</option><option value="request_critique">检查论证</option></select></label> : null}
          <label className="research-field">目标分支（可选）<select value={interventionBranch} onChange={(event) => setInterventionBranch(event.target.value)}><option value="">当前分支 · {state.currentBranch || "main"}</option>{(project.branches || []).map((branch) => <option key={branch.id} value={branch.name}>{branch.name}</option>)}</select></label>
          <label className="research-field">输入内容<textarea value={interventionText} onChange={(event) => setInterventionText(event.target.value)} placeholder="写入研究记录或计划要求；此处不会直接启动 AI 讨论" rows={3} /></label>
        </div>
        <ActionButton className="research-secondary-button" type="button" disabled={busy === "intervention" || !interventionText.trim()} onClick={() => void submitIntervention()}>提交研究输入</ActionButton>
        {snapshot.queuedInterventions?.length ? <p>有 {snapshot.queuedInterventions.length} 条研究动作已记录，可在时间线查看。</p> : null}
      </section> : null}


    </section>
  );
}
