import type { ResearchArtifact, ResearchProject } from "@/lib/research-api";

const nonConclusion = /^No separate conclusion was supplied/i;
function extractText(artifact: ResearchArtifact): string {
  const structured = artifact.metadata?.structuredReport as Record<string, unknown> | undefined;
  const conclusion = typeof structured?.["Current Conclusion"] === "string" ? structured["Current Conclusion"].trim() : "";
  if (conclusion && !nonConclusion.test(conclusion)) return conclusion;
  const output = typeof structured?.Outputs === "string" ? structured.Outputs.trim() : "";
  if (output) {
    // Retain the first actual result and any immediately following displayed formula.
    const paragraphs = output.split(/\n\s*\n/).filter(Boolean);
    const result: string[] = [];
    for (const paragraph of paragraphs) {
      if (result.length && /^\*\*?(?:Lemma|Conjecture|Counterexample|Finding|Route|引理|猜想|反例|发现|路线)/i.test(paragraph)) break;
      result.push(paragraph);
      if (result.length >= 3) break;
    }
    return result.join("\n\n");
  }
  if (artifact.metadata?.artifactKind === "agent-output" && artifact.content.trim()) return artifact.content.split(/\n\s*\n/).slice(0, 3).join("\n\n");
  return "";
}

export function reviewSummary(project: ResearchProject, currentStepId: string) {
  const artifacts = (project.artifacts ?? []).filter(item => item.planVersion === project.currentVersion);
  const milestone = artifacts.find(item => item.metadata?.artifactKind === "milestone-report" && Array.isArray(item.metadata?.stepIds) && item.metadata.stepIds.includes(currentStepId));
  const stepIds = Array.isArray(milestone?.metadata?.stepIds) ? milestone.metadata.stepIds.filter((id): id is string => typeof id === "string") : [currentStepId].filter(Boolean);
  const matched = artifacts.filter(item => stepIds.includes(item.stepId));
  const stepReport = matched.find(item => item.metadata?.artifactKind === "step-report");
  const useful = [...matched.filter(item => item.metadata?.artifactKind === "step-report"), ...matched.filter(item => item.metadata?.artifactKind === "agent-output")];
  const text = useful.map(extractText).find(Boolean) || "";
  const pending = [...(project.plan?.steps || [])].filter(step => step.enabled && step.status === "pending").sort((a,b) => a.order-b.order)[0];
  const description = (pending?.description || pending?.title || "").trim();
  const nextStep = description.match(/^.*?[。！？]|^.*?[.!?](?=\s+[A-Z\u4e00-\u9fff]|$)/s)?.[0]?.trim() || description;
  return { stepId: currentStepId || stepIds[0] || "", text, hasArtifacts: matched.length > 0 || Boolean(milestone),
    nextStep, evidence: typeof stepReport?.metadata?.evidenceClass === "string" ? stepReport.metadata.evidenceClass : undefined };
}
