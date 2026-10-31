"""Evidence-driven university-math learning services for A-leggo Next."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import math
import re
from typing import Any
import uuid

import yaml

from aleggo.study.models import (
    CompanionCreate,
    CompanionMemoryCreate,
    EvidenceCreate,
    EvidenceKind,
    MemoryKind,
    MethodCardCreate,
    NoteCreate,
    ReviewGrade,
    SkillRegistration,
    SkillRunCreate,
    StyleCreate,
)
from aleggo.study.store import AleggoStore, utc_now
from aleggo.services.path_service import get_path_service
from aleggo.services.skill.service import SkillExistsError, get_skill_service

EVIDENCE_WEIGHTS: dict[EvidenceKind, float] = {
    EvidenceKind.READING: 0.08,
    EvidenceKind.EXPLANATION: 0.16,
    EvidenceKind.BASIC_PROBLEM: 0.45,
    EvidenceKind.TRANSFER_PROBLEM: 0.68,
    EvidenceKind.ERROR_REDO: 0.72,
    EvidenceKind.ACTIVE_RECALL: 0.78,
    EvidenceKind.SELF_ASSESSMENT: 0.10,
    EvidenceKind.DIAGNOSIS_REPAIR: 0.58,
}
ASSESSED_KINDS = {
    EvidenceKind.BASIC_PROBLEM,
    EvidenceKind.TRANSFER_PROBLEM,
    EvidenceKind.ERROR_REDO,
    EvidenceKind.ACTIVE_RECALL,
    EvidenceKind.DIAGNOSIS_REPAIR,
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _decode_json(row: dict[str, Any], *columns: str) -> dict[str, Any]:
    result = dict(row)
    for column in columns:
        value = result.pop(column, None)
        result[column.removesuffix("_json")] = json.loads(value or "[]")
    for key in ("correct", "active", "enabled", "success"):
        if key in result and result[key] is not None:
            result[key] = bool(result[key])
    return result


class AleggoService:
    def __init__(self, store: AleggoStore) -> None:
        self.store = store

    # ── Evidence, mastery, weakness, and review ───────────────────────

    def add_evidence(
        self,
        item: EvidenceCreate,
        *,
        schedule_review: bool = True,
    ) -> dict[str, Any]:
        evidence_id = _id("ev")
        occurred_at = utc_now()
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO evidence(
                    id,concept_id,course_id,kind,correct,confidence,source_type,
                    source_id,error_tag,note,metadata_json,occurred_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,
                    item.concept_id.strip(),
                    item.course_id.strip(),
                    item.kind.value,
                    None if item.correct is None else int(item.correct),
                    item.confidence,
                    item.source_type.strip() or "manual",
                    item.source_id.strip(),
                    item.error_tag.strip(),
                    item.note.strip(),
                    json.dumps(item.metadata, ensure_ascii=False),
                    occurred_at,
                ),
            )
        if schedule_review and item.correct is False and item.kind in ASSESSED_KINDS:
            self._schedule_after_failure(item.concept_id, item.course_id)
        self._sync_learning_state_memory(item.concept_id, evidence_id)
        return {"id": evidence_id, "occurred_at": occurred_at, **item.model_dump(mode="json")}

    def _sync_learning_state_memory(self, concept_id: str, evidence_id: str) -> None:
        """Refresh system-derived learning state for every active companion.

        This is distinct from a model inference: it is deterministically
        derived from the evidence ledger and therefore may be used immediately.
        """
        companions = self.store.rows("SELECT id FROM companions WHERE active=1")
        if not companions:
            return
        state = self.mastery(concept_id)
        marker = f"concept:{concept_id}"
        content = (
            f"[{marker}] 学习状态={state['level']}，"
            f"已评估证据={state['assessed_count']}，置信度={state['confidence']:.2f}"
        )
        now = utc_now()
        with self.store.connection() as conn:
            for companion in companions:
                existing = conn.execute(
                    """SELECT id FROM companion_memories
                    WHERE companion_id=? AND kind='learning_state' AND source_evidence_id=?""",
                    (companion["id"], marker),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE companion_memories SET content=?,status='confirmed',updated_at=? WHERE id=?",
                        (content, now, existing["id"]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO companion_memories VALUES(?,?,?,?,?,?,?,?)",
                        (
                            _id("cmem"),
                            companion["id"],
                            MemoryKind.LEARNING_STATE.value,
                            content,
                            "confirmed",
                            marker,
                            now,
                            now,
                        ),
                    )

    def list_evidence(self, concept_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if concept_id:
            rows = self.store.rows(
                "SELECT * FROM evidence WHERE concept_id=? ORDER BY occurred_at DESC LIMIT ?",
                (concept_id, limit),
            )
        else:
            rows = self.store.rows("SELECT * FROM evidence ORDER BY occurred_at DESC LIMIT ?", (limit,))
        return [_decode_json(row, "metadata_json") for row in rows]

    def mastery(self, concept_id: str) -> dict[str, Any]:
        rows = self.list_evidence(concept_id, 500)
        now = datetime.now(UTC)
        numerator = 0.0
        denominator = 0.0
        assessed_count = 0
        kinds: set[str] = set()
        sources: set[str] = set()
        latest_error = False
        for row in rows:
            kind = EvidenceKind(row["kind"])
            occurred = datetime.fromisoformat(row["occurred_at"])
            age_days = max(0.0, (now - occurred).total_seconds() / 86400)
            decay = math.exp(-age_days / 120)
            weight = EVIDENCE_WEIGHTS[kind] * float(row["confidence"]) * decay
            correct = row["correct"]
            if kind in ASSESSED_KINDS and correct is not None:
                assessed_count += 1
                kinds.add(kind.value)
                if row["source_id"]:
                    sources.add(row["source_id"])
                value = 1.0 if correct else 0.0
                numerator += value * weight
                denominator += weight
                if assessed_count == 1:
                    latest_error = not correct
            elif kind in {EvidenceKind.READING, EvidenceKind.EXPLANATION}:
                # Exposure contributes confidence context, never a pass signal.
                denominator += weight * 0.12
                numerator += weight * 0.12 * 0.25
        raw = numerator / denominator if denominator else 0.0
        diversity = min(1.0, len(kinds) / 3)
        volume = 1 - math.exp(-denominator / 1.4)
        confidence = min(1.0, volume * (0.65 + 0.35 * diversity))
        score = max(0.0, min(1.0, raw * confidence))
        if latest_error:
            score = min(score, 0.62)
        sufficient = assessed_count >= 2 and len(kinds) >= 2
        if not sufficient:
            level = "insufficient"
        elif score >= 0.85 and EvidenceKind.TRANSFER_PROBLEM.value in kinds:
            level = "mastered"
        elif score >= 0.65:
            level = "developing"
        else:
            level = "weak"
        return {
            "concept_id": concept_id,
            "score": round(score, 4),
            "confidence": round(confidence, 4),
            "level": level,
            "evidence_sufficient": sufficient,
            "assessed_count": assessed_count,
            "evidence_kinds": sorted(kinds),
            "source_count": len(sources),
            "message": "证据不足/暂无法判断" if not sufficient else "",
        }

    def mastery_map(self, course_id: str = "") -> list[dict[str, Any]]:
        if course_id:
            rows = self.store.rows(
                "SELECT DISTINCT concept_id FROM evidence WHERE course_id=? ORDER BY concept_id",
                (course_id,),
            )
        else:
            rows = self.store.rows("SELECT DISTINCT concept_id FROM evidence ORDER BY concept_id")
        return [self.mastery(row["concept_id"]) for row in rows]

    def weak_points(self, course_id: str = "") -> list[dict[str, Any]]:
        clause = "WHERE correct=0"
        params: tuple[Any, ...] = ()
        if course_id:
            clause += " AND course_id=?"
            params = (course_id,)
        rows = self.store.rows(
            f"""SELECT concept_id,error_tag,COUNT(*) AS error_count,MAX(occurred_at) AS last_error
                FROM evidence {clause}
                GROUP BY concept_id,error_tag ORDER BY error_count DESC,last_error DESC""",
            params,
        )
        for row in rows:
            row["mastery"] = self.mastery(row["concept_id"])
        return rows

    def _schedule_after_failure(self, concept_id: str, course_id: str) -> None:
        now = datetime.now(UTC)
        with self.store.connection() as conn:
            existing = conn.execute(
                "SELECT ease,lapses FROM reviews WHERE concept_id=?", (concept_id,)
            ).fetchone()
            ease = max(1.3, float(existing["ease"] if existing else 2.5) - 0.2)
            lapses = int(existing["lapses"] if existing else 0) + 1
            conn.execute(
                """INSERT INTO reviews(
                    concept_id,course_id,due_at,interval_days,ease,streak,lapses,last_result,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(concept_id) DO UPDATE SET
                    course_id=excluded.course_id,due_at=excluded.due_at,
                    interval_days=excluded.interval_days,ease=excluded.ease,
                    streak=0,lapses=excluded.lapses,last_result='again',updated_at=excluded.updated_at""",
                (
                    concept_id,
                    course_id,
                    (now + timedelta(days=1)).isoformat(),
                    1.0,
                    ease,
                    0,
                    lapses,
                    "again",
                    now.isoformat(),
                ),
            )

    def grade_review(self, concept_id: str, course_id: str, grade: ReviewGrade) -> dict[str, Any]:
        # A wrong answer overrides an accidental Easy click.
        rating = grade.rating if grade.correct else "again"
        now = datetime.now(UTC)
        existing = self.store.row("SELECT * FROM reviews WHERE concept_id=?", (concept_id,)) or {}
        ease = float(existing.get("ease", 2.5))
        streak = int(existing.get("streak", 0))
        lapses = int(existing.get("lapses", 0))
        previous = float(existing.get("interval_days", 0.0))
        if not grade.correct:
            interval = 1.0
            ease = max(1.3, ease - 0.2)
            streak = 0
            lapses += 1
        else:
            streak += 1
            if rating == "hard":
                interval = max(1.0, previous * 1.2)
                ease = max(1.3, ease - 0.05)
            elif rating == "easy":
                interval = 2.0 if previous <= 0 else max(2.0, previous * ease * 1.3)
                ease = min(3.0, ease + 0.1)
            else:
                interval = 1.0 if previous <= 0 else max(1.0, previous * ease)
        due_at = (now + timedelta(days=interval)).isoformat()
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO reviews(
                    concept_id,course_id,due_at,interval_days,ease,streak,lapses,last_result,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(concept_id) DO UPDATE SET
                    course_id=excluded.course_id,due_at=excluded.due_at,
                    interval_days=excluded.interval_days,ease=excluded.ease,
                    streak=excluded.streak,lapses=excluded.lapses,
                    last_result=excluded.last_result,updated_at=excluded.updated_at""",
                (concept_id, course_id, due_at, interval, ease, streak, lapses, rating, now.isoformat()),
            )
        evidence = self.add_evidence(
            EvidenceCreate(
                concept_id=concept_id,
                course_id=course_id,
                kind=grade.evidence_kind,
                correct=grade.correct,
                source_type="spaced_review",
                source_id=grade.source_id,
                error_tag=grade.error_tag,
            ),
            schedule_review=False,
        )
        row = self.store.row("SELECT * FROM reviews WHERE concept_id=?", (concept_id,)) or {}
        row["rating_applied"] = rating
        row["evidence_id"] = evidence["id"]
        return row

    def due_reviews(self, course_id: str = "", at: datetime | None = None) -> list[dict[str, Any]]:
        due = (at or datetime.now(UTC)).isoformat()
        if course_id:
            return self.store.rows(
                "SELECT * FROM reviews WHERE due_at<=? AND course_id=? ORDER BY due_at",
                (due, course_id),
            )
        return self.store.rows("SELECT * FROM reviews WHERE due_at<=? ORDER BY due_at", (due,))

    # ── User-owned learning objects ────────────────────────────────────

    def create_method_card(self, item: MethodCardCreate) -> dict[str, Any]:
        card_id, now = _id("card"), utc_now()
        status = "confirmed" if item.confirmed else "candidate"
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO method_cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    card_id,
                    item.course_id,
                    item.concept_id,
                    item.problem_type,
                    item.key_insight,
                    json.dumps(item.common_methods, ensure_ascii=False),
                    json.dumps(item.key_steps, ensure_ascii=False),
                    json.dumps(item.common_errors, ensure_ascii=False),
                    item.next_first_reaction,
                    item.source_id,
                    status,
                    now,
                    now,
                ),
            )
        return self.get_method_card(card_id)

    def get_method_card(self, card_id: str) -> dict[str, Any]:
        row = self.store.row("SELECT * FROM method_cards WHERE id=?", (card_id,))
        if row is None:
            raise KeyError(card_id)
        return _decode_json(row, "common_methods_json", "key_steps_json", "common_errors_json")

    def confirm_method_card(self, card_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            changed = conn.execute(
                "UPDATE method_cards SET status='confirmed',updated_at=? WHERE id=?",
                (utc_now(), card_id),
            ).rowcount
        if not changed:
            raise KeyError(card_id)
        return self.get_method_card(card_id)

    def list_method_cards(self, include_candidates: bool = True) -> list[dict[str, Any]]:
        where = "" if include_candidates else "WHERE status='confirmed'"
        rows = self.store.rows(f"SELECT * FROM method_cards {where} ORDER BY updated_at DESC")
        return [
            _decode_json(row, "common_methods_json", "key_steps_json", "common_errors_json")
            for row in rows
        ]

    def create_note(self, item: NoteCreate) -> dict[str, Any]:
        note_id, now = _id("note"), utc_now()
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?)",
                (
                    note_id,
                    item.title,
                    item.body,
                    item.course_id,
                    item.concept_id,
                    json.dumps(item.references, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return _decode_json(self.store.row("SELECT * FROM notes WHERE id=?", (note_id,)) or {}, "references_json")

    def list_notes(self) -> list[dict[str, Any]]:
        return [
            _decode_json(row, "references_json")
            for row in self.store.rows("SELECT * FROM notes ORDER BY updated_at DESC")
        ]

    def create_style(self, item: StyleCreate) -> dict[str, Any]:
        style_id, now = _id("style"), utc_now()
        with self.store.connection() as conn:
            if item.active:
                conn.execute("UPDATE styles SET active=0")
            conn.execute(
                "INSERT INTO styles VALUES(?,?,?,?,?,?)",
                (style_id, item.name, item.instructions, int(item.active), now, now),
            )
        return _decode_json(self.store.row("SELECT * FROM styles WHERE id=?", (style_id,)) or {})

    def create_companion(self, item: CompanionCreate) -> dict[str, Any]:
        companion_id, now = _id("companion"), utc_now()
        with self.store.connection() as conn:
            if item.active:
                conn.execute("UPDATE companions SET active=0")
            conn.execute(
                "INSERT INTO companions VALUES(?,?,?,?,?,?)",
                (companion_id, item.name, item.description, int(item.active), now, now),
            )
        return _decode_json(
            self.store.row("SELECT * FROM companions WHERE id=?", (companion_id,)) or {}
        )

    def add_companion_memory(self, item: CompanionMemoryCreate) -> dict[str, Any]:
        memory_id, now = _id("cmem"), utc_now()
        # Model inference is pending regardless of caller input. Explicit facts
        # are confirmed only through an explicit user action.
        confirmed = item.confirmed and item.kind != MemoryKind.MODEL_INFERENCE
        status = "confirmed" if confirmed else "pending"
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO companion_memories VALUES(?,?,?,?,?,?,?,?)",
                (
                    memory_id,
                    item.companion_id,
                    item.kind.value,
                    item.content,
                    status,
                    item.source_evidence_id,
                    now,
                    now,
                ),
            )
        return self.store.row("SELECT * FROM companion_memories WHERE id=?", (memory_id,)) or {}

    def confirm_companion_memory(self, memory_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            changed = conn.execute(
                "UPDATE companion_memories SET status='confirmed',updated_at=? WHERE id=?",
                (utc_now(), memory_id),
            ).rowcount
        if not changed:
            raise KeyError(memory_id)
        return self.store.row("SELECT * FROM companion_memories WHERE id=?", (memory_id,)) or {}

    def delete_companion_memory(self, memory_id: str) -> bool:
        with self.store.connection() as conn:
            return bool(conn.execute("DELETE FROM companion_memories WHERE id=?", (memory_id,)).rowcount)

    def companion_context(self, companion_id: str) -> str:
        companion = self.store.row("SELECT * FROM companions WHERE id=?", (companion_id,))
        if companion is None:
            return ""
        memories = self.store.rows(
            """SELECT kind,content FROM companion_memories
                WHERE companion_id=? AND status='confirmed'
                ORDER BY updated_at DESC LIMIT 30""",
            (companion_id,),
        )
        lines = [f"长期学习伙伴：{companion['name']}", str(companion["description"] or "")]
        lines.extend(f"- [{row['kind']}] {row['content']}" for row in memories)
        return "\n".join(line for line in lines if line)

    def personalization_context(self) -> str:
        """Return only active style plus confirmed companion memory."""
        style = self.store.row("SELECT * FROM styles WHERE active=1 ORDER BY updated_at DESC LIMIT 1")
        companion = self.store.row(
            "SELECT * FROM companions WHERE active=1 ORDER BY updated_at DESC LIMIT 1"
        )
        parts: list[str] = []
        if style:
            parts.append(f"回答风格：{style['name']}\n{style['instructions']}")
        if companion:
            context = self.companion_context(str(companion["id"]))
            if context:
                parts.append(context)
        return "\n\n".join(parts)

    def list_identity(self) -> dict[str, Any]:
        return {
            "styles": [
                _decode_json(row) for row in self.store.rows("SELECT * FROM styles ORDER BY updated_at DESC")
            ],
            "companions": [
                _decode_json(row)
                for row in self.store.rows("SELECT * FROM companions ORDER BY updated_at DESC")
            ],
            "memories": self.store.rows(
                "SELECT * FROM companion_memories ORDER BY updated_at DESC LIMIT 300"
            ),
        }

    # ── Real Skill Registry integration ───────────────────────────────

    def register_skill(self, item: SkillRegistration) -> dict[str, Any]:
        skill_service = get_skill_service()
        header = {
            "name": item.name,
            "description": item.description,
            "tags": ["a-leggo", item.kind.value],
            "always": False,
            "enabled": item.enabled,
            "skill_type": item.kind.value,
            "scope": item.scope,
            "version": item.version,
        }
        if item.requires:
            header["requires"] = item.requires
        if item.entrypoint:
            header["entrypoint"] = item.entrypoint
        content = f"---\n{yaml.safe_dump(header, sort_keys=False, allow_unicode=True).strip()}\n---\n\n{item.instructions.strip()}\n"
        try:
            skill_service.create(item.name, item.description, content, tags=header["tags"])
        except SkillExistsError:
            skill_service.update(
                item.name,
                description=item.description,
                content=content,
                tags=header["tags"],
            )
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO skill_registry VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(skill_name) DO UPDATE SET
                    kind=excluded.kind,scope=excluded.scope,version=excluded.version,
                    enabled=excluded.enabled,entrypoint=excluded.entrypoint,
                    requires_json=excluded.requires_json,updated_at=excluded.updated_at""",
                (
                    item.name,
                    item.kind.value,
                    item.scope,
                    item.version,
                    int(item.enabled),
                    item.entrypoint,
                    json.dumps(item.requires, ensure_ascii=False),
                    utc_now(),
                ),
            )
        return self.get_skill(item.name)

    def set_skill_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        with self.store.connection() as conn:
            changed = conn.execute(
                "UPDATE skill_registry SET enabled=?,updated_at=? WHERE skill_name=?",
                (int(enabled), utc_now(), name),
            ).rowcount
        if not changed:
            raise KeyError(name)
        service = get_skill_service()
        detail = service.get_detail(name)
        meta, body = service._parse_frontmatter(detail.content)  # noqa: SLF001
        meta["enabled"] = enabled
        content = f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()}\n---\n\n{body.lstrip()}"
        service.update(name, content=content)
        return self.get_skill(name)

    def get_skill(self, name: str) -> dict[str, Any]:
        row = self.store.row("SELECT * FROM skill_registry WHERE skill_name=?", (name,))
        if row is None:
            raise KeyError(name)
        result = _decode_json(row, "requires_json")
        if not result["enabled"]:
            result["available"] = False
            result["missing_dependencies"] = ["disabled"]
        else:
            summary = next((x for x in get_skill_service().summary_entries() if x.name == name), None)
            result["available"] = bool(summary and summary.available)
            result["missing_dependencies"] = list(summary.missing if summary else ["skill-file"])
        return result

    def list_skills(self) -> list[dict[str, Any]]:
        return [self.get_skill(row["skill_name"]) for row in self.store.rows("SELECT skill_name FROM skill_registry ORDER BY skill_name")]

    def record_skill_run(self, name: str, item: SkillRunCreate) -> dict[str, Any]:
        self.get_skill(name)
        run_id, now = _id("skillrun"), utc_now()
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO skill_runs VALUES(?,?,?,?,?,?)",
                (run_id, name, int(item.success), item.session_id, item.detail, now),
            )
        return _decode_json(self.store.row("SELECT * FROM skill_runs WHERE id=?", (run_id,)) or {})

    # ── Legacy import, backup, and overview ────────────────────────────

    def import_legacy(self, workspace: dict[str, Any], source_name: str) -> dict[str, Any]:
        if not isinstance(workspace.get("courses"), list):
            raise ValueError("不是有效的 A-leggo 备份：缺少 courses 数组")
        backup_path = self.store.write_json_backup(workspace, source_name)
        counts = {
            "courses": 0,
            "evidence": 0,
            "method_cards": 0,
            "notes": 0,
            "styles": 0,
            "companions": 0,
            "skills": 0,
            "preserved_records": 0,
        }
        warnings: list[str] = []
        # Credentials are deliberately never imported into the database.
        agent = workspace.get("agentConfig") or {}
        if any(agent.get(key) for key in ("key", "apiKey", "token", "secret")):
            warnings.append("旧备份中的模型凭据已忽略；请在安全设置中重新配置。")
        self._preserve_legacy_records(workspace, counts)
        counts["courses"] = self._import_legacy_courses(workspace)
        for raw in workspace.get("evidence") or []:
            if not isinstance(raw, dict):
                continue
            kind = self._legacy_evidence_kind(raw)
            concept_id = str(raw.get("kpId") or raw.get("conceptId") or raw.get("topic") or "").strip()
            if not concept_id:
                continue
            correct = raw.get("correct")
            if correct is None and "score" in raw:
                correct = float(raw.get("score") or 0) >= 0.6
            self.add_evidence(
                EvidenceCreate(
                    concept_id=concept_id,
                    course_id=str(raw.get("courseId") or ""),
                    kind=kind,
                    correct=correct if isinstance(correct, bool) else None,
                    confidence=0.75,
                    source_type="legacy_v3",
                    source_id=str(raw.get("sourceId") or raw.get("chatId") or ""),
                    error_tag=str(raw.get("errorTag") or raw.get("errorType") or ""),
                    note=str(raw.get("note") or raw.get("summary") or "")[:4000],
                    metadata={"legacy_id": str(raw.get("id") or "")},
                )
            )
            counts["evidence"] += 1
        for raw in workspace.get("methodCards") or []:
            if not isinstance(raw, dict):
                continue
            problem_type = str(raw.get("title") or raw.get("problemType") or raw.get("type") or "").strip()
            insight = str(raw.get("eye") or raw.get("keyInsight") or raw.get("insight") or "").strip()
            reaction = str(raw.get("reaction") or raw.get("nextFirstReaction") or "").strip()
            if not (problem_type and insight and reaction):
                continue
            self.create_method_card(
                MethodCardCreate(
                    course_id=str(raw.get("courseId") or ""),
                    concept_id=str(raw.get("kpId") or raw.get("conceptId") or ""),
                    problem_type=problem_type,
                    key_insight=insight,
                    common_methods=self._as_lines(raw.get("methods") or raw.get("commonMethods")),
                    key_steps=self._as_lines(raw.get("steps") or raw.get("keySteps")),
                    common_errors=self._as_lines(raw.get("errors") or raw.get("commonErrors")),
                    next_first_reaction=reaction,
                    source_id=str(raw.get("sourceId") or raw.get("chatId") or ""),
                    confirmed=True,
                )
            )
            counts["method_cards"] += 1
        for raw in workspace.get("notes") or []:
            if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
                continue
            self.create_note(
                NoteCreate(
                    title=str(raw.get("title")),
                    body=str(raw.get("body") or raw.get("content") or ""),
                    course_id=str(raw.get("courseId") or ""),
                    concept_id=str(raw.get("kpId") or raw.get("conceptId") or ""),
                    references=self._normalise_references(raw.get("refs")),
                )
            )
            counts["notes"] += 1
        active_style_id = str(workspace.get("activeStyleId") or "")
        for raw in workspace.get("customStyles") or []:
            if isinstance(raw, dict) and raw.get("name") and (raw.get("instruction") or raw.get("instructions")):
                self.create_style(
                    StyleCreate(
                        name=str(raw["name"]),
                        instructions=str(raw.get("instruction") or raw.get("instructions")),
                        active=str(raw.get("id") or "") == active_style_id,
                    )
                )
                counts["styles"] += 1
        active_companion_id = str(workspace.get("activeCompanionId") or "")
        active_companion: dict[str, Any] | None = None
        for raw in workspace.get("companions") or []:
            if isinstance(raw, dict) and raw.get("name"):
                legacy_id = str(raw.get("id") or "")
                description = "；".join(
                    part
                    for part in (
                        str(raw.get("role") or "").strip(),
                        str(raw.get("personality") or "").strip(),
                        str(raw.get("collaboration") or "").strip(),
                        str(raw.get("description") or raw.get("prompt") or "").strip(),
                    )
                    if part
                )
                companion = self.create_companion(
                    CompanionCreate(
                        name=str(raw["name"]),
                        description=description,
                        active=legacy_id == active_companion_id,
                    )
                )
                if legacy_id == active_companion_id:
                    active_companion = companion
                counts["companions"] += 1
                for memory in raw.get("memories") or []:
                    text = str(memory.get("text") or "") if isinstance(memory, dict) else str(memory)
                    source = str(memory.get("source") or "") if isinstance(memory, dict) else ""
                    if text.strip():
                        explicit = source == "manual"
                        self.add_companion_memory(
                            CompanionMemoryCreate(
                                companion_id=companion["id"],
                                kind=(MemoryKind.EXPLICIT_FACT if explicit else MemoryKind.MODEL_INFERENCE),
                                content=text,
                                confirmed=explicit,
                            )
                        )
        if active_companion is not None:
            for memory in workspace.get("memories") or []:
                text = str(memory.get("text") or "") if isinstance(memory, dict) else str(memory)
                if text.strip():
                    self.add_companion_memory(
                        CompanionMemoryCreate(
                            companion_id=str(active_companion["id"]),
                            kind=MemoryKind.EXPLICIT_FACT,
                            content=text,
                            confirmed=True,
                        )
                    )
        for raw in workspace.get("skills") or []:
            if not isinstance(raw, dict) or not raw.get("name") or not raw.get("instruction"):
                continue
            slug = re.sub(r"[^a-z0-9-]+", "-", str(raw["name"]).lower()).strip("-")[:64]
            if not slug:
                slug = f"legacy-{counts['skills'] + 1}"
            self.register_skill(SkillRegistration(name=slug, description=f"从 A-leggo V3 导入：{raw['name']}", instructions=str(raw["instruction"]), enabled=raw.get("enabled") is not False))
            counts["skills"] += 1
        import_id = _id("import")
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO legacy_imports VALUES(?,?,?,?,?,?)",
                (import_id, source_name, str(backup_path), json.dumps(counts), json.dumps(warnings, ensure_ascii=False), utc_now()),
            )
        return {"id": import_id, "backup_path": str(backup_path), "counts": counts, "warnings": warnings}

    def _preserve_legacy_records(
        self,
        workspace: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        """Keep every non-secret V3 collection row even when no V4 table maps it.

        This ledger is the lossless bridge for quiz attempts, mastery snapshots,
        the living book, Co-Writer draft, and curriculum nodes. Endpoint and
        Agent credential objects are deliberately excluded.
        """
        excluded = {"agentConfig", "endpoints"}
        now = utc_now()
        with self.store.connection() as conn:
            for collection, value in workspace.items():
                if collection in excluded:
                    continue
                rows = value if isinstance(value, list) else [value]
                for index, raw in enumerate(rows):
                    if raw is None:
                        continue
                    external_id = (
                        str(raw.get("id") or index)
                        if isinstance(raw, dict)
                        else str(index)
                    )
                    conn.execute(
                        """INSERT INTO legacy_records(collection,external_id,payload_json,imported_at)
                        VALUES(?,?,?,?) ON CONFLICT(collection,external_id)
                        DO UPDATE SET payload_json=excluded.payload_json,imported_at=excluded.imported_at""",
                        (collection, external_id, json.dumps(raw, ensure_ascii=False), now),
                    )
                    counts["preserved_records"] += 1

    @staticmethod
    def _import_legacy_courses(workspace: dict[str, Any]) -> int:
        from aleggo.services.courses import CourseNameConflictError, get_course_service

        service = get_course_service()
        existing_names = {course.name.casefold() for course in service.list_courses()}
        chapters = workspace.get("chapters") if isinstance(workspace.get("chapters"), list) else []
        points = (
            workspace.get("knowledgePoints")
            if isinstance(workspace.get("knowledgePoints"), list)
            else []
        )
        imported = 0
        for raw in workspace.get("courses") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or raw.get("title") or "").strip()
            if not name or name.casefold() in existing_names:
                continue
            course_id = str(raw.get("id") or "")
            syllabus: list[dict[str, Any]] = []
            for chapter in chapters:
                if not isinstance(chapter, dict) or str(chapter.get("courseId") or "") != course_id:
                    continue
                chapter_id = str(chapter.get("id") or "")
                topics = [
                    str(point.get("title") or "").strip()
                    for point in points
                    if isinstance(point, dict)
                    and str(point.get("chapterId") or "") == chapter_id
                    and str(point.get("title") or "").strip()
                ]
                syllabus.append(
                    {
                        "id": chapter_id or _id("unit"),
                        "title": str(chapter.get("title") or "章节"),
                        "topics": topics,
                        "covered": False,
                    }
                )
            try:
                created = service.create(
                    name=name,
                    description=str(raw.get("description") or raw.get("desc") or "")[:300],
                    default_capability="aleggo_tutor",
                )
                if syllabus:
                    service.set_syllabus(created.id, syllabus)
                existing_names.add(name.casefold())
                imported += 1
            except (CourseNameConflictError, ValueError):
                continue
        return imported

    @staticmethod
    def _as_lines(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [line.strip(" -•\t") for line in str(value or "").splitlines() if line.strip()]

    @staticmethod
    def _normalise_references(value: Any) -> list[dict[str, str]]:
        """Preserve readable legacy note links without violating the V4 schema."""
        if not isinstance(value, list):
            return []
        references: list[dict[str, str]] = []
        for item in value:
            if isinstance(item, dict):
                cleaned = {
                    str(key): str(item_value)
                    for key, item_value in item.items()
                    if isinstance(key, str) and isinstance(item_value, (str, int, float))
                }
                if cleaned:
                    references.append(cleaned)
            elif isinstance(item, str) and item.strip():
                references.append({"label": item.strip()})
        return references[:100]

    @staticmethod
    def _legacy_evidence_kind(raw: dict[str, Any]) -> EvidenceKind:
        text = str(raw.get("kind") or raw.get("type") or "").lower()
        aliases = {
            "read": EvidenceKind.READING,
            "reading": EvidenceKind.READING,
            "learn": EvidenceKind.EXPLANATION,
            "explain": EvidenceKind.EXPLANATION,
            "quiz": EvidenceKind.BASIC_PROBLEM,
            "basic": EvidenceKind.BASIC_PROBLEM,
            "transfer": EvidenceKind.TRANSFER_PROBLEM,
            "redo": EvidenceKind.ERROR_REDO,
            "review": EvidenceKind.ACTIVE_RECALL,
            "self": EvidenceKind.SELF_ASSESSMENT,
            "diagnosis": EvidenceKind.DIAGNOSIS_REPAIR,
        }
        return next((kind for key, kind in aliases.items() if key in text), EvidenceKind.EXPLANATION)

    def export_backup(self) -> dict[str, Any]:
        return {
            "product": "A-leggo Next",
            "schemaVersion": 4,
            "exportedAt": utc_now(),
            "evidence": self.list_evidence(limit=100_000),
            "reviews": self.store.rows("SELECT * FROM reviews ORDER BY due_at"),
            "methodCards": self.list_method_cards(True),
            "notes": self.list_notes(),
            "identity": self.list_identity(),
            "skills": self.list_skills(),
            "skillRuns": [
                _decode_json(row) for row in self.store.rows("SELECT * FROM skill_runs ORDER BY created_at")
            ],
            "legacyRecords": [
                {
                    "collection": row["collection"],
                    "external_id": row["external_id"],
                    "payload": json.loads(row["payload_json"]),
                    "imported_at": row["imported_at"],
                }
                for row in self.store.rows(
                    "SELECT * FROM legacy_records ORDER BY collection,external_id"
                )
            ],
        }

    def overview(self) -> dict[str, Any]:
        due = self.due_reviews()
        return {
            "product": "A-leggo Next",
            "version": "4.2.0",
            "due_reviews": due[:8],
            "due_review_count": len(due),
            "weak_points": self.weak_points()[:8],
            "mastery": self.mastery_map()[:50],
            "recent_evidence": self.list_evidence(limit=8),
            "method_card_count": len(self.list_method_cards(False)),
            "note_count": len(self.list_notes()),
        }


_instances: dict[str, AleggoService] = {}


def get_aleggo_service() -> AleggoService:
    root = (get_path_service().get_workspace_dir() / "aleggo").resolve()
    key = str(root)
    if key not in _instances:
        _instances[key] = AleggoService(AleggoStore(root))
    return _instances[key]


__all__ = ["AleggoService", "EVIDENCE_WEIGHTS", "get_aleggo_service"]
