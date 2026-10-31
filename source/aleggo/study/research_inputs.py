"""Researcher input requests for the research runtime.

A research plan (or a running step) sometimes cannot proceed without the
researcher: a missing definition, an original paper, a dataset, a constraint.
The planner model returns these as ``inputRequests`` (objects with ``label``,
``reason`` and ``kind``); this module normalises them, stores them per project
in the research database, and gates execution on them:

* :func:`normalize_input_requests` coerces raw model output into clean records.
* :class:`ResearchInputs` persists requests (``pending`` / ``paused`` /
  ``answered``), renders answered inputs back into prompt context, blocks
  ``start``/``resume`` while anything is unresolved (:meth:`guard`), and
  records the researcher's answers (:meth:`respond`).

Everything is local SQLite in the researcher's own data directory; the table is
created idempotently next to the other research tables.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

KINDS = ("text", "document")
#: Requests that still need a researcher answer.
UNRESOLVED = ("pending", "paused")

MAX_REQUESTS = 50
MAX_LABEL_CHARS = 200
MAX_REASON_CHARS = 2000
MAX_RESPONSE_CHARS = 20000
MAX_SOURCE_IDS = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_inputs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    label TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'text',
    step_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    response_text TEXT NOT NULL DEFAULT '',
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    answered_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_research_inputs_project
    ON research_inputs(project_id, status);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _request_id() -> str:
    return "input_" + uuid4().hex


def normalize_input_requests(raw: Any) -> list[dict[str, str]]:
    """Coerce model-produced ``inputRequests`` into clean ``{label, reason, kind}`` records.

    Accepts the shapes models actually return (a list of objects, a list of
    bare strings, ``None``); anything else becomes ``[]``.  Labels are required,
    ``kind`` is restricted to ``text``/``document``, and lengths are capped so a
    rogue model cannot flood the database.
    """
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in raw[:MAX_REQUESTS]:
        if isinstance(item, str):
            label, reason, kind = item.strip(), "", "text"
        elif isinstance(item, dict):
            label = str(item.get("label") or "").strip()
            reason = str(item.get("reason") or "").strip()
            kind = str(item.get("kind") or "text").strip().lower()
        else:
            continue
        if not label:
            continue
        if kind not in KINDS:
            kind = "text"
        cleaned.append({
            "label": label[:MAX_LABEL_CHARS],
            "reason": reason[:MAX_REASON_CHARS],
            "kind": kind,
        })
    return cleaned


class ResearchInputs:
    """Per-project input-request ledger backed by the research store's database."""

    def __init__(self, store: Any) -> None:
        self.store = store
        with self.store.connection() as conn:
            conn.executescript(_SCHEMA)

    # -- registration ---------------------------------------------------- #

    def register(self, project_id: str, requests: Any, step_id: str = "",
                 reopen: bool = False) -> int:
        """Record new input requests; idempotent per (project, step, label).

        Already ``pending``/``paused`` duplicates are skipped.  A request that
        was answered before is only asked again when ``reopen`` is true (a new
        plan version asking for the same material), and then as a new row so the
        answered history is preserved.  Returns the number of newly added rows.
        """
        items = normalize_input_requests(requests)
        if not items:
            return 0
        step_id = str(step_id or "")
        added = 0
        now = _now()
        with self.store.connection() as conn:
            for item in items:
                row = conn.execute(
                    "SELECT id, status FROM research_inputs"
                    " WHERE project_id = ? AND step_id = ? AND label = ?"
                    " ORDER BY created_at DESC LIMIT 1",
                    (project_id, step_id, item["label"]),
                ).fetchone()
                if row and row["status"] in UNRESOLVED:
                    continue
                if row and row["status"] == "answered" and not reopen:
                    continue
                conn.execute(
                    "INSERT INTO research_inputs"
                    " (id, project_id, label, reason, kind, step_id, status,"
                    "  created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (_request_id(), project_id, item["label"], item["reason"],
                     item["kind"], step_id, now, now),
                )
                added += 1
        return added

    # -- queries ---------------------------------------------------------- #

    def _rows(self, project_id: str, statuses: tuple[str, ...],
              order: str) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in statuses)
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_inputs WHERE project_id = ?"
                f" AND status IN ({placeholders}) ORDER BY {order}",
                (project_id, *statuses),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending(self, project_id: str) -> list[dict[str, Any]]:
        """Unresolved requests (``pending`` or ``paused``), oldest first."""
        return self._rows(project_id, UNRESOLVED, "created_at")

    def list(self, project_id: str) -> list[dict[str, Any]]:
        """All requests for a project, oldest first (for the input panel)."""
        return self._rows(project_id, ("pending", "paused", "answered"), "created_at")

    def context(self, project_id: str) -> str:
        """Answered inputs rendered for prompt context; ``""`` when none."""
        rows = self._rows(project_id, ("answered",), "answered_at")
        if not rows:
            return ""
        lines = ["研究者已提供的资料："]
        for row in rows:
            try:
                source_ids = json.loads(row.get("source_ids_json") or "[]")
            except (ValueError, TypeError):
                source_ids = []
            suffix = f"（关联资料：{'、'.join(str(s) for s in source_ids)}）" if source_ids else ""
            lines.append(f"- {row['label']}：{row['response_text']}{suffix}")
        return "\n".join(lines)

    # -- gating ----------------------------------------------------------- #

    def guard(self, project_id: str) -> None:
        """Raise :class:`PermissionError` while any request is unresolved."""
        unresolved = self.pending(project_id)
        if unresolved:
            labels = "；".join(item["label"] for item in unresolved[:5])
            extra = f" 等 {len(unresolved)} 项" if len(unresolved) > 5 else ""
            raise PermissionError(f"启动研究前需要先补充资料：{labels}{extra}。请在输入面板中逐项回复。")

    def pause(self, project_id: str) -> int:
        """Mark pending requests as paused (waiting on the researcher)."""
        with self.store.connection() as conn:
            cursor = conn.execute(
                "UPDATE research_inputs SET status = 'paused', updated_at = ?"
                " WHERE project_id = ? AND status = 'pending'",
                (_now(), project_id),
            )
            return cursor.rowcount

    # -- answers ---------------------------------------------------------- #

    def respond(self, project_id: str, request_id: str, text: str,
                source_ids: list[str] | None = None) -> dict[str, Any]:
        """Record the researcher's answer to one request.

        Raises :class:`KeyError` for an unknown request and
        :class:`ValueError` for an empty/oversized answer or a repeat answer.
        """
        text = str(text or "").strip()
        if not text:
            raise ValueError("回复内容不能为空。")
        if len(text) > MAX_RESPONSE_CHARS:
            raise ValueError(f"回复内容过长（最多 {MAX_RESPONSE_CHARS} 字）。")
        sources = [str(source) for source in (source_ids or [])][:MAX_SOURCE_IDS]
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_inputs WHERE id = ? AND project_id = ?",
                (request_id, project_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"未找到输入请求 {request_id}。")
            if row["status"] == "answered":
                raise ValueError("该请求已回复过，无需重复回复。")
            now = _now()
            conn.execute(
                "UPDATE research_inputs SET status = 'answered', response_text = ?,"
                " source_ids_json = ?, updated_at = ?, answered_at = ? WHERE id = ?",
                (text, json.dumps(sources, ensure_ascii=False), now, now, request_id),
            )
            updated = conn.execute(
                "SELECT * FROM research_inputs WHERE id = ?", (request_id,),
            ).fetchone()
            return dict(updated)
