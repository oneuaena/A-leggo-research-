"""Local provider settings; API keys are protected with Windows DPAPI."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import ipaddress
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlparse, urlsplit

DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_NON_CHAT_HINTS = (
    "embedding",
    "rerank",
    "tts",
    "whisper",
    "speech",
    "audio",
    "moderation",
    "bge-",
    "text-embedding",
    "dall-e",
    "flux",
    "stable-diffusion",
    "wanx",
    "cosyvoice",
    "paraformer",
)


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt(value: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Local key storage requires Windows DPAPI")
    buf = ctypes.create_string_buffer(value)
    source = _Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    dest = _Blob()
    crypt = ctypes.windll.crypt32
    if protect:
        ok = crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise OSError("Windows could not protect or unlock the API key")
    try:
        return ctypes.string_at(dest.pbData, dest.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(dest.pbData, ctypes.c_void_p))


def _path() -> Path:
    return Path(os.environ["ALEGGO_RESEARCH_HOME"]) / "provider.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".provider-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def models_endpoint_candidates(base_url: str) -> list[str]:
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        cleaned = DEFAULT_BASE
    if cleaned.endswith("/chat/completions"):
        prefix = cleaned[: -len("/chat/completions")]
        return [prefix + "/models"]
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").casefold()
    path = (parsed.path or "").rstrip("/")
    candidates: list[str] = []
    if not path:
        if host.endswith("aliyuncs.com"):
            candidates.append(cleaned + "/compatible-mode/v1/models")
        candidates.append(cleaned + "/v1/models")
        candidates.append(cleaned + "/models")
    else:
        candidates.append(cleaned + "/models")
        if not path.endswith("/v1"):
            candidates.append(cleaned + "/v1/models")
    return list(dict.fromkeys(candidates))


def extract_supported_models_from_text(message: str) -> list[str]:
    if not message:
        return []
    patterns = (
        r"supported (?:API )?model(?: name)?s? (?:are|is)[:\s]+([A-Za-z0-9._:/,\s-]+?)(?:\.\s|\.$|,\s*but\b|\s*\(|$)",
        r"available models?[:\s]+([A-Za-z0-9._:/,\s-]+?)(?:\.\s|\.$|,\s*but\b|\s*\(|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            raw = match.group(1)
            items = [
                token.strip().strip("'\"`")
                for token in re.split(r"[,，]|\band\b|\bor\b", raw)
                if token.strip().strip("'\"`")
            ]
            valid = [item for item in items if re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", item)]
            if valid:
                return list(dict.fromkeys(valid))
    return []


def choose_default_model(models: list[str]) -> str:
    cleaned = [str(m).strip() for m in models if str(m).strip()]
    if not cleaned:
        return ""
    chat_models = [
        m for m in cleaned
        if not any(hint in m.casefold() for hint in _NON_CHAT_HINTS)
    ]
    pool = chat_models or cleaned
    return pool[0]


def provider_trust_env(url: str) -> bool:
    """Keep system proxies for remote providers; connect to loopback directly."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def discover_provider_models(base_url: str, api_key: str, *, timeout: float = 8.0) -> list[str]:
    try:
        import httpx
    except Exception:
        return []
    headers: dict[str, str] = {}
    if api_key.strip():
        headers["Authorization"] = "Bearer " + api_key.strip()
    for url in models_endpoint_candidates(base_url):
        try:
            with httpx.Client(timeout=timeout, trust_env=provider_trust_env(url)) as client:
                response = client.get(url, headers=headers)
            if response.status_code != 200:
                continue
            payload = response.json()
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        items = payload.get("data")
        if not isinstance(items, list):
            items = payload.get("models")
        if not isinstance(items, list):
            continue
        extracted: list[str] = []
        for item in items:
            if isinstance(item, dict):
                mid = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
                if mid:
                    extracted.append(mid)
            elif isinstance(item, str) and item.strip():
                extracted.append(item.strip())
        if extracted:
            chat_models = [
                m for m in extracted
                if not any(hint in m.casefold() for hint in _NON_CHAT_HINTS)
            ]
            return list(dict.fromkeys(chat_models or extracted))[:100]
    return []


def read_provider() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        encrypted = str(data.get("protected_key") or "")
        key = _crypt(base64.b64decode(encrypted, validate=True), False).decode("utf-8") if encrypted else ""
        avail = data.get("available_models")
        available_models = [str(x).strip() for x in avail if str(x).strip()] if isinstance(avail, list) else []
        configured_model = str(data.get("model") or "").strip()
        model_warning = ""
        if configured_model and available_models and configured_model not in available_models:
            model_warning = f"用户配置的模型 '{configured_model}' 不在服务端检测到的可用模型列表 {available_models} 中。"
        return {
            "base_url": str(data.get("base_url") or DEFAULT_BASE),
            "api_key": key,
            "model": configured_model,
            "detected_model": str(data.get("detected_model") or "").strip(),
            "available_models": available_models,
            "model_warning": model_warning,
        }
    except FileNotFoundError:
        return {
            "base_url": DEFAULT_BASE,
            "api_key": "",
            "model": "",
            "detected_model": "",
            "available_models": [],
        }
    except Exception:
        return {
            "base_url": DEFAULT_BASE,
            "api_key": "",
            "model": "",
            "detected_model": "",
            "available_models": [],
        }


def record_detected_model(model_name: str, available: list[str] | None = None) -> None:
    clean = str(model_name or "").strip()
    if not clean:
        return
    try:
        path = _path()
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(raw, dict):
            raw = {}
        raw["detected_model"] = clean
        if available:
            avail_clean = [str(x).strip() for x in available if str(x).strip()][:100]
            raw["available_models"] = avail_clean
            configured = str(raw.get("model") or "").strip()
            if configured and configured not in avail_clean:
                import logging
                logging.getLogger(__name__).warning(
                    "Configured model '%s' is not in detected available_models %s; keeping user configuration unchanged.",
                    configured,
                    avail_clean,
                )
        _write_json_atomic(path, raw)
    except Exception:
        pass


def ensure_detected_model() -> str:
    provider = read_provider()
    if provider.get("model"):
        return str(provider["model"])
    if provider.get("detected_model"):
        return str(provider["detected_model"])
    base_url = str(provider.get("base_url") or DEFAULT_BASE)
    api_key = str(provider.get("api_key") or "")
    if not api_key and not base_url.lower().startswith("http://"):
        return ""
    discovered = discover_provider_models(base_url, api_key)
    detected = choose_default_model(discovered)
    if detected:
        record_detected_model(detected, discovered)
    return detected


def save_provider(
    base_url: str,
    api_key: str,
    clear_key: bool = False,
    model: str = "",
) -> dict[str, Any]:
    url = base_url.strip().rstrip("/") or DEFAULT_BASE
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("服务地址必须是完整的 HTTP/HTTPS 地址，且不能包含账号、查询参数或片段。")
    if len(url) > 2000 or len(api_key) > 4000 or len(model) > 200:
        raise ValueError("服务地址、密钥或模型名称过长。")
    old = read_provider()
    key = "" if clear_key else (api_key.strip() or str(old.get("api_key") or ""))
    clean_model = "" if clear_key else model.strip()
    protected = base64.b64encode(_crypt(key.encode("utf-8"), True)).decode("ascii") if key else ""
    discovered: list[str] = []
    detected = ""
    if not clear_key and (key or parsed.scheme == "http"):
        discovered = discover_provider_models(url, key)
        detected = choose_default_model(discovered)
        if not detected and url == old.get("base_url"):
            detected = str(old.get("detected_model") or "")
    _write_json_atomic(
        _path(),
        {
            "base_url": url,
            "protected_key": protected,
            "model": clean_model,
            "detected_model": detected,
            "available_models": discovered,
        },
    )
    return {
        "baseUrl": url,
        "configured": bool(key) or (not clear_key and parsed.scheme == "http"),
        "model": clean_model,
        "effectiveModel": clean_model or detected,
        "availableModels": discovered,
    }
