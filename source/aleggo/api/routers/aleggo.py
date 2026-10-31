"""A-leggo Next API: evidence loop, methods, identity, skills, and migration."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from aleggo.study import connection as agent_link
from aleggo.study import pairing as phone_pair
from aleggo.study.models import (
    CompanionCreate,
    CompanionMemoryCreate,
    DiagnosisPrepareRequest,
    EvidenceCreate,
    LegacyImportRequest,
    MethodCardCreate,
    NoteCreate,
    ReviewGrade,
    SkillRegistration,
    SkillRunCreate,
    StyleCreate,
)
from aleggo.study.prompts import FIRST_ERROR_POLICY_ZH, diagnosis_user_message
from aleggo.study.service import get_aleggo_service
from aleggo.services.llm.config import clear_llm_config_cache
from aleggo.services.llm.provider_factory import reset_runtime_provider_pool
from aleggo.services.session import get_session_store, make_imported_session_id

router = APIRouter()


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ReviewGradeRequest(ReviewGrade):
    course_id: str = Field(default="", max_length=200)


@router.get("/agent-connection")
async def get_agent_connection():
    service = get_aleggo_service()
    return agent_link.load_connection(service).public_view()


@router.post("/agent-connection")
async def update_agent_connection(payload: agent_link.AgentConnectionUpdate):
    service = get_aleggo_service()
    current = agent_link.load_connection(service)
    saved = agent_link.save_connection(service, payload, keep_key=current.api_key)
    # The connection doubles as the no-catalog-model LLM fallback; keep the
    # cached LLM config and pooled providers aligned with the new key/endpoint.
    clear_llm_config_cache()
    reset_runtime_provider_pool()
    return saved.public_view()


@router.post("/agent-connection/test")
async def test_agent_connection():
    connection = agent_link.load_connection(get_aleggo_service())
    return await agent_link.probe(connection)


@router.post("/agent/completion")
async def agent_completion(payload: agent_link.AgentCompletionRequest):
    connection = agent_link.load_connection(get_aleggo_service())
    if not connection.enabled:
        raise HTTPException(status_code=409, detail="Agent 连接已停用，请在设置中启用")
    if not connection.ready:
        raise HTTPException(status_code=409, detail="Agent 未配置：请先填写应用 ID 与 API Key")
    try:
        return await agent_link.complete(
            connection,
            payload.prompt,
            session_id=payload.session_id,
            images=payload.images,
            pipeline_ids=payload.pipeline_ids,
        )
    except agent_link.AgentNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except agent_link.AgentCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/overview")
async def overview():
    return get_aleggo_service().overview()


@router.post("/evidence", status_code=201)
async def add_evidence(payload: EvidenceCreate):
    return get_aleggo_service().add_evidence(payload)


@router.get("/evidence")
async def list_evidence(concept_id: str = "", limit: int = Query(default=200, ge=1, le=2000)):
    return {"items": get_aleggo_service().list_evidence(concept_id, limit)}


@router.get("/mastery")
async def mastery(concept_id: str = "", course_id: str = ""):
    service = get_aleggo_service()
    if concept_id:
        return service.mastery(concept_id)
    return {"items": service.mastery_map(course_id)}


@router.get("/weak-points")
async def weak_points(course_id: str = ""):
    return {"items": get_aleggo_service().weak_points(course_id)}


@router.get("/reviews/due")
async def due_reviews(course_id: str = ""):
    return {"items": get_aleggo_service().due_reviews(course_id)}


@router.post("/reviews/{concept_id}/grade")
async def grade_review(concept_id: str, payload: ReviewGradeRequest):
    grade = ReviewGrade.model_validate(payload.model_dump(exclude={"course_id"}))
    return get_aleggo_service().grade_review(concept_id, payload.course_id, grade)


@router.post("/method-cards", status_code=201)
async def create_method_card(payload: MethodCardCreate):
    return get_aleggo_service().create_method_card(payload)


@router.get("/method-cards")
async def list_method_cards(include_candidates: bool = True):
    return {"items": get_aleggo_service().list_method_cards(include_candidates)}


@router.post("/method-cards/{card_id}/confirm")
async def confirm_method_card(card_id: str):
    try:
        return get_aleggo_service().confirm_method_card(card_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="method card not found") from exc


@router.post("/notes", status_code=201)
async def create_note(payload: NoteCreate):
    return get_aleggo_service().create_note(payload)


@router.get("/notes")
async def list_notes():
    return {"items": get_aleggo_service().list_notes()}


@router.post("/styles", status_code=201)
async def create_style(payload: StyleCreate):
    return get_aleggo_service().create_style(payload)


@router.post("/companions", status_code=201)
async def create_companion(payload: CompanionCreate):
    return get_aleggo_service().create_companion(payload)


@router.get("/identity")
async def identity():
    return get_aleggo_service().list_identity()


@router.post("/companion-memories", status_code=201)
async def add_companion_memory(payload: CompanionMemoryCreate):
    try:
        return get_aleggo_service().add_companion_memory(payload)
    except Exception as exc:
        if "FOREIGN KEY" in str(exc).upper():
            raise HTTPException(status_code=404, detail="companion not found") from exc
        raise


@router.post("/companion-memories/{memory_id}/confirm")
async def confirm_companion_memory(memory_id: str):
    try:
        return get_aleggo_service().confirm_companion_memory(memory_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="memory not found") from exc


@router.delete("/companion-memories/{memory_id}")
async def delete_companion_memory(memory_id: str):
    if not get_aleggo_service().delete_companion_memory(memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"deleted": memory_id}


@router.post("/skills", status_code=201)
async def register_skill(payload: SkillRegistration):
    return get_aleggo_service().register_skill(payload)


@router.get("/skills")
async def list_skills():
    return {"items": get_aleggo_service().list_skills()}


@router.put("/skills/{name}/enabled")
async def set_skill_enabled(name: str, payload: EnabledRequest):
    try:
        return get_aleggo_service().set_skill_enabled(name, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc


@router.post("/skills/{name}/runs", status_code=201)
async def record_skill_run(name: str, payload: SkillRunCreate):
    try:
        return get_aleggo_service().record_skill_run(name, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc


@router.post("/migration/import-v3")
async def import_v3(payload: LegacyImportRequest):
    try:
        result = get_aleggo_service().import_legacy(payload.workspace, payload.source_name)
        result["counts"]["chats"] = await _import_legacy_chats(payload.workspace)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _import_legacy_chats(workspace: dict) -> int:
    """Move V3 conversations into A-leggo's real, searchable session store."""
    store = get_session_store()
    imported = 0
    for index, raw in enumerate(workspace.get("chats") or []):
        if not isinstance(raw, dict):
            continue
        external_id = str(raw.get("id") or f"chat-{index}")
        session_id = make_imported_session_id("aleggo_v3", external_id)
        messages = []
        for message in raw.get("messages") or []:
            if not isinstance(message, dict):
                continue
            messages.append(
                {
                    "role": "assistant" if message.get("role") == "assistant" else "user",
                    "content": str(message.get("content") or ""),
                    "created_at": float(message.get("ts") or raw.get("updatedAt") or time.time() * 1000)
                    / 1000,
                    "metadata": {
                        "legacy_message_id": str(message.get("id") or ""),
                        "legacy_attachments": message.get("attachments") or [],
                    },
                }
            )
        stamp = float(raw.get("updatedAt") or time.time() * 1000) / 1000
        outcome = await store.import_session(
            session_id,
            str(raw.get("title") or "A-leggo V3 对话"),
            stamp,
            stamp,
            {
                "import": {"source": "aleggo_v3", "external_id": external_id},
                "capability": str(raw.get("ability") or "aleggo_tutor"),
                "legacy_knowledge_point_ids": raw.get("knowledgePointIds") or [],
                "legacy_style_id": str(raw.get("responseStyleId") or ""),
                "legacy_companion_id": str(raw.get("companionId") or ""),
            },
            messages,
        )
        if outcome.get("imported"):
            imported += 1
    return imported


@router.get("/backup")
async def export_backup():
    return get_aleggo_service().export_backup()


@router.post("/diagnosis/prepare")
async def prepare_diagnosis(payload: DiagnosisPrepareRequest):
    """Prepare a turn for the real first-error capability.

    The returned prompt is sent through A-leggo's ordinary session/WebSocket
    runtime by the UI; this route neither fabricates an answer nor bypasses tool
    calling, RAG, attachment parsing, streaming, or recovery.
    """

    return {
        "capability": "first_error_diagnosis",
        "user_message": diagnosis_user_message(
            payload.problem,
            payload.student_work,
            payload.source_ids,
        ),
        "policy": FIRST_ERROR_POLICY_ZH,
        "course_id": payload.course_id,
        "concept_id": payload.concept_id,
    }


class PairVerifyRequest(BaseModel):
    token: str = Field(min_length=8)


@router.get("/pair/info")
async def pair_info():
    """Address + token bundle rendered as the workbench QR code."""

    return phone_pair.pairing_info()


@router.get("/pair/qr.svg", response_class=Response)
async def pair_qr_svg():
    svg = phone_pair.qr_svg(phone_pair.qr_payload())
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@router.post("/pair/verify")
async def pair_verify(payload: PairVerifyRequest):
    if not phone_pair.verify(payload.token):
        raise HTTPException(status_code=403, detail="pairing token mismatch")
    info = phone_pair.pairing_info()
    return {"ok": True, "name": info["name"], "host": info["host"],
            "port": info["port"]}


@router.post("/pair/rotate")
async def pair_rotate():
    return phone_pair.rotate()
