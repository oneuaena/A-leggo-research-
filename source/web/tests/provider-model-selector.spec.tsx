import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ProviderModelSelector from "@/features/research/ProviderModelSelector";

afterEach(() => vi.unstubAllGlobals());

it("lists the connected provider models and persists only an explicit selection", async () => {
  const provider = { baseUrl: "https://provider.example/v1", configured: true, model: "", effectiveModel: "provider-chat", availableModels: ["provider-chat", "provider-reason"] };
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => ({ ok: true, json: async () => init?.method === "PUT" ? { ...provider, model: "provider-reason", effectiveModel: "provider-reason" } : provider }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ProviderModelSelector />);
  const selector = await screen.findByRole("combobox", { name: "选择 API 模型" });
  await waitFor(() => expect(selector).toHaveValue("provider-chat"));
  expect(screen.getByRole("option", { name: "provider-reason" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /DeepSeek/i })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "PUT")).toHaveLength(0);
  fireEvent.change(selector, { target: { value: "provider-reason" } });
  await waitFor(() => expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "PUT")).toHaveLength(1));
  const write = fetchMock.mock.calls.find((call) => call[1]?.method === "PUT")?.[1];
  expect(JSON.parse(String(write?.body))).toEqual({ baseUrl: provider.baseUrl, apiKey: "", model: "provider-reason", clearKey: false });
});
