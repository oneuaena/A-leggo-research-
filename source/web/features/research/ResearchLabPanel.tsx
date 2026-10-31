"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  BarChart3, BookOpen, Check, CircleAlert, Coins, Cpu, Database, FileUp, FlaskConical, GitBranch,
  LoaderCircle, MessageSquare, Plus, RefreshCw, Search, ShieldCheck, Trash2, Users, Wand2,
} from "@/shared/ui/glyphs";
import {
  addProjectMember, addResearchAssignment, addResearchComment, annotateProofStep, cancelEvalRun, cancelLeanInstall,
  checkLeanUpdates, pauseLeanInstall,
  createLongTermMemory, deleteLongTermMemory, deleteResearchComment, distillProjectMemory, getAutoProve, getCasStatus,
  getLeanResearchSettings, saveLeanResearchSettings,
  getCollaborationCandidates, getEvalProblems, getEvalRun, getLeanStatus, getMemorySuggestions, getProjectMembers,
  getProjectUsage, getProofReview, getRelatedProjects, getUsagePrices, getVisionConfig, importFormulaPdf, suggestImportInsights, getGmailImportStatus, importGmailAttachment, listPdfContinues, continuePdfFromStored, importEmailAttachment,
  importLatexProject, listEvalRuns, listLongTermMemory, listProofReviews, listResearchAssignments, listResearchComments,
  promoteLatexImport, queryMathDatabase, removeProjectMember, saveCasConfig, saveHandwritingTranscription,
  saveUsagePrices, saveVisionConfig, startAutoProve, startEvalRun, startLeanInstall, startLeanUpgrade, startProofReview,
  transcribeHandwriting, updateLongTermMemory, updateResearchAssignment, updateResearchComment,
  type AutoProveJob, type CasEngine, type CollaborationRole, type EvalRun, type EvalRunSummary, type HandwritingResult,
  type LatexImportResult, type GmailFetcherStatus, type PdfContinueItem, type LeanStatus, type LeanUpdates, type LongTermMemory, type LongTermMemoryKind, type MathDatabaseResult, type MemorySuggestion,
  type MathDatabaseTool, type ProjectMembers, type ProjectUsage, type ProofReview, type ProofReviewSummary,
  type ProofTreeNode, type ProofVerdict, type ResearchAssignment, type ResearchComment, type ResearchProject,
  type UsagePrices, type LeanResearchSettings,
} from "@/lib/research-api";

type LabTab = "lean" | "cas" | "databases" | "imports" | "review" | "memory" | "collab" | "usage" | "eval";

const LAB_TABS: { id: LabTab; label: string }[] = [
  { id: "lean", label: "Lean 形式化" },
  { id: "cas", label: "计算系统" },
  { id: "databases", label: "数学数据库" },
  { id: "imports", label: "导入资料" },
  { id: "review", label: "证明审查" },
  { id: "memory", label: "长期记忆" },
  { id: "collab", label: "协作" },
  { id: "usage", label: "用量与费用" },
  { id: "eval", label: "评测" },
];

const VERDICT_LABEL: Record<string, string> = { valid: "成立", gap: "有缺口", error: "错误", unclear: "需澄清" };
const OVERALL_LABEL: Record<string, string> = {
  "no-issues-found": "未发现问题（仍需人工确认）",
  "gaps-found": "发现推理缺口",
  "errors-found": "发现错误",
  "needs-clarification": "需要澄清",
};
const MEMORY_KIND_LABEL: Record<LongTermMemoryKind, string> = {
  note: "笔记", technique: "技巧", result: "结论", "failed-route": "失败路线", reference: "参考", preference: "偏好", definition: "定义",
};

type Feedback = { error: (message: unknown) => void; notice: (message: string) => void };
type TabProps = { project: ResearchProject; isAdmin: boolean; feedback: Feedback };

function messageOf(value: unknown) {
  return value instanceof Error ? value.message : typeof value === "string" ? value : "研究服务暂时无法完成该操作。";
}

function formatNumber(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—";
}

function formatBytes(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未知";
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function formatCost(value: number, currency: string, priced: boolean) {
  if (priced) return `${value.toFixed(4)} ${currency}`;
  if (value > 0) return `${value.toFixed(4)} ${currency}（部分未定价）`;
  return "未配置单价";
}

const EVAL_STATE_LABEL: Record<string, string> = {
  RUNNING: "进行中", COMPLETED: "已完成", CANCELLED: "已取消", FAILED: "失败", NONE: "未开始",
};
const EVAL_CATEGORY_LABEL: Record<string, string> = {
  compute: "计算", counterexample: "反例", honesty: "诚实性", lean: "Lean 证明",
  "proof-review": "证明审查", sequence: "数列/OEIS",
};

/** Runs `tick` immediately and every `ms` while `active` is true. */
function usePolling(active: boolean, tick: () => Promise<void> | void, ms = 2500) {
  const saved = useRef(tick);
  useEffect(() => { saved.current = tick; }, [tick]);
  useEffect(() => {
    if (!active) return undefined;
    const handle = window.setInterval(() => { void saved.current(); }, ms);
    return () => window.clearInterval(handle);
  }, [active, ms]);
}

function Card({ eyebrow, title, icon, children }: { eyebrow: string; title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="research-tool-form research-lab-card">
      <div className="research-card-heading"><div><span className="research-eyebrow">{eyebrow}</span><h4>{title}</h4></div>{icon}</div>
      {children}
    </div>
  );
}

function Busy({ on, children }: { on: boolean; children: ReactNode }) {
  return <>{on ? <LoaderCircle size={15} className="research-spin" /> : null}{children}</>;
}

// ---------------------------------------------------------------------------
// Lean onboarding + AI proof with kernel feedback
// ---------------------------------------------------------------------------

const LEAN_PHASE_LABELS: Record<string, string> = {
  check: "检查环境",
  download: "下载安装包",
  verify: "校验文件",
  extract: "解压安装",
  smoke: "安装后检查",
  // 传统安装模式（from_source）的阶段
  elan: "安装 elan",
  toolchain: "安装 Lean 工具链",
  workspace: "创建 Lake 工程",
  mathlib: "获取 Mathlib",
  cache: "下载预编译缓存",
};

function formatSpeed(bps: number) {
  if (typeof bps !== "number" || !Number.isFinite(bps) || bps <= 0) return "";
  if (bps >= 1024 * 1024) return `${(bps / 1024 / 1024).toFixed(1)} MB/s`;
  return `${Math.max(1, Math.round(bps / 1024))} KB/s`;
}

function formatEta(downloaded: number, total: number, bps: number) {
  if (!total || total <= downloaded || !bps || bps <= 0) return "";
  const seconds = Math.round((total - downloaded) / bps);
  if (seconds < 90) return `还剩约 ${seconds} 秒`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `还剩约 ${minutes} 分钟`;
  return `还剩约 ${(minutes / 60).toFixed(1)} 小时`;
}

function LeanTab({ project, isAdmin, feedback }: TabProps) {
  const [status, setStatus] = useState<LeanStatus | null>(null);
  const [busy, setBusy] = useState("");
  const [updates, setUpdates] = useState<LeanUpdates | null>(null);
  const autoStarted = useRef(false);
  const confirmed = useMemo(() => (project.formalBindings ?? []).filter((item) => item.mappingAuthority === "USER_CONFIRMED"), [project.formalBindings]);
  const [bindingId, setBindingId] = useState("");
  const [hint, setHint] = useState("");
  const [maxAttempts, setMaxAttempts] = useState(4);
  const [job, setJob] = useState<AutoProveJob | null>(null);
  const [leanSettings, setLeanSettings] = useState<LeanResearchSettings>({ confirmBeforeCheck: true, autoCallEnabled: false });
  const [settingsBusy, setSettingsBusy] = useState(false);
  const availableBindings = useMemo(() => leanSettings.confirmBeforeCheck ? confirmed : (project.formalBindings ?? []), [confirmed, leanSettings.confirmBeforeCheck, project.formalBindings]);
  const selected = availableBindings.some((item) => item.id === bindingId) ? bindingId : availableBindings[0]?.id || "";
  const selectedBinding = availableBindings.find((item) => item.id === selected);

  const refresh = useCallback(async () => {
    try { setStatus(await getLeanStatus()); } catch (error) { feedback.error(error); }
  }, [feedback]);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    let active = true;
    void getLeanResearchSettings(project.id).then((value) => { if (active) setLeanSettings(value); }).catch((error) => feedback.error(error));
    return () => { active = false; };
  }, [feedback, project.id]);
  usePolling(status?.install.status === "running" || status?.install.status === "paused", refresh, 3000);

  const refreshJob = useCallback(async () => {
    if (!selected) return;
    try {
      const next = await getAutoProve(project.id, selected);
      setJob(next.state === "NONE" ? null : next);
    } catch (error) { feedback.error(error); }
  }, [feedback, project.id, selected]);
  useEffect(() => { void refreshJob(); }, [refreshJob]);
  usePolling(job?.state === "RUNNING", refreshJob);

  const install = useCallback(async (action: "start" | "cancel" | "pause" | "upgrade" = "start") => {
    setBusy(action);
    try {
      if (action === "cancel") await cancelLeanInstall();
      else if (action === "pause") await pauseLeanInstall();
      else if (action === "upgrade") await startLeanUpgrade();
      else await startLeanInstall();
      await refresh();
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  }, [feedback, refresh]);

  // 第一次打开标签页：未就绪 + 管理员 + 允许自动下载 + 用户没取消过 → 静默后台开始
  // autoStart 用可选链：旧后端没有该字段时不自动开始，避免白屏。
  useEffect(() => {
    if (autoStarted.current || !isAdmin || !status || status.ready) return;
    const job = status.install;
    if (job.status !== "idle" || status.autoStart?.enabled !== true || status.autoStart?.declined) return;
    autoStarted.current = true;
    void install("start");
  }, [isAdmin, status, install]);

  // 就绪后检查一次版本更新（后端带 1 小时缓存，不会频繁请求网络）
  useEffect(() => {
    if (!status?.ready) return;
    let cancelled = false;
    void checkLeanUpdates().then((result) => { if (!cancelled) setUpdates(result); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [status?.ready]);

  const prove = async () => {
    if (!selected) return;
    setBusy("prove");
    try {
      setJob(await startAutoProve(project.id, selected, { maxAttempts, hint: hint.trim() }));
      feedback.notice("AI 证明已开始：每次尝试都会交给 Lean 内核检查，并把报错反馈给下一次尝试。");
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };

  const install_ = status?.install;
  const running = install_?.status === "running";
  const paused = install_?.status === "paused";
  return (
    <div className="research-tool-grid">
      <Card eyebrow="Lean 环境" title="Lean 环境" icon={<ShieldCheck size={17} />}>
        {status ? (
          <>
            <p className={status.ready ? "research-lab-ok" : "research-lab-warn"} role="status">
              {status.ready ? `Lean 已就绪（Lean ${status.pinnedLean} · Mathlib ${status.pinnedMathlib}）` : `Lean 尚未就绪：${status.reason || "未找到固定版本的 Mathlib 工作区"}`}
            </p>
            <ul className="research-lab-checklist">
              {status.checks.map((check) => <li key={check.id} data-ok={check.ok ? "true" : "false"}>{check.ok ? <Check size={13} /> : <CircleAlert size={13} />} {check.label}<small>{check.detail}</small></li>)}
            </ul>
            <p className="research-muted-copy">安装目录：{status.toolsHome || "默认"} · 可用磁盘 {formatBytes(status.freeDiskBytes)} / 需要约 {formatBytes(status.requiredDiskBytes)}</p>
            {updates?.canUpgrade ? (
              <p className="research-lab-warn" role="status">
                发现 Lean 环境新版本 {updates.latestVersion}（当前 {updates.installedVersion}），升级不会动你的项目数据。{isAdmin ? "" : "请管理员升级。"}
              </p>
            ) : null}
            {install_ && install_.status !== "idle" ? (
              <div className="research-lab-install" aria-label="Lean 安装进度">
                {install_.totalBytes > 0 ? (
                  <>
                    <progress max={install_.totalBytes} value={install_.downloadedBytes} />
                    <p>
                      {formatBytes(install_.downloadedBytes)} / {formatBytes(install_.totalBytes)}
                      {formatSpeed(install_.speedBps) ? ` · ${formatSpeed(install_.speedBps)}` : ""}
                      {formatEta(install_.downloadedBytes, install_.totalBytes, install_.speedBps) ? ` · ${formatEta(install_.downloadedBytes, install_.totalBytes, install_.speedBps)}` : ""}
                    </p>
                  </>
                ) : (
                  <progress max={100} value={Math.round(install_.progress || 0)} />
                )}
                <p>{install_.phaseLabel || LEAN_PHASE_LABELS[install_.phase] || install_.phase}{install_.message ? `：${install_.message}` : ""}</p>
                {paused ? <p className="research-muted-copy">已暂停，进度已保存，可随时继续下载。</p> : null}
                {install_.errorHelp ? (
                  <div className="research-error" role="alert">
                    <p><strong>{install_.errorHelp.title}</strong></p>
                    <p>{install_.errorHelp.reason}</p>
                    <p>解决办法：{install_.errorHelp.fix}</p>
                  </div>
                ) : install_.error ? <p className="research-error" role="alert">{install_.error}</p> : null}
                {install_.log.length ? <pre className="research-lab-log">{install_.log.slice(-12).join("\n")}</pre> : null}
              </div>
            ) : null}
            <div className="research-lab-actions">
              {isAdmin ? (
                running || paused
                  ? <>
                      <ActionButton type="button" className="research-secondary-button" disabled={busy === "pause"} onClick={() => void install("pause")}><Busy on={busy === "pause"}>{paused ? "继续下载" : "暂停"}</Busy></ActionButton>
                      <ActionButton type="button" className="research-secondary-button" disabled={busy === "cancel"} onClick={() => void install("cancel")}><Busy on={busy === "cancel"}>取消安装</Busy></ActionButton>
                    </>
                  : <>
                      <ActionButton type="button" className="research-primary-button" disabled={busy === "start"} onClick={() => void install("start")}><Busy on={busy === "start"}>{status.ready ? "重新检查 / 修复安装" : "一键安装 Lean + Mathlib"}</Busy></ActionButton>
                      {updates?.canUpgrade ? <ActionButton type="button" className="research-secondary-button" disabled={busy === "upgrade"} onClick={() => void install("upgrade")}><Busy on={busy === "upgrade"}>升级到 {updates.latestVersion}</Busy></ActionButton> : null}
                    </>
              ) : <p className="research-muted-copy">请管理员在此一键安装 Lean 与 Mathlib。</p>}
              <ActionButton type="button" className="research-secondary-button" onClick={() => void refresh()}><RefreshCw size={14} /> 刷新</ActionButton>
            </div>
          </>
        ) : <p className="research-muted-copy">正在检查 Lean 环境…</p>}
      </Card>

      <Card eyebrow="研究环境" title="Lean 调用策略" icon={<ShieldCheck size={17} />}>
        <label className="research-lab-setting-row"><span><strong>AI 证明前先由我确认 Lean 陈述</strong><small>关闭后，Lean 仍只检查形式化陈述；未确认的映射不会提升为原命题的证明。</small></span><input type="checkbox" checked={leanSettings.confirmBeforeCheck} disabled={!isAdmin || settingsBusy} onChange={(event) => {
          const next = { ...leanSettings, confirmBeforeCheck: event.target.checked };
          setSettingsBusy(true); void saveLeanResearchSettings(project.id, next).then(setLeanSettings).catch((error) => feedback.error(error)).finally(() => setSettingsBusy(false));
        }} /></label>
        <label className="research-lab-setting-row"><span><strong>研究中允许 AI 自动调用 Lean</strong><small>关闭后，计划执行不会自动启动 Lean；仍可从研究资料里手动运行检查。</small></span><input type="checkbox" checked={leanSettings.autoCallEnabled} disabled={!isAdmin || settingsBusy} onChange={(event) => {
          const next = { ...leanSettings, autoCallEnabled: event.target.checked };
          setSettingsBusy(true); void saveLeanResearchSettings(project.id, next).then(setLeanSettings).catch((error) => feedback.error(error)).finally(() => setSettingsBusy(false));
        }} /></label>
        {!isAdmin ? <p className="research-muted-copy">请管理员调整项目的 Lean 调用策略。</p> : null}
      </Card>

      <Card eyebrow="内核反馈" title="AI 证明（Lean 内核把关）" icon={<Wand2 size={17} />}>
        {availableBindings.length === 0 ? (
            <p className="research-muted-copy">先在「形式化证明」面板里建立命题映射{leanSettings.confirmBeforeCheck ? "并确认 Lean 陈述" : ""}，AI 才能尝试证明。</p>
        ) : (
          <>
            <label className="research-field"><span className="research-field-label">{leanSettings.confirmBeforeCheck ? "已确认的形式化命题" : "形式化命题（未确认项仅检查 Lean 陈述）"}</span>
              <select value={selected} onChange={(event) => { setBindingId(event.target.value); setJob(null); }}>
                {availableBindings.map((item) => <option key={item.id} value={item.id}>{item.leanStatement.slice(0, 90)}{item.mappingAuthority === "USER_CONFIRMED" ? "" : " · 尚未确认"}</option>)}
              </select>
            </label>
            <label className="research-field"><span className="research-field-label">证明提示（可选）</span><textarea rows={2} value={hint} maxLength={2000} onChange={(event) => setHint(event.target.value)} placeholder="例如：对 n 归纳；用 Nat.Prime.two_le" /></label>
            <label className="research-field"><span className="research-field-label">最多尝试次数</span><input type="number" min={1} max={6} value={maxAttempts} onChange={(event) => setMaxAttempts(Math.max(1, Math.min(6, Number(event.target.value) || 1)))} /></label>
            <ActionButton type="button" className="research-primary-button" disabled={busy === "prove" || job?.state === "RUNNING" || !status?.ready} onClick={() => void prove()}>
              <Busy on={busy === "prove" || job?.state === "RUNNING"}>{job?.state === "RUNNING" ? "正在证明…" : "让 AI 尝试证明"}</Busy>
            </ActionButton>
            {!status?.ready ? <p className="research-muted-copy">Lean 就绪后才能启动内核检查。</p> : null}
          </>
        )}
        {job ? (
          <div className="research-lab-attempts" aria-label="证明尝试记录">
            <p className={job.passed ? "research-lab-ok" : job.state === "RUNNING" ? "research-muted-copy" : "research-lab-warn"} role="status">
              {job.passed ? (selectedBinding?.mappingAuthority === "USER_CONFIRMED" ? "Lean 内核已通过该证明。" : "Lean 内核通过了这条尚未确认映射的陈述；它不证明原始命题。") : job.state === "RUNNING" ? `进行中：已完成 ${job.attempts.length} / ${job.maxAttempts} 次尝试` : job.error || "未能在限定次数内得到被内核接受的证明。"}
            </p>
            {job.attempts.map((attempt) => (
              <details key={attempt.attempt} open={attempt.attempt === job.attempts.length}>
                <summary>第 {attempt.attempt} 次 · {attempt.status} · {attempt.elapsedMs} ms</summary>
                {attempt.explanation ? <p>{attempt.explanation}</p> : null}
                <pre className="research-lab-code">{attempt.proof}</pre>
                {attempt.feedback ? <pre className="research-lab-log">{attempt.feedback}</pre> : null}
              </details>
            ))}
          </div>
        ) : null}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CAS bridges + vision model
// ---------------------------------------------------------------------------

function CasTab({ isAdmin, feedback }: TabProps) {
  const [engines, setEngines] = useState<CasEngine[]>([]);
  const [isolation, setIsolation] = useState("");
  const [paths, setPaths] = useState<Record<string, string>>({});
  const [vision, setVision] = useState({ model: "", source: "" });
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const [cas, visionConfig] = await Promise.all([getCasStatus(), getVisionConfig()]);
      setEngines(cas.engines);
      setIsolation(cas.isolation);
      setPaths(cas.config ?? {});
      setVision({ model: visionConfig.model, source: visionConfig.source });
    } catch (error) { feedback.error(error); }
  }, [feedback]);
  useEffect(() => { void load(); }, [load]);

  const savePaths = async () => {
    setBusy("cas");
    try {
      const next = await saveCasConfig(paths);
      setEngines(next.engines);
      feedback.notice("计算系统路径已保存。");
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };
  const saveVision = async () => {
    setBusy("vision");
    try {
      const next = await saveVisionConfig(vision.model.trim());
      setVision({ model: next.model, source: next.source });
      feedback.notice("视觉模型已保存，可用于手写稿和扫描 PDF 识别。");
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };

  return (
    <div className="research-tool-grid">
      <Card eyebrow="外部 CAS" title="外部计算系统" icon={<Cpu size={17} />}>
        <p className="research-muted-copy">研究智能体会自动调用已安装的系统（隔离方式：{isolation || "资源限制"}）。未安装的系统不会出现在 AI 的工具列表中。</p>
        <table className="research-lab-table">
          <thead><tr><th>系统</th><th>状态</th><th>版本 / 路径</th></tr></thead>
          <tbody>
            {engines.map((engine) => (
              <tr key={engine.key}>
                <td><strong>{engine.label}</strong><small>{engine.language}</small></td>
                <td>{engine.available ? <span className="research-lab-ok">可用</span> : <span className="research-lab-warn">未找到</span>}</td>
                <td>
                  {engine.available ? <small>{engine.version} · {engine.path}</small> : <small>{engine.installHint}</small>}
                  {isAdmin ? <input aria-label={`${engine.label} 可执行文件路径`} value={paths[engine.key] ?? ""} placeholder={engine.envVar ? `可执行文件路径（或环境变量 ${engine.envVar}）` : "可执行文件路径"} onChange={(event) => setPaths((current) => ({ ...current, [engine.key]: event.target.value }))} /> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="research-lab-actions">
          {isAdmin ? <ActionButton type="button" className="research-primary-button" disabled={busy === "cas"} onClick={() => void savePaths()}><Busy on={busy === "cas"}>保存路径</Busy></ActionButton> : null}
          <ActionButton type="button" className="research-secondary-button" onClick={() => void load()}><RefreshCw size={14} /> 重新检测</ActionButton>
        </div>
      </Card>
      <Card eyebrow="视觉模型" title="手写稿 / 扫描件识别模型" icon={<FileUp size={17} />}>
        <p className="research-muted-copy">需要支持图像输入的模型（例如 qwen-vl-max、gpt-4o）。当前：{vision.model || "未配置"}{vision.source ? `（${vision.source}）` : ""}</p>
        <label className="research-field"><span className="research-field-label">视觉模型名称</span><input value={vision.model} disabled={!isAdmin} onChange={(event) => setVision((current) => ({ ...current, model: event.target.value }))} /></label>
        {isAdmin ? <ActionButton type="button" className="research-secondary-button" disabled={busy === "vision"} onClick={() => void saveVision()}><Busy on={busy === "vision"}>保存视觉模型</Busy></ActionButton> : null}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// OEIS / zbMATH / arXiv / LMFDB
// ---------------------------------------------------------------------------

const DATABASES: { id: MathDatabaseTool; label: string; placeholder: string }[] = [
  { id: "oeis_search", label: "OEIS 整数数列", placeholder: "1, 1, 2, 5, 14, 42 或 A000108" },
  { id: "zbmath_search", label: "zbMATH Open", placeholder: "Erdős–Straus conjecture" },
  { id: "arxiv_search", label: "arXiv", placeholder: "Collatz conjecture almost all orbits" },
  { id: "lmfdb_search", label: "LMFDB", placeholder: "11.a2、2.0.4.1 等标签" },
];

function itemTitle(item: Record<string, unknown>) {
  return String(item.title ?? item.name ?? item.label ?? item.id ?? "结果");
}

function itemDetail(item: Record<string, unknown>) {
  const parts = [item.authors ?? item.author, item.year ?? item.published, item.source].filter(Boolean).map(String);
  return parts.join(" · ");
}

function itemBody(item: Record<string, unknown>) {
  const text = item.summary ?? item.review ?? item.terms ?? "";
  if (text) return String(text);
  const { url: _url, id: _id, ...rest } = item;
  void _url; void _id;
  return Object.entries(rest).slice(0, 6).map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`).join("\n");
}

function DatabaseTab({ project, feedback }: TabProps) {
  const [tool, setTool] = useState<MathDatabaseTool>("oeis_search");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [collection, setCollection] = useState("");
  const [save, setSave] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MathDatabaseResult | null>(null);
  const meta = DATABASES.find((item) => item.id === tool) ?? DATABASES[0];

  const run = async () => {
    if (!query.trim()) return;
    setBusy(true);
    try {
      const input: Record<string, unknown> = tool === "lmfdb_search" ? { label: query.trim(), ...(collection ? { collection } : {}) } : { query: query.trim(), limit: 10 };
      if (tool === "arxiv_search" && category.trim()) input.category = category.trim();
      const next = await queryMathDatabase(project.id, tool, input, save);
      setResult(next);
      if (!next.ok) feedback.error(next.error || "查询失败。");
      else if (next.savedSourceIds?.length) feedback.notice(`已把 ${next.savedSourceIds.length} 条结果存入文献库。`);
    } catch (error) { feedback.error(error); } finally { setBusy(false); }
  };

  const items = result?.data?.items ?? [];
  return (
    <div className="research-tool-grid">
      <Card eyebrow="数学数据库" title="查询数学数据库" icon={<Database size={17} />}>
        <div className="research-control-grid">
          <label className="research-field"><span className="research-field-label">数据库</span>
            <select value={tool} onChange={(event) => { setTool(event.target.value as MathDatabaseTool); setResult(null); }}>{DATABASES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
          </label>
          {tool === "arxiv_search" ? <label className="research-field"><span className="research-field-label">分类（可选）</span><input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="math.NT" /></label> : null}
          {tool === "lmfdb_search" ? <label className="research-field"><span className="research-field-label">集合（可选）</span><input value={collection} onChange={(event) => setCollection(event.target.value)} placeholder="ec_curvedata" /></label> : null}
        </div>
        <label className="research-field"><span className="research-field-label">查询内容</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={meta.placeholder} onKeyDown={(event) => { if (event.key === "Enter") void run(); }} /></label>
        <label className="research-lab-inline"><input type="checkbox" checked={save} onChange={(event) => setSave(event.target.checked)} /> 将结果存入本项目文献库</label>
        <ActionButton type="button" className="research-primary-button" disabled={busy || !query.trim()} onClick={() => void run()}><Busy on={busy}><Search size={15} /> 查询</Busy></ActionButton>
      </Card>
      <div className="research-graph-ledger" aria-label="数据库查询结果">
        <h4>结果 <small>{busy ? "查询中…" : result?.data ? `${result.data.provider ?? ""} · 共 ${formatNumber(result.data.count ?? items.length)} 条` : "尚未查询"}</small></h4>
        {busy ? <p className="research-muted-copy" role="status">正在查询数据库，请稍候…</p> : null}
        {result && !result.ok ? <p className="research-error" role="alert">{result.error}</p> : null}
        {!busy && items.map((item, index) => (
          <article key={`${itemTitle(item)}-${index}`} className="research-lab-result">
            <strong>{item.url ? <a href={String(item.url)} target="_blank" rel="noreferrer">{itemTitle(item)}</a> : itemTitle(item)}</strong>
            {item.id && itemTitle(item) !== item.id ? <small>{String(item.id)}</small> : null}
            {itemDetail(item) ? <small>{itemDetail(item)}</small> : null}
            <p>{itemBody(item).slice(0, 600)}</p>
          </article>
        ))}
        {!busy && result?.ok && items.length === 0 ? (
          <p className="research-muted-copy" role="status">
            {String((result.data as { emptyHint?: string } | undefined)?.emptyHint || "没有找到匹配结果。可尝试更短关键词、A 编号，或稍后重试（OEIS/LMFDB 有时会触发 Cloudflare 验证）。")}
          </p>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Imports: LaTeX projects, formula PDFs, handwritten photos
// ---------------------------------------------------------------------------


function importErrorText(error: unknown): string {
  if (!error) return "导入失败，请重试。";
  if (typeof error === "string") return error;
  if (error instanceof Error) {
    const anyErr = error as Error & { detail?: unknown; body?: unknown };
    const detail = anyErr.detail ?? (anyErr as { response?: { detail?: unknown } }).response?.detail;
    if (detail && typeof detail === "object" && detail !== null && "message" in (detail as object)) {
      return String((detail as { message: string }).message);
    }
    if (typeof detail === "string") return detail;
    return error.message || "导入失败，请重试。";
  }
  if (typeof error === "object" && error !== null && "message" in error) {
    const msg = (error as { message?: unknown; detail?: unknown }).message;
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && detail !== null && "message" in (detail as object)) {
      return String((detail as { message: string }).message);
    }
    return String(msg || "导入失败，请重试。");
  }
  return "导入失败，请重试。";
}

function ImportsTab({ project, feedback }: TabProps) {
  const [busy, setBusy] = useState("");
  const [latex, setLatex] = useState<LatexImportResult | null>(null);
  const [pickedStatements, setPickedStatements] = useState<Set<number>>(new Set());
  const [pickedReferences, setPickedReferences] = useState<Set<number>>(new Set());
  const [pdfMode, setPdfMode] = useState<"auto" | "text" | "vision">("auto");
  const [pdfSummary, setPdfSummary] = useState("");
  const [pdfResult, setPdfResult] = useState<Awaited<ReturnType<typeof importFormulaPdf>> | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [maxVisionPages, setMaxVisionPages] = useState(40);
  const [hint, setHint] = useState("");
  const [photoStyle, setPhotoStyle] = useState<"handwriting" | "whiteboard">("handwriting");
  const [handwriting, setHandwriting] = useState<HandwritingResult | null>(null);
  const [handwritingText, setHandwritingText] = useState("");
  const [handwritingFiles, setHandwritingFiles] = useState<string[]>([]);
  const [title, setTitle] = useState("手写推导");
  const [suggestions, setSuggestions] = useState<Awaited<ReturnType<typeof suggestImportInsights>> | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [gmailStatus, setGmailStatus] = useState<GmailFetcherStatus | null>(null);
  const [gmailMessageId, setGmailMessageId] = useState("");
  const [gmailAttachmentId, setGmailAttachmentId] = useState("");
  const [gmailFilename, setGmailFilename] = useState("");
  const [pdfContinues, setPdfContinues] = useState<PdfContinueItem[]>([]);
  const [continuesLoading, setContinuesLoading] = useState(true);
  const [continuesError, setContinuesError] = useState("");
  const [gmailLoading, setGmailLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setContinuesLoading(true);
    setContinuesError("");
    setGmailLoading(true);
    void getGmailImportStatus()
      .then((status) => { if (active) setGmailStatus(status); })
      .catch(() => { if (active) setGmailStatus(null); })
      .finally(() => { if (active) setGmailLoading(false); });
    void listPdfContinues(project.id)
      .then((result) => { if (active) setPdfContinues(result.items || []); })
      .catch(() => { if (active) { setPdfContinues([]); setContinuesError("无法加载待续传列表，可稍后重试。"); } })
      .finally(() => { if (active) setContinuesLoading(false); });
    return () => { active = false; };
  }, [project.id]);

  const fileToBase64 = (file: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.onerror = () => reject(reader.error || new Error("读取文件失败，请重试。"));
    reader.readAsDataURL(file);
  });

  const routeDroppedFiles = async (files: File[]) => {
    if (!files.length) return;
    const images = files.filter((file) => file.type.startsWith("image/") || /\.(heic|heif)$/i.test(file.name));
    const pdfs = files.filter((file) => file.type === "application/pdf" || /\.pdf$/i.test(file.name));
    const latex = files.filter((file) => /\.(tex|ltx|zip|bib|gz|tgz|tar)$/i.test(file.name));
    if (images.length) await uploadPhotos(images);
    for (const file of pdfs) await uploadPdf(file);
    for (const file of latex) await uploadLatex(file);
    const other = files.filter((file) => !images.includes(file) && !pdfs.includes(file) && !latex.includes(file));
    for (const file of other) {
      try {
        setBusy("attach");
        const data = await fileToBase64(file);
        await importEmailAttachment(project.id, { filename: file.name, data, question: project.question || "" });
        feedback.notice(`已将 ${file.name} 作为附件入库。`);
      } catch (error) { feedback.error(error); } finally { setBusy(""); }
    }
  };

  const toggle = (setter: typeof setPickedStatements, index: number) => setter((current) => {
    const next = new Set(current);
    if (next.has(index)) next.delete(index); else next.add(index);
    return next;
  });

  const loadSuggestions = async (sourceId: string) => {
    try {
      const next = await suggestImportInsights(project.id, sourceId, project.question || "");
      setSuggestions(next);
    } catch {
      /* suggestions are optional convenience */
    }
  };

  const uploadLatex = async (file?: File) => {
    if (!file) return;
    setBusy("latex");
    try {
      const next = await importLatexProject(project.id, file);
      setLatex(next);
      setPickedStatements(new Set(next.statements.map((item) => item.index)));
      setPickedReferences(new Set(next.references.map((_, index) => index)));
      feedback.notice(`已解析 ${next.mainFile || file.name}：${next.statements.length} 条定理类环境，${next.references.length} 条参考文献。`);
      if (next.source?.id) void loadSuggestions(next.source.id);
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };
  const promote = async () => {
    if (!latex) return;
    setBusy("promote");
    try {
      const next = await promoteLatexImport(project.id, latex.source.id, { statements: [...pickedStatements], references: [...pickedReferences] });
      feedback.notice(`已加入证据图 ${next.nodes.length} 个节点、文献库 ${next.sources.length} 条文献。`);
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };
  const uploadPdf = async (file?: File, continueFrom?: Awaited<ReturnType<typeof importFormulaPdf>>) => {
    const target = file || pdfFile;
    if (!target) return;
    setBusy("pdf");
    setPdfFile(target);
    try {
      const next = await importFormulaPdf(project.id, target, pdfMode, {
        maxVisionPages,
        pageOffset: continueFrom?.nextPageOffset ?? 0,
        continueSourceId: continueFrom?.source?.id,
      });
      setPdfResult(next);
      const transcribed = Array.isArray(next.transcribedPages) ? next.transcribedPages : [];
      const vision = transcribed.length
        ? `，其中 ${transcribed.length} 页由视觉模型 ${next.visionModel || ""} 转写为 LaTeX`
        : "";
      const remaining = next.moreVisionPagesRemaining
        ? `；仍有 ${next.moreVisionPagesRemaining} 页待转写（本批上限 ${next.visionPageCap ?? maxVisionPages}）`
        : "";
      const unmet = (next.pagesNeedingVision || []).filter((page) => !transcribed.includes(page));
      const pending = unmet.length && !next.moreVisionPagesRemaining
        ? `；第 ${unmet.slice(0, 12).join("、")} 页仍缺可靠文本，请配置视觉模型或提高页数上限`
        : "";
      setPdfSummary(`已导入 ${next.pages} 页${vision}${remaining}${pending}。${(next.warnings || []).join(" ")}`);
      if (next.source?.id) void loadSuggestions(next.source.id);
      void listPdfContinues(project.id).then((result) => setPdfContinues(result.items || [])).catch(() => undefined);
      feedback.notice(remaining ? `PDF 已导入；可点击「继续转写剩余页」。` : `PDF 已导入 ${next.pages} 页。`);
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };
  const uploadPhotos = async (files: File[]) => {
    if (!files.length) return;
    const capped = files.slice(0, 24);
    if (files.length > capped.length) feedback.notice(`一次最多 24 张，已截取前 ${capped.length} 张。`);
    setBusy("photos");
    try {
      const next = await transcribeHandwriting(project.id, capped, hint, photoStyle);
      setHandwriting(next);
      setHandwritingText(next.transcription);
      setHandwritingFiles(capped.map((file) => file.name));
      setTitle(photoStyle === "whiteboard" ? "白板板书" : "手写推导");
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };
  const saveTranscription = async () => {
    if (!handwriting) return;
    setBusy("save");
    try {
      const source = await saveHandwritingTranscription(project.id, { title: title.trim() || "手写推导", content: handwritingText, model: handwriting.model, originals: handwritingFiles, reviewed: true });
      feedback.notice("转写结果已保存为项目资料。");
      if (source?.id) void loadSuggestions(source.id);
      setHandwriting(null);
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };

  const onGmailImport = async () => {
    if (!gmailMessageId.trim() || !gmailAttachmentId.trim()) {
      feedback.error(new Error("请填写 Gmail messageId 与 attachmentId。"));
      return;
    }
    setBusy("gmail");
    try {
      const result = await importGmailAttachment(project.id, {
        messageId: gmailMessageId.trim(),
        attachmentId: gmailAttachmentId.trim(),
        filename: gmailFilename.trim() || undefined,
        question: project.question || "",
        mode: pdfMode,
        maxVisionPages,
      });
      const source = (result as { source?: { id?: string; title?: string } }).source;
      feedback.notice(`已从 Gmail 导入 ${source?.title || gmailFilename || gmailAttachmentId}。`);
      if (source?.id) void loadSuggestions(source.id);
    } catch (error) { feedback.error(importErrorText(error)); } finally { setBusy(""); }
  };

  return (
    <div
      className={`research-tool-grid${dragOver ? " research-drop-active" : ""}`}
      onDragEnter={(event) => { event.preventDefault(); setDragOver(true); }}
      onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
      onDragLeave={(event) => { if (event.currentTarget === event.target) setDragOver(false); }}
      onDrop={(event) => {
        event.preventDefault();
        setDragOver(false);
        void routeDroppedFiles(Array.from(event.dataTransfer.files || []));
      }}
    >
      <p className="research-tool-notice" role="status">
        {dragOver ? "松开即可导入（按扩展名自动分流到 LaTeX / PDF / 手写）" : "可将文件拖放到此区域；也支持下方各入口。"}
      </p>
      {continuesLoading ? (
        <p className="research-muted-copy" role="status">正在加载待续传讲义状态…</p>
      ) : continuesError ? (
        <p className="research-error" role="alert">{continuesError}</p>
      ) : pdfContinues.length === 0 ? (
        <p className="research-muted-copy">当前没有待续传的长 PDF。导入讲义后若仍有剩余页，会出现在此处。</p>
      ) : null}
      {busy ? (
        <p className="research-muted-copy" role="status">正在处理导入任务，请稍候…</p>
      ) : null}
      {pdfContinues.length ? (
        <Card eyebrow="续传" title="待继续的讲义 / 长 PDF 视觉转写" icon={<BookOpen size={17} />}>
          <ul>{pdfContinues.map((item) => (
            <li key={item.sourceId} className="research-lab-inline">
              <span>{item.filename || item.sourceId} · 剩余约 {item.remaining ?? "?"} 页
                {item.hasOriginalFile ? <small>已保存原文件，可直接续传</small> : <small>{item.summary || "需重新选择同一 PDF"}</small>}
              </span>
              {item.hasOriginalFile ? (
                <ActionButton type="button" className="research-primary-button" disabled={busy === "pdf"}
                  onClick={() => void (async () => {
                    setBusy("pdf");
                    try {
                      const next = await continuePdfFromStored(project.id, item.sourceId, {
                        mode: (item.mode as "auto" | "text" | "vision") || pdfMode,
                        maxVisionPages: item.maxVisionPages ?? maxVisionPages,
                        pageOffset: item.nextPageOffset ?? undefined,
                      });
                      setPdfResult(next as never);
                      void listPdfContinues(project.id).then((result) => setPdfContinues(result.items || [])).catch(() => undefined);
                      const rem = Number((next as { moreVisionPagesRemaining?: number }).moreVisionPagesRemaining || 0);
                      feedback.notice(rem ? `已从保存的原文件继续；仍有 ${rem} 页待转写。` : "已从保存的原文件完成续传。");
                    } catch (error) { feedback.error(importErrorText(error)); } finally { setBusy(""); }
                  })()}>
                  <Busy on={busy === "pdf"}>无需重选 · 继续转写</Busy>
                </ActionButton>
              ) : (
                <ActionButton type="button" className="research-secondary-button" disabled={busy === "pdf" || !pdfFile}
                  onClick={() => void uploadPdf(undefined, {
                    source: { id: item.sourceId } as never,
                    nextPageOffset: item.nextPageOffset ?? 0,
                    moreVisionPagesRemaining: item.remaining,
                  } as never)}>
                  用当前 PDF 文件继续
                </ActionButton>
              )}
            </li>
          ))}</ul>
          <p className="research-muted-copy">若首次导入已保存原 PDF，可一键续传；否则请再次选择同一文件。</p>
        </Card>
      ) : null}
      <Card eyebrow="LaTeX" title="导入 .tex / .zip / arXiv 源码包" icon={<FileUp size={17} />}>
        <p className="research-muted-copy">自动识别主文件、展开 \input/\include（含子目录相对路径与无input file 无形式），抽取中英文定理/引理/定义与证明，解析 .bib。也支持单独导入 .bib。</p>
        <label className="research-attach-control"><Busy on={busy === "latex"}><FileUp size={15} /> 选择 .tex / .zip / .tar.gz / .bib</Busy><input aria-label="导入 LaTeX 工程" type="file" accept=".tex,.ltx,.zip,.bib,.gz,.tgz,.tar" disabled={Boolean(busy)} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; void uploadLatex(file); }} /></label>
        {latex ? (
          <div className="research-lab-picklist">
            <h5>定理类环境（{latex.statements.length}）</h5>
            {latex.statements.map((item) => (
              <label key={item.index} className="research-lab-inline">
                <input type="checkbox" checked={pickedStatements.has(item.index)} onChange={() => toggle(setPickedStatements, item.index)} />
                <span><b>{item.environment}{item.label ? ` (${item.label})` : ""}</b>{item.note ? ` [${item.note}]` : ""}{item.hasProof ? " · 含证明" : ""}<small>{item.statement.slice(0, 220)}</small></span>
              </label>
            ))}
            <h5>参考文献（{latex.references.length}）</h5>
            {latex.references.map((item, index) => (
              <label key={`${item.key}-${index}`} className="research-lab-inline">
                <input type="checkbox" checked={pickedReferences.has(index)} onChange={() => toggle(setPickedReferences, index)} />
                <span>{item.title || item.key}<small>{[item.authors, item.year, item.doi || item.arxivId].filter(Boolean).join(" · ")}</small></span>
              </label>
            ))}
            <ActionButton type="button" className="research-primary-button" disabled={busy === "promote" || (!pickedStatements.size && !pickedReferences.size)} onClick={() => void promote()}><Busy on={busy === "promote"}><GitBranch size={15} /> 加入证据图与文献库</Busy></ActionButton>
          </div>
        ) : null}
      </Card>
      <Card eyebrow="PDF" title="导入含公式的 PDF / 讲义长文档" icon={<BookOpen size={17} />}>
        <label className="research-field"><span className="research-field-label">识别方式</span>
          <select value={pdfMode} onChange={(event) => setPdfMode(event.target.value as typeof pdfMode)}>
            <option value="auto">自动（公式密集页用视觉模型）</option>
            <option value="text">仅文本层</option>
            <option value="vision">全部页面用视觉模型</option>
          </select>
        </label>
        <label className="research-field"><span className="research-field-label">本批视觉转写页数上限（讲义可调高，最大 120）</span>
          <input type="number" min={1} max={120} value={maxVisionPages} onChange={(event) => setMaxVisionPages(Math.max(1, Math.min(120, Number(event.target.value) || 40)))} />
        </label>
        <label className="research-attach-control"><Busy on={busy === "pdf"}><FileUp size={15} /> 选择 PDF</Busy><input aria-label="导入公式 PDF" type="file" accept=".pdf" disabled={Boolean(busy)} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; void uploadPdf(file); }} /></label>
        {pdfSummary ? <p className="research-tool-notice" role="status">{pdfSummary}</p> : null}
        {pdfResult?.moreVisionPagesRemaining ? (
          <ActionButton type="button" className="research-secondary-button" disabled={busy === "pdf" || !pdfFile} onClick={() => void uploadPdf(undefined, pdfResult)}>
            <Busy on={busy === "pdf"}>继续转写剩余 {pdfResult.moreVisionPagesRemaining} 页</Busy>
          </ActionButton>
        ) : null}
      </Card>
      <Card eyebrow="手写识别" title="手写 / 白板拍照识别" icon={<FileUp size={17} />}>
        <label className="research-field"><span className="research-field-label">类型</span>
          <select value={photoStyle} onChange={(event) => setPhotoStyle(event.target.value as typeof photoStyle)}>
            <option value="handwriting">手写推导</option>
            <option value="whiteboard">课堂白板</option>
          </select>
        </label>
        <label className="research-field"><span className="research-field-label">识别提示（可选）</span><input value={hint} onChange={(event) => setHint(event.target.value)} placeholder="例如：符号 ζ 表示 Riemann zeta 函数；从左到右两栏" /></label>
        <p className="research-muted-copy">支持 PNG/JPEG/WebP/HEIC（需 pillow-heif）。请转写后人工校对再保存。</p>
        <label className="research-attach-control"><Busy on={busy === "photos"}><FileUp size={15} /> 选择照片（最多 24 张）</Busy><input aria-label="上传手写照片" type="file" accept="image/*,.heic,.heif" multiple disabled={Boolean(busy)} onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; void uploadPhotos(files); }} /></label>
        {handwriting ? (
          <>
            {handwriting.notice ? <p className="research-muted-copy">{handwriting.notice}</p> : null}
            {handwriting.reviewChecklist?.length ? (
              <ul className="research-muted-copy">{handwriting.reviewChecklist.map((item) => <li key={item}>{item}</li>)}</ul>
            ) : null}
            <label className="research-field"><span className="research-field-label">转写结果（请校对后保存）</span><textarea rows={8} value={handwritingText} onChange={(event) => setHandwritingText(event.target.value)} /></label>
            <div className="research-lab-preview"><MarkdownRenderer content={handwritingText} allowHtml={false} /></div>
            <label className="research-field"><span className="research-field-label">资料标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
            <ActionButton type="button" className="research-primary-button" disabled={busy === "save" || !handwritingText.trim()} onClick={() => void saveTranscription()}><Busy on={busy === "save"}>保存到项目资料</Busy></ActionButton>
          </>
        ) : null}
      </Card>
      {suggestions ? (
        <Card eyebrow="导入建议" title="导入后建议（对齐课题）" icon={<BookOpen size={17} />}>
          <p className="research-muted-copy">{suggestions.notice}</p>
          {suggestions.alignedClaims.length ? (
            <>
              <h5>与课题相关的开放命题（{suggestions.alignedClaims.length}）</h5>
              <ul>{suggestions.alignedClaims.slice(0, 8).map((item, index) => (
                <li key={`a-${index}`}><b>{item.kind}</b> {item.label ? `(${item.label}) ` : ""}{item.statement.slice(0, 180)}</li>
              ))}</ul>
            </>
          ) : null}
          {suggestions.computableExamples.length ? (
            <>
              <h5>可计算的例子 / 练习</h5>
              <ul>{suggestions.computableExamples.slice(0, 6).map((item, index) => <li key={`e-${index}`}>{item.slice(0, 200)}</li>)}</ul>
            </>
          ) : null}
          {suggestions.relatedLiterature.length ? (
            <>
              <h5>相关文献线索</h5>
              <ul>{suggestions.relatedLiterature.slice(0, 8).map((item, index) => (
                <li key={`l-${index}`}>{item.title}{[item.authors, item.year, item.doi || item.arxivId].filter(Boolean).length ? ` · ${[item.authors, item.year, item.doi || item.arxivId].filter(Boolean).join(" · ")}` : ""}</li>
              ))}</ul>
            </>
          ) : null}
        </Card>
      ) : null}
      <Card eyebrow="邮件附件" title="Gmail 附件一键入库" icon={<FileUp size={17} />}>
        <p className={`research-muted-copy${gmailStatus && !gmailStatus.available ? " research-warn-copy" : ""}`}>
          <strong>{gmailLoading ? "拉取通道：检查中…" : gmailStatus?.available ? "拉取通道：可用" : "拉取通道：需本机上传 / 智能体协助"}</strong>
          {" — "}
          {gmailStatus?.available
            ? "可只填 messageId 与 attachmentId，服务器将代为拉取。"
            : (gmailStatus?.stubMessage || "请上传已下载附件，或配置 ALEGGO_GMAIL_ATTACHMENT_FETCH_URL。")}
        </p>
        <label className="research-field"><span className="research-field-label">Gmail 消息 ID</span>
          <input value={gmailMessageId} onChange={(event) => setGmailMessageId(event.target.value)} placeholder="18f…" /></label>
        <label className="research-field"><span className="research-field-label">附件 ID</span>
          <input value={gmailAttachmentId} onChange={(event) => setGmailAttachmentId(event.target.value)} placeholder="附件 ID" /></label>
        <label className="research-field"><span className="research-field-label">文件名（可选）</span>
          <input value={gmailFilename} onChange={(event) => setGmailFilename(event.target.value)} placeholder="notes.pdf / paper.zip" /></label>
        <label className="research-attach-control">也可先本地下载再上传字节
          <input aria-label="上传已下载的邮件附件" type="file" disabled={Boolean(busy)} onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (!file) return;
            void (async () => {
              setBusy("gmail");
              try {
                const data = await fileToBase64(file);
                if (gmailMessageId.trim() && gmailAttachmentId.trim()) {
                  await importGmailAttachment(project.id, {
                    messageId: gmailMessageId.trim(), attachmentId: gmailAttachmentId.trim(),
                    filename: file.name, data, question: project.question || "", mode: pdfMode, maxVisionPages,
                  });
                } else {
                  await importEmailAttachment(project.id, { filename: file.name, data, question: project.question || "", mode: pdfMode, maxVisionPages });
                }
                feedback.notice(`已导入邮件附件 ${file.name}。`);
              } catch (error) { feedback.error(error); } finally { setBusy(""); }
            })();
          }} />
        </label>
        <ActionButton type="button" className="research-primary-button" disabled={busy === "gmail"} onClick={() => void onGmailImport()}>
          <Busy on={busy === "gmail"}>从 Gmail 元数据导入</Busy>
        </ActionButton>
      </Card>
    </div>
  );
}

export function ProofTree({ node, stepsById, affected }: { node: ProofTreeNode; stepsById: Map<string, { text: string }>; affected: Record<string, string[]> }) {
  const issues = affected[node.id] ?? [];
  const selfBad = node.verdict === "gap" || node.verdict === "error";
  const marked = issues.length > 0 || selfBad || Object.prototype.hasOwnProperty.call(affected, node.id);
  return (
    <li className="research-proof-tree-node" data-verdict={node.verdict} data-affected={marked ? "true" : "false"}>
      <span className="research-proof-tree-label">
        <b>{node.id}</b> <span className="research-proof-verdict" data-verdict={node.verdict}>{VERDICT_LABEL[node.verdict] ?? node.verdict}</span>
        {node.repeated ? <small>（见上文）</small> : <small>{stepsById.get(node.id)?.text.slice(0, 120)}</small>}
        {issues.length && !node.repeated ? <small className="research-lab-warn">依赖有问题的步骤：{issues.join("、")}</small> : null}
        {selfBad && !issues.length && !node.repeated ? <small className="research-lab-warn">本步存在缺口或错误，后续依赖可能受影响</small> : null}
      </span>
      {node.children.length && !node.repeated ? <ul>{node.children.map((child, index) => <ProofTree key={`${child.id}-${index}`} node={child} stepsById={stepsById} affected={affected} />)}</ul> : null}
    </li>
  );
}

function ReviewTab({ project, feedback }: TabProps) {
  const [items, setItems] = useState<ProofReviewSummary[]>([]);
  const [review, setReview] = useState<ProofReview | null>(null);
  const [statement, setStatement] = useState("");
  const [proof, setProof] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [reviewLoading, setReviewLoading] = useState(true);
  const [reviewLoadError, setReviewLoadError] = useState("");

  const loadList = useCallback(async () => {
    setReviewLoading(true);
    try {
      setItems((await listProofReviews(project.id)).items);
      setReviewLoadError("");
    } catch (error) {
      setReviewLoadError(importErrorText(error));
      feedback.error(error);
    } finally {
      setReviewLoading(false);
    }
  }, [feedback, project.id]);
  useEffect(() => { void loadList(); }, [loadList]);

  const open = useCallback(async (reviewId: string) => {
    try {
      const next = await getProofReview(project.id, reviewId);
      setReview(next);
      setNotes(Object.fromEntries(next.steps.map((step) => [step.id, step.researcherNote || ""])));
      if (next.state !== "RUNNING") void loadList();
    } catch (error) { feedback.error(error); }
  }, [feedback, loadList, project.id]);
  usePolling(review?.state === "RUNNING", () => (review ? open(review.id) : undefined));

  const submit = async () => {
    if (!proof.trim()) return;
    setBusy("start");
    try {
      const next = await startProofReview(project.id, { proof: proof.trim(), statement: statement.trim(), title: title.trim() });
      setReview(next);
      void loadList();
      feedback.notice("已开始逐步审查：先拆分证明步骤，再逐步检查并在可能时调用计算工具验证。");
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };
  const annotate = async (stepId: string, verdict: "" | ProofVerdict) => {
    if (!review) return;
    setBusy(stepId);
    try { setReview(await annotateProofStep(project.id, review.id, stepId, verdict, notes[stepId] ?? "")); } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };

  const stepsById = useMemo(() => new Map((review?.steps ?? []).map((step) => [step.id, step])), [review]);
  const affected = useMemo(() => review?.tree?.affectedByIssues ?? {}, [review]);
  return (
    <div className="research-tool-grid">
      <Card eyebrow="逐步审查" title="逐步审查一份证明" icon={<ShieldCheck size={17} />}>
        <label className="research-field"><span className="research-field-label">标题（可选）</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label className="research-field"><span className="research-field-label">待证命题（可选）</span><textarea rows={2} value={statement} onChange={(event) => setStatement(event.target.value)} placeholder="支持 LaTeX，例如 $\sum_{k=1}^n k = n(n+1)/2$" /></label>
        <label className="research-field"><span className="research-field-label">证明全文</span><textarea rows={8} value={proof} onChange={(event) => setProof(event.target.value)} /></label>
        <ActionButton type="button" className="research-primary-button" disabled={busy === "start" || !proof.trim()} onClick={() => void submit()}><Busy on={busy === "start"}>开始审查</Busy></ActionButton>
        {items.length ? (
          <div className="research-lab-history">
            <h5>历史审查</h5>
            {reviewLoading ? <p className="research-muted-copy" role="status">正在加载证明审查列表…</p> : null}
          {reviewLoadError ? <p className="research-error" role="alert">{reviewLoadError}</p> : null}
          {!reviewLoading && !items.length ? <p className="research-muted-copy">暂无证明审查。粘贴证明文本后即可开始逐步审查。</p> : null}
          {items.map((item) => <ActionButton key={item.id} type="button" className={review?.id === item.id ? "is-active" : ""} onClick={() => void open(item.id)}>{item.title || item.id} · {item.state === "COMPLETED" ? OVERALL_LABEL[item.overall] ?? item.overall : item.state} · {item.stepCount} 步</ActionButton>)}
          </div>
        ) : null}
      </Card>
      {review ? (
        <div className="research-graph-ledger research-proof-review" aria-label="证明审查结果">
          <h4>{review.title || "证明审查"} <small>{review.state === "RUNNING" ? "审查中…" : OVERALL_LABEL[review.overall] ?? review.state}</small></h4>
          {review.error ? <p className="research-error" role="alert">{review.error}</p> : null}
          {review.statement ? <div className="research-lab-preview"><MarkdownRenderer content={review.statement} allowHtml={false} /></div> : null}
          {review.summary ? <p>{review.summary}</p> : null}
          {review.missingHypotheses?.length ? <p className="research-lab-warn">可能缺失的假设：{review.missingHypotheses.join("；")}</p> : null}
          {review.tree?.tree ? (
            <div className="research-proof-tree" aria-label="证明树">
              <h5>证明树（结论 ← 依赖）</h5>
              <ul><ProofTree node={review.tree.tree} stepsById={stepsById} affected={affected} /></ul>
            </div>
          ) : null}
          <ol className="research-proof-steps">
            {review.steps.map((step) => (
              <li key={step.id} data-verdict={step.researcherVerdict || step.verdict}>
                <div className="research-proof-step-head">
                  <b>{step.id}</b>
                  <span className="research-proof-verdict" data-verdict={step.verdict}>AI：{VERDICT_LABEL[step.verdict] ?? step.verdict}</span>
                  {step.researcherVerdict ? <span className="research-proof-verdict" data-verdict={step.researcherVerdict}>你：{VERDICT_LABEL[step.researcherVerdict]}</span> : null}
                  {step.dependsOn.length ? <small>依赖 {step.dependsOn.join("、")}</small> : null}
                </div>
                <MarkdownRenderer content={step.text} allowHtml={false} />
                {step.issue ? <p className="research-lab-warn">问题：{step.issue}</p> : null}
                {step.suggestion ? <p className="research-muted-copy">建议：{step.suggestion}</p> : null}
                {step.check ? <p className="research-muted-copy">计算核验（{step.check.tool ?? "工具"}）：{step.check.ok ? "通过" : "未通过"} {step.check.summary ?? ""}</p> : null}
                {review.state !== "RUNNING" ? (
                  <div className="research-lab-actions">
                    <input aria-label={`${step.id} 批注`} value={notes[step.id] ?? ""} placeholder="你的批注" onChange={(event) => setNotes((current) => ({ ...current, [step.id]: event.target.value }))} />
                    <select aria-label={`${step.id} 你的判断`} value={step.researcherVerdict} disabled={busy === step.id} onChange={(event) => void annotate(step.id, event.target.value as "" | ProofVerdict)}>
                      <option value="">采纳 AI 判断</option>
                      <option value="valid">成立</option>
                      <option value="gap">有缺口</option>
                      <option value="error">错误</option>
                      <option value="unclear">需澄清</option>
                    </select>
                    <ActionButton type="button" className="research-secondary-button" disabled={busy === step.id} onClick={() => void annotate(step.id, step.researcherVerdict)}>保存批注</ActionButton>
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
          <p className="research-proof-gate"><ShieldCheck size={14} /> AI 审查只是辅助意见；“未发现问题”不等于证明正确，只有 Lean 内核检查才能标记为已验证。</p>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cross-project long-term memory
// ---------------------------------------------------------------------------

function MemoryTab({ project, feedback }: TabProps) {
  const [items, setItems] = useState<LongTermMemory[]>([]);
  const [memoryLoading, setMemoryLoading] = useState(true);
  const [suggestions, setSuggestions] = useState<MemorySuggestion[]>([]);
  const [related, setRelated] = useState<{ projectId: string; question: string; status: string; score?: number }[]>([]);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [content, setContent] = useState("");
  const [newKind, setNewKind] = useState<LongTermMemoryKind>("technique");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    setMemoryLoading(true);
    try {
      const [memory, suggested, relatedProjects] = await Promise.all([listLongTermMemory(query, kind), getMemorySuggestions(project.id), getRelatedProjects(project.id)]);
      setItems(memory.items);
      setSuggestions(suggested.items);
      setRelated(relatedProjects.items);
    } catch (error) { feedback.error(error); } finally { setMemoryLoading(false); }
  }, [feedback, kind, project.id, query]);
  useEffect(() => { void load(); }, [load]);

  const add = async () => {
    if (!content.trim()) return;
    setBusy("add");
    try {
      await createLongTermMemory({ content: content.trim(), kind: newKind, tags: tags.split(/[,，]/).map((item) => item.trim()).filter(Boolean), confirmed: true });
      setContent(""); setTags("");
      await load();
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };
  const distill = async () => {
    setBusy("distill");
    try {
      const next = await distillProjectMemory(project.id);
      feedback.notice(next.items.length ? `从本项目提炼出 ${next.items.length} 条候选记忆，确认后才会在其他项目中使用。` : "本项目暂时没有可提炼的新经验。");
      await load();
    } catch (error) { feedback.error(error); } finally { setBusy(""); }
  };
  const toggleConfirm = async (item: LongTermMemory) => {
    try { await updateLongTermMemory(item.id, { confirmed: !item.confirmed }); await load(); } catch (error) { feedback.error(error); }
  };
  const remove = async (item: LongTermMemory) => {
    try { await deleteLongTermMemory(item.id); await load(); } catch (error) { feedback.error(error); }
  };

  const row = (item: LongTermMemory) => (
    <article key={item.id} className="research-lab-result" data-confirmed={item.confirmed ? "true" : "false"}>
      <span className="research-graph-type">{MEMORY_KIND_LABEL[item.kind] ?? item.kind}</span>
      <MarkdownRenderer content={item.content} allowHtml={false} />
      <small>{item.confirmed ? "已确认" : "待确认"}{item.tags.length ? ` · ${item.tags.join("、")}` : ""}{item.originProject ? ` · 来自项目 ${item.originProject.slice(0, 8)}` : ""} · 被引用 {item.useCount} 次</small>
      <div className="research-lab-actions">
        <ActionButton type="button" className="research-secondary-button" onClick={() => void toggleConfirm(item)}>{item.confirmed ? "撤销确认" : <><Check size={13} /> 确认</>}</ActionButton>
        <ActionButton type="button" className="research-secondary-button" aria-label="删除记忆" onClick={() => void remove(item)}><Trash2 size={13} /></ActionButton>
      </div>
    </article>
  );

  return (
    <div className="research-tool-grid">
      <Card eyebrow="长期记忆" title="跨项目长期记忆" icon={<BookOpen size={17} />}>
        <p className="research-muted-copy">已确认的技巧、结论和失败路线会在新项目规划和讨论时自动检索并注明来源；未确认的条目不会被 AI 使用。</p>
        <label className="research-field"><span className="research-field-label">新记忆</span><textarea rows={3} value={content} onChange={(event) => setContent(event.target.value)} placeholder="例如：处理 $\sum 1/p$ 型估计时，先用 Mertens 定理" /></label>
        <div className="research-control-grid">
          <label className="research-field"><span className="research-field-label">类型</span><select value={newKind} onChange={(event) => setNewKind(event.target.value as LongTermMemoryKind)}>{Object.entries(MEMORY_KIND_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label className="research-field"><span className="research-field-label">标签（逗号分隔）</span><input value={tags} onChange={(event) => setTags(event.target.value)} /></label>
        </div>
        <div className="research-lab-actions">
          <ActionButton type="button" className="research-primary-button" disabled={busy === "add" || !content.trim()} onClick={() => void add()}><Busy on={busy === "add"}><Plus size={14} /> 保存记忆</Busy></ActionButton>
          <ActionButton type="button" className="research-secondary-button" disabled={busy === "distill"} onClick={() => void distill()}><Busy on={busy === "distill"}>从本项目提炼经验</Busy></ActionButton>
        </div>
        {related.length ? (
          <div className="research-lab-history">
            <h5>相关的历史项目</h5>
            {related.map((item) => <p key={item.projectId} className="research-muted-copy">{item.question.slice(0, 120)} <small>（{item.status}）</small></p>)}
          </div>
        ) : null}
      </Card>
      <div className="research-graph-ledger" aria-label="长期记忆列表">
        <div className="research-control-grid">
          <label className="research-field"><span className="research-field-label">搜索</span><input value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          <label className="research-field"><span className="research-field-label">类型</span><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="">全部</option>{Object.entries(MEMORY_KIND_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        </div>
        {suggestions.length ? (
          <>
            <h4>本项目可提炼的经验 <small>{suggestions.length} 条 · 尚未保存，点击“从本项目提炼经验”后作为待确认记忆保存</small></h4>
            {suggestions.map((item) => (
              <article key={item.suggestionKey} className="research-lab-result" data-confirmed="false">
                <span className="research-graph-type">{MEMORY_KIND_LABEL[item.kind] ?? item.kind}</span>
                <p>{item.content}</p>
              </article>
            ))}
          </>
        ) : null}
        <h4>全部记忆 <small>{items.length}</small></h4>
        {memoryLoading ? <p className="research-muted-copy" role="status">正在加载长期记忆…</p> : items.length ? items.map(row) : <p className="research-muted-copy">还没有长期记忆。可先「从本项目提炼经验」，确认后再供 AI 使用。</p>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Collaboration: members, comments, assignments
// ---------------------------------------------------------------------------

function CollaborationTab({ project, feedback }: TabProps) {
  const [members, setMembers] = useState<ProjectMembers | null>(null);
  const [collabLoading, setCollabLoading] = useState(true);
  const [candidates, setCandidates] = useState<{ id: string; username: string }[]>([]);
  const [multiUser, setMultiUser] = useState(true);
  const [comments, setComments] = useState<ResearchComment[]>([]);
  const [assignments, setAssignments] = useState<ResearchAssignment[]>([]);
  const [memberId, setMemberId] = useState("");
  const [role, setRole] = useState<CollaborationRole>("editor");
  const [commentBody, setCommentBody] = useState("");
  const [commentTarget, setCommentTarget] = useState("project");
  const [assignStep, setAssignStep] = useState("");
  const [assignee, setAssignee] = useState("");
  const [assignNote, setAssignNote] = useState("");
  const steps = project.plan?.steps ?? [];

  const fetchAll = useCallback(() => Promise.all([getCollaborationCandidates(), getProjectMembers(project.id), listResearchComments(project.id), listResearchAssignments(project.id)]), [project.id]);
  const apply = useCallback(([who, team, thread, tasks]: Awaited<ReturnType<typeof fetchAll>>) => {
    setMultiUser(who.multiUser);
    setCandidates(who.items.filter((item) => item.id !== who.me.id));
    setMembers(team);
    setComments(thread.items);
    setAssignments(tasks.items);
  }, []);
  const load = useCallback(async () => {
    setCollabLoading(true);
    try { apply(await fetchAll()); } catch (error) { feedback.error(error); } finally { setCollabLoading(false); }
  }, [apply, feedback, fetchAll]);
  useEffect(() => {
    let active = true;
    fetchAll().then((data) => { if (active) { apply(data); setCollabLoading(false); } }).catch((error: unknown) => { if (active) { feedback.error(error); setCollabLoading(false); } });
    return () => { active = false; };
  }, [apply, feedback, fetchAll]);

  const act = async (action: () => Promise<unknown>, done?: string) => {
    try { await action(); if (done) feedback.notice(done); await load(); } catch (error) { feedback.error(error); }
  };
  const owner = members?.myRole === "owner";
  const canEdit = owner || members?.myRole === "editor";
  const people = [...(members ? [members.owner] : []), ...(members?.members ?? [])];
  const stepTitle = (stepId: string) => steps.find((step) => step.id === stepId)?.title ?? stepId;
  const topLevel = comments.filter((item) => !item.parentId);

  return (
    <div className="research-tool-grid">
      {collabLoading ? <p className="research-muted-copy" role="status">正在加载协作信息…</p> : null}
      <Card eyebrow="团队" title="项目成员" icon={<Users size={17} />}>
        {!multiUser ? <p className="research-lab-warn">当前是单用户模式。启用多用户登录后，才能把项目共享给其他老师或学生。</p> : null}
        {members ? (
          <ul className="research-lab-checklist">
            <li data-ok="true"><b>{members.owner.username}</b><small>所有者</small></li>
            {members.members.map((item) => (
              <li key={item.id} data-ok="true"><b>{item.username}</b><small>{item.role === "editor" ? "可编辑" : "只读"}</small>
                {owner ? <ActionButton type="button" className="research-secondary-button" aria-label={`移除 ${item.username}`} onClick={() => void act(() => removeProjectMember(project.id, item.id), "已移除成员。")}><Trash2 size={13} /></ActionButton> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {owner && multiUser ? (
          <div className="research-control-grid">
            <label className="research-field"><span className="research-field-label">添加成员</span><select value={memberId} onChange={(event) => setMemberId(event.target.value)}><option value="">选择账号</option>{candidates.map((item) => <option key={item.id} value={item.id}>{item.username}</option>)}</select></label>
            <label className="research-field"><span className="research-field-label">权限</span><select value={role} onChange={(event) => setRole(event.target.value as CollaborationRole)}><option value="editor">可编辑</option><option value="viewer">只读</option></select></label>
            <ActionButton type="button" className="research-primary-button" disabled={!memberId} onClick={() => void act(() => addProjectMember(project.id, memberId, role), "已共享项目。")}><Plus size={14} /> 共享</ActionButton>
          </div>
        ) : null}
        {members && !owner ? <p className="research-muted-copy">你在此项目中的权限：{members.myRole === "editor" ? "可编辑" : "只读"}</p> : null}
      </Card>

      <Card eyebrow="分工" title="分工" icon={<GitBranch size={17} />}>
        {canEdit && steps.length ? (
          <>
            <div className="research-control-grid">
              <label className="research-field"><span className="research-field-label">研究步骤</span><select value={assignStep} onChange={(event) => setAssignStep(event.target.value)}><option value="">选择步骤</option>{steps.map((step) => <option key={step.id} value={step.id}>{step.title}</option>)}</select></label>
              <label className="research-field"><span className="research-field-label">负责人</span><select value={assignee} onChange={(event) => setAssignee(event.target.value)}><option value="">选择成员</option>{people.map((item) => <option key={item.id} value={item.id}>{item.username}</option>)}</select></label>
            </div>
            <label className="research-field"><span className="research-field-label">说明（可选）</span><input value={assignNote} onChange={(event) => setAssignNote(event.target.value)} /></label>
            <ActionButton type="button" className="research-secondary-button" disabled={!assignStep || !assignee} onClick={() => void act(async () => { await addResearchAssignment(project.id, { stepId: assignStep, assigneeId: assignee, note: assignNote }); setAssignNote(""); }, "已分配。")}>分配</ActionButton>
          </>
        ) : null}
        {assignments.map((item) => (
          <p key={item.id} className="research-lab-inline" data-status={item.status}>
            <input type="checkbox" aria-label={`${stepTitle(item.stepId)} 已完成`} checked={item.status === "done"} disabled={!canEdit} onChange={() => void act(() => updateResearchAssignment(project.id, item.id, item.status === "done" ? "open" : "done"))} />
            <span>{stepTitle(item.stepId)} → <b>{item.assignee}</b>{item.note ? <small>{item.note}</small> : null}</span>
          </p>
        ))}
        {!assignments.length ? <p className="research-muted-copy">还没有分工。</p> : null}
      </Card>

      <div className="research-graph-ledger" aria-label="讨论与批注">
        <h4>讨论与批注 <small>{comments.length}</small></h4>
        <div className="research-control-grid">
          <label className="research-field"><span className="research-field-label">针对</span><select value={commentTarget} onChange={(event) => setCommentTarget(event.target.value)}><option value="project">整个项目</option>{steps.map((step) => <option key={step.id} value={`step:${step.id}`}>步骤：{step.title}</option>)}</select></label>
        </div>
        <label className="research-field"><span className="research-field-label">评论（支持 LaTeX）</span><textarea rows={3} value={commentBody} onChange={(event) => setCommentBody(event.target.value)} /></label>
        <ActionButton type="button" className="research-primary-button" disabled={!commentBody.trim()} onClick={() => void act(async () => {
          const [targetType, targetId] = commentTarget.startsWith("step:") ? ["step", commentTarget.slice(5)] as const : ["project", ""] as const;
          await addResearchComment(project.id, { body: commentBody.trim(), targetType, targetId });
          setCommentBody("");
        })}><MessageSquare size={14} /> 发表</ActionButton>
        {topLevel.map((item) => (
          <article key={item.id} className="research-lab-result" data-resolved={item.resolved ? "true" : "false"}>
            <small><b>{item.author}</b> · {item.targetType === "step" ? `步骤：${stepTitle(item.targetId)}` : "项目"} · {new Date(item.createdAt).toLocaleString("zh-CN")}{item.resolved ? " · 已解决" : ""}</small>
            <MarkdownRenderer content={item.body} allowHtml={false} />
            {comments.filter((reply) => reply.parentId === item.id).map((reply) => <blockquote key={reply.id}><small>{reply.author}</small><MarkdownRenderer content={reply.body} allowHtml={false} /></blockquote>)}
            <div className="research-lab-actions">
              <ActionButton type="button" className="research-secondary-button" onClick={() => void act(() => updateResearchComment(project.id, item.id, { resolved: !item.resolved }))}>{item.resolved ? "重新打开" : "标记已解决"}</ActionButton>
              <ActionButton type="button" className="research-secondary-button" aria-label="删除评论" onClick={() => void act(() => deleteResearchComment(project.id, item.id))}><Trash2 size={13} /></ActionButton>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Usage and cost
// ---------------------------------------------------------------------------

function UsageTab({ project, isAdmin, feedback }: TabProps) {
  const [usage, setUsage] = useState<ProjectUsage | null>(null);
  const [prices, setPrices] = useState<UsagePrices>({ currency: "CNY", models: {} });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [next, priceTable] = await Promise.all([getProjectUsage(project.id), getUsagePrices()]);
      setUsage(next);
      setPrices(priceTable);
    } catch (error) { feedback.error(error); }
  }, [feedback, project.id]);
  useEffect(() => { void load(); }, [load]);

  const models = useMemo(() => Array.from(new Set([...(usage?.byModel ?? []).map((item) => item.model), ...Object.keys(prices.models)])), [prices.models, usage]);
  const setPrice = (model: string, field: "prompt" | "completion", value: string) => setPrices((current) => ({
    ...current, models: { ...current.models, [model]: { prompt: current.models[model]?.prompt ?? 0, completion: current.models[model]?.completion ?? 0, [field]: Number(value) || 0 } },
  }));
  const save = async () => {
    setBusy(true);
    try { setPrices(await saveUsagePrices(prices)); feedback.notice("单价已保存，费用会按新单价重新估算。"); await load(); } catch (error) { feedback.error(error); } finally { setBusy(false); }
  };

  if (!usage) return <p className="research-muted-copy">正在统计用量…</p>;
  const table = (title: string, rows: { key: string; bucket: ProjectUsage["total"] }[]) => (
    <table className="research-lab-table" aria-label={title}>
      <thead><tr><th>{title}</th><th>调用</th><th>输入 tokens</th><th>输出 tokens</th><th>估算费用</th></tr></thead>
      <tbody>{rows.map(({ key, bucket }) => <tr key={key}><td>{key}</td><td>{formatNumber(bucket.calls)}</td><td>{formatNumber(bucket.promptTokens)}</td><td>{formatNumber(bucket.completionTokens)}</td><td>{formatCost(bucket.cost, usage.currency, bucket.priced)}</td></tr>)}</tbody>
    </table>
  );
  return (
    <div className="research-tool-grid">
      <Card eyebrow="用量" title="本项目模型用量" icon={<BarChart3 size={17} />}>
        <p className="research-lab-total"><b>{formatNumber(usage.total.totalTokens)}</b> tokens · {formatNumber(usage.total.calls)} 次调用 · {formatCost(usage.total.cost, usage.currency, usage.total.priced)}</p>
        {usage.untrackedCalls ? <p className="research-muted-copy">另有 {usage.untrackedCalls} 次调用的服务商未返回 token 数。</p> : null}
        {!usage.pricesConfigured ? <p className="research-muted-copy">尚未配置模型单价，费用暂不估算。</p> : null}
        {table("按角色", usage.byRole.map((item) => ({ key: item.role, bucket: item })))}
        {table("按模型", usage.byModel.map((item) => ({ key: item.model, bucket: item })))}
        {table("按日期", usage.byDay.map((item) => ({ key: item.date, bucket: item })))}
      </Card>
      <Card eyebrow="单价" title="模型单价（每千 tokens）" icon={<Coins size={17} />}>
        <label className="research-field"><span className="research-field-label">币种</span><input value={prices.currency} disabled={!isAdmin} onChange={(event) => setPrices((current) => ({ ...current, currency: event.target.value }))} /></label>
        <table className="research-lab-table">
          <thead><tr><th>模型</th><th>输入</th><th>输出</th></tr></thead>
          <tbody>{models.map((model) => (
            <tr key={model}><td>{model}</td>
              <td><input aria-label={`${model} 输入单价`} type="number" min={0} step="0.0001" disabled={!isAdmin} value={prices.models[model]?.prompt ?? 0} onChange={(event) => setPrice(model, "prompt", event.target.value)} /></td>
              <td><input aria-label={`${model} 输出单价`} type="number" min={0} step="0.0001" disabled={!isAdmin} value={prices.models[model]?.completion ?? 0} onChange={(event) => setPrice(model, "completion", event.target.value)} /></td>
            </tr>
          ))}</tbody>
        </table>
        {isAdmin ? <ActionButton type="button" className="research-primary-button" disabled={busy || !models.length} onClick={() => void save()}><Busy on={busy}>保存单价</Busy></ActionButton> : null}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Evaluation harness
// ---------------------------------------------------------------------------

const EVAL_MODELS = [
  { id: "research.lumen", label: "Lumen" },
  { id: "research.meridian", label: "Meridian" },
  { id: "research.axiom", label: "Axiom" },
];

function EvalTab({ isAdmin, feedback }: TabProps) {
  const [categories, setCategories] = useState<Record<string, number>>({});
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [modelId, setModelId] = useState("research.lumen");
  const [limit, setLimit] = useState(0);
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [run, setRun] = useState<EvalRun | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [problems, history] = await Promise.all([getEvalProblems(), listEvalRuns()]);
      setCategories(problems.categories);
      setRuns(history.items);
    } catch (error) { feedback.error(error); }
  }, [feedback]);
  useEffect(() => { void load(); }, [load]);

  const open = useCallback(async (runId: string) => {
    try {
      const next = await getEvalRun(runId);
      setRun(next);
      if (next.state !== "RUNNING") setRuns((await listEvalRuns()).items);
    } catch (error) { feedback.error(error); }
  }, [feedback]);
  usePolling(run?.state === "RUNNING", () => (run ? open(run.id) : undefined), 4000);

  const start = async () => {
    setBusy(true);
    try {
      const next = await startEvalRun({ modelId, categories: [...picked], ...(limit > 0 ? { limit } : {}) });
      setRun(next);
      feedback.notice("评测已开始，会真实调用所选模型并消耗额度。");
      void load();
    } catch (error) { feedback.error(error); } finally { setBusy(false); }
  };

  const total = Object.values(categories).reduce((sum, value) => sum + value, 0);
  return (
    <div className="research-tool-grid">
      <Card eyebrow="评测" title="研究能力评测" icon={<FlaskConical size={17} />}>
        <p className="research-muted-copy">内置 {total} 道可自动判分的题目（计算、反例、文献检索、形式化等），用于比较模型与配置。</p>
        <div className="research-lab-picklist">
          {Object.entries(categories).map(([name, count]) => (
            <label key={name} className="research-lab-inline"><input type="checkbox" checked={picked.has(name)} onChange={() => setPicked((current) => { const next = new Set(current); if (next.has(name)) next.delete(name); else next.add(name); return next; })} /> {EVAL_CATEGORY_LABEL[name] || name} <small>{count} 题</small></label>
          ))}
        </div>
        <div className="research-control-grid">
          <label className="research-field"><span className="research-field-label">题目上限（0 = 全部）</span><input type="number" min={0} value={limit} onChange={(event) => setLimit(Math.max(0, Number(event.target.value) || 0))} /></label>
        </div>
        {isAdmin ? <ActionButton type="button" className="research-primary-button" disabled={busy || run?.state === "RUNNING"} onClick={() => void start()}><Busy on={busy}>开始评测</Busy></ActionButton> : <p className="research-muted-copy">评测会消耗模型额度，仅管理员可启动。</p>}
        {runs.length ? (
          <div className="research-lab-history">
            <h5>历史评测</h5>
            {runs.map((item) => <ActionButton key={item.id} type="button" className={run?.id === item.id ? "is-active" : ""} onClick={() => void open(item.id)}>{item.model} · {EVAL_STATE_LABEL[item.state] || item.state} · {item.summary?.score != null ? `${Math.round(item.summary.score * 100)}%` : "—"} · {new Date(item.startedAt).toLocaleString("zh-CN")}</ActionButton>)}
          </div>
        ) : null}
      </Card>
      {run ? (
        <div className="research-graph-ledger" aria-label="评测报告">
          <h4>{run.model} <small>{EVAL_STATE_LABEL[run.state] || run.state}{run.summary ? ` · 得分 ${run.summary.score != null ? `${Math.round(run.summary.score * 100)}%` : "—"}（${run.summary.passed}/${run.summary.scored}）` : ""}</small></h4>
          {run.state === "RUNNING" ? (
            <p className="research-muted-copy" role="status">进度：已完成 {run.results.length} / {run.problemCount || run.summary?.total || "?"} 题（约每 4 秒刷新）</p>
          ) : null}
          {run.error ? <p className="research-error" role="alert">{run.error}</p> : null}
          {run.state === "RUNNING" ? <ActionButton type="button" className="research-secondary-button" onClick={() => void cancelEvalRun(run.id).then((next) => { setRun(next); feedback.notice("已请求取消评测，当前题目结束后会停止。"); }).catch(feedback.error)}>取消评测</ActionButton> : null}
          {run.summary ? (
            <table className="research-lab-table" aria-label="分类得分">
              <thead><tr><th>类别</th><th>通过</th><th>得分</th></tr></thead>
              <tbody>{Object.entries(run.summary.byCategory).map(([name, bucket]) => <tr key={name}><td>{EVAL_CATEGORY_LABEL[name] || name}</td><td>{bucket.passed}/{bucket.total}</td><td>{bucket.score != null ? `${Math.round(bucket.score * 100)}%` : "—"}</td></tr>)}</tbody>
            </table>
          ) : null}
          <table className="research-lab-table" aria-label="逐题结果">
            <thead><tr><th>题目</th><th>结果</th><th>耗时</th><th>工具</th></tr></thead>
            <tbody>{run.results.map((item) => (
              <tr key={item.id}><td>{item.id}<small>{EVAL_CATEGORY_LABEL[item.category] || item.category}</small></td>
                <td>{item.skipped ? "跳过" : item.passed ? <span className="research-lab-ok">通过</span> : <span className="research-lab-warn">未通过</span>}{item.error ? <small>{item.error}</small> : null}</td>
                <td>{(item.latencyMs / 1000).toFixed(1)} s</td><td>{item.tools.join("、") || "—"}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel shell
// ---------------------------------------------------------------------------

export default function ResearchLabPanel({ project, isAdmin, settingsGroup }: { project: ResearchProject; isAdmin: boolean; settingsGroup?: string }) {
  const groupTabs: Record<string, LabTab[]> = { lean: ["lean"], compute: ["cas", "databases"], review: ["memory", "review", "eval"], collab: ["collab"], data: ["imports"], usage: ["usage"] };
  const visibleTabs = settingsGroup ? groupTabs[settingsGroup] || ["lean"] : LAB_TABS.map((item) => item.id);
  const [tab, setTab] = useState<LabTab>(visibleTabs[0]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const feedback = useMemo<Feedback>(() => ({
    error: (value) => { setNotice(""); setError(messageOf(value)); },
    notice: (message) => { setError(""); setNotice(message); },
  }), []);
  const props: TabProps = { project, isAdmin, feedback };

  return (
    <section className="research-toolbench research-lab" aria-label="研究实验室">
      <header className="research-toolbench-header">
        <div><span className="research-eyebrow">研究实验室</span><h3>形式化、计算、文献与协作</h3></div>
      </header>
      <nav className="research-tool-tabs" aria-label="研究实验室功能">
        {LAB_TABS.filter((item) => visibleTabs.includes(item.id)).map((item) => <ActionButton key={item.id} type="button" className={tab === item.id ? "is-active" : ""} aria-pressed={tab === item.id} onClick={() => { setTab(item.id); setError(""); setNotice(""); }}>{item.label}</ActionButton>)}
      </nav>
      {error ? <div className="research-error" role="alert"><CircleAlert size={15} /> {error}</div> : null}
      {notice ? <p className="research-tool-notice" role="status">{notice}</p> : null}
      {tab === "lean" ? <LeanTab {...props} /> : null}
      {tab === "cas" ? <CasTab {...props} /> : null}
      {tab === "databases" ? <DatabaseTab {...props} /> : null}
      {tab === "imports" ? <ImportsTab {...props} /> : null}
      {tab === "review" ? <ReviewTab {...props} /> : null}
      {tab === "memory" ? <MemoryTab {...props} /> : null}
      {tab === "collab" ? <CollaborationTab {...props} /> : null}
      {tab === "usage" ? <UsageTab {...props} /> : null}
      {tab === "eval" ? <EvalTab {...props} /> : null}
    </section>
  );
}
