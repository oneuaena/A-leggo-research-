"""First-error diagnosis as a real A-leggo turn capability."""

from __future__ import annotations

from aleggo.agents.chat.agentic_pipeline import AgenticChatPipeline
from aleggo.study.prompts import (
    FIRST_ERROR_POLICY_ZH,
    LEARNING_PERSONALIZATION_ZH,
    TUTOR_POLICY_ZH,
)
from aleggo.study.service import get_aleggo_service
from aleggo.core.capability_protocol import CapabilityManifest, TurnCapability
from aleggo.core.context import UnifiedContext
from aleggo.runtime.request_contracts import get_capability_request_schema
from aleggo.runtime.stream_bus import StreamBus


class FirstErrorDiagnosisCapability(TurnCapability):
    manifest = CapabilityManifest(
        name="first_error_diagnosis",
        description="A-leggo mathematical correction that locates and repairs the first genuine error.",
        stages=["examining", "diagnosing", "responding"],
        tools_used=[
            "rag",
            "read_source",
            "code_execution",
            "reason",
            "aleggo_learning_context",
            "aleggo_record_evidence",
            "aleggo_propose_method_card",
            "aleggo_bailian_agent",
        ],
        cli_aliases=["diagnose", "first-error"],
        request_schema=get_capability_request_schema("first_error_diagnosis"),
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        personal = get_aleggo_service().personalization_context()
        policy = "\n\n".join(
            part for part in (FIRST_ERROR_POLICY_ZH, LEARNING_PERSONALIZATION_ZH, personal) if part
        )
        context.sidebar_context = (
            f"{context.sidebar_context}\n\n{policy}" if context.sidebar_context else policy
        )
        pipeline = AgenticChatPipeline(
            language=context.language,
            event_source="first_error_diagnosis",
            event_stage="responding",
        )
        await pipeline.run(context, stream)


class AleggoTutorCapability(TurnCapability):
    manifest = CapabilityManifest(
        name="aleggo_tutor",
        description="Evidence-driven university-math tutoring with guided learning and transfer.",
        stages=["orienting", "teaching", "checking", "responding"],
        tools_used=[
            "rag",
            "read_source",
            "code_execution",
            "reason",
            "aleggo_learning_context",
            "aleggo_record_evidence",
            "aleggo_propose_method_card",
            "aleggo_bailian_agent",
        ],
        cli_aliases=["learn", "tutor"],
        request_schema=get_capability_request_schema("aleggo_tutor"),
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        personal = get_aleggo_service().personalization_context()
        policy = "\n\n".join(
            part for part in (TUTOR_POLICY_ZH, LEARNING_PERSONALIZATION_ZH, personal) if part
        )
        context.sidebar_context = (
            f"{context.sidebar_context}\n\n{policy}" if context.sidebar_context else policy
        )
        await AgenticChatPipeline(
            language=context.language,
            event_source="aleggo_tutor",
            event_stage="responding",
        ).run(context, stream)


__all__ = ["AleggoTutorCapability", "FirstErrorDiagnosisCapability"]
