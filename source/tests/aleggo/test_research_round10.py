"""Round 10 standalone: resume/stale Chinese, vision/usage robustness, no hangs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_collaborator_resume_chinese_and_hang_guard():
    src = (ROOT / "aleggo/study/research_collaborator.py").read_text(encoding="utf-8")
    assert "讨论未自动恢复" in src
    assert "已自动恢复因应用重启而中断的讨论回复" in src
    assert "48 小时" in src
    assert "RESUME_MAX_AGE_SECONDS = 48 * 3600" in src
    assert "_stale_discussion_message" in src
    assert "计算超过" in src
    assert "Discussion not resumed" not in src
    assert "Resumed discussion replies interrupted by a restart" not in src
    # Claim then start_discussion failure must not leave RUNNING forever.
    assert "尝试恢复讨论时出错" in src
    assert 'status="FAILED"' in src


def test_store_restart_messages_chinese():
    src = (ROOT / "aleggo/study/research_store.py").read_text(encoding="utf-8")
    assert "应用重启打断了进行中的研究" in src
    assert "应用重启打断了计划修订" in src
    assert "应用重启时计划尚未保存" in src
    assert "请审阅已恢复的检查点后继续" in src
    assert "App restarted during research" not in src


def test_runtime_control_interrupt_chinese():
    src = (ROOT / "aleggo/study/research_runtime.py").read_text(encoding="utf-8")
    assert "研究者已停止后续执行" in src
    assert "已保存的研究计划已变更，需重新批准" in src
    assert "Researcher control stopped further execution" not in src
    assert "The saved research plan changed" not in src


def test_thread_discussion_resumed_event():
    src = (ROOT / "aleggo/study/research_thread.py").read_text(encoding="utf-8")
    assert '"discussion_resumed": "info"' in src
    assert "_EVENT_LABELS_ZH" in src
    assert "已自动恢复中断的讨论" in src


def test_model_registry_vision_chinese_and_usage():
    from aleggo.study.model_registry import normalise_usage, summarise_usage, vision_model_setting

    src = (ROOT / "aleggo/study/model_registry.py").read_text(encoding="utf-8")
    assert "未配置视觉模型" in src
    assert "请输入有效的模型名称" in src
    assert "视觉模型返回了无效数据" in src
    assert "尚未配置模型访问凭证" in src
    assert "请为本提供商设置视觉语言模型名称" in src
    assert "No vision model is configured" not in src
    assert "Enter a valid model name" not in src

    assert normalise_usage({"prompt_tokens": "12", "completion_tokens": "3"}) == {
        "promptTokens": 12, "completionTokens": 3, "totalTokens": 15,
    }
    assert normalise_usage({"promptTokens": "1,000", "completionTokens": 2})["promptTokens"] == 1000
    assert normalise_usage({"prompt_tokens": True}) == {}  # bool rejected
    assert normalise_usage("not-a-dict") == {}

    summary = summarise_usage([
        {"promptTokens": "10", "completionTokens": 1, "totalTokens": 11, "modelId": "a"},
        "skip-me",
        {"promptTokens": 5, "completionTokens": "2", "totalTokens": 7, "modelId": "b"},
    ])
    assert summary["promptTokens"] == 15
    assert summary["completionTokens"] == 3
    assert summary["totalTokens"] == 18
    assert summary["requests"] == 2
    assert summary["modelId"] == "b"
    assert summarise_usage([]) == {}
    assert summarise_usage(["x", None]) == {}


def test_stale_discussion_helper_logic():
    """Execute the helper in isolation (overlay lacks full aleggo.study.service)."""
    src = (ROOT / "aleggo/study/research_collaborator.py").read_text(encoding="utf-8")
    # Slice from helper def through resume_pending_discussions docstring start.
    start = src.index("def _stale_discussion_message")
    end = src.index("def resume_pending_discussions")
    ns: dict = {"RESUME_MAX_AGE_SECONDS": 48 * 3600, "MAX_RESUMED_DISCUSSIONS": 5}
    exec(src[start:end], ns)  # noqa: S102 — test-only isolated helper
    fn = ns["_stale_discussion_message"]
    assert "没有有效问题内容" in fn(message="", age=0, index=0)
    assert "48 小时" in fn(message="hi", age=ns["RESUME_MAX_AGE_SECONDS"] + 1, index=0)
    assert "5" in fn(message="hi", age=0, index=5)
