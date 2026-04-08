from __future__ import annotations

from app.adapters.llm_client import LLMGenerateRequest
from app.schemas.contracts import DividerDecision
from app.services.divider import DividerSchemaError, DividerService


class StubLLMClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[LLMGenerateRequest] = []

    def generate_json(self, request: LLMGenerateRequest) -> object:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("No stub responses remaining")
        return self._responses.pop(0)


def test_divider_extracts_work_plan_for_base_case() -> None:
    llm = StubLLMClient(
        responses=[
            {
                "decision": "BASE_CASE",
                "rationale": "Single persona can execute this linearly.",
                "work_plan": [
                    {"step": 1, "description": "Inspect requirements"},
                    {"step": 2, "description": "Implement service"},
                    {"step": 3, "description": "Run tests"},
                ],
                "suggested_persona": "python_developer",
            }
        ]
    )
    service = DividerService(llm_client=llm, max_schema_retries=2)

    result = service.divide("Implement typed divider service", depth=0)

    assert result.decision == DividerDecision.BASE_CASE
    assert result.base_case is not None
    assert result.recursive_case is None
    assert [step["step"] for step in result.base_case.work_plan] == [1, 2, 3]
    assert result.base_case.work_plan[1]["description"] == "Implement service"
    assert result.base_case.suggested_persona == "python_developer"
    assert result.attempts_used == 1
    assert len(result.events) == 1
    assert result.events[0].event_type == "node.decomposed"
    assert result.events[0].payload["decision"] == "BASE_CASE"


def test_divider_extracts_children_for_recursive_case() -> None:
    llm = StubLLMClient(
        responses=[
            {
                "decision": "RECURSIVE_CASE",
                "rationale": "Split backend and frontend concerns.",
                "children": [
                    {
                        "objective": "Build FastAPI endpoints",
                        "dependencies": [],
                        "suggested_persona": "python_developer",
                        "interface_contract": "REST run APIs",
                    },
                    {
                        "objective": "Build React graph UI",
                        "dependencies": ["child_backend"],
                        "suggested_persona": "frontend_developer",
                        "interface_contract": "SSE event envelope",
                    },
                ],
            }
        ]
    )
    service = DividerService(llm_client=llm, max_schema_retries=2)

    result = service.divide("Deliver mission control app", depth=1)

    assert result.decision == DividerDecision.RECURSIVE_CASE
    assert result.base_case is None
    assert result.recursive_case is not None
    assert len(result.recursive_case.children) == 2
    assert result.recursive_case.children[0].objective == "Build FastAPI endpoints"
    assert result.recursive_case.children[1].dependencies == ["child_backend"]
    assert result.recursive_case.children[1].interface_contract == "SSE event envelope"
    assert result.attempts_used == 1
    assert len(result.events) == 1
    assert result.events[0].payload["decision"] == "RECURSIVE_CASE"


def test_divider_retries_and_raises_on_malformed_outputs() -> None:
    llm = StubLLMClient(
        responses=[
            {"decision": "BASE_CASE", "rationale": "missing work_plan"},
            {
                "decision": "RECURSIVE_CASE",
                "rationale": "too few children",
                "children": [{"objective": "only one child"}],
            },
            {"unexpected": "shape"},
        ]
    )
    service = DividerService(llm_client=llm, max_schema_retries=2)

    try:
        service.divide("Malformed output handling", depth=2)
        raise AssertionError("Expected DividerSchemaError")
    except DividerSchemaError as exc:
        assert "failed schema validation" in str(exc)

    assert len(llm.calls) == 3
    assert llm.calls[0].metadata["attempt"] == "1"
    assert llm.calls[1].metadata["attempt"] == "2"
    assert llm.calls[2].metadata["attempt"] == "3"
