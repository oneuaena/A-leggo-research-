"""Durable lifecycle, literature, reproducibility, and workspace services."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
import threading
from typing import Any
from uuid import uuid4
import zipfile
from io import BytesIO

from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_store import ResearchStore


# Serializes checkpoint read-modify-write cycles (save_checkpoint,
# reset_stalled_attempt, charge_step): without it a periodic save racing a
# charge_step could silently drop the other's keys (lost update).
_CHECKPOINT_LOCK = threading.RLock()
from aleggo.study.research_tool_catalog import SYMBOLIC_TOOLS


SUPERVISION_MODES = {"AUTONOMOUS", "MILESTONE_REVIEW", "STEP_BY_STEP"}
_MEANINGFUL_SIGNALS = {
    "new_evidence", "new_hypothesis", "new_counterexample", "new_literature",
    "new_experiment", "new_lemma", "new_route", "new_information", "information_gain",
}


def _safe_workspace_relative(relative: str) -> str:
    """Reject absolute / parent-escaping relative paths used for workspace writes."""
    from pathlib import PurePosixPath
    rel = str(relative or "").replace("\\", "/").strip().lstrip("./")
    if not rel or rel.startswith("/") or rel.startswith("~") or ".." in PurePosixPath(rel).parts:
        raise ValueError("工作区相对路径无效。")
    return rel


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return prefix + "_" + uuid4().hex


def _safe_json(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw) if raw else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _resolve_existing_dir(folder: str) -> Path:
    """Resolve a workspace folder; missing paths become a Chinese ValueError."""
    try:
        return Path(folder).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("所选文件夹不存在或无法访问，请重新选择项目工作区文件夹。") from exc


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _normalized_route(text: str) -> str:
    value = re.sub(r"(?im)^\s*(?:attempt|route|method|proof attempt|route tried)\s*:\s*", "", text)
    value = re.sub(r"```[\s\S]*?```", " [code] ", value)
    value = re.sub(r"\s+", " ", value.casefold()).strip()
    return value[:8000]


def _route_similarity(left: str, right: str) -> float:
    a = set(re.findall(r"[\w\u3400-\u9fff]+", left.casefold()))
    b = set(re.findall(r"[\w\u3400-\u9fff]+", right.casefold()))
    if not a or not b:
        return 1.0 if left == right else 0.0
    jaccard = len(a & b) / len(a | b)
    return max(jaccard, SequenceMatcher(None, left, right).ratio())


def evaluate_stagnation(
    attempts: list[dict[str, Any]], *, required_tools_available: bool = True,
    required_tool_reason: str = "",
) -> dict[str, Any]:
    """Classify BLOCKED separately from STALLED and never equate a failed proof with stagnation."""
    if not required_tools_available:
        return {"state": "BLOCKED", "reason": required_tool_reason or "A required research tool is unavailable.",
                "attempts": len(attempts), "informationGain": "none"}
    recent = attempts[-5:]
    if any(any(bool(value) for key, value in (item.get("signals") or {}).items() if key in _MEANINGFUL_SIGNALS) for item in recent):
        return {"state": "CONTINUE", "reason": "Recent work added evidence, a route, a claim, literature, or an experiment.",
                "attempts": len(recent), "informationGain": "observed"}
    if len(recent) < 5:
        return {"state": "CONTINUE", "reason": "The repeated-route threshold has not been reached.",
                "attempts": len(recent), "informationGain": "not-yet-assessed"}
    routes = [_normalized_route(str(item.get("route") or item.get("content") or "")) for item in recent]
    similarities = [_route_similarity(routes[i], routes[j]) for i in range(len(routes)) for j in range(i + 1, len(routes))]
    errors = [str((item.get("signals") or {}).get("error_signature") or "") for item in recent]
    repeated_error = bool(errors and errors[0] and len(set(errors)) == 1)
    repeated_route = bool(similarities and min(similarities) >= 0.72)
    if repeated_error or repeated_route or len(recent) >= 5:
        return {"state": "STALLED", "reason": ("Five consecutive attempts repeated the same tool error without new information." if repeated_error else "Five consecutive attempts repeated essentially the same route without new information." if repeated_route else "Five consecutive attempts produced no new substantive information."),
                "attempts": len(recent), "informationGain": "near-zero", "routeSimilarity": round(sum(similarities) / len(similarities), 3)}
    return {"state": "CONTINUE", "reason": "Recent attempts explore materially different routes.",
            "attempts": len(recent), "informationGain": "route-change"}


def evidence_classification(tool_result: str, agent_result: str, sources: list[dict[str, Any]], *, step_id: str = "", claim_id: str = "") -> str:
    # Only machine-produced tool records can elevate an evidence class. Agent
    # prose is intentionally ignored here, even if it claims a proof passed.
    try:
        record = json.loads(tool_result) if tool_result else {}
    except (TypeError, json.JSONDecodeError):
        record = {}
    if not isinstance(record, dict):
        record = {}
    status = str(record.get("status") or "").upper()
    level = str(record.get("verificationLevel") or "").upper()
    tool = str(record.get("tool") or "").casefold()
    exit_code = record.get("workerExitCode")
    output = record.get("output")
    try:
        output_payload = json.loads(output) if isinstance(output, str) else output
    except (TypeError, json.JSONDecodeError):
        output_payload = {}
    if (status == "COUNTEREXAMPLE_FOUND" and record.get("ok") is True and exit_code == 0
            and isinstance(output_payload, dict) and any(key in output_payload for key in ("witness", "counterexample", "value", "model"))):
        return "COUNTEREXAMPLE_FOUND"
    if (tool in {"lean_kernel", "lean-kernel"} and record.get("ok") is True and exit_code == 0
            and status == "KERNEL_CERTIFIED" and record.get("proofHash")
            and record.get("mappingAuthority") == "USER_CONFIRMED" and record.get("bindingHash")
            and claim_id and record.get("claimId") == claim_id):
        return "FORMALLY_VERIFIED"
    if (tool in {"symbolic_checker", "symbolic-checker"} and record.get("ok") is True and exit_code == 0
            and (status == "SYMBOLICALLY_VERIFIED" or level == "SYMBOLICALLY_VERIFIED")):
        return "SYMBOLICALLY_VERIFIED"
    # The worker verifies this symbolic operation, not every claim in a step.
    # Proposed mathematical conclusions remain separate CONJECTURAL nodes.
    if (tool in SYMBOLIC_TOOLS
            and record.get("ok") is True and exit_code == 0 and level == "SYMBOLIC_CHECK"
            and isinstance(record.get("input"), dict) and bool(record.get("output"))):
        return "SYMBOLICALLY_VERIFIED"
    if (status in {"CONTRADICTED", "REFUTED"} and record.get("ok") is True and exit_code == 0
            and isinstance(output_payload, dict) and any(key in output_payload for key in ("witness", "counterexample", "value", "model"))):
        return "CONTRADICTED"
    if record.get("ok") is True and exit_code == 0 and level == "BOUNDED_CHECK":
        if isinstance(output_payload, dict) and any(key in output_payload for key in ("casesChecked", "cases_checked", "checkedCases")):
            return "NUMERICALLY_OBSERVED"
    scoped = [item for item in sources if item.get("readingStatus") == "read" and
              ((claim_id and item.get("scopeClaimId") == claim_id) or
               (step_id and item.get("scopeStepId") == step_id))]
    if any(item.get("relationship") in {"CONTRADICTS", "REFUTES"} for item in scoped):
        return "CONTRADICTED"
    if any(item.get("relationship") == "SUPPORTS" for item in scoped):
        return "LITERATURE_SUPPORTED"
    return "CONJECTURAL"


class ResearchWorkbench:
    def __init__(self, store: ResearchStore) -> None:
        self.store = store
        self.domain = ResearchDomainStore(store)

    def _state_row(self, project_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_workbench_state WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError("未找到该研究项目。")
        return dict(row)

    @staticmethod
    def _decode_state(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "supervisionMode": row["supervision_mode"], "currentStepId": row["current_step_id"],
            "currentAgent": row["current_agent"], "currentAction": row["current_action"],
            "currentBranch": row["current_branch"], "currentMilestone": row["current_milestone"],
            "runStartedAt": row["run_started_at"], "statusReason": row["status_reason"],
            "cumulativeRunSeconds": row.get("cumulative_run_seconds"),
            "activeRunStartedAt": row.get("active_run_started_at"),
            "blockerType": row["blocker_type"], "blockerDetail": row["blocker_detail"],
            "stalledAttempts": row["stalled_attempts"], "llmCalls": row["llm_calls"],
            "toolCalls": row["tool_calls"], "failures": row["failures"],
            "lastProgressAt": row["last_progress_at"], "workspaceRoot": row["workspace_root"],
            "workspaceStatus": row["workspace_status"], "updatedAt": row["updated_at"],
            "currentTool": row.get("current_tool", ""),
            "activeAgents": _safe_json(row.get("active_agents_json", "[]") or "[]", []),
            "stepIndex": row.get("step_index", 0), "stepTotal": row.get("step_total", 0),
            "milestoneIndex": row.get("milestone_index", 0), "milestoneTotal": row.get("milestone_total", 0),
            "phase": row.get("phase", ""),
            "lastBudgetEvaluation": _safe_json(row.get("last_budget_evaluation_json", "{}") or "{}", {}),
        }

    def state(self, project_id: str) -> dict[str, Any]:
        return self._decode_state(self._state_row(project_id))

    def _update_state(self, project_id: str, **fields: Any) -> None:
        allowed = {
            "supervision_mode", "current_step_id", "current_agent", "current_action", "current_branch",
            "current_milestone", "run_started_at", "status_reason", "blocker_type", "blocker_detail",
            "stalled_attempts", "llm_calls", "tool_calls", "failures", "last_progress_at",
            "last_fingerprint", "workspace_root", "workspace_status",
            "current_tool", "active_agents_json", "step_index", "step_total", "milestone_index", "milestone_total", "phase", "last_budget_evaluation_json",
        }
        assignments = {key: value for key, value in fields.items() if key in allowed}
        assignments["updated_at"] = _now()
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE research_workbench_state SET " + ",".join(f"{key}=?" for key in assignments) + " WHERE project_id=?",
                (*assignments.values(), project_id),
            )

    def configure_supervision(self, project_id: str, mode: str) -> dict[str, Any]:
        mode = mode.upper()
        if mode not in SUPERVISION_MODES:
            raise ValueError("请选择 AUTONOMOUS、MILESTONE_REVIEW 或 STEP_BY_STEP。")
        project = self.store.get_project(project_id)
        if not project:
            raise KeyError("未找到该研究项目。")
        if project["status"] in {"RUNNING", "VERIFYING"}:
            raise ValueError("更改督导模式前请先暂停研究。")
        self._update_state(project_id, supervision_mode=mode)
        self.record_event(project_id, "supervision_mode_changed", "researcher", summary=f"Supervision mode set to {mode}.", details={"mode": mode})
        self.record_decision(project_id, "supervision_mode", f"Researcher selected {mode} supervision.", source="researcher")
        return self.state(project_id)

    def record_event(self, project_id: str, event_type: str, actor: str, *, step_id: str = "",
                     branch: str = "", summary: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {"id": _id("timeline"), "eventType": event_type, "actor": actor, "stepId": step_id,
                 "branch": branch, "summary": summary[:2000], "details": details or {}, "createdAt": _now()}
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO research_timeline(id,project_id,event_type,actor,step_id,branch_name,summary,details_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (event["id"], project_id, event_type[:100], actor[:100], step_id[:200], branch[:120], event["summary"], _json(details or {}), event["createdAt"]),
            )
        return event

    def list_timeline(self, project_id: str, limit: int = 250) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM research_timeline WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, max(1, min(limit, 1000)))).fetchall()
        return [{"id": r["id"], "eventType": r["event_type"], "actor": r["actor"], "stepId": r["step_id"],
                 "branch": r["branch_name"], "summary": r["summary"], "details": _safe_json(r["details_json"] or "{}", {}),
                 "createdAt": r["created_at"]} for r in rows]

    def record_decision(self, project_id: str, decision: str, rationale: str, *, actor: str = "researcher",
                        source: str = "researcher", branch: str = "", artifact_id: str | None = None) -> dict[str, Any]:
        item = {"id": _id("decision"), "decision": decision[:200], "actor": actor[:100],
                "rationale": rationale[:8000], "source": source[:300], "branch": branch[:120],
                "artifactId": artifact_id, "createdAt": _now()}
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO research_decisions(id,project_id,decision,actor,rationale,source,branch_name,related_artifact_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (item["id"], project_id, item["decision"], item["actor"], item["rationale"], item["source"], item["branch"], artifact_id, item["createdAt"]),
            )
        self.record_event(project_id, "decision_recorded", actor, branch=branch, summary=decision, details={"rationale": rationale, "source": source, "artifactId": artifact_id})
        return item

    def list_decisions(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM research_decisions WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [{"id": r["id"], "decision": r["decision"], "actor": r["actor"], "rationale": r["rationale"],
                 "source": r["source"], "branch": r["branch_name"], "artifactId": r["related_artifact_id"],
                 "createdAt": r["created_at"]} for r in rows]

    def add_recommendation(self, project_id: str, step_id: str, content: dict[str, Any]) -> dict[str, Any]:
        now, rec_id = _now(), _id("recommendation")
        for key in ("primary", "secondary", "deprioritize"):
            content.setdefault(key, [])
            if not isinstance(content[key], list):
                content[key] = [str(content[key])]
        with self.store.connection() as conn:
            conn.execute("INSERT INTO research_recommendations(id,project_id,step_id,content_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (rec_id, project_id, step_id[:200], _json(content), "PENDING", now, now))
        self.record_event(project_id, "next_path_recommended", "research-synthesizer", step_id=step_id,
                          summary=str(content.get("summary") or "建议的下一步研究路径。"), details=content)
        return {"id": rec_id, "stepId": step_id, "content": content, "status": "PENDING", "createdAt": now}

    def list_recommendations(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM research_recommendations WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [{"id": r["id"], "stepId": r["step_id"], "content": _safe_json(r["content_json"], {}),
                 "status": r["status"], "createdAt": r["created_at"], "updatedAt": r["updated_at"]} for r in rows]

    def decide_recommendation(self, project_id: str, recommendation_id: str, action: str,
                              rationale: str = "", modified: dict[str, Any] | None = None) -> dict[str, Any]:
        statuses = {"accept": "ACCEPTED", "modify": "MODIFIED", "reject": "REJECTED", "alternative": "ALTERNATIVE", "add_human_idea": "HUMAN_IDEA"}
        if action not in statuses:
            raise ValueError("请选择 accept、modify、reject 或 alternative。")
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_recommendations WHERE id=? AND project_id=?", (recommendation_id, project_id)).fetchone()
            if not row:
                raise KeyError("未找到该研究路径建议。")
            conn.execute("UPDATE research_recommendations SET status=?,updated_at=? WHERE id=?", (statuses[action], _now(), recommendation_id))
        decision_text = f"{action.title()} recommended research path"
        self.record_decision(project_id, decision_text, rationale or _json(modified or _safe_json(row["content_json"], {})), source="recommendation:" + recommendation_id)
        if action in {"modify", "alternative", "add_human_idea"} and modified:
            self.add_recommendation(project_id, row["step_id"], modified)
        # Acceptance only records intent; the approved plan remains unchanged until the researcher edits and re-approves it.
        return {"id": recommendation_id, "status": statuses[action], "planChanged": False}

    def observe_attempt(self, project_id: str, step_id: str, route: str, signals: dict[str, Any],
                        *, required_tools_available: bool = True, required_tool_reason: str = "") -> dict[str, Any]:
        clean = _normalized_route(route)
        with self.store.connection() as conn:
            # A deliberate Resume Anyway starts a fresh stagnation window,
            # while the original attempts remain in the research record.
            override = conn.execute(
                "SELECT created_at FROM research_decisions WHERE project_id=? AND source='explicit_resume_anyway' ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            since_override = override["created_at"] if override else ""
            previous = conn.execute(
                "SELECT content_text FROM research_progress_attempts WHERE project_id=? AND created_at>? ORDER BY created_at DESC LIMIT 5",
                (project_id, since_override),
            ).fetchall()
            conn.execute("INSERT INTO research_progress_attempts(id,project_id,step_id,route_signature,content_text,signals_json,created_at) VALUES(?,?,?,?,?,?,?)",
                         (_id("progress"), project_id, step_id[:200], hashlib.sha256(clean.encode()).hexdigest(), clean[:8000], _json(signals), _now()))
            rows = conn.execute(
                "SELECT step_id,content_text,signals_json FROM research_progress_attempts WHERE project_id=? AND created_at>? ORDER BY created_at DESC LIMIT 5",
                (project_id, since_override),
            ).fetchall()
        attempts = [{"stepId": r["step_id"], "route": r["content_text"], "signals": _safe_json(r["signals_json"] or "{}", {})} for r in reversed(rows)]
        verdict = evaluate_stagnation(attempts, required_tools_available=required_tools_available,
                                      required_tool_reason=required_tool_reason)
        state = self._state_row(project_id)
        self._update_state(project_id, stalled_attempts=state["stalled_attempts"] + 1 if verdict["state"] == "STALLED" else 0,
                           last_fingerprint=hashlib.sha256(clean.encode()).hexdigest(),
                           last_progress_at=_now() if any(bool(signals.get(key)) for key in _MEANINGFUL_SIGNALS) else state["last_progress_at"],
                           failures=state["failures"] + (1 if signals.get("failed_attempt") else 0))
        self.record_event(project_id, "progress_evaluated", "budget-guard", step_id=step_id,
                          summary=verdict["state"] + ": " + verdict["reason"], details={"signals": signals, **verdict})
        return verdict

    def checkpoint(self, project_id: str, version: int, step_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_step_checkpoints WHERE project_id=? AND plan_version=? AND step_id=?", (project_id, version, step_id)).fetchone()
        if not row:
            return None
        value = dict(row)
        for name in ("payload", "resources", "signals"):
            # Dirty data must not take down the whole workbench: fall back to {}.
            value[name] = _safe_json(value.get(name + "_json") or "{}", {})
        return value

    def save_checkpoint(self, project_id: str, version: int, step_id: str, *, phase: str | None = None,
                        status: str | None = None, payload: dict[str, Any] | None = None,
                        resources: dict[str, Any] | None = None, signals: dict[str, Any] | None = None,
                        milestone: str | None = None) -> dict[str, Any]:
        with _CHECKPOINT_LOCK:
            return self._save_checkpoint_locked(project_id, version, step_id, phase=phase, status=status,
                                                payload=payload, resources=resources, signals=signals,
                                                milestone=milestone)

    def _save_checkpoint_locked(self, project_id: str, version: int, step_id: str, *, phase: str | None = None,
                                status: str | None = None, payload: dict[str, Any] | None = None,
                                resources: dict[str, Any] | None = None, signals: dict[str, Any] | None = None,
                                milestone: str | None = None) -> dict[str, Any]:
        current = self.checkpoint(project_id, version, step_id) or {}
        now = _now()
        merged = {**current.get("payload", {}), **(payload or {})}
        with self.store.connection() as conn:
            conn.execute("INSERT INTO research_step_checkpoints(project_id,plan_version,step_id,phase,status,payload_json,resources_json,signals_json,milestone_key,started_at,completed_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,plan_version,step_id) DO UPDATE SET phase=excluded.phase,status=excluded.status,payload_json=excluded.payload_json,resources_json=excluded.resources_json,signals_json=excluded.signals_json,milestone_key=excluded.milestone_key,completed_at=excluded.completed_at,updated_at=excluded.updated_at",
                         (project_id, version, step_id, phase or current.get("phase", "pending"), status or current.get("status", "ACTIVE"), _json(merged), _json(resources if resources is not None else current.get("resources", {})), _json(signals if signals is not None else current.get("signals", {})), milestone if milestone is not None else current.get("milestone_key", ""), current.get("started_at") or now, now if status == "COMPLETED" else current.get("completed_at", ""), now))
        return self.checkpoint(project_id, version, step_id) or {}

    def begin_step(self, project_id: str, version: int, step: dict[str, Any], milestone: str) -> dict[str, Any]:
        existing = self.checkpoint(project_id, version, step["id"])
        if existing:
            return existing
        expected = {"llmCalls": 16, "toolCalls": 6, "elapsedSeconds": 180, "failedAttempts": 5}
        expected.update({key: max(1, float(value)) for key, value in (step.get("researchBudget") or {}).items() if key in expected and isinstance(value, (int, float)) and not isinstance(value, bool)})
        return self.save_checkpoint(project_id, version, step["id"], phase="execution", milestone=milestone,
                                    payload={"expectedBudget": expected}, resources={"llmCalls": 0, "toolCalls": 0, "elapsedSeconds": 0, "failedAttempts": 0})

    def charge_step(self, project_id: str, version: int, step_id: str, *, llm_calls: int = 0,
                    tool_calls: int = 0, elapsed_seconds: float = 0, failed: bool = False) -> None:
        with _CHECKPOINT_LOCK:
            current = self.checkpoint(project_id, version, step_id) or {}
            resources = dict(current.get("resources", {}))
            for key, amount in (("llmCalls", llm_calls), ("toolCalls", tool_calls), ("workerSeconds", max(0, elapsed_seconds)), ("failedAttempts", int(failed))):
                resources[key] = resources.get(key, 0) + amount
            self._save_checkpoint_locked(project_id, version, step_id, resources=resources)
        if llm_calls or tool_calls:
            self.bump_usage(project_id, llm_calls=llm_calls, tool_calls=tool_calls)
        if failed:
            state = self._state_row(project_id)
            self._update_state(project_id, failures=state["failures"] + 1)

    def register_progress(self, project_id: str, kind: str, value: Any, source_id: str = "") -> bool:
        canonical = _normalized_route(value) if isinstance(value, str) else _json(value)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.store.connection() as conn:
            route_record = kind == "new_route" or kind.startswith("negative_route:")
            if route_record:
                # Bound the similarity scan: comparing against the whole history
                # is O(n) SequenceMatcher work per registration.
                rows = conn.execute("SELECT source_id FROM research_progress_fingerprints WHERE project_id=? AND signal_kind=? ORDER BY created_at DESC LIMIT 200", (project_id, kind)).fetchall()
                if any(_route_similarity(canonical, str(row["source_id"])) >= 0.72 for row in rows):
                    return False
            cursor = conn.execute("INSERT OR IGNORE INTO research_progress_fingerprints(project_id,signal_kind,fingerprint,source_id,created_at) VALUES(?,?,?,?,?)", (project_id, kind, digest, canonical[:8000] if route_record else source_id[:300], _now()))
        inserted = cursor.rowcount > 0
        if inserted:
            project = self.store.get_project(project_id)
            state = self.state(project_id)
            if project and state["currentStepId"]:
                checkpoint = self.checkpoint(project_id, project["currentVersion"], state["currentStepId"])
                if checkpoint and checkpoint["status"] != "COMPLETED":
                    self.save_checkpoint(project_id, project["currentVersion"], state["currentStepId"], payload={"informationRecords": checkpoint["payload"].get("informationRecords", 0) + 1})
            if kind in _MEANINGFUL_SIGNALS:
                self._update_state(project_id, last_progress_at=_now())
        return inserted

    def evaluate_budget(self, project_id: str, version: int, step_id: str, *, force: bool = False) -> dict[str, Any]:
        current = self.checkpoint(project_id, version, step_id) or {}
        payload, resources, gain = current.get("payload", {}), current.get("resources", {}), current.get("signals", {})
        expected = payload.get("expectedBudget", {"llmCalls": 16, "toolCalls": 6, "elapsedSeconds": 180, "failedAttempts": 5})
        baseline = payload.get("budgetBaseline", {})
        exceeded = [key for key, limit in expected.items() if resources.get(key, 0) - baseline.get(key, 0) >= limit]
        if not exceeded and not force:
            return {"state": "CONTINUE", "evaluated": False, "resources": resources}
        observed = payload.get("informationRecords", 0) > payload.get("budgetInformationBaseline", 0) or (force and any(bool(gain.get(key)) for key in _MEANINGFUL_SIGNALS))
        result = {"state": "CONTINUE" if observed or not exceeded else "STALLED", "evaluated": True,
                  "resources": resources, "gain": gain, "thresholdsReached": exceeded,
                  "reason": "Resource evaluation found newly persisted substantive information." if observed else "Step resources exceeded expectations without newly persisted substantive information." if exceeded else "Step completed within its expected resource budget."}
        with self.store.connection() as conn:
            conn.execute("INSERT INTO research_budget_evaluations(id,project_id,plan_version,step_id,state,resources_json,gain_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (_id("budget"), project_id, version, step_id, result["state"], _json(resources), _json(gain), result["reason"], _now()))
        self._update_state(project_id, last_budget_evaluation_json=_json(result))
        if observed and exceeded:
            self.save_checkpoint(project_id, version, step_id, payload={"budgetBaseline": resources, "budgetInformationBaseline": payload.get("informationRecords", 0)})
        self.record_event(project_id, "budget_evaluated", "budget-guard", step_id=step_id, summary=result["reason"], details=result)
        return result

    def reset_stalled_attempt(self, project_id: str, version: int, step_id: str) -> None:
        with _CHECKPOINT_LOCK:
            current = self.checkpoint(project_id, version, step_id)
            if not current:
                return
            saved = current["payload"]
            history = list(saved.get("attemptHistory", []))
            # Compact record: the full payload is already superseded; keeping
            # every past payload made payload_json grow linearly with stalls.
            history.append({
                "attempt": len(history) + 1,
                "resetAt": _now(),
                "phase": current.get("phase", ""),
                "status": current.get("status", ""),
                "resources": current.get("resources", {}),
            })
            history = history[-10:]
            fresh = {"attemptHistory": history, "expectedBudget": saved.get("expectedBudget", {}), "budgetReserved": True, "budgetBaseline": current["resources"], "budgetInformationBaseline": saved.get("informationRecords", 0), "informationRecords": saved.get("informationRecords", 0)}
            with self.store.connection() as conn:
                conn.execute("UPDATE research_step_checkpoints SET payload_json=?,signals_json='{}',phase='execution',status='ACTIVE',completed_at='',updated_at=? WHERE project_id=? AND plan_version=? AND step_id=?", (_json(fresh), _now(), project_id, version, step_id))

    def complete_milestone(self, project_id: str, version: int, milestone: str, step_ids: list[str]) -> dict[str, Any]:
        key = "milestone:" + hashlib.sha256(milestone.encode()).hexdigest()[:20]
        existing = self.checkpoint(project_id, version, key)
        if existing and existing["payload"].get("artifactId"):
            return existing["payload"]
        artifacts = [a for a in self.store.list_artifacts(project_id) if a["planVersion"] == version and a["stepId"] in step_ids]
        graph = self.domain.list_graph(project_id)
        sources = self.domain.list_sources(project_id)
        recommendations = [r for r in self.list_recommendations(project_id) if r["stepId"] in step_ids]
        failures = self.domain.failed_paths(project_id) if hasattr(self.domain, "failed_paths") else [a for a in artifacts if a["status"] == "failed"]
        # Compact snapshot: IDs and counts only. The full graph, sources and
        # recommendations stay queryable by ID; duplicating them here (and
        # again in the checkpoint payload) made every milestone repeat the
        # entire evidence store.
        nodes = graph.get("nodes", [])
        snapshot = {"milestone": milestone, "stepIds": step_ids, "artifactIds": [a["id"] for a in artifacts],
                    "evidence": {"nodeIds": [n.get("id") for n in nodes], "nodeCount": len(nodes),
                                 "edgeCount": len(graph.get("edges", [])),
                                 "verifiedCount": sum(1 for n in nodes if str(n.get("status") or "").casefold() in {"verified", "kernel_certified"})},
                    "literature": {"sourceIds": [s["id"] for s in sources], "count": len(sources)},
                    "failedPaths": failures if len(failures) <= 20 else failures[:20],
                    "recommendations": {"ids": [r.get("id") for r in recommendations], "count": len(recommendations)},
                    "capturedAt": _now()}
        content = "# Milestone report: " + milestone + "\n\n## Completed research steps\n\n" + "\n".join("- " + value for value in step_ids)
        content += "\n\n## Stage findings and reports\n\n" + "\n\n".join(a["content"] for a in artifacts if a.get("metadata", {}).get("artifactKind") == "step-report")
        content += "\n\n## Evidence snapshot, literature update, failed paths and next actions\n\n```json\n" + json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n```\n"
        artifact = self.store.create_artifact(project_id, version, key, "milestone-controller", "Milestone · " + milestone, content, status="MILESTONE_REPORT", metadata={"artifactKind": "milestone-report", **snapshot})
        result = {"artifactId": artifact["id"], "snapshot": snapshot}
        self.save_checkpoint(project_id, version, key, phase="review", status="COMPLETED", payload=result, milestone=milestone)
        self.record_event(project_id, "milestone_report_created", "milestone-controller", summary="Saved milestone report: " + milestone, details={"artifactId": artifact["id"], "stepIds": step_ids})
        return result

    def set_live(self, project_id: str, *, step_id: str = "", agent: str = "", action: str = "",
                 branch: str = "main", milestone: str = "", reason: str | None = None,
                 blocker_type: str = "", blocker_detail: str = "", clear_run_start: bool = False,
                 tool: str = "", active_agents: list[str] | None = None, phase: str = "",
                 step_index: int | None = None, step_total: int | None = None,
                 milestone_index: int | None = None, milestone_total: int | None = None) -> None:
        state = self._state_row(project_id)
        fields: dict[str, Any] = {
            "current_step_id": step_id, "current_agent": agent, "current_action": action,
            "current_branch": branch, "current_milestone": milestone,
            "status_reason": state["status_reason"] if reason is None else reason,
            "blocker_type": blocker_type, "blocker_detail": blocker_detail,
            "current_tool": tool, "active_agents_json": _json(active_agents if active_agents is not None else ([agent] if agent else [])), "phase": phase,
        }
        for name, value in (("step_index", step_index), ("step_total", step_total), ("milestone_index", milestone_index), ("milestone_total", milestone_total)):
            if value is not None:
                fields[name] = value
        if not state["run_started_at"] and not clear_run_start:
            fields["run_started_at"] = _now()
        if clear_run_start:
            fields["run_started_at"] = ""
        self._update_state(project_id, **fields)

    def bump_usage(self, project_id: str, *, llm_calls: int = 0, tool_calls: int = 0) -> None:
        state = self._state_row(project_id)
        self._update_state(project_id, llm_calls=state["llm_calls"] + max(0, llm_calls),
                           tool_calls=state["tool_calls"] + max(0, tool_calls))

    def snapshot(self, project_id: str) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if not project:
            raise KeyError("未找到该研究项目。")
        domain = self.domain
        state = self.state(project_id)
        artifacts = self.store.list_artifacts(project_id)
        sources = domain.list_sources(project_id)
        graph = domain.list_graph(project_id)
        with self.store.connection() as conn:
            queued = [{"id": row["id"], "kind": row["intervention_type"], "status": row["status"], "details": _safe_json(row["payload_json"] or "{}", {}), "resultArtifactId": row["result_artifact_id"], "createdAt": row["created_at"]} for row in conn.execute("SELECT * FROM research_intervention_queue WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()]
        current_step = next((s for s in (project.get("plan") or {}).get("steps", []) if s.get("id") == state["currentStepId"]), None)
        checkpoint = self.checkpoint(project_id, project["currentVersion"], state["currentStepId"]) if state["currentStepId"] else None
        resources = (checkpoint or {}).get("resources", {})
        step_elapsed = resources.get("elapsedSeconds", 0)
        if project["status"] in {"RUNNING", "VERIFYING"} and resources.get("activeSegmentStartedAt"):
            try:
                step_elapsed = resources.get("activeSegmentBaseline", 0) + max(0, (datetime.now(UTC) - datetime.fromisoformat(resources["activeSegmentStartedAt"])).total_seconds())
            except (TypeError, ValueError):
                pass
        counts = {
            "artifacts": len(artifacts), "literature": sum(1 for item in sources if item["type"] == "literature"),
            "evidence": sum(1 for item in graph["nodes"] if item["type"] in {"evidence", "experiment", "counterexample"}),
            "counterexamples": sum(1 for item in graph["nodes"] if item["type"] == "counterexample"),
            "failedPaths": sum(1 for item in artifacts if item["status"] == "failed"),
        }
        timeline = self.list_timeline(project_id, limit=1000)
        elapsed, elapsed_kind = self._research_elapsed(state, project["status"], timeline)
        # A truncated history cannot prove that all old run segments were paired.
        if elapsed_kind == "estimated" and len(timeline) >= 1000:
            elapsed, elapsed_kind = None, "unavailable"
        return {"project": project, "state": state, "currentStep": current_step,
                "timeline": timeline, "decisions": self.list_decisions(project_id),
                "recommendations": self.list_recommendations(project_id), "literatureLinks": domain.list_literature_links(project_id),
                "summary": self.state_summary(project, state, graph, sources, artifacts),
                "queuedInterventions": queued,
                "counts": counts, "workspacePath": state["workspaceRoot"], "budget": {
                    "llmCalls": state["llmCalls"], "toolCalls": state["toolCalls"], "failures": state["failures"],
                    "elapsedSeconds": step_elapsed, "projectElapsedSeconds": self._elapsed_seconds(state),
                    "researchElapsedSeconds": elapsed, "researchElapsedKind": elapsed_kind,
                    "stepResources": {**resources, "elapsedSeconds": step_elapsed},
                    "informationGain": "observed" if any(bool((checkpoint or {}).get("signals", {}).get(key)) for key in _MEANINGFUL_SIGNALS) else "none-observed",
                }}

    def state_summary(self, project: dict[str, Any], state: dict[str, Any], graph: dict[str, Any], sources: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
        nodes = graph["nodes"]
        rankings = {"FORMALLY_VERIFIED": 7, "SYMBOLICALLY_VERIFIED": 6, "COUNTEREXAMPLE_FOUND": 5, "NUMERICALLY_OBSERVED": 4, "LITERATURE_SUPPORTED": 3, "CONJECTURAL": 1}
        evidence = [n for n in nodes if (n["type"] in {"evidence", "experiment", "counterexample"}
                    or (n["type"] == "proof_attempt" and n.get("status") == "verified"
                        and n.get("metadata", {}).get("checkStatus") == "KERNEL_CERTIFIED"))
                    and str(n.get("status") or "").casefold() not in {"rejected", "human-marked-invalid", "invalid"}
                    and n.get("metadata", {}).get("humanFeedback") != "invalid"
                    and not n.get("metadata", {}).get("verificationSuspended")]
        evidence.sort(key=lambda n: rankings.get(str(n.get("metadata", {}).get("evidenceClass")
                      or n.get("metadata", {}).get("evidenceClassification")
                      or ("FORMALLY_VERIFIED" if n.get("status") == "verified" and n.get("metadata", {}).get("checkStatus") == "KERNEL_CERTIFIED" else n.get("status"))).upper(), 0), reverse=True)
        failed = self.domain.failed_paths(project["id"]) if hasattr(self.domain, "failed_paths") else [a for a in artifacts if a["status"] == "failed"]
        recommendations = self.list_recommendations(project["id"])
        return {"question": project["question"], "planVersion": project["currentVersion"], "milestone": state["currentMilestone"], "step": state["currentStepId"], "branch": state["currentBranch"], "conjectures": [n for n in nodes if n["type"] == "conjecture"], "lemmas": [n for n in nodes if n["type"] == "lemma"], "strongestEvidence": evidence[:8], "counterexamples": [n for n in nodes if n["type"] == "counterexample"], "failedPaths": failed, "literature": {"papers": sum(s["type"] == "literature" for s in sources), "asOf": max((s.get("retrievedAt") or s.get("createdAt") or "" for s in sources), default=""), "scope": "Currently retrieved project sources"}, "blockers": [state["blockerDetail"]] if state["blockerDetail"] else [], "nextAction": recommendations[0] if recommendations else None}

    @staticmethod
    def _research_elapsed(state: dict[str, Any], status: str, timeline: list[dict[str, Any]]) -> tuple[int | None, str]:
        """Measured for new projects; conservative paired-event estimate for old ones."""
        running = status in {"RUNNING", "VERIFYING"}
        total = state.get("cumulativeRunSeconds")
        if total is not None:
            if running:
                started = state.get("activeRunStartedAt")
                try:
                    if not started:
                        return None, "unavailable"
                    segment = (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
                    if segment < 0:
                        return None, "unavailable"
                    total += segment
                except (ValueError, TypeError):
                    return None, "unavailable"
            return max(0, int(total)), "measured"
        starts = {"research_started"}
        ends = {"research_review_required", "research_stalled", "research_blocked", "final_package_created"}
        decision_starts = {"research_resumed", "resume_anyway_override"}
        decision_ends = {"research_paused", "research_stopped"}
        active = None
        elapsed = 0.0
        found = False
        for event in sorted(timeline, key=lambda item: item["createdAt"]):
            kind, summary = event["eventType"], event["summary"]
            begin = kind in starts or (kind == "decision_recorded" and summary in decision_starts)
            end = kind in ends or (kind == "decision_recorded" and summary in decision_ends)
            if not begin and not end:
                continue
            try:
                timestamp = datetime.fromisoformat(event["createdAt"])
                if begin:
                    if active is not None:
                        return None, "unavailable"
                    active = timestamp
                    found = True
                elif active is not None:
                    seconds = (timestamp - active).total_seconds()
                    if seconds < 0:
                        return None, "unavailable"
                    elapsed += seconds
                    active = None
                elif summary != "research_stopped" or not found:
                    return None, "unavailable"
            except (ValueError, TypeError):
                return None, "unavailable"
        if not found or (active is not None and not running) or (active is None and running):
            return None, "unavailable"
        if active is not None:
            elapsed += max(0, (datetime.now(UTC) - active).total_seconds())
        return int(elapsed), "estimated"

    @staticmethod
    def _elapsed_seconds(state: dict[str, Any]) -> int:
        if not state["runStartedAt"]:
            return 0
        try:
            return max(0, int((datetime.now(UTC) - datetime.fromisoformat(state["runStartedAt"])).total_seconds()))
        except ValueError:
            return 0

    @staticmethod
    def _slug(value: str) -> str:
        clean = re.sub(r"[^\w\u3400-\u9fff.-]+", "-", value, flags=re.UNICODE).strip(".-")[:70]
        return clean or "research"

    def set_workspace(self, project_id: str, folder: str) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if not project:
            raise KeyError("未找到该研究项目。")
        path = _resolve_existing_dir(folder)
        if not path.is_dir():
            raise ValueError("请为本研究项目选择一个已存在的文件夹。")
        try:
            probe = path / ".aleggo-write-check"
            probe.write_text("workspace writable", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise ValueError("A-leggo 无法写入所选文件夹。") from exc
        marker = path / ".aleggo-workspace.json"
        if marker.exists():
            try:
                owner = json.loads(marker.read_text(encoding="utf-8")).get("projectId")
            except (OSError, json.JSONDecodeError):
                owner = None
            if owner not in {None, project_id}:
                raise ValueError("该文件夹已由另一个研究项目管理。")
        previous_state = self.state(project_id)
        self._update_state(project_id, workspace_root=str(path), workspace_status="SYNCING")
        self.record_decision(project_id, "workspace_selected", f"Researcher selected local workspace: {path}", source="native_folder_picker")
        self.record_event(project_id, "workspace_selected", "researcher", summary="Local project workspace selected.", details={"path": str(path)})
        try:
            self.sync_workspace(project_id)
        except (ValueError, OSError):
            self._update_state(project_id, workspace_root=previous_state["workspaceRoot"], workspace_status=previous_state["workspaceStatus"])
            raise
        return self.state(project_id)

    def _workspace_files(self, project_id: str) -> dict[str, bytes]:
        from aleggo.study.research_delivery import ResearchDelivery
        return ResearchDelivery(self.store).files(project_id)

    @staticmethod
    def file_description(name: str, content: bytes) -> dict[str, Any]:
        suffix = Path(name).suffix.lower()
        text_formats = {".md", ".txt", ".json", ".csv", ".py", ".tex", ".lean", ".bib", ".ris", ".yaml", ".yml", ".log", ".smt2", ".sh", ".sha256"}
        kind = "text" if suffix in text_formats else "pdf" if suffix == ".pdf" else "image" if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else "download"
        media = mimetypes.guess_type(name)[0] or ("text/plain" if kind == "text" else "application/octet-stream")
        return {"path": name, "sizeBytes": len(content), "mediaType": media, "previewKind": kind}

    def list_workspace_files(self, project_id: str) -> list[dict[str, Any]]:
        return [self.file_description(name, content) for name, content in sorted(self._workspace_files(project_id).items())]

    def workspace_file(self, project_id: str, name: str) -> bytes:
        # Resolve only an exact member of this project's authoritative export.
        # User paths never become arbitrary filesystem reads.
        files = self._workspace_files(project_id)
        if name not in files:
            raise KeyError("未找到研究工作区文件。")
        return files[name]

    @staticmethod
    def _bibtex(sources: list[dict[str, Any]]) -> str:
        from aleggo.study.research_delivery import ResearchDelivery
        return ResearchDelivery._bibtex(sources)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".aleggo-", dir=path.parent, delete=False) as handle:
            handle.write(data)
            temp = Path(handle.name)
        temp.replace(path)

    def sync_workspace(self, project_id: str) -> dict[str, Any]:
        state = self.state(project_id)
        if not state["workspaceRoot"]:
            return {"status": "UNCONFIGURED", "filesWritten": 0, "path": ""}
        root = _resolve_existing_dir(state["workspaceRoot"])
        marker = root / ".aleggo-workspace.json"
        managed: set[str] = set()
        if marker.exists():
            marker_data = _safe_json(marker.read_text(encoding="utf-8"), {})
            owner = marker_data.get("projectId")
            if owner not in {None, project_id}:
                self._update_state(project_id, workspace_status="ERROR")
                raise ValueError("工作区文件夹归属已变更，已停止同步。")
            managed = set(marker_data.get("managedFiles", []))
            # Earlier workspace mirrors did not record managed files; their
            # checksum manifests identify the files owned by this project.
            if owner == project_id and not managed and (root / "MANIFEST.sha256.json").exists():
                managed = set(json.loads((root / "MANIFEST.sha256.json").read_text(encoding="utf-8")))
                managed.update({"MANIFEST.sha256", "MANIFEST.sha256.json"})
        files = self._workspace_files(project_id)
        for relative in files:
            safe = _safe_workspace_relative(relative)
            target = (root / safe).resolve()
            if not target.is_relative_to(root):
                raise ValueError("工作区文件路径超出所选项目文件夹。")
            if target.exists() and relative not in managed:
                raise ValueError(f"所选文件夹包含未托管文件：{relative}。请选择空的项目文件夹。")
        marker_value = {"projectId": project_id, "product": "A-leggo Math Research", "managedFiles": sorted(files), "updatedAt": _now()}
        self._atomic_write(marker, _json(marker_value).encode("utf-8"))
        for relative, data in files.items():
            safe = _safe_workspace_relative(relative)
            target = (root / safe).resolve()
            if not target.is_relative_to(root):
                raise ValueError("工作区文件路径超出所选项目文件夹。")
            self._atomic_write(target, data)
        self._atomic_write(marker, _json(marker_value).encode("utf-8"))
        self._update_state(project_id, workspace_status="READY")
        for artifact in self.store.list_artifacts(project_id):
            from aleggo.study.research_delivery import _safe_name
            target = root / "artifacts" / f"{_safe_name(artifact['title'])}-{_safe_name(artifact['id'])}.md"
            self.store.set_artifact_delivery(artifact["id"], project_id, str(target), {"syncedAt": _now()})
        return {"status": "READY", "filesWritten": len(files) + 1, "path": str(root)}

    def export_package(self, project_id: str) -> bytes:
        from aleggo.study.research_delivery import ResearchDelivery
        return ResearchDelivery(self.store).export_package(project_id)

    def build_final_report(self, project_id: str) -> str:
        from aleggo.study.research_reports import build_markdown_report
        return build_markdown_report(self.store, project_id)

    @staticmethod
    def _literature_gap(sources: list[dict[str, Any]]) -> str:
        items = [item for item in sources if item["type"] == "literature"]
        if not items:
            return "尚未捕获文献元数据，研究新颖性与覆盖范围未知。"
        years = [item["year"] for item in items if item.get("year")]
        direct = sum(1 for item in items if item.get("classification") == "direct")
        recent = sum(1 for item in items if item.get("classification") == "recent")
        return (f"Captured {len(items)} deduplicated metadata records from public indexes; {direct} classified as directly related and {recent} as recent. "
                f"Indexed year span: {str(min(years)) + '–' + str(max(years)) if years else 'unknown'}. This is a search-coverage summary, not a claim of novelty or exhaustive review.")

    def open_workspace(self, project_id: str) -> dict[str, Any]:
        state = self.state(project_id)
        if not state["workspaceRoot"]:
            raise ValueError("请先选择项目工作区文件夹。")
        path = _resolve_existing_dir(state["workspaceRoot"])
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", str(path)], close_fds=True)
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(path)], close_fds=True)
        else:
            raise ValueError("此主机不支持打开文件管理器。")
        self.record_event(project_id, "workspace_opened", "researcher", summary="Opened local research workspace.", details={"path": str(path)})
        return {"path": str(path), "opened": True}

    def artifact_markdown(self, project_id: str, artifact_id: str) -> tuple[str, str]:
        artifact = next((item for item in self.store.list_artifacts(project_id) if item["id"] == artifact_id), None)
        if not artifact:
            raise KeyError("未找到研究产物。")
        return artifact["title"], artifact["content"]
