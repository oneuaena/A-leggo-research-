"""Restricted local model adapter for the official Codex CLI.

This adapter is deliberately a model-only bridge.  Each request runs in an
empty temporary working directory, ignores the user's tool/plugin config,
disables command execution and other tool surfaces, and does not persist a
Codex session.  A-leggo remains responsible for math-tool execution and
records the returned answer and usage in its own trace.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any

logger = logging.getLogger(__name__)

MODEL_ID = "gpt-6-luna"
CLI_FEATURES_TO_DISABLE = (
    "shell_tool",
    "apps",
    "plugins",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "sleep_tool",
    "standalone_web_search",
    "multi_agent",
    "code_mode",
)
_TOOL_ITEM_TYPES = frozenset(
    {
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "tool_call",
        "web_search",
        "image_view",
        "computer_call",
    }
)


class CodexExecError(RuntimeError):
    def __init__(self, message: str, *, code: str = "codex_exec", usage: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.reported_usage = usage


def _codex_executable() -> str:
    configured = os.environ.get("CODEX_CLI_PATH", "").strip()
    candidate = configured or shutil.which("codex") or ""
    if not candidate or not Path(candidate).is_file():
        raise CodexExecError("本机未找到官方 Codex CLI。", code="not_configured")
    return candidate


def configured() -> bool:
    """Report whether the CLI and its local ChatGPT auth file are present without reading credentials."""
    try:
        _codex_executable()
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        return (codex_home / "auth.json").is_file()
    except (CodexExecError, OSError):
        return False


def build_command(*, executable: str, model: str = MODEL_ID, reasoning_effort: str = "medium") -> list[str]:
    command = [
        executable,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "-c",
        'approval_policy="never"',
        "-c",
        'model_reasoning_effort="' + reasoning_effort + '"',
    ]
    for feature in CLI_FEATURES_TO_DISABLE:
        command.extend(("--disable", feature))
    command.append("-")  # prompt is piped on stdin; never interpreted by a shell
    return command


def _prompt_from_messages(
    messages: list[dict[str, str]], *, max_output_tokens: int | None = None, json_object: bool = False
) -> str:
    sections = [
        "A-leggo is using this official Codex CLI invocation as a text-only model.\n"
        "Do not call tools, run commands, search the web, delegate, or access files.\n"
        "A-leggo itself executes and records any permitted math tools. Respond only to the messages below."
    ]
    for item in messages:
        role = str(item.get("role") or "user").upper()
        content = str(item.get("content") or "")
        sections.append(f"<{role}>\n{content}\n</{role}>")
    if json_object:
        sections.append("Return exactly one valid JSON object and no surrounding prose.")
    if max_output_tokens:
        sections.append(f"Keep the final response within approximately {int(max_output_tokens)} tokens.")
    return "\n\n".join(sections)


def _usage_from_event(event: dict[str, Any]) -> dict[str, int] | None:
    candidates: list[dict[str, Any]] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(value, dict):
            keys = set(value)
            if keys.intersection(
                {"input_tokens", "prompt_tokens", "promptTokens", "output_tokens", "completion_tokens", "completionTokens"}
            ):
                candidates.append(value)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)

    visit(event)
    if not candidates:
        return None
    raw = candidates[-1]

    def number(*keys: str) -> int:
        for key in keys:
            try:
                value = int(raw.get(key))
                if value >= 0:
                    return value
            except (TypeError, ValueError):
                pass
        return 0

    prompt = number("input_tokens", "prompt_tokens", "promptTokens")
    completion = number("output_tokens", "completion_tokens", "completionTokens")
    total = number("total_tokens", "totalTokens") or prompt + completion
    return {"promptTokens": prompt, "completionTokens": completion, "totalTokens": total}


def _minimal_environment() -> dict[str, str]:
    keep = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "HOME",
        "CODEX_HOME",
        "CODEX_CLI_PATH",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in keep}


async def complete(
    messages: list[dict[str, str]],
    *,
    timeout_seconds: float = 240,
    max_output_tokens: int | None = None,
    reasoning_effort: str = "medium",
    json_object: bool = False,
) -> dict[str, Any]:
    executable = _codex_executable()
    command = build_command(executable=executable, reasoning_effort=reasoning_effort)
    prompt = _prompt_from_messages(messages, max_output_tokens=max_output_tokens, json_object=json_object)
    started = time.monotonic()
    final_text = ""
    usage: dict[str, int] | None = None
    forbidden_tool_items: list[str] = []
    stderr_tail = bytearray()
    return_code: int | None = None

    with tempfile.TemporaryDirectory(prefix="aleggo-codex-model-") as temp_dir:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=temp_dir,
            env=_minimal_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=4 * 1024 * 1024,
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        async def drain_stderr() -> None:
            assert proc.stderr is not None
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_tail.extend(chunk)
                if len(stderr_tail) > 4096:
                    del stderr_tail[:-4096]

        stderr_task = asyncio.create_task(drain_stderr())
        deadline = started + max(1.0, float(timeout_seconds))
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                if not line:
                    break
                try:
                    event = json.loads(line.decode("utf-8", errors="replace"))
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict):
                    continue
                event_usage = _usage_from_event(event)
                if event_usage is not None:
                    usage = event_usage
                item = event.get("item") if isinstance(event.get("item"), dict) else {}
                item_type = str(item.get("type") or item.get("item_type") or "")
                event_type = str(event.get("type") or "")
                if item_type in _TOOL_ITEM_TYPES or "tool_call" in event_type or "command_execution" in event_type:
                    forbidden_tool_items.append(item_type or event_type)
                    proc.kill()
                    break
                if item_type in {"agent_message", "assistant_message"} and event_type.endswith(".completed"):
                    text = item.get("text") or item.get("message") or item.get("content")
                    if isinstance(text, str) and text.strip():
                        final_text = text.strip()
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            await stderr_task
            raise CodexExecError(
                f"GPT-6 Luna 调用超过 {int(timeout_seconds)} 秒上限。", code="timeout", usage=usage
            ) from exc
        finally:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            return_code = proc.returncode
            await stderr_task

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if forbidden_tool_items:
        logger.error("Codex model-only boundary violation: tools=%s", forbidden_tool_items)
        raise CodexExecError(
            "Codex CLI 尝试调用禁用工具；本次模型响应作废。", code="tool_forbidden", usage=usage
        )
    if return_code != 0:
        detail = bytes(stderr_tail).decode("utf-8", errors="replace").strip()
        detail = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", detail)
        detail = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", detail)
        safe_detail = detail[-600:] if detail else f"exit={return_code}"
        raise CodexExecError(f"官方 Codex CLI 调用失败：{safe_detail}", code="codex_exec", usage=usage)
    if not final_text:
        raise CodexExecError("官方 Codex CLI 没有返回可见的最终文本。", code="invalid_response", usage=usage)

    model_usage = usage or {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    logger.info(
        "Codex model call completed model=%s elapsed_ms=%d usage_reported=%s prompt_tokens=%d completion_tokens=%d forbidden_tools=%d",
        MODEL_ID,
        elapsed_ms,
        usage is not None,
        model_usage["promptTokens"],
        model_usage["completionTokens"],
        len(forbidden_tool_items),
    )
    return {
        "text": final_text,
        "usage": model_usage,
        "usage_reported": usage is not None,
        "actual_model": MODEL_ID,
        "elapsed_ms": elapsed_ms,
        "tool_calls": [],
    }


async def cli_version(timeout_seconds: float = 5) -> str:
    executable = _codex_executable()
    proc = await asyncio.create_subprocess_exec(
        executable,
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_minimal_environment(),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise CodexExecError("读取官方 Codex CLI 版本超时。", code="timeout") from exc
    if proc.returncode != 0:
        raise CodexExecError("无法读取官方 Codex CLI 版本。", code="not_configured")
    return stdout.decode("utf-8", errors="replace").strip()


__all__ = ["CodexExecError", "MODEL_ID", "build_command", "cli_version", "complete", "configured"]
