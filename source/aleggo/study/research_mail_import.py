"""Gmail / mail attachment → research project import glue.

The A-leggo web app does not embed the Gmail MCP.  Attachment bytes reach this
module in one of three ways:

1. The UI / agent already downloaded the file and posts base64 ``data``
   (see :func:`import_mail_attachment_bytes`).
2. An operator configures ``ALEGGO_GMAIL_ATTACHMENT_FETCH_URL`` — a trusted
   HTTPS endpoint that accepts ``messageId`` + ``attachmentId`` and returns
   the raw bytes (for deployments that bridge MCP or the Gmail API).
3. Otherwise the API returns a clear ``GMAIL_FETCHER_UNAVAILABLE`` stub so the
   client can fall back to paste/upload.

Agent-side (this Grok Bot session) can still use Gmail MCP ``get_message`` for
metadata and ``DownloadFile`` with ``fileId="<messageId>/<attachmentId>"``,
then POST the bytes to ``/imports/email-attachment``.
"""

from __future__ import annotations

import base64
import os
from pathlib import PurePosixPath
from typing import Any, Callable

import httpx

from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_imports import (
    MAX_DOCUMENT_BYTES,
    import_email_attachment,
    import_email_pdf,
    import_kind,
)

Fetcher = Callable[[str, str], bytes]

# Optional process-wide fetcher (tests / custom deployments inject one).
_FETCHER: Fetcher | None = None


class GmailFetcherUnavailable(RuntimeError):
    def __init__(self, message: str = "") -> None:
        super().__init__(
            message
            or "本部署未配置 Gmail 附件拉取。请在客户端上传附件（base64 data）、设置 ALEGGO_GMAIL_ATTACHMENT_FETCH_URL，"
            "或由智能体经 Gmail MCP DownloadFile 后 POST /imports/email-attachment。"
        )
        self.error_type = "GMAIL_FETCHER_UNAVAILABLE"


def set_attachment_fetcher(fetcher: Fetcher | None) -> None:
    global _FETCHER
    _FETCHER = fetcher


def list_attachment_hints(message: dict[str, Any]) -> list[dict[str, str]]:
    """Normalise Gmail ``get_message`` attachment metadata into import hints."""
    items: list[dict[str, str]] = []
    attachments = message.get("attachments") or []
    attachment_ids = message.get("attachment_ids") or message.get("attachmentIds") or []
    if isinstance(attachments, list) and attachments:
        for index, item in enumerate(attachments):
            if not isinstance(item, dict):
                continue
            att_id = str(item.get("id") or item.get("attachmentId") or "")
            name = str(item.get("filename") or item.get("name") or f"attachment-{index + 1}")
            mime = str(item.get("mimeType") or item.get("mime_type") or "application/octet-stream")
            if att_id:
                items.append({"attachmentId": att_id, "filename": name[:255], "mimeType": mime[:120],
                              "kind": import_kind(name), "downloadFileId": f"{message.get('id', '')}/{att_id}"})
    elif isinstance(attachment_ids, list):
        for index, att_id in enumerate(attachment_ids):
            att_id = str(att_id)
            if not att_id:
                continue
            name = f"attachment-{index + 1}"
            items.append({"attachmentId": att_id, "filename": name, "mimeType": "application/octet-stream",
                          "kind": "document", "downloadFileId": f"{message.get('id', '')}/{att_id}"})
    return items


def fetch_attachment_bytes(message_id: str, attachment_id: str) -> bytes:
    """Resolve attachment bytes via injected fetcher or configured HTTPS bridge."""
    message_id = str(message_id or "").strip()
    attachment_id = str(attachment_id or "").strip()
    if not message_id or not attachment_id:
        raise ValueError("请填写 Gmail messageId 与 attachmentId。")
    if _FETCHER is not None:
        data = _FETCHER(message_id, attachment_id)
        if not isinstance(data, bytes) or not data:
            raise ValueError("Gmail 拉取返回空数据。")
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError("邮件附件上限为 25 MB。")
        return data
    url = os.environ.get("ALEGGO_GMAIL_ATTACHMENT_FETCH_URL", "").strip()
    if not url:
        raise GmailFetcherUnavailable()
    if not url.startswith("https://"):
        raise GmailFetcherUnavailable("ALEGGO_GMAIL_ATTACHMENT_FETCH_URL 必须是 https:// 地址。")
    try:
        response = httpx.get(url, params={"messageId": message_id, "attachmentId": attachment_id},
                             timeout=60.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise GmailFetcherUnavailable(f"Gmail fetch bridge unreachable: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise GmailFetcherUnavailable(f"Gmail fetch bridge returned HTTP {response.status_code}.")
    data = response.content
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("邮件附件须非空且不超过 25 MB。")
    return data


def decode_base64_payload(raw: str) -> bytes:
    if raw.startswith("data:"):
        _prefix, comma, payload = raw.partition(",")
        if not comma:
            raise ValueError("附件不是有效的 base64 编码。")
    else:
        payload = raw
    try:
        data = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise ValueError("附件不是有效的 base64 编码。") from exc
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("邮件附件须非空且不超过 25 MB。")
    return data


async def import_mail_attachment_bytes(
    domain: ResearchDomainStore, project_id: str, *, filename: str, data: bytes,
    message_id: str = "", subject: str = "", from_address: str = "",
    question: str = "", mode: str = "auto", max_vision_pages: int | None = None,
) -> dict[str, Any]:
    """Import already-fetched attachment bytes into the research project."""
    name = PurePosixPath(filename or "attachment.bin").name
    kind = import_kind(name)
    if kind == "pdf":
        return await import_email_pdf(
            domain, project_id, filename=name, data=data, message_id=message_id,
            subject=subject, from_address=from_address, mode=mode,
            max_vision_pages=max_vision_pages, question=question,
        )
    if kind == "image":
        raise ValueError("图片类邮件附件请下载后走「手写转写」入口。")
    result = import_email_attachment(
        domain, project_id, filename=name, data=data, message_id=message_id,
        subject=subject, from_address=from_address, question=question,
    )
    return result


async def import_gmail_attachment(
    domain: ResearchDomainStore, project_id: str, *, message_id: str, attachment_id: str,
    filename: str = "", data_b64: str = "", subject: str = "", from_address: str = "",
    question: str = "", mode: str = "auto", max_vision_pages: int | None = None,
) -> dict[str, Any]:
    """Fetch (if needed) and import one Gmail attachment."""
    if data_b64.strip():
        data = decode_base64_payload(data_b64)
    else:
        data = fetch_attachment_bytes(message_id, attachment_id)
    name = filename.strip() or f"gmail-{attachment_id[:12]}.bin"
    result = await import_mail_attachment_bytes(
        domain, project_id, filename=name, data=data, message_id=message_id,
        subject=subject, from_address=from_address, question=question,
        mode=mode, max_vision_pages=max_vision_pages,
    )
    result["gmail"] = {
        "messageId": message_id, "attachmentId": attachment_id,
        "downloadFileId": f"{message_id}/{attachment_id}",
        "agentHint": "Agents: DownloadFile connection=user-Gmail source.fileId='<messageId>/<attachmentId>', "
                     "then POST base64 to /imports/email-attachment or /imports/gmail.",
    }
    return result


def gmail_fetcher_status() -> dict[str, Any]:
    return {
        "injectedFetcher": _FETCHER is not None,
        "bridgeUrlConfigured": bool(os.environ.get("ALEGGO_GMAIL_ATTACHMENT_FETCH_URL", "").strip()),
        "available": _FETCHER is not None or bool(os.environ.get("ALEGGO_GMAIL_ATTACHMENT_FETCH_URL", "").strip()),
        "stubMessage": (
            "本 overlay 中 Gmail MCP 属于智能体侧，不在 Aleggo 服务进程内。"
            "请在「导入」页上传已下载附件、配置 ALEGGO_GMAIL_ATTACHMENT_FETCH_URL，"
            "或由智能体 DownloadFile 后调用 importEmailAttachment / importGmailAttachment。"
        ),
    }
