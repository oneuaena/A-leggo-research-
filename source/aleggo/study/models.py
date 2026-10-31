"""Public types for the A-leggo evidence-driven learning loop."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvidenceKind(StrEnum):
    READING = "reading"
    EXPLANATION = "explanation"
    BASIC_PROBLEM = "basic_problem"
    TRANSFER_PROBLEM = "transfer_problem"
    ERROR_REDO = "error_redo"
    ACTIVE_RECALL = "active_recall"
    SELF_ASSESSMENT = "self_assessment"
    DIAGNOSIS_REPAIR = "diagnosis_repair"


class SkillKind(StrEnum):
    PROMPT = "prompt"
    WORKFLOW = "workflow"
    TOOL = "tool"
    AGENT = "agent"


class MemoryKind(StrEnum):
    EXPLICIT_FACT = "explicit_fact"
    LEARNING_STATE = "learning_state"
    SESSION_CONTEXT = "session_context"
    MODEL_INFERENCE = "model_inference"


class EvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(min_length=1, max_length=200)
    course_id: str = Field(default="", max_length=200)
    kind: EvidenceKind
    correct: bool | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_type: str = Field(default="manual", max_length=80)
    source_id: str = Field(default="", max_length=300)
    error_tag: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MethodCardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_id: str = Field(default="", max_length=200)
    concept_id: str = Field(default="", max_length=200)
    problem_type: str = Field(min_length=1, max_length=200)
    key_insight: str = Field(min_length=1, max_length=2000)
    common_methods: list[str] = Field(default_factory=list, max_length=12)
    key_steps: list[str] = Field(default_factory=list, max_length=16)
    common_errors: list[str] = Field(default_factory=list, max_length=12)
    next_first_reaction: str = Field(min_length=1, max_length=1200)
    source_id: str = Field(default="", max_length=300)
    confirmed: bool = False


class ReviewGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correct: bool
    rating: Literal["again", "hard", "good", "easy"]
    evidence_kind: EvidenceKind = EvidenceKind.ACTIVE_RECALL
    source_id: str = Field(default="", max_length=300)
    error_tag: str = Field(default="", max_length=120)


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=100_000)
    course_id: str = Field(default="", max_length=200)
    concept_id: str = Field(default="", max_length=200)
    references: list[dict[str, str]] = Field(default_factory=list, max_length=100)


class StyleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=4000)
    active: bool = False


class CompanionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    active: bool = False


class CompanionMemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=100)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=4000)
    source_evidence_id: str = Field(default="", max_length=100)
    confirmed: bool = False


class SkillRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1, max_length=100_000)
    kind: SkillKind = SkillKind.PROMPT
    scope: str = Field(default="global", max_length=200)
    version: str = Field(default="1.0.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    enabled: bool = True
    requires: dict[str, Any] = Field(default_factory=dict)
    entrypoint: str = Field(default="", max_length=500)


class SkillRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    session_id: str = Field(default="", max_length=200)
    detail: str = Field(default="", max_length=4000)


class LegacyImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: dict[str, Any]
    source_name: str = Field(default="a-leggo-v3-backup.json", max_length=255)


class DiagnosisPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: str = Field(min_length=1, max_length=50_000)
    student_work: str = Field(min_length=1, max_length=100_000)
    course_id: str = Field(default="", max_length=200)
    concept_id: str = Field(default="", max_length=200)
    source_ids: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("source_ids")
    @classmethod
    def unique_sources(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

