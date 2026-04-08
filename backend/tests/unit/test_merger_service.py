from __future__ import annotations

from app.adapters.llm_client import LLMGenerateRequest
from app.schemas.contracts import MergeRequest, MergeResponse
from app.services.merger import MergerSchemaError, MergerService


class StubLLMClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[LLMGenerateRequest] = []

    def generate_json(self, request: LLMGenerateRequest) -> object:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("No stub responses remaining")
        return self._responses.pop(0)


def _build_request() -> MergeRequest:
    return MergeRequest(
        parent_objective="Deliver integrated backend + frontend run flow",
        child_outputs=[
            {
                "node_id": "node_backend",
                "persona_id": "python_developer",
                "output": {
                    "api": {"run_id": "string", "status": "running"},
                    "events": {"seq": "integer", "type": "string"},
                },
                "boundary_contract": "Backend emits snake_case payload fields",
            },
            {
                "node_id": "node_frontend",
                "persona_id": "frontend_developer",
                "output": {
                    "api": {"runId": "string", "status": "running"},
                    "events": {"sequence": "integer", "kind": "string"},
                },
                "boundary_contract": "Frontend expects camelCase fields",
            },
        ],
    )


def test_merger_populates_conflict_resolution_for_conflicting_children() -> None:
    llm = StubLLMClient(
        responses=[
            {
                "merged_output": {
                    "api_contract": {
                        "runId": "string",
                        "status": "running|completed|failed",
                    },
                    "event_contract": {
                        "seq": "integer",
                        "type": "string",
                    },
                },
                "conflict_resolutions": [
                    {
                        "conflict": "snake_case backend fields conflict with frontend camelCase expectation",
                        "chosen_approach": "Adopt camelCase at REST boundary and map backend internals",
                        "rejected_approach": "Force frontend to use snake_case payloads",
                        "rationale": "Preserves frontend API conventions while maintaining backend compatibility via serializer mapping",
                    }
                ],
                "unresolved_conflicts": [],
            }
        ]
    )
    service = MergerService(llm_client=llm, max_schema_retries=2)

    result = service.merge(_build_request())

    assert result.attempts_used == 1
    assert result.has_unresolved_conflicts is False
    assert len(result.response.conflict_resolutions) == 1
    resolution = result.response.conflict_resolutions[0]
    assert "snake_case" in resolution.conflict
    assert "camelCase" in resolution.chosen_approach
    assert result.response.unresolved_conflicts == []
    assert result.checker_payload["integration_ready"] is True
    assert len(result.events) == 2
    assert result.events[0].event_type == "merge.started"
    assert result.events[1].event_type == "merge.completed"


def test_merger_represents_unresolved_conflicts_for_downstream_checker() -> None:
    llm = StubLLMClient(
        responses=[
            {
                "merged_output": {
                    "api_contract": {
                        "runId": "string",
                        "status": "running|completed|failed",
                    }
                },
                "conflict_resolutions": [
                    {
                        "conflict": "Pagination cursor format differs across child outputs",
                        "chosen_approach": "Prefer opaque cursor token from backend",
                        "rejected_approach": "Expose raw numeric offset",
                        "rationale": "Opaque cursor is safer for forward-compatible pagination",
                    }
                ],
                "unresolved_conflicts": [
                    "Event ordering guarantee unspecified between SSE reconnection and replay window"
                ],
            }
        ]
    )
    service = MergerService(llm_client=llm, max_schema_retries=2)

    result = service.merge(_build_request())

    assert result.has_unresolved_conflicts is True
    assert result.response.unresolved_conflicts == [
        "Event ordering guarantee unspecified between SSE reconnection and replay window"
    ]
    assert result.checker_payload["integration_ready"] is False
    assert (
        result.checker_payload["unresolved_conflicts"]
        == result.response.unresolved_conflicts
    )
    completed_payload = result.events[1].payload
    assert completed_payload["has_unresolved_conflicts"] is True


def test_merger_output_validates_against_contract_schema() -> None:
    llm = StubLLMClient(
        responses=[
            {
                "merged_output": {
                    "final_plan": [
                        "backend ready",
                        "frontend ready",
                        "contracts aligned",
                    ]
                },
                "conflict_resolutions": [],
                "unresolved_conflicts": [],
            }
        ]
    )
    service = MergerService(llm_client=llm, max_schema_retries=2)

    result = service.merge(_build_request())

    validated = MergeResponse.model_validate(result.response.model_dump())
    assert validated.merged_output == result.response.merged_output
    assert validated.unresolved_conflicts == []


def test_merger_retries_and_raises_on_schema_invalid_responses() -> None:
    llm = StubLLMClient(
        responses=[
            {"conflict_resolutions": [], "unresolved_conflicts": []},
            {
                "merged_output": "string is allowed",
                "conflict_resolutions": [
                    {
                        "conflict": "missing chosen_approach",
                        "rationale": "invalid shape",
                    }
                ],
                "unresolved_conflicts": [],
            },
            {"unexpected": "shape"},
        ]
    )
    service = MergerService(llm_client=llm, max_schema_retries=2)

    try:
        service.merge(_build_request())
        raise AssertionError("Expected MergerSchemaError")
    except MergerSchemaError as exc:
        assert "failed schema validation" in str(exc)

    assert len(llm.calls) == 3
    assert llm.calls[0].metadata["attempt"] == "1"
    assert llm.calls[1].metadata["attempt"] == "2"
    assert llm.calls[2].metadata["attempt"] == "3"
