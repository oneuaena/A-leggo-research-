"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  CircleAlert,
  FileCheck2,
  History,
  Paperclip,
  LoaderCircle,
  Plus,
  Search,
  X,
} from "@/shared/ui/glyphs";
import ResearchInputPanel from "@/features/research/ResearchInputPanel";
import ResearchTopBar from "@/features/research/ResearchTopBar";
import ResearchToolbench from "@/features/research/ResearchToolbench";
import ResearchLabPanel from "@/features/research/ResearchLabPanel";
import ResearchWorkbenchPanel from "@/features/research/ResearchWorkbenchPanel";
import ResearchThread from "@/features/research/ResearchThread";
import LocalModelConfig from "@/features/research/LocalModelConfig";
import CodexLunaConnectionTest from "@/features/research/CodexLunaConnectionTest";
import { RESEARCH_MODES } from "@/features/research/ResearchModeSelector";
import MathText from "@/features/research/MathText";
import ResearchPlanPanel from "@/features/research/ResearchPlanPanel";
import ResearchPlanEditor from "@/features/research/ResearchPlanEditor";
import { SelfImprovementPanel } from "@/features/research/SelfImprovementPanel";
import { formatProjectTime, projectListStatus, sameSavedPlan, statusText } from "@/features/research/research-presentation";
import {
  approveResearchPlan,
  createResearchProject,
  generateResearchPlan,
  importResearchDocument,
  getResearchModels,
  getResearchSettings,
  getResearchProject,
  listResearchProjects,
  researchAction,
  restoreResearchWorkspace,
  saveResearchPlan,
  saveResearchSettings,
  setResearchModel,
  setResearchSupervision,
  exportReproducibilityPackage,
  type ResearchWorkbenchSnapshot,
  type ResearchModelId,
  type ResearchPlan,
  type ResearchProject,
  type ResearchSettings,
} from "@/lib/research-api";

const MODELS: { id: ResearchModelId; label: string; description: string }[] = [
  { id: "research.lumen", label: "Lumen", description: "快速研究" },
  { id: "research.meridian", label: "Meridian", description: "深度研究 · 默认" },
  { id: "research.axiom", label: "Axiom", description: "多智能体研究" },
  { id: "research.codex_luna", label: "GPT-6 Luna", description: "官方 Codex exec · A-leggo 执行数学工具" },
];

function clonePlan(plan: ResearchPlan | null): ResearchPlan | null {
  return plan ? JSON.parse(JSON.stringify(plan)) as ResearchPlan : null;
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "出错了，请重试。";
}

export default function ResearchWorkspace() {
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [project, setProject] = useState<ResearchProject | null>(null);
  const [draftPlan, setDraftPlan] = useState<ResearchPlan | null>(null);
  const [models, setModels] = useState(MODELS);
  const [credentialConfigured, setCredentialConfigured] = useState(false);
  const [atlasAgentConfigured, setAtlasAgentConfigured] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [atlasAgentId, setAtlasAgentId] = useState("");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [question, setQuestion] = useState("");
  const [newModelId, setNewModelId] = useState<ResearchModelId>("research.codex_luna");
  const [context, setContext] = useState("");
  const [reason, setReason] = useState("研究者修改了计划");
  const [selectedId, setSelectedId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [, setToolbenchOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState("model");
  const [selfImprovementOpen, setSelfImprovementOpen] = useState(false);
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const [projectSearchOpen, setProjectSearchOpen] = useState(false);
  const [projectSearch, setProjectSearch] = useState("");
  const [intakeMode, setIntakeMode] = useState<ResearchWorkbenchSnapshot["state"]["supervisionMode"]>("MILESTONE_REVIEW");
  const [attachments, setAttachments] = useState<File[]>([]);
  const [intakeDraft, setIntakeDraft] = useState<ResearchProject | null>(null);
  const [planSaveBusy, setPlanSaveBusy] = useState(false);
  const [planSaveError, setPlanSaveError] = useState("");
  const [planSaveAmbiguous, setPlanSaveAmbiguous] = useState(false);
  const saveCheckpoint = useRef<{ signature: string; version: number } | null>(null);
  const [reviewFocus, setReviewFocus] = useState<{ stepId: string; sequence: number } | null>(null);
  const scrollContainerRef = useRef<HTMLElement>(null);
  const [showQuestionForm, setShowQuestionForm] = useState(true);
  const [leftRailCollapsed, setLeftRailCollapsed] = useState(false);
  const [mobileDrawer, setMobileDrawer] = useState<"none" | "left" | "right">("none");
  const [themeMode, setThemeMode] = useState<"system" | "light" | "dark">("system");
  const [systemDark, setSystemDark] = useState(false);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("aleggo-research-theme-mode");
      if (saved === "light" || saved === "dark" || saved === "system") {
        setThemeMode(saved);
      }
    } catch {
      // ignore storage errors
    }
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    if (!media) return;
    setSystemDark(Boolean(media.matches));
    const onChange = (event: MediaQueryListEvent) => setSystemDark(Boolean(event.matches));
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    }
    return undefined;
  }, []);

  const resolvedTheme: "light" | "dark" = themeMode === "system" ? (systemDark ? "dark" : "light") : themeMode;

  useEffect(() => {
    if (typeof document === "undefined") return;
    const html = document.documentElement;
    html.classList.toggle("dark", resolvedTheme === "dark");
    html.classList.toggle("theme-snow", resolvedTheme === "light");
    html.dataset.theme = resolvedTheme;
  }, [resolvedTheme]);

  const selectThemeMode = useCallback((mode: "system" | "light" | "dark") => {
    setThemeMode(mode);
    try {
      window.localStorage.setItem("aleggo-research-theme-mode", mode);
    } catch {
      // ignore storage errors
    }
  }, []);

  const activeModel = useMemo(
    () => MODELS.find((item) => item.id === project?.modelId)?.id ?? newModelId,
    [newModelId, project?.modelId],
  );
  const projectIsRunning = project?.status === "RUNNING" || project?.status === "VERIFYING";

  const loadProject = useCallback(async (id: string, preserveDraft = false) => {
    if (!id) return;
    const next = await getResearchProject(id);
    setProject(next);
    if (!preserveDraft) setDraftPlan(clonePlan(next.plan));
    if (!preserveDraft) {
      if (next.plan && ["PLAN_READY", "AWAITING_APPROVAL"].includes(next.status)) setEditing(false);
      else if (["APPROVED", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED", "FAILED"].includes(next.status)) setEditing(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    void Promise.all([listResearchProjects(), getResearchModels(), getResearchSettings()])
      .then(([projectResponse, modelResponse, settingsResponse]) => {
        if (!alive) return;
        setProjects(projectResponse.items);
        const params = new URLSearchParams(window.location.search);
        const handoffQuestion = params.get("question") || "";
        if (handoffQuestion) {
          setQuestion(handoffQuestion);
          setShowQuestionForm(true);
        }
        setModels(modelResponse.items.filter((item) => item.id.startsWith("research.")) as typeof MODELS);
        setCredentialConfigured(modelResponse.credentialConfigured);
        setAtlasAgentConfigured(modelResponse.atlasAgentConfigured);
        setIsAdmin(settingsResponse.isAdmin);
        setAtlasAgentId(settingsResponse.atlasAgentId);
        const requestedProject = params.get("project") || "";
        const requestedExists = projectResponse.items.some((item) => item.id === requestedProject);
        const wanted = requestedExists
          ? requestedProject
          : (!handoffQuestion && projectResponse.items.length > 0 ? projectResponse.items[0].id : "");
        if (wanted) {
          setSelectedId(wanted);
          setShowQuestionForm(false);
          window.history.replaceState(null, "", `/?project=${encodeURIComponent(wanted)}`);
          void getResearchProject(wanted).then((next) => {
            if (!alive) return;
            setProject(next);
            setDraftPlan(clonePlan(next.plan));
            setEditing(false);
          }).catch((loadError) => setError(errorText(loadError)));
        }
      })
      .catch((loadError) => setError(errorText(loadError)));
    return () => { alive = false; };
  }, []);



  useEffect(() => {
    if (!selectedId || !projectIsRunning && !["PLANNING", "REPLANNING"].includes(project?.status || "")) return;
    const timer = window.setInterval(() => {
      void Promise.all([getResearchProject(selectedId), listResearchProjects()])
        .then(([nextProject, nextList]) => {
          setProject(nextProject);
          setProjects(nextList.items);
          if (!editing) setDraftPlan(clonePlan(nextProject.plan));
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [editing, project?.status, projectIsRunning, selectedId]);

  const runAction = useCallback(async (name: string, fn: () => Promise<ResearchProject>) => {
    setBusy(name);
    setError("");
    try {
      const next = await fn();
      setProject(next);
      setDraftPlan(clonePlan(next.plan));
      setProjects((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
      setEditing(false);
      return next;
    } catch (actionError) {
      setError(errorText(actionError));
      if (project) await loadProject(project.id).catch(() => undefined);
      return null;
    } finally {
      setBusy("");
    }
  }, [loadProject, project]);

  const createProject = useCallback(async () => {
    if (!question.trim() || busy) return;
    setBusy("create"); setError("");
    let created: ResearchProject | null = null;
    try {
      created = intakeDraft?.question === question.trim() && intakeDraft.modelId === newModelId
        ? await getResearchProject(intakeDraft.id) : await createResearchProject(question.trim(), newModelId);
      setIntakeDraft(created);
      await setResearchSupervision(created.id, intakeMode);
      // Import real bytes before asking the planner to read them.
      for (const file of attachments) {
        const source = await importResearchDocument(created.id, file);
        if (!source.chunkCount) throw new Error(`${file.name} 未解析出可读内容，请补充可读文档。`);
        setAttachments((items) => items.filter((item) => item !== file));
      }
      const next = await generateResearchPlan(created.id, newModelId, context);
      setProject(next); setSelectedId(next.id); setDraftPlan(clonePlan(next.plan));
      setProjects((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
      setIntakeDraft(null);
      setQuestion(""); setContext(""); setAttachments([]); setEditing(false); setShowQuestionForm(false);
      window.history.replaceState(null, "", `/?project=${encodeURIComponent(next.id)}`);
    } catch (createError) {
      setError(errorText(createError));
      // Keep the question/files for retry; the draft and successfully imported sources are also persisted.
      if (created) {
        const saved = await getResearchProject(created.id).catch(() => created!);
        setProject(saved); setSelectedId(saved.id); setDraftPlan(clonePlan(saved.plan));
        setProjects((previous) => [saved, ...previous.filter((item) => item.id !== saved.id)]);
        setIntakeDraft(saved); setShowQuestionForm(true);
        window.history.replaceState(null, "", `/?project=${encodeURIComponent(saved.id)}`);
      }
    } finally { setBusy(""); }
  }, [attachments, busy, context, intakeDraft, intakeMode, newModelId, question]);

  const generatePlan = useCallback(async () => {
    if (!project || busy) return;
    setBusy("generate");
    setError("");
    try {
      const next = await generateResearchPlan(project.id, activeModel, context);
      setProject(next);
      setDraftPlan(clonePlan(next.plan));
      setProjects((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
      setEditing(false);
      setContext("");
    } catch (generateError) {
      setError(errorText(generateError));
    } finally {
      setBusy("");
    }
  }, [activeModel, busy, context, project]);

  const attachProjectDocuments = useCallback(async (files: File[]) => {
    if (!project || busy || !files.length) return;
    setBusy("upload"); setError("");
    try {
      for (const file of files) {
        const source = await importResearchDocument(project.id, file);
        if (!source.chunkCount) throw new Error(`${file.name} 未解析出可读内容，请补充可读文档。`);
      }
      await loadProject(project.id, true);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }, [busy, loadProject, project]);

  const approveAndStart = useCallback(async () => {
    if (!project || !draftPlan) return;
    setBusy("start");
    setError("");
    try {
      let current = project;
      if (editing) current = await saveResearchPlan(project.id, draftPlan, reason, false);
      current = await approveResearchPlan(project.id);
      setProject(current);
      current = await researchAction(project.id, "start");
      setProject(current);
      setDraftPlan(clonePlan(current.plan));
      setProjects((previous) => [current, ...previous.filter((item) => item.id !== current.id)]);
      setEditing(false);
    } catch (startError) {
      await loadProject(project.id).catch(() => undefined);
      setError(errorText(startError));
    } finally {
      setBusy("");
    }
  }, [draftPlan, editing, loadProject, project, reason]);

  const changeProjectModel = useCallback(async (modelId: ResearchModelId) => {
    if (!project) return;
    setBusy("model");
    setError("");
    try {
      const next = await setResearchModel(project.id, modelId);
      setProject(next);
      setProjects((previous) => previous.map((item) => item.id === next.id ? next : item));
    } catch (modelError) {
      setError(errorText(modelError));
    } finally {
      setBusy("");
    }
  }, [project]);

  const editPlan = useCallback(() => {
    setDraftPlan(clonePlan(project?.plan ?? null));
    setEditing(true);
    setReason("研究者审阅时修改了计划");
    setPlanSaveError("");
    setPlanSaveAmbiguous(false);
    saveCheckpoint.current = null;
  }, [project?.plan]);

  const saveAndContinue = useCallback(async () => {
    if (!project || !draftPlan || planSaveBusy || planSaveAmbiguous) return;
    setPlanSaveBusy(true); setPlanSaveError("");
    const signature = JSON.stringify(draftPlan);
    let phase = "保存";
    try {
      let current = project;
      if (["RUNNING", "VERIFYING"].includes(current.status)) {
        phase = "暂停当前研究";
        try {
          current = await researchAction(current.id, "pause");
        } catch (pauseCause) {
          const latest = await getResearchProject(current.id);
          if (["RUNNING", "VERIFYING"].includes(latest.status)) throw pauseCause;
          current = latest;
        }
        setProject(current);
      }
      if (!saveCheckpoint.current || saveCheckpoint.current.signature !== signature || saveCheckpoint.current.version !== current.currentVersion) {
        phase = "保存";
        current = await saveResearchPlan(current.id, draftPlan, reason, false);
        saveCheckpoint.current = { signature, version: current.currentVersion };
        setProject(current);
      }
      if (current.approvedVersion !== current.currentVersion) {
        phase = "批准";
        current = await approveResearchPlan(current.id);
        setProject(current);
      }
      phase = "启动";
      current = await researchAction(current.id, "start");
      setProject(current);
      if (current.status === "APPROVED") throw new Error("上一轮研究尚未完全停止，可以稍后重试启动。");
      setProjects(previous => [current, ...previous.filter(item => item.id !== current.id)]);
      setDraftPlan(clonePlan(current.plan));
      saveCheckpoint.current = null; setEditing(false);
    } catch (cause) {
      const refreshed = await getResearchProject(project.id).catch(() => null);
      if (refreshed) setProject(refreshed);
      let extra = saveCheckpoint.current ? " 已保存的计划会保留，可重试。" : "";
      if (phase === "保存" && (!refreshed || refreshed.currentVersion > project.currentVersion)) {
        if (refreshed && refreshed.currentVersion > project.currentVersion && sameSavedPlan(draftPlan, refreshed.plan)) {
          saveCheckpoint.current = { signature, version: refreshed.currentVersion };
          extra = " 已确认计划保存成功，可重试完成批准和启动。";
        } else {
          setPlanSaveAmbiguous(true);
          extra = " 保存状态无法确认，请取消并检查当前计划后再操作。";
        }
      }
      setPlanSaveError(`${phase}未完成：${errorText(cause)}${extra}`);
    } finally { setPlanSaveBusy(false); }
  }, [project, draftPlan, planSaveBusy, planSaveAmbiguous, reason]);

  const refreshImportedProjects = useCallback(async () => {
    const next = await listResearchProjects();
    setProjects(next.items);
  }, []);

  const beginNewQuestion = useCallback(() => {
    setProject(null);
    setSelectedId("");
    setDraftPlan(null);
    setShowQuestionForm(true);
    setAttachments([]); setIntakeDraft(null); setQuestion(""); setContext(""); setError("");
    setToolbenchOpen(false);
    setMobileDrawer("none");
    window.history.replaceState(null, "", "/");
  }, []);

  const selectProject = useCallback((item: ResearchProject) => {
    setSelectedId(item.id);
    setProject(item);
    setDraftPlan(clonePlan(item.plan));
    setShowQuestionForm(false); setToolbenchOpen(false);
    setMobileDrawer("none");
    void loadProject(item.id).catch((loadError) => setError(errorText(loadError)));
    window.history.replaceState(null, "", `/?project=${encodeURIComponent(item.id)}`);
  }, [loadProject]);

  const restoreWorkspace = useCallback(async (file: File) => {
    setBusy("restore"); setError("");
    try {
      const restored = await restoreResearchWorkspace(file);
      selectProject(restored);
      setProjects((previous) => [restored, ...previous.filter((item) => item.id !== restored.id)]);
    } catch (restoreError) { setError(errorText(restoreError)); }
    finally { setBusy(""); }
  }, [selectProject]);

  const saveAtlasSettings = useCallback(async () => {
    setSettingsBusy(true);
    setSettingsSaved(false);
    setError("");
    try {
      const saved: ResearchSettings = await saveResearchSettings(atlasAgentId.trim());
      setAtlasAgentId(saved.atlasAgentId);
      setAtlasAgentConfigured(saved.atlasAgentConfigured);
      setSettingsSaved(true);
    } catch (settingsError) {
      setError(errorText(settingsError));
    } finally {
      setSettingsBusy(false);
    }
  }, [atlasAgentId]);

  const doResearchAction = useCallback(async (action: "start") => {
    if (!project) return;
    await runAction(action, () => researchAction(project.id, action));
  }, [project, runAction]);

  useEffect(() => {
    const open = (event: Event) => {
      const tab = (event as CustomEvent<string>).detail;
      setWorkbenchOpen(true); setToolbenchOpen(true);
      window.setTimeout(() => window.dispatchEvent(new CustomEvent("research-toolbench-tab", { detail: tab })), 0);
    };
    window.addEventListener("research-open-materials", open);
    return () => window.removeEventListener("research-open-materials", open);
  }, []);

  const missingInput = Boolean(project?.inputRequests?.some((item) => item.status === "OPEN"));
  const planCanBeGenerated = project && !missingInput && !["RUNNING", "VERIFYING"].includes(project.status);
  const canApprove = project && draftPlan && project.currentVersion > 0 && ["PLAN_READY", "AWAITING_APPROVAL", "APPROVED", "PAUSED"].includes(project.status) && project.approvedVersion !== project.currentVersion;

  return (
    <main className="research-workspace" data-theme={resolvedTheme} ref={scrollContainerRef}>
      {selfImprovementOpen ? (
        <div
          role="dialog"
          aria-label="自我改进闭环面板"
          style={{ position: "fixed", inset: 0, zIndex: 9000, background: "#0b0f17" }}
        >
          <SelfImprovementPanel onClose={() => setSelfImprovementOpen(false)} />
        </div>
      ) : null}
      {settingsOpen ? <div className="reference-settings-page" role="dialog" aria-label="研究设置">
        <nav className="reference-settings-nav"><button type="button" onClick={() => setSettingsOpen(false)}>‹ 返回研究</button><small>常规</small>
          {[["model", "模型与外观"], ["self-improvement", "自我进化闭环"], ["lean", "Lean 形式化"], ["compute", "计算与数据库"], ["review", "记忆、审查与评测"], ["collab", "协作"], ["data", "数据与迁移"], ["usage", "用量与费用"]].map(([id, label]) => <button type="button" key={id} className={settingsTab === id ? "is-active" : ""} onClick={() => setSettingsTab(id)}>{label}</button>)}
        </nav>
        <div className="reference-settings-content">{settingsTab === "model" ? <><h2>模型与外观</h2><LocalModelConfig embedded /><CodexLunaConnectionTest /><div className="research-theme-switcher" role="group" aria-label="外观模式">{(["system", "light", "dark"] as const).map((mode) => <button key={mode} type="button" className={themeMode === mode ? "is-active" : ""} onClick={() => selectThemeMode(mode)}>{mode === "system" ? "跟随系统" : mode === "light" ? "浅色" : "深色"}</button>)}</div></> : settingsTab === "self-improvement" ? <SelfImprovementPanel /> : (project || projects[0]) ? <ResearchLabPanel key={`settings-${(project || projects[0]).id}-${settingsTab}`} project={(project || projects[0])} isAdmin={isAdmin} settingsGroup={settingsTab} /> : <><h2>研究环境</h2><p>打开一个研究项目后可查看相关环境与功能。</p></>}
        {settingsTab === "data" && (project || projects[0]) ? <ResearchToolbench project={(project || projects[0])} isAdmin={isAdmin} onImportFinished={refreshImportedProjects} initialTab="delivery" /> : null}
        {settingsTab === "data" ? <label className="research-restore-control"><span>恢复研究压缩包 (.zip)</span><input type="file" accept=".zip" disabled={busy === "restore"} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void restoreWorkspace(file); }} /></label> : null}</div>
      </div> : null}
      <header className="research-workspace-header">
        <div className="research-header-actions">
          <div className="research-drawer-triggers">
            <button
              type="button"
              className={mobileDrawer === "left" ? "research-drawer-btn is-active" : "research-drawer-btn"}
              aria-expanded={mobileDrawer === "left"}
              onClick={() => setMobileDrawer((cur) => (cur === "left" ? "none" : "left"))}
            >
              ☰ 研究列表
            </button>
            {project && !showQuestionForm ? (
              <button
                type="button"
                className={mobileDrawer === "right" ? "research-drawer-btn is-active" : "research-drawer-btn"}
                aria-expanded={mobileDrawer === "right"}
                onClick={() => setMobileDrawer((cur) => (cur === "right" ? "none" : "right"))}
              >
                计划与证据 ▤
              </button>
            ) : null}
          </div>
          {projectIsRunning ? <span className="research-live-indicator"><span /> 研究在后台继续进行</span> : null}
        </div>
      </header>

      {error ? (
        <div className="research-error" role="alert">
          <CircleAlert size={16} /> <span>{error}</span>
          <ActionButton type="button" onClick={() => setError("")} aria-label="Dismiss error"><X size={15} /></ActionButton>
        </div>
      ) : null}

      {mobileDrawer !== "none" ? (
        <div
          className="research-drawer-backdrop"
          aria-hidden="true"
          onClick={() => setMobileDrawer("none")}
        />
      ) : null}

      <div className={leftRailCollapsed ? "research-layout is-left-collapsed" : "research-layout"}>
        <aside
          className={[
            "research-left-rail",
            leftRailCollapsed ? "is-collapsed" : "",
            mobileDrawer === "left" ? "is-drawer-open" : "",
          ].filter(Boolean).join(" ")}
          aria-label="研究列表"
        >
          {leftRailCollapsed && mobileDrawer !== "left" ? (
            <div className="research-rail-collapsed-bar">
              <button
                type="button"
                className="research-rail-toggle"
                onClick={() => setLeftRailCollapsed(false)}
                aria-label="展开研究列表"
                title="展开研究列表"
              >
                <History size={16} />
              </button>
              <button
                type="button"
                className="research-rail-toggle"
                onClick={beginNewQuestion}
                aria-label="新研究"
                title="新研究"
              >
                <Plus size={16} />
              </button>
            </div>
          ) : (
            <div className="research-left-rail-inner">
              <div className="reference-brand"><span className="reference-brand-mark">A</span><strong>A-leggo Research</strong><button type="button" className="reference-search-toggle" aria-label="搜索研究" onClick={() => setProjectSearchOpen((open) => !open)}><Search size={16} /></button></div>
              {projectSearchOpen ? <input className="reference-project-search" aria-label="搜索研究记录" value={projectSearch} onChange={(event) => setProjectSearch(event.target.value)} placeholder="搜索研究记录" /> : null}
              <div className="research-left-rail-top">
                <ActionButton type="button" data-al-tone="secondary" className="research-new-project" onClick={beginNewQuestion}>
                  <Plus size={16} /> 新研究
                </ActionButton>
                <button
                  type="button"
                  className="research-rail-toggle research-desktop-collapse"
                  onClick={() => setLeftRailCollapsed(true)}
                  aria-label="收起研究列表"
                  title="收起研究列表"
                >
                  ‹
                </button>
                <button
                  type="button"
                  className="research-rail-toggle research-drawer-close"
                  onClick={() => setMobileDrawer("none")}
                  aria-label="关闭研究列表抽屉"
                  title="关闭"
                >
                  ×
                </button>
              </div>
              <div className="research-history-header">
                <span>最近</span>
                <span>{projects.length}</span>
              </div>
              <ul className="research-project-list" aria-label="历史研究列表">
                {projects.filter((item) => `${item.title} ${item.question}`.toLocaleLowerCase().includes(projectSearch.toLocaleLowerCase())).map((item) => {
                  const statusInfo = projectListStatus(item);
                  const titleText = (item.title || item.question || "未命名研究").replace(/^"|"$/g, "");
                  const isSelected = !showQuestionForm && item.id === selectedId;
                  return (
                    <li key={item.id} className="research-project-list-item">
                      <button
                        type="button"
                        className={isSelected ? "research-project-card is-selected" : "research-project-card"}
                        onClick={() => selectProject(item)}
                      >
                        <div className="research-project-card-title">
                          <MathText text={titleText} />
                        </div>
                        <div className="research-project-card-meta">
                          <span className="research-project-status-badge" data-tone={statusInfo.tone}>
                            {statusInfo.label}
                          </span>
                          <time className="research-project-time">
                            {formatProjectTime(item.updatedAt || item.createdAt)}
                          </time>
                        </div>
                      </button>
                    </li>
                  );
                })}
                {!projects.length ? <li className="research-empty-rail">暂无历史研究记录</li> : null}
              </ul>
              <div className="research-left-rail-bottom">
                <button className="reference-settings-link" type="button" onClick={() => setSelfImprovementOpen(true)}>⟳ 自我进化闭环面板</button>
                <button className="reference-settings-link" type="button" onClick={() => { setSettingsTab("model"); setSettingsOpen(true); }}>⚙ 模型连接与设置</button>
                <div className="research-rail-footer">
                  <span className={credentialConfigured ? "research-credential-dot ready" : "research-credential-dot"} />
                  <span>{credentialConfigured ? "模型访问已就绪" : "尚未配置后端模型访问"}</span>
                </div>
              </div>
            </div>
          )}
        </aside>

        <section className="research-main-panel">
          {!project || showQuestionForm ? (
            <div className="research-intake research-conversation-entry">
              <h2>今天研究什么？</h2>
              <p>写下命题或问题。AI 先起草研究计划，你批准后才开始执行。</p>
              <form className="research-composer" onSubmit={(event) => { event.preventDefault(); void createProject(); }}>
                <textarea aria-label="研究问题" value={question} onChange={(event) => setQuestion(event.target.value)}
                  placeholder="例如：证明或否定——对所有正整数 n，n² + n + 41 都是素数" rows={4} maxLength={12000} disabled={Boolean(busy)}
                  onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void createProject(); } }} />
                {attachments.length ? <div className="research-attachment-list">{attachments.map((file, index) =>
                  <span key={`${file.name}-${index}`}><Paperclip size={14} />{file.name}
                    <ActionButton type="button" aria-label={`移除 ${file.name}`} disabled={Boolean(busy)} onClick={() => setAttachments((items) => items.filter((_, i) => i !== index))}><X size={13} /></ActionButton>
                  </span>)}</div> : null}
                <div className="research-composer-footer">
                  <label className="research-attach-control" title="添加研究资料">
                    <Paperclip size={18} /><span>附资料</span>
                    <input aria-label="添加研究资料" type="file" multiple accept=".pdf,.txt,.md,.docx,.html,.htm,.tex,.csv" disabled={Boolean(busy)} onChange={(event) => {
                      const files = Array.from(event.target.files ?? []); event.target.value = "";
                      if (files.some((file) => file.size > 25 * 1024 * 1024)) { setError("单份资料不能超过 25 MiB。"); return; }
                      setAttachments((items) => [...items, ...files]);
                    }} />
                  </label>
                  <div className="reference-mode-segment" role="group" aria-label="研究运行方式">{RESEARCH_MODES.map((mode) => <button type="button" key={mode.id} className={intakeMode === mode.id ? "is-active" : ""} aria-pressed={intakeMode === mode.id} onClick={() => setIntakeMode(mode.id)}>{mode.label}</button>)}</div>
                  <details className="research-composer-options"><summary>背景</summary>
                    <label>补充背景（可选）<textarea rows={2} value={context} onChange={(event) => setContext(event.target.value)} placeholder="已知结果、约束或阅读笔记" /></label>
                  </details>
                  <label className="reference-model-select" title="选择负责本次研究的 A-leggo 模型">
                    <span className="sr-only">选择研究模型</span>
                    <select aria-label="选择研究模型" value={newModelId} disabled={Boolean(busy) || !credentialConfigured}
                      onChange={(event) => setNewModelId(event.target.value as ResearchModelId)}>
                      {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
                    </select>
                  </label>
                  <ActionButton type="submit" data-al-tone="primary" className="reference-plan-button" disabled={Boolean(busy) || !question.trim()} aria-label="起草计划">
                    {busy ? <LoaderCircle size={16} className="research-spin" /> : <>起草计划 <span aria-hidden="true">→</span></>}
                  </ActionButton>
                </div>
              </form>
              <span className="research-entry-hint">{RESEARCH_MODES.find((mode) => mode.id === intakeMode)?.description}</span>
              <div className="reference-prompt-suggestions">
                <button type="button" onClick={() => setQuestion("寻找一个明确反例，并验证其成立条件。 ")}><strong>找反例</strong><span>先做精确计算，检查命题在哪里不成立</span></button>
                <button type="button" onClick={() => setQuestion("形式化这个命题，并检查证明中需要的假设。 ")}><strong>形式化</strong><span>整理命题、假设和可验证的证明步骤</span></button>
                {projects[0] ? <button type="button" onClick={() => selectProject(projects[0])}><strong>继续</strong><span>打开最近的研究记录</span></button> : null}
              </div>
              {!credentialConfigured ? <p className="research-entry-setup">请先在左侧“模型连接”中配置访问，再发送研究问题。</p> : null}
            </div>
          ) : (
            <>
              <div className="research-project-heading">
                <div className="research-project-heading-main">
                  <div role="heading" aria-level={2} className="research-heading-title" title={project.question}><MathText text={project.question.replace(/^"|"$/g, "") || "未命名研究"} /></div>
                  <span className="research-status-pill" data-status={project.status}>{statusText(project.workbench?.state?.statusReason || project.status)}</span>
                  <span className="reference-project-meta">{project.workbench?.state?.currentBranch || "main"} · 计划 v{project.currentVersion || 0}{project.workbench?.state?.stepTotal ? ` · 步骤 ${project.workbench.state.stepIndex || 0}/${project.workbench.state.stepTotal}` : ""}</span>
                </div>
                <div className="reference-heading-actions"><button type="button" onClick={() => { setWorkbenchOpen(true); setToolbenchOpen(true); }}>研究资料</button><button type="button" onClick={() => { setWorkbenchOpen(true); setToolbenchOpen(true); requestAnimationFrame(() => window.dispatchEvent(new CustomEvent("research-toolbench-tab", { detail: "branches" }))); }}>分支</button><button type="button" disabled={busy === "export"} onClick={() => { void (async () => { setBusy("export"); setError(""); try { const blob = await exportReproducibilityPackage(project.id); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "研究包.zip"; document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); } catch (cause) { setError(errorText(cause)); } finally { setBusy(""); } })(); }}>导出研究包</button></div>
              </div>

              {project.status === "COMPLETED" ? (
                <div className="al-event-card" data-tone="success" role="note">
                  <div className="al-event-card-head"><FileCheck2 size={15} /> 研究已完成</div>
                  <div className="al-event-card-body">全部步骤成果、证据与最终报告已保存到本项目，可在下方工作台查看、下载与导出。</div>
                  <div className="al-event-card-actions">
                    <ActionButton type="button" className="al-btn" onClick={() => { setWorkbenchOpen(true); setToolbenchOpen(true); }}>查看成果与报告</ActionButton>
                  </div>
                </div>
              ) : null}

              {project.lastError && !missingInput && project.lastError !== "Research artifacts and evidence snapshot are ready for researcher review." && !["MILESTONE_REVIEW_REQUIRED", "STEP_REVIEW_REQUIRED"].includes(project.workbench?.state?.statusReason || "") ? <div className="research-error research-project-error"><CircleAlert size={16} /> {project.lastError.replace(/\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b/g, statusText)}</div> : null}
              <aside className={mobileDrawer === "right" ? "research-inspector is-drawer-open" : "research-inspector"}>
                <div className="research-inspector-drawer-bar">
                  <strong>研究面板</strong>
                  <button
                    type="button"
                    className="research-rail-toggle research-drawer-close"
                    onClick={() => setMobileDrawer("none")}
                    aria-label="关闭右侧面板抽屉"
                    title="关闭"
                  >
                    ×
                  </button>
                </div>
                <ResearchPlanPanel
                  project={project}
                  onEdit={() => {
                    setMobileDrawer("none");
                    editPlan();
                  }}
                  onOpenStep={(stepId) => {
                    setMobileDrawer("none");
                    setReviewFocus((previous) => ({ stepId, sequence: (previous?.sequence || 0) + 1 }));
                  }}
                />
                {!project.plan && !missingInput ? <div className="research-plan-empty"><p>先生成计划草案，检查假设与步骤。</p><ActionButton type="button" disabled={Boolean(busy) || !planCanBeGenerated} onClick={() => void generatePlan()}>生成研究计划</ActionButton></div> : null}
                {project.workbench?.budget ? <div className="reference-plan-metrics"><span><strong>{project.workbench.budget.llmCalls}</strong>模型调用</span><span><strong>{project.workbench.budget.toolCalls}</strong>工具调用</span><span><strong>{project.workbench.budget.researchElapsedSeconds == null ? "不可用" : `${Math.floor(project.workbench.budget.researchElapsedSeconds / 60)}:${String(project.workbench.budget.researchElapsedSeconds % 60).padStart(2, "0")}`}</strong>用时{project.workbench.budget.researchElapsedKind === "estimated" ? "（估算）" : ""}</span></div> : null}
              </aside>
              <ResearchThread
                key={project.id}
                project={project}
                compactProcess externalScrollRef={scrollContainerRef}
                focusRequest={reviewFocus}
                dock={<ResearchTopBar project={project} refreshKey={`${project.status}:${project.currentVersion ?? 0}:${project.artifacts?.length ?? 0}`} onChanged={() => { void loadProject(project.id, true); }} onEditPlan={editPlan} onOpenReview={(stepId) => setReviewFocus(previous => ({stepId, sequence:(previous?.sequence || 0) + 1}))} />}
                busy={Boolean(busy)}
                onChanged={() => { void loadProject(project.id, true); }}
                onAttach={attachProjectDocuments}
                onProposedPlan={(proposal) => { setDraftPlan(clonePlan(proposal)); setPlanSaveError(""); setPlanSaveAmbiguous(false); saveCheckpoint.current = null; setReason("研究者调整研究方向"); setEditing(true); }}
                footer={
                  <>
                    {project.status === "APPROVED" ? <ActionButton type="button" disabled={Boolean(busy)} onClick={() => void doResearchAction("start")}>开始研究</ActionButton> : null}
                    {project.status === "FAILED" && project.approvedVersion === project.currentVersion ? <ActionButton type="button" disabled={Boolean(busy)} onClick={() => void doResearchAction("start")}>重试已批准的计划</ActionButton> : null}
                    <ResearchInputPanel busy={Boolean(busy)} project={project} onChanged={(next) => { setProject(next); setDraftPlan(clonePlan(next.plan)); setProjects((items) => [next, ...items.filter((item) => item.id !== next.id)]); }} onPlan={() => void generatePlan()} />
                    {project.plan && canApprove && !missingInput && !projectIsRunning && ["PLAN_READY", "AWAITING_APPROVAL"].includes(project.status) ? (
                      <div className="research-thread-approval" role="note">
                        <strong>计划已就绪，等你批准后才会开始研究</strong>
                        <MathText text={project.plan.objective} />
                        <div className="research-thread-approval-actions">
                          <ActionButton type="button" data-al-tone="primary" disabled={Boolean(busy) || !draftPlan} onClick={() => void approveAndStart()}>批准并开始</ActionButton>
                          <ActionButton type="button" onClick={editPlan}>先改计划</ActionButton>
                        </div>
                      </div>
                    ) : null}
                  </>
                }
              />
              {workbenchOpen ? <aside className="reference-materials-panel" aria-label="研究资料">
                <header><strong>研究资料</strong><button type="button" onClick={() => setWorkbenchOpen(false)} aria-label="关闭研究资料">×</button></header>
                <ResearchToolbench key={project.id} project={project} isAdmin={isAdmin} onImportFinished={refreshImportedProjects} />
                <details className="reference-materials-more"><summary>成果、时间线与项目文件</summary>
                  <ResearchWorkbenchPanel showIntervention={false} project={project} onEditPlan={editPlan} onRefresh={() => loadProject(project.id, true)} onProposedPlan={(proposal) => { setDraftPlan(clonePlan(proposal)); setPlanSaveError(""); setPlanSaveAmbiguous(false); saveCheckpoint.current = null; setEditing(true); }} onRestored={(restored) => { selectProject(restored); setProjects(previous => [restored, ...previous.filter(item => item.id !== restored.id)]); }} />
                </details>
              </aside> : null}
              {editing && draftPlan ? <ResearchPlanEditor draft={draftPlan} onChange={setDraftPlan} onCancel={() => { if (!planSaveBusy) { setEditing(false); setPlanSaveError(""); setPlanSaveAmbiguous(false); saveCheckpoint.current = null; } }} onSave={() => void saveAndContinue()} saving={planSaveBusy} saveDisabled={planSaveAmbiguous} error={planSaveError} /> : null}

            </>
          )}
        </section>
      </div>
    </main>
  );
}
