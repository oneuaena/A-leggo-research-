"""Research-side endpoints added on top of :mod:`aleggo.api.routers.research`.

Included into the research router, so every route here shares its auth and
shared-project binding (``_store()`` resolves to the owner's store for a
project shared with the current account).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from aleggo.multi_user.context import get_current_user
from aleggo.study import research_sharing
from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_store import ResearchStore
from aleggo.study.research_workbench import ResearchWorkbench

router = APIRouter()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _store() -> ResearchStore:
    from aleggo.api.routers import research  # late import: research includes this router

    return research._store()


def _project(project_id: str, store: ResearchStore) -> dict[str, Any]:
    """Load a project and refuse sibling-project access under a shared binding.

    When the request is bound to a shared project, the store is the *owner's*
    store; without this check a guessed sibling ``project_id`` on that store
    would otherwise be readable/writable by the sharee. Also fail closed if the
    share was revoked after the request binding was set.
    """
    try:
        research_sharing.revalidate_share(project_id)
        research_sharing.assert_bound_project(project_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="未找到该研究项目。")
    return project


def _admin() -> None:
    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可修改此设置。")


def _owner(project_id: str) -> None:
    if research_sharing.current_role(project_id) != "owner":
        raise HTTPException(status_code=403, detail="仅项目所有者可管理成员。")


async def _body(request: Request, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read the request body with a hard size cap (content-length and stream)."""
    try:
        declared = int(request.headers.get("content-length", "0") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > limit:
        raise HTTPException(status_code=413, detail=f"上传上限为 {limit // (1024 * 1024)} MB。")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=f"上传上限为 {limit // (1024 * 1024)} MB。")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=422, detail="上传内容为空。")
    return data


def _filename(filename: str) -> str:
    name = Path(str(filename or "")).name
    if not name or len(name) > 255:
        raise HTTPException(status_code=422, detail="请选择带有效文件名的文件。")
    return name


def _record_vision(store: ResearchStore, project_id: str, usage: dict[str, Any], model: str, purpose: str) -> None:
    if not usage:
        return
    ResearchDomainStore(store).record_tool_run(
        project_id, "llm:vision-transcriber", {"purpose": purpose, "model": model}, purpose, "COMPLETED",
        step_id="import", metadata={"usage": {**usage, "modelId": model, "actualModel": model}, "toolVersion": "vision"})


# --------------------------------------------------------------------------- #
# Lean onboarding and AI proving
# --------------------------------------------------------------------------- #
class AutoProveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    max_attempts: int = Field(default=4, ge=1, le=6, alias="maxAttempts")
    hint: str = Field(default="", max_length=2000)


@router.get("/lean/status")
async def lean_status():
    from aleggo.study import lean_setup

    return lean_setup.lean_status()


@router.post("/lean/install", status_code=202)
async def lean_install():
    from aleggo.study import lean_setup

    _admin()
    return lean_setup.start_install()


@router.post("/lean/install/cancel")
async def lean_install_cancel():
    from aleggo.study import lean_setup

    _admin()
    return lean_setup.cancel_install()


@router.post("/lean/install/pause", status_code=202)
async def lean_install_pause():
    """Pause a running bundle download, or resume a paused one."""
    from aleggo.study import lean_setup

    _admin()
    return lean_setup.pause_install()


@router.get("/lean/updates")
async def lean_updates():
    """Compare the installed bundle version with the release manifest."""
    from aleggo.study import lean_setup

    return lean_setup.check_updates()


@router.post("/lean/upgrade", status_code=202)
async def lean_upgrade():
    """Start a bundle upgrade job (same flow as install, newer version)."""
    from aleggo.study import lean_setup

    _admin()
    return lean_setup.start_upgrade()


@router.post("/projects/{project_id}/formal-bindings/{binding_id}/auto-prove", status_code=202)
async def start_auto_prove(project_id: str, binding_id: str, payload: AutoProveRequest):
    from aleggo.study import lean_autoprove
    from aleggo.study.lean_formal import LeanConfigurationError

    store = _store()
    _project(project_id, store)
    try:
        return lean_autoprove.start(project_id, binding_id, store, max_attempts=payload.max_attempts, hint=payload.hint)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except LeanConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "already running" in str(exc) else 422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/formal-bindings/{binding_id}/auto-prove")
async def auto_prove_status(project_id: str, binding_id: str):
    from aleggo.study import lean_autoprove

    store = _store()
    _project(project_id, store)
    return lean_autoprove.status(project_id, binding_id, store)


# --------------------------------------------------------------------------- #
# Computer algebra systems and vision model settings
# --------------------------------------------------------------------------- #
class CasConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: dict[str, str] = Field(default_factory=dict)


class VisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(default="", max_length=120)


def _cas_status() -> dict[str, Any]:
    from aleggo.study import external_cas

    return {"engines": external_cas.engine_status(), "isolation": external_cas.isolation_level(),
            "config": external_cas.load_config()}


@router.get("/cas/status")
async def cas_status():
    return _cas_status()


@router.put("/cas/config")
async def cas_config(payload: CasConfig):
    from aleggo.study import external_cas

    _admin()
    try:
        external_cas.save_config(payload.paths)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _cas_status()


@router.get("/vision/config")
async def vision_config():
    from aleggo.study.model_registry import vision_model_setting

    return vision_model_setting()


@router.put("/vision/config")
async def save_vision_config(payload: VisionConfig):
    from aleggo.study.model_registry import save_vision_model_setting

    _admin()
    try:
        return save_vision_model_setting(payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Mathematical databases
# --------------------------------------------------------------------------- #
class DatabaseQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tool: Literal["oeis_search", "zbmath_search", "arxiv_search", "lmfdb_search"]
    input: dict[str, Any] = Field(default_factory=dict)
    save_sources: bool = Field(default=False, alias="saveSources")


@router.post("/projects/{project_id}/database-query")
async def database_query(project_id: str, payload: DatabaseQuery):
    from aleggo.study.math_databases import database_sources, run_database_tool

    store = _store()
    _project(project_id, store)
    started = time.monotonic()
    try:
        result = await run_database_tool(payload.tool, payload.input)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    domain = ResearchDomainStore(store)
    status = str(result.get("verification_status") or ("SUCCESS" if result.get("ok") else "TOOL_FAILURE"))
    domain.record_tool_run(project_id, payload.tool, payload.input, result.get("output") or result.get("error") or "", status,
                           step_id="database-query", elapsed_ms=int((time.monotonic() - started) * 1000),
                           metadata={"source": "researcher-database-query", "toolFamily": "database",
                                     "environment": result.get("environment", {})})
    try:
        data = json.loads(result.get("output") or "{}") if result.get("ok") else {}
    except ValueError:
        data = {}
    sources = database_sources(payload.tool, result)
    saved = []
    if payload.save_sources:
        for item in sources[:20]:
            if not item.get("title"):
                continue
            saved.append(domain.save_source(project_id, {
                "type": "literature", "title": item["title"], "url": item.get("url", ""), "authors": item.get("authors", ""),
                "year": item.get("year", ""), "doi": item.get("doi", ""), "provider": item.get("provider") or payload.tool,
                "branch": "main", "metadata": {"database": payload.tool, "query": payload.input},
            })["id"])
        if saved:
            ResearchWorkbench(store).sync_workspace(project_id)
    return {"ok": bool(result.get("ok")), "tool": payload.tool, "data": data, "error": result.get("error", ""),
            "errorType": result.get("error_type", ""), "sources": sources, "savedSourceIds": saved,
            "environment": result.get("environment", {})}


# --------------------------------------------------------------------------- #
# Imports: LaTeX sources, formula PDFs, handwritten notes
# --------------------------------------------------------------------------- #
class ImportPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statements: list[int] = Field(default_factory=list, max_length=200)
    references: list[int] = Field(default_factory=list, max_length=500)
    branch: str = Field(default="main", max_length=120)


class HandwritingImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    data: str = Field(min_length=1, max_length=22_000_000)


class HandwritingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    images: list[HandwritingImage] = Field(min_length=1, max_length=24)
    hint: str = Field(default="", max_length=800)
    style: Literal["handwriting", "whiteboard", "board"] = "handwriting"


class TranscriptionSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=1_000_000)
    model: str = Field(default="", max_length=120)
    originals: list[str] = Field(default_factory=list, max_length=24)
    reviewed: bool = Field(default=False, description="研究者是否已校对转写草稿")


def _imported(store: ResearchStore, project_id: str, summary: str, details: dict[str, Any]) -> None:
    workbench = ResearchWorkbench(store)
    workbench.record_event(project_id, "local_document_indexed", "researcher", summary=summary, details=details)
    workbench.sync_workspace(project_id)


@router.post("/projects/{project_id}/imports/latex", status_code=201)
async def import_latex_source(project_id: str, request: Request, filename: str):
    from aleggo.study.research_imports import import_latex

    store = _store()
    _project(project_id, store)
    name = _filename(filename)
    data = await _body(request)
    try:
        result = import_latex(ResearchDomainStore(store), project_id, name, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _imported(store, project_id, f"Imported LaTeX source {result['source']['title']}.",
              {"sourceId": result["source"]["id"], "statements": len(result["statements"]), "references": len(result["references"])})
    return result


@router.post("/projects/{project_id}/imports/pdf", status_code=201)
async def import_pdf_source(
    project_id: str, request: Request, filename: str,
    mode: Literal["auto", "text", "vision"] = "auto",
    maxVisionPages: int | None = None,  # noqa: N803
    pageOffset: int = 0,  # noqa: N803
    continueSourceId: str = "",  # noqa: N803
):
    from aleggo.study.model_registry import ModelRuntimeError
    from aleggo.study.research_imports import import_pdf

    store = _store()
    _project(project_id, store)
    name = _filename(filename)
    data = await _body(request)
    try:
        result = await import_pdf(
            ResearchDomainStore(store), project_id, name, data, mode=mode,
            max_vision_pages=maxVisionPages, page_offset=pageOffset,
            continue_source_id=continueSourceId.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _record_vision(store, project_id, result.get("usage") or {}, str(result.get("visionModel") or ""), "pdf:" + name)
    source = result.get("source") or {}
    remaining = int(result.get("moreVisionPagesRemaining") or 0)
    details = {"sourceId": source.get("id"), "mode": mode, "transcribed": result.get("transcribedPages"),
               "remaining": remaining, "nextPageOffset": result.get("nextPageOffset"),
               "visionPageCap": result.get("visionPageCap"), "filename": name,
               "pages": result.get("pages"), "continued": bool(result.get("continued"))}
    _imported(store, project_id, f"Imported PDF {source.get('title', name)}.", details)
    if remaining > 0 and source.get("id"):
        ResearchWorkbench(store).record_event(
            project_id, "pdf_vision_continue_pending", "researcher",
            summary=f"PDF「{source.get('title', name)}」仍有 {remaining} 页待视觉转写。",
            details={**details, "resumeHint": {
                "sourceId": source["id"], "pageOffset": result.get("nextPageOffset"),
                "maxVisionPages": result.get("visionPageCap"), "filename": name, "mode": mode,
            }},
        )
    elif source.get("id"):
        ResearchWorkbench(store).record_event(
            project_id, "pdf_vision_continue_cleared", "researcher",
            summary=f"PDF「{source.get('title', name)}」视觉转写批次完成。",
            details={"sourceId": source.get("id")},
        )
    return result


@router.post("/projects/{project_id}/imports/{source_id}/promote")
async def promote_imported_source(project_id: str, source_id: str, payload: ImportPromotion):
    from aleggo.study.research_imports import promote_import

    store = _store()
    _project(project_id, store)
    try:
        result = promote_import(ResearchDomainStore(store), project_id, source_id, statements=payload.statements,
                                references=payload.references, branch=payload.branch)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ResearchWorkbench(store).sync_workspace(project_id)
    return result


@router.post("/projects/{project_id}/imports/handwriting")
async def transcribe_handwriting(project_id: str, request: Request):
    """Photos → editable Markdown/LaTeX draft.  Accepts multipart ``files`` or JSON base64 images."""
    from aleggo.study.model_registry import ModelRuntimeError
    from aleggo.study.research_imports import MAX_IMAGE_BYTES, MAX_IMAGES, transcribe_images

    from aleggo.study.research_imports import image_limit

    store = _store()
    _project(project_id, store)
    files: list[tuple[str, bytes]] = []
    hint = ""
    style = "handwriting"
    cap = image_limit()
    if request.headers.get("content-type", "").startswith("multipart/"):
        form = await request.form()
        hint = str(form.get("hint") or "")[:800]
        style = str(form.get("style") or "handwriting")[:40]
        for item in form.getlist("files")[:cap + 1]:
            if hasattr(item, "read"):
                files.append((_filename(getattr(item, "filename", "") or "image.png"), await item.read(MAX_IMAGE_BYTES + 1)))
    else:
        try:
            payload = HandwritingRequest.model_validate_json(await _body(request, 24 * 22_000_000))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="请以 multipart 文件或 base64 JSON 发送图片。") from exc
        hint = payload.hint
        style = payload.style
        for image in payload.images[:cap]:
            raw = image.data.split(",", 1)[1] if image.data.startswith("data:") else image.data
            try:
                files.append((_filename(image.name), base64.b64decode(raw, validate=True)))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="图片不是有效的 base64。") from exc
    try:
        result = await transcribe_images(files, hint=hint, style=style)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _record_vision(store, project_id, result.get("usage") or {}, str(result.get("model") or ""), "handwriting")
    return result


@router.post("/projects/{project_id}/imports/handwriting/save", status_code=201)
async def save_handwriting(project_id: str, payload: TranscriptionSave):
    from aleggo.study.research_imports import save_transcription

    store = _store()
    _project(project_id, store)
    try:
        source = save_transcription(ResearchDomainStore(store), project_id, payload.title, payload.content,
                                    model=payload.model, originals=payload.originals, reviewed=payload.reviewed)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _imported(store, project_id, f"Saved handwritten notes {source.get('title', '')}.", {"sourceId": source.get("id")})
    return source


class ImportSuggestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(default="", max_length=2000)


class ChatAttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    filename: str = Field(min_length=1, max_length=255)
    # base64 payload for small text/markdown/tex attachments dragged into chat
    data: str = Field(min_length=1, max_length=22_000_000)
    question: str = Field(default="", max_length=2000)


class EmailAttachmentImport(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(min_length=1, max_length=34_000_000)  # base64 of up to ~25MB
    message_id: str = Field(default="", max_length=200, alias="messageId")
    subject: str = Field(default="", max_length=300)
    from_address: str = Field(default="", max_length=200, alias="from")
    question: str = Field(default="", max_length=2000)
    mode: Literal["auto", "text", "vision"] = "auto"
    max_vision_pages: int | None = Field(default=None, alias="maxVisionPages")


@router.get("/projects/{project_id}/imports/{source_id}/suggest")
async def suggest_from_import(project_id: str, source_id: str, question: str = ""):
    """After import: related literature, open claims, computable examples aligned to the project question."""
    from aleggo.study.research_imports import suggest_after_import

    store = _store()
    _project(project_id, store)
    try:
        return suggest_after_import(ResearchDomainStore(store), project_id, source_id, question=question[:2000])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@router.post("/projects/{project_id}/imports/{source_id}/suggest")
async def suggest_from_import_post(project_id: str, source_id: str, payload: ImportSuggestRequest):
    from aleggo.study.research_imports import suggest_after_import

    store = _store()
    _project(project_id, store)
    try:
        return suggest_after_import(ResearchDomainStore(store), project_id, source_id, question=payload.question)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@router.post("/projects/{project_id}/imports/chat-attach", status_code=201)
async def chat_attach_import(project_id: str, payload: ChatAttachRequest):
    """Drag-into-chat helper: index a small attachment and align it to the research question."""
    from aleggo.study.research_imports import align_attachment_to_project

    store = _store()
    project = _project(project_id, store)
    raw = payload.data.split(",", 1)[1] if payload.data.startswith("data:") else payload.data
    try:
        data = base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="附件不是有效的 base64。") from exc
    question = payload.question or str(project.get("question") or "")
    try:
        result = align_attachment_to_project(ResearchDomainStore(store), project_id, _filename(payload.filename),
                                             data, question=question)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = (result.get("import") or {}).get("source") or result.get("source") or {}
    if source.get("id"):
        _imported(store, project_id, f"Chat attachment {source.get('title', payload.filename)} indexed.",
                  {"sourceId": source.get("id"), "kind": result.get("kind")})
    return result


@router.post("/projects/{project_id}/imports/email-attachment", status_code=201)
async def import_from_email_attachment(project_id: str, payload: EmailAttachmentImport):
    """One-click import of a mail attachment (bytes supplied by the Gmail/mail connector UI)."""
    from aleggo.study.model_registry import ModelRuntimeError
    from aleggo.study.research_imports import import_email_attachment, import_email_pdf, import_kind

    store = _store()
    project = _project(project_id, store)
    raw = payload.data.split(",", 1)[1] if payload.data.startswith("data:") else payload.data
    try:
        data = base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="附件不是有效的 base64。") from exc
    name = _filename(payload.filename)
    question = payload.question or str(project.get("question") or "")
    kind = import_kind(name)
    try:
        if kind == "pdf":
            result = await import_email_pdf(
                ResearchDomainStore(store), project_id, filename=name, data=data,
                message_id=payload.message_id, subject=payload.subject, from_address=payload.from_address,
                mode=payload.mode, max_vision_pages=payload.max_vision_pages, question=question,
            )
            _record_vision(store, project_id, result.get("usage") or {}, str(result.get("visionModel") or ""),
                           "email-pdf:" + name)
        else:
            result = import_email_attachment(
                ResearchDomainStore(store), project_id, filename=name, data=data,
                message_id=payload.message_id, subject=payload.subject, from_address=payload.from_address,
                question=question,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    source = result.get("source") or {}
    _imported(store, project_id, f"Email attachment {source.get('title', name)} imported.",
              {"sourceId": source.get("id"), "messageId": payload.message_id, "kind": result.get("kind")})
    return result


class GmailAttachmentImport(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    message_id: str = Field(min_length=1, max_length=200, alias="messageId")
    attachment_id: str = Field(min_length=1, max_length=200, alias="attachmentId")
    filename: str = Field(default="", max_length=255)
    # Optional: agent/UI already downloaded bytes (base64). If omitted, server tries bridge/fetcher.
    data: str = Field(default="", max_length=34_000_000)
    subject: str = Field(default="", max_length=300)
    from_address: str = Field(default="", max_length=200, alias="from")
    question: str = Field(default="", max_length=2000)
    mode: Literal["auto", "text", "vision"] = "auto"
    max_vision_pages: int | None = Field(default=None, alias="maxVisionPages")


@router.get("/imports/gmail/status")
async def gmail_import_status():
    from aleggo.study.research_mail_import import gmail_fetcher_status

    return gmail_fetcher_status()


@router.post("/projects/{project_id}/imports/gmail", status_code=201)
async def import_gmail_attachment_endpoint(project_id: str, payload: GmailAttachmentImport):
    """Import one Gmail attachment by messageId/attachmentId (+ optional base64 data)."""
    from aleggo.study.model_registry import ModelRuntimeError
    from aleggo.study.research_mail_import import GmailFetcherUnavailable, import_gmail_attachment

    store = _store()
    project = _project(project_id, store)
    question = payload.question or str(project.get("question") or "")
    try:
        result = await import_gmail_attachment(
            ResearchDomainStore(store), project_id,
            message_id=payload.message_id, attachment_id=payload.attachment_id,
            filename=payload.filename, data_b64=payload.data, subject=payload.subject,
            from_address=payload.from_address, question=question, mode=payload.mode,
            max_vision_pages=payload.max_vision_pages,
        )
    except GmailFetcherUnavailable as exc:
        raise HTTPException(status_code=503, detail={
            "message": "服务器尚未配置 Gmail 附件拉取。请在下方上传已下载的附件，或由智能体 DownloadFile 后填入，或设置 ALEGGO_GMAIL_ATTACHMENT_FETCH_URL。",
            "errorType": getattr(exc, "error_type", "GMAIL_FETCHER_UNAVAILABLE"),
            "hint": str(exc),
        }) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"视觉模型不可用：{exc}") from exc
    if result.get("usage"):
        _record_vision(store, project_id, result.get("usage") or {}, str(result.get("visionModel") or ""),
                       "gmail:" + payload.attachment_id)
    source = result.get("source") or {}
    _imported(store, project_id, f"已导入 Gmail 附件 {source.get('title', payload.filename)}。",
              {"sourceId": source.get("id"), "messageId": payload.message_id, "attachmentId": payload.attachment_id})
    return result


@router.get("/projects/{project_id}/imports/pdf-continues")
async def list_pdf_continues(project_id: str):
    """Pending long-PDF vision batches persisted in the workbench timeline (multi-session lecture packs)."""
    store = _store()
    _project(project_id, store)
    workbench = ResearchWorkbench(store)
    # Timeline is newest-first: keep only the latest continue-related event per source.
    pending: dict[str, dict] = {}
    seen: set[str] = set()
    for event in workbench.list_timeline(project_id, limit=400):
        details = event.get("details") or {}
        source_id = str(details.get("sourceId") or "")
        event_type = event.get("eventType")
        if not source_id or source_id in seen:
            continue
        if event_type not in {"pdf_vision_continue_pending", "pdf_vision_continue_cleared"}:
            continue
        seen.add(source_id)
        if event_type == "pdf_vision_continue_cleared":
            continue
        hint = details.get("resumeHint") or details
        pending[source_id] = {
            "sourceId": source_id,
            "filename": hint.get("filename") or details.get("filename") or "",
            "remaining": details.get("remaining"),
            "nextPageOffset": hint.get("pageOffset") if hint.get("pageOffset") is not None else details.get("nextPageOffset"),
            "maxVisionPages": hint.get("maxVisionPages") or details.get("visionPageCap"),
            "mode": hint.get("mode") or details.get("mode") or "auto",
            "summary": event.get("summary", ""),
            "updatedAt": event.get("createdAt", ""),
            "hasOriginalFile": False,
        }
    domain = ResearchDomainStore(store)
    for item in pending.values():
        source = domain.get_source(project_id, item["sourceId"])
        item["hasOriginalFile"] = bool(source and source.get("hasOriginalFile"))
        if source and source.get("title") and not item["filename"]:
            item["filename"] = str(source["title"])
    return {"items": sorted(pending.values(), key=lambda item: item.get("updatedAt") or "", reverse=True)}


class PdfContinueBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: Literal["auto", "text", "vision"] = "auto"
    max_vision_pages: int | None = Field(default=None, alias="maxVisionPages")
    page_offset: int | None = Field(default=None, alias="pageOffset")


@router.post("/projects/{project_id}/imports/pdf/{source_id}/continue", status_code=201)
async def continue_pdf_from_stored(project_id: str, source_id: str, payload: PdfContinueBody | None = None):
    """Continue vision transcription using the original PDF bytes already stored on the source."""
    from aleggo.study.model_registry import ModelRuntimeError
    from aleggo.study.research_imports import import_pdf

    store = _store()
    _project(project_id, store)
    domain = ResearchDomainStore(store)
    source = domain.get_source(project_id, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="未找到该导入来源。")
    if not source.get("hasOriginalFile"):
        raise HTTPException(status_code=409, detail="该来源未保存原始 PDF 文件，请重新选择同一文件后继续转写。")
    body = payload or PdfContinueBody()
    meta = source.get("metadata") or {}
    page_offset = body.page_offset
    if page_offset is None:
        page_offset = meta.get("nextPageOffset") or 0
    mode = body.mode or meta.get("mode") or "auto"
    max_pages = body.max_vision_pages if body.max_vision_pages is not None else meta.get("visionPageCap")
    filename = str(source.get("title") or "document.pdf")
    try:
        result = await import_pdf(
            domain, project_id, filename, b"", mode=mode,
            max_vision_pages=max_pages, page_offset=int(page_offset or 0),
            continue_source_id=source_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"视觉模型不可用：{exc}") from exc
    _record_vision(store, project_id, result.get("usage") or {}, str(result.get("visionModel") or ""), "pdf-continue:" + source_id)
    remaining = int(result.get("moreVisionPagesRemaining") or 0)
    details = {"sourceId": source_id, "mode": mode, "transcribed": result.get("transcribedPages"),
               "remaining": remaining, "nextPageOffset": result.get("nextPageOffset"),
               "visionPageCap": result.get("visionPageCap"), "filename": filename,
               "pages": result.get("pages"), "continued": True, "fromStoredBlob": True}
    _imported(store, project_id, f"Continued PDF {filename} from stored original.", details)
    workbench = ResearchWorkbench(store)
    if remaining > 0:
        workbench.record_event(
            project_id, "pdf_vision_continue_pending", "researcher",
            summary=f"PDF「{filename}」仍有 {remaining} 页待视觉转写。",
            details={**details, "resumeHint": {
                "sourceId": source_id, "pageOffset": result.get("nextPageOffset"),
                "maxVisionPages": result.get("visionPageCap"), "filename": filename, "mode": mode,
            }},
        )
    else:
        workbench.record_event(
            project_id, "pdf_vision_continue_cleared", "researcher",
            summary=f"PDF「{filename}」视觉转写批次完成。",
            details={"sourceId": source_id},
        )
    return result


# --------------------------------------------------------------------------- #
# Step-by-step proof review
# --------------------------------------------------------------------------- #
class ProofReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    proof: str = Field(min_length=1, max_length=60_000)
    statement: str = Field(default="", max_length=8000)
    title: str = Field(default="", max_length=200)
    node_id: str = Field(default="", max_length=120, alias="nodeId")
    branch: str = Field(default="main", max_length=120)


class ProofStepAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["", "valid", "gap", "error", "unclear"] = ""
    note: str = Field(default="", max_length=3000)


@router.post("/projects/{project_id}/proof-reviews", status_code=202)
async def create_proof_review(project_id: str, payload: ProofReviewCreate):
    from aleggo.study.proof_review import ProofReviewer

    store = _store()
    _project(project_id, store)
    reviewer = ProofReviewer(store)
    try:
        created = reviewer.create(project_id, payload.proof, statement=payload.statement, title=payload.title,
                                  node_id=payload.node_id, branch=payload.branch)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    reviewer.start(project_id, created["id"])
    return reviewer.view(project_id, created["id"])


@router.get("/projects/{project_id}/proof-reviews")
async def list_proof_reviews(project_id: str):
    from aleggo.study.proof_review import ProofReviewer

    store = _store()
    _project(project_id, store)
    return {"items": ProofReviewer(store).list(project_id)}


@router.get("/projects/{project_id}/proof-reviews/{review_id}")
async def get_proof_review(project_id: str, review_id: str):
    from aleggo.study.proof_review import ProofReviewer

    store = _store()
    _project(project_id, store)
    try:
        return ProofReviewer(store).view(project_id, review_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到证明审查。") from exc


@router.put("/projects/{project_id}/proof-reviews/{review_id}/steps/{step_id}")
async def annotate_proof_step(project_id: str, review_id: str, step_id: str, payload: ProofStepAnnotation):
    from aleggo.study.proof_review import ProofReviewer

    store = _store()
    _project(project_id, store)
    try:
        return ProofReviewer(store).annotate(project_id, review_id, step_id, verdict=payload.verdict, note=payload.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Cross-project long-term memory
# --------------------------------------------------------------------------- #
MemoryKind = Literal["note", "technique", "result", "failed-route", "reference", "preference", "definition"]


class LongTermMemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000)
    kind: MemoryKind = "note"
    tags: list[str] = Field(default_factory=list, max_length=12)
    confirmed: bool = True


class LongTermMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, min_length=1, max_length=8000)
    kind: MemoryKind | None = None
    tags: list[str] | None = Field(default=None, max_length=12)
    confirmed: bool | None = None


def _memory(store: ResearchStore | None = None):
    from aleggo.study.research_memory import ResearchMemory

    # Long-term memory belongs to the signed-in researcher, never to a shared owner.
    return ResearchMemory(store or ResearchStore())


@router.get("/memory")
async def list_long_term_memory(query: str = "", kind: str = ""):
    return {"items": _memory().list_long_term(query[:500], kind=kind[:40])}


@router.post("/memory", status_code=201)
async def add_long_term_memory(payload: LongTermMemoryCreate):
    try:
        return _memory().add(payload.content, kind=payload.kind, tags=payload.tags, confirmed=payload.confirmed)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/memory/{memory_id}")
async def update_long_term_memory(memory_id: str, payload: LongTermMemoryUpdate):
    memory = _memory()
    current = memory.get(memory_id)
    if not current or current.get("projectId"):
        raise HTTPException(status_code=404, detail="未找到长期记忆。")
    try:
        return memory.update(memory_id, content=payload.content, kind=payload.kind, tags=payload.tags, confirmed=payload.confirmed, require_long_term=True)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/memory/{memory_id}", status_code=204)
async def delete_long_term_memory(memory_id: str):
    memory = _memory()
    current = memory.get(memory_id)
    if not current or current.get("projectId"):
        raise HTTPException(status_code=404, detail="未找到长期记忆。")
    try:
        deleted = memory.delete(memory_id, require_long_term=True)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到长期记忆。")


@router.post("/projects/{project_id}/memories/{memory_id}/promote")
async def promote_project_memory(project_id: str, memory_id: str):
    store = _store()
    _project(project_id, store)
    if research_sharing.current_binding() is not None:
        raise HTTPException(status_code=403, detail="长期记忆属于个人；请将笔记复制到自己的记忆库。")
    try:
        return _memory(store).promote(project_id, memory_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到项目记忆。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/memory/suggestions")
async def memory_suggestions(project_id: str):
    store = _store()
    _project(project_id, store)
    return {"items": _memory(store).suggestions(project_id)}


@router.post("/projects/{project_id}/memory/distill")
async def distill_memory(project_id: str):
    """Store this project's lessons as *unconfirmed* long-term memories in the researcher's own store."""
    store = _store()
    _project(project_id, store)
    suggestions = _memory(store).suggestions(project_id)
    personal = _memory()
    created = [personal.add(item["content"], kind=item["kind"], tags=item["tags"], confirmed=False,
                            origin_project=project_id, suggestion_key=item["suggestionKey"]) for item in suggestions]
    return {"items": created}


@router.get("/projects/{project_id}/related-projects")
async def related_projects(project_id: str, query: str = ""):
    store = _store()
    _project(project_id, store)
    return {"items": _memory(store).related_projects(project_id, query[:500])}


# --------------------------------------------------------------------------- #
# Collaboration
# --------------------------------------------------------------------------- #
class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    member_id: str = Field(min_length=1, max_length=120, alias="memberId")
    role: Literal["viewer", "editor"] = "viewer"


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    body: str = Field(min_length=1, max_length=10_000)
    target_type: Literal["project", "step", "node", "artifact", "review-step", "source"] = Field(default="project", alias="targetType")
    target_id: str = Field(default="", max_length=200, alias="targetId")
    parent_id: str = Field(default="", max_length=120, alias="parentId")


class CommentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(default=None, min_length=1, max_length=10_000)
    resolved: bool | None = None


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    step_id: str = Field(min_length=1, max_length=200, alias="stepId")
    assignee_id: str = Field(min_length=1, max_length=120, alias="assigneeId")
    note: str = Field(default="", max_length=2000)


class AssignmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "done"]


def _moderator(project_id: str) -> bool:
    return research_sharing.current_role(project_id) in {"owner", "editor"}


@router.get("/collaboration/candidates")
async def collaboration_candidates():
    from aleggo.services.auth import AUTH_ENABLED

    return {"me": research_sharing.current_identity(), "multiUser": bool(AUTH_ENABLED),
            "items": research_sharing.list_candidates(), "sharedWithMe": research_sharing.shared_with_me()}


@router.get("/projects/{project_id}/members")
async def project_members(project_id: str):
    store = _store()
    _project(project_id, store)
    return research_sharing.members(project_id)


@router.post("/projects/{project_id}/members", status_code=201)
async def add_project_member(project_id: str, payload: MemberAdd):
    store = _store()
    project = _project(project_id, store)
    _owner(project_id)
    try:
        research_sharing.add_member(project_id, payload.member_id, payload.role, project_question=project.get("question", ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ResearchWorkbench(store).record_event(project_id, "project_shared", "researcher",
                                          summary=f"Shared the project ({payload.role}).", details={"memberId": payload.member_id})
    return research_sharing.members(project_id)


@router.delete("/projects/{project_id}/members/{member_id}")
async def remove_project_member(project_id: str, member_id: str):
    store = _store()
    _project(project_id, store)
    me = research_sharing.current_identity()
    if member_id != me["id"]:
        _owner(project_id)  # members may leave; only the owner removes others
    if not research_sharing.remove_member(project_id, member_id, store):
        raise HTTPException(status_code=404, detail="未找到该成员。")
    return {"removed": member_id}


@router.get("/projects/{project_id}/comments")
async def list_project_comments(project_id: str, targetType: str = "", targetId: str = ""):  # noqa: N803 - query names
    store = _store()
    _project(project_id, store)
    return {"items": research_sharing.list_comments(store, project_id, targetType[:40], targetId[:200]),
            "myRole": research_sharing.current_role(project_id)}


@router.post("/projects/{project_id}/comments", status_code=201)
async def add_project_comment(project_id: str, payload: CommentCreate):
    store = _store()
    _project(project_id, store)
    try:
        return research_sharing.add_comment(store, project_id, payload.body, target_type=payload.target_type,
                                            target_id=payload.target_id, parent_id=payload.parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/projects/{project_id}/comments/{comment_id}")
async def update_project_comment(project_id: str, comment_id: str, payload: CommentUpdate):
    store = _store()
    _project(project_id, store)
    try:
        return research_sharing.update_comment(store, project_id, comment_id, resolved=payload.resolved, body=payload.body,
                                               can_moderate=_moderator(project_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到该评论。") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/projects/{project_id}/comments/{comment_id}", status_code=204)
async def delete_project_comment(project_id: str, comment_id: str):
    store = _store()
    _project(project_id, store)
    try:
        deleted = research_sharing.delete_comment(store, project_id, comment_id,
                                                  can_moderate=_moderator(project_id))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到该评论。")


@router.get("/projects/{project_id}/assignments")
async def list_project_assignments(project_id: str):
    store = _store()
    _project(project_id, store)
    return {"items": research_sharing.list_assignments(store, project_id)}


@router.post("/projects/{project_id}/assignments", status_code=201)
async def assign_project_step(project_id: str, payload: AssignmentCreate):
    store = _store()
    _project(project_id, store)
    if not _moderator(project_id):
        raise HTTPException(status_code=403, detail="仅所有者或编辑者可分配步骤。")
    roster = research_sharing.members(project_id)
    people = {roster["owner"]["id"]: roster["owner"]["username"],
              **{item["id"]: item["username"] for item in roster["members"] if item["role"] == "editor"}}
    if payload.assignee_id not in people:
        raise HTTPException(status_code=422, detail="只能把步骤分配给本项目的所有者或编辑者。")
    try:
        return research_sharing.assign_step(store, project_id, payload.step_id, payload.assignee_id, people[payload.assignee_id], payload.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'") or "Plan step not found.") from exc


@router.put("/projects/{project_id}/assignments/{assignment_id}")
async def update_project_assignment(project_id: str, assignment_id: str, payload: AssignmentUpdate):
    store = _store()
    _project(project_id, store)
    me = research_sharing.current_identity()
    try:
        return research_sharing.update_assignment(
            store, project_id, assignment_id, payload.status,
            requester_id=str(me.get("id") or ""), can_moderate=_moderator(project_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到该任务分配。") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Model usage and cost
# --------------------------------------------------------------------------- #
class PriceTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(default="CNY", min_length=1, max_length=8)
    # model name → {"prompt": price per 1K prompt tokens, "completion": price per 1K completion tokens}
    models: dict[str, dict[str, float]] = Field(default_factory=dict, max_length=100)


def _prices_path() -> Path:
    from aleggo.study.lean_formal import tools_home

    return tools_home() / "model-prices.json"


def load_prices() -> dict[str, Any]:
    try:
        data = json.loads(_prices_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"currency": "CNY", "models": {}}
    return {"currency": str(data.get("currency") or "CNY")[:8],
            "models": {str(k): {"prompt": float(v.get("prompt") or 0), "completion": float(v.get("completion") or 0)}
                       for k, v in (data.get("models") or {}).items() if isinstance(v, dict)}}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _cost(prices: dict[str, Any], model: str, prompt: int, completion: int) -> float | None:
    table = prices["models"]
    price = table.get(model) or next((value for key, value in table.items() if key and (key in model or model in key)), None)
    if not price:
        return None
    try:
        return round(prompt / 1000 * float(price["prompt"]) + completion / 1000 * float(price["completion"]), 6)
    except (TypeError, ValueError, KeyError):
        return None


def project_usage(store: ResearchStore, project_id: str) -> dict[str, Any]:
    with store.connection() as conn:
        try:
            rows = conn.execute("SELECT tool_name,input_json,metadata_json,created_at FROM research_tool_runs "
                                "WHERE project_id=? AND tool_name LIKE 'llm:%'", (project_id,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
    prices = load_prices()
    project = store.get_project(project_id) or {}

    def bucket() -> dict[str, Any]:
        return {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0, "calls": 0, "cost": 0.0, "priced": True}

    total, by_role, by_model, by_day = bucket(), {}, {}, {}
    untracked = 0
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except ValueError:
            metadata = {}
        usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
        if not usage:
            untracked += 1
            continue
        try:
            inputs = json.loads(row["input_json"] or "{}")
        except ValueError:
            inputs = {}
        model = str(usage.get("actualModel") or usage.get("modelId") or (inputs.get("modelId") if isinstance(inputs, dict) else "")
                    or project.get("modelId") or "unknown")
        prompt, completion = _as_int(usage.get("promptTokens")), _as_int(usage.get("completionTokens"))
        tokens_total = _as_int(usage.get("totalTokens"), prompt + completion)
        if prompt == 0 and completion == 0 and tokens_total == 0 and not usage.get("requests") and not usage.get("modelCalls"):
            # Present but empty usage blob — still count as an untracked call for teachers.
            untracked += 1
            continue
        cost = _cost(prices, model, prompt, completion)
        role = row["tool_name"][4:] or "model"
        calls = max(1, _as_int(usage.get("requests") or usage.get("modelCalls"), 1))
        for target in (total, by_role.setdefault(role, bucket()), by_model.setdefault(model, bucket()),
                       by_day.setdefault(str(row["created_at"] or "")[:10] or "unknown", bucket())):
            target["promptTokens"] += prompt
            target["completionTokens"] += completion
            target["totalTokens"] += tokens_total
            target["calls"] += calls
            if cost is None:
                target["priced"] = False
            else:
                target["cost"] = round(target["cost"] + cost, 6)

    def rows_of(groups: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
        return sorted(({key: name, **value} for name, value in groups.items()), key=lambda item: -item["totalTokens"])

    return {"total": total, "byRole": rows_of(by_role, "role"), "byModel": rows_of(by_model, "model"),
            "byDay": sorted(({"date": day, **value} for day, value in by_day.items()), key=lambda item: item["date"]),
            "untrackedCalls": untracked, "currency": prices["currency"], "pricesConfigured": bool(prices["models"])}


@router.get("/projects/{project_id}/usage")
async def get_project_usage(project_id: str):
    store = _store()
    _project(project_id, store)
    return project_usage(store, project_id)


@router.get("/usage/prices")
async def get_prices():
    return load_prices()


@router.put("/usage/prices")
async def save_prices(payload: PriceTable):
    _admin()
    for name, price in payload.models.items():
        if not name.strip() or any(float(price.get(key) or 0) < 0 for key in ("prompt", "completion")):
            raise HTTPException(status_code=422, detail="价格须为每千 token 的非负数。")
    path = _prices_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {"currency": payload.currency.strip().upper(),
             "models": {name.strip()[:120]: {"prompt": float(value.get("prompt") or 0), "completion": float(value.get("completion") or 0)}
                        for name, value in payload.models.items()}}
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_prices()


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
class EvalRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model_id: str = Field(default="research.meridian", min_length=1, max_length=120, alias="modelId")
    categories: list[str] = Field(default_factory=list, max_length=10)
    ids: list[str] = Field(default_factory=list, max_length=100)
    limit: int | None = Field(default=None, ge=1, le=200)
    concurrency: int = Field(default=1, ge=1, le=4)


def _eval_root() -> Path:
    return ResearchStore().aleggo_store.root


@router.get("/eval/problems")
async def eval_problems():
    from aleggo.study.research_eval import problem_catalogue

    return problem_catalogue()


@router.post("/eval/runs", status_code=202)
async def start_eval_run(payload: EvalRunCreate):
    from aleggo.study.research_eval import start_eval
    from aleggo.study.research_runtime import validate_research_model

    _admin()
    if not validate_research_model(payload.model_id):
        raise HTTPException(status_code=422, detail="请选择 Lumen、Meridian 或 Axiom。")
    try:
        return start_eval(_eval_root(), payload.model_id, categories=payload.categories or None, ids=payload.ids or None,
                          limit=payload.limit, concurrency=payload.concurrency)
    except ValueError as exc:
        raise HTTPException(status_code=409 if "already running" in str(exc) else 422, detail=str(exc)) from exc


@router.get("/eval/runs")
async def list_eval_runs():
    from aleggo.study.research_eval import list_reports

    return {"items": list_reports(_eval_root())}


@router.get("/eval/runs/{report_id}")
async def get_eval_run(report_id: str):
    from aleggo.study.research_eval import get_report

    report = get_report(_eval_root(), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="未找到评测报告。")
    return report


@router.post("/eval/runs/{report_id}/cancel")
async def cancel_eval_run(report_id: str):
    from aleggo.study.research_eval import cancel_eval

    _admin()
    return {"cancelled": cancel_eval(report_id, _eval_root())}
