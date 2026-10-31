"use client";
import { useEffect, useState } from "react";

export default function LocalModelConfig({ embedded = false }: { embedded?: boolean }) {
  const [baseUrl, setBaseUrl] = useState("");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [effectiveModel, setEffectiveModel] = useState("");
  const [detectedModel, setDetectedModel] = useState("");
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [configured, setConfigured] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => fetch("/api/local-provider")
      .then((r) => r.json())
      .then((v) => {
        if (!alive) return;
        setBaseUrl(v.baseUrl || "");
        setConfigured(Boolean(v.configured));
        setModel(v.model || "");
        setEffectiveModel(v.effectiveModel || "");
        setDetectedModel(v.detectedModel || "");
        setAvailableModels(Array.isArray(v.availableModels) ? v.availableModels : []);
      })
      .catch(() => { if (alive) setMessage("无法读取 API 设置"); });
    void load();
    window.addEventListener("research-provider-changed", load);
    return () => { alive = false; window.removeEventListener("research-provider-changed", load); };
  }, []);

  async function save(clearKey = false) {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/local-provider", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseUrl, apiKey: key, model, clearKey }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "保存失败");
      setKey("");
      setConfigured(Boolean(data.configured));
      setModel(data.model || "");
      setEffectiveModel(data.effectiveModel || "");
      setAvailableModels(Array.isArray(data.availableModels) ? data.availableModels : []);
      setMessage("已保存，正在刷新…");
      window.location.reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    setBusy(true);
    setMessage("正在测试连接…");
    try {
      const response = await fetch("/api/local-provider/test", { method: "POST" });
      const data = await response.json();
      if (data.ok) {
        if (data.actual_model) setEffectiveModel(data.actual_model);
        setMessage(
          `✓ 连接成功${data.actual_model ? `（模型：${data.actual_model}）` : ""}${data.latency_ms != null ? ` · ${data.latency_ms}ms` : ""}`,
        );
      } else {
        setMessage(`测试失败：${data.message || "无法连接服务商"}`);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "测试失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="local-config" id="local-model-config" open={embedded || undefined}>
      <summary><span className={configured ? "research-credential-dot ready" : "research-credential-dot"} aria-hidden="true" /> 模型连接{effectiveModel ? ` · ${effectiveModel}` : ""}</summary>
      <div className="local-config-panel">
        <strong>API 连接配置</strong>
        <p>直接接入任意兼容 OpenAI Chat Completions 的 API。密钥由 Windows 加密保存在本机。</p>
        <p>研究讨论限时 30 分钟，最多 6 次模型调用；单次请求最多 15 分钟、输出最多 65,536 tokens（六次合计最多 393,216 输出 tokens）。输入 tokens 另计；服务商可能对已取消或超时的请求收费。</p>
        <label>
          服务地址
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="例如 https://api.deepseek.com 或 https://api.openai.com/v1"
          />
        </label>
        <label>
          API Key
          <input
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={configured ? "已保存；留空保持不变" : "输入 API Key"}
          />
        </label>
        <label>
          模型名称（可选，留空自动识别）
          <input
            list="local-provider-models"
            autoComplete="off"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={effectiveModel ? `自动使用：${effectiveModel}` : "留空自动匹配服务商支持的模型"}
          />
          {availableModels.length > 0 ? (
            <datalist id="local-provider-models">
              {availableModels.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          ) : null}
        </label>
        <p>
          {configured
            ? effectiveModel
              ? `已配置密钥 · 请求模型：${effectiveModel}`
              : "已保存密钥（发送请求时将自动识别模型）"
            : "尚未配置密钥"}
        </p>
        {model && detectedModel && model !== detectedModel ? (
          <p role="status">配置模型为 {model}；探测到的模型为 {detectedModel}。服务商拒绝配置模型时可能自动切换，请使用“测试连接”查看实际响应模型。</p>
        ) : null}
        <button type="button" disabled={busy} onClick={() => void save()}>
          保存连接
        </button>{" "}
        {configured ? (
          <>
            <button type="button" disabled={busy} onClick={() => void testConnection()}>
              测试连接
            </button>{" "}
            <button type="button" disabled={busy} onClick={() => void save(true)}>
              清除密钥
            </button>
          </>
        ) : null}
        {message ? <p role="status">{message}</p> : null}
      </div>
    </details>
  );
}
