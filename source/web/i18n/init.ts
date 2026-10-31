import i18n, { type PostProcessorModule, type Resource } from "i18next";
import { initReactI18next } from "react-i18next";

import enApp from "@/locales/en/app.json";
import { brandText } from "@/lib/product-brand";

export type AppLanguage = "en" | "zh";

export function normalizeLanguage(lang: unknown): AppLanguage {
  if (!lang) return "en";
  const s = String(lang).toLowerCase();
  if (s === "zh" || s === "cn" || s === "chinese") return "zh";
  return "en";
}

let _initialized = false;

/**
 * The product shell has its own public identity even though the runtime keeps
 * upstream-compatible capability names and API contracts.  A single
 * post-processor is intentional here: it also covers less-travelled settings
 * and error states whose copy lives in the upstream locale catalog, so a
 * release cannot accidentally expose an implementation brand in the UI.
 */
const aleggoBrandProcessor: PostProcessorModule = {
  name: "aleggoBrand",
  type: "postProcessor",
  process(value) {
    return brandText(value);
  },
};

export function initI18n(language?: unknown) {
  if (_initialized) return i18n;

  const resources: Resource = {
    en: { app: enApp },
  };

  i18n.use(initReactI18next).use(aleggoBrandProcessor).init({
    resources,
    lng: normalizeLanguage(language),
    fallbackLng: "en",
    // Use a single default namespace to keep lookups simple.
    // We intentionally keep keySeparator disabled so keys like "Generating..." remain valid.
    defaultNS: "app",
    ns: ["app"],
    keySeparator: false,
    interpolation: {
      escapeValue: false,
    },
    returnEmptyString: false,
    returnNull: false,
    postProcess: ["aleggoBrand"],
  });

  _initialized = true;
  return i18n;
}

export async function ensureLanguage(language: AppLanguage) {
  if (i18n.hasResourceBundle(language, "app")) return;
  if (language === "zh") {
    const zhApp = (await import("@/locales/zh/app.json")).default;
    i18n.addResourceBundle("zh", "app", zhApp, true, true);
  }
}
