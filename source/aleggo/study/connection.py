"""Bailian (DashScope) Agent connection for A-leggo Next.

V3 kept the Agent key in the browser localStorage; V4 keeps it server-side
next to the other learning data so the desktop app, the web app and the CLI
share one connection and the key never ships to the browser. The wire format
is the same V3 client contract: ``POST {base}/api/v1/apps/{app_id}/completion``
with ``input`` / ``parameters`` / ``debug`` and an optional
``parameters.rag_options.pipeline_ids`` when a knowledge base is pinned.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
CONNECTION_FILE = "agent-connection.json"
KNOWLEDGE_MODES = ("default", "specified")


class AgentError(Exception):
    """Base error for the Bailian Agent client."""


class AgentNotConfigured(AgentError):
    """App ID or API Key missing."""


class AgentCallError(AgentError):
    """The Bailian endpoint answered with an error."""


class AgentConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=500)
    base_url: str = Field(default=DEFAULT_BASE_URL, max_length=300)
    knowledge_mode: str = Field(default="default")
    pipeline_ids: list[str] = Field(default_factory=list)
    enabled: bool = True

    @property
    def ready(self) -> bool:
        return bool(self.app_id.strip() and self.api_key.strip())

    def normalised(self) -> "AgentConnection":
        return AgentConnection(
            app_id=self.app_id.strip(),
            api_key=self.api_key.strip(),
            base_url=(self.base_url.strip() or DEFAULT_BASE_URL).rstrip("/"),
            knowledge_mode=(
                self.knowledge_mode if self.knowledge_mode in KNOWLEDGE_MODES else "default"
            ),
            pipeline_ids=[item.strip() for item in self.pipeline_ids if item.strip()][:50],
            enabled=self.enabled,
        )

    def public_view(self) -> dict[str, Any]:
        """What the Settings card may see — never the raw key."""
        return {
            "app_id": self.app_id,
            "has_key": bool(self.api_key),
            "masked_key": "•" * min(len(self.api_key), 32) if self.api_key else "",
            "base_url": self.base_url,
            "knowledge_mode": self.knowledge_mode,
            "pipeline_ids": list(self.pipeline_ids),
            "enabled": self.enabled,
            "ready": self.ready,
        }


class AgentConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=500)
    base_url: str = Field(default=DEFAULT_BASE_URL, max_length=300)
    knowledge_mode: str = Field(default="default")
    pipeline_ids: list[str] = Field(default_factory=list)
    enabled: bool = True


class AgentCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=20000)
    session_id: str = Field(default="", max_length=200)
    images: list[str] = Field(default_factory=list)
    pipeline_ids: list[str] = Field(default_factory=list)


def connection_path(service: Any) -> Path:
    return Path(service.store.root) / CONNECTION_FILE


def load_connection(service: Any) -> AgentConnection:
    path = connection_path(service)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AgentConnection()
    if not isinstance(raw, dict):
        return AgentConnection()
    kept = {key: value for key, value in raw.items() if key in AgentConnection.model_fields}
    try:
        return AgentConnection.model_validate(kept).normalised()
    except ValidationError:
        return AgentConnection()


def save_connection(
    service: Any, update: AgentConnectionUpdate, *, keep_key: str = ""
) -> AgentConnection:
    key = update.api_key.strip()
    # The Settings card shows dots; submitting them back means "keep the key".
    # Submitting a truly empty field means "clear the connection".
    if key and set(key) <= {"•", "."}:
        key = keep_key
    connection = AgentConnection(
        app_id=update.app_id,
        api_key=key,
        base_url=update.base_url,
        knowledge_mode=update.knowledge_mode,
        pipeline_ids=update.pipeline_ids,
        enabled=update.enabled,
    ).normalised()
    path = connection_path(service)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(connection.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return connection


async def complete(
    connection: AgentConnection,
    prompt: str,
    *,
    session_id: str = "",
    images: list[str] | None = None,
    pipeline_ids: list[str] | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    if not connection.ready:
        raise AgentNotConfigured("Agent 未配置：请填写应用 ID 与 API Key")
    input_payload: dict[str, Any] = {"prompt": prompt}
    if session_id:
        input_payload["session_id"] = session_id
    if images:
        input_payload["image_list"] = list(images)
    parameters: dict[str, Any] = {}
    pipes = (
        list(pipeline_ids)
        if pipeline_ids
        else (
            list(connection.pipeline_ids)
            if connection.knowledge_mode == "specified"
            else []
        )
    )
    if pipes:
        parameters["rag_options"] = {"pipeline_ids": pipes}
    url = f"{connection.base_url}/api/v1/apps/{connection.app_id}/completion"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            json={"input": input_payload, "parameters": parameters, "debug": {}},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {connection.api_key}",
            },
        )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = str(
            data.get("message") or data.get("code") or f"HTTP {response.status_code}"
        )
        raise AgentCallError(message)
    output = data.get("output") or {}
    return {
        "text": str(output.get("text") or "") or "Agent 没有返回文本，请再试一次。",
        "session_id": str(output.get("session_id") or ""),
        "request_id": str(data.get("request_id") or ""),
    }


async def probe(connection: AgentConnection, *, timeout: float = 45.0) -> dict[str, Any]:
    started = time.perf_counter()

    def latency() -> int:
        return int((time.perf_counter() - started) * 1000)

    if not connection.ready:
        return {
            "ok": False,
            "message": "Agent 未配置：请填写应用 ID 与 API Key",
            "latency_ms": latency(),
        }
    try:
        result = await complete(connection, "ping", timeout=timeout)
    except AgentError as exc:
        return {"ok": False, "message": str(exc), "latency_ms": latency()}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"网络错误：{exc}", "latency_ms": latency()}
    return {
        "ok": True,
        "message": "已连接。应用 ID 与 API Key 已填写，请求将携带本机学习上下文。",
        "latency_ms": latency(),
        "request_id": result.get("request_id", ""),
    }


__all__ = [
    "AgentCallError",
    "AgentCompletionRequest",
    "AgentConnection",
    "AgentConnectionUpdate",
    "AgentError",
    "AgentNotConfigured",
    "DEFAULT_BASE_URL",
    "complete",
    "connection_path",
    "load_connection",
    "probe",
    "save_connection",
]
