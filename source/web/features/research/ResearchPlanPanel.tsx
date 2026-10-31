"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { downloadResearchArtifact, type ResearchProject } from "@/lib/research-api";
import EvidenceBadge from "./EvidenceBadge";
import MathText from "./MathText";
import {
  EVIDENCE_HIERARCHY,
  buildProjectArtifacts,
  buildProjectEvidence,
  statusText,
  type ResearchArtifactItem,
} from "./research-presentation";
import { hasSubstantialEnglish, translateMathFields } from "./research-translation";

const firstSentence = (text: string) => {
  const value = (text || "").trim();
  return value.match(/^.*?[。！？]|^.*?[.!?](?=\s+[A-Z\u4e00-\u9fff]|$)/s)?.[0]?.trim() || value;
};

function saveBlob(blob: Blob, filename: string) {
  const safeName = filename.replace(/[\\/:*?"<>|]+/g, "-").slice(0, 100) || "research-artifact.md";
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = safeName;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ResearchPlanPanel({
  project,
  onEdit,
  onOpenStep,
}: {
  project: ResearchProject;
  onEdit: () => void;
  onOpenStep?: (stepId: string) => void;
}) {
  const [activeTab, setActiveTab] = useState<"plan" | "evidence" | "artifacts">("plan");
  const [seenArtifactCounts, setSeenArtifactCounts] = useState<Record<string, number>>({});
  const [previewArtifact, setPreviewArtifact] = useState<ResearchArtifactItem | null>(null);
  const [downloadingId, setDownloadingId] = useState("");
  const [planTranslated, setPlanTranslated] = useState<Record<string, string> | null>(null);
  const [showPlanZh, setShowPlanZh] = useState(false);
  const [planTranslating, setPlanTranslating] = useState(false);
  const previewDialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    setPlanTranslated(null);
    setShowPlanZh(false);
    setPlanTranslating(false);
  }, [project.id, project.currentVersion]);

  const evidenceItems = useMemo(() => buildProjectEvidence(project), [project]);
  const artifactItems = useMemo(() => buildProjectArtifacts(project), [project]);

  const seenCount = seenArtifactCounts[project.id] ?? 0;
  const hasNewArtifacts = artifactItems.length > seenCount && activeTab !== "artifacts";

  useEffect(() => {
    if (activeTab === "artifacts" && artifactItems.length !== seenCount) {
      setSeenArtifactCounts((prev) => ({ ...prev, [project.id]: artifactItems.length }));
    }
  }, [activeTab, artifactItems.length, project.id, seenCount]);

  useEffect(() => {
    const el = previewDialogRef.current;
    if (!el) return;
    if (previewArtifact && !el.open) {
      el.showModal();
    } else if (!previewArtifact && el.open) {
      el.close();
    }
  }, [previewArtifact]);

  const handleSelectTab = (tab: "plan" | "evidence" | "artifacts") => {
    setActiveTab(tab);
    if (tab === "artifacts") {
      setSeenArtifactCounts((prev) => ({ ...prev, [project.id]: artifactItems.length }));
    }
  };

  const handleDownload = async (item: ResearchArtifactItem) => {
    setDownloadingId(item.id);
    try {
      if (item.downloadArtifactId) {
        const blob = await downloadResearchArtifact(project.id, item.downloadArtifactId, "markdown");
        saveBlob(blob, item.downloadFilename);
      } else {
        saveBlob(new Blob([item.content], { type: "text/markdown;charset=utf-8" }), item.downloadFilename);
      }
    } catch {
      saveBlob(new Blob([item.content], { type: "text/markdown;charset=utf-8" }), item.downloadFilename);
    } finally {
      setDownloadingId("");
    }
  };

  const plan = project.plan;
  const current = project.workbench?.state.currentStepId;
  const steps = plan ? [...plan.steps].filter((step) => step.enabled).sort((a, b) => a.order - b.order) : [];
  const sections: [string, string][] = plan
    ? [
        ["问题形式化", plan.problemFormalization],
        ["假设", plan.assumptions.join("\n\n")],
        ["定义", plan.definitions.join("\n\n")],
        ["关键问题", plan.keyQuestions.join("\n\n")],
        ["证明策略", plan.proofStrategy],
        ["反例策略", plan.counterexampleStrategy],
        ["验证策略", plan.verificationStrategy],
        ["所需工具", plan.toolsRequired.join("、")],
      ]
    : [];

  const planHasEnglish = Boolean(
    plan &&
      (hasSubstantialEnglish(plan.objective) ||
        steps.some((s) => hasSubstantialEnglish(s.title) || hasSubstantialEnglish(s.description))),
  );

  const handleTranslatePlan = async () => {
    if (!plan) return;
    if (planTranslated) {
      setShowPlanZh((prev) => !prev);
      return;
    }
    setPlanTranslating(true);
    try {
      const sourceFields: Record<string, string> = {
        objective: plan.objective || "",
      };
      for (const step of steps) {
        sourceFields[`title_${step.id}`] = step.title || "";
        sourceFields[`desc_${step.id}`] = firstSentence(step.description || "");
      }
      for (const [label, value] of sections) {
        if (value) sourceFields[`sec_${label}`] = value;
      }
      const translated = await translateMathFields(sourceFields, `plan:${project.id}:${project.currentVersion}`);
      setPlanTranslated(translated);
      setShowPlanZh(true);
    } finally {
      setPlanTranslating(false);
    }
  };

  return (
    <aside className="research-plan-panel" aria-label="研究面板">
      <div className="inspector-tabs" role="tablist" aria-label="右侧面板标签">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "plan"}
          className={activeTab === "plan" ? "inspector-tab is-active" : "inspector-tab"}
          onClick={() => handleSelectTab("plan")}
        >
          计划
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "evidence"}
          className={activeTab === "evidence" ? "inspector-tab is-active" : "inspector-tab"}
          onClick={() => handleSelectTab("evidence")}
        >
          证据
          {evidenceItems.length > 0 ? <span className="inspector-tab-count">{evidenceItems.length}</span> : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "artifacts"}
          className={activeTab === "artifacts" ? "inspector-tab is-active" : "inspector-tab"}
          onClick={() => handleSelectTab("artifacts")}
        >
          产物
          {hasNewArtifacts ? <span className="inspector-tab-dot" aria-label="有新产物" title="有新产物" /> : null}
        </button>
      </div>

      {activeTab === "plan" ? (
        !plan ? (
          <div className="plan-panel-empty">
            <h3>研究计划</h3>
            <p>计划生成后会显示在这里。</p>
          </div>
        ) : (
          <div className="inspector-tab-panel" role="tabpanel" aria-label="计划">
            <div className="plan-panel-header">
              <div>
                <h3>研究计划</h3>
                <span>版本 {project.currentVersion}</span>
              </div>
              <div className="plan-panel-header-actions">
                {planHasEnglish ? (
                  <button
                    type="button"
                    className="research-translate-btn"
                    disabled={planTranslating}
                    onClick={() => void handleTranslatePlan()}
                  >
                    {planTranslating ? "翻译中…" : planTranslated ? (showPlanZh ? "查看原文" : "查看中文") : "翻译成中文"}
                  </button>
                ) : null}
                <button type="button" onClick={onEdit}>
                  修改计划
                </button>
              </div>
            </div>
            <div className="reference-plan-progress" aria-label={`已完成 ${steps.filter((step) => step.status === "completed").length} / ${steps.length} 步`}><span style={{ width: `${steps.length ? steps.filter((step) => step.status === "completed").length / steps.length * 100 : 0}%` }} /></div>
            <details className="plan-panel-objective"><summary>研究目标</summary>
              <MathText text={(showPlanZh && planTranslated?.objective) || plan.objective || "暂无研究目标"} />
            </details>
            <section>
              <div className="plan-panel-section-title">
                <h4>研究步骤</h4>
                <span>{steps.length} 步</span>
              </div>
              <ol className="plan-panel-steps">
                {steps.map((step, index) => {
                  const done = step.status === "completed";
                  const active = step.id === current || step.status === "running";
                  const isRunning = ["RUNNING", "VERIFYING"].includes(project.status);
                  const displayStatus = step.status === "running" && !isRunning ? (project.status === "PAUSED" ? "已暂停" : "已停止") : statusText(step.status);
                  const icon = done ? "✓" : step.status === "running" ? "◉" : "○";
                  const stepTitle = (showPlanZh && planTranslated?.[`title_${step.id}`]) || step.title;
                  const stepDesc = (showPlanZh && planTranslated?.[`desc_${step.id}`]) || firstSentence(step.description);
                  return (
                    <li key={step.id} className={active ? "is-current" : ""}>
                      <div className="plan-step-heading">
                        <span className="plan-step-number">{index + 1}</span>
                        <span className="plan-step-icon" aria-label={displayStatus}>
                          {icon}
                        </span>
                        <div>
                          <MathText text={stepTitle} />
                        </div>
                      </div>
                      {step.description ? (
                        <details className="plan-step-description"><summary>查看步骤说明</summary>
                          <MathText text={stepDesc} />
                        </details>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            </section>
            <details className="plan-panel-settings">
              <summary>详细设定</summary>
              {sections.map(([label, value]) => (
                <section key={label}>
                  <h4>{label}</h4>
                  <MathText text={(showPlanZh && planTranslated?.[`sec_${label}`]) || value || "未填写"} />
                </section>
              ))}
            </details>
          </div>
        )
      ) : null}

      {activeTab === "evidence" ? (
        <div className="inspector-tab-panel" role="tabpanel" aria-label="证据">
          <div className="plan-panel-header">
            <div>
              <h3>证据清单</h3>
              <span>按结论列出 · 未经证明记录不标为已证明</span>
            </div>
          </div>
          <div className="evidence-scale" aria-label="证据等级从弱到强">
            {EVIDENCE_HIERARCHY.map((level) => (
              <span key={level.key} className="research-evidence-badge is-mini" data-evidence-tone={level.tone}>
                {level.label}
              </span>
            ))}
          </div>
          {evidenceItems.length === 0 ? (
            <p className="inspector-empty-hint">当前暂无已记录的具体结论证据。</p>
          ) : (
            <ul className="evidence-panel-list">
              {evidenceItems.map((item) => {
                const matchedArtifact = item.artifactId
                  ? artifactItems.find((a) => a.id === item.artifactId)
                  : undefined;
                return (
                  <li key={item.id} className="evidence-panel-item">
                    <div className="evidence-item-top">
                      <EvidenceBadge value={item.evidenceLevel} />
                      <span className="evidence-item-source">
                        {item.stepNumber ? `步骤 ${item.stepNumber} · ` : ""}
                        <MathText text={item.stepTitle} />
                      </span>
                    </div>
                    <div className="evidence-item-conclusion">
                      <MathText text={item.conclusion} />
                    </div>
                    <div className="evidence-item-footer">
                      <button
                        type="button"
                        className="evidence-view-link"
                        onClick={() => {
                          if (item.stepId && onOpenStep) {
                            onOpenStep(item.stepId);
                          } else if (matchedArtifact) {
                            setPreviewArtifact(matchedArtifact);
                          }
                        }}
                      >
                        查看来源记录 →
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}

      {activeTab === "artifacts" ? (
        <div className="inspector-tab-panel" role="tabpanel" aria-label="产物">
          <div className="plan-panel-header">
            <div>
              <h3>研究产物</h3>
              <span>共 {artifactItems.length} 项</span>
            </div>
          </div>
          {artifactItems.length === 0 ? (
            <p className="inspector-empty-hint">步骤执行后生成的阶段报告、表格与代码会显示在这里。</p>
          ) : (
            <ul className="artifact-panel-list">
              {artifactItems.map((item) => (
                <li key={item.id} className="artifact-panel-item">
                  <div className="artifact-item-top">
                    <span className="artifact-kind-tag">{item.kindLabel}</span>
                    <span className="artifact-step-source">
                      <MathText text={item.stepLabel} />
                    </span>
                  </div>
                  <div className="artifact-item-title">
                    <MathText text={item.title} />
                  </div>
                  <div className="artifact-item-actions">
                    <button
                      type="button"
                      className="artifact-action-btn"
                      onClick={() => setPreviewArtifact(item)}
                    >
                      打开
                    </button>
                    <button
                      type="button"
                      className="artifact-action-btn"
                      disabled={downloadingId === item.id}
                      onClick={() => void handleDownload(item)}
                    >
                      {downloadingId === item.id ? "下载中…" : "下载"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {previewArtifact ? (
        <dialog
          ref={previewDialogRef}
          className="plan-editor-dialog artifact-preview-dialog"
          aria-labelledby="artifact-preview-title"
          onCancel={(e) => {
            e.preventDefault();
            setPreviewArtifact(null);
          }}
        >
          <div className="plan-editor-header">
            <div>
              <h2 id="artifact-preview-title">
                <MathText text={previewArtifact.title} />
              </h2>
              <p>{previewArtifact.kindLabel}</p>
            </div>
            <button type="button" onClick={() => setPreviewArtifact(null)} aria-label="关闭预览">
              ×
            </button>
          </div>
          <div className="plan-editor-body artifact-preview-body">
            <MathText text={previewArtifact.content} showRaw />
          </div>
          <div className="plan-editor-footer">
            <button type="button" onClick={() => void handleDownload(previewArtifact)}>
              下载产物
            </button>
            <button type="button" className="plan-editor-save" onClick={() => setPreviewArtifact(null)}>
              关闭
            </button>
          </div>
        </dialog>
      ) : null}
    </aside>
  );
}
