"""LLM-powered base-case worker: executes multi-step work plans via persona-aware LLM calls."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapters.llm_client import LLMClient, LLMGenerateRequest, LLMMessage
from app.domain.events import DomainEventType
from app.services.persona_registry import PersonaProfile, PersonaRegistry

EventEmitter = Callable[[str, str, DomainEventType, dict[str, object]], None]

# Default workspace root — each run gets a subfolder
_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[3] / "workspace"


class WorkerSchemaError(RuntimeError):
    """Raised when work step output cannot be parsed as JSON."""


@dataclass(slots=True, frozen=True)
class StepResult:
    """Result of executing one work-plan step via LLM."""

    step_index: int
    description: str
    output: dict[str, Any] | list[Any] | str | int | float | bool | None
    error: str | None = None


@dataclass(slots=True, frozen=True)
class FileProposal:
    """Normalized file proposal emitted by a work step."""

    path: str
    content: str
    step_index: int
    node_id: str


class LLMBaseCaseWorker:
    """Executes base-case work plans by calling the LLM for each step with persona context.

    This is the piece that makes the recursive engine actually *do* work.
    The divider breaks objectives into a work_plan of sequential steps;
    this worker iterates through those steps, injecting the assigned persona's
    system prompt and guardrails, and calling the LLM for each step.
    """

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        persona_registry: PersonaRegistry,
        temperature: float = 0.2,
        event_emitter: EventEmitter | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._persona_registry = persona_registry
        self._temperature = temperature
        self._event_emitter = event_emitter
        self._workspace_root = workspace_root or _DEFAULT_WORKSPACE_ROOT

    def execute(
        self,
        *,
        run_id: str,
        node_id: str,
        objective: str,
        depth: int,
        persona_id: str | None,
        work_plan: list[dict[str, Any]],
    ) -> Any:
        """Execute the full work plan step-by-step and return a WorkExecutionResult."""
        from app.services.executor import WorkExecutionResult

        workspace_dir = self._workspace_root / run_id

        profile = self._resolve_persona(persona_id)
        system_prompt = self._build_system_prompt(profile, workspace_dir)

        step_results: list[dict[str, Any]] = []
        accumulated_context: list[str] = []
        file_proposals: list[dict[str, Any]] = []

        for index, step in enumerate(work_plan):
            step_index = step.get("step", index + 1)
            step_description = step.get("description", f"Step {step_index}")

            self._emit_step_started(
                run_id=run_id,
                node_id=node_id,
                step_index=step_index,
                description=step_description,
                total_steps=len(work_plan),
            )

            try:
                step_output = self._execute_step(
                    system_prompt=system_prompt,
                    objective=objective,
                    step_description=step_description,
                    step_index=step_index,
                    total_steps=len(work_plan),
                    depth=depth,
                    prior_context=accumulated_context,
                    profile=profile,
                    workspace_dir=workspace_dir,
                )
            except Exception as first_error:
                # Attempt self-heal: retry with error context
                step_output = self._attempt_self_heal(
                    system_prompt=system_prompt,
                    objective=objective,
                    step_description=step_description,
                    step_index=step_index,
                    total_steps=len(work_plan),
                    depth=depth,
                    prior_context=accumulated_context,
                    profile=profile,
                    workspace_dir=workspace_dir,
                    original_error=str(first_error),
                )
                if step_output is None:
                    self._emit_step_completed(
                        run_id=run_id,
                        node_id=node_id,
                        step_index=step_index,
                        description=step_description,
                        total_steps=len(work_plan),
                        error=str(first_error),
                    )
                    return WorkExecutionResult.failed(
                        f"step {step_index} failed (self-heal also failed): {first_error}"
                    )

            # Normalize any proposed files without performing final writes yet.
            proposals_from_step = self._extract_file_proposals(
                step_output, workspace_dir, node_id, step_index
            )
            file_proposals.extend(
                [
                    {
                        "path": proposal["path"],
                        "content": proposal["content"],
                        "step_index": proposal["step_index"],
                        "node_id": proposal["node_id"],
                    }
                    for proposal in proposals_from_step
                ]
            )

            accumulated_context.append(
                f"Step {step_index} ({step_description}): {json.dumps(step_output, ensure_ascii=False, default=str)[:500]}"
            )

            step_results.append(
                {
                    "step": step_index,
                    "description": step_description,
                    "output": step_output,
                    "file_proposals": proposals_from_step,
                }
            )

            self._emit_step_completed(
                run_id=run_id,
                node_id=node_id,
                step_index=step_index,
                description=step_description,
                total_steps=len(work_plan),
                error=None,
            )

        synthesized = {
            "objective": objective,
            "persona_id": persona_id,
            "steps_completed": len(step_results),
            "step_results": step_results,
            "workspace": str(workspace_dir),
            "file_proposals": file_proposals,
        }

        return WorkExecutionResult.completed(synthesized)

    def _resolve_persona(self, persona_id: str | None) -> PersonaProfile | None:
        if not persona_id:
            return None
        return self._persona_registry.get_profile(persona_id)

    def _build_system_prompt(
        self, profile: PersonaProfile | None, workspace_dir: Path | None = None
    ) -> str:
        workspace_instruction = ""
        if workspace_dir:
            workspace_instruction = (
                f"\n\nUse workspace root reference: {workspace_dir}\n"
                "When your step produces code, configs, or documents, include a 'files' array "
                "in your JSON response. Each entry: {\"path\": \"relative/path.ext\", \"content\": \"file content\"}. "
                "Paths are relative to the workspace root. These are proposed files for review, not final writes. Example:\n"
                '{"reasoning": "...", "output": "...", "files": [{"path": "src/main.py", "content": "print(\'hello\')"}]}'
            )

        if profile is None:
            return (
                "You are a skilled execution agent in a recursive workflow engine. "
                "Complete the assigned step precisely and return structured JSON output."
                f"{workspace_instruction}"
            )

        guardrail_block = ""
        if profile.guardrails:
            guardrail_lines = "\n".join(f"- {g}" for g in profile.guardrails)
            guardrail_block = f"\n\nGuardrails you MUST follow:\n{guardrail_lines}"

        tool_block = ""
        if profile.tools:
            tool_lines = ", ".join(profile.tools)
            tool_block = f"\n\nAvailable tools: {tool_lines}"

        return (
            f"{profile.system_prompt}"
            f"{guardrail_block}"
            f"{tool_block}"
            f"{workspace_instruction}"
            "\n\nReturn your response as structured JSON."
        )

    def _extract_file_proposals(
        self,
        step_output: Any,
        workspace_dir: Path,
        node_id: str,
        step_index: int,
    ) -> list[dict[str, Any]]:
        """Extract normalized file proposals from LLM output without writing them."""
        if not isinstance(step_output, dict):
            return []

        files = step_output.get("files", [])
        if not isinstance(files, list):
            return []

        proposals: list[dict[str, Any]] = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            rel_path = entry.get("path", "")
            content = entry.get("content", "")
            if not rel_path or not isinstance(rel_path, str):
                continue
            # Sanitize: prevent path traversal
            clean = Path(rel_path).as_posix()
            if ".." in clean.split("/"):
                continue

            normalized_content = (
                content if isinstance(content, str) else json.dumps(content, indent=2)
            )
            proposals.append(
                {
                    "path": clean,
                    "content": normalized_content,
                    "step_index": step_index,
                    "node_id": node_id,
                    "workspace_root": str(workspace_dir),
                }
            )
        return proposals

    def _attempt_self_heal(
        self,
        *,
        system_prompt: str,
        objective: str,
        step_description: str,
        step_index: int,
        total_steps: int,
        depth: int,
        prior_context: list[str],
        profile: PersonaProfile | None,
        workspace_dir: Path | None = None,
        original_error: str,
    ) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
        """One retry attempt with error context injected into the prompt."""
        try:
            heal_context = list(prior_context) + [
                f"PREVIOUS ATTEMPT FAILED with error: {original_error}. "
                "Please fix the issue and try a different approach."
            ]
            return self._execute_step(
                system_prompt=system_prompt,
                objective=objective,
                step_description=f"[RETRY] {step_description}",
                step_index=step_index,
                total_steps=total_steps,
                depth=depth,
                prior_context=heal_context,
                profile=profile,
                workspace_dir=workspace_dir,
            )
        except Exception:
            return None

    def _execute_step(
        self,
        *,
        system_prompt: str,
        objective: str,
        step_description: str,
        step_index: int,
        total_steps: int,
        depth: int,
        prior_context: list[str],
        profile: PersonaProfile | None,
        workspace_dir: Path | None = None,
    ) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
        context_block = ""
        if prior_context:
            context_block = (
                "\n\nPrior step results (use as context for this step):\n"
                + "\n".join(prior_context)
            )

        persona_name = profile.name if profile else "General Agent"
        user_prompt = (
            f"You are acting as: {persona_name}\n"
            f"Overall objective: {objective}\n"
            f"Current step ({step_index}/{total_steps}): {step_description}\n"
            f"Tree depth: {depth}"
            f"{context_block}\n\n"
            "Execute this step. Return JSON with your result. "
            "Include a 'reasoning' field explaining your approach and an 'output' field "
            "with the concrete deliverable for this step."
        )

        response = self._llm_client.generate_json(
            LLMGenerateRequest(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt),
                ],
                temperature=self._temperature,
                metadata={
                    "service": "worker",
                    "step": str(step_index),
                    "total_steps": str(total_steps),
                    "persona": profile.persona_id if profile else "none",
                },
            )
        )

        return response

    def _emit_step_started(
        self,
        *,
        run_id: str,
        node_id: str,
        step_index: int,
        description: str,
        total_steps: int,
    ) -> None:
        if self._event_emitter is None:
            return
        self._event_emitter(
            run_id,
            node_id,
            DomainEventType.WORK_STEP_STARTED,
            {
                "stepIndex": step_index,
                "description": description,
                "totalSteps": total_steps,
            },
        )

    def _emit_step_completed(
        self,
        *,
        run_id: str,
        node_id: str,
        step_index: int,
        description: str,
        total_steps: int,
        error: str | None,
    ) -> None:
        if self._event_emitter is None:
            return
        payload: dict[str, object] = {
            "stepIndex": step_index,
            "description": description,
            "totalSteps": total_steps,
            "success": error is None,
        }
        if error is not None:
            payload["error"] = error
        self._event_emitter(
            run_id,
            node_id,
            DomainEventType.WORK_STEP_COMPLETED,
            payload,
        )


__all__ = ["FileProposal", "LLMBaseCaseWorker", "StepResult", "WorkerSchemaError"]
