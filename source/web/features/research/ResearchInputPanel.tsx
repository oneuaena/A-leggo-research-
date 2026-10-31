"use client";

import { useState } from "react";
import { ActionButton } from "@/shared/ui/ActionButton";
import { Check, FileText, Paperclip, Pause, LoaderCircle } from "@/shared/ui/glyphs";
import { importResearchDocument, respondResearchInput, type ResearchInputRequest, type ResearchProject } from "@/lib/research-api";

function InputReply({ project, request, onChanged }: { project: ResearchProject; request: ResearchInputRequest; onChanged: (project: ResearchProject) => void }) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const readableSources = (project.sources ?? []).filter((source) => source.chunkCount > 0);
  const submit = async () => {
    setBusy(true); setError("");
    const uploadedIds = [...sourceIds];
    try {
      for (const file of files) {
        if (file.size > 25 * 1024 * 1024) throw new Error("单份资料不能超过 25 MiB。");
        const source = await importResearchDocument(project.id, file);
        if (!source.chunkCount) throw new Error(`${file.name} 未解析出可读内容，请上传文字版或可解析的文档。`);
        uploadedIds.push(source.id);
        setSourceIds([...uploadedIds]);
        setFiles((items) => items.filter((item) => item !== file));
      }
      onChanged(await respondResearchInput(project.id, request.id, text, uploadedIds));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "提交失败，请重试。"); }
    finally { setBusy(false); }
  };
  return <form className="research-input-reply" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
    <h4><FileText size={17} />{request.label}</h4>
    <p>{request.reason}</p>
    <textarea aria-label={`补充：${request.label}`} value={text} onChange={(event) => setText(event.target.value)} maxLength={20000}
      placeholder={request.kind === "document" ? "资料说明（可选），请附上可读的原文" : "填写定义、条件或必要的背景…"} rows={3} disabled={busy} />
    {readableSources.length ? <label>使用已上传资料<select aria-label={`已有资料：${request.label}`} value="" disabled={busy} onChange={(event) => { if (event.target.value) setSourceIds((items) => [...new Set([...items, event.target.value])]); }}><option value="">选择文档…</option>{readableSources.map((source) => <option key={source.id} value={source.id}>{source.title}</option>)}</select></label> : null}
    {sourceIds.length ? <p className="research-muted-copy">已选 {sourceIds.length} 份可读资料 <ActionButton type="button" disabled={busy} onClick={() => setSourceIds([])}>清除选择</ActionButton></p> : null}
    {files.length ? <p className="research-muted-copy">待上传：{files.map((file) => file.name).join("、")}</p> : null}
    <div className="research-input-actions">
      <label className="research-attach-control"><Paperclip size={16} />附资料<input aria-label={`上传：${request.label}`} type="file" multiple accept=".pdf,.txt,.md,.docx,.html,.htm,.tex,.csv" disabled={busy} onChange={(event) => { setFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} /></label>
      <ActionButton type="submit" data-al-tone="primary" disabled={busy || (request.kind === "document" ? !files.length && !sourceIds.length : !text.trim() && !files.length && !sourceIds.length)}>{busy ? <LoaderCircle size={16} className="research-spin" /> : <Check size={16} />}提交补充</ActionButton>
    </div>
    {error ? <p role="alert" className="research-error">{error}</p> : null}
  </form>;
}

export default function ResearchInputPanel({ project, onChanged, onPlan, busy = false }: { busy?: boolean; project: ResearchProject; onChanged: (project: ResearchProject) => void; onPlan: () => void }) {
  const pending = (project.inputRequests ?? []).filter((request) => request.status === "OPEN");
  if (!pending.length) {
    if (!project.plan && project.inputRequests?.some((request) => request.status === "RESOLVED")) return <div className="research-input-ready"><Check size={16} />资料已保存，可以继续生成计划。<ActionButton type="button" data-al-tone="primary" disabled={busy} onClick={onPlan}>继续生成计划</ActionButton></div>;
    return null;
  }
  return <section className="research-input-panel" aria-label="等待补充资料">
    <div className="research-input-heading"><Pause size={19} /><div><h3>需要你补充一点资料</h3><p>研究已暂停，当前进度已保存。补齐以下必要信息后，可继续原计划。</p></div></div>
    {pending.map((request) => <InputReply key={request.id} project={project} request={request} onChanged={onChanged} />)}
  </section>;
}
