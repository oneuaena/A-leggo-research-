import { evidenceLabels } from "./research-presentation";

export default function EvidenceBadge({ value }: { value?: string }) {
  const evidence = evidenceLabels[(value || "").toUpperCase()] ?? { label: "证据待确认", tone: "conjectural" };
  return <span className="research-evidence-badge" data-evidence-tone={evidence.tone}>{evidence.label}</span>;
}
