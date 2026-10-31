"""Markdown / PDF research reports for teachers and solo researchers.

PDF prefers matplotlib + Noto CJK when available; otherwise embeds the Markdown
as a PDF file attachment with a bilingual cover page so export never silently
drops the narrative.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _clip(value: Any, limit: int = 8000) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n\n[…]"


def _plan(store: Any, project: dict[str, Any]) -> dict[str, Any]:
    try:
        with store.connection() as conn:
            row = conn.execute(
                "SELECT plan_json FROM research_plan_versions WHERE project_id=? AND version=?",
                (project["id"], project.get("currentVersion") or project.get("current_version") or 0),
            ).fetchone()
        if not row:
            return {}
        data = json.loads(row["plan_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_markdown_report(store: Any, project_id: str) -> str:
    """Human-readable research package narrative (UTF-8 Markdown)."""
    project = store.get_project(project_id)
    if project is None:
        raise ValueError("未找到该研究项目。")
    plan = _plan(store, project)
    artifacts = list(store.list_artifacts(project_id) or [])
    lines: list[str] = [
        f"# {project.get('title') or '研究项目报告'}",
        "",
        f"_导出时间：{_now()}_",
        "",
        "## 研究问题",
        "",
        _clip(project.get("question"), 20000) or "（未填写）",
        "",
    ]
    if plan.get("objective") and str(plan.get("objective")) != str(project.get("question")):
        lines += ["## 研究目标", "", _clip(plan["objective"], 8000), ""]
    if plan.get("problemFormalization"):
        lines += ["## 形式化表述", "", _clip(plan["problemFormalization"], 12000), ""]
    assumptions = [str(item) for item in (plan.get("assumptions") or []) if str(item).strip()]
    if assumptions:
        lines += ["## 基本假设", ""] + [f"- {item}" for item in assumptions[:40]] + [""]
    questions = [str(item) for item in (plan.get("keyQuestions") or []) if str(item).strip()]
    if questions:
        lines += ["## 关键问题", ""] + [f"{i}. {item}" for i, item in enumerate(questions[:30], 1)] + [""]

    try:
        from aleggo.study.research_domain import ResearchDomainStore
        domain = ResearchDomainStore(store)
        graph = domain.list_graph(project_id)
        nodes = list(graph.get("nodes") or [])
        sources = list(domain.list_sources(project_id) if hasattr(domain, "list_sources") else [])
    except Exception:
        nodes, sources = [], []
        try:
            with store.connection() as conn:
                nodes = [dict(row) for row in conn.execute(
                    "SELECT * FROM research_graph_nodes WHERE project_id=? ORDER BY rowid", (project_id,))]
                sources = [dict(row) for row in conn.execute(
                    "SELECT * FROM research_sources WHERE project_id=? ORDER BY rowid", (project_id,))]
        except Exception:
            pass

    claims = [n for n in nodes if (n.get("type") or n.get("node_type") or n.get("nodeType")) in
              {"theorem", "lemma", "proposition", "conjecture", "definition", "counterexample", "evidence"}]
    if claims:
        lines += ["## 证据图命题", ""]
        for node in claims[:120]:
            kind = node.get("type") or node.get("node_type") or node.get("nodeType") or "node"
            status = node.get("status") or "unverified"
            title = node.get("title") or ""
            content = node.get("content") or ""
            lines += [f"### [{kind}] {title}", "", f"_证据状态：{status}_", "", _clip(content, 6000), ""]

    if sources:
        lines += ["## 文献与来源", ""]
        for source in sources[:80]:
            title = source.get("title") or source.get("id")
            authors = source.get("authors") or ""
            year = source.get("year") or ""
            doi = source.get("doi") or ""
            lines.append(f"- **{title}** — {authors} {year}" + (f" DOI {doi}" if doi else ""))
        lines.append("")

    finals = [a for a in artifacts if (a.get("metadata") or {}).get("artifactKind") == "final-verification"
              or str(a.get("title") or "").lower().startswith("final")]
    if finals:
        lines += ["## 综合小结", "", _clip(finals[-1].get("content"), 30000), ""]
    elif artifacts:
        lines += ["## 最近产物", "", _clip(artifacts[-1].get("content"), 12000), ""]

    lines += [
        "## 说明",
        "",
        "本稿由 A-leggo 根据研究记录自动整理。命题状态为记录的证据等级：只有 Lean 内核已验证的结果"
        "经过证明检查，其余论证须由研究者核对。导出包中的 `paper/main.tex` 更适合投稿前编辑。",
        "",
    ]
    return "\n".join(lines)


def _pdf_via_matplotlib(markdown: str, title: str) -> bytes | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import font_manager
        from matplotlib.backends.backend_pdf import PdfPages
        import matplotlib.pyplot as plt
    except Exception:
        return None
    font_path = ""
    for path in font_manager.findSystemFonts():
        name = Path(path).name.lower()
        if "notosanscjk" in name or "notosanssc" in name or "wqy" in name or "droidsansfallback" in name:
            font_path = path
            break
    if not font_path and _CJK.search(markdown + title):
        return None
    prop = font_manager.FontProperties(fname=font_path) if font_path else None
    lines = markdown.replace("\r\n", "\n").split("\n")
    pages: list[list[str]] = []
    chunk: list[str] = []
    for line in lines:
        # Soft-wrap long lines for the page canvas.
        while len(line) > 96:
            chunk.append(line[:96])
            line = line[96:]
            if len(chunk) >= 48:
                pages.append(chunk)
                chunk = []
        chunk.append(line)
        if len(chunk) >= 48:
            pages.append(chunk)
            chunk = []
    if chunk:
        pages.append(chunk)
    if not pages:
        pages = [[title or "研究项目报告"]]
    from io import BytesIO
    buffer = BytesIO()
    with PdfPages(buffer) as pdf:
        for page_lines in pages[:80]:
            fig = plt.figure(figsize=(8.27, 11.69))
            fig.patch.set_facecolor("white")
            y = 0.96
            for raw in page_lines:
                text = raw.replace("\t", "    ")[:120]
                weight = "bold" if text.startswith("#") else "normal"
                size = 12 if text.startswith("# ") else 10 if text.startswith("##") else 9
                display = text.lstrip("#").strip() if text.startswith("#") else text
                fig.text(0.06, y, display or " ", fontproperties=prop, fontsize=size, fontweight=weight,
                         va="top", ha="left", wrap=False, family=None)
                y -= 0.018 if size <= 9 else 0.022
                if y < 0.04:
                    break
            pdf.savefig(fig)
            plt.close(fig)
    return buffer.getvalue()


def _pdf_with_embedded_markdown(markdown: str, title: str) -> bytes:
    """Minimal PDF 1.4: cover page + embedded research-report.md (always works)."""
    import hashlib
    import zlib

    safe_title = re.sub(r"[^\x20-\x7E]+", " ", title or "Research report").strip()[:80] or "Research report"
    cover_lines = [
        safe_title,
        "",
        "A-leggo research report",
        "Full Markdown is attached as research-report.md",
        "Open the attachment in your PDF reader, or download Markdown / LaTeX.",
        "",
        "[zh] Wan zheng Markdown yi fu jian xing shi qian ru;",
        "qing yong yue du qi da kai fu jian, huo gai yong Markdown / LaTeX dao chu.",
    ]

    def pdf_escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    y = 750
    parts = ["BT /F1 11 Tf"]
    for line in cover_lines:
        parts.append(f"1 0 0 1 50 {y} Tm ({pdf_escape(line)}) Tj")
        y -= 16
    parts.append("ET")
    content_b = "\n".join(parts).encode("latin-1", errors="replace")
    md_bytes = markdown.encode("utf-8")
    md_compressed = zlib.compress(md_bytes)
    checksum = hashlib.md5(md_bytes).hexdigest()

    objs: list[bytes] = []

    def obj(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    contents = obj(b"<< /Length %d >>\nstream\n" % len(content_b) + content_b + b"\nendstream")
    embedded = obj(
        (b"<< /Type /EmbeddedFile /Subtype /text#2Fmarkdown /Params << /Size %d /CheckSum <%s> >> "
         b"/Filter /FlateDecode /Length %d >>\nstream\n") % (len(md_bytes), checksum.encode(), len(md_compressed))
        + md_compressed + b"\nendstream"
    )
    filespec = obj(
        b"<< /Type /Filespec /F (research-report.md) /UF (research-report.md) "
        b"/EF << /F %d 0 R >> /Desc (Full Markdown research report) >>" % embedded
    )
    # pages id will be len+2 after we reserve page; build pages after page
    pages_ref = len(objs) + 2  # page then pages
    page = obj(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
        b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_ref, contents, font)
    )
    pages = obj(b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % page)
    assert pages == pages_ref
    catalog = obj(
        b"<< /Type /Catalog /Pages %d 0 R /Names << /EmbeddedFiles << /Names [(research-report.md) %d 0 R] >> >> "
        b"/AF [%d 0 R] >>" % (pages, filespec, filespec)
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(out)


def build_pdf_report(markdown: str, title: str = "") -> bytes:
    """Return PDF bytes. Never silently returns empty content."""
    markdown = str(markdown or "").strip() or "# Research report\n\n(empty)\n"
    title = str(title or "研究项目报告")
    rendered = _pdf_via_matplotlib(markdown, title)
    if rendered and len(rendered) > 100 and rendered.startswith(b"%PDF"):
        return rendered
    return _pdf_with_embedded_markdown(markdown, title)
