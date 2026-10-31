"""Agentic chat capability."""

from __future__ import annotations

from aleggo.agents.chat.agentic_pipeline import CHAT_OPTIONAL_TOOLS, AgenticChatPipeline
from aleggo.core.capability_protocol import CapabilityManifest, TurnCapability
from aleggo.core.context import UnifiedContext
from aleggo.runtime.request_contracts import get_capability_request_schema
from aleggo.runtime.stream_bus import StreamBus


class ChatCapability(TurnCapability):
    manifest = CapabilityManifest(
        name="chat",
        description=(
            "Agentic chat: an exploring agent loop with tools, followed by "
            "a respond stage that streams the answer."
        ),
        stages=["exploring", "responding"],
        tools_used=CHAT_OPTIONAL_TOOLS,
        cli_aliases=["chat"],
        request_schema=get_capability_request_schema("chat"),
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        pipeline = AgenticChatPipeline(language=context.language)
        await pipeline.run(context, stream)
