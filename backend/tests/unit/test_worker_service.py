"""Unit tests for LLMBaseCaseWorker — the real work executor."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.adapters.llm_client import LLMGenerateRequest
from app.domain.events import DomainEventType
from app.services.executor import WorkExecutionResult
from app.services.persona_registry import PersonaProfile
from app.services.worker import LLMBaseCaseWorker


class StubLLMClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[LLMGenerateRequest] = []

    def generate_json(self, request: LLMGenerateRequest) -> object:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("No stub responses remaining")
        return self._responses.pop(0)


class StubPersonaRegistry:
    def __init__(self, profiles: dict[str, PersonaProfile] | None = None) -> None:
        self._profiles = profiles or {}

    def get_profile(self, persona_id: str) -> PersonaProfile | None:
        return self._profiles.get(persona_id)

    def list_profiles(self) -> list[PersonaProfile]:
        return list(self._profiles.values())

    def reload(self) -> None:
        pass


def _make_profile(persona_id: str = "python_developer") -> PersonaProfile:
    return PersonaProfile(
        persona_id=persona_id,
        name="Python Developer",
        description="Builds Python services.",
        system_prompt="You are a senior Python developer.",
        guardrails=("Validate assumptions.", "Prefer testable designs."),
        tools=("search_api", "python_runtime"),
        routing_hints=("python", "backend"),
        source_path="/personas/python_developer.md",
        profile_hash="abc123",
        prompt_guardrails_hash="def456",
    )


def _make_work_plan(steps: int = 2) -> list[dict[str, Any]]:
    return [
        {"step": i + 1, "description": f"Execute step {i + 1}"}
        for i in range(steps)
    ]


def test_worker_executes_all_steps_and_returns_completed() -> None:
    """Worker should call LLM for each step and return completed result."""
    llm = StubLLMClient(
        responses=[
            {"reasoning": "Analyzing requirements", "output": {"schema": "users"}},
            {"reasoning": "Writing implementation", "output": {"code": "def main(): ..."}},
        ]
    )
    registry = StubPersonaRegistry({"python_developer": _make_profile()})

    worker = LLMBaseCaseWorker(
        llm_client=llm,
        persona_registry=registry,
        temperature=0.1,
    )

    result = worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Build a user service",
        depth=1,
        persona_id="python_developer",
        work_plan=_make_work_plan(2),
    )

    assert isinstance(result, WorkExecutionResult)
    assert result.status == "completed"
    assert result.output is not None
    assert result.output["steps_completed"] == 2
    assert len(result.output["step_results"]) == 2
    assert result.output["step_results"][0]["output"]["output"] == {"schema": "users"}
    assert result.output["persona_id"] == "python_developer"
    assert len(llm.calls) == 2


def test_worker_injects_persona_system_prompt() -> None:
    """System prompt should include persona guardrails and tools."""
    llm = StubLLMClient(
        responses=[{"reasoning": "ok", "output": "done"}]
    )
    registry = StubPersonaRegistry({"python_developer": _make_profile()})

    worker = LLMBaseCaseWorker(llm_client=llm, persona_registry=registry)

    worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Do something",
        depth=0,
        persona_id="python_developer",
        work_plan=_make_work_plan(1),
    )

    assert len(llm.calls) == 1
    system_msg = llm.calls[0].messages[0]
    assert system_msg.role == "system"
    assert "senior Python developer" in system_msg.content
    assert "Validate assumptions" in system_msg.content
    assert "search_api" in system_msg.content


def test_worker_uses_fallback_prompt_without_persona() -> None:
    """Without persona, worker should use generic system prompt."""
    llm = StubLLMClient(
        responses=[{"reasoning": "ok", "output": "done"}]
    )
    registry = StubPersonaRegistry()

    worker = LLMBaseCaseWorker(llm_client=llm, persona_registry=registry)

    result = worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Do something",
        depth=0,
        persona_id=None,
        work_plan=_make_work_plan(1),
    )

    assert result.status == "completed"
    system_msg = llm.calls[0].messages[0]
    assert "execution agent" in system_msg.content


def test_worker_returns_failed_on_llm_error() -> None:
    """If LLM raises, worker should return failed result."""

    class FailingLLM:
        def generate_json(self, request: LLMGenerateRequest) -> object:
            raise RuntimeError("provider timeout")

    registry = StubPersonaRegistry()

    worker = LLMBaseCaseWorker(llm_client=FailingLLM(), persona_registry=registry)

    result = worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Do something",
        depth=0,
        persona_id=None,
        work_plan=_make_work_plan(1),
    )

    assert result.status == "failed"
    assert "step 1 failed" in result.error
    assert "provider timeout" in result.error


def test_worker_stops_on_first_step_failure() -> None:
    """If step 1 fails, step 2 should never execute."""
    call_count = 0

    class FailOnSecondLLM:
        def generate_json(self, request: LLMGenerateRequest) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"reasoning": "ok", "output": "step1"}
            raise RuntimeError("step 2 exploded")

    registry = StubPersonaRegistry()
    worker = LLMBaseCaseWorker(llm_client=FailOnSecondLLM(), persona_registry=registry)

    result = worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Multi-step",
        depth=0,
        persona_id=None,
        work_plan=_make_work_plan(3),
    )

    assert result.status == "failed"
    assert "step 2 failed" in result.error
    assert call_count == 3  # step 1 ok + step 2 fail + self-heal retry fail; never reached step 3


def test_worker_passes_prior_context_to_subsequent_steps() -> None:
    """Step 2 prompt should include step 1 output as context."""
    llm = StubLLMClient(
        responses=[
            {"reasoning": "designed", "output": {"table": "users"}},
            {"reasoning": "implemented", "output": {"code": "CREATE TABLE"}},
        ]
    )
    registry = StubPersonaRegistry()

    worker = LLMBaseCaseWorker(llm_client=llm, persona_registry=registry)

    worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Design and build",
        depth=0,
        persona_id=None,
        work_plan=_make_work_plan(2),
    )

    step2_user_msg = llm.calls[1].messages[1].content
    assert "Prior step results" in step2_user_msg
    assert "users" in step2_user_msg  # step 1 output carried forward


def test_worker_emits_step_events() -> None:
    """Worker should emit step_started and step_completed events."""
    llm = StubLLMClient(
        responses=[{"reasoning": "ok", "output": "done"}]
    )
    registry = StubPersonaRegistry()

    emitted: list[tuple[str, str, DomainEventType, dict]] = []

    def mock_emitter(
        run_id: str,
        node_id: str,
        event_type: DomainEventType,
        payload: dict[str, object],
    ) -> None:
        emitted.append((run_id, node_id, event_type, payload))

    worker = LLMBaseCaseWorker(
        llm_client=llm,
        persona_registry=registry,
        event_emitter=mock_emitter,
    )

    worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Do it",
        depth=0,
        persona_id=None,
        work_plan=_make_work_plan(1),
    )

    event_types = [e[2] for e in emitted]
    assert DomainEventType.WORK_STEP_STARTED in event_types
    assert DomainEventType.WORK_STEP_COMPLETED in event_types

    started = [e for e in emitted if e[2] == DomainEventType.WORK_STEP_STARTED][0]
    assert started[3]["stepIndex"] == 1
    assert started[3]["totalSteps"] == 1

    completed = [e for e in emitted if e[2] == DomainEventType.WORK_STEP_COMPLETED][0]
    assert completed[3]["success"] is True


def test_worker_emits_error_event_on_step_failure() -> None:
    """Failed step should emit step_completed with success=False and error."""

    class FailLLM:
        def generate_json(self, request: LLMGenerateRequest) -> object:
            raise RuntimeError("boom")

    emitted: list[tuple[str, str, DomainEventType, dict]] = []

    def mock_emitter(
        run_id: str,
        node_id: str,
        event_type: DomainEventType,
        payload: dict[str, object],
    ) -> None:
        emitted.append((run_id, node_id, event_type, payload))

    worker = LLMBaseCaseWorker(
        llm_client=FailLLM(),
        persona_registry=StubPersonaRegistry(),
        event_emitter=mock_emitter,
    )

    worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Fail",
        depth=0,
        persona_id=None,
        work_plan=_make_work_plan(1),
    )

    completed_events = [e for e in emitted if e[2] == DomainEventType.WORK_STEP_COMPLETED]
    assert len(completed_events) == 1
    assert completed_events[0][3]["success"] is False
    assert "boom" in completed_events[0][3]["error"]


def test_worker_metadata_includes_service_worker() -> None:
    """LLM request metadata should identify the worker service."""
    llm = StubLLMClient(
        responses=[{"reasoning": "ok", "output": "done"}]
    )
    registry = StubPersonaRegistry()
    worker = LLMBaseCaseWorker(llm_client=llm, persona_registry=registry)

    worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Test",
        depth=0,
        persona_id=None,
        work_plan=_make_work_plan(1),
    )

    assert llm.calls[0].metadata["service"] == "worker"
    assert llm.calls[0].metadata["step"] == "1"


def test_worker_returns_file_proposals_instead_of_writing_files(tmp_path) -> None:
    """Files emitted by the LLM should become proposals, not direct writes."""
    llm = StubLLMClient(
        responses=[
            {
                "reasoning": "Prepared draft files",
                "output": {"summary": "proposal ready"},
                "files": [
                    {"path": "src/main.py", "content": "print('hello')"},
                    {"path": "../escape.txt", "content": "blocked"},
                    {"path": "docs/spec.json", "content": {"version": 1}},
                ],
            }
        ]
    )
    registry = StubPersonaRegistry({"python_developer": _make_profile()})
    worker = LLMBaseCaseWorker(
        llm_client=llm,
        persona_registry=registry,
        workspace_root=tmp_path,
    )

    result = worker.execute(
        run_id="run_1",
        node_id="node_1",
        objective="Draft files",
        depth=0,
        persona_id="python_developer",
        work_plan=_make_work_plan(1),
    )

    assert result.status == "completed"
    assert result.output is not None

    proposals = result.output["file_proposals"]
    assert len(proposals) == 2
    assert proposals[0]["path"] == "src/main.py"
    assert proposals[0]["content"] == "print('hello')"
    assert proposals[0]["step_index"] == 1
    assert proposals[1]["path"] == "docs/spec.json"
    assert '"version": 1' in proposals[1]["content"]

    step_proposals = result.output["step_results"][0]["file_proposals"]
    assert len(step_proposals) == 2
    assert not (tmp_path / "run_1" / "src" / "main.py").exists()
