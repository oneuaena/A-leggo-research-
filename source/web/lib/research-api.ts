import { requestBlob, requestJson, requestVoid } from "@/shared/api/client";

export type ResearchModelId = "research.lumen" | "research.meridian" | "research.axiom" | "research.codex_luna";

export type ResearchStep = {
  id: string;
  title: string;
  description: string;
  status: "pending" | "running" | "completed" | "cancelled" | "changed" | "superseded" | "blocked" | "failed";
  order: number;
  priority: number;
  enabled: boolean;
  branch: string;
  dependencies: string[];
  assignedAgent: string;
  requiredTools: string[];
  expectedOutput: string;
  verificationMethod: string;
  milestone: string;
  humanNotes: string;
  agentNotes: string;
};

export type ResearchPlan = {
  inputRequests?: { label: string; reason: string; kind: "text" | "document" }[];
  objective: string;
  problemFormalization: string;
  assumptions: string[];
  definitions: string[];
  keyQuestions: string[];
  proposedLemmas: string[];
  researchBranches: string[];
  suggestedExperiments: string[];
  proofStrategy: string;
  counterexampleStrategy: string;
  toolsRequired: string[];
  verificationStrategy: string;
  expectedOutputs: string[];
  riskUncertainty: string;
  steps: ResearchStep[];
  branches: { id: string; title: string; status: "open" | "closed"; priority: number }[];
  version?: number;
  changedBy?: string;
  changeReason?: string;
  changeSummary?: string;
  approved?: boolean;
  createdAt?: string;
};

export type ResearchArtifact = {
  id: string;
  projectId: string;
  planVersion: number;
  stepId: string;
  agentRole: string;
  title: string;
  content: string;
  status: string;
  createdAt: string;
  branch?: string;
  metadata?: Record<string, unknown>;
  filePath?: string;
};

export type ResearchBranch = {
  id: string;
  name: string;
  parentId: string | null;
  parentName?: string | null;
  status: string;
  priority?: number;
  basePlanVersion: number;
  limits: { evaluations: number; proofAttempts: number; toolSeconds: number };
  used: { evaluations: number; proofAttempts: number; toolSeconds: number };
};

export type ResearchGraphNode = {
  id: string;
  type: string;
  title: string;
  content: string;
  status: string;
  branch: string;
  artifactId?: string | null;
  metadata?: Record<string, unknown>;
  createdAt?: string;
};

export type ResearchFormalBinding = {
  id: string;
  projectId: string;
  claimNodeId: string;
  proofNodeId: string | null;
  originalStatement: string;
  formalIr: string;
  leanStatement: string;
  originalHash: string;
  formalIrHash: string;
  leanStatementHash: string;
  bindingHash: string;
  mappingAuthority: "USER_CONFIRMED" | "AI_PROPOSED" | "UNCONFIRMED";
  status: string;
  proofSourceHash: string;
  leanVersion: string;
  checkerOutput: string;
  createdAt: string;
  updatedAt: string;
};

export type ResearchSource = {
  id: string;
  type: string;
  title: string;
  url: string;
  authors: string;
  year: number | null;
  abstract: string;
  contentHash: string;
  chunkCount: number;
  doi?: string;
  provider?: string;
  classification?: string;
  relevance?: number;
  readingStatus?: "unread" | "important" | "read" | "irrelevant";
  important?: boolean;
  notes?: string;
  firstSeenAt?: string;
  lastSeenAt?: string;
  hasOriginalFile?: boolean;
  metadata?: Record<string, unknown>;
};

export type ResearchInputRequest = {
  id: string; label: string; reason: string; kind: "text" | "document";
  status: "OPEN" | "RESOLVED"; stepId: string; planVersion: number;
  response: string; sourceIds: string[];
};

export type ResearchRecommendation = {
  id: string;
  stepId: string;
  status: string;
  createdAt: string;
  content: {
    summary?: string;
    primary: { path: string; reason: string }[];
    secondary: { path: string; reason: string }[];
    deprioritize: { path: string; reason: string }[];
    uncertainty?: string;
    source?: string;
  };
};

export type ResearchWorkbenchSnapshot = {
  state: {
    supervisionMode: "AUTONOMOUS" | "MILESTONE_REVIEW" | "STEP_BY_STEP";
    currentStepId: string;
    currentAgent: string;
    currentAction: string;
    currentBranch: string;
    currentMilestone: string;
    runStartedAt: string;
    statusReason: string;
    blockerType: string;
    blockerDetail: string;
    stalledAttempts: number;
    llmCalls: number;
    toolCalls: number;
    failures: number;
    lastProgressAt: string;
    workspaceRoot: string;
    workspaceStatus: string;
    currentTool?: string;
    activeAgents?: string[];
    stepIndex?: number;
    stepTotal?: number;
    milestoneIndex?: number;
    milestoneTotal?: number;
    phase?: string;
  };
  project?: ResearchProject;
  summary?: Record<string, unknown>;
  queuedInterventions?: { id: string; kind: string; status: string; details: Record<string, unknown> }[];
  currentStep: ResearchStep | null;
  timeline: { id: string; eventType: string; actor: string; stepId: string; branch: string; summary: string; details: Record<string, unknown>; createdAt: string }[];
  decisions: { id: string; decision: string; actor: string; rationale: string; source: string; branch: string; createdAt: string }[];
  recommendations: ResearchRecommendation[];
  counts: { artifacts: number; literature: number; evidence: number; counterexamples: number; failedPaths: number };
  workspacePath: string;
  budget: { llmCalls: number; toolCalls: number; failures: number; elapsedSeconds: number; researchElapsedSeconds?: number | null; researchElapsedKind?: "measured" | "estimated" | "unavailable"; informationGain: string };
};

export type ResearchMemory = { id: string; projectId: string | null; content: string; confirmed: boolean };

export type ResearchProject = {
  inputRequests?: ResearchInputRequest[];
  id: string;
  title: string;
  question: string;
  status: string;
  modelId: ResearchModelId;
  currentVersion: number;
  approvedVersion: number | null;
  lastError: string;
  createdAt: string;
  updatedAt: string;
  plan: ResearchPlan | null;
  revisions: {
    version: number;
    changedBy: string;
    changeReason: string;
    changeSummary: string;
    approved: boolean;
    createdAt: string;
  }[];
  artifacts?: ResearchArtifact[];
  branches?: ResearchBranch[];
  graph?: { nodes: ResearchGraphNode[]; edges: { id: string; source: string; target: string; type: string; evidence: string }[] };
  formalBindings?: ResearchFormalBinding[];
  sources?: ResearchSource[];
  memories?: ResearchMemory[];
  activity?: { id: string; kind: string; actor: string; details: Record<string, unknown>; createdAt: string }[];
  workbench?: ResearchWorkbenchSnapshot;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>(`/api/research${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

export const listResearchProjects = () => request<{ items: ResearchProject[] }>("/projects");

export const getResearchModels = () =>
  request<{
    items: { id: string; label: string; description: string }[];
    credentialConfigured: boolean;
    atlasAgentConfigured: boolean;
  }>("/models");

export type ResearchSettings = {
  isAdmin: boolean;
  atlasAgentId: string;
  atlasAgentConfigured: boolean;
  credentialConfigured: boolean;
};

export const getResearchSettings = () => request<ResearchSettings>("/settings");

export const saveResearchSettings = (atlasAgentId: string) =>
  request<ResearchSettings>("/settings", {
    method: "PUT",
    body: JSON.stringify({ atlasAgentId }),
  });

export type LeanResearchSettings = { confirmBeforeCheck: boolean; autoCallEnabled: boolean };
export const getLeanResearchSettings = (id: string) =>
  request<LeanResearchSettings>(`/projects/${encodeURIComponent(id)}/settings/lean`);
export const saveLeanResearchSettings = (id: string, settings: LeanResearchSettings) =>
  request<LeanResearchSettings>(`/projects/${encodeURIComponent(id)}/settings/lean`, {
    method: "PUT",
    body: JSON.stringify(settings),
  });

export type ResearchModelAccess = {
  isAdmin: boolean;
  access: {
    credential: {
      configured: boolean;
      source: string;
      host: string;
      profile_id: string;
      profile_name: string;
      service: string;
      active: boolean;
    };
    products: Record<string, { actual_model: string; model_source: string }>;
    profile_id_setting: string;
  };
  profiles?: {
    id: string;
    name: string;
    binding: string;
    service: string;
    host: string;
    base_url: string;
    has_key: boolean;
    active: boolean;
    bailian_compatible: boolean;
    models: { id: string; model: string; name: string }[];
  }[];
  settings?: {
    studyProfileId: string;
    studyModelOverrides: Record<string, { actual_model: string; model_source: string }>;
  };
};

export type ResearchModelTestResult = {
  ok: boolean;
  message: string;
  code?: string | null;
  status_code?: number | null;
  provider_request_id?: string | null;
  actual_model?: string;
  host?: string;
  latency_ms?: number;
};

export const getResearchModelAccess = () => request<ResearchModelAccess>("/model-access");

export const saveResearchModelConfig = (payload: {
  studyProfileId: string;
  studyModelOverrides: Record<string, string>;
}) =>
  request<{ saved: boolean }>("/model-config", {
    method: "PUT",
    body: JSON.stringify({
      studyProfileId: payload.studyProfileId,
      studyModelOverrides: payload.studyModelOverrides,
    }),
  });

export const testResearchModel = (modelId: string) =>
  request<ResearchModelTestResult>("/model-test", {
    method: "POST",
    body: JSON.stringify({ modelId }),
  });

export const completeResearchModel = (
  messages: { role: "system" | "user" | "assistant"; content: string }[],
  modelId = "study.compass",
) =>
  request<{ text: string; session_id?: string | null }>("/model-completion", {
    method: "POST",
    body: JSON.stringify({ modelId, messages }),
  });

export const createResearchProject = (question: string, modelId: ResearchModelId) =>
  request<ResearchProject>("/projects", { method: "POST", body: JSON.stringify({ question, modelId }) });

export const getResearchProject = (id: string) =>
  request<ResearchProject>(`/projects/${encodeURIComponent(id)}`);

export const setResearchModel = (id: string, modelId: ResearchModelId) =>
  request<ResearchProject>(`/projects/${encodeURIComponent(id)}/model`, {
    method: "PUT",
    body: JSON.stringify({ modelId }),
  });

export const generateResearchPlan = (id: string, modelId: ResearchModelId, context: string) =>
  request<ResearchProject>(`/projects/${encodeURIComponent(id)}/plan/generate`, {
    method: "POST",
    body: JSON.stringify({ modelId, context }),
  });

const VALID_STEP_STATUSES = new Set([
  "pending",
  "running",
  "completed",
  "cancelled",
  "changed",
  "superseded",
  "blocked",
]);

function sanitizePlanPayload(plan: ResearchPlan) {
  return {
    inputRequests: (plan.inputRequests || []).map((item) => ({
      label: item.label,
      reason: item.reason,
      kind: item.kind,
    })),
    objective: plan.objective || "",
    problemFormalization: plan.problemFormalization || "",
    assumptions: plan.assumptions || [],
    definitions: plan.definitions || [],
    keyQuestions: plan.keyQuestions || [],
    proposedLemmas: plan.proposedLemmas || [],
    researchBranches: plan.researchBranches || [],
    suggestedExperiments: plan.suggestedExperiments || [],
    proofStrategy: plan.proofStrategy || "",
    counterexampleStrategy: plan.counterexampleStrategy || "",
    toolsRequired: plan.toolsRequired || [],
    verificationStrategy: plan.verificationStrategy || "",
    expectedOutputs: plan.expectedOutputs || [],
    riskUncertainty: plan.riskUncertainty || "",
    branches: plan.branches || [],
    steps: (plan.steps || []).map((step, index) => {
      const extra = step as ResearchStep & {
        researchBudget?: Record<string, number>;
        formalBindingId?: string;
        proofSource?: string;
      };
      return {
        id: step.id,
        title: step.title,
        description: step.description || "",
        status: VALID_STEP_STATUSES.has(step.status) ? step.status : "pending",
        order: typeof step.order === "number" ? step.order : index,
        priority: typeof step.priority === "number" ? step.priority : 3,
        enabled: step.enabled ?? true,
        branch: step.branch || "main",
        dependencies: step.dependencies || [],
        assignedAgent: step.assignedAgent || "explorer",
        requiredTools: step.requiredTools || [],
        expectedOutput: step.expectedOutput || "",
        verificationMethod: step.verificationMethod || "",
        milestone: step.milestone || "",
        humanNotes: step.humanNotes || "",
        agentNotes: step.agentNotes || "",
        ...(extra.researchBudget ? { researchBudget: extra.researchBudget } : {}),
        ...(extra.formalBindingId ? { formalBindingId: extra.formalBindingId } : {}),
        ...(extra.proofSource ? { proofSource: extra.proofSource } : {}),
      };
    }),
  };
}

export const saveResearchPlan = (id: string, plan: ResearchPlan, reason: string, saveDraft = false) =>
  request<ResearchProject>(`/projects/${encodeURIComponent(id)}/plan`, {
    method: "PUT",
    body: JSON.stringify({ plan: sanitizePlanPayload(plan), reason, saveDraft }),
  });

export const approveResearchPlan = (id: string) =>
  request<ResearchProject>(`/projects/${encodeURIComponent(id)}/approve`, { method: "POST" });

export const researchAction = (id: string, action: "start" | "pause" | "resume" | "stop") =>
  request<ResearchProject>(`/projects/${encodeURIComponent(id)}/${action}`, { method: "POST" });

export const addResearchIntervention = (id: string, kind: string, details: Record<string, unknown>) =>
  request<{ recorded: string; projectId: string; status?: string; queueId?: string; proposedPlan?: ResearchPlan }>(`/projects/${encodeURIComponent(id)}/interventions`, {
    method: "POST",
    body: JSON.stringify({ kind, details }),
  });

export const cancelResearchDiscussion = (id: string, queueId: string) =>
  request<{ cancelled: boolean; queueId: string }>(`/projects/${encodeURIComponent(id)}/discussions/${encodeURIComponent(queueId)}/cancel`, { method: "POST" });

export const resumeResearchAnyway = (id: string) => request<ResearchProject>(`/projects/${encodeURIComponent(id)}/resume-anyway`, { method: "POST" });

export const getResearchWorkbench = (id: string) =>
  request<ResearchWorkbenchSnapshot>(`/projects/${encodeURIComponent(id)}/workbench`);

export const setResearchSupervision = (id: string, mode: ResearchWorkbenchSnapshot["state"]["supervisionMode"]) =>
  request<ResearchWorkbenchSnapshot["state"]>(`/projects/${encodeURIComponent(id)}/supervision`, { method: "PUT", body: JSON.stringify({ mode }) });

export const setResearchWorkspace = (id: string, path: string) =>
  request<ResearchWorkbenchSnapshot["state"]>(`/projects/${encodeURIComponent(id)}/workspace`, { method: "POST", body: JSON.stringify({ path }) });

export const syncResearchWorkspace = (id: string) =>
  request<{ status: string; filesWritten: number; path: string }>(`/projects/${encodeURIComponent(id)}/workspace/sync`, { method: "POST" });

export const openResearchWorkspace = (id: string) =>
  request<{ path: string; opened: boolean }>(`/projects/${encodeURIComponent(id)}/workspace/open`, { method: "POST" });

export type ResearchWorkspaceFile = { path: string; sizeBytes: number; mediaType: string; previewKind: "text" | "pdf" | "image" | "download" };
export const listResearchWorkspaceFiles = (id: string) =>
  request<{ items: ResearchWorkspaceFile[] }>(`/projects/${encodeURIComponent(id)}/workspace/files`);
export const previewResearchWorkspaceFile = (id: string, path: string) =>
  request<ResearchWorkspaceFile & { text: string; truncated: boolean; sha256: string }>(`/projects/${encodeURIComponent(id)}/workspace/file/preview?path=${encodeURIComponent(path)}`);
export const downloadResearchWorkspaceFile = (id: string, path: string) =>
  requestBlob(`/api/research/projects/${encodeURIComponent(id)}/workspace/file?path=${encodeURIComponent(path)}`);
export const openResearchWorkspaceFile = (id: string, path: string, reveal: boolean) =>
  request<{ path: string; opened: boolean }>(`/projects/${encodeURIComponent(id)}/workspace/file/open`, { method: "POST", body: JSON.stringify({ path, reveal }) });

export const exportReproducibilityPackage = (id: string) =>
  requestBlob(`/api/research/projects/${encodeURIComponent(id)}/package`);

export type ResearchArtifactFormat = "markdown" | "json" | "text";
export const downloadResearchArtifact = (projectId: string, artifactId: string, format: ResearchArtifactFormat = "markdown") =>
  requestBlob(`/api/research/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/download?format=${format}`);

export const openResearchArtifact = (projectId: string, artifactId: string, reveal = true) =>
  request<{ path: string; opened: boolean }>(`/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/open`, { method: "POST", body: JSON.stringify({ reveal }) });

export const restoreResearchWorkspace = (file: File) => {
  const body = new FormData();
  body.append("file", file);
  return requestJson<ResearchProject>("/api/research/workspace/restore", { method: "POST", body });
};

export const decideResearchRecommendation = (projectId: string, recommendationId: string, action: "accept" | "modify" | "reject" | "alternative" | "add_human_idea", rationale: string, modified?: Record<string, unknown>) =>
  request<{ id: string; status: string; planChanged: boolean; proposedPlan?: ResearchPlan; project?: ResearchProject }>(`/projects/${encodeURIComponent(projectId)}/recommendations/${encodeURIComponent(recommendationId)}`, {
    method: "PUT", body: JSON.stringify({ action, rationale, modified }),
  });

export const listResearchBranches = (id: string) => request<{ items: ResearchBranch[] }>(`/projects/${encodeURIComponent(id)}/branches`);

export const createResearchBranch = (id: string, name: string, parentName = "main", limits?: ResearchBranch["limits"]) =>
  request<ResearchBranch>(`/projects/${encodeURIComponent(id)}/branches`, {
    method: "POST", body: JSON.stringify({ name, parentName, limits }),
  });

export const compareResearchBranches = (id: string, left: string, right: string) =>
  request<Record<string, unknown>>(`/projects/${encodeURIComponent(id)}/branches/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);

export const setResearchBranchState = (id: string, name: string, status: string, reason: string, priority?: number) =>
  request<ResearchBranch>(`/projects/${encodeURIComponent(id)}/branches/${encodeURIComponent(name)}/state`, { method: "POST", body: JSON.stringify({ status, reason, priority }) });

export type LiteratureResearchNote = { sourceId?: string; content?: Record<string, string>; fields?: Record<string, string>; status?: string; updatedAt?: string; [key: string]: unknown };
export const getLiteratureState = (id: string) => request<Record<string, unknown>>(`/projects/${encodeURIComponent(id)}/literature/state`);
export const getLiteratureGraph = (id: string) => request<{ nodes: Record<string, unknown>[]; edges: Record<string, unknown>[] }>(`/projects/${encodeURIComponent(id)}/literature/graph`);
export const analyzeResearchLiterature = (id: string) => request<Record<string, unknown>>(`/projects/${encodeURIComponent(id)}/literature/analyze`, { method: "POST" });
export const getLiteratureNote = (id: string, sourceId: string) => request<LiteratureResearchNote>(`/projects/${encodeURIComponent(id)}/sources/${encodeURIComponent(sourceId)}/note`);
export const generateLiteratureNote = (id: string, sourceId: string) => request<LiteratureResearchNote>(`/projects/${encodeURIComponent(id)}/sources/${encodeURIComponent(sourceId)}/note`, { method: "POST" });
export const saveLiteratureNote = (id: string, sourceId: string, content: Record<string, string>) => request<LiteratureResearchNote>(`/projects/${encodeURIComponent(id)}/sources/${encodeURIComponent(sourceId)}/note`, { method: "PUT", body: JSON.stringify({ content }) });
export const importLiteratureDoi = (id: string, doi: string) => request<ResearchSource>(`/projects/${encodeURIComponent(id)}/literature/doi`, { method: "POST", body: JSON.stringify({ doi }) });
export const importLiteratureLibrary = (id: string, file: File) => requestJson<{ items: ResearchSource[] }>(`/api/research/projects/${encodeURIComponent(id)}/literature/import?filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });

export const createResearchGraphNode = (id: string, input: { type: string; title: string; content: string; branch: string }) =>
  request<ResearchGraphNode>(`/projects/${encodeURIComponent(id)}/graph/nodes`, { method: "POST", body: JSON.stringify(input) });

export const createResearchGraphEdge = (id: string, input: { sourceId: string; targetId: string; type: string; evidence: string }) =>
  request(`/projects/${encodeURIComponent(id)}/graph/edges`, { method: "POST", body: JSON.stringify(input) });

export const createFormalBinding = (id: string, input: {
  claimNodeId: string;
  originalStatement: string;
  formalIr: string;
  leanStatement: string;
  confirmMapping: boolean;
}) => request<ResearchFormalBinding>(`/projects/${encodeURIComponent(id)}/formal-bindings`, {
  method: "POST", body: JSON.stringify(input),
});

export const proposeFormalBinding = (id: string, claimNodeId: string) =>
  request<ResearchFormalBinding>(`/projects/${encodeURIComponent(id)}/formal-bindings/propose`, {
    method: "POST", body: JSON.stringify({ claimNodeId }),
  });

export const confirmFormalBinding = (id: string, bindingId: string, input: {
  originalStatement: string; formalIr: string; leanStatement: string;
}) => request<ResearchFormalBinding>(
  `/projects/${encodeURIComponent(id)}/formal-bindings/${encodeURIComponent(bindingId)}/confirm`,
  { method: "PUT", body: JSON.stringify(input) },
);

export const checkFormalBinding = (id: string, bindingId: string, proofSource: string) =>
  request<{ binding: ResearchFormalBinding; check: { status: string; passed: boolean; exitCode: number | null; elapsedMs: number; leanVersion: string; output: string } }>(
    `/projects/${encodeURIComponent(id)}/formal-bindings/${encodeURIComponent(bindingId)}/check`,
    { method: "POST", body: JSON.stringify({ proofSource }) },
  );

export const promoteFormalBinding = (id: string, bindingId: string) =>
  request<ResearchFormalBinding>(`/projects/${encodeURIComponent(id)}/formal-bindings/${encodeURIComponent(bindingId)}/promote`, { method: "POST" });

export type FiniteDiscoveryResult = {
  status: string;
  seed: number;
  evaluationBudget: number;
  evaluations: number;
  searchSpaceSize: string;
  exhaustive: boolean;
  bestCandidates: { candidate: number[]; violations: number; coverage: number; coverageTotal: number; spread: number }[];
  scope: string;
  artifactId: string;
  graphNodeId: string;
  branch: string;
};

export const runFiniteDiscoverySearch = (id: string, input: {
  problem: { universeSize: number; candidateSize: number; incompatibilities: number[][]; coverageGroups: number[][] };
  branch: string; seed: number; evaluationBudget: number;
}) => request<FiniteDiscoveryResult>(`/projects/${encodeURIComponent(id)}/discovery-search`, {
  method: "POST", body: JSON.stringify(input),
});

export type IntegerCounterexampleResult = {
  status: string;
  variable: string;
  value?: number;
  leftValue?: string;
  rightValue?: string;
  casesChecked: number;
  searchRange: [number, number];
  verification: string;
  scope: string;
  artifactId: string;
  graphNodeId: string;
  branch: string;
  requestedRange: [number, number];
};

export const runIntegerCounterexampleSearch = (id: string, input: {
  leftExpression: string; rightExpression: string; variable: string; start: number; end: number; branch: string;
}) => request<IntegerCounterexampleResult>(`/projects/${encodeURIComponent(id)}/counterexample-search`, {
  method: "POST", body: JSON.stringify(input),
});

export const searchFormalProof = (id: string, bindingId: string) =>
  request<{ binding: ResearchFormalBinding; check: { status: string; passed: boolean; elapsedMs: number; leanVersion: string; output: string }; searchPortfolio: number }>(
    `/projects/${encodeURIComponent(id)}/formal-bindings/${encodeURIComponent(bindingId)}/search`, { method: "POST" },
  );

export const searchResearchLiterature = (id: string, query: string) =>
  request<{ items: ResearchSource[]; providers: string[]; providerStatuses: Record<string, string>; availabilityStatus: string; warnings: Record<string, string>; emptyHint: string }>(`/projects/${encodeURIComponent(id)}/literature/search`, {
    method: "POST", body: JSON.stringify({ query, limit: 8 }),
  });

export const importResearchDocument = (id: string, file: File) =>
  request<ResearchSource>(`/projects/${encodeURIComponent(id)}/documents?filename=${encodeURIComponent(file.name)}`, {
    method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
  });

export const respondResearchInput = (id: string, requestId: string, text: string, sourceIds: string[]) =>
  request<ResearchProject>(`/projects/${encodeURIComponent(id)}/inputs/${encodeURIComponent(requestId)}/respond`, {
    method: "POST", body: JSON.stringify({ text, sourceIds }),
  });

export const listResearchSources = (id: string) => request<{ items: ResearchSource[] }>(`/projects/${encodeURIComponent(id)}/sources`);

export const curateResearchSource = (projectId: string, sourceId: string, input: { reading_status?: ResearchSource["readingStatus"]; classification?: string; notes?: string; important?: boolean }) =>
  request<ResearchSource>(`/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}`, { method: "PUT", body: JSON.stringify(input) });

export const linkResearchLiterature = (projectId: string, input: { sourceId: string; entityType: "artifact" | "graph_node" | "branch" | "lemma" | "conjecture" | "experiment"; entityId: string; relationship: "SUPPORTS" | "CONTRADICTS" | "USES" | "RELATED_TO" | "CITES"; notes?: string }) =>
  request<Record<string, unknown>>(`/projects/${encodeURIComponent(projectId)}/literature/links`, { method: "POST", body: JSON.stringify(input) });

export const searchResearchSources = (id: string, query: string) =>
  request<{ items: { id: string; source_id: string; title: string; url: string; page_number: number | null; section: string; content: string }[] }>(
    `/projects/${encodeURIComponent(id)}/sources/search?q=${encodeURIComponent(query)}`,
  );

export const listResearchMemories = (id: string) => request<{ items: ResearchMemory[] }>(`/projects/${encodeURIComponent(id)}/memories`);

export const addResearchMemory = (id: string, content: string, scope: "project" | "workspace", confirmed: boolean) =>
  request<ResearchMemory>(`/projects/${encodeURIComponent(id)}/memories`, {
    method: "POST", body: JSON.stringify({ content, scope, confirmed }),
  });

export const confirmResearchMemory = (projectId: string, memoryId: string, confirmed: boolean) =>
  request(`/projects/${encodeURIComponent(projectId)}/memories/${encodeURIComponent(memoryId)}`, {
    method: "PUT", body: JSON.stringify({ confirmed }),
  });

export const listMraImports = () => request<{ items: Record<string, unknown>[] }>("/imports");

export const importExistingMraWorkspace = () =>
  request<Record<string, unknown>>("/imports/mra-existing", { method: "POST" });

export const importMraDatabaseFile = (file: File) =>
  request<Record<string, unknown>>(`/imports/mra-database?filename=${encodeURIComponent(file.name)}`, {
    method: "POST", headers: { "Content-Type": "application/vnd.sqlite3" }, body: file,
  });

export const exportResearchReport = (id: string, format: "markdown" | "pdf" | "latex") =>
  requestBlob(`/api/research/projects/${encodeURIComponent(id)}/export?format=${format}`);

export const giveArtifactFeedback = (projectId: string, artifactId: string, feedback: "useful" | "invalid") =>
  request(`/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/feedback`, {
    method: "POST", body: JSON.stringify({ feedback }),
  });

export const listImportedResearchRecords = (projectId: string, collection?: string) =>
  request<{ items: { sourceTable: string; id: string; collection: string; payload: unknown; createdAt: string }[] }>(
    `/projects/${encodeURIComponent(projectId)}/legacy-records?limit=100${collection ? `&collection=${encodeURIComponent(collection)}` : ""}`,
  );

export type ResearchThreadItem =
  | { id: string; cursor: string; createdAt: string; kind: "user"; intervention: string; text: string; truncated: boolean; branch: string; queueId: string }
  | { id: string; cursor: string; createdAt: string; kind: "tool"; tool: string; status: string; ok: boolean; input: string; inputTruncated: boolean; output: string; outputTruncated: boolean; elapsedMs: number; stepId?: string; branch?: string; artifactId?: string }
  | { id: string; cursor: string; createdAt: string; kind: "answer"; title: string; role: string; status: string; text: string; truncated: boolean; artifactId: string; stepId?: string; branch?: string }
  | { id: string; cursor: string; createdAt: string; kind: "event"; eventType: string; tone: "info" | "success" | "warning" | "danger"; actor: string; summary: string; stepId?: string; branch?: string; artifactId?: string };

export type ResearchThreadLive = { action: string; tool: string; agent: string; phase: string; step: string; stepIndex: number; stepTotal: number };

export type ResearchThreadPage = {
  /** Always newest-first; the client reverses for display. */
  items: ResearchThreadItem[];
  hasMore: boolean;
  nextCursor: string;
  newestCursor: string;
  queue: Record<string, string>;
  live: ResearchThreadLive | null;
};

/** `before` pages back through history on demand; `after` returns only rows newer than a cursor. */
export const getResearchThread = (id: string, opts: { limit?: number; before?: string; after?: string } = {}) => {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.after) params.set("after", opts.after);
  else if (opts.before) params.set("before", opts.before);
  const query = params.toString();
  return request<ResearchThreadPage>(`/projects/${encodeURIComponent(id)}/thread${query ? `?${query}` : ""}`);
};

// ---------------------------------------------------------------------------
// Research lab: Lean onboarding, CAS bridges, math databases, imports, proof
// review, cross-project memory, collaboration, usage and evaluation.
// ---------------------------------------------------------------------------

const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`;

export type LeanErrorHelp = {
  code: string;
  title: string;
  reason: string;
  fix: string;
};

export type LeanInstallState = {
  status: "idle" | "running" | "paused" | "succeeded" | "failed" | "cancelled";
  mode: "install" | "upgrade";
  phase: string;
  phaseLabel: string;
  message: string;
  error: string;
  errorCode: string;
  errorHelp: LeanErrorHelp | null;
  downloadedBytes: number;
  totalBytes: number;
  speedBps: number;
  startedAt: string | null;
  finishedAt: string | null;
  progress: number;
  phases: string[];
  log: string[];
};

export type LeanStatus = {
  ready: boolean;
  reason: string;
  activeWorkspace: string;
  pinnedLean: string;
  pinnedMathlib: string;
  toolsHome: string;
  lake: string;
  elan: string;
  gitAvailable: boolean;
  freeDiskBytes: number | null;
  requiredDiskBytes: number;
  platform: string;
  bundleVersion: string;
  autoStart: { enabled: boolean; declined: boolean };
  checks: { id: string; label: string; ok: boolean; detail: string }[];
  install: LeanInstallState;
};

export type LeanUpdates = {
  installedVersion: string;
  latestVersion: string;
  canUpgrade: boolean;
  platform: string;
};

export const getLeanStatus = () => request<LeanStatus>("/lean/status");
export const startLeanInstall = () => request<LeanInstallState>("/lean/install", { method: "POST" });
export const cancelLeanInstall = () => request<LeanInstallState>("/lean/install/cancel", { method: "POST" });
export const pauseLeanInstall = () => request<LeanInstallState>("/lean/install/pause", { method: "POST" });
export const checkLeanUpdates = () => request<LeanUpdates>("/lean/updates");
export const startLeanUpgrade = () => request<LeanInstallState>("/lean/upgrade", { method: "POST" });

export type AutoProveAttempt = { attempt: number; proof: string; explanation: string; status: string; feedback: string; elapsedMs: number };
export type AutoProveJob = {
  id: string;
  bindingId: string;
  state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED" | "NONE";
  attempts: AutoProveAttempt[];
  passed: boolean;
  proof: string;
  error: string;
  startedAt: string | null;
  finishedAt: string | null;
  maxAttempts: number;
  artifactId?: string | null;
};

export const startAutoProve = (id: string, bindingId: string, options: { maxAttempts?: number; hint?: string } = {}) =>
  request<AutoProveJob>(`${projectPath(id)}/formal-bindings/${encodeURIComponent(bindingId)}/auto-prove`, { method: "POST", body: JSON.stringify(options) });
export const getAutoProve = (id: string, bindingId: string) =>
  request<AutoProveJob>(`${projectPath(id)}/formal-bindings/${encodeURIComponent(bindingId)}/auto-prove`);

export type CasEngine = {
  key: string;
  tool: string;
  label: string;
  available: boolean;
  path: string;
  version: string;
  isolation: string;
  homepage: string;
  installHint: string;
  envVar: string;
  example: string;
  language: string;
};

export const getCasStatus = () => request<{ engines: CasEngine[]; isolation: string; config: Record<string, string> }>("/cas/status");
export const saveCasConfig = (paths: Record<string, string>) =>
  request<{ engines: CasEngine[]; isolation: string; config: Record<string, string> }>("/cas/config", { method: "PUT", body: JSON.stringify({ paths }) });
export const getVisionConfig = () => request<{ model: string; source: string; reason?: string }>("/vision/config");
export const saveVisionConfig = (model: string) => request<{ model: string; source: string; reason?: string }>("/vision/config", { method: "PUT", body: JSON.stringify({ model }) });

export type MathDatabaseTool = "oeis_search" | "zbmath_search" | "arxiv_search" | "lmfdb_search";
export type MathDatabaseResult = {
  ok: boolean;
  tool: string;
  data: { provider?: string; query?: string; url?: string; count?: number; items?: Record<string, unknown>[]; emptyHint?: string } | null;
  error: string;
  errorType: string;
  savedSourceIds: string[];
};

export const queryMathDatabase = (id: string, tool: MathDatabaseTool, input: Record<string, unknown>, saveSources = false) =>
  request<MathDatabaseResult>(`${projectPath(id)}/database-query`, { method: "POST", body: JSON.stringify({ tool, input, saveSources }) });

export type LatexImportStatement = { index: number; environment: string; kind: string; label: string; note: string; statement: string; hasProof: boolean };
export type LatexImportReference = { key: string; type: string; title: string; authors: string; year: string; doi: string; arxivId: string; url: string; venue: string };
export type LatexImportResult = {
  source: ResearchSource;
  mainFile: string;
  files: string[];
  statements: LatexImportStatement[];
  references: LatexImportReference[];
};

const rawUpload = (file: File): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });

export const importLatexProject = (id: string, file: File) =>
  request<LatexImportResult>(`${projectPath(id)}/imports/latex?filename=${encodeURIComponent(file.name)}`, rawUpload(file));
export const promoteLatexImport = (id: string, sourceId: string, selection: { statements: number[]; references: number[]; branch?: string }) =>
  request<{ nodes: ResearchGraphNode[]; sources: ResearchSource[] }>(`${projectPath(id)}/imports/${encodeURIComponent(sourceId)}/promote`, { method: "POST", body: JSON.stringify(selection) });
export type PdfImportResult = {
  source: ResearchSource;
  pages: number;
  transcribedPages: number[];
  pagesNeedingVision: number[];
  pagesNeedingVisionInitially?: number[];
  warnings: string[];
  visionModel: string;
  visionPageCap?: number;
  moreVisionPagesRemaining?: number;
  nextPageOffset?: number | null;
  progress?: { page: number; status: string; error?: string; chars?: number }[];
  continued?: boolean;
  suggestions?: ImportSuggestions;
};

export type ImportSuggestions = {
  sourceId: string;
  title: string;
  question: string;
  openClaims: { index: number | null; kind: string; label: string; statement: string; hasProof: boolean }[];
  alignedClaims: { index: number | null; kind: string; label: string; statement: string; hasProof: boolean; overlap: number }[];
  computableExamples: string[];
  relatedLiterature: { title: string; doi?: string; arxivId?: string; authors?: string; year?: string }[];
  notice: string;
};

export const importFormulaPdf = (
  id: string,
  file: File,
  mode: "auto" | "text" | "vision" = "auto",
  opts: { maxVisionPages?: number; pageOffset?: number; continueSourceId?: string } = {},
) => {
  const params = new URLSearchParams({ filename: file.name, mode });
  if (opts.maxVisionPages != null) params.set("maxVisionPages", String(opts.maxVisionPages));
  if (opts.pageOffset) params.set("pageOffset", String(opts.pageOffset));
  if (opts.continueSourceId) params.set("continueSourceId", opts.continueSourceId);
  return request<PdfImportResult>(`${projectPath(id)}/imports/pdf?${params}`, rawUpload(file));
};

export const suggestImportInsights = (id: string, sourceId: string, question = "") =>
  request<ImportSuggestions>(`${projectPath(id)}/imports/${encodeURIComponent(sourceId)}/suggest?question=${encodeURIComponent(question)}`);

export const importChatAttachment = (id: string, filename: string, base64Data: string, question = "") =>
  request<{ kind: string; import: { source: ResearchSource }; suggestions: ImportSuggestions }>(
    `${projectPath(id)}/imports/chat-attach`,
    { method: "POST", body: JSON.stringify({ filename, data: base64Data, question }) },
  );

export const importEmailAttachment = (id: string, payload: {
  filename: string; data: string; messageId?: string; subject?: string; from?: string;
  question?: string; mode?: "auto" | "text" | "vision"; maxVisionPages?: number;
}) => request<Record<string, unknown>>(`${projectPath(id)}/imports/email-attachment`, { method: "POST", body: JSON.stringify(payload) });

export type GmailFetcherStatus = {
  injectedFetcher: boolean;
  bridgeUrlConfigured: boolean;
  available: boolean;
  stubMessage: string;
};

export const getGmailImportStatus = () => request<GmailFetcherStatus>("/imports/gmail/status");

export const importGmailAttachment = (id: string, payload: {
  messageId: string; attachmentId: string; filename?: string; data?: string;
  subject?: string; from?: string; question?: string; mode?: "auto" | "text" | "vision"; maxVisionPages?: number;
}) => request<Record<string, unknown>>(`${projectPath(id)}/imports/gmail`, { method: "POST", body: JSON.stringify(payload) });

export type PdfContinueItem = {
  sourceId: string;
  filename: string;
  remaining?: number;
  nextPageOffset?: number | null;
  maxVisionPages?: number;
  mode?: "auto" | "text" | "vision";
  summary: string;
  updatedAt: string;
  hasOriginalFile?: boolean;
};

export const listPdfContinues = (id: string) =>
  request<{ items: PdfContinueItem[] }>(`${projectPath(id)}/imports/pdf-continues`);

export const continuePdfFromStored = (
  id: string,
  sourceId: string,
  opts: { mode?: "auto" | "text" | "vision"; maxVisionPages?: number; pageOffset?: number } = {},
) => request<Record<string, unknown>>(`${projectPath(id)}/imports/pdf/${encodeURIComponent(sourceId)}/continue`, {
  method: "POST",
  body: JSON.stringify({
    mode: opts.mode || "auto",
    maxVisionPages: opts.maxVisionPages,
    pageOffset: opts.pageOffset,
  }),
});

export type HandwritingResult = {
  pages: { index: number; name: string; text: string; error?: string }[];
  transcription: string;
  model: string;
  notice: string;
  imageCap?: number;
  style?: string;
  failedCount?: number;
  reviewChecklist?: string[];
};

export const transcribeHandwriting = (id: string, files: File[], hint = "", style: "handwriting" | "whiteboard" | "board" = "handwriting") => {
  const body = new FormData();
  files.slice(0, 24).forEach((file) => body.append("files", file, file.name));
  body.append("hint", hint);
  body.append("style", style);
  return requestJson<HandwritingResult>(`/api/research${projectPath(id)}/imports/handwriting`, { method: "POST", body });
};
export const saveHandwritingTranscription = (id: string, payload: { title: string; content: string; model: string; originals: string[]; reviewed: boolean }) =>
  request<ResearchSource>(`${projectPath(id)}/imports/handwriting/save`, { method: "POST", body: JSON.stringify(payload) });

export type ProofVerdict = "valid" | "gap" | "error" | "unclear";
export type ProofReviewStep = {
  id: string;
  text: string;
  verdict: ProofVerdict;
  dependsOn: string[];
  issue: string;
  suggestion: string;
  check?: { tool?: string; ok?: boolean; summary?: string } | null;
  researcherVerdict: "" | ProofVerdict;
  researcherNote: string;
};
export type ProofTreeNode = { id: string; verdict: string; repeated?: boolean; children: ProofTreeNode[] };
export type ProofReview = {
  id: string;
  title: string;
  state: "RUNNING" | "COMPLETED" | "FAILED" | "INTERRUPTED";
  statement: string;
  steps: ProofReviewStep[];
  /** `affectedByIssues` maps a step to the gap/error steps it transitively relies on. */
  tree: { edges: { source: string; target: string }[]; roots: string[]; conclusion: string; tree: ProofTreeNode | null; affectedByIssues: Record<string, string[]> } | null;
  overall: "no-issues-found" | "gaps-found" | "errors-found" | "needs-clarification" | "";
  summary: string;
  missingHypotheses: string[];
  error: string;
  createdAt?: string;
};
export type ProofReviewSummary = { id: string; title: string; createdAt: string; state: ProofReview["state"]; overall: ProofReview["overall"]; stepCount: number };

export const startProofReview = (id: string, payload: { proof: string; statement?: string; title?: string; nodeId?: string; branch?: string }) =>
  request<ProofReview>(`${projectPath(id)}/proof-reviews`, { method: "POST", body: JSON.stringify(payload) });
export const listProofReviews = (id: string) => request<{ items: ProofReviewSummary[] }>(`${projectPath(id)}/proof-reviews`);
export const getProofReview = (id: string, reviewId: string) => request<ProofReview>(`${projectPath(id)}/proof-reviews/${encodeURIComponent(reviewId)}`);
export const annotateProofStep = (id: string, reviewId: string, stepId: string, verdict: "" | ProofVerdict, note: string) =>
  request<ProofReview>(`${projectPath(id)}/proof-reviews/${encodeURIComponent(reviewId)}/steps/${encodeURIComponent(stepId)}`, { method: "PUT", body: JSON.stringify({ verdict, note }) });

export type LongTermMemoryKind = "note" | "technique" | "result" | "failed-route" | "reference" | "preference" | "definition";
export type LongTermMemory = {
  id: string;
  content: string;
  confirmed: boolean;
  kind: LongTermMemoryKind;
  tags: string[];
  originProject: string | null;
  useCount: number;
  lastUsedAt: string | null;
  suggested?: boolean;
};

export const listLongTermMemory = (query = "", kind = "") => {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  if (kind) params.set("kind", kind);
  const suffix = params.toString();
  return request<{ items: LongTermMemory[] }>(`/memory${suffix ? `?${suffix}` : ""}`);
};
export const createLongTermMemory = (payload: { content: string; kind: LongTermMemoryKind; tags: string[]; confirmed: boolean }) =>
  request<LongTermMemory>("/memory", { method: "POST", body: JSON.stringify(payload) });
export const updateLongTermMemory = (memoryId: string, payload: Partial<Pick<LongTermMemory, "content" | "kind" | "tags" | "confirmed">>) =>
  request<LongTermMemory>(`/memory/${encodeURIComponent(memoryId)}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteLongTermMemory = (memoryId: string) => requestVoid(`/api/research/memory/${encodeURIComponent(memoryId)}`, { method: "DELETE" });
export const promoteProjectMemory = (id: string, memoryId: string) =>
  request<LongTermMemory>(`${projectPath(id)}/memories/${encodeURIComponent(memoryId)}/promote`, { method: "POST" });
export type MemorySuggestion = { kind: LongTermMemoryKind; content: string; suggestionKey: string; tags: string[]; sourceArtifactId: string | null };
export const getMemorySuggestions = (id: string) => request<{ items: MemorySuggestion[] }>(`${projectPath(id)}/memory/suggestions`);
export const distillProjectMemory = (id: string) => request<{ items: LongTermMemory[] }>(`${projectPath(id)}/memory/distill`, { method: "POST" });
export const getRelatedProjects = (id: string, query = "") =>
  request<{ items: { projectId: string; question: string; status: string; title?: string; score?: number }[] }>(`${projectPath(id)}/related-projects${query ? `?query=${encodeURIComponent(query)}` : ""}`);

export type CollaborationRole = "viewer" | "editor";
export type ProjectMembers = { owner: { id: string; username: string }; myRole: string; members: { id: string; username: string; role: CollaborationRole; since: string }[] };
export type ResearchComment = {
  id: string;
  targetType: "project" | "step" | "node" | "artifact" | "review-step" | "source";
  targetId: string;
  parentId: string | null;
  author: string;
  authorId: string;
  body: string;
  resolved: boolean;
  createdAt: string;
  updatedAt: string;
};
export type ResearchAssignment = { id: string; stepId: string; assigneeId: string; assignee: string; note: string; status: "open" | "done"; createdBy: string; createdAt: string; updatedAt: string };

export const getCollaborationCandidates = () =>
  request<{ me: { id: string; username: string; isAdmin: boolean }; multiUser: boolean; items: { id: string; username: string; role: string }[]; sharedWithMe: { projectId: string; role: string; owner?: string }[] }>("/collaboration/candidates");
export const getProjectMembers = (id: string) => request<ProjectMembers>(`${projectPath(id)}/members`);
export const addProjectMember = (id: string, memberId: string, role: CollaborationRole) =>
  request<ProjectMembers>(`${projectPath(id)}/members`, { method: "POST", body: JSON.stringify({ memberId, role }) });
export const removeProjectMember = (id: string, memberId: string) =>
  request<{ removed: string }>(`${projectPath(id)}/members/${encodeURIComponent(memberId)}`, { method: "DELETE" });
export const listResearchComments = (id: string, target: { targetType?: string; targetId?: string } = {}) => {
  const params = new URLSearchParams();
  if (target.targetType) params.set("targetType", target.targetType);
  if (target.targetId) params.set("targetId", target.targetId);
  const suffix = params.toString();
  return request<{ items: ResearchComment[] }>(`${projectPath(id)}/comments${suffix ? `?${suffix}` : ""}`);
};
export const addResearchComment = (id: string, payload: { body: string; targetType: ResearchComment["targetType"]; targetId?: string; parentId?: string }) =>
  request<ResearchComment>(`${projectPath(id)}/comments`, { method: "POST", body: JSON.stringify(payload) });
export const updateResearchComment = (id: string, commentId: string, payload: { body?: string; resolved?: boolean }) =>
  request<ResearchComment>(`${projectPath(id)}/comments/${encodeURIComponent(commentId)}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteResearchComment = (id: string, commentId: string) =>
  requestVoid(`/api/research${projectPath(id)}/comments/${encodeURIComponent(commentId)}`, { method: "DELETE" });
export const listResearchAssignments = (id: string) => request<{ items: ResearchAssignment[] }>(`${projectPath(id)}/assignments`);
export const addResearchAssignment = (id: string, payload: { stepId: string; assigneeId: string; note?: string }) =>
  request<ResearchAssignment>(`${projectPath(id)}/assignments`, { method: "POST", body: JSON.stringify(payload) });
export const updateResearchAssignment = (id: string, assignmentId: string, status: ResearchAssignment["status"]) =>
  request<ResearchAssignment>(`${projectPath(id)}/assignments/${encodeURIComponent(assignmentId)}`, { method: "PUT", body: JSON.stringify({ status }) });

export type UsageBucket = { promptTokens: number; completionTokens: number; totalTokens: number; calls: number; cost: number; priced: boolean };
export type ProjectUsage = {
  total: UsageBucket;
  byRole: (UsageBucket & { role: string })[];
  byModel: (UsageBucket & { model: string })[];
  byDay: (UsageBucket & { date: string })[];
  untrackedCalls: number;
  currency: string;
  pricesConfigured: boolean;
};
export type UsagePrices = { currency: string; models: Record<string, { prompt: number; completion: number }> };

export const getProjectUsage = (id: string) => request<ProjectUsage>(`${projectPath(id)}/usage`);
export const getUsagePrices = () => request<UsagePrices>("/usage/prices");
export const saveUsagePrices = (prices: UsagePrices) => request<UsagePrices>("/usage/prices", { method: "PUT", body: JSON.stringify(prices) });

export type EvalSummary = {
  total: number;
  scored: number;
  passed: number;
  skipped: number;
  failed: number;
  score: number | null;
  byCategory: Record<string, { total: number; passed: number; scored?: number; score?: number | null }>;
  usage?: Partial<UsageBucket>;
  toolCalls?: number;
  meanLatencyMs?: number | null;
};
export type EvalRunSummary = { id: string; model: string; state: string; startedAt: string; finishedAt: string | null; categories: string[]; summary: EvalSummary | null; error: string; problemCount: number };
export type EvalResult = {
  id: string;
  category: string;
  kind: string;
  passed: boolean | null;
  skipped: boolean;
  checks: { name?: string; passed?: boolean; detail?: string }[];
  answer: string;
  error: string;
  latencyMs: number;
  toolCalls: number;
  tools: string[];
};
export type EvalRun = EvalRunSummary & { results: EvalResult[] };

export const getEvalProblems = () =>
  request<{ version: string; description: string; categories: Record<string, number>; problems: { id: string; category: string; kind: string; prompt: string }[] }>("/eval/problems");
export const startEvalRun = (payload: { modelId: string; categories?: string[]; ids?: string[]; limit?: number; concurrency?: number }) =>
  request<EvalRun>("/eval/runs", { method: "POST", body: JSON.stringify(payload) });
export const listEvalRuns = () => request<{ items: EvalRunSummary[] }>("/eval/runs");
export const getEvalRun = (runId: string) => request<EvalRun>(`/eval/runs/${encodeURIComponent(runId)}`);
export const cancelEvalRun = (runId: string) => request<EvalRun>(`/eval/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });

// ------------------------------------------------------------------
// Self-Improvement Closed Loop API (执行 → 记录 → 反思 → 上报 → 审批 → 实践 → 验证 → 更新)
// ------------------------------------------------------------------
export type SITokenUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
};

export type SITraceStep = {
  step_index: number;
  reasoning_summary: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_output: string;
  tool_ok: boolean;
  elapsed_ms: number;
};

export type SIStrategySelection = {
  selected_strategy_ids: string[];
  selections: { strategy_id: string; version: number; reason: string }[];
  overall_plan: string;
};

export type SIVerificationResult = {
  passed: boolean;
  verifier_type: string;
  expected_answer: string;
  extracted_answer: string;
  verifier_output: string;
  details: Record<string, unknown>;
};

export type SITaskTrace = {
  id: string;
  agent_id: string;
  task_id: string;
  problem_text: string;
  available_strategy_versions: { strategy_id: string; version: number; name: string; status: string }[];
  strategy_selection: SIStrategySelection;
  steps: SITraceStep[];
  final_answer: string;
  verification_result: SIVerificationResult;
  token_usage: SITokenUsage;
  total_elapsed_ms: number;
  created_at: string;
};

export type SIExperience = {
  id: string;
  agent_id: string;
  experience_type: "success" | "failure" | "heuristic" | "pitfall";
  content: string;
  source_trace_ids: string[];
  created_at: string;
  is_compressed: boolean;
  compressed_from: string[];
  strategy_ids: string[];
  tags: string[];
};

export type SIStrategy = {
  id: string;
  name: string;
  version: number;
  owner: "agent_a" | "agent_b" | "meta";
  status: "draft" | "proposed" | "approved_for_trial" | "candidate" | "active" | "deprecated" | "rejected" | "rolled_back";
  applicable_conditions: string;
  execution_steps: string[];
  taboos_and_counterexamples: string[];
  parent_version: number | null;
  origin_proposal_id: string | null;
  evidence: string[];
  proposed_by: string;
  approved_by: string | null;
  created_at: string;
  updated_at: string;
};

export type SIStrategyTreeItem = {
  strategy_id: string;
  name: string;
  owner: string;
  active_version: number | null;
  latest_version: number;
  versions: SIStrategy[];
};

export type SIStrategyProposal = {
  id: string;
  target_agent: "agent_a" | "agent_b" | "meta";
  base_strategy_id: string | null;
  base_strategy_version: number | null;
  new_strategy_id: string | null;
  new_strategy_version: number | null;
  problem_found: string;
  claimed_failure_signature: string;
  novel_method_evidence: string;
  recommendations: { type: string; summary: string; risk: "low" | "medium" | "high"; evidence_trace_ids: string[] }[];
  evidence_trace_ids: string[];
  experience_ids: string[];
  structured_changes: {
    name: string;
    applicable_conditions: string;
    execution_steps: string[];
    taboos_and_counterexamples: string[];
    change_summary: string;
  };
  expected_benefit: string;
  applicable_scope: string;
  status: "draft" | "proposed" | "approved" | "rejected" | "revision_requested" | "completed";
  review_comment: string;
  proposed_by: string;
  reviewed_by: string | null;
  token_usage: SITokenUsage;
  created_at: string;
  updated_at: string;
};

export type SIExperimentTask = {
  id: string;
  proposal_id: string;
  target_agent: string;
  strategy_id: string;
  old_strategy_version: number | null;
  new_strategy_version: number;
  benchmark_problem_ids: string[];
  runs_per_problem_k: number;
  metrics_and_thresholds: {
    min_pass_rate_delta: number;
    max_token_increase_ratio: number;
    min_absolute_pass_rate: number;
  };
  status: string;
  created_at: string;
};

export type SIExperimentReport = {
  id: string;
  task_id: string;
  proposal_id: string;
  target_agent: string;
  strategy_id: string;
  old_strategy_version: number | null;
  new_strategy_version: number;
  old_run_trace_ids: string[];
  new_run_trace_ids: string[];
  all_trace_ids: string[];
  old_pass_rate: number;
  new_pass_rate: number;
  old_avg_tokens: number;
  new_avg_tokens: number;
  old_avg_elapsed_ms: number;
  new_avg_elapsed_ms: number;
  problem_breakdown: {
    problem_id: string;
    title: string;
    category: string;
    runs_k: number;
    old_passed: number;
    new_passed: number;
    old_pass_rate: number;
    new_pass_rate: number;
    old_trace_ids: string[];
    new_trace_ids: string[];
  }[];
  rule_judgment: {
    passed: boolean;
    old_pass_rate: number;
    new_pass_rate: number;
    pass_rate_delta: number;
    old_avg_tokens: number;
    new_avg_tokens: number;
    token_increase_ratio: number;
    reason: string;
    promoted_to_candidate: boolean;
  };
  meta_review_comment: string;
  meta_approved: boolean | null;
  human_approved: boolean | null;
  created_at: string;
};

export type SIAuditLog = {
  id: string;
  who: string;
  when: string;
  target_type: string;
  target_id: string;
  from_state: string;
  to_state: string;
  rationale: string;
  metadata: Record<string, unknown>;
};

export type SISettings = {
  require_human_approval: boolean;
  default_runs_per_problem_k: number;
  min_pass_rate_delta: number;
  max_token_increase_ratio: number;
  evolution_memos: { proposal_id: string; memo: string; created_at: string }[];
};

export type SIBenchmarkProblem = {
  id: string;
  category: string;
  difficulty: string;
  title: string;
  problem_text: string;
  verifier: { type: string; expected: string; code?: string; symbols?: string[] };
};

export type SIProgress = {
  running: boolean;
  stage: string;
  message: string;
  started_at: string | null;
  finished_at: string | null;
  events: { stage: string; message: string; timestamp: string }[];
  last_result: Record<string, unknown> | null;
  error: string | null;
};

export type SIDashboardSnapshot = {
  settings: SISettings;
  strategy_trees: {
    agent_a: SIStrategyTreeItem[];
    agent_b: SIStrategyTreeItem[];
    meta: SIStrategyTreeItem[];
  };
  experiences: {
    agent_a: SIExperience[];
    agent_b: SIExperience[];
    meta: SIExperience[];
  };
  proposals: SIStrategyProposal[];
  experiment_tasks: SIExperimentTask[];
  experiment_reports: SIExperimentReport[];
  traces: SITaskTrace[];
  audit_logs: SIAuditLog[];
  benchmark_problems: SIBenchmarkProblem[];
  summary: {
    total_traces: number;
    agent_a_traces: number;
    agent_b_traces: number;
    total_proposals: number;
    total_experiments: number;
    total_audit_logs: number;
  };
  progress: SIProgress;
};

export const getSelfImprovementDashboard = () =>
  request<SIDashboardSnapshot>("/self-improvement/dashboard");

export const getSelfImprovementProgress = () =>
  request<SIProgress>("/self-improvement/progress");

export const getSelfImprovementTrace = (traceId: string) =>
  request<SITaskTrace>(`/self-improvement/traces/${encodeURIComponent(traceId)}`);

export const updateSelfImprovementSettings = (payload: {
  require_human_approval?: boolean;
  default_runs_per_problem_k?: number;
  min_pass_rate_delta?: number;
  max_token_increase_ratio?: number;
}) =>
  request<SISettings>("/self-improvement/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export const triggerSelfImprovementEvolutionRound = (payload?: {
  agent_id?: string;
  problem_id?: string;
  initial_problem_ids?: string[];
  problem_text?: string;
  benchmark_count?: number;
  runs_per_problem_k?: number;
}) =>
  request<{ started: boolean; progress: SIProgress }>("/self-improvement/evolve", {
    method: "POST",
    body: JSON.stringify(payload || {}),
  });

export const solveSelfImprovementProblem = (payload: {
  agent_id: "agent_a" | "agent_b";
  problem_id: string;
}) =>
  request<SITaskTrace>("/self-improvement/solve", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const rollbackSelfImprovementStrategy = (payload: {
  strategy_id: string;
  version?: number;
  actor?: string;
  rationale?: string;
}) =>
  request<{ rolled_back: SIStrategy; restored_active: SIStrategy | null }>("/self-improvement/strategies/rollback", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const reviewSelfImprovementProposal = (
  proposalId: string,
  payload: {
    decision: "approve" | "reject" | "request_revision";
    review_comment?: string;
    reviewer_role?: string;
    benchmark_problem_ids?: string[];
    runs_per_problem_k?: number;
  },
) =>
  request<{ proposal: SIStrategyProposal; experiment_task: SIExperimentTask | null }>(
    `/self-improvement/proposals/${encodeURIComponent(proposalId)}/review`,
    { method: "POST", body: JSON.stringify(payload) },
  );

export const approveSelfImprovementReport = (reportId: string, payload?: { approved?: boolean; comment?: string }) =>
  request<{ strategy: SIStrategy; report: SIExperimentReport }>(
    `/self-improvement/reports/${encodeURIComponent(reportId)}/approve`,
    { method: "POST", body: JSON.stringify(payload || { approved: true }) },
  );

export const compressSelfImprovementExperiences = (agentId: string) =>
  request<{ compressed_count: number; items: SIExperience[] }>(
    `/self-improvement/experiences/${encodeURIComponent(agentId)}/compress`,
    { method: "POST" },
  );

