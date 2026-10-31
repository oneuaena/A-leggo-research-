"""A-leggo Math Research API with a mandatory human plan approval gate."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
import json
import hashlib
import tempfile
from pathlib import Path, PurePosixPath
import time
from urllib.parse import quote
import re
import os
import math
import subprocess
import httpx
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, AliasChoices, field_validator

from aleggo.study.model_registry import (
    ModelRouter,
    ModelRuntimeError,
    atlas_agent_id,
    describe_model_access,
    has_research_model_access,
    list_available_profiles,
    public_model_catalog,
    test_model_access,
)
from aleggo.study.research_runtime import (
    _extract_json,
    generate_plan,
    MultiAgentRuntime,
    start_project,
    stop_project,
    validate_plan_dependencies,
    validate_research_model,
)
from aleggo.study.research_store import ResearchStore
from aleggo.study.research_inputs import ResearchInputs
from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_documents import MAX_DOCUMENT_BYTES, import_research_document
from aleggo.study.research_literature import search_literature
from aleggo.study.research_literature import plan_literature_queries
from aleggo.study.research_migration import MAX_IMPORT_BYTES, import_mra_database
from aleggo.study.research_reports import build_markdown_report, build_pdf_report
from aleggo.study.research_workbench import ResearchWorkbench
from aleggo.study.research_thread import build_thread_page
from aleggo.study.research_collaborator import (
    cancel_discussion,
    create_discussion,
    resume_pending_discussions,
    schedule_request_drain,
    start_discussion,
)
from aleggo.study.research_search import finite_discovery_search, integer_counterexample_search
from aleggo.study.lean_formal import (
    DEFAULT_TIMEOUT_SECONDS, LeanConfigurationError, UnsafeLeanInput, detect_lean_workspace, run_kernel_check,
)
from aleggo.study import research_sharing
from aleggo.study.settings import save_aleggo_settings
from aleggo.multi_user.context import get_current_user



async def _bind_shared_project(request: Request) -> None:
    """Requests for a project another account shared with this one use the owner's store.

    ``async`` so the binding is set in the request's own context (the endpoint
    and any background task it starts see it).  Viewers may only read and
    comment; only the owner may delete a shared project.
    """
    project_id = str(request.path_params.get("project_id") or "")
    try:
        binding = research_sharing.binding_for_request(project_id, request.method, request.url.path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    research_sharing.bind(binding)


router = APIRouter(dependencies=[Depends(_bind_shared_project)])
_STEP_STATUS = {"pending", "running", "completed", "cancelled", "changed", "superseded", "blocked"}


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question: str = Field(min_length=1, max_length=12000)
    model_id: str = Field(default="research.meridian", alias="modelId")

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入研究问题。")
        return value.strip()


class WorkspaceFileOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=2000)
    reveal: bool = True


class PlanGenerate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model_id: str = Field(default="research.meridian", alias="modelId")
    context: str = Field(default="", max_length=20000)


class ResearchStep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=8000)
    status: str = "pending"
    order: int = Field(default=0, ge=0, le=1000)
    priority: int = Field(default=3, ge=1, le=5)
    enabled: bool = True
    branch: str = Field(default="main", max_length=200)
    dependencies: list[str] = Field(default_factory=list, max_length=30)
    assigned_agent: str = Field(default="explorer", alias="assignedAgent", max_length=80)
    required_tools: list[str] = Field(default_factory=list, alias="requiredTools", max_length=20)
    expected_output: str = Field(default="", alias="expectedOutput", max_length=4000)
    verification_method: str = Field(default="", alias="verificationMethod", max_length=4000)
    milestone: str = Field(default="", max_length=240)
    human_notes: str = Field(default="", alias="humanNotes", max_length=4000)
    agent_notes: str = Field(default="", alias="agentNotes", max_length=4000)
    research_budget: dict[str, float] = Field(default_factory=dict, alias="researchBudget")
    formal_binding_id: str = Field(default="", alias="formalBindingId", max_length=200)
    proof_source: str = Field(default="", alias="proofSource", max_length=100000)

    @field_validator("research_budget")
    @classmethod
    def validate_research_budget(cls, value):
        if any(not math.isfinite(amount) or amount < 0 for amount in value.values()):
            raise ValueError("Research budget values must be finite non-negative numbers.")
        return value


class ResearchInputProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)
    kind: Literal["text", "document"] = "text"


class ResearchInputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str = Field(default="", max_length=20000)
    source_ids: list[str] = Field(default_factory=list, alias="sourceIds", max_length=20)


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    input_requests: list[ResearchInputProposal] = Field(default_factory=list, alias="inputRequests", max_length=20)
    objective: str = Field(min_length=1, max_length=8000)
    problem_formalization: str = Field(default="", alias="problemFormalization", max_length=12000)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    definitions: list[str] = Field(default_factory=list, max_length=100)
    key_questions: list[str] = Field(default_factory=list, alias="keyQuestions", max_length=100)
    proposed_lemmas: list[str] = Field(default_factory=list, alias="proposedLemmas", max_length=100)
    research_branches: list[str] = Field(default_factory=list, alias="researchBranches", max_length=60)
    suggested_experiments: list[str] = Field(default_factory=list, alias="suggestedExperiments", max_length=100)
    proof_strategy: str = Field(default="", alias="proofStrategy", max_length=12000)
    counterexample_strategy: str = Field(default="", alias="counterexampleStrategy", max_length=12000)
    tools_required: list[str] = Field(default_factory=list, alias="toolsRequired", max_length=60)
    verification_strategy: str = Field(default="", alias="verificationStrategy", max_length=12000)
    expected_outputs: list[str] = Field(default_factory=list, alias="expectedOutputs", max_length=100)
    risk_uncertainty: str = Field(default="", alias="riskUncertainty", max_length=12000)
    steps: list[ResearchStep] = Field(min_length=1, max_length=40)
    branches: list[dict] = Field(default_factory=list, max_length=60)


class PlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plan: ResearchPlan
    reason: str = Field(default="Researcher edited plan", max_length=1000)
    save_draft: bool = Field(default=False, alias="saveDraft")


class InterventionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "human_note", "request_critique", "request_counterexample_search",
        "request_verification", "mark_useful", "mark_invalid",
        "human_result", "human_lemma", "request_literature", "request_proof", "request_counterexample",
        "request_numerical", "request_symbolic", "add_human_idea", "discuss",
    ] = Field(validation_alias=AliasChoices("kind", "type"))
    details: dict = Field(default_factory=dict, validation_alias=AliasChoices("details", "payload"))
    message: str = Field(default="", max_length=8000)
    branch: str = Field(default="", max_length=120)


class ModelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=40000)


class ModelCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model_id: str = Field(alias="modelId")
    messages: list[ModelMessage] = Field(min_length=1, max_length=80)
    session_id: str | None = Field(default=None, alias="sessionId", max_length=200)


class ALEGGOSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    atlas_agent_id: str = Field(default="", alias="atlasAgentId", max_length=200)


class LeanProjectSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    confirm_before_check: bool = Field(alias="confirmBeforeCheck")
    auto_call_enabled: bool = Field(alias="autoCallEnabled")


class ModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    study_profile_id: str = Field(default="", alias="studyProfileId", max_length=200)
    study_model_overrides: dict[str, str] = Field(default_factory=dict, alias="studyModelOverrides")


class ModelTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model_id: str = Field(alias="modelId", max_length=100)


class BranchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=120)
    parent_name: str = Field(default="main", alias="parentName", max_length=120)
    limits: dict[str, int] = Field(default_factory=dict)


class BranchBudgetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limits: dict[str, int]


class LiteratureSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)


class SupervisionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["AUTONOMOUS", "MILESTONE_REVIEW", "STEP_BY_STEP"]


class WorkspaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4000)


class LiteratureCuration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_status: Literal["unread", "important", "read", "irrelevant"] | None = None
    classification: Literal["foundational", "classic", "survey", "direct", "methodological", "recent", "preprint", "contradictory", "supporting", "background"] | None = None
    notes: str | None = Field(default=None, max_length=8000)
    important: bool | None = None


class LiteratureLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_id: str = Field(alias="sourceId", min_length=1, max_length=200)
    entity_type: Literal["artifact", "graph_node", "branch", "step", "lemma", "conjecture", "experiment"] = Field(alias="entityType")
    entity_id: str = Field(alias="entityId", min_length=1, max_length=200)
    relationship: Literal["SUPPORTS", "CONTRADICTS", "USES", "RELATED_TO", "CITES"] = "RELATED_TO"
    notes: str = Field(default="", max_length=4000)


class RecommendationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "modify", "reject", "alternative", "add_human_idea"]
    rationale: str = Field(default="", max_length=8000)
    modified: dict | None = None


class DecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=8000)
    source: str = Field(default="researcher", max_length=300)
    branch: str = Field(default="", max_length=120)
    artifact_id: str | None = Field(default=None, alias="artifactId", max_length=200)


class BranchStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    priority: int | None = Field(default=None, ge=1, le=5)
    reason: str = Field(default="", max_length=8000)


class LiteratureNoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: dict = Field(default_factory=dict)


class LiteratureDoiImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doi: str = Field(min_length=4, max_length=500)


class GraphNodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    node_type: Literal["question", "conjecture", "definition", "lemma", "proposition", "theorem", "proof_attempt", "counterexample", "evidence", "experiment", "source"] = Field(alias="type")
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=100000)
    branch: str = Field(default="main", max_length=120)
    artifact_id: str | None = Field(default=None, alias="artifactId", max_length=200)
    metadata: dict = Field(default_factory=dict)


class GraphEdgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_id: str = Field(alias="sourceId", min_length=1, max_length=200)
    target_id: str = Field(alias="targetId", min_length=1, max_length=200)
    edge_type: Literal["SUPPORTS", "REFUTES", "DEPENDS_ON", "DERIVED_FROM", "VERIFIES", "CITES", "USES", "CONTRADICTS"] = Field(alias="type")
    evidence: str = Field(default="", max_length=12000)
    metadata: dict = Field(default_factory=dict)


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    content: str = Field(min_length=1, max_length=8000)
    confirmed: bool = False
    scope: Literal["project", "workspace"] = "project"


class MemoryConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool


class ArtifactFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: Literal["useful", "invalid"]


class FormalBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    claim_node_id: str = Field(alias="claimNodeId", min_length=1, max_length=200)
    original_statement: str = Field(alias="originalStatement", min_length=1, max_length=12000)
    formal_ir: str = Field(alias="formalIr", min_length=1, max_length=12000)
    lean_statement: str = Field(alias="leanStatement", min_length=1, max_length=12000)
    confirm_mapping: bool = Field(alias="confirmMapping")


class FormalProofCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    proof_source: str = Field(alias="proofSource", min_length=1, max_length=40000)


class FormalBindingProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    claim_node_id: str = Field(alias="claimNodeId", min_length=1, max_length=200)


class FormalBindingConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    original_statement: str = Field(alias="originalStatement", min_length=1, max_length=12000)
    formal_ir: str = Field(alias="formalIr", min_length=1, max_length=12000)
    lean_statement: str = Field(alias="leanStatement", min_length=1, max_length=12000)


class FiniteDiscoverySearch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    problem: dict
    branch: str = Field(default="main", max_length=120)
    seed: int = Field(default=42, ge=0, le=2_147_483_646, strict=True)
    evaluation_budget: int = Field(default=1000, alias="evaluationBudget", ge=1, le=10000, strict=True)


class IntegerCounterexampleSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    left_expression: str = Field(alias="leftExpression", min_length=1, max_length=2000)
    right_expression: str = Field(alias="rightExpression", min_length=1, max_length=2000)
    variable: str = Field(default="n", min_length=1, max_length=32)
    start: int = Field(ge=-1_000_000_000_000, le=1_000_000_000_000, strict=True)
    end: int = Field(ge=-1_000_000_000_000, le=1_000_000_000_000, strict=True)
    branch: str = Field(default="main", max_length=120)


def _store() -> ResearchStore:
    binding = research_sharing.current_binding()
    if binding is not None:
        return binding.store
    return ResearchStore()


def _require_project(project_id: str, store: ResearchStore) -> dict:
    try:
        research_sharing.revalidate_share(project_id)
        research_sharing.assert_bound_project(project_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="未找到该研究项目。")
    return project


def _require_model(model_id: str) -> None:
    if not validate_research_model(model_id or "research.meridian"):
        raise HTTPException(status_code=422, detail="无效的模型标识。")


def _runtime_error(exc: ModelRuntimeError) -> HTTPException:
    detail: dict[str, Any] = {"message": str(exc)}
    if exc.code:
        detail["code"] = exc.code
    if exc.status_code:
        detail["providerStatus"] = exc.status_code
    if exc.provider_request_id:
        detail["providerRequestId"] = exc.provider_request_id
    return HTTPException(status_code=503, detail=detail)


@router.get("/models")
async def model_catalog():
    return {
        "items": public_model_catalog(),
        "defaults": {"study": "study.compass", "research": "research.meridian"},
        "credentialConfigured": has_research_model_access(),
        "atlasAgentConfigured": bool(atlas_agent_id()),
        "access": describe_model_access(),
    }


@router.get("/model-access")
async def model_access():
    user = get_current_user()
    access = describe_model_access()
    if not user.is_admin:
        return {"isAdmin": False, "access": access}
    return {
        "isAdmin": True,
        "access": access,
        "profiles": list_available_profiles(),
        "settings": {
            "studyProfileId": access.get("profile_id_setting", ""),
            "studyModelOverrides": describe_model_access()["products"],
        },
    }


@router.put("/model-config")
async def update_model_config(payload: ModelConfigUpdate):
    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail="Only an administrator can change the shared model mapping.")
    overrides = {
        product: model
        for product, model in payload.study_model_overrides.items()
        if model.strip()
    }
    try:
        saved = save_aleggo_settings(
            {
                "study_profile_id": payload.study_profile_id,
                "study_model_overrides": overrides,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "saved": True,
        "access": describe_model_access(),
        "settings": {
            "studyProfileId": saved["study_profile_id"],
            "studyModelOverrides": saved["study_model_overrides"],
        },
    }


@router.post("/model-test")
async def run_model_test(payload: ModelTestRequest):
    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail="Only an administrator can run the connection test.")
    result = await test_model_access(payload.model_id)
    return result


@router.get("/settings")
async def product_settings():
    user = get_current_user()
    return {
        "isAdmin": user.is_admin,
        "atlasAgentId": atlas_agent_id() if user.is_admin else "",
        "atlasAgentConfigured": bool(atlas_agent_id()),
        "credentialConfigured": has_research_model_access(),
    }


@router.put("/settings")
async def update_product_settings(payload: ALEGGOSettingsUpdate):
    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail="Only an administrator can change shared A-leggo settings.")
    try:
        saved = save_aleggo_settings({"atlas_agent_id": payload.atlas_agent_id})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "isAdmin": True,
        "atlasAgentId": saved["atlas_agent_id"],
        "atlasAgentConfigured": bool(atlas_agent_id()),
        "credentialConfigured": has_research_model_access(),
    }


@router.post("/model-completion")
async def model_completion(payload: ModelCompletionRequest):
    if payload.model_id not in {"study.quill", "study.compass", "study.atlas"}:
        raise HTTPException(status_code=409, detail="Math Research must begin from a proposed plan.")
    router_service = ModelRouter()
    try:
        return await router_service.complete(
            payload.model_id,
            [item.model_dump() for item in payload.messages],
            session_id=payload.session_id,
        )
    except ModelRuntimeError as exc:
        raise _runtime_error(exc) from exc


@router.get("/projects")
async def list_projects():
    items = _store().list_projects()
    try:
        shared = research_sharing.shared_project_summaries()
    except Exception:  # the share registry must never hide the researcher's own projects
        shared = []
    own = {item["id"] for item in items}
    return {"items": items + [item for item in shared if item["id"] not in own]}


@router.post("/projects", status_code=201)
async def create_project(payload: ProjectCreate):
    _require_model(payload.model_id)
    return _store().create_project(payload.question, payload.model_id)


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    store = _store()
    project = _require_project(project_id, store)
    domain = ResearchDomainStore(store)
    return {
        **project, "artifacts": store.list_artifacts(project_id), "activity": store.list_interventions(project_id),
        "branches": domain.list_branches(project_id), "graph": domain.list_graph(project_id),
        "sources": domain.list_sources(project_id), "memories": domain.list_memories(project_id),
        "formalBindings": domain.list_formal_bindings(project_id),
        "literatureLinks": domain.list_literature_links(project_id),
        "workbench": ResearchWorkbench(store).snapshot(project_id),
    }


@router.put("/projects/{project_id}/model")
async def update_model(project_id: str, payload: PlanGenerate):
    _require_model(payload.model_id)
    store = _store()
    project = _require_project(project_id, store)
    if project["status"] in {"RUNNING", "VERIFYING", "REPLANNING"}:
        raise HTTPException(status_code=409, detail="更改模型前请先暂停当前运行。")
    if not store.set_model(project_id, payload.model_id):
        raise HTTPException(status_code=404, detail="Research project not found.")
    return store.get_project(project_id)


@router.post("/projects/{project_id}/plan/generate")
async def generate_research_plan(project_id: str, payload: PlanGenerate):
    store = _store()
    project = _require_project(project_id, store)
    _require_model(payload.model_id)
    saved_context = ResearchDomainStore(store).research_context(project_id, project["question"])
    planning_context = "\n\n".join(part for part in (payload.context.strip(), saved_context, ResearchInputs(store).context(project_id)) if part)
    replacing_active_run = project["status"] in {"RUNNING", "VERIFYING", "REPLANNING"}
    if replacing_active_run:
        store.set_status(project_id, "REPLANNING", actor="researcher")
    else:
        store.set_status(project_id, "PLANNING", actor="researcher")
    store.set_model(project_id, payload.model_id)
    try:
        plan = await MultiAgentRuntime(store).propose_plan(project["question"], planning_context, payload.model_id)
    except ModelRuntimeError as exc:
        current = store.get_project(project_id)
        if current and current["status"] in {"PLANNING", "REPLANNING"}:
            store.set_status(project_id, "PAUSED" if replacing_active_run else "FAILED", error=str(exc))
        raise _runtime_error(exc) from exc
    if plan.get("_needsInput"):
        ResearchInputs(store).register(project_id, plan["inputRequests"], reopen=True)
        ResearchInputs(store).pause(project_id)
        return await get_project(project_id)
    try:
        plan_model = ResearchPlan.model_validate(plan).model_dump(by_alias=True)
    except ValidationError as exc:
        current = store.get_project(project_id)
        if current and current["status"] in {"PLANNING", "REPLANNING"}:
            store.set_status(project_id, "PAUSED" if replacing_active_run else "FAILED", error="The model returned an invalid plan structure.")
        raise HTTPException(status_code=502, detail="模型返回了无效的研究计划。") from exc
    store.save_plan(
        project_id,
        plan_model,
        changed_by="researcher",
        change_reason="Generated proposed plan",
        change_summary="Planner generated a proposed plan; no research execution has started.",
        status="AWAITING_APPROVAL",
        generated=True,
    )
    inputs = ResearchInputs(store)
    if inputs.register(project_id, plan_model.get("inputRequests", [])):
        inputs.pause(project_id)
    return await get_project(project_id)


@router.post("/projects/{project_id}/inputs/{request_id}/respond")
async def respond_research_input(project_id: str, request_id: str, payload: ResearchInputResponse):
    store = _store()
    _require_project(project_id, store)
    try:
        ResearchInputs(store).respond(project_id, request_id, payload.text, payload.source_ids)
        return await get_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/projects/{project_id}/plan")
async def update_research_plan(project_id: str, payload: PlanUpdate):
    store = _store()
    project = _require_project(project_id, store)
    plan = payload.plan.model_dump(by_alias=True)
    step_ids = [item["id"] for item in plan["steps"]]
    if len(step_ids) != len(set(step_ids)):
        raise HTTPException(status_code=422, detail="Research step IDs must be unique.")
    for step in plan["steps"]:
        if step["status"] not in _STEP_STATUS:
            raise HTTPException(status_code=422, detail="A research step has an unsupported status.")
    dependency_error = validate_plan_dependencies(plan["steps"])
    if dependency_error:
        raise HTTPException(status_code=422, detail=dependency_error)
    summary = f"Researcher saved {len(plan['steps'])} steps; enabled: {sum(1 for item in plan['steps'] if item['enabled'])}."
    status = "PLAN_READY" if payload.save_draft else "AWAITING_APPROVAL"
    result = store.save_plan(
        project_id,
        plan,
        changed_by="researcher",
        change_reason=payload.reason,
        change_summary=summary,
        status=status,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Research project not found.")
    return result


@router.post("/projects/{project_id}/approve")
async def approve_research_plan(project_id: str):
    store = _store()
    project = _require_project(project_id, store)
    if project["currentVersion"] <= 0 or not project["plan"]:
        raise HTTPException(status_code=409, detail="Generate and save a research plan before approving it.")
    result = store.approve(project_id)
    if result is None:
        raise HTTPException(status_code=409, detail="A research plan is required before approval.")
    workbench = ResearchWorkbench(store)
    workbench.record_event(project_id, "plan_approved", "researcher", summary=f"Plan version {result['currentVersion']} approved.", details={"version": result["currentVersion"]})
    workbench.record_decision(project_id, "plan_approved", f"Researcher approved plan version {result['currentVersion']}.", source="approved_plan")
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    intelligence = LiteratureIntelligence(store)
    auto_literature = await intelligence.search(project_id, trigger_type="approved_plan",
                                                 trigger_id=str(result["currentVersion"]), limit=40)
    domain = ResearchDomainStore(store)
    for source in auto_literature.get("items", []):
        domain.link_literature(project_id, source["id"], "branch", "main", "RELATED_TO", "Initial search after plan approval.")
    provider_summary = "; ".join(f"{name}: {message}" for name, message in
                                 (auto_literature.get("providerStatuses") or auto_literature.get("warnings") or {}).items())
    workbench.record_event(project_id, "initial_literature_search", "literature-intelligence",
                          summary=f"Initial literature search: {auto_literature.get('availabilityStatus')}; saved {len(auto_literature.get('items', []))} records. Provider status: {provider_summary}",
                          details=auto_literature)
    workbench.sync_workspace(project_id)
    result["initialLiteratureSearch"] = auto_literature
    return result


@router.get("/projects/{project_id}/workbench")
async def get_research_workbench(project_id: str):
    store = _store()
    _require_project(project_id, store)
    return ResearchWorkbench(store).snapshot(project_id)


@router.put("/projects/{project_id}/supervision")
async def set_research_supervision(project_id: str, payload: SupervisionUpdate):
    store = _store()
    _require_project(project_id, store)
    try:
        state = ResearchWorkbench(store).configure_supervision(project_id, payload.mode)
        ResearchWorkbench(store).sync_workspace(project_id)
        return state
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/workspace")
async def choose_research_workspace(project_id: str, payload: WorkspaceUpdate):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchWorkbench(store).set_workspace(project_id, payload.path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/workspace/sync")
async def sync_research_workspace(project_id: str):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchWorkbench(store).sync_workspace(project_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/workspace/open")
async def open_research_workspace(project_id: str):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchWorkbench(store).open_workspace(project_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _attachment_header(filename: str) -> str:
    return f"attachment; filename=aleggo-export{Path(filename).suffix}; filename*=UTF-8''{quote(filename)}"


def _workspace_file(store: ResearchStore, project_id: str, path: str) -> bytes:
    _require_project(project_id, store)
    try:
        return ResearchWorkbench(store).workspace_file(project_id, path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/workspace/files")
async def list_research_workspace_files(project_id: str):
    store = _store()
    _require_project(project_id, store)
    try:
        return {"items": ResearchWorkbench(store).list_workspace_files(project_id)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/workspace/file")
async def download_research_workspace_file(project_id: str, path: str):
    content = _workspace_file(_store(), project_id, path)
    description = ResearchWorkbench.file_description(path, content)
    return Response(content=content, media_type=description["mediaType"], headers={
        "Content-Disposition": _attachment_header(Path(path).name),
        "X-Content-SHA256": hashlib.sha256(content).hexdigest(),
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/projects/{project_id}/workspace/file/preview")
async def preview_research_workspace_file(project_id: str, path: str):
    content = _workspace_file(_store(), project_id, path)
    description = ResearchWorkbench.file_description(path, content)
    limit = 256 * 1024
    return {**description, "sha256": hashlib.sha256(content).hexdigest(),
            "text": content[:limit].decode("utf-8", errors="replace") if description["previewKind"] == "text" else "",
            "truncated": description["previewKind"] == "text" and len(content) > limit,
            "previewLimitBytes": limit}


def _open_research_file(path: Path, reveal: bool) -> None:
    if os.name == "nt":
        if reveal:
            subprocess.Popen(["explorer.exe", "/select,", str(path)], close_fds=True)
        else:
            os.startfile(str(path.parent))
    else:
        import sys
        command = ["open", "-R", str(path)] if sys.platform == "darwin" and reveal else ["open" if sys.platform == "darwin" else "xdg-open", str(path.parent)]
        subprocess.Popen(command, close_fds=True)


@router.post("/projects/{project_id}/workspace/file/open")
async def open_research_workspace_file(project_id: str, payload: WorkspaceFileOpen):
    store = _store()
    _workspace_file(store, project_id, payload.path)
    workbench = ResearchWorkbench(store)
    state = workbench.state(project_id)
    if not state["workspaceRoot"]:
        raise HTTPException(status_code=409, detail="请先选择研究工作区文件夹。")
    try:
        workbench.sync_workspace(project_id)
        root = Path(state["workspaceRoot"]).resolve(strict=True)
        relative = str(payload.path or "").replace("\\", "/").strip()
        if (not relative or relative.startswith("/") or relative.startswith("~")
                or ".." in PurePosixPath(relative).parts):
            raise ValueError("工作区相对路径无效。")
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("文件不在所选研究工作区之内。")
        _open_research_file(path, payload.reveal)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"path": str(path), "opened": True, "reveal": payload.reveal}


@router.get("/projects/{project_id}/package")
@router.get("/projects/{project_id}/package/reproducibility")
async def export_reproducibility_package(project_id: str):
    store = _store()
    project = _require_project(project_id, store)
    try:
        body = ResearchWorkbench(store).export_package(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    safe_title = re.sub(r"[^\w\u3400-\u9fff.-]+", "-", project["title"]).strip(".-")[:80] or "research"
    return Response(content=body, media_type="application/zip", headers={"Content-Disposition": _attachment_header(f"{safe_title}-research-package.zip")})


@router.post("/workspace/restore")
async def restore_research_workspace(request: Request):
    from aleggo.study.research_delivery import ResearchDelivery
    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > 260_000_000:
        raise HTTPException(status_code=413, detail="研究包上限为 250 MB。")
    form = await request.form()
    uploaded = form.get("file")
    if uploaded is None or not hasattr(uploaded, "read"):
        raise HTTPException(status_code=422, detail="请将研究包作为 file 上传。")
    data = await uploaded.read(250_000_001)
    if len(data) > 250_000_000:
        raise HTTPException(status_code=413, detail="研究包上限为 250 MB。")
    try:
        return ResearchDelivery(_store()).restore_package(data)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/artifacts/{artifact_id}/download")
async def download_research_artifact(project_id: str, artifact_id: str, format: Literal["markdown", "json", "text"] = "markdown"):
    store = _store()
    _require_project(project_id, store)
    try:
        title, content = ResearchWorkbench(store).artifact_markdown(project_id, artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_title = re.sub(r"[^\w\u3400-\u9fff.-]+", "-", title).strip(".-")[:80] or "research-artifact"
    if format == "json":
        artifact = next(a for a in store.list_artifacts(project_id) if a["id"] == artifact_id)
        content = json.dumps(artifact, ensure_ascii=False, indent=2)
    suffix, media = {"markdown": ("md", "text/markdown"), "json": ("json", "application/json"), "text": ("txt", "text/plain")}[format]
    return Response(content=content.encode("utf-8"), media_type=media + "; charset=utf-8", headers={"Content-Disposition": _attachment_header(f"{safe_title}.{suffix}")})


@router.post("/projects/{project_id}/artifacts/{artifact_id}/open")
async def open_research_artifact(project_id: str, artifact_id: str, request: Request):
    store = _store()
    _require_project(project_id, store)
    workbench = ResearchWorkbench(store)
    state = workbench.state(project_id)
    if not state["workspaceRoot"]:
        raise HTTPException(status_code=409, detail="Choose a research workspace folder before opening local artifacts.")
    workbench.sync_workspace(project_id)
    artifact = next((a for a in store.list_artifacts(project_id) if a["id"] == artifact_id), None)
    if not artifact:
        raise HTTPException(status_code=404, detail="Research artifact not found.")
    root = Path(state["workspaceRoot"]).resolve(strict=True)
    path = Path(artifact["filePath"]).resolve(strict=True)
    if not path.is_relative_to(root):
        raise HTTPException(status_code=409, detail="产物不在所选工作区之内。")
    payload = await request.json()
    _open_research_file(path, bool(payload.get("reveal", True)))
    return {"path": str(path), "opened": True, "reveal": payload.get("reveal", True)}


@router.post("/projects/{project_id}/decisions", status_code=201)
async def create_research_decision(project_id: str, payload: DecisionCreate):
    store = _store()
    _require_project(project_id, store)
    if payload.artifact_id and not any(item["id"] == payload.artifact_id for item in store.list_artifacts(project_id)):
        raise HTTPException(status_code=422, detail="The linked artifact is not part of this research project.")
    result = ResearchWorkbench(store).record_decision(project_id, payload.decision, payload.rationale,
                                                     source=payload.source, branch=payload.branch,
                                                     artifact_id=payload.artifact_id)
    ResearchWorkbench(store).sync_workspace(project_id)
    return result


def _proposed_route_plan(project: dict, selected: dict, rationale: str = "") -> dict:
    """Return an editable proposal; saving and approval are separate human actions."""
    base = {key: value for key, value in (project.get("plan") or {}).items()
            if key in {field.alias or name for name, field in ResearchPlan.model_fields.items()}}
    if not base:
        base = {"objective": project["question"], "steps": []}
    base = json.loads(json.dumps(base))
    routes = selected.get("steps") or selected.get("primary") or selected.get("secondary") or [selected.get("idea") or selected.get("message") or rationale]
    if not isinstance(routes, list):
        routes = [routes]
    for route in routes[:max(0, 40 - len(base["steps"]))]:
        if not route:
            continue
        description = str(route.get("description") or route.get("action") or route.get("rationale") or json.dumps(route, ensure_ascii=False)) if isinstance(route, dict) else str(route)
        title = str(route.get("title") or route.get("route") or description) if isinstance(route, dict) else description
        step = ResearchStep(id="humanroute_" + uuid4().hex, title=title[:240], description=description[:8000],
                            order=len(base["steps"]), humanNotes=rationale[:4000])
        base["steps"].append(step.model_dump(by_alias=True))
    return ResearchPlan.model_validate(base).model_dump(by_alias=True)


@router.put("/projects/{project_id}/recommendations/{recommendation_id}")
async def decide_research_recommendation(project_id: str, recommendation_id: str, payload: RecommendationDecision):
    store = _store()
    _require_project(project_id, store)
    try:
        result = ResearchWorkbench(store).decide_recommendation(project_id, recommendation_id, payload.action,
                                                                 payload.rationale, payload.modified)
        if payload.action != "reject":
            rec = next(r for r in ResearchWorkbench(store).list_recommendations(project_id) if r["id"] == recommendation_id)
            selected = payload.modified or rec["content"]
            result["proposedPlan"] = _proposed_route_plan(store.get_project(project_id), selected, payload.rationale)
            result["pendingReview"] = True
        ResearchWorkbench(store).sync_workspace(project_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/start")
async def start_research(project_id: str):
    store = _store()
    project = _require_project(project_id, store)
    if project["approvedVersion"] is None or project["approvedVersion"] != project["currentVersion"]:
        raise HTTPException(status_code=409, detail="Approve the current research plan before starting research.")
    if project["status"] == "PAUSED":
        raise HTTPException(status_code=409, detail="Use Resume for a paused research run.")
    workbench = ResearchWorkbench(store)
    try:
        ResearchInputs(store).register(project_id, (project.get("plan") or {}).get("inputRequests", []))
        ResearchInputs(store).guard(project_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    workbench.set_live(project_id, action="Starting approved research plan", reason="RUNNING")
    workbench.record_event(project_id, "research_started", "researcher", summary=f"Started plan version {project['currentVersion']}.", details={"mode": workbench.state(project_id)["supervisionMode"], "planVersion": project["currentVersion"]})
    try:
        await start_project(project_id, store)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return store.get_project(project_id)


@router.post("/projects/{project_id}/pause")
async def pause_research(project_id: str):
    store = _store()
    project = _require_project(project_id, store)
    if project["status"] not in {"RUNNING", "VERIFYING"}:
        raise HTTPException(status_code=409, detail="Only a running research task can be paused.")
    workbench = ResearchWorkbench(store)
    workbench.set_live(project_id, action="Paused by researcher", reason="RESEARCHER_PAUSED")
    workbench.record_decision(project_id, "research_paused", "Researcher paused the active run.", source="researcher")
    return store.set_status(project_id, "PAUSED", actor="researcher")


@router.post("/projects/{project_id}/resume")
async def resume_research(project_id: str):
    store = _store()
    _require_project(project_id, store)
    try:
        ResearchInputs(store).guard(project_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = ResearchWorkbench(store).state(project_id)
    if state["statusReason"] in {"STALLED", "BLOCKED"}:
        raise HTTPException(status_code=409, detail="This run stopped as STALLED or BLOCKED. Review the blocker or use Resume Anyway to record an override.")
    ResearchWorkbench(store).set_live(project_id, action="Resuming at the next incomplete step", reason="RUNNING", blocker_type="", blocker_detail="")
    ResearchWorkbench(store).record_decision(project_id, "research_resumed", "Researcher resumed from the saved step boundary.", source="researcher")
    try:
        await start_project(project_id, store, resume=True)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return store.get_project(project_id)


@router.post("/projects/{project_id}/resume-anyway")
async def resume_research_anyway(project_id: str):
    store = _store()
    project = _require_project(project_id, store)
    if project["status"] != "PAUSED":
        raise HTTPException(status_code=409, detail="Resume Anyway is available only for a paused run.")
    workbench = ResearchWorkbench(store)
    state = workbench.state(project_id)
    try:
        ResearchInputs(store).guard(project_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if state["statusReason"] not in {"STALLED", "BLOCKED"}:
        raise HTTPException(status_code=409, detail="This project has no stalled or blocked state to override.")
    workbench.record_decision(project_id, "resume_anyway_override", f"Researcher overrode {state['statusReason']}: {state['blockerDetail'] or state['statusReason']}.", source="explicit_resume_anyway")
    workbench.set_live(project_id, action="Researcher override: resuming despite prior blocker", reason="RUNNING", blocker_type="", blocker_detail="")
    try:
        await start_project(project_id, store, resume=True)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return store.get_project(project_id)


@router.post("/projects/{project_id}/stop")
async def stop_research(project_id: str):
    store = _store()
    project = _require_project(project_id, store)
    if project["status"] not in {"RUNNING", "VERIFYING", "PAUSED", "REPLANNING", "APPROVED", "FAILED"}:
        raise HTTPException(status_code=409, detail="This research project cannot be stopped in its current state.")
    result = store.set_status(project_id, "CANCELLED", actor="researcher")
    workbench = ResearchWorkbench(store)
    workbench.set_live(project_id, action="Stopped by researcher", reason="STOPPED", clear_run_start=True)
    workbench.record_decision(project_id, "research_stopped", "Researcher stopped this project.", source="researcher")
    await stop_project(project_id, store)
    return result


@router.post("/projects/{project_id}/interventions", status_code=201)
async def record_intervention(project_id: str, payload: InterventionRequest):
    store = _store()
    project = _require_project(project_id, store)
    details = dict(payload.details)
    if payload.message:
        details["message"] = payload.message
    branch = payload.branch or str(details.get("branch") or "main")
    if not ResearchDomainStore(store).get_branch(project_id, branch):
        raise HTTPException(status_code=422, detail="Research branch not found.")
    result = {"recorded": payload.kind, "projectId": project_id}
    if payload.kind in {"human_note", "human_result", "human_lemma"}:
        note = str(details.get("note") or details.get("message") or details.get("content") or "").strip()
        if not note or len(note) > 8000:
            raise HTTPException(status_code=422, detail="Enter a note of at most 8000 characters.")
        artifact = store.create_artifact(
            project_id,
            project["currentVersion"],
            "",
            "researcher",
            {"human_note": "Researcher note", "human_result": "Researcher result", "human_lemma": "Researcher lemma"}[payload.kind],
            note,
            status="human-note",
            branch_name=branch,
            metadata={"evidenceClassification": "CONJECTURAL", "humanSupplied": True},
        )
        details.update({"artifactId": artifact["id"], "note": note, "branch": branch})
        result["artifactId"] = artifact["id"]
        if payload.kind != "human_note":
            node = ResearchDomainStore(store).create_graph_node(project_id, "lemma" if payload.kind == "human_lemma" else "evidence",
                                                               note[:200], note, branch_name=branch, artifact_id=artifact["id"],
                                                               metadata={"humanSupplied": True, "evidenceClassification": "CONJECTURAL"})
            result["nodeId"] = node["id"]
    elif payload.kind in {"mark_useful", "mark_invalid"}:
        artifact_id = str(details.get("artifactId") or "")
        if not ResearchDomainStore(store).update_artifact_feedback(project_id, artifact_id, "useful" if payload.kind == "mark_useful" else "invalid"):
            raise HTTPException(status_code=404, detail="Research artifact not found.")
    elif payload.kind == "add_human_idea":
        result["proposedPlan"] = _proposed_route_plan(project, details, str(details.get("message") or details.get("note") or ""))
        result["pendingReview"] = True
    elif payload.kind == "discuss":
        # A direct question to the AI collaborator: answered now, in the
        # background, without waiting for (or altering) the approved plan.
        message = str(details.get("message") or details.get("note") or "").strip()
        if not message or len(message) > 8000:
            raise HTTPException(status_code=422, detail="Enter a message of at most 8000 characters.")
        queue_id = create_discussion(store, project_id, message, branch)
        details.update({"message": message, "branch": branch, "queueId": queue_id})
        result.update({"queueId": queue_id, "status": "RUNNING"})
    else:
        kind = "request_counterexample" if payload.kind == "request_counterexample_search" else payload.kind
        queue_id, now = "intervention_" + uuid4().hex, datetime.now(UTC).isoformat()
        queued = {"message": str(details.get("message") or details.get("note") or ""), "branch": branch,
                  "payload": details.get("payload") or {k: v for k, v in details.items() if k not in {"message", "note", "branch"}}}
        with store.connection() as conn:
            conn.execute("INSERT INTO research_intervention_queue(id,project_id,intervention_type,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (queue_id, project_id, kind, json.dumps(queued, ensure_ascii=False), "QUEUED", now, now))
        result.update({"queueId": queue_id, "status": "QUEUED"})
        details["queueId"] = queue_id
    # The research-specific store owns the event table; a short-lived
    # transaction records all controls without retaining hidden client state.
    with store.connection() as conn:
        store._intervention(
            conn,
            project_id,
            project["currentVersion"],
            payload.kind,
            "researcher",
            details,
            datetime.now(UTC).isoformat(),
        )
    workbench = ResearchWorkbench(store)
    workbench.record_event(project_id, "human_intervention", "researcher", branch=branch, summary=payload.kind, details={**details, **result})
    workbench.sync_workspace(project_id)
    if payload.kind == "discuss":
        start_discussion(project_id, store, result["queueId"], details["message"], branch)
    elif result.get("status") == "QUEUED" and schedule_request_drain(project_id, store):
        # No further step will run on a finished project, so run the request now.
        result["status"] = "RUNNING_NOW"
    return result


@router.post("/projects/{project_id}/discussions/{queue_id}/cancel")
async def cancel_research_discussion(project_id: str, queue_id: str):
    store = _store()
    _require_project(project_id, store)
    if not await cancel_discussion(project_id, store, queue_id):
        raise HTTPException(status_code=409, detail="讨论不存在或已结束。")
    return {"cancelled": True, "queueId": queue_id}


@router.post("/projects/{project_id}/branches/{branch_name}/state")
async def update_research_branch_state(project_id: str, branch_name: str, payload: BranchStateUpdate):
    store = _store()
    _require_project(project_id, store)
    try:
        result = ResearchDomainStore(store).set_branch_state(project_id, branch_name, payload.status, payload.priority, payload.reason)
        workbench = ResearchWorkbench(store)
        workbench.record_decision(project_id, "branch_state", payload.reason or f"Set branch {branch_name} to {payload.status}", branch=branch_name)
        workbench.sync_workspace(project_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/branches")
async def list_research_branches(project_id: str):
    store = _store()
    _require_project(project_id, store)
    return {"items": ResearchDomainStore(store).list_branches(project_id)}


@router.post("/projects/{project_id}/branches", status_code=201)
async def create_research_branch(project_id: str, payload: BranchCreate):
    store = _store()
    _require_project(project_id, store)
    try:
        branch = ResearchDomainStore(store).create_branch(
            project_id, payload.name, parent_name=payload.parent_name, limits=payload.limits,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return branch


@router.put("/projects/{project_id}/branches/{branch_name}/budget")
async def update_research_branch_budget(project_id: str, branch_name: str, payload: BranchBudgetUpdate):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchDomainStore(store).update_branch_limits(project_id, branch_name, payload.limits)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/branches/compare")
async def compare_research_branches(project_id: str, left: str, right: str):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchDomainStore(store).compare_branches(project_id, left, right)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/graph")
async def get_research_graph(project_id: str, branch: str | None = None):
    store = _store()
    _require_project(project_id, store)
    return ResearchDomainStore(store).list_graph(project_id, branch)


@router.post("/projects/{project_id}/graph/nodes", status_code=201)
async def create_research_graph_node(project_id: str, payload: GraphNodeCreate):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchDomainStore(store).create_graph_node(
            project_id, payload.node_type, payload.title, payload.content, branch_name=payload.branch,
            artifact_id=payload.artifact_id, metadata=payload.metadata,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/graph/edges", status_code=201)
async def create_research_graph_edge(project_id: str, payload: GraphEdgeCreate):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchDomainStore(store).add_graph_edge(
            project_id, payload.source_id, payload.target_id, payload.edge_type,
            evidence=payload.evidence, metadata=payload.metadata,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/formal-bindings")
async def list_formal_bindings(project_id: str):
    store = _store()
    _require_project(project_id, store)
    return {"items": ResearchDomainStore(store).list_formal_bindings(project_id)}


@router.get("/projects/{project_id}/settings/lean")
async def get_lean_project_settings(project_id: str):
    store = _store()
    _require_project(project_id, store)
    return ResearchDomainStore(store).lean_settings(project_id)


@router.put("/projects/{project_id}/settings/lean")
async def update_lean_project_settings(project_id: str, payload: LeanProjectSettingsUpdate):
    store = _store()
    _require_project(project_id, store)
    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail="只有管理员可以更改 Lean 研究设置。")
    try:
        return ResearchDomainStore(store).save_lean_settings(
            project_id, confirm_before_check=payload.confirm_before_check,
            auto_call_enabled=payload.auto_call_enabled,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/formal-bindings", status_code=201)
async def create_formal_binding(project_id: str, payload: FormalBindingCreate):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchDomainStore(store).create_formal_binding(
            project_id, payload.claim_node_id, payload.original_statement, payload.formal_ir,
            payload.lean_statement, confirm_mapping=payload.confirm_mapping,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/formal-bindings/propose", status_code=201)
async def propose_formal_binding(project_id: str, payload: FormalBindingProposal):
    store = _store()
    project = _require_project(project_id, store)
    graph = ResearchDomainStore(store).list_graph(project_id)
    claim = next((node for node in graph["nodes"] if node["id"] == payload.claim_node_id), None)
    if not claim or claim["type"] not in {"conjecture", "lemma", "proposition", "theorem"}:
        raise HTTPException(status_code=422, detail="Choose a conjecture, lemma, proposition, or theorem graph node.")
    original = claim.get("content") or claim["title"]
    try:
        proposal = await ModelRouter().complete(
            project["modelId"],
            [
                {"role": "system", "content": (
                    "You are a mathematical formalization assistant. Return JSON with formalIr and leanStatement. "
                    "Preserve every quantifier and assumption. Use only standard Lean 4 / Mathlib expression syntax "
                    "in leanStatement. Do not include imports, theorem declarations, tactic proofs, sorry, axioms, or "
                    "claims of verification. Treat all ambiguous mappings as uncertain and explain the ambiguity in Formal IR."
                )},
                {"role": "user", "content": "Original graph claim:\n" + str(original) + "\n\nReturn an inspectable proposed Formal IR and one Lean proposition expression."},
            ],
            temperature=0.1,
            json_object=True,
        )
        fields = _extract_json(str(proposal.get("text") or ""))
        formal_ir = str(fields.get("formalIr") or "").strip()
        lean_statement = str(fields.get("leanStatement") or "").strip()
        if not formal_ir or not lean_statement or len(formal_ir) > 12000 or len(lean_statement) > 12000:
            raise ValueError("The model did not return a complete bounded formalization proposal.")
        saved = ResearchDomainStore(store).create_formal_binding(
            project_id, payload.claim_node_id, str(original), formal_ir, lean_statement,
            confirm_mapping=False, mapping_authority="AI_PROPOSED",
        )
        return saved
    except ModelRuntimeError as exc:
        raise _runtime_error(exc) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="The formalization proposal could not be safely saved.") from exc


@router.put("/projects/{project_id}/formal-bindings/{binding_id}/confirm")
async def confirm_formal_binding(project_id: str, binding_id: str, payload: FormalBindingConfirmation):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchDomainStore(store).confirm_formal_binding(
            project_id, binding_id, payload.original_statement, payload.formal_ir, payload.lean_statement,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/formal-bindings/{binding_id}/check")
async def check_formal_binding(project_id: str, binding_id: str, payload: FormalProofCheck):
    store = _store()
    project = _require_project(project_id, store)
    domain = ResearchDomainStore(store)
    binding = domain.get_formal_binding(project_id, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="Formal binding not found.")
    lean_settings = domain.lean_settings(project_id)
    if lean_settings["confirmBeforeCheck"] and binding["mappingAuthority"] != "USER_CONFIRMED":
        raise HTTPException(status_code=409, detail="Review and confirm the statement mapping before checking a proof.")
    if binding["mappingAuthority"] not in {"USER_CONFIRMED", "AI_PROPOSED", "UNCONFIRMED"}:
        raise HTTPException(status_code=409, detail="该形式化映射状态不允许进行 Lean 检查。")
    graph = domain.list_graph(project_id)
    claim = next((node for node in graph["nodes"] if node["id"] == binding["claimNodeId"]), None)
    if not claim:
        raise HTTPException(status_code=409, detail="The bound graph claim no longer exists.")
    branch_name = str(claim.get("branch") or "main")
    branch = domain.get_branch(project_id, branch_name)
    if not branch:
        raise HTTPException(status_code=409, detail="The claim's research branch is missing.")
    seconds_left = float(branch["limits"].get("toolSeconds", 0)) - float(branch["used"].get("toolSeconds", 0))
    if seconds_left <= 0:
        raise HTTPException(status_code=409, detail="This branch has no formal-check time remaining.")
    try:
        detect_lean_workspace()
    except LeanConfigurationError as exc:
        result = {"status": "LEAN_UNAVAILABLE", "passed": False, "exitCode": None,
                  "elapsedMs": 0, "leanVersion": "", "output": str(exc)}
        updated = domain.record_formal_check(project_id, binding_id, payload.proof_source, result)
        store.create_artifact(
            project_id, project["currentVersion"], "", "lean-kernel-check",
            "Lean dependency unavailable · " + binding["originalStatement"][:120], str(exc),
            status="lean-unavailable", branch_name=branch_name,
        )
        domain.record_tool_run(
            project_id, "lean_kernel_check",
            {"bindingId": binding_id, "proofHash": updated["proofSourceHash"], "leanStatementHash": binding["leanStatementHash"]},
            str(exc), "LEAN_UNAVAILABLE", branch_name=branch_name, elapsed_ms=0,
        )
        return {"binding": updated, "check": result}
    try:
        domain.consume_budget(project_id, branch_name, "proofAttempts", 1)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    started = time.monotonic()
    try:
        result = await run_kernel_check(
            project_id, store.aleggo_store.root, binding["leanStatement"], payload.proof_source,
            timeout_seconds=min(180.0, seconds_left),
        )
    except LeanConfigurationError as exc:
        result = {"status": "LEAN_UNAVAILABLE", "passed": False, "exitCode": None,
                  "elapsedMs": 0, "leanVersion": "", "output": str(exc)}
    except UnsafeLeanInput as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    elapsed_ms = int((time.monotonic() - started) * 1000)
    result["elapsedMs"] = max(int(result.get("elapsedMs") or 0), elapsed_ms)
    try:
        domain.consume_budget(project_id, branch_name, "toolSeconds", result["elapsedMs"] / 1000)
    except ValueError as exc:
        result.update({"passed": False, "status": "RESOURCE_BUDGET_EXCEEDED", "output": (str(result.get("output") or "") + "\n" + str(exc)).strip()})
    updated = domain.record_formal_check(project_id, binding_id, payload.proof_source, result)
    store.create_artifact(
        project_id, project["currentVersion"], "", "lean-kernel-check",
        "Lean kernel check · " + binding["originalStatement"][:120],
        "Status: " + str(result.get("status")) + "\nLean: " + str(result.get("leanVersion") or "unavailable") +
        "\nElapsed: " + str(result.get("elapsedMs", 0)) + " ms\n\n" + str(result.get("output") or "")[-12_000:],
        status=str(result.get("status") or "CHECK_FAILED").lower(), branch_name=branch_name,
    )
    domain.record_tool_run(
        project_id, "lean_kernel_check",
        {"bindingId": binding_id, "proofHash": updated["proofSourceHash"], "leanStatementHash": binding["leanStatementHash"]},
        str(result.get("output") or "")[-12_000:], str(result.get("status") or "CHECK_FAILED"),
        branch_name=branch_name, elapsed_ms=int(result.get("elapsedMs") or 0),
    )
    return {
        "binding": updated,
        "check": result,
        "mappingConfirmed": binding["mappingAuthority"] == "USER_CONFIRMED",
    }


@router.post("/projects/{project_id}/formal-bindings/{binding_id}/promote")
async def promote_formal_binding(project_id: str, binding_id: str):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchDomainStore(store).promote_formal_binding(project_id, binding_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


_SAFE_PROOF_SEARCH = "first\n  | rfl\n  | simp\n  | simpa\n  | norm_num\n  | omega\n  | linarith\n  | ring\n  | aesop"


@router.post("/projects/{project_id}/formal-bindings/{binding_id}/search")
async def search_formal_proof(project_id: str, binding_id: str):
    """Try a fixed, auditable tactic portfolio against the selected formal binding."""
    store = _store()
    project = _require_project(project_id, store)
    if project["approvedVersion"] is None or project["approvedVersion"] != project["currentVersion"]:
        raise HTTPException(status_code=409, detail="Approve the current research plan before starting proof search.")
    domain = ResearchDomainStore(store)
    binding = domain.get_formal_binding(project_id, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="Formal binding not found.")
    lean_settings = domain.lean_settings(project_id)
    if lean_settings["confirmBeforeCheck"] and binding["mappingAuthority"] != "USER_CONFIRMED":
        raise HTTPException(status_code=409, detail="Review and confirm the statement mapping before proof search.")
    if binding["mappingAuthority"] not in {"USER_CONFIRMED", "AI_PROPOSED", "UNCONFIRMED"}:
        raise HTTPException(status_code=409, detail="该形式化映射状态不允许进行 Lean 检查。")
    claim = next((node for node in domain.list_graph(project_id)["nodes"] if node["id"] == binding["claimNodeId"]), None)
    branch_name = str(claim.get("branch") or "main") if claim else "main"
    branch = domain.get_branch(project_id, branch_name)
    if not branch:
        raise HTTPException(status_code=409, detail="The claim's research branch is missing.")
    seconds_left = float(branch["limits"].get("toolSeconds", 0)) - float(branch["used"].get("toolSeconds", 0))
    if seconds_left <= 0:
        raise HTTPException(status_code=409, detail="This branch has no proof-search time remaining.")
    try:
        detect_lean_workspace()
        domain.consume_budget(project_id, branch_name, "proofAttempts", 8)
    except LeanConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    started = time.monotonic()
    try:
        result = await run_kernel_check(
            project_id, store.aleggo_store.root, binding["leanStatement"], _SAFE_PROOF_SEARCH,
            timeout_seconds=min(float(DEFAULT_TIMEOUT_SECONDS), seconds_left),
        )
    except LeanConfigurationError as exc:
        result = {"status": "LEAN_UNAVAILABLE", "passed": False, "exitCode": None,
                  "elapsedMs": 0, "leanVersion": "", "output": str(exc)}
    except (UnsafeLeanInput, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["elapsedMs"] = max(int(result.get("elapsedMs") or 0), int((time.monotonic() - started) * 1000))
    try:
        domain.consume_budget(project_id, branch_name, "toolSeconds", result["elapsedMs"] / 1000)
    except ValueError as exc:
        result.update({"passed": False, "status": "RESOURCE_BUDGET_EXCEEDED", "output": (str(result.get("output") or "") + "\n" + str(exc)).strip()})
    updated = domain.record_formal_check(project_id, binding_id, _SAFE_PROOF_SEARCH, result)
    store.create_artifact(
        project_id, project["currentVersion"], "", "lean-proof-search",
        "Bounded Lean proof search · " + binding["originalStatement"][:120],
        "Search portfolio: rfl, simp, simpa, norm_num, omega, linarith, ring, aesop\n"
        "The checker records a kernel certificate only when Lean exits successfully.\n"
        "Status: " + str(result.get("status")) + "\n" + str(result.get("output") or "")[-12000:],
        status=str(result.get("status") or "CHECK_FAILED").lower(), branch_name=branch_name,
    )
    domain.record_tool_run(
        project_id, "lean_bounded_proof_search",
        {"bindingId": binding_id, "searchPortfolio": ["rfl", "simp", "simpa", "norm_num", "omega", "linarith", "ring", "aesop"]},
        str(result.get("output") or "")[-12000:], str(result.get("status") or "CHECK_FAILED"),
        branch_name=branch_name, elapsed_ms=int(result.get("elapsedMs") or 0),
    )
    return {
        "binding": updated,
        "check": result,
        "searchPortfolio": 8,
        "mappingConfirmed": binding["mappingAuthority"] == "USER_CONFIRMED",
    }


def _require_current_approval(project: dict, label: str) -> None:
    if project["approvedVersion"] is None or project["approvedVersion"] != project["currentVersion"]:
        raise HTTPException(status_code=409, detail=f"Approve the current research plan before running {label}.")


@router.post("/projects/{project_id}/discovery-search")
async def run_finite_discovery(project_id: str, payload: FiniteDiscoverySearch):
    store = _store()
    project = _require_project(project_id, store)
    _require_current_approval(project, "finite discovery search")
    domain = ResearchDomainStore(store)
    branch = domain.get_branch(project_id, payload.branch)
    if not branch:
        raise HTTPException(status_code=404, detail="Research branch not found.")
    remaining = max(0, int(float(branch["limits"]["evaluations"]) - float(branch["used"]["evaluations"])))
    effective_budget = min(payload.evaluation_budget, remaining)
    if effective_budget < 1:
        raise HTTPException(status_code=409, detail="This branch has no evaluations remaining.")
    try:
        result = finite_discovery_search(payload.problem, seed=payload.seed, evaluation_budget=effective_budget)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        domain.consume_budget(project_id, payload.branch, "evaluations", result["evaluations"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    title = "Finite discovery " + result["status"].replace("_", " ").lower()
    best = result["bestCandidates"][0] if result["bestCandidates"] else None
    content = json.dumps(result, ensure_ascii=False, indent=2)
    artifact = store.create_artifact(
        project_id, project["currentVersion"], "discovery-search", "finite-discovery",
        title, content, status="bounded-search", branch_name=payload.branch,
    )
    node = domain.create_graph_node(
        project_id, "conjecture" if best else "evidence", title,
        "Best finite candidate: " + json.dumps(best, ensure_ascii=False) + "\n\n" + result["scope"] if best else result["scope"],
        branch_name=payload.branch, status="unverified", artifact_id=artifact["id"],
        metadata={"searchStatus": result["status"], "seed": result["seed"], "evaluations": result["evaluations"], "exhaustive": result["exhaustive"]},
    )
    domain.record_tool_run(
        project_id, "finite_discovery_search",
        {"problem": payload.problem, "seed": payload.seed, "evaluationBudget": effective_budget},
        content, result["status"], branch_name=payload.branch, elapsed_ms=0,
    )
    return {**result, "artifactId": artifact["id"], "graphNodeId": node["id"], "branch": payload.branch}


@router.post("/projects/{project_id}/counterexample-search")
async def run_integer_counterexample_search(project_id: str, payload: IntegerCounterexampleSearch):
    store = _store()
    project = _require_project(project_id, store)
    _require_current_approval(project, "counterexample search")
    domain = ResearchDomainStore(store)
    branch = domain.get_branch(project_id, payload.branch)
    if not branch:
        raise HTTPException(status_code=404, detail="Research branch not found.")
    if payload.end < payload.start:
        raise HTTPException(status_code=422, detail="The search end must be at least the start.")
    requested_cases = payload.end - payload.start + 1
    remaining = max(0, int(float(branch["limits"]["evaluations"]) - float(branch["used"]["evaluations"])))
    effective_cases = min(requested_cases, remaining, 100000)
    if effective_cases < 1:
        raise HTTPException(status_code=409, detail="This branch has no evaluations remaining.")
    effective_end = payload.start + effective_cases - 1
    try:
        result = integer_counterexample_search(
            payload.left_expression, payload.right_expression, variable=payload.variable,
            start=payload.start, end=effective_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        domain.consume_budget(project_id, payload.branch, "evaluations", result["casesChecked"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    title = "Integer counterexample search · " + result["status"].replace("_", " ").lower()
    content = json.dumps(result, ensure_ascii=False, indent=2)
    artifact = store.create_artifact(
        project_id, project["currentVersion"], "counterexample-search", "counterexample-search",
        title, content, status="bounded-search", branch_name=payload.branch,
    )
    node_type = "counterexample" if result["status"] == "COUNTEREXAMPLE_FOUND" else "evidence"
    node = domain.create_graph_node(
        project_id, node_type, title, content + "\n\n" + result["scope"], branch_name=payload.branch,
        status="unverified", artifact_id=artifact["id"],
        metadata={"searchStatus": result["status"], "casesChecked": result["casesChecked"], "searchRange": result["searchRange"]},
    )
    domain.record_tool_run(
        project_id, "integer_counterexample_search",
        {"leftExpression": payload.left_expression, "rightExpression": payload.right_expression,
         "variable": payload.variable, "searchRange": [payload.start, effective_end]},
        content, result["status"], branch_name=payload.branch, elapsed_ms=0,
    )
    return {**result, "artifactId": artifact["id"], "graphNodeId": node["id"], "branch": payload.branch,
            "requestedRange": [payload.start, payload.end]}


@router.get("/projects/{project_id}/sources")
async def list_research_sources(project_id: str):
    store = _store()
    _require_project(project_id, store)
    return {"items": ResearchDomainStore(store).list_sources(project_id)}


@router.put("/projects/{project_id}/sources/{source_id}")
async def curate_research_source(project_id: str, source_id: str, payload: LiteratureCuration):
    store = _store()
    _require_project(project_id, store)
    try:
        item = ResearchDomainStore(store).curate_source(project_id, source_id,
                                                       reading_status=payload.reading_status,
                                                       classification=payload.classification,
                                                       notes=payload.notes, important=payload.important)
        workbench = ResearchWorkbench(store)
        workbench.record_event(project_id, "literature_curated", "researcher", summary=f"Updated literature record {item['title']}.", details={"sourceId": source_id, "readingStatus": item["readingStatus"], "classification": item["classification"]})
        workbench.sync_workspace(project_id)
        return item
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/literature/search")
async def search_project_literature(project_id: str, payload: LiteratureSearch):
    store = _store()
    _require_project(project_id, store)
    try:
        from aleggo.study.research_literature_intelligence import LiteratureIntelligence
        result = await LiteratureIntelligence(store).search(project_id, [payload.query], limit=payload.limit)
        workbench = ResearchWorkbench(store)
        provider_warnings = result.get("warnings", {})
        warning_summary = "; ".join(f"{name}: {warning}" for name, warning in provider_warnings.items())
        status = result.get("availabilityStatus", "IMPLEMENTED BUT EXTERNAL SERVICE UNAVAILABLE")
        summary = f"Literature search {status}: {payload.query}; saved {len(result.get('items', []))} metadata records."
        if warning_summary:
            summary += " Provider status: " + warning_summary
        workbench.record_event(project_id, "literature_search", "researcher", summary=summary, details={"query": payload.query, "providers": result.get("providers", []), "providerStatuses": result.get("providerStatuses", {}), "warnings": provider_warnings, "availabilityStatus": status, "resultCount": len(result.get("items", []))})
        workbench.sync_workspace(project_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/literature/state")
async def get_literature_intelligence(project_id: str):
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    store = _store()
    _require_project(project_id, store)
    return LiteratureIntelligence(store).snapshot(project_id)


@router.get("/projects/{project_id}/literature/graph")
async def get_literature_graph(project_id: str):
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    store = _store()
    _require_project(project_id, store)
    return LiteratureIntelligence(store).graph(project_id)


@router.post("/projects/{project_id}/literature/analyze")
async def analyze_project_literature(project_id: str):
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    store = _store()
    _require_project(project_id, store)
    result = await LiteratureIntelligence(store).analyze(project_id)
    ResearchWorkbench(store).sync_workspace(project_id)
    return result


@router.get("/projects/{project_id}/sources/{source_id}/note")
async def get_literature_note(project_id: str, source_id: str):
    store = _store()
    _require_project(project_id, store)
    source = next((s for s in ResearchDomainStore(store).list_sources(project_id) if s["id"] == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail="Literature source not found.")
    return source.get("structuredNote") or {"sourceId": source_id, "content": {}, "status": "NOT_GENERATED"}


@router.post("/projects/{project_id}/sources/{source_id}/note")
async def generate_literature_note(project_id: str, source_id: str):
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    store = _store()
    _require_project(project_id, store)
    try:
        result = await LiteratureIntelligence(store).generate_note(project_id, source_id)
        ResearchWorkbench(store).sync_workspace(project_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/projects/{project_id}/sources/{source_id}/note")
async def update_literature_note(project_id: str, source_id: str, payload: LiteratureNoteUpdate):
    from aleggo.study.research_literature_intelligence import NOTE_FIELDS
    store = _store()
    _require_project(project_id, store)
    if set(payload.content) - set(NOTE_FIELDS) or any(not isinstance(v, str) or len(v) > 10000 for v in payload.content.values()):
        raise HTTPException(status_code=422, detail="Use the twelve literature note fields with text values up to 10000 characters.")
    note = {"sourceId": source_id, "content": {field: payload.content.get(field) or "UNKNOWN" for field in NOTE_FIELDS},
            "provenance": "RESEARCHER_EDITED", "status": "HUMAN_EDITED", "updatedAt": datetime.now(UTC).isoformat()}
    try:
        ResearchDomainStore(store).curate_source(project_id, source_id, structured_note=note)
        workbench = ResearchWorkbench(store)
        workbench.record_event(project_id, "literature_note_edited", "researcher", details={"sourceId": source_id})
        workbench.sync_workspace(project_id)
        return note
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/literature/doi")
async def import_literature_doi(project_id: str, payload: LiteratureDoiImport):
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    store = _store()
    _require_project(project_id, store)
    try:
        result = await LiteratureIntelligence(store).lookup_doi(project_id, payload.doi)
        ResearchWorkbench(store).sync_workspace(project_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"IMPLEMENTED BUT EXTERNAL SERVICE UNAVAILABLE: DOI lookup ({type(exc).__name__}).") from exc


@router.post("/projects/{project_id}/literature/import")
async def import_literature_library(project_id: str, request: Request, filename: str = "library.json"):
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    store = _store()
    _require_project(project_id, store)
    data = await request.body()
    if len(data) > 5_000_000:
        raise HTTPException(status_code=413, detail="文献库上限为 5 MB。")
    try:
        result = LiteratureIntelligence(store).import_library(project_id, data.decode("utf-8-sig"), format=Path(filename).suffix or "auto")
        ResearchWorkbench(store).sync_workspace(project_id)
        return result
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/documents", status_code=201)
async def add_research_document(project_id: str, request: Request, filename: str):
    store = _store()
    _require_project(project_id, store)
    if len(filename) > 255 or Path(filename).name != filename:
        raise HTTPException(status_code=422, detail="Choose a file with a valid name.")
    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="研究文档上限为 25 MB。")
    data = await request.body()
    try:
        source = import_research_document(ResearchDomainStore(store), project_id, filename, data)
        workbench = ResearchWorkbench(store)
        workbench.record_event(project_id, "local_document_indexed", "researcher", summary=f"Indexed local document {source['title']}.", details={"sourceId": source["id"], "chunkCount": source["chunkCount"]})
        workbench.sync_workspace(project_id)
        return source
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/literature/links", status_code=201)
async def create_literature_link(project_id: str, payload: LiteratureLinkCreate):
    store = _store()
    _require_project(project_id, store)
    try:
        link = ResearchDomainStore(store).link_literature(project_id, payload.source_id, payload.entity_type,
                                                          payload.entity_id, payload.relationship, payload.notes)
        workbench = ResearchWorkbench(store)
        workbench.record_event(project_id, "literature_linked", "researcher", summary="Linked literature to a research claim or branch.", details=link)
        workbench.sync_workspace(project_id)
        return link
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/sources/search")
async def search_research_sources(project_id: str, q: str, limit: int = 20):
    store = _store()
    _require_project(project_id, store)
    return {"items": ResearchDomainStore(store).search_sources(project_id, q, limit)}


@router.get("/projects/{project_id}/tool-runs")
async def list_research_tool_runs(project_id: str, limit: int = 100):
    store = _store()
    _require_project(project_id, store)
    return {"items": ResearchDomainStore(store).list_tool_runs(project_id, limit)}


@router.get("/projects/{project_id}/thread")
async def research_thread(project_id: str, limit: int = 40, before: str = "", after: str = ""):
    """Paged, newest-first conversation feed (messages, real tool runs, step events, results).

    ``before`` loads older history on demand; ``after`` returns only new rows for cheap polling.
    """
    store = _store()
    project = _require_project(project_id, store)
    # Requests left queued on a finished project (for example by an earlier
    # release, or by a restart) would otherwise wait forever for a next step.
    schedule_request_drain(project_id, store)
    # Discussion replies interrupted by a restart are answered again.
    resume_pending_discussions(project_id, store)
    return build_thread_page(store, project, limit=limit, before=before, after=after)


@router.get("/projects/{project_id}/memories")
async def list_research_memories(project_id: str):
    store = _store()
    _require_project(project_id, store)
    return {"items": ResearchDomainStore(store).list_memories(project_id)}


@router.post("/projects/{project_id}/memories", status_code=201)
async def add_research_memory(project_id: str, payload: MemoryCreate):
    store = _store()
    _require_project(project_id, store)
    try:
        return ResearchDomainStore(store).add_memory(
            None if payload.scope == "workspace" else project_id, payload.content, confirmed=payload.confirmed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/projects/{project_id}/memories/{memory_id}")
async def confirm_research_memory(project_id: str, memory_id: str, payload: MemoryConfirmation):
    store = _store()
    _require_project(project_id, store)
    if not ResearchDomainStore(store).confirm_memory(project_id, memory_id, payload.confirmed):
        raise HTTPException(status_code=404, detail="Research memory not found.")
    return {"id": memory_id, "confirmed": payload.confirmed}


@router.get("/projects/{project_id}/legacy-records")
async def list_imported_mra_records(project_id: str, collection: str | None = None, limit: int = 100):
    store = _store()
    _require_project(project_id, store)
    return {"items": ResearchDomainStore(store).list_legacy_records(project_id, collection, limit)}


@router.get("/imports")
async def list_research_imports():
    return {"items": ResearchDomainStore(_store()).list_imports()}


@router.post("/imports/mra-existing")
async def import_existing_mra_database():
    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail="Only an administrator can import the local Math Research Agent workspace.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise HTTPException(status_code=404, detail="The local Math Research Agent database was not found.")
    source = Path(appdata) / "math-research-agent" / "research.sqlite3"
    if not source.is_file():
        raise HTTPException(status_code=404, detail="The local Math Research Agent database was not found.")
    try:
        return import_mra_database(_store(), source, source.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Math Research Agent database import failed; source was left unchanged.") from exc


@router.post("/imports/mra-database")
async def import_mra_database_route(request: Request, filename: str = "research.sqlite3"):
    if len(filename) > 255 or Path(filename).name != filename:
        raise HTTPException(status_code=422, detail="Choose a valid SQLite database file.")
    content_length = int(request.headers.get("content-length", "0") or 0)
    if content_length > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Math Research Agent database import is limited to 512 MB.")
    data = await request.body()
    if not data or len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Choose a non-empty database smaller than 512 MB.")
    suffix = Path(filename).suffix.casefold()
    if suffix not in {".sqlite", ".sqlite3", ".db"}:
        raise HTTPException(status_code=422, detail="Choose a .sqlite, .sqlite3, or .db database.")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="aleggo-mra-upload-", suffix=suffix, delete=False) as temp_file:
            temp_file.write(data)
            temp_path = Path(temp_file.name)
        result = import_mra_database(_store(), temp_path, filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Math Research Agent database import failed; source was left unchanged.") from exc
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
    return result


@router.post("/projects/{project_id}/artifacts/{artifact_id}/feedback")
async def provide_artifact_feedback(project_id: str, artifact_id: str, payload: ArtifactFeedback):
    store = _store()
    _require_project(project_id, store)
    if not ResearchDomainStore(store).update_artifact_feedback(project_id, artifact_id, payload.feedback):
        raise HTTPException(status_code=404, detail="Research artifact not found.")
    workbench = ResearchWorkbench(store)
    workbench.record_decision(project_id, "artifact_" + payload.feedback, f"Researcher marked artifact {artifact_id} {payload.feedback}.", artifact_id=artifact_id)
    workbench.sync_workspace(project_id)
    return {"artifactId": artifact_id, "feedback": payload.feedback}


@router.get("/projects/{project_id}/export")
async def export_research_report(project_id: str, format: Literal["markdown", "pdf", "latex"] = "markdown"):
    store = _store()
    project = _require_project(project_id, store)
    safe_title = re.sub(r"[^\w\u3400-\u9fff.-]+", "-", project["title"]).strip(".-")[:80] or "research-report"
    if format == "latex":
        from io import BytesIO
        import zipfile

        from aleggo.study.research_delivery import ResearchDelivery
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in ResearchDelivery(store).paper_files(project_id).items():
                archive.writestr("paper/" + name, content)
        disposition = f"attachment; filename=research-paper-latex.zip; filename*=UTF-8''{quote(safe_title)}-latex.zip"
        return Response(content=buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": disposition})
    markdown = build_markdown_report(store, project_id)
    if format == "pdf":
        try:
            body = build_pdf_report(markdown, project["title"])
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        extension, media_type = "pdf", "application/pdf"
    else:
        body, extension, media_type = markdown.encode("utf-8"), "md", "text/markdown; charset=utf-8"
    disposition = f"attachment; filename=research-report.{extension}; filename*=UTF-8''{quote(safe_title)}.{extension}"
    return Response(content=body, media_type=media_type, headers={"Content-Disposition": disposition})


# Lean onboarding, computer algebra systems, mathematical databases, imports,
# proof review, long-term memory, collaboration, usage, evaluation, and self-improvement.
from aleggo.api.routers.research_extensions import router as _extensions_router  # noqa: E402
from aleggo.api.routers.self_improvement import router as _self_improvement_router  # noqa: E402

router.include_router(_extensions_router)
router.include_router(_self_improvement_router)
