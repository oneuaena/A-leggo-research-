"""Paged conversation feed for the research workspace.

The feed is a *read model*: it merges rows that already exist (researcher
messages, real tool runs, step events and result artifacts) into one newest-first
stream.  Nothing here writes to the database, so the approval, pause/resume,
intervention queue and export paths keep their existing behaviour.

Contract
--------
* ``before`` pages backwards for older history.
* ``after`` returns only rows newer than a cursor, so a running session can be
  polled cheaply without re-sending (or re-rendering) the whole history.
* Items are always returned newest-first; the client reverses them for display.
"""

from __future__ import annotations

import json
import re
from typing import Any

from aleggo.study.research_store import ResearchStore
from aleggo.study.research_workbench import ResearchWorkbench

_TOOL_PREFIX = re.compile(r'^\{"tool":\s*"([^"]+)"')

DEFAULT_LIMIT = 40
MAX_LIMIT = 100
MAX_AFTER_ITEMS = 200
ANSWER_TEXT_LIMIT = 6000
TOOL_TEXT_LIMIT = 4000
INPUT_TEXT_LIMIT = 2000

USER_KINDS = {
    "human_note", "human_result", "human_lemma", "add_human_idea",
    "request_literature", "request_proof", "request_counterexample", "request_counterexample_search",
    "request_numerical", "request_symbolic", "request_symbolic_check",
    "request_critique", "request_critique_attack", "request_verification", "discuss",
}

# Timeline rows worth showing as one-line status in a conversation.  Per-call and
# bookkeeping events (budget checks, workspace sync, the duplicate
# "human_intervention" row) stay in the expert console.
EVENT_TYPES = {
    "plan_approved": "info",
    "research_started": "info",
    "step_started": "info",
    "step_completed": "success",
    "intervention_completed": "success",
    "stage_review_continued": "info",
    "research_review_required": "warning",
    "research_stalled": "warning",
    "research_blocked": "warning",
    "research_failed": "danger",
    "final_package_created": "success",
    "local_document_indexed": "info",
    "targeted_literature_search": "info",
    "pdf_vision_continue_pending": "warning",
    "pdf_vision_continue_cleared": "success",
    "discussion_resumed": "info",
}
# Discussion replies are shown as answers; their bookkeeping event stays hidden.

_EVENT_LABELS_ZH = {
    "discussion_resumed": "已自动恢复中断的讨论",
    "discussion_replied": "讨论已回复",
    "research_started": "研究已开始",
    "research_failed": "研究失败",
    "research_blocked": "研究已阻塞",
    "research_stalled": "研究进展停滞",
    "final_package_created": "最终研究包已就绪",
    "pdf_vision_continue_pending": "PDF 视觉识别待继续",
    "pdf_vision_continue_cleared": "PDF 视觉识别已完成",
}



# Not real tool invocations: model planning calls and the failure ledger entry.
_NON_TOOL_PREFIXES = ("llm:",)
_FAILURE_LEDGER_TOOL = "research-step-failure"
_NON_ANSWER_ROLES = {"researcher", "math-tool"}
_TERMINAL_QUEUE = {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}
_LIVE_STATUSES = {"RUNNING", "VERIFYING", "PLANNING", "REPLANNING"}


def _clip(value: Any, limit: int) -> tuple[str, bool]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str, indent=2)
    return (text[:limit], len(text) > limit)


def _safe_int(raw: Any) -> int:
    """Coerce dirty DB numerics (None, "abc", 12.7) to int without raising."""
    try:
        return int(float(raw or 0))
    except (TypeError, ValueError):
        return 0


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def parse_cursor(cursor: str) -> tuple[str, str] | None:
    """``"<createdAt>|<id>"`` -> ``(createdAt, id)``; anything malformed is ignored."""
    if not cursor or "|" not in cursor:
        return None
    created_at, _, row_id = cursor.rpartition("|")
    return (created_at, row_id) if created_at and row_id else None


def make_cursor(item: dict[str, Any]) -> str:
    return f"{item['createdAt']}|{item['rowId']}"


def _where(column: str, id_column: str, cursor: tuple[str, str] | None, direction: str) -> tuple[str, list[str]]:
    if not cursor:
        return "", []
    created_at, row_id = cursor
    op = "<" if direction == "before" else ">"
    return f" AND ({column} {op} ? OR ({column} = ? AND {id_column} {op} ?))", [created_at, created_at, row_id]


def _user_item(row: Any) -> dict[str, Any] | None:
    if row["kind"] not in USER_KINDS:
        return None
    details = _json(row["details_json"], {})
    details = details if isinstance(details, dict) else {}
    text = str(details.get("message") or details.get("note") or details.get("content") or details.get("text") or "").strip()
    clipped, truncated = _clip(text, ANSWER_TEXT_LIMIT)
    return {
        "id": "u:" + row["id"], "rowId": row["id"], "kind": "user", "createdAt": row["created_at"],
        "intervention": row["kind"], "text": clipped, "truncated": truncated,
        "branch": str(details.get("branch") or ""), "queueId": str(details.get("queueId") or ""),
    }


def _tool_view(row: Any) -> dict[str, Any] | None:
    name = str(row["tool_name"] or "")
    if name.startswith(_NON_TOOL_PREFIXES):
        return None
    created_at = row["created_at"]
    raw_input = _json(row["input_json"], {})
    if name == _FAILURE_LEDGER_TOOL:
        title = raw_input.get("title") if isinstance(raw_input, dict) else ""
        error_type = raw_input.get("errorType") if isinstance(raw_input, dict) else ""
        return {
            "id": "t:" + row["id"], "rowId": row["id"], "kind": "event", "createdAt": created_at,
            "eventType": "step_failed", "tone": "danger", "actor": "runtime",
            "summary": f"步骤未完成：{title or row['step_id']}" + (f"（{error_type}）" if error_type else ""),
            "stepId": row["step_id"],
        }
    record = _json(row["output_text"], None)
    if isinstance(record, dict) and ("tool" in record or "output" in record):
        output = record.get("output") or ""
        error = record.get("error") or ""
        ok = record.get("ok")
        body = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str, indent=2)
        if error:
            body = (body + "\n" if body else "") + "错误：" + str(error)
    else:
        ok, body = None, str(row["output_text"] or "")
    out_text, out_truncated = _clip(body, TOOL_TEXT_LIMIT)
    in_text, in_truncated = _clip(raw_input, INPUT_TEXT_LIMIT)
    status = str(row["status"] or "")
    return {
        "id": "t:" + row["id"], "rowId": row["id"], "kind": "tool", "createdAt": created_at,
        "tool": name, "status": status, "ok": bool(ok) if ok is not None else status.upper() in {"COMPLETED", "VERIFIED", "OK"},
        "input": in_text, "inputTruncated": in_truncated, "output": out_text, "outputTruncated": out_truncated,
        "elapsedMs": _safe_int(row["elapsed_ms"]), "stepId": row["step_id"], "branch": row["branch_name"] or "main",
    }


def _artifact_view(row: Any) -> dict[str, Any] | None:
    role = str(row["agent_role"] or "")
    status = str(row["status"] or "")
    if role in _NON_ANSWER_ROLES or (role == "runtime" and status == "failed"):
        return None
    content = row["content"] or ""
    base = {"id": "a:" + row["id"], "rowId": row["id"], "createdAt": row["created_at"],
            "stepId": row["step_id"], "branch": row["branch_name"] or "main", "artifactId": row["id"]}
    if content.startswith('{"tool"'):
        record = _json(content, None)
        match = _TOOL_PREFIX.match(content)
        tool = str(record.get("tool")) if isinstance(record, dict) and record.get("tool") else (match.group(1) if match else "tool")
        shown = record.get("output", record) if isinstance(record, dict) else content
        body, truncated = _clip(shown, TOOL_TEXT_LIMIT)
        raw_in = record.get("input", {}) if isinstance(record, dict) else {}
        in_text, in_truncated = _clip(raw_in, INPUT_TEXT_LIMIT)
        return {**base, "kind": "tool", "tool": tool, "status": status,
                "ok": bool(record.get("ok")) if isinstance(record, dict) else False,
                "input": in_text, "inputTruncated": in_truncated,
                "output": body, "outputTruncated": truncated or int(row["content_len"] or 0) > len(content),
                "elapsedMs": 0}
    text, truncated = _clip(content, ANSWER_TEXT_LIMIT)
    return {**base, "kind": "answer", "title": row["title"], "role": role, "status": status,
            "text": text, "truncated": truncated or int(row["content_len"] or 0) > len(content)}


def _event_view(row: Any) -> dict[str, Any] | None:
    tone = EVENT_TYPES.get(row["event_type"])
    if tone is None:
        return None
    details = _json(row["details_json"], {})
    return {"id": "e:" + row["id"], "rowId": row["id"], "kind": "event", "createdAt": row["created_at"],
            "eventType": row["event_type"], "tone": tone, "actor": row["actor"],
            "summary": (row["summary"] or _EVENT_LABELS_ZH.get(row["event_type"]) or row["event_type"].replace("_", " "))[:400],
            "stepId": row["step_id"], "branch": row["branch_name"],
            "artifactId": str(details.get("artifactId") or details.get("reportArtifactId") or "") if isinstance(details, dict) else ""}


def _query(conn: Any, sql: str, params: list[Any], builder: Any, wanted: int) -> list[dict[str, Any]]:
    """Run one source query and keep up to ``wanted`` successfully classified rows.

    Rows that classify to ``None`` (internal bookkeeping) are skipped, so each
    query over-fetches a little and the loop stops once enough items exist.
    """
    items: list[dict[str, Any]] = []
    for row in conn.execute(sql, params):
        item = builder(row)
        if item is not None:
            items.append(item)
            if len(items) >= wanted:
                break
    return items


def build_thread_page(
    store: ResearchStore, project: dict[str, Any], *, limit: int = DEFAULT_LIMIT, before: str = "", after: str = "",
) -> dict[str, Any]:
    project_id = project["id"]
    after_cursor = parse_cursor(after)
    before_cursor = None if after_cursor else parse_cursor(before)
    direction = "after" if after_cursor else "before"
    wanted = MAX_AFTER_ITEMS if after_cursor else max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    cursor = after_cursor or before_cursor
    order = "ASC" if after_cursor else "DESC"
    scan = wanted * 6 + 20  # bounded scan so a long run of hidden rows cannot stall a request

    gathered: list[dict[str, Any]] = []
    with store.connection() as conn:
        clause, args = _where("created_at", "id", cursor, direction)
        gathered += _query(
            conn, f"SELECT * FROM research_interventions WHERE project_id=?{clause} ORDER BY created_at {order}, id {order} LIMIT ?",
            [project_id, *args, scan], _user_item, wanted + 1)
        clause, args = _where("t.created_at", "t.id", cursor, direction)
        gathered += _query(
            conn, "SELECT t.*,b.name AS branch_name FROM research_tool_runs t LEFT JOIN research_branches b ON b.id=t.branch_id "
                  f"WHERE t.project_id=?{clause} ORDER BY t.created_at {order}, t.id {order} LIMIT ?",
            [project_id, *args, scan], _tool_view, wanted + 1)
        clause, args = _where("a.created_at", "a.id", cursor, direction)
        gathered += _query(
            conn, "SELECT a.id,a.agent_role,a.title,substr(a.content,1,?) AS content,length(a.content) AS content_len,a.status,"
                  "a.step_id,a.created_at,b.name AS branch_name FROM research_artifacts a LEFT JOIN research_branches b ON b.id=a.branch_id "
                  f"WHERE a.project_id=?{clause} ORDER BY a.created_at {order}, a.id {order} LIMIT ?",
            [ANSWER_TEXT_LIMIT, project_id, *args, scan], _artifact_view, wanted + 1)
        clause, args = _where("created_at", "id", cursor, direction)
        gathered += _query(
            conn, f"SELECT * FROM research_timeline WHERE project_id=?{clause} ORDER BY created_at {order}, id {order} LIMIT ?",
            [project_id, *args, scan], _event_view, wanted + 1)
        queue_rows = conn.execute(
            "SELECT id,status FROM research_intervention_queue WHERE project_id=? ORDER BY created_at DESC LIMIT 60", (project_id,)).fetchall()

    gathered.sort(key=lambda item: (item["createdAt"], item["rowId"]), reverse=True)
    if after_cursor:
        # Oldest `wanted` rows after the cursor, still returned newest-first.
        page = sorted(gathered, key=lambda item: (item["createdAt"], item["rowId"]))[:wanted]
        page.sort(key=lambda item: (item["createdAt"], item["rowId"]), reverse=True)
        has_more = len(gathered) > wanted
    else:
        page = gathered[:wanted]
        has_more = len(gathered) > wanted
    for item in page:
        item["cursor"] = make_cursor(item)
        item.pop("rowId", None)

    live = None
    if project.get("status") in _LIVE_STATUSES:
        state = ResearchWorkbench(store).state(project_id)
        live = {"action": state.get("currentAction", ""), "tool": state.get("currentTool", ""),
                "agent": state.get("currentAgent", ""), "phase": state.get("phase", ""),
                "step": state.get("currentStepId", ""), "stepIndex": state.get("stepIndex", 0), "stepTotal": state.get("stepTotal", 0)}
    return {
        "items": page,
        "hasMore": has_more,
        "nextCursor": page[-1]["cursor"] if page and not after_cursor else "",
        "newestCursor": page[0]["cursor"] if page else "",
        "queue": {row["id"]: row["status"] for row in queue_rows},
        "live": live,
    }
