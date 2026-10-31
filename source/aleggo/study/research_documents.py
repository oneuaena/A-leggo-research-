"""Local research document import: raw bytes become an indexed source.

``research_imports`` (LaTeX / PDF / handwriting / mail ingestion) and the
research API router import this module.  It owns the document size cap, the
text chunking used for retrieval indexing, and the generic "uploaded bytes
become a source" flow.

The module is intentionally stdlib-only so importing it never pulls in the
host application's service locator or the ingestion engine (which would be a
circular import: ``research_imports`` imports this module).
"""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

# Matches the "研究文档上限为 25 MB" / "邮件附件上限为 25 MB" messages used by
# every caller, and the value the ingestion tests pin.
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
# Mirrors research_imports.MAX_TEX_CHARS: text indexed per document is capped.
_MAX_TEXT_CHARS = 3_000_000
# Retrieval chunk size, in characters.
_CHUNK_CHARS = 4000


def _decode_text(data: bytes) -> str:
    """Decode document bytes to text.

    Local copy of the decoding used by research_imports (utf-8-sig, then
    latin-1); kept here to avoid a circular import.
    """
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _split_text(text: str) -> list[str]:
    """Split text into retrieval chunks of at most ``_CHUNK_CHARS`` characters.

    Paragraph-aware: paragraphs are greedily packed so chunk boundaries fall
    on blank lines when possible; a single overlong paragraph is hard-split.
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue
        if len(paragraph) > _CHUNK_CHARS:
            # Flush first: hard-split pieces must keep document order.
            if current:
                chunks.append("\n\n".join(current))
                current, size = [], 0
            while len(paragraph) > _CHUNK_CHARS:
                chunks.append(paragraph[:_CHUNK_CHARS])
                paragraph = paragraph[_CHUNK_CHARS:]
        elif current and size + len(paragraph) + 2 > _CHUNK_CHARS:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        if paragraph:
            current.append(paragraph)
            size += len(paragraph) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def import_research_document(domain: Any, project_id: str, filename: str, data: bytes) -> dict[str, Any]:
    """Index uploaded document bytes as a research source.

    ``domain`` is a ``ResearchDomainStore`` (duck-typed to keep this module
    import-clean).  Returns the saved source payload, which carries ``id``,
    ``title`` and ``chunkCount``.  Raises ``ValueError`` for empty or
    oversized payloads -- the API layer maps it to 422/413.
    """
    if not data:
        raise ValueError("文档为空。")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("研究文档上限为 25 MB。")
    name = PurePosixPath(str(filename or "")).name.strip()
    if not name:
        raise ValueError("文件名无效。")
    text = _decode_text(data)[:_MAX_TEXT_CHARS]
    if not text.strip():
        raise ValueError("文档内容为空。")
    chunks = [{"page": None, "section": "Document", "content": part} for part in _split_text(text)]
    source = domain.save_source(
        project_id,
        {
            "type": "document",
            "title": name[:500],
            "contentHash": sha256(text.encode("utf-8")).hexdigest(),
            "branch": "main",
            "provider": "local-document",
            "metadata": {"originalName": name[:255], "importKind": "research-document"},
        },
        chunks,
        original_file=data,
    )
    return source
