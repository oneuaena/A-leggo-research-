"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { useCallback, useEffect, useState } from "react";
import { CircleAlert, Key, LoaderCircle } from "@/shared/ui/glyphs";
import {
  getResearchModelAccess, saveResearchModelConfig, testResearchModel,
  type ResearchModelAccess,
} from "@/lib/research-api";

/*
 * Shared model access panel for Math Research (admin).
 *
 * One configuration drives chat AND research: pick which model-catalog
 * profile supplies the credential + base URL, optionally override the
 * actual provider model ID per A-leggo product, and verify with a real
 * minimal provider request. Keys never appear here — only "已保存".
 */

const PRODUCT_LABELS: Record<string, string> = {
  "study.quill": "Quill（快速学习）",
  "study.compass": "Compass（均衡讲解）",
  "research.lumen": "Lumen（快速分析）",
  "research.meridian": "Meridian（进阶研究）",
  "research.axiom": "Axiom（多智能体研究）",
  "research.codex_luna": "GPT-6 Luna（官方 Codex exec）",
};

const PRODUCT_ORDER = ["study.quill", "study.compass", "research.lumen", "research.meridian", "research.axiom", "research.codex_luna"];

export default function ResearchModelConfig() {
  const [access, setAccess] = useState<ResearchModelAccess | null>(null);
  const [profileId, setProfileId] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [testBusy, setTestBusy] = useState("");
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; message: string; latency_ms?: number; actual_model?: string }>>({});

  const load = useCallback(async () => {
    try {
      const next = await getResearchModelAccess();
      setAccess(next);
      setProfileId(next.access?.profile_id_setting || "");
      const products = next.access?.products || {};
      const nextOverrides: Record<string, string> = {};
      for (const [product, info] of Object.entries(products)) {
        if (info.model_source === "override") nextOverrides[product] = info.actual_model;
      }
      setOverrides(nextOverrides);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法读取模型配置。");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(async () => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await saveResearchModelConfig({
        studyProfileId: profileId.trim(),
        studyModelOverrides: Object.fromEntries(
          Object.entries(overrides).filter(([, value]) => value.trim()),
        ),
      });
      setSaved(true);
      await load();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "保存失败。");
    } finally {
      setBusy(false);
    }
  }, [load, overrides, profileId]);

  const runTest = useCallback(async (modelId: string) => {
    setTestBusy(modelId);
    setTestResult((previous) => ({ ...previous, [modelId]: { ok: false, message: "" } }));
    try {
      const result = await testResearchModel(modelId);
      setTestResult((previous) => ({
        ...previous,
        [modelId]: { ok: Boolean(result.ok), message: result.message || (result.ok ? "连接成功" : "测试失败"), latency_ms: result.latency_ms, actual_model: result.actual_model },
      }));
    } catch (testError) {
      setTestResult((previous) => ({
        ...previous,
        [modelId]: { ok: false, message: testError instanceof Error ? testError.message : "测试失败" },
      }));
    } finally {
      setTestBusy("");
    }
  }, []);

  if (!access?.isAdmin) {
    return (
      <div className="research-admin-settings">
        <label>共享模型访问</label>
        <small>{access?.access?.credential?.configured
          ? `已由管理员配置（${access.access.credential.profile_name || access.access.credential.host || "共享配置"}）`
          : "尚未配置。请让管理员在模型设置中添加一个模型连接。"}</small>
      </div>
    );
  }

  const credential = access.access?.credential;
  const profiles = access.profiles ?? [];

  return (
    <div className="research-admin-settings" aria-label="共享模型访问配置">
      <label><Key size={13} className="mr-1 inline" /> 共享模型访问（聊天与研究共用）</label>

      <small data-testid="al-credential-state">
        {credential?.configured
          ? `当前使用：${credential.profile_name || credential.profile_id || "环境变量"}（${credential.host}）· 密钥仅保存在后端`
          : "尚未检测到可用模型连接。请在 模型设置 中先保存一个模型连接（例如阿里云百炼），或设置 ALEGGO_BAILIAN_API_KEY。"}
      </small>

      <label className="sr-only" htmlFor="al-profile-select">凭证来源</label>
      <select
        id="al-profile-select"
        value={profileId}
        onChange={(event) => { setProfileId(event.target.value); setSaved(false); }}
      >
        <option value="">自动选择（优先使用已激活的百炼 / DashScope 连接）</option>
        {profiles.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.name || profile.id} · {profile.service === "llm" ? "对话" : "任务"}{profile.has_key ? "" : " · 无密钥"}{profile.bailian_compatible ? "" : " · 非百炼端点"}
          </option>
        ))}
      </select>

      <details>
        <summary>高级：按产品覆盖实际模型 ID</summary>
        {PRODUCT_ORDER.map((product) => (
          <label key={product} className="al-model-override-row">
            <span>{PRODUCT_LABELS[product]}</span>
            <input
              value={overrides[product] ?? ""}
              placeholder="留空使用默认模型"
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => { setOverrides((previous) => ({ ...previous, [product]: event.target.value })); setSaved(false); }}
            />
          </label>
        ))}
      </details>

      <div className="al-config-actions">
        <ActionButton type="button" className="al-btn al-btn-primary" disabled={busy} onClick={() => void save()}>
          {busy ? <LoaderCircle size={13} className="animate-spin" /> : null} 保存映射
        </ActionButton>
        {saved ? <span className="al-save-note">已保存，后续请求立即生效</span> : null}
      </div>

      <div className="al-config-actions">
        <ActionButton type="button" className="al-btn" disabled={testBusy !== ""} onClick={() => void runTest("research.meridian")}>
          {testBusy === "research.meridian" ? <LoaderCircle size={13} className="animate-spin" /> : null} 测试研究模型连接
        </ActionButton>
        <ActionButton type="button" className="al-btn" disabled={testBusy !== ""} onClick={() => void runTest("research.codex_luna")}>
          {testBusy === "research.codex_luna" ? <LoaderCircle size={13} className="animate-spin" /> : null} 测试 GPT-6 Luna（官方 Codex exec）
        </ActionButton>
        <ActionButton type="button" className="al-btn" disabled={testBusy !== ""} onClick={() => void runTest("study.compass")}>
          {testBusy === "study.compass" ? <LoaderCircle size={13} className="animate-spin" /> : null} 测试学习模型连接
        </ActionButton>
      </div>

      {Object.entries(testResult).map(([product, result]) => result.message ? (
        <small key={product} role="status" className={result.ok ? "al-test-ok" : "al-test-fail"}>
          {result.ok ? "✓" : <CircleAlert size={11} className="inline" />} {PRODUCT_LABELS[product] || product}：{result.message}
          {result.ok && result.latency_ms != null ? `（${result.latency_ms}ms · ${result.actual_model}）` : ""}
        </small>
      ) : null)}

      {error ? <small role="alert" className="al-test-fail">{error}</small> : null}
    </div>
  );
}
