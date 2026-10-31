"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2, PlugZap, XCircle } from "@/shared/ui/glyphs";

import {
  SettingRow,
  SettingSection,
  SettingsPageHeader,
  inputClass,
  nativeSelectClass,
} from "@/components/settings/shared";
import {
  fetchAgentConnection,
  saveAgentConnection,
  testAgentConnection,
} from "@/features/aleggo/api";
import type { AgentConnectionView, AgentProbeResult } from "@/features/aleggo/types";

const DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com";

function splitPipelineIds(value: string): string[] {
  return value
    .split(/[,;\n\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * Bailian (DashScope) Agent connection — the A-leggo V3 "百炼 Agent 连接"
 * card, rebuilt on the V4 settings document. The API key lives server-side
 * (workspace/aleggo/agent-connection.json); this card only ever sees the
 * masked view and submits dots back when the key should be kept.
 */
export default function AleggoConnectionSettingsPage() {
  // Chinese-first product: the workbench hero and the reference design are
  // Chinese, and the app's i18n defaults to "en" (html lang="en"), so sniffing
  // the language would wrongly flip this card to English. Pin the zh labels.
  const [zh] = useState(true);

  const [view, setView] = useState<AgentConnectionView | null>(null);
  const [appId, setAppId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL);
  const [knowledgeMode, setKnowledgeMode] = useState<"default" | "specified">("default");
  const [pipelineIds, setPipelineIds] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [probe, setProbe] = useState<AgentProbeResult | null>(null);
  const [error, setError] = useState("");

  const applyView = useCallback((next: AgentConnectionView) => {
    setView(next);
    setAppId(next.app_id);
    setApiKey(next.has_key ? next.masked_key : "");
    setBaseUrl(next.base_url || DEFAULT_BASE_URL);
    setKnowledgeMode(next.knowledge_mode === "specified" ? "specified" : "default");
    setPipelineIds(next.pipeline_ids.join(", "));
    setEnabled(next.enabled);
  }, []);

  useEffect(() => {
    let alive = true;
    fetchAgentConnection()
      .then((next) => alive && applyView(next))
      .catch((exc) => alive && setError(String(exc?.message ?? exc)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [applyView]);

  const connected = Boolean(view?.ready && enabled);

  const onSave = useCallback(async () => {
    setSaving(true);
    setError("");
    setProbe(null);
    try {
      const next = await saveAgentConnection({
        app_id: appId.trim(),
        api_key: apiKey,
        base_url: baseUrl.trim() || DEFAULT_BASE_URL,
        knowledge_mode: knowledgeMode,
        pipeline_ids: splitPipelineIds(pipelineIds),
        enabled,
      });
      applyView(next);
      setProbe({
        ok: true,
        message: zh ? "连接设置已保存。" : "Connection settings saved.",
        latency_ms: 0,
      });
    } catch (exc) {
      setError(String((exc as Error)?.message ?? exc));
    } finally {
      setSaving(false);
    }
  }, [apiKey, appId, applyView, baseUrl, enabled, knowledgeMode, pipelineIds, zh]);

  const onTest = useCallback(async () => {
    setTesting(true);
    setError("");
    setProbe(null);
    try {
      setProbe(await testAgentConnection());
    } catch (exc) {
      setProbe({
        ok: false,
        message: String((exc as Error)?.message ?? exc),
        latency_ms: 0,
      });
    } finally {
      setTesting(false);
    }
  }, []);

  return (
    <>
      <SettingsPageHeader
        title={zh ? "a-leggo 连接" : "a-leggo Connection"}
        description={
          zh
            ? "把阿里云百炼 Agent 应用接入 A-leggo：应用 ID 与 API Key 保存在本机服务端，浏览器只看到掩码。"
            : "Connect an Alibaba Cloud Bailian Agent app. Credentials stay on the local server; the browser only sees a masked view."
        }
      />

      <SettingSection
        title={zh ? "百炼 Agent 连接" : "Bailian Agent Connection"}
        description={
          zh
            ? "连接后，讲题与批改能力可把子问题委托给你的百炼 Agent 应用及其知识库。"
            : "Once connected, tutor and diagnosis capabilities can delegate sub-questions to your Bailian Agent app and its knowledge base."
        }
      >
        {loading ? (
          <div className="flex items-center gap-2 py-6 text-[12.5px] text-[var(--muted-foreground)]">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            {zh ? "正在读取连接状态…" : "Loading connection state…"}
          </div>
        ) : (
          <div className="py-4">
            {/* Status strip */}
            <div
              className={`mb-5 flex items-center gap-2.5 rounded-lg border px-3.5 py-2.5 text-[12.5px] ${
                connected
                  ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400"
                  : "border-[var(--border)] bg-[var(--muted)]/40 text-[var(--muted-foreground)]"
              }`}
            >
              {connected ? (
                <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
              ) : (
                <XCircle className="size-4 shrink-0" aria-hidden="true" />
              )}
              <span>
                {connected
                  ? zh
                    ? "已连接。应用 ID 与 API Key 已填写，请求将携带本机学习上下文。"
                    : "Connected. App ID and API Key are set; requests carry local learning context."
                  : zh
                    ? "未连接：请填写应用 ID 与 API Key。"
                    : "Not connected: fill in the App ID and API Key."}
              </span>
            </div>

            <div className="grid gap-4">
              <label className="grid gap-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                  {zh ? "应用 ID（App ID）" : "App ID"}
                </span>
                <input
                  className={inputClass}
                  value={appId}
                  onChange={(event) => setAppId(event.target.value)}
                  placeholder={zh ? "百炼应用 ID，例如 8d4f…c21a" : "Bailian application id"}
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>

              <label className="grid gap-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                  API Key
                </span>
                <input
                  className={inputClass}
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder={zh ? "sk-… （已保存时显示圆点，保持不变即沿用旧 Key）" : "sk-… (dots mean the saved key is kept)"}
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>

              <label className="grid gap-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                  {zh ? "接口地址" : "Endpoint"}
                </span>
                <input
                  className={inputClass}
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder={DEFAULT_BASE_URL}
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>

              <label className="grid gap-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                  {zh ? "知识库模式" : "Knowledge base mode"}
                </span>
                <select
                  className={nativeSelectClass}
                  value={knowledgeMode}
                  onChange={(event) =>
                    setKnowledgeMode(event.target.value === "specified" ? "specified" : "default")
                  }
                >
                  <option value="default">
                    {zh ? "默认（不使用指定知识库）" : "Default (no pinned knowledge base)"}
                  </option>
                  <option value="specified">
                    {zh ? "指定知识库（携带 Pipeline IDs）" : "Specified (send Pipeline IDs)"}
                  </option>
                </select>
              </label>

              <label className="grid gap-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                  {zh ? "知识库 Pipeline IDs" : "Knowledge base Pipeline IDs"}
                </span>
                <input
                  className={inputClass}
                  value={pipelineIds}
                  onChange={(event) => setPipelineIds(event.target.value)}
                  placeholder={zh ? "多个 ID 用逗号分隔" : "Comma-separated pipeline ids"}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={knowledgeMode !== "specified"}
                />
              </label>

              <SettingRow
                title={zh ? "启用 Agent 连接" : "Enable Agent connection"}
                description={
                  zh
                    ? "停用后讲题能力将不再调用百炼 Agent，其余功能不受影响。"
                    : "When disabled, tutoring capabilities skip the Bailian Agent; everything else is unaffected."
                }
                control={
                  <ActionButton
                    type="button"
                    role="switch"
                    aria-checked={enabled}
                    onClick={() => setEnabled((value) => !value)}
                    className={`relative h-5.5 w-10 rounded-full transition-colors ${
                      enabled ? "bg-[var(--primary)]" : "bg-[var(--muted-foreground)]/30"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 size-4.5 rounded-full bg-white shadow transition-all ${
                        enabled ? "left-5" : "left-0.5"
                      }`}
                    />
                  </ActionButton>
                }
              />
            </div>

            {probe && (
              <p
                className={`mt-4 flex items-start gap-2 text-[12.5px] ${
                  probe.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"
                }`}
              >
                {probe.ok ? (
                  <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                ) : (
                  <XCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                )}
                <span>
                  {probe.message}
                  {probe.ok && probe.latency_ms > 0 ? ` (${probe.latency_ms} ms)` : ""}
                </span>
              </p>
            )}
            {error && (
              <p className="mt-4 text-[12.5px] text-red-600 dark:text-red-400">{error}</p>
            )}

            <div className="mt-5 flex items-center gap-2.5">
              <ActionButton
                type="button"
                onClick={onSave}
                disabled={saving}
                className="inline-flex items-center gap-2 rounded-lg bg-[var(--primary)] px-4 py-2 text-[12.5px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {saving && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
                <PlugZap className="size-3.5" aria-hidden="true" />
                {zh ? "保存连接设置" : "Save connection"}
              </ActionButton>
              <ActionButton
                type="button"
                onClick={onTest}
                disabled={testing || !view?.ready}
                className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-4 py-2 text-[12.5px] font-medium text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/50 disabled:opacity-50"
              >
                {testing && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
                {zh ? "测试连接" : "Test connection"}
              </ActionButton>
            </div>
          </div>
        )}
      </SettingSection>
    </>
  );
}
