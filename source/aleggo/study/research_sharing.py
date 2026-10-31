"""Multi-person research collaboration.

Research projects live in the owner's private store (``data/user`` for an
administrator, ``data/users/<id>`` for everyone else).  Sharing keeps it that
way: a deployment-wide registry (``data/system/research/shares.json``) records
which accounts may open which project and with which role, and API requests
for a shared project are bound to the *owner's* store.

Roles
  ``owner``  – everything, including managing members;
  ``editor`` – everything on the project (plans, runs, discussion, imports,
               reviews, comments, assignments) except managing members;
  ``viewer`` – read-only, plus comments.

Collaboration records are stored with the project (owner store):
``research_comments`` (threaded on project / step / claim / artifact / review
step targets) and ``research_assignments`` (who works on which plan step).
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import Any
from uuid import uuid4

from aleggo.study.research_store import ResearchStore

ROLES = ("viewer", "editor")
COMMENT_TARGETS = ("project", "step", "node", "artifact", "review-step", "source")
_LOCK = threading.Lock()


@dataclass(frozen=True)
class SharedBinding:
    project_id: str
    owner_id: str
    owner_username: str
    owner_is_admin: bool
    role: str
    store: ResearchStore


_BINDING: ContextVar[SharedBinding | None] = ContextVar("aleggo_research_shared_binding", default=None)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# Identity helpers (multi-user deployments; single-user runs act as admin)
# --------------------------------------------------------------------------- #

def current_identity() -> dict[str, Any]:
    try:
        from aleggo.multi_user.context import get_current_user

        user = get_current_user()
        return {"id": user.id, "username": user.username, "isAdmin": user.is_admin}
    except Exception:  # pragma: no cover - multi-user package always present in the app
        return {"id": "local-admin", "username": "local", "isAdmin": True}


def registry_path() -> Path:
    configured = os.environ.get("ALEGGO_RESEARCH_SHARES_FILE", "").strip()
    if configured:
        return Path(configured)
    from aleggo.multi_user.paths import SYSTEM_ROOT

    return SYSTEM_ROOT / "research" / "shares.json"


def _read() -> list[dict[str, Any]]:
    path = registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    shares = data.get("shares") if isinstance(data, dict) else None
    return [item for item in shares or [] if isinstance(item, dict)]


def _write(shares: list[dict[str, Any]]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".shares-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "shares": shares}, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def list_candidates() -> list[dict[str, Any]]:
    """Accounts the current user can share with (enabled accounts other than oneself)."""
    me = current_identity()
    try:
        from aleggo.multi_user.identity import list_user_info

        users = list_user_info()
    except Exception:
        users = []
    return [{"id": item["id"], "username": item["username"], "role": item.get("role", "user")}
            for item in users if item.get("id") and item["id"] != me["id"] and not item.get("disabled")]


def owner_store(owner_id: str, owner_is_admin: bool) -> ResearchStore:
    from aleggo.multi_user.paths import get_path_service_for_scope, scope_for_user
    from aleggo.study.store import AleggoStore

    scope = scope_for_user(owner_id, is_admin=owner_is_admin)
    root = (get_path_service_for_scope(scope).get_workspace_dir() / "aleggo").resolve()
    return ResearchStore(AleggoStore(root))


# --------------------------------------------------------------------------- #
# Share registry
# --------------------------------------------------------------------------- #

def shares_for_project(project_id: str) -> list[dict[str, Any]]:
    return [item for item in _read() if item.get("projectId") == project_id]


def shared_with_me() -> list[dict[str, Any]]:
    me = current_identity()
    return [item for item in _read() if item.get("memberId") == me["id"]]


def add_member(project_id: str, member_id: str, role: str, *, project_question: str = "") -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError("角色须为 viewer（只读）或 editor（可编辑）。")
    me = current_identity()
    if member_id == me["id"]:
        raise ValueError("您已是该项目的所有者。")
    candidate = next((item for item in list_candidates() if item["id"] == member_id), None)
    if candidate is None:
        raise KeyError("该账号不存在或已停用。")
    with _LOCK:
        shares = [item for item in _read() if not (item.get("projectId") == project_id and item.get("memberId") == member_id)]
        entry = {"projectId": project_id, "ownerId": me["id"], "ownerUsername": me["username"], "ownerIsAdmin": me["isAdmin"],
                 "memberId": member_id, "memberUsername": candidate["username"], "role": role,
                 "projectQuestion": project_question[:300], "createdAt": _now()}
        shares.append(entry)
        _write(shares)
    return entry


def remove_member(project_id: str, member_id: str, store: "ResearchStore | None" = None) -> bool:
    """Owner revokes a member, or the member leaves. Locked against concurrent writes.

    When ``store`` is given, the member's open assignments are removed as well,
    so the task list no longer shows work to someone who lost access.
    """
    me = current_identity()
    member_id = str(member_id or "").strip()
    if not member_id:
        return False
    with _LOCK:
        shares = _read()
        target = next((item for item in shares if item.get("projectId") == project_id and item.get("memberId") == member_id), None)
        if target is None:
            return False
        is_owner = target.get("ownerId") == me["id"]
        is_self = member_id == me["id"]
        if not is_owner and not is_self:
            raise PermissionError("仅所有者可撤销其他成员的共享，或由成员自行退出。")
        kept = [item for item in shares if not (item.get("projectId") == project_id and item.get("memberId") == member_id)]
        _write(kept)
    if store is not None:
        ensure_tables(store)
        with store.connection() as conn:
            conn.execute("DELETE FROM research_assignments WHERE project_id=? AND assignee_id=? AND status='open'",
                         (project_id, member_id))
    return True


def resolve(project_id: str) -> SharedBinding | None:
    """The share granting the current user access to someone else's project.

    Reads under ``_LOCK`` so a concurrent revoke cannot leave a torn registry
    view between list membership and the returned binding.
    """
    me = current_identity()
    with _LOCK:
        shares = list(_read())
    for item in shares:
        if item.get("projectId") == project_id and item.get("memberId") == me["id"] and item.get("ownerId") != me["id"]:
            store = owner_store(str(item["ownerId"]), bool(item.get("ownerIsAdmin")))
            if store.get_project(project_id) is None:
                return None
            return SharedBinding(project_id, str(item["ownerId"]), str(item.get("ownerUsername") or ""),
                                 bool(item.get("ownerIsAdmin")), str(item.get("role") or "viewer"), store)
    return None


def bind(binding: SharedBinding | None) -> None:
    _BINDING.set(binding)


def current_binding() -> SharedBinding | None:
    return _BINDING.get()


def assert_bound_project(project_id: str) -> None:
    """Refuse sibling-project access when a shared binding is active.

    The bound store is the *owner's* store; without this check a guessed sibling
    ``project_id`` on that store would otherwise succeed.
    """
    binding = current_binding()
    if binding is not None and binding.project_id != project_id:
        raise PermissionError("无权访问该项目（共享绑定仅覆盖已分享的项目）。")


def revalidate_share(project_id: str) -> None:
    """Fail closed if a share was revoked after the request binding was set."""
    binding = current_binding()
    if binding is None:
        return
    if binding.project_id != project_id:
        raise PermissionError("无权访问该项目（共享绑定仅覆盖已分享的项目）。")
    fresh = resolve(project_id)
    if fresh is None:
        bind(None)
        raise PermissionError("对该项目的共享访问已撤销。")
    if fresh.role != binding.role or fresh.owner_id != binding.owner_id:
        # Role/owner changed underfoot — rebind to the fresh grant or deny.
        bind(fresh)




def current_role(project_id: str) -> str:
    binding = _BINDING.get()
    if binding and binding.project_id == project_id:
        return binding.role
    return "owner"


def _project_in_own_store(project_id: str) -> bool:
    try:
        store = ResearchStore()
        with store.connection() as conn:
            return conn.execute("SELECT 1 FROM research_projects WHERE id=?", (project_id,)).fetchone() is not None
    except Exception:
        return False


def _is_comment_write_path(path: str, project_id: str) -> bool:
    """True only for this project's comment collection or a comment id under it.

    Rejects lookalikes such as ``/comments-extra`` or ``/commentsfoo``.
    """
    if not project_id or "/" in project_id or ".." in project_id:
        return False
    trimmed = path.split("?", 1)[0].rstrip("/")
    for api_prefix in ("", "/research", "/api/research"):
        marker = f"{api_prefix}/projects/{project_id}/comments"
        if trimmed == marker:
            return True
        prefix = marker + "/"
        if trimmed.startswith(prefix):
            rest = trimmed[len(prefix):]
            # One segment: comment id (no further nesting / path tricks).
            return bool(rest) and "/" not in rest and ".." not in rest
    return False


def binding_for_request(project_id: str, method: str, path: str) -> SharedBinding | None:
    """Resolve the store for a project-scoped request.

    Raises ``PermissionError`` for viewer writes and for actions only the
    owner may take on a shared project (deleting it).
    """
    if not project_id:
        return None
    me = current_identity()
    if not any(item.get("projectId") == project_id and item.get("memberId") == me["id"] for item in _read()):
        return None  # fast path: nothing is shared with this account for this project
    if _project_in_own_store(project_id):
        return None
    binding = resolve(project_id)
    if binding is None:
        return None
    verb = method.upper()
    trimmed = path.rstrip("/")
    if verb == "DELETE" and trimmed.endswith("/projects/" + project_id):
        raise PermissionError("仅所有者可删除共享项目。")
    if binding.role == "viewer" and verb not in {"GET", "HEAD", "OPTIONS"}:
        if not _is_comment_write_path(trimmed, project_id):
            raise PermissionError("您对此共享项目仅有只读权限。")
    return binding


def shared_project_summaries() -> list[dict[str, Any]]:
    items = []
    for share in shared_with_me():
        try:
            store = owner_store(str(share["ownerId"]), bool(share.get("ownerIsAdmin")))
            project = store.get_project(str(share["projectId"]))
        except Exception:
            project = None
        if project:
            items.append({**project, "sharing": {"role": share.get("role", "viewer"), "owner": share.get("ownerUsername", ""),
                                                 "ownerId": share.get("ownerId", "")}})
    return items


# --------------------------------------------------------------------------- #
# Comments and assignments (stored with the project)
# --------------------------------------------------------------------------- #

def ensure_tables(store: ResearchStore) -> None:
    with store.connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_comments (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL DEFAULT '',
                parent_id TEXT NOT NULL DEFAULT '',
                author_id TEXT NOT NULL,
                author_name TEXT NOT NULL,
                body TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_research_comments_project ON research_comments(project_id, created_at);
            CREATE TABLE IF NOT EXISTS research_assignments (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                step_id TEXT NOT NULL,
                assignee_id TEXT NOT NULL,
                assignee_name TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def _comment(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "targetType": row["target_type"], "targetId": row["target_id"], "parentId": row["parent_id"],
            "author": row["author_name"], "authorId": row["author_id"], "body": row["body"], "resolved": bool(row["resolved"]),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def list_comments(store: ResearchStore, project_id: str, target_type: str = "", target_id: str = "") -> list[dict[str, Any]]:
    ensure_tables(store)
    clauses, args = ["project_id=?"], [project_id]
    if target_type:
        clauses.append("target_type=?")
        args.append(target_type)
    if target_id:
        clauses.append("target_id=?")
        args.append(target_id)
    with store.connection() as conn:
        rows = conn.execute(f"SELECT * FROM research_comments WHERE {' AND '.join(clauses)} ORDER BY created_at LIMIT 1000", args).fetchall()
    return [_comment(row) for row in rows]


def add_comment(store: ResearchStore, project_id: str, body: str, *, target_type: str = "project", target_id: str = "",
                parent_id: str = "") -> dict[str, Any]:
    ensure_tables(store)
    clean = str(body or "").strip()
    if not clean or len(clean) > 10000:
        raise ValueError("评论须为 1 到 10,000 个字符。")
    if target_type not in COMMENT_TARGETS:
        raise ValueError("未知的评论目标类型。")
    parent_id = str(parent_id or "").strip()[:200]
    me = current_identity()
    comment_id, now = "comment_" + uuid4().hex, _now()
    with store.connection() as conn:
        if parent_id:
            parent = conn.execute(
                "SELECT id, target_type, target_id FROM research_comments WHERE id=? AND project_id=?",
                (parent_id, project_id),
            ).fetchone()
            if not parent:
                raise ValueError("父评论不存在或不属于本项目。")
            # Keep thread on the same target unless caller omitted target (inherit).
            if not target_id:
                target_type = parent["target_type"]
                target_id = parent["target_id"]
            elif parent["target_type"] != target_type or parent["target_id"] != target_id:
                raise ValueError("回复必须针对与父评论相同的目标。")
        conn.execute("INSERT INTO research_comments(id,project_id,target_type,target_id,parent_id,author_id,author_name,body,resolved,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,0,?,?)",
                     (comment_id, project_id, target_type, target_id[:200], parent_id, me["id"], me["username"], clean, now, now))
        row = conn.execute("SELECT * FROM research_comments WHERE id=?", (comment_id,)).fetchone()
    return _comment(row)


def update_comment(store: ResearchStore, project_id: str, comment_id: str, *, resolved: bool | None = None,
                   body: str | None = None, can_moderate: bool = False) -> dict[str, Any]:
    ensure_tables(store)
    me = current_identity()
    with store.connection() as conn:
        row = conn.execute("SELECT * FROM research_comments WHERE id=? AND project_id=?", (comment_id, project_id)).fetchone()
        if not row:
            raise KeyError("未找到该评论。")
        if body is not None:
            if row["author_id"] != me["id"]:
                raise PermissionError("仅作者可编辑评论。")
            clean = body.strip()
            if not clean or len(clean) > 10000:
                raise ValueError("评论须为 1 到 10,000 个字符。")
            conn.execute("UPDATE research_comments SET body=?,updated_at=? WHERE id=?", (clean, _now(), comment_id))
        if resolved is not None:
            if row["author_id"] != me["id"] and not can_moderate:
                raise PermissionError("仅作者或编辑者可标记评论已解决。")
            conn.execute("UPDATE research_comments SET resolved=?,updated_at=? WHERE id=?", (int(resolved), _now(), comment_id))
        row = conn.execute("SELECT * FROM research_comments WHERE id=?", (comment_id,)).fetchone()
    return _comment(row)


def delete_comment(store: ResearchStore, project_id: str, comment_id: str, *, can_moderate: bool = False) -> bool:
    ensure_tables(store)
    me = current_identity()
    with store.connection() as conn:
        row = conn.execute("SELECT author_id FROM research_comments WHERE id=? AND project_id=?", (comment_id, project_id)).fetchone()
        if not row:
            return False
        if row["author_id"] != me["id"] and not can_moderate:
            raise PermissionError("仅作者或可管理成员可删除评论。")
        # Reparent direct replies to the grandparent (NULL for top-level) so
        # they don't become orphaned threads pointing at a deleted comment.
        parent = conn.execute("SELECT parent_id FROM research_comments WHERE id=?", (comment_id,)).fetchone()
        grandparent = parent["parent_id"] if parent else None
        conn.execute("UPDATE research_comments SET parent_id=? WHERE parent_id=? AND project_id=?",
                     (grandparent, comment_id, project_id))
        conn.execute("DELETE FROM research_comments WHERE id=?", (comment_id,))
    return True


def _assignment(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "stepId": row["step_id"], "assigneeId": row["assignee_id"], "assignee": row["assignee_name"],
            "note": row["note"], "status": row["status"], "createdBy": row["created_by"], "createdAt": row["created_at"],
            "updatedAt": row["updated_at"]}


def list_assignments(store: ResearchStore, project_id: str) -> list[dict[str, Any]]:
    ensure_tables(store)
    with store.connection() as conn:
        rows = conn.execute("SELECT * FROM research_assignments WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
    return [_assignment(row) for row in rows]


def assign_step(store: ResearchStore, project_id: str, step_id: str, assignee_id: str, assignee_name: str, note: str = "") -> dict[str, Any]:
    ensure_tables(store)
    project = store.get_project(project_id) or {}
    plan = project.get("plan") or {}
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except (TypeError, ValueError, json.JSONDecodeError):
            plan = {}
    if not isinstance(plan, dict):
        plan = {}
    step_ids = {str(step.get("id")) for step in (plan.get("steps") or []) if isinstance(step, dict)}
    if step_id not in step_ids:
        raise KeyError("未找到该计划步骤。")
    me = current_identity()
    assignment_id, now = "assignment_" + uuid4().hex, _now()
    # Serialize the DELETE-then-INSERT pair: two concurrent assigns of the
    # same step could otherwise interleave and leave two open records.
    with _LOCK, store.connection() as conn:
        conn.execute("DELETE FROM research_assignments WHERE project_id=? AND step_id=? AND status='open'", (project_id, step_id))
        conn.execute("INSERT INTO research_assignments(id,project_id,step_id,assignee_id,assignee_name,note,status,created_by,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,?,'open',?,?,?)",
                     (assignment_id, project_id, step_id, assignee_id, assignee_name[:120], note.strip()[:2000], me["username"], now, now))
        row = conn.execute("SELECT * FROM research_assignments WHERE id=?", (assignment_id,)).fetchone()
    return _assignment(row)


def update_assignment(store: ResearchStore, project_id: str, assignment_id: str, status: str, *,
                      requester_id: str | None = None, can_moderate: bool = False) -> dict[str, Any]:
    ensure_tables(store)
    if status not in {"open", "done"}:
        raise ValueError("状态须为 open（进行中）或 done（已完成）。")
    if requester_id is None:
        requester_id = str(current_identity().get("id") or "")
    with store.connection() as conn:
        row = conn.execute("SELECT * FROM research_assignments WHERE id=? AND project_id=?",
                           (assignment_id, project_id)).fetchone()
        if not row:
            raise KeyError("未找到该任务分配。")
        # Only the assignee or a moderator may flip the status; viewers cannot.
        if not can_moderate and row["assignee_id"] != requester_id:
            raise PermissionError("只有任务负责人或管理员可以更新任务状态。")
        conn.execute("UPDATE research_assignments SET status=?,updated_at=? WHERE id=? AND project_id=?",
                     (status, _now(), assignment_id, project_id))
        row = conn.execute("SELECT * FROM research_assignments WHERE id=?", (assignment_id,)).fetchone()
    return _assignment(row)


def members(project_id: str) -> dict[str, Any]:
    binding = current_binding()
    me = current_identity()
    if binding and binding.project_id == project_id:
        owner = {"id": binding.owner_id, "username": binding.owner_username}
    else:
        owner = {"id": me["id"], "username": me["username"]}
    return {"owner": owner, "myRole": current_role(project_id),
            "members": [{"id": item["memberId"], "username": item.get("memberUsername", ""), "role": item.get("role", "viewer"),
                         "since": item.get("createdAt", "")} for item in shares_for_project(project_id)
                        if item.get("ownerId") == owner["id"]]}
