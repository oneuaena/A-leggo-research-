type NativeBridge = {
  dialog?: { open: (options: { directory: boolean; multiple: boolean; title: string }) => Promise<string | string[] | null> };
  core?: { invoke: <T>(command: string, arguments_: Record<string, unknown>) => Promise<T> };
};

declare global { interface Window { __TAURI__?: NativeBridge } }

export function hasResearchFolderPicker(bridge: NativeBridge | undefined = typeof window === "undefined" ? undefined : window.__TAURI__) {
  return Boolean(bridge?.dialog?.open || bridge?.core?.invoke);
}

export async function pickResearchFolder(bridge: NativeBridge | undefined = typeof window === "undefined" ? undefined : window.__TAURI__) {
  const options = { directory: true, multiple: false, title: "Choose this project’s research workspace" };
  if (!bridge) throw new Error("Choose a local folder in the installed desktop app.");
  const selected = bridge.dialog?.open
    ? await bridge.dialog.open(options)
    : await bridge.core?.invoke<string | string[] | null>("plugin:dialog|open", { options });
  if (selected === undefined) throw new Error("The desktop folder picker is unavailable.");
  return Array.isArray(selected) ? selected[0] || null : selected;
}
