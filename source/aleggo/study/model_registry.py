"""Server-only registry and provider adapters for the six A-leggo models.

The browser receives product identifiers and display labels only. Product
IDs stay stable, but the actual provider access is resolved from the same
backend model catalog the chat side configures (Settings → 模型连接):

1. the administrator study/research mapping in ``aleggo.json``
   (``study_profile_id`` + per-product ``study_model_overrides``);
2. otherwise the best matching Bailian / DashScope-compatible profile in
   the catalog (LLM service active profile first, then task);
3. otherwise the legacy shared environment key with the default endpoints.

Every credential is read at request time — no cached keys or endpoints —
so saving a new configuration affects all subsequent calls, including
running research projects.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re
import time
from typing import Any, Iterator
from urllib.parse import urlparse

# NOTE: ``httpx`` is imported lazily inside the two functions that perform real
# HTTP calls.  The model registry is also imported on code paths that never
# touch the network (evaluation harness with an injected fake router, tests),
# and those paths must keep working where the HTTP stack is not installed.
# All ``httpx.Response`` annotations are strings (``from __future__ import
# annotations`` above), so nothing else needs the top-level import.


BAILIAN_CHAT_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
BAILIAN_AGENT_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/apps/{agent_id}/completion"
DEFAULT_ATLAS_AGENT_ID = "0fbaf2a733d84c289fced4f698bffb67"
DEFAULT_COMPAT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

MODEL_REGISTRY: dict[str, dict[str, str]] = {
    "study.quill": {
        "label": "A-leggo Quill",
        "description": "Fast study support",
        "runtime": "model",
        "model_id": "qwen-flash",
    },
    "study.compass": {
        "label": "A-leggo Compass",
        "description": "Balanced study and teaching",
        "runtime": "model",
        "model_id": "qwen-plus",
    },
    "study.atlas": {
        "label": "A-leggo Atlas",
        "description": "Advanced learning agent",
        "runtime": "agent",
        "model_id": "",
    },
    "research.lumen": {
        "label": "A-leggo Lumen",
        "description": "Fast research analysis",
        "runtime": "model",
        "model_id": "deepseek-v4.1-flash",
    },
    "research.meridian": {
        "label": "A-leggo Meridian",
        "description": "Advanced mathematical research",
        "runtime": "model",
        "model_id": "qwen3.8-max",
    },
    "research.axiom": {
        "label": "A-leggo Axiom",
        "description": "Multi-agent mathematical research",
        "runtime": "multi-agent",
        "model_id": "qwen3.8-max",
    },
    "research.codex_luna": {
        "label": "GPT-6 Luna · official Codex exec",
        "description": "Local text-only Codex CLI route; A-leggo retains math-tool execution and logging",
        "runtime": "codex-exec",
        "model_id": "gpt-6-luna",
    },
}

STUDY_CHAT_MODELS = ("study.quill", "study.compass")
STUDY_PROFILE_ID = "aleggo-study-shared"

# Bindings / hosts that identify an Alibaba Cloud Bailian (DashScope) style
# OpenAI-compatible endpoint. Matching is deliberately broad so custom
# regional or mirror base URLs keep working.
_BAILIAN_BINDING_HINTS = ("bailian", "dashscope", "aliyun", "qwen", "tongyi")
_BAILIAN_HOST_SUFFIXES = ("dashscope.aliyuncs.com", ".aliyuncs.com")


class ModelRuntimeError(RuntimeError):
    """Safe-to-return provider failure without credential-bearing details."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.provider_request_id = provider_request_id


def study_profile_id_config() -> str:
    """Administrator-selected catalog profile used by all product models."""
    try:
        from aleggo.study.settings import load_aleggo_settings

        return str(load_aleggo_settings().get("study_profile_id") or "").strip()
    except Exception:
        return ""


def study_model_overrides() -> dict[str, str]:
    """Administrator per-product actual model ID overrides."""
    try:
        from aleggo.study.settings import load_aleggo_settings

        raw = load_aleggo_settings().get("study_model_overrides")
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if str(v).strip()}
    except Exception:
        return {}


def actual_model_id(product_id: str = "research.meridian") -> str:
    spec = MODEL_REGISTRY.get(product_id) or MODEL_REGISTRY["research.meridian"]
    try:
        from aleggo.study.standalone_config import ensure_detected_model, read_provider

        provider = read_provider()
        if provider.get("model"):
            return str(provider["model"]).strip()
    except Exception:
        provider = {}
    overrides = study_model_overrides()
    override = overrides.get(product_id, "").strip()
    if not override and overrides:
        override = next((v.strip() for v in overrides.values() if v.strip()), "")
    if override:
        return override
    if provider.get("detected_model"):
        return str(provider["detected_model"]).strip()
    if provider.get("api_key") or str(provider.get("base_url") or "").lower().startswith("http://"):
        try:
            from aleggo.study.standalone_config import ensure_detected_model

            detected = ensure_detected_model().strip()
            if detected:
                return detected
        except Exception:
            pass
    if product_id and product_id not in MODEL_REGISTRY and not product_id.startswith(("research.", "study.")):
        return product_id.strip()
    return spec["model_id"]


def _host_of(base_url: str) -> str:
    if not base_url:
        return ""
    return (urlparse(base_url).hostname or "").casefold()


def _is_bailian_profile(profile: dict[str, Any]) -> bool:
    binding = str(profile.get("binding") or "").casefold()
    if any(hint in binding for hint in _BAILIAN_BINDING_HINTS):
        return True
    host = _host_of(str(profile.get("base_url") or ""))
    return any(host == suffix or host.endswith(suffix) for suffix in _BAILIAN_HOST_SUFFIXES)


def iter_catalog_profiles() -> list[dict[str, Any]]:
    """Resolve the standalone application's own provider, without host-app dependencies."""
    from aleggo.study.standalone_config import read_provider
    provider = read_provider()
    base_url = str(provider.get("base_url") or "")
    api_key = str(provider.get("api_key") or "")
    if not api_key and not base_url.lower().startswith("http://"):
        return []
    return [{"id": "local-provider", "name": "独立研究端", "binding": "bailian",
             "base_url": base_url, "api_key": api_key or "local",
             "_service": "llm", "_active": True}]


def resolve_shared_profile() -> dict[str, Any] | None:
    """Resolve the active catalog profile for any OpenAI-compatible API."""
    profiles = iter_catalog_profiles()
    explicit = study_profile_id_config()
    if explicit:
        for profile in profiles:
            if str(profile.get("id") or "") == explicit:
                return profile
    for profile in profiles:
        if profile.get("_active") and str(profile.get("api_key") or "").strip():
            return profile
    for profile in profiles:
        if str(profile.get("api_key") or "").strip():
            return profile
    return None


def shared_bailian_api_key() -> str:
    """Resolve the shared API key: env first, then the model catalog."""
    configured = os.environ.get("ALEGGO_BAILIAN_API_KEY", "").strip()
    if configured:
        return configured
    profile = resolve_shared_profile()
    if profile:
        return str(profile.get("api_key") or "").strip()
    return ""


def has_shared_bailian_key() -> bool:
    return bool(shared_bailian_api_key())


def has_research_model_access() -> bool:
    """True when either the shared API provider or the restricted Codex route is configured."""
    if has_shared_bailian_key():
        return True
    try:
        from aleggo.study.codex_exec_provider import configured

        return configured()
    except Exception:
        return False


def chat_completions_url(base_url: str) -> str:
    """Join a user base URL with the chat-completions path."""
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return BAILIAN_CHAT_ENDPOINT
    if cleaned.endswith("/chat/completions"):
        return cleaned
    host = _host_of(cleaned)
    path = urlparse(cleaned).path.casefold()
    if not path or path == "/":
        if any(host == s or host.endswith(s) for s in _BAILIAN_HOST_SUFFIXES):
            return cleaned + "/compatible-mode/v1/chat/completions"
        return cleaned + "/v1/chat/completions"
    return cleaned + "/chat/completions"


def resolve_model_access(product_id: str = "research.meridian") -> dict[str, Any]:
    """Resolve credentials, base URL and the actual model for any call."""
    profile = resolve_shared_profile()
    env_key = os.environ.get("ALEGGO_BAILIAN_API_KEY", "").strip()
    if profile and str(profile.get("api_key") or "").strip():
        base_url = str(profile.get("base_url") or "").strip() or DEFAULT_COMPAT_BASE_URL
        return {
            "api_key": str(profile.get("api_key")).strip(),
            "base_url": base_url,
            "endpoint": chat_completions_url(base_url),
            "actual_model": actual_model_id(product_id),
            "profile_id": str(profile.get("id") or ""),
            "profile_name": str(profile.get("name") or ""),
            "service": str(profile.get("_service") or ""),
            "source": "catalog",
            "host": _host_of(base_url),
        }
    if env_key:
        return {
            "api_key": env_key,
            "base_url": DEFAULT_COMPAT_BASE_URL,
            "endpoint": BAILIAN_CHAT_ENDPOINT,
            "actual_model": actual_model_id(product_id),
            "profile_id": "",
            "profile_name": "",
            "service": "",
            "source": "env",
            "host": _host_of(DEFAULT_COMPAT_BASE_URL),
        }
    raise ModelRuntimeError(
        "A-leggo 模型访问尚未配置。请先在右上角「模型连接」中添加 API 地址和密钥。",
        code="not_configured",
    )


def list_available_profiles() -> list[dict[str, Any]]:
    """Sanitized catalog profiles for the admin model-mapping UI (no keys)."""
    items: list[dict[str, Any]] = []
    for profile in iter_catalog_profiles():
        items.append(
            {
                "id": str(profile.get("id") or ""),
                "name": str(profile.get("name") or ""),
                "binding": str(profile.get("binding") or ""),
                "service": str(profile.get("_service") or ""),
                "host": _host_of(str(profile.get("base_url") or "")),
                "base_url": str(profile.get("base_url") or ""),
                "has_key": bool(str(profile.get("api_key") or "").strip()),
                "active": bool(profile.get("_active")),
                "bailian_compatible": _is_bailian_profile(profile),
                "models": [
                    {"id": str(m.get("id") or ""), "model": str(m.get("model") or ""), "name": str(m.get("name") or "")}
                    for m in (profile.get("models") or [])
                    if isinstance(m, dict)
                ][:50],
            }
        )
    return items


def describe_model_access() -> dict[str, Any]:
    """Public, credential-free view of the shared model resolution."""
    profile = resolve_shared_profile()
    env_key = bool(os.environ.get("ALEGGO_BAILIAN_API_KEY", "").strip())
    credential: dict[str, Any]
    if profile and str(profile.get("api_key") or "").strip():
        base_url = str(profile.get("base_url") or "").strip() or DEFAULT_COMPAT_BASE_URL
        credential = {
            "configured": True,
            "source": "catalog",
            "host": _host_of(base_url),
            "profile_id": str(profile.get("id") or ""),
            "profile_name": str(profile.get("name") or ""),
            "service": str(profile.get("_service") or ""),
            "active": bool(profile.get("_active")),
        }
    elif env_key:
        credential = {
            "configured": True,
            "source": "env",
            "host": _host_of(DEFAULT_COMPAT_BASE_URL),
            "profile_id": "",
            "profile_name": "",
            "service": "",
            "active": False,
        }
    else:
        credential = {
            "configured": False,
            "source": "",
            "host": "",
            "profile_id": "",
            "profile_name": "",
            "service": "",
            "active": False,
        }
    overrides = study_model_overrides()
    products: dict[str, dict[str, str]] = {}
    for product_id, spec in MODEL_REGISTRY.items():
        if spec["runtime"] in {"agent", "codex-exec"}:
            continue
        override = overrides.get(product_id, "").strip()
        products[product_id] = {
            "actual_model": override or spec["model_id"],
            "model_source": "override" if override else "default",
        }
    return {
        "credential": credential,
        "products": products,
        "profile_id_setting": study_profile_id_config(),
    }


class ModelRegistry:
    """Stable product IDs and backend-only runtime configuration."""

    @staticmethod
    def resolve(model_id: str) -> dict[str, str]:
        spec = MODEL_REGISTRY.get(model_id)
        if spec is None:
            return {
                "label": model_id or "Research Model",
                "description": "Configured API model",
                "runtime": "model",
                "model_id": model_id or "research.meridian",
            }
        return spec

    @staticmethod
    def public_catalog() -> list[dict[str, str]]:
        return [
            {"id": model_id, "label": spec["label"], "description": spec["description"]}
            for model_id, spec in MODEL_REGISTRY.items()
        ]


def public_model_catalog() -> list[dict[str, str]]:
    """Return display metadata; never include the key or Atlas application ID."""
    return ModelRegistry.public_catalog()


def _api_key() -> str:
    return resolve_model_access("study.compass")["api_key"]


def atlas_agent_id() -> str:
    """User-configured Atlas application ID, or "" when not configured.

    The built-in default application ID is never reported as configured.
    """
    override = os.environ.get("ALEGGO_ATLAS_AGENT_ID", "").strip()
    if override:
        return override
    try:
        from aleggo.study.settings import load_aleggo_settings

        stored = str(load_aleggo_settings().get("atlas_agent_id") or "").strip()
        if stored and stored != DEFAULT_ATLAS_AGENT_ID:
            return stored
    except Exception:
        pass
    return ""


def atlas_agent_configured() -> bool:
    return bool(atlas_agent_id())


def study_chat_options() -> list[dict[str, Any]]:
    """Options for the regular Study chat route (Atlas uses its own Agent API)."""
    overrides = study_model_overrides()
    return [
        {
            "profile_id": STUDY_PROFILE_ID,
            "model_id": model_id,
            "profile_name": "A-leggo Study",
            "model_name": MODEL_REGISTRY[model_id]["label"],
            "model": MODEL_REGISTRY[model_id]["label"],
            "provider": "bailian",
            "provider_label": "Bailian · shared backend key",
            "is_active_default": model_id == "study.compass",
        }
        for model_id in STUDY_CHAT_MODELS
    ]


# --------------------------------------------------------------------------- #
# Token usage capture.  ``complete_model`` reports the provider's ``usage``
# block to every active :func:`capture_model_usage` scope.  Scopes hold plain
# lists so usage still arrives when the call runs in a child task (tasks copy
# the context, the list object is shared).
# --------------------------------------------------------------------------- #

_USAGE_SINKS: ContextVar[tuple[list[dict[str, Any]], ...]] = ContextVar("aleggo_model_usage_sinks", default=())


@contextmanager
def capture_model_usage() -> Iterator[list[dict[str, Any]]]:
    sink: list[dict[str, Any]] = []
    token = _USAGE_SINKS.set(_USAGE_SINKS.get() + (sink,))
    try:
        yield sink
    finally:
        _USAGE_SINKS.reset(token)


def _coerce_nonneg_int(value: Any) -> int | None:
    """Accept ints/floats and numeric strings from heterogeneous providers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value >= 0 and value == value:  # reject NaN
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            num = float(text)
        except ValueError:
            return None
        if num >= 0 and num == num:
            return int(num)
    return None


def normalise_usage(raw: Any) -> dict[str, int]:
    """OpenAI-compatible ``usage`` → ``{promptTokens, completionTokens, totalTokens}``.

    Tolerates camelCase / snake_case keys and numeric strings so a single odd
    provider payload cannot drop the whole token ledger for a research step.
    """
    if not isinstance(raw, dict):
        return {}

    def number(*keys: str) -> int:
        for key in keys:
            coerced = _coerce_nonneg_int(raw.get(key))
            if coerced is not None:
                return coerced
        return 0

    prompt = number("prompt_tokens", "input_tokens", "promptTokens")
    completion = number("completion_tokens", "output_tokens", "completionTokens")
    total = number("total_tokens", "totalTokens") or prompt + completion
    if not (prompt or completion or total):
        return {}
    return {"promptTokens": prompt, "completionTokens": completion, "totalTokens": total}


def summarise_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum usage records captured during one logical call (retries included)."""
    if not records:
        return {}
    summary: dict[str, Any] = {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0, "requests": 0}
    last: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        summary["requests"] += 1
        last = record
        for key in ("promptTokens", "completionTokens", "totalTokens"):
            coerced = _coerce_nonneg_int(record.get(key))
            if coerced is not None:
                summary[key] += coerced
    if not summary["requests"]:
        return {}
    summary["modelId"] = str(last.get("modelId") or "")
    summary["actualModel"] = str(last.get("actualModel") or "")
    return summary


def _report_usage(model_id: str, actual_model: str, payload: dict[str, Any] | None) -> None:
    sinks = _USAGE_SINKS.get()
    if not sinks or not isinstance(payload, dict):
        return
    try:
        usage = normalise_usage(payload.get("usage"))
    except Exception:
        return
    if not usage:
        return
    usage.update({"modelId": model_id, "actualModel": str(actual_model or "")})
    for sink in sinks:
        try:
            sink.append(dict(usage))
        except Exception:
            # A broken sink must not abort the model call itself.
            pass


def _clean_think_tags(text: str) -> str:
    stripped = text.strip()
    if "<think>" in stripped.casefold():
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", stripped, flags=re.IGNORECASE).strip()
        if cleaned:
            return cleaned
    return stripped


def _message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {})
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, str) and content.strip():
            return _clean_think_tags(content)
        if isinstance(content, list):
            joined = "\n".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") in {"text", "output_text", None}
            ).strip()
            if joined:
                return _clean_think_tags(joined)
        reasoning = message.get("reasoning_content", "") if isinstance(message, dict) else ""
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()
        legacy_text = first.get("text")
        if isinstance(legacy_text, str) and legacy_text.strip():
            return _clean_think_tags(legacy_text)
    output = payload.get("output")
    if isinstance(output, dict):
        text = output.get("text")
        if isinstance(text, str):
            return _clean_think_tags(text)
    raise ModelRuntimeError("The configured model returned an unsupported response.", code="invalid_response")


def _complete_message_text(payload: dict[str, Any], *, model_id: str = "", actual_model: str = "") -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict) and choices[0].get("finish_reason") == "length":
        error = ModelRuntimeError("模型达到输出上限，回复不完整。请缩小问题或重新尝试。", code="output_limit")
        usage = normalise_usage(payload.get("usage"))
        if usage:
            usage.update({"modelId": model_id, "actualModel": actual_model, "requests": 1})
            error.reported_usage = usage
        raise error
    return _message_text(payload)


_PROVIDER_REQUEST_ID_HEADERS = ("x-request-id", "request-id")


def _request_id_from(response: httpx.Response | None, payload: dict[str, Any] | None) -> str:
    if response is not None:
        for header in _PROVIDER_REQUEST_ID_HEADERS:
            value = response.headers.get(header)
            if value:
                return str(value)[:100]
    if isinstance(payload, dict):
        for key in ("request_id", "id", "requestid"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)[:100]
    return ""


def _provider_error(payload: dict[str, Any] | None, status_code: int) -> tuple[str, str]:
    """Return (code, message) from an OpenAI / DashScope style error body."""
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    if not isinstance(error, dict):
        error = payload if payload.get("code") or payload.get("message") else {}
    code = str(error.get("code") or error.get("err_code") or "").strip()
    message = str(error.get("message") or error.get("msg") or "").strip()
    return code[:80], message[:300]


_STATUS_HINTS: dict[int, tuple[str, str]] = {
    401: ("invalid_api_key", "The API key was rejected. Check the key in Settings → 模型连接."),
    403: ("insufficient_permission", "The API key has no permission for this model or region."),
    404: ("model_not_found", "The configured model does not exist for this key or region."),
    413: ("request_too_large", "The request exceeded the provider size limit."),
    429: ("rate_limited", "The provider rate limit or quota was reached. Retry later."),
}


def _classify_status(status_code: int, code: str, message: str) -> tuple[str, str]:
    if status_code in _STATUS_HINTS:
        hint_code, hint_message = _STATUS_HINTS[status_code]
        if code in {"", "invalid_api_key"} and "api key" in message.casefold():
            return "invalid_api_key", message
        return hint_code, message or hint_message
    if status_code >= 500:
        return "provider_error", message or "The provider had an internal error. Retry later."
    return "provider_error", message or "The model provider returned an error."


def _completion_body(
    spec: dict[str, str],
    messages: list[dict[str, str]],
    *,
    temperature: float,
    json_object: bool,
    actual_model: str | None = None,
) -> dict[str, Any]:
    model_name = actual_model or spec["model_id"]
    body: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if model_name.startswith("deepseek-v4"):
        body["temperature"] = 1.0
    if json_object:
        body["response_format"] = {"type": "json_object"}
    return body


def _resolve_fallback_model(access: dict[str, Any], current_model: str, error_message: str) -> str:
    try:
        from aleggo.study.standalone_config import (
            choose_default_model,
            discover_provider_models,
            extract_supported_models_from_text,
            record_detected_model,
        )

        candidates = extract_supported_models_from_text(error_message)
        if not candidates:
            candidates = discover_provider_models(str(access.get("base_url") or ""), str(access.get("api_key") or ""))
        remaining = [m for m in candidates if m != current_model]
        chosen = choose_default_model(remaining or candidates)
        if chosen and chosen != current_model:
            record_detected_model(chosen, candidates)
            return chosen
    except Exception:
        pass
    return ""


# Statuses worth one more try: rate-limited or a transient provider failure.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_CHAT_MAX_ATTEMPTS = 3


def _retry_delay_seconds(attempt: int, response: "httpx.Response | None") -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), 30.0))
            except ValueError:
                pass
    return min(2.0 ** attempt, 8.0)


async def _post_chat_completion(
    endpoint: str,
    api_key: str,
    body: dict[str, Any],
    *,
    timeout_seconds: float,
) -> tuple[httpx.Response, dict[str, Any] | None]:
    # Transient provider failures (rate limit / 5xx) get a bounded number of
    # retries with exponential backoff; a long literature/review/eval run
    # should not die on one throttled request.
    import httpx

    from aleggo.study.standalone_config import provider_trust_env

    last_response: httpx.Response | None = None
    for attempt in range(_CHAT_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=provider_trust_env(endpoint)) as client:
                last_response = await client.post(
                    endpoint,
                    headers={"Authorization": "Bearer " + api_key},
                    json=body,
                )
        except (httpx.TimeoutException, TimeoutError) as exc:
            if attempt == _CHAT_MAX_ATTEMPTS - 1:
                raise ModelRuntimeError("模型请求超时。", code="timeout") from exc
            await asyncio.sleep(2.0 ** attempt)
            continue
        except (httpx.NetworkError, Exception) as exc:
            if attempt == _CHAT_MAX_ATTEMPTS - 1:
                raise ModelRuntimeError(f"无法连接到模型服务商: {exc}", code="network") from exc
            await asyncio.sleep(2.0 ** attempt)
            continue
        if last_response.status_code not in _RETRYABLE_STATUS or attempt == _CHAT_MAX_ATTEMPTS - 1:
            break
        await asyncio.sleep(_retry_delay_seconds(attempt, last_response))
    assert last_response is not None
    try:
        payload = last_response.json()
    except (json.JSONDecodeError, ValueError):
        payload = None
    return last_response, payload if isinstance(payload, dict) else None


def _raise_for_status(response: httpx.Response, payload: dict[str, Any] | None) -> None:
    if not response.is_error:
        return
    request_id = _request_id_from(response, payload)
    code, message = _provider_error(payload, response.status_code)
    hint_code, hint_message = _classify_status(response.status_code, code, message)
    raise ModelRuntimeError(
        hint_message,
        status_code=response.status_code,
        code=hint_code,
        provider_request_id=request_id,
    )


async def complete_model(
    model_id: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    timeout_seconds: float = 240,
    json_object: bool = False,
    max_output_tokens: int | None = None,
) -> str:
    spec = MODEL_REGISTRY.get(model_id) or MODEL_REGISTRY["research.meridian"]
    if spec["runtime"] == "agent":
        raise ModelRuntimeError("This model cannot use the chat-completion runtime.")
    if spec["runtime"] == "codex-exec":
        from aleggo.study import codex_exec_provider

        try:
            result = await codex_exec_provider.complete(
                messages,
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
                json_object=json_object,
            )
        except codex_exec_provider.CodexExecError as exc:
            error = ModelRuntimeError(str(exc), code=exc.code)
            if exc.reported_usage:
                setattr(
                    error,
                    "reported_usage",
                    {
                        **exc.reported_usage,
                        "requests": 1,
                        "modelId": model_id,
                        "actualModel": spec["model_id"],
                    },
                )
            raise error from exc
        usage_record = {
            **result["usage"],
            "modelId": model_id,
            "actualModel": result["actual_model"],
            "tokenUsageAvailable": bool(result["usage_reported"]),
        }
        for sink in _USAGE_SINKS.get():
            sink.append(dict(usage_record))
        return str(result["text"])
    access = resolve_model_access(model_id)
    body = _completion_body(
        spec,
        messages,
        temperature=temperature,
        json_object=json_object,
        actual_model=access["actual_model"],
    )
    if max_output_tokens is not None:
        body["max_tokens"] = max(1, min(int(max_output_tokens), 65536))
    response, payload = await _post_chat_completion(
        access["endpoint"],
        access["api_key"],
        body,
        timeout_seconds=timeout_seconds,
    )
    if response.is_error and response.status_code in {400, 404, 422}:
        _, err_message = _provider_error(payload, response.status_code)
        fallback_model = _resolve_fallback_model(access, str(body.get("model") or ""), err_message)
        if fallback_model:
            access["actual_model"] = fallback_model
            body["model"] = fallback_model
            response, payload = await _post_chat_completion(
                access["endpoint"],
                access["api_key"],
                body,
                timeout_seconds=timeout_seconds,
            )
    if response.is_error and response.status_code in {400, 422} and "response_format" in body:
        code, message = _provider_error(payload, response.status_code)
        # Some OpenAI-compatible endpoints reject response_format; retry once
        # without it so JSON-mode callers degrade instead of failing hard.
        retry_body = {k: v for k, v in body.items() if k != "response_format"}
        response, payload = await _post_chat_completion(
            access["endpoint"],
            access["api_key"],
            retry_body,
            timeout_seconds=timeout_seconds,
        )
        if response.is_error and response.status_code in {400, 422} and "temperature" in retry_body:
            retry_body_no_temp = {k: v for k, v in retry_body.items() if k != "temperature"}
            response, payload = await _post_chat_completion(
                access["endpoint"],
                access["api_key"],
                retry_body_no_temp,
                timeout_seconds=timeout_seconds,
            )
        if not response.is_error:
            payload = payload if isinstance(payload, dict) else {}
            _report_usage(model_id, access["actual_model"], payload)
            return _complete_message_text(payload, model_id=model_id, actual_model=access["actual_model"])
        _raise_for_status(response, payload)
        raise ModelRuntimeError(
            message or "The configured model rejected the request parameters.",
            status_code=response.status_code,
            code=code or "unsupported_parameter",
            provider_request_id=_request_id_from(response, payload),
        )
    if response.is_error and response.status_code in {400, 422} and "temperature" in body:
        retry_body_no_temp = {k: v for k, v in body.items() if k != "temperature"}
        response, payload = await _post_chat_completion(
            access["endpoint"],
            access["api_key"],
            retry_body_no_temp,
            timeout_seconds=timeout_seconds,
        )
    _raise_for_status(response, payload)
    try:
        if payload is None:
            raise json.JSONDecodeError("empty", "null", 0)
        _report_usage(model_id, access["actual_model"], payload)
        return _complete_message_text(payload, model_id=model_id, actual_model=access["actual_model"])
    except json.JSONDecodeError as exc:
        raise ModelRuntimeError(
            "The model provider returned invalid data.",
            code="invalid_response",
            provider_request_id=_request_id_from(response, payload),
        ) from exc


# --------------------------------------------------------------------------- #
# Vision (image → Markdown/LaTeX) for handwritten notes and formula pages.
# --------------------------------------------------------------------------- #

DEFAULT_DASHSCOPE_VISION_MODEL = "qwen-vl-max"


def _vision_config_path() -> Any:
    from aleggo.study.lean_formal import tools_home

    return tools_home() / "vision-config.json"


def vision_model_setting() -> dict[str, Any]:
    """Which vision-language model transcribes images, and where that comes from.

    ``ALEGGO_VISION_MODEL`` wins; then the saved setting; then ``qwen-vl-max``
    when the configured provider is Alibaba Cloud Bailian (DashScope).  Other
    providers need an explicit model name because there is no portable default.
    """
    configured = os.environ.get("ALEGGO_VISION_MODEL", "").strip()
    if configured:
        return {"model": configured, "source": "env"}
    try:
        saved = json.loads(_vision_config_path().read_text(encoding="utf-8"))
        if isinstance(saved, dict) and str(saved.get("model") or "").strip():
            return {"model": str(saved["model"]).strip()[:120], "source": "settings"}
    except (OSError, ValueError):
        pass
    try:
        access = resolve_model_access("research.meridian")
    except ModelRuntimeError:
        return {"model": "", "source": "unconfigured", "reason": "尚未配置模型访问凭证。"}
    if "dashscope" in str(access.get("host") or access.get("base_url") or "") or "aliyuncs" in str(access.get("base_url") or ""):
        return {"model": DEFAULT_DASHSCOPE_VISION_MODEL, "source": "default"}
    if access.get("actual_model"):
        return {"model": str(access["actual_model"]), "source": "default"}
    return {"model": "", "source": "unconfigured",
            "reason": "请为本提供商设置视觉语言模型名称（研究工具 → 导入）。"}


def save_vision_model_setting(model: str) -> dict[str, Any]:
    clean = str(model or "").strip()
    if clean and not re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", clean):
        raise ValueError("请输入有效的模型名称。")
    path = _vision_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": clean}, ensure_ascii=False), encoding="utf-8")
    return vision_model_setting()


async def complete_vision(
    prompt: str,
    images: list[tuple[str, bytes]],
    *,
    timeout_seconds: float = 180,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Send text + images (``(mime, bytes)``) to the configured vision model.

    Uses the same provider credentials as the research models and the
    OpenAI-compatible ``image_url`` data-URI message format.
    """
    import base64

    setting = vision_model_setting()
    if not setting.get("model"):
        raise ModelRuntimeError(str(setting.get("reason") or "未配置视觉模型。"), code="not_configured")
    access = resolve_model_access("research.meridian")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for mime, data in images:
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")}})
    body = {"model": setting["model"], "messages": [{"role": "user", "content": content}],
            "temperature": temperature, "stream": False}
    response, payload = await _post_chat_completion(access["endpoint"], access["api_key"], body, timeout_seconds=timeout_seconds)
    _raise_for_status(response, payload)
    if payload is None:
        raise ModelRuntimeError("视觉模型返回了无效数据。", code="invalid_response")
    _report_usage("vision:" + setting["model"], setting["model"], payload)
    return {"text": _message_text(payload), "model": setting["model"], "usage": normalise_usage(payload.get("usage"))}


async def complete_atlas(prompt: str, *, session_id: str | None = None) -> dict[str, Any]:
    """Call the user-configured Bailian Agent application (optional feature).

    Resolution order: the user-owned Agent connection (Settings → A-leggo
    连接) when ready, else the administrator Atlas Agent ID with the shared
    key. The built-in default application is never used automatically.
    """
    # The standalone product uses the configured agent ID and its own provider key.
    agent_id = atlas_agent_id()
    if not agent_id:
        raise ModelRuntimeError(
            "Atlas 智能体 ID 尚未配置。请在「设置 → A-leggo 连接」中添加百炼应用，或在数学研究设置中填写 Atlas 应用 ID。",
            code="atlas_not_configured",
        )
    endpoint = BAILIAN_AGENT_ENDPOINT.format(agent_id=agent_id)
    body: dict[str, Any] = {"input": {"prompt": prompt}, "parameters": {}, "debug": {}}
    if session_id:
        body["input"]["session_id"] = session_id
    import httpx  # lazy: see the module docstring note

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": "Bearer " + _api_key()},
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise ModelRuntimeError("The Atlas agent request timed out.", code="timeout") from exc
    except httpx.NetworkError as exc:
        raise ModelRuntimeError("The Atlas agent could not be reached.", code="network") from exc
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        payload = None
    _raise_for_status(response, payload if isinstance(payload, dict) else None)
    data = payload if isinstance(payload, dict) else {}
    output = data.get("output")
    output = output if isinstance(output, dict) else {}
    try:
        text = _message_text(data)
    except ModelRuntimeError as exc:
        raise ModelRuntimeError(
            "The Atlas agent returned invalid data.",
            code="invalid_response",
            provider_request_id=_request_id_from(response, data),
        ) from exc
    return {"text": text, "session_id": output.get("session_id") or session_id}


async def test_model_access(
    product_id: str = "research.meridian",
    *,
    timeout_seconds: float = 45,
) -> dict[str, Any]:
    """Real, minimal provider request used by the Settings connection test."""
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    spec = MODEL_REGISTRY.get(product_id) or MODEL_REGISTRY["research.meridian"]
    if spec["runtime"] == "agent":
        return {"ok": False, "message": "Unknown A-leggo model.", "latency_ms": elapsed_ms()}
    if spec["runtime"] == "codex-exec":
        try:
            from aleggo.study import codex_exec_provider

            version = await codex_exec_provider.cli_version(timeout_seconds=min(timeout_seconds, 10))
            with capture_model_usage() as usage:
                text = await complete_model(
                    product_id,
                    [{"role": "user", "content": "Reply with the single word: OK"}],
                    temperature=0.0,
                    timeout_seconds=timeout_seconds,
                    max_output_tokens=32,
                )
            return {
                "ok": text.strip().strip("`.* ").upper() == "OK",
                "message": "连接成功：官方 Codex CLI 已返回真实响应。" if text else "Codex CLI 返回空响应。",
                "actual_model": spec["model_id"],
                "host": "local official codex exec",
                "profile_id": "",
                "source": "codex_chatgpt_auth",
                "codex_cli_version": version,
                "token_usage_reported": any(bool(item.get("tokenUsageAvailable")) for item in usage),
                "usage": summarise_usage(usage),
                "latency_ms": elapsed_ms(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": str(exc),
                "code": getattr(exc, "code", "codex_exec"),
                "actual_model": spec["model_id"],
                "host": "local official codex exec",
                "latency_ms": elapsed_ms(),
            }
    try:
        access = resolve_model_access(product_id)
    except ModelRuntimeError as exc:
        return {"ok": False, "message": str(exc), "code": exc.code, "latency_ms": elapsed_ms()}
    body = _completion_body(
        spec,
        [{"role": "user", "content": "Reply with the single word: OK"}],
        temperature=0.0,
        json_object=False,
        actual_model=access["actual_model"],
    )
    try:
        response, payload = await _post_chat_completion(
            access["endpoint"],
            access["api_key"],
            body,
            timeout_seconds=timeout_seconds,
        )
        if response.is_error and response.status_code in {400, 404, 422}:
            _, err_message = _provider_error(payload, response.status_code)
            fallback_model = _resolve_fallback_model(access, str(body.get("model") or ""), err_message)
            if fallback_model:
                access["actual_model"] = fallback_model
                body["model"] = fallback_model
                response, payload = await _post_chat_completion(
                    access["endpoint"],
                    access["api_key"],
                    body,
                    timeout_seconds=timeout_seconds,
                )
        if response.is_error and response.status_code in {400, 422} and "temperature" in body:
            retry_body_no_temp = {k: v for k, v in body.items() if k != "temperature"}
            response, payload = await _post_chat_completion(
                access["endpoint"],
                access["api_key"],
                retry_body_no_temp,
                timeout_seconds=timeout_seconds,
            )
    except ModelRuntimeError as exc:
        return {
            "ok": False,
            "message": str(exc),
            "code": exc.code,
            "status_code": exc.status_code,
            "provider_request_id": exc.provider_request_id,
            "actual_model": access["actual_model"],
            "host": access["host"],
            "latency_ms": elapsed_ms(),
        }
    try:
        _raise_for_status(response, payload)
        text = _message_text(payload if isinstance(payload, dict) else {})
    except ModelRuntimeError as exc:
        return {
            "ok": False,
            "message": str(exc),
            "code": exc.code,
            "status_code": exc.status_code,
            "provider_request_id": exc.provider_request_id,
            "actual_model": access["actual_model"],
            "host": access["host"],
            "latency_ms": elapsed_ms(),
        }
    return {
        "ok": True,
        "message": "连接成功：模型已返回真实响应。" if text else "连接成功。",
        "actual_model": access["actual_model"],
        "host": access["host"],
        "profile_id": access["profile_id"],
        "source": access["source"],
        "provider_request_id": _request_id_from(response, payload if isinstance(payload, dict) else None),
        "latency_ms": elapsed_ms(),
    }


class ProviderAdapter:
    """Provider-side adapter. It resolves credentials per call."""

    async def complete(self, model_id: str, messages: list[dict[str, str]], **options: Any) -> str:
        return await complete_model(model_id, messages, **options)


class AgentRuntimeAdapter:
    """Adapter for a user-configured Alibaba Cloud Agent application."""

    async def complete(self, prompt: str, *, session_id: str | None = None) -> dict[str, Any]:
        return await complete_atlas(prompt, session_id=session_id)


class ModelRouter:
    """Dispatch logical product IDs without provider details in the UI."""

    def __init__(self) -> None:
        self.provider = ProviderAdapter()
        self.agent = AgentRuntimeAdapter()

    async def complete(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        *,
        session_id: str | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        spec = ModelRegistry.resolve(model_id)
        if spec["runtime"] == "agent":
            prompt = "\n\n".join(item.get("content", "") for item in messages if item.get("role") == "user")
            return await self.agent.complete(prompt, session_id=session_id)
        with capture_model_usage() as usage:
            text = await self.provider.complete(model_id, messages, **options)
        result: dict[str, Any] = {"text": text, "session_id": session_id}
        if usage:
            result["usage"] = summarise_usage(usage)
        return result
