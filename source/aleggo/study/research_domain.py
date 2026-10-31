"""Structured research-domain persistence layered on the shared A-leggo SQLite file."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import hashlib
import re
import sqlite3
import threading
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from aleggo.study.research_store import ResearchStore
from aleggo.study.lean_formal import binding_hash, proof_digest

# Serializes consume_budget's read-modify-write so concurrent workers cannot
# lost-update each other's consumption and silently exceed the budget.
_BUDGET_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return prefix + "_" + uuid4().hex


def _merge_metadata(previous: Any, incoming: Any) -> Any:
    """Provider refreshes retain captured information when a new field is empty."""
    if incoming in (None, "", [], {}):
        return previous
    if isinstance(previous, dict) and isinstance(incoming, dict):
        return {**previous, **{key: _merge_metadata(previous.get(key), value) for key, value in incoming.items()}}
    if isinstance(previous, list) and isinstance(incoming, list):
        return list({json.dumps(item, ensure_ascii=False, sort_keys=True, default=str): item for item in previous + incoming}.values())
    return incoming



def _safe_json(raw, fallback):
    try:
        value = json.loads(raw) if raw else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _safe_relevance(raw: Any) -> float:
    """Coerce a relevance score to float; non-numeric input (e.g. "high") falls back to 0."""
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


class ResearchDomainStore:
    """Branches, proof graph, sources, memories, and tool ledger in A-leggo storage."""

    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def lean_settings(self, project_id: str) -> dict[str, bool]:
        """Return project Lean preferences, applying safe defaults for legacy rows."""
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT lean_confirm_before_check,lean_auto_call_enabled FROM research_projects WHERE id=?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError("未找到研究项目。")
        return {
            "confirmBeforeCheck": True if row[0] is None else bool(row[0]),
            "autoCallEnabled": False if row[1] is None else bool(row[1]),
        }

    def save_lean_settings(self, project_id: str, *, confirm_before_check: bool, auto_call_enabled: bool) -> dict[str, bool]:
        with self.store.connection() as conn:
            cursor = conn.execute(
                "UPDATE research_projects SET lean_confirm_before_check=?,lean_auto_call_enabled=?,updated_at=? WHERE id=?",
                (int(confirm_before_check), int(auto_call_enabled), _now(), project_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("未找到研究项目。")
        return {"confirmBeforeCheck": confirm_before_check, "autoCallEnabled": auto_call_enabled}

    def list_branches(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT b.*,p.name AS parent_name FROM research_branches b "
                "LEFT JOIN research_branches p ON p.id=b.parent_id "
                "WHERE b.project_id=? ORDER BY b.created_at,b.name", (project_id,),
            ).fetchall()
        return [self._branch(row) for row in rows]

    def get_branch(self, project_id: str, branch_name: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT b.*,p.name AS parent_name FROM research_branches b "
                "LEFT JOIN research_branches p ON p.id=b.parent_id WHERE b.project_id=? AND b.name=?",
                (project_id, branch_name),
            ).fetchone()
        return self._branch(row) if row else None

    @staticmethod
    def _branch(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "projectId": row["project_id"], "name": row["name"],
            "parentId": row["parent_id"], "parentName": row["parent_name"] if "parent_name" in row.keys() else None,
            "basePlanVersion": row["base_plan_version"], "status": row["status"],
            "priority": row["priority"],
            "limits": _safe_json(row["limits_json"], {}), "used": _safe_json(row["used_json"], {}),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def create_branch(
        self, project_id: str, name: str, *, parent_name: str = "main",
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = " ".join(str(name).split())
        if not name or len(name) > 120:
            raise ValueError("分支名须为 1 到 120 个字符。")
        raw_limits = limits or {}
        normalized: dict[str, int] = {}
        for key, default in (("evaluations", 1_000_000), ("proofAttempts", 128), ("toolSeconds", 900)):
            value = raw_limits.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError("分支预算须为非负数字。")
            normalized[key] = min(int(value), 100_000_000)
        now = _now()
        branch_id = _id("branch")
        with self.store.connection() as conn:
            project = conn.execute("SELECT current_version FROM research_projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise KeyError("未找到该研究项目。")
            parent = conn.execute(
                "SELECT id FROM research_branches WHERE project_id=? AND name=?", (project_id, parent_name)
            ).fetchone()
            if not parent:
                raise ValueError("父分支在本项目中不存在。")
            try:
                conn.execute(
                    "INSERT INTO research_branches(id,project_id,name,parent_id,base_plan_version,limits_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (branch_id, project_id, name, parent["id"], project["current_version"], json.dumps(normalized), now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("分支名已存在，请换一个名字。") from exc
            row = conn.execute(
                "SELECT b.*,p.name AS parent_name FROM research_branches b LEFT JOIN research_branches p ON p.id=b.parent_id WHERE b.id=?",
                (branch_id,),
            ).fetchone()
        return self._branch(row)

    def update_branch_limits(self, project_id: str, branch_name: str, limits: dict[str, Any]) -> dict[str, Any]:
        current = next((item for item in self.list_branches(project_id) if item["name"] == branch_name), None)
        if not current:
            raise ValueError("研究分支不存在。")
        normalized = dict(current["limits"])
        for key in normalized:
            if key in limits:
                value = limits[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < current["used"].get(key, 0):
                    raise ValueError("分支限额须为不小于当前用量的数字。")
                normalized[key] = min(int(value), 100_000_000)
        with self.store.connection() as conn:
            # Reopen an exhausted branch only when every limit now strictly
            # covers current usage; otherwise a BUDGET_EXHAUSTED branch must
            # not be silently revived by a limits update. Other statuses are
            # left untouched.
            if current["status"] == "BUDGET_EXHAUSTED" and all(
                    normalized[key] > current["used"].get(key, 0) for key in normalized):
                conn.execute(
                    "UPDATE research_branches SET limits_json=?,status='OPEN',updated_at=? WHERE project_id=? AND name=?",
                    (json.dumps(normalized), _now(), project_id, branch_name),
                )
            else:
                conn.execute(
                    "UPDATE research_branches SET limits_json=?,updated_at=? WHERE project_id=? AND name=?",
                    (json.dumps(normalized), _now(), project_id, branch_name),
                )
        return next(item for item in self.list_branches(project_id) if item["name"] == branch_name)

    def consume_budget(self, project_id: str, branch_name: str, kind: str, amount: float) -> dict[str, Any]:
        if kind not in {"evaluations", "proofAttempts", "toolSeconds"} or amount < 0:
            raise ValueError("预算扣减参数无效。")
        exceeded = False
        # The SELECT-then-UPDATE below must be atomic: without the lock two
        # workers could read the same used_json and one consumption would be
        # silently lost, letting the branch exceed its budget.
        with _BUDGET_LOCK:
            with self.store.connection() as conn:
                row = conn.execute(
                    "SELECT * FROM research_branches WHERE project_id=? AND name=?", (project_id, branch_name)
                ).fetchone()
                if not row:
                    raise ValueError("研究分支不存在。")
                limits = _safe_json(row["limits_json"], {})
                used = _safe_json(row["used_json"], {})
                if row["status"] == "BUDGET_EXHAUSTED":
                    raise ValueError("该研究分支的资源预算已用尽。")
                if row["status"] != "OPEN":
                    raise ValueError("该研究分支已关闭。")
                next_value = float(used.get(kind, 0)) + amount
                if next_value > float(limits.get(kind, 0)):
                    exceeded = True
                    used[kind] = float(limits.get(kind, 0))
                    conn.execute("UPDATE research_branches SET status='BUDGET_EXHAUSTED',updated_at=? WHERE id=?", (_now(), row["id"]))
                    conn.execute("UPDATE research_branches SET used_json=? WHERE id=?", (json.dumps(used), row["id"]))
                else:
                    used[kind] = next_value
                    conn.execute("UPDATE research_branches SET used_json=?,updated_at=? WHERE id=?", (json.dumps(used), _now(), row["id"]))
        if exceeded:
            raise ValueError(f"该研究分支的资源预算已用尽：{kind}。")
        return {"branch": branch_name, "limits": limits, "used": used, "status": row["status"]}

    def set_branch_state(self, project_id: str, branch_name: str, status: str,
                         priority: int | None = None, reason: str = "") -> dict[str, Any]:
        normalized = {"ACTIVE": "OPEN", "CLOSED": "CLOSED", "PAUSED": "PAUSED", "OPEN": "OPEN"}.get(status.upper())
        if normalized is None or (priority is not None and (isinstance(priority, bool) or not 1 <= priority <= 5)):
            raise ValueError("请选择 active、closed 或 paused，优先级为 1 到 5。")
        branch = self.get_branch(project_id, branch_name)
        if not branch:
            raise KeyError("未找到该研究分支。")
        if normalized == "OPEN" and any(branch["used"].get(k, 0) >= v for k, v in branch["limits"].items() if v > 0):
            raise ValueError("请先提高已用尽的分支限额，再重新打开该分支。")
        with self.store.connection() as conn:
            conn.execute("UPDATE research_branches SET status=?,priority=?,updated_at=? WHERE id=?",
                         (normalized, branch["priority"] if priority is None else priority, _now(), branch["id"]))
            project = conn.execute("SELECT current_version FROM research_projects WHERE id=?", (project_id,)).fetchone()
            self.store._intervention(conn, project_id, project[0], "branch_state", "researcher",
                                     {"branch": branch_name, "status": normalized, "priority": priority, "reason": reason}, _now())
        return self.get_branch(project_id, branch_name)

    def compare_branches(self, project_id: str, left_name: str, right_name: str) -> dict[str, Any]:
        branches = {item["name"]: item for item in self.list_branches(project_id)}
        if left_name == right_name or left_name not in branches or right_name not in branches:
            raise ValueError("请选择本项目中两个不同的分支。")
        result: dict[str, Any] = {"left": branches[left_name], "right": branches[right_name]}
        with self.store.connection() as conn:
            for name in (left_name, right_name):
                branch_id = branches[name]["id"]
                artifacts = conn.execute(
                    "SELECT id,title,status,created_at AS createdAt FROM research_artifacts WHERE project_id=? AND branch_id=? ORDER BY created_at",
                    (project_id, branch_id),
                ).fetchall()
                counts = conn.execute(
                    "SELECT node_type AS type,status,count(*) AS count FROM research_graph_nodes WHERE project_id=? AND branch_id=? GROUP BY node_type,status",
                    (project_id, branch_id),
                ).fetchall()
                result[name] = {"artifacts": [dict(row) for row in artifacts], "graphCounts": [dict(row) for row in counts]}
        left_titles = {item["title"] for item in result[left_name]["artifacts"]}
        right_titles = {item["title"] for item in result[right_name]["artifacts"]}
        result["differences"] = {"onlyLeft": sorted(left_titles - right_titles), "onlyRight": sorted(right_titles - left_titles)}
        return result

    def create_graph_node(
        self, project_id: str, node_type: str, title: str, content: str = "", *,
        branch_name: str = "main", status: str = "unverified", artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed_types = {"question", "conjecture", "definition", "lemma", "proposition", "theorem", "proof_attempt", "counterexample", "evidence", "experiment", "source"}
        if node_type not in allowed_types:
            raise ValueError("不支持的图谱节点类型。")
        # External callers may label an attempt, but only the server-side Lean
        # adapter can promote it to verified after a real kernel process exits 0.
        if status not in {"unverified", "llm_assessed", "refuted"}:
            raise ValueError("只有服务端形式化检查器可将证明标为已验证。")
        title = " ".join(str(title).split())
        if not title or len(title) > 500 or len(content) > 100_000:
            raise ValueError("图谱节点标题或内容超出允许的长度。")
        now = _now()
        node_id = _id("node")
        with self.store.connection() as conn:
            branch = conn.execute("SELECT id FROM research_branches WHERE project_id=? AND name=?", (project_id, branch_name)).fetchone()
            if not branch:
                raise ValueError("研究分支不存在。")
            if artifact_id:
                artifact = conn.execute("SELECT id,branch_id FROM research_artifacts WHERE id=? AND project_id=?", (artifact_id, project_id)).fetchone()
                if not artifact:
                    raise ValueError("产物须属于本研究项目。")
                if artifact["branch_id"] and artifact["branch_id"] != branch["id"]:
                    raise ValueError("该产物属于另一分支，不能挂载到本分支的节点上。")
            conn.execute(
                "INSERT INTO research_graph_nodes(id,project_id,branch_id,node_type,title,content,status,artifact_id,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (node_id, project_id, branch["id"], node_type, title, content, status, artifact_id, json.dumps(metadata or {}, ensure_ascii=False), now, now),
            )
        return {"id": node_id, "projectId": project_id, "branch": branch_name, "type": node_type,
                "title": title, "content": content, "status": status, "artifactId": artifact_id,
                "metadata": metadata or {}, "createdAt": now}

    def create_formal_binding(
        self, project_id: str, claim_node_id: str, original_statement: str,
        formal_ir: str, lean_statement: str, *, confirm_mapping: bool,
        mapping_authority: str = "UNCONFIRMED",
    ) -> dict[str, Any]:
        original_hash, formal_ir_hash, lean_hash, combined_hash = binding_hash(
            original_statement, formal_ir, lean_statement,
        )
        original_clean = " ".join(original_statement.split())
        binding_id, now = _id("formal"), _now()
        with self.store.connection() as conn:
            claim = conn.execute(
                "SELECT id,node_type,title,content,branch_id FROM research_graph_nodes WHERE project_id=? AND id=?",
                (project_id, claim_node_id),
            ).fetchone()
            if not claim or claim["node_type"] not in {"conjecture", "lemma", "proposition", "theorem"}:
                raise ValueError("请选择猜想、引理、命题或定理类型的图谱节点。")
            claim_text = {" ".join(str(claim["title"]).split()), " ".join(str(claim["content"]).split())}
            if original_clean not in claim_text:
                raise ValueError("原命题陈述须与所选图谱命题的标题或内容一致。")
            if mapping_authority not in {"UNCONFIRMED", "AI_PROPOSED"}:
                raise ValueError("形式化映射的确认权限须由服务端指定。")
            authority = "USER_CONFIRMED" if confirm_mapping else mapping_authority
            status = "MAPPING_CONFIRMED" if confirm_mapping else "MAPPING_REVIEW_REQUIRED"
            prior = conn.execute(
                "SELECT * FROM research_formal_bindings WHERE project_id=? AND claim_node_id=? AND binding_hash=?",
                (project_id, claim_node_id, combined_hash),
            ).fetchone()
            if prior:
                return self._formal_binding(prior)
            conn.execute(
                "INSERT INTO research_formal_bindings(id,project_id,claim_node_id,original_statement,formal_ir,lean_statement,original_hash,formal_ir_hash,lean_statement_hash,binding_hash,mapping_authority,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (binding_id, project_id, claim_node_id, original_statement.strip(), formal_ir.strip(), lean_statement.strip(),
                 original_hash, formal_ir_hash, lean_hash, combined_hash, authority, status, now, now),
            )
            project = conn.execute("SELECT current_version FROM research_projects WHERE id=?", (project_id,)).fetchone()
            event = "formal_mapping_confirmed" if confirm_mapping else "formal_mapping_proposed" if authority == "AI_PROPOSED" else "formal_mapping_saved"
            actor = "researcher" if confirm_mapping or authority == "UNCONFIRMED" else "formalizer"
            self.store._intervention(conn, project_id, project["current_version"] if project else None,
                                     event, actor, {"bindingId": binding_id, "bindingHash": combined_hash, "authority": authority}, now)
            row = conn.execute("SELECT * FROM research_formal_bindings WHERE id=?", (binding_id,)).fetchone()
        return self._formal_binding(row)

    def confirm_formal_binding(
        self, project_id: str, binding_id: str, original_statement: str,
        formal_ir: str, lean_statement: str,
    ) -> dict[str, Any]:
        original_hash, formal_ir_hash, lean_hash, combined_hash = binding_hash(
            original_statement, formal_ir, lean_statement,
        )
        original_clean = " ".join(original_statement.split())
        now = _now()
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_formal_bindings WHERE project_id=? AND id=?", (project_id, binding_id),
            ).fetchone()
            if not row:
                raise KeyError("未找到形式化绑定。")
            if row["mapping_authority"] == "USER_CONFIRMED":
                raise ValueError("该形式化映射已确认，如需修改请创建新的映射版本。")
            duplicate = conn.execute(
                "SELECT * FROM research_formal_bindings WHERE project_id=? AND claim_node_id=? AND binding_hash=? AND id<>?",
                (project_id, row["claim_node_id"], combined_hash, binding_id),
            ).fetchone()
            if duplicate:
                # Never silently return another binding's payload: the caller
                # asked to confirm *this* binding_id, so report the conflict.
                raise ValueError(
                    "已存在内容相同的映射（" + str(duplicate["id"]) + "，状态：" + str(duplicate["mapping_authority"]) + "），"
                    "请直接确认那一条，或修改当前内容后再确认。")
            if row["proof_source_hash"]:
                raise ValueError("已做过内核检查的形式化映射不可编辑，请创建新的映射版本。")
            claim = conn.execute(
                "SELECT title,content FROM research_graph_nodes WHERE project_id=? AND id=?",
                (project_id, row["claim_node_id"]),
            ).fetchone()
            if not claim:
                raise ValueError("所绑定的图谱命题已不存在。")
            claim_text = {" ".join(str(claim["title"]).split()), " ".join(str(claim["content"]).split())}
            if original_clean not in claim_text:
                raise ValueError("原命题陈述须与所选图谱命题的标题或内容一致。")
            conn.execute(
                "UPDATE research_formal_bindings SET original_statement=?,formal_ir=?,lean_statement=?,original_hash=?,formal_ir_hash=?,lean_statement_hash=?,binding_hash=?,mapping_authority='USER_CONFIRMED',status='MAPPING_CONFIRMED',updated_at=? WHERE id=?",
                (original_statement.strip(), formal_ir.strip(), lean_statement.strip(), original_hash, formal_ir_hash,
                 lean_hash, combined_hash, now, binding_id),
            )
            project = conn.execute("SELECT current_version FROM research_projects WHERE id=?", (project_id,)).fetchone()
            self.store._intervention(conn, project_id, project["current_version"] if project else None,
                                     "formal_mapping_confirmed", "researcher", {"bindingId": binding_id, "bindingHash": combined_hash}, now)
            updated = conn.execute("SELECT * FROM research_formal_bindings WHERE id=?", (binding_id,)).fetchone()
        return self._formal_binding(updated)

    @staticmethod
    def _formal_binding(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "projectId": row["project_id"], "claimNodeId": row["claim_node_id"],
            "proofNodeId": row["proof_node_id"], "originalStatement": row["original_statement"],
            "formalIr": row["formal_ir"], "leanStatement": row["lean_statement"],
            "originalHash": row["original_hash"], "formalIrHash": row["formal_ir_hash"],
            "leanStatementHash": row["lean_statement_hash"], "bindingHash": row["binding_hash"],
            "mappingAuthority": row["mapping_authority"], "status": row["status"],
            "proofSourceHash": row["proof_source_hash"], "leanVersion": row["lean_version"],
            "checkerOutput": row["checker_output"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def list_formal_bindings(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM research_formal_bindings WHERE project_id=? ORDER BY created_at DESC", (project_id,),
            ).fetchall()
        return [self._formal_binding(row) for row in rows]

    def get_formal_binding(self, project_id: str, binding_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_formal_bindings WHERE project_id=? AND id=?", (project_id, binding_id),
            ).fetchone()
        return self._formal_binding(row) if row else None

    def record_formal_check(
        self, project_id: str, binding_id: str, proof_source: str, result: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now()
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_formal_bindings WHERE project_id=? AND id=?", (project_id, binding_id),
            ).fetchone()
            if not row:
                raise KeyError("未找到形式化绑定。")
            proof_node_id = _id("node")
            claim = conn.execute(
                "SELECT branch_id,title FROM research_graph_nodes WHERE project_id=? AND id=?",
                (project_id, row["claim_node_id"]),
            ).fetchone()
            if not claim:
                raise ValueError("所绑定的命题已不存在。")
            check_status = str(result.get("status") or "CHECK_FAILED")
            proof_status = "KERNEL_CERTIFIED" if check_status == "KERNEL_CERTIFIED" else check_status
            node_content = "Lean proof attempt:\n" + proof_source[:40_000] + "\n\nChecker status: " + proof_status
            if result.get("output"):
                node_content += "\n\nChecker output:\n" + str(result["output"])[-12_000:]
            conn.execute(
                "INSERT INTO research_graph_nodes(id,project_id,branch_id,node_type,title,content,status,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (proof_node_id, project_id, claim["branch_id"], "proof_attempt", "Lean kernel check: " + claim["title"][:430],
                 node_content[:100_000], "unverified", json.dumps({
                     "formalBindingId": binding_id, "checkStatus": proof_status,
                     "proofHash": proof_digest(proof_source), "elapsedMs": result.get("elapsedMs", 0),
                 }, ensure_ascii=False), now, now),
            )
            binding_status = check_status if check_status in {
                "KERNEL_CERTIFIED", "CHECK_FAILED", "TIMEOUT", "OUTPUT_LIMIT",
                "LEAN_UNAVAILABLE", "RESOURCE_BUDGET_EXCEEDED",
            } else "CHECK_FAILED"
            conn.execute(
                "UPDATE research_formal_bindings SET proof_node_id=?,proof_source=?,proof_source_hash=?,lean_version=?,checker_output=?,status=?,updated_at=? WHERE id=?",
                (proof_node_id, proof_source, proof_digest(proof_source), str(result.get("leanVersion") or ""),
                 str(result.get("output") or "")[-12_000:], binding_status, now, binding_id),
            )
            updated = conn.execute("SELECT * FROM research_formal_bindings WHERE id=?", (binding_id,)).fetchone()
        return self._formal_binding(updated)

    def promote_formal_binding(self, project_id: str, binding_id: str) -> dict[str, Any]:
        now = _now()
        with self.store.connection() as conn:
            binding = conn.execute(
                "SELECT * FROM research_formal_bindings WHERE project_id=? AND id=?", (project_id, binding_id),
            ).fetchone()
            if not binding:
                raise KeyError("未找到形式化绑定。")
            if binding["mapping_authority"] != "USER_CONFIRMED" or binding["status"] != "KERNEL_CERTIFIED":
                raise ValueError("仅研究者确认过的映射且通过 Lean 内核检查后，才能提升为已验证。")
            hashes = binding_hash(binding["original_statement"], binding["formal_ir"], binding["lean_statement"])
            if hashes != (binding["original_hash"], binding["formal_ir_hash"], binding["lean_statement_hash"], binding["binding_hash"]):
                raise ValueError("内核检查后形式化绑定发生了变化，请重新检查。")
            if not binding["proof_source"] or proof_digest(binding["proof_source"]) != binding["proof_source_hash"]:
                raise ValueError("内核检查后已认证的证明来源发生了变化。")
            proof = conn.execute(
                "SELECT id,branch_id,node_type,metadata_json FROM research_graph_nodes WHERE project_id=? AND id=?",
                (project_id, binding["proof_node_id"]),
            ).fetchone()
            claim = conn.execute(
                "SELECT id,branch_id,node_type,title,content,status FROM research_graph_nodes WHERE project_id=? AND id=?",
                (project_id, binding["claim_node_id"]),
            ).fetchone()
            if not proof or not claim or proof["node_type"] != "proof_attempt":
                raise ValueError("所绑定的命题或证明尝试不存在。")
            if claim["status"] == "rejected":
                raise ValueError("该命题已被研究者驳回，请修订并重新评审后再提升。")
            claim_text = {" ".join(str(claim["title"]).split()), " ".join(str(claim["content"]).split())}
            if " ".join(binding["original_statement"].split()) not in claim_text:
                raise ValueError("创建形式化绑定后源命题发生了变化。")
            metadata = _safe_json(proof["metadata_json"] or "{}", {})
            if metadata.get("formalBindingId") != binding_id or metadata.get("checkStatus") != "KERNEL_CERTIFIED" or metadata.get("proofHash") != binding["proof_source_hash"]:
                raise ValueError("最新证明节点不是该绑定的内核已认证来源。")
            metadata.update(evidenceClass="FORMALLY_VERIFIED", verificationSuspended=False)
            conn.execute("UPDATE research_graph_nodes SET status='verified',metadata_json=?,updated_at=? WHERE id=?",
                         (json.dumps(metadata, ensure_ascii=False), now, proof["id"]))
            # The claim itself is now kernel-certified too; leaving it
            # "unverified" would mislead the graph view.
            conn.execute("UPDATE research_graph_nodes SET status='verified',updated_at=? WHERE id=? AND status<>'rejected'",
                         (now, claim["id"]))
            exists = conn.execute(
                "SELECT id FROM research_graph_edges WHERE project_id=? AND source_id=? AND target_id=? AND edge_type='VERIFIES'",
                (project_id, proof["id"], claim["id"]),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO research_graph_edges(id,project_id,branch_id,source_id,target_id,edge_type,evidence,metadata_json,created_at) VALUES(?,?,?,?,?,'VERIFIES',?,?,?)",
                    (_id("edge"), project_id, proof["branch_id"] if proof["branch_id"] == claim["branch_id"] else None,
                     proof["id"], claim["id"], "Lean kernel accepted the bound theorem statement.",
                     json.dumps({"formalBindingId": binding_id, "bindingHash": binding["binding_hash"], "proofHash": binding["proof_source_hash"]}), now),
                )
            conn.execute("UPDATE research_formal_bindings SET status='PROMOTED',updated_at=? WHERE id=?", (now, binding_id))
            updated = conn.execute("SELECT * FROM research_formal_bindings WHERE id=?", (binding_id,)).fetchone()
        return self._formal_binding(updated)

    def add_graph_edge(
        self, project_id: str, source_id: str, target_id: str, edge_type: str, *, evidence: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {"SUPPORTS", "REFUTES", "DEPENDS_ON", "DERIVED_FROM", "VERIFIES", "CITES", "USES", "CONTRADICTS"}
        if edge_type not in allowed or source_id == target_id:
            raise ValueError("不支持的关系类型，或边的起点与终点相同。")
        edge_id, now = _id("edge"), _now()
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT id,branch_id FROM research_graph_nodes WHERE project_id=? AND id IN (?,?)",
                (project_id, source_id, target_id),
            ).fetchall()
            if len(rows) != 2:
                raise ValueError("两个图谱节点都须属于本项目。")
            branch_id = rows[0]["branch_id"] if rows[0]["branch_id"] == rows[1]["branch_id"] else None
            try:
                conn.execute(
                    "INSERT INTO research_graph_edges(id,project_id,branch_id,source_id,target_id,edge_type,evidence,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (edge_id, project_id, branch_id, source_id, target_id, edge_type, evidence[:12000], json.dumps(metadata or {}, ensure_ascii=False), now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("这两点之间已存在同类型的关系，无需重复添加。") from exc
        return {"id": edge_id, "source": source_id, "target": target_id, "type": edge_type, "evidence": evidence, "createdAt": now}

    def list_graph(self, project_id: str, branch_name: str | None = None) -> dict[str, list[dict[str, Any]]]:
        params: list[Any] = [project_id]
        branch_filter = ""
        if branch_name:
            branch_filter = " AND b.name=?"
            params.append(branch_name)
        with self.store.connection() as conn:
            nodes = conn.execute(
                "SELECT n.*,b.name AS branch_name FROM research_graph_nodes n LEFT JOIN research_branches b ON b.id=n.branch_id WHERE n.project_id=?" + branch_filter + " ORDER BY n.created_at",
                params,
            ).fetchall()
            edges = conn.execute(
                "SELECT e.*,b.name AS branch_name FROM research_graph_edges e LEFT JOIN research_branches b ON b.id=e.branch_id WHERE e.project_id=?" + branch_filter + " ORDER BY e.created_at",
                params,
            ).fetchall()
        return {
            "nodes": [{"id": r["id"], "type": r["node_type"], "title": r["title"], "content": r["content"], "status": r["status"], "branch": r["branch_name"], "artifactId": r["artifact_id"], "metadata": _safe_json(r["metadata_json"], {}), "createdAt": r["created_at"]} for r in nodes],
            "edges": [{"id": r["id"], "source": r["source_id"], "target": r["target_id"], "type": r["edge_type"], "evidence": r["evidence"], "branch": r["branch_name"], "metadata": _safe_json(r["metadata_json"], {}), "createdAt": r["created_at"]} for r in edges],
        }

    def save_source(
        self, project_id: str, data: dict[str, Any], chunks: list[dict[str, Any]] | None = None,
        *, original_file: bytes | None = None,
    ) -> dict[str, Any]:
        source_id = str(data.get("id") or _id("source"))
        now = _now()
        title_key = re.sub(r"[^\w]+", " ", str(data.get("title") or "").casefold()).strip()
        from aleggo.study.research_literature import normalize_doi, normalize_arxiv_id
        doi = normalize_doi(data.get("doi"))
        url = str(data.get("url") or "")[:2000]
        arxiv_id = normalize_arxiv_id(data.get("arxivId") or (url if "arxiv.org/" in url else ""))
        with self.store.connection() as conn:
            if data.get("type") == "literature":
                prior = None
                if doi:
                    prior = conn.execute(
                        "SELECT id FROM research_sources WHERE project_id=? AND source_type='literature' AND lower(doi)=? LIMIT 1",
                        (project_id, doi),
                    ).fetchone()
                if prior is None and arxiv_id:
                    prior = conn.execute(
                        "SELECT id FROM research_sources WHERE project_id=? AND source_type='literature' AND lower(arxiv_id)=? LIMIT 1",
                        (project_id, arxiv_id),
                    ).fetchone()
                if prior is None and url:
                    prior = conn.execute(
                        "SELECT id FROM research_sources WHERE project_id=? AND source_type='literature' AND lower(url)=? LIMIT 1",
                        (project_id, url.casefold()),
                    ).fetchone()
                if prior is None and title_key:
                    candidates = conn.execute(
                        "SELECT id,title,doi,arxiv_id,authors,year FROM research_sources WHERE project_id=? AND source_type='literature' ORDER BY created_at DESC",
                        (project_id,),
                    ).fetchall()
                    for candidate in candidates:
                        if (doi and candidate["doi"] and doi != candidate["doi"]) or (arxiv_id and candidate["arxiv_id"] and arxiv_id != candidate["arxiv_id"]):
                            continue
                        if data.get("year") and candidate["year"] and str(data["year"]) != str(candidate["year"]):
                            continue
                        authors = re.sub(r"\W+", " ", str(data.get("authors") or "").casefold()).strip()
                        old_authors = re.sub(r"\W+", " ", str(candidate["authors"] or "").casefold()).strip()
                        if authors and old_authors and SequenceMatcher(None, authors, old_authors).ratio() < 0.85:
                            continue
                        prior_title = re.sub(r"[^\w]+", " ", str(candidate["title"]).casefold()).strip()
                        if prior_title and SequenceMatcher(None, title_key, prior_title).ratio() >= 0.94:
                            prior = candidate
                            break
                if prior:
                    source_id = str(prior["id"])
            branch_name = str(data.get("branch") or "main")
            branch = conn.execute("SELECT id FROM research_branches WHERE project_id=? AND name=?", (project_id, branch_name)).fetchone()
            if not branch:
                raise ValueError("研究分支不存在。")
            old = conn.execute("SELECT * FROM research_sources WHERE id=?", (source_id,)).fetchone()
            if old and old["project_id"] != project_id:
                raise ValueError("来源 ID 属于其他项目。")
            metadata = _safe_json(old["metadata_json"] or "{}", {}) if old else {}
            incoming = dict(data.get("metadata") or {})
            for key, value in incoming.items():
                if key in {"structuredNote", "curated", "classifications"} and metadata.get("curated") and metadata.get(key):
                    continue
                metadata[key] = _merge_metadata(metadata.get(key), value)
            metadata.setdefault("provenance", []).append({"provider": data.get("provider", "local"), "url": url, "retrievedAt": now})
            metadata["provenance"] = metadata["provenance"][-200:]
            def richer(key: str, column: str, default=""):
                value = data.get(key)
                previous = old[column] if old else default
                if value in (None, "", [], {}) or (key == "abstract" and len(str(previous)) > len(str(value))):
                    return previous
                return value
            classification = old["classification"] if old and metadata.get("curated") else richer("classification", "classification", "background")
            author_value = richer("authors", "authors")
            if isinstance(author_value, list):
                author_value = "; ".join(str(a) for a in author_value)
            conn.execute(
                "INSERT INTO research_sources(id,project_id,branch_id,source_type,title,url,authors,year,abstract,content_hash,doi,provider,classification,reading_status,notes,metadata_json,first_seen_at,last_seen_at,document_blob,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,authors=excluded.authors,year=excluded.year,abstract=excluded.abstract,content_hash=excluded.content_hash,doi=CASE WHEN excluded.doi='' THEN research_sources.doi ELSE excluded.doi END,provider=CASE WHEN excluded.provider='' THEN research_sources.provider ELSE excluded.provider END,classification=CASE WHEN excluded.classification='' THEN research_sources.classification ELSE excluded.classification END,metadata_json=excluded.metadata_json,last_seen_at=excluded.last_seen_at,document_blob=COALESCE(excluded.document_blob,research_sources.document_blob)",
                (source_id, project_id, branch["id"], str(data.get("type") or "document"), str(richer("title", "title", "Untitled source"))[:500], richer("url", "url"), str(author_value)[:4000], richer("year", "year", None), str(richer("abstract", "abstract"))[:20000], richer("contentHash", "content_hash"), doi[:500], str(richer("provider", "provider"))[:100], str(classification)[:60], str(data.get("readingStatus") or "unread")[:30], str(data.get("notes") or "")[:8000], json.dumps(metadata, ensure_ascii=False, default=str), now, now, original_file, now),
            )
            conn.execute("UPDATE research_sources SET arxiv_id=?,is_important=?,relevance=? WHERE id=?",
                         (arxiv_id or (old["arxiv_id"] if old else ""), old["is_important"] if old else int(bool(data.get("important", False))),
                          _safe_relevance(data.get("relevance", metadata.get("relevance", old["relevance"] if old else 0))), source_id))
            if chunks is not None:
                conn.execute("DELETE FROM research_document_chunks WHERE project_id=? AND source_id=?", (project_id, source_id))
            for index, chunk in enumerate(chunks or []):
                conn.execute(
                    "INSERT OR REPLACE INTO research_document_chunks(id,project_id,source_id,chunk_index,page_number,section,content,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (_id("chunk"), project_id, source_id, index, chunk.get("page"), str(chunk.get("section") or "")[:500], str(chunk.get("content") or "")[:8000], now),
                )
            row = conn.execute("SELECT * FROM research_sources WHERE id=?", (source_id,)).fetchone()
            count = conn.execute("SELECT count(*) FROM research_document_chunks WHERE source_id=?", (source_id,)).fetchone()[0]
        return self._source_payload(row, count)

    @staticmethod
    def _source_payload(row: sqlite3.Row, count: int) -> dict[str, Any]:
        metadata = _safe_json(row["metadata_json"] or "{}", {})
        return {
            "id": row["id"], "type": row["source_type"], "title": row["title"], "url": row["url"],
            "authors": row["authors"], "year": row["year"], "abstract": row["abstract"],
            "contentHash": row["content_hash"], "doi": row["doi"], "provider": row["provider"],
            "classification": row["classification"], "readingStatus": row["reading_status"],
            "notes": row["notes"], "metadata": metadata,
            "arxivId": row["arxiv_id"], "important": bool(row["is_important"]), "relevance": row["relevance"],
            **{key: metadata.get(key) for key in ("structuredNote", "classifications", "classificationSuggestions", "classificationReason", "keywords", "researchArea", "publicationType", "citationMetadata", "provenance")},
            "firstSeenAt": row["first_seen_at"], "lastSeenAt": row["last_seen_at"],
            "hasOriginalFile": row["document_blob"] is not None, "chunkCount": count,
            "createdAt": row["created_at"],
        }

    def get_source(self, project_id: str, source_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT s.*,count(c.id) AS chunk_count FROM research_sources s "
                "LEFT JOIN research_document_chunks c ON c.source_id=s.id "
                "WHERE s.project_id=? AND s.id=? GROUP BY s.id",
                (project_id, source_id),
            ).fetchone()
        return self._source_payload(row, row["chunk_count"]) if row else None

    def get_document_blob(self, project_id: str, source_id: str) -> bytes | None:
        """Return the original uploaded bytes for a source, if stored."""
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT document_blob FROM research_sources WHERE project_id=? AND id=?",
                (project_id, source_id),
            ).fetchone()
        if not row:
            return None
        blob = row["document_blob"]
        return bytes(blob) if blob is not None else None

    def list_sources(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT s.*,count(c.id) AS chunk_count FROM research_sources s LEFT JOIN research_document_chunks c ON c.source_id=s.id WHERE s.project_id=? GROUP BY s.id ORDER BY s.created_at DESC", (project_id,)).fetchall()
        return [self._source_payload(r, r["chunk_count"]) for r in rows]

    def curate_source(self, project_id: str, source_id: str, *, reading_status: str | None = None,
                      classification: str | None = None, notes: str | None = None,
                      important: bool | None = None, structured_note: dict | None = None) -> dict[str, Any]:
        if reading_status is not None and reading_status not in {"unread", "important", "read", "irrelevant"}:
            raise ValueError("不支持的文献阅读状态。")
        classes = {"foundational", "classic", "survey", "direct", "methodological", "recent", "preprint", "contradictory", "supporting", "background"}
        if classification is not None and classification not in classes:
            raise ValueError("不支持的文献分类。")
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_sources WHERE project_id=? AND id=?", (project_id, source_id)).fetchone()
            if not row:
                raise KeyError("未找到该文献来源。")
            metadata = _safe_json(row["metadata_json"] or "{}", {})
            metadata["curated"] = True
            if structured_note is not None:
                metadata["structuredNote"] = structured_note
            if classification:
                metadata["classifications"] = list(dict.fromkeys(metadata.get("classifications", []) + [classification]))
            if reading_status == "important":
                important = True
                reading_status = row["reading_status"] if row["reading_status"] != "important" else "unread"
            conn.execute(
                "UPDATE research_sources SET reading_status=?,classification=?,notes=?,last_seen_at=?,is_important=?,metadata_json=? WHERE id=? AND project_id=?",
                (reading_status or row["reading_status"], classification or row["classification"],
                 row["notes"] if notes is None else notes[:8000], _now(), row["is_important"] if important is None else int(important), json.dumps(metadata, ensure_ascii=False), source_id, project_id),
            )
            updated = conn.execute("SELECT s.*,count(c.id) AS chunk_count FROM research_sources s LEFT JOIN research_document_chunks c ON c.source_id=s.id WHERE s.id=? GROUP BY s.id", (source_id,)).fetchone()
        return self._source_payload(updated, updated["chunk_count"])

    def link_literature(self, project_id: str, source_id: str, entity_type: str, entity_id: str,
                        relationship: str = "RELATED_TO", notes: str = "") -> dict[str, Any]:
        if entity_type not in {"artifact", "graph_node", "branch", "step", "lemma", "conjecture", "experiment"}:
            raise ValueError("文献关联不支持该研究实体类型。")
        if relationship not in {"SUPPORTS", "CONTRADICTS", "USES", "RELATED_TO", "CITES"}:
            raise ValueError("不支持的文献关联关系。")
        now = _now()
        with self.store.connection() as conn:
            source = conn.execute("SELECT id FROM research_sources WHERE project_id=? AND id=?", (project_id, source_id)).fetchone()
            if not source:
                raise KeyError("未找到该文献来源。")
            if entity_type in {"artifact", "graph_node"}:
                table = "research_artifacts" if entity_type == "artifact" else "research_graph_nodes"
                if not conn.execute(f"SELECT id FROM {table} WHERE project_id=? AND id=?", (project_id, entity_id)).fetchone():
                    raise KeyError("关联的研究记录不存在。")
            elif entity_type == "branch":
                if not conn.execute("SELECT id FROM research_branches WHERE project_id=? AND (id=? OR name=?)", (project_id, entity_id, entity_id)).fetchone():
                    raise KeyError("关联的研究分支不存在。")
            elif entity_type == "step":
                plans = conn.execute("SELECT plan_json FROM research_plan_versions WHERE project_id=?", (project_id,)).fetchall()
                if not any(str(step.get("id")) == entity_id for plan in plans for step in _safe_json(plan[0], {}).get("steps", [])):
                    raise KeyError("关联的已保存研究步骤不存在。")
            else:
                node = conn.execute("SELECT node_type FROM research_graph_nodes WHERE project_id=? AND id=?", (project_id, entity_id)).fetchone()
                if not node or node[0] != entity_type:
                    raise KeyError("关联的类型化研究记录不存在。")
            conn.execute(
                "INSERT INTO research_literature_links(project_id,source_id,entity_type,entity_id,relationship,notes,created_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id,source_id,entity_type,entity_id,relationship) DO UPDATE SET notes=excluded.notes",
                (project_id, source_id, entity_type, entity_id, relationship, notes[:4000], now),
            )
        return {"projectId": project_id, "sourceId": source_id, "entityType": entity_type,
                "entityId": entity_id, "relationship": relationship, "notes": notes, "createdAt": now}

    def list_literature_links(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM research_literature_links WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
        return [{"sourceId": r["source_id"], "entityType": r["entity_type"], "entityId": r["entity_id"],
                 "relationship": r["relationship"], "notes": r["notes"], "createdAt": r["created_at"]} for r in rows]

    def search_sources(self, project_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        terms = [part for part in query.strip().split() if len(part) > 1][:8]
        if not terms:
            return []
        clause = " OR ".join("(c.content LIKE ? OR s.title LIKE ? OR s.abstract LIKE ?)" for _ in terms)
        args: list[Any] = [project_id]
        for term in terms:
            pattern = f"%{term[:80]}%"
            args.extend((pattern, pattern, pattern))
        args.append(max(1, min(limit * 6, 300)))
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT COALESCE(c.id,s.id) AS id,s.id AS source_id,s.title,s.url,s.abstract,c.page_number,"
                "COALESCE(c.section,'Abstract') AS section,COALESCE(NULLIF(c.content,''),s.abstract,'') AS content "
                "FROM research_sources s LEFT JOIN research_document_chunks c ON c.source_id=s.id WHERE s.project_id=? AND (" + clause + ") "
                "ORDER BY CASE WHEN c.chunk_index IS NULL THEN -1 ELSE c.chunk_index END LIMIT ?",
                args,
            ).fetchall()
        ranked = []
        for row in rows:
            item = dict(row)
            searchable = " ".join((item["title"], item["abstract"], item["content"])).casefold()
            item["matchScore"] = sum(1 for term in terms if term.casefold() in searchable)
            ranked.append(item)
        ranked.sort(key=lambda item: (-item["matchScore"], item.get("page_number") or 0, item["id"]))
        return [{key: value for key, value in item.items() if key != "abstract"} for item in ranked[:max(1, min(limit, 100))]]

    def research_context(self, project_id: str, query: str, *, memory_limit: int = 12, source_limit: int = 8) -> str:
        """Context for models includes only confirmed memories and cited local sources."""
        invalid_artifacts = {item["id"] for item in self.store.list_artifacts(project_id) if item["status"] == "human-marked-invalid"}
        from aleggo.study.research_memory import ResearchMemory

        long_term = ResearchMemory(self.store)
        project = self.store.get_project(project_id) or {}
        relevance_query = " ".join([str(project.get("question") or ""), query])
        # Under a shared binding the store is the owner's; do not inject the
        # owner's personal long-term memories into the sharee's model context.
        try:
            from aleggo.study import research_sharing
            include_lt = research_sharing.current_binding() is None
        except Exception:
            include_lt = True
        memories = [item for item in long_term.relevant(
            project_id, relevance_query, limit=memory_limit + 6, include_long_term=include_lt)
                    if item.get("sourceArtifactId") not in invalid_artifacts][:max(0, min(memory_limit, 30))]
        sources = self.search_sources(project_id, query, source_limit)
        sections: list[str] = []
        if memories:
            sections.append("已确认的研究者记忆：\n" + "\n".join("- " + item["content"][:1200] for item in memories))
        if sources:
            excerpts = []
            for item in sources:
                location = item.get("section") or (f"page {item['page_number']}" if item.get("page_number") else "indexed record")
                citation = f"[{item['title']} · {location}]({item.get('url') or 'local source'})"
                excerpts.append(citation + "\n" + str(item.get("content") or "")[:1800])
            sections.append("Relevant indexed source passages (cite these titles in the result):\n" + "\n\n".join(excerpts))
        failed = self.failed_paths(project_id)[:12]
        if failed:
            sections.insert(0, "Recorded failed routes; do not retry without satisfying the retry condition:\n" + json.dumps(failed, ensure_ascii=False)[:6000])
        graph = self.list_graph(project_id)
        graph["nodes"] = [node for node in graph["nodes"] if node["status"] != "rejected"]
        if graph["nodes"]:
            sections.insert(1, "Research claims and evidence (preserve their recorded verification status):\n" + json.dumps(graph["nodes"][-24:], ensure_ascii=False)[:5000])
        artifacts = [item for item in self.store.list_artifacts(project_id) if item["id"] not in invalid_artifacts]
        # Researcher messages sent during a run (notes, results, lemmas) steer every later step.
        guidance = [a for a in artifacts if a["agentRole"] == "researcher" and a["status"] == "human-note"][:10]
        if guidance:
            sections.insert(0, "Researcher guidance added during this research (treat as constraints and direction; do not ignore):\n" + "\n".join(
                f"- [{a['title']}] {a['content'][:1500]}" for a in guidance))
        if artifacts:
            sections.append("Recent research outputs:\n" + "\n".join(
                f"[{a['id']}] {a['title']} ({a['status']}): {a['content'][:700]}" for a in artifacts[:6]))
        related = long_term.context_section(project_id, relevance_query)
        if related:
            sections.append(related)
        return "\n\n".join(sections)[:20_000]

    def add_memory(self, project_id: str | None, content: str, source_artifact_id: str | None = None, confirmed: bool = False) -> dict[str, Any]:
        clean = content.strip()
        if not clean or len(clean) > 8000:
            raise ValueError("研究记忆须为 1 到 8000 个字符。")
        memory_id, now = _id("memory"), _now()
        with self.store.connection() as conn:
            conn.execute("INSERT INTO research_memories(id,project_id,content,source_artifact_id,confirmed,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (memory_id, project_id, clean, source_artifact_id, int(confirmed), now, now))
        return {"id": memory_id, "projectId": project_id, "content": clean, "confirmed": confirmed, "createdAt": now}

    def list_memories(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM research_memories WHERE project_id IS NULL OR project_id=? ORDER BY confirmed DESC,updated_at DESC", (project_id,)).fetchall()
        return [{"id": r["id"], "projectId": r["project_id"], "content": r["content"], "sourceArtifactId": r["source_artifact_id"], "confirmed": bool(r["confirmed"]), "createdAt": r["created_at"], "updatedAt": r["updated_at"]} for r in rows]

    def confirm_memory(self, project_id: str, memory_id: str, confirmed: bool) -> bool:
        with self.store.connection() as conn:
            cur = conn.execute("UPDATE research_memories SET confirmed=?,updated_at=? WHERE id=? AND (project_id IS NULL OR project_id=?)", (int(confirmed), _now(), memory_id, project_id))
        return cur.rowcount > 0

    def record_tool_run(self, project_id: str, tool_name: str, input_data: dict[str, Any], output_text: str, status: str, *, step_id: str = "", branch_name: str = "main", elapsed_ms: int = 0, metadata: dict[str, Any] | None = None) -> str:
        run_id, now = _id("tool"), _now()
        # A runaway worker can dump megabytes of output; cap what hits the DB.
        output_text = str(output_text or "")
        if len(output_text) > 200_000:
            output_text = output_text[:200_000] + "\n…[输出过长，已截断]"
        input_json = json.dumps(input_data, ensure_ascii=False, default=str)
        if len(input_json) > 100_000:
            input_json = input_json[:100_000] + "…[输入过长，已截断]"
        try:
            elapsed_ms = max(0, int(float(elapsed_ms or 0)))
        except (TypeError, ValueError):
            elapsed_ms = 0
        with self.store.connection() as conn:
            branch = conn.execute("SELECT id FROM research_branches WHERE project_id=? AND name=?", (project_id, branch_name)).fetchone()
            conn.execute("INSERT INTO research_tool_runs(id,project_id,branch_id,step_id,tool_name,input_json,output_text,status,elapsed_ms,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (run_id, project_id, branch["id"] if branch else None, step_id[:200], tool_name[:100], input_json, output_text, status[:80], elapsed_ms, json.dumps(metadata or {}, ensure_ascii=False, default=str), now))
        return run_id

    def record_failed_path(
        self, project_id: str, version: int, step_id: str, title: str,
        branch_name: str, error_type: str, diagnostic: str,
        *, route: str = "", route_signature: str = "", assumptions: list | None = None,
        evidence_ids: list[str] | None = None, retry_condition: str = "",
    ) -> dict[str, str]:
        """Persist a failed execution path as an artifact, graph record, and tool event."""
        now = _now()
        artifact_id, node_id, run_id = _id("artifact"), _id("node"), _id("tool")
        path_id = _id("failedpath")
        assumption_json = json.dumps(assumptions or [], ensure_ascii=False, sort_keys=True)
        signature = route_signature or hashlib.sha256(" ".join((route or title).casefold().split()).encode()).hexdigest()
        retry_condition = retry_condition or "Retry only after changed assumptions, new evidence, or repaired prerequisites; record the change."
        content = f"Step: {title}\nStep ID: {step_id}\nFailure type: {error_type}\nDiagnostic: {diagnostic[:4000]}"
        with self.store.connection() as conn:
            branch = conn.execute(
                "SELECT id FROM research_branches WHERE project_id=? AND name=?",
                (project_id, branch_name),
            ).fetchone()
            if not branch:
                raise ValueError("失败步骤的研究分支已不存在。")
            conn.execute(
                "INSERT INTO research_artifacts(id,project_id,branch_id,plan_version,step_id,agent_role,title,content,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, project_id, branch["id"], version, step_id[:200], "runtime", "Failed path: " + title[:400], content[:50000], "failed", now),
            )
            conn.execute(
                "INSERT INTO research_graph_nodes(id,project_id,branch_id,node_type,title,content,status,artifact_id,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (node_id, project_id, branch["id"], "evidence", "Failed execution path: " + title[:400], content[:100000], "unverified", artifact_id,
                 json.dumps({"failure": True, "errorType": error_type, "stepId": step_id}, ensure_ascii=False), now, now),
            )
            conn.execute(
                "INSERT INTO research_tool_runs(id,project_id,branch_id,step_id,tool_name,input_json,output_text,status,elapsed_ms,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, branch["id"], step_id[:200], "research-step-failure",
                 json.dumps({"title": title, "errorType": error_type}, ensure_ascii=False), content[:50000], "FAILED", 0, now),
            )
            project = conn.execute("SELECT current_version FROM research_projects WHERE id=?", (project_id,)).fetchone()
            self.store._intervention(conn, project_id, project["current_version"] if project else version,
                                     "failed_path_recorded", "runtime", {"stepId": step_id, "artifactId": artifact_id, "nodeId": node_id}, now)
            conn.execute(
                "INSERT INTO research_failed_paths(id,project_id,step_id,branch_name,route_text,route_signature,assumptions_json,assumption_hash,status,reason,evidence_ids_json,retry_condition,source_artifact_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (path_id, project_id, step_id, branch_name, (route or title)[:20000], signature,
                 assumption_json, hashlib.sha256(assumption_json.encode()).hexdigest(), "FAILED", diagnostic[:8000],
                 json.dumps(evidence_ids or [artifact_id]), retry_condition[:4000], artifact_id, now, now),
            )
        return {"artifactId": artifact_id, "nodeId": node_id, "toolRunId": run_id, "failedPathId": path_id}

    def failed_paths(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM research_failed_paths WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [{"id": r["id"], "stepId": r["step_id"], "branch": r["branch_name"], "route": r["route_text"],
                 "routeSignature": r["route_signature"], "assumptions": _safe_json(r["assumptions_json"], []),
                 "assumptionHash": r["assumption_hash"], "status": r["status"], "reason": r["reason"],
                 "evidenceIds": _safe_json(r["evidence_ids_json"], []), "retryCondition": r["retry_condition"],
                 "artifactId": r["source_artifact_id"], "createdAt": r["created_at"]} for r in rows]

    def list_tool_runs(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT t.*,b.name AS branch_name FROM research_tool_runs t LEFT JOIN research_branches b ON b.id=t.branch_id WHERE t.project_id=? ORDER BY t.created_at DESC LIMIT ?", (project_id, max(1, min(limit, 500)))).fetchall()
        return [{"id": r["id"], "branch": r["branch_name"], "stepId": r["step_id"], "tool": r["tool_name"], "input": _safe_json(r["input_json"], {}), "output": r["output_text"], "status": r["status"], "elapsedMs": r["elapsed_ms"], "metadata": _safe_json(r["metadata_json"] or "{}", {}) if "metadata_json" in r.keys() else {}, "createdAt": r["created_at"]} for r in rows]

    def record_import(self, source_hash: str, original_name: str, summary: dict[str, Any], *, project_mappings: list[tuple[str, str]], records: list[dict[str, Any]]) -> None:
        now = _now()
        with self.store.connection() as conn:
            conn.execute("INSERT INTO mra_imports(source_hash,original_name,imported_at,summary_json) VALUES(?,?,?,?)", (source_hash, original_name[:255], now, json.dumps(summary, ensure_ascii=False)))
            conn.executemany("INSERT INTO mra_project_imports(source_hash,source_project_id,target_project_id) VALUES(?,?,?)", [(source_hash, source_id, target_id) for source_id, target_id in project_mappings])
            conn.executemany(
                "INSERT INTO mra_legacy_records(source_hash,source_table,source_record_id,target_project_id,collection,payload_json,source_created_at) VALUES(?,?,?,?,?,?,?)",
                [(source_hash, item["table"], item["id"], item.get("targetProjectId"), item.get("collection", ""), json.dumps(item["payload"], ensure_ascii=False, default=str), item.get("createdAt", "")) for item in records],
            )

    def import_summary(self, source_hash: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM mra_imports WHERE source_hash=?", (source_hash,)).fetchone()
        return {"sourceHash": row["source_hash"], "originalName": row["original_name"], "importedAt": row["imported_at"], **_safe_json(row["summary_json"], {})} if row else None

    def list_legacy_records(self, project_id: str, collection: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            if collection:
                rows = conn.execute("SELECT source_table,source_record_id,collection,payload_json,source_created_at FROM mra_legacy_records WHERE target_project_id=? AND collection=? ORDER BY source_created_at DESC LIMIT ?", (project_id, collection, max(1, min(limit, 500)))).fetchall()
            else:
                rows = conn.execute("SELECT source_table,source_record_id,collection,payload_json,source_created_at FROM mra_legacy_records WHERE target_project_id=? ORDER BY source_created_at DESC LIMIT ?", (project_id, max(1, min(limit, 500)))).fetchall()
        return [{"sourceTable": r["source_table"], "id": r["source_record_id"], "collection": r["collection"], "payload": _safe_json(r["payload_json"], {}), "createdAt": r["source_created_at"]} for r in rows]

    def list_imports(self) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT * FROM mra_imports ORDER BY imported_at DESC").fetchall()
        return [{"sourceHash": row["source_hash"], "originalName": row["original_name"], "importedAt": row["imported_at"], **_safe_json(row["summary_json"], {})} for row in rows]

    def update_artifact_feedback(self, project_id: str, artifact_id: str, feedback: str) -> bool:
        if feedback not in {"useful", "invalid"}:
            raise ValueError("反馈须为 useful 或 invalid。")
        status = "human-marked-useful" if feedback == "useful" else "human-marked-invalid"
        with self.store.connection() as conn:
            cursor = conn.execute("UPDATE research_artifacts SET status=? WHERE id=? AND project_id=?", (status, artifact_id, project_id))
            if cursor.rowcount:
                nodes = conn.execute("SELECT id,metadata_json FROM research_graph_nodes WHERE project_id=? AND artifact_id=?", (project_id, artifact_id)).fetchall()
                for node in nodes:
                    metadata = _safe_json(node["metadata_json"] or "{}", {})
                    metadata["humanFeedback"] = feedback
                    if feedback == "invalid":
                        metadata["verificationSuspended"] = True
                        conn.execute("UPDATE research_graph_nodes SET status='rejected',metadata_json=?,updated_at=? WHERE id=?",
                                     (json.dumps(metadata, ensure_ascii=False), _now(), node["id"]))
                    else:
                        conn.execute("UPDATE research_graph_nodes SET metadata_json=?,updated_at=? WHERE id=?",
                                     (json.dumps(metadata, ensure_ascii=False), _now(), node["id"]))
                project = conn.execute("SELECT current_version FROM research_projects WHERE id=?", (project_id,)).fetchone()
                ResearchStore._intervention(conn, project_id, project["current_version"] if project else None, "artifact_feedback", "researcher", {"artifactId": artifact_id, "feedback": feedback}, _now())
        return cursor.rowcount > 0
