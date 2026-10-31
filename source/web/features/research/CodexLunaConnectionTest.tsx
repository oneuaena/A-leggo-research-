"use client";

import { useState } from "react";
import { ActionButton } from "@/shared/ui/ActionButton";
import { CircleAlert, LoaderCircle } from "@/shared/ui/glyphs";
import { testResearchModel } from "@/lib/research-api";

type Result = {
  ok: boolean;
  message: string;
  latency_ms?: number;
  actual_model?: string;
  token_usage_reported?: boolean;
  usage?: { promptTokens?: number; completionTokens?: number; totalTokens?: number; requests?: number; actualModel?: string };
};

export default function CodexLunaConnectionTest() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  const run = async () => {
    setBusy(true);
    setResult(null);
    try {
      setResult(await testResearchModel("research.codex_luna") as Result);
    } catch (error) {
      setResult({ ok: false, message: error instanceof Error ? error.message : "连接测试失败" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="research-admin-settings" aria-label="GPT-6 Luna 连接测试">
      <label>GPT-6 Luna（官方 Codex exec）</label>
      <small>由 A-leggo 发起最小连接请求；不修改 API 凭证或共享模型映射。</small>
      <div className="al-config-actions">
        <ActionButton type="button" className="al-btn" disabled={busy} onClick={() => void run()}>
          {busy ? <LoaderCircle size={13} className="animate-spin" /> : null} 测试 GPT-6 Luna 连接
        </ActionButton>
      </div>
      {result ? (
        <small role="status" className={result.ok ? "al-test-ok" : "al-test-fail"}>
          {result.ok ? "✓" : <CircleAlert size={11} className="inline" />} {result.message}
          {result.ok && result.latency_ms != null ? `（${result.latency_ms} ms · ${result.actual_model || "GPT-6 Luna"} · ${result.usage?.promptTokens ?? "?"}/${result.usage?.completionTokens ?? "?"}/${result.usage?.totalTokens ?? "?"} tokens）` : ""}
        </small>
      ) : null}
    </section>
  );
}
