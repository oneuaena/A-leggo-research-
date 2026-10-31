"""Versioned research projects stored in A-leggo's existing SQLite database."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
import sqlite3
import threading
from typing import Any
from uuid import uuid4

# NOTE: the host application's service registry (``aleggo.study.service``) is
# intentionally NOT imported at module level.  It only exists inside the host
# app; importing it here would make every research module unimportable outside
# the host (evaluation harness, tests, offline tooling).  It is imported lazily
# in ``ResearchStore.__init__`` on the single path that needs it.


_lock = threading.RLock()
_RECOVERED_PATHS: set[str] = set()
_SCHEMA_VERSION = 8


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return prefix + "_" + uuid4().hex


def _safe_json(raw: Any, fallback: Any) -> Any:
    """Parse a DB JSON column; dirty data degrades to ``fallback`` instead of
    raising and taking down the whole list/detail endpoint."""
    try:
        return json.loads(raw) if raw else fallback
    except (ValueError, TypeError):
        return fallback


class ResearchStore:
    """Research-domain tables share the A-leggo database file and backups."""

    def __init__(self, aleggo_store: Any | None = None) -> None:
        if aleggo_store is None:
            from pathlib import Path
            import os
            from aleggo.study.store import AleggoStore

            root = Path(os.environ["ALEGGO_RESEARCH_HOME"]) / "projects"
            aleggo_store = AleggoStore(root)
        self.aleggo_store = aleggo_store
        self.path = self.aleggo_store.path
        self._ensure_schema()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with _lock:
            path_key = str(self.path.resolve())
            first_open_this_process = path_key not in _RECOVERED_PATHS
            with self.connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                row = conn.execute("SELECT value FROM meta WHERE key='research_schema_version'").fetchone()
                previous = int(row[0]) if row else 0
            if previous < _SCHEMA_VERSION and self.path.exists() and self.path.stat().st_size:
                self.aleggo_store.snapshot_database(f"research-schema-v{previous}-to-v{_SCHEMA_VERSION}")
            with self.connection() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS research_projects (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        question TEXT NOT NULL,
                        status TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        current_version INTEGER NOT NULL DEFAULT 0,
                        approved_version INTEGER,
                        last_error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_plan_versions (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        version INTEGER NOT NULL,
                        plan_json TEXT NOT NULL,
                        changed_by TEXT NOT NULL,
                        change_reason TEXT NOT NULL DEFAULT '',
                        change_summary TEXT NOT NULL DEFAULT '',
                        approved INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        UNIQUE(project_id, version)
                    );
                    CREATE TABLE IF NOT EXISTS research_artifacts (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        branch_id TEXT REFERENCES research_branches(id) ON DELETE SET NULL,
                        plan_version INTEGER NOT NULL,
                        step_id TEXT NOT NULL DEFAULT '',
                        agent_role TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        file_path TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'unverified',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_interventions (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        plan_version INTEGER,
                        kind TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_branches (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        name TEXT NOT NULL,
                        parent_id TEXT REFERENCES research_branches(id) ON DELETE SET NULL,
                        base_plan_version INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'OPEN',
                        limits_json TEXT NOT NULL DEFAULT '{"evaluations":1000000,"proofAttempts":128,"toolSeconds":900}',
                        used_json TEXT NOT NULL DEFAULT '{"evaluations":0,"proofAttempts":0,"toolSeconds":0}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(project_id, name)
                    );
                    CREATE TABLE IF NOT EXISTS research_graph_nodes (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        branch_id TEXT REFERENCES research_branches(id) ON DELETE SET NULL,
                        node_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'unverified',
                        artifact_id TEXT REFERENCES research_artifacts(id) ON DELETE SET NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_graph_edges (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        branch_id TEXT REFERENCES research_branches(id) ON DELETE SET NULL,
                        source_id TEXT NOT NULL REFERENCES research_graph_nodes(id) ON DELETE CASCADE,
                        target_id TEXT NOT NULL REFERENCES research_graph_nodes(id) ON DELETE CASCADE,
                        edge_type TEXT NOT NULL,
                        evidence TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        UNIQUE(project_id, source_id, target_id, edge_type)
                    );
                    CREATE TABLE IF NOT EXISTS research_tool_runs (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        branch_id TEXT REFERENCES research_branches(id) ON DELETE SET NULL,
                        step_id TEXT NOT NULL DEFAULT '',
                        tool_name TEXT NOT NULL,
                        input_json TEXT NOT NULL,
                        output_text TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        elapsed_ms INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_sources (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        branch_id TEXT REFERENCES research_branches(id) ON DELETE SET NULL,
                        source_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL DEFAULT '',
                        authors TEXT NOT NULL DEFAULT '',
                        year INTEGER,
                        abstract TEXT NOT NULL DEFAULT '',
                        content_hash TEXT NOT NULL DEFAULT '',
                        doi TEXT NOT NULL DEFAULT '',
                        provider TEXT NOT NULL DEFAULT '',
                        classification TEXT NOT NULL DEFAULT 'background',
                        reading_status TEXT NOT NULL DEFAULT 'unread',
                        notes TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        first_seen_at TEXT NOT NULL DEFAULT '',
                        last_seen_at TEXT NOT NULL DEFAULT '',
                        document_blob BLOB,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_input_requests (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        plan_version INTEGER NOT NULL,
                        step_id TEXT NOT NULL DEFAULT '',
                        label TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'OPEN',
                        response TEXT NOT NULL DEFAULT '',
                        source_ids_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_document_chunks (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        source_id TEXT NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
                        chunk_index INTEGER NOT NULL,
                        page_number INTEGER,
                        section TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(source_id, chunk_index)
                    );
                    CREATE TABLE IF NOT EXISTS research_memories (
                        id TEXT PRIMARY KEY,
                        project_id TEXT REFERENCES research_projects(id) ON DELETE CASCADE,
                        content TEXT NOT NULL,
                        source_artifact_id TEXT REFERENCES research_artifacts(id) ON DELETE SET NULL,
                        confirmed INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_formal_bindings (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        claim_node_id TEXT NOT NULL REFERENCES research_graph_nodes(id) ON DELETE CASCADE,
                        proof_node_id TEXT REFERENCES research_graph_nodes(id) ON DELETE SET NULL,
                        original_statement TEXT NOT NULL,
                        formal_ir TEXT NOT NULL,
                        lean_statement TEXT NOT NULL,
                        original_hash TEXT NOT NULL,
                        formal_ir_hash TEXT NOT NULL,
                        lean_statement_hash TEXT NOT NULL,
                        binding_hash TEXT NOT NULL,
                        mapping_authority TEXT NOT NULL,
                        status TEXT NOT NULL,
                        proof_source TEXT NOT NULL DEFAULT '',
                        proof_source_hash TEXT NOT NULL DEFAULT '',
                        lean_version TEXT NOT NULL DEFAULT '',
                        checker_output TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(project_id,claim_node_id,binding_hash)
                    );
                    CREATE TABLE IF NOT EXISTS mra_imports (
                        source_hash TEXT PRIMARY KEY,
                        original_name TEXT NOT NULL,
                        imported_at TEXT NOT NULL,
                        summary_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS mra_project_imports (
                        source_hash TEXT NOT NULL REFERENCES mra_imports(source_hash) ON DELETE CASCADE,
                        source_project_id TEXT NOT NULL,
                        target_project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        PRIMARY KEY(source_hash, source_project_id)
                    );
                    CREATE TABLE IF NOT EXISTS mra_legacy_records (
                        source_hash TEXT NOT NULL REFERENCES mra_imports(source_hash) ON DELETE CASCADE,
                        source_table TEXT NOT NULL,
                        source_record_id TEXT NOT NULL,
                        target_project_id TEXT REFERENCES research_projects(id) ON DELETE CASCADE,
                        collection TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL,
                        source_created_at TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY(source_hash, source_table, source_record_id)
                    );
                    CREATE TABLE IF NOT EXISTS research_workbench_state (
                        project_id TEXT PRIMARY KEY REFERENCES research_projects(id) ON DELETE CASCADE,
                        supervision_mode TEXT NOT NULL DEFAULT 'MILESTONE_REVIEW',
                        current_step_id TEXT NOT NULL DEFAULT '',
                        current_agent TEXT NOT NULL DEFAULT '',
                        current_action TEXT NOT NULL DEFAULT '',
                        current_branch TEXT NOT NULL DEFAULT 'main',
                        current_milestone TEXT NOT NULL DEFAULT '',
                        run_started_at TEXT NOT NULL DEFAULT '',
                        status_reason TEXT NOT NULL DEFAULT '',
                        blocker_type TEXT NOT NULL DEFAULT '',
                        blocker_detail TEXT NOT NULL DEFAULT '',
                        stalled_attempts INTEGER NOT NULL DEFAULT 0,
                        llm_calls INTEGER NOT NULL DEFAULT 0,
                        tool_calls INTEGER NOT NULL DEFAULT 0,
                        failures INTEGER NOT NULL DEFAULT 0,
                        last_progress_at TEXT NOT NULL DEFAULT '',
                        last_fingerprint TEXT NOT NULL DEFAULT '',
                        workspace_root TEXT NOT NULL DEFAULT '',
                        workspace_status TEXT NOT NULL DEFAULT 'UNCONFIGURED',
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_timeline (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        event_type TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        step_id TEXT NOT NULL DEFAULT '',
                        branch_name TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL DEFAULT '',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_decisions (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        decision TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        rationale TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT '',
                        branch_name TEXT NOT NULL DEFAULT '',
                        related_artifact_id TEXT REFERENCES research_artifacts(id) ON DELETE SET NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_recommendations (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        step_id TEXT NOT NULL DEFAULT '',
                        content_json TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_literature_links (
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        source_id TEXT NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
                        entity_type TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        relationship TEXT NOT NULL DEFAULT 'RELATED_TO',
                        notes TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(project_id,source_id,entity_type,entity_id,relationship)
                    );
                    CREATE TABLE IF NOT EXISTS research_progress_attempts (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        step_id TEXT NOT NULL DEFAULT '',
                        route_signature TEXT NOT NULL,
                        content_text TEXT NOT NULL DEFAULT '',
                        signals_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_step_checkpoints (
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        plan_version INTEGER NOT NULL,
                        step_id TEXT NOT NULL,
                        phase TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        resources_json TEXT NOT NULL DEFAULT '{}',
                        signals_json TEXT NOT NULL DEFAULT '{}',
                        milestone_key TEXT NOT NULL DEFAULT '',
                        started_at TEXT NOT NULL DEFAULT '',
                        completed_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(project_id,plan_version,step_id)
                    );
                    CREATE TABLE IF NOT EXISTS research_progress_fingerprints (
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        signal_kind TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        source_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(project_id,signal_kind,fingerprint)
                    );
                    CREATE TABLE IF NOT EXISTS research_budget_evaluations (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        plan_version INTEGER NOT NULL,
                        step_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        resources_json TEXT NOT NULL DEFAULT '{}',
                        gain_json TEXT NOT NULL DEFAULT '{}',
                        reason TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_intervention_queue (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        intervention_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'QUEUED',
                        result_artifact_id TEXT REFERENCES research_artifacts(id) ON DELETE SET NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_failed_paths (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        step_id TEXT NOT NULL DEFAULT '',
                        branch_name TEXT NOT NULL DEFAULT 'main',
                        route_text TEXT NOT NULL,
                        route_signature TEXT NOT NULL,
                        assumptions_json TEXT NOT NULL DEFAULT '[]',
                        assumption_hash TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'FAILED',
                        reason TEXT NOT NULL,
                        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                        retry_condition TEXT NOT NULL DEFAULT '',
                        source_artifact_id TEXT REFERENCES research_artifacts(id) ON DELETE SET NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_literature_searches (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        trigger_type TEXT NOT NULL,
                        trigger_id TEXT NOT NULL DEFAULT '',
                        step_id TEXT NOT NULL DEFAULT '',
                        branch_name TEXT NOT NULL DEFAULT 'main',
                        queries_json TEXT NOT NULL DEFAULT '[]',
                        scope_json TEXT NOT NULL DEFAULT '{}',
                        providers_json TEXT NOT NULL DEFAULT '{}',
                        source_ids_json TEXT NOT NULL DEFAULT '[]',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_literature_analyses (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        analysis_type TEXT NOT NULL,
                        content_json TEXT NOT NULL DEFAULT '{}',
                        search_ids_json TEXT NOT NULL DEFAULT '[]',
                        source_ids_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS research_workspace_imports (
                        source_hash TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_research_projects_updated
                        ON research_projects(updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_research_artifacts_project
                        ON research_artifacts(project_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_research_interventions_project
                        ON research_interventions(project_id, created_at);
                    CREATE INDEX IF NOT EXISTS idx_research_graph_nodes_project
                        ON research_graph_nodes(project_id, branch_id, node_type, created_at);
                    CREATE INDEX IF NOT EXISTS idx_research_graph_edges_project
                        ON research_graph_edges(project_id, branch_id, edge_type);
                    CREATE INDEX IF NOT EXISTS idx_research_sources_project
                        ON research_sources(project_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_research_input_requests_project
                        ON research_input_requests(project_id, plan_version, status);
                    CREATE INDEX IF NOT EXISTS idx_research_document_chunks_source
                        ON research_document_chunks(source_id, chunk_index);
                    CREATE INDEX IF NOT EXISTS idx_research_formal_bindings_project
                        ON research_formal_bindings(project_id,claim_node_id,created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_mra_legacy_records_project
                        ON mra_legacy_records(target_project_id, collection, source_created_at);
                    CREATE INDEX IF NOT EXISTS idx_research_timeline_project
                        ON research_timeline(project_id,created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_research_decisions_project
                        ON research_decisions(project_id,created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_research_literature_links_entity
                        ON research_literature_links(project_id,entity_type,entity_id);
                    CREATE INDEX IF NOT EXISTS idx_research_progress_project
                        ON research_progress_attempts(project_id,created_at DESC);
                    """
                )
                self._ensure_columns(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO research_workbench_state(project_id,updated_at) "
                    "SELECT id,updated_at FROM research_projects"
                )
                artifact_columns = {
                    str(column[1]) for column in conn.execute("PRAGMA table_info(research_artifacts)").fetchall()
                }
                if "branch_id" not in artifact_columns:
                    conn.execute("ALTER TABLE research_artifacts ADD COLUMN branch_id TEXT")
                conn.execute(
                    "INSERT OR IGNORE INTO research_branches(id,project_id,name,base_plan_version,created_at,updated_at) "
                    "SELECT 'branch_' || replace(id, '-', ''),id,'main',current_version,created_at,updated_at FROM research_projects"
                )
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES('research_schema_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(_SCHEMA_VERSION),),
                )
                # Recover orphaned runs only once per database per process.
                # ResearchStore instances are short-lived per API request; doing
                # this on every GET would pause a live worker during UI polling.
                if first_open_this_process:
                    # Discussion replies run in-process.  Re-queue interrupted ones;
                    # research_collaborator.resume_pending_discussions answers them
                    # again when the project is next opened.
                    conn.execute(
                        "UPDATE research_intervention_queue SET status='QUEUED',updated_at=? "
                        "WHERE intervention_type='discuss' AND status='RUNNING'", (_now(),),
                    )
                    # Requests that were executing for a finished project are re-queued
                    # and drained again (see research_collaborator.schedule_request_drain).
                    conn.execute(
                        "UPDATE research_intervention_queue SET status='QUEUED',updated_at=? WHERE status='RUNNING' AND "
                        "project_id IN (SELECT id FROM research_projects WHERE status IN ('COMPLETED','FAILED','CANCELLED'))", (_now(),),
                    )
                    interrupted = [row[0] for row in conn.execute(
                        "SELECT id FROM research_projects WHERE status IN ('RUNNING','VERIFYING','REPLANNING','PLANNING')"
                    ).fetchall()]
                    conn.execute(
                        "UPDATE research_projects SET status='PAUSED',last_error='应用重启打断了进行中的研究；可从上一完成步骤继续。',updated_at=? WHERE status IN ('RUNNING','VERIFYING')",
                        (_now(),),
                    )
                    conn.execute(
                        "UPDATE research_projects SET status='AWAITING_APPROVAL',approved_version=NULL,last_error='应用重启打断了计划修订；请重新审阅并批准当前已保存的计划。',updated_at=? WHERE status='REPLANNING'",
                        (_now(),),
                    )
                    conn.execute(
                        "UPDATE research_projects SET status='DRAFT',last_error='应用重启时计划尚未保存；请重新生成或编辑计划后继续。',updated_at=? WHERE status='PLANNING'",
                        (_now(),),
                    )
                    for project_id in interrupted:
                        conn.execute(
                            "UPDATE research_workbench_state SET current_agent='',active_agents_json='[]',"
                            "current_tool='',current_action='请审阅已恢复的检查点后继续',"
                            "status_reason='应用已重启；已保存的成果与检查点均保留',"
                            "run_started_at='',cumulative_run_seconds=CASE WHEN active_run_started_at IS NOT NULL THEN NULL ELSE cumulative_run_seconds END,active_run_started_at=NULL,updated_at=? WHERE project_id=?", (_now(), project_id),
                        )
                        conn.execute(
                            "UPDATE research_step_checkpoints SET status='PAUSED',updated_at=? "
                            "WHERE project_id=? AND status IN ('RUNNING','VERIFYING')", (_now(), project_id),
                        )
                        conn.execute(
                            "UPDATE research_intervention_queue SET status='QUEUED',updated_at=? "
                            "WHERE project_id=? AND status='RUNNING'", (_now(), project_id),
                        )
                    _RECOVERED_PATHS.add(path_key)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        """Additive, idempotent migrations for databases created by earlier releases."""
        additions = {
            "research_projects": {
                "lean_confirm_before_check": "INTEGER",
                "lean_auto_call_enabled": "INTEGER",
            },
            "research_tool_runs": {"metadata_json": "TEXT NOT NULL DEFAULT '{}'"},
            "research_sources": {
                "arxiv_id": "TEXT NOT NULL DEFAULT ''",
                "is_important": "INTEGER NOT NULL DEFAULT 0",
                "relevance": "REAL NOT NULL DEFAULT 0",
                "doi": "TEXT NOT NULL DEFAULT ''",
                "provider": "TEXT NOT NULL DEFAULT ''",
                "classification": "TEXT NOT NULL DEFAULT 'background'",
                "reading_status": "TEXT NOT NULL DEFAULT 'unread'",
                "notes": "TEXT NOT NULL DEFAULT ''",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                "first_seen_at": "TEXT NOT NULL DEFAULT ''",
                "last_seen_at": "TEXT NOT NULL DEFAULT ''",
                "document_blob": "BLOB",
            },
            "research_artifacts": {
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                "file_path": "TEXT NOT NULL DEFAULT ''",
            },
            "research_branches": {"priority": "INTEGER NOT NULL DEFAULT 3"},
            "research_memories": {
                "memory_kind": "TEXT NOT NULL DEFAULT 'note'",
                "tags_json": "TEXT NOT NULL DEFAULT '[]'",
                "origin_project": "TEXT NOT NULL DEFAULT ''",
                "use_count": "INTEGER NOT NULL DEFAULT 0",
                "last_used_at": "TEXT NOT NULL DEFAULT ''",
                "suggestion_key": "TEXT NOT NULL DEFAULT ''",
            },
            "research_workbench_state": {
                "current_tool": "TEXT NOT NULL DEFAULT ''",
                "active_agents_json": "TEXT NOT NULL DEFAULT '[]'",
                "step_index": "INTEGER NOT NULL DEFAULT 0",
                "step_total": "INTEGER NOT NULL DEFAULT 0",
                "milestone_index": "INTEGER NOT NULL DEFAULT 0",
                "milestone_total": "INTEGER NOT NULL DEFAULT 0",
                "phase": "TEXT NOT NULL DEFAULT ''",
                "last_budget_evaluation_json": "TEXT NOT NULL DEFAULT '{}'",
                "cumulative_run_seconds": "REAL",
                "active_run_started_at": "TEXT",
            },
        }
        for table, columns in additions.items():
            existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, declaration in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        conn.execute(
            "UPDATE research_sources SET first_seen_at=created_at WHERE first_seen_at=''"
        )
        conn.execute(
            "UPDATE research_sources SET last_seen_at=created_at WHERE last_seen_at=''"
        )
        conn.execute("UPDATE research_sources SET is_important=1,reading_status='unread' WHERE reading_status='important'")

    @staticmethod
    def _decode_plan(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = _safe_json(row["plan_json"], {})
        if not isinstance(result, dict):
            result = {}
        result.update(
            {
                "version": row["version"],
                "changedBy": row["changed_by"],
                "changeReason": row["change_reason"],
                "changeSummary": row["change_summary"],
                "approved": bool(row["approved"]),
                "createdAt": row["created_at"],
            }
        )
        return result

    def _project(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        version = conn.execute(
            "SELECT * FROM research_plan_versions WHERE project_id=? AND version=?",
            (row["id"], row["current_version"]),
        ).fetchone()
        revisions = conn.execute(
            "SELECT version,changed_by,change_reason,change_summary,approved,created_at "
            "FROM research_plan_versions WHERE project_id=? ORDER BY version DESC",
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "title": row["title"],
            "question": row["question"],
            "status": row["status"],
            "modelId": row["model_id"],
            "currentVersion": row["current_version"],
            "approvedVersion": row["approved_version"],
            "lastError": row["last_error"],
            "inputRequests": [
                {"id": item["id"], "label": item["label"], "reason": item["reason"],
                 "kind": item["kind"], "status": item["status"], "stepId": item["step_id"],
                 "planVersion": item["plan_version"], "response": item["response"],
                 "sourceIds": _safe_json(item["source_ids_json"], [])}
                for item in conn.execute(
                    "SELECT * FROM research_input_requests WHERE project_id=? AND plan_version=? ORDER BY created_at,id",
                    (row["id"], row["current_version"]),
                ).fetchall()
            ],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "plan": self._decode_plan(version),
            "revisions": [
                {
                    "version": item["version"],
                    "changedBy": item["changed_by"],
                    "changeReason": item["change_reason"],
                    "changeSummary": item["change_summary"],
                    "approved": bool(item["approved"]),
                    "createdAt": item["created_at"],
                }
                for item in revisions
            ],
        }

    def create_project(self, question: str, model_id: str) -> dict[str, Any]:
        project_id = _id("research")
        now = _now()
        title = question.strip().replace("\n", " ")[:100]
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO research_projects(id,title,question,status,model_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (project_id, title, question.strip(), "DRAFT", model_id, now, now),
            )
            # The workbench state table is introduced by the additive schema
            # migration. That migration backfills projects already present at
            # first open, while newly created projects need their row in this
            # same transaction so the workbench is immediately addressable.
            conn.execute(
                "INSERT INTO research_workbench_state(project_id,updated_at,cumulative_run_seconds) VALUES(?,?,0)",
                (project_id, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO research_branches(id,project_id,name,created_at,updated_at) VALUES(?,?,?,?,?)",
                (_id("branch"), project_id, "main", now, now),
            )
            row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            return self._project(conn, row)

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM research_projects ORDER BY updated_at DESC").fetchall()
            return [self._project(conn, row) for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            return self._project(conn, row) if row else None

    def set_model(self, project_id: str, model_id: str) -> bool:
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE research_projects SET model_id=?,updated_at=? WHERE id=?",
                (model_id, _now(), project_id),
            )
            return cursor.rowcount > 0

    def save_plan(
        self,
        project_id: str,
        plan: dict[str, Any],
        *,
        changed_by: str,
        change_reason: str,
        change_summary: str,
        status: str = "AWAITING_APPROVAL",
        generated: bool = False,
    ) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                return None
            previous_version = row["current_version"]
            previous = conn.execute(
                "SELECT plan_json FROM research_plan_versions WHERE project_id=? AND version=?",
                (project_id, previous_version),
            ).fetchone()
            if previous and not generated:
                old_plan = json.loads(previous["plan_json"])
                old_steps = {item["id"]: item for item in old_plan.get("steps", [])}
                for step in plan.get("steps", []):
                    old_step = old_steps.get(step["id"])
                    if old_step and old_step != step:
                        if old_step.get("status") == "completed":
                            step["status"] = "superseded"
                        else:
                            step["status"] = "changed"
                    elif old_step:
                        step["status"] = old_step.get("status", "pending")
                    else:
                        step["status"] = "pending"
                    if not step.get("enabled", True):
                        step["status"] = "cancelled"
            else:
                for step in plan.get("steps", []):
                    step["status"] = "pending" if step.get("enabled", True) else "cancelled"
            version = previous_version + 1
            now = _now()
            conn.execute(
                "INSERT INTO research_plan_versions(id,project_id,version,plan_json,changed_by,change_reason,change_summary,approved,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    _id("plan"),
                    project_id,
                    version,
                    json.dumps(plan, ensure_ascii=False),
                    changed_by,
                    change_reason[:1000],
                    change_summary[:4000],
                    0,
                    now,
                ),
            )
            declared_branches: list[tuple[str, str]] = [("main", "OPEN")]
            for item in plan.get("branches", []):
                if isinstance(item, dict):
                    name = str(item.get("title") or item.get("id") or "").strip()
                    status_value = "CLOSED" if item.get("status") == "closed" else "OPEN"
                    if name:
                        declared_branches.append((name[:120], status_value))
            for value in plan.get("researchBranches", []):
                name = str(value or "").strip()
                if name:
                    declared_branches.append((name[:120], "OPEN"))
            for item in plan.get("steps", []):
                name = str(item.get("branch") or "main").strip() or "main"
                declared_branches.append((name[:120], "OPEN"))
            main_branch = conn.execute(
                "SELECT id FROM research_branches WHERE project_id=? AND name='main'", (project_id,)
            ).fetchone()
            for branch_name, branch_status in dict(declared_branches).items():
                conn.execute(
                    "INSERT OR IGNORE INTO research_branches(id,project_id,name,parent_id,base_plan_version,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (_id("branch"), project_id, branch_name, main_branch["id"] if main_branch and branch_name != "main" else None, version, branch_status, now, now),
                )
                if branch_name != "main":
                    conn.execute("UPDATE research_branches SET status=?,updated_at=? WHERE project_id=? AND name=?", (branch_status, now, project_id, branch_name))
            # Editing a running plan suspends execution at its next step boundary.
            next_status = "AWAITING_APPROVAL" if row["status"] in {"RUNNING", "PAUSED", "REPLANNING"} else status
            self._transition_run_clock(conn, project_id, row["status"], next_status, now)
            conn.execute(
                "UPDATE research_projects SET current_version=?,approved_version=NULL,status=?,last_error='',updated_at=? WHERE id=?",
                (version, next_status, now, project_id),
            )
            self._intervention(
                conn,
                project_id,
                version,
                "plan_revision",
                changed_by,
                {"reason": change_reason, "summary": change_summary},
                now,
            )
            row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            return self._project(conn, row)

    def approve(self, project_id: str, actor: str = "researcher") -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            if not row or row["current_version"] <= 0:
                return None
            now = _now()
            conn.execute(
                "UPDATE research_plan_versions SET approved=1 WHERE project_id=? AND version=?",
                (project_id, row["current_version"]),
            )
            conn.execute(
                "UPDATE research_projects SET approved_version=current_version,status='APPROVED',updated_at=? WHERE id=?",
                (now, project_id),
            )
            self._intervention(conn, project_id, row["current_version"], "plan_approved", actor, {}, now)
            row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            return self._project(conn, row)

    @staticmethod
    def _transition_run_clock(conn: sqlite3.Connection, project_id: str, old_status: str, new_status: str, now: str) -> None:
        """Update the active segment in the same transaction as the status."""
        timing = conn.execute("SELECT cumulative_run_seconds,active_run_started_at FROM research_workbench_state WHERE project_id=?", (project_id,)).fetchone()
        running_before = old_status in {"RUNNING", "VERIFYING"}
        running_after = new_status in {"RUNNING", "VERIFYING"}
        if timing and not running_before and running_after:
            conn.execute("UPDATE research_workbench_state SET active_run_started_at=? WHERE project_id=?", (now, project_id))
        elif timing and running_before and not running_after:
            total, started = timing["cumulative_run_seconds"], timing["active_run_started_at"]
            if total is not None and started:
                try:
                    segment = (datetime.fromisoformat(now) - datetime.fromisoformat(started)).total_seconds()
                    total = total + segment if segment >= 0 else None
                except (ValueError, TypeError):
                    total = None
            else:
                total = None
            conn.execute("UPDATE research_workbench_state SET cumulative_run_seconds=?,active_run_started_at=NULL WHERE project_id=?", (total, project_id))

    def set_status(self, project_id: str, status: str, *, error: str = "", actor: str = "system") -> dict[str, Any] | None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                return None
            now = _now()
            self._transition_run_clock(conn, project_id, row["status"], status, now)
            conn.execute(
                "UPDATE research_projects SET status=?,last_error=?,updated_at=? WHERE id=?",
                (status, error[:2000], now, project_id),
            )
            self._intervention(conn, project_id, row["current_version"], status.lower(), actor, {"status": status}, now)
            row = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            return self._project(conn, row)

    def update_step(self, project_id: str, version: int, step_id: str, status: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT plan_json FROM research_plan_versions WHERE project_id=? AND version=?",
                (project_id, version),
            ).fetchone()
            if not row:
                return False
            plan = json.loads(row["plan_json"])
            found = False
            for step in plan.get("steps", []):
                if step.get("id") == step_id:
                    step["status"] = status
                    found = True
                    break
            if not found:
                return False
            conn.execute(
                "UPDATE research_plan_versions SET plan_json=? WHERE project_id=? AND version=?",
                (json.dumps(plan, ensure_ascii=False), project_id, version),
            )
            conn.execute("UPDATE research_projects SET updated_at=? WHERE id=?", (_now(), project_id))
            return True

    def create_artifact(
        self,
        project_id: str,
        version: int,
        step_id: str,
        agent_role: str,
        title: str,
        content: str,
        status: str = "llm-assessed-only",
        branch_name: str = "main",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact = {
            "id": _id("artifact"),
            "projectId": project_id,
            "branch": branch_name,
            "planVersion": version,
            "stepId": step_id,
            "agentRole": agent_role,
            "title": title,
            "content": content,
            "metadata": metadata or {},
            "status": status,
            "createdAt": _now(),
        }
        with self.connection() as conn:
            branch = conn.execute(
                "SELECT id FROM research_branches WHERE project_id=? AND name=?",
                (project_id, branch_name),
            ).fetchone()
            conn.execute(
                "INSERT INTO research_artifacts(id,project_id,branch_id,plan_version,step_id,agent_role,title,content,metadata_json,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    artifact["id"], project_id, branch["id"] if branch else None, version, step_id, agent_role,
                    title, content, json.dumps(metadata or {}, ensure_ascii=False), status, artifact["createdAt"],
                ),
            )
        try:
            from aleggo.study.research_workbench import ResearchWorkbench
            ResearchWorkbench(self).sync_workspace(project_id)
        except Exception:
            # SQLite is authoritative; workspace sync failures are surfaced in
            # the lifecycle state and never roll back a durable artifact.
            try:
                with self.connection() as conn:
                    conn.execute(
                        "UPDATE research_workbench_state SET workspace_status='ERROR',updated_at=? WHERE project_id=?",
                        (_now(), project_id),
                    )
            except Exception:
                pass
        return artifact

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT a.*,b.name AS branch_name FROM research_artifacts a "
                "LEFT JOIN research_branches b ON b.id=a.branch_id WHERE a.project_id=? ORDER BY a.created_at DESC",
                (project_id,),
            ).fetchall()
        return [
            {
                "id": row["id"], "projectId": row["project_id"],
                "branchId": row["branch_id"] if "branch_id" in row.keys() else None,
                "branch": row["branch_name"] if row["branch_name"] else "main",
                "planVersion": row["plan_version"], "stepId": row["step_id"],
                "agentRole": row["agent_role"], "title": row["title"],
                "content": row["content"],
                "metadata": _safe_json(row["metadata_json"] or "{}", {}) if "metadata_json" in row.keys() else {},
                "filePath": row["file_path"] if "file_path" in row.keys() else "",
                "status": row["status"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def set_artifact_delivery(self, artifact_id: str, project_id: str, file_path: str, metadata: dict[str, Any] | None = None) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM research_artifacts WHERE id=? AND project_id=?",
                (artifact_id, project_id),
            ).fetchone()
            if not row:
                return False
            merged = json.loads(row["metadata_json"] or "{}")
            merged.update(metadata or {})
            conn.execute(
                "UPDATE research_artifacts SET file_path=?,metadata_json=? WHERE id=? AND project_id=?",
                (file_path[:4000], json.dumps(merged, ensure_ascii=False), artifact_id, project_id),
            )
            return True

    def list_interventions(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM research_interventions WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [
            {
                "id": row["id"], "projectId": row["project_id"],
                "planVersion": row["plan_version"], "kind": row["kind"],
                "actor": row["actor"], "details": _safe_json(row["details_json"], {}),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _intervention(
        conn: sqlite3.Connection,
        project_id: str,
        version: int | None,
        kind: str,
        actor: str,
        details: dict[str, Any],
        at: str,
    ) -> None:
        conn.execute(
            "INSERT INTO research_interventions(id,project_id,plan_version,kind,actor,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (_id("event"), project_id, version, kind, actor, json.dumps(details, ensure_ascii=False), at),
        )
