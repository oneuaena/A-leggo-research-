"""SQLite storage and permission guard for the A-leggo Research self-improvement system.

Enforces:
- Append-only TaskTrace records via SQLite BEFORE UPDATE / BEFORE DELETE triggers.
- Strict memory isolation between Execution Agent A and Execution Agent B.
- Role-based write restrictions (only StrategyManager can write strategies, proposals, or audit logs).
- Experience memory compression with full provenance (compressed_from + source_trace_ids).
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator

from aleggo.study.self_improvement.models import (
    AuditLog,
    Experience,
    ExperimentReport,
    ExperimentTask,
    SelfImprovementSettings,
    Strategy,
    StrategyProposal,
    TaskTrace,
    new_id,
    utc_now_iso,
)
from aleggo.study.self_improvement.seed_strategies import (
    build_meta_seed_strategies,
    build_seed_strategies_for_agent,
)


class PermissionViolationError(PermissionError):
    """Raised when an Agent or external caller attempts an unauthorized read or write operation."""


VALID_EXECUTION_AGENTS = frozenset({"agent_a", "agent_b"})
VALID_ROLES = frozenset({"agent_a", "agent_b", "reflector", "meta", "practitioner", "strategy_manager", "human"})


class SelfImprovementStore:
    """SQLite store for TaskTrace, Experience, Strategy, StrategyProposal, ExperimentTask/Report, and AuditLog."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._strategy_manager_token = object()
        self._init_schema()
        self._ensure_seed_strategies()

    def _bind_strategy_manager(self, manager: Any) -> object:
        """Bind a StrategyManager instance and return the private capability token."""
        if type(manager).__name__ != "StrategyManager":
            raise PermissionViolationError("Only StrategyManager may bind to SelfImprovementStore state mutators.")
        return self._strategy_manager_token

    def _verify_manager_token(self, token: object | None, action: str) -> None:
        if token is not self._strategy_manager_token:
            raise PermissionViolationError(
                f"Permission denied: '{action}' is private and may only be invoked through StrategyManager."
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS si_task_traces (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    problem_text TEXT NOT NULL,
                    available_strategy_versions_json TEXT NOT NULL,
                    strategy_selection_json TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    final_answer TEXT NOT NULL,
                    verification_result_json TEXT NOT NULL,
                    token_usage_json TEXT NOT NULL,
                    total_elapsed_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    legacy_demo INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_si_traces_agent ON si_task_traces(agent_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_si_traces_task ON si_task_traces(task_id);

                CREATE TRIGGER IF NOT EXISTS trg_si_task_traces_no_update
                BEFORE UPDATE ON si_task_traces
                BEGIN
                    SELECT RAISE(ABORT, 'TaskTrace is append-only: UPDATE is forbidden by Hard Principle 5.');
                END;

                CREATE TRIGGER IF NOT EXISTS trg_si_task_traces_no_delete
                BEFORE DELETE ON si_task_traces
                BEGIN
                    SELECT RAISE(ABORT, 'TaskTrace is append-only: DELETE is forbidden by Hard Principle 5.');
                END;

                CREATE TABLE IF NOT EXISTS si_experiences (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    experience_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_trace_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    is_compressed INTEGER NOT NULL DEFAULT 0,
                    compressed_from_json TEXT NOT NULL DEFAULT '[]',
                    strategy_ids_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    archived INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_si_experiences_agent ON si_experiences(agent_id, archived, created_at DESC);

                CREATE TABLE IF NOT EXISTS si_strategies (
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    applicable_conditions TEXT NOT NULL,
                    execution_steps_json TEXT NOT NULL,
                    taboos_and_counterexamples_json TEXT NOT NULL,
                    parent_version INTEGER,
                    origin_proposal_id TEXT,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    proposed_by TEXT NOT NULL,
                    approved_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    legacy_demo INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_si_strategies_owner_status ON si_strategies(owner, status);

                CREATE TABLE IF NOT EXISTS si_proposals (
                    id TEXT PRIMARY KEY,
                    target_agent TEXT NOT NULL,
                    base_strategy_id TEXT,
                    base_strategy_version INTEGER,
                    new_strategy_id TEXT,
                    new_strategy_version INTEGER,
                    problem_found TEXT NOT NULL,
                    claimed_failure_signature TEXT NOT NULL DEFAULT '',
                    novel_method_evidence TEXT NOT NULL DEFAULT '',
                    recommendations_json TEXT NOT NULL DEFAULT '[]',
                    evidence_trace_ids_json TEXT NOT NULL,
                    experience_ids_json TEXT NOT NULL DEFAULT '[]',
                    structured_changes_json TEXT NOT NULL,
                    expected_benefit TEXT NOT NULL,
                    applicable_scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    review_comment TEXT NOT NULL DEFAULT '',
                    reviewed_by TEXT,
                    proposed_by TEXT NOT NULL DEFAULT 'reflector',
                    token_usage_json TEXT NOT NULL DEFAULT '{}',
                    raw_model_output TEXT NOT NULL DEFAULT '',
                    legacy_demo INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_si_proposals_status ON si_proposals(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS si_experiment_tasks (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    target_agent TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    old_strategy_version INTEGER,
                    new_strategy_version INTEGER NOT NULL,
                    benchmark_problem_ids_json TEXT NOT NULL,
                    runs_per_problem_k INTEGER NOT NULL,
                    metrics_and_thresholds_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    legacy_demo INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS si_experiment_reports (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    target_agent TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    old_strategy_version INTEGER,
                    new_strategy_version INTEGER NOT NULL,
                    old_run_trace_ids_json TEXT NOT NULL,
                    new_run_trace_ids_json TEXT NOT NULL,
                    all_trace_ids_json TEXT NOT NULL,
                    old_pass_rate REAL NOT NULL,
                    new_pass_rate REAL NOT NULL,
                    old_avg_tokens REAL NOT NULL,
                    new_avg_tokens REAL NOT NULL,
                    old_avg_elapsed_ms REAL NOT NULL,
                    new_avg_elapsed_ms REAL NOT NULL,
                    problem_breakdown_json TEXT NOT NULL,
                    rule_judgment_json TEXT NOT NULL,
                    meta_review_comment TEXT NOT NULL DEFAULT '',
                    meta_approved INTEGER,
                    human_approved INTEGER,
                    raw_model_output TEXT NOT NULL DEFAULT '',
                    legacy_demo INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS si_legacy_demo_markers (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    marked_at TEXT NOT NULL,
                    PRIMARY KEY (entity_type, entity_id)
                );

                CREATE TABLE IF NOT EXISTS si_audit_logs (
                    id TEXT PRIMARY KEY,
                    who TEXT NOT NULL,
                    when_ts TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_si_audit_when ON si_audit_logs(when_ts DESC);

                CREATE TRIGGER IF NOT EXISTS trg_si_audit_logs_no_update
                BEFORE UPDATE ON si_audit_logs
                BEGIN
                    SELECT RAISE(ABORT, 'AuditLog is append-only: UPDATE is forbidden.');
                END;

                CREATE TRIGGER IF NOT EXISTS trg_si_audit_logs_no_delete
                BEFORE DELETE ON si_audit_logs
                BEGIN
                    SELECT RAISE(ABORT, 'AuditLog is append-only: DELETE is forbidden.');
                END;

                CREATE TABLE IF NOT EXISTS si_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                """
            )
            # Migrate existing tables if columns are missing
            self._ensure_column(conn, "si_task_traces", "legacy_demo", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "si_strategies", "legacy_demo", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "si_proposals", "raw_model_output", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "si_proposals", "legacy_demo", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "si_proposals", "claimed_failure_signature", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "si_proposals", "novel_method_evidence", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "si_proposals", "recommendations_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "si_experiment_tasks", "legacy_demo", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "si_experiment_reports", "raw_model_output", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "si_experiment_reports", "legacy_demo", "INTEGER NOT NULL DEFAULT 0")

    def _ensure_seed_strategies(self) -> None:
        with self.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM si_strategies").fetchone()[0]
            if count > 0:
                return
            seeds: list[Strategy] = []
            for agent_id in ("agent_a", "agent_b"):
                seeds.extend(build_seed_strategies_for_agent(agent_id))
            seeds.extend(build_meta_seed_strategies())
            for strat in seeds:
                self._insert_strategy_row(conn, strat)
                audit = AuditLog(
                    who="strategy_manager",
                    when=strat.created_at,
                    target_type="strategy",
                    target_id=f"{strat.id}:v{strat.version}",
                    from_state="none",
                    to_state=strat.status,
                    rationale=f"初始化种子策略（owner={strat.owner}）",
                    metadata={"strategy_id": strat.id, "version": strat.version, "owner": strat.owner},
                )
                self._insert_audit_row(conn, audit)

    def _get_legacy_markers(self, conn: sqlite3.Connection, entity_type: str) -> set[str]:
        rows = conn.execute(
            "SELECT entity_id FROM si_legacy_demo_markers WHERE entity_type = ?",
            (entity_type,),
        ).fetchall()
        return {str(r["entity_id"]) for r in rows}

    def mark_legacy_demo(
        self,
        *,
        strategy_ids: list[str] | None = None,
        trace_ids: list[str] | None = None,
        proposal_ids: list[str] | None = None,
        task_ids: list[str] | None = None,
        report_ids: list[str] | None = None,
        reason: str = "归档旧版造失败演示数据（legacy_demo）",
        _manager_token: object | None = None,
    ) -> None:
        """Mark strategies, traces, proposals, tasks, and reports as legacy_demo (hidden by default)."""
        self._verify_manager_token(_manager_token, "mark_legacy_demo")
        now = utc_now_iso()
        with self.connection() as conn:
            for sid in strategy_ids or []:
                conn.execute("UPDATE si_strategies SET legacy_demo = 1 WHERE id = ?", (sid,))
                conn.execute(
                    "INSERT OR REPLACE INTO si_legacy_demo_markers (entity_type, entity_id, reason, marked_at) VALUES ('strategy', ?, ?, ?)",
                    (sid, reason, now),
                )
            for tid in trace_ids or []:
                # TaskTrace is append-only (UPDATE forbidden by trigger), so record in si_legacy_demo_markers
                conn.execute(
                    "INSERT OR REPLACE INTO si_legacy_demo_markers (entity_type, entity_id, reason, marked_at) VALUES ('trace', ?, ?, ?)",
                    (tid, reason, now),
                )
            for pid in proposal_ids or []:
                conn.execute("UPDATE si_proposals SET legacy_demo = 1 WHERE id = ?", (pid,))
                conn.execute(
                    "INSERT OR REPLACE INTO si_legacy_demo_markers (entity_type, entity_id, reason, marked_at) VALUES ('proposal', ?, ?, ?)",
                    (pid, reason, now),
                )
            for tkid in task_ids or []:
                conn.execute("UPDATE si_experiment_tasks SET legacy_demo = 1 WHERE id = ?", (tkid,))
                conn.execute(
                    "INSERT OR REPLACE INTO si_legacy_demo_markers (entity_type, entity_id, reason, marked_at) VALUES ('experiment_task', ?, ?, ?)",
                    (tkid, reason, now),
                )
            for rpid in report_ids or []:
                conn.execute("UPDATE si_experiment_reports SET legacy_demo = 1 WHERE id = ?", (rpid,))
                conn.execute(
                    "INSERT OR REPLACE INTO si_legacy_demo_markers (entity_type, entity_id, reason, marked_at) VALUES ('experiment_report', ?, ?, ?)",
                    (rpid, reason, now),
                )

    # ------------------------------------------------------------------
    # Permission checks
    # ------------------------------------------------------------------
    @staticmethod
    def assert_can_read_agent_memory(caller_role: str, target_agent_id: str) -> None:
        """Enforce memory isolation between Execution Agent A and Execution Agent B."""
        if caller_role in VALID_EXECUTION_AGENTS and caller_role != target_agent_id:
            raise PermissionViolationError(
                f"Permission denied: {caller_role} is forbidden from reading {target_agent_id}'s private memory or strategies."
            )
        if caller_role == "practitioner" and target_agent_id in VALID_EXECUTION_AGENTS:
            raise PermissionViolationError(
                "Permission denied: practitioner agent cannot read execution agents' private experience memory."
            )

    @staticmethod
    def assert_can_write_trace(caller_role: str, trace_agent_id: str) -> None:
        if caller_role in VALID_EXECUTION_AGENTS and caller_role != trace_agent_id:
            raise PermissionViolationError(
                f"Permission denied: {caller_role} cannot write TaskTrace for {trace_agent_id}."
            )
        if caller_role in {"reflector", "meta"}:
            raise PermissionViolationError(f"Permission denied: {caller_role} cannot write TaskTrace records.")

    @staticmethod
    def assert_can_write_experience(caller_role: str, exp_agent_id: str) -> None:
        if caller_role != exp_agent_id and caller_role != "strategy_manager":
            raise PermissionViolationError(
                f"Permission denied: {caller_role} cannot write Experience entries for {exp_agent_id}."
            )

    @staticmethod
    def assert_strategy_manager_only(caller_role: str, action: str) -> None:
        if caller_role != "strategy_manager":
            raise PermissionViolationError(
                f"Permission denied: only 'strategy_manager' may perform '{action}' (attempted by '{caller_role}')."
            )

    # ------------------------------------------------------------------
    # TaskTrace (Append-only)
    # ------------------------------------------------------------------
    def append_trace(self, trace: TaskTrace, *, caller_role: str | None = None) -> TaskTrace:
        role = caller_role or trace.agent_id
        self.assert_can_write_trace(role, trace.agent_id)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO si_task_traces (
                    id, agent_id, task_id, problem_text,
                    available_strategy_versions_json, strategy_selection_json,
                    steps_json, final_answer, verification_result_json,
                    token_usage_json, total_elapsed_ms, created_at, legacy_demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.id,
                    trace.agent_id,
                    trace.task_id,
                    trace.problem_text,
                    json.dumps([item.model_dump() for item in trace.available_strategy_versions], ensure_ascii=False),
                    json.dumps(trace.strategy_selection.model_dump(), ensure_ascii=False),
                    json.dumps([step.model_dump() for step in trace.steps], ensure_ascii=False),
                    trace.final_answer,
                    json.dumps(trace.verification_result.model_dump(), ensure_ascii=False),
                    json.dumps(trace.token_usage.model_dump(), ensure_ascii=False),
                    trace.total_elapsed_ms,
                    trace.created_at,
                    1 if trace.legacy_demo else 0,
                ),
            )
        return trace

    def get_trace(self, trace_id: str, *, caller_role: str = "strategy_manager") -> TaskTrace | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM si_task_traces WHERE id = ?", (trace_id,)).fetchone()
            if not row:
                return None
            legacy_markers = self._get_legacy_markers(conn, "trace")
            trace = self._row_to_trace(row, legacy_markers)
            if caller_role in VALID_EXECUTION_AGENTS and trace.agent_id != caller_role:
                raise PermissionViolationError(
                    f"Permission denied: {caller_role} cannot read TaskTrace {trace_id} belonging to {trace.agent_id}."
                )
            return trace

    def list_traces(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        caller_role: str = "strategy_manager",
        include_legacy_demo: bool = False,
    ) -> list[TaskTrace]:
        if caller_role in VALID_EXECUTION_AGENTS:
            if agent_id and agent_id != caller_role:
                raise PermissionViolationError(
                    f"Permission denied: {caller_role} cannot list traces of {agent_id}."
                )
            agent_id = caller_role
        query = "SELECT * FROM si_task_traces WHERE 1=1"
        params: list[Any] = []
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit * 2 if not include_legacy_demo else limit)
        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            legacy_markers = self._get_legacy_markers(conn, "trace")
        traces = [self._row_to_trace(r, legacy_markers) for r in rows]
        if not include_legacy_demo:
            traces = [t for t in traces if not t.legacy_demo]
        return traces[:limit]

    @staticmethod
    def _row_to_trace(row: sqlite3.Row, legacy_markers: set[str] | None = None) -> TaskTrace:
        tid = str(row["id"])
        col_legacy = bool(row["legacy_demo"]) if "legacy_demo" in row.keys() else False
        is_legacy = col_legacy or (legacy_markers is not None and tid in legacy_markers)
        return TaskTrace(
            id=tid,
            agent_id=row["agent_id"],
            task_id=row["task_id"],
            problem_text=row["problem_text"],
            available_strategy_versions=json.loads(row["available_strategy_versions_json"]),
            strategy_selection=json.loads(row["strategy_selection_json"]),
            steps=json.loads(row["steps_json"]),
            final_answer=row["final_answer"],
            verification_result=json.loads(row["verification_result_json"]),
            token_usage=json.loads(row["token_usage_json"]),
            total_elapsed_ms=int(row["total_elapsed_ms"]),
            created_at=row["created_at"],
            legacy_demo=is_legacy,
        )

    # ------------------------------------------------------------------
    # Experience & Memory Compression
    # ------------------------------------------------------------------
    def add_experience(self, exp: Experience, *, caller_role: str | None = None) -> Experience:
        role = caller_role or exp.agent_id
        self.assert_can_write_experience(role, exp.agent_id)
        with self.connection() as conn:
            for tid in exp.source_trace_ids:
                exists = conn.execute("SELECT 1 FROM si_task_traces WHERE id = ?", (tid,)).fetchone()
                if not exists:
                    raise ValueError(f"Experience source_trace_id '{tid}' does not exist in si_task_traces.")
            conn.execute(
                """
                INSERT INTO si_experiences (
                    id, agent_id, experience_type, content, source_trace_ids_json,
                    created_at, is_compressed, compressed_from_json, strategy_ids_json, tags_json, archived
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    exp.id,
                    exp.agent_id,
                    exp.experience_type,
                    exp.content,
                    json.dumps(exp.source_trace_ids, ensure_ascii=False),
                    exp.created_at,
                    1 if exp.is_compressed else 0,
                    json.dumps(exp.compressed_from, ensure_ascii=False),
                    json.dumps(exp.strategy_ids, ensure_ascii=False),
                    json.dumps(exp.tags, ensure_ascii=False),
                ),
            )
        return exp

    def list_experiences(
        self,
        agent_id: str,
        *,
        caller_role: str,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[Experience]:
        self.assert_can_read_agent_memory(caller_role, agent_id)
        query = "SELECT * FROM si_experiences WHERE agent_id = ?"
        params: list[Any] = [agent_id]
        if not include_archived:
            query += " AND archived = 0"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_experience(r) for r in rows]

    def compress_experiences(self, agent_id: str, *, caller_role: str | None = None) -> list[Experience]:
        """Basic memory compression: merge duplicate/near-duplicate experiences under the same agent_id.

        Preserves compressed_from (original experience IDs) and all source_trace_ids.
        """
        role = caller_role or agent_id
        self.assert_can_write_experience(role, agent_id)
        active_exps = self.list_experiences(agent_id, caller_role=role, include_archived=False, limit=500)
        groups: dict[tuple[str, str], list[Experience]] = {}
        for exp in active_exps:
            norm_key = re.sub(r"\s+", " ", exp.content.strip().lower())[:120]
            key = (exp.experience_type, norm_key)
            groups.setdefault(key, []).append(exp)

        newly_compressed: list[Experience] = []
        with self.connection() as conn:
            for (exp_type, _), items in groups.items():
                if len(items) < 2:
                    continue
                merged_traces: list[str] = []
                compressed_from: list[str] = []
                merged_strategies: list[str] = []
                merged_tags: list[str] = []
                for item in items:
                    compressed_from.append(item.id)
                    for tid in item.source_trace_ids:
                        if tid not in merged_traces:
                            merged_traces.append(tid)
                    for sid in item.strategy_ids:
                        if sid not in merged_strategies:
                            merged_strategies.append(sid)
                    for tag in item.tags:
                        if tag not in merged_tags:
                            merged_tags.append(tag)
                compressed_exp = Experience(
                    id=new_id("exp_cmp"),
                    agent_id=agent_id,
                    experience_type=exp_type,  # type: ignore[arg-type]
                    content=items[0].content,
                    source_trace_ids=merged_traces,
                    created_at=utc_now_iso(),
                    is_compressed=True,
                    compressed_from=compressed_from,
                    strategy_ids=merged_strategies,
                    tags=merged_tags,
                )
                conn.execute(
                    """
                    INSERT INTO si_experiences (
                        id, agent_id, experience_type, content, source_trace_ids_json,
                        created_at, is_compressed, compressed_from_json, strategy_ids_json, tags_json, archived
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 0)
                    """,
                    (
                        compressed_exp.id,
                        compressed_exp.agent_id,
                        compressed_exp.experience_type,
                        compressed_exp.content,
                        json.dumps(compressed_exp.source_trace_ids, ensure_ascii=False),
                        compressed_exp.created_at,
                        json.dumps(compressed_exp.compressed_from, ensure_ascii=False),
                        json.dumps(compressed_exp.strategy_ids, ensure_ascii=False),
                        json.dumps(compressed_exp.tags, ensure_ascii=False),
                    ),
                )
                placeholders = ",".join("?" for _ in compressed_from)
                conn.execute(
                    f"UPDATE si_experiences SET archived = 1 WHERE id IN ({placeholders})",
                    tuple(compressed_from),
                )
                newly_compressed.append(compressed_exp)
        return newly_compressed

    @staticmethod
    def _row_to_experience(row: sqlite3.Row) -> Experience:
        return Experience(
            id=row["id"],
            agent_id=row["agent_id"],
            experience_type=row["experience_type"],
            content=row["content"],
            source_trace_ids=json.loads(row["source_trace_ids_json"]),
            created_at=row["created_at"],
            is_compressed=bool(row["is_compressed"]),
            compressed_from=json.loads(row["compressed_from_json"]),
            strategy_ids=json.loads(row["strategy_ids_json"]),
            tags=json.loads(row["tags_json"]),
        )

    # ------------------------------------------------------------------
    # Strategies (Read isolated by owner for Execution Agents; Write restricted exclusively to StrategyManager)
    # ------------------------------------------------------------------
    def _insert_strategy_row(self, conn: sqlite3.Connection, strategy: Strategy) -> None:
        conn.execute(
            """
            INSERT INTO si_strategies (
                id, version, name, owner, status, applicable_conditions,
                execution_steps_json, taboos_and_counterexamples_json,
                parent_version, origin_proposal_id, evidence_json,
                proposed_by, approved_by, created_at, updated_at, legacy_demo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy.id,
                strategy.version,
                strategy.name,
                strategy.owner,
                strategy.status,
                strategy.applicable_conditions,
                json.dumps(strategy.execution_steps, ensure_ascii=False),
                json.dumps(strategy.taboos_and_counterexamples, ensure_ascii=False),
                strategy.parent_version,
                strategy.origin_proposal_id,
                json.dumps(strategy.evidence, ensure_ascii=False),
                strategy.proposed_by,
                strategy.approved_by,
                strategy.created_at,
                strategy.updated_at,
                1 if strategy.legacy_demo else 0,
            ),
        )

    def _save_new_strategy_version(
        self,
        strategy: Strategy,
        *,
        _manager_token: object | None = None,
    ) -> Strategy:
        self._verify_manager_token(_manager_token, "_save_new_strategy_version")
        with self.connection() as conn:
            self._insert_strategy_row(conn, strategy)
        return strategy

    def save_new_strategy_version(self, *args: Any, **kwargs: Any) -> Strategy:
        raise PermissionViolationError(
            "Direct call to store.save_new_strategy_version is forbidden. "
            "All strategy creations and state changes must go through StrategyManager."
        )

    def _update_strategy_status(
        self,
        strategy_id: str,
        version: int,
        new_status: str,
        *,
        approved_by: str | None = None,
        evidence: list[str] | None = None,
        _manager_token: object | None = None,
    ) -> Strategy:
        self._verify_manager_token(_manager_token, "_update_strategy_status")
        now = utc_now_iso()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM si_strategies WHERE id = ? AND version = ?",
                (strategy_id, version),
            ).fetchone()
            if not row:
                raise KeyError(f"Strategy {strategy_id} v{version} not found.")
            legacy_markers = self._get_legacy_markers(conn, "strategy")
            current = self._row_to_strategy(row, legacy_markers)
            merged_evidence = list(dict.fromkeys(current.evidence + (evidence or [])))
            final_approved_by = approved_by if approved_by is not None else current.approved_by
            conn.execute(
                """
                UPDATE si_strategies
                SET status = ?, approved_by = ?, evidence_json = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    new_status,
                    final_approved_by,
                    json.dumps(merged_evidence, ensure_ascii=False),
                    now,
                    strategy_id,
                    version,
                ),
            )
            updated_row = conn.execute(
                "SELECT * FROM si_strategies WHERE id = ? AND version = ?",
                (strategy_id, version),
            ).fetchone()
            assert updated_row is not None
            return self._row_to_strategy(updated_row, legacy_markers)

    def update_strategy_status(self, *args: Any, **kwargs: Any) -> Strategy:
        raise PermissionViolationError(
            "Direct call to store.update_strategy_status is forbidden. "
            "All strategy state transitions must go through StrategyManager.transition_strategy_status."
        )

    def get_strategy(
        self,
        strategy_id: str,
        version: int | None = None,
        *,
        caller_role: str = "strategy_manager",
    ) -> Strategy | None:
        with self.connection() as conn:
            if version is not None:
                row = conn.execute(
                    "SELECT * FROM si_strategies WHERE id = ? AND version = ?",
                    (strategy_id, version),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM si_strategies WHERE id = ? ORDER BY version DESC LIMIT 1",
                    (strategy_id,),
                ).fetchone()
            legacy_markers = self._get_legacy_markers(conn, "strategy")
        if not row:
            return None
        strat = self._row_to_strategy(row, legacy_markers)
        self.assert_can_read_agent_memory(caller_role, strat.owner)
        return strat

    def list_strategies(
        self,
        *,
        owner: str | None = None,
        status: str | None = None,
        caller_role: str = "strategy_manager",
        include_legacy_demo: bool = False,
    ) -> list[Strategy]:
        if caller_role in VALID_EXECUTION_AGENTS:
            if owner and owner != caller_role:
                self.assert_can_read_agent_memory(caller_role, owner)
            owner = caller_role
        query = "SELECT * FROM si_strategies WHERE 1=1"
        params: list[Any] = []
        if owner:
            query += " AND owner = ?"
            params.append(owner)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY owner ASC, id ASC, version ASC"
        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            legacy_markers = self._get_legacy_markers(conn, "strategy")
        strategies = [self._row_to_strategy(r, legacy_markers) for r in rows]
        if not include_legacy_demo:
            strategies = [s for s in strategies if not s.legacy_demo]
        return strategies

    @staticmethod
    def _row_to_strategy(row: sqlite3.Row, legacy_markers: set[str] | None = None) -> Strategy:
        sid = str(row["id"])
        col_legacy = bool(row["legacy_demo"]) if "legacy_demo" in row.keys() else False
        is_legacy = col_legacy or (legacy_markers is not None and sid in legacy_markers)
        return Strategy(
            id=sid,
            name=row["name"],
            version=int(row["version"]),
            owner=row["owner"],
            status=row["status"],
            applicable_conditions=row["applicable_conditions"],
            execution_steps=json.loads(row["execution_steps_json"]),
            taboos_and_counterexamples=json.loads(row["taboos_and_counterexamples_json"]),
            parent_version=row["parent_version"],
            origin_proposal_id=row["origin_proposal_id"],
            evidence=json.loads(row["evidence_json"]),
            proposed_by=row["proposed_by"],
            approved_by=row["approved_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            legacy_demo=is_legacy,
        )

    # ------------------------------------------------------------------
    # StrategyProposal (Write restricted exclusively to StrategyManager)
    # ------------------------------------------------------------------
    def _save_proposal(
        self,
        proposal: StrategyProposal,
        *,
        caller_role: str = "strategy_manager",
        _manager_token: object | None = None,
    ) -> StrategyProposal:
        self._verify_manager_token(_manager_token, "_save_proposal")
        if caller_role not in {"reflector", "meta", "strategy_manager", "human"}:
            raise PermissionViolationError(
                f"Permission denied: role '{caller_role}' is not allowed to submit or update StrategyProposals."
            )
        if caller_role == "reflector" and proposal.target_agent == "meta":
            raise PermissionViolationError(
                "Permission denied: reflector agent cannot submit proposals for 'meta' strategies."
            )
        with self.connection() as conn:
            for tid in proposal.evidence_trace_ids:
                row = conn.execute("SELECT id FROM si_task_traces WHERE id = ?", (tid,)).fetchone()
                if not row:
                    raise ValueError(f"Proposal evidence_trace_id '{tid}' does not exist in si_task_traces.")
            for eid in proposal.experience_ids:
                erow = conn.execute("SELECT source_trace_ids_json FROM si_experiences WHERE id = ?", (eid,)).fetchone()
                if not erow or not json.loads(erow["source_trace_ids_json"]):
                    raise ValueError(f"Experience '{eid}' lacks raw TaskTrace provenance and cannot be used as evidence.")
            conn.execute(
                """
                INSERT OR REPLACE INTO si_proposals (
                    id, target_agent, base_strategy_id, base_strategy_version,
                    new_strategy_id, new_strategy_version, problem_found,
                    claimed_failure_signature, novel_method_evidence,
                    recommendations_json,
                    evidence_trace_ids_json, experience_ids_json, structured_changes_json,
                    expected_benefit, applicable_scope, status, review_comment,
                    reviewed_by, proposed_by, token_usage_json, raw_model_output,
                    legacy_demo, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.id,
                    proposal.target_agent,
                    proposal.base_strategy_id,
                    proposal.base_strategy_version,
                    proposal.new_strategy_id,
                    proposal.new_strategy_version,
                    proposal.problem_found,
                    proposal.claimed_failure_signature,
                    proposal.novel_method_evidence,
                    json.dumps(proposal.recommendations, ensure_ascii=False),
                    json.dumps(proposal.evidence_trace_ids, ensure_ascii=False),
                    json.dumps(proposal.experience_ids, ensure_ascii=False),
                    json.dumps(proposal.structured_changes.model_dump(), ensure_ascii=False),
                    proposal.expected_benefit,
                    proposal.applicable_scope,
                    proposal.status,
                    proposal.review_comment,
                    proposal.reviewed_by,
                    proposal.proposed_by,
                    json.dumps(proposal.token_usage.model_dump(), ensure_ascii=False),
                    proposal.raw_model_output,
                    1 if proposal.legacy_demo else 0,
                    proposal.created_at,
                    proposal.updated_at,
                ),
            )
        return proposal

    def save_proposal(self, *args: Any, **kwargs: Any) -> StrategyProposal:
        raise PermissionViolationError(
            "Direct call to store.save_proposal is forbidden. "
            "All proposal registrations and status transitions must go through StrategyManager."
        )

    def get_proposal(self, proposal_id: str) -> StrategyProposal | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM si_proposals WHERE id = ?", (proposal_id,)).fetchone()
            legacy_markers = self._get_legacy_markers(conn, "proposal")
        return self._row_to_proposal(row, legacy_markers) if row else None

    def list_proposals(
        self,
        *,
        target_agent: str | None = None,
        status: str | None = None,
        include_legacy_demo: bool = False,
    ) -> list[StrategyProposal]:
        query = "SELECT * FROM si_proposals WHERE 1=1"
        params: list[Any] = []
        if target_agent:
            query += " AND target_agent = ?"
            params.append(target_agent)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            legacy_markers = self._get_legacy_markers(conn, "proposal")
        proposals = [self._row_to_proposal(r, legacy_markers) for r in rows]
        if not include_legacy_demo:
            proposals = [p for p in proposals if not p.legacy_demo]
        return proposals

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row, legacy_markers: set[str] | None = None) -> StrategyProposal:
        pid = str(row["id"])
        keys = row.keys()
        col_legacy = bool(row["legacy_demo"]) if "legacy_demo" in keys else False
        is_legacy = col_legacy or (legacy_markers is not None and pid in legacy_markers)
        return StrategyProposal(
            id=pid,
            target_agent=row["target_agent"],
            base_strategy_id=row["base_strategy_id"],
            base_strategy_version=row["base_strategy_version"],
            new_strategy_id=row["new_strategy_id"],
            new_strategy_version=row["new_strategy_version"],
            problem_found=row["problem_found"],
            claimed_failure_signature=row["claimed_failure_signature"] if "claimed_failure_signature" in keys else "",
            novel_method_evidence=row["novel_method_evidence"] if "novel_method_evidence" in keys else "",
            recommendations=json.loads(row["recommendations_json"] or "[]") if "recommendations_json" in keys else [],
            evidence_trace_ids=json.loads(row["evidence_trace_ids_json"]),
            experience_ids=json.loads(row["experience_ids_json"]),
            structured_changes=json.loads(row["structured_changes_json"]),
            expected_benefit=row["expected_benefit"],
            applicable_scope=row["applicable_scope"],
            status=row["status"],
            review_comment=row["review_comment"],
            reviewed_by=row["reviewed_by"],
            proposed_by=row["proposed_by"],
            token_usage=json.loads(row["token_usage_json"] or "{}"),
            raw_model_output=str(row["raw_model_output"] or "") if "raw_model_output" in keys else "",
            legacy_demo=is_legacy,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # ExperimentTask & ExperimentReport
    # ------------------------------------------------------------------
    def save_experiment_task(self, task: ExperimentTask, *, caller_role: str) -> ExperimentTask:
        if caller_role not in {"meta", "strategy_manager", "practitioner"}:
            raise PermissionViolationError(f"Permission denied: '{caller_role}' cannot save ExperimentTask.")
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO si_experiment_tasks (
                    id, proposal_id, target_agent, strategy_id,
                    old_strategy_version, new_strategy_version,
                    benchmark_problem_ids_json, runs_per_problem_k,
                    metrics_and_thresholds_json, status, legacy_demo, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.proposal_id,
                    task.target_agent,
                    task.strategy_id,
                    task.old_strategy_version,
                    task.new_strategy_version,
                    json.dumps(task.benchmark_problem_ids, ensure_ascii=False),
                    task.runs_per_problem_k,
                    json.dumps(task.metrics_and_thresholds.model_dump(), ensure_ascii=False),
                    task.status,
                    1 if task.legacy_demo else 0,
                    task.created_at,
                ),
            )
        return task

    def get_experiment_task(self, task_id: str) -> ExperimentTask | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM si_experiment_tasks WHERE id = ?", (task_id,)).fetchone()
            legacy_markers = self._get_legacy_markers(conn, "experiment_task")
        return self._row_to_experiment_task(row, legacy_markers) if row else None

    def list_experiment_tasks(
        self,
        *,
        proposal_id: str | None = None,
        include_legacy_demo: bool = False,
    ) -> list[ExperimentTask]:
        query = "SELECT * FROM si_experiment_tasks WHERE 1=1"
        params: list[Any] = []
        if proposal_id:
            query += " AND proposal_id = ?"
            params.append(proposal_id)
        query += " ORDER BY created_at DESC"
        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            legacy_markers = self._get_legacy_markers(conn, "experiment_task")
        tasks = [self._row_to_experiment_task(r, legacy_markers) for r in rows]
        if not include_legacy_demo:
            tasks = [t for t in tasks if not t.legacy_demo]
        return tasks

    @staticmethod
    def _row_to_experiment_task(row: sqlite3.Row, legacy_markers: set[str] | None = None) -> ExperimentTask:
        tkid = str(row["id"])
        keys = row.keys()
        col_legacy = bool(row["legacy_demo"]) if "legacy_demo" in keys else False
        is_legacy = col_legacy or (legacy_markers is not None and tkid in legacy_markers)
        return ExperimentTask(
            id=tkid,
            proposal_id=row["proposal_id"],
            target_agent=row["target_agent"],
            strategy_id=row["strategy_id"],
            old_strategy_version=row["old_strategy_version"],
            new_strategy_version=int(row["new_strategy_version"]),
            benchmark_problem_ids=json.loads(row["benchmark_problem_ids_json"]),
            runs_per_problem_k=int(row["runs_per_problem_k"]),
            metrics_and_thresholds=json.loads(row["metrics_and_thresholds_json"]),
            status=row["status"],
            legacy_demo=is_legacy,
            created_at=row["created_at"],
        )

    def save_experiment_report(self, report: ExperimentReport, *, caller_role: str) -> ExperimentReport:
        if caller_role not in {"strategy_manager", "practitioner"}:
            raise PermissionViolationError(f"Permission denied: '{caller_role}' cannot save ExperimentReport.")
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO si_experiment_reports (
                    id, task_id, proposal_id, target_agent, strategy_id,
                    old_strategy_version, new_strategy_version,
                    old_run_trace_ids_json, new_run_trace_ids_json, all_trace_ids_json,
                    old_pass_rate, new_pass_rate, old_avg_tokens, new_avg_tokens,
                    old_avg_elapsed_ms, new_avg_elapsed_ms, problem_breakdown_json,
                    rule_judgment_json, meta_review_comment, meta_approved, human_approved,
                    raw_model_output, legacy_demo, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.id,
                    report.task_id,
                    report.proposal_id,
                    report.target_agent,
                    report.strategy_id,
                    report.old_strategy_version,
                    report.new_strategy_version,
                    json.dumps(report.old_run_trace_ids, ensure_ascii=False),
                    json.dumps(report.new_run_trace_ids, ensure_ascii=False),
                    json.dumps(report.all_trace_ids, ensure_ascii=False),
                    report.old_pass_rate,
                    report.new_pass_rate,
                    report.old_avg_tokens,
                    report.new_avg_tokens,
                    report.old_avg_elapsed_ms,
                    report.new_avg_elapsed_ms,
                    json.dumps(report.problem_breakdown, ensure_ascii=False),
                    json.dumps(report.rule_judgment.model_dump(), ensure_ascii=False),
                    report.meta_review_comment,
                    None if report.meta_approved is None else (1 if report.meta_approved else 0),
                    None if report.human_approved is None else (1 if report.human_approved else 0),
                    report.raw_model_output,
                    1 if report.legacy_demo else 0,
                    report.created_at,
                ),
            )
        return report

    def get_experiment_report(self, report_id: str) -> ExperimentReport | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM si_experiment_reports WHERE id = ?", (report_id,)).fetchone()
            legacy_markers = self._get_legacy_markers(conn, "experiment_report")
        return self._row_to_experiment_report(row, legacy_markers) if row else None

    def list_experiment_reports(
        self,
        *,
        proposal_id: str | None = None,
        include_legacy_demo: bool = False,
    ) -> list[ExperimentReport]:
        query = "SELECT * FROM si_experiment_reports WHERE 1=1"
        params: list[Any] = []
        if proposal_id:
            query += " AND proposal_id = ?"
            params.append(proposal_id)
        query += " ORDER BY created_at DESC"
        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            legacy_markers = self._get_legacy_markers(conn, "experiment_report")
        reports = [self._row_to_experiment_report(r, legacy_markers) for r in rows]
        if not include_legacy_demo:
            reports = [r for r in reports if not r.legacy_demo]
        return reports

    @staticmethod
    def _row_to_experiment_report(row: sqlite3.Row, legacy_markers: set[str] | None = None) -> ExperimentReport:
        rpid = str(row["id"])
        keys = row.keys()
        col_legacy = bool(row["legacy_demo"]) if "legacy_demo" in keys else False
        is_legacy = col_legacy or (legacy_markers is not None and rpid in legacy_markers)
        return ExperimentReport(
            id=rpid,
            task_id=row["task_id"],
            proposal_id=row["proposal_id"],
            target_agent=row["target_agent"],
            strategy_id=row["strategy_id"],
            old_strategy_version=row["old_strategy_version"],
            new_strategy_version=int(row["new_strategy_version"]),
            old_run_trace_ids=json.loads(row["old_run_trace_ids_json"]),
            new_run_trace_ids=json.loads(row["new_run_trace_ids_json"]),
            all_trace_ids=json.loads(row["all_trace_ids_json"]),
            old_pass_rate=float(row["old_pass_rate"]),
            new_pass_rate=float(row["new_pass_rate"]),
            old_avg_tokens=float(row["old_avg_tokens"]),
            new_avg_tokens=float(row["new_avg_tokens"]),
            old_avg_elapsed_ms=float(row["old_avg_elapsed_ms"]),
            new_avg_elapsed_ms=float(row["new_avg_elapsed_ms"]),
            problem_breakdown=json.loads(row["problem_breakdown_json"]),
            rule_judgment=json.loads(row["rule_judgment_json"]),
            meta_review_comment=row["meta_review_comment"],
            meta_approved=None if row["meta_approved"] is None else bool(row["meta_approved"]),
            human_approved=None if row["human_approved"] is None else bool(row["human_approved"]),
            raw_model_output=str(row["raw_model_output"] or "") if "raw_model_output" in keys else "",
            legacy_demo=is_legacy,
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # AuditLog (Append-only, StrategyManager only)
    # ------------------------------------------------------------------
    def _insert_audit_row(self, conn: sqlite3.Connection, log: AuditLog) -> None:
        conn.execute(
            """
            INSERT INTO si_audit_logs (
                id, who, when_ts, target_type, target_id,
                from_state, to_state, rationale, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log.id,
                log.who,
                log.when,
                log.target_type,
                log.target_id,
                log.from_state,
                log.to_state,
                log.rationale,
                json.dumps(log.metadata, ensure_ascii=False),
            ),
        )

    def append_audit_log(self, log: AuditLog, *, caller_role: str = "strategy_manager") -> AuditLog:
        self.assert_strategy_manager_only(caller_role, "append_audit_log")
        with self.connection() as conn:
            self._insert_audit_row(conn, log)
        return log

    def list_audit_logs(self, *, limit: int = 200) -> list[AuditLog]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM si_audit_logs ORDER BY when_ts DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AuditLog(
                id=r["id"],
                who=r["who"],
                when=r["when_ts"],
                target_type=r["target_type"],
                target_id=r["target_id"],
                from_state=r["from_state"],
                to_state=r["to_state"],
                rationale=r["rationale"],
                metadata=json.loads(r["metadata_json"] or "{}"),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_settings(self) -> SelfImprovementSettings:
        with self.connection() as conn:
            row = conn.execute("SELECT value_json FROM si_settings WHERE key = 'global'").fetchone()
        if not row:
            return SelfImprovementSettings()
        try:
            return SelfImprovementSettings(**json.loads(row["value_json"]))
        except Exception:
            return SelfImprovementSettings()

    def save_settings(
        self,
        settings: SelfImprovementSettings,
        *,
        who: str = "human",
    ) -> SelfImprovementSettings:
        old = self.get_settings()
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO si_settings (key, value_json) VALUES ('global', ?)",
                (json.dumps(settings.model_dump(), ensure_ascii=False),),
            )
            if old.require_human_approval != settings.require_human_approval:
                self._insert_audit_row(
                    conn,
                    AuditLog(
                        who=who,
                        target_type="settings",
                        target_id="require_human_approval",
                        from_state=str(old.require_human_approval),
                        to_state=str(settings.require_human_approval),
                        rationale="切换人工审批门禁开关",
                    ),
                )
        return settings
