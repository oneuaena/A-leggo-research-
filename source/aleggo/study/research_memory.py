"""Cross-project long-term research memory.

A university mathematician works on many related questions over years.  The
research store already supports *workspace* memories (``project_id IS NULL``)
that every project sees; this module turns them into a usable long-term memory:

* **distillation** – at any time (typically when a project finishes) the
  project's failed routes, verified results, researcher guidance and important
  sources are distilled into *suggested* long-term memories.  Suggestions are
  stored unconfirmed and never reach a model until the researcher confirms them;
* **relevance** – instead of "the 12 newest", the memories most relevant to the
  current question are selected (token overlap with CJK bigrams so Chinese and
  formulas both match) and their use is counted;
* **related projects** – earlier projects on similar questions are found and
  summarised (status, verified claims, failed routes) so a new project starts
  from what was already learned;
* **curation** – edit, tag, classify, promote a project memory to long-term,
  or delete.

Everything is local SQLite in the researcher's own data directory.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import math
import re
import sqlite3
from typing import Any

from aleggo.study.research_store import ResearchStore

MEMORY_KINDS = ("note", "technique", "result", "failed-route", "reference", "preference", "definition")
MAX_MEMORY_CHARS = 8000
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_']+|\d+(?:\.\d+)?|\\[A-Za-z]+")
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")
_STOP = frozenset(
    "the a an and or of to in on for is are be by with that this as at from it we our can not if then let all any "
    "each one two show prove whether does do has have which there their such via using use into than also".split()
)
_CJK_STOP = frozenset({"的", "是", "在", "和", "与", "了", "对", "一个", "我们", "是否", "如何", "这个", "可以"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def tokens(text: str) -> set[str]:
    """Search tokens: lower-cased words/numbers/TeX commands plus CJK unigrams and bigrams."""
    value = str(text or "")
    found = {item.casefold() for item in _WORD.findall(value)}
    found = {item for item in found if item not in _STOP and (len(item) > 1 or item.isdigit())}
    for run in _CJK.findall(value):
        if len(run) == 1:
            found.add(run)
        for index in range(len(run) - 1):
            pair = run[index:index + 2]
            if pair not in _CJK_STOP:
                found.add(pair)
    return found


def relevance(query: set[str], text: str) -> float:
    other = tokens(text)
    if not query or not other:
        return 0.0
    shared = query & other
    if not shared:
        return 0.0
    # Overlap normalised by both sizes; long memories do not win by length alone.
    return len(shared) / math.sqrt(len(query) * len(other))


class ResearchMemory:
    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ #
    # Rows
    # ------------------------------------------------------------------ #
    @staticmethod
    def _payload(row: Any) -> dict[str, Any]:
        keys = row.keys()

        def get(name: str, default: Any) -> Any:
            return row[name] if name in keys and row[name] is not None else default

        try:
            tags = json.loads(get("tags_json", "[]"))
        except ValueError:
            tags = []
        return {
            "id": row["id"], "projectId": row["project_id"], "scope": "long-term" if row["project_id"] is None else "project",
            "content": row["content"], "sourceArtifactId": row["source_artifact_id"], "confirmed": bool(row["confirmed"]),
            "kind": get("memory_kind", "note") or "note", "tags": tags if isinstance(tags, list) else [],
            "originProject": get("origin_project", ""), "useCount": int(get("use_count", 0) or 0),
            "lastUsedAt": get("last_used_at", ""), "suggested": bool(get("suggestion_key", "")) and not bool(row["confirmed"]),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def get(self, memory_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_memories WHERE id=?", (memory_id,)).fetchone()
        return self._payload(row) if row else None

    def list_long_term(self, query: str = "", *, kind: str = "", include_unconfirmed: bool = True,
                       limit: int = 200) -> list[dict[str, Any]]:
        """List personal long-term memories only (project_id IS NULL)."""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 200
        limit = max(0, min(limit, 500))
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM research_memories WHERE project_id IS NULL ORDER BY confirmed DESC,updated_at DESC"
            ).fetchall()
        items = [self._payload(row) for row in rows]
        kind = str(kind or "").strip()
        if kind:
            if kind not in MEMORY_KINDS:
                return []
            items = [item for item in items if item["kind"] == kind]
        if not include_unconfirmed:
            items = [item for item in items if item["confirmed"]]
        needle = str(query or "").strip()
        if needle:
            wanted = tokens(needle)
            folded = needle.casefold()
            scored = []
            for item in items:
                haystack = item["content"] + " " + " ".join(item["tags"])
                score = relevance(wanted, haystack) + (1.0 if folded in haystack.casefold() else 0.0)
                if score > 0:
                    scored.append((score, item))
            scored.sort(key=lambda pair: -pair[0])
            items = [item for _, item in scored]
        return items[:limit]

    def add(self, content: str, *, project_id: str | None = None, kind: str = "note", tags: list[str] | None = None,
            confirmed: bool = True, origin_project: str = "", source_artifact_id: str | None = None,
            suggestion_key: str = "") -> dict[str, Any]:
        clean = str(content or "").strip()
        if not clean or len(clean) > MAX_MEMORY_CHARS:
            raise ValueError(f"研究记忆须为 1 到 {MAX_MEMORY_CHARS} 个字符。")
        kind = kind if kind in MEMORY_KINDS else "note"
        # A model may return "tags": "重要" instead of a list; iterating a str
        # would silently store one tag per character.
        if isinstance(tags, str):
            tags = [tags]
        tag_list = sorted({str(tag).strip()[:40] for tag in (tags or []) if str(tag).strip()})[:12]
        memory_id = "memory_" + hashlib.sha256((clean + _now()).encode()).hexdigest()[:24]
        now = _now()
        try:
            with self.store.connection() as conn:
                conn.execute(
                    "INSERT INTO research_memories(id,project_id,content,source_artifact_id,confirmed,created_at,updated_at,"
                    "memory_kind,tags_json,origin_project,suggestion_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (memory_id, project_id, clean, source_artifact_id, int(confirmed), now, now, kind,
                     json.dumps(tag_list, ensure_ascii=False), origin_project, (suggestion_key or "")),
                )
        except sqlite3.IntegrityError as exc:
            # suggestion_key is NOT NULL (fixed above); what remains is a
            # genuine foreign-key violation on project_id/source_artifact_id.
            if "FOREIGN KEY" not in str(exc).upper():
                raise
            raise ValueError("关联的项目或来源不存在，记忆保存失败。") from exc
        return self.get(memory_id) or {}

    def update(self, memory_id: str, *, content: str | None = None, kind: str | None = None,
               tags: list[str] | None = None, confirmed: bool | None = None,
               require_long_term: bool = False, require_project_id: str | None = None) -> dict[str, Any]:
        current = self.get(memory_id)
        if not current:
            raise KeyError("未找到该研究记忆。")
        if require_long_term and current.get("projectId"):
            raise PermissionError("只能修改个人长期记忆，不能改写其他项目的记忆。")
        if require_project_id is not None and current.get("projectId") != require_project_id:
            raise PermissionError("不能修改其他项目的记忆。")
        fields: dict[str, Any] = {}
        if content is not None:
            clean = str(content or "").strip()
            if not clean or len(clean) > MAX_MEMORY_CHARS:
                raise ValueError(f"研究记忆须为 1 到 {MAX_MEMORY_CHARS} 个字符。")
            fields["content"] = clean
        if kind is not None:
            if kind not in MEMORY_KINDS:
                raise ValueError("未知的记忆类型。")
            fields["memory_kind"] = kind
        if tags is not None:
            if isinstance(tags, str):
                tags = [tags]
            fields["tags_json"] = json.dumps(sorted({str(t).strip()[:40] for t in tags if str(t).strip()})[:12], ensure_ascii=False)
        if confirmed is not None:
            fields["confirmed"] = int(confirmed)
        if fields:
            fields["updated_at"] = _now()
            assignments = ",".join(f"{name}=?" for name in fields)
            with self.store.connection() as conn:
                conn.execute(f"UPDATE research_memories SET {assignments} WHERE id=?", (*fields.values(), memory_id))
        return self.get(memory_id) or {}

    def delete(self, memory_id: str, *, require_long_term: bool = False, require_project_id: str | None = None) -> bool:
        current = self.get(memory_id)
        if not current:
            return False
        if require_long_term and current.get("projectId"):
            raise PermissionError("只能删除个人长期记忆。")
        if require_project_id is not None and current.get("projectId") != require_project_id:
            raise PermissionError("不能删除其他项目的记忆。")
        with self.store.connection() as conn:
            return conn.execute("DELETE FROM research_memories WHERE id=?", (memory_id,)).rowcount > 0

    def promote(self, project_id: str, memory_id: str) -> dict[str, Any]:
        """Turn a project memory into a long-term memory visible to every project.

        Only confirmed project memories may be promoted (avoids leaking draft
        suggestions into every future project context).
        """
        current = self.get(memory_id)
        if not current or current.get("projectId") != project_id:
            raise KeyError("未找到该项目记忆。")
        if current.get("scope") == "long-term":
            raise ValueError("该记忆已是长期记忆，无需再提升。")
        if not current.get("confirmed"):
            raise ValueError("请先确认该记忆，再提升为长期记忆。")
        with self.store.connection() as conn:
            # Keep source_artifact_id: promoting to long-term memory must not
            # sever the provenance link to the artifact it came from.
            changed = conn.execute(
                "UPDATE research_memories SET project_id=NULL,origin_project=?,updated_at=? "
                "WHERE id=? AND project_id=? AND confirmed=1",
                (project_id, _now(), memory_id, project_id),
            ).rowcount
        if not changed:
            raise KeyError("未找到该项目记忆。")
        return self.get(memory_id) or {}

    # ------------------------------------------------------------------ #
    # Relevance for model context
    # ------------------------------------------------------------------ #
    def relevant(self, project_id: str, query: str, *, limit: int = 12, record_use: bool = True,
                 include_long_term: bool = True) -> list[dict[str, Any]]:
        """Confirmed memories for this project, most relevant first.

        Pass include_long_term=False on a shared owner store so another
        researcher's long-term notes are not leaked into model context.
        """
        if include_long_term:
            sql, args = (
                "SELECT * FROM research_memories WHERE confirmed=1 AND (project_id IS NULL OR project_id=?)",
                (project_id,),
            )
        else:
            sql, args = (
                "SELECT * FROM research_memories WHERE confirmed=1 AND project_id=?",
                (project_id,),
            )
        with self.store.connection() as conn:
            rows = conn.execute(sql, args).fetchall()
        wanted = tokens(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            item = self._payload(row)
            score = relevance(wanted, item["content"] + " " + " ".join(item["tags"]))
            if item["scope"] == "project":
                score += 0.15 if score > 0 else 0.05  # slight boost; empty overlap still eligible but weak
            elif item["kind"] == "preference":
                score = max(score, 0.3)
            elif score <= 0:
                continue
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[1]["updatedAt"], reverse=True)
        scored.sort(key=lambda pair: -pair[0])  # stable: newest first among equal scores
        chosen = [item for _, item in scored[:max(0, min(limit, 30))]]
        if record_use and chosen:
            now = _now()
            with self.store.connection() as conn:
                conn.executemany(
                    "UPDATE research_memories SET use_count=COALESCE(use_count,0)+1,last_used_at=? WHERE id=?",
                    [(now, item["id"]) for item in chosen if item["scope"] == "long-term"],
                )
        return chosen

    # ------------------------------------------------------------------ #
    # Distillation
    # ------------------------------------------------------------------ #
    def suggestions(self, project_id: str) -> list[dict[str, Any]]:
        """Deterministic candidate lessons from one project (not yet stored)."""
        from aleggo.study.research_domain import ResearchDomainStore

        project = self.store.get_project(project_id)
        if not project:
            raise KeyError("未找到该研究项目。")
        domain = ResearchDomainStore(self.store)
        question = str(project.get("question") or "")[:300]
        topic = " ".join(sorted(tokens(question), key=len, reverse=True)[:6])
        candidates: list[dict[str, Any]] = []

        def add(kind: str, text: str, source: str = "") -> None:
            text = text.strip()
            if text:
                key = hashlib.sha256((kind + "\n" + " ".join(text.split())).encode()).hexdigest()[:32]
                candidates.append({"kind": kind, "content": text[:MAX_MEMORY_CHARS], "suggestionKey": key,
                                   "sourceArtifactId": source or None, "tags": [tag for tag in topic.split()[:4]]})

        for path in domain.failed_paths(project_id)[:10]:
            add("failed-route", f"在研究「{question}」时，路线「{path['route'][:300]}」未成功：{path['reason'][:600]}"
                + (f"（重试条件：{path['retryCondition'][:300]}）" if path.get("retryCondition") else ""), path.get("artifactId") or "")
        for node in domain.list_graph(project_id)["nodes"]:
            status = str(node.get("status") or "").casefold()
            if status in {"verified", "kernel_certified", "formally_verified", "proved"}:
                add("result", f"已验证结论（来自「{question}」）：{node['title'][:300]} —— {str(node.get('content') or '')[:900]}",
                    node.get("artifactId") or "")
        for artifact in self.store.list_artifacts(project_id):
            if artifact.get("agentRole") == "researcher" and artifact.get("status") == "human-note":
                add("preference", f"研究者在「{question}」中的指导：{artifact['content'][:900]}", artifact["id"])
        for source in domain.list_sources(project_id):
            if source.get("important"):
                authors = str(source.get("authors") or "")[:120]
                year = source.get("year") or ""
                add("reference", f"重要文献：{source['title'][:300]}" + (f"，{authors}" if authors else "")
                    + (f"（{year}）" if year else "") + (f" {source['url']}" if source.get("url") else "")
                    + f"。用于「{question}」。")
        with self.store.connection() as conn:
            known = {row[0] for row in conn.execute(
                "SELECT suggestion_key FROM research_memories "
                "WHERE suggestion_key IS NOT NULL AND suggestion_key<>''").fetchall()}
        unique: dict[str, dict[str, Any]] = {}
        for item in candidates:
            if item["suggestionKey"] not in known:
                unique.setdefault(item["suggestionKey"], item)
        return list(unique.values())[:40]

    def distill(self, project_id: str) -> list[dict[str, Any]]:
        """Store this project's suggestions as unconfirmed long-term memories."""
        created = []
        for item in self.suggestions(project_id):
            created.append(self.add(item["content"], kind=item["kind"], tags=item["tags"], confirmed=False,
                                    origin_project=project_id, suggestion_key=item["suggestionKey"]))
        return created

    # ------------------------------------------------------------------ #
    # Related projects
    # ------------------------------------------------------------------ #
    def related_projects(self, project_id: str, query: str = "", *, limit: int = 5,
                         threshold: float = 0.12) -> list[dict[str, Any]]:
        from aleggo.study.research_domain import ResearchDomainStore

        project = self.store.get_project(project_id)
        if not project:
            raise KeyError("未找到该研究项目。")
        plan = project.get("plan") or {}
        wanted = tokens(" ".join([query, str(project.get("question") or ""), str(plan.get("objective") or "")]))
        domain = ResearchDomainStore(self.store)
        scored = []
        for other in self.store.list_projects():
            if other["id"] == project_id:
                continue
            other_plan = other.get("plan") or {}
            # Models sometimes write "steps": null or mix raw strings into the
            # step list; both used to crash this scan with TypeError/AttributeError.
            raw_steps = other_plan.get("steps") or []
            step_titles = [str(step.get("title") or "") for step in raw_steps[:20] if isinstance(step, dict)]
            text = " ".join([str(other.get("question") or ""), str(other_plan.get("objective") or ""),
                             " ".join(step_titles)])
            score = relevance(wanted, text)
            if score >= threshold:
                scored.append((score, other))
        scored.sort(key=lambda pair: -pair[0])
        results = []
        for score, other in scored[:max(1, min(limit, 20))]:
            nodes = domain.list_graph(other["id"])["nodes"]
            verified = [node["title"] for node in nodes if str(node.get("status") or "").casefold() in {"verified", "kernel_certified"}]
            failed = [path["route"][:200] for path in domain.failed_paths(other["id"])[:5]]
            results.append({"projectId": other["id"], "question": other.get("question", ""), "status": other.get("status", ""),
                            "score": round(score, 3), "verified": verified[:5], "failedRoutes": failed,
                            "updatedAt": other.get("updatedAt") or other.get("updated_at") or ""})
        return results

    def context_section(self, project_id: str, query: str, *, limit: int = 3) -> str:
        """Short "earlier related projects" section for model prompts."""
        try:
            related = self.related_projects(project_id, query, limit=limit, threshold=0.2)
        except KeyError:
            return ""
        if not related:
            return ""
        lines = []
        for item in related:
            line = f"- 相关历史项目（{item['status']}）：{item['question'][:300]}"
            if item["verified"]:
                line += "｜已验证：" + "; ".join(title[:150] for title in item["verified"][:3])
            if item["failedRoutes"]:
                line += "｜失败路线：" + "; ".join(item["failedRoutes"][:3])
            lines.append(line)
        return ("同一研究者的相关历史项目（可复用已验证结论、避开已记录失败路线；仅作语境，不是本项目证据）：\n"
                + "\n".join(lines))[:3000]
