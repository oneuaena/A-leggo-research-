import pytest
import asyncio
from unittest.mock import patch

from aleggo.study.model_registry import ModelRuntimeError, _complete_message_text, complete_model
from aleggo.study import math_databases


def test_truncated_provider_output_is_not_a_completed_answer():
    with pytest.raises(ModelRuntimeError, match="输出上限") as exc:
        _complete_message_text({"choices": [{"finish_reason": "length", "message": {"content": "unfinished"}}], "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}}, model_id="research.meridian", actual_model="example")
    assert exc.value.reported_usage["totalTokens"] == 46
    assert exc.value.reported_usage["actualModel"] == "example"
    assert _complete_message_text({"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]}) == "done"


def test_discussion_limits_reach_real_provider_request():
    seen = {}
    class Response:
        is_error = False
    async def post(endpoint, api_key, body, *, timeout_seconds):
        seen.update(body=body, timeout=timeout_seconds)
        return Response(), {"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]}
    access = {"endpoint": "http://localhost/v1/chat/completions", "api_key": "test", "actual_model": "model"}
    with patch("aleggo.study.model_registry.resolve_model_access", return_value=access), patch("aleggo.study.model_registry._post_chat_completion", side_effect=post):
        result = asyncio.run(complete_model("research.meridian", [{"role": "user", "content": "x"}], timeout_seconds=900, max_output_tokens=65536))
    assert result == "done"
    assert seen["timeout"] == 900
    assert seen["body"]["max_tokens"] == 65536


def test_database_network_error_returns_tool_failure():
    import httpx
    async def offline(*args, **kwargs):
        raise httpx.ConnectError("blocked")
    math_databases._CACHE.clear()
    with patch.object(math_databases, "_get", side_effect=offline):
        result = asyncio.run(math_databases.run_database_tool("oeis_search", {"query": "1,2,3"}))
    assert result["error_type"] == "UNAVAILABLE"
    assert result["verification_status"] == "TOOL_FAILURE"
