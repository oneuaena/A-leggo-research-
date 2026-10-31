"""Read-only, idempotent import of Math Research Agent SQLite workspaces."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any
from urllib.parse import quote

from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_store import ResearchStore, _id


MAX_IMPORT_BYTES = 512 * 1024 * 1024
_SECRET_WORDS = ("secret", "api_key", "token", "password", "credential", "authorization", "private_key", "bearer")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_only(path: Path) -> sqlite3.Connection:
    encoded = quote(path.resolve().as_posix(), safe="/:\\")
    conn = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _snapshot(source: Path, target: Path) -> None:
    source_conn = _read_only(source)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
        target_conn.commit()
    finally:
        target_conn.close()
        source_conn.close()


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _secret_setting(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").casefold()).strip("_")
    return any(word in normalized for word in _SECRET_WORDS)


def _text(value: Any, limit: int = 20_000) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return str(value).strip()[:limit]


def _payloads(records: list[dict[str, Any]], collection: str) -> list[dict[str, Any]]:
    result = []
    for row in records:
        if row.get("collection") != collection:
            continue
        payload = _decode(row.get("payload"))
        if isinstance(payload, dict):
            result.append(payload)
    return result


def _mapped_plan(project: dict[str, Any], project_records: list[dict[str, Any]], branch_names: dict[str, str]) -> dict[str, Any]:
    specs = _payloads(project_records, "specifications")
    spec = specs[-1] if specs else {}
    steps = _payloads(project_records, "researchSteps")
    steps.sort(key=lambda item: (int(item.get("iteration") or 0), str(item.get("createdAt") or "")))
    steps = steps[-40:]
    converted = []
    for index, old in enumerate(steps):
        stage = _text(old.get("stage") or old.get("role") or "Research", 80)
        title = _text(old.get("action") or f"Review imported {stage.lower()} record", 240)
        description = "\n".join(part for part in (
            _text(old.get("goal"), 3000), _text(old.get("rationaleSummary"), 3000),
            "Imported historical stage: " + stage,
        ) if part)
        old_branch = str(old.get("branchId") or "")
        assigned = str(old.get("role") or "explorer")
        if assigned not in {"research-planner", "explorer", "experimental-mathematician", "lemma-generator", "proof-builder", "skeptic", "independent-verifier", "research-synthesizer"}:
            assigned = "explorer"
        converted.append({
            "id": "imported_step_" + str(index + 1), "title": title, "description": description[:8000],
            "status": "pending", "order": index, "priority": max(1, min(5, int(old.get("iteration") or 3))),
            "enabled": True, "branch": branch_names.get(old_branch, "main"), "dependencies": [],
            "assignedAgent": assigned, "requiredTools": [], "expectedOutput": _text(old.get("outputs"), 2000),
            "verificationMethod": "Imported output must be reviewed and re-run under the new shared tool and proof gates.",
            "humanNotes": "", "agentNotes": "Historical MRA stage preserved in the import ledger; this draft step awaits researcher approval.",
        })
    if not converted:
        converted.append({
            "id": "imported_step_1", "title": "Review imported research state",
            "description": "Inspect imported plans, evidence, experiments, sources, and proof attempts before starting a new run.",
            "status": "pending", "order": 0, "priority": 3, "enabled": True, "branch": "main",
            "dependencies": [], "assignedAgent": "research-planner", "requiredTools": [],
            "expectedOutput": "A researcher-reviewed route for continuing the imported project.",
            "verificationMethod": "Researcher reviews and approves the new plan.", "humanNotes": "", "agentNotes": "",
        })
    branch_items = _payloads(project_records, "branches")
    branches = [{"id": "mra_branch_" + str(index + 1), "title": branch_names.get(str(item.get("id") or ""), _text(item.get("title") or "Imported branch", 120)), "status": "open", "priority": max(1, min(5, int(item.get("priority") or 3)))} for index, item in enumerate(branch_items)]
    lemmas = [_text(item.get("statement") or item.get("title"), 1000) for item in _payloads(project_records, "nodes") if item.get("kind") == "LEMMA"][:100]
    formalization = {
        "quantifiers": spec.get("quantifiers", []), "variables": spec.get("variables", []),
        "domains": spec.get("domains", {}), "assumptions": spec.get("assumptions", []),
        "target": spec.get("target", {}), "equivalentForms": spec.get("equivalentForms", []),
        "symbolicExpressions": spec.get("symbolicExpressions", []),
        "uncertainty": spec.get("uncertainty", []),
    }
    assumptions = project.get("assumptions_text") or ""
    definitions = "\n".join(part for part in (project.get("variables") or "", project.get("domain_text") or "") if part)
    return {
        "objective": _text(project.get("goal") or project.get("name") or "Review imported research", 8000),
        "problemFormalization": _text(formalization, 12000) if spec else _text("\n".join(part for part in (project.get("background"), project.get("known_results"), project.get("constraints_text")) if part), 12000),
        "assumptions": [part for part in str(assumptions).splitlines() if part.strip()][:100],
        "definitions": [part for part in definitions.splitlines() if part.strip()][:100],
        "keyQuestions": [_text(project.get("question"), 4000)], "proposedLemmas": [item for item in lemmas if item],
        "researchBranches": [item["title"] for item in branches], "suggestedExperiments": [],
        "proofStrategy": "Review imported proof attempts and preserve their unverified status until independently checked.",
        "counterexampleStrategy": "Review imported finite experiments and design a new bounded falsification route.",
        "toolsRequired": [], "verificationStrategy": "Use the shared bounded math worker and pinned Lean kernel gate; imported statuses are not certificates.",
        "expectedOutputs": ["Researcher-approved plan", "Traceable evidence and experiment records", "Explicit uncertainty boundary"],
        "riskUncertainty": "This is an imported draft. Historical MRA record statuses are preserved as metadata and are not re-certified by A-leggo.",
        "steps": converted, "branches": branches,
    }


def _restore_structured_project(
    conn: sqlite3.Connection, source_hash: str, project: dict[str, Any], target_id: str,
    project_records: list[dict[str, Any]], activities: list[dict[str, Any]],
    document_chunks: list[dict[str, Any]], now: str,
) -> dict[str, int]:
    """Restore first-class project, branch, graph, source, memory, and tool rows.

    The complete old payload remains in mra_legacy_records as well. Historical
    verification labels are retained as metadata; imported work is not promoted
    to a new Lean certificate.
    """
    source_project_id = str(project.get("id") or "")
    title = _text(project.get("name") or "Imported research", 100)
    question = _text(project.get("question") or title, 12_000)
    created = str(project.get("created_at") or now)
    updated = str(project.get("updated_at") or created)
    conn.execute(
        "INSERT INTO research_projects(id,title,question,status,model_id,current_version,approved_version,last_error,created_at,updated_at) VALUES(?,?,?,'AWAITING_APPROVAL','research.meridian',1,NULL,'Imported plan requires review and approval.',?,?)",
        (target_id, title, question, created, updated),
    )
    main_id = _id("branch")
    conn.execute(
        "INSERT INTO research_branches(id,project_id,name,created_at,updated_at) VALUES(?,?,'main',?,?)",
        (main_id, target_id, created, updated),
    )
    branch_items = _payloads(project_records, "branches")
    branch_ids: dict[str, str] = {"": main_id}
    branch_names: dict[str, str] = {"": "main"}
    reserved_names = {"main"}
    for item in branch_items:
        old_id = str(item.get("id") or "")
        name = re.sub(r"\s+", " ", _text(item.get("title") or item.get("name") or "Imported branch", 120)).strip()
        name = name or "Imported branch"
        candidate, suffix = name, 2
        while candidate.casefold() in {value.casefold() for value in reserved_names}:
            candidate = f"{name[:110]} ({suffix})"
            suffix += 1
        reserved_names.add(candidate)
        branch_id = "mra_branch_" + hashlib.sha256((source_hash + source_project_id + old_id).encode("utf-8")).hexdigest()[:24]
        branch_ids[old_id] = branch_id
        branch_names[old_id] = candidate
        status = "CLOSED" if str(item.get("status") or "").casefold() in {"closed", "complete", "completed", "abandoned"} else "OPEN"
        conn.execute(
            "INSERT INTO research_branches(id,project_id,name,parent_id,base_plan_version,status,created_at,updated_at) VALUES(?,?,?,?,1,?,?,?)",
            (branch_id, target_id, candidate, main_id, status, str(item.get("createdAt") or created), str(item.get("updatedAt") or updated)),
        )

    mapped_plan = _mapped_plan(project, project_records, branch_names)
    conn.execute(
        "INSERT INTO research_plan_versions(id,project_id,version,plan_json,changed_by,change_reason,change_summary,approved,created_at) VALUES(?,?,1,?,'mra-import','Imported from Math Research Agent; researcher review required','Imported history preserved; execution steps reset for review.',0,?)",
        (_id("plan"), target_id, json.dumps(mapped_plan, ensure_ascii=False, default=str), now),
    )

    node_ids: dict[str, str] = {}
    node_kinds = {
        "CONJECTURE": "conjecture", "SUBGOAL": "lemma", "LEMMA": "lemma", "CLAIM": "theorem",
        "IDENTITY": "proposition", "PARAMETRIC_FAMILY": "conjecture", "PROOF_ATTEMPT": "proof_attempt",
        "PROOF_GAP": "evidence", "DEAD_END": "evidence", "QUESTION": "question", "DEFINITION": "definition",
        "EXPERIMENT": "experiment", "COUNTEREXAMPLE": "counterexample", "EVIDENCE": "evidence",
    }
    node_records = _payloads(project_records, "nodes")
    for item in node_records:
        old_id = str(item.get("id") or _id("node"))
        node_id = "mra_node_" + hashlib.sha256((source_hash + source_project_id + old_id).encode("utf-8")).hexdigest()[:28]
        node_ids[old_id] = node_id
        old_kind = str(item.get("kind") or "EVIDENCE").upper()
        node_type = node_kinds.get(old_kind, "evidence")
        title_text = _text(item.get("title") or item.get("statement") or old_kind, 500)
        content = "\n\n".join(part for part in (
            _text(item.get("statement"), 30_000), _text(item.get("content"), 30_000),
            _text(item.get("summary"), 12_000),
        ) if part)[:100_000]
        original_status = str(item.get("status") or "UNKNOWN")
        node_status = "refuted" if old_kind == "DEAD_END" or original_status.casefold() in {"refuted", "dead_end", "failed"} else "unverified"
        branch_id = branch_ids.get(str(item.get("branchId") or ""), main_id)
        metadata = {
            "importSource": "Math Research Agent 2.1.0", "mraRecordId": old_id, "mraKind": old_kind,
            "mraStatus": original_status, "mraVerificationStatus": item.get("verificationStatus"),
            "mraDependencies": item.get("dependencies", []), "mraSourceIds": item.get("sources", []),
            "mraToolIds": item.get("tools", []), "mraEvidenceIds": item.get("evidenceIds", []),
            "historicalVerificationNotRechecked": True,
        }
        conn.execute(
            "INSERT INTO research_graph_nodes(id,project_id,branch_id,node_type,title,content,status,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (node_id, target_id, branch_id, node_type, title_text, content, node_status, json.dumps(metadata, ensure_ascii=False),
             str(item.get("createdAt") or created), str(item.get("updatedAt") or updated)),
        )

    edge_kinds = {
        "SUPPORTS": "SUPPORTS", "REFUTES": "REFUTES", "BLOCKED_BY": "DEPENDS_ON", "IMPLIES": "SUPPORTS",
        "DERIVED_FROM": "DERIVED_FROM", "USES": "USES", "VERIFIED_BY": "VERIFIES", "VERIFIES": "VERIFIES",
        "CITES": "CITES", "CONTRADICTS": "CONTRADICTS", "FOUND_BY": "DERIVED_FROM", "FAILED_BECAUSE": "REFUTES",
        "GENERALIZES": "SUPPORTS", "SPECIAL_CASE_OF": "DEPENDS_ON", "SPECIALIZES": "DEPENDS_ON",
    }
    mapped_edges = 0
    for item in _payloads(project_records, "graphEdges"):
        old_source, old_target = str(item.get("sourceId") or ""), str(item.get("targetId") or "")
        source_node, target_node = node_ids.get(old_source), node_ids.get(old_target)
        if not source_node or not target_node or source_node == target_node:
            continue
        old_kind = str(item.get("kind") or "SUPPORTS").upper()
        conn.execute(
            "INSERT OR IGNORE INTO research_graph_edges(id,project_id,branch_id,source_id,target_id,edge_type,evidence,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (_id("edge"), target_id, None, source_node, target_node, edge_kinds.get(old_kind, "SUPPORTS"),
             "Imported MRA relationship; historical verification was not rechecked. " + _text(item.get("label") or old_kind, 12000), json.dumps({"importSource": "Math Research Agent 2.1.0", "mraKind": old_kind, "historicalVerificationNotRechecked": True}),
             str(item.get("createdAt") or now)),
        )
        mapped_edges += 1

    source_ids: dict[str, str] = {}
    source_payloads = _payloads(project_records, "sources")
    def ensure_source(old_source_id: str, title_text: str, document_type: str = "mra-document", source_payload: dict[str, Any] | None = None) -> str:
        existing = source_ids.get(old_source_id)
        if existing:
            return existing
        data = source_payload or {}
        new_id = "mra_source_" + hashlib.sha256((source_hash + source_project_id + old_source_id).encode("utf-8")).hexdigest()[:24]
        raw_path = str(data.get("path") or "")
        url = raw_path if raw_path.startswith(("https://", "http://")) else ""
        abstract = "\n\n".join(part for part in (
            _text(data.get("abstract"), 12_000), _text(data.get("excerpt"), 6000), _text(data.get("notes"), 2000),
        ) if part)[:20_000]
        source_ids[old_source_id] = new_id
        conn.execute(
            "INSERT INTO research_sources(id,project_id,branch_id,source_type,title,url,authors,abstract,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (new_id, target_id, main_id, "mra_" + re.sub(r"[^a-z0-9_-]", "_", str(data.get("type") or document_type).casefold())[:40],
             _text(data.get("title") or title_text or "Imported MRA source", 500), url, _text(data.get("authors"), 4000), abstract,
             _text(data.get("contentHash"), 128), str(data.get("createdAt") or now)),
        )
        return new_id

    for item in source_payloads:
        old_id = str(item.get("id") or _id("source"))
        ensure_source(old_id, _text(item.get("title") or "Imported source", 500), str(item.get("documentType") or item.get("type") or "mra-source"), item)
    mapped_chunks = 0
    for item in document_chunks:
        if str(item.get("project_id") or "") != source_project_id:
            continue
        old_source_id = str(item.get("source_id") or "")
        filename = _text(item.get("filename") or "Imported document", 500)
        source_id = ensure_source(old_source_id, filename, str(item.get("document_type") or "mra-document"))
        content = str(item.get("text_content") or "")
        part_limit = 7600
        split = [content[index:index + part_limit] for index in range(0, len(content), part_limit)] or [""]
        original_index = max(0, int(item.get("chunk_index") or 0))
        for part_index, part in enumerate(split):
            conn.execute(
                "INSERT OR IGNORE INTO research_document_chunks(id,project_id,source_id,chunk_index,page_number,section,content,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (_id("chunk"), target_id, source_id, original_index * 100 + part_index, item.get("page_number"),
                 _text(item.get("section_text") or filename, 500), part, str(item.get("created_at") or now)),
            )
            mapped_chunks += 1

    for item in _payloads(project_records, "memories"):
        content = _text(item.get("content") or item.get("title"), 8000)
        if not content:
            continue
        memory_id = "mra_memory_" + hashlib.sha256((source_hash + source_project_id + str(item.get("id") or content)).encode("utf-8")).hexdigest()[:24]
        full_text = _text(item.get("title"), 500)
        if full_text and full_text != content:
            content = full_text + "\n\n" + content
        conn.execute(
            "INSERT INTO research_memories(id,project_id,content,confirmed,created_at,updated_at) VALUES(?,?,?,0,?,?)",
            (memory_id, target_id, content, str(item.get("createdAt") or now), str(item.get("createdAt") or now)),
        )

    evidence_records = _payloads(project_records, "evidence")
    for item in evidence_records:
        branch_id = branch_ids.get(str(item.get("branchId") or ""), main_id)
        content = _text(item.get("content"), 50_000)
        status_text = _text(item.get("verificationStatus") or "unverified", 120)
        conn.execute(
            "INSERT INTO research_artifacts(id,project_id,branch_id,plan_version,step_id,agent_role,title,content,status,created_at) VALUES(?,?,?,1,'','mra-evidence',?,?,?,?)",
            (_id("artifact"), target_id, branch_id, _text(item.get("title") or "Imported MRA evidence", 500),
             "Historical MRA verification label: " + status_text + "\n\n" + content,
             "imported-unverified", str(item.get("createdAt") or now)),
        )

    experiment_records = _payloads(project_records, "experiments")
    for item in experiment_records:
        branch_id = branch_ids.get(str(item.get("branchId") or ""), main_id)
        input_value = _decode(item.get("input"))
        tool = _text(item.get("tool") or "imported-mra-experiment", 120)
        output_text = "\n".join(part for part in (
            "Historical status: " + _text(item.get("verificationStatus") or item.get("status") or "unknown", 120),
            _text(item.get("output"), 12_000), _text(item.get("stdout"), 6000), _text(item.get("stderr"), 2000),
        ) if part)
        conn.execute(
            "INSERT INTO research_tool_runs(id,project_id,branch_id,step_id,tool_name,input_json,output_text,status,elapsed_ms,created_at) VALUES(?,?,?,'',?,?,?,?,?,?)",
            (_id("tool"), target_id, branch_id, tool, json.dumps(input_value, ensure_ascii=False, default=str),
             output_text[:20_000], "IMPORTED_UNVERIFIED", max(0, int(item.get("durationMs") or 0)), str(item.get("createdAt") or now)),
        )

    proof_records = _payloads(project_records, "proofs")
    for item in proof_records:
        old_id = str(item.get("id") or _id("proof"))
        theorem = _text(item.get("theorem") or item.get("conclusion") or "Imported proof attempt", 500)
        body = json.dumps({key: item.get(key) for key in ("theorem", "assumptions", "definitions", "steps", "edgeCases", "conclusion", "status", "verificationStatus", "independentlyReviewed")}, ensure_ascii=False, default=str)[:99_000]
        node_id = "mra_proof_" + hashlib.sha256((source_hash + source_project_id + old_id).encode("utf-8")).hexdigest()[:24]
        conn.execute(
            "INSERT INTO research_graph_nodes(id,project_id,branch_id,node_type,title,content,status,metadata_json,created_at,updated_at) VALUES(?,?,?,'proof_attempt',?,?, 'unverified',?,?,?)",
            (node_id, target_id, branch_ids.get(str(item.get("branchId") or ""), main_id), "Imported proof attempt: " + theorem[:470], body,
             json.dumps({"importSource": "Math Research Agent 2.1.0", "mraRecordId": old_id,
                         "mraStatus": item.get("status"), "mraVerificationStatus": item.get("verificationStatus"),
                         "historicalVerificationNotRechecked": True}, ensure_ascii=False),
             str(item.get("createdAt") or now), str(item.get("updatedAt") or now)),
        )

    for item in activities:
        if str(item.get("project_id") or "") != source_project_id:
            continue
        payload = _decode(item.get("payload"))
        details = payload if isinstance(payload, dict) else {"historicalPayload": payload}
        ResearchStore._intervention(conn, target_id, None, "mra_imported_activity", "mra-import", details,
                                    str(item.get("created_at") or now))

    return {
        "branches": len(branch_items), "graphNodes": len(node_records) + len(proof_records),
        "graphEdges": mapped_edges, "sources": len(source_ids), "documentChunks": mapped_chunks,
        "memories": len(_payloads(project_records, "memories")), "evidenceArtifacts": len(evidence_records),
        "experiments": len(experiment_records), "proofAttempts": len(proof_records),
    }


def import_mra_database(store: ResearchStore, source: Path, original_name: str) -> dict[str, Any]:
    """Import projects, research records, logs, settings, and canonical document chunks.

    The source is opened read-only and never modified. Provider credential-like
    settings are intentionally excluded so the new product continues to use
    its single shared backend credential.
    """
    source = source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError("Choose a SQLite database smaller than 512 MB.")
    with tempfile.TemporaryDirectory(prefix="aleggo-mra-import-") as temp_dir:
        snapshot = Path(temp_dir) / "research.sqlite3"
        _snapshot(source, snapshot)
        source_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        domain = ResearchDomainStore(store)
        previous = domain.import_summary(source_hash)
        if previous:
            return {**previous, "alreadyImported": True}

        src = _read_only(snapshot)
        try:
            tables = {str(row[0]) for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"projects", "records", "activities"}
            if not required.issubset(tables):
                raise ValueError("This SQLite file is not a Math Research Agent database.")
            if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("The source Math Research Agent database failed SQLite integrity checks.")

            projects = [dict(row) for row in src.execute("SELECT * FROM projects")]
            records = [dict(row) for row in src.execute("SELECT * FROM records")]
            activities = [dict(row) for row in src.execute("SELECT * FROM activities")]
            document_chunks = [dict(row) for row in src.execute("SELECT * FROM document_chunks")] if "document_chunks" in tables else []
            settings = [dict(row) for row in src.execute("SELECT * FROM settings")] if "settings" in tables else []
            jobs = [dict(row) for row in src.execute("SELECT * FROM research_jobs")] if "research_jobs" in tables else []
        finally:
            src.close()

        now = _now()
        project_ids: dict[str, str] = {}
        for item in projects:
            source_id = str(item.get("id") or "")
            suffix = hashlib.sha256((source_hash + "\0" + source_id).encode("utf-8")).hexdigest()[:20]
            project_ids[source_id] = "mra_" + suffix

        summary = {
            "sourceHash": source_hash,
            "projects": len(projects),
            "records": len(records),
            "activities": len(activities),
            "documentChunks": len(document_chunks),
            "settingsImported": sum(
                1 for item in settings if not _secret_setting(item.get("key"))
            ),
            "credentialsExcluded": sum(
                1 for item in settings if _secret_setting(item.get("key"))
            ),
            "jobs": len(jobs),
            "canonicalTables": sorted(name for name in tables if not name.startswith("document_chunks_fts")),
            "alreadyImported": False,
        }
        mappings = [(source_id, target_id) for source_id, target_id in project_ids.items()]
        legacy: list[dict[str, Any]] = []
        for row in records:
            payload = _decode(row.get("payload"))
            legacy.append({
                "table": "records", "id": str(row.get("id") or _id("legacy")),
                "targetProjectId": project_ids.get(str(row.get("project_id") or "")),
                "collection": str(row.get("collection") or ""), "payload": payload,
                "createdAt": str(row.get("updated_at") or row.get("created_at") or ""),
            })
        for row in activities:
            legacy.append({
                "table": "activities", "id": str(row.get("id") or _id("legacy")),
                "targetProjectId": project_ids.get(str(row.get("project_id") or "")),
                "collection": "activity", "payload": _decode(row.get("payload")),
                "createdAt": str(row.get("created_at") or ""),
            })
        for row in document_chunks:
            legacy.append({
                "table": "document_chunks", "id": str(row.get("id") or _id("legacy")),
                "targetProjectId": project_ids.get(str(row.get("project_id") or "")),
                "collection": "document_chunk", "payload": row,
                "createdAt": str(row.get("created_at") or ""),
            })
        for row in settings:
            key = str(row.get("key") or "")
            if _secret_setting(key):
                continue
            legacy.append({"table": "settings", "id": key or _id("setting"), "collection": "legacy_setting", "payload": row, "createdAt": ""})
        for row in jobs:
            legacy.append({
                "table": "research_jobs", "id": str(row.get("id") or _id("job")),
                "targetProjectId": project_ids.get(str(row.get("project_id") or "")),
                "collection": "job", "payload": row, "createdAt": str(row.get("updated_at") or ""),
            })
        for row in projects:
            legacy.append({
                "table": "projects", "id": str(row.get("id") or _id("project")),
                "targetProjectId": project_ids.get(str(row.get("id") or "")),
                "collection": "project_metadata", "payload": row,
                "createdAt": str(row.get("updated_at") or row.get("created_at") or ""),
            })

        store.aleggo_store.snapshot_database("before-math-research-agent-import")
        with store.connection() as conn:
            conn.execute(
                "INSERT INTO mra_imports(source_hash,original_name,imported_at,summary_json) VALUES(?,?,?,?)",
                (source_hash, Path(original_name).name[:255], now, json.dumps(summary, ensure_ascii=False)),
            )
            records_by_project: dict[str, list[dict[str, Any]]] = {}
            for record in records:
                records_by_project.setdefault(str(record.get("project_id") or ""), []).append(record)
            mapped_counts = {"mappedBranches": 0, "mappedGraphNodes": 0, "mappedGraphEdges": 0, "mappedSources": 0,
                             "mappedDocumentChunks": 0, "mappedMemories": 0, "mappedEvidenceArtifacts": 0,
                             "mappedExperiments": 0, "mappedProofAttempts": 0}
            for item in projects:
                source_id = str(item.get("id") or "")
                target_id = project_ids[source_id]
                counts = _restore_structured_project(
                    conn, source_hash, item, target_id, records_by_project.get(source_id, []),
                    activities, document_chunks, now,
                )
                for key, value in counts.items():
                    normalized_key = "mappedDocumentChunks" if key == "documentChunks" else "mapped" + key[:1].upper() + key[1:]
                    mapped_counts[normalized_key] += value
                conn.execute(
                    "INSERT INTO mra_project_imports(source_hash,source_project_id,target_project_id) VALUES(?,?,?)",
                    (source_hash, source_id, target_id),
                )
            summary.update(mapped_counts)
            conn.execute("UPDATE mra_imports SET summary_json=? WHERE source_hash=?",
                         (json.dumps(summary, ensure_ascii=False), source_hash))
            conn.executemany(
                "INSERT INTO mra_legacy_records(source_hash,source_table,source_record_id,target_project_id,collection,payload_json,source_created_at) VALUES(?,?,?,?,?,?,?)",
                [
                    (source_hash, item["table"], item["id"], item.get("targetProjectId"), item.get("collection", ""), json.dumps(item["payload"], ensure_ascii=False, default=str), item.get("createdAt", ""))
                    for item in legacy
                ],
            )
        return summary
