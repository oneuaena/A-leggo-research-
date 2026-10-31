from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from aleggo.study import standalone_config
from aleggo.study.model_registry import ModelRegistry, actual_model_id, resolve_model_access


def test_single_api_auto_detects_model_from_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALEGGO_RESEARCH_HOME", str(tmp_path))

    with patch.object(
        standalone_config,
        "discover_provider_models",
        return_value=["deepseek-flash", "deepseek-v4-pro"],
    ):
        saved = standalone_config.save_provider("https://api.deepseek.com", "sk-test-key-123456")

    assert saved["configured"] is True
    assert saved["model"] == ""
    assert saved["effectiveModel"] == "deepseek-flash"
    assert saved["availableModels"] == ["deepseek-flash", "deepseek-v4-pro"]

    # Any internal or arbitrary model id resolves to the single configured API + detected model
    for requested in ("research.lumen", "research.meridian", "research.axiom", "custom-model", ""):
        assert actual_model_id(requested) == "deepseek-flash"
        access = resolve_model_access(requested)
        assert access["actual_model"] == "deepseek-flash"
        assert access["base_url"] == "https://api.deepseek.com"
        assert access["api_key"] == "sk-test-key-123456"


def test_single_api_explicit_model_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALEGGO_RESEARCH_HOME", str(tmp_path))

    with patch.object(standalone_config, "discover_provider_models", return_value=["deepseek-flash"]):
        saved = standalone_config.save_provider(
            "https://api.deepseek.com",
            "sk-test-key-123456",
            model="deepseek-v4-pro",
        )

    assert saved["model"] == "deepseek-v4-pro"
    assert saved["effectiveModel"] == "deepseek-v4-pro"
    assert actual_model_id("research.meridian") == "deepseek-v4-pro"


def test_extract_supported_models_from_error_message(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALEGGO_RESEARCH_HOME", str(tmp_path))
    with patch.object(standalone_config, "discover_provider_models", return_value=[]):
        standalone_config.save_provider("https://api.deepseek.com", "sk-test-key-123456")

    err_msg = (
        "Invalid request: The supported API model names are deepseek-flash, deepseek-v4-pro, "
        "but you passed qwen3.8-max"
    )
    models = standalone_config.extract_supported_models_from_text(err_msg)
    assert models == ["deepseek-flash", "deepseek-v4-pro"]

    standalone_config.record_detected_model(models[0], models)
    assert actual_model_id("research.lumen") == "deepseek-flash"


def test_model_registry_accepts_any_model_name() -> None:
    registry = ModelRegistry()
    spec = registry.resolve("any-arbitrary-model-id")
    assert isinstance(spec, dict)
    assert "model_id" in spec


def test_loopback_provider_works_with_system_proxy(monkeypatch) -> None:
    import asyncio
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from aleggo.study.model_registry import _post_chat_completion

    class LocalProvider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, payload):
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.send_json({"data": [{"id": "local-test"}]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_json({"choices": [{"message": {"content": "local result"}}]})

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalProvider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        assert standalone_config.discover_provider_models(base, "", timeout=2) == ["local-test"]
        response, payload = asyncio.run(_post_chat_completion(
            base + "/chat/completions", "local", {"messages": []}, timeout_seconds=2))
        assert response.status_code == 200
        assert payload["choices"][0]["message"]["content"] == "local result"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert standalone_config.provider_trust_env("https://api.deepseek.com/v1") is True
    assert standalone_config.provider_trust_env("http://localhost:11434/v1") is False
    assert standalone_config.provider_trust_env("http://[::1]:11434/v1") is False
