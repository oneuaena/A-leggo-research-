"""Portable, checked research packages shared by ZIP export and workspace sync.

The portable snapshot contains durable SQLite records, never a live database,
provider credentials, or machine workspace locations. Restoring always creates
new identities and pauses execution; historical verification remains evidence.
"""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import sqlite3
import stat
from typing import Any
from uuid import uuid4
import zipfile

from aleggo.study.research_latex import build_latex_bibliography, build_latex_paper
from aleggo.study.research_reports import build_markdown_report, build_pdf_report
from aleggo.study.research_store import ResearchStore, _now


FORMAT = "aleggo-research-workspace"
VERSION = 1
MAX_FILES = 20_000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_COMPRESSED_BYTES = 250_000_000
SNAPSHOT = "portable-snapshot.json"
MANIFEST = "MANIFEST.sha256.json"
TEXT_MANIFEST = "MANIFEST.sha256"
REPRO_ZIP = "final/reproducibility-package.zip"

# Explicit table names keep imported snapshots away from settings and Study data.
TABLES = (
    "research_projects", "research_plan_versions", "research_branches",
    "research_artifacts", "research_graph_nodes", "research_graph_edges",
    "research_tool_runs", "research_sources", "research_document_chunks",
    "research_memories", "research_formal_bindings", "research_interventions",
    "research_workbench_state", "research_timeline", "research_decisions",
    "research_recommendations", "research_literature_links",
    "research_progress_attempts", "research_step_checkpoints",
    "research_progress_fingerprints", "research_budget_evaluations",
    "research_intervention_queue", "research_failed_paths",
    "research_literature_searches", "research_literature_analyses",
    "research_input_requests",
    "mra_imports", "mra_project_imports", "mra_legacy_records",
)
_SECRET_KEYS = {
    "apikey", "apisecret", "secret", "password", "passwd", "authorization",
    "accesstoken", "refreshtoken", "bearertoken", "credential", "credentials",
    "privatekey", "clientsecret", "cookie", "cookies", "token",
}
_PATH_KEYS = {
    "workspaceroot", "workspacepath", "filepath", "outputpath", "workingdirectory",
    "cwd", "runtimepath", "sandboxpath", "datadir", "homedir", "originalpath",
}


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _safe_name(value: str) -> str:
    # Record identity, rather than a user title, is the unique filename suffix.
    return re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip(".-")[:100] or "record"


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def _manifest(files: dict[str, bytes]) -> dict[str, bytes]:
    result = dict(files)
    checksums = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    result[MANIFEST] = _json(checksums)
    result[TEXT_MANIFEST] = "".join(f"{digest}  {name}\n" for name, digest in checksums.items()).encode("utf-8")
    return result


def _recorded_generated_files(metadata: Any) -> dict[str, bytes]:
    generated_files = metadata.get("generatedFiles", []) if isinstance(metadata, dict) else []
    if not isinstance(generated_files, list):
        raise ValueError("记录的生成文件列表须为数组。")
    output, names = {}, set()
    for generated in generated_files:
        if not isinstance(generated, dict):
            raise ValueError("生成文件描述无效。")
        name = str(generated.get("relativeName") or "")
        path = PurePosixPath(name)
        if (not name or "\\" in name or "\x00" in name or ":" in name or path.is_absolute()
                or any(part in {".", "..", ""} for part in name.split("/")) or name != path.as_posix()):
            raise ValueError("生成文件相对路径不安全。")
        if name.casefold() in names:
            raise ValueError("生成文件路径重复。")
        names.add(name.casefold())
        if generated.get("encoding") != "base64":
            raise ValueError("生成文件编码不受支持。")
        try:
            content = base64.b64decode(generated["data"], validate=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("生成文件字节无效。") from exc
        if (len(content) > MAX_FILE_BYTES or len(content) != generated.get("sizeBytes")
                or hashlib.sha256(content).hexdigest() != generated.get("sha256")):
            raise ValueError("生成文件校验和或大小无效。")
        output[name] = content
    return output


class _PortableSanitizer:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]):
        self.secrets: set[str] = set()
        self.paths: set[str] = set()
        for records in rows.values():
            for row in records:
                self._collect(row)

    def _collect(self, value: Any, key: str = "") -> None:
        normalized = _key(key)
        if normalized in _SECRET_KEYS and isinstance(value, str) and value:
            self.secrets.add(value)
        if normalized in _PATH_KEYS and isinstance(value, str) and value:
            self.paths.add(value)
        if isinstance(value, dict):
            for field, item in value.items():
                self._collect(item, str(field))
        elif isinstance(value, list):
            for item in value:
                self._collect(item)
        elif isinstance(value, str) and key.endswith("_json"):
            try:
                self._collect(json.loads(value))
            except (ValueError, TypeError):
                pass

    def text(self, value: str) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            if len(secret) >= 4:
                value = value.replace(secret, "[credential omitted]")
        for path in sorted(self.paths, key=len, reverse=True):
            value = value.replace(path, "[local workspace omitted]")
            value = value.replace(path.replace("\\", "\\\\"), "[local workspace omitted]")
            value = value.replace(path.replace("\\", "/"), "[local workspace omitted]")
        # Defensive handling of credentials embedded in URLs or recorded headers.
        value = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1", value)
        value = re.sub(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|password)=)[^&\s]+", r"\1[omitted]", value)
        value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{12,}", r"\1[omitted]", value)
        return value

    def value(self, value: Any, key: str = "") -> Any:
        normalized = _key(key)
        if normalized in _SECRET_KEYS:
            return "[credential omitted]"
        if normalized in _PATH_KEYS:
            return ""
        if isinstance(value, (bytes, bytearray, memoryview)):
            data = bytes(value)
            return {"$binary": "base64", "data": base64.b64encode(data).decode("ascii"), "sha256": hashlib.sha256(data).hexdigest()}
        if isinstance(value, dict):
            return {str(field): self.value(item, str(field)) for field, item in value.items()}
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, str):
            if key.endswith("_json"):
                try:
                    return json.dumps(self.value(json.loads(value)), ensure_ascii=False, sort_keys=True)
                except (ValueError, TypeError):
                    pass
            return self.text(value)
        return value


class ResearchDelivery:
    def __init__(self, store: ResearchStore):
        self.store = store

    def _rows(self, project_id: str) -> tuple[dict[str, list[dict[str, Any]]], str]:
        records: dict[str, list[dict[str, Any]]] = {}
        with self.store.connection() as conn:
            project = conn.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise KeyError("未找到该研究项目。")
            schema_version = conn.execute("SELECT value FROM meta WHERE key='research_schema_version'").fetchone()
            for table in TABLES:
                if table.startswith("mra_"):
                    continue
                if table == "research_projects":
                    rows = [project]
                elif table == "research_memories":
                    rows = conn.execute(f"SELECT * FROM {table} WHERE project_id=? OR project_id IS NULL ORDER BY rowid", (project_id,)).fetchall()
                else:
                    rows = conn.execute(f"SELECT * FROM {table} WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()
                records[table] = [dict(row) for row in rows]
            # Legacy source provenance travels with the imported project as well.
            for table in ("mra_project_imports", "mra_legacy_records"):
                records[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE target_project_id=? ORDER BY rowid", (project_id,))]
            source_hashes = {row["source_hash"] for table in ("mra_project_imports", "mra_legacy_records") for row in records[table]}
            records["mra_imports"] = [dict(row) for digest in sorted(source_hashes) for row in conn.execute("SELECT * FROM mra_imports WHERE source_hash=?", (digest,))]
        return records, str(schema_version[0] if schema_version else "4")

    @staticmethod
    def _decode(value: str | None, fallback: Any = None) -> Any:
        try:
            return json.loads(value or "null")
        except (ValueError, TypeError):
            return fallback

    @staticmethod
    def _bibtex(sources: list[dict[str, Any]]) -> str:
        """LaTeX-safe BibTeX (%, _, &, math) via research_latex."""
        question = str((sources[0].get("title") if sources else "") or "")
        return build_latex_bibliography(sources, question)

    def paper_files(self, project_id: str) -> dict[str, bytes]:
        """The LaTeX draft alone, from the same sanitized records as the package."""
        raw, _schema_version = self._rows(project_id)
        tables = _PortableSanitizer(raw).value(raw)
        project = tables["research_projects"][0]
        plan = next((self._decode(revision["plan_json"], {}) for revision in tables["research_plan_versions"]
                     if revision["version"] == project["current_version"]), {})
        return {
            "main.tex": build_latex_paper(tables, plan if isinstance(plan, dict) else {}).encode("utf-8"),
            "references.bib": build_latex_bibliography(tables["research_sources"], project["question"]).encode("utf-8"),
        }

    def files(self, project_id: str) -> dict[str, bytes]:
        raw, schema_version = self._rows(project_id)
        sanitizer = _PortableSanitizer(raw)
        tables = sanitizer.value(raw)
        snapshot = {"format": FORMAT, "version": VERSION, "schemaVersion": schema_version, "projectId": project_id, "tables": tables}
        project = tables["research_projects"][0]
        files: dict[str, bytes] = {SNAPSHOT: _json(snapshot)}
        warnings: list[str] = []

        def put(name: str, value: Any) -> None:
            files[name] = value if isinstance(value, bytes) else str(value).encode("utf-8")

        for table, rows in tables.items():
            put(f"records/{table}.json", _json(rows))
        put("project.json", _json(project))
        for revision in tables["research_plan_versions"]:
            plan = self._decode(revision["plan_json"], {})
            put(f"plan/revisions/v{revision['version']:03d}.json", _json({**revision, "plan": plan}))
            if revision["version"] == project["current_version"]:
                put("plan/current.json", _json(plan))
        for artifact in tables["research_artifacts"]:
            name = f"{_safe_name(artifact['title'])}-{_safe_name(artifact['id'])}"
            put(f"artifacts/{name}.md", artifact["content"])
            put(f"artifacts/{name}.json", _json(artifact))
            if artifact["step_id"]:
                put(f"steps/{_safe_name(artifact['step_id'])}/{name}.md", artifact["content"])
        for source in tables["research_sources"]:
            name = _safe_name(source["id"])
            metadata = self._decode(source.get("metadata_json"), {})
            blob = source.get("document_blob")
            if blob:
                original_name = str(metadata.get("originalFilename") or metadata.get("filename") or source["title"])
                suffix = PurePosixPath(str(original_name).replace("\\", "/")).suffix.casefold()
                try:
                    raw = base64.b64decode(blob["data"], validate=False)
                except (ValueError, TypeError, KeyError) as exc:
                    warnings.append(f"literature/originals/{name}: 原文二进制缺失或损坏（{exc}）")
                    raw = b""
                if raw:
                    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
                        suffix = ".pdf" if raw.startswith(b"%PDF-") else ".bin"
                    put(f"literature/originals/{name}{suffix}", raw)
            put(f"literature/notes/{name}.json", _json(metadata.get("structuredNote") or {"notes": source.get("notes", "")}))
            put(f"literature/notes/{name}.md", f"# {source['title']}\n\n{source.get('notes', '')}\n\n" + json.dumps(metadata.get("structuredNote") or {}, ensure_ascii=False, indent=2))
        put("literature/citations.bib", self._bibtex(tables["research_sources"]))
        for target, table in (("sources", "research_sources"), ("chunks", "research_document_chunks"), ("links", "research_literature_links"), ("searches", "research_literature_searches"), ("analyses", "research_literature_analyses")):
            put(f"literature/{target}.json", _json(tables[table]))
        put("evidence/graph.json", _json({"nodes": tables["research_graph_nodes"], "edges": tables["research_graph_edges"]}))
        put("branches/branches.json", _json(tables["research_branches"]))
        put("branches/failed-paths.json", _json(tables["research_failed_paths"]))
        put("memory/memories.json", _json(tables["research_memories"]))
        for run in tables["research_tool_runs"]:
            folder = f"experiments/{_safe_name(run['id'])}"
            input_data = self._decode(run["input_json"], {})
            put(folder + "/input.json", _json(input_data))
            put(folder + "/result.txt", run["output_text"])
            put(folder + "/record.json", _json(run))
            metadata = self._decode(run["metadata_json"], {})
            environment = {key: value for key, value in metadata.items() if key != "generatedFiles"} if isinstance(metadata, dict) else metadata
            put(folder + "/environment.json", _json(environment))
            try:
                generated = _recorded_generated_files(metadata)
            except ValueError as exc:
                warnings.append(f"experiments/{_safe_name(run['id'])}: 跳过损坏的生成文件记录（{exc}）")
                generated = {}
            for name, content in generated.items():
                put(folder + "/files/" + name, content)
            code = input_data.get("code") or input_data.get("pythonCode") or input_data.get("script") if isinstance(input_data, dict) else None
            if isinstance(code, str) and code:
                suffix = ".lean" if "lean" in run["tool_name"].casefold() else ".py"
                put(folder + "/recorded-code" + suffix, code)
            result = self._decode(run["output_text"])
            if result is not None:
                put(folder + "/result.json", _json(result))
        for binding in tables["research_formal_bindings"]:
            folder = f"proofs/{_safe_name(binding['id'])}"
            put(folder + "/binding.json", _json(binding))
            put(folder + "/statement.lean", binding["lean_statement"])
            put(folder + "/proof.lean", binding["proof_source"])
            put(folder + "/checker-output.txt", binding["checker_output"])
        for short, table in (("timeline", "research_timeline"), ("decisions", "research_decisions"), ("interventions", "research_interventions"), ("queued-interventions", "research_intervention_queue"), ("recommendations", "research_recommendations"), ("checkpoints", "research_step_checkpoints"), ("input-requests", "research_input_requests"), ("budgets", "research_budget_evaluations"), ("progress", "research_progress_attempts"), ("fingerprints", "research_progress_fingerprints")):
            put(f"logs/{short}.json", _json(tables[table]))
        markdown = sanitizer.text(build_markdown_report(self.store, project_id))
        put("final/FINAL-RESEARCH-PACKAGE.md", markdown)
        put("final/FINAL-RESEARCH-PACKAGE.pdf", build_pdf_report(markdown, project["title"]))
        put("final/bibliography.bib", self._bibtex(tables["research_sources"]))
        current_plan = next((self._decode(revision["plan_json"], {}) for revision in tables["research_plan_versions"]
                             if revision["version"] == project["current_version"]), {})
        # Editable LaTeX article skeleton; claims keep their recorded evidence status.
        put("paper/main.tex", build_latex_paper(tables, current_plan if isinstance(current_plan, dict) else {}))
        put("paper/references.bib", build_latex_bibliography(tables["research_sources"], project["question"]))
        if warnings:
            put("final/WARNINGS.md", "# 导出警告\n\n" + "\n".join(f"- {item}" for item in warnings) + "\n")
        put("final/reproducibility.json", _json({"projectId": project_id, "planVersion": project["current_version"], "recordedToolRuns": len(tables["research_tool_runs"]), "recordedFormalBindings": len(tables["research_formal_bindings"]), "warnings": warnings, "note": ("已尽量收录记录中的输入、代码、输出、种子、工作环境与形式化证明；缺失项不会编造。导入的核验仅作历史证据。模型输出不保证可复现。" + ((" 导出时跳过了 " + str(len(warnings)) + " 项损坏记录，详见 final/WARNINGS.md。") if warnings else ""))}))
        put("README.md", f"# {project['title']}\n\n{project['question']}\n\n这是 A-leggo 研究工作区快照（A-leggo research workspace snapshot）。 `portable-snapshot.json` preserves all durable project records and original document blobs. `records/` exposes complete tables; `experiments/` and `proofs/` contain actual recorded inputs, outputs and proof source. `final/` contains the same canonical Markdown/PDF report, bibliography and reproducibility archive. `paper/main.tex` with `paper/references.bib` is an editable LaTeX paper draft assembled from the recorded claims, computations and failed routes; every claim keeps its recorded evidence status.\n\nRestore using Research → Restore workspace ZIP. A fresh project is created, execution is paused, and no model or tool is automatically started. Provider credentials and local workspace paths are excluded. Existing projects remain unchanged. Reimporting identical package bytes returns the same restored project.\n\nVerify every listed file with `MANIFEST.sha256` or `MANIFEST.sha256.json`.\n")
        # The inner archive has its own complete manifest and excludes only itself.
        put(REPRO_ZIP, _zip(_manifest(files)))
        # No file is added or rewritten after the outer manifests are calculated.
        return _manifest(files)

    def export_package(self, project_id: str) -> bytes:
        data = _zip(self.files(project_id))
        # Never hand out an archive that the same workspace importer refuses.
        self._checked_files(data)
        return data

    @staticmethod
    def _checked_files(data: bytes, *, _inner: bool = False) -> dict[str, bytes]:
        if len(data) > min(MAX_TOTAL_BYTES, MAX_COMPRESSED_BYTES):
            raise ValueError("研究包压缩后体积过大。")
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_FILES:
                    raise ValueError("研究包文件数量无效。")
                seen: set[str] = set()
                total = 0
                for info in infos:
                    name = info.orig_filename
                    path = PurePosixPath(name)
                    if (not name or "\\" in name or "\x00" in name or ":" in name or path.is_absolute()
                            or any(part in {".", "..", ""} for part in name.split("/"))
                            or name != path.as_posix() or info.is_dir()):
                        raise ValueError("研究包包含不安全路径。")
                    if name.casefold() in seen:
                        raise ValueError("研究包包含重复路径。")
                    seen.add(name.casefold())
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                        raise ValueError("研究包不能包含链接或特殊文件。")
                    total += info.file_size
                    if info.file_size < 0 or info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                        raise ValueError("研究包解压后体积超限。")
                    if info.flag_bits & 1:
                        raise ValueError("不支持加密的研究包。")
                files = {info.filename: archive.read(info) for info in infos}
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError) as exc:
            raise ValueError("研究包 ZIP 无效或校验失败。") from exc
        if SNAPSHOT not in files or MANIFEST not in files or TEXT_MANIFEST not in files:
            raise ValueError("研究包缺少可移植快照或清单。")
        try:
            expected = json.loads(files[MANIFEST])
        except (ValueError, UnicodeError) as exc:
            raise ValueError("研究包清单无效。") from exc
        payload_names = set(files) - {MANIFEST, TEXT_MANIFEST}
        if not isinstance(expected, dict) or set(expected) != payload_names:
            raise ValueError("研究包清单与载荷文件不完全对应。")
        for name in sorted(payload_names):
            if not isinstance(expected[name], str) or expected[name] != hashlib.sha256(files[name]).hexdigest():
                raise ValueError("研究包校验和不匹配：" + name)
        text_manifest = "".join(f"{expected[name]}  {name}\n" for name in sorted(expected)).encode("utf-8")
        if files[TEXT_MANIFEST] != text_manifest:
            raise ValueError("研究包文本清单与 JSON 清单不一致。")
        if REPRO_ZIP in files:
            if _inner:
                raise ValueError("不支持嵌套的可复现归档。")
            inner = ResearchDelivery._checked_files(files[REPRO_ZIP], _inner=True)
            if set(inner) != set(files) - {REPRO_ZIP}:
                raise ValueError("可复现归档与研究工作区不匹配。")
            if any(inner[name] != files[name] for name in inner if name not in {MANIFEST, TEXT_MANIFEST}):
                raise ValueError("可复现归档与研究工作区不一致。")
        return files

    @staticmethod
    def _remap(value: Any, identities: dict[str, str]) -> Any:
        if isinstance(value, dict):
            return {identities.get(field, field): ResearchDelivery._remap(item, identities) for field, item in value.items()}
        if isinstance(value, list):
            return [ResearchDelivery._remap(item, identities) for item in value]
        if isinstance(value, str):
            if value in identities:
                return identities[value]
            for prefix in ("recommendation:", "artifact:", "source:", "node:", "step:"):
                if value.startswith(prefix) and value[len(prefix):] in identities:
                    return prefix + identities[value[len(prefix):]]
        return value

    def restore_package(self, data: bytes) -> dict[str, Any]:
        files = self._checked_files(data)
        source_hash = hashlib.sha256(data).hexdigest()
        try:
            snapshot = json.loads(files[SNAPSHOT])
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("可移植研究快照无效。") from exc
        if not isinstance(snapshot, dict) or snapshot.get("format") != FORMAT or snapshot.get("version") != VERSION:
            raise ValueError("不支持的可移植研究快照格式或版本。")
        tables = snapshot.get("tables")
        old_project_id = snapshot.get("projectId")
        # v4.4.0 packages did not yet contain durable researcher input requests.
        if isinstance(tables, dict) and set(tables) == set(TABLES) - {"research_input_requests"}:
            tables = {**tables, "research_input_requests": []}
        if (not isinstance(tables, dict) or set(tables) != set(TABLES)
                or not isinstance(old_project_id, str) or not isinstance(tables.get("research_projects"), list)
                or len(tables["research_projects"]) != 1 or not isinstance(tables["research_projects"][0], dict)
                or tables["research_projects"][0].get("id") != old_project_id):
            raise ValueError("可移植快照表或项目标识无效。")
        new_project_id = "research_" + uuid4().hex
        identities: dict[str, str] = {old_project_id: new_project_id}
        now = _now()
        try:
            with self.store.connection() as conn:
                previous = conn.execute("SELECT project_id FROM research_workspace_imports WHERE source_hash=?", (source_hash,)).fetchone()
                if previous:
                    project = conn.execute("SELECT * FROM research_projects WHERE id=?", (previous[0],)).fetchone()
                    if project:
                        return self.store._project(conn, project)
                conn.execute("PRAGMA defer_foreign_keys=ON")
                schemas: dict[str, dict[str, str]] = {}
                for table in TABLES:
                    schemas[table] = {str(row[1]): str(row[2]) for row in conn.execute(f"PRAGMA table_info({table})")}
                    records = tables[table]
                    if not isinstance(records, list):
                        raise ValueError("可移植研究表须为数组。")
                    for row in records:
                        if not isinstance(row, dict) or set(row) != set(schemas[table]):
                            raise ValueError("可移植快照列与已安装 schema 不匹配：" + table)
                        if table == "research_tool_runs":
                            generated = _recorded_generated_files(self._decode(row["metadata_json"], {}))
                            for name, content in generated.items():
                                archive_name = f"experiments/{_safe_name(row['id'])}/files/{name}"
                                if files.get(archive_name) != content:
                                    raise ValueError("记录的生成文件与研究包不一致。")
                        owner = row.get("project_id", row.get("target_project_id"))
                        if owner not in {None, old_project_id}:
                            raise ValueError("可移植快照包含其他项目的记录。")
                        if "id" in row:
                            identity = row["id"]
                            if not isinstance(identity, str) or not identity:
                                raise ValueError("可移植记录标识无效。")
                            identities.setdefault(identity, table.removeprefix("research_").rstrip("s") + "_" + uuid4().hex)
                        step_id = row.get("step_id") or row.get("current_step_id")
                        if isinstance(step_id, str) and step_id:
                            identities.setdefault(step_id, "step_" + uuid4().hex)
                        if table == "research_plan_versions":
                            plan = json.loads(row["plan_json"])
                            for step in plan.get("steps", []):
                                if isinstance(step.get("id"), str):
                                    identities.setdefault(step["id"], "step_" + uuid4().hex)
                        if "source_hash" in row:
                            identities.setdefault(row["source_hash"], hashlib.sha256((source_hash + ":" + row["source_hash"]).encode()).hexdigest())
                # A valid FK to an existing *different* project must not turn a
                # hostile snapshot into an attachment to that project's data.
                # Check source identities before any remapped rows are inserted.
                foreign_keys = {table: list(conn.execute(f"PRAGMA foreign_key_list({table})")) for table in TABLES}
                for table in TABLES:
                    for row in tables[table]:
                        for fk in foreign_keys[table]:
                            target_table, column, target_column = str(fk[2]), str(fk[3]), str(fk[4])
                            value = row[column]
                            if value is None:
                                continue
                            if target_table not in tables:
                                raise ValueError("可移植快照引用了不支持的表。")
                            included = {item[target_column] for item in tables[target_table]}
                            if value not in included:
                                if table == "research_memories" and row["project_id"] is None and column == "source_artifact_id":
                                    # Global memory text is reusable, but an
                                    # artifact from another project is not owned
                                    # by this package. Its original pointer is
                                    # retained in the portable snapshot only.
                                    continue
                                raise ValueError("可移植快照包含项目外引用。")
                # Sanitize again: a modified, correctly rehashed external snapshot
                # still cannot inject a local workspace or a provider credential.
                sanitizer = _PortableSanitizer(tables)
                for table in TABLES:
                    for original in tables[table]:
                        row = sanitizer.value(original)
                        for column, value in row.items():
                            if isinstance(value, dict):
                                if "BLOB" not in schemas[table][column].upper() or value.get("$binary") != "base64" or set(value) != {"$binary", "data", "sha256"}:
                                    raise ValueError("可移植二进制描述无效。")
                                binary = base64.b64decode(value["data"], validate=True)
                                if hashlib.sha256(binary).hexdigest() != value["sha256"]:
                                    raise ValueError("可移植二进制校验和不匹配。")
                                row[column] = binary
                            elif column.endswith("_json") and isinstance(value, str):
                                decoded = self._remap(json.loads(value), identities)
                                if table == "research_plan_versions" and column == "plan_json":
                                    for step in decoded.get("steps", []):
                                        if str(step.get("status", "")).casefold() in {"running", "verifying"}:
                                            step["status"] = "pending"
                                row[column] = json.dumps(decoded, ensure_ascii=False)
                            elif column == "id" or column.endswith("_id") or column == "source_hash":
                                row[column] = self._remap(value, identities)
                        if table == "research_memories" and row["project_id"] is None:
                            row["project_id"] = new_project_id
                            included_artifacts = {item["id"] for item in tables["research_artifacts"]}
                            if original["source_artifact_id"] not in included_artifacts:
                                row["source_artifact_id"] = None
                        if table == "research_projects":
                            row["status"] = "PAUSED"
                            row["approved_version"] = None
                            row["updated_at"] = now
                        if table in {"research_graph_nodes", "research_artifacts"}:
                            metadata = json.loads(row.get("metadata_json") or "{}")
                            historical = {"status": original.get("status"),
                                          "checkStatus": metadata.get("checkStatus"),
                                          "evidenceClass": metadata.get("evidenceClass"),
                                          "evidenceClassification": metadata.get("evidenceClassification")}
                            metadata["importedHistoricalEvidence"] = True
                            metadata["historicalVerification"] = historical
                            formal = (str(row.get("status", "")).casefold() in {"verified", "kernel_certified", "formally_verified"}
                                      or str(metadata.get("checkStatus", "")).upper() == "KERNEL_CERTIFIED"
                                      or any(str(metadata.get(key, "")).upper() == "FORMALLY_VERIFIED"
                                             for key in ("evidenceClass", "evidenceClassification")))
                            if formal:
                                row["status"] = "unverified" if table == "research_graph_nodes" else "historical"
                                metadata.update(verificationSuspended=True, evidenceClass="CONJECTURAL",
                                                evidenceClassification="CONJECTURAL")
                                if "checkStatus" in metadata:
                                    metadata["checkStatus"] = "IMPORTED_HISTORICAL"
                            if table == "research_graph_nodes":
                                historical_bindings = [{"status": binding["status"], "mappingAuthority": binding["mapping_authority"],
                                                        "proofHash": binding["proof_source_hash"]}
                                                       for binding in tables["research_formal_bindings"]
                                                       if original["id"] in {binding["claim_node_id"], binding["proof_node_id"]}]
                                if historical_bindings:
                                    metadata["historicalFormalBindings"] = historical_bindings
                            row["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
                        if table == "research_formal_bindings":
                            row["status"] = "IMPORTED_REQUIRES_VERIFICATION"
                            row["mapping_authority"] = "UNCONFIRMED"
                            row["updated_at"] = now
                        if table == "research_workbench_state":
                            row.update(workspace_root="", workspace_status="UNCONFIGURED", run_started_at="",
                                       current_agent="", current_action="", current_step_id="", status_reason="工作区已恢复；继续运行需要研究者明确操作。", updated_at=now)
                            for field, empty in (("current_tool", ""), ("active_agents_json", "[]"), ("phase", "")):
                                if field in row:
                                    row[field] = empty
                        if table == "research_intervention_queue" and row["status"] in {"RUNNING", "IN_PROGRESS", "VERIFYING", "QUEUED"} and row.get("intervention_type") == "discuss":
                            row["status"] = "FAILED"  # a discussion reply never resumes on restore
                        elif table == "research_intervention_queue" and row["status"] in {"RUNNING", "IN_PROGRESS", "VERIFYING"}:
                            row["status"] = "QUEUED"
                        if table == "research_step_checkpoints" and row["status"] in {"RUNNING", "IN_PROGRESS", "VERIFYING"}:
                            row["status"] = "INTERRUPTED"
                        if table == "research_step_checkpoints":
                            payload = json.loads(row.get("payload_json") or "{}")
                            cached = payload.get("toolResult")
                            if isinstance(cached, list) and len(cached) >= 3 and isinstance(cached[2], str):
                                try:
                                    record = json.loads(cached[2])
                                except ValueError:
                                    record = {}
                                if isinstance(record, dict) and (record.get("status") == "KERNEL_CERTIFIED" or record.get("verificationLevel") == "FORMALLY_VERIFIED"):
                                    record.update(status="IMPORTED_HISTORICAL", verificationLevel="UNKNOWN", mappingAuthority="IMPORTED_HISTORICAL")
                                    cached[2] = json.dumps(record, ensure_ascii=False)
                            payload["importedHistoricalEvidence"] = True
                            row["payload_json"] = json.dumps(payload, ensure_ascii=False)
                        columns = list(row)
                        conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(row[column] for column in columns))
                if conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("可移植快照包含损坏的引用。")
                conn.execute("INSERT INTO research_workspace_imports(source_hash,project_id,created_at) VALUES(?,?,?)", (source_hash, new_project_id, now))
                restored = conn.execute("SELECT * FROM research_projects WHERE id=?", (new_project_id,)).fetchone()
                return self.store._project(conn, restored)
        except (sqlite3.Error, TypeError, KeyError, AttributeError, json.JSONDecodeError, UnicodeError, RecursionError) as exc:
            raise ValueError("无法恢复可移植研究快照；现有数据已保留。") from exc
