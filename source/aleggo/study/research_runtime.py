"""Research planning and plan-bound multi-agent execution runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import re
import shutil
import sys
import time
from typing import Any

from aleggo.study.model_registry import (
    MODEL_REGISTRY,
    ModelRuntimeError,
    ModelRouter,
    capture_model_usage,
    summarise_usage,
)
from aleggo.study.math_tools import run_math_tool
from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_store import ResearchStore
from aleggo.study.research_workbench import ResearchWorkbench, evidence_classification
from aleggo.study.research_tool_catalog import (
    canonical_requirement, executable_requirements,
    canonical_worker_invocation, plan_tool_vocabulary, planner_tool_reference, worker_tool_allowed,
    CAS_CAPABILITIES, WORKER_CAPABILITIES,
)
from aleggo.study.research_inputs import ResearchInputs, normalize_input_requests


_RUNS: dict[str, asyncio.Task[None]] = {}
_RUNS_LOCK = asyncio.Lock()
_MODEL_ROUTER = ModelRouter()
_LITERATURE_TOOLS = {"literature", "literature search", "paper_search", "paper search", "academic search", "arxiv", "crossref", "openalex", "semantic scholar", "doi metadata"}


class _ResearchControlInterrupt(Exception):
    """A researcher control or approved-plan change stopped the current worker."""


class _ResearchBoundary(Exception):
    def __init__(self, reason: str, detail: str, blocker_type: str = "") -> None:
        super().__init__(detail)
        self.reason, self.detail, self.blocker_type = reason, detail, blocker_type or reason


def _guard_execution(project: dict[str, Any], store: ResearchStore, step: dict[str, Any] | None = None) -> None:
    # Helpers can be exercised independently without launching a project. Every
    # live project execution sets this server-owned flag before calling them.
    if not project.get("_guarded"):
        return
    pending_inputs = ResearchInputs(store).pending(project["id"])
    if pending_inputs:
        raise _ResearchBoundary("BLOCKED", "需要补充必要资料：" + "；".join(item["label"] for item in pending_inputs), "MISSING_INPUT")
    timed_step = project.get("_timedStep")
    if timed_step:
        workbench = ResearchWorkbench(store)
        timed_checkpoint = workbench.checkpoint(project["id"], project["currentVersion"], timed_step)
        if timed_checkpoint and timed_checkpoint["status"] != "COMPLETED":
            resources = dict(timed_checkpoint["resources"])
            resources["elapsedSeconds"] = project.get("_stepElapsedBaseline", 0) + time.monotonic() - project["_stepStarted"]
            workbench.save_checkpoint(project["id"], project["currentVersion"], timed_step, resources=resources)
    live = store.get_project(project["id"])
    if not live or live["status"] not in {"RUNNING", "VERIFYING"}:
        raise _ResearchControlInterrupt("研究者已停止后续执行。")
    if live["approvedVersion"] != project["approvedVersion"] or live["currentVersion"] != project["currentVersion"]:
        raise _ResearchControlInterrupt("已保存的研究计划已变更，需重新批准。")
    if step:
        workbench = ResearchWorkbench(store)
        checkpoint = workbench.checkpoint(project["id"], project["currentVersion"], str(step["id"]))
        if checkpoint:
            if project.get("_timedStep") != step["id"]:
                project["_timedStep"] = step["id"]
                project["_stepStarted"] = time.monotonic()
                project["_stepElapsedBaseline"] = checkpoint["resources"].get("elapsedSeconds", 0)
            resources = dict(checkpoint["resources"])
            if resources.get("activeSegmentStartedAt") != project.get("_stepStartedAt"):
                project["_stepStartedAt"] = datetime.now(UTC).isoformat()
                resources["activeSegmentStartedAt"] = project["_stepStartedAt"]
                resources["activeSegmentBaseline"] = project["_stepElapsedBaseline"]
            resources["elapsedSeconds"] = project["_stepElapsedBaseline"] + time.monotonic() - project["_stepStarted"]
            workbench.save_checkpoint(project["id"], project["currentVersion"], str(step["id"]), resources=resources)
        verdict = workbench.evaluate_budget(project["id"], project["currentVersion"], str(step["id"]))
        if verdict["state"] == "STALLED":
            raise _ResearchBoundary("STALLED", verdict["reason"], "BUDGET_NO_PROGRESS")


def _live_phase(project: dict[str, Any], step: dict[str, Any], store: ResearchStore, phase: str, action: str,
                *, agent: str = "", tool: str = "", active_agents: list[str] | None = None) -> None:
    if not project.get("_guarded"):
        return
    _guard_execution(project, store)
    workbench = ResearchWorkbench(store)
    previous = workbench.state(project["id"])
    workbench.set_live(project["id"], step_id=step["id"], agent=agent,
                       action=action[:2000], branch=str(step.get("branch") or "main"),
                       milestone=str(step.get("milestone") or previous["currentMilestone"]),
                       reason="VERIFYING" if (store.get_project(project["id"]) or {}).get("status") == "VERIFYING" else "RUNNING",
                       tool=tool, phase=phase, active_agents=active_agents or (project.get("_activeAgents") if phase == "agent_execution" else None) or ([agent] if agent else []))


def _cached_call(project: dict[str, Any], step: dict[str, Any], store: ResearchStore, digest: str) -> str | None:
    checkpoint = ResearchWorkbench(store).checkpoint(project["id"], project["currentVersion"], step["id"])
    value = (checkpoint or {}).get("payload", {}).get("calls", {}).get(digest)
    return str(value["output"]) if isinstance(value, dict) and value.get("status") == "COMPLETED" else None


def _cache_call(project: dict[str, Any], step: dict[str, Any], store: ResearchStore, digest: str, output: str) -> None:
    workbench = ResearchWorkbench(store)
    current = workbench.checkpoint(project["id"], project["currentVersion"], step["id"]) or {}
    calls = {**current.get("payload", {}).get("calls", {}), digest: {"output": output, "status": "COMPLETED"}}
    workbench.save_checkpoint(project["id"], project["currentVersion"], step["id"], payload={"calls": calls})

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "inputRequests": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "reason": {"type": "string"}, "kind": {"enum": ["text", "document"]}}, "required": ["label", "reason", "kind"]}},
        "objective": {"type": "string"},
        "problemFormalization": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "definitions": {"type": "array", "items": {"type": "string"}},
        "keyQuestions": {"type": "array", "items": {"type": "string"}},
        "proposedLemmas": {"type": "array", "items": {"type": "string"}},
        "researchBranches": {"type": "array", "items": {"type": "string"}},
        "suggestedExperiments": {"type": "array", "items": {"type": "string"}},
        "proofStrategy": {"type": "string"},
        "counterexampleStrategy": {"type": "string"},
        "toolsRequired": {"type": "array", "items": {"type": "string"}},
        "verificationStrategy": {"type": "string"},
        "expectedOutputs": {"type": "array", "items": {"type": "string"}},
        "riskUncertainty": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "integer"},
                    "branch": {"type": "string"},
                    "dependencies": {"type": "array", "items": {"type": "string"}},
                    "assignedAgent": {"type": "string"},
                    "requiredTools": {"type": "array", "items": {"type": "string"}},
                    "expectedOutput": {"type": "string"},
                    "verificationMethod": {"type": "string"},
                    "milestone": {"type": "string"},
                },
                "required": ["title", "description"],
            },
        },
    },
    "required": [
        "objective", "problemFormalization", "assumptions", "definitions",
        "keyQuestions", "proposedLemmas", "researchBranches",
        "suggestedExperiments", "proofStrategy", "counterexampleStrategy",
        "toolsRequired", "verificationStrategy", "expectedOutputs",
        "riskUncertainty", "steps",
    ],
}

_PLANNER_SYSTEM = (
    "You are the research planner in A-leggo Math Research. Produce a proposed plan only. "
    "Do not execute research, assert an unverified theorem, or silently approve the plan. "
    "Return valid JSON matching the requested schema. Write concise, testable steps with "
    "dependencies and verification methods. Keep conjectures explicitly unverified. "
    "Write all human-readable plan text in Simplified Chinese, with mathematics in LaTeX using $...$ or $$...$$. "
    "请全部使用简体中文撰写所有面向研究者的文本字段（包括研究目标、问题形式化、假设、定义、关键问题、引理、策略及每个步骤的标题、说明、预期产出、验证方法与阶段目标），英文专业术语可以括注；所有数学表达式、变量、集合与同余式一律使用 LaTeX（行内 $...$，独立公式 $$...$$），不要输出英文句子。 "
    "Keep JSON keys, status values, role names and tool identifiers unchanged. "
    "If essential definitions, referenced papers, data or constraints are missing, return an inputRequests array "
    "of objects with label, reason and kind (text or document). Use kind=document for an original "
    "paper or dataset, kind=text for definitions or constraints. Do not invent them or continue planning. "
    "For a self-contained mathematical question use inputRequests: [] and do not demand optional uploads. "
    + plan_tool_vocabulary()
)

_FORMALIZER_SYSTEM = (
    "You are the mathematical problem formalizer in A-leggo Math Research. Refine the "
    "proposed plan for definitions, assumptions, quantifiers, edge cases, and dependency "
    "order. Preserve unresolved inputRequests from the planner; do not invent missing sources. "
    "Write human-readable plan text in Simplified Chinese and mathematics in LaTeX using $...$ or $$...$$; "
    "请全部使用简体中文输出所有面向研究者的计划字段与步骤说明（英文专业术语可以括注），所有数学公式与符号一律使用 LaTeX（行内 $...$，独立公式 $$...$$）；"
    "preserve JSON keys, status values, role names and tool identifiers. "
    "Return only valid JSON matching the same plan schema. Do not start execution."
)

_ROLE_SYSTEM = {
    # These role names match the role assignments in Math Research Agent's STAGE_ROLE map.
    "research-planner": "Clarify the goal, assumptions, dependencies, and next research action. Do not change the approved plan. 请用简体中文输出（英文术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
    "explorer": "Explore the mathematical structure and plausible proof routes. Label conjectures and uncertainty. 请用简体中文输出（英文术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
    "proof-builder": "Develop a proof attempt for the approved step. State each obligation and leave gaps explicit. 请用简体中文输出（英文术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
    "experimental-mathematician": "Design or analyze a bounded experiment or counterexample search. Do not claim machine verification without an executed tool result. 请用简体中文输出（英文术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
    "lemma-generator": "Find useful intermediate claims, symbolic identities, and dependency lemmas that could make the current approved proof route tractable. Label each as a proposal until checked. 请用简体中文输出（英文术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
    "independent-verifier": "Independently check the supplied argument for gaps, counterexamples, and unsupported conclusions. Do not promote it to verified. 请用简体中文输出（英文术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
    "research-skeptic": "Try to falsify the proposed reasoning, identify hidden assumptions and edge cases, and state what evidence would change your assessment. Do not claim formal verification. 请用简体中文输出（英文术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
    "research-synthesizer": "Summarize the recorded work, evidence types, remaining uncertainty, and proposed next steps without changing the plan. 请用简体中文输出（英文术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
}

_ASSIGNMENT_ALIASES = {
    "formalizer": "research-planner",
    "planner": "research-planner",
    "proof explorer": "proof-builder",
    "proof-explorer": "proof-builder",
    "proof builder": "proof-builder",
    "lemma generator": "lemma-generator",
    "symbolic analyst": "lemma-generator",
    "counterexample hunter": "experimental-mathematician",
    "counterexample-hunter": "experimental-mathematician",
    "experimentalist": "experimental-mathematician",
    "skeptic": "research-skeptic",
    "symbolic-analyst": "lemma-generator",
    "critic": "independent-verifier",
    "verifier": "independent-verifier",
    "synthesizer": "research-synthesizer",
}


def _extract_json(content: str) -> dict[str, Any]:
    value = content.strip()
    if "<think>" in value.casefold():
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", value, flags=re.IGNORECASE).strip()
        if cleaned:
            value = cleaned
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        left, right = value.find("{"), value.rfind("}")
        if left < 0 or right <= left:
            raise ModelRuntimeError("The model did not return a valid research plan.")
        try:
            parsed = json.loads(value[left : right + 1])
        except json.JSONDecodeError as exc:
            raise ModelRuntimeError("The model did not return a valid research plan.") from exc
    if not isinstance(parsed, dict):
        raise ModelRuntimeError("The model did not return a valid research plan.")
    return parsed


def _normalise_plan(raw: dict[str, Any]) -> dict[str, Any]:
    input_requests = normalize_input_requests(raw.get("inputRequests"))
    if input_requests:
        return {"inputRequests": input_requests, "_needsInput": True}
    fields = {
        "inputRequests": input_requests,
        "objective": "",
        "problemFormalization": "",
        "assumptions": [],
        "definitions": [],
        "keyQuestions": [],
        "proposedLemmas": [],
        "researchBranches": [],
        "suggestedExperiments": [],
        "proofStrategy": "",
        "counterexampleStrategy": "",
        "toolsRequired": [],
        "verificationStrategy": "",
        "expectedOutputs": [],
        "riskUncertainty": "",
    }
    plan = {key: raw.get(key, default) for key, default in fields.items()}
    plan["inputRequests"] = input_requests
    steps = raw.get("steps", [])
    if not isinstance(steps, list) or not steps:
        raise ModelRuntimeError("The model returned a plan without research steps.")
    candidate_steps = [
        item for item in steps[:40]
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    ]
    candidate_titles = [str(item.get("title", "")).strip() for item in candidate_steps]
    candidate_ids = [
        "step_" + str(index + 1) + "_" + str(abs(hash(title)) % 100000000)
        for index, title in enumerate(candidate_titles)
    ]

    def resolve_dependency(value: Any, current_index: int) -> str | None:
        reference = str(value).strip()
        if reference in candidate_ids:
            target = candidate_ids.index(reference)
        else:
            title_match = next(
                (index for index, title in enumerate(candidate_titles) if title.casefold() == reference.casefold()),
                None,
            )
            number_match = re.fullmatch(r"(?:step\s*)?(\d+)", reference, flags=re.IGNORECASE)
            target = title_match if title_match is not None else int(number_match.group(1)) - 1 if number_match else None
        if target is None or target < 0 or target >= current_index:
            return None
        return candidate_ids[target]

    normalised_steps = []
    for index, item in enumerate(candidate_steps):
        title = str(item.get("title", "")).strip()
        dependencies = []
        unresolved_dependencies = []
        for value in item.get("dependencies", [])[:30]:
            dependency_id = resolve_dependency(value, index)
            if dependency_id:
                dependencies.append(dependency_id)
            elif str(value).strip():
                unresolved_dependencies.append(str(value).strip())
        normalised_steps.append(
            {
                "id": candidate_ids[index],
                "title": title[:240],
                "description": str(item.get("description", "")).strip()[:8000],
                "status": "pending",
                "order": index,
                "priority": max(1, min(5, int(item.get("priority", 3)))),
                "enabled": True,
                "branch": str(item.get("branch", "main"))[:200],
                "dependencies": dependencies,
                "assignedAgent": str(item.get("assignedAgent", "explorer"))[:80],
                "requiredTools": [str(value) for value in item.get("requiredTools", [])][:20],
                "expectedOutput": str(item.get("expectedOutput", ""))[:4000],
                "verificationMethod": str(item.get("verificationMethod", ""))[:4000],
                "milestone": str(item.get("milestone", ""))[:240],
                "humanNotes": "",
                "agentNotes": (
                    "Planner dependency references need review: " + "; ".join(unresolved_dependencies)
                    if unresolved_dependencies else ""
                ),
            }
        )
    if not normalised_steps:
        raise ModelRuntimeError("The model returned a plan without usable research steps.")
    raw_branches = plan.get("researchBranches")
    if not isinstance(raw_branches, list):
        raw_branches = []
    plan["researchBranches"] = [str(value).strip()[:120] for value in raw_branches[:30] if str(value).strip()]
    plan["branches"] = [
        {"id": "branch_" + str(index + 1), "title": str(value), "status": "open", "priority": 3}
        for index, value in enumerate(plan["researchBranches"])
    ]
    branch_aliases = {
        alias: item["title"]
        for item in plan["branches"]
        for alias in (item["id"], item["title"])
    }
    for item in normalised_steps:
        item["branch"] = branch_aliases.get(item["branch"], item["branch"])
    plan["steps"] = normalised_steps
    return plan


def _prompt_for_plan(question: str, context: str) -> str:
    return (
        "请针对以下研究问题制定一份拟议的研究计划（研究者审阅并批准后才会开始执行）。"
        "所有面向研究者的文本字段（研究目标、形式化表述、假设、定义、关键问题、拟证引理、研究分支、实验建议、证明策略、反例策略、验证策略、预期产出、风险与不确定性，以及每个步骤的 title、description、expectedOutput、verificationMethod、milestone）必须全部使用简体中文撰写（英文专业术语可以括注），所有数学公式、变量、集合与同余式一律使用 LaTeX（行内公式用 $...$，独立公式用 $$...$$）。\n\n"
        "Research question:\n" + question +
        ("\n\nResearcher context:\n" + context if context.strip() else "") +
        "\n\nReturn the complete required JSON plan, including at least four ordered steps. "
        "Represent uncertainty and any tool needs explicitly. If necessary information is missing, "
        "return only {\"inputRequests\": [{\"label\": \"所缺必要项（中文）\", \"reason\": \"为何必须提供（中文）\", \"kind\": \"text\"}]}. "
        "Otherwise use this plan schema: " + json.dumps(PLAN_SCHEMA)
    )


async def generate_plan(question: str, context: str, model_id: str = "research.meridian") -> dict[str, Any]:
    model_id = (model_id or "").strip() or "research.meridian"
    planner_result = await _MODEL_ROUTER.complete(
        model_id,
        [
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": _prompt_for_plan(question, context)},
        ],
        temperature=0.2,
        json_object=True,
    )
    proposed = _extract_json(planner_result["text"])
    if normalize_input_requests(proposed.get("inputRequests")):
        return _normalise_plan(proposed)
    if model_id == "research.axiom":
        # Axiom planning is a multi-role proposal pass; this does not start the
        # research executor. The formalizer receives the planner draft and
        # returns a second, independently generated structured plan.
        refined_result = await _MODEL_ROUTER.complete(
            model_id,
            [
                {"role": "system", "content": _FORMALIZER_SYSTEM},
                {
                    "role": "user",
                    "content": "Research question:\n" + question +
                    "\n\nPlanner draft:\n" + json.dumps(proposed, ensure_ascii=False) +
                    "\n\nReturn a complete revised JSON plan using the required fields. 请确保所有可读文本字段和步骤内容均使用简体中文（英文专业术语可括注），数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。",
                },
            ],
            temperature=0.1,
            json_object=True,
        )
        proposed = _extract_json(refined_result["text"])
    return _normalise_plan(proposed)


def _role_for_step(step: dict[str, Any], ordinal: int) -> str:
    assigned = str(step.get("assignedAgent", "")).strip().lower()
    role = _ASSIGNMENT_ALIASES.get(assigned, assigned)
    if role in _ROLE_SYSTEM:
        return role
    text = (str(step.get("title", "")) + " " + str(step.get("description", ""))).lower()
    if any(word in text for word in ("counterexample", "falsif", "反例")):
        return "experimental-mathematician"
    if any(word in text for word in ("proof", "prove", "lemma", "证明", "引理")):
        return "proof-builder"
    if any(word in text for word in ("experiment", "numerical", "compute", "实验", "数值")):
        return "experimental-mathematician"
    if any(word in text for word in ("verify", "verification", "check", "验证", "核查")):
        return "independent-verifier"
    if ordinal == 0:
        return "research-planner"
    return "explorer"


def _capability_available(capability: str) -> bool:
    import importlib.util
    if capability in CAS_CAPABILITIES:
        from aleggo.study.external_cas import engine_available
        return engine_available(capability)
    if capability == "lean":
        from aleggo.study.lean_formal import _lake_executable
        return _lake_executable() is not None or bool(shutil.which("lean"))
    module = {"python": "", "sympy": "sympy", "numpy": "numpy", "scipy": "scipy", "z3": "z3"}.get(capability)
    if module is None:
        return capability in {"literature", "database"}
    if not module:
        return True  # the worker always runs under this interpreter
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _requested_tools(step: dict[str, Any]) -> list[str]:
    """Describe each declared requirement's execution state for the role prompt."""
    states: list[str] = []
    for value in executable_requirements(step.get("requiredTools", [])):
        capability = canonical_requirement(value)
        label = value if capability in {"", value.casefold()} else f"{value} (via {capability})"
        if capability in (WORKER_CAPABILITIES | CAS_CAPABILITIES | {"database"}) and _capability_available(capability):
            states.append("AVAILABLE_FOR_EXECUTION:" + label)
        elif capability in {"literature", "lean"} and _capability_available(capability):
            states.append("AVAILABLE_BUT_NOT_BRIDGED:" + label)
        else:
            states.append("UNAVAILABLE_OR_NOT_BRIDGED:" + label)
    return states


def validate_plan_dependencies(steps: list[dict[str, Any]]) -> str | None:
    """Require each dependency to name an earlier step in the same saved plan."""
    by_id = {str(step.get("id") or ""): step for step in steps}
    if len(by_id) != len(steps) or "" in by_id:
        return "Research step IDs must be non-empty and unique."
    for step in steps:
        step_id = str(step["id"])
        for dependency in step.get("dependencies", []):
            dependency_id = str(dependency)
            dependency_step = by_id.get(dependency_id)
            if dependency_step is None:
                return f"Step {step_id} depends on unknown step {dependency_id}."
            if dependency_id == step_id:
                return f"Step {step_id} cannot depend on itself."
            if int(dependency_step.get("order", 0)) >= int(step.get("order", 0)):
                return f"Dependency {dependency_id} must appear before step {step_id}."
    return None


def _role_messages(
    project: dict[str, Any],
    plan: dict[str, Any],
    step: dict[str, Any],
    role: str,
    prior: str,
) -> list[dict[str, str]]:
    project_prompt = (
        "已批准的研究问题：\n" + project["question"] +
        "\n\n已批准的研究目标：\n" + str(plan.get("objective", "")) +
        "\n\n已批准的假设：\n" + json.dumps(plan.get("assumptions", []), ensure_ascii=False) +
        "\n\n当前批准的步骤：\n" + step["title"] + "\n" + step["description"] +
        "\n\n预期产出：\n" + str(step.get("expectedOutput", "")) +
        "\n\n研究者指定的参数（须保留原值）：\n" + json.dumps(step.get("requestedParameters", {}), ensure_ascii=False) +
        "\n\n验证要求：\n" + str(step.get("verificationMethod", "")) +
        "\n\n研究者备注：\n" + str(step.get("humanNotes", "")) +
        "\n\n声明的工具需求与桥接状态：\n" + json.dumps(_requested_tools(step), ensure_ascii=False) +
        "\n\n前序智能体与工具输出（供复核参考）：\n" + prior[:16000] +
        "\n\n请全部使用简体中文（英文专业术语可以括注）撰写简洁明确的步骤产物与阶段报告结论，"
        "所有数学符号、变量、表达式、同余式与公式一律使用 LaTeX（行内公式用 $...$，独立公式用 $$...$$）。"
        "明确标注证据类型、未解决的证明缺口，以及任何修改计划的建议（仅作为建议，切勿擅自修改计划，切勿将未执行的工具或未验证的推导声称为已证明）。"
        "请用独立前缀行标记实质性结论：引理：命题表述；猜想：命题表述；候选反例：具体反例（仅在确实发现反例时填写）；"
        "新发现：新信息；研究路线：策略说明；路线失败：失败原因（仅在某条路线确实受阻失败时填写，若无路线失败则不要输出该行）；"
        "当前结论：本步骤在当前证据边界下的结论。切勿将已知结论重复标为新发现，需给出真实假设与原因。"
    )
    return [
        {"role": "system", "content": _ROLE_SYSTEM[role] + " Write human-readable findings and stage-report content in Simplified Chinese, with mathematics in LaTeX using $...$ or $$...$$. 请全程使用简体中文输出（英文专业术语可括注），数学公式一律用 LaTeX（行内 $...$，独立公式 $$...$$）。 Preserve identifiers, JSON keys and the distinction between conjectures and verified evidence. If essential researcher information or a referenced document is missing, stop and return only JSON: {\"inputRequests\": [{\"label\": \"所缺必要项（中文）\", \"reason\": \"为何需要（中文）\", \"kind\": \"text\"}]}. Use kind document for an original paper/dataset requirement, text for definitions/constraints. Never invent unavailable source contents. Optional literature is not a blocker for a self-contained step."},
        {"role": "user", "content": project_prompt},
    ]


async def _run_role(
    project: dict[str, Any],
    plan: dict[str, Any],
    step: dict[str, Any],
    role: str,
    prior: str,
) -> str:
    result = await _MODEL_ROUTER.complete(
        "research.axiom" if project["modelId"] == "research.axiom" else project["modelId"],
        _role_messages(project, plan, step, role, prior),
        temperature=0.15,
    )
    return result["text"]


async def _run_role_recorded(
    project: dict[str, Any], plan: dict[str, Any], step: dict[str, Any], role: str,
    prior: str, store: ResearchStore,
) -> str:
    """Call an agent and durably record its exact prompt, output, and settings."""
    model_id = "research.axiom" if project["modelId"] == "research.axiom" else project["modelId"]
    prior = ResearchInputs(store).context(project["id"]) + "\n" + prior
    messages = _role_messages(project, plan, step, role, prior)
    input_data = {"modelId": model_id, "messages": messages}
    input_digest = hashlib.sha256(json.dumps(input_data, ensure_ascii=False).encode("utf-8")).hexdigest()
    _guard_execution(project, store)
    cached = _cached_call(project, step, store, input_digest)
    if cached is not None:
        return cached
    _guard_execution(project, store, step)
    _live_phase(project, step, store, "agent_execution", f"{role.replace('-', ' ').title()}: {step['title']}. {step.get('description', '')[:250]}", agent=role)
    workbench = ResearchWorkbench(store)
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], llm_calls=1)
    metadata = {
        "toolVersion": "ModelRouter/A-leggo",
        "providerRelease": "not exposed by provider response",
        "parameters": {"temperature": 0.15, "jsonObject": False},
        "randomSeed": None,
        "environment": {"pythonVersion": sys.version.split()[0], "platform": sys.platform},
        "inputSha256": input_digest,
        "agentRole": role,
        "recordedAt": datetime.now(UTC).isoformat(),
    }
    started = time.monotonic()
    domain = ResearchDomainStore(store)
    usage_records: list[dict[str, Any]] = []
    try:
        with capture_model_usage() as usage_records:
            # Bound a single model call: a hung provider must not stall the
            # whole run forever (the step can be retried after the timeout).
            output = await asyncio.wait_for(_run_role(project, plan, step, role, prior), timeout=600.0)
    except asyncio.CancelledError:
        workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=time.monotonic() - started)
        domain.record_tool_run(project["id"], "llm:" + role, input_data, "已被研究者取消。", "CANCELLED",
                               step_id=str(step.get("id") or ""), branch_name=str(step.get("branch") or "main"),
                               elapsed_ms=int((time.monotonic() - started) * 1000), metadata=metadata)
        raise
    except (TimeoutError, asyncio.TimeoutError) as exc:
        workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=time.monotonic() - started, failed=True)
        domain.record_tool_run(project["id"], "llm:" + role, input_data,
                               json.dumps({"errorType": "ModelTimeout", "error": "模型调用超过 600 秒未返回，已中止。"}, ensure_ascii=False),
                               "TIMEOUT", step_id=str(step.get("id") or ""), branch_name=str(step.get("branch") or "main"),
                               elapsed_ms=int((time.monotonic() - started) * 1000), metadata=metadata)
        raise _ResearchBoundary("BLOCKED", "模型调用超过 600 秒未返回，已中止；可稍后重试该步骤。", "MODEL_TIMEOUT") from exc
    except Exception as exc:
        workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=time.monotonic() - started, failed=True)
        domain.record_tool_run(project["id"], "llm:" + role, input_data,
                               json.dumps({"errorType": type(exc).__name__, "error": str(exc)[:4000]}, ensure_ascii=False),
                               "FAILED", step_id=str(step.get("id") or ""), branch_name=str(step.get("branch") or "main"),
                               elapsed_ms=int((time.monotonic() - started) * 1000), metadata=metadata)
        raise
    if usage_records:
        metadata["usage"] = summarise_usage(usage_records)
    domain.record_tool_run(project["id"], "llm:" + role, input_data, output, "COMPLETED",
                           step_id=str(step.get("id") or ""), branch_name=str(step.get("branch") or "main"),
                           elapsed_ms=int((time.monotonic() - started) * 1000), metadata=metadata)
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=time.monotonic() - started)
    try:
        requested_inputs = normalize_input_requests(_extract_json(output).get("inputRequests"))
    except ModelRuntimeError:
        requested_inputs = []
    if requested_inputs:
        ResearchInputs(store).register(project["id"], requested_inputs, step_id=step["id"], reopen=True)
        raise _ResearchBoundary("BLOCKED", "需要补充必要资料：" + "；".join(item["label"] for item in requested_inputs), "MISSING_INPUT")
    if ResearchInputs(store).pending(project["id"]):
        raise _ResearchBoundary("BLOCKED", "等待补充必要资料。", "MISSING_INPUT")
    _cache_call(project, step, store, input_digest, output)
    artifact = store.create_artifact(project["id"], project["currentVersion"], step["id"], role,
                                     role.replace("-", " ").title() + ": " + step["title"], output,
                                     status="CONJECTURAL", branch_name=str(step.get("branch") or "main"),
                                     metadata={"artifactKind": "agent-output", "inputSha256": input_digest, "evidenceClass": "CONJECTURAL"})
    current = workbench.checkpoint(project["id"], project["currentVersion"], step["id"]) or {}
    new_signals = _progress_signals(output, "CONJECTURAL", {}, None, project=project, step=step, store=store, artifact_id=artifact["id"])
    signals = {**current.get("signals", {})}
    for key, value in new_signals.items():
        signals[key] = (signals.get(key, False) or value) if isinstance(value, bool) else value
    workbench.save_checkpoint(project["id"], project["currentVersion"], step["id"], signals=signals)
    return output


async def _run_axiom_team(
    project: dict[str, Any],
    plan: dict[str, Any],
    step: dict[str, Any],
    assigned_role: str,
    prior: str,
    store: ResearchStore,
) -> str:
    """Run independent Axiom role agents concurrently, then review and synthesize.

    Each role is a separate model call with its own system prompt and durable
    artifact. The two review roles run concurrently after the specialist
    proposals, matching the latest MRA coordinator's skeptic/verifier pattern.
    """
    specialists = ["explorer", "lemma-generator", "proof-builder", "experimental-mathematician"]
    if assigned_role in _ROLE_SYSTEM and assigned_role not in {
        "independent-verifier", "research-skeptic", "research-synthesizer"
    }:
        specialists = [assigned_role, *[role for role in specialists if role != assigned_role]]
    _guard_execution(project, store)
    project["_activeAgents"] = specialists
    _live_phase(project, step, store, "specialist_exploration", "Independent specialists exploring " + step["title"], active_agents=specialists)
    specialist_results = await asyncio.gather(
        *(_run_role_recorded(project, plan, step, role, prior, store) for role in specialists),
        return_exceptions=True,
    )
    contributions: list[dict[str, str]] = []
    for role, result in zip(specialists, specialist_results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            if isinstance(result, (_ResearchControlInterrupt, _ResearchBoundary)):
                raise result
            content = "Agent call failed: " + str(result)
            store.create_artifact(
                project["id"], project["currentVersion"], step["id"], role,
                "Agent failure: " + step["title"], content, status="failed",
                branch_name=str(step.get("branch") or "main"),
            )
            continue
        contribution = {"role": role, "content": str(result)}
        contributions.append(contribution)
    if not contributions:
        raise ModelRuntimeError("All Axiom specialist agents failed on this research step.")

    review_context = json.dumps(contributions, ensure_ascii=False)
    _guard_execution(project, store)
    project["_activeAgents"] = ["research-skeptic", "independent-verifier"]
    _live_phase(project, step, store, "independent_review", "Critic and verifier reviewing specialist arguments for " + step["title"], active_agents=project["_activeAgents"])
    review_step = {
        **step,
        "title": "Independent review: " + step["title"],
        "description": step["description"] + "\n\nSpecialist reports:\n" + review_context[:24000],
    }
    reviews = await asyncio.gather(
        _run_role_recorded(project, plan, review_step, "research-skeptic", prior, store),
        _run_role_recorded(project, plan, review_step, "independent-verifier", prior, store),
        return_exceptions=True,
    )
    review_records: list[dict[str, str]] = []
    for role, result in zip(("research-skeptic", "independent-verifier"), reviews, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            if isinstance(result, (_ResearchControlInterrupt, _ResearchBoundary)):
                raise result
            content = "Review call failed: " + str(result)
            store.create_artifact(
                project["id"], project["currentVersion"], step["id"], role,
                "Review failure: " + step["title"], content, status="failed",
                branch_name=str(step.get("branch") or "main"),
            )
            continue
        review = str(result)
        review_records.append({"role": role, "content": review})

    if not review_records:
        raise ModelRuntimeError("Both independent Axiom review agents failed; this step remains incomplete.")

    synthesis_step = {
        **step,
        "title": "Synthesize agent work: " + step["title"],
        "description": step["description"] + "\n\nAgent reports and reviews:\n" + json.dumps(
            {"contributions": contributions, "reviews": review_records}, ensure_ascii=False
        )[:32000],
    }
    _guard_execution(project, store)
    project["_activeAgents"] = ["research-synthesizer"]
    synthesis = await _run_role_recorded(
        project, plan, synthesis_step, "research-synthesizer", prior, store
    )
    return synthesis


def _tool_planner_system() -> str:
    """Tool-planner instructions with the tools installed *now* (CAS discovery is live)."""
    return (
        "You propose exactly one bounded mathematical tool call for an already approved research step. "
        "Return JSON only with keys name and input. Allowed names and their exact input schemas:\n"
        + planner_tool_reference() +
        "\nPrefer an exact symbolic operation when the step asks for an identity, closed form, limit, series, sum, "
        "ODE solution or matrix invariant; use run_python for experiments, enumeration, counterexample searches, "
        "numerics and figures. Use run_pari / run_gap / run_sage / run_macaulay2 only when the step requires that "
        "system and it is listed above, and the database tools (oeis_search, zbmath_search, arxiv_search, lmfdb_search) "
        "only when the step requires a database or literature lookup. Never use shell commands or Lean here. For "
        "run_python, input.code must be "
        "a short reproducible calculation that assigns a JSON-friendly summary to result; for searches include "
        "the checked range and a casesChecked count in result (for example result = {\"casesChecked\": n, "
        "\"counterexample\": None}). If a previous attempt failed, fix the reported error instead of repeating it. "
        "If no safe useful call is needed, return {\"name\":\"capability_check\",\"input\":{}}."
    )


_TOOL_PLANNER_SYSTEM = _tool_planner_system()

# Worker error types that mean "the proposed program or input was wrong", which
# the planner can repair from the error text.  Infrastructure failures
# (unavailable worker, protocol errors, budget) are never retried here.
_REPAIRABLE_ERRORS = {"PROGRAM_ERROR", "VALIDATION_ERROR", "OUTPUT_LIMIT", "INVALID_SEED", "TIMEOUT"}
_MAX_TOOL_REPAIRS = 2


def _tool_allowed_by_plan(step: dict[str, Any], name: str) -> bool:
    return worker_tool_allowed(step.get("requiredTools", []), name)


def _tool_planner_input(project: dict[str, Any], step: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    content = (
        "Approved research step:\n" + step["title"] + "\n" + step["description"] +
        "\n\nExplicitly requested tools:\n" + json.dumps(step["requiredTools"], ensure_ascii=False) +
        "\n\nExpected output:\n" + str(step.get("expectedOutput", "")) +
        "\n\nResearcher requested parameters (preserve supplied values):\n" + json.dumps(step.get("requestedParameters", {}), ensure_ascii=False)
    )
    if attempts:
        content += (
            "\n\nPrevious attempts in this step failed. Repair the call using the exact error; do not repeat an identical call:\n"
            + json.dumps(attempts, ensure_ascii=False)[:12000]
        )
    return {
        "modelId": project["modelId"],
        "messages": [
            {"role": "system", "content": _tool_planner_system()},
            {"role": "user", "content": content + "\n\nReturn a single JSON invocation."},
        ],
    }


async def _propose_tool_invocation(
    project: dict[str, Any], step: dict[str, Any], store: ResearchStore, attempts: list[dict[str, Any]],
) -> dict[str, Any] | tuple[str, str, str] | None:
    """Ask the tool planner for one call.

    The first proposal keeps the original failure semantics (model outage blocks,
    unparsable proposals become a NOT_EXECUTED artifact).  A *repair* proposal
    that fails returns ``None`` so the last real worker result is retained.
    """
    workbench = ResearchWorkbench(store)
    repairing = bool(attempts)
    _live_phase(project, step, store, "tool_planning",
                ("Repairing the failed " if repairing else "Preparing a bounded ") + ", ".join(step["requiredTools"]) + " calculation for " + step["title"],
                agent="tool-planner")
    planner_input = _tool_planner_input(project, step, attempts)
    planner_started = time.monotonic()
    planner_metadata = {
        "toolVersion": "ModelRouter/A-leggo", "providerRelease": "not exposed by provider response",
        "parameters": {"temperature": 0, "jsonObject": True}, "randomSeed": None,
        "environment": {"pythonVersion": sys.version.split()[0], "platform": sys.platform},
        "recordedAt": datetime.now(UTC).isoformat(), "repairAttempt": len(attempts),
    }
    proposal_digest = hashlib.sha256(json.dumps(planner_input, ensure_ascii=False).encode()).hexdigest()
    cached = _cached_call(project, step, store, proposal_digest)
    try:
        if cached is not None:
            proposal = {"text": cached}
        else:
            _guard_execution(project, store, step)
            workbench.charge_step(project["id"], project["currentVersion"], step["id"], llm_calls=1)
            proposal = await _MODEL_ROUTER.complete(project["modelId"], planner_input["messages"], temperature=0, json_object=True)
            _cache_call(project, step, store, proposal_digest, proposal["text"])
            workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=time.monotonic() - planner_started)
    except (_ResearchControlInterrupt, _ResearchBoundary, asyncio.CancelledError):
        raise
    except Exception as exc:
        ResearchDomainStore(store).record_tool_run(
            project["id"], "llm:tool-planner", planner_input,
            json.dumps({"errorType": type(exc).__name__, "error": str(exc)[:4000]}, ensure_ascii=False),
            "FAILED", step_id=step["id"], branch_name=str(step.get("branch") or "main"),
            elapsed_ms=int((time.monotonic() - planner_started) * 1000), metadata=planner_metadata,
        )
        if repairing:
            return None
        if isinstance(exc, ModelRuntimeError):
            content = json.dumps({"status": "NOT_EXECUTED", "reason": str(exc)}, ensure_ascii=False)
            artifact = store.create_artifact(
                project["id"], project["currentVersion"], step["id"], "math-tool", "Tool proposal", content,
                status="not-executed", branch_name=str(step.get("branch") or "main"),
            )
            workbench.save_checkpoint(project["id"], project["currentVersion"], step["id"], payload={"toolResult": [artifact["id"], "math-tool", content]})
            raise _ResearchBoundary("BLOCKED", "所需工具规划服务失败：" + str(exc), "MODEL_UNAVAILABLE") from exc
        raise
    if isinstance(proposal.get("usage"), dict):
        planner_metadata["usage"] = proposal["usage"]
    ResearchDomainStore(store).record_tool_run(
        project["id"], "llm:tool-planner", planner_input, str(proposal.get("text") or ""), "COMPLETED",
        step_id=step["id"], branch_name=str(step.get("branch") or "main"),
        elapsed_ms=int((time.monotonic() - planner_started) * 1000), metadata=planner_metadata,
    )
    try:
        return _extract_json(proposal["text"])
    except ModelRuntimeError as exc:
        from aleggo.study.research_collaborator import _tool_call

        dsml_call = _tool_call(str(proposal.get("text") or ""))
        if isinstance(dsml_call, dict):
            return dsml_call
        if repairing:
            return None
        content = json.dumps({"status": "NOT_EXECUTED", "reason": str(exc)}, ensure_ascii=False)
        artifact = store.create_artifact(
            project["id"], project["currentVersion"], step["id"], "math-tool", "Tool proposal", content,
            status="not-executed",
            branch_name=str(step.get("branch") or "main"),
        )
        return artifact["id"], "math-tool", content


async def _execute_tool_invocation(
    project: dict[str, Any], step: dict[str, Any], store: ResearchStore, name: str, input_data: Any,
) -> tuple[dict[str, Any], int]:
    """Run one allow-listed worker call inside the branch's tool-time budget."""
    if not isinstance(input_data, dict) or not _tool_allowed_by_plan(step, name):
        return {
            "status": "REJECTED",
            "reason": "The proposed tool call was outside the approved step's allow-listed tool requirements.",
            "error": ("Unknown or unapproved tool name " + json.dumps(name) + "." if isinstance(input_data, dict)
                      else "The tool input must be a JSON object."),
        }, 0
    workbench = ResearchWorkbench(store)
    _guard_execution(project, store, step)
    domain = ResearchDomainStore(store)
    branch_name = str(step.get("branch") or "main")
    branch = domain.get_branch(project["id"], branch_name)
    if not branch:
        return {"ok": False, "status": "NOT_EXECUTED", "error": "The approved branch is missing."}, 0
    seconds_left = float(branch["limits"].get("toolSeconds", 0)) - float(branch["used"].get("toolSeconds", 0))
    if seconds_left <= 0:
        return {"ok": False, "status": "RESOURCE_BUDGET_EXCEEDED", "error": "This branch has no tool time remaining."}, 0
    _live_phase(project, step, store, "tool_execution", "Running " + name + " with " + json.dumps(input_data, ensure_ascii=False)[:1000], agent="mathematical-worker", tool=name)
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], tool_calls=1)
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            run_math_tool(
                workspace_root=store.aleggo_store.root,
                project_id=project["id"],
                name=name,
                input_data=input_data,
            ),
            timeout=max(0.05, min(120.0, seconds_left)),
        )
    except TimeoutError:
        result = {"ok": False, "status": "RESOURCE_BUDGET_EXCEEDED", "error": "The branch tool time limit was reached."}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=elapsed_ms / 1000, failed=result.get("ok") is not True)
    try:
        domain.consume_budget(project["id"], branch_name, "toolSeconds", elapsed_ms / 1000)
    except ValueError as exc:
        result = {**result, "ok": False, "status": "RESOURCE_BUDGET_EXCEEDED", "error": str(exc)}
    return result, elapsed_ms


def _tool_record(name: str, input_data: Any, result: dict[str, Any], attempts: list[dict[str, Any]]) -> tuple[str, str]:
    status = str(result.get("verification_status") or result.get("status") or "UNVERIFIED")
    record = {
        "tool": name,
        "ok": bool(result.get("ok")),
        "status": status,
        "verificationLevel": result.get("verification_level", "UNKNOWN"),
        "output": result.get("output", ""),
        "input": input_data,
        "error": result.get("error", ""),
        "environment": result.get("environment", ""),
        "workerExitCode": result.get("worker_exit_code"),
        "proofHash": result.get("proofHash"),
    }
    if attempts:
        record["repairAttempts"] = attempts
    return status, json.dumps(record, ensure_ascii=False)


async def _run_planned_tool(
    project: dict[str, Any], step: dict[str, Any], store: ResearchStore
) -> tuple[str, str, str] | None:
    if not executable_requirements(step.get("requiredTools", [])):
        return None
    _guard_execution(project, store, step)
    workbench = ResearchWorkbench(store)
    checkpoint = workbench.checkpoint(project["id"], project["currentVersion"], step["id"]) or {}
    saved = checkpoint.get("payload", {}).get("toolResult")
    if saved:
        try:
            record = json.loads(saved[2]) if isinstance(saved[2], str) else {}
        except (TypeError, ValueError):
            record = {}
        if not isinstance(record, dict):
            record = {}
        if record.get("status") not in {"TOOL_FAILURE", "RESOURCE_BUDGET_EXCEEDED", "NOT_EXECUTED"}:
            return tuple(saved)
    explicit = step.get("explicitValidatedInvocation")
    attempts: list[dict[str, Any]] = []
    if isinstance(explicit, dict):
        # Runtime allow-list and worker validation below apply equally to exact human inputs,
        # which are executed exactly once and never reinterpreted or repaired.
        invocation: Any = explicit
    else:
        invocation = await _propose_tool_invocation(project, step, store, attempts)
        if isinstance(invocation, tuple):
            return invocation
    while True:
        if isinstance(explicit, dict):
            name, input_data = str(invocation.get("name") or ""), invocation.get("input")
        else:
            name, input_data = canonical_worker_invocation(invocation)
        result, elapsed_ms = await _execute_tool_invocation(project, step, store, name, input_data)
        status, content = _tool_record(name, input_data, result, attempts)
        ResearchDomainStore(store).record_tool_run(
            project["id"], name or "proposal", input_data if isinstance(input_data, dict) else {"unparsed": str(input_data)[:4000]},
            content, status, step_id=str(step.get("id") or ""),
            branch_name=str(step.get("branch") or "main"), elapsed_ms=elapsed_ms,
            metadata={"toolVersion": result.get("tool_version", "worker-reported"), "input": input_data,
                      "parameters": input_data, "randomSeed": input_data.get("seed") if isinstance(input_data, dict) else None,
                      "environment": result.get("environment", ""), "workerExitCode": result.get("worker_exit_code"),
                      "generatedFiles": result.get("generated_files", []),
                      "generatedFileWarnings": result.get("generated_file_warnings", []),
                      "repairAttempt": len(attempts),
                      "recordedAt": datetime.now(UTC).isoformat()},
        )
        error_type = str(result.get("error_type") or "")
        repairable = (not isinstance(explicit, dict) and len(attempts) < _MAX_TOOL_REPAIRS
                      and (status == "REJECTED" or (result.get("ok") is not True and error_type in _REPAIRABLE_ERRORS)))
        if not repairable:
            break
        failure = {"name": name, "input": input_data if isinstance(input_data, dict) else str(input_data)[:2000],
                   "errorType": error_type or status, "error": str(result.get("error") or result.get("reason") or "")[:2000],
                   "stderrTail": str(result.get("stderr") or "")[-1500:]}
        if attempts and {k: failure[k] for k in ("name", "input", "error")} == {k: attempts[-1][k] for k in ("name", "input", "error")}:
            break
        attempts.append(failure)
        _guard_execution(project, store, step)
        repaired = await _propose_tool_invocation(project, step, store, attempts)
        if not isinstance(repaired, dict) or canonical_worker_invocation(repaired) == (name, input_data):
            # No usable repair, or the planner repeated the identical failing call.
            attempts.pop()
            break
        invocation = repaired
    artifact = store.create_artifact(
        project["id"], project["currentVersion"], step["id"], "math-tool",
        "Tool result: " + (name or "proposal"), content, status=status.lower(),
        branch_name=str(step.get("branch") or "main"),
    )
    saved_result = [artifact["id"], "math-tool", content]
    workbench.save_checkpoint(project["id"], project["currentVersion"], step["id"], payload={"toolResult": saved_result})
    if status in {"TOOL_FAILURE", "RESOURCE_BUDGET_EXCEEDED", "NOT_EXECUTED"}:
        raise _ResearchBoundary("BLOCKED", str(result.get("error") or result.get("reason") or status), "TOOL_UNAVAILABLE" if status == "TOOL_FAILURE" else "BUDGET" if status == "RESOURCE_BUDGET_EXCEEDED" else "REQUIRED_TOOL")
    signals = _progress_signals("", evidence_classification(content, "", []), {}, tuple(saved_result), project=project, step=step, store=store, artifact_id=artifact["id"])
    current = workbench.checkpoint(project["id"], project["currentVersion"], step["id"]) or {}
    workbench.save_checkpoint(project["id"], project["currentVersion"], step["id"], signals={**current.get("signals", {}), **signals})
    return artifact["id"], "math-tool", content


def _step_milestone(step: dict[str, Any], ordinal: int) -> str:
    value = str(step.get("milestone") or "").strip()
    return value or f"Milestone {ordinal // 2 + 1}"


def _required_tool_blocker(step: dict[str, Any], *, auto_lean_enabled: bool = True) -> str:
    for tool in executable_requirements(step.get("requiredTools", [])):
        capability = canonical_requirement(tool)
        if capability == "lean":
            if not auto_lean_enabled:
                return "项目设置已关闭研究过程中的自动 Lean 调用；需要时可在研究资料中手动启动 Lean 检查。"
            try:
                from aleggo.study.lean_formal import detect_lean_workspace
                detect_lean_workspace()
            except Exception as exc:
                return str(exc)
            if not (step.get("formalBindingId") and step.get("proofSource")):
                return "Lean 验证需要本步骤带有研究者确认的 formalBindingId 与明确的证明来源 proofSource。"
        elif capability in {"sympy", "numpy", "scipy", "z3"}:
            if not _capability_available(capability):
                return f"数学工作环境中未安装所需工具 '{tool}'。"
        elif capability in CAS_CAPABILITIES:
            if not _capability_available(capability):
                from aleggo.study.external_cas import ENGINES
                engine = ENGINES[capability]
                return (f"本机未安装所需工具 '{tool}'（{engine.label}）。{engine.install_hint} "
                        f"然后在「研究工具 → 计算机代数」中设置其路径（或环境变量 {engine.env_var}），或把步骤改成用 python、sympy、z3 或文献。")
        elif capability in {"python", "literature", "database"}:
            continue
        else:
            return (f"所需工具 '{tool}' 在本安装中没有已注册的运行时桥接。 "
                    "请把步骤改成用 python、sympy、z3、pari、gap、sage、macaulay2、database 或文献， "
                    "或把外部结果记为研究者笔记。")
    return ""


async def _recommend_next_path(project: dict[str, Any], plan: dict[str, Any], step: dict[str, Any],
                               result: str, store: ResearchStore) -> dict[str, Any]:
    workbench = ResearchWorkbench(store)
    _guard_execution(project, store, step)
    _live_phase(project, step, store, "route_recommendation", "Comparing remaining approved routes with recorded evidence and failed paths after " + step["title"], agent="next-path-advisor")
    prompt = {
        "question": project["question"], "approvedPlan": plan,
        "completedStep": {"title": step["title"], "description": step["description"], "result": result[-12000:]},
        "memory": workbench.domain.research_context(project["id"], project["question"] + " " + step["title"]),
        "failedPaths": workbench.domain.failed_paths(project["id"]) if hasattr(workbench.domain, "failed_paths") else [],
        "instruction": "请用简体中文（英文专业术语可括注）建议后续可行的研究路径，所有数学内容一律使用 LaTeX（行内 $...$，独立公式 $$...$$）。切勿修改、重排或静默替换已批准的计划；为每条路径注明理由与不确定性，除非假设改变，否则避免重复已记录为失败的路线。",
        "requiredKeys": {"summary": "string", "primary": [{"path": "string", "reason": "string"}],
                         "secondary": [{"path": "string", "reason": "string"}],
                         "deprioritize": [{"path": "string", "reason": "string"}], "uncertainty": "string"},
    }
    call_started = time.monotonic()
    response: dict[str, Any] | None = None
    call_metadata = {
        "toolVersion": "ModelRouter/A-leggo", "providerRelease": "not exposed by provider response",
        "parameters": {"temperature": 0.15, "jsonObject": True}, "randomSeed": None,
        "environment": {"pythonVersion": sys.version.split()[0], "platform": sys.platform},
        "recordedAt": datetime.now(UTC).isoformat(),
    }
    digest = hashlib.sha256(json.dumps(prompt, ensure_ascii=False).encode()).hexdigest()
    cached = _cached_call(project, step, store, digest)
    try:
        if cached is not None:
            response = {"text": cached}
        else:
            _guard_execution(project, store, step)
            workbench.charge_step(project["id"], project["currentVersion"], step["id"], llm_calls=1)
            response = await _MODEL_ROUTER.complete(
            project["modelId"],
            [{"role": "system", "content": "You are a mathematical research path advisor. Provide advisory options only. Preserve the approved plan; do not claim novelty or proof. Write human-readable advice in Simplified Chinese and mathematics in LaTeX using $...$ or $$...$$; keep JSON keys and identifiers unchanged."},
             {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            temperature=0.15, json_object=True,
            )
            _cache_call(project, step, store, digest, response["text"])
            workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=time.monotonic() - call_started)
        recommendation = _extract_json(response["text"])
        for key in ("primary", "secondary", "deprioritize"):
            values = recommendation.get(key, [])
            if not isinstance(values, list):
                values = [values]
            recommendation[key] = [
                {"path": str(item.get("path") or "")[:2000], "reason": str(item.get("reason") or "")[:2000]}
                if isinstance(item, dict) else {"path": str(item)[:2000], "reason": "Reason not supplied."}
                for item in values[:5]
            ]
        recommendation["summary"] = str(recommendation.get("summary") or "Advisory research route options.")[:2000]
        recommendation["uncertainty"] = str(recommendation.get("uncertainty") or "Recommendations are advisory and require researcher review.")[:2000]
        recommendation["source"] = "AI_ADVISORY"
    except (_ResearchControlInterrupt, _ResearchBoundary):
        raise
    except Exception as exc:
        workbench.domain.record_tool_run(
            project["id"], "llm:next-path-advisor", {"modelId": project["modelId"], "prompt": prompt},
            str(response.get("text") or "") if response else json.dumps(
                {"errorType": type(exc).__name__, "error": str(exc)[:4000]}, ensure_ascii=False,
            ),
            "INVALID_RESPONSE" if response else "FAILED", step_id=step["id"],
            branch_name=str(step.get("branch") or "main"),
            elapsed_ms=int((time.monotonic() - call_started) * 1000), metadata=call_metadata,
        )
        recommendation = {"summary": "AI route recommendation unavailable.", "primary": [], "secondary": [],
                          "deprioritize": [], "uncertainty": f"IMPLEMENTED BUT EXTERNAL SERVICE UNAVAILABLE ({type(exc).__name__}).",
                          "source": "UNAVAILABLE"}
    else:
        if isinstance(response, dict) and isinstance(response.get("usage"), dict):
            call_metadata["usage"] = response["usage"]
        workbench.domain.record_tool_run(
            project["id"], "llm:next-path-advisor", {"modelId": project["modelId"], "prompt": prompt},
            str(response.get("text") or ""), "COMPLETED", step_id=step["id"],
            branch_name=str(step.get("branch") or "main"),
            elapsed_ms=int((time.monotonic() - call_started) * 1000), metadata=call_metadata,
        )
    return workbench.add_recommendation(project["id"], step["id"], recommendation)


async def _targeted_literature(project: dict[str, Any], plan: dict[str, Any], step: dict[str, Any],
                              result: str, artifact_id: str, store: ResearchStore) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    workbench = ResearchWorkbench(store)
    service = LiteratureIntelligence(store)
    queries = service.targeted_queries(project["id"], step, result)
    if not queries:
        return [], {"status": "not-triggered", "queries": []}
    _guard_execution(project, store, step)
    _live_phase(project, step, store, "literature_search", "Searching public academic indexes for " + "; ".join(queries)[:1000], agent="literature-intelligence", tool="academic-index-apis")
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], tool_calls=1)
    started = time.monotonic()
    try:
        response = await service.search(project["id"], queries=queries, trigger_type="counterexample" if re.search(r"(?i)counterexample|反例", result) else "new_claim", trigger_id=artifact_id, step_id=step["id"], branch=str(step.get("branch") or "main"), limit=20, max_pages=1, guard=lambda: _guard_execution(project, store))
    except (_ResearchControlInterrupt, _ResearchBoundary):
        raise
    except Exception as exc:
        unavailable = f"IMPLEMENTED BUT EXTERNAL SERVICE UNAVAILABLE — {type(exc).__name__}"
        response = {"items": [], "providers": [], "providerStatuses": {"literature_search": unavailable},
                    "availabilityStatus": "IMPLEMENTED BUT EXTERNAL SERVICE UNAVAILABLE",
                    "warnings": {"literature_search": unavailable}, "queries": queries, "newRecords": 0}
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=time.monotonic() - started)
    items = response.get("items", [])
    for source in items:
        try:
            ResearchDomainStore(store).link_literature(project["id"], source["id"], "artifact", artifact_id,
                                                       "RELATED_TO", "Targeted search after a new lemma, conjecture, or counterexample signal.")
        except (KeyError, ValueError):
            pass
    warnings = response.get("warnings") or {}
    has_provider = bool(response.get("providers"))
    response.update({"queries": queries, "status": response.get("status") or ("COMPLETE" if has_provider and not warnings else "PARTIAL" if has_provider else "IMPLEMENTED BUT EXTERNAL SERVICE UNAVAILABLE")})
    workbench.record_event(project["id"], "targeted_literature_search", "literature-intelligence", step_id=step["id"],
                           branch=str(step.get("branch") or "main"), summary="Targeted search after a persisted research finding.",
                           details={"queries": queries, "providers": response.get("providers", []),
                                    "warnings": response.get("warnings", {}), "status": response["status"],
                                    "newRecords": response.get("newRecords", 0), "sourceIds": [item["id"] for item in items]})
    return items, response


def _step_report(project: dict[str, Any], plan: dict[str, Any], step: dict[str, Any], result: str,
                 tool_result: tuple[str, str, str] | None, sources: list[dict[str, Any]],
                 recommendation: dict[str, Any], classification: str, *, structured: dict[str, Any] | None = None) -> str:
    if structured is not None:
        content = f"# 步骤产物 · {step['title']}\n\n所记录操作的证据等级：**{classification}**。所提结论保留各自的证据等级。\n\n"
        for field, value in structured.items():
            rendered = value if isinstance(value, str) else "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"
            content += "## " + field + "\n\n" + rendered + "\n\n"
        return content
    tool_text = tool_result[2] if tool_result else "本步骤未请求或执行运行时工具。"
    liter_text = "\n".join(f"- [{item.get('title')}]({item.get('url') or 'local source'}) · {item.get('authors','')} · {item.get('year') or 'year unknown'} · {item.get('classification','background')}" for item in sources) or "本步骤未添加文献来源。"
    def paths(key: str) -> str:
        values = recommendation.get("content", {}).get(key, [])
        return "\n".join(f"- {item.get('path')}: {item.get('reason')}" for item in values if isinstance(item, dict)) or "- 无记录。"
    output = result.strip() or "智能体未返回文本输出。"
    literature = recommendation.get("literature", {})
    provider_statuses = literature.get("providerStatuses") or literature.get("warnings") or {}
    provider_lines = "\n".join(f"- {provider}: {status}" for provider, status in provider_statuses.items()) or "- 未查询外部提供方。"
    return (
        f"# 步骤产物 · {step['title']}\n\n"
        f"- 项目：{project['title']}（`{project['id']}`）\n- 计划版本：{project['currentVersion']}\n"
        f"- 步骤：`{step['id']}` · 里程碑：{_step_milestone(step, int(step.get('order', 0)))}\n"
        f"- 分支：{step.get('branch') or 'main'} · 智能体：{step.get('assignedAgent') or 'explorer'}\n"
        f"- 完成时间：{datetime.now(UTC).isoformat()}\n- 证据等级：**{classification}**\n\n"
        f"## 目标与方法\n\n{step.get('description') or step['title']}\n\n"
        f"期望输出：{step.get('expectedOutput') or '未指定'}\n\n"
        f"验证要求：{step.get('verificationMethod') or '未指定'}\n\n"
        f"## 工作与输出\n\n{output}\n\n"
        f"## 工具、输入与可复现性\n\n{tool_text}\n\n"
        f"## 新增或查阅的文献\n\n{liter_text}\n\n"
        f"定向文献检索状态：{literature.get('status', 'not-triggered')}\n\n提供方可用性：\n{provider_lines}\n\n"
        f"## 证据与断言\n\n证据等级为 {classification}。模型生成的文字不是证明。工具输出保留其明确的验证状态。\n\n"
        f"## 猜想、引理、反例与失败\n\n"
        f"{_extract_report_terms(output, ('conjecture', 'lemma', 'counterexample', 'failed', 'failure', 'uncertainty'))}\n\n"
        f"## 不确定性与未竟义务\n\n{step.get('verificationMethod') or '请检查输出，并对照既定假设验证所有断言。'}\n\n"
        f"## 建议的下一步研究路径（仅供参考）\n\n摘要：{recommendation.get('content', {}).get('summary', '暂无 AI 建议。')}\n\n"
        f"优先\n{paths('primary')}\n\n次选\n{paths('secondary')}\n\n暂缓\n{paths('deprioritize')}\n\n"
        f"不确定性：{recommendation.get('content', {}).get('uncertainty', '需要研究者复核。')}\n"
    )


def _structured_step_report(project: dict[str, Any], plan: dict[str, Any], step: dict[str, Any],
                            result: str, tool_result: tuple[str, str, str] | None, sources: list[dict[str, Any]],
                            recommendation: dict[str, Any], classification: str, store: ResearchStore) -> dict[str, Any]:
    domain, workbench = ResearchDomainStore(store), ResearchWorkbench(store)
    artifacts = [a for a in store.list_artifacts(project["id"]) if a["planVersion"] == project["currentVersion"] and a["stepId"] == step["id"]]
    nodes = [n for n in domain.list_graph(project["id"])["nodes"] if n.get("metadata", {}).get("stepId") == step["id"] and n.get("metadata", {}).get("planVersion") == project["currentVersion"]]
    attempts = [a for a in domain.failed_paths(project["id"]) if a.get("stepId") == step["id"]]
    runs = [r for r in domain.list_tool_runs(project["id"], 500) if r["stepId"] == step["id"]]
    conclusions = [line for line in result.splitlines() if re.search(r"(?i)^\s*(?:#+\s*)?(?:current conclusion|conclusion|当前结论|结论)\s*[:：]", line)]
    return {"Research Step": {"id": step["id"], "title": step["title"], "planVersion": project["currentVersion"], "milestone": workbench.state(project["id"])["currentMilestone"], "completedAt": datetime.now(UTC).isoformat()},
            "Objective": step.get("description") or step["title"],
            "What Was Done": {"agentArtifacts": [{"id": a["id"], "role": a["agentRole"], "title": a["title"]} for a in artifacts], "executedToolRecord": json.loads(tool_result[2]) if tool_result else None, "literatureSearch": recommendation.get("literature", {})},
            "Methods": {"approvedMethod": step.get("description"), "verificationRequirement": step.get("verificationMethod"), "actualOperations": [r["tool"] for r in runs], "scope": "Only operations with persisted run records were executed; declared tools alone are not execution."},
            "Tools Used": [{"runId": r["id"], "tool": r["tool"], "status": r["status"], "elapsedMs": r["elapsedMs"], "metadata": r["metadata"]} for r in runs],
            "Literature Used": sources,
            "Inputs": {"question": project["question"], "assumptions": plan.get("assumptions", []), "step": step, "toolInputs": [r["input"] for r in runs]},
            "Outputs": result,
            "New Evidence": [n for n in nodes if n["type"] in {"evidence", "experiment", "counterexample"}],
            "New Conjectures": [n for n in nodes if n["type"] == "conjecture"],
            "New Lemmas": [n for n in nodes if n["type"] == "lemma"],
            "Counterexamples": [n for n in nodes if n["type"] == "counterexample"],
            "Failed Attempts": attempts,
            "Uncertainty": {"verificationObligations": step.get("verificationMethod"), "advisorUncertainty": recommendation.get("content", {}).get("uncertainty"), "evidenceBoundary": "Numerical observations do not prove general claims. A symbolic operation verifies its exact inputs only. Model-generated proofs and candidate witnesses remain conjectural until independently checked."},
            "Current Conclusion": "\n".join(conclusions) or "No separate conclusion was supplied. Consult the complete Outputs and each claim's recorded evidence level; no unverified theorem is promoted.",
            "Recommended Next Action": recommendation.get("content", {}),
            "Generated Files": [{"artifactId": a["id"], "title": a["title"], "filePath": a.get("filePath"), "status": a["status"]} for a in artifacts]}


def _extract_report_terms(text: str, needles: tuple[str, ...]) -> str:
    lines = [line.strip(" #-") for line in text.splitlines() if any(term in line.casefold() for term in needles)]
    return "\n".join(f"- {line[:1200]}" for line in lines[:24]) or "No explicit item was marked in the returned text."


def _progress_signals(result: str, evidence_class: str, literature_result: dict[str, Any],
                      tool_result: tuple[str, str, str] | None, *, project: dict[str, Any],
                      step: dict[str, Any], store: ResearchStore, artifact_id: str) -> dict[str, Any]:
    """Persist proposed findings and count genuinely new records, never keywords alone."""
    workbench, domain = ResearchWorkbench(store), ResearchDomainStore(store)
    assumptions = (project.get("plan") or {}).get("assumptions", [])
    assumption_hash = hashlib.sha256(json.dumps(assumptions, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    checkpoint = workbench.checkpoint(project["id"], project["currentVersion"], step["id"]) or {}
    signals: dict[str, Any] = {key: bool(checkpoint.get("signals", {}).get(key)) for key in ("new_evidence", "new_hypothesis", "new_counterexample", "new_literature", "new_experiment", "new_lemma", "new_route", "new_information", "failed_attempt")}
    patterns = [("new_lemma", "lemma", r"^(?:new |proposed )?lemma\s*(?:\d+\s*)?[:.：-]\s*(.+)|^(?:新|提出的)?引理\s*\d*\s*[:：.]\s*(.+)"),
                ("new_hypothesis", "conjecture", r"^(?:new |proposed )?(?:conjecture|hypothesis|claim)\s*(?:[A-Z]?\d+\s*)?[:.：-]\s*(.+)|^(?:新)?(?:猜想|假设)\s*\d*\s*[:：.]\s*(.+)"),
                ("new_counterexample", "counterexample", r"^(?:counterexample (?:candidate|found|at)|counterexample|witness)\s*[:：-]?\s*(.+)|^(?:候选)?反例\s*[:：]?\s*(.+)")]
    for line in result.splitlines()[:200]:
        clean = line.strip(" #*-")
        for signal, node_type, pattern in patterns:
            match = re.search(pattern, clean, re.IGNORECASE)
            if not match:
                continue
            statement = next((value for value in match.groups() if value), "").strip()
            if re.match(r"(?i)^(?:none\b|no\b|not found\b|unknown\b|unable\b|could not\b|未发现|没有|无法|尚未)", statement):
                continue
            if len(statement) < 3 or not workbench.register_progress(project["id"], signal, statement, artifact_id):
                continue
            domain.create_graph_node(project["id"], node_type, statement[:200], statement, branch_name=str(step.get("branch") or "main"), artifact_id=artifact_id,
                                     metadata={"evidenceClass": "CONJECTURAL", "proposal": True, "stepId": step["id"], "planVersion": project["currentVersion"], "assumptions": (project.get("plan") or {}).get("assumptions", [])})
            signals[signal] = True
        if re.search(r"(?i)(?:route.*fail|proof attempt.*fail|could not prove|unable to establish|证明.*失败|路线.*失败)", clean):
            signals["failed_attempt"] = True
            if not any(path["stepId"] == step["id"] and path["reason"] == clean and path.get("assumptionHash") == assumption_hash for path in domain.failed_paths(project["id"])):
                domain.record_failed_path(project["id"], project["currentVersion"], step["id"], step["title"], str(step.get("branch") or "main"), "REJECTED_ROUTE", clean,
                                          route=clean, assumptions=assumptions, evidence_ids=[artifact_id], retry_condition="Retry only when the failed obligation, assumptions, or supporting evidence changes.")
            if workbench.register_progress(project["id"], "negative_route:" + assumption_hash, clean, artifact_id):
                signals["new_information"] = True
        if re.search(r"(?i)^(?:new (?:insight|finding)|insight|finding|新发现|新信息|发现)\s*[:：]", clean):
            information = re.split(r"[:：]", clean, maxsplit=1)[-1].strip()
            if not re.match(r"(?i)^(?:none\b|no\b|nothing\b|not found\b|unknown\b|unable\b|未发现|没有|无法|尚未)", information):
                signals["new_information"] = workbench.register_progress(project["id"], "new_information", clean, artifact_id) or signals["new_information"]
        if re.search(r"(?i)^(?:new |alternative |proposed )?(?:proof route|research path|route|strategy)\s*[:：]|^(?:新路线|研究路线|证明路线)\s*[:：]", clean):
            signals["new_route"] = workbench.register_progress(project["id"], "new_route", clean, artifact_id) or signals["new_route"]
    if tool_result:
        try:
            record = json.loads(tool_result[2])
        except (TypeError, ValueError):
            record = {}
        tool = str(record.get("tool") or "")
        material = {key: record.get(key) for key in ("tool", "input", "output", "status", "verificationLevel")}
        useful = record.get("ok") is True and record.get("workerExitCode") == 0 and tool != "capability_check"
        if useful and workbench.register_progress(project["id"], "new_experiment", material, tool_result[0]):
            kind = "counterexample" if evidence_class == "COUNTEREXAMPLE_FOUND" else "experiment" if tool == "run_python" else "evidence"
            domain.create_graph_node(project["id"], kind, "Executed " + tool + ": " + step["title"][:160], tool_result[2], branch_name=str(step.get("branch") or "main"), artifact_id=tool_result[0],
                                     metadata={"evidenceClass": evidence_class, "scope": "executed operation and its explicit inputs; proposed conclusions remain unverified", "stepId": step["id"], "planVersion": project["currentVersion"]})
            signals["new_experiment"] = True
            signals["new_evidence"] = evidence_class in {"NUMERICALLY_OBSERVED", "SYMBOLICALLY_VERIFIED", "FORMALLY_VERIFIED", "COUNTEREXAMPLE_FOUND", "CONTRADICTED"}
            signals["new_counterexample"] = evidence_class == "COUNTEREXAMPLE_FOUND" or signals["new_counterexample"]
        if record.get("ok") is not True or record.get("status") in {"UNKNOWN", "PROGRAM_FAILURE", "TOOL_FAILURE"}:
            signals["error_signature"] = hashlib.sha256(json.dumps({"tool": tool, "input": record.get("input"), "status": record.get("status"), "error": record.get("error")}, sort_keys=True, default=str).encode()).hexdigest()
    for source in literature_result.get("items", []):
        signals["new_literature"] = workbench.register_progress(project["id"], "new_literature", source["id"], source["id"]) or signals["new_literature"]
    return signals


async def _run_formal_step(project: dict[str, Any], step: dict[str, Any], store: ResearchStore) -> tuple[str, str, str]:
    from aleggo.study.lean_formal import LeanConfigurationError, run_kernel_check
    domain, workbench = ResearchDomainStore(store), ResearchWorkbench(store)
    binding = domain.get_formal_binding(project["id"], str(step.get("formalBindingId") or ""))
    proof = str(step.get("proofSource") or "")
    lean_settings = domain.lean_settings(project["id"])
    mapping_allowed = binding and (
        binding["mappingAuthority"] == "USER_CONFIRMED" or
        (not lean_settings["confirmBeforeCheck"] and binding["mappingAuthority"] in {"AI_PROPOSED", "UNCONFIRMED"})
    )
    if not mapping_allowed or not proof.strip():
        raise _ResearchBoundary("BLOCKED", "形式化验证需要可用的命题映射与明确证明来源；项目设置要求时还需研究者确认映射。", "FORMAL_MAPPING")
    _guard_execution(project, store, step)
    _live_phase(project, step, store, "formal_verification", "Running Lean kernel verification for " + binding["originalStatement"][:600], agent="independent-verifier", tool="lean-kernel")
    branch = domain.get_branch(project["id"], str(step.get("branch") or "main"))
    seconds_left = float((branch or {}).get("limits", {}).get("toolSeconds", 0)) - float((branch or {}).get("used", {}).get("toolSeconds", 0))
    if seconds_left <= 0:
        raise _ResearchBoundary("BLOCKED", "形式化检查工具的时间预算已用尽。", "BUDGET")
    # The step-preparation phase may already have reserved a proof attempt for
    # this step (checkpoint payload "budgetReserved"); don't charge twice.
    reserved = ((workbench.checkpoint(project["id"], project["currentVersion"], step["id"]) or {}).get("payload") or {}).get("budgetReserved")
    if not reserved:
        try:
            domain.consume_budget(project["id"], str(step.get("branch") or "main"), "proofAttempts", 1)
        except ValueError as exc:
            raise _ResearchBoundary("BLOCKED", str(exc), "BUDGET") from exc
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], tool_calls=1)
    started = time.monotonic()
    try:
        result = await run_kernel_check(project["id"], store.aleggo_store.root, binding["leanStatement"], proof, timeout_seconds=min(180.0, seconds_left))
    except LeanConfigurationError as exc:
        raise _ResearchBoundary("BLOCKED", str(exc), "LEAN_UNAVAILABLE") from exc
    elapsed = time.monotonic() - started
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=elapsed, failed=not result.get("passed"))
    try:
        domain.consume_budget(project["id"], str(step.get("branch") or "main"), "toolSeconds", elapsed)
    except ValueError as exc:
        raise _ResearchBoundary("BLOCKED", str(exc), "BUDGET") from exc
    updated = domain.record_formal_check(project["id"], binding["id"], proof, result)
    if result.get("passed") and result.get("exitCode") == 0 and result.get("status") == "KERNEL_CERTIFIED" and binding["mappingAuthority"] == "USER_CONFIRMED":
        domain.promote_formal_binding(project["id"], binding["id"])
    record = {"tool": "lean-kernel", "ok": bool(result.get("passed")), "status": result.get("status"), "verificationLevel": result.get("status"), "workerExitCode": result.get("exitCode"), "proofHash": updated["proofSourceHash"], "bindingHash": binding["bindingHash"], "mappingAuthority": binding["mappingAuthority"], "claimId": binding["claimNodeId"], "input": {"bindingId": binding["id"], "leanStatement": binding["leanStatement"], "proofSource": proof}, "output": result.get("output"), "environment": {"leanVersion": result.get("leanVersion")}}
    text = json.dumps(record, ensure_ascii=False)
    artifact = store.create_artifact(project["id"], project["currentVersion"], step["id"], "lean-kernel", "Lean verification: " + step["title"], text, status=str(result.get("status") or "CHECK_FAILED"), branch_name=str(step.get("branch") or "main"), metadata={"artifactKind": "formal-check", "bindingId": binding["id"], "proofSource": proof})
    domain.record_tool_run(project["id"], "lean-kernel", record["input"], text, record["status"], step_id=step["id"], branch_name=str(step.get("branch") or "main"), elapsed_ms=int(elapsed * 1000), metadata=record["environment"])
    workbench.save_checkpoint(project["id"], project["currentVersion"], step["id"], payload={"toolResult": [artifact["id"], "lean-kernel", text]})
    return artifact["id"], "lean-kernel", text


def _bounded_literature_queries(service: Any, project: dict[str, Any], step: dict[str, Any], requested: Any = None) -> list[str]:
    """Build bounded index queries while durable records retain the full question."""
    if requested is not None:
        values = requested if isinstance(requested, list) else [requested]
    else:
        values = service.targeted_queries(project["id"], step, "")
        values += [str(step.get("title") or "") + " " + str(step.get("description") or "")]
        values += service.strategy(project["id"]).get("queries", [])
    queries = list(dict.fromkeys(re.sub(r"\s+", " ", str(value)).strip()[:500] for value in values if isinstance(value, str)))
    queries = [query for query in queries if len(query) >= 3][:12]
    return queries or [str(project["question"]).strip()[:500]]


def _intervention_invocation(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    explicit = payload.get("invocation") or payload.get("toolInvocation")
    if isinstance(explicit, dict):
        return explicit
    if isinstance(payload.get("input"), dict) and payload.get("name"):
        return {"name": payload["name"], "input": payload["input"]}
    if kind in {"request_numerical", "request_counterexample"} and isinstance(payload.get("code"), str):
        return {"name": "run_python", "input": {key: value for key, value in payload.items() if key not in {"message", "queries", "bindingId", "proofSource"}}}
    if kind == "request_symbolic" and (payload.get("expression") or isinstance(payload.get("input"), dict)):
        return {"name": payload.get("name") or payload.get("tool") or "symbolic_simplify", "input": payload.get("input") if isinstance(payload.get("input"), dict) else {key: value for key, value in payload.items() if key not in {"message", "tool", "name"}}}
    return None


async def _run_literature_step(project: dict[str, Any], step: dict[str, Any], store: ResearchStore) -> tuple[str, str, str]:
    from aleggo.study.research_literature_intelligence import LiteratureIntelligence
    _guard_execution(project, store, step)
    workbench = ResearchWorkbench(store)
    _live_phase(project, step, store, "literature_search", "Searching academic metadata for " + project["question"] + ": " + step["title"], agent="literature-intelligence", tool="academic-index-apis")
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], tool_calls=1)
    started = time.monotonic()
    service = LiteratureIntelligence(store)
    queries = _bounded_literature_queries(service, project, step)
    result = await service.search(project["id"], queries=queries, trigger_type="approved_step", trigger_id="", step_id=step["id"], branch=str(step.get("branch") or "main"), limit=20, max_pages=1, guard=lambda: _guard_execution(project, store))
    workbench.charge_step(project["id"], project["currentVersion"], step["id"], elapsed_seconds=time.monotonic() - started)
    text = json.dumps({"tool": "academic-index-apis", "ok": bool(result.get("providers")), "status": result.get("status"), "input": {"question": project["question"], "step": step["description"], "queries": queries}, "output": result, "evidenceScope": "retrieved metadata; no theorem verification"}, ensure_ascii=False)
    artifact = store.create_artifact(project["id"], project["currentVersion"], step["id"], "literature-intelligence", "Literature search: " + step["title"], text, status="LITERATURE_METADATA", branch_name=str(step.get("branch") or "main"), metadata={"artifactKind": "literature-search", "searchId": result.get("searchId")})
    current = workbench.checkpoint(project["id"], project["currentVersion"], step["id"]) or {}
    signals = dict(current.get("signals", {}))
    for source in result.get("items", []):
        signals["new_literature"] = workbench.register_progress(project["id"], "new_literature", source["id"], source["id"]) or signals.get("new_literature", False)
    workbench.save_checkpoint(project["id"], project["currentVersion"], step["id"], payload={"toolResult": [artifact["id"], "academic-index-apis", text], "initialLiteratureResult": result}, signals=signals)
    if not result.get("providers"):
        raise _ResearchBoundary("BLOCKED", "文献检索已实现但外部服务不可用。" + json.dumps(result.get("warnings", {}), ensure_ascii=False), "LITERATURE_UNAVAILABLE")
    return artifact["id"], "academic-index-apis", text


def _scoped_sources(project_id: str, step_id: str, domain: ResearchDomainStore) -> list[dict[str, Any]]:
    by_id = {source["id"]: source for source in domain.list_sources(project_id)}
    rejected = [node for node in domain.list_graph(project_id)["nodes"]
                if node.get("metadata", {}).get("stepId") == step_id and
                (str(node.get("status") or "").casefold() in {"rejected", "human-marked-invalid", "invalid"}
                 or node.get("metadata", {}).get("humanFeedback") == "invalid")]
    # A generic link to a step cannot restore researcher-rejected claims.
    # A fresh, explicit claim relationship must be reviewed instead.
    if rejected:
        return []
    return [{**by_id[link["sourceId"]], "scopeStepId": step_id, "relationship": link["relationship"]}
            for link in domain.list_literature_links(project_id)
            if link["entityType"] == "step" and link["entityId"] == step_id and link["sourceId"] in by_id
            and by_id[link["sourceId"]].get("metadata", {}).get("humanFeedback") != "invalid"]


async def _process_interventions(project: dict[str, Any], plan: dict[str, Any], store: ResearchStore) -> None:
    workbench, domain = ResearchWorkbench(store), ResearchDomainStore(store)
    with store.connection() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM research_intervention_queue WHERE project_id=? AND status='QUEUED' AND intervention_type<>'discuss' ORDER BY created_at", (project["id"],)).fetchall()]
    for row in rows:
        _guard_execution(project, store)
        try:
            details = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        payload = details.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        kind = {"request_counterexample_search": "request_counterexample", "request_symbolic_check": "request_symbolic", "request_critique_attack": "request_critique"}.get(row["intervention_type"], row["intervention_type"])
        tool = "python" if kind in {"request_numerical", "request_counterexample"} else "sympy" if kind == "request_symbolic" else "Lean" if kind == "request_verification" else ""
        step = {"id": "intervention:" + row["id"], "title": "Researcher requested " + kind.replace("request_", ""), "description": str(details.get("message") or payload.get("message") or "Carry out the requested bounded research action and report uncertainty."), "branch": details.get("branch") or "main", "requiredTools": [tool] if tool else [], "formalBindingId": payload.get("bindingId"), "proofSource": payload.get("proofSource"), "expectedOutput": "A durable result artifact with reproducible inputs and explicit evidence boundaries.", "verificationMethod": "Use executed machine records; model prose remains conjectural."}
        step["requestedParameters"] = payload
        step["explicitValidatedInvocation"] = _intervention_invocation(kind, payload)
        workbench.begin_step(project["id"], project["currentVersion"], step, "Researcher intervention")
        prior_checkpoint = workbench.checkpoint(project["id"], project["currentVersion"], step["id"]) or {}
        if prior_checkpoint.get("status") == "COMPLETED" and prior_checkpoint.get("payload", {}).get("artifactId"):
            with store.connection() as conn:
                conn.execute("UPDATE research_intervention_queue SET status='COMPLETED',result_artifact_id=?,updated_at=? WHERE id=?", (prior_checkpoint["payload"]["artifactId"], datetime.now(UTC).isoformat(), row["id"]))
            continue
        blocker = _required_tool_blocker(step)
        try:
            if blocker:
                raise _ResearchBoundary("BLOCKED", blocker, "TOOL_UNAVAILABLE")
            with store.connection() as conn:
                conn.execute("UPDATE research_intervention_queue SET status='RUNNING',updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), row["id"]))
            if kind == "request_literature":
                from aleggo.study.research_literature_intelligence import LiteratureIntelligence
                _live_phase(project, step, store, "literature_search", "Searching literature requested by the researcher: " + step["description"][:600], agent="literature-intelligence", tool="academic-index-apis")
                workbench.charge_step(project["id"], project["currentVersion"], step["id"], tool_calls=1)
                service = LiteratureIntelligence(store)
                queries = _bounded_literature_queries(service, project, step, payload.get("queries") or [step["description"]])
                result = await service.search(project["id"], queries=queries, trigger_type="human_request", trigger_id=row["id"], step_id=step["id"], branch=step["branch"], limit=20, max_pages=1, guard=lambda: _guard_execution(project, store))
                content = json.dumps(result, ensure_ascii=False, indent=2)
            elif kind == "request_verification":
                evidence = await _run_formal_step(project, step, store)
                content = evidence[2]
            else:
                evidence = await _run_planned_tool(project, step, store) if tool else None
                _guard_execution(project, store, step)
                role = "independent-verifier" if kind == "request_critique" else "proof-builder" if kind == "request_proof" else "experimental-mathematician"
                content = await _run_role_recorded(project, plan, step, role, domain.research_context(project["id"], step["description"]) + "\nExact researcher request parameters:\n" + json.dumps(payload, ensure_ascii=False) + ("\n" + evidence[2] if evidence else ""), store)
            artifact = store.create_artifact(project["id"], project["currentVersion"], step["id"], "researcher-intervention", step["title"], content, status="CONJECTURAL" if kind != "request_verification" else "FORMAL_CHECK_RECORD", branch_name=step["branch"], metadata={"artifactKind": "intervention-result", "requestId": row["id"], "requestedBy": "researcher"})
            workbench.save_checkpoint(project["id"], project["currentVersion"], step["id"], phase="complete", status="COMPLETED", payload={"artifactId": artifact["id"]})
            with store.connection() as conn:
                conn.execute("UPDATE research_intervention_queue SET status='COMPLETED',result_artifact_id=?,updated_at=? WHERE id=?", (artifact["id"], datetime.now(UTC).isoformat(), row["id"]))
            workbench.record_event(project["id"], "intervention_completed", "researcher-intervention", step_id=step["id"], branch=step["branch"], summary=step["title"], details={"requestId": row["id"], "artifactId": artifact["id"]})
            _guard_execution(project, store)
        except _ResearchBoundary as exc:
            exc.research_step = step
            artifact = store.create_artifact(project["id"], project["currentVersion"], step["id"], "researcher-intervention", "干预被阻止", str(exc), status="BLOCKED", metadata={"requestId": row["id"]})
            with store.connection() as conn:
                conn.execute("UPDATE research_intervention_queue SET status='BLOCKED',result_artifact_id=?,updated_at=? WHERE id=?", (artifact["id"], datetime.now(UTC).isoformat(), row["id"]))
            raise
        except (_ResearchControlInterrupt, asyncio.CancelledError):
            with store.connection() as conn:
                conn.execute("UPDATE research_intervention_queue SET status='QUEUED',updated_at=? WHERE id=? AND status='RUNNING'", (datetime.now(UTC).isoformat(), row["id"]))
            raise


async def _execute_project(project_id: str, store: ResearchStore, *, resume: bool = False) -> None:
    project = store.get_project(project_id)
    if not project:
        return
    version = project["approvedVersion"]
    if version is None or project["currentVersion"] != version:
        store.set_status(project_id, "AWAITING_APPROVAL")
        return
    plan = project.get("plan") or {}
    ResearchInputs(store).register(project_id, plan.get("inputRequests", []))
    dependency_error = validate_plan_dependencies(plan.get("steps", []))
    if not plan.get("steps") or dependency_error:
        store.set_status(project_id, "FAILED", error=dependency_error or "Approved plan has no research steps.")
        return
    project["_guarded"] = True
    workbench, domain = ResearchWorkbench(store), ResearchDomainStore(store)
    ordered = sorted(plan["steps"], key=lambda item: (item.get("order", 0), -item.get("priority", 3)))
    steps = [step for step in ordered if step.get("enabled", True)]
    milestones = list(dict.fromkeys(_step_milestone(step, index) for index, step in enumerate(steps)))
    completed = {str(s["id"]) for s in ordered if s.get("status") == "completed"}
    active_step: dict[str, Any] | None = None
    if not resume:
        store.set_status(project_id, "RUNNING")
    try:
        for ordinal, step in enumerate(steps):
            active_step = step
            _guard_execution(project, store)
            await _process_interventions(project, plan, store)
            _guard_execution(project, store)
            milestone = _step_milestone(step, ordinal)
            checkpoint = workbench.begin_step(project_id, version, step, milestone)
            if resume and checkpoint["status"] == "STALLED":
                workbench.reset_stalled_attempt(project_id, version, step["id"])
                checkpoint = workbench.checkpoint(project_id, version, step["id"]) or checkpoint
            saved = checkpoint["payload"]
            # Continue acknowledges a saved review boundary exactly once.
            if saved.get("reviewPending") and resume:
                workbench.save_checkpoint(project_id, version, step["id"], payload={"reviewPending": False, "reviewedAt": datetime.now(UTC).isoformat()})
                workbench.record_event(project_id, "stage_review_continued", "researcher", step_id=step["id"], summary="Researcher continued after reviewing saved stage artifacts.")
            if checkpoint["status"] == "COMPLETED" or step["id"] in completed:
                completed.add(str(step["id"]))
                continue
            missing = [value for value in step.get("dependencies", []) if value not in completed]
            if missing:
                raise _ResearchBoundary("BLOCKED", "依赖未完成：" + ", ".join(missing), "DEPENDENCY")
            branch = str(step.get("branch") or "main")
            agent = _role_for_step(step, ordinal)
            workbench.set_live(project_id, step_id=step["id"], agent=agent, action="Preparing " + step["title"] + ": " + step.get("description", "")[:400], branch=branch, milestone=milestone, reason="RUNNING", phase=checkpoint["phase"], step_index=ordinal + 1, step_total=len(steps), milestone_index=milestones.index(milestone) + 1, milestone_total=len(milestones))
            store.update_step(project_id, version, step["id"], "running")
            if not saved.get("budgetReserved"):
                try:
                    domain.consume_budget(project_id, branch, "evaluations", 1)
                    if project["modelId"] == "research.axiom" or agent == "proof-builder":
                        domain.consume_budget(project_id, branch, "proofAttempts", 1)
                except ValueError as exc:
                    raise _ResearchBoundary("BLOCKED", str(exc), "BUDGET") from exc
                workbench.save_checkpoint(project_id, version, step["id"], payload={"budgetReserved": True})
                workbench.record_event(project_id, "step_started", agent, step_id=step["id"], branch=branch, summary="开始步骤：" + step["title"], details={"milestone": milestone})
            saved = (workbench.checkpoint(project_id, version, step["id"]) or {})["payload"]
            tool_result = tuple(saved["toolResult"]) if saved.get("toolResult") else None
            if "executionResult" not in saved:
                lean_settings = domain.lean_settings(project_id)
                blocker = _required_tool_blocker(step, auto_lean_enabled=lean_settings["autoCallEnabled"])
                if blocker:
                    raise _ResearchBoundary("BLOCKED", blocker, "TOOL_UNAVAILABLE")
                if not tool_result or json.loads(tool_result[2]).get("status") in {"TOOL_FAILURE", "RESOURCE_BUDGET_EXCEEDED", "NOT_EXECUTED"}:
                    capabilities = [canonical_requirement(tool) for tool in executable_requirements(step.get("requiredTools", []))]
                    formal = "lean" in capabilities
                    literature_only = bool(capabilities) and all(item == "literature" for item in capabilities)
                    tool_result = await _run_formal_step(project, step, store) if formal else await _run_literature_step(project, step, store) if literature_only else await _run_planned_tool(project, step, store)
                _guard_execution(project, store, step)
                saved = (workbench.checkpoint(project_id, version, step["id"]) or {})["payload"]
                prior = saved.get("executionPrior")
                if prior is None:
                    prior = domain.research_context(project_id, project["question"] + " " + step["title"] + " " + step["description"])
                    if tool_result:
                        prior += "\n\nExecuted tool evidence (scope is explicit inputs):\n" + tool_result[2]
                    workbench.save_checkpoint(project_id, version, step["id"], payload={"executionPrior": prior})
                result = await _run_axiom_team(project, plan, step, agent, prior, store) if project["modelId"] == "research.axiom" else await _run_role_recorded(project, plan, step, agent, prior, store)
                output = next((a for a in store.list_artifacts(project_id) if a["planVersion"] == version and a["stepId"] == step["id"] and a["content"] == result), None)
                if output is None:
                    output = store.create_artifact(project_id, version, step["id"], agent, "Step output: " + step["title"], result, status="CONJECTURAL", branch_name=branch, metadata={"artifactKind": "step-output", "evidenceClass": "CONJECTURAL"})
                workbench.save_checkpoint(project_id, version, step["id"], phase="findings", payload={"executionResult": result, "outputArtifactId": output["id"], "toolResult": list(tool_result) if tool_result else None})
            saved = (workbench.checkpoint(project_id, version, step["id"]) or {})["payload"]
            result, output_id = saved["executionResult"], saved["outputArtifactId"]
            tool_result = tuple(saved["toolResult"]) if saved.get("toolResult") else None
            tool_record = json.loads(tool_result[2]) if tool_result else {}
            sources = _scoped_sources(project_id, step["id"], domain)
            class_name = evidence_classification(tool_result[2] if tool_result else "", result, sources, step_id=step["id"], claim_id=str(tool_record.get("claimId") or ""))
            # Persist typed findings before literature retrieval and budget evaluation.
            if not saved.get("findingsSaved"):
                signals = _progress_signals(result, class_name, {}, tool_result, project=project, step=step, store=store, artifact_id=output_id)
                workbench.save_checkpoint(project_id, version, step["id"], phase="literature", payload={"findingsSaved": True, "evidenceClass": class_name}, signals=signals)
            _guard_execution(project, store, step)
            saved = (workbench.checkpoint(project_id, version, step["id"]) or {})["payload"]
            if "literatureResult" not in saved:
                new_sources, literature = await _targeted_literature(project, plan, step, result, output_id, store)
                current = workbench.checkpoint(project_id, version, step["id"]) or {}
                initial_literature = current["payload"].get("initialLiteratureResult", {})
                all_sources = list({source["id"]: source for source in initial_literature.get("items", []) + new_sources + sources}.values())
                if initial_literature:
                    literature = {**literature, "initialSearch": initial_literature}
                signals = dict(current["signals"])
                for source in new_sources:
                    signals["new_literature"] = workbench.register_progress(project_id, "new_literature", source["id"], source["id"]) or signals.get("new_literature", False)
                workbench.save_checkpoint(project_id, version, step["id"], phase="recommendation", payload={"literatureResult": literature, "sourcesUsed": all_sources}, signals=signals)
            _guard_execution(project, store, step)
            saved = (workbench.checkpoint(project_id, version, step["id"]) or {})["payload"]
            if "recommendation" not in saved:
                recommendation = await _recommend_next_path(project, plan, step, result, store)
                workbench.save_checkpoint(project_id, version, step["id"], phase="report", payload={"recommendation": recommendation})
            _guard_execution(project, store)
            saved = (workbench.checkpoint(project_id, version, step["id"]) or {})["payload"]
            if not saved.get("reportArtifactId"):
                _live_phase(project, step, store, "artifact_generation", "Generating the step report, evidence inventory, uncertainty and next actions for " + step["title"], agent="research-step-report")
                structured_report = _structured_step_report(project, plan, step, result, tool_result, saved.get("sourcesUsed", []), {**saved["recommendation"], "literature": saved["literatureResult"]}, class_name, store)
                report = _step_report(project, plan, step, result, tool_result, saved.get("sourcesUsed", []), {**saved["recommendation"], "literature": saved["literatureResult"]}, class_name, structured=structured_report)
                report += "\n\n## Generated files and linked findings\n\n" + "\n".join("- " + a["title"] + " [" + a["id"] + "]" for a in store.list_artifacts(project_id) if a["planVersion"] == version and a["stepId"] == step["id"])
                artifact = store.create_artifact(project_id, version, step["id"], "research-step-report", f"Step {ordinal + 1:02d} · {step['title']}", report, status=class_name, branch_name=branch, metadata={"artifactKind": "step-report", "structuredReport": structured_report, "evidenceClass": class_name, "evidenceScope": "executed operations and explicitly linked sources; proposed claims retain their own evidence class", "milestone": milestone, "literatureStatus": saved["literatureResult"].get("status"), "recommendationId": saved["recommendation"].get("id"), "input": {"question": project["question"], "step": step}})
                workbench.save_checkpoint(project_id, version, step["id"], phase="progress", payload={"reportArtifactId": artifact["id"]})
                workbench.record_event(project_id, "step_report_created", "research-step-report", step_id=step["id"], branch=branch, summary="Saved formal step report.", details={"artifactId": artifact["id"], "evidenceClass": class_name})
            _guard_execution(project, store)
            checkpoint = workbench.checkpoint(project_id, version, step["id"]) or {}
            saved = checkpoint["payload"]
            if "progressVerdict" not in saved:
                verdict = workbench.observe_attempt(project_id, step["id"], result, checkpoint["signals"])
                budget_verdict = workbench.evaluate_budget(project_id, version, step["id"], force=True)
                if budget_verdict["state"] == "STALLED":
                    verdict = budget_verdict
                workbench.save_checkpoint(project_id, version, step["id"], payload={"progressVerdict": verdict})
            else:
                verdict = saved["progressVerdict"]
            if verdict["state"] in {"STALLED", "BLOCKED"}:
                raise _ResearchBoundary(verdict["state"], verdict["reason"])
            # A completed step always has a durable report and all delivery records.
            workbench.save_checkpoint(project_id, version, step["id"], phase="complete", status="COMPLETED")
            store.update_step(project_id, version, step["id"], "completed")
            completed.add(str(step["id"]))
            workbench.record_event(project_id, "step_completed", agent, step_id=step["id"], branch=branch, summary="完成步骤：" + step["title"], details={"reportArtifactId": saved["reportArtifactId"], "milestone": milestone})
            at_milestone_boundary = ordinal == len(steps) - 1 or _step_milestone(steps[ordinal + 1], ordinal + 1) != milestone
            milestone_report = None
            if at_milestone_boundary:
                milestone_ids = [s["id"] for index, s in enumerate(steps[:ordinal + 1]) if _step_milestone(s, index) == milestone]
                milestone_report = workbench.complete_milestone(project_id, version, milestone, milestone_ids)
            _guard_execution(project, store)
            mode = workbench.state(project_id)["supervisionMode"]
            if mode == "STEP_BY_STEP" or (mode == "MILESTONE_REVIEW" and at_milestone_boundary):
                reason = "STEP_REVIEW_REQUIRED" if mode == "STEP_BY_STEP" else "MILESTONE_REVIEW_REQUIRED"
                workbench.save_checkpoint(project_id, version, step["id"], payload={"reviewPending": True})
                workbench.set_live(project_id, step_id=step["id"], agent=agent, action="Review saved " + ("step" if mode == "STEP_BY_STEP" else "milestone") + " findings and choose Continue, Modify Plan, Pause or Stop.", branch=branch, milestone=milestone, reason=reason, phase="review")
                store.set_status(project_id, "PAUSED", error="Research artifacts and evidence snapshot are ready for researcher review.", actor="supervision-controller")
                workbench.record_event(project_id, "research_review_required", "supervision-controller", step_id=step["id"], branch=branch, summary=reason, details={"mode": mode, "artifactId": milestone_report["artifactId"] if mode == "MILESTONE_REVIEW" and milestone_report else saved["reportArtifactId"], "milestone": milestone})
                workbench.sync_workspace(project_id)
                return
            workbench.sync_workspace(project_id)
        await _process_interventions(project, plan, store)
        _guard_execution(project, store)
        final_step = {"id": "final-verification", "title": "Verify recorded evidence boundaries and prepare final report", "description": "Summarize actual formal and symbolic records, experiments, failures and remaining obligations. Proposed proofs remain unverified.", "branch": "main"}
        active_step = final_step
        workbench.begin_step(project_id, version, final_step, "Final verification")
        store.set_status(project_id, "VERIFYING")
        _live_phase(project, final_step, store, "final_verification", "Checking recorded verification levels, user-confirmed formal mappings and unresolved claims", agent="independent-verifier")
        final_checkpoint = workbench.checkpoint(project_id, version, final_step["id"]) or {}
        if not final_checkpoint["payload"].get("reportArtifactId"):
            verification = {"formalBindings": domain.list_formal_bindings(project_id), "typedClaims": [n for n in domain.list_graph(project_id)["nodes"] if n["type"] in {"conjecture", "lemma", "proposition", "theorem"}], "uncertainty": "Numerical observations are not proof; only exact bound kernel checks with confirmed mappings establish formally verified claims."}
            prior = final_checkpoint["payload"].get("executionPrior")
            if prior is None:
                prior = json.dumps({"artifacts": store.list_artifacts(project_id), "verification": verification}, ensure_ascii=False)[-40000:]
                workbench.save_checkpoint(project_id, version, final_step["id"], payload={"executionPrior": prior})
            review = final_checkpoint["payload"].get("verificationReview")
            if review is None:
                review = await _run_role_recorded(project, plan, final_step, "independent-verifier", prior, store)
                workbench.save_checkpoint(project_id, version, final_step["id"], payload={"verificationReview": review})
            _guard_execution(project, store)
            synthesis = await _run_role_recorded(project, plan, final_step, "research-synthesizer", prior + "\n\nIndependent review (an argument review, not a kernel certificate):\n" + review, store)
            artifact = store.create_artifact(project_id, version, final_step["id"], "independent-verifier", "Final verification and unresolved claims", "# Verification and unresolved claims\n\n" + json.dumps(verification, ensure_ascii=False, indent=2) + "\n\n## Independent review\n\n" + review + "\n\n## Synthesis\n\n" + synthesis, status="UNVERIFIED_SYNTHESIS", metadata={"artifactKind": "final-verification", "verification": verification, "independentReview": review})
            workbench.save_checkpoint(project_id, version, final_step["id"], phase="delivery", payload={"reportArtifactId": artifact["id"]})
        _guard_execution(project, store)
        workbench.sync_workspace(project_id)
        _guard_execution(project, store)
        workbench.save_checkpoint(project_id, version, final_step["id"], phase="complete", status="COMPLETED")
        workbench.set_live(project_id, action="Final research package ready", reason="COMPLETED", clear_run_start=True, phase="complete")
        store.set_status(project_id, "COMPLETED")
        workbench.record_event(project_id, "final_package_created", "research-synthesizer", summary="Final report, research records and reproducibility package are ready.")
        workbench.sync_workspace(project_id)
    except (_ResearchControlInterrupt, asyncio.CancelledError):
        # Completed outputs and checkpoints remain durable; never overwrite a
        # researcher-selected status while unwinding an interrupted call.
        if active_step:
            current = workbench.checkpoint(project_id, version, active_step["id"])
            if current and current["status"] != "COMPLETED":
                workbench.save_checkpoint(project_id, version, active_step["id"], status="INTERRUPTED")
        timed = project.get("_timedStep")
        timed_checkpoint = workbench.checkpoint(project_id, version, timed) if timed else None
        if timed_checkpoint and timed_checkpoint["status"] != "COMPLETED":
            workbench.save_checkpoint(project_id, version, timed, resources={**timed_checkpoint["resources"], "elapsedSeconds": project.get("_stepElapsedBaseline", 0) + time.monotonic() - project["_stepStarted"]})
        workbench.sync_workspace(project_id)
        return
    except _ResearchBoundary as exc:
        if (store.get_project(project_id) or {}).get("status") not in {"RUNNING", "VERIFYING"}:
            return
        step = getattr(exc, "research_step", None) or active_step or {"id": "", "title": "Research"}
        workbench.set_live(project_id, step_id=step["id"], action="Research paused: " + exc.reason + ". " + exc.detail[:1000], branch=str(step.get("branch") or "main"), reason="MISSING_INPUT" if exc.blocker_type == "MISSING_INPUT" else exc.reason, blocker_type=exc.blocker_type, blocker_detail=exc.detail, phase="waiting_for_input" if exc.blocker_type == "MISSING_INPUT" else "blocked" if exc.reason == "BLOCKED" else "stalled")
        if active_step:
            workbench.save_checkpoint(project_id, version, step["id"], status=exc.reason)
            store.update_step(project_id, version, step["id"], "blocked" if exc.reason == "BLOCKED" else "pending")
        checkpoint = workbench.checkpoint(project_id, version, step["id"]) if step["id"] else None
        store.create_artifact(project_id, version, step["id"], "research-budget-guard", exc.reason + ": " + step["title"], "# Research paused — " + exc.reason + "\n\n## Why execution stopped\n\n" + exc.detail + "\n\n## Saved attempts and resources\n\n" + json.dumps(checkpoint or {}, ensure_ascii=False, indent=2) + ("\n\nSupply the required information in the research input panel before continuing. Saved steps will be retained." if exc.blocker_type == "MISSING_INPUT" else "\n\nReview the blocker and adjust the plan or execution settings before continuing. Saved steps will be retained."), status=exc.reason, branch_name=str(step.get("branch") or "main"), metadata={"artifactKind": "stop-report", "reason": exc.reason})
        store.set_status(project_id, "PAUSED", error=exc.detail, actor="research-budget-guard")
        workbench.record_event(project_id, "research_stalled" if exc.reason == "STALLED" else "research_blocked", "research-budget-guard", step_id=step["id"], summary=exc.detail, details={"reason": exc.reason, "blockerType": exc.blocker_type})
        workbench.sync_workspace(project_id)
    except Exception as exc:
        if (store.get_project(project_id) or {}).get("status") not in {"RUNNING", "VERIFYING"}:
            return
        diagnostic = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[redacted]", str(exc))
        diagnostic = re.sub(r"(?i)(api[_-]?key|token|secret)(\s*[:=]\s*)[^\s,&]+", r"\1\2[redacted]", diagnostic)
        if active_step:
            domain.record_failed_path(project_id, version, active_step["id"], active_step["title"], str(active_step.get("branch") or "main"), type(exc).__name__, diagnostic)
            workbench.save_checkpoint(project_id, version, active_step["id"], status="FAILED")
        blocked = bool(re.search(r"(?i)(api key|credential|model access|not configured|unavailable tool|Lean is unavailable)", diagnostic))
        reason = "BLOCKED" if blocked else "FAILED"
        workbench.set_live(project_id, step_id=active_step["id"] if active_step else "", action="Execution stopped; saved research is preserved", reason=reason, blocker_type="DEPENDENCY_UNAVAILABLE" if blocked else "EXECUTION_FAILURE", blocker_detail=diagnostic, phase="blocked" if blocked else "failed")
        store.set_status(project_id, "PAUSED" if blocked else "FAILED", error=diagnostic, actor="runtime")
        workbench.record_event(project_id, "research_blocked" if blocked else "research_failed", "runtime", summary=diagnostic)
        workbench.sync_workspace(project_id)


async def start_project(project_id: str, store: ResearchStore, *, resume: bool = False) -> None:
    project = store.get_project(project_id)
    if not project:
        raise KeyError("Research project not found.")
    if project["approvedVersion"] is None or project["approvedVersion"] != project["currentVersion"]:
        raise PermissionError("Approve the current research plan before starting research.")
    ResearchInputs(store).register(project_id, (project.get("plan") or {}).get("inputRequests", []))
    ResearchInputs(store).guard(project_id)
    if resume and project["status"] != "PAUSED":
        raise ValueError("仅已暂停的研究运行可以恢复。")
    async with _RUNS_LOCK:
        task = _RUNS.get(project_id)
        live = task is not None and not task.done()
        if live and not resume:
            # A run is genuinely in flight; starting again would duplicate it.
            return
        if live:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # NOTE: a stale "RUNNING" status with no live task (e.g. the server
        # restarted mid-run) intentionally falls through to a fresh start
        # instead of silently doing nothing forever.
        current = store.get_project(project_id)
        if not current or current["approvedVersion"] != current["currentVersion"]:
            raise PermissionError("Approve the current research plan before starting research.")
        ResearchInputs(store).guard(project_id)
        store.set_status(project_id, "RUNNING")
        task = asyncio.create_task(MultiAgentRuntime(store).execute_project(project_id, resume=resume))
        _RUNS[project_id] = task
        task.add_done_callback(lambda done, key=project_id: _RUNS.pop(key, None) if _RUNS.get(key) is done else None)


async def stop_project(project_id: str, store: ResearchStore) -> None:
    # The lock keeps the get/cancel/await sequence atomic against
    # start_project's check-and-register and the task done-callback's pop.
    # The done-callback never takes this lock, so awaiting under it cannot
    # deadlock.
    async with _RUNS_LOCK:
        task = _RUNS.get(project_id)
        if task is not None and not task.done():
            task.cancel()
            if task is not asyncio.current_task():
                await asyncio.gather(task, return_exceptions=True)


def validate_research_model(model_id: str) -> bool:
    return bool(str(model_id or "").strip())


class MultiAgentRuntime:
    """Plan-bound executor adapted from the Math Research Agent role model.

    Axiom dispatches concurrent specialist agents, then concurrent skeptic and
    verifier roles, and persists each contribution before synthesis. Research
    plan changes remain human-approved domain revisions.
    """

    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    async def propose_plan(self, question: str, context: str, model_id: str) -> dict[str, Any]:
        return await generate_plan(question, context, model_id)

    async def execute_project(self, project_id: str, *, resume: bool = False) -> None:
        await _execute_project(project_id, self.store, resume=resume)
