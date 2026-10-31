"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import { downloadResearchWorkspaceFile, listResearchWorkspaceFiles, openResearchWorkspaceFile, previewResearchWorkspaceFile, type ResearchWorkspaceFile } from "@/lib/research-api";

export default function ResearchFilesPanel({ projectId, workspacePath, revision }: { projectId: string; workspacePath: string; revision: string }) {
  const [opened, setOpened] = useState(false);
  const [files, setFiles] = useState<ResearchWorkspaceFile[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [preview, setPreview] = useState<{ file: ResearchWorkspaceFile; text?: string; url?: string; truncated?: boolean } | null>(null);
  const alive = useRef(true);
  const sequence = useRef(0);
  const loads = useRef(0);

  useEffect(() => { alive.current = true; return () => { alive.current = false; sequence.current += 1; }; }, []);
  useEffect(() => () => { if (preview?.url) URL.revokeObjectURL(preview.url); }, [preview]);
  const load = useCallback(async () => {
    const request = ++loads.current;
    setLoading(true); setError("");
    try { const result = await listResearchWorkspaceFiles(projectId); if (alive.current && request === loads.current) setFiles(result.items); }
    catch (cause) { if (alive.current && request === loads.current) setError(cause instanceof Error ? cause.message : "无法加载研究文件。"); }
    finally { if (alive.current && request === loads.current) setLoading(false); }
  }, [projectId]);
  useEffect(() => { if (opened) void load(); }, [opened, revision, load]);

  const view = async (file: ResearchWorkspaceFile) => {
    const request = ++sequence.current;
    setBusy(file.path); setError("");
    try {
      if (file.previewKind === "text") {
        const result = await previewResearchWorkspaceFile(projectId, file.path);
        if (alive.current && request === sequence.current) setPreview({ file, text: result.text, truncated: result.truncated });
      } else if (file.previewKind === "image" || file.previewKind === "pdf") {
        const data = await downloadResearchWorkspaceFile(projectId, file.path);
        if (alive.current && request === sequence.current) setPreview({ file, url: URL.createObjectURL(data) });
      } else if (alive.current && request === sequence.current) setPreview({ file, text: "请下载文件并使用相应程序打开。" });
    } catch (cause) { if (alive.current && request === sequence.current) setError(cause instanceof Error ? cause.message : "无法预览文件。"); }
    finally { if (alive.current && request === sequence.current) setBusy(""); }
  };
  const download = async (file: ResearchWorkspaceFile) => {
    setBusy(file.path); setError("");
    try {
      const data = await downloadResearchWorkspaceFile(projectId, file.path);
      if (!alive.current) return;
      const url = URL.createObjectURL(data), anchor = document.createElement("a");
      anchor.href = url; anchor.download = file.path.split("/").pop() || "research-file";
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { if (alive.current) setError(cause instanceof Error ? cause.message : "无法下载文件。"); }
    finally { if (alive.current) setBusy(""); }
  };
  const openLocal = async (file: ResearchWorkspaceFile, reveal: boolean) => {
    setBusy(file.path); setError("");
    try { await openResearchWorkspaceFile(projectId, file.path, reveal); }
    catch (cause) { if (alive.current) setError(cause instanceof Error ? cause.message : "无法打开本地文件夹。"); }
    finally { if (alive.current) setBusy(""); }
  };
  const filtered = files.filter(file => file.path.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / 15));
  const currentPage = Math.min(page, pages);

  return <details className="research-stage-artifacts" onToggle={event => setOpened((event.currentTarget as HTMLDetailsElement).open)}>
    <summary><strong>研究工作区文件</strong></summary>
    {opened ? <>
      <p className="research-muted-copy">实验文件、证明源码、文献和报告均保存在这里，下载保留原始文件。</p>
      <div className="research-inline-actions"><input className="research-monospace-input" aria-label="搜索研究文件" value={query} onChange={event => { setQuery(event.target.value); setPage(1); }} /><ActionButton type="button" className="research-text-button" disabled={loading} onClick={() => void load()}>刷新文件</ActionButton></div>
      {error ? <p role="alert" className="research-error">{error}</p> : null}
      {loading ? <p>正在加载研究文件…</p> : null}
      {filtered.slice((currentPage - 1) * 15, currentPage * 15).map(file => <div key={file.path} className="research-workbench-artifact">
        <strong>{file.path}</strong><small>{file.sizeBytes.toLocaleString()} 字节 · {file.mediaType}</small>
        <div className="research-inline-actions">
          <ActionButton type="button" className="research-secondary-button" disabled={busy === file.path} onClick={() => void view(file)}>预览 {file.path.split("/").pop()}</ActionButton>
          <ActionButton type="button" className="research-text-button" disabled={busy === file.path} onClick={() => void download(file)}>下载 {file.path.split("/").pop()}</ActionButton>
          {workspacePath ? <><ActionButton type="button" className="research-text-button" onClick={() => void openLocal(file, true)}>在文件管理器中显示</ActionButton><ActionButton type="button" className="research-text-button" onClick={() => void openLocal(file, false)}>打开文件夹</ActionButton></> : null}
        </div>
      </div>)}
      <div className="research-inline-actions"><ActionButton type="button" className="research-text-button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</ActionButton><small>{filtered.length} 个文件 · 第 {currentPage} / {pages}</small><ActionButton type="button" className="research-text-button" disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)}>下一页</ActionButton></div>
      {preview ? <section aria-label={`File preview ${preview.file.path}`} className="research-blocker-card">
        <strong>{preview.file.path}</strong><ActionButton type="button" className="research-text-button" onClick={() => { sequence.current += 1; setPreview(null); setBusy(""); }}>关闭预览</ActionButton>
        {preview.truncated ? <p>预览仅显示前 256 KiB，请下载完整文件。</p> : null}
        {preview.text != null ? <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 500, overflow: "auto" }}>{preview.text}</pre> : null}
        {preview.url && preview.file.previewKind === "pdf" ? <iframe title={`PDF preview ${preview.file.path}`} src={preview.url} style={{ width: "100%", height: 600, border: 0 }} /> : null}
        {preview.url && preview.file.previewKind === "image" ? <Image unoptimized src={preview.url} alt={preview.file.path} width={800} height={600} style={{ width: "100%", height: "auto", objectFit: "contain" }} /> : null}
      </section> : null}
    </> : null}
  </details>;
}
