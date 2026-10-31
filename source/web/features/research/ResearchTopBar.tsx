"use client";

import { useCallback, useEffect, useState } from "react";
import { ActionButton } from "@/shared/ui/ActionButton";
import { getResearchWorkbench, researchAction, resumeResearchAnyway, type ResearchProject, type ResearchWorkbenchSnapshot } from "@/lib/research-api";
import EvidenceBadge from "./EvidenceBadge";
import MathText from "./MathText";
import { statusText } from "./research-presentation";
import { hasSubstantialEnglish, translateMathFields } from "./research-translation";
import { reviewSummary } from "./review-summary";

/** Preserve the existing control API and its safety conditions in the compact review card. */
export default function ResearchTopBar({ project, refreshKey, onChanged, onEditPlan, onOpenReview }: {
  project: ResearchProject; refreshKey: string; onChanged: () => void; onEditPlan?: () => void; onOpenReview?: (stepId: string) => void;
}) {
  const [snapshot, setSnapshot] = useState<ResearchWorkbenchSnapshot | null>(project.workbench ?? null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [translatedReview, setTranslatedReview] = useState<{ text: string; nextStep: string } | null>(null);
  const [showReviewZh, setShowReviewZh] = useState(false);
  const [translatingReview, setTranslatingReview] = useState(false);

  useEffect(() => {
    setTranslatedReview(null);
    setShowReviewZh(false);
    setTranslatingReview(false);
  }, [project.id, refreshKey]);
  const load = useCallback(async () => {
    try { setSnapshot(await getResearchWorkbench(project.id)); } catch { /* keep the last snapshot */ }
  }, [project.id]);
  useEffect(() => { void load(); }, [load, refreshKey]);
  useEffect(() => {
    if (!["RUNNING", "VERIFYING", "REPLANNING"].includes(project.status)) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load, project.status]);
  const run = useCallback(async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setError("");
    try { await fn(); await load(); onChanged(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败，请重试。"); }
    finally { setBusy(""); }
  }, [load, onChanged]);
  const state = snapshot?.state;
  const running = ["RUNNING", "VERIFYING", "REPLANNING"].includes(project.status);
  const paused = project.status === "PAUSED";
  if (!running && !paused) return null;
  const reason = state?.statusReason || "";
  const isReview = paused && ["MILESTONE_REVIEW_REQUIRED", "STEP_REVIEW_REQUIRED"].includes(reason);
  const missing = Boolean(project.inputRequests?.some(item => item.status === "OPEN"));
  const canResume = Boolean(project.plan && project.approvedVersion === project.currentVersion && !missing);
  const stalled = reason === "STALLED" || reason === "BLOCKED";
  const summary = reviewSummary(project, state?.currentStepId || "");
  const reviewHasEnglish = Boolean(
    isReview && (hasSubstantialEnglish(summary.text) || hasSubstantialEnglish(summary.nextStep)),
  );
  const handleTranslateReview = async () => {
    if (translatedReview) {
      setShowReviewZh(prev => !prev);
      return;
    }
    setTranslatingReview(true);
    setError("");
    try {
      const result = await translateMathFields(
        { text: summary.text || "", nextStep: summary.nextStep || "" },
        `review:${project.id}:${summary.stepId || state?.currentStepId || "current"}`,
      );
      setTranslatedReview(result);
      setShowReviewZh(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "翻译失败，请重试。");
    } finally {
      setTranslatingReview(false);
    }
  };
  const displayReviewText = (showReviewZh && translatedReview?.text) || summary.text;
  const displayNextStep = (showReviewZh && translatedReview?.nextStep) || summary.nextStep;
  return <section className="research-review-card" aria-label={isReview ? "阶段审阅" : "研究控制"}>
    <div className="research-review-heading"><strong>{isReview ? statusText(reason) : running ? "研究进行中" : statusText(reason || project.status)}</strong>
      {isReview ? <span className="research-review-evidence">
        {reviewHasEnglish ? (
          <button
            type="button"
            className="research-translate-btn"
            disabled={translatingReview}
            onClick={() => void handleTranslateReview()}
          >
            {translatingReview ? "翻译中…" : translatedReview ? (showReviewZh ? "查看原文" : "查看中文") : "翻译成中文"}
          </button>
        ) : null}
        <EvidenceBadge value={summary.evidence} />
      </span> : null}</div>
    {isReview ? <div className="research-review-content">
      {displayReviewText ? <MathText text={displayReviewText} /> : <p>{summary.hasArtifacts ? "本阶段已有产物，但其中没有可提取的正文；请查看阶段记录。" : "本阶段没有关联产物，因此暂时无法展示成果。"}</p>}
      {displayNextStep ? <div className="research-review-next"><strong>下一步：</strong><MathText text={displayNextStep} /></div> : null}
      {onOpenReview && summary.stepId ? <button type="button" className="research-review-link" onClick={() => onOpenReview(summary.stepId)}>查看本阶段记录 →</button> : null}
    </div> : null}
    <div className="research-review-actions">
      {paused && canResume ? <ActionButton type="button" data-al-tone="primary" disabled={Boolean(busy)} onClick={() => void run("resume", () => stalled ? resumeResearchAnyway(project.id) : researchAction(project.id, "resume"))}>{stalled ? "仍要继续" : "继续"}</ActionButton> : null}
      {onEditPlan ? <ActionButton type="button" disabled={Boolean(busy)} onClick={onEditPlan}>修改计划</ActionButton> : null}
      {running ? <ActionButton type="button" disabled={Boolean(busy)} onClick={() => void run("pause", () => researchAction(project.id, "pause"))}>暂停</ActionButton> : null}
      <ActionButton type="button" data-al-tone="danger" disabled={Boolean(busy)} onClick={() => void run("stop", () => researchAction(project.id, "stop"))}>停止</ActionButton>
    </div>
    {error ? <p className="research-review-error" role="alert">{error}</p> : null}
  </section>;
}
