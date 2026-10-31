"""AI-drafted Lean proofs checked by the real Lean kernel, with feedback.

For a researcher-confirmed formal binding the model drafts a tactic proof,
the server validates it with the same safety filter as manual checks, the
pinned Lean + Mathlib kernel checks it, and the *real* error output goes back
to the model for the next attempt — until the kernel certifies a proof or the
attempt/budget limit is reached.  Only a proof the kernel accepted is ever
reported as passed; the model's opinion never is.

Jobs run in the background (``start``); ``status`` is polled.  Every attempt
is recorded as a ``lean_kernel_check`` tool run, the model calls as
``llm:lean-prover`` runs, and the final (passing or last) attempt goes through
``record_formal_check`` so promotion works exactly as for a manual proof.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import re
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

from aleggo.study.lean_formal import (
    LeanConfigurationError,
    UnsafeLeanInput,
    _validate,
    detect_lean_workspace,
    run_kernel_check,
)
from aleggo.study.research_domain import ResearchDomainStore
from aleggo.study.research_store import ResearchStore

MAX_ATTEMPTS = 6
ROLE = "lean-prover"
_JOBS: dict[str, dict[str, Any]] = {}
_TASKS: dict[str, asyncio.Task] = {}
_START_LOCK = __import__('threading').Lock()

Completion = Callable[[list[dict[str, str]]], Awaitable[dict[str, Any]]]
Checker = Callable[..., Awaitable[dict[str, Any]]]

SYSTEM = (
    "You are an expert Lean 4 + Mathlib prover. Write a tactic proof for `example : <statement> := by` using the "
    "pinned Mathlib (imported as `import Mathlib`). Rules enforced by the checker: output ONLY the tactic block that "
    "follows `by` (no `theorem`/`lemma`/`def`/`example` header, no `import`, no `open` — use fully qualified names "
    "such as `Nat.Prime` or `Real.sqrt`), and never use `sorry`, `admit`, `axiom`, `native_decide`, `#eval` or other "
    "commands. Prefer robust tactics: intro, rcases/obtain, induction, cases, simp, simp_all, omega, decide, norm_num, "
    "linarith, nlinarith, positivity, ring, field_simp, aesop, exact?-free explicit lemma names, calc blocks. "
    "Reply with ONLY JSON: {\"proof\": \"<tactic block, newline separated>\", \"explanation\": \"<one or two sentences>\"}."
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(text: str) -> tuple[str, str]:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, re.DOTALL)
    if fenced:
        value = fenced.group(1)
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(value[start:end + 1])
            if isinstance(parsed, dict) and str(parsed.get("proof") or "").strip():
                return _strip_header(str(parsed["proof"])), str(parsed.get("explanation") or "")[:1000]
        except ValueError:
            pass
    lean = re.search(r"```(?:lean4?|)\s*\n(.*?)```", text, re.DOTALL)
    if lean:
        return _strip_header(lean.group(1)), ""
    raise ValueError("模型未返回可用证明。")


def _strip_header(proof: str) -> str:
    text = proof.strip().replace("\r\n", "\n")
    # Tolerate a model that repeats `example : ... := by` or a leading `by`.
    text = re.sub(r"^\s*(?:example|theorem\s+\S+)[^\n]*:=\s*by\s*\n?", "", text)
    text = re.sub(r"^\s*by\s*\n", "", text)
    lines = text.splitlines()
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    common = min(indents) if indents else 0
    return "\n".join(line[common:] for line in lines).strip()


def _kernel_feedback(output: str) -> str:
    # Hide server paths; the model only needs line:column positions.
    text = re.sub(r"\S*check-[A-Za-z0-9_]+\.lean", "Proof.lean", str(output or ""))
    errors = [block for block in re.split(r"\n(?=\S[^\n]*:\d+:\d+:)", text) if "error" in block]
    return ("\n".join(errors) or text)[-3500:]


def job_view(job: dict[str, Any]) -> dict[str, Any]:
    return {key: job.get(key) for key in ("id", "bindingId", "state", "attempts", "passed", "proof", "error",
                                           "startedAt", "finishedAt", "maxAttempts", "usage", "artifactId")}


def status(project_id: str, binding_id: str, store: ResearchStore) -> dict[str, Any]:
    for job in reversed(list(_JOBS.values())):
        if job["projectId"] == project_id and job["bindingId"] == binding_id:
            # Stale in-memory RUNNING with no live task (e.g. cancelled waiter lost) → FAILED.
            if job.get("state") == "RUNNING" and job["id"] not in _TASKS:
                job.update(state="FAILED", error="证明任务已中断（进程内任务丢失）。", finishedAt=job.get("finishedAt") or _now())
            return job_view(job)
    # After a restart only the stored summary remains.
    for artifact in store.list_artifacts(project_id):
        metadata = artifact.get("metadata") or {}
        if artifact["agentRole"] == ROLE and metadata.get("bindingId") == binding_id:
            return {"id": metadata.get("jobId", ""), "bindingId": binding_id, "state": metadata.get("state", "COMPLETED"),
                    "attempts": metadata.get("attempts", []), "passed": bool(metadata.get("passed")),
                    "proof": metadata.get("proof", ""), "error": metadata.get("error", ""), "artifactId": artifact["id"],
                    "startedAt": metadata.get("startedAt", ""), "finishedAt": metadata.get("finishedAt", ""),
                    "maxAttempts": metadata.get("maxAttempts", 0), "usage": metadata.get("usage", {})}
    return {"state": "NONE", "bindingId": binding_id, "attempts": []}


def prepare(project_id: str, binding_id: str, store: ResearchStore, *, ignore_job: str = "") -> dict[str, Any]:
    """Validate preconditions; raises ``KeyError``/``ValueError``/``LeanConfigurationError``."""
    domain = ResearchDomainStore(store)
    binding = domain.get_formal_binding(project_id, binding_id)
    if binding is None:
        raise KeyError("未找到形式化绑定。")
    settings = domain.lean_settings(project_id)
    if settings["confirmBeforeCheck"] and binding["mappingAuthority"] != "USER_CONFIRMED":
        raise ValueError("请先核对并确认命题映射，再让 AI 尝试证明。")
    if binding["mappingAuthority"] not in {"USER_CONFIRMED", "AI_PROPOSED", "UNCONFIRMED"}:
        raise ValueError("当前形式化映射状态不能启动 AI 证明。")
    claim = next((node for node in domain.list_graph(project_id)["nodes"] if node["id"] == binding["claimNodeId"]), None)
    if not claim:
        raise ValueError("绑定的证据图命题已不存在。")
    branch_name = str(claim.get("branch") or "main")
    branch = domain.get_branch(project_id, branch_name)
    if not branch:
        raise ValueError("该命题所属研究分支缺失。")
    detect_lean_workspace()
    for key, job in _JOBS.items():
        if key != ignore_job and job.get("bindingId") == binding_id and job.get("state") == "RUNNING":
            raise ValueError("该命题已有正在进行的 AI 证明任务。")
    return {"binding": binding, "claim": claim, "branch": branch_name}


async def run(project_id: str, binding_id: str, store: ResearchStore, *, max_attempts: int = 4, hint: str = "",
              completion: Completion | None = None, checker: Checker | None = None, job_id: str = "") -> dict[str, Any]:
    job_id = job_id or "leanjob_" + uuid4().hex
    context = prepare(project_id, binding_id, store, ignore_job=job_id)
    binding, branch_name = context["binding"], context["branch"]
    domain = ResearchDomainStore(store)
    project = store.get_project(project_id) or {}
    if completion is None:
        from aleggo.study import research_runtime as runtime

        async def completion(messages: list[dict[str, str]]) -> dict[str, Any]:
            return await runtime._MODEL_ROUTER.complete(project.get("modelId") or "research.meridian", messages,
                                                        temperature=0.2, json_object=True)
    checker = checker or run_kernel_check
    try:
        attempts_allowed = max(1, min(MAX_ATTEMPTS, int(max_attempts)))
    except (TypeError, ValueError):
        attempts_allowed = 4
    job = _JOBS.setdefault(job_id, {})
    job.update({"id": job_id, "projectId": project_id, "bindingId": binding_id, "state": "RUNNING", "attempts": [], "passed": False,
                "proof": "", "error": "", "startedAt": _now(), "finishedAt": "", "maxAttempts": attempts_allowed,
                "usage": {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0, "modelCalls": 0}})
    statement = binding["leanStatement"]
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Original statement: " + binding["originalStatement"][:4000]
                 + "\nLean statement (prove exactly this): " + statement
                 + ("\nResearcher hint: " + hint.strip()[:2000] if hint.strip() else "")}]
    final_proof, final_result = "", None
    try:
        for number in range(1, attempts_allowed + 1):
            started = time.monotonic()
            try:
                response = await asyncio.wait_for(completion(messages), timeout=600.0)
            except asyncio.TimeoutError:
                timeout_attempt: dict[str, Any] = {"attempt": number, "proof": "", "explanation": "",
                                                   "status": "TIMEOUT", "feedback": "模型调用超时（600 秒），已跳过本次尝试。",
                                                   "elapsedMs": 0}
                job["attempts"].append(timeout_attempt)
                break
            usage = response.get("usage") or {}
            job["usage"]["modelCalls"] += 1
            for key in ("promptTokens", "completionTokens", "totalTokens"):
                job["usage"][key] += int(usage.get(key) or 0)
            text = str(response.get("text") or "")
            domain.record_tool_run(project_id, "llm:" + ROLE, {"bindingId": binding_id, "attempt": number, "messages": messages[-1:]},
                                   text, "COMPLETED", branch_name=branch_name, step_id="lean-autoprove:" + job["id"],
                                   elapsed_ms=int((time.monotonic() - started) * 1000), metadata={"usage": usage})
            attempt: dict[str, Any] = {"attempt": number, "proof": "", "explanation": "", "status": "", "feedback": "", "elapsedMs": 0}
            try:
                proof, explanation = _parse(text)
                attempt.update(proof=proof, explanation=explanation)
                _validate(statement, proof)
            except (UnsafeLeanInput, ValueError) as exc:
                attempt.update(status="REJECTED_BY_FILTER", feedback=str(exc))
                job["attempts"].append(attempt)
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "The checker rejected that reply before running Lean: " + str(exc)
                              + "\nReturn a corrected JSON reply that follows the rules."}]
                continue
            branch = domain.get_branch(project_id, branch_name) or {"limits": {}, "used": {}}
            seconds_left = float(branch["limits"].get("toolSeconds", 0)) - float(branch["used"].get("toolSeconds", 0))
            if seconds_left <= 0:
                attempt.update(status="RESOURCE_BUDGET_EXCEEDED", feedback="This branch has no formal-check time remaining.")
                job["attempts"].append(attempt)
                break
            domain.consume_budget(project_id, branch_name, "proofAttempts", 1)
            check_started = time.monotonic()
            try:
                result = await checker(project_id, store.aleggo_store.root, statement, proof, timeout_seconds=min(180.0, seconds_left))
            except LeanConfigurationError as exc:
                result = {"status": "LEAN_UNAVAILABLE", "passed": False, "output": str(exc), "elapsedMs": 0}
            elapsed = max(int(result.get("elapsedMs") or 0), int((time.monotonic() - check_started) * 1000))
            result["elapsedMs"] = elapsed
            try:
                domain.consume_budget(project_id, branch_name, "toolSeconds", elapsed / 1000)
            except ValueError:
                pass
            domain.record_tool_run(project_id, "lean_kernel_check",
                                   {"bindingId": binding_id, "attempt": number, "source": "ai-autoprove", "proof": proof},
                                   str(result.get("output") or "")[-12_000:], str(result.get("status") or "CHECK_FAILED"),
                                   branch_name=branch_name, elapsed_ms=elapsed, step_id="lean-autoprove:" + job["id"])
            attempt.update(status=str(result.get("status") or "CHECK_FAILED"), elapsedMs=elapsed,
                           feedback=_kernel_feedback(str(result.get("output") or "")))
            job["attempts"].append(attempt)
            final_proof, final_result = proof, result
            if result.get("passed") and result.get("status") == "KERNEL_CERTIFIED":
                job.update(passed=True, proof=proof)
                break
            if result.get("status") == "LEAN_UNAVAILABLE":
                break
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"Lean kernel result: {attempt['status']}. Error output:\n{attempt['feedback']}"
                          + "\n\nFix the proof. Return the full corrected tactic block as JSON."}]
        if final_result is not None:
            domain.record_formal_check(project_id, binding_id, final_proof, final_result)
        job["state"] = "COMPLETED"
    except asyncio.CancelledError:
        job.update(state="CANCELLED", error="证明任务已取消。")
        raise
    except Exception as exc:
        job.update(state="FAILED", error=f"{type(exc).__name__}: {str(exc)[:1500]}")
    finally:
        job["finishedAt"] = _now()
        lines = [f"Attempt {item['attempt']}: {item['status']}" for item in job["attempts"]]
        try:
            artifact = store.create_artifact(
                project_id, project.get("currentVersion") or 0, "", ROLE,
                ("AI Lean 证明 · " + ("内核已认证" if job["passed"] else "未通过认证") + " · " + binding["originalStatement"][:100]),
                ("Lean 内核已认证 AI 起草的证明：\n\n```lean\n" + job["proof"] + "\n```" if job["passed"] else
                 "没有一次尝试通过 Lean 内核认证。") + "\n\n" + "\n".join(lines),
                status="kernel_certified" if job["passed"] else "check_failed", branch_name=branch_name,
                metadata={"artifactKind": "lean-autoprove", "bindingId": binding_id, "jobId": job["id"], "state": job["state"],
                          "attempts": job["attempts"], "passed": job["passed"], "proof": job["proof"], "error": job["error"],
                          "startedAt": job["startedAt"], "finishedAt": job["finishedAt"], "maxAttempts": attempts_allowed,
                          "usage": job["usage"]})
            job["artifactId"] = artifact["id"]
        except Exception as exc:
            if not job.get("error"):
                job["error"] = f"证明任务结束，但未能写入产物：{type(exc).__name__}: {str(exc)[:400]}"
    return job_view(job)


def start(project_id: str, binding_id: str, store: ResearchStore, **kwargs: Any) -> dict[str, Any]:
    """Start one autoprove job per binding; serialize starts to avoid double RUNNING."""
    with _START_LOCK:
        prepare(project_id, binding_id, store)
        job_id = "leanjob_" + uuid4().hex
        try:
            _max = max(1, min(MAX_ATTEMPTS, int(kwargs.get("max_attempts", 4))))
        except (TypeError, ValueError):
            _max = 4
        kwargs["max_attempts"] = _max
        _JOBS[job_id] = {"id": job_id, "projectId": project_id, "bindingId": binding_id, "state": "RUNNING", "attempts": [],
                         "passed": False, "proof": "", "error": "", "startedAt": _now(), "finishedAt": "",
                         "maxAttempts": _max, "usage": {}}
        while len(_JOBS) > 200:
            finished = next((key for key, job in _JOBS.items() if job.get("state") != "RUNNING"), None)
            if finished is None:
                break  # all slots are live jobs; refuse to drop an in-flight proof
            _JOBS.pop(finished, None)

        async def runner() -> None:
            try:
                await run(project_id, binding_id, store, job_id=job_id, **kwargs)
            except Exception as exc:  # preconditions changed between start and run
                _JOBS[job_id].update(state="FAILED", error=f"{type(exc).__name__}: {exc}"[:1500], finishedAt=_now())

        task = asyncio.create_task(runner())
        _TASKS[job_id] = task
        task.add_done_callback(lambda _done, key=job_id: _TASKS.pop(key, None))
        return job_view(_JOBS[job_id])
