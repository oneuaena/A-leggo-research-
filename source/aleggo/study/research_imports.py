"""Import mathematical material: LaTeX sources, arXiv bundles, formula PDFs, photos.

Ordinary research material is rarely a clean text file:

* a LaTeX paper is a ``.tex`` file, or an arXiv source bundle (``.zip`` /
  ``.tar.gz``) whose main file pulls chapters in with ``\\input``;
* PDFs of papers carry their formulas as glyph soup that plain text extraction
  destroys;
* a lot of mathematics still lives on paper and whiteboards.

This module turns each of those into an indexed research source:

* :func:`import_latex` finds the main file, inlines ``\\input``/``\\include``,
  strips comments, splits by section, extracts theorem-like environments
  (with their labels and proofs) and BibTeX references.  Extracted statements
  and references are *offered*, not silently added: :func:`promote_import`
  adds the ones the researcher picks to the claim graph (``unverified``) and
  to the source library;
* :func:`import_pdf` uses PyMuPDF text extraction per page and, when a vision
  model is configured, transcribes formula-dense or scanned pages to
  Markdown + LaTeX;
* :func:`transcribe_images` turns photos of handwritten notes into Markdown +
  LaTeX for the researcher to review before :func:`save_transcription`
  indexes it.

Archives are read in memory with size, count and compression-ratio limits;
nothing is extracted to disk.
"""

from __future__ import annotations

import asyncio
import os
from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import tarfile
from typing import Any
import zipfile

from aleggo.study.research_documents import MAX_DOCUMENT_BYTES, _split_text
from aleggo.study.research_domain import ResearchDomainStore

MAX_ARCHIVE_MEMBERS = 3000
MAX_ARCHIVE_UNCOMPRESSED = 80 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_TEX_CHARS = 3_000_000
MAX_INPUT_DEPTH = 12
# Defaults; overridable via ALEGGO_MAX_VISION_PAGES / ALEGGO_MAX_IMAGES (lecture packs, long papers).
DEFAULT_MAX_VISION_PAGES = 40
HARD_MAX_VISION_PAGES = 120
DEFAULT_MAX_IMAGES = 16
HARD_MAX_IMAGES = 24
MAX_VISION_PAGES = DEFAULT_MAX_VISION_PAGES  # backwards-compatible alias
MAX_IMAGES = DEFAULT_MAX_IMAGES
MAX_IMAGE_BYTES = 15 * 1024 * 1024
VISION_BATCH_SIZE = 4  # pages transcribed concurrently within one batch
VISION_BATCH_PAUSE_S = 0.15
IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                  ".heic": "image/heic", ".heif": "image/heif", ".bmp": "image/bmp", ".gif": "image/gif"}
_TEXT_MEMBERS = {".tex", ".bib", ".bbl", ".sty", ".cls", ".ltx"}

THEOREM_KINDS = {
    "theorem": "theorem", "thm": "theorem", "lemma": "lemma", "lem": "lemma", "proposition": "proposition",
    "prop": "proposition", "corollary": "proposition", "cor": "proposition", "definition": "definition",
    "defn": "definition", "def": "definition", "conjecture": "conjecture", "conj": "conjecture", "claim": "lemma",
    "fact": "lemma", "observation": "lemma", "assumption": "definition", "hypothesis": "definition",
    "notation": "definition", "example": "evidence", "remark": "evidence", "exercise": "question",
    "question": "question", "problem": "question", "solution": "evidence",
    "定理": "theorem", "引理": "lemma", "命题": "proposition", "推论": "proposition", "定义": "definition",
    "猜想": "conjecture", "例子": "evidence", "注": "evidence", "备注": "evidence", "假设": "definition",
    "证明": "evidence", "问题": "question", "习题": "question", "例题": "question", "解": "evidence",
    "解答": "evidence", "事实": "lemma", "观察": "lemma", "记号": "definition", "约定": "definition",
    "公理": "definition", "性质": "lemma", "结论": "proposition", "断言": "lemma",
}

VISION_TRANSCRIBE_PROMPT = (
    "Transcribe this page of mathematics exactly into Markdown with LaTeX. Use $...$ for inline and $$...$$ for "
    "display mathematics, keep the original language, numbering, theorem/lemma labels and line structure. Do not "
    "solve, correct, complete or summarise anything; if a symbol is illegible write [illegible] instead of guessing. "
    "Return only the transcription."
)

HANDWRITING_TRANSCRIBE_PROMPT = (
    "Transcribe this photograph of handwritten or whiteboard mathematics into Markdown with LaTeX. "
    "Preserve the writer's language (Chinese or English), numbering, arrows and case distinctions. "
    "Use $...$ / $$...$$ for maths. Do not solve or complete the argument. Mark unclear symbols as [illegible]. "
    "If several boards/pages appear in one image, separate them with a horizontal rule. Return only the transcription."
)


def vision_page_limit(requested: int | None = None) -> int:
    """Effective per-import vision page cap (env + request, hard-clamped)."""
    try:
        configured = int(os.environ.get("ALEGGO_MAX_VISION_PAGES", str(DEFAULT_MAX_VISION_PAGES)))
    except ValueError:
        configured = DEFAULT_MAX_VISION_PAGES
    base = max(1, min(HARD_MAX_VISION_PAGES, configured))
    if requested is None:
        return base
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return base
    return max(1, min(HARD_MAX_VISION_PAGES, value))


def image_limit(requested: int | None = None) -> int:
    try:
        configured = int(os.environ.get("ALEGGO_MAX_IMAGES", str(DEFAULT_MAX_IMAGES)))
    except ValueError:
        configured = DEFAULT_MAX_IMAGES
    base = max(1, min(HARD_MAX_IMAGES, configured))
    if requested is None:
        return base
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return base
    return max(1, min(HARD_MAX_IMAGES, value))



# --------------------------------------------------------------------------- #
# Archives
# --------------------------------------------------------------------------- #

def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _safe_member(name: str) -> str | None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return str(path)



def _read_bounded(handle, limit: int) -> bytes:
    """Read at most ``limit`` bytes; reject streams that keep producing more."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = handle.read(min(65536, max(0, limit - total + 1)))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("压缩包中某个文件超过大小限制。")
        chunks.append(chunk)
    return b"".join(chunks)


def _gzip_decompress_bounded(data: bytes, limit: int) -> bytes:
    """Decompress gzip without materialising an unbounded bomb in memory."""
    import gzip

    with gzip.GzipFile(fileobj=BytesIO(data)) as handle:
        return _read_bounded(handle, limit)


def read_archive(filename: str, data: bytes) -> dict[str, str]:
    """Text members of a .zip / .tar(.gz) bundle, keyed by relative path."""
    lower = filename.casefold()
    members: dict[str, str] = {}
    total = 0

    def accept(name: str, size: int, compressed: int) -> bool:
        nonlocal total
        safe = _safe_member(name)
        if not safe or PurePosixPath(safe).suffix.casefold() not in _TEXT_MEMBERS:
            return False
        if compressed and size / max(1, compressed) > MAX_COMPRESSION_RATIO and size > 1_000_000:
            raise ValueError("该压缩包疑似压缩炸弹，已拒绝。")
        total += size
        if total > MAX_ARCHIVE_UNCOMPRESSED:
            raise ValueError("压缩包内 LaTeX 源合计超过 80 MB。")
        return True

    if lower.endswith(".zip"):
        try:
            archive = zipfile.ZipFile(BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError("ZIP 压缩包无效。") from exc
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("压缩包内文件数量过多。")
        for info in infos:
            if info.is_dir() or not accept(info.filename, info.file_size, info.compress_size):
                continue
            # Prefer the declared size already counted into ``total``; never trust an
            # unbounded ZipFile.read() beyond the remaining uncompressed budget.
            remaining = MAX_ARCHIVE_UNCOMPRESSED - (total - info.file_size)
            with archive.open(info) as handle:
                payload = _read_bounded(handle, min(info.file_size, remaining) if info.file_size else remaining)
            members[_safe_member(info.filename) or info.filename] = _decode(payload)
    elif lower.endswith((".tar.gz", ".tgz", ".tar", ".gz")):
        try:
            archive = tarfile.open(fileobj=BytesIO(data), mode="r:*")
        except tarfile.TarError as exc:
            if lower.endswith(".gz") and not lower.endswith((".tar.gz", ".tgz")):
                import gzip

                try:  # arXiv serves single-file sources as plain .gz
                    return {PurePosixPath(filename[:-3]).name or "main.tex": _decode(
                        _gzip_decompress_bounded(data, MAX_TEX_CHARS * 2))}
                except (OSError, ValueError):
                    pass
            raise ValueError("压缩包无效。") from exc
        with archive:
            count = 0
            for info in archive:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise ValueError("压缩包内文件数量过多。")
                if not info.isfile() or not accept(info.name, info.size, 0):
                    continue
                handle = archive.extractfile(info)
                if handle:
                    remaining = MAX_ARCHIVE_UNCOMPRESSED - (total - info.size)
                    payload = _read_bounded(handle, min(info.size, remaining) if info.size else remaining)
                    members[_safe_member(info.name) or info.name] = _decode(payload)
    else:
        raise ValueError("LaTeX 压缩包请使用 .zip、.tar.gz 或 .tgz。")
    if not members:
        raise ValueError("压缩包中没有找到 .tex 文件。")
    return members


# --------------------------------------------------------------------------- #
# LaTeX parsing
# --------------------------------------------------------------------------- #

def strip_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        match = re.search(r"(?<!\\)%", line)
        lines.append(line[:match.start()] if match else line)
    return "\n".join(lines)


def _braced(text: str, start: int) -> tuple[str, int]:
    """Content of the balanced ``{...}`` group starting at ``text[start] == '{'``."""
    depth, index = 0, start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
        index += 1
    return text[start + 1:], len(text)


def _command_argument(text: str, command: str) -> str:
    match = re.search(r"\\" + command + r"\*?\s*(?:\[[^\]]*\])?\s*\{", text)
    if not match:
        return ""
    value, _ = _braced(text, match.end() - 1)
    return " ".join(value.split())


def choose_main(members: dict[str, str]) -> str:
    candidates = [name for name, text in members.items() if name.casefold().endswith((".tex", ".ltx"))
                  and re.search(r"\\documentclass", strip_comments(text)) and "\\begin{document}" in text]
    if not candidates:
        candidates = [name for name in members if name.casefold().endswith((".tex", ".ltx"))]
    if not candidates:
        raise ValueError("未找到 LaTeX 源文件。")
    preferred = ("main.tex", "paper.tex", "ms.tex", "article.tex", "thesis.tex")
    candidates.sort(key=lambda name: (PurePosixPath(name).name.casefold() not in preferred, name.count("/"), -len(members[name])))
    return candidates[0]


def inline_inputs(members: dict[str, str], main: str) -> tuple[str, list[str]]:
    """Main file with ``\\input``/``\\include``/``\\subfile`` expanded (cycle-safe)."""
    used: list[str] = []
    lookup = {name.casefold(): name for name in members}

    def resolve(reference: str, base: PurePosixPath) -> str | None:
        reference = reference.strip().strip('"')
        for candidate in (base / reference, PurePosixPath(reference)):
            for name in (str(candidate), str(candidate) + ".tex"):
                normal = _safe_member(name)
                if normal and normal.casefold() in lookup:
                    return lookup[normal.casefold()]
        return None

    def expand(name: str, depth: int, stack: tuple[str, ...]) -> str:
        used.append(name)
        text = strip_comments(members[name])
        if depth >= MAX_INPUT_DEPTH:
            return text
        base = PurePosixPath(name).parent  # relative \input from nested files

        def replace(match: re.Match[str]) -> str:
            target = resolve(match.group(2) or match.group(3) or "", base)
            if not target or target in stack:
                return match.group(0)
            return "\n" + expand(target, depth + 1, stack + (target,)) + "\n"

        # Braced form is preferred; bare ``\\input chapter`` is common in older papers.
        return re.sub(r"\\(input|include|subfile)\s*(?:\{([^}]+)\}|([A-Za-z0-9_./-]+))", replace, text)

    return expand(main, 0, (main,))[:MAX_TEX_CHARS], used


def theorem_environments(preamble: str) -> dict[str, str]:
    """``\\newtheorem{thm}{Theorem}`` → ``{"thm": "theorem"}`` plus the standard names."""
    mapping = dict(THEOREM_KINDS)
    # Cover both ``\\newtheorem{thm}{Theorem}`` and ``\\newtheorem{thm}[theorem]{Theorem}``.
    pattern = re.compile(
        r"\\(?:newtheorem|declaretheorem|newtheoremstyle)\*?\s*\{([^}]+)\}"
        r"(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}"
        r"|(?:\\(?:newtheorem|declaretheorem)\*?\s*\{([^}]+)\}\s*\{([^}]+)\}\s*\[[^\]]*\])"
    )
    for match in pattern.finditer(preamble):
        env = (match.group(1) or match.group(3) or "").strip()
        label = (match.group(2) or match.group(4) or "").strip().casefold()
        if not env:
            continue
        for key, kind in THEOREM_KINDS.items():
            if key in label or key == env.casefold():
                mapping[env] = kind
                break
        else:
            mapping.setdefault(env, "lemma")
    return mapping


def extract_statements(body: str, environments: dict[str, str], *, limit: int = 300) -> list[dict[str, Any]]:
    names = "|".join(sorted((re.escape(name) for name in environments), key=len, reverse=True))
    pattern = re.compile(r"\\begin\{(" + names + r")\*?\}(\[[^\]]*\])?(.*?)\\end\{\1\*?\}", re.DOTALL)
    statements: list[dict[str, Any]] = []
    for index, match in enumerate(pattern.finditer(body)):
        env, note, content = match.group(1), (match.group(2) or "").strip("[]"), match.group(3)
        label_match = re.search(r"\\label\{([^}]+)\}", content)
        clean = re.sub(r"\\label\{[^}]+\}", "", content).strip()
        proof = ""
        tail = body[match.end():match.end() + 20000]
        # Skip blank lines / % comments, then take the next proof-like environment
        # (amsthm proof, common aliases, Chinese 证明). Stop if another theorem env starts first.
        proof_match = re.match(
            r"\s*(?:%[^\n]*\n\s*)*\\begin\{(proof|pf|demo|证明)\}(\[[^\]]*\])?(.*?)\\end\{\1\}",
            tail, re.DOTALL,
        )
        next_env = re.match(r"\s*(?:%[^\n]*\n\s*)*\\begin\{(" + names + r")\*?\}", tail)
        if proof_match and (not next_env or proof_match.start() <= next_env.start()):
            proof = proof_match.group(3).strip()
        statements.append({
            "index": index, "environment": env, "kind": environments.get(env, "lemma"),
            "label": label_match.group(1) if label_match else "", "note": note[:300],
            "statement": clean[:8000], "proof": proof[:20000],
        })
        if len(statements) >= limit:
            break
    return statements


def parse_bibtex(text: str, *, limit: int = 500) -> list[dict[str, Any]]:
    entries = []
    for match in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        kind = match.group(1).casefold()
        if kind in {"comment", "preamble", "string"}:
            continue
        start = match.end()
        depth, index = 1, start
        while index < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            index += 1
        body = text[start:index - 1]
        fields: dict[str, str] = {}
        for field in re.finditer(r"(\w+)\s*=\s*", body):
            position = field.end()
            if position < len(body) and body[position] == "{":
                value, _ = _braced(body, position)
            elif position < len(body) and body[position] == '"':
                end = body.find('"', position + 1)
                value = body[position + 1:end if end > 0 else len(body)]
            else:
                value = re.match(r"[^,\n]*", body[position:]).group(0)  # type: ignore[union-attr]
            fields.setdefault(field.group(1).casefold(), " ".join(value.replace("{", "").replace("}", "").split()))
        entries.append({"key": match.group(2), "type": kind, "title": fields.get("title", "")[:500],
                        "authors": fields.get("author", "")[:500], "year": fields.get("year", "")[:10],
                        "doi": fields.get("doi", "")[:200], "arxivId": fields.get("eprint", "")[:40],
                        "url": fields.get("url", "")[:500], "venue": (fields.get("journal") or fields.get("booktitle") or "")[:300]})
        if len(entries) >= limit:
            break
    return entries


def _bbl_entries(text: str) -> list[dict[str, Any]]:
    items = []
    for match in re.finditer(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\})", text, re.DOTALL):
        body = " ".join(re.sub(r"\\newblock|\\emph|\\textit|[{}]", " ", match.group(2)).split())
        items.append({"key": match.group(1), "type": "bibitem", "title": body[:500], "authors": "", "year": "",
                      "doi": "", "arxivId": "", "url": "", "venue": ""})
    return items[:500]


def latex_sections(body: str) -> list[dict[str, Any]]:
    """Chunks split at \\section / \\subsection with readable section names."""
    parts = re.split(r"(\\(?:sub)*section\*?\s*(?:\[[^\]]*\])?\s*\{[^}]*\})", body)
    chunks: list[dict[str, Any]] = []
    section = "Front matter"
    for part in parts:
        heading = re.match(r"\\((?:sub)*section)\*?\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", part)
        if heading:
            section = " ".join(heading.group(2).split())[:240] or section
            continue
        text = re.sub(r"\n{3,}", "\n\n", part).strip()
        if not text:
            continue
        for piece in _split_text(text):
            chunks.append({"page": None, "section": section, "content": piece})
    return chunks


def parse_latex(filename: str, data: bytes) -> dict[str, Any]:
    lower = filename.casefold()
    if lower.endswith((".tex", ".ltx")):
        members = {PurePosixPath(filename).name: _decode(data)}
    elif lower.endswith(".bib"):
        # A lone BibTeX file: surface references without requiring a full .tex project.
        text = _decode(data)
        references = parse_bibtex(text)
        if not references:
            raise ValueError("该 .bib 文件中没有文献条目。")
        return {
            "main": PurePosixPath(filename).name, "files": [PurePosixPath(filename).name],
            "title": PurePosixPath(filename).name, "authors": "", "abstract": "",
            "chunks": [{"page": None, "section": "Bibliography", "content": text[:MAX_TEX_CHARS]}],
            "statements": [], "references": references[:500], "content": text[:MAX_TEX_CHARS],
        }
    else:
        members = read_archive(filename, data)
    main = choose_main(members)
    full, used = inline_inputs(members, main)
    begin = full.find("\\begin{document}")
    preamble, body = (full[:begin], full[begin + len("\\begin{document}"):]) if begin >= 0 else ("", full)
    body = body.split("\\end{document}")[0]
    environments = theorem_environments(preamble + "\n" + body[:20000])
    references: list[dict[str, Any]] = []
    for name, text in members.items():
        if name.casefold().endswith(".bib"):
            references += parse_bibtex(text)
    if not references:
        for name, text in members.items():
            if name.casefold().endswith(".bbl") or "\\begin{thebibliography}" in text:
                references += _bbl_entries(text)
    abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body, re.DOTALL)
    title = _command_argument(preamble + body[:4000], "title")
    chunks = latex_sections(body)
    if not chunks:
        raise ValueError("LaTeX 源在 \\begin{document} 之后没有正文。")
    return {
        "main": main, "files": used, "title": re.sub(r"\\\\|\\thanks\{.*", " ", title).strip()[:500],
        "authors": _command_argument(preamble + body[:4000], "author")[:500],
        "abstract": " ".join(abstract.group(1).split())[:4000] if abstract else "",
        "chunks": chunks, "statements": extract_statements(body, environments), "references": references[:500],
        "content": body,
    }


def import_latex(domain: ResearchDomainStore, project_id: str, filename: str, data: bytes) -> dict[str, Any]:
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("请选择非空且小于 25 MB 的文件。")
    parsed = parse_latex(filename, data)
    digest = sha256(parsed["content"].encode("utf-8")).hexdigest()
    chunks = parsed["chunks"]
    if parsed["abstract"]:
        chunks = [{"page": None, "section": "Abstract", "content": parsed["abstract"]}, *chunks]
    source = domain.save_source(project_id, {
        "type": "tex", "title": (parsed["title"] or PurePosixPath(filename).name)[:500], "authors": parsed["authors"],
        "abstract": parsed["abstract"], "contentHash": digest, "branch": "main", "provider": "local-latex",
        "metadata": {"importKind": "latex", "originalName": PurePosixPath(filename).name[:255], "mainFile": parsed["main"],
                     "files": parsed["files"][:200], "statements": parsed["statements"], "references": parsed["references"]},
    }, chunks, original_file=data)
    return {"source": source, "mainFile": parsed["main"], "files": parsed["files"],
            "statements": [{key: item[key] for key in ("index", "environment", "kind", "label", "note", "statement")}
                           | {"hasProof": bool(item["proof"])} for item in parsed["statements"]],
            "references": parsed["references"]}


def promote_import(domain: ResearchDomainStore, project_id: str, source_id: str, *, statements: list[int],
                   references: list[int], branch: str = "main") -> dict[str, Any]:
    """Add researcher-selected statements to the claim graph and references to the library."""
    source = next((item for item in domain.list_sources(project_id) if item["id"] == source_id), None)
    if not source:
        raise KeyError("Imported source not found.")
    metadata = source.get("metadata") or {}
    stored_statements = {item["index"]: item for item in metadata.get("statements", [])}
    stored_references = metadata.get("references", [])
    nodes, sources = [], []
    for index in statements[:200]:
        try:
            item = stored_statements.get(int(index))
        except (TypeError, ValueError):
            continue
        if not item:
            continue
        label = item.get("note") or item.get("label") or f"{item['environment']} {index + 1}"
        title = f"{item['kind'].title()} ({label}) from {source['title']}"[:300]
        node = domain.create_graph_node(project_id, item["kind"], title, item["statement"], branch_name=branch,
                                        metadata={"importedFrom": source_id, "latexLabel": item.get("label", ""),
                                                  "latexEnvironment": item["environment"], "proof": item.get("proof", "")[:20000],
                                                  "evidence": "imported statement; not verified by A-leggo"})
        nodes.append(node)
    for index in references[:500]:
        try:
            position = int(index)
        except (TypeError, ValueError):
            continue
        if not (0 <= position < len(stored_references)):
            continue
        ref = stored_references[position]
        if not ref.get("title"):
            continue
        saved = domain.save_source(project_id, {
            "type": "literature", "title": ref["title"], "authors": ref.get("authors", ""), "year": ref.get("year", ""),
            "doi": ref.get("doi", ""), "arxivId": ref.get("arxivId", ""), "url": ref.get("url", ""), "branch": branch,
            "provider": "latex-bibliography", "metadata": {"bibKey": ref.get("key", ""), "citedBy": source_id, "venue": ref.get("venue", "")},
        }, [])
        sources.append(saved)
    return {"nodes": nodes, "sources": sources}


# --------------------------------------------------------------------------- #
# PDFs with formulas, photographs of handwriting
# --------------------------------------------------------------------------- #

def _page_needs_vision(text: str) -> bool:
    """Scanned (no text) or formula-dense pages whose text extraction is unreliable."""
    clean = text.strip()
    if len(clean) < 60:
        return True
    letters = sum(char.isalpha() for char in clean)
    math_glyphs = sum(1 for char in clean if 0x2200 <= ord(char) <= 0x22FF or 0x1D400 <= ord(char) <= 0x1D7FF
                      or char in "∑∫∏√≤≥≠±∞∂∇αβγδεζηθλμπρστφχψω")
    replacement = clean.count("\ufffd") + clean.count("�")
    return replacement > 5 or math_glyphs / max(1, len(clean)) > 0.06 or letters / max(1, len(clean)) < 0.35


def _render_pages(data: bytes, pages: list[int], *, zoom: float = 2.0) -> dict[int, bytes]:
    import pymupdf

    # Cap the rendered pixels per page: an oversized page (e.g. a poster-size
    # PDF) at zoom 2.0 could otherwise allocate hundreds of megapixels.
    max_side = 4096
    images: dict[int, bytes] = {}
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for number in pages:
            page = document[number - 1]
            rect = page.rect
            scale = min(zoom, max_side / max(rect.width, rect.height, 1))
            images[number] = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    return images


async def _transcribe(images: list[tuple[str, bytes]], prompt: str = VISION_TRANSCRIBE_PROMPT) -> dict[str, Any]:
    from aleggo.study.model_registry import complete_vision

    return await complete_vision(prompt, images)


async def import_pdf(domain: ResearchDomainStore, project_id: str, filename: str, data: bytes, *,
                     mode: str = "auto", transcribe: Any = None, max_vision_pages: int | None = None,
                     page_offset: int = 0, continue_source_id: str = "") -> dict[str, Any]:
    """Index a PDF; formula-dense or scanned pages are transcribed by the vision model.

    ``mode``: ``text`` (never call the vision model), ``auto`` (only pages whose
    extraction looks unreliable) or ``vision`` (every page, up to the limit).

    Long lecture packs / papers: pass ``max_vision_pages`` (up to
    :data:`HARD_MAX_VISION_PAGES`) and/or ``page_offset`` to transcribe the next
    batch.  ``continue_source_id`` merges new transcriptions into an existing
    PDF source instead of creating a duplicate.
    """
    page_cap = vision_page_limit(max_vision_pages)
    try:
        offset = max(0, int(page_offset))
    except (TypeError, ValueError):
        offset = 0
    loaded_from_blob = False
    # Resume without re-upload: load original PDF bytes from the source blob.
    if (not data) and continue_source_id:
        stored = domain.get_document_blob(project_id, continue_source_id)
        if not stored:
            raise ValueError(
                "未找到已保存的 PDF 原文件，无法跨会话续传。请重新选择同一 PDF 文件后继续转写。"
            )
        data = stored
        loaded_from_blob = True
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("请选择非空且小于 25 MB 的 PDF 文件。")
    bytes_hash = sha256(data).hexdigest()
    # Resume: skip pages already transcribed on a previous continue pass.
    already: set[int] = set()
    prior_source = None
    if continue_source_id:
        prior_source = domain.get_source(project_id, continue_source_id)
        if prior_source is None:
            raise ValueError("未找到要续传的 PDF 来源，请重新导入。")
        # Re-upload path: reject a different file (content-hash / stored blob mismatch).
        if not loaded_from_blob:
            expected = str((prior_source.get("metadata") or {}).get("originalBytesHash") or "").strip()
            if not expected:
                blob = domain.get_document_blob(project_id, continue_source_id)
                if blob:
                    expected = sha256(blob).hexdigest()
            if expected and expected != bytes_hash:
                raise ValueError(
                    "上传的 PDF 与原先导入的文件不一致（内容哈希不匹配）。"
                    "请选择同一文件，或在已保存原文件时使用「无需重选 · 继续转写」。"
                )
        already = {
            int(p) for p in (prior_source.get("metadata") or {}).get("transcribedPages") or []
            if str(p).isdigit() or isinstance(p, int)
        }
    try:
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as document:
            texts = [page.get_text("text") for page in document]
            total_pages = len(document)
    except Exception as exc:
        raise ValueError("无法读取该 PDF，请确认文件未损坏。") from exc
    candidates = [index + 1 for index, text in enumerate(texts) if mode == "vision" or _page_needs_vision(text)]
    remaining_candidates = [p for p in candidates if p not in already]
    if offset:
        remaining_candidates = [p for p in remaining_candidates if p > offset]
    wanted = [] if mode == "text" else remaining_candidates[:page_cap]
    transcribed: dict[int, str] = {}
    warnings: list[str] = []
    usage = {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    model = ""
    progress: list[dict[str, Any]] = []
    if wanted:
        transcribe = transcribe or _transcribe
        # Batch render + transcribe so long papers do not spike memory with every page at once.
        for batch_start in range(0, len(wanted), VISION_BATCH_SIZE):
            batch = wanted[batch_start:batch_start + VISION_BATCH_SIZE]
            images = _render_pages(data, batch)
            semaphore = asyncio.Semaphore(min(3, len(batch)))

            async def one(number: int) -> None:
                nonlocal model
                async with semaphore:
                    try:
                        result = await transcribe([("image/png", images[number])])
                    except Exception as exc:
                        warnings.append(f"Page {number}: {type(exc).__name__}: {str(exc)[:300]}")
                        progress.append({"page": number, "status": "failed", "error": str(exc)[:200]})
                        return
                    text = str(result.get("text") or "").strip()
                    model = str(result.get("model") or model)
                    for key in usage:
                        usage[key] += int((result.get("usage") or {}).get(key) or 0)
                    if text:
                        transcribed[number] = text
                        progress.append({"page": number, "status": "ok", "chars": len(text)})
                    else:
                        progress.append({"page": number, "status": "empty"})

            await asyncio.gather(*(one(number) for number in batch))
            if batch_start + VISION_BATCH_SIZE < len(wanted):
                await asyncio.sleep(VISION_BATCH_PAUSE_S)
    skipped = len(remaining_candidates) - len(wanted) if mode != "text" else 0
    if skipped > 0:
        next_offset = wanted[-1] if wanted else offset
        warnings.append(
            f"还有 {skipped} 页公式密集或为扫描件未转写（本批上限 {page_cap} 页）。"
            f"可用「继续转写」并设置 pageOffset={next_offset}，或提高 ALEGGO_MAX_VISION_PAGES。"
        )
    # Merge prior transcriptions when continuing (pageTexts metadata + DB chunks).
    if prior_source:
        model = model or str((prior_source.get("metadata") or {}).get("visionModel") or "")
    chunks = []
    prior_by_page: dict[int, str] = {}
    if prior_source and continue_source_id:
        for page, body in ((prior_source.get("metadata") or {}).get("pageTexts") or {}).items():
            try:
                prior_by_page[int(page)] = str(body)
            except (TypeError, ValueError):
                continue
        try:
            with domain.store.connection() as conn:
                rows = conn.execute(
                    "SELECT page_number, content FROM research_document_chunks "
                    "WHERE project_id=? AND source_id=? ORDER BY chunk_index",
                    (project_id, continue_source_id),
                ).fetchall()
            for row in rows:
                page = row["page_number"] if not isinstance(row, tuple) else row[0]
                content = row["content"] if not isinstance(row, tuple) else row[1]
                if page is None:
                    continue
                try:
                    page_n = int(page)
                except (TypeError, ValueError):
                    continue
                if page_n in transcribed:
                    continue
                prior_by_page[page_n] = (prior_by_page.get(page_n, "") + "\n\n" + str(content or "")).strip()
        except Exception:
            pass

    all_transcribed_pages = sorted(set(already) | set(transcribed))
    for number, text_layer in enumerate(texts, 1):
        content = transcribed.get(number) or prior_by_page.get(number) or re.sub(r"[ \t]+", " ", text_layer).strip()
        if not content:
            continue
        label = f"Page {number}" + (" · transcribed" if number in all_transcribed_pages or number in transcribed else "")
        for part_index, part in enumerate(parts := _split_text(content)):
            chunks.append({"page": number, "section": label + (f" · part {part_index + 1}" if len(parts) > 1 else ""), "content": part})
    if not chunks:
        raise ValueError("未能提取到文本。请配置视觉模型以转写扫描页，或改用「视觉」模式。")
    full = "\n\n".join(chunk["content"] for chunk in chunks)
    page_texts = {str(n): transcribed[n][:20000] for n in transcribed}
    if prior_source:
        for key, value in ((prior_source.get("metadata") or {}).get("pageTexts") or {}).items():
            page_texts.setdefault(str(key), value)
    prior_bytes_hash = str((prior_source.get("metadata") or {}).get("originalBytesHash") or "") if prior_source else ""
    meta = {
        "importKind": "pdf", "pages": total_pages, "transcribedPages": all_transcribed_pages,
        "visionModel": model, "warnings": warnings[:40], "usage": usage,
        "visionPageCap": page_cap, "pageOffset": offset, "progress": progress[:200],
        "pageTexts": page_texts,
        "moreVisionPagesRemaining": skipped,
        "nextPageOffset": (wanted[-1] if wanted and skipped else None),
        "originalBytesHash": prior_bytes_hash or bytes_hash,
        "transcriptionNotice": "视觉转写可能有误差，请对照原件核对公式与符号。"
        if all_transcribed_pages else "",
    }
    source_payload = {
        "type": "pdf", "title": PurePosixPath(filename).name[:500],
        "contentHash": sha256(full.encode()).hexdigest(),
        "branch": "main", "provider": "local-pdf", "metadata": meta,
    }
    if prior_source and continue_source_id:
        source_payload["id"] = continue_source_id
        source_payload["title"] = prior_source.get("title") or source_payload["title"]
    source = domain.save_source(project_id, source_payload, chunks,
                                original_file=None if continue_source_id else data)
    still_need = [page for page in candidates if page not in set(all_transcribed_pages)]
    return {
        "source": source, "pages": total_pages, "transcribedPages": all_transcribed_pages,
        "pagesNeedingVision": still_need, "pagesNeedingVisionInitially": candidates,
        "warnings": warnings, "usage": usage, "visionModel": model,
        "visionPageCap": page_cap, "moreVisionPagesRemaining": skipped,
        "nextPageOffset": meta["nextPageOffset"], "progress": progress,
        "continued": bool(prior_source and continue_source_id),
    }



def _prepare_image(filename: str, data: bytes) -> tuple[str, bytes]:
    suffix = PurePosixPath(filename.casefold()).suffix
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("支持的图片格式：PNG、JPEG、WebP、HEIC、BMP、GIF。")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("每张图片须非空且小于 15 MB。")
    try:
        from PIL import Image, ImageOps

        if suffix in {".heic", ".heif"}:
            try:
                import pillow_heif  # type: ignore[import-not-found]

                pillow_heif.register_heif_opener()
            except ImportError as exc:
                raise ValueError("本环境暂无法直接读 HEIC，请先导出为 JPEG 再上传。") from exc
        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((2200, 2200))
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=90)
            return "image/jpeg", buffer.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("无法读取该图片，请换一张或改用 JPEG/PNG。") from exc


async def transcribe_images(files: list[tuple[str, bytes]], *, transcribe: Any = None, hint: str = "",
                              batch_size: int = 2, style: str = "handwriting") -> dict[str, Any]:
    """Photos of handwritten / whiteboard mathematics → Markdown + LaTeX draft (not yet saved).

    Images are prepared (EXIF-rotated, HEIC→JPEG when pillow-heif is available), then
    transcribed in small concurrent batches.  The researcher must review/correct the
    draft before :func:`save_transcription`.
    """
    limit = image_limit()
    if not files or len(files) > limit:
        raise ValueError(f"请选择 1 到 {limit} 张图片（可用 ALEGGO_MAX_IMAGES 提高上限，最多 {HARD_MAX_IMAGES}）。")
    heic_note = ""
    prepared: list[tuple[str, bytes]] = []
    names: list[str] = []
    prep_warnings: list[str] = []
    for name, data in files:
        try:
            prepared.append(_prepare_image(name, data))
            names.append(name[:200])
            if PurePosixPath(name.casefold()).suffix in {".heic", ".heif"}:
                heic_note = "HEIC 已转换为 JPEG 再送视觉模型。"
        except ValueError as exc:
            prep_warnings.append(f"{name}: {exc}")
    if not prepared:
        raise ValueError("没有可读图片。 " + "; ".join(prep_warnings[:5]))
    transcribe = transcribe or _transcribe
    base_prompt = HANDWRITING_TRANSCRIBE_PROMPT if style in {"handwriting", "whiteboard", "board"} else VISION_TRANSCRIBE_PROMPT
    if style == "whiteboard":
        base_prompt += " This is a classroom whiteboard; keep multi-column layouts top-to-bottom, left-to-right."
    prompt = base_prompt + (" Context from the researcher: " + hint.strip()[:800] if hint.strip() else "")
    pages: list[dict[str, Any]] = []
    usage = {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    model = ""
    batch = max(1, min(4, int(batch_size or 2)))
    for start_i in range(0, len(prepared), batch):
        chunk = list(enumerate(prepared[start_i:start_i + batch], start_i))
        semaphore = asyncio.Semaphore(batch)

        async def one(index: int, image: tuple[str, bytes]) -> dict[str, Any]:
            nonlocal model
            async with semaphore:
                try:
                    result = await transcribe([image], prompt)
                except Exception as exc:
                    return {"index": index, "name": names[index], "text": "", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                model = str(result.get("model") or model)
                for key in usage:
                    usage[key] += int((result.get("usage") or {}).get(key) or 0)
                return {"index": index, "name": names[index], "text": str(result.get("text") or "").strip(), "error": ""}

        results = await asyncio.gather(*(one(i, img) for i, img in chunk))
        pages.extend(sorted(results, key=lambda item: item["index"]))
        if start_i + batch < len(prepared):
            await asyncio.sleep(VISION_BATCH_PAUSE_S)
    combined = "\n\n---\n\n".join(page["text"] for page in pages if page.get("text"))
    failed = [page for page in pages if page.get("error") or not page.get("text")]
    notice = "请对照原图检查公式；转写可能有误，保存前可直接在下方修改。"
    if heic_note:
        notice = heic_note + " " + notice
    if failed:
        notice += f" 有 {len(failed)} 张未成功转写，可重试或改用 JPEG/PNG。"
    if prep_warnings:
        notice += " " + "; ".join(prep_warnings[:3])
    return {
        "pages": pages, "transcription": combined, "model": model, "usage": usage,
        "notice": notice, "imageCap": limit, "style": style,
        "failedCount": len(failed), "prepWarnings": prep_warnings[:10],
        "reviewChecklist": [
            "核对定理编号与符号是否与原图一致",
            "把 [illegible] 补全或标成待查",
            "确认多页/多栏顺序正确后再保存",
        ],
    }



def save_transcription(domain: ResearchDomainStore, project_id: str, title: str, content: str, *,
                       model: str = "", originals: list[str] | None = None,
                       reviewed: bool = False) -> dict[str, Any]:
    clean = str(content or "").strip()
    if not clean or len(clean) > 1_000_000:
        raise ValueError("转写结果须为 1 到 1,000,000 个字符。")
    chunks = [{"page": None, "section": "Handwritten notes", "content": part} for part in _split_text(clean)]
    return domain.save_source(project_id, {
        "type": "handwritten", "title": (title.strip() or "Handwritten notes")[:500],
        "contentHash": sha256(clean.encode()).hexdigest(), "branch": "main", "provider": "vision-transcription",
        "metadata": {"importKind": "handwriting", "visionModel": model[:120], "originalImages": (originals or [])[:MAX_IMAGES],
                     # Only True when the researcher actually proofread the draft
                     # (the UI shows an editable preview before saving).
                     "reviewedByResearcher": bool(reviewed)},
    }, chunks)


def import_kind(filename: str) -> str:
    lower = filename.casefold()
    if lower.endswith((".tex", ".ltx", ".bib", ".zip", ".tar.gz", ".tgz", ".tar", ".gz")):
        return "latex"
    if lower.endswith(".pdf"):
        return "pdf"
    if PurePosixPath(lower).suffix in IMAGE_SUFFIXES:
        return "image"
    return "document"



# --------------------------------------------------------------------------- #
# Post-import helpers: align to research question, extract claims / examples
# --------------------------------------------------------------------------- #

_CLAIM_HINT = re.compile(
    r"(?im)^(?:\s*(?:theorem|lemma|proposition|corollary|conjecture|claim|definition|假设|定理|引理|命题|推论|猜想|定义)\b"
    r"|.*(?:we (?:prove|show|claim|conjecture)|我们(?:证明|得到|断言|猜想))).{10,400}$"
)
_EXAMPLE_HINT = re.compile(
    r"(?im)^(?:\s*(?:example|exercise|for instance|e\.g\.|例如|例子|例)\b|.*\b(?:n\s*=\s*\d+|compute|calculate|计算|验证)\b).{5,300}$"
)
_LIT_HINT = re.compile(
    r"(?i)\b(?:arxiv\.org/abs/([\w./-]+)|doi:\s*(10\.\S+)|A\d{6}\b|(?:see|cf\.|参见)\s+[A-Z][\w\s,-]{2,40}\(\d{4}\))"
)


def suggest_after_import(domain: ResearchDomainStore, project_id: str, source_id: str, *,
                         question: str = "") -> dict[str, Any]:
    """Heuristic suggestions after a document lands: open claims, computable examples, related lit."""
    source = next((item for item in domain.list_sources(project_id) if item["id"] == source_id), None)
    if not source:
        raise KeyError("Imported source not found.")
    meta = source.get("metadata") or {}
    question = (question or "").strip()
    if not question:
        project = domain.store.get_project(project_id) or {}
        question = str(project.get("question") or project.get("title") or "")
    bodies: list[str] = []
    for item in meta.get("statements") or []:
        bodies.append(str(item.get("statement") or ""))
    try:
        with domain.store.connection() as conn:
            rows = conn.execute(
                "SELECT content FROM research_document_chunks WHERE project_id=? AND source_id=? "
                "ORDER BY chunk_index LIMIT 80",
                (project_id, source_id),
            ).fetchall()
        bodies.extend(str(row["content"] if not isinstance(row, tuple) else row[0] or "") for row in rows)
    except Exception:
        pass
    text_blob = "\n".join(bodies)
    open_claims = []
    for item in meta.get("statements") or []:
        kind = str(item.get("kind") or "")
        if kind in {"theorem", "lemma", "proposition", "conjecture", "question"} or not item.get("proof"):
            open_claims.append({
                "index": item.get("index"), "kind": kind or "claim",
                "label": item.get("label") or item.get("note") or "",
                "statement": str(item.get("statement") or "")[:600],
                "hasProof": bool(item.get("proof")),
            })
    if not open_claims:
        for match in _CLAIM_HINT.finditer(text_blob):
            open_claims.append({"index": None, "kind": "claim", "label": "", "statement": match.group(0).strip()[:600], "hasProof": False})
            if len(open_claims) >= 12:
                break
    examples = []
    for match in _EXAMPLE_HINT.finditer(text_blob):
        examples.append(match.group(0).strip()[:400])
        if len(examples) >= 10:
            break
    literature = []
    for item in meta.get("references") or []:
        if item.get("title"):
            literature.append({"title": item["title"], "doi": item.get("doi", ""), "arxivId": item.get("arxivId", ""),
                               "authors": item.get("authors", ""), "year": item.get("year", "")})
    for match in _LIT_HINT.finditer(text_blob):
        arxiv = match.group(1) or ""
        doi = match.group(2) or ""
        literature.append({"title": match.group(0)[:200], "doi": doi, "arxivId": arxiv, "authors": "", "year": ""})
    # Dedup literature by title
    seen = set()
    deduped = []
    for item in literature:
        key = (item.get("doi") or item.get("arxivId") or item.get("title") or "").casefold()
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    alignment = []
    q_tokens = {tok for tok in re.findall("[A-Za-z]{3,}|[㐀-鿿]{1,2}", question.casefold())}
    if q_tokens:
        for claim in open_claims[:20]:
            body = claim["statement"].casefold()
            overlap = sum(1 for tok in q_tokens if tok in body)
            if overlap:
                alignment.append({**claim, "overlap": overlap})
        alignment.sort(key=lambda item: -item["overlap"])
    return {
        "sourceId": source_id, "title": source.get("title", ""),
        "question": question[:500],
        "openClaims": open_claims[:20],
        "alignedClaims": alignment[:12],
        "computableExamples": examples[:10],
        "relatedLiterature": deduped[:20],
        "notice": "以上为启发式抽取，供教师核对后加入证据图或文献库；不是自动认定的结论。",
    }


def align_attachment_to_project(domain: ResearchDomainStore, project_id: str, filename: str, data: bytes, *,
                                question: str = "") -> dict[str, Any]:
    """Drag-into-chat / attach helper: import by kind, then return alignment suggestions."""
    kind = import_kind(filename)
    if kind == "latex":
        result = import_latex(domain, project_id, filename, data)
        source_id = result["source"]["id"]
        suggestions = suggest_after_import(domain, project_id, source_id, question=question)
        return {"kind": kind, "import": result, "suggestions": suggestions}
    if kind == "pdf":
        raise ValueError("PDF 附件请使用异步 PDF 导入接口。")
    if kind == "image":
        raise ValueError("图片附件请先走「手写转写」入口。")
    # Generic text / markdown fallback
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("请选择非空且小于 25 MB 的文件。")
    text = _decode(data)[:MAX_TEX_CHARS]
    chunks = [{"page": None, "section": "Attachment", "content": part} for part in _split_text(text)]
    source = domain.save_source(project_id, {
        "type": "document", "title": PurePosixPath(filename).name[:500],
        "contentHash": sha256(text.encode()).hexdigest(), "branch": "main", "provider": "chat-attachment",
        "metadata": {"importKind": "attachment", "originalName": PurePosixPath(filename).name[:255]},
    }, chunks, original_file=data)
    suggestions = suggest_after_import(domain, project_id, source["id"], question=question)
    return {"kind": "document", "import": {"source": source}, "suggestions": suggestions}


def import_email_attachment(domain: ResearchDomainStore, project_id: str, *, filename: str, data: bytes,
                            message_id: str = "", subject: str = "", from_address: str = "",
                            question: str = "") -> dict[str, Any]:
    """One-click import of a Gmail/mail attachment into the research project.

    The API layer is responsible for fetching bytes from Gmail (or another mail
    connector).  This function only classifies and indexes the payload.
    """
    if not data:
        raise ValueError("邮件附件为空。")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("邮件附件上限为 25 MB。")
    kind = import_kind(filename)
    provenance = {"emailMessageId": message_id[:200], "emailSubject": subject[:300],
                  "emailFrom": from_address[:200], "importKind": "email-attachment"}
    if kind == "latex":
        result = import_latex(domain, project_id, filename, data)
        meta = dict((result["source"].get("metadata") or {}))
        meta.update(provenance)
        domain.save_source(project_id, {"id": result["source"]["id"], "type": result["source"]["type"],
                                        "title": result["source"]["title"], "metadata": meta,
                                        "provider": "email-attachment", "branch": "main",
                                        "contentHash": result["source"].get("contentHash", "")}, None)
        suggestions = suggest_after_import(domain, project_id, result["source"]["id"], question=question)
        return {"kind": kind, "source": result["source"], "statements": result.get("statements", []),
                "references": result.get("references", []), "suggestions": suggestions}
    if kind == "pdf":
        # Text-first indexing here; caller may follow up with vision continue.
        import asyncio as _asyncio
        # Synchronous wrapper path: only text layer when called from sync context is awkward;
        # require async endpoint to call import_pdf.  Here do text-only via mode override helper.
        raise ValueError("PDF 邮件附件请使用专用的邮件 PDF 导入接口。")
    if kind == "image":
        raise ValueError("图片类邮件附件请下载后走「手写转写」入口。")
    text = _decode(data)[:MAX_TEX_CHARS]
    chunks = [{"page": None, "section": subject or "Email attachment", "content": part} for part in _split_text(text)]
    source = domain.save_source(project_id, {
        "type": "document", "title": (subject or PurePosixPath(filename).name)[:500],
        "contentHash": sha256(text.encode()).hexdigest(), "branch": "main", "provider": "email-attachment",
        "metadata": {**provenance, "originalName": PurePosixPath(filename).name[:255]},
    }, chunks, original_file=data)
    suggestions = suggest_after_import(domain, project_id, source["id"], question=question)
    return {"kind": "document", "source": source, "suggestions": suggestions}


async def import_email_pdf(domain: ResearchDomainStore, project_id: str, *, filename: str, data: bytes,
                           message_id: str = "", subject: str = "", from_address: str = "",
                           mode: str = "auto", max_vision_pages: int | None = None,
                           question: str = "", transcribe: Any = None) -> dict[str, Any]:
    """Async email PDF path with vision support and post-import suggestions."""
    result = await import_pdf(domain, project_id, filename or "attachment.pdf", data, mode=mode,
                              transcribe=transcribe, max_vision_pages=max_vision_pages)
    source = result["source"]
    meta = dict(source.get("metadata") or {})
    meta.update({"emailMessageId": message_id[:200], "emailSubject": subject[:300],
                 "emailFrom": from_address[:200], "importKind": "email-attachment"})
    source = domain.save_source(project_id, {
        "id": source["id"], "type": "pdf", "title": (subject or source.get("title") or filename)[:500],
        "contentHash": source.get("contentHash", ""), "branch": "main", "provider": "email-attachment",
        "metadata": meta,
    }, None)
    suggestions = suggest_after_import(domain, project_id, source["id"], question=question)
    return {**result, "source": source, "suggestions": suggestions, "kind": "pdf"}

def summary_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)[:2000]
