"""Bailian (DashScope) Agent provider - routes chat to the user's Agent app.

The A-leggo connection card stores a Bailian Agent application (App ID +
API Key).  When no catalog model is configured, the generic chat runtime
is routed to this provider so every capability is answered by the user's
own Bailian Agent - including its cloud-side knowledge bases - instead of
requiring a separate model entry.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aleggo.services.llm.provider_core.base import (
    GenerationSettings,
    LLMProvider,
    LLMResponse,
)

_MAX_PROMPT_CHARS = 20000
_MAX_IMAGES = 5


def _message_text(content: Any) -> str:
    """Flatten an OpenAI-style message content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(part for part in parts if part)
    return str(content or "")


class BailianAgentProvider(LLMProvider):
    """LLMProvider backed by the Bailian Agent completion endpoint."""

    def __init__(self, default_model: str | None = None) -> None:
        super().__init__()
        self.default_model = default_model or "bailian-agent-app"
        self.generation = GenerationSettings()

    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        return self.default_model

    @staticmethod
    def _load_connection():
        from aleggo.study.connection import load_connection
        from aleggo.study.service import get_aleggo_service

        return load_connection(get_aleggo_service())

    @staticmethod
    def _compose_prompt(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            role = str(message.get("role") or "user")
            text = _message_text(message.get("content")).strip()
            if not text:
                continue
            label = {"system": "Setting", "user": "User", "assistant": "Assistant"}.get(role, role)
            lines.append(f"[{label}]\n{text}")
        return "\n\n".join(lines)

    @staticmethod
    def _collect_images(messages: list[dict[str, Any]]) -> list[str]:
        images: list[str] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "image_url":
                    continue
                url = item.get("image_url")
                if isinstance(url, dict):
                    url = url.get("url")
                if isinstance(url, str) and url.startswith("data:image"):
                    images.append(url)
        return images

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        from aleggo.study.connection import AgentError, complete as agent_complete

        connection = await asyncio.to_thread(self._load_connection)
        if not (connection.enabled and connection.ready):
            raise RuntimeError(
                "Agent 未配置：请先在 设置 > a-leggo 连接 中填写应用 ID 与 API Key"
            )
        prompt = self._compose_prompt(messages)[:_MAX_PROMPT_CHARS]
        images = self._collect_images(messages)[:_MAX_IMAGES]
        try:
            result = await agent_complete(connection, prompt, images=images, timeout=240.0)
        except AgentError as exc:
            raise RuntimeError(f"Agent 调用失败：{exc}") from exc
        text = str(result.get("text") or "")
        prompt_chars = len(prompt)
        output_chars = len(text)
        return LLMResponse(
            content=text,
            finish_reason="stop",
            usage={
                "prompt_tokens": max(1, prompt_chars // 3),
                "completion_tokens": max(1, output_chars // 3),
                "total_tokens": max(1, (prompt_chars + output_chars) // 3),
            },
        )


__all__ = ["BailianAgentProvider"]
