import { requestJson } from "@/shared/api/client";
import type {
  AgentCompletionResult,
  AgentConnectionPayload,
  AgentConnectionView,
  AgentProbeResult,
  AleggoOverview,
  IdentityState,
  MethodCard,
  RegisteredSkill,
} from "./types";

async function json<T>(path: string, method: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    scope: "network",
  });
}

export const fetchOverview = () => json<AleggoOverview>("/api/aleggo/overview", "GET");
export const fetchMethodCards = async () =>
  (await json<{ items: MethodCard[] }>("/api/aleggo/method-cards", "GET")).items;
export const createMethodCard = (body: Record<string, unknown>) =>
  json<MethodCard>("/api/aleggo/method-cards", "POST", body);
export const confirmMethodCard = (id: string) =>
  json<MethodCard>(`/api/aleggo/method-cards/${encodeURIComponent(id)}/confirm`, "POST");
export const fetchIdentity = () => json<IdentityState>("/api/aleggo/identity", "GET");
export const createStyle = (body: Record<string, unknown>) => json("/api/aleggo/styles", "POST", body);
export const createCompanion = (body: Record<string, unknown>) =>
  json<{ id: string }>("/api/aleggo/companions", "POST", body);
export const addCompanionMemory = (body: Record<string, unknown>) =>
  json("/api/aleggo/companion-memories", "POST", body);
export const confirmCompanionMemory = (id: string) =>
  json(`/api/aleggo/companion-memories/${encodeURIComponent(id)}/confirm`, "POST");
export const deleteCompanionMemory = (id: string) =>
  json(`/api/aleggo/companion-memories/${encodeURIComponent(id)}`, "DELETE");
export const fetchSkills = async () =>
  (await json<{ items: RegisteredSkill[] }>("/api/aleggo/skills", "GET")).items;
export const registerSkill = (body: Record<string, unknown>) =>
  json<RegisteredSkill>("/api/aleggo/skills", "POST", body);
export const setSkillEnabled = (name: string, enabled: boolean) =>
  json<RegisteredSkill>(`/api/aleggo/skills/${encodeURIComponent(name)}/enabled`, "PUT", { enabled });
export const importLegacy = (workspace: Record<string, unknown>, sourceName: string) =>
  json<{ id: string; backup_path: string; counts: Record<string, number>; warnings: string[] }>(
    "/api/aleggo/migration/import-v3",
    "POST",
    { workspace, source_name: sourceName },
  );
export const fetchBackup = () => json<Record<string, unknown>>("/api/aleggo/backup", "GET");
export const fetchAgentConnection = () =>
  json<AgentConnectionView>("/api/aleggo/agent-connection", "GET");
export const saveAgentConnection = (body: AgentConnectionPayload) =>
  json<AgentConnectionView>("/api/aleggo/agent-connection", "POST", body);
export const testAgentConnection = () =>
  json<AgentProbeResult>("/api/aleggo/agent-connection/test", "POST");
export const agentCompletion = (body: {
  prompt: string;
  session_id?: string;
  images?: string[];
  pipeline_ids?: string[];
}) => json<AgentCompletionResult>("/api/aleggo/agent/completion", "POST", body);
