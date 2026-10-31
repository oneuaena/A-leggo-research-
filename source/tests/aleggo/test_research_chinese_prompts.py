"""The planning and review prompts retain the structured contract."""
import asyncio
import json
from unittest.mock import patch
from aleggo.study import research_runtime as runtime


def test_planning_passes_request_chinese_and_preserve_plan_structure():
    calls = []
    plan = {"objective": "研究命题 $n^2+n+41$", "steps": [
        {"id": f"step_{i}", "title": f"步骤{i}", "description": "验证结论", "requiredTools": ["python"]}
        for i in range(1, 5)
    ]}
    class Router:
        async def complete(self, model_id, messages, **kwargs):
            calls.append(messages)
            return {"text": json.dumps(plan, ensure_ascii=False)}
    with patch.object(runtime, "_MODEL_ROUTER", Router()):
        result = asyncio.run(runtime.generate_plan("检查命题", "", "research.axiom"))
    assert len(calls) == 2
    assert all("Simplified Chinese" in call[0]["content"] and "LaTeX" in call[0]["content"] for call in calls)
    assert all("简体中文" in call[1]["content"] and "LaTeX" in call[1]["content"] for call in calls)
    assert result["objective"] == plan["objective"]
    assert len(result["steps"]) == 4
    assert result["steps"][0]["requiredTools"] == ["python"]


def test_role_messages_request_chinese_and_latex():
    project = {"question": "证明不存在最大的素数"}
    plan = {"objective": "证明素数有无穷多个", "assumptions": ["素数定义为大于1且仅有1和自身为正因子的整数"]}
    step = {"title": "步骤 1：构造欧几里得乘积", "description": "设有限素数集合并构造 $N = p_1 p_2 \\cdots p_k + 1$", "requiredTools": []}
    messages = runtime._role_messages(project, plan, step, "proof-builder", "")
    assert "Simplified Chinese" in messages[0]["content"]
    assert "简体中文" in messages[1]["content"]
    assert "LaTeX" in messages[1]["content"]
    assert "当前结论：" in messages[1]["content"]
