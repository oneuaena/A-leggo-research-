"""Public literature metadata search (OpenAlex, Crossref, arXiv, Semantic Scholar).

Bounded HTTPS lookups with Chinese teacher-facing errors, Cloudflare clarity,
result dedupe, and empty-result hints. Records are saved through the domain store.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote, urlencode

import httpx

PROVIDERS = ("openalex", "crossref", "arxiv", "semantic_scholar")
CLASSES = (
    "foundational", "classic", "survey", "direct", "methodological", "recent",
    "preprint", "contradictory", "supporting", "background",
)
USER_AGENT = "A-leggo-Math-Research/1.0 (literature; educational; mailto:research@aleggo.local)"
MAX_RESPONSE_BYTES = 2_000_000
_ALLOWED = {
    "api.openalex.org", "api.crossref.org", "export.arxiv.org", "api.semanticscholar.org",
    "doi.org", "api.unpaywall.org",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I).strip()
    if not text or len(text) > 256 or " " in text:
        return ""
    return text.casefold()


def normalize_arxiv_id(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", text, flags=re.I)
    text = re.sub(r"\.pdf$", "", text, flags=re.I).strip()
    text = text.split("v")[0] if re.search(r"v\d+$", text) else text
    if re.fullmatch(r"\d{4}\.\d{4,5}", text) or re.fullmatch(r"[a-z\-]+/\d{7}", text, re.I):
        return text.casefold()
    return ""


def _blocked(text: str) -> bool:
    head = text[:2500].lower()
    return "just a moment" in head or "challenge-platform" in head or "cf-browser-verification" in head or "captcha" in head


def _assert_host(url: str) -> None:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").casefold()
    if not url.startswith("https://") or host not in _ALLOWED:
        raise ValueError(f"拒绝访问非白名单文献主机：{host or url[:80]}")


async def _get(url: str, *, accept: str = "application/json", timeout: float = 20.0) -> tuple[int, str]:
    _assert_host(url)

    async def _on_request(request: httpx.Request) -> None:
        _assert_host(str(request.url))

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, event_hooks={"request": [_on_request]},
                                     headers={"User-Agent": USER_AGENT, "Accept": accept}) as client:
            async with client.stream("GET", url) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("文献服务响应超过大小上限。")
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks).decode(response.encoding or "utf-8", "replace")
    except httpx.TimeoutException as exc:
        raise TimeoutError("文献服务超时，请稍后重试。") from exc
    except httpx.HTTPError as exc:
        raise ConnectionError(f"无法连接文献服务（{type(exc).__name__}）。") from exc


def _dedupe_key(item: dict[str, Any]) -> str:
    doi = normalize_doi(item.get("doi"))
    if doi:
        return "doi:" + doi
    arxiv = normalize_arxiv_id(item.get("arxivId") or item.get("arxiv_id") or "")
    if arxiv:
        return "arxiv:" + arxiv
    url = str(item.get("url") or "").strip().casefold()
    if url:
        return "url:" + url
    title = re.sub(r"[^\w]+", " ", str(item.get("title") or "").casefold()).strip()
    year = str(item.get("year") or "")
    return "title:" + title + "|" + year


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = _dedupe_key(item)
        if not item.get("title") or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_query_strategy(project: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = plan if isinstance(plan, dict) else {}
    question = str(project.get("question") or "").strip()
    objective = str(plan.get("objective") or "").strip()
    lemmas = [str(x) for x in (plan.get("proposedLemmas") or []) if str(x).strip()][:8]
    keywords = []
    for blob in (question, objective, *lemmas):
        keywords.extend(re.findall(r"[\w\u3400-\u9fff]{2,40}", blob)[:12])
    keywords = list(dict.fromkeys(keywords))[:16]
    queries = []
    if question:
        queries.append(question[:500])
    if objective and objective != question:
        queries.append(objective[:500])
    if keywords:
        queries.append(" ".join(keywords[:8])[:500])
    for lemma in lemmas[:3]:
        queries.append(lemma[:500])
    queries = list(dict.fromkeys(q for q in queries if len(q) >= 3))[:8] or ["mathematics research"]
    return {
        "queries": queries,
        "coreKeywords": keywords[:12],
        "synonyms": [],
        "mathematicalTerms": keywords[:8],
        "theoremNames": [],
        "relatedAuthors": [],
        "researchAreas": [],
        "msc": [],
        "method": "HEURISTIC_QUERY_STRATEGY",
        "reviewRequired": False,
    }


def plan_literature_queries(project: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_query_strategy(project, plan)


def classification_suggestions(item: dict[str, Any], question: str = "") -> list[dict[str, Any]]:
    title = str(item.get("title") or "").casefold()
    abstract = str(item.get("abstract") or "").casefold()
    hay = title + " " + abstract
    q = question.casefold()
    suggestions = []
    if "survey" in hay or "综述" in hay or "review" in title:
        suggestions.append({"classification": "survey", "reason": "标题/摘要像综述"})
    if q and any(tok in hay for tok in re.findall(r"[\w\u3400-\u9fff]{4,}", q)[:8]):
        suggestions.append({"classification": "direct", "reason": "与课题关键词重叠"})
    if re.search(r"\b(19\d\d|200\d)\b", str(item.get("year") or "")):
        suggestions.append({"classification": "classic", "reason": "较早年份"})
    year = str(item.get("year") or "")
    if year.isdigit() and int(year) >= 2020:
        suggestions.append({"classification": "recent", "reason": "近年文献"})
    if item.get("arxivId") and not item.get("doi"):
        suggestions.append({"classification": "preprint", "reason": "arXiv 预印本"})
    if not suggestions:
        suggestions.append({"classification": "background", "reason": "默认背景"})
    return suggestions[:4]


async def _openalex(query: str, limit: int) -> list[dict[str, Any]]:
    url = "https://api.openalex.org/works?" + urlencode({"search": query, "per_page": min(limit, 25), "mailto": "research@aleggo.local"})
    status, text = await _get(url)
    if _blocked(text):
        raise PermissionError("OpenAlex 触发了人机验证（Cloudflare）。请稍后重试，或换用其他文献源。")
    if status >= 400:
        raise RuntimeError(f"OpenAlex 返回 HTTP {status}。")
    payload = json.loads(text or "{}")
    items = []
    for work in payload.get("results") or []:
        doi = normalize_doi((work.get("doi") or "").replace("https://doi.org/", ""))
        title = " ".join(str(work.get("title") or "").split())
        authors = "; ".join(
            str(((a.get("author") or {}).get("display_name") or ""))
            for a in (work.get("authorships") or [])[:8]
        )
        year = work.get("publication_year")
        items.append({
            "type": "literature", "title": title, "authors": authors, "year": year,
            "doi": doi, "url": work.get("id") or (f"https://doi.org/{doi}" if doi else ""),
            "abstract": "", "provider": "openalex",
            "metadata": {"openAlexId": work.get("id"), "retrievedAt": _now()},
        })
    return items


async def _crossref(query: str, limit: int) -> list[dict[str, Any]]:
    url = "https://api.crossref.org/works?" + urlencode({"query": query, "rows": min(limit, 25)})
    status, text = await _get(url)
    if _blocked(text):
        raise PermissionError("Crossref 触发了人机验证（Cloudflare）。请稍后重试。")
    if status >= 400:
        raise RuntimeError(f"Crossref 返回 HTTP {status}。")
    payload = json.loads(text or "{}")
    items = []
    for work in ((payload.get("message") or {}).get("items") or []):
        doi = normalize_doi(work.get("DOI"))
        title = " ".join((work.get("title") or [" "])[0].split())
        authors = "; ".join(
            f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
            for a in (work.get("author") or [])[:8]
        )
        year = None
        for part in (work.get("published-print") or work.get("published-online") or {}).get("date-parts") or []:
            if part:
                year = part[0]
                break
        items.append({
            "type": "literature", "title": title, "authors": authors, "year": year,
            "doi": doi, "url": f"https://doi.org/{doi}" if doi else str(work.get("URL") or ""),
            "abstract": re.sub(r"<[^>]+>", "", str(work.get("abstract") or ""))[:2000],
            "provider": "crossref", "metadata": {"retrievedAt": _now()},
        })
    return items


async def _arxiv(query: str, limit: int) -> list[dict[str, Any]]:
    words = " AND ".join("all:" + quote(w, safe="") for w in re.findall(r"[\w\u3400-\u9fff\-]+", query)[:10]) or "all:" + quote(query)
    url = (f"https://export.arxiv.org/api/query?search_query=(cat:math.*+OR+cat:math-ph)+AND+{words.replace(' ', '+')}"
           f"&start=0&max_results={min(limit, 25)}&sortBy=relevance")
    status, text = await _get(url, accept="application/atom+xml")
    if status >= 400:
        raise RuntimeError(f"arXiv 返回 HTTP {status}。")
    try:
        from defusedxml import ElementTree as ET  # type: ignore
    except ImportError:
        from xml.etree import ElementTree as ET  # noqa: S405
    root = ET.fromstring(text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in root.findall("a:entry", ns):
        link = entry.findtext("a:id", default="", namespaces=ns).strip()
        arxiv_id = normalize_arxiv_id(link)
        items.append({
            "type": "literature",
            "title": " ".join(entry.findtext("a:title", default="", namespaces=ns).split()),
            "authors": "; ".join(a.findtext("a:name", default="", namespaces=ns) for a in entry.findall("a:author", ns)[:8]),
            "year": (entry.findtext("a:published", default="", namespaces=ns) or "")[:4],
            "doi": "", "arxivId": arxiv_id, "url": link,
            "abstract": " ".join(entry.findtext("a:summary", default="", namespaces=ns).split())[:2000],
            "provider": "arxiv", "metadata": {"retrievedAt": _now()},
        })
    return items


async def _semantic(query: str, limit: int) -> list[dict[str, Any]]:
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode({
        "query": query, "limit": min(limit, 25),
        "fields": "title,authors,year,abstract,externalIds,url",
    })
    status, text = await _get(url)
    if _blocked(text):
        raise PermissionError(
            "Semantic Scholar 触发了人机验证（Cloudflare）。请稍后重试，或在浏览器打开 semanticscholar.org 完成验证后再查。"
        )
    if status == 429:
        raise RuntimeError("Semantic Scholar 请求过于频繁（HTTP 429），请稍后再试。")
    if status >= 400:
        raise RuntimeError(f"Semantic Scholar 返回 HTTP {status}。")
    payload = json.loads(text or "{}")
    items = []
    for paper in payload.get("data") or []:
        ids = paper.get("externalIds") or {}
        doi = normalize_doi(ids.get("DOI"))
        arxiv = normalize_arxiv_id(ids.get("ArXiv"))
        authors = "; ".join(str(a.get("name") or "") for a in (paper.get("authors") or [])[:8])
        items.append({
            "type": "literature", "title": str(paper.get("title") or ""), "authors": authors,
            "year": paper.get("year"), "doi": doi, "arxivId": arxiv,
            "url": paper.get("url") or (f"https://doi.org/{doi}" if doi else ""),
            "abstract": str(paper.get("abstract") or "")[:2000],
            "provider": "semantic_scholar", "metadata": {"retrievedAt": _now()},
        })
    return items


_FETCHERS = {
    "openalex": _openalex,
    "crossref": _crossref,
    "arxiv": _arxiv,
    "semantic_scholar": _semantic,
}


async def search_literature(domain: Any, project_id: str, query: str, *, limit: int = 40,
                            max_pages: int = 2, guard: Any = None) -> dict[str, Any]:
    """Search all providers, dedupe, save via domain; return teacher-friendly status."""
    del max_pages, guard  # reserved for paginated providers
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not 3 <= len(query) <= 500:
        raise ValueError("文献查询须为 3 到 500 个字符。")
    per = max(3, min(25, int(limit)))
    providers_ok: set[str] = set()
    warnings: dict[str, str] = {}
    statuses: dict[str, str] = {}
    collected: list[dict[str, Any]] = []

    async def one(name: str) -> None:
        try:
            hits = await asyncio.wait_for(_FETCHERS[name](query, per), timeout=25)
            collected.extend(hits)
            providers_ok.add(name)
            statuses[name] = "AVAILABLE"
            if not hits:
                warnings[name] = "无匹配结果"
        except PermissionError as exc:
            statuses[name] = "BLOCKED"
            warnings[name] = str(exc)
        except TimeoutError as exc:
            statuses[name] = "TIMEOUT"
            warnings[name] = str(exc) or "文献服务超时，请稍后重试。"
        except Exception as exc:
            statuses[name] = "ERROR"
            warnings[name] = str(exc)[:400] or type(exc).__name__

    await asyncio.gather(*(one(name) for name in PROVIDERS))
    unique = _dedupe(collected)[: max(1, min(int(limit), 240))]
    project = domain.store.get_project(project_id) or {}
    saved = []
    for item in unique:
        suggestions = classification_suggestions(item, str(project.get("question") or ""))
        item = {
            **item,
            "classification": suggestions[0]["classification"],
            "branch": "main",
            "metadata": {
                **(item.get("metadata") or {}),
                "classificationSuggestions": suggestions,
                "verificationStatus": "EXTERNAL_METADATA_UNVERIFIED",
            },
        }
        item.pop("id", None)
        try:
            saved.append(domain.save_source(project_id, item))
        except Exception as exc:
            warnings["save"] = (warnings.get("save") or "") + f"保存失败：{exc}; "

    if len(providers_ok) == len(PROVIDERS) and not warnings:
        availability = "COMPLETE"
    elif providers_ok:
        availability = "PARTIAL"
    else:
        availability = "IMPLEMENTED BUT EXTERNAL SERVICE UNAVAILABLE"

    empty_hint = ""
    if not saved:
        if any(statuses.get(n) == "BLOCKED" for n in PROVIDERS):
            empty_hint = "外部文献源触发了 Cloudflare 人机验证，未返回结果。请稍后重试，或在浏览器打开对应站点完成验证后再查。"
        elif all(statuses.get(n) in {"TIMEOUT", "ERROR", "BLOCKED"} for n in PROVIDERS):
            empty_hint = "全部文献源暂时不可用（超时或错误）。请检查网络后重试。"
        else:
            empty_hint = "没有找到匹配的文献元数据。可尝试更短关键词、英文术语或作者姓名。"

    return {
        "query": query,
        "queriedAt": _now(),
        "scope": {"providers": list(PROVIDERS), "limit": limit, "bounded": True},
        "items": saved,
        "providers": sorted(providers_ok),
        "providerStatuses": statuses,
        "warnings": warnings,
        "availabilityStatus": availability,
        "resultCount": len(saved),
        "candidateCount": len(unique),
        "emptyHint": empty_hint,
    }


async def lookup_doi(domain: Any, project_id: str, doi: str, *, guard: Any = None) -> dict[str, Any]:
    del guard
    doi = normalize_doi(doi)
    if not doi:
        raise ValueError("请提供有效的 DOI。")
    url = "https://api.crossref.org/works/" + quote(doi)
    try:
        status, text = await _get(url)
    except TimeoutError as exc:
        raise ValueError(str(exc)) from exc
    except ConnectionError as exc:
        raise ValueError(str(exc)) from exc
    if _blocked(text):
        raise ValueError("Crossref DOI 查询触发了 Cloudflare 人机验证，请稍后重试。")
    if status == 404:
        raise ValueError("未找到该 DOI。")
    if status >= 400:
        raise ValueError(f"DOI 查询失败（HTTP {status}）。")
    work = (json.loads(text).get("message") or {})
    title = " ".join((work.get("title") or [""])[0].split())
    authors = "; ".join(
        f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
        for a in (work.get("author") or [])[:12]
    )
    year = None
    for part in (work.get("published-print") or work.get("published-online") or {}).get("date-parts") or []:
        if part:
            year = part[0]
            break
    item = {
        "type": "literature", "title": title or doi, "authors": authors, "year": year,
        "doi": doi, "url": f"https://doi.org/{doi}", "provider": "crossref",
        "abstract": re.sub(r"<[^>]+>", "", str(work.get("abstract") or ""))[:2000],
        "branch": "main",
        "metadata": {"retrievedAt": _now(), "verificationStatus": "EXTERNAL_METADATA_UNVERIFIED"},
    }
    suggestions = classification_suggestions(item, "")
    item["classification"] = suggestions[0]["classification"]
    item["metadata"]["classificationSuggestions"] = suggestions
    saved = domain.save_source(project_id, item)
    return {"item": saved, "source": saved}
