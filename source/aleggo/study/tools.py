"""Agent tools that close the A-leggo learning-data loop."""

from __future__ import annotations

from typing import Any

from aleggo.study.models import EvidenceCreate, EvidenceKind, MethodCardCreate
from aleggo.study.service import get_aleggo_service
from aleggo.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult


class AleggoLearningContextTool(BaseTool):
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="aleggo_learning_context",
            description="Read evidence-backed mastery, weak points, due review, and confirmed method cards before personalizing math teaching.",
            parameters=[
                ToolParameter("concept_id", "string", "Knowledge point id; empty returns the map.", required=False),
                ToolParameter("course_id", "string", "Course id for scoping.", required=False),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        service = get_aleggo_service()
        concept_id = str(kwargs.get("concept_id") or "").strip()
        course_id = str(kwargs.get("course_id") or "").strip()
        payload = {
            "mastery": service.mastery(concept_id) if concept_id else service.mastery_map(course_id),
            "weak_points": service.weak_points(course_id)[:20],
            "due_reviews": service.due_reviews(course_id)[:20],
            "confirmed_method_cards": service.list_method_cards(False)[:20],
        }
        return ToolResult(content=str(payload), metadata={"aleggo": payload})


class AleggoRecordEvidenceTool(BaseTool):
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="aleggo_record_evidence",
            description=(
                "Record a real observed learning event after the learner reads, answers, repairs, "
                "recalls, or transfers knowledge. Never mark correctness without observable work."
            ),
            parameters=[
                ToolParameter("concept_id", "string", "Stable knowledge-point name or id."),
                ToolParameter("kind", "string", "Evidence kind.", enum=[kind.value for kind in EvidenceKind]),
                ToolParameter("correct", "boolean", "Observed correctness; omit for reading/explanation.", required=False),
                ToolParameter("course_id", "string", "Course id.", required=False),
                ToolParameter("confidence", "number", "Observation confidence from 0 to 1.", required=False),
                ToolParameter("source_id", "string", "Session, document, or cited source id.", required=False),
                ToolParameter("error_tag", "string", "Specific misconception tag when incorrect.", required=False),
                ToolParameter("note", "string", "Concise evidence note.", required=False),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            item = EvidenceCreate(
                concept_id=str(kwargs.get("concept_id") or ""),
                course_id=str(kwargs.get("course_id") or ""),
                kind=EvidenceKind(str(kwargs.get("kind") or "")),
                correct=kwargs.get("correct"),
                confidence=float(kwargs.get("confidence", 1.0)),
                source_type="agent_observation",
                source_id=str(kwargs.get("source_id") or ""),
                error_tag=str(kwargs.get("error_tag") or ""),
                note=str(kwargs.get("note") or ""),
            )
            created = get_aleggo_service().add_evidence(item)
            mastery = get_aleggo_service().mastery(item.concept_id)
            return ToolResult(
                content=f"Learning evidence saved as {created['id']}; mastery state: {mastery['level']}.",
                metadata={"evidence": created, "mastery": mastery},
            )
        except Exception as exc:
            return ToolResult(content=f"Learning evidence was not saved: {exc}", success=False)


class AleggoProposeMethodCardTool(BaseTool):
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="aleggo_propose_method_card",
            description="Create an A-leggo six-field method-card candidate. It remains unsaved for learning use until the user confirms it.",
            parameters=[
                ToolParameter("problem_type", "string", "题型名称"),
                ToolParameter("key_insight", "string", "本题题眼"),
                ToolParameter("common_methods", "array", "常用方法"),
                ToolParameter("key_steps", "array", "关键步骤"),
                ToolParameter("common_errors", "array", "常见错误"),
                ToolParameter("next_first_reaction", "string", "下次第一反应"),
                ToolParameter("concept_id", "string", "Knowledge point id.", required=False),
                ToolParameter("course_id", "string", "Course id.", required=False),
                ToolParameter("source_id", "string", "Source session or material id.", required=False),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            card = get_aleggo_service().create_method_card(
                MethodCardCreate(
                    problem_type=str(kwargs.get("problem_type") or ""),
                    key_insight=str(kwargs.get("key_insight") or ""),
                    common_methods=[str(x) for x in kwargs.get("common_methods") or []],
                    key_steps=[str(x) for x in kwargs.get("key_steps") or []],
                    common_errors=[str(x) for x in kwargs.get("common_errors") or []],
                    next_first_reaction=str(kwargs.get("next_first_reaction") or ""),
                    concept_id=str(kwargs.get("concept_id") or ""),
                    course_id=str(kwargs.get("course_id") or ""),
                    source_id=str(kwargs.get("source_id") or ""),
                    confirmed=False,
                )
            )
            return ToolResult(
                content=f"Method-card candidate {card['id']} created. Ask the learner to confirm before it becomes active.",
                metadata={"method_card_candidate": card, "requires_confirmation": True},
            )
        except Exception as exc:
            return ToolResult(content=f"Method-card candidate was not created: {exc}", success=False)


class AleggoBailianAgentTool(BaseTool):
    """Delegate a sub-question to the learner's connected Bailian Agent app."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="aleggo_bailian_agent",
            description=(
                "Ask the learner's connected Bailian (DashScope) Agent application a focused "
                "sub-question. The Agent answers with its own knowledge base configured in "
                "Settings; use it when its pinned knowledge may beat local RAG."
            ),
            parameters=[
                ToolParameter("prompt", "string", "The question to send to the Bailian Agent."),
                ToolParameter("session_id", "string", "Optional Agent session id for follow-ups.", required=False),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        from aleggo.study import connection as agent_link

        prompt = str(kwargs.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(content="aleggo_bailian_agent requires a non-empty prompt.", success=False)
        connection = agent_link.load_connection(get_aleggo_service())
        if not connection.enabled or not connection.ready:
            return ToolResult(
                content=(
                    "Bailian Agent is not connected. Continue with local tools; the learner can "
                    "connect it under Settings -> a-leggo connection."
                ),
                success=False,
            )
        try:
            result = await agent_link.complete(
                connection, prompt, session_id=str(kwargs.get("session_id") or "")
            )
        except agent_link.AgentError as exc:
            return ToolResult(content=f"Bailian Agent call failed: {exc}", success=False)
        return ToolResult(
            content=result["text"],
            metadata={"bailian_session_id": result.get("session_id", ""), "bailian_request_id": result.get("request_id", "")},
        )


__all__ = [
    "AleggoBailianAgentTool",
    "AleggoLearningContextTool",
    "AleggoProposeMethodCardTool",
    "AleggoRecordEvidenceTool",
]
