from __future__ import annotations

from aleggo.study.codex_exec_provider import (
    CLI_FEATURES_TO_DISABLE,
    _minimal_environment,
    _prompt_from_messages,
    _usage_from_event,
    build_command,
)


def test_codex_exec_command_is_model_only_and_isolated() -> None:
    command = build_command(executable="codex.exe")
    assert command[0:3] == ["codex.exe", "exec", "--json"]
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--skip-git-repo-check" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-6-luna"
    for feature in CLI_FEATURES_TO_DISABLE:
        index = command.index(feature)
        assert command[index - 1] == "--disable"
    assert command[-1] == "-"


def test_prompt_preserves_roles_and_tool_boundary() -> None:
    prompt = _prompt_from_messages(
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Find a determinant."},
        ],
        json_object=True,
        max_output_tokens=100,
    )
    assert "Do not call tools" in prompt
    assert "<SYSTEM>\nReturn JSON." in prompt
    assert "<USER>\nFind a determinant." in prompt
    assert "exactly one valid JSON object" in prompt


def test_usage_extraction_accepts_codex_cumulative_shape() -> None:
    usage = _usage_from_event(
        {
            "type": "turn.completed",
            "info": {"last_token_usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}},
        }
    )
    assert usage == {"promptTokens": 120, "completionTokens": 30, "totalTokens": 150}


def test_child_environment_drops_provider_keys(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-propagate")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-propagate")
    env = _minimal_environment()
    assert "OPENAI_API_KEY" not in env
    assert "DEEPSEEK_API_KEY" not in env

