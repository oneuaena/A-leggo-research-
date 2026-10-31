"""Lookups in the mathematical databases research mathematicians use daily.

* OEIS — identify an integer sequence from its first terms (or an A-number);
* zbMATH Open — reviewed mathematical literature with MSC classification;
* arXiv (math and math-ph categories) — recent preprints;
* LMFDB — elliptic curves, number fields, modular forms, L-functions, groups.

Each lookup is a bounded, read-only HTTPS GET with a timeout, a response-size
cap and a short in-process cache.  Results are returned in the same shape as
mathematical worker results so that they are recorded as reproducible tool runs
(query, provider URL, retrieval time) and can be cited.  Database records are
*literature/metadata evidence*: a sequence match or an LMFDB entry is a strong
hint, never a proof of a new claim.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import quote, urlencode

# NOTE: ``httpx`` is imported lazily inside ``_get()`` (the only place that
# performs HTTP).  The module's tool *schemas* are read at import time by the
# tool catalog / runtime planner, and those import-time readers must keep
# working where the HTTP stack is not installed (evaluation harness with an
# injected router, tests).  ``httpx.Request`` below is a string annotation
# (``from __future__ import annotations``), so it needs no top-level import.

USER_AGENT = "A-leggo-research/4.4 (mathematics research assistant; +https://www.lmfdb.org/api/ friendly client)"
MAX_RESPONSE_BYTES = 3_000_000
CACHE_SECONDS = 6 * 3600
DATABASE_TOOLS = frozenset({"oeis_search", "zbmath_search", "arxiv_search", "lmfdb_search"})
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

DATABASE_TOOL_SCHEMAS: dict[str, str] = {
    "oeis_search": '{"query": "1, 2, 5, 14, 42, 132" | "A000108" | "keywords", "limit": 5} — identify integer sequences (OEIS)',
    "zbmath_search": '{"query": "Catalan numbers lattice paths", "limit": 5} — reviewed literature with MSC codes (zbMATH Open)',
    "arxiv_search": '{"query": "Ramsey numbers lower bound", "category": "math.CO" (optional), "limit": 5} — recent math preprints (arXiv)',
    "lmfdb_search": '{"collection": "ec_curvedata" | "nf_fields" | "mf_newforms" | "g2c_curves" | "char_dirichlet" | "gps_groups", '
                    '"query": {"lmfdb_label": "37.a1"} | {"conductor": "11"}, "limit": 5} — LMFDB objects (label lookups also accept {"label": "37.a1"})',
}
_LMFDB_COLLECTIONS = {"ec_curvedata", "ec_classdata", "nf_fields", "mf_newforms", "mf_newspaces", "g2c_curves",
                      "char_dirichlet", "gps_groups", "gps_transitive", "lf_fields", "artin_reps", "lfunc_search",
                      "av_fq_isog", "hgm_families", "belyi_galmaps"}
_LMFDB_LABEL_RULES = (
    (re.compile(r"^\d+\.[a-z]+\d+$"), "ec_curvedata", "lmfdb_label"),            # 37.a1
    (re.compile(r"^\d+[a-z]+\d+$"), "ec_curvedata", "Clabel"),                   # 37a1 (Cremona)
    (re.compile(r"^\d+\.\d+\.\d+\.\d+$"), "nf_fields", "label"),                 # 2.0.4.1
    (re.compile(r"^\d+\.\d+\.[a-z]+\.[a-z]+$"), "mf_newforms", "label"),          # 11.2.a.a
    (re.compile(r"^\d+\.[a-z]+\.\d+\.\d+$"), "g2c_curves", "label"),              # 169.a.169.1
    (re.compile(r"^\d+\.\d+$"), "gps_groups", "label"),                          # 8.3
)
_FIELD_LIMIT = 40


class DatabaseError(RuntimeError):
    def __init__(self, message: str, error_type: str = "UNAVAILABLE") -> None:
        super().__init__(message)
        self.error_type = error_type


def _now() -> str:
    return datetime.now(UTC).isoformat()


_ALLOWED_HOSTS = frozenset({
    "oeis.org", "www.oeis.org", "api.zbmath.org", "zbmath.org", "www.zbmath.org",
    "export.arxiv.org", "arxiv.org", "www.arxiv.org", "www.lmfdb.org", "lmfdb.org",
})


def _assert_public_db_url(url: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or host not in _ALLOWED_HOSTS:
        raise DatabaseError("拒绝访问非白名单数据库主机。", "VALIDATION_ERROR")


async def _get(url: str, *, accept: str = "application/json", timeout: float = 25.0) -> tuple[int, str]:
    _assert_public_db_url(url)
    import httpx  # lazy: see the module note above

    async def _on_request(request: httpx.Request) -> None:
        _assert_public_db_url(str(request.url))

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, event_hooks={"request": [_on_request]},
                                 headers={"User-Agent": USER_AGENT, "Accept": accept}) as client:
        async with client.stream("GET", url) as response:
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise DatabaseError("数据库响应超过大小上限。", "OUTPUT_LIMIT")
                chunks.append(chunk)
            return response.status_code, b"".join(chunks).decode(response.encoding or "utf-8", "replace")


def _blocked(text: str) -> bool:
    head = text[:2000].lower()
    return "just a moment" in head or "challenge-platform" in head or "captcha" in head


def _limit(input_data: dict[str, Any], default: int = 5) -> int:
    try:
        return max(1, min(20, int(input_data.get("limit") or default)))
    except (TypeError, ValueError):
        return default


def _query(input_data: dict[str, Any]) -> str:
    query = str(input_data.get("query") or input_data.get("q") or "").strip()
    if not query or len(query) > 1000:
        raise DatabaseError('请提供 {"query": "..."}，长度 1 到 1000 个字符。', "VALIDATION_ERROR")
    return query


def _first(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #

async def _oeis(input_data: dict[str, Any]) -> dict[str, Any]:
    query = _query(input_data)
    if re.fullmatch(r"[-\d\s,]+", query):
        query = ",".join(part for part in re.split(r"[\s,]+", query.strip()) if part)
    url = "https://oeis.org/search?" + urlencode({"q": query, "fmt": "json"})
    status, text = await _get(url)
    if _blocked(text):
        raise DatabaseError("OEIS 触发了 Cloudflare 人机验证，未返回数列结果。请在浏览器打开 " + url.replace("&fmt=json", "") + " 完成验证后重试；本系统不会伪造 OEIS 记录。", "BLOCKED")
    if status >= 400:
        raise DatabaseError(f"OEIS 返回 HTTP {status}。")
    try:
        payload = json.loads(text) if text.strip() else []
    except ValueError as exc:
        raise DatabaseError("OEIS 返回了非 JSON 数据。", "PROTOCOL_ERROR") from exc
    if isinstance(payload, dict):  # pre-2024 envelope
        count, results = int(payload.get("count") or 0), payload.get("results") or []
    else:
        results = payload or []
        count = len(results)
    items = []
    seen: set[str] = set()
    for entry in results[: _limit(input_data) * 2]:
        number = int(entry.get("number") or 0)
        anum = f"A{number:06d}"
        if anum in seen:
            continue
        seen.add(anum)
        items.append({
            "id": anum, "name": str(entry.get("name") or "")[:500],
            "terms": str(entry.get("data") or "")[:400],
            "formulas": [str(item)[:300] for item in (entry.get("formula") or [])[:3]],
            "comments": [str(item)[:300] for item in (entry.get("comment") or [])[:2]],
            "keywords": str(entry.get("keyword") or ""), "author": str(entry.get("author") or "")[:200],
            "url": f"https://oeis.org/{anum}",
        })
        if len(items) >= _limit(input_data):
            break
    empty_hint = "" if items else "OEIS 未找到匹配数列。可尝试更少的前几项、去掉空格，或直接使用 A 编号（如 A000108）。"
    return {"provider": "OEIS", "query": query, "url": url.replace("&fmt=json", ""), "count": count, "items": items, "emptyHint": empty_hint}


async def _zbmath(input_data: dict[str, Any]) -> dict[str, Any]:
    query = _query(input_data)
    url = "https://api.zbmath.org/v1/document/_search?" + urlencode(
        {"search_string": query, "page": 0, "results_per_page": _limit(input_data)})
    status, text = await _get(url)
    if status == 404:
        return {"provider": "zbMATH Open", "query": query, "url": url, "count": 0, "items": []}
    if status >= 400:
        raise DatabaseError(f"zbMATH Open 返回 HTTP {status}。")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DatabaseError("zbMATH Open 返回了非 JSON 数据。", "PROTOCOL_ERROR") from exc
    items = []
    for entry in (payload.get("result") or [])[: _limit(input_data)]:
        title = entry.get("title") or {}
        authors = (entry.get("contributors") or {}).get("authors") or []
        source = entry.get("source") or {}
        series = source.get("series") or [{}]
        msc = [str(code.get("code")) for code in entry.get("msc") or [] if isinstance(code, dict) and code.get("code")]
        review = next((str(item.get("text") or "") for item in entry.get("editorial_contributions") or [] if isinstance(item, dict)), "")
        identifier = entry.get("identifier") or entry.get("id")
        links = [str(link.get("identifier") or "") for link in entry.get("links") or [] if isinstance(link, dict) and link.get("type") == "doi"]
        items.append({
            "id": str(identifier or ""), "title": str(title.get("title") if isinstance(title, dict) else title)[:500],
            "authors": "; ".join(str(author.get("name") or "") for author in authors[:8] if isinstance(author, dict)),
            "year": str(entry.get("year") or ""),
            "source": str((series[0] or {}).get("title") or source.get("source") or "")[:300] if isinstance(series, list) and series else str(source.get("source") or "")[:300],
            "msc": msc[:6], "doi": links[0] if links else "", "review": review[:700],
            "url": f"https://zbmath.org/?q=an:{identifier}" if identifier else "",
        })
    total = (payload.get("status") or {}).get("nr_total_results") if isinstance(payload.get("status"), dict) else None
    return {"provider": "zbMATH Open", "query": query, "url": url, "count": int(total or len(items)), "items": items, "emptyHint": ("" if items else "zbMATH Open 无匹配。可尝试英文关键词、作者或 MSC 分类号。")}


_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


async def _arxiv(input_data: dict[str, Any]) -> dict[str, Any]:
    query = _query(input_data)
    category = str(input_data.get("category") or "").strip()
    if category and not re.fullmatch(r"(?:math|math-ph)(?:\.[A-Z]{2})?", category):
        raise DatabaseError('category 须为数学 arXiv 分类，例如 "math.NT" 或 "math-ph"。', "VALIDATION_ERROR")
    words = " AND ".join("all:" + quote(word, safe="") for word in re.findall(r"[\w\-]+", query)[:12]) or "all:" + quote(query)
    scope = f"cat:{category}" if category else "(cat:math.*+OR+cat:math-ph)"
    url = (f"https://export.arxiv.org/api/query?search_query={scope}+AND+{words.replace(' ', '+')}"
           f"&start=0&max_results={_limit(input_data)}&sortBy=relevance")
    status, text = await _get(url, accept="application/atom+xml")
    if status >= 400:
        raise DatabaseError(f"arXiv 返回 HTTP {status}。")
    try:
        from defusedxml import ElementTree as SafeTree  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - fall back to the stdlib parser (no DTD/entity expansion used by arXiv)
        from xml.etree import ElementTree as SafeTree  # noqa: S405
    try:
        root = SafeTree.fromstring(text)
    except Exception as exc:
        raise DatabaseError("arXiv 返回了损坏的 XML。", "PROTOCOL_ERROR") from exc
    items = []
    for entry in root.findall("a:entry", _ATOM):
        link = entry.findtext("a:id", default="", namespaces=_ATOM).strip()
        arxiv_id = link.rsplit("/abs/", 1)[-1]
        items.append({
            "id": arxiv_id, "title": " ".join(entry.findtext("a:title", default="", namespaces=_ATOM).split())[:500],
            "authors": "; ".join(author.findtext("a:name", default="", namespaces=_ATOM) for author in entry.findall("a:author", _ATOM)[:8]),
            "published": entry.findtext("a:published", default="", namespaces=_ATOM)[:10],
            "categories": [item.get("term", "") for item in entry.findall("a:category", _ATOM)][:6],
            "summary": " ".join(entry.findtext("a:summary", default="", namespaces=_ATOM).split())[:900],
            "url": link,
        })
    total = root.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults") or len(items)
    return {"provider": "arXiv", "query": query, "url": url, "count": int(total), "items": items, "emptyHint": ("" if items else "arXiv 无匹配。可缩小关键词，或指定 math.NT 等分类。")}


def _lmfdb_value(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value and ("__RealLiteral__" in value or "prec" in value):
        return value["data"]
    if isinstance(value, list):
        return [_lmfdb_value(item) for item in value[:_FIELD_LIMIT]]
    if isinstance(value, str):
        return value[:400]
    return value


async def _lmfdb(input_data: dict[str, Any]) -> dict[str, Any]:
    collection = str(input_data.get("collection") or "").strip()
    query = input_data.get("query") if isinstance(input_data.get("query"), dict) else {}
    label = str(input_data.get("label") or (input_data.get("query") if isinstance(input_data.get("query"), str) else "") or "").strip()
    if label and not query:
        for pattern, guessed, field in _LMFDB_LABEL_RULES:
            if pattern.fullmatch(label) and (not collection or collection == guessed):
                collection, query = guessed, {field: label}
                break
        else:
            if not collection:
                raise DatabaseError("无法从标签推断 LMFDB 集合；请提供 collection。", "VALIDATION_ERROR")
            query = {"label": label}
    if collection not in _LMFDB_COLLECTIONS:
        raise DatabaseError("collection 须为以下之一：" + ", ".join(sorted(_LMFDB_COLLECTIONS)), "VALIDATION_ERROR")
    if not query or len(query) > 8:
        raise DatabaseError('请提供 1 到 8 个查询字段，例如 {"conductor": "11"}。', "VALIDATION_ERROR")
    params: dict[str, str] = {}
    for key, value in query.items():
        key = str(key)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,60}", key):
            raise DatabaseError(f"无效的 LMFDB 字段名 {key!r}。", "VALIDATION_ERROR")
        text = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
        params[key] = text[:200]
    params.update({"_format": "json", "_limit": str(_limit(input_data))})
    fields = input_data.get("fields")
    if isinstance(fields, list) and fields:
        params["_fields"] = ",".join(str(item) for item in fields[:30] if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,60}", str(item)))
    url = f"https://www.lmfdb.org/api/{collection}/?" + urlencode(params)
    status, text = await _get(url)
    if _blocked(text):
        raise DatabaseError("LMFDB 触发了 Cloudflare 人机验证。请在浏览器打开 " + url.replace("_format=json&", "") + " 完成验证后重试。", "BLOCKED")
    if status >= 400:
        raise DatabaseError(f"LMFDB 返回 HTTP {status}。")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DatabaseError("LMFDB 返回了非 JSON 数据。", "PROTOCOL_ERROR") from exc
    items = []
    for record in (payload.get("data") or [])[: _limit(input_data)]:
        if isinstance(record, dict):
            compact = {key: _lmfdb_value(value) for key, value in list(record.items())[:_FIELD_LIMIT] if key != "id"}
            items.append(compact)
    return {"provider": "LMFDB", "collection": collection, "query": query, "url": url, "count": len(items), "items": items,
            "browseUrl": f"https://www.lmfdb.org/api/{collection}/",
            "emptyHint": ("" if items else "LMFDB 无匹配。请确认集合名与标签（如 11.a2）是否正确。")}


_PROVIDERS = {"oeis_search": _oeis, "zbmath_search": _zbmath, "arxiv_search": _arxiv, "lmfdb_search": _lmfdb}


async def run_database_tool(name: str, input_data: dict[str, Any], *, timeout_seconds: int = 30) -> dict[str, Any]:
    """Worker-shaped result for one database lookup (never raises for provider errors)."""
    import httpx  # exception classes must be available in this scope as well as in _get
    if name not in _PROVIDERS:
        raise ValueError("未知的数学数据库工具。")
    if not isinstance(input_data, dict):
        input_data = {}
    key = name + ":" + hashlib.sha256(json.dumps(input_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cached = _CACHE.get(key)
    started = time.monotonic()
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        payload, from_cache = cached[1], True
    else:
        try:
            payload = await asyncio.wait_for(_PROVIDERS[name](input_data), timeout=timeout_seconds)
        except DatabaseError as exc:
            status = "REJECTED" if exc.error_type in {"VALIDATION_ERROR", "BLOCKED"} else "TOOL_FAILURE"
            return {"ok": False, "output": "", "error": str(exc), "error_type": exc.error_type,
                    "verification_status": status, "verification_level": "UNKNOWN", "tool_version": "math-databases/1",
                    "environment": {"provider": name}, "recorded_at": _now()}
        except (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException):
            return {"ok": False, "output": "", "error": "数据库未在时限内响应，请稍后重试。", "error_type": "TIMEOUT",
                    "verification_status": "TOOL_FAILURE", "verification_level": "UNKNOWN", "tool_version": "math-databases/1",
                    "environment": {"provider": name}, "recorded_at": _now()}
        except httpx.HTTPError as exc:
            return {"ok": False, "output": "", "error": f"无法连接数据库（{type(exc).__name__}）。",
                    "error_type": "UNAVAILABLE", "verification_status": "TOOL_FAILURE", "verification_level": "UNKNOWN",
                    "tool_version": "math-databases/1", "environment": {"provider": name}, "recorded_at": _now()}
        payload["retrievedAt"] = _now()
        _CACHE[key] = (time.time(), payload)
        from_cache = False
        if len(_CACHE) > 500:
            for stale in sorted(_CACHE, key=lambda item: _CACHE[item][0])[:100]:
                _CACHE.pop(stale, None)
    return {"ok": True, "output": json.dumps(payload, ensure_ascii=False), "error": "", "verification_status": "SUCCESS",
            "verification_level": "DATABASE_LOOKUP", "worker_exit_code": 0, "tool_version": "math-databases/1",
            "environment": {"provider": payload.get("provider"), "url": payload.get("url"), "cached": from_cache,
                            "elapsedMs": int((time.monotonic() - started) * 1000)},
            "recorded_at": payload.get("retrievedAt"), "generated_files": [], "generated_file_warnings": []}


def database_sources(name: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    """Citable source records (title, url, provider) from a successful lookup."""
    if not result.get("ok"):
        return []
    try:
        payload = json.loads(result.get("output") or "{}")
    except ValueError:
        return []
    sources = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        if name == "oeis_search":
            title = f"{item.get('id')}: {item.get('name')}"
        elif name == "lmfdb_search":
            label = item.get("lmfdb_label") or item.get("label") or item.get("Clabel") or ""
            title = f"LMFDB {payload.get('collection')} {label}".strip()
            item = {**item, "url": f"https://www.lmfdb.org/api/{payload.get('collection')}/?label={label}" if label else payload.get("browseUrl", "")}
        else:
            title = str(item.get("title") or "")
        sources.append({"title": title[:500], "url": str(item.get("url") or ""), "provider": payload.get("provider", ""),
                        "authors": str(item.get("authors") or item.get("author") or "")[:500],
                        "year": str(item.get("year") or item.get("published") or "")[:4], "doi": str(item.get("doi") or "")})
    return sources
