"""Shared A-leggo product settings stored with administrator settings.

Version 2 adds the shared study/research model mapping (``study_profile_id``
and ``study_model_overrides``) so every product model resolves through the
same backend model catalog the chat side configures. The built-in Atlas
application ID is intentionally **never** auto-applied: an empty
``atlas_agent_id`` means "not configured" and legacy files that only carry
the built-in default are treated the same way.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from aleggo.multi_user.paths import get_admin_path_service
from aleggo.services.file_io import atomic_write_json


# Kept only so legacy installs can be recognised; it is never re-applied as a
# configured value and never reported as "user configured".
DEFAULT_ATLAS_AGENT_ID = "0fbaf2a733d84c289fced4f698bffb67"
SETTINGS_VERSION = 2
DEFAULT_ALEGGO_SETTINGS: dict[str, Any] = {
    "version": SETTINGS_VERSION,
    "atlas_agent_id": "",
    "study_profile_id": "",
    "study_model_overrides": {},
}
_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{0,200}$")
_PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{0,200}$")
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")


def aleggo_settings_path() -> Path:
    return get_admin_path_service().get_settings_file("aleggo")


def _normalize_agent_id(value: Any) -> str:
    agent_id = str(value or "").strip()
    if agent_id == DEFAULT_ATLAS_AGENT_ID:
        # The built-in default application is not a user configuration.
        return ""
    if agent_id and not _AGENT_ID_PATTERN.fullmatch(agent_id):
        raise ValueError("Atlas Agent ID may contain letters, numbers, dots, underscores, colons, and hyphens only.")
    return agent_id


def _normalize_profile_id(value: Any) -> str:
    profile_id = str(value or "").strip()
    if profile_id and not _PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError("Study profile ID may contain letters, numbers, dots, underscores, colons, and hyphens only.")
    return profile_id


def _normalize_overrides(value: Any) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    from aleggo.study.model_registry import MODEL_REGISTRY

    overrides: dict[str, str] = {}
    for product_id, model_id in raw.items():
        key = str(product_id or "").strip()
        model = str(model_id or "").strip()
        if key not in MODEL_REGISTRY:
            continue
        if not model:
            continue
        if not _MODEL_ID_PATTERN.fullmatch(model):
            raise ValueError("Model IDs may contain letters, numbers, dots, underscores, colons, slashes, and hyphens only.")
        overrides[key] = model
    return overrides


def normalize_aleggo_settings(payload: Any, *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    merged: dict[str, Any] = dict(base or DEFAULT_ALEGGO_SETTINGS)
    merged["version"] = SETTINGS_VERSION
    if "atlas_agent_id" in raw or base is None:
        merged["atlas_agent_id"] = _normalize_agent_id(raw.get("atlas_agent_id", merged.get("atlas_agent_id", "")))
    if "study_profile_id" in raw or base is None:
        merged["study_profile_id"] = _normalize_profile_id(raw.get("study_profile_id", merged.get("study_profile_id", "")))
    if "study_model_overrides" in raw or base is None:
        merged["study_model_overrides"] = _normalize_overrides(
            raw.get("study_model_overrides", merged.get("study_model_overrides", {}))
        )
    return {
        "version": SETTINGS_VERSION,
        "atlas_agent_id": str(merged.get("atlas_agent_id") or ""),
        "study_profile_id": str(merged.get("study_profile_id") or ""),
        "study_model_overrides": dict(merged.get("study_model_overrides") or {}),
    }


def load_aleggo_settings() -> dict[str, Any]:
    path = aleggo_settings_path()
    defaults = dict(DEFAULT_ALEGGO_SETTINGS)
    if not path.exists():
        return dict(defaults)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(defaults)
    try:
        return normalize_aleggo_settings(raw, base=defaults)
    except ValueError:
        return dict(defaults)


def save_aleggo_settings(payload: Any) -> dict[str, Any]:
    """Partially update the stored settings; omitted keys keep their values."""
    current = load_aleggo_settings()
    normalized = normalize_aleggo_settings(payload if isinstance(payload, dict) else {}, base=current)
    atomic_write_json(aleggo_settings_path(), normalized)
    return normalized
