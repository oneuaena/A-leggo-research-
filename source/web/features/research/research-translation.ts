import { completeResearchModel } from "@/lib/research-api";

const STORAGE_KEY = "aleggo-research-translations-v1";
const memoryCache = new Map<string, string>();

function readStorageMap(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as Record<string, string>) : {};
  } catch {
    return {};
  }
}

function writeStorageEntry(key: string, value: string): void {
  memoryCache.set(key, value);
  if (typeof window === "undefined") return;
  try {
    const current = readStorageMap();
    current[key] = value;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(current));
  } catch {
    // ignore localStorage quota/access errors
  }
}

export function hashTranslationKey(prefix: string, content: string): string {
  let h = 2166136261;
  for (let i = 0; i < content.length; i++) {
    h ^= content.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return `${prefix}:${(h >>> 0).toString(16)}`;
}

export function getCachedTranslation(key: string): string | null {
  if (memoryCache.has(key)) return memoryCache.get(key) ?? null;
  const map = readStorageMap();
  if (typeof map[key] === "string" && map[key].trim()) {
    memoryCache.set(key, map[key]);
    return map[key];
  }
  return null;
}

/**
 * Detect whether a text block is predominantly legacy English prose
 * (ignoring LaTeX math, code blocks, URLs, and short symbols).
 */
export function hasSubstantialEnglish(text: string | undefined | null): boolean {
  if (!text) return false;
  const stripped = text
    .replace(/\$\$[\s\S]*?\$\$|\$[^$\n]+\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]/g, " ")
    .replace(/```[\s\S]*?```|`[^`]*`/g, " ")
    .replace(/https?:\/\/\S+/g, " ");
  const cjkCount = (stripped.match(/[\u4e00-\u9fff]/g) ?? []).length;
  const englishWords = stripped.match(/\b[A-Za-z]{3,}\b/g) ?? [];
  if (englishWords.length < 3) return false;
  const asciiLetters = englishWords.join("").length;
  return cjkCount === 0 || asciiLetters > cjkCount * 2.5;
}

const TRANSLATION_SYSTEM_PROMPT =
  "你是一名严谨的数学研究翻译专家。请将用户提供的英文数学研究内容准确翻译为简体中文。\n" +
  "硬性要求：\n" +
  "1. 必须完整保留所有数学公式（行内 $...$ 与独立 $$...$$）、变量名、数值、同余式、等式与不等式；若原文中有未加 $ 的数学式（如 P(n) = n^2 + n + 41、n=40），翻译时统一用 $...$ 包裹。\n" +
  "2. 必须严格保留原命题的量词（如 for all / every / universal -> 对所有/任意，there exists -> 存在）和否定条件（not / negation / counterexample / fail -> 否定/反例/不成立），严禁改变任何数学逻辑关系或夸大证据等级。\n" +
  "3. 保持原有的 Markdown 结构与代码块不变，只输出翻译结果本身，不要添加额外解释。";

export async function translateMathText(text: string, cacheKey?: string): Promise<string> {
  const key = cacheKey || hashTranslationKey("text", text);
  const cached = getCachedTranslation(key);
  if (cached) return cached;

  const response = await completeResearchModel(
    [
      { role: "system", content: TRANSLATION_SYSTEM_PROMPT },
      { role: "user", content: text },
    ],
    "study.compass",
  );
  const translated = (response.text || "").trim() || text;
  writeStorageEntry(key, translated);
  return translated;
}

export async function translateMathFields<T extends Record<string, string>>(
  fields: T,
  cacheKeyPrefix: string,
): Promise<T> {
  const payloadStr = JSON.stringify(fields);
  const key = hashTranslationKey(cacheKeyPrefix, payloadStr);
  const cached = getCachedTranslation(key);
  if (cached) {
    try {
      const parsed = JSON.parse(cached) as T;
      if (parsed && typeof parsed === "object") return parsed;
    } catch {
      // fall through to re-translate
    }
  }

  const response = await completeResearchModel(
    [
      {
        role: "system",
        content:
          TRANSLATION_SYSTEM_PROMPT +
          "\n4. 输入是一个 JSON 对象，请将该 JSON 对象中的每个字符串值翻译为简体中文，保持完全相同的 JSON 键名，只输出合法的 JSON 对象（不要用解释包裹）。",
      },
      { role: "user", content: payloadStr },
    ],
    "study.compass",
  );

  const raw = (response.text || "").trim();
  const cleaned = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
  try {
    const start = cleaned.indexOf("{");
    const end = cleaned.lastIndexOf("}");
    const jsonSlice = start >= 0 && end > start ? cleaned.slice(start, end + 1) : cleaned;
    const parsed = JSON.parse(jsonSlice) as Record<string, unknown>;
    const result = { ...fields };
    for (const k of Object.keys(fields) as (keyof T)[]) {
      if (typeof parsed[k as string] === "string" && (parsed[k as string] as string).trim()) {
        result[k] = parsed[k as string] as T[keyof T];
      }
    }
    writeStorageEntry(key, JSON.stringify(result));
    return result;
  } catch {
    // Fallback: if JSON parsing fails, translate Objective or single block
    return fields;
  }
}
