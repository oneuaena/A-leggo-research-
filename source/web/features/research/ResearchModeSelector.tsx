"use client";
import { useEffect, useState } from "react";
import { getResearchWorkbench, setResearchSupervision, type ResearchWorkbenchSnapshot } from "@/lib/research-api";

type Mode = ResearchWorkbenchSnapshot["state"]["supervisionMode"];
export const RESEARCH_MODES: { id: Mode; label: string; description: string }[] = [
  { id: "AUTONOMOUS", label: "全自动", description: "持续推进，遇到阻塞时暂停。" },
  { id: "MILESTONE_REVIEW", label: "大步骤暂停", description: "完成里程碑后暂停，等待你审阅。" },
  { id: "STEP_BY_STEP", label: "小步骤暂停", description: "每完成一个步骤暂停，等待你审阅。" },
];
export default function ResearchModeSelector({ projectId, disabled }: { projectId: string; disabled: boolean }) {
  const [mode, setMode] = useState<Mode | "">("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { let live = true; setMode(""); getResearchWorkbench(projectId).then((data) => { if (live) setMode(data.state.supervisionMode); }).catch(() => { if (live) setError("无法读取运行方式"); }); return () => { live = false; }; }, [projectId]);
  const change = async (next: Mode) => {
    setSaving(true); setError("");
    try { await setResearchSupervision(projectId, next); setMode(next); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "运行方式保存失败"); }
    finally { setSaving(false); }
  };
  return <span className="reference-mode-compact"><label><span className="sr-only">研究运行方式</span><select aria-label="研究运行方式" value={mode} disabled={disabled || saving || !mode} onChange={(event) => void change(event.target.value as Mode)}>{!mode ? <option value="">读取运行方式…</option> : null}{RESEARCH_MODES.map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}</select></label>{error ? <small role="alert">{error}</small> : null}</span>;
}
