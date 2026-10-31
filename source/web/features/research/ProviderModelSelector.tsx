"use client";

import { useCallback, useEffect, useState } from "react";

type Provider = { baseUrl: string; configured: boolean; model: string; effectiveModel: string; availableModels: string[] };

/** The provider's actual IDs are the source of truth; no sample models are invented. */
export default function ProviderModelSelector({ disabled = false }: { disabled?: boolean }) {
  const [provider, setProvider] = useState<Provider | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/local-provider");
      if (!response.ok) throw new Error("无法读取模型连接");
      setProvider(await response.json() as Provider);
      setError("");
    } catch { setError("无法读取模型连接"); }
  }, []);
  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener("research-provider-changed", refresh);
    window.addEventListener("focus", refresh);
    return () => { window.removeEventListener("research-provider-changed", refresh); window.removeEventListener("focus", refresh); };
  }, [load]);
  const current = provider?.effectiveModel || provider?.model || "";
  const options = [...new Set([...(current ? [current] : []), ...(provider?.availableModels || [])].filter(Boolean))];
  const select = async (value: string) => {
    if (!provider || value === current || !options.includes(value)) return;
    setSaving(true); setError("");
    try {
      const response = await fetch("/api/local-provider", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ baseUrl: provider.baseUrl, apiKey: "", model: value, clearKey: false }) });
      const next = await response.json();
      if (!response.ok) throw new Error(next.detail || "模型切换失败");
      setProvider(next as Provider);
      window.dispatchEvent(new Event("research-provider-changed"));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "模型切换失败"); }
    finally { setSaving(false); }
  };
  return <div className="reference-model-select" title={disabled ? "研究运行时不可切换模型" : "当前连接提供的模型"}>
    <label><span className="sr-only">选择 API 模型</span><select aria-label="选择 API 模型" value={current} disabled={disabled || saving || !provider?.configured || !options.length} onChange={(event) => void select(event.target.value)}>
      {!current ? <option value="">{provider?.configured ? "正在识别模型" : "未连接模型"}</option> : null}
      {options.map((id) => <option key={id} value={id}>{id}</option>)}
    </select></label>
    {saving ? <small>保存中…</small> : null}{error ? <small role="alert">{error}</small> : null}
  </div>;
}
