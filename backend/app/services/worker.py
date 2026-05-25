"""LLM-powered base-case worker: executes multi-step work plans via persona-aware LLM calls."""
from __future__ import annotations

import importlib.util
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapters.llm_client import LLMClient, LLMGenerateRequest, LLMMessage
from app.domain.events import DomainEventType
from app.domain.models import NodeContext
from app.services.persona_registry import PersonaProfile, PersonaRegistry

EventEmitter = Callable[[str, str, DomainEventType, dict[str, object]], None]
_log = logging.getLogger(__name__)


class WorkerSchemaError(RuntimeError):
    """Raised when work step output cannot be parsed as JSON."""


@dataclass(slots=True, frozen=True)
class StepResult:
    step_index: int
    description: str
    output: dict[str, Any] | list[Any] | str | int | float | bool | None
    error: str | None = None


@dataclass(slots=True, frozen=True)
class FileProposal:
    path: str
    content: str
    step_index: int
    node_id: str


class LLMBaseCaseWorker:
    """Executes base-case work plans step-by-step with persona context, hooks, and schema validation."""

    def __init__(self, *, llm_client: LLMClient, persona_registry: PersonaRegistry,
                 temperature: float = 0.2, event_emitter: EventEmitter | None = None) -> None:
        self._llm = llm_client
        self._registry = persona_registry
        self._temp = temperature
        self._emitter = event_emitter

    def execute(self, *, run_id: str, node_id: str, objective: str, depth: int,
                persona_id: str | None, work_plan: list[dict[str, Any]],
                node_context: NodeContext | None = None, model: str | None = None) -> Any:
        from app.services.executor import WorkExecutionResult

        profile = self._registry.get_profile(persona_id) if persona_id else None
        sys_prompt = self._build_system(profile)
        steps: list[dict[str, Any]] = []
        ctx: list[str] = []
        proposals: list[dict[str, Any]] = []

        for i, step in enumerate(work_plan):
            si = step.get("step", i + 1)
            desc = step.get("description", f"Step {si}")
            total = len(work_plan)
            self._emit(run_id, node_id, DomainEventType.WORK_STEP_STARTED,
                       {"stepIndex": si, "description": desc, "totalSteps": total})

            try:
                out = self._call_step(sys_prompt, objective, desc, si, total, depth,
                                      _sliding_context(ctx), profile, node_context, model=model)
            except Exception as err:
                out = self._self_heal(sys_prompt, objective, desc, si, total, depth, ctx, profile, str(err))
                if out is None:
                    self._emit(run_id, node_id, DomainEventType.WORK_STEP_COMPLETED,
                               {"stepIndex": si, "description": desc, "totalSteps": total, "success": False, "error": str(err)})
                    return WorkExecutionResult.failed(f"step {si} failed (self-heal also failed): {err}")

            sp = _extract_file_proposals(out, node_id, si)
            proposals.extend(sp)
            ctx.append(f"Step {si} ({desc}): {json.dumps(out, ensure_ascii=False, default=str)[:300]}")
            steps.append({"step": si, "description": desc, "output": out, "file_proposals": sp})
            self._emit(run_id, node_id, DomainEventType.WORK_STEP_COMPLETED,
                       {"stepIndex": si, "description": desc, "totalSteps": total, "success": True})

        return WorkExecutionResult.completed({
            "objective": objective, "persona_id": persona_id,
            "steps_completed": len(steps), "step_results": steps, "file_proposals": proposals,
        })

    # ---- prompt building ----

    def _build_system(self, profile: PersonaProfile | None) -> str:
        if profile is None:
            return ("You are a skilled execution agent in a recursive workflow engine. "
                    "Complete the assigned step precisely and return structured JSON output.\n\n"
                    "When producing code/files, include a 'files' array: "
                    '[{"path": "relative/path.ext", "content": "..."}].\n'
                    'Return JSON with "reasoning" and "output" fields.')
        parts = [profile.system_prompt]
        if profile.guardrails:
            parts.append("\n\nGuardrails you MUST follow:\n" + "\n".join(f"- {g}" for g in profile.guardrails))
        if profile.tools:
            parts.append(f"\n\nAvailable tools: {', '.join(profile.tools)}")
        if profile.examples:
            parts.append(_examples_block(profile.examples))
        parts.append("\n\nReturn your response as structured JSON.")
        return "".join(parts)

    # ---- LLM call ----

    def _call_step(self, sys_prompt: str, objective: str, desc: str, si: int, total: int,
                   depth: int, prior: str, profile: PersonaProfile | None,
                   node_ctx: NodeContext | None = None, *, model: str | None = None) -> Any:
        lineage = f"\n\n{node_ctx.to_prompt_block()}" if node_ctx else ""
        ctx_block = f"\n\nPrior progress:\n{prior}" if prior else ""
        name = profile.name if profile else "General Agent"
        user_prompt = (f"You are acting as: {name}\nOverall objective: {objective}\n"
                       f"Current step ({si}/{total}): {desc}\nTree depth: {depth}"
                       f"{lineage}{ctx_block}\n\nExecute this step. Return JSON with 'reasoning' and 'output' fields.")
        if profile:
            user_prompt = _run_hook(profile, "pre", user_prompt)
        temp = profile.model.temperature if profile and profile.model.temperature is not None else self._temp
        resp = self._llm.generate_json(LLMGenerateRequest(
            messages=[LLMMessage(role="system", content=sys_prompt), LLMMessage(role="user", content=user_prompt)],
            temperature=temp, model=model,
            metadata={"service": "worker", "step": str(si), "total_steps": str(total),
                      "persona": profile.persona_id if profile else "none"},
        )).data
        if profile:
            resp = _run_hook(profile, "post", resp)
        return resp

    def _self_heal(self, sys_prompt: str, objective: str, desc: str, si: int, total: int,
                   depth: int, ctx: list[str], profile: PersonaProfile | None, error: str) -> Any:
        try:
            heal_ctx = _sliding_context(ctx)
            heal_ctx += f"\nPREVIOUS ATTEMPT FAILED: {error}. Fix the issue and try a different approach."
            return self._call_step(sys_prompt, objective, f"[RETRY] {desc}", si, total, depth, heal_ctx, profile)
        except Exception as exc:
            _log.warning(
                "self-heal failed for step %d/%d (persona=%s): %s",
                si, total,
                profile.persona_id if profile else "none",
                exc,
                exc_info=True,
            )
            return None

    def _emit(self, run_id: str, node_id: str, evt: DomainEventType, payload: dict[str, object]) -> None:
        if self._emitter:
            self._emitter(run_id, node_id, evt, payload)


# ---- pure functions (no self) ----

def _sliding_context(steps: list[str]) -> str:
    if not steps:
        return ""
    if len(steps) == 1:
        return steps[0]
    summary = []
    for s in steps[:-1]:
        label, _, output = s.partition(": ")
        snippet = (output[:120] + "...") if len(output) > 120 else output
        summary.append(f"{label}: {snippet}" if output else label)
    return "Previous steps:\n" + "\n".join(summary) + f"\nCurrent step detail:\n{steps[-1]}"


def _examples_block(examples: tuple[dict[str, Any], ...]) -> str:
    parts = ["\n\nExamples of expected input/output:"]
    for i, ex in enumerate(examples[:3], 1):
        inp = ex.get("input", ex.get("objective", ""))
        out = ex.get("output", ex.get("expected", ""))
        parts.append(f"\nExample {i}:")
        if inp:
            parts.append(f"Input: {json.dumps(inp, ensure_ascii=False)[:500]}")
        if out:
            parts.append(f"Output: {json.dumps(out, ensure_ascii=False)[:500]}")
    return "\n".join(parts)


def _run_hook(profile: PersonaProfile, hook_type: str, data: Any) -> Any:
    if not profile.package_dir:
        return data
    hook_path_str = getattr(profile.hooks, hook_type, None)
    if not hook_path_str:
        return data
    hook_path = Path(profile.package_dir) / hook_path_str
    if not hook_path.exists():
        return data
    try:
        spec = importlib.util.spec_from_file_location(f"hook_{hook_type}", hook_path)
        if not spec or not spec.loader:
            return data
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, "run", None) or getattr(mod, hook_type, None)
        if callable(fn):
            return fn(data)
    except Exception:
        _log.warning("hook %s failed for persona=%s", hook_type, profile.persona_id, exc_info=True)
    return data


def _extract_file_proposals(output: Any, node_id: str, step_index: int) -> list[dict[str, Any]]:
    if not isinstance(output, dict):
        return []
    files = output.get("files", [])
    if not isinstance(files, list):
        return []
    proposals = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        rel_path = entry.get("path", "")
        content = entry.get("content", "")
        if not rel_path or not isinstance(rel_path, str):
            continue
        clean = Path(rel_path).as_posix()
        if ".." in clean.split("/"):
            continue
        proposals.append({
            "path": clean,
            "content": content if isinstance(content, str) else json.dumps(content, indent=2),
            "step_index": step_index, "node_id": node_id,
        })
    return proposals


__all__ = ["FileProposal", "LLMBaseCaseWorker", "StepResult", "WorkerSchemaError"]
